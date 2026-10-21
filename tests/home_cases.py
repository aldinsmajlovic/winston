"""Home request cases: which spoken turns think first, and what he says while he does. No hardware needed.

    uv run python tests/home_cases.py
"""
import asyncio

from harness import check, finish_checks
from pipecat.processors.aggregators.llm_context import LLMContext

from winston.config.schema import WinstonConfig
from winston.core.brain import acted, build_llm, home_request, name_forms, only_name, own_answers
from winston.providers.home_assistant import Snapshot, registry_view, tool_description, tool_label, unusable

cfg = WinstonConfig.model_validate({"llm": {"model": "x"}, "providers": {"home_assistant": {
    "url": "http://ha.invalid:8123", "expose": {
        "light.shelf": {"aliases": ["shelf lamp"]},
        "script.movie_night": {"aliases": ["movie night", "set up movie night in here"], "say_first": "Movie night it is.",
                               "quiet_after": True},
        "script.movie": {"aliases": ["movie"], "say_first": "A film, then."},
        "script.lights_out": {"aliases": ["lights out"], "quiet_after": True, "shortcut": True},
        "script.party": {"aliases": ["party time"], "say_first": "Party it is.", "shortcut": True}}}}})
home, own = home_request(cfg), own_answers(cfg)


def first(said: str) -> str | None:
    return next((o.line for o in own if o.pattern.search(said.lower())), None)


def quiet(said: str) -> bool:
    return next((o.quiet for o in own if o.pattern.search(said.lower())), False)


def turn(*steps) -> list:
    """A user message, then (tool, result) calls as the context keeps them."""
    msgs = [{"role": "user", "content": "[21:00] set up movie night in here"}]
    for i, (tool, result) in enumerate(steps):
        msgs.append({"role": "assistant", "tool_calls": [{"id": f"c{i}", "type": "function",
                                                          "function": {"name": tool, "arguments": "{}"}}]})
        msgs.append({"role": "tool", "tool_call_id": f"c{i}", "content": result})
    return msgs


check("an alias makes a home request", bool(home.search("set up movie night in here")), True)
check("a change verb makes a home request", bool(home.search("turn it off")), True)
check("small talk does not", bool(home.search("tell me a joke")), False)
check("a named script says its own line", first("Set up movie night in here."), "Movie night it is.")
check("the longest name wins", first("movie night, please"), "Movie night it is.")
check("the object id calls it too", first("movie night"), "Movie night it is.")
check("a shorter name still has its line", first("put a movie on"), "A film, then.")
check("an entity without a line keeps One moment", first("turn off the shelf lamp"), None)
check("only whole words", first("moviegoers"), None)
check("quiet after is per entity", (quiet("movie night"), quiet("put a movie on"), quiet("lights out")), (True, False, True))
check("quiet after without a line keeps One moment", first("lights out"), None)
check("a script that ran is an action", acted(turn(("script__movie_night", '{"success": true}'))), True)
check("a read is not", acted(turn(("homeassistant__GetLiveContext", "Live Context: ..."))), False)
check("a failed action is not", acted(turn(("script__movie_night", "Error: no such device"))), False)
check("a read then an action is", acted(turn(("homeassistant__GetLiveContext", "ok"), ("intent__HassTurnOn", "{}"))), True)
check("an action before the newest message does not count",
      acted([*turn(("script__movie_night", "{}")), {"role": "user", "content": "[21:01] and now?"}]), False)
check("only the name is left of a bare request", only_name("Okay Winston, please movie night in here, sir.", "Winston"),
      "movie night in here")
check("a longer request is not only the name", only_name("Winston, don't do party time now", "Winston"),
      "don t do party time")
check("a place after the name is still the name",
      "movie night" in name_forms(only_name("Uh can you movie night in the living room?", "Winston")), True)
check("a name that ends in a place keeps it", "movie night in here" in name_forms("movie night in here"), True)
check("more than a place is not the name", name_forms(only_name("party time in the kitchen and dim it", "Winston")),
      {"party time in the kitchen and dim it"})


