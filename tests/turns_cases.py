"""Turn record cases: a turn is written as one line and a FLAC file, old days are removed, and a
browser session's frames are cut into turns. Writes only to a temporary folder. No hardware needed.

    uv run python tests/turns_cases.py
"""
import asyncio
import json
import tempfile
import time
from datetime import date
from pathlib import Path

import numpy as np
import soundfile as sf
from harness import check, finish_checks, mismatches, scratch_data
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

scratch_data("winston-turns-")     # logging.latency and the other relative paths land here, not in data/

from winston.config.schema import WinstonConfig  # noqa: E402
from winston.core import turns as turns_mod  # noqa: E402
from winston.core.hints import Heard  # noqa: E402
from winston.core.turns import SpokenTurns, TurnLog, TurnStore, make_mark_tool  # noqa: E402


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
    while he speaks, not while busy (a state check reading)."""
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


def marks(folder: Path):
    """A marked turn is copied with its audio, outlives its day, and comes off again; ids and kinds are
    checked, so a request cannot reach outside the folder."""
    c = cfg(folder)
    log = TurnLog(c, "satellite")
    t = log.begin(room="main room", start="wake")
    t.add("Turn on the lamb.", heard("Turn on the lamb."))
    log.write_now(t)
    v = TurnLog(c, "voice")
    t2 = v.begin(start="speech")
    t2.add("Hello.", heard("Hello."))
    v.write_now(t2)
    store = TurnStore(c)
    today = date.today().isoformat()
    check("store: the day merges channels", [x["channel"] for x in store.day(today)], ["satellite", "voice"])
    rec = store.mark(t.id, "misheard", said="Turn on the lamp.", by="cli")
    check("mark: kept with what was said", (rec["mark"]["kind"], rec["mark"]["said"]), ("misheard", "Turn on the lamp."))
    check("mark: shown on its day", store.day(today)[0].get("mark", {}).get("kind"), "misheard")
    check("mark: audio copied", (store.marked_dir / f"{t.id}.flac").is_file(), True)
    import shutil
    shutil.rmtree(folder / today)                        # its day removed by pruning
    check("mark: outlives its day", (store.find(t.id) or {}).get("heard"), "Turn on the lamb.")
    check("mark: audio outlives its day", store.audio(t.id) is not None, True)
    check("mark: listed", [m["id"] for m in store.marked()], [t.id])
    check("unmark: gone", (store.unmark(t.id), store.find(t.id), store.marked()), (True, None, []))
    for bad, why in (("../../etc/x", "id"), (t2.id, "kind")):
        try:
            store.mark(bad, "misheard" if why == "id" else "rude")
            mismatches.append(f"mark: a bad {why} was taken")
        except ValueError:
            pass


async def voice_mark(folder: Path):
    """mark_mistake marks the turn before the one that asks, with what was said; off, it says why."""
    c = cfg(folder)
    log = TurnLog(c, "voice")
    t = log.begin(start="speech")
    t.add("Set a time for ten minutes.", heard("Set a time for ten minutes."))
    t.spoken.append("I'm not sure what you mean.")
    log.write_now(t)

    class Params:
        def __init__(self, arguments):
            self.arguments, self.result = arguments, None

        async def result_callback(self, r):
            self.result = r

    _, handler = make_mark_tool(c, lambda: t.id)
    p1 = Params({"kind": "misheard", "said": "Set a timer for ten minutes."})
    await handler(p1)
    check("voice: marked", (p1.result.get("marked"), TurnStore(c).find(t.id)["mark"]["said"], TurnStore(c).find(t.id)["mark"]["by"]),
          ("misheard", "Set a timer for ten minutes.", "voice"))
    p2 = Params({"kind": "not_for_you"})
    await handler(p2)
    check("voice: the model's word for not addressed", TurnStore(c).find(t.id)["mark"]["kind"], "not_addressed")
    _, off_handler = make_mark_tool(cfg(folder, enabled=False), lambda: t.id)
    p3 = Params({"kind": "misheard"})
    await off_handler(p3)
    check("voice: off says why", "not being kept" in p3.result.get("error", ""), True)


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
    marks(root / "marks")
    asyncio.run(voice_mark(root / "voice_mark"))
    off(root / "off")

finish_checks("turns: box and browser turns written, a reply over ends a turn, old days removed, marks kept and "
              "checked, off writes nothing")
