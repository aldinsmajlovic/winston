"""Listen on the default microphone and print each "Hey Winston" detection with its score.

    uv sync --extra mic                      # once; needs portaudio on the box
    uv run python listen.py [model.onnx] [--threshold 0.5]

Use it to tune the threshold in the room the satellite will live in: raise it if it fires on
its own, lower it if it misses you from across the room."""
import argparse
import asyncio
from pathlib import Path

from livekit.wakeword import WakeWordListener, WakeWordModel

DEFAULT = Path(__file__).resolve().parents[2] / "assets" / "wake" / "hey_winston.onnx"


async def main(model_path: Path, threshold: float) -> None:
    model = WakeWordModel(models=[model_path])
    print(f"listening with {model_path.name} at threshold {threshold} (Ctrl-C to stop)")
    async with WakeWordListener(model, threshold=threshold, debounce=1.5) as listener:
        while True:
            d = await listener.wait_for_detection()
            print(f"  {d.name}  {d.confidence:.2f}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("model", nargs="?", default=str(DEFAULT))
    p.add_argument("--threshold", type=float, default=0.5)
    a = p.parse_args()
    try:
        asyncio.run(main(Path(a.model), a.threshold))
    except KeyboardInterrupt:
        pass