async def shortcut():
    llm = build_llm(cfg, "satellite")
    said = []

    async def keep(frame, direction=None):
        said.append(getattr(frame, "text", None))
    llm.push_frame = keep
    llm.bind(["intent__HassTurnOn", "script__party", "script__lights_out"])

    async def first_call(text: str, history: list | None = None) -> list:
        stream = await llm.get_chat_completions(LLMContext([*(history or []), {"role": "user", "content": f"[21:00] {text}"}]))
        return [c.choices[0].delta.tool_calls[0].function.name async for c in stream]
    check("a bare shortcut name calls its script", await first_call("Winston, party time!"), ["script__party"])
    check("with its line first", said, ["Party it is."])
    check("a filler before and a place after still call it",
          await first_call("Uh, party time in the living room, please."), ["script__party"])
    ran = [{"role": "user", "content": "[20:59] party time"},
           {"role": "assistant", "tool_calls": [{"id": "c0", "type": "function",
                                                 "function": {"name": "script__party", "arguments": "{}"}}]},
           {"role": "tool", "tool_call_id": "c0", "content": '{"success": true}'}]
    check("said again it runs again", await first_call("Party time.", ran), ["script__party"])
    after = await llm.get_chat_completions(LLMContext([{"role": "user", "content": "[21:00] lights out"}, *ran[1:]]))
    check("a quiet shortcut says nothing after", [c async for c in after], [])
    check("and ends the turn", llm.ended, True)

    # A home request thinks while it decides what to call, not while it answers from the result.
    parent, thought = type(llm).__mro__[1], []

    async def record(self, context):
        thought.append(self._think)

        async def nothing():
            return
            yield
        return nothing()
    real, parent.get_chat_completions = parent.get_chat_completions, record
    lamp = [{"role": "user", "content": "[21:00] turn off the shelf lamp"}]
    off = [{"role": "assistant", "tool_calls": [{"id": "c1", "type": "function",
                                                 "function": {"name": "intent__HassTurnOff", "arguments": "{}"}}]},
           {"role": "tool", "tool_call_id": "c1", "content": '{"success": true}'}]
    await llm.get_chat_completions(LLMContext(lamp))
    await llm.get_chat_completions(LLMContext(lamp + off))
    check("a home request thinks before its call, not after the result", thought, [True, False])
    llm.ended, more = False, [{"role": "user", "content": "[21:00] lights out and play some jazz"}, *ran[1:]]
    await llm.get_chat_completions(LLMContext(more))
    check("a quiet name in a longer request goes on to the rest", (llm.ended, len(thought)), (False, 3))
    parent.get_chat_completions = real

    # A call that failed is not made again with the same arguments in the same request (2026-10-16: four
    # spotify_play calls against the same refusal, then the call written out as text and said aloud).
    from types import SimpleNamespace
    ran, results = [], []

    async def play(params):
        ran.append(params.arguments["what"])
        await params.result_callback({"error": "Spotify is refusing searches until about 00:10."})
    llm.register_function("x_play", play, timeout_secs=12)
    handler = llm._functions["x_play"].handler

    async def call(what: str):
        async def got(result, **_):
            results.append(result)
        await handler(SimpleNamespace(arguments={"what": what}, result_callback=got))
    real, parent.get_chat_completions = parent.get_chat_completions, record
    request = [{"role": "user", "content": "[07:40] put on push the feeling on"}]
    await llm.get_chat_completions(LLMContext(request))
    await call("Push the Feeling On")
    await call("Push the Feeling On")
    await call("Push the Feeling On by Nightcrawlers")
    check("a failed call is not made again with the same arguments", ran,
          ["Push the Feeling On", "Push the Feeling On by Nightcrawlers"])
    check("and answers that it would fail again", "would fail again" in results[1]["error"], True)
    await llm.get_chat_completions(LLMContext(request + [{"role": "assistant", "content": "Spotify is refusing."},
                                                         {"role": "user", "content": "[07:42] try again"}]))
    await call("Push the Feeling On")
    parent.get_chat_completions = real
    check("the next request may try it again", ran[-1], "Push the Feeling On")
    check("its options stay", llm._functions["x_play"].timeout_secs, 12)

    # An action under way finishes when he talks over the reply; a read is cut short (2026-10-02: a pause
    # cancelled that way left the music on while he heard "The music is paused").
    from winston.core import guard
    done = []

    async def slow(params):
        await asyncio.sleep(0.05)
        done.append(params.arguments["n"])
        await params.result_callback({"ok": True})
    for name, tier in (("x_pause", guard.ACT), ("x_look", guard.READ)):
        guard.set_tier(name, tier)
        llm.register_function(name, slow)

    async def talked_over(name: str, n: int):
        async def got(result, **_):
            pass
        task = asyncio.create_task(llm._functions[name].handler(SimpleNamespace(arguments={"n": n}, result_callback=got)))
        await asyncio.sleep(0.01)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    await talked_over("x_pause", 1)
    await talked_over("x_look", 2)
    await asyncio.sleep(0.1)
    check("an action talked over finishes, a read does not", done, [1])

    # Past the model's context it starts afresh from the newest request and says so (2026-10-15: silence
    # after 16,384 tokens until it was reset by hand).
    import httpx
    from openai import BadRequestError
    asked = []

    async def full_then_fine(self, context):
        asked.append(len(context.get_messages()))
        if len(asked) == 1:
            raise BadRequestError("Error code: 400 - request (16467 tokens) exceeds the available context size",
                                  response=httpx.Response(400, request=httpx.Request("POST", "http://llm.invalid")),
                                  body=None)

        async def nothing():
            return
            yield
        return nothing()
    real, parent.get_chat_completions = parent.get_chat_completions, full_then_fine
    said.clear()
    long_talk = LLMContext([{"role": "user", "content": "[21:50] tell me a story"},
                            {"role": "assistant", "content": "Once upon a time..."},
                            {"role": "user", "content": "[21:57] what happened?"}])
    await llm.get_chat_completions(long_talk)
    parent.get_chat_completions = real
    check("a full context starts afresh from the newest request", (asked, long_talk.get_messages()),
          ([3, 1], [{"role": "user", "content": "[21:57] what happened?"}]))
    check("and says so first", said[-1:], ["That conversation had grown too long, so I've started a fresh one."])

