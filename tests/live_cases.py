"""Live feed cases: the history merges reply pieces and tool updates, tool labels read as
sentences, and the observer turns a typed turn into events. No hardware needed.

    uv run python tests/live_cases.py
"""
import asyncio

from harness import check, finish_checks
from pipecat.frames.frames import (FunctionCallFromLLM, FunctionCallResultFrame, FunctionCallsStartedFrame,
                                   LLMFullResponseEndFrame, LLMFullResponseStartFrame, LLMTextFrame)
from pipecat.observers.base_observer import FramePushed
from pipecat.processors.frame_processor import FrameDirection

from winston.core.live import Feed, LiveEvent, LiveObserver, know_extension_tools, tool_icon, tool_label

LABELS = [
    ("intent__HassTurnOn", {"name": "Kitchen lights"}, "ok", "Turned on Kitchen lights"),
    ("intent__HassTurnOff", {"area": "Living Room", "domain": ["light"]}, "running", "Turning off Living Room lights"),
    ("light__HassLightSet", {"name": "living room lights", "brightness": 50}, "error", "Couldn't set living room lights"),
    ("intent__HassTurnOn", {"area": "Hall", "domain": "switch"}, "ok", "Turned on Hall switches"),
    ("intent__HassTurnOff", {"area": "Hall", "domain": ["media_player"]}, "ok", "Turned off Hall media players"),
    ("homeassistant__GetLiveContext", {"area": "Living Room"}, "ok", "Checked the home"),
    ("forget", {"fact": "Omar visits on Fridays"}, "ok", "Forgot Omar visits on Fridays"),
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

finish_checks(f"live: {len(LABELS)} labels, history and observer ok")
