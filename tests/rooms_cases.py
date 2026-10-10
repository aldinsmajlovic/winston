"""Room cases: one conversation per room across sessions, idle reset, starting over, the mic lease, the
gate and the room in the time stamp. No hardware needed.

    uv run python tests/rooms_cases.py
"""
import asyncio
import re
import time
from types import SimpleNamespace

from harness import check, finish_checks
from pipecat.frames.frames import InputAudioRawFrame, LLMContextFrame, TranscriptionFrame
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection

from winston.config.schema import SatelliteDevice, WinstonConfig
from winston.core.brain import _STAMP, Clock
from winston.core.rooms import (MicGate, RoomLink, Rooms, area, dismissal, local_sync, make_start_over_tool,
                                scoped_by_name, wake_phrases)

cfg = WinstonConfig.model_validate({"llm": {"model": "x"}})
cfg.channels.satellite.devices = {"main room": SatelliteDevice(host="10.0.0.5", area="Main Room"),
                                  "kitchen": SatelliteDevice(host="10.0.0.6")}
check("area from the box", area(cfg, "Main  ROOM"), "Main Room")
check("area is the room's name", area(cfg, "kitchen"), "kitchen")
check("area of a room without a box", area(cfg, "Office "), "Office")

user = lambda t: {"role": "user", "content": t}             # noqa: E731
bot = lambda t: {"role": "assistant", "content": t}         # noqa: E731
texts = lambda ctx: [m["content"] for m in ctx.get_messages()]   # noqa: E731
said = lambda ctx: [m.get("content") for m in ctx.get_messages()]   # noqa: E731


async def turn(link: RoomLink, ctx: LLMContext, said: str, reply: str):
    """What a session's pipeline does in one turn: the user message, sync before the LLM, the reply,
    sync after it."""
    ctx.add_message(user(said))
    await link.sync(ctx, pull=True)
    ctx.add_message(bot(reply))
    await link.sync(ctx, pull=False)


