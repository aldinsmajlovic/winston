"""Turn record cases: a turn is written as one line and a FLAC file, old days are removed, and a
browser session's frames are cut into turns. Writes only to a temporary folder. No hardware needed.

    uv run python tests/turns_cases.py
"""
import asyncio
import json
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

import numpy as np
import soundfile as sf
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    FunctionCallFromLLM,
    FunctionCallResultFrame,
    FunctionCallsStartedFrame,
    LLMFullResponseEndFrame,
    LLMTextFrame,
    TranscriptionFrame,
    TTSTextFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.observers.base_observer import FramePushed
from pipecat.processors.frame_processor import FrameDirection

from winston.config.schema import WinstonConfig
from winston.core import turns as turns_mod
from winston.core.hints import Heard
from winston.core.turns import SpokenTurns, TurnLog

failed = []


def check(name, got, want):
    if got != want:
        failed.append(f"{name}: got {got!r}, want {want!r}")


def cfg(folder: Path, enabled: bool = True) -> WinstonConfig:
    return WinstonConfig.model_validate({"llm": {"model": "x"},
                                         "logging": {"turns": {"enabled": enabled, "dir": str(folder), "keep_days": 3}}})


def tone(secs: float, rate: int = 16000) -> np.ndarray:
    t = np.arange(int(secs * rate)) / rate
    return (np.sin(2 * np.pi * 220 * t) * 8000).astype(np.int16)


def heard(text: str, raw: str | None = None, secs: float = 1.0) -> Heard:
    return Heard(text, raw or text, [], secs, 200, "parakeet/test", tone(secs), 16000)


def lines(folder: Path, channel: str) -> list[dict]:
    out = []
    for f in sorted(folder.glob(f"*/{channel}.jsonl")):
        out += [json.loads(x) for x in f.read_text().splitlines()]
    return out


class Gate:
    may_drop_speech = True


async def box_turns(folder: Path):
    """What the satellite channel does around one request, without a box."""
    log = TurnLog(cfg(folder), "satellite")
    t = log.begin(room="main room", start="wake")
    t.add("Turn on the lamp.", heard("Turn on the lamp.", "Turn on the lamb.", 1.5))
    t.tools.append({"name": "HassTurnOn", "args": {"name": "lamp"}, "ok": True, "result": "{}"})
    t.spoken.append("Done, sir.")
    log.write_now(t)
    t2 = log.begin(room="main room", start="follow_up")
    t2.add("", heard("", secs=0.8))
    t2.outcome = "nothing_heard"
    log.write_now(t2)
    t3 = log.begin(room="main room", start="follow_up")
    t3.add("Hmm.", heard("Hmm."))
    log.write_now(t3)
    got = lines(folder, "satellite")
    check("box: three lines", len(got), 3)
    first = got[0]
    check("box: fields", {k: first[k] for k in ("channel", "room", "start", "heard", "raw", "said", "outcome", "secs")},
          {"channel": "satellite", "room": "main room", "start": "wake", "heard": "Turn on the lamp.",
           "raw": "Turn on the lamb.", "said": "Done, sir.", "outcome": "answered", "secs": 1.5})
    check("box: tool kept", first["tools"][0]["name"], "HassTurnOn")
    pcm, rate = sf.read(folder / date.today().isoformat() / first["audio"], dtype="int16")
    check("box: audio round trip", (rate, len(pcm)), (16000, 24000))
    check("box: nothing heard", (got[1]["outcome"], got[1]["heard"], "raw" in got[1]), ("nothing_heard", "", False))
    check("box: no reply and no tool is silent", got[2]["outcome"], "silent")


