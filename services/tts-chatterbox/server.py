"""Winston TTS adapter: OpenAI-compatible /v1/audio/speech on top of Chatterbox-Turbo.

Runs in its own venv so its torch pins never touch the pipeline venv; the protocol, the voices and
their cache are ../speech_server.py, shared with the Pocket server. Pipecat's OpenAITTSService talks
to it with response_format=pcm and expects 24 kHz mono int16, which is Chatterbox's native rate.

    .venv/bin/python server.py --voice ../../data/voices/vctk/p254_023.wav --voices-dir ../../data/voices

Streaming: Chatterbox renders a sentence whole, and its speech-token stage takes about 25 ms per
character, so a long sentence kept the first audio waiting 1.5 to 2 s. With --stream true (default)
the speech tokens are generated one by one and the prefix so far is vocoded after --first-secs of
audio, then in chunks that double up to --chunk-secs, so the next chunk is always ready before
playback catches up. Only the new samples go out, minus the last --lookahead-secs whose right
context is still missing, cross-faded into the next chunk. Winston passes these from tts.streaming.
A request may carry its own stream, first_secs, chunk_secs and lookahead_secs (the Voice page's
sample, before the settings are saved); what it leaves out keeps the startup values.
The vocoder is non-causal, so each prefix is a fresh render of the whole; the words come out the
same (Parakeet transcribes both identically).
"""
import sys
import time
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
import torch
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import speech_server as ss

log = ss.log
SAMPLES_PER_TOKEN = 960           # speech tokens run at 25 Hz, audio at 24 kHz
FADE = 480                        # 20 ms cross-fade at each seam


@dataclass(frozen=True)
class Stream:
    """How a sentence is cut into chunks, in speech tokens (25 per second of audio)."""
    on: bool = True
    first: int = 24               # rendered before the first chunk
    step: int = 40                # later chunks double up to this
    hold: int = 8                 # held back at a chunk's end until its right context is known


def _tokens(secs: float) -> int:
    return max(1, round(secs * 25))   # speech tokens run at 25 Hz