async def conversation():
    rooms = Rooms(cfg)
    box, screen = RoomLink("main room", "box", local_sync(rooms)), RoomLink("Main Room", "screen", local_sync(rooms))
    at_box, at_screen = LLMContext([]), LLMContext([])
    await turn(box, at_box, "[09:00] lights off", "The lights are off.")
    await turn(screen, at_screen, "[09:01] and the shelf lamp?", "The shelf lamp is off too.")
    check("the screen continues the box's conversation", texts(at_screen),
          ["[09:00] lights off", "The lights are off.", "[09:01] and the shelf lamp?", "The shelf lamp is off too."])
    await turn(box, at_box, "[09:02] thanks", "You're welcome.")
    check("the box gets the screen's turn before its own", texts(at_box),
          ["[09:00] lights off", "The lights are off.", "[09:01] and the shelf lamp?", "The shelf lamp is off too.",
           "[09:02] thanks", "You're welcome."])
    check("the room has each message once", len(rooms.room("main room").messages), 6)

    # An announcement note both sessions added for themselves is not doubled.
    note = user("[Home notification, not said by the user. You announced: \"Washer done\".]")
    at_box.add_message(note); at_screen.add_message(dict(note))
    await turn(box, at_box, "[09:03] how long did it run?", "About an hour.")
    await turn(screen, at_screen, "[09:04] ok", "Good.")
    check("a shared note is kept once", sum("Washer done" in t for t in texts(at_screen)), 1)

    # A hand-over after the reply brings nothing in: the others wait for the next user turn.
    other = LLMContext([])
    third = RoomLink("main room", "third", local_sync(rooms))
    await turn(third, other, "[09:05] hi", "Hello.")
    before = len(at_box.get_messages())
    await box.sync(at_box, pull=False)
    check("nothing arrives mid-turn", len(at_box.get_messages()), before)

    # A question another session never answered (its tab closed mid-reply) waits until an answer
    # follows it, so the model does not answer it in place of this session's own.
    gone, left = RoomLink("main room", "gone", local_sync(rooms)), LLMContext([])
    left.add_message(user("[09:06] how many people live there?"))
    await gone.sync(left, pull=True)
    await turn(box, at_box, "[09:07] what time is it?", "It's 09:07.")
    check("an unanswered question waits", texts(at_box)[-2:], ["[09:07] what time is it?", "It's 09:07."])
    check("it is not in the box's context yet", "[09:06] how many people live there?" in texts(at_box), False)
    left.add_message(bot("About 729,000."))
    await gone.sync(left, pull=False)
    await turn(box, at_box, "[09:08] thanks", "You're welcome.")
    check("it comes in with its answer", texts(at_box)[-4:],
          ["[09:06] how many people live there?", "About 729,000.", "[09:08] thanks", "You're welcome."])

    # Idle: the room starts over; a session from the old conversation drops it at its next turn.
    rooms.room("main room").last_used -= cfg.channels.satellite.session_idle_mins * 60 + 1
    await turn(screen, at_screen, "[10:30] good morning", "Good morning.")
    check("an idle room starts fresh", texts(at_screen), ["[10:30] good morning", "Good morning."])
    await turn(box, at_box, "[10:31] what did I just say?", "You said good morning.")
    check("the box picks up the new conversation", texts(at_box),
          ["[10:30] good morning", "Good morning.", "[10:31] what did I just say?", "You said good morning."])

    # Starting over at the screen: the room's conversation ends for the box too; the request that asked
    # stays, with its tool call, so the model has something to answer.
    _, start_over = make_start_over_tool()
    results = []

    async def done(result, **_):
        results.append(result)
    call = {"role": "assistant", "tool_calls": [{"id": "s1", "type": "function", "function": {
        "name": "start_over", "arguments": "{}"}}]}
    at_screen.add_message(user("[10:32] start over"))
    await screen.sync(at_screen, pull=True)
    at_screen.add_message(call)
    await start_over(SimpleNamespace(context=at_screen, result_callback=done))
    check("start over keeps the request and its call", (said(at_screen), results),
          (["[10:32] start over", None], [{"started_over": True}]))
    at_screen.add_message({"role": "tool", "tool_call_id": "s1", "content": '{"started_over": true}'})
    await screen.sync(at_screen, pull=True)
    at_screen.add_message(bot("Very well."))
    await screen.sync(at_screen, pull=False)
    check("the screen goes on in the new conversation", said(at_screen),
          ["[10:32] start over", None, '{"started_over": true}', "Very well."])
    await turn(box, at_box, "[10:33] what did I say before?", "Only to start over.")
    check("the box starts over too", said(at_box),
          ["[10:32] start over", None, '{"started_over": true}', "Very well.", "[10:33] what did I say before?",
           "Only to start over."])
    no_room = LLMContext([user("[10:34] hi"), bot("Hello."), user("[10:35] new conversation"), call])
    await start_over(SimpleNamespace(context=no_room, result_callback=done))
    check("without a room only the session starts over", said(no_room), ["[10:35] new conversation", None])

    # Tool calls travel with their results; objects that are not chat messages stay local.
    tool_ctx = LLMContext([])
    tools = RoomLink("kitchen", "tools", local_sync(rooms))
    tool_ctx.add_message(user("[11:00] kitchen light on"))
    await tools.sync(tool_ctx, pull=True)
    tool_ctx.add_messages([{"role": "assistant", "tool_calls": [{"id": "c1", "type": "function", "function": {
        "name": "HassTurnOn", "arguments": "{}"}}]}, {"role": "tool", "tool_call_id": "c1", "content": "ok"},
        bot("Done."), {"role": "system", "content": "local only"}])
    await tools.sync(tool_ctx, pull=False)
    check("tool messages shared, system not", [m.get("role") for _, m in rooms.room("kitchen").messages],
          ["user", "assistant", "tool", "assistant"])

    # winstond away: the session goes on with its own context.
    async def away(room, req):
        return None
    alone, ctx = RoomLink("main room", "alone", away), LLMContext([])
    await turn(alone, ctx, "[12:00] hello", "Hi.")
    check("without winstond the session goes on", texts(ctx), ["[12:00] hello", "Hi."])


async def mic():
    events = []
    rooms = Rooms(cfg, on_mic=lambda room, held: events.append(held))
    q = rooms.subscribe("main room")
    check("first event is the current holder", q.get_nowait()["held_by"], None)
    rooms.hold("main room", "box", 0.2)
    rooms.hold("Main Room", "box", 0.3)             # a renewal is not a change
    check("held", rooms.holder("main room"), "box")
    rooms.hold("main room", "screen", 0)            # only the holder lets go
    check("still held", rooms.holder("main room"), "box")
    await asyncio.sleep(0.45)
    check("the lease runs out", rooms.holder("main room"), None)
    check("mic events", events, ["box", None])
    rooms.hold("main room", "box", 5); rooms.hold("main room", "box", 0)
    check("let go", (rooms.holder("main room"), events[-1]), (None, None))
    check("room list", [(r.name, r.box, r.area) for r in rooms.states()],
          [("main room", True, "Main Room"), ("kitchen", True, "kitchen")])


