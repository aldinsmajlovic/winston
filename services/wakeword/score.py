"""Score WAV files against a wake word model the way the listener will hear them: 16 kHz, a 2 s
window sliding every 80 ms with silence before and after the clip, reporting the peak score.

    uv run python score.py <dir-of-wavs> [model.onnx]

Use it with clips from a voice the trainer never heard (Winston's own TTS is handy: POST
/v1/audio/speech with response_format wav) and with near-miss phrases, not just the target."""
import glob
import sys
from pathlib import Path

import librosa
import numpy as np
import soundfile as sf
from livekit.wakeword import WakeWordModel

CHUNK = 32000            # 2 s at 16 kHz, the model's window
HOP = 1280               # 80 ms, the listener's frame
DEFAULT = Path(__file__).resolve().parents[2] / "assets" / "wake" / "hey_winston.onnx"


def load_16k(path: str) -> np.ndarray:
    x, sr = sf.read(path, dtype="float32", always_2d=False)
    if x.ndim > 1:
        x = x.mean(axis=1)
    return librosa.resample(x, orig_sr=sr, target_sr=16000) if sr != 16000 else x


def peak(model: WakeWordModel, x: np.ndarray) -> float:
    x = np.concatenate([np.zeros(CHUNK, np.float32), x, np.zeros(8000, np.float32)])
    return max(next(iter(model.predict(x[s:s + CHUNK]).values()))
               for s in range(0, len(x) - CHUNK + 1, HOP))


if __name__ == "__main__":
    folder = sys.argv[1]
    model = WakeWordModel(models=[sys.argv[2] if len(sys.argv) > 2 else DEFAULT])
    for path in sorted(glob.glob(f"{folder}/*.wav")):
        print(f"  {Path(path).name:<36} {peak(model, load_16k(path)):.3f}")