async def browser_turns(folder: Path):
    log = TurnLog(cfg(folder), "voice")
    turns = SpokenTurns(log, room="study")

    async def push(frame, destination=None, hops=2):
        for _ in range(hops):                        # the same frame seen at two hops counts once
            await turns.on_push_frame(FramePushed(source=None, destination=destination, frame=frame,
                                                  direction=FrameDirection.DOWNSTREAM, timestamp=0))

    def words(text: str, h: Heard | None = None) -> TranscriptionFrame:
        return TranscriptionFrame(text, "u", "now", result=h)

    stop = VADUserStoppedSpeakingFrame(stop_secs=0.3)
    stop.timestamp = time.time()
    await push(stop)
    await push(words("What's the", heard("What's the", secs=0.6)))
    await push(words("time?", heard("time?", secs=0.4)))                     # a pause inside one request
    call = FunctionCallFromLLM(function_name="GetDateTime", tool_call_id="c1", arguments={}, context=None)
    await push(FunctionCallsStartedFrame(function_calls=[call]))
    await push(FunctionCallResultFrame(function_name="GetDateTime", tool_call_id="c1", arguments={},
                                       result={"time": "21:04"}))
    await push(LLMTextFrame("It is"))
    await push(TTSTextFrame("It is four past nine.", aggregated_by="sentence"))
    await push(words("Ignored.", heard("Ignored.")), destination=Gate(), hops=1)   # dropped by the mic gate
    await push(words("Thanks.", heard("Thanks.")))                              # the next turn
    turns.flush()                                                               # the session ends
    await asyncio.gather(*log._writing)
    got = lines(folder, "voice")
    check("browser: two turns", [g["heard"] for g in got], ["What's the time?", "Thanks."])
    first = got[0]
    check("browser: room and start", (first["room"], first["start"]), ("study", "speech"))
    check("browser: both pieces' audio", first["secs"], 1.0)
    check("browser: tool and result", (first["tools"][0]["name"], first["tools"][0]["ok"]), ("GetDateTime", True))
    check("browser: said", first["said"], "It is four past nine.")
    check("browser: the gate's words are not a turn", any("Ignored" in g["heard"] for g in got), False)
    check("browser: a turn with no reply is silent", got[1]["outcome"], "silent")


async def reply_over(folder: Path):
    """A turn is written once his reply is over, however long a tool takes: not while a tool runs, not
    while he speaks, not while busy (the guard retrying, a state check reading)."""
    turns_mod.SETTLE_SECS = 0.05
    log = TurnLog(cfg(folder), "voice")
    busy = [False]
    turns = SpokenTurns(log, busy=lambda: busy[0])

    async def push(frame):
        await turns.on_push_frame(FramePushed(source=None, destination=None, frame=frame,
                                              direction=FrameDirection.DOWNSTREAM, timestamp=0))

    async def written() -> int:
        await asyncio.sleep(0.3)                     # several SETTLE_SECS
        await asyncio.gather(*log._writing)
        return len(lines(folder, "voice"))

    await push(TranscriptionFrame("Ask Claude about tides.", "u", "now", result=heard("Ask Claude about tides.")))
    call = FunctionCallFromLLM(function_name="ask_claude", tool_call_id="c9", arguments={}, context=None)
    await push(FunctionCallsStartedFrame(function_calls=[call]))
    await push(LLMFullResponseEndFrame())
    check("over: open while the tool runs", await written(), 0)
    await push(FunctionCallResultFrame(function_name="ask_claude", tool_call_id="c9", arguments={}, result="Tides..."))
    await push(BotStartedSpeakingFrame())
    await push(TTSTextFrame("Tides come from the moon.", aggregated_by="sentence"))
    await push(LLMFullResponseEndFrame())
    check("over: open while he speaks", await written(), 0)
    busy[0] = True
    await push(BotStoppedSpeakingFrame())
    check("over: open while busy", await written(), 0)
    busy[0] = False
    check("over: written once it is over", await written(), 1)
    check("over: what he said", lines(folder, "voice")[0]["said"], "Tides come from the moon.")


def prune(folder: Path):
    log = TurnLog(cfg(folder), "voice")
    for d in ("2026-10-01", "2026-10-04", "2026-10-05", "2026-10-07", "marked"):
        (folder / d).mkdir(parents=True, exist_ok=True)
    log.prune(today=date(2026, 10, 7))
    check("prune: three days kept, other folders stay", sorted(p.name for p in folder.iterdir()),
          ["2026-10-05", "2026-10-07", "marked"])             # today and the two days before


def off(folder: Path):
    log = TurnLog(cfg(folder, enabled=False), "voice")
    turns = SpokenTurns(log)

    async def run():
        await turns.on_push_frame(FramePushed(source=None, destination=None, frame=TranscriptionFrame("Hi.", "u", "now"),
                                              direction=FrameDirection.DOWNSTREAM, timestamp=0))
        turns.flush()
    asyncio.run(run())
    check("off: nothing written", list(folder.iterdir()) if folder.exists() else [], [])


with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    asyncio.run(box_turns(root / "box"))
    asyncio.run(browser_turns(root / "browser"))
    asyncio.run(reply_over(root / "over"))
    prune(root / "prune")
    off(root / "off")

if failed:
    print("\n".join(failed)); sys.exit(1)
print("turns: box and browser turns written, a reply over ends a turn, old days removed, off writes nothing")
