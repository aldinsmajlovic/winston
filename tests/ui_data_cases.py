"""What the UI reads, in words: an extension's reach, tool lines on Talk, the error he shows when he cannot
answer (the brain off too) and why a typed conversation starts afresh, the keys an MCP server can be given,
phone names, an extension's own list for a picker and its settings that did not load, a room box's connection, and the saves that touch Home Assistant or stop a part. Runs against a scratch data directory, no hardware or winstond needed.

    uv run python tests/ui_data_cases.py
"""
import asyncio
import time
from types import SimpleNamespace

import httpx
import openai
from harness import check, finish_checks, free_port, scratch_data
from loguru import logger

scratch_data("winston-ui-data-")     # before Winston is imported

from winston_sdk.manifest import Permissions  # noqa: E402
from mcp.shared.exceptions import MCPError  # noqa: E402
from mcp.types import INVALID_PARAMS  # noqa: E402

from pipecat.frames.frames import ErrorFrame  # noqa: E402
from pipecat.observers.base_observer import FramePushed  # noqa: E402
from pipecat.processors.frame_processor import FrameDirection  # noqa: E402

from winston.api import options as options_api  # noqa: E402
from winston.api import secrets  # noqa: E402
from winston.channels.text import Printer, TurnTracker  # noqa: E402
from winston.channels.web import FAILED, UNREACHABLE, WebChat, error_text  # noqa: E402
from winston.config.schema import SatelliteDevice, WinstonConfig  # noqa: E402
from winston.core.common import failure_text, failure_words  # noqa: E402
from winston.core.live import Feed, know_extension_tools, tool_label  # noqa: E402
from winston.core.rooms import Rooms  # noqa: E402
from winston.engines.supervisor import Supervisor  # noqa: E402
from winston.extension_host.api import PHRASES, reach  # noqa: E402
from winston.extension_host.host import Host, first_sentence, in_words, problem_words, server_name  # noqa: E402
from winston.providers.home_assistant import ha_changed  # noqa: E402

logger.remove()         # a failed answer below logs its traceback, as it should; only mismatches print here

# Reach: every permission has words, the riskiest first; this computer stands for its loopback hosts.
check("every permission has a phrase", sorted(set(Permissions.model_fields) - set(PHRASES)), [])
host = SimpleNamespace(described={"other": {"manifest": {"name": "Other"}}})
lines = reach(host, {"network": ["127.0.0.1", "api.example.com", "localhost", "cdn.example.com"],
                     "presence": True, "home_assistant": "act", "speakers": True,
                     "extensions": ["other", "gone"], "memory": None})
check("reach, riskiest first", [(r.risk, r.text) for r in lines], [
    ("changes", "Plays on your speakers"), ("changes", "Controls devices in Home Assistant"),
    ("reads", "Knows whether you are home"), ("reads", "Uses actions of Other and gone"),
    ("connects", "Uses the internet: api.example.com and 2 more")])
check("its hosts, this computer once", lines[-1].hosts,
      ["api.example.com", "cdn.example.com", "This computer"])
check("any website", [(r.text, r.hosts) for r in reach(host, {"network": ["*"]})], [("Uses any website", [])])
check("only this computer", [r.text for r in reach(host, {"network": ["localhost", "::1"]})],
      ["Connects to programs on this computer"])
check("read-only tools and memory", [r.text for r in reach(host, {"tools": "read", "memory": "read"})],
      ["Can use his read-only actions", "Reads his memory"])

# Tool lines on Talk: an extension's tool by its own title; a Home Assistant tool in words, never its name.
know_extension_tools([{"name": "music_play", "extension": "music", "title": "Music",
                       "tool_title": "Play music"}])
check("a tool with an argument", tool_label("music_play", {"query": " Daft Punk "}, "ok"),
      "Play music: Daft Punk")