asyncio.run(shortcut())

# Home Assistant offers its satellite tools in a home with no satellite; there they would say "done" for nothing.
offered = ["intent__HassTurnOn", "assist_satellite__HassBroadcast", "intent__HassCancelAllTimers", "llm__GetDateTime"]
check("no satellite: its tools are left out", unusable(offered, Snapshot(states={"light.shelf": {}})),
      {"assist_satellite__HassBroadcast", "intent__HassCancelAllTimers"})
check("a satellite: all kept", unusable(offered, Snapshot(states={"assist_satellite.kitchen": {}})), set())
check("Home Assistant unread: all kept", unusable(offered, None), set())

# The picker hides Home Assistant's settings and diagnostics, and reads each room by name; buttons, scenes and
# scripts say when they last ran.
snap = Snapshot(states={
    "light.b": {"state": "on", "attributes": {"friendly_name": "bench lamp"}},
    "light.a": {"state": "off", "attributes": {"friendly_name": "Wall lamp"}},
    "sensor.rssi": {"state": "-60", "attributes": {"friendly_name": "Signal"}},
    "button.wake": {"state": "2026-09-24T07:12:00+00:00", "attributes": {}},
    "button.never": {"state": "unknown", "attributes": {}},
    "script.movie": {"state": "off", "attributes": {"last_triggered": "2026-10-11T20:00:00+00:00"}},
    "sensor.boot": {"state": "2026-10-01T06:00:00+00:00", "attributes": {}}},
    registry={"sensor.rssi": {"entity_category": "diagnostic"}, "light.a": {"hidden_by": "user"}, "light.b": {}})
view = {e.entity_id: e for g in registry_view(cfg.providers.home_assistant, snap).groups for e in g.entities}
check("a diagnostic entity says so", (view["sensor.rssi"].category, view["light.b"].category), ("diagnostic", None))
check("a hidden one too", (view["light.a"].hidden, view["light.b"].hidden), (True, False))
check("a room lists by name, not by id", [e.entity_id for e in registry_view(cfg.providers.home_assistant, snap).groups[0].entities][:2],
      ["light.b", "button.never"])
check("buttons and scripts say when they last ran",
      [view[e].last_run for e in ("button.wake", "button.never", "script.movie", "sensor.boot", "light.b")],
      ["2026-09-24T07:12:00+00:00", None, "2026-10-11T20:00:00+00:00", None, None])

# A room's devices Home Assistant cannot reach sit under the ones it can; a room where none can comes after the
# others, and the devices with no room last.
snap = Snapshot(states={
    "media_player.a": {"state": "unavailable", "attributes": {"friendly_name": "A player"}},
    "media_player.b": {"state": "unavailable", "attributes": {"friendly_name": "B player"}},
    "light.c": {"state": "unavailable", "attributes": {"friendly_name": "Ceiling"}},
    "light.d": {"state": "on", "attributes": {"friendly_name": "Desk"}},
    "light.e": {"state": "unavailable", "attributes": {}}},
    registry={"media_player.a": {"area_id": "g"}, "media_player.b": {"area_id": "g"}, "light.c": {"area_id": "o"},
              "light.d": {"area_id": "o"}, "light.e": {}},
    areas={"g": "Attic", "o": "Office"})
check("unavailable sinks, in a room and as a room", [(g.name, [e.entity_id for e in g.entities])
                                                     for g in registry_view(cfg.providers.home_assistant, snap).groups],
      [("Office", ["light.d", "light.c"]), ("Attic", ["media_player.a", "media_player.b"]), (None, ["light.e"])])

# Tools read in plain words; a script's description loses the aliases Home Assistant appends to it as a Python list,
# and keeps one end to its sentence.
check("the vacuum's room tool", tool_label("vacuum__HassVacuumCleanArea"), "Clean a room with the vacuum")
check("a script's aliases are not shown",
      tool_description("Lights only, for parties. Aliases: ['Lets Rock', \"let's rock and roll\"]"), "Lights only, for parties.")
check("its own period is not doubled", tool_description("Only when it is said.. Aliases: ['a']"), "Only when it is said.")
check("nor follows its question mark", tool_description("Ready?. Aliases: ['a']"), "Ready?")
check("a description of only aliases is none", tool_description("Aliases: ['movie night']"), "")
check("a long one is cut at a word", tool_description("word " * 60)[-5:], "word…")
check("other tools keep theirs", tool_description("Turns on a device.\nMore."), "Turns on a device.")

finish_checks("home: home requests, first lines, quiet after and shortcuts ok")
