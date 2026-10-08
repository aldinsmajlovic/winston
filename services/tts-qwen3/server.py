"""Winston TTS engine: Qwen3-TTS 1.7B Base behind the OpenAI speech protocol (../speech_server.py).

    .venv/bin/python server.py --voice /path/voices/vctk/p254_023.wav --voices-dir /path/voices --port 8880

Qwen3-TTS-12Hz-1.7B-Base (Apache-2.0, Alibaba Qwen) clones from a clip and its words (ICL): the
clip is encoded to 12.5 Hz codec frames and transcribed once (../speech_server.py:transcript), and
the model continues from them. 24 kHz out.

The qwen-tts package (0.1.1) has no streaming API: generate() returns every frame at the end. The
talker makes one 16-codebook frame per step, so this server hooks the talker's forward to take
each frame as it is made and decodes the frames so far every few frames, with the clip's last
frames as left context (the codec decoder looks only left, as its own chunked_decode shows).
"""
import functools
import sys
from pathlib import Path

import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import speech_server as ss

log = ss.log


class Qwen3Engine:
    builtin_voices: tuple[str, ...] = ()

    def __init__(self, model_id: str, device: str, language: str, first: int, step: int):
        from qwen_tts import Qwen3TTSModel
        dtype = torch.bfloat16 if device.startswith("cuda") else torch.float32
        self.tts = Qwen3TTSModel.from_pretrained(model_id, device_map=device, dtype=dtype, attn_implementation="sdpa")
        self.model, self.language, self.first, self.step = model_id, language, first, step
        self.sample_rate = int(self.tts.model.speech_tokenizer.get_output_sample_rate()) \
            if hasattr(self.tts.model.speech_tokenizer, "get_output_sample_rate") else 24000
        self.eos = self.tts.model.config.talker_config.codec_eos_token_id

    def prepare(self, clip: Path):
        words = ss.transcript(clip)
        with torch.inference_mode():
            return self.tts.create_voice_clone_prompt(ref_audio=str(clip), ref_text=words, x_vector_only_mode=False)

    def stream_for(self, req: ss.SpeechRequest, on: bool) -> bool:
        return on

    def _decode(self, frames: list) -> np.ndarray:
        codes = torch.stack(frames).to(self.tts.device)            # (T, 16)
        with torch.inference_mode():
            wavs, _ = self.tts.model.speech_tokenizer.decode([{"audio_codes": codes}])
        return np.asarray(wavs[0], dtype=np.float32).reshape(-1)

    def synth(self, text: str, voice, stream: bool):
        if not stream:
            with torch.inference_mode():
                wavs, _ = self.tts.generate_voice_clone(text=text, language=self.language, voice_clone_prompt=voice)
            yield np.asarray(wavs[0], dtype=np.float32)
            return
        talker = self.tts.model.talker

        def run(emit):
            forward = talker.forward

            @functools.wraps(forward)   # generate() reads the signature for its checks
            def hooked(*a, **kw):
                out = forward(*a, **kw)
                codes = out.hidden_states[-1] if isinstance(out.hidden_states, tuple) else None
                if codes is not None and int(codes[0, 0]) != self.eos:
                    emit(codes[0].detach().clone())
                return out

            talker.forward = hooked
            try:
                with torch.inference_mode():
                    self.tts.generate_voice_clone(text=text, language=self.language, voice_clone_prompt=voice,
                                                  max_new_tokens=1024)
            finally:
                del talker.forward                                   # back to the class method

        ref = voice[0].ref_code                                      # (T_ref, 16), the clip's own frames
        context = list(ref[-25:].to(self.tts.device)) if ref is not None else []
        yield from ss.stream_frames(ss.frames_from(run), self._decode, context=context,
                                    first=self.first, step=self.step, left=25, hold=0)


def main():
    ap = ss.arg_parser(__doc__.splitlines()[0])
    ap.add_argument("--device", default="cuda", help="cuda or cpu")
    ap.add_argument("--model", default="Qwen/Qwen3-TTS-12Hz-1.7B-Base", help="Hugging Face id or local dir")
    ap.add_argument("--language", default="English", help="English, or Auto to let the model tell")
    ap.add_argument("--first-frames", type=int, default=4, help="frames (80 ms each) before the first chunk")
    ap.add_argument("--chunk-frames", type=int, default=24, help="later chunks double up to this many frames")
    args = ap.parse_args()
    ss.setup_logging()
    import time
    t0 = time.perf_counter()
    engine = Qwen3Engine(args.model, args.device, args.language, args.first_frames, args.chunk_frames)
    log.info("model %s loaded on %s in %.1f s, %d Hz", args.model, args.device, time.perf_counter() - t0,
             engine.sample_rate)
    ss.serve(engine, args)


if __name__ == "__main__":
    main()
