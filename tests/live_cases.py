"""Live feed cases: the history merges reply pieces and tool updates, tool labels read as
sentences, the observer turns a typed turn into events, and his voice's level goes out as it plays.
No hardware needed.

    uv run python tests/live_cases.py
"""
import asyncio
import time

import numpy as np

from harness import check, finish_checks
from pipecat.frames.frames import (FunctionCallFromLLM, FunctionCallResultFrame, FunctionCallsStartedFrame,
                                   InterruptionFrame, LLMFullResponseEndFrame, LLMFullResponseStartFrame, LLMTextFrame,
                                   TTSAudioRawFrame)
from pipecat.observers.base_observer import FramePushed
from pipecat.processors.frame_processor import FrameDirection

from winston.core.live import Feed, LiveEvent, LiveObserver, know_extension_tools, loudness, tool_icon, tool_label

LABELS = [
    ("intent__HassTurnOn", {"name": "Kitchen lights"}, "ok", "Turned on Kitchen lights"),
    ("intent__HassTurnOff", {"area": "Living Room", "domain": ["light"]}, "running", "Turning off Living Room lights"),
    ("light__HassLightSet", {"name": "living room lights", "brightness": 50}, "error", "Couldn't set living room lights"),
    ("intent__HassTurnOn", {"area": "Hall", "domain": "switch"}, "ok", "Turned on Hall switches"),
    ("intent__HassTurnOff", {"area": "Hall", "domain": ["media_player"]}, "ok", "Turned off Hall media players"),
    ("homeassistant__GetLiveContext", {"area": "Living Room"}, "ok", "Checked the home"),
    ("forget", {"fact": "Omar visits on Fridays"}, "ok", "Forgot Omar visits on Fridays"),
    ("light__HassLightSet", {"name": "Desk lamp", "brightness": 50}, "ok", "Changed Desk lamp"),
    ("some_new_tool", {}, "ok", "Some new tool"),
]
for name, args, status, want in LABELS:
    check(f"label {name}", tool_label(name, args, status), want)
# An extension's tool, by its title (its extension's name when it has none) and first text argument, with its icon.
know_extension_tools([{"name": "web_search", "extension": "web", "title": "Web search", "icon": "search"}])
check("label of an extension's tool", tool_label("web_search", {"query": "population of Sarajevo"}, "ok"),
      "Web search: population of Sarajevo")
check("an extension's tool failing", tool_label("web_search", {"query": "x"}, "error"), "Couldn't web search")
check("its icon", tool_icon("web_search"), "search")
# Done, a title led by a verb is in the past tense; one led by anything else stays as it is.
know_extension_tools([{"name": "rem_add", "extension": "reminders", "tool_title": "Add a reminder"},
                      {"name": "news_read", "extension": "news", "tool_title": "Read the news"},
                      {"name": "music_skip", "extension": "music", "tool_title": "Skip to the next song"},
                      {"name": "later_list", "extension": "later", "tool_title": "List what is waiting"},
                      {"name": "pl_empty", "extension": "pl", "tool_title": "Empty the queue"}])
for name, want in (("rem_add", "Added a reminder: bread"), ("news_read", "Read the news: bread"),
                   ("music_skip", "Skipped to the next song: bread"), ("later_list", "Listed what is waiting: bread"),
                   ("pl_empty", "Emptied the queue: bread")):
    check(f"past: {want}", tool_label(name, {"what": "bread"}, "ok"), want)
check("running, as its title", tool_label("rem_add", {"what": "bread"}, "running"), "Add a reminder: bread")

# History: pieces of one reply become one event; a tool's result updates its running event.
feed = Feed(keep=10)
q = feed.subscribe()
for text in ("About ", "275,000", "."):
    feed.publish(LiveEvent(channel="web", type="reply", reply="r1", text=text))
feed.publish(LiveEvent(channel="web", type="reply_end", reply="r1"))
feed.publish(LiveEvent(channel="web", type="tool", call="c1", name="web_search", status="running", label="Searching"))
feed.publish(LiveEvent(channel="web", type="tool", call="c1", name="web_search", status="ok", label="Searched", ms=1400))
feed.publish(LiveEvent(channel="web", type="state", state="idle"))
hist = feed.backlog()
check("history types", [e.type for e in hist], ["reply", "tool", "state"])
check("merged reply", hist[0].text, "About 275,000.")
check("tool updated", (hist[1].status, hist[1].label, hist[1].ms), ("ok", "Searched", 1400))
check("subscriber sees pieces", q.qsize(), 7)
for i in range(30):                                  # old ids are forgotten with their events
    feed.publish(LiveEvent(channel="web", type="reply", reply=f"x{i}", text="hi"))
check("history bounded", len(feed.backlog()), 11)

