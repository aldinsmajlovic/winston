"""Claim guard cases: pure checks on judge(), no hardware needed.

    uv run python tests/guard_cases.py
"""
import sys
from pathlib import Path
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
    # memory changes: only remember or forget back them, in this turn or the one before
    ("I've forgotten that.", [], "act"),
    ("I've forgotten that.", [("forget", True)], None),
    ("I've forgotten that.", [("forget", False)], "act"),            # no such fact: nothing went
    ("I've forgotten that.", ACT_OK, "act"),                         # a light call is not a forget
    ("Noted.", ACT_OK, "act"),
    ("Got it, I'll remember that the dentist moved to Friday.", [("remember", True)], None),
    ("I removed the note about the dentist.", [], "act"),
    ("I removed the note about the dentist.", ACT_OK, "act"),
    ("I've updated the fact about your sister.", [("remember", True)], None),
    ("I removed milk from the shopping list.", [("todo__HassListRemoveItem", True)], None),
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

# The guard itself, driven frame by frame as the LLM would push them: sentences are let through as
# they finish, an unbacked claim is held back, a reply gets one retry per user turn, and a retry that
# fails too ends in the soft line.
import json as _json  # noqa: E402
import tempfile  # noqa: E402

from pipecat.frames.frames import (FunctionCallFromLLM, FunctionCallsStartedFrame, InterruptionFrame,  # noqa: E402
                                   LLMFullResponseEndFrame, LLMFullResponseStartFrame, LLMTextFrame)
from pipecat.processors.frame_processor import FrameDirection  # noqa: E402

from winston.core.announce import note as announcement_note  # noqa: E402
from winston.core.guard import SOFT, ClaimGuard, promise  # noqa: E402

PROMISES = [
    # (sentence, calls this turn, expected)
    ("Let me turn them on.", [], "act"),
    ("I'll go ahead and switch it off.", [], "act"),
    ("Let me do that now.", [], "act"),
    ("Let me turn them on.", ACT_OK, None),
    ("Let me turn them on.", READ_OK, "act"),                    # a look is not an action
    ("Let me check.", [], "state"),
    ("Let me check.", READ_OK, None),
    ("Let me think of something fun.", [], None),                 # no tool keeps these
    ("Let me know if you need anything else.", [], None),
    ("Let me tell you a story.", [], None),
    ("Shall I turn them on?", [], None),                          # a question promises nothing
    ("I won't switch anything off.", [], None),
]
for text, tools, expected in PROMISES:
    got = promise(text, tools)
    mark = "ok  " if got == expected else "FAIL"
    failed += got != expected
    print(f"{mark} {got!s:5} expected {expected!s:5}  promise {text!r} with {tools}")


class Rig:
    """One ClaimGuard with what it pushes recorded; `llm` pushes a frame the way the LLM does (the
    observer sees it first, then the guard processes it)."""

    def __init__(self, spoken=True):
        self.log = Path(tempfile.mkdtemp()) / "tool_calls.jsonl"
        cfg = SimpleNamespace(providers=SimpleNamespace(home_assistant=None),
                              logging=SimpleNamespace(tool_calls=str(self.log)))
        self.g = ClaimGuard(cfg, channel="guard-test", spoken=spoken)
        self.out: list[tuple] = []

        async def record(frame, direction=FrameDirection.DOWNSTREAM):
            self.out.append((frame, direction))
        async def nothing():
            pass
        self.g.push_frame = record
        self.g._start_interruption = nothing       # no task manager outside a pipeline
        self._n = 0

    async def seen(self, frame):
        await self.g.observer.on_push_frame(FramePushed(source=None, destination=self.g, frame=frame,
                                                        direction=FrameDirection.DOWNSTREAM, timestamp=0))

    async def llm(self, frame):
        await self.seen(frame)
        await self.g.process_frame(frame, FrameDirection.DOWNSTREAM)

    async def reply(self, *pieces, call: str | None = None, ok: bool = True):
        """One LLM response: its text pieces, then optionally a call (started before the response ends,
        its result after, as Pipecat pushes them)."""
        await self.llm(LLMFullResponseStartFrame())
        for p in pieces:
            await self.llm(LLMTextFrame(p))
        if call:
            self._n += 1
            cid = f"c{self._n}"
            await self.llm(FunctionCallsStartedFrame(function_calls=[FunctionCallFromLLM(
                function_name=call, tool_call_id=cid, arguments={}, context=None)]))
        await self.llm(LLMFullResponseEndFrame())
        if call:
            await self.seen(FunctionCallResultFrame(function_name=call, tool_call_id=cid, arguments={},
                                                    result="Error: no" if not ok else {"success": True}))

    async def user(self, text):
        await self.seen(LLMMessagesAppendFrame(messages=[{"role": "user", "content": text}]))

    def said(self) -> str:
        return "".join(f.text for f, _ in self.out if isinstance(f, LLMTextFrame))

    def notes(self) -> list[tuple[str, FrameDirection]]:
        return [(f.messages[0]["content"], d) for f, d in self.out if isinstance(f, LLMMessagesAppendFrame)]

    def records(self) -> list[dict]:
        return [_json.loads(x) for x in self.log.read_text().splitlines()] if self.log.exists() else []


