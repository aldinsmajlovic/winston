"""Benchmark candidate: VoxCPM2 behind the OpenAI speech protocol (../speech_server.py).

    .venv/bin/python server.py --voice ../../../tests/tts_bench/work/voices/vctk/p254_023.wav --port 8892

VoxCPM2 (Apache-2.0, OpenBMB; 2B, 48 kHz out) clones in its "ultimate" mode: the clip as the
reference and as the prompt to continue from, with the clip's words (../speech_server.py:transcript).
The prompt is encoded once per clip (build_prompt_cache) and reused. The package streams itself
(generate_streaming): its AudioVAE decodes each generated patch as it comes, so no hooks here.
--compile runs torch.compile as the package does by default: slower to start, faster after.
Upstream lists about 8 GB of VRAM; measure it beside the brain before relying on it.
"""
import sys
import time
from pathlib import Path

import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import speech_server as ss

log = ss.log


class VoxCPM2Engine:
    streams = True
    builtin_voices: tuple[str, ...] = ()

    def __init__(self, model_id: str, device: str, compile_: bool, cfg: float, steps: int):
        from voxcpm import VoxCPM
        self.vox = VoxCPM.from_pretrained(model_id, load_denoiser=False, optimize=compile_, device=device)
        self.tts = self.vox.tts_model
        self.model, self.cfg, self.steps = model_id, cfg, steps
        self.sample_rate = int(self.tts.sample_rate)

    def prepare(self, clip: Path):
        words = ss.transcript(clip)
        return self.tts.build_prompt_cache(prompt_text=words, prompt_wav_path=str(clip), reference_wav_path=str(clip))

    def synth(self, text: str, voice, stream: bool):
        gen = self.tts._generate_with_prompt_cache(target_text=text, prompt_cache=voice, inference_timesteps=self.steps,
                                                   cfg_value=self.cfg, streaming=stream, retry_badcase=not stream)
        try:
            with torch.inference_mode():
                for wav, _, _ in gen:
                    yield wav.squeeze(0).float().cpu().numpy()
        finally:
            gen.close()


def main():
    ap = ss.arg_parser(__doc__.splitlines()[0])
    ap.add_argument("--model", default="openbmb/VoxCPM2", help="Hugging Face id or local dir")
    ap.add_argument("--compile", action="store_true", help="torch.compile the model (slow start)")
    ap.add_argument("--cfg", type=float, default=2.0, help="guidance scale, upstream's default")
    ap.add_argument("--steps", type=int, default=10, help="diffusion steps per patch, upstream's default")
    args = ap.parse_args()
    ss.setup_logging()
    t0 = time.perf_counter()
    engine = VoxCPM2Engine(args.model, args.device, args.compile, args.cfg, args.steps)
    log.info("model %s loaded on %s in %.1f s, %d Hz", args.model, args.device, time.perf_counter() - t0,
             engine.sample_rate)
    ss.serve(engine, args)


if __name__ == "__main__":
    main()
