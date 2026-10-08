"""Home request cases: which spoken turns think first, and what he says while he does. No hardware needed.

    uv run python tests/home_cases.py
"""
import asyncio

from harness import check, finish_checks
from pipecat.processors.aggregators.llm_context import LLMContext

from winston.config.schema import WinstonConfig
from winston.core.brain import acted, build_llm, home_request, name_forms, only_name, own_answers

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

asyncio.run(shortcut())

finish_checks("home: home requests, first lines, quiet after and shortcuts ok")