# A kept turn's id goes onto the channel's latest user line, and only once.
feed = Feed(keep=20)
feed.publish(LiveEvent(channel="satellite", type="user", text="What time is it?"))
feed.publish(LiveEvent(channel="voice", type="user", text="Hello."))
feed.publish(LiveEvent(channel="satellite", type="turn", call="aaaaaaaaaaaa", text="What time is it?"))
feed.publish(LiveEvent(channel="satellite", type="turn", call="bbbbbbbbbbbb", text=""))   # nothing heard: no line
users = [e for e in feed.backlog() if e.type == "user"]
check("turn id on its line", [(e.channel, e.call) for e in users], [("satellite", "aaaaaaaaaaaa"), ("voice", None)])


async def observer_turn():
    events: list[LiveEvent] = []
    obs = LiveObserver("web", events.append)

    async def push(frame):
        for _ in range(2):                           # the same frame seen at two hops counts once
            await obs.on_push_frame(FramePushed(source=None, destination=None, frame=frame,
                                                direction=FrameDirection.DOWNSTREAM, timestamp=0))
    call = FunctionCallFromLLM(function_name="web_search", tool_call_id="c1",
                               arguments={"query": "capital of Norway"}, context=None)
    result = {"function_name": "web_search", "tool_call_id": "c1", "arguments": {"query": "capital of Norway"},
              "result": {"answer": "Oslo"}}
    for frame in (LLMFullResponseStartFrame(), FunctionCallsStartedFrame(function_calls=[call]),
                  FunctionCallsStartedFrame(function_calls=[call]),     # the upstream copy
                  LLMFullResponseEndFrame(), FunctionCallResultFrame(**result),
                  FunctionCallResultFrame(**result),
                  LLMFullResponseStartFrame(), LLMTextFrame("\n\nIt is"), LLMTextFrame(" Oslo."),
                  LLMFullResponseEndFrame()):
        await push(frame)
    return events

ev = asyncio.run(observer_turn())
check("observer events", [(e.type, e.state or e.status or e.text) for e in ev], [
    ("state", "thinking"), ("tool", "running"), ("tool", "ok"), ("state", "thinking"),
    ("reply", "It is"), ("reply", " Oslo."), ("reply_end", None), ("state", "idle")])

# His voice's level: streamed to the page, never kept; 0 for silence, 1 from -10 dBFS up.
RATE = 24000


def tone(secs: float, dbfs: float) -> bytes:
    t = np.arange(int(RATE * secs)) / RATE
    return (np.sin(2 * np.pi * 220 * t) * 32767 * 10 ** (dbfs / 20) * np.sqrt(2)).astype("<i2").tobytes()


check("silence", loudness(bytes(4800)), 0.0)
check("loud speech", loudness(tone(0.05, -6)), 1.0)
check("halfway", round(loudness(tone(0.05, -30)), 2), 0.5)
feed = Feed(keep=10)
q = feed.subscribe()
feed.publish(LiveEvent(channel="satellite", type="level", loudness=0.4, room="Kitchen"))
check("level streamed", q.qsize(), 1)
check("level not kept", feed.backlog(), [])


async def spoken(plays_after: float | None, cut: bool = False):
    """0.3 s of his voice in three pieces, arriving at once as TTS audio does, then the reply finished."""
    events: list[tuple[float, LiveEvent]] = []
    t0 = time.perf_counter()
    obs = LiveObserver("satellite", lambda e: events.append((time.perf_counter() - t0, e)), spoken=True,
                       room="Kitchen", plays_after=plays_after)

    async def push(frame):
        await obs.on_push_frame(FramePushed(source=None, destination=None, frame=frame,
                                            direction=FrameDirection.DOWNSTREAM, timestamp=0))
    for dbfs in (-30, -10, -30):
        await push(TTSAudioRawFrame(tone(0.1, dbfs), RATE, 1))
    if cut:
        await asyncio.sleep(0.15)
        await push(InterruptionFrame())
    obs.finished()
    await asyncio.sleep(0.6)
    return events


box = asyncio.run(spoken(0.1))
kinds = [e.state or e.type for _, e in box]
levels = [e.loudness for _, e in box if e.type == "level"]
check("a box: speaking as it starts, idle once played", (kinds[0], kinds[-1]), ("speaking", "idle"))
check("a box: a level every 50 ms of it", len(levels) in (5, 6, 7), True)
check("a box: the loud middle is louder", max(levels[2:4]) > max(levels[0], levels[-1]), True)
check("a box: the first level once it plays", 0.09 < box[0][0] < 0.16, True)
check("a box: idle when it has all played", 0.38 < box[-1][0] < 0.5, True)
check("a box: every level in its room", {e.room for _, e in box}, {"Kitchen"})
voice = asyncio.run(spoken(None))
check("a transport: levels only, it says speaking itself", {e.type for _, e in voice}, {"level"})
cut = asyncio.run(spoken(0.0, cut=True))
check("cut short: nothing after", max(t for t, _ in cut) < 0.2, True)

finish_checks(f"live: {len(LABELS)} labels, history, observer and his voice's level ok")
