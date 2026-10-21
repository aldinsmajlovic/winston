"""winstond: the control service. Supervises engines and channels, exposes the API the web UI
and the CLI use, and serves the built UI from web/dist. Bound to localhost by default; put it
behind auth before exposing it."""
import asyncio
import html
import json
import re
import uuid
from collections import OrderedDict
from datetime import date, datetime, timedelta
import subprocess
from collections.abc import Callable, Iterable
from contextlib import asynccontextmanager
from pathlib import PurePosixPath
from typing import Annotated, Any, Literal
from urllib.parse import quote, urlsplit

import httpx
from loguru import logger
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.routing import Route as StarletteRoute
from pydantic import BaseModel, ConfigDict, Field, ValidationError, create_model, field_validator

from winston import __version__
from winston.api import secrets
from winston.api.options import PROVIDERS as OPTION_NAMES, HomeAssistantCheck, Options, check_home_assistant, options
from winston.config import load, repo_root, save
from winston.config.loader import apply_changes, get_in, version as file_version
from winston.config.presets import LLM, PROFILES, TTS, stt_presets
from winston.config.restarts import BRAIN_CHANNELS, control_sections, restarts_for
from winston.config.schema import LLMConfig, TTSStreamingConfig, WinstonConfig
from winston.channels.web import WebChat
from winston.core.announce import Announcement, Delivery, Hub, nudge_note
from winston.core.common import failure_words, key
from winston.core.brain import api_key
from winston.core import memory, persona, speaker, turns, voices
from winston.core.live import Feed, LiveEvent, know_extension_tools, tool_icon, tool_label
from winston.core.rooms import BoxState, MicHold, Rooms, RoomState, SyncRequest, SyncResult
from winston.engines.supervisor import Supervisor
from winston.extension_host import accounts
from winston.extension_host.api import LogLines, router as extensions_router
from winston.extension_host.host import Host, Pending
from winston.extension_host.nudges import Nudges
from winston.extension_host.scheduler import Scheduler
from winston.extension_host.outward import build as outward_mcp
from winston.extension_host.services import Services as ExtensionServices
from winston.providers.home_assistant import (APPLY_TIMEOUT, ExposurePlan, HAApply, HAExposure, HARegistry,
                                              HATool, HomeAssistant, apply_saved, exposure_view, ha_changed,
                                              registry_view)


class EngineStatus(BaseModel):
    name: str
    kind: Literal["engine", "channel"]
    driver: str
    state: Literal["stopped", "starting", "healthy", "unhealthy", "error"]
    detail: str
    since: float
    port: int
    startup_timeout: int
    started_in: float | None = None     # seconds the last start took, for "about N minutes"
    config_changed: bool = False        # saved settings or secrets wait for a restart (pending or stale)


class GpuStatus(BaseModel):
    name: str
    used_mb: int
    total_mb: int


class ExtensionBrief(BaseModel):
    id: str
    name: str
    state: str
    detail: str = ""


class Status(BaseModel):
    version: str
    assistant: str
    engines: list[EngineStatus]
    gpu: GpuStatus | None = None
    extensions: list[ExtensionBrief] = Field(default_factory=list, description="Extensions switched on")
    # Saved since winstond started and read only when it starts: config sections ("announce") and
    # secrets ("HA_TOKEN"). They apply at the next `winston up`; nothing here restarts winstond.
    pending_control: list[str] = []
    talking: bool = Field(False, description="A conversation ran on any channel within 30 s; background model "
                                             "work (a box's prewarm, extension calls) waits")


class Ok(BaseModel):
    ok: bool = True


