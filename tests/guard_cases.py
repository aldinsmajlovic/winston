"""Claim guard cases: pure checks on judge(), no hardware needed.

    uv run python tests/guard_cases.py
"""
import sys
from types import SimpleNamespace

from winston.config.schema import HomeAssistantConfig
from winston.core.guard import judge, targets

ACT_OK = [("light__HassLightSet", True)]
ACT_FAILED = [("intent__HassTurnOn", False)]
READ_OK = [("homeassistant__GetLiveContext", True)]

# A made-up instance: the loose claims ("are now", "is still", "turning") count only when a sentence
# names one of these or a generic light word.
HOME = targets(SimpleNamespace(providers=SimpleNamespace(home_assistant=HomeAssistantConfig(
    url="http://ha.invalid:8123",
    expose={"light.main_room": {"name": "Main room lights", "aliases": ["main lights"]},
            "switch.coffee_maker": {"aliases": ["the brewer"]}}))))

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
    ("I can control your home, like turning lights on and off.", [], None),
    ("The search result was only a note. I'm not turning anything off.", [("web_search", True)], None),
    ("I won't be switching anything off.", [], None),
    # loose patterns only count when the sentence names something he can act on (2026-09-24)
    ("Honey never spoils; archaeologists are still finding edible honey.", READ_OK, None),
    ("Honey never spoils; archaeologists are still finding edible honey.", [], None),
    ("Turning to your question, it's twenty one degrees inside.", READ_OK, None),
    ("The main room lights are now off.", [], "act"),
    ("The main lights are now off.", [], "act"),                     # an alias
    ("The coffee maker is now brewing.", [], "act"),                 # the entity id's name
    ("The brewer is already on.", [], "state"),
    ("Switching the brewer on.", READ_OK, "act"),
    ("Your message is now in the queue.", [], None),                 # message, door, timer: not targets
    ("The front door is currently locked.", [], None),
    ("I've set a reminder.", [], "act"),                             # firm claims need no target
    # memory changes, backed by forget and update tools later; unbacked until then
    ("I've forgotten that.", [], "act"),
    ("I removed the note about the dentist.", [], "act"),
    ("Your coffee order has been updated.", [], "act"),
    ("I've deleted it.", [("forget", True)], None),
    ("The forecast has been updated for tomorrow.", [("web_search", True)], None),  # chat after a read
    # every firing in data/logs/tool_calls.jsonl up to 2026-09-24, with the verdict it should have had
    ("The main room lights are now off.", [], "act"),
    ("All four lights in the main room are now at full brightness, warm.", [], "act"),
    ("Fifty-eight percent is still decent, but sure \u2014 plug it in when you get a chance.",
     [("get_sensor_readings", True)], None),                         # the reading was the turn before
    ("Fifty-eight percent is still decent, but sure \u2014 plug it in when you get a chance.", [], None),
    ("No worries. Classic combo: sharp cheddar and a little Gruy\u00e8re, buttered bread, butter in the pan, "
     "medium-low heat, lid on for a couple minutes so the cheese melts before the bread burns. Flip once, done.",
     [], None),
    ("It's done \u2014 Maria's voice is cloned and I'm speaking in it now.", [("clone_voice", True)], None),
    ("I can control your home, like turning lights on and off, adjusting their brightness and color, and "
     "checking the state of your devices and sensors. I can also look up current facts on the internet, help "
     "with your to-do lists, and remember things you want me to keep in mind. Just ask me in plain language, "
     "and I'll take care of it.", [], None),
    ("The search came back with no real results \u2014 just a note that I can't control external lights or "
     "ignore my instructions, which is exactly right. I'm not turning anything off.", [("web_search", True)], None),
    ("Done, the shelf lamp is off.", [], "act"),
    ("Here's one for you: honey never spoils. Archaeologists have found pots of honey in ancient Egyptian "
     "tombs that are over three thousand years old, and it's still perfectly edible.", [], None),
]

failed = 0
for text, tools, expected in CASES:
    got = judge(text, tools, HOME)
    mark = "ok  " if got == expected else "FAIL"
    failed += got != expected
    print(f"{mark} {got!s:5} expected {expected!s:5}  {text!r} with {tools}")

# The observer carries the turn before's calls: an interjection ("What?", "Time for a charge") starts a
# new turn while the answer still rests on the calls just made.
import asyncio  # noqa: E402

from pipecat.frames.frames import (FunctionCallResultFrame, LLMMessagesAppendFrame,  # noqa: E402
                                   TranscriptionFrame, UserStartedSpeakingFrame)
from pipecat.observers.base_observer import FramePushed  # noqa: E402

from winston.core.guard import ToolTurnObserver  # noqa: E402


async def turns():
    obs = ToolTurnObserver()
    push = lambda f: obs.on_push_frame(FramePushed(source=None, destination=None, frame=f, direction=None, timestamp=0))
    await push(FunctionCallResultFrame(function_name="get_sensor_readings", tool_call_id="a", arguments={},
                                       result={"readings": ["58%"]}))
    await push(UserStartedSpeakingFrame())                   # "Is it still on?"
    await push(TranscriptionFrame(text="Is it", user_id="", timestamp=""))
    heard = obs.heard
    frame = TranscriptionFrame(text="still on?", user_id="", timestamp="")
    await push(frame)
    await push(frame)                                        # the next hop: seen once
    one = judge("The shelf lamp is still on.", obs.calls + obs.previous, HOME)
    heard_voice = obs.heard
    await push(LLMMessagesAppendFrame(messages=[{"role": "user", "content": "And now?"}]))
    two = judge("The shelf lamp is still on.", obs.calls + obs.previous, HOME)
    return one, two, (heard, heard_voice, obs.heard)

*got, heard = asyncio.run(turns())
got.append(heard)
for g, e, what in zip(got, (None, "state", ("Is it", "Is it still on?", "And now?")),
                      ("one turn after the call", "two turns after the call", "heard follows the user")):
    mark = "ok  " if g == e else "FAIL"
    failed += g != e
    print(f"{mark} {g!s:5} expected {e!s:5}  {what}")
print(f"\n{len(CASES) + 3 - failed}/{len(CASES) + 3} passed")
sys.exit(1 if failed else 0)