async def rigs():
    got = {}
    # A backed claim goes straight through; sentences leave as they finish, the last at the end.
    r = Rig()
    await r.user("Turn off the shelf lamp.")
    await r.reply(call="intent__HassTurnOff")
    await r.llm(LLMFullResponseStartFrame())
    await r.llm(LLMTextFrame("Done"))
    got["nothing before a sentence ends"] = (r.said(), "")
    await r.llm(LLMTextFrame(", the shelf lamp is off. It"))
    got["a finished sentence leaves at once"] = (r.said(), "Done, the shelf lamp is off. ")
    await r.llm(LLMTextFrame("'s 21.5 degrees too."))
    await r.llm(LLMFullResponseEndFrame())
    got["backed reply passes whole"] = (r.said(), "Done, the shelf lamp is off. It's 21.5 degrees too.")
    got["no retry when backed"] = (r.notes(), [])

    # A stray think tag (the model writes one now and then with thinking off) is never said.
    r = Rig()
    await r.user("Turn off the shelf lamp.")
    await r.reply(call="intent__HassTurnOff")
    await r.reply("Shelf lamp is off.\n</", "think>\n\nAnything else?</think>")
    got["a think tag is not said"] = (r.said(), "Shelf lamp is off.\n\n\nAnything else?")

    # An unbacked claim is held back and never said; one retry, with the note upstream over voice.
    r = Rig()
    await r.user("Turn off the shelf lamp.")
    await r.reply("Sure. ", "I've turned off the shelf lamp.")
    notes = r.notes()
    got["claim held back"] = (r.said(), "Sure. ")
    got["one retry note"] = (len(notes), 1)
    got["note in the house convention"] = (notes[0][0].startswith(
        "[Automatic check, not said by the user: you were about to say \"I've turned off the shelf lamp.\""), True)
    got["note goes upstream over voice"] = (notes[0][1], FrameDirection.UPSTREAM)
    got["retrying until answered"] = (r.g.retrying, True)
    turn = r.g.observer.turn
    await r.seen(LLMMessagesAppendFrame(messages=[{"role": "user", "content": notes[0][0]}], run_llm=True))
    got["the note is not a user turn"] = (r.g.observer.turn, turn)
    await r.reply(call="intent__HassTurnOff")                 # the retry calls the tool...
    got["still retrying while the call runs"] = (r.g.retrying, True)
    await r.reply("Done, it's off.")                          # ...and says so
    got["retried reply said"] = (r.said(), "Sure. Done, it's off.")
    got["retry over"] = (r.g.retrying, False)
    rec = r.records()[-1]
    got["log: retried and backed"] = ((rec["retried"], rec["retry_backed"], rec["fallback"]), (True, True, False))

    # The retry claims again without a call: the soft line, said as part of the reply.
    r = Rig()
    await r.user("Turn off the shelf lamp.")
    await r.reply("I've turned off the shelf lamp.")
    await r.reply("I've turned it off now.")
    got["soft line after a failed retry"] = (r.said(), SOFT["act"])
    rec = r.records()[-1]
    got["log: retried, not backed, fallback"] = ((rec["retried"], rec["retry_backed"], rec["fallback"]),
                                                 (True, False, True))
    # Once per user turn: a second unbacked claim in the same turn gets the soft line, no second note.
    await r.reply("The shelf lamp is now off.")
    got["retry once per turn"] = (len(r.notes()), 1)
    got["second miss: soft line"] = (r.said(), SOFT["act"] + SOFT["act"])
    # A new user turn gets a retry again.
    await r.user("And the ceiling lamp?")
    await r.reply("Done.")
    got["a new turn may retry"] = (len(r.notes()), 2)

    # A reply that ends on a promise with an act verb and no call is retried; the promise is not said.
    r = Rig()
    await r.user("The lights, please.")
    await r.reply("Sure thing. ", "Let me turn them on.")
    got["dangling promise retried"] = (len(r.notes()), 1)
    got["the promise is held back"] = (r.said(), "Sure thing. ")
    # A retry that says plainly it cannot is an answer, not a failure.
    await r.reply("I can't find any lights I can control.")
    got["a plain refusal needs no soft line"] = (r.said(), "Sure thing. I can't find any lights I can control.")
    got["refusal logged, not backed"] = (r.records()[-1]["retry_backed"], False)

    # A promise mid-reply waits too: over text a line break ends it before the reply does.
    r = Rig()
    await r.user("Shelf lamp off.")
    await r.reply("Let me turn it off.\n", "Anything else?")
    got["mid-reply promise held with what follows"] = ((r.said(), len(r.notes())), ("", 1))
    # A promise kept by a call in the same response is said, just before the call's result.
    r = Rig()
    await r.user("Shelf lamp off.")
    await r.reply("Let me turn it off.\n", call="intent__HassTurnOff")
    got["promise kept by its own call"] = ((r.said(), r.notes()), ("Let me turn it off.\n", []))
    # The turn before's call does not keep a promise made now.
    await r.reply("Shelf lamp is off.")
    await r.user("And the ceiling lamp on.")
    await r.reply("Let me turn it on.")
    got["an old call keeps no new promise"] = (len(r.notes()), 1)

    # "Let me think of something" promises no tool.
    r = Rig()
    await r.user("Tell me something fun.")
    await r.reply("Let me think of something fun.")
    got["no act verb, no retry"] = ((r.notes(), r.said()), ([], "Let me think of something fun."))

    # A claim made just before its own call in the same response is kept.
    r = Rig()
    await r.user("Shelf lamp off.")
    await r.reply("I'll switch off the shelf lamp.", call="intent__HassTurnOff")
    got["claim before its own call kept"] = ((r.said(), r.notes()), ("I'll switch off the shelf lamp.", []))

    # Over text the note goes downstream, behind the end of the reply.
    r = Rig(spoken=False)
    await r.user("Shelf lamp off.")
    await r.reply("Done, the shelf lamp is off.")
    kinds = [type(f).__name__ for f, _ in r.out]
    got["text: note after the end"] = (kinds[-2:], ["LLMFullResponseEndFrame", "LLMMessagesAppendFrame"])
    got["text: note downstream"] = (r.notes()[0][1], FrameDirection.DOWNSTREAM)

    # An interruption drops what is held and abandons the retry.
    r = Rig()
    await r.user("Shelf lamp off.")
    await r.reply("I've turned off the shelf lamp.")
    await r.llm(LLMFullResponseStartFrame())
    await r.llm(LLMTextFrame("I've turned it"))
    await r.g.process_frame(InterruptionFrame(), FrameDirection.DOWNSTREAM)
    await r.llm(LLMFullResponseEndFrame())
    got["interruption drops held text"] = (r.said(), "")
    got["interruption ends the retry"] = ((r.g.retrying, r.records()[-1].get("interrupted")), (False, True))

    # Announcement notes do not start a turn either: the calls stay in this turn.
    r = Rig()
    await r.user("Shelf lamp off.")
    await r.reply(call="intent__HassTurnOff")
    await r.user(announcement_note("The washing machine is done.", {}))
    got["announcement keeps the turn's calls"] = ((r.g.observer.calls, r.g.observer.previous),
                                                  ([("intent__HassTurnOff", True)], []))

    # Pipecat broadcasts UserStartedSpeakingFrame as a pair with their own ids: one new turn, not two.
    r = Rig()
    await r.user("Shelf lamp off.")
    await r.reply(call="intent__HassTurnOff")
    down, up = UserStartedSpeakingFrame(), UserStartedSpeakingFrame()
    down.broadcast_sibling_id, up.broadcast_sibling_id = up.id, down.id
    await r.seen(down)
    await r.seen(up)
    got["a broadcast pair is one turn"] = (r.g.observer.previous, [("intent__HassTurnOff", True)])

    # A retry that answers with a negated state claim and no call is not a plain refusal.
    r = Rig()
    await r.user("Is the shelf lamp on?")
    await r.reply("The shelf lamp is currently on.")
    await r.reply("The shelf lamp isn't on.")
    got["a negated claim is no refusal"] = (r.said(), "The shelf lamp isn't on. " + SOFT["state"])
    r = Rig()
    await r.user("Is the shelf lamp on?")
    await r.reply("The shelf lamp is currently on.")
    await r.reply("I haven't checked the shelf lamp.")
    got["saying he has not checked is an answer"] = (r.said(), "I haven't checked the shelf lamp.")
    return got

for what, (g, e) in asyncio.run(rigs()).items():
    mark = "ok  " if g == e else "FAIL"
    failed += g != e
    print(f"{mark} {what}" + ("" if g == e else f": got {g!r}, expected {e!r}"))
    CASES.append(what)
total = len(CASES) + len(PROMISES) + 3
print(f"\n{total - failed}/{total} passed")
sys.exit(1 if failed else 0)