async def gate():
    g = MicGate()
    passed = []

    async def keep(frame, direction=FrameDirection.DOWNSTREAM):
        passed.append(frame)
    for p in (g.audio, g.text):
        p.push_frame = keep
    audio = InputAudioRawFrame(audio=b"\x01\x02" * 160, sample_rate=16000, num_channels=1)
    check("gate opens and closes", (g.set("box"), g.set("box"), g.closed), (True, False, True))
    await g.audio.process_frame(audio, FrameDirection.DOWNSTREAM)
    check("closed: the mic is silence", passed[-1].audio, b"\0" * 320)
    await g.text.process_frame(TranscriptionFrame("okay winston", "u", "t"), FrameDirection.DOWNSTREAM)
    check("closed: nothing heard is a turn", len(passed), 1)
    g.set(None)
    await g.text.process_frame(TranscriptionFrame("hello", "u", "t"), FrameDirection.DOWNSTREAM)
    check("open: speech passes", passed[-1].text, "hello")

    cfg.channels.satellite.devices["main room"].wake_word = "okay_winston,hey_jarvis"
    check("wake phrases", wake_phrases(cfg) >= {"okay winston", "hey winston", "hey jarvis"}, True)
    g = MicGate(wake_phrases(cfg))
    g.text.push_frame = keep
    n = len(passed)
    for said in ("Okay, Winston.", "Hey Jarvis!", "Okay Winston, lights off.", "Winston?"):
        await g.text.process_frame(TranscriptionFrame(said, "u", "t"), FrameDirection.DOWNSTREAM)
    check("a wake word on its own is the box's", [f.text for f in passed[n:]], ["Okay Winston, lights off.", "Winston?"])


async def stamp():
    out = []
    for c in (Clock(), Clock("Main Room")):
        c.push_frame = lambda f, d=FrameDirection.DOWNSTREAM: out.append(f) or asyncio.sleep(0)
        ctx = LLMContext([user("lights off")])
        await c.process_frame(LLMContextFrame(context=ctx), FrameDirection.DOWNSTREAM)
        await c.process_frame(LLMContextFrame(context=ctx), FrameDirection.DOWNSTREAM)     # stamped once
    msgs = [f.context.get_messages()[-1]["content"] for f in out[::2]]
    check("plain stamp", bool(re.fullmatch(r"\[\d\d:\d\d\] lights off", msgs[0])), True)
    check("room stamp", bool(re.fullmatch(r"\[\d\d:\d\d, room: Main Room\] lights off", msgs[1])), True)
    check("stamp pattern knows rooms", bool(_STAMP.match(msgs[1])), True)


def named_device():
    areas = {"window lamps": "Living Room", "shelf lamp": "Main Room"}
    check("a named device in another room loses the stamp's area",
          scoped_by_name({"name": "Window  Lamps", "area": "Main Room", "floor": "Ground"}, areas), {"name": "Window  Lamps"})
    check("a named device in this room keeps it",
          scoped_by_name({"name": "shelf lamp", "area": "main room"}, areas), {"name": "shelf lamp", "area": "main room"})
    check("an unknown name keeps the area", scoped_by_name({"name": "the lights", "area": "Living Room"}, areas),
          {"name": "the lights", "area": "Living Room"})
    check("an area alone is left alone", scoped_by_name({"area": "Main Room", "domain": ["light"]}, areas),
          {"area": "Main Room", "domain": ["light"]})



def dismissals():
    ends = ["Stop.", "Yeah we're taking it. Stop.", "No, that's all, thanks.", "That's all, Winston.", "Okay Winston, stop.",
            "Never mind.", "Stop it, please.", "Nothing.", "Be quiet now.", "I wasn't talking to you.",
            "That's all. Thanks.", "Stop. Thank you, Winston."]
    asks = ["No.", "Thank you.", "Stop the music.", "Turn off the lights.", "Stop the timer", "Yes, turn it off.", "",
            "Thanks. Please.", "Turn it off. Thanks."]
    check("dismissals end the conversation", [t for t in ends if not dismissal(t, "Winston")], [])
    check("answers and requests do not", [t for t in asks if dismissal(t, "Winston")], [])


t0 = time.perf_counter()
dismissals()
named_device()
asyncio.run(conversation())
asyncio.run(mic())
asyncio.run(gate())
asyncio.run(stamp())
finish_checks(f"rooms: dismissals, named devices, conversation, mic lease, gate and stamp ok "
              f"({time.perf_counter() - t0:.1f} s)")
