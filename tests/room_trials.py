"""Room trials: does the room in the time stamp ([06:22, room: Main Room]) make "the lights" mean that
room's lights? Typed turns with thinking off, as on the spoken channels, against the real LLM and
Home Assistant's real tool list; actions are stubbed (tests/conversations.py), so nothing switches.
Each trial is a fresh conversation. Needs the LLM (:8000).

    uv run python tests/room_trials.py [--n 4] [--room "main room"]

Prints each trial's calls and a count per case. A comparison, run on purpose like `winston check brain`."""
import argparse
import asyncio
import os
import sys
import tempfile
from pathlib import Path

from loguru import logger

sys.path.insert(0, str(Path(__file__).parent))
os.environ["WINSTON_THINKING"] = "0"
from conversations import ACT_STUBS, stub
from winston.channels.text import TextSession
from winston.config import load
from winston.core.rooms import area

logger.remove(); logger.add(sys.stderr, level="WARNING")


def targets(calls: list[tuple[str, dict]]) -> list[str]:
    """Each action's target: its area, else its name ("Main room lights" is the room's group)."""
    return [(a.get("area") or a.get("name") or "?").strip() for _, a in calls]


async def trial(cfg, place: str | None, said: str) -> tuple[list[tuple[str, dict]], str]:
    calls: list[tuple[str, dict]] = []
    s = TextSession(cfg, mentions=False, channel="room-trial", area=place)
    await s.start()
    for t in s.brain.tools.standard_tools:
        if t.name in ACT_STUBS:
            inner = stub(t.name)

            async def spy(params, _name=t.name, _inner=inner):
                calls.append((_name.split("__")[-1], dict(params.arguments)))    # actions only
                await _inner(params)
            s.brain.llm.register_function(t.name, spy)
    try:
        reply = await s.ask(said)
    finally:
        await s.close()
    return calls, reply


async def main(n: int, room: str) -> int:
    cfg = load()
    cfg.logging.tool_calls = str(Path(tempfile.gettempdir()) / "winston-room-trials.jsonl")   # not data/logs
    place = area(cfg, room)
    cases = [   # (room or None, what is said, the target wanted, how many of n must hit it)
        (place, "Turn off the lights.", place),
        (place, "Dim the lights to 30 percent.", place),
        (place, "Turn on the kitchen lights.", "kitchen"),
        (None, "Turn off the lights.", None),
    ]
    failed = 0
    for where, said, want in cases:
        hits = 0
        print(f"\n{said!r} in {where or 'no room'}:")
        for _ in range(n):
            calls, reply = await trial(cfg, where, said)
            got = targets(calls)
            ok = want is None or any(want.lower() in g.lower() for g in got)
            hits += ok
            print(f"  {'ok ' if ok else 'NO '} {[(c, a) for c, a in calls]} -> {reply[:90]!r}")
        print(f"  {hits}/{n}" + ("" if want else " (baseline, no target asked)"))
        failed += want is not None and hits < n
    return 1 if failed else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=4)
    ap.add_argument("--room", default="main room")
    a = ap.parse_args()
    sys.exit(asyncio.run(main(a.n, a.room)))