class ChatterboxEngine:
    model = "ResembleAI/chatterbox-turbo"
    builtin_voices: tuple[str, ...] = ()

    def __init__(self, device: str, stream: Stream):
        from chatterbox.tts_turbo import ChatterboxTurboTTS
        t0 = time.perf_counter()
        self.tts = ChatterboxTurboTTS.from_pretrained(device=device)
        self.sample_rate = self.tts.sr
        self.stream = stream          # from the command line; a request with its own values gets a copy
        log.info("model loaded in %.1f s, sr=%d", time.perf_counter() - t0, self.sample_rate)

    def prepare(self, clip: Path):
        # without autograd, whose saved tensors otherwise stay on the GPU with every cached voice
        # (11 clips ran a 3090 out of memory)
        with torch.inference_mode():
            before = self.tts.conds
            self.tts.prepare_conditionals(str(clip))
            conds, self.tts.conds = self.tts.conds, before
        torch.cuda.empty_cache()
        return conds

    def stream_for(self, req: ss.SpeechRequest, on: bool) -> Stream:
        """The startup streaming values with the request's own on top, refused (422) as the flags are at start."""
        given = {"first": req.first_secs, "step": req.chunk_secs, "hold": req.lookahead_secs}
        p = replace(self.stream, on=on, **{k: _tokens(v) for k, v in given.items() if v is not None})
        if p.hold >= p.first:
            raise HTTPException(422, "lookahead_secs must be shorter than first_secs")
        return p

    def synth(self, text: str, conds, stream: Stream):
        with torch.inference_mode():
            self.tts.conds = conds
            if stream.on:
                yield from self._synth_stream(text, stream)
            else:
                wav = self.tts.generate(text)
                yield wav.detach().cpu().numpy() if isinstance(wav, torch.Tensor) else wav

    def _speech_tokens(self, text: str):
        """Chatterbox-Turbo's T3 loop (t3.inference_turbo) as a generator, same sampling as generate()."""
        from chatterbox.tts_turbo import punc_norm
        from transformers.generation.logits_process import (
            LogitsProcessorList,
            RepetitionPenaltyLogitsProcessor,
            TemperatureLogitsWarper,
            TopKLogitsWarper,
            TopPLogitsWarper,
        )
        m = self.tts
        t3 = m.t3
        procs = LogitsProcessorList([TemperatureLogitsWarper(0.8), TopKLogitsWarper(1000), TopPLogitsWarper(0.95),
                                     RepetitionPenaltyLogitsProcessor(1.2)])
        tok = m.tokenizer(punc_norm(text), return_tensors="pt", padding=True, truncation=True).input_ids.to(m.device)
        start = t3.hp.start_speech_token * torch.ones_like(tok[:, :1])
        embeds, _ = t3.prepare_input_embeds(t3_cond=m.conds.t3, text_tokens=tok, speech_tokens=start, cfg_weight=0.0)
        out = t3.tfmr(inputs_embeds=embeds, use_cache=True)
        past, logits, seen = out.past_key_values, t3.speech_head(out[0][:, -1:]), start
        for _ in range(1000):
            nxt = torch.multinomial(torch.softmax(procs(seen, logits[:, -1, :]), dim=-1), num_samples=1)
            if int(nxt) == t3.hp.stop_speech_token:
                return
            seen = nxt if seen is start else torch.cat([seen, nxt], dim=1)
            yield int(nxt)
            out = t3.tfmr(inputs_embeds=t3.speech_emb(nxt), past_key_values=past, use_cache=True)
            past, logits = out.past_key_values, t3.speech_head(out[0])

    def _vocode(self, tokens: list[int], noise: torch.Tensor, final: bool) -> np.ndarray:
        """The whole prefix to float audio. One noise tensor per utterance keeps the renders close."""
        from chatterbox.models.s3gen.s3gen import S3Token2Mel
        from chatterbox.tts_turbo import S3GEN_SIL
        m = self.tts
        g = m.s3gen
        st = torch.tensor([t for t in tokens if t < 6561] + ([S3GEN_SIL] * 3 if final else []), device=m.device)
        torch.manual_seed(7)                     # the prompt part of the flow noise, and the vocoder's source
        mels = S3Token2Mel.forward(g, st[None], ref_wav=None, ref_sr=None, ref_dict=m.conds.gen, n_cfm_timesteps=2,
                                   finalize=True, noised_mels=noise[:, :, :len(st) * 2])   # finalize=False is broken upstream
        wav, _ = g.hift_inference(mels.to(g.dtype), None)
        wav[:, :len(g.trim_fade)] *= g.trim_fade
        return wav.squeeze(0).float().cpu().numpy()

    def _synth_stream(self, text: str, p: Stream):
        """One sentence as watermarked chunks, each as soon as it is safe to send."""
        g = self.tts.s3gen
        noise = torch.randn(1, 80, 2 * 1010, dtype=g.dtype, device=self.tts.device)
        tokens, emitted, tail, due = [], 0, None, p.first
        inc = min(p.step, p.first)   # chunks double up to the chunk length, so the next one is
                                     # always ready before the audio sent so far runs out
        gen = self._speech_tokens(text)
        while True:
            done = False
            for t in gen:
                tokens.append(t)
                if len(tokens) >= due:
                    break
            else:
                done = True
            if not tokens:
                return
            wav = self._vocode(tokens, noise, done)
            end = len(wav) if done else max(emitted, len(wav) - p.hold * SAMPLES_PER_TOKEN)
            seg = wav[emitted:end].copy()
            if tail is not None and len(seg) >= FADE:
                r = np.linspace(0, 1, FADE, dtype=np.float32)
                seg[:FADE] = tail[:FADE] * (1 - r) + seg[:FADE] * r
            tail, emitted, due = wav[end:end + FADE], end, due + inc
            inc = min(p.step, inc * 2)
            if len(seg):
                yield self.tts.watermarker.apply_watermark(seg, sample_rate=self.sample_rate)
            if done:
                return


def main():
    ap = ss.arg_parser(__doc__.splitlines()[0])
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--first-secs", type=float, default=0.96, help="audio rendered before the first chunk")
    ap.add_argument("--chunk-secs", type=float, default=1.6, help="audio per later chunk; longer is smoother")
    ap.add_argument("--lookahead-secs", type=float, default=0.32,
                    help="audio held back at a chunk's end until what follows is known")
    args = ap.parse_args()
    stream = Stream(on=args.stream, first=_tokens(args.first_secs), step=_tokens(args.chunk_secs),
                    hold=_tokens(args.lookahead_secs))
    if stream.hold >= stream.first:
        ap.error("--lookahead-secs must be shorter than --first-secs")
    ss.setup_logging()
    ss.serve(ChatterboxEngine(args.device, stream), args)


if __name__ == "__main__":
    main()