check("a tool without one", tool_label("music_play", {"volume": 3}, "running"), "Play music")
check("a tool that failed", tool_label("music_play", {"query": "x"}, "error"), "Couldn't play music")
check("Home Assistant, by its label",
      tool_label("vacuum__HassVacuumCleanArea", {"area": "Kitchen"}, "ok"), "Clean a room with the vacuum: Kitchen")
check("and failing", tool_label("HassVacuumCleanArea", {}, "error"), "Couldn't clean a room with the vacuum")
check("a known verb keeps its sentence", tool_label("HassTurnOn", {"name": "Desk lamp"}, "ok"),
      "Turned on Desk lamp")
check("names in words", [in_words(n) for n in ("next_track", "getWeather", "list-files")],
      ["Next track", "Get weather", "List files"])
check("a summary skips headings and ends with its paragraph",
      [first_sentence(t) for t in ("# Weather\n\nThe forecast. Ask for a city.", "Lists files\n\nUse it. x", None)],
      ["The forecast.", "Lists files", ""])

# The error on Talk: unreachable anywhere in the chain of causes, else something failed. Never the exception.
request = httpx.Request("POST", "http://127.0.0.1:1/v1/chat/completions")


def chained(outer: Exception, inner: Exception) -> Exception:
    outer.__cause__ = inner
    return outer


check("the brain's server refused", error_text(openai.APIConnectionError(request=request)), UNREACHABLE)
check("it timed out", error_text(openai.APITimeoutError(request=request)), UNREACHABLE)
check("a connect error under another",
      error_text(chained(RuntimeError("start failed"), httpx.ConnectError("refused"))), UNREACHABLE)
check("anything else", error_text(chained(ValueError("bad"), KeyError("x"))), FAILED)

# Why a server could not be reached, in words, from inside an exception group too.
refused = httpx.HTTPStatusError("x", request=request, response=httpx.Response(502, request=request))
check("why it was unreachable", [failure_words(e) for e in (
    TimeoutError(), httpx.ReadTimeout("x"), BaseExceptionGroup("g", [refused]), httpx.ConnectError("refused"),
    PermissionError("it refused its token (401)"), KeyError())], [
    "it did not answer in time", "it did not answer in time", "it answered 502", "nothing answered at that address",
    "it refused its token (401)", "KeyError"])


# A typed conversation that starts afresh says why, before the message that starts it.
class Session:
    busy, brain, error = False, SimpleNamespace(), None

    def __init__(self, fails: bool = False):
        self.last_used, self.fails = time.monotonic(), fails

    async def ask(self, text):
        if self.fails:
            raise RuntimeError("starting") from httpx.ConnectError("refused")

    async def close(self): ...


async def typed() -> list[tuple[str, str | None]]:
    feed = Feed()
    chat = WebChat(WinstonConfig.model_validate({"llm": {"model": "x"}}), feed)
    async def current(room=None):
        return chat.sessions.setdefault(None, Session(fails=len(chat.sessions) == 0 and room == "fail"))
    chat.current = current
    await chat.ask("first")
    chat.sessions[None].last_used -= 31 * 60
    await chat.ask("after a while")
    await chat.reset()
    await chat.ask("after a save", "fail")
    return [(e.type, e.text) for e in feed.backlog() if e.type in ("conversation", "user", "error")]

check("why a conversation is new", asyncio.run(typed()), [
    ("user", "first"), ("conversation", "after 30 min of quiet"), ("user", "after a while"),
    ("conversation", "settings were saved"), ("user", "after a save"), ("error", UNREACHABLE)])


