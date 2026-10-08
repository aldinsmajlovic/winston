"""Winston TTS adapter: OpenAI-compatible /v1/audio/speech on top of Kyutai Pocket TTS, on the CPU.

Same protocol as the Chatterbox server (../speech_server.py, shared), so `tts.provider: openai_speech`
works unchanged. Pocket TTS streams audio while it generates (about 70 ms to the first chunk on a
desktop CPU, a quarter of real time on four threads), so each sentence starts playing before it is
finished.

    .venv/bin/python server.py --voice george --port 8880                  # a built-in voice
    .venv/bin/python server.py --voice /path/voices/vctk/p254_023.wav --voices-dir /path/voices

Built-in voices need nothing beyond the model download. Cloning from a clip needs the gated
weights: accept the terms at https://huggingface.co/kyutai/pocket-tts, then `uvx hf auth login`.
"""
import sys
import time
from pathlib import Path

import numpy as np
import torch
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import speech_server as ss

log = ss.log


class PocketEngine:
    model = "kyutai/pocket-tts"

    def __init__(self, threads: int):
        from pocket_tts import TTSModel
        from pocket_tts.models import tts_model
        torch.set_num_threads(threads)
        t0 = time.perf_counter()
        self.tts = TTSModel.load_model()
        self.tts.eval()
        self.sample_rate = self.tts.sample_rate
        self.builtin_voices = tuple(sorted(tts_model._ORIGINS_OF_PREDEFINED_VOICES))
        log.info("model loaded in %.1f s, sample_rate %d, %d built-in voices, %d threads",
                 time.perf_counter() - t0, self.sample_rate, len(self.builtin_voices), threads)

    def prepare(self, source: Path | str):
        try:
            return self.tts.get_state_for_audio_prompt(str(source))
        except ValueError as e:               # the cloning weights are gated behind Kyutai's terms
            raise HTTPException(400, str(e)) from e

    def stream_for(self, req: ss.SpeechRequest, on: bool) -> bool:
        return on

    def synth(self, text: str, state, stream: bool):
        # no torch.inference_mode() here: the model's own decode thread updates the (cloned) state in place,
        # and tensors cloned under inference mode cannot be updated outside it
        chunks = (c.detach().cpu().numpy().reshape(-1) for c in self.tts.generate_audio_stream(state, text))
        yield from chunks if stream else [np.concatenate([np.zeros(0, np.float32), *chunks])]


def main():
    ap = ss.arg_parser(__doc__.splitlines()[0], voice="george")
    ap.add_argument("--threads", type=int, default=4, help="CPU threads for generation")
    args = ap.parse_args()
    ss.setup_logging()
    ss.serve(PocketEngine(args.threads), args)


if __name__ == "__main__":
    main()
