"""Winston TTS engine: Breeze TTS 2 behind the OpenAI speech protocol (../speech_server.py).

    git clone https://github.com/breezeblue-ai/breeze-tts upstream && git -C upstream checkout 58ec70ce5fa4
    .venv/bin/python server.py --voice /path/voices/vctk/p254_023.wav --voices-dir /path/voices --port 8880

Breeze TTS 2 (BreezeBlue; 3B; weights under the BreezeBlue Research and Non-Commercial License,
inference code Apache-2.0) clones from a clip and its words (../speech_server.py:transcript); the
clip is encoded to codec frames once per voice. 24 kHz out. The inference code is not a package,
so it is cloned into upstream/ at a pinned commit and put on the path.

It streams itself (FastBreezeStreamingRuntime.iter_audio_chunks). Eager it runs at RTF 2.2 on the
3090, too slow to stream; `--fast backbone_decode depth_decoder codec` captures the per-frame work as
CUDA graphs: RTF 0.44, 9.1 GB, 13.7 GB peak while capturing (docs/voices.md#breeze-tts-2-8-october).
The warmup captures graphs only for the guidance scale in use (upstream's profile also captures the
two-branch ones for a scale of 4).

--backbone-bits and --text-encoder-bits below 16 build the model in RAM instead of on the card: those
parts' linear layers go to the card as 4-bit weights (torchao, the tile-packed layout Ampere has
kernels for, HQQ scales, --int4-group weights a scale) or 8-bit ones (which torchao turns back into
bf16 for every product on CUDA, so slower per frame), the two text lookup tables (1.7 GB, read once per sentence) and the codec copy only
upstream's generate() uses stay in RAM, and the rest, the depth decoder included, goes as bf16 (a
community GGUF port found a quantized depth decoder drifts muffled on long speech). Both in 4 bits:
3.8 GB on the card at the same speed, but more word errors (5.1% against 1.9%).

Every sentence is a separate generation (the speech server splits a request at sentence ends, and
Winston sends a reply a sentence at a time), and Breeze's voice can change between two of them, heard
in the benchmark as the voice shifting at a full stop. So a sentence within --chain-secs of the last
one in the same voice gets that sentence, its words and its codec frames, appended to the clip as
its reference: it carries on from what was just said, and the clip stays first so the voice cannot
drift from it. Only the one sentence before is kept.

Its tags are in parentheses, "(laugh)", so Chatterbox's "[laugh]" is rewritten to them; upstream
says tags take effect more reliably with a guidance scale above 1, which runs two branches.
"""
import re
import sys
import time
from dataclasses import replace
from pathlib import Path

import numpy as np
import torch

here = Path(__file__).resolve().parent
sys.path.insert(0, str(here.parent))
sys.path.insert(0, str(here / "upstream"))
import speech_server as ss

log = ss.log
STAGES = ("text_encoder", "backbone_prefill", "backbone_decode", "depth_decoder", "codec")
_TAG = re.compile(r"\[([a-z][a-z ]*)\]")


def load_small(ckpt: Path, device: str, backbone_bits: int, text_encoder_bits: int, group: int):
    """upstream's load_runtime with the model built in RAM and quantized on its way to the card."""
    from models.breeze import BreezeForConditionalGeneration
    from qwen_tts import Qwen3TTSTokenizer
    from torchao.quantization import Int4WeightOnlyConfig, Int8WeightOnlyConfig, quantize_
    from transformers import AutoTokenizer

    torch.cuda.set_device(device)
    tokenizer = AutoTokenizer.from_pretrained(ckpt, fix_mistral_regex=False)
    model = BreezeForConditionalGeneration.from_pretrained(ckpt, dtype=torch.bfloat16, attn_implementation="eager")
    tables = [model.embed_text_tokens, model.text_encoder.embed_tokens]
    # the lookup tables, and the codec only upstream's non-streaming generate() uses, are unhooked from
    # every parent while the rest goes to the card (quantize_ moves what it walks), then put back
    in_ram = {id(m): m for m in (*tables, model.codec_model)}
    refs = [(parent, name, child) for _, parent in model.named_modules(remove_duplicate=False)
            for name, child in parent._modules.items() if id(child) in in_ram]
    for parent, name, _ in refs:
        parent._modules[name] = None
    quant = {4: Int4WeightOnlyConfig(group_size=group, int4_packing_format="tile_packed_to_4d",
                                     int4_choose_qparams_algorithm="hqq"),
             8: Int8WeightOnlyConfig()}
    for part, bits in ((model.backbone_model, backbone_bits), (model.text_encoder, text_encoder_bits)):
        if bits in quant:
            quantize_(part, quant[bits], device=device)
    model.to(device)
    for parent, name, child in refs:
        parent._modules[name] = child
    model.eval()
    for table in tables:
        table.register_forward_pre_hook(lambda _m, args: (args[0].cpu(), *args[1:]))
        table.register_forward_hook(lambda _m, _args, out: out.to(device))
    codec = Qwen3TTSTokenizer.from_pretrained(str(ckpt / "audio_tokenizer"), device_map=device)
    return tokenizer, model, codec