# The brain off: its error travels upstream past the printer, which stops waiting, and Talk says why.
async def unanswered() -> tuple[str, bool, list[str | None]]:
    tracker = TurnTracker()
    printer = Printer(tracker, echo=False)
    down = openai.APIConnectionError(request=request)
    await tracker.on_push_frame(FramePushed(source=printer, destination=printer, direction=FrameDirection.UPSTREAM,
                                            frame=ErrorFrame("Connection error.", exception=down), timestamp=0))
    t0 = time.perf_counter()
    reply = await printer.wait_reply(timeout=5)
    quick = time.perf_counter() - t0 < 1
    feed = Feed()
    chat = WebChat(WinstonConfig.model_validate({"llm": {"model": "x"}}), feed)
    s = Session()
    s.error = tracker.error

    async def current(room=None):
        return s
    chat.current = current
    await chat.ask("ping")
    return reply, quick, [e.text for e in feed.backlog() if e.type == "error"]

check("no reply while the brain is off", asyncio.run(unanswered()),
      ("", True, [UNREACHABLE]))

# Home Assistant: a first connect (none before, or no address) never applies exposure; a changed list does.
base = {"llm": {"model": "x"}}
ha = {"url": "http://ha.local:8123", "expose": {"light.desk": {}}}
none, connected = WinstonConfig.model_validate(base), WinstonConfig.model_validate({**base, "providers": {
    "home_assistant": ha}})
no_url = WinstonConfig.model_validate({**base, "providers": {"home_assistant": {**ha, "url": ""}}})
more = WinstonConfig.model_validate({**base, "providers": {"home_assistant": {**ha, "expose": {
    "light.desk": {}, "light.hall": {}}}}})
check("first connect", (ha_changed(none, connected), ha_changed(no_url, connected)), (False, False))
check("a changed list", ha_changed(connected, more), True)

# Keys an extension marks optional are never missing.
host = Host(none, api="http://127.0.0.1:1")
host.described["x"] = {"settings": {
    "secrets": {"key": "WINSTON_UI_CASE_KEY", "extra": "WINSTON_UI_CASE_EXTRA"},
    "schema": {"properties": {"extra": {"secret": {"optional": True}}}}}}
check("an optional key is not needed", host.missing_secrets("x"), ["WINSTON_UI_CASE_KEY"])

# The keys an MCP server gets can be stored and are listed; a variable that steers processes still cannot.
mcp = WinstonConfig.model_validate({**base, "extensions": {"x": {"mcp": {"command": ["x"], "env": [
    "FAKE_KEY", "PATH"]}}}}).model_dump(mode="json")
schema = WinstonConfig.model_json_schema()
check("an MCP server's keys", (secrets.references(mcp, schema).get("FAKE_KEY"), secrets.allowed("FAKE_KEY", mcp, schema),
                               secrets.allowed("PATH", mcp, schema)),
      ([["extensions", "x", "mcp", "env"]], True, False))
secrets.restarts(mcp, schema, [["extensions", "x", "mcp", "env"]])      # steps no further than the list

# A phone is named as its device is in Home Assistant; without the device registry, by its service in words.
class FakeHA:
    devices_fail = False

    def __init__(self, cfg): pass
    async def get(self, path): return [{"domain": "notify", "services": {
        "mobile_app_galaxy_s24": {}, "mobile_app_sam_s_pixel": {}, "persistent_notification": {}}}]

    async def devices(self):
        if FakeHA.devices_fail:
            raise RuntimeError("not an admin token")
        return [{"name": "Galaxy S24", "identifiers": [["mobile_app", "a"]]},
                {"name": "Sam's Pixel", "name_by_user": "My Pixel", "identifiers": [["mobile_app", "b"]]}], {}


def phones() -> list[str]:
    return [c.label for c in asyncio.run(options_api.options("ha_notify", connected, {})).choices]


options_api.HomeAssistant = FakeHA
check("phones by their device's name", phones(), ["Galaxy S24", "My Pixel"])
FakeHA.devices_fail = True
check("by the service without the registry", phones(), ["Galaxy s24", "Sam s pixel"])


# An extension's own list for a picker in its settings, asked of it while it runs; else why there is none.
class Proc:
    state, is_mcp_server, answer = "running", False, None

    async def call(self, method, timeout=30, **params):
        if self.answer is None:
            raise TimeoutError
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


