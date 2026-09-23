"""Claim guard cases: pure checks on judge(), no hardware needed.

    uv run python tests/guard_cases.py
"""
import sys

from winston.core.guard import judge

ACT_OK = [("light__HassLightSet", True)]
ACT_FAILED = [("intent__HassTurnOn", False)]
READ_OK = [("homeassistant__GetLiveContext", True)]

CASES = [
    # (reply, tool calls this turn, expected verdict)
    ("Setting the living room lights to fifty percent brightness now.", [], "act"),
    ("I'll set the lights to warm.", [], "act"),
    ("Done.", [], "act"),
    ("The main room lights are now set to warm.", [], "act"),
    ("The main room lights are now set to warm.", ACT_FAILED, "act"),
    ("The main room lights are already on at one hundred percent.", [], "state"),
    ("The lights are currently on.", [], "state"),
    ("Living room lights at fifty percent, warm.", ACT_OK, None),
    ("The main room lights are now set to warm.", ACT_OK, None),
    ("The lights are now on.", READ_OK, None),                       # a state report after a read
    ("Your phone is currently at sixty four percent, read two hours ago.", READ_OK, None),
    ("It's ten past eight.", READ_OK, None),
    ("I couldn't find the living room lights. Could you tell me the exact name?", [], None),
    ("Would you like me to adjust the brightness or color temperature?", [], None),
    ("Hey Aldin. What can I help you with today?", [], None),
    ("Hello! I'm here to help you around the house.", [], None),
    ("I can't do that by voice.", [], None),
    ("Sweden has about 10.6 million people as of 2025.", [("web_search", True)], None),
    ("Done, the lights are warm.", [("web_search", True)], "act"),        # a search is not an action
    ("I've sent Omar a fun fact about octopuses.", [], "act"),
    ("Your message has been sent.", [], "act"),
    ("I sent Omar this: octopuses have three hearts.", [("send_message", True)], None),
    ("I couldn't message Omar, he doesn't share a server with me.", [("send_message", False)], None),
    ("Got it, I'll remember that you prefer warm light.", [], "act"),
    ("Noted.", [], "act"),
    ("I'll keep that in mind.", [], "act"),
    ("Got it, I'll remember that you prefer warm light.", [("remember", True)], None),
    ("I remember you prefer warm light, so I set it to warm.", ACT_OK, None),
    ("As I remember it, you like the lights warm in the evening.", [], None),
    # false alarms from 2026-09-23 (the turn before's calls are passed in by the guard, see below)
    ("Flip once, done.", [], None),
    ("Butter in the pan, medium-low heat, lid on for a couple minutes. Flip once, done.", [("ask_claude", True)], None),
    ("It's done, Maria's voice is cloned and I'm speaking in it now.", [("clone_voice", True)], None),
    ("It's done, Maria's voice is cloned.", [], "act"),
    ("That's done.", [], "act"),
]

failed = 0
for text, tools, expected in CASES:
    got = judge(text, tools)
    mark = "ok  " if got == expected else "FAIL"
    failed += got != expected
    print(f"{mark} {got!s:5} expected {expected!s:5}  {text!r} with {tools}")

# The observer carries the turn before's calls: an interjection ("What?", "Time for a charge") starts a
# new turn while the answer still rests on the calls just made.
import asyncio  # noqa: E402

from pipecat.frames.frames import FunctionCallResultFrame, UserStartedSpeakingFrame  # noqa: E402
from pipecat.observers.base_observer import FramePushed  # noqa: E402

from winston.core.guard import ToolTurnObserver  # noqa: E402


async def turns():
    obs = ToolTurnObserver()
    push = lambda f: obs.on_push_frame(FramePushed(source=None, destination=None, frame=f, direction=None, timestamp=0))
    await push(FunctionCallResultFrame(function_name="get_sensor_readings", tool_call_id="a", arguments={},
                                       result={"readings": ["58%"]}))
    await push(UserStartedSpeakingFrame())                   # "Time for a charge."
    one = judge("Fifty-eight percent is still decent.", obs.calls + obs.previous)
    await push(UserStartedSpeakingFrame())                   # two turns on, the reading no longer backs it
    two = judge("Fifty-eight percent is still decent.", obs.calls + obs.previous)
    return one, two

got = asyncio.run(turns())
for g, e, what in zip(got, (None, "state"), ("one turn after the call", "two turns after the call")):
    mark = "ok  " if g == e else "FAIL"
    failed += g != e
    print(f"{mark} {g!s:5} expected {e!s:5}  a state claim {what}")
print(f"\n{len(CASES) + 2 - failed}/{len(CASES) + 2} passed")
sys.exit(1 if failed else 0)
