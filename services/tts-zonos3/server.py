"""Winston TTS engine: ZONOS3-Mini behind the OpenAI speech protocol (../speech_server.py).

    git clone https://github.com/Zyphra/ZONOS3-Mini upstream && git -C upstream checkout a50e14117d99
    git -C upstream submodule update --init third_party/fusealign-multilingual
    (cd upstream && UV_PROJECT_ENVIRONMENT=../.venv uv sync --locked --package zonos3-sgl --no-dev)
    .venv/bin/python server.py --voice /path/voices/vctk/p254_023.wav --voices-dir /path/voices --port 8880

ZONOS3-Mini (Zyphra; 800M mixture of experts, about 100M active; Apache-2.0 weights, MIT code) clones
from a clip alone, no words needed, and speaks 44.1 kHz. The venv is upstream's own uv lock, installed
for its Mini-SGLang CUDA server package (zonos3-sgl); this engine runs that package's offline API
(TTSLLM) in-process, with the serve.sh settings, so there is no second server and no IPC in the way.

The clip is encoded once per voice into the codec's frames and goes in front of every sentence as its
raw reference prefix: upstream's server does that for an uploaded clip (its speaker embedding is the
other way). TTSLLM.generate_stream streams the server's way (silence prefill, the codec's prefix
decode) but only with an embedding, so its queueing is wrapped to add the clip's frames. Chunks get
the server's output stage (the decoder's headroom gain, then the soft limiter).

Two of upstream's exact-mode kernels are Hopper-only; below sm_90 (the 3090) its env.sh switches them
to the portable Triton ones, and so does this. Two small kernels (KV store, embedding lookup) are
built by nvcc on first start: the CUDA 13 nvcc on the PATH builds them for sm_86 (upstream names
12.8). It has no tags, so Chatterbox's "[chuckle]" and the like are left out.
"""
import os
import re
import sys
import time
from pathlib import Path

import numpy as np
import torch

here = Path(__file__).resolve().parent
sys.path.insert(0, str(here.parent))
import speech_server as ss

log = ss.log
MODEL = "hf://Zyphra/ZONOS3-Mini/lm/zonos3_mini"
TEXT_ROWS = MODEL + "/text_rows/pause_tokens_s5000.pt"   # serve.sh's phoneme, start and pause rows
_TAG = re.compile(r"\[[a-z][a-z ]*\]")


def exact_mode_env() -> None:
    """upstream's servers/sgl/scripts/env.sh, before its modules read it; a value set beforehand wins."""
    hopper = "1" if torch.cuda.get_device_capability()[0] >= 9 else "0"
    for key, value in {"FLA_TILELANG": hopper, "ZONOS3_USE_TRAINING_SONIC_EXPERTS": hopper,
                       "ZONOS3_USE_TRAINING_RMSNORM": "1", "ZONOS3_USE_TRAINING_ROUTER_WEIGHTS": "1",
                       "ZONOS3_USE_TRAINING_ATTNRES": "1", "ZONOS3_USE_TRAINING_ROPE": "1",
                       "OMP_NUM_THREADS": "1", "TILELANG_CACHE_DIR": str(here / "upstream" / ".cache" / "tilelang")
                       }.items():
        os.environ.setdefault(key, value)


class Zonos3Engine:
    builtin_voices: tuple[str, ...] = ()

    def __init__(self, model: str, attention: str, memory_ratio: float, seed: int, language: str):
        from zonos3.generation import SAMPLE_RATE
        from zonos3.tokenizer.matched_codec import decoder_headroom_db
        from zonos3_sgl.message.tts import TTSSamplingParams
        from zonos3_sgl.tts.llm import TTSLLM

        # serve.sh's settings
        self.tts = TTSLLM(model_path=model, attention_backend=attention, cache_type="naive",
                          memory_ratio=memory_ratio, max_running_req=2, cuda_graph_max_bs=2, max_extend_tokens=256,
                          phoneme_rows=TEXT_ROWS if model == MODEL else None)
        self._codes = None
        enqueue = self.tts._enqueue
        self.tts._enqueue = lambda *a, **kw: enqueue(*a, **{**kw, "speaker_reference_codes": self._codes})
        self.gain = 10.0 ** (-decoder_headroom_db(self.tts.engine.model_config.audio_codec) / 20.0)
        self.params = TTSSamplingParams(seed=seed)
        self._stream = torch.cuda.current_stream()   # what the scheduler set, for this thread only
        self.model, self.language, self.sample_rate = model, language, SAMPLE_RATE

    def prepare(self, clip: Path):
        a, sr = ss.read_clip(clip)
        return self.tts.encode_reference(torch.from_numpy(a)[None], sr)

    def stream_for(self, req: ss.SpeechRequest, on: bool) -> bool:
        return on

    def synth(self, text: str, codes, stream: bool):
        from zonos3.tts.audio import output_pcm

        torch.cuda.set_stream(self._stream)          # requests come on other threads
        said = " ".join(_TAG.sub(" ", text).split()) or text
        self._codes = codes
        chunks = (output_pcm(c, self.gain) for c in
                  self.tts.generate_stream(said, self.params, language=self.language))
        if stream:
            yield from chunks
        else:
            yield np.concatenate(list(chunks))


def main():
    ap = ss.arg_parser(__doc__.splitlines()[0])
    ap.add_argument("--model", default=MODEL, help="checkpoint dir or hf:// location")
    ap.add_argument("--attention", default="fa", choices=("fa", "fi"),
                    help="fa: sgl-kernel's FlashAttention 3 (serve.sh's; built for sm_80, sm_86, sm_90a); fi: FlashInfer")
    ap.add_argument("--memory-ratio", type=float, default=0.5,
                    help="share of the card's free memory the model and its KV cache may take (serve.sh's 0.5)")
    ap.add_argument("--seed", type=int, default=42, help="sampling seed per sentence (the server draws one)")
    ap.add_argument("--language", default="en_us", help="upstream's language code, for text normalization")
    args = ap.parse_args()
    ss.setup_logging()
    exact_mode_env()
    t0 = time.perf_counter()
    engine = Zonos3Engine(args.model, args.attention, args.memory_ratio, args.seed, args.language)
    log.info("model %s loaded in %.1f s, %d Hz, attention %s", args.model, time.perf_counter() - t0,
             engine.sample_rate, args.attention)
    ss.serve(engine, args)


if __name__ == "__main__":
    main()