lists = Host(none, api="http://127.0.0.1:1")
lists.described["ex"] = {"manifest": {"name": "Ex"}}
lists.enabled = lambda ext_id: True
lists.procs["ex"] = proc = Proc()


def own_list() -> tuple:
    r = asyncio.run(options_api.options("extension_choices", none, {"extensions": lists, "extension": "ex",
                                                                    "choices": "playlists"}))
    return r.ok, r.source, [(c.value, c.image) for c in r.choices]


proc.answer = {"ok": True, "choices": [{"value": "Mix", "label": "Mix", "image": "https://i.example/c.jpg"}]}
check("its list", own_list(), (True, "ex", [("Mix", "https://i.example/c.jpg")]))
proc.answer = None
check("it did not answer", own_list()[:2], (False, "Ex gave no list: it did not answer in time"))
proc.state = "restarting"
check("it is not running", own_list()[:2], (False, "Ex is not running, so there is no list to pick from."))

# Settings that did not load, in words: each by its setting's title; what the extension said goes to its log.
lists.described["ex"]["settings"] = {"schema": {"properties": {"account": {"title": "Your whole library"}}},
                                     "secrets": {}}
proc.id = "ex"


def not_loaded() -> tuple[str, str]:
    try:
        asyncio.run(lists._configure(proc))
    except RuntimeError as e:
        return failure_words(e), failure_text(e)
    return "", ""


proc.answer = {"ok": False, "errors": [{"field": "account", "message": "Input should be a valid string"},
                                       {"field": None, "message": "Value error, pick a speaker first"}]}
check("refused by the extension", not_loaded(), (
    "its settings did not load: Your whole library: should be text; pick a speaker first",
    "ValueError: account: Input should be a valid string; settings: Value error, pick a speaker first"))
proc.answer = MCPError(INVALID_PARAMS, "winston/configure: secrets.account: Input should be a valid integer, unable")
check("refused by an older SDK", not_loaded(), (
    "its settings did not load: Your whole library: should be a whole number",
    "MCPError: winston/configure: secrets.account: Input should be a valid integer, unable"))
check("a missing one", problem_words("Field required"), "not set")
check("a nameless MCP server's name", [server_name(i) for i in ("pc", "x", "home_lab", "a1")],
      ["PC", "X", "Home lab", "A1"])


# A room box's connection, as the satellite channel reports it.
cfg = WinstonConfig.model_validate(base)
cfg.channels.satellite.devices = {"kitchen": SatelliteDevice(host="10.0.0.6")}
rooms = Rooms(cfg)
rooms.subscribe("office")


def state() -> dict:
    return {r.name: (r.connected, r.since is not None) for r in rooms.states()}


check("a box never connected; a room without one", state(),
      {"kitchen": (False, False), "office": (None, False)})
rooms.box("kitchen", False)
check("a box that never connected cannot go away", state()["kitchen"], (False, False))
rooms.box("Kitchen", True)
check("connected", state()["kitchen"], (True, True))
rooms.box("kitchen", False)
check("and gone since", state()["kitchen"], (False, True))


# Saving a config without a part stops it and drops it (never a real process here).
class Driver:
    stops = 0

    async def running(self): return self.stops == 0
    async def stop(self): self.stops += 1
    def log_path(self): return None
    def tail(self, n): return []


async def switched_off():
    sup = Supervisor(WinstonConfig.model_validate({**base, "channels": {"voice": {"port": free_port()}}}))
    sup.items["voice"].driver = driver = Driver()
    off = WinstonConfig.model_validate({**base, "channels": {"voice": {"enabled": False}}})
    waiting = await sup.reload(off)
    return waiting, "voice" in sup.items, driver.stops

check("off means off", asyncio.run(switched_off()), ([], False, 1))

finish_checks("ui data: reach, tool lines, errors, new conversations, first connect, optional and MCP keys, phones, "
              "extensions' own lists, settings that did not load, boxes and stops ok")
