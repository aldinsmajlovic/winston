"""Benchmark candidate: Gepard (nineninesix) behind the OpenAI speech protocol (../speech_server.py).

    .venv/bin/python server.py --voice ../../../tests/tts_bench/work/voices/vctk/p254_023.wav --port 8893
    .venv/bin/python server.py --checkpoint nineninesix/gepard-1.0 ...   # the 1.0 weights instead of 1.1

Gepard 1.1 (Apache-2.0 weights; the NVIDIA NanoCodec it decodes with is under the NVIDIA Open
Model License) is a 0.56B decoder-only model on a Qwen3.5 backbone, 22.05 kHz out. It clones from
the clip alone, no transcript: the clip (first 15 s) is encoded once into codec codes that a
compressor turns into a speaker prefix.

The reference runner (gepard-inference) returns every frame at the end, so this server hooks its
per-frame sampler to take each 32-channel frame as it is made and decodes the frames so far every
few frames with left context, holding back the last frames until what follows is known (the
NanoCodec decoder is not documented as causal). Upstream serves it with vLLM for speed; this is
the plain transformers loop, so its latency here is an upper bound.
"""
import functools
import sys
import time
from pathlib import Path

import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import speech_server as ss

log = ss.log


class GepardEngine:
    streams = True
    builtin_voices: tuple[str, ...] = ()

    def __init__(self, checkpoint: str, device: str, temperature: float, first: int, step: int, hold: int):
        from gepard_inference import GepardRunner, Player
        self.runner = GepardRunner.from_checkpoint(checkpoint, device=device, attn_implementation="sdpa")
        self.player = Player.from_checkpoint(checkpoint, device=device, max_ref_seconds=15.0)
        self.model, self.sample_rate = checkpoint, self.player.sample_rate
        self.gen = {"temperature": temperature, "top_k": 0, "stop_threshold": 0.5, "max_frames": 2000,
                    "repetition_penalty": 1.0, "repetition_window": 32}   # upstream config.yaml's defaults
        self.first, self.step, self.hold = first, step, hold

    def prepare(self, clip: Path):
        return self.player.encode_reference(str(clip))

    def _decode(self, frames: list) -> np.ndarray:
        return self.player.decode(torch.stack(frames).T.contiguous())[1]

    def synth(self, text: str, voice, stream: bool):
        if not stream:
            tokens = self.runner.generate(text, ref_codes=voice, **self.gen)
            yield self.player.decode(tokens)[1]
            return
        runner = self.runner

        def run(emit):
            sample = runner._sample_frame

            @functools.wraps(sample)
            def hooked(*a, **kw):
                frame = sample(*a, **kw)
                emit(frame.detach().clone())
                return frame

            runner._sample_frame = hooked
            try:
                runner.generate(text, ref_codes=voice, **self.gen)
            finally:
                del runner._sample_frame                             # back to the class method

        yield from ss.stream_frames(ss.frames_from(run), self._decode, first=self.first, step=self.step,
                                    left=16, hold=self.hold)


def main():
    ap = ss.arg_parser(__doc__.splitlines()[0])
    ap.add_argument("--checkpoint", default="nineninesix/gepard-1.1", help="Hugging Face id or local dir")
    ap.add_argument("--temperature", type=float, default=0.3, help="upstream's default")
    ap.add_argument("--first-frames", type=int, default=8, help="frames (46 ms each) before the first chunk")
    ap.add_argument("--chunk-frames", type=int, default=32, help="later chunks double up to this many frames")
    ap.add_argument("--hold-frames", type=int, default=3, help="frames held back until their right context is known")
    args = ap.parse_args()
    ss.setup_logging()
    t0 = time.perf_counter()
    engine = GepardEngine(args.checkpoint, args.device, args.temperature, args.first_frames, args.chunk_frames,
                          args.hold_frames)
    log.info("model %s loaded on %s in %.1f s, %d Hz", args.checkpoint, args.device, time.perf_counter() - t0,
             engine.sample_rate)
    ss.serve(engine, args)


if __name__ == "__main__":
    main()