class Breeze2Engine:
    builtin_voices: tuple[str, ...] = ()

    def __init__(self, model_id: str, device: str, cfg: float, seed: int, fast: list[str], bits: tuple[int, int, int],
                 chain_secs: float):
        from breeze_infer import templates
        from breeze_infer.runtime import load_runtime, update_generation_config_for_breeze
        from huggingface_hub import snapshot_download
        from models.fast_streaming import FastBreezeStreamingRuntime, FastStreamingConfig
        from models.warmup_profile import load_warmup_profile

        ckpt = Path(model_id) if Path(model_id).is_dir() else Path(snapshot_download(model_id))
        if min(bits[:2]) < 16:
            self.tokenizer, self.tts, self.codec = load_small(ckpt, device, *bits)
        else:
            self.tokenizer, self.tts, self.codec = load_runtime(ckpt, device=device, attn_implementation="eager")
        update_generation_config_for_breeze(self.tts)
        # upstream's api.py values
        config = FastStreamingConfig(max_new_tokens=1500, max_seq_len=2048, repetition_penalty=1.1,
                                     **{f"fast_{s}": s in fast for s in STAGES})
        self.runtime = FastBreezeStreamingRuntime(self.tts, self.codec, config, tokenizer=self.tokenizer)
        if self.runtime.fast_enabled:
            profile = load_warmup_profile(here / "upstream" / "configs" / "fast.json")
            branches = 1 if cfg == 1.0 else 2
            self.runtime.warmup_from_profile(replace(
                profile, codec_chunk_frames=self.runtime.codec_chunk_frames, cfg_scales=(cfg,),
                backbone_decode_branch_batch_sizes=(branches,), depth_decoder_batch_sizes=(branches,)))
        # prepare_inputs encodes the clip on every request; synth hands it the codes prepared once
        self._codes = None
        templates._encode_prompt_audio = lambda _tokenizer, _path: self._codes
        self.templates = templates
        self.model, self.cfg, self.seed, self.chain_secs = model_id, cfg, seed, chain_secs
        self._last: dict[str, tuple[str, torch.Tensor, float]] = {}   # clip -> last sentence, its frames, when
        self.sample_rate = self.runtime.sample_rate

    def prepare(self, clip: Path):
        from breeze_infer.audio import encode_prompt_audio
        return str(clip), ss.transcript(clip), encode_prompt_audio(self.codec, clip)

    def stream_for(self, req: ss.SpeechRequest, on: bool) -> bool:
        return on

    def synth(self, text: str, voice, stream: bool):
        t = self.templates
        path, words, codes = voice
        said = _TAG.sub(r"(\1)", text)
        last = self._last.get(path)
        if last and time.monotonic() - last[2] < self.chain_secs:
            words, codes = f"{words} {last[0]}", torch.cat([codes, last[1]])
        self._codes = codes
        request = {"id": "winston", "text": said, "speaker": "S0", "ref_audio_path": path, "ref_text": words}
        frames = []
        with torch.inference_mode():
            inputs = t.prepare_inputs(self.tokenizer, self.codec, self.tts, [request],
                                      t.get_template(t.select_template_name(request)), guidance_scale=self.cfg,
                                      guidance_scale_ref=None, guidance_scale_ins=None)
            chunks = (c.audio for c in self.runtime.iter_audio_chunks(inputs, seed=self.seed,
                                                                      token_observer=frames.append))
            if stream:
                yield from chunks
            else:
                yield np.concatenate(list(chunks))
        if frames:      # not reached when the sentence was cut off; the one before stays the reference
            made = torch.stack(frames).to("cpu", torch.int16)
            made = made[(made != self.tts.config.codebook_pad_token_id).any(dim=1)]
            self._last[path] = (said, made, time.monotonic())


def main():
    ap = ss.arg_parser(__doc__.splitlines()[0])
    ap.add_argument("--device", default="cuda:0", help="a CUDA device with its index: upstream's streaming runtime needs both")
    ap.add_argument("--model", default="BreezeBlue/Breeze-TTS-2", help="Hugging Face id or local dir")
    ap.add_argument("--cfg", type=float, default=1.0, help="guidance scale, upstream's default; above 1 runs two branches")
    ap.add_argument("--seed", type=int, default=42, help="sampling seed per sentence, upstream's default")
    ap.add_argument("--chain-secs", type=float, default=30.0,
                    help="a sentence this soon after the last one in its voice carries on from it (0: never)")
    ap.add_argument("--fast", nargs="*", default=[], choices=STAGES, help="stages to capture as CUDA graphs")
    ap.add_argument("--backbone-bits", type=int, default=16, choices=(4, 8, 16), help="below 16 builds in RAM (docstring)")
    ap.add_argument("--text-encoder-bits", type=int, default=16, choices=(4, 8, 16), help="below 16 builds in RAM")
    ap.add_argument("--int4-group", type=int, default=128, choices=(32, 64, 128, 256), help="weights per 4-bit scale")
    args = ap.parse_args()
    ss.setup_logging()
    t0 = time.perf_counter()
    bits = (args.backbone_bits, args.text_encoder_bits, args.int4_group)
    engine = Breeze2Engine(args.model, args.device, args.cfg, args.seed, args.fast, bits, args.chain_secs)
    log.info("model %s loaded on %s in %.1f s, %d Hz, fast %s, bits %s", args.model, args.device,
             time.perf_counter() - t0, engine.sample_rate, args.fast or "off", bits)
    ss.serve(engine, args)


if __name__ == "__main__":
    main()