class ChatMessage(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    room: str | None = Field(None, max_length=60, description="The room the page is set to; its conversation is "
                                                               "shared with the room's box. None: the household's.")


class HomeAssistantTry(BaseModel):
    url: str | None = Field(None, max_length=500, description="Address to try instead of the saved one")
    token: str | None = Field(None, max_length=4000, description="Token to try instead of the saved one; "
                                                                 "used for this check only, never kept")


class SpeakerVolume(BaseModel):
    room: str | None = Field(None, max_length=60, description="The box's room")
    volume: float = Field(ge=0, le=1, description="Its media player's volume, as its dial sets it")


class SpeakersStopped(BaseModel):
    ext: str = Field(max_length=64, description="The extension whose stream was playing")
    room: str | None = Field(None, max_length=60, description="Where he stopped it")


class StartOver(BaseModel):
    room: str | None = Field(None, max_length=60, description="The room the page is set to: its conversation ends "
                                                               "for its box and screens too. None: the household's.")


def _partial(model: type[BaseModel], name: str, doc: str) -> type[BaseModel]:
    """model's fields, same names and bounds, each optional and unset unless sent. Its own validators do not
    run here: the caller merges what was sent onto a saved value and validates the whole with model."""
    fields = {n: (Annotated[(f.annotation, *f.metadata)] if f.metadata else f.annotation,
                  Field(None, title=f.title, description=f.description)) for n, f in model.model_fields.items()}
    return create_model(name, __config__=ConfigDict(extra="forbid"), __doc__=doc, **fields)


SpeakStreaming = _partial(TTSStreamingConfig, "SpeakStreaming",
                          "tts.streaming for this request only; unset fields keep the saved values.")


class SpeakRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    voice: str | None = Field(None, min_length=1, max_length=200,
                              description="Speak in this voice instead of tts.voice (a clip name under the "
                                          "server's voices directory, or a voice the server knows)")
    streaming: SpeakStreaming | None = Field(  # type: ignore[valid-type]
        None, description="Try these streaming settings instead of the saved ones. Reaches the speech server "
                          "only when its engine command uses {tts.streaming.*}; otherwise ignored (X-Streaming).")

    @field_validator("voice")
    @classmethod
    def _voice_stays_inside(cls, v: str | None) -> str | None:
        # the same refusal as the speech servers' own (a name must stay under their voices directory)
        if v is not None and ("\\" in v or "\0" in v or v.startswith("/") or ".." in PurePosixPath(v).parts):
            raise ValueError("a voice is a name under the voices directory: no '..', absolute path, backslash or NUL")
        return v


class TurnMarkBody(BaseModel):
    """A mistake noted on a turn (core/turns.py): its kind, and when known what you actually said, what
    you wanted done, and the tools it should and must not call (the brain trials' substrings)."""
    kind: Literal["misheard", "not_addressed", "wrong_action", "too_slow"]
    said: str | None = None
    wanted: str | None = None
    expect: list[str] | None = None
    forbid: list[str] | None = None
    note: str | None = None


class TurnMark(TurnMarkBody):
    by: str = "page"
    ts: float = 0


class TurnTool(BaseModel):
    name: str
    args: dict[str, Any] = {}
    ok: bool | None = None
    result: str | None = None
    label: str = Field("", description="How the live feed words it: \"Turned on Kitchen lights\"")
    icon: str | None = Field(None, description="Its extension's icon (a lucide name, or simple-icons:<slug>), for an extension's tool")


class TurnRecord(BaseModel):
    """One spoken turn as the record keeps it (docs/understanding.md, part 1)."""
    model_config = ConfigDict(extra="ignore")
    id: str
    ts: float
    day: str
    channel: str
    room: str | None = None
    start: str
    secs: float = 0
    heard: str
    raw: str | None = None
    engine: str | None = None
    stt_ms: int | None = None
    first_token_ms: int | None = None
    first_audio_ms: int | None = None
    tools: list[TurnTool] = []
    said: str = ""
    outcome: str
    audio: str | None = None
    mark: TurnMark | None = None


class TurnDay(BaseModel):
    enabled: bool = Field(description="Whether turns are being kept (logging.turns.enabled)")
    day: str
    days: list[str] = Field(description="Every day with turns, newest first")
    turns: list[TurnRecord]
    diary: str | None = Field(None, description="His diary entry for the day, once he has written it")


class ClonedVoice(BaseModel):
    slug: str
    name: str
    voice: str              # the name the speech server knows it by, "friends/<slug>"
    created: float
    seconds: float
    consent_text: str       # what the consent recording was heard to say
    consent_score: float | None = None        # same-voice check (core/speaker); None: not checked
    consent_checked_at: float | None = None
    consent_model: str | None = None
    same_voice_at: float = speaker.SAME_VOICE  # the score a clone needs to be kept


class MemoryFact(BaseModel):
    id: str                 # the stored line, text and stamp; edits and deletes name the fact by it
    text: str
    noted: str | None       # YYYY-MM-DD
    until: str | None = None    # YYYY-MM-DD: the last day it is in his prompt
    expired: bool = False       # until has passed: kept here, left out of his prompt


class ClearedFact(MemoryFact):
    topic: str
    index: int              # where it was among the topic's facts, for Undo


class MemoryTopic(BaseModel):
    name: str               # the file's stem
    title: str
    description: str = ""   # what belongs here; the memory tools show it to the model
    facts: list[MemoryFact]


class MemoryView(BaseModel):
    enabled: bool
    topics: list[MemoryTopic]
    chars: int              # of facts in every conversation's prompt
    limit: int              # past this, memory costs latency on every turn


class NewFact(BaseModel):
    fact: str = Field(min_length=1, max_length=1000)
    index: int | None = Field(None, ge=0, description="Position among the topic's facts; the end when unset")
    noted: date | None = Field(None, description="When it was first noted, for Undo; today when unset")
    until: date | None = Field(None, description="Its last day, for Undo; from a date still to come in the fact "
                                                 "when unset")


class FactEdit(BaseModel):
    id: str
    fact: str = Field(min_length=1, max_length=1000)
    until: date | None = Field(None, description="Its last day; null clears it. Left out, the fact keeps its "
                                                 "until unless the new text has a date still to come")


class NewTopic(BaseModel):
    title: str = Field(min_length=1, max_length=60)
    description: str | None = Field(None, max_length=200, description="What belongs here; a plain default when unset")


class TopicRename(BaseModel):
    title: str = Field(min_length=1, max_length=60)
    description: str | None = Field(None, max_length=200, description="What belongs here; kept when unset, "
                                                                        "removed when empty")


class DeletedTopic(BaseModel):
    text: str               # the whole file, for Undo


class TopicRestore(BaseModel):
    text: str = Field(max_length=200_000)


class AllFacts(BaseModel):
    facts: list[str] = Field(max_length=2000)


class Persona(BaseModel):
    title: str
    behaviour: list[str]    # "How you behave": his manner, one rule per line
    instructions: list[str]  # the technical ones


class PersonaEdit(BaseModel):
    behaviour: list[str] = Field(max_length=500)
    instructions: list[str] = Field(max_length=500)


class Forgotten(BaseModel):
    index: int              # where it was, for Undo


class PersonaRename(BaseModel):
    old: str
    new: str
    lines: list[str]        # the persona file's lines that say the old name as a whole word, as they are now
    places: int             # whole-word occurrences in those lines
    renamed: bool = False   # the save rewrote them (persona.md.bak keeps the previous file)


class ConfigResult(BaseModel):
    saved: bool
    restarts: list[str]     # engines that must restart before the change is live; "control" is winstond itself
    version: str            # the saved file's version after this call (unchanged by a dry run), as in ETag
    # Set when the save changed the Home Assistant list, domains or switch and Home Assistant is on: what
    # applying it there did. A Home Assistant that is away never fails the save; applied says false.
    ha_apply: HAApply | None = None
    # The same, on a dry run: what the save would do there, or why that could not be read.
    ha_plan: ExposurePlan | None = None
    ha_plan_error: str | None = None
    # Set when the change renames him and his persona file says the old name: what a rename would
    # rewrite there (dry run), or what it did (a save with rename_persona).
    persona_rename: PersonaRename | None = None


class ConfigChange(BaseModel):
    path: list[str | int] = Field(min_length=1, description="Keys from the config root, e.g. [\"tts\", \"voice\"]")
    value: Any = Field(None, description="Any JSON value; null removes the key, so the default applies")


class ConfigPatch(BaseModel):
    changes: list[ConfigChange] = Field(min_length=1, max_length=500)


class ConfigConflict(BaseModel):
    detail: str
    paths: list[list[str | int]]    # the changed paths someone else changed since the If-Match version
    version: str                    # the version on disk now


class SecretStatus(BaseModel):
    name: str
    set: bool                       # holds a non-empty value; the value itself never leaves winstond
    used_by: list[list[str]]        # config paths that name it
    restarts: list[str]             # what reads it: restarting these applies a new value


class SecretValue(BaseModel):
    value: str = Field(min_length=1, max_length=secrets.MAX_LEN)


class SecretResult(BaseModel):
    name: str
    set: bool
    restarts: list[str]             # marked as waiting for a restart (config_changed in status)


def _page(text: str) -> HTMLResponse:
    """The page the browser lands on after a sign-in: one line, light or dark as the system is."""
    return HTMLResponse('<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
                        '<title>Winston</title><style>body{font:16px system-ui,sans-serif;margin:3rem 1rem;color:#222;'
                        'background:#fff}@media (prefers-color-scheme:dark){body{color:#ddd;background:#111}}</style>'
                        f'<p>{html.escape(text)}</p>')


class AccountStart(BaseModel):
    url: str                        # the service's sign-in page, to open
    redirect: str                   # where the service sends the browser back: its app must list this address


class Preset(BaseModel):
    id: str
    title: str
    hint: str
    icon: str | None = None                 # a logo or kind the UI draws next to the title
    needs: list[str]
    engine: dict[str, Any] | None = None    # EngineConfig fields for the slot, None for in-process adapters
    settings: dict[str, Any]                # section fields that go with it (llm, tts, stt)


class Profile(BaseModel):
    id: str
    title: str
    hint: str
    presets: dict[str, str]


class Presets(BaseModel):
    llm: list[Preset]
    tts: list[Preset]
    stt: list[Preset]
    profiles: list[Profile]


def gpu_status() -> GpuStatus | None:
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.used,memory.total", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=3, check=False).stdout.strip().split(", ")
        return GpuStatus(name=out[0], used_mb=int(out[1]), total_mb=int(out[2]))
    except Exception:  # noqa: BLE001
        return None


def _events(q: asyncio.Queue, close: Callable[[], object],
            encode: Callable[[Any], str] = lambda ev: json.dumps(ev, ensure_ascii=False),
            head: Iterable[str] = ()) -> StreamingResponse:
    """Server-sent events: head, then each item from q as encode(item), with a keepalive after 15 s without one
    (which also lets a dead client be noticed), and close() once the client has gone."""
    async def gen():
        try:
            for line in head:
                yield line
            while True:
                try:
                    yield f"data: {encode(await asyncio.wait_for(q.get(), timeout=15))}\n\n"
                except TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            close()
    return StreamingResponse(gen(), media_type="text/event-stream")


def _dump(cfg: WinstonConfig) -> dict:
    return cfg.model_dump(mode="json", exclude_none=True)


def _etag(version: str) -> str:
    return f'"{version}"'


def _if_match(header: str | None) -> str | None:
    """The version an If-Match header names ("abc", W/"abc" or abc); None when absent or *."""
    if not header or header.strip() == "*":
        return None
    return header.strip().removeprefix("W/").strip('"') or None


RENAME_PERSONA = ("When the change renames him: also rewrite the old name as a whole word in his persona file "
                  "(persona_rename in the dry run lists where), keeping persona.md.bak")
MISSING = object()


def create_app(cfg: WinstonConfig | None = None, autostart: bool = True) -> FastAPI:
    cfg = cfg or load()
    sup = Supervisor(cfg)
    hub = Hub(cfg)
    feed = Feed()
    rooms = Rooms(cfg, on_mic=lambda room, held: feed.publish(
        LiveEvent(channel="rooms", type="mic", room=room, held_by=held)))
    web = WebChat(cfg, feed, rooms)
    extensions = Host(cfg, api=cfg.control.url)
    extension_services = ExtensionServices(extensions)
    extension_services.talking = feed.talking             # background model calls yield to conversations

    def llm_up() -> bool:
        """The brain's server (engines.llm) is healthy, or Winston neither runs nor checks one."""
        m = sup.items.get("llm")
        return m is None or m.state == "healthy"
    extension_services.llm_up = llm_up                    # and wait for it to load
    extensions.on_feed = lambda ev: feed.publish(LiveEvent(**ev))
    extensions.scheduler = Scheduler(extensions, extension_services)
    mcp_out = outward_mcp(extensions)
    mcp_app = mcp_out.streamable_http_app(streamable_http_path="/mcp", stateless_http=True, json_response=True,
                                          host=httpx.URL(cfg.control.url).host)
    tasks: set[asyncio.Task] = set()

    def spawn(coro):
        """Run in the background, holding a reference so the task is not collected mid-way."""
        t = asyncio.create_task(coro); tasks.add(t); t.add_done_callback(tasks.discard)

    def publish_announcement(a: Announcement) -> Delivery:
        """The hub delivers to the channels; the feed and the web conversation get it too."""
        d = hub.publish(a)
        feed.publish(LiveEvent(channel=a.source, type="announcement", text=a.text))
        spawn(web.announced(a.text, a.context))
        return d

    def ha_announcement(data: dict):
        """An automation's announce.ha_event, {text, context}, from Home Assistant's websocket (the scheduler's)."""
        try:
            publish_announcement(Announcement(text=str(data.get("text", "")).strip(),
                                              context=data.get("context") or {}, source="home_assistant"))
        except ValidationError as e:
            logger.warning(f"ignored {cfg.announce.ha_event} with bad data {data}: {e.errors()[0]['msg']}")
    if cfg.announce.enabled and cfg.announce.ha_event:      # read at start, like every control section
        extensions.scheduler.announce_event, extensions.scheduler.on_announce = cfg.announce.ha_event, ha_announcement

    # Speaking up: the engine decides (extension_host/nudges.py); here is how a nudge gets out.

    async def to_phone(message: str, tag: str, actions: list[dict[str, str]], url: str | None = None) -> bool:
        """A notification through the notify service picked under speaking up (the companion app's),
        with buttons that come back as mobile_app_notification_action events (or open a link: action
        URI), and a tap that opens `url`."""
        service = extensions.cfg.speak_up.phone
        if not service:
            return False
        data: dict = {"tag": tag, "actions": actions}
        if url:
            data |= {"url": url, "clickAction": url}           # iOS reads url, Android clickAction
        try:
            async with extension_services._ha_client() as c:
                r = await c.post(f"/api/services/notify/{service.removeprefix('notify.')}",
                                 json={"title": extensions.cfg.assistant.name, "message": message, "data": data})
            if r.status_code >= 300:
                logger.warning(f"phone: notify.{service} answered {r.status_code}: {r.text[:200]}")
            return r.status_code < 300
        except Exception as e:                                          # noqa: BLE001
            logger.warning(f"phone: {e}")
            return False

    async def deliver_nudge(ev: dict, where: str) -> str | None:
        if where == "speakers" and (n := hub.publish_nudge(ev, rooms.recent())):
            spawn(web.noted(nudge_note(ev), ev["say"]))
            return f"speakers:{n}"
        links = ev.get("links") or []
        if links:           # its links are its buttons; a tap opens the first
            buttons = [{"action": "URI", "title": link["title"][:30], "uri": link["url"]} for link in links[:3]]
        else:
            later = "When I'm home" if extensions.cfg.speak_up.home else "In two hours"
            buttons = [{"action": f"WINSTON_LATER_{ev['id']}", "title": later},
                       {"action": f"WINSTON_DISMISS_{ev['id']}", "title": "Done"}]
        if await to_phone(ev["say"], ev["id"], buttons, url=links[0]["url"] if links else None):
            return "phone"
        text = "\n".join([ev["say"], *(f"{link['title']}: {link['url']}" for link in links)])
        if hub.publish_discord({"type": "nudge", **ev, "text": text, "context": {}}):
            return "discord"
        return None

    async def home_now() -> bool | None:
        if not extensions.cfg.speak_up.home:
            return None
        return bool((await extension_services.presence())["home"])

    class Speakers:
        """What extensions play on the speakers (ctx.speakers): the satellite box in the room you are in."""

        def play(self, ext_id: str, url: str, title: str | None) -> int:
            n = hub.publish_media({"type": "media", "action": "play", "id": uuid.uuid4().hex[:12], "ext": ext_id,
                                   "url": url, "title": title}, rooms.recent())
            logger.info(f"speakers: {ext_id} plays {title or url} on {n}")
            return n

        def stop(self, ext_id: str) -> None:
            hub.publish_media({"type": "media", "action": "stop", "ext": ext_id})
            logger.info(f"speakers: {ext_id} stops")

        def volume(self, level: float | None, change: float) -> float | None:
            """The volume of the box in the room you are in (the only box, else none), as it last reported it,
            set or moved; None while no box has reported one."""
            room = rooms.recent()
            only = next(iter(box_volume.values())) if len(box_volume) == 1 else None
            now = box_volume.get(key(room)) if room else only
            if now is None:
                return None
            want = round(min(1.0, max(0.0, level if level is not None else now + change)), 2)
            if want != now:
                hub.publish_media({"type": "media", "action": "volume", "level": want}, room)
                logger.info(f"speakers: volume {now:.2f} to {want:.2f}{f' in {room}' if room else ''}")
            return want
    extensions.speakers = Speakers()
    box_volume: dict[str, float] = {}           # room -> its box's volume, as each box last reported it

    nudges = Nudges(extensions, deliver=deliver_nudge, busy=feed.talking, home=home_now,
                    feed=lambda ev: feed.publish(LiveEvent(**ev)))
    extensions.nudges = nudges

    def confirm_on_phone(p: Pending):
        where = {"web": "on the Talk page", "voice": "on the Talk page", "satellite": "on a room box",
                 "discord": "on Discord", "text": "in a terminal"}.get(p.channel, "here")
        spawn(to_phone(f"{p.label}? Asked for {where}.", f"confirm-{p.id}",
                       [{"action": f"WINSTON_CONFIRM_{p.id}", "title": "Confirm"},
                        {"action": f"WINSTON_DECLINE_{p.id}", "title": "Decline"}]))
    extensions.on_pending = confirm_on_phone

    def phone_action(data: dict):
        """A button on one of his notifications."""
        action = str(data.get("action") or "")
        m = re.fullmatch(r"WINSTON_(LATER|DISMISS|CONFIRM|DECLINE)_(\w+)", action)
        if m is None:
            return
        what, ident = m.groups()
        logger.info(f"phone: {what.lower()} {ident}")
        if what in ("CONFIRM", "DECLINE"):
            spawn(extensions.confirm(ident, what == "CONFIRM"))
        elif what == "LATER":
            spawn(nudges.follow_up(ident, "later", "when I'm home" if extensions.cfg.speak_up.home else "in 2 hours",
                                   channel="phone"))
        else:
            spawn(nudges.follow_up(ident, "dismiss", channel="phone"))
    extensions.scheduler.on_phone_action = phone_action

    async def _diary_loop():
        """Write any missing diary entries on startup, then check again after midnight."""
        from winston.core.diary import Diary
        diary = Diary(cfg)
        while not llm_up():          # the LLM engine loads first
            await asyncio.sleep(2)
        await diary.write_pending(talking=feed.talking)
        while True:
            now = datetime.now()
            tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=5, second=0, microsecond=0)
            await asyncio.sleep((tomorrow - now).total_seconds())
            await diary.write_pending(talking=feed.talking)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if autostart:
            asyncio.create_task(sup.start_all())
        # Found and described either way, so the page shows them; run only when winstond starts things.
        spawn(extensions.start() if autostart else extensions.refresh())
        extensions.scheduler.start(jobs=autostart)      # Home Assistant's events either way: announcements
        if autostart:
            nudges.start()
            spawn(_diary_loop())
        async with mcp_out.session_manager.run():
            yield
        await nudges.stop()
        await extensions.scheduler.stop()
        await extensions.stop_all()
        await web.close()
        await sup.stop_all(owned_only=True)

    app = FastAPI(title="winstond", version=__version__, lifespan=lifespan)
    app.state.supervisor, app.state.cfg, app.state.hub, app.state.feed = sup, cfg, hub, feed
    app.state.rooms = rooms
    app.state.extensions = extensions
    app.include_router(extensions_router(extensions, extension_services))
    # The tools you offer, for Claude Code and other MCP clients, at exactly /mcp (a mount would want /mcp/).
    app.router.routes.append(StarletteRoute("/mcp", endpoint=mcp_app))
    schema = WinstonConfig.model_json_schema()

    def full_schema() -> dict:
        """The config schema with each described extension's settings grafted in, so key fields,
        field errors and the settings form work for extensions too."""
        return extensions.graft(schema)
    # Config versions this winstond has seen, so a patch made against an older one can be merged:
    # only a path that also changed since then is a conflict.
    history: OrderedDict[str, dict] = OrderedDict()
    started = _dump(cfg)            # what winstond itself runs with until the next `winston up`
    control_secrets: set[str] = set()
    config_lock = asyncio.Lock()

    def remember(version: str, c: WinstonConfig):
        history[version] = _dump(c)
        history.move_to_end(version)
        while len(history) > 64:
            history.popitem(last=False)

    app.state.version = file_version()
    remember(app.state.version, cfg)

    def current() -> tuple[WinstonConfig, str]:
        """The saved config and its version. A file edited by hand since winstond saw it is read afresh."""
        v = file_version()
        if v == app.state.version:
            return app.state.cfg, v
        try:
            c = load()
        except Exception as e:                                          # noqa: BLE001
            raise HTTPException(409, f"config.yaml was changed on disk and does not load: {e}")
        remember(v, c)
        return c, v

    @app.middleware("http")
    async def same_site_writes(request: Request, call_next):
        """Every write (config, engines, memory, secrets) must come from a page on this server: a changed
        engine command runs at its next start, so a foreign page must not reach it through DNS rebinding."""
        if request.method not in ("GET", "HEAD", "OPTIONS") and (why := secrets.cross_site(request)):
            return JSONResponse({"detail": why}, status_code=403)
        return await call_next(request)

    @app.exception_handler(RequestValidationError)
    async def invalid(request: Request, exc: RequestValidationError):
        """FastAPI's 422, without the refused input for a secret."""
        errors = jsonable_encoder(exc.errors())
        if request.url.path.startswith(("/api/secrets", "/api/providers/home_assistant/check")):
            # never echo a pasted key back (FastAPI puts the refused body in `input`)
            errors = [{k: v for k, v in e.items() if k not in ("input", "ctx")} for e in errors]
        return JSONResponse({"detail": errors}, status_code=422)

    def status_payload() -> Status:
        return Status(
            version=__version__, assistant=app.state.cfg.assistant.name,
            engines=[EngineStatus(name=m.name, kind=m.kind, driver=m.cfg.driver, state=m.state, detail=m.detail,
                                  since=m.since, port=m.cfg.port, startup_timeout=m.cfg.startup_timeout,
                                  started_in=m.started_in,
                                  config_changed=bool(m.pending or m.stale)) for m in sup.items.values()],
            gpu=gpu_status(),
            extensions=[ExtensionBrief(id=i, name=extensions.described.get(i, {}).get("manifest", {}).get("name", i),
                                       state=p.state, detail=p.detail)
                        for i, p in sorted(extensions.procs.items()) if extensions.enabled(i)],
            pending_control=control_sections(started, _dump(app.state.cfg), schema) + sorted(control_secrets),
            talking=feed.talking(),
        )

    def managed(name: str):
        if name not in sup.items:
            raise HTTPException(404, f"no engine {name}")

    @app.get("/api/health", response_model=Ok)
    async def health():
        return Ok()

    @app.get("/api/status", response_model=Status)
    async def status():
        await sup.refresh()
        return status_payload()

    @app.get("/api/status/stream", summary="Status every 2 s as server-sent events (Status objects)")
    async def status_stream():
        async def gen():
            while True:
                await sup.refresh()
                yield f"data: {status_payload().model_dump_json()}\n\n"
                await asyncio.sleep(2)
        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.post("/api/announce", response_model=Delivery,
              summary="Say something unprompted: chime and speech on connected speakers, Discord per config")
    async def announce(a: Announcement):
        if not app.state.cfg.announce.enabled:
            raise HTTPException(409, "announcements are off (announce.enabled)")
        return publish_announcement(a)

    @app.get("/api/events", summary="Announcements and nudges for one channel session, as server-sent events")
    async def events(channel: Literal["voice", "satellite", "discord"] = Query(...),
                     room: str | None = Query(None, description="The room the session is in: nudges go to the "
                                                                "room you are in")):
        q = hub.subscribe(channel, room)
        return _events(q, lambda: hub.unsubscribe(channel, q), head=[": subscribed\n\n"])

    @app.get("/api/live", summary="What he hears, says and does: recent events, then new ones, as "
                                  "server-sent events (LiveEvent objects)")
    async def live():
        q = feed.subscribe()

        def encode(ev: LiveEvent) -> str:
            return ev.model_dump_json(exclude_none=True)
        return _events(q, lambda: feed.unsubscribe(q), encode, [f"data: {encode(ev)}\n\n" for ev in feed.backlog()])

    @app.post("/api/live", response_model=Ok, summary="Events from a channel process into the live feed")
    async def live_publish(events: list[LiveEvent]):
        for ev in events:
            feed.publish(ev)
        return Ok()

    @app.post("/api/chat", response_model=Ok,
              summary="Say something to him in the web conversation; the reply arrives on /api/live")
    async def chat(m: ChatMessage):
        spawn(web.ask(m.text.strip(), (m.room or "").strip() or None))
        return Ok()

    @app.post("/api/chat/start-over", response_model=Ok,
              summary="New conversation: the room's, for every box and screen in it, or the household's typed one")
    async def chat_start_over(m: StartOver):
        await web.start_over((m.room or "").strip() or None)
        feed.publish(LiveEvent(channel="web", type="tool", call=uuid.uuid4().hex[:12], name="start_over",
                               status="ok", label=tool_label("start_over", None, "ok")))
        return Ok()

    @app.post("/api/speakers/volume", response_model=Ok,
              summary="A box's own volume, as its dial or a command left it (the box reports each change)")
    async def speaker_volume(m: SpeakerVolume):
        box_volume[key(m.room or "")] = m.volume
        return Ok()

    @app.post("/api/speakers/stopped", response_model=Ok,
              summary="He stopped what an extension plays on the speakers (\"stop\" to a box): its on_speakers_stop runs")
    async def speakers_stopped(m: SpeakersStopped):
        n = extensions.scheduler.speakers_stopped(m.ext)
        logger.info(f"speakers: stopped by voice{f' in {m.room}' if m.room else ''}; {m.ext} told ({n} handler(s))")
        return Ok()

    @app.get("/api/rooms", response_model=list[RoomState],
             summary="Rooms: the satellite boxes' and any a browser session named, with who has each mic")
    async def list_rooms():
        sat = sup.items.get("satellite")
        if sat is None or sat.state in ("stopped", "error"):     # the boxes' channel is down, so they are too
            for name in rooms.cfg.channels.satellite.devices:
                rooms.box(name, False)
        return rooms.states()

    @app.post("/api/rooms/{room}/sync", response_model=SyncResult,
              summary="A session in a room hands over its new messages and takes in the others' (core/rooms.py)")
    async def room_sync(room: str, req: SyncRequest):
        return rooms.sync(room, req)

    @app.put("/api/rooms/{room}/box", response_model=Ok,
             summary="A room's box connected or went away (the satellite channel reports each change)")
    async def room_box(room: str, b: BoxState):
        rooms.box(room, b.connected)
        return Ok()

    @app.post("/api/rooms/{room}/mic", response_model=Ok,
              summary="Hold the room's mic for secs (a box that woke up); secs 0 lets go")
    async def room_mic(room: str, h: MicHold):
        rooms.hold(room, h.by, h.secs)
        return Ok()

    @app.get("/api/rooms/{room}/mic", summary="Who has the room's mic, now and on every change, as server-sent "
                                              "events ({room, held_by})")
    async def room_mic_events(room: str):
        q = rooms.subscribe(room)
        return _events(q, lambda: rooms.unsubscribe(room, q))

    @app.post("/api/speak", response_class=StreamingResponse,
              responses={200: {"content": {"audio/pcm": {}}, "description": "16-bit mono PCM, rate in X-Sample-Rate",
                               "headers": {
                                   "X-Sample-Rate": {"schema": {"type": "string"}, "description": "PCM rate in Hz"},
                                   "X-Voice": {"schema": {"type": "string"},
                                               "description": "The voice sent to the speech server, percent-encoded"},
                                   "X-Streaming": {"schema": {"type": "string", "enum": ["on", "off", "ignored"]},
                                                   "description": "on or off: the streaming this request used, when "
                                                                  "the engine takes streaming settings. ignored: "
                                                                  "streaming was sent but the engine does not take "
                                                                  "it (or its server predates per-request "
                                                                  "streaming). Absent otherwise."}}}},
              summary="Text in his voice as streamed 16-bit mono PCM, for read aloud and the Voice page's "
                      "previews; voice and streaming may be tried before they are saved")
    async def speak(s: SpeakRequest):
        cfg = app.state.cfg
        t = cfg.tts
        voice = s.voice or t.voice
        body: dict[str, Any] = {"model": "winston-tts", "input": s.text.strip(), "voice": voice,
                                "response_format": "pcm"}
        headers = {"X-Sample-Rate": str(t.sample_rate), "X-Voice": quote(voice, safe="/._-")}
        eng = cfg.engines.get("tts")
        # the streaming settings reach a server whose command takes them (decisions, 09-23); a hosted
        # OpenAI server could refuse fields it does not know
        takes = eng is not None and any("{tts.streaming." in w for w in [*eng.cmd, *eng.args])
        st, sent = t.streaming, False
        if s.streaming is not None:
            try:
                st = TTSStreamingConfig.model_validate({**t.streaming.model_dump(),
                                                        **s.streaming.model_dump(exclude_unset=True)})
            except ValidationError as e:
                raise RequestValidationError([{**err, "loc": ("body", "streaming", *err["loc"])} for err in
                                              e.errors(include_url=False, include_context=False)])
            if takes:
                body.update(stream=st.enabled, first_secs=st.first_secs, chunk_secs=st.chunk_secs,
                            lookahead_secs=st.lookahead_secs)
                sent = True
            else:
                headers["X-Streaming"] = "ignored"
        c = httpx.AsyncClient(timeout=httpx.Timeout(10, read=60))
        req = c.build_request("POST", f"{t.base_url.rstrip('/')}/audio/speech",
                              headers={"Authorization": f"Bearer {api_key(t.api_key_env)}"}, json=body)
        try:
            r = await c.send(req, stream=True)
        except httpx.HTTPError as e:
            await c.aclose()
            raise HTTPException(503, f"Can't reach the voice server: {failure_words(e)}")
        if r.status_code != 200:
            detail = (await r.aread()).decode(errors="replace")[:200]
            await r.aclose(); await c.aclose()
            raise HTTPException(502, f"speech server answered {r.status_code}: {detail}")

        if takes:
            # the Chatterbox server says what it did; one started before it took streaming per request
            # does not, and then sent settings went unused, while unsent ones are the saved ones
            headers["X-Streaming"] = r.headers.get("x-streaming") or (
                "ignored" if sent else "on" if st.enabled else "off")

        async def gen():
            try:
                async for chunk in r.aiter_bytes():
                    yield chunk
            finally:
                await r.aclose(); await c.aclose()
        return StreamingResponse(gen(), media_type="audio/pcm", headers=headers)

    def clone(slug: str) -> voices.Clone:
        if not (c := next((c for c in voices.clones() if c.slug == slug), None)):
            raise HTTPException(404, f"no cloned voice {slug}")
        return c

    @app.get("/api/voices/clones", response_model=list[ClonedVoice],
             summary="Voices cloned from friends by asking, each with its consent recording")
    async def list_clones():
        return [ClonedVoice(**vars(c), voice=c.voice) for c in voices.clones()]

    @app.get("/api/voices/clones/{slug}/audio", response_class=FileResponse,
             responses={200: {"content": {"audio/wav": {}}}}, summary="A clone's voice clip or its consent recording")
    async def clone_audio(slug: str, kind: Literal["voice", "consent"] = Query("voice")):
        c = clone(slug)
        return FileResponse(voices.friends_dir() / f"{c.slug}{'.consent' if kind == 'consent' else ''}.wav",
                            media_type="audio/wav")

    @app.delete("/api/voices/clones/{slug}", response_model=Ok, summary="Delete a cloned voice and its consent")
    async def delete_clone(slug: str):
        c = clone(slug)
        if app.state.cfg.tts.voice == c.voice:
            raise HTTPException(409, f"{c.name}'s voice is {app.state.cfg.assistant.name}'s voice now; pick another voice first")
        voices.delete(c.slug)
        return Ok()

    def turn_store() -> turns.TurnStore:
        return turns.TurnStore(app.state.cfg)

    def labelled(rows: list[dict]) -> list[dict]:
        """Each tool call worded as the live feed words it, for the page."""
        know_extension_tools(extensions.brain_tools())
        return [{**t, "tools": [{**c, "label": tool_label(c["name"], c.get("args"), "error" if c.get("ok") is False else "ok"),
                                 "icon": tool_icon(c["name"])} for c in t.get("tools", [])]} for t in rows]

    @app.get("/api/turns", response_model=TurnDay, summary="A day's spoken turns: heard, done, said, and any mark")
    async def turns_day(day: str | None = Query(None, description="YYYY-MM-DD; the newest day with turns if left out")):
        store = turn_store()
        days = store.days()
        day = day or (days[0] if days else date.today().isoformat())
        from winston.core.diary import Diary
        try:
            return TurnDay(enabled=store.enabled, day=day, days=days, turns=labelled(store.day(day)),
                           diary=Diary(app.state.cfg).entry(day))
        except ValueError as e:
            raise HTTPException(400, str(e))

    @app.get("/api/turns/marked", response_model=list[TurnRecord], summary="Every marked turn, oldest first")
    async def turns_marked():
        return labelled(turn_store().marked())

    @app.get("/api/turns/{turn_id}/audio", response_class=FileResponse,
             responses={200: {"content": {"audio/flac": {}}}}, summary="A turn's audio, as the speech engine heard it")
    async def turn_audio(turn_id: str):
        try:
            path = turn_store().audio(turn_id)
        except ValueError as e:
            raise HTTPException(400, str(e))
        if path is None:
            raise HTTPException(404, f"no audio for turn {turn_id}")
        return FileResponse(path, media_type="audio/flac")

    @app.put("/api/turns/{turn_id}/mark", response_model=TurnRecord, summary="Mark a turn as a mistake")
    async def mark_turn(turn_id: str, body: TurnMarkBody):
        try:
            return turn_store().mark(turn_id, body.kind, said=body.said, wanted=body.wanted, expect=body.expect,
                                     forbid=body.forbid, note=body.note, by="page")
        except KeyError as e:
            raise HTTPException(404, str(e.args[0]))
        except ValueError as e:
            raise HTTPException(400, str(e))

    @app.delete("/api/turns/{turn_id}/mark", response_model=Ok, summary="Take a turn's mark off")
    async def unmark_turn(turn_id: str):
        try:
            if not turn_store().unmark(turn_id):
                raise HTTPException(404, f"turn {turn_id} has no mark")
        except ValueError as e:
            raise HTTPException(400, str(e))
        return Ok()

    # Diary

    class DiaryEntry(BaseModel):
        date: str
        text: str

    @app.get("/api/diary", summary="Diary entries, newest first")
    async def get_diary(limit: int = Query(14, ge=1, le=365)) -> list[DiaryEntry]:
        from winston.core.diary import Diary
        diary = Diary(app.state.cfg)
        return [DiaryEntry(date=d, text=t) for d, t in diary.entries(limit)]

    @app.get("/api/diary/{day}", summary="One diary entry by date")
    async def get_diary_day(day: str) -> DiaryEntry:
        from winston.core.diary import Diary
        diary = Diary(app.state.cfg)
        text = diary.entry(day)
        if not text:
            raise HTTPException(404, f"No diary entry for {day}")
        return DiaryEntry(date=day, text=text)

    def memory_call(fn, *args):
        try:
            return fn(app.state.cfg, *args)
        except memory.MemoryEditError as e:
            raise HTTPException(400, str(e))

    @app.get("/api/memory", response_model=MemoryView, summary="Everything he remembers, by topic, as stored")
    async def get_memory():
        cfg = app.state.cfg
        return MemoryView(enabled=cfg.memory.enabled, topics=memory.view(cfg), chars=memory.size(cfg),
                          limit=memory.PROMPT_WARN)

    @app.post("/api/memory/topics", response_model=MemoryTopic, summary="A new topic (a new file in the vault)")
    async def add_topic(t: NewTopic):
        name = memory_call(memory.new_topic, t.title, t.description)
        return MemoryTopic(name=name, title=memory.title(app.state.cfg, name),
                           description=memory.description(app.state.cfg, name), facts=[])

    @app.post("/api/memory/expired/clear", response_model=list[ClearedFact],
              summary="Forget every fact whose until has passed, in one commit; returns them for Undo")
    async def clear_expired():
        return memory_call(memory.clear_expired)

    @app.put("/api/memory/topics/{topic}", response_model=MemoryTopic,
             summary="Rename a topic (and its file), and change its description")
    async def rename_topic(topic: str, r: TopicRename):
        name = memory_call(memory.rename_topic, topic, r.title, r.description)
        return next(MemoryTopic(**t) for t in memory.view(app.state.cfg) if t["name"] == name)

    @app.delete("/api/memory/topics/{topic}", response_model=DeletedTopic, summary="Delete a topic and its facts")
    async def delete_topic(topic: str):
        return DeletedTopic(text=memory_call(memory.delete_topic, topic))

    @app.post("/api/memory/topics/{topic}/restore", response_model=Ok, summary="Put a deleted topic back (Undo)")
    async def restore_topic(topic: str, r: TopicRestore):
        memory_call(memory.restore_topic, topic, r.text)
        return Ok()

    @app.put("/api/memory/{topic}/all", response_model=list[MemoryFact],
             summary="Edit all: the topic's facts become these lines, each checked like remember")
    async def replace_facts(topic: str, a: AllFacts):
        return memory_call(memory.replace_all, topic, a.facts)

    @app.get("/api/persona", response_model=Persona, summary="His instructions: how he behaves, and the technical ones")
    async def get_persona():
        return persona.read(app.state.cfg)

    @app.put("/api/persona", response_model=Persona, summary="Rewrite his instructions; only from here, never by a conversation")
    async def put_persona(p: PersonaEdit):
        try:
            return persona.write(app.state.cfg, p.behaviour, p.instructions)
        except persona.PersonaError as e:
            raise HTTPException(400, str(e))

    @app.post("/api/memory/{topic}", response_model=MemoryFact,
              summary="Add a fact, with the remember tool's checks (no secrets, one line, dates made absolute)")
    async def add_fact(topic: str, f: NewFact):
        return memory_call(memory.add, topic, f.fact, f.index, f.noted, f.until)

    @app.put("/api/memory/{topic}", response_model=MemoryFact, summary="Change a fact")
    async def edit_fact(topic: str, f: FactEdit):
        if "until" in f.model_fields_set:
            return memory_call(memory.replace, topic, f.id, f.fact, f.until)
        return memory_call(memory.replace, topic, f.id, f.fact)

    @app.delete("/api/memory/{topic}", response_model=Forgotten, summary="Forget a fact")
    async def forget_fact(topic: str, id: str = Query(..., description="The fact's id from GET /api/memory")):
        return Forgotten(index=memory_call(memory.forget, topic, id))

    @app.api_route("/api/voice/offer", methods=["POST", "PATCH"], include_in_schema=False)
    async def voice_offer(request: Request):
        """The browser's WebRTC offer and ICE candidates, passed to the voice channel, so the page
        needs only winstond's origin."""
        v = app.state.cfg.channels.voice
        if not v.enabled:
            raise HTTPException(409, "The Talk page microphone is off. Switch it on in Ways to talk.")
        body = await request.body()
        if request.method == "POST":
            # The browser client sends its data (the room) as requestData; Pipecat's /api/offer reads only
            # request_data and drops the other without a word.
            try:
                offer = json.loads(body)
                if isinstance(offer, dict) and "requestData" in offer and "request_data" not in offer:
                    offer["request_data"] = offer.pop("requestData")
                    body = json.dumps(offer).encode()
            except ValueError:
                pass
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.request(request.method, f"http://{v.host}:{v.port}/api/offer",
                                    params=request.query_params, content=body,
                                    headers={"content-type": "application/json"})
        except httpx.HTTPError:
            raise HTTPException(503, "The Talk page microphone is not running. Status shows why.")
        return JSONResponse(r.json(), status_code=r.status_code)

    @app.post("/api/engines/{name}/start", response_model=Ok)
    async def start(name: str):
        managed(name)
        asyncio.create_task(sup.start(name))
        return Ok()

    @app.post("/api/engines/{name}/stop", response_model=Ok)
    async def stop(name: str):
        managed(name)
        await sup.stop(name)
        return Ok()

    @app.post("/api/engines/{name}/restart", response_model=Ok)
    async def restart(name: str):
        managed(name)
        await sup.stop(name)
        asyncio.create_task(sup.start(name))
        return Ok()

    @app.get("/api/engines/{name}/log", response_model=LogLines,
             summary="The end of an engine's or channel's log; none for an external server")
    async def engine_log(name: str, lines: int = Query(200, ge=1, le=5000)):
        managed(name)
        return LogLines(lines=await asyncio.to_thread(sup.items[name].driver.tail, lines))

    @app.post("/api/engines/start_all", response_model=Ok)
    async def start_all():
        asyncio.create_task(sup.start_all())
        return Ok()

    @app.post("/api/engines/stop_all", response_model=Ok)
    async def stop_all():
        await sup.stop_all()
        return Ok()

    etag_header = {"ETag": {"description": "The saved file's version; send it back as If-Match",
                            "schema": {"type": "string"}}}

    @app.get("/api/config", response_model=WinstonConfig, response_model_exclude_none=True,
             responses={200: {"headers": etag_header}})
    async def get_config(response: Response):
        response.headers["ETag"] = _etag(app.state.version)
        return app.state.cfg      # no secrets live in config, only the names of env variables

    def running_restarts(old: dict, new: dict) -> list[str]:
        return sorted(r for r in restarts_for(old, new, schema) if r in sup.items or r == "control")

    def persona_rename(cur: WinstonConfig, new: WinstonConfig) -> PersonaRename | None:
        """What renaming him would change in the persona file new points at; None when the name is the
        same or the file never says the old one."""
        old, name = cur.assistant.name.strip(), " ".join(new.assistant.name.split())
        if not name or old == name or not (lines := persona.rename_lines(new, old)):
            return None
        word = persona.name_word(old)
        return PersonaRename(old=old, new=name, lines=lines, places=sum(len(word.findall(x)) for x in lines))

    async def commit(cur: WinstonConfig, v: str, new: WinstonConfig, dry_run: bool, applied: list[str],
                     response: Response, rename_persona: bool = False) -> ConfigResult:
        restarts = [r for r in running_restarts(_dump(cur), _dump(new)) if r not in applied]
        rename = persona_rename(cur, new)
        if dry_run:
            response.headers["ETag"] = _etag(v)
            result = ConfigResult(saved=False, restarts=restarts, version=v, persona_rename=rename)
            if ha_changed(cur, new) and new.providers.ha is not None:
                result.ha_plan, result.ha_plan_error = await preview_ha(cur, new)
            return result
        save(new)
        app.state.cfg, app.state.version = new, file_version()
        remember(app.state.version, new)
        if rename and rename_persona:       # after the config, so a refused save never touches the file
            rename.renamed = persona.rename(new, rename.old, rename.new) > 0
        # Before the reload, so channels that restart read Home Assistant's new tool list. A live voice
        # switch never touches Home Assistant.
        ha_apply = await apply_saved(cur, new) if "voice" not in applied and ha_changed(cur, new) else None
        before = set(sup.items)
        await sup.reload(new)
        if autostart:       # what the config just gained (a hearing engine, a channel) starts: no restart would
            for name in sup.items.keys() - before:
                spawn(sup.start(name))
        rooms.cfg = new
        await extensions.apply(cur, new)
        new_d = _dump(new)
        sup.mark_stale((r for r in restarts if r != "control"), base=_dump(cur))
        sup.settle(lambda base: restarts_for(base, new_d, schema))     # a change undone needs no restart
        await web.reset(new)
        response.headers["ETag"] = _etag(app.state.version)
        return ConfigResult(saved=True, restarts=restarts, version=app.state.version, ha_apply=ha_apply,
                            persona_rename=rename)

    conflict = {409: {"model": ConfigConflict, "description": "Changed on disk since the If-Match version"}}

    @app.patch("/api/config", response_model=ConfigResult, responses=conflict,
               summary="Change some fields of the saved config: each change sets one path (null removes it). "
                       "Applied to the config on disk, so saves from elsewhere are kept. With If-Match, a path "
                       "that was also changed since that version is a conflict (409); without it the changes win")
    async def patch_config(p: ConfigPatch, request: Request, response: Response, dry_run: bool = Query(False),
                           applied: list[str] = Query([], description="Processes that already run this change "
                                                                      "(a live voice switch): not marked for restart"),
                           rename_persona: bool = Query(False, description=RENAME_PERSONA)):
        async with config_lock:
            cur, v = current()
            cur_d = _dump(cur)
            if (base_v := _if_match(request.headers.get("if-match"))) and base_v != v:
                base = history.get(base_v)
                clash = [c.path for c in p.changes
                         if base is None or get_in(base, c.path, MISSING) != get_in(cur_d, c.path, MISSING)]
                if clash:
                    detail = ("Changed elsewhere since it was loaded" if base is not None else
                              "The config it was loaded from is not known here any more; reload")
                    return JSONResponse(ConfigConflict(detail=detail, paths=clash, version=v).model_dump(),
                                        status_code=409)
            try:
                merged = apply_changes(cur_d, [(c.path, c.value) for c in p.changes])
            except ValueError as e:
                return JSONResponse({"detail": [{"loc": [], "msg": str(e), "type": "value_error"}]}, status_code=422)
            try:
                new = WinstonConfig.model_validate(merged)
            except ValidationError as e:
                return JSONResponse({"detail": jsonable_encoder(e.errors(include_url=False, include_context=False,
                                                                         include_input=False))}, status_code=422)
            if errors := await extensions.validate(new):
                return JSONResponse({"detail": errors}, status_code=422)
            return await commit(cur, v, new, dry_run, applied, response, rename_persona)

    @app.get("/api/config/schema", summary="JSON Schema of the config, with restarts and what the form draws per field")
    async def get_schema() -> dict:
        return full_schema()

    def secret_status(name: str, dump: dict) -> SecretStatus:
        refs = secrets.references(dump, full_schema())
        for ext_id in extensions.described:
            for field, var in extensions.secret_fields(ext_id).items():
                if var == name and ["extensions", ext_id, "settings", field] not in refs.get(name, []):
                    refs.setdefault(name, []).append(["extensions", ext_id, "settings", field])
        used = refs.get(name, [])
        # a feature switched off reads nothing: no restart for it (an extension, Home Assistant, a channel)
        live = [u for u in used if not any(get_in(dump, u[:i] + ["enabled"]) is False for i in range(1, len(u) + 1))]
        affects = secrets.restarts(dump, schema, live)
        return SecretStatus(name=name, set=secrets.is_set(name), used_by=used,
                            restarts=sorted(r for r in affects if r in sup.items or r == "control"))

    @app.get("/api/secrets", response_model=list[SecretStatus],
             summary="Variables in data/.env the config and the extensions name: set or not, never values")
    async def list_secrets():
        dump = _dump(app.state.cfg)
        ext_vars = [v for i in extensions.described
                    for v in (*extensions.secret_fields(i).values(), *extensions.account_vars(i).values())]
        return [secret_status(n, dump) for n in sorted({*secrets.references(dump, full_schema()), *ext_vars})]

    async def secret_changed(name: str) -> SecretResult:
        dump = _dump(app.state.cfg)
        st = secret_status(name, dump)
        sup.mark_stale(r for r in st.restarts if r != "control")
        if "control" in st.restarts:
            control_secrets.add(name)
        if set(st.restarts) & set(BRAIN_CHANNELS):
            await web.reset(app.state.cfg)          # the Talk page's brain reads keys when it is built
        await extensions.secret_changed(name)
        return SecretResult(name=name, set=st.set, restarts=st.restarts)

    def writable(request: Request, name: str):
        """Checked before the body is read, so a refused request says why rather than how its body was wrong."""
        secrets.guard(request, [started["control"]["host"], app.state.cfg.control.host], request.method == "PUT",
                      None if secrets.denied(name) else name)
        if secrets.denied(name):
            raise HTTPException(403, f"{name} is not a variable Winston sets")
        ext_vars = {v for i in extensions.described
                    for v in (*extensions.secret_fields(i).values(), *extensions.account_vars(i).values())}
        if name not in ext_vars and not secrets.allowed(name, _dump(app.state.cfg), full_schema()):
            raise HTTPException(403, f"no setting names {name}; save the setting first")

    @app.put("/api/secrets/{name}", response_model=SecretResult, dependencies=[Depends(writable)],
             summary="Store a secret in data/.env (localhost only); what reads it is marked for restart")
    async def put_secret(name: str, s: SecretValue):
        secrets.check_value(name, s.value)
        secrets.write(name, s.value)
        return await secret_changed(name)

    @app.delete("/api/secrets/{name}", response_model=SecretResult, dependencies=[Depends(writable)],
                summary="Remove a secret from data/.env (localhost only)")
    async def delete_secret(name: str):
        secrets.remove(name)
        return await secret_changed(name)

    @app.post("/api/extensions/{ext_id}/accounts/{field}", response_model=AccountStart,
              summary="Start signing in to an extension's account (localhost only): the service's sign-in page")
    async def start_account(ext_id: str, field: str, request: Request):
        try:
            secrets.guard(request, [started["control"]["host"], app.state.cfg.control.host], False)
        except HTTPException as e:
            raise HTTPException(e.status_code, f"{e.detail.split(';')[0]}; run winston ext connect {ext_id} in a "
                                               "terminal on this machine instead") from e
        port = urlsplit("//" + request.headers.get("host", "")).port or request.url.port or 80
        redirect = f"http://127.0.0.1:{port}{accounts.CALLBACK}"
        try:
            return AccountStart(url=accounts.start(extensions, ext_id, field, redirect), redirect=redirect)
        except accounts.AccountError as e:
            raise HTTPException(409, str(e)) from e

    @app.get(accounts.CALLBACK, include_in_schema=False)
    async def account_callback(request: Request, state: str = "", code: str = "", error: str = ""):
        """Where the service sends the browser back after its sign-in: the code becomes a refresh token in
        data/.env. Only a sign-in started on this machine (its state, a file in data/) finishes, and only in a
        browser on it: winstond may listen beyond it (`winston ext connect` starts one then)."""
        local = request.client is not None and secrets.loopback(request.client.host)
        if not local or (why := secrets.cross_site(request)):
            raise HTTPException(403, why if local else "a sign-in comes back to this machine only")
        if error or not code:
            return _page(f"Not connected: {error or 'no code came back'}.")
        try:
            ext_id, var = await accounts.finish(extensions, state, code)
        except accounts.AccountError as e:
            return _page(f"Not connected: {e}.")
        await secret_changed(var)
        return _page(f"{extensions.sources[ext_id].manifest.name} is connected. You can close this tab.")

    @app.get("/api/presets", response_model=Presets,
             summary="Engine and model presets the UI offers per slot, and the hardware profiles")
    async def get_presets():
        return Presets(llm=LLM, tts=TTS, stt=stt_presets(), profiles=PROFILES)

    @app.get("/api/options/{name}", response_model=Options,
             summary="Live choices for a dropdown: " + ", ".join(OPTION_NAMES))
    async def get_options(name: str,
                          base_url: str | None = Query(None, description="Server to ask instead of the saved one, for an unsaved draft"),
                          domain: str | None = Query(None, description="ha_entities: comma-separated domains to keep"),
                          q: str | None = Query(None, description="discord_users: a user id to look up"),
                          engine: str | None = Query(None, description="llm_models: the draft brain engine block as JSON, "
                                                                       "to list the downloads it can load"),
                          tool: str | None = Query(None, description="extension_tools: the read tool they must have"),
                          extension: str | None = Query(None, description="extension_choices: the extension to ask"),
                          choices: str | None = Query(None, description="extension_choices: its @ext.choices list")):
        out = await options(name, app.state.cfg, {"base_url": base_url, "domain": domain, "q": q, "engine": engine,
                                                  "tool": tool, "extension": extension, "choices": choices,
                                                  "extensions": extensions})
        if out is None:
            raise HTTPException(404, f"no options named {name}")
        return out

    # A POST only: it may send the saved token to another address, so it sits behind the same-site write guard.
    @app.post("/api/providers/home_assistant/check", response_model=HomeAssistantCheck,
              summary="Whether Home Assistant answers at an address with a token, both before they are saved")
    async def ha_check(t: HomeAssistantTry):
        return await check_home_assistant(app.state.cfg, t.url, t.token)

    def ha_client() -> HomeAssistant:
        try:
            return HomeAssistant(app.state.cfg)
        except RuntimeError as e:
            raise HTTPException(409, str(e))

    def ha_away(e: BaseException) -> str:
        """Why Home Assistant could not be read, in words: the MCP client wraps its errors in
        exception groups, so the first leaf is the one that says what happened."""
        while isinstance(e, BaseExceptionGroup) and e.exceptions:
            e = e.exceptions[0]
        if str(e).startswith("Token"):
            return str(e)
        status = getattr(getattr(e, "response", None), "status_code", None)
        if status == 404:
            return "Not found: is the MCP server integration loaded?"
        if status in (401, 403):
            return "Refused: the token was not accepted"
        return f"Not reachable: {failure_words(e)}"

    async def preview_ha(old: WinstonConfig, new: WinstonConfig) -> tuple[ExposurePlan | None, str | None]:
        """What saving new would do in Home Assistant, or why that could not be read."""
        try:
            ha = HomeAssistant(new)
        except RuntimeError as e:                                       # its token is not set
            return None, str(e)
        try:
            return (await asyncio.wait_for(ha.plan(old.providers.home_assistant), APPLY_TIMEOUT))[0], None
        except Exception as e:                                          # noqa: BLE001
            return None, ha_away(e)

    @app.get("/api/providers/home_assistant/exposure", response_model=HAExposure,
             summary="His list as Home Assistant has it: exposed or not, aliases, area, device and state per "
                     "entity, what else is exposed (drift), the MCP server and whether a save would change anything")
    async def ha_exposure():
        ha = ha_client()
        try:
            snap = await ha.snapshot(list(ha.ha.expose))
        except Exception as e:                                          # noqa: BLE001
            return HAExposure(ok=False, detail=ha_away(e))
        return exposure_view(ha.ha, snap)

    @app.get("/api/providers/home_assistant/registry", response_model=HARegistry,
             summary="Every entity in Home Assistant grouped by area, with its type, state, device, name and "
                     "aliases, for the picker. Without an admin token: one group, no areas or aliases")
    async def ha_registry():
        ha = ha_client()
        try:
            snap = await ha.snapshot()
        except Exception as e:                                          # noqa: BLE001
            return HARegistry(ok=False, detail=ha_away(e))
        return registry_view(ha.ha, snap)

    @app.get("/api/providers/home_assistant/tools", response_model=list[HATool],
             summary="The tools Home Assistant's MCP server offers, one row per job in plain words (cached a minute)")
    async def ha_tools():
        ha = ha_client()
        try:
            return await ha.tools()
        except Exception as e:                                          # noqa: BLE001
            raise HTTPException(502, f"Home Assistant's MCP server: {ha_away(e)}")

    @app.post("/api/providers/home_assistant/apply", response_model=HAApply,
              summary="Apply the saved list to Home Assistant again (after it was away during a save). Adds and "
                      "exposes; removes no aliases, since it has no earlier list to compare with")
    async def ha_apply_now():
        ha_client()
        async with config_lock:
            return await apply_saved(app.state.cfg, app.state.cfg)

    dist = repo_root() / "web" / "dist"
    if (dist / "index.html").is_file():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        async def ui(path: str):
            if path.startswith("api/"):
                raise HTTPException(404)
            f = dist / path
            return FileResponse(f if path and f.is_file() else dist / "index.html")

    return app


def openapi_spec() -> dict:
    """The API description without an instance: what `winston api schema` prints and the web
    client is generated from."""
    return create_app(WinstonConfig(llm=LLMConfig(model="none")), autostart=False).openapi()
