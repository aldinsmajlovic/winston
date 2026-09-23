"""Live feed cases: the history merges reply pieces and tool updates, tool labels read as
sentences, and the observer turns a typed turn into events. No hardware needed.

    uv run python tests/live_cases.py
"""
import asyncio
import sys

from pipecat.frames.frames import (FunctionCallFromLLM, FunctionCallResultFrame, FunctionCallsStartedFrame,
                                   LLMFullResponseEndFrame, LLMFullResponseStartFrame, LLMTextFrame)
from pipecat.observers.base_observer import FramePushed
from pipecat.processors.frame_processor import FrameDirection

from winston.core.live import Feed, LiveEvent, LiveObserver, tool_label

failed = []


def check(name, got, want):
    if got != want:
        failed.append(f"{name}: got {got!r}, want {want!r}")


LABELS = [
    ("intent__HassTurnOn", {"name": "Kitchen lights"}, "ok", "Turned on Kitchen lights"),
    ("intent__HassTurnOff", {"area": "Living Room", "domain": ["light"]}, "running", "Turning off Living Room lights"),
    ("light__HassLightSet", {"name": "living room lights", "brightness": 50}, "error", "Could not set living room lights"),
    ("homeassistant__GetLiveContext", {"area": "Living Room"}, "ok", "Checked the home"),
    ("web_search", {"query": "population of Sarajevo"}, "ok", "Searched the web for population of Sarajevo"),
    ("some_new_tool", {}, "ok", "Used some_new_tool"),
]
for name, args, status, want in LABELS:
    check(f"label {name}", tool_label(name, args, status), want)

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


async def observer_turn():
    events: list[LiveEvent] = []
    obs = LiveObserver("web", events.append)

    async def push(frame):
        for _ in range(2):                           # the same frame seen at two hops counts once
            await obs.on_push_frame(FramePushed(source=None, destination=None, frame=frame,
                                                direction=FrameDirection.DOWNSTREAM, timestamp=0))
    call = FunctionCallFromLLM(function_name="web_search", tool_call_id="c1",
                               arguments={"query": "capital of Norway"}, context=None)
    result = dict(function_name="web_search", tool_call_id="c1", arguments={"query": "capital of Norway"},
                  result={"answer": "Oslo"})
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

if failed:
    print("\n".join(failed)); sys.exit(1)
print(f"live: {len(LABELS)} labels, history and observer ok")
