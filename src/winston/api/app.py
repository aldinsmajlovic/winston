"""winstond: the control service. Supervises engines and channels, exposes the API the web UI
and the CLI use, and serves the built UI from web/dist. Bound to localhost by default; put it
behind auth before exposing it."""
import asyncio
import json
from collections import OrderedDict
from datetime import date
import subprocess
from contextlib import asynccontextmanager
from pathlib import PurePosixPath
from typing import Annotated, Any, Literal
from urllib.parse import quote

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
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
from winston.core.announce import Announcement, Delivery, Hub
from winston.core.brain import api_key
from winston.core import memory, persona, region, voices
from winston.core.live import Feed, LiveEvent
from winston.core.tools.ask_claude import auth_source
from winston.engines.supervisor import Supervisor
from winston.providers import ha_events


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
    config_changed: bool = False        # saved settings or secrets wait for a restart (pending, retired or stale)


class GpuStatus(BaseModel):
    name: str
    used_mb: int
    total_mb: int


class Status(BaseModel):
    version: str
    assistant: str
    engines: list[EngineStatus]
    gpu: GpuStatus | None = None
    # Saved since winstond started and read only when it starts: config sections ("announce") and
    # secrets ("HA_TOKEN"). They apply at the next `winston up`; nothing here restarts winstond.
    pending_control: list[str] = []


class Ok(BaseModel):
    ok: bool = True


class ChatMessage(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


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


class ClonedVoice(BaseModel):
    slug: str
    name: str
    voice: str              # the name the speech server knows it by, "friends/<slug>"
    created: float
    seconds: float
    consent_text: str       # what the consent recording was heard to say


class MemoryFact(BaseModel):
    id: str                 # the stored line, text and stamp; edits and deletes name the fact by it
    text: str
    noted: str | None       # YYYY-MM-DD


class MemoryTopic(BaseModel):
    name: str               # the file's stem
    title: str
    facts: list[MemoryFact]


class MemoryView(BaseModel):
    enabled: bool
    topics: list[MemoryTopic]
    chars: int              # of facts in every conversation's prompt
    limit: int              # past this, memory costs latency on every turn
    day_first: bool         # dates as the region setting says ("24 Sep" or "Sep 24")


class NewFact(BaseModel):
    fact: str = Field(min_length=1, max_length=1000)
    index: int | None = Field(None, ge=0, description="Position among the topic's facts; the end when unset")
    noted: date | None = Field(None, description="When it was first noted, for Undo; today when unset")


class FactEdit(BaseModel):
    id: str
    fact: str = Field(min_length=1, max_length=1000)


class NewTopic(BaseModel):
    title: str = Field(min_length=1, max_length=60)


class TopicRename(BaseModel):
    title: str = Field(min_length=1, max_length=60)


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


class ConfigResult(BaseModel):
    saved: bool
    restarts: list[str]     # engines that must restart before the change is live; "control" is winstond itself
    version: str            # the saved file's version after this call (unchanged by a dry run), as in ETag


class ConfigVersion(BaseModel):
    version: str


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


class ClaudeAuth(BaseModel):
    source: Literal["api_key", "oauth_token", "login", "none"]
    variable: str | None            # the variable it reads, for api_key and oauth_token
    label: str


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
                             capture_output=True, text=True, timeout=3).stdout.strip().split(", ")
        return GpuStatus(name=out[0], used_mb=int(out[1]), total_mb=int(out[2]))
    except Exception:
        return None


def _dump(cfg: WinstonConfig) -> dict:
    return cfg.model_dump(mode="json", exclude_none=True)


def _etag(version: str) -> str:
    return f'"{version}"'


def _if_match(header: str | None) -> str | None:
    """The version an If-Match header names ("abc", W/"abc" or abc); None when absent or *."""
    if not header or header.strip() == "*":
        return None
    return header.strip().removeprefix("W/").strip('"') or None


CLAUDE_LABELS = {"api_key": "API key (ANTHROPIC_API_KEY)", "oauth_token": "Token from claude setup-token",
                 "login": "Claude Code login on this machine", "none": "Nothing"}
MISSING = object()


def create_app(cfg: WinstonConfig | None = None, autostart: bool = True) -> FastAPI:
    cfg = cfg or load()
    sup = Supervisor(cfg)
    hub = Hub(cfg)
    feed = Feed()
    web = WebChat(cfg, feed)
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

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if autostart:
            asyncio.create_task(sup.start_all())
        listener = asyncio.create_task(ha_events.listen(cfg, publish_announcement))
        yield
        listener.cancel()
        await web.close()
        await sup.stop_all(owned_only=True)

    app = FastAPI(title="winstond", version=__version__, lifespan=lifespan)
    app.state.supervisor, app.state.cfg, app.state.hub, app.state.feed = sup, cfg, hub, feed
    schema = WinstonConfig.model_json_schema()
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
        """FastAPI's 422, except that a whole config sent to PUT /api/config gets locations from the config root."""
        errors = jsonable_encoder(exc.errors())
        if request.url.path.startswith("/api/secrets"):
            # never echo a pasted key back (FastAPI puts the refused body in `input`)
            errors = [{k: v for k, v in e.items() if k not in ("input", "ctx")} for e in errors]
        if request.url.path == "/api/config" and request.method == "PUT":
            for e in errors:
                if e.get("loc", [])[:1] == ["body"]:
                    e["loc"] = e["loc"][1:]
        return JSONResponse({"detail": errors}, status_code=422)

    def status_payload() -> Status:
        return Status(
            version=__version__, assistant=app.state.cfg.assistant.name,
            engines=[EngineStatus(name=m.name, kind=m.kind, driver=m.cfg.driver, state=m.state, detail=m.detail,
                                  since=m.since, port=m.cfg.port, startup_timeout=m.cfg.startup_timeout,
                                  started_in=m.started_in,
                                  config_changed=bool(m.pending or m.retired or m.stale)) for m in sup.items.values()],
            gpu=gpu_status(),
            pending_control=control_sections(started, _dump(app.state.cfg), schema) + sorted(control_secrets),
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

    @app.get("/api/events", summary="Announcements for one channel process, as server-sent events")
    async def events(channel: Literal["voice", "discord"] = Query(...)):
        q = hub.subscribe(channel)

        async def gen():
            try:
                yield ": subscribed\n\n"
                while True:
                    try:
                        ev = await asyncio.wait_for(q.get(), timeout=15)
                        yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"           # also lets a dead client be noticed
            finally:
                hub.unsubscribe(channel, q)
        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.get("/api/live", summary="What Winston hears, says and does: recent events, then new ones, as "
                                  "server-sent events (LiveEvent objects)")
    async def live():
        q = feed.subscribe()

        async def gen():
            try:
                for ev in feed.backlog():
                    yield f"data: {ev.model_dump_json(exclude_none=True)}\n\n"
                while True:
                    try:
                        ev = await asyncio.wait_for(q.get(), timeout=15)
                        yield f"data: {ev.model_dump_json(exclude_none=True)}\n\n"
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
            finally:
                feed.unsubscribe(q)
        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.post("/api/live", response_model=Ok, summary="Events from a channel process into the live feed")
    async def live_publish(events: list[LiveEvent]):
        for ev in events:
            feed.publish(ev)
        return Ok()

    @app.post("/api/chat", response_model=Ok,
              summary="Say something to Winston in the web conversation; the reply arrives on /api/live")
    async def chat(m: ChatMessage):
        spawn(web.ask(m.text.strip()))
        return Ok()

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
              summary="Text in Winston's voice as streamed 16-bit mono PCM, for read aloud and the Voice page's "
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
            raise HTTPException(503, f"speech server not reachable: {type(e).__name__}")
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
            raise HTTPException(409, f"{c.name}'s voice is Winston's voice now; pick another voice first")
        voices.delete(c.slug)
        return Ok()

    def memory_call(fn, *args):
        try:
            return fn(app.state.cfg, *args)
        except memory.MemoryEditError as e:
            raise HTTPException(400, str(e))

    @app.get("/api/memory", response_model=MemoryView, summary="Everything he remembers, by topic, as stored")
    async def get_memory():
        cfg = app.state.cfg
        return MemoryView(enabled=cfg.memory.enabled, topics=memory.view(cfg), chars=memory.size(cfg),
                          limit=memory.PROMPT_WARN, day_first=region.resolve(cfg)["date_format"] == "day_month")

    @app.post("/api/memory/topics", response_model=MemoryTopic, summary="A new topic (a new file in the vault)")
    async def add_topic(t: NewTopic):
        name = memory_call(memory.new_topic, t.title)
        return MemoryTopic(name=name, title=memory.title(app.state.cfg, name), facts=[])

    @app.put("/api/memory/topics/{topic}", response_model=MemoryTopic, summary="Rename a topic (and its file)")
    async def rename_topic(topic: str, r: TopicRename):
        name = memory_call(memory.rename_topic, topic, r.title)
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
        return memory_call(memory.add, topic, f.fact, f.index, f.noted)

    @app.put("/api/memory/{topic}", response_model=MemoryFact, summary="Change a fact")
    async def edit_fact(topic: str, f: FactEdit):
        return memory_call(memory.replace, topic, f.id, f.fact)

    @app.delete("/api/memory/{topic}", response_model=Forgotten, summary="Forget a fact")
    async def forget_fact(topic: str, id: str = Query(..., description="The fact's id from GET /api/memory")):
        return Forgotten(index=memory_call(memory.forget, topic, id))

    @app.api_route("/api/voice/offer", methods=["POST", "PATCH"], include_in_schema=False)
    async def voice_offer(request: Request):
        """The browser's WebRTC offer and ICE candidates, passed to the voice channel, so the page
        needs only winstond's origin."""
        v = app.state.cfg.channels.voice
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.request(request.method, f"http://{v.host}:{v.port}/api/offer",
                                    params=request.query_params, content=await request.body(),
                                    headers={"content-type": "application/json"})
        except httpx.HTTPError as e:
            raise HTTPException(503, f"voice channel not reachable: {type(e).__name__}")
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
        if name in sup.items:           # a retired engine is gone after its stop
            asyncio.create_task(sup.start(name))
        return Ok()

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

    @app.get("/api/config/version", response_model=ConfigVersion,
             summary="The saved config's version, the same as GET /api/config's ETag")
    async def get_config_version():
        return ConfigVersion(version=app.state.version)

    def running_restarts(old: dict, new: dict) -> list[str]:
        return sorted(r for r in restarts_for(old, new, schema) if r in sup.items or r == "control")

    async def commit(cur: WinstonConfig, v: str, new: WinstonConfig, dry_run: bool, applied: list[str],
                     response: Response) -> ConfigResult:
        restarts = [r for r in running_restarts(_dump(cur), _dump(new)) if r not in applied]
        if dry_run:
            response.headers["ETag"] = _etag(v)
            return ConfigResult(saved=False, restarts=restarts, version=v)
        save(new)
        app.state.cfg, app.state.version = new, file_version()
        remember(app.state.version, new)
        await sup.reload(new)
        new_d = _dump(new)
        sup.mark_stale((r for r in restarts if r != "control"), base=_dump(cur))
        sup.settle(lambda base: restarts_for(base, new_d, schema))     # a change undone needs no restart
        await web.reset(new)
        response.headers["ETag"] = _etag(app.state.version)
        return ConfigResult(saved=True, restarts=restarts, version=app.state.version)

    conflict = {409: {"model": ConfigConflict, "description": "Changed on disk since the If-Match version"}}

    @app.put("/api/config", response_model=ConfigResult, responses=conflict,
             summary="Validate and save a whole config; dry_run only reports which engines a change restarts. "
                     "With If-Match, refused while the saved file is another version")
    async def put_config(new: WinstonConfig, request: Request, response: Response, dry_run: bool = Query(False)):
        async with config_lock:
            cur, v = current()
            if (base := _if_match(request.headers.get("if-match"))) and base != v:
                return JSONResponse(ConfigConflict(detail="The config was saved elsewhere since it was loaded",
                                                   paths=[], version=v).model_dump(), status_code=409)
            return await commit(cur, v, new, dry_run, [], response)

    @app.patch("/api/config", response_model=ConfigResult, responses=conflict,
               summary="Change some fields of the saved config: each change sets one path (null removes it). "
                       "Applied to the config on disk, so saves from elsewhere are kept. With If-Match, a path "
                       "that was also changed since that version is a conflict (409); without it the changes win")
    async def patch_config(p: ConfigPatch, request: Request, response: Response, dry_run: bool = Query(False),
                           applied: list[str] = Query([], description="Processes that already run this change "
                                                                      "(a live voice switch): not marked for restart")):
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
            return await commit(cur, v, new, dry_run, applied, response)

    @app.get("/api/config/schema", summary="JSON Schema of the config, with level and restarts per field")
    async def get_schema() -> dict:
        return WinstonConfig.model_json_schema()

    def secret_status(name: str, dump: dict) -> SecretStatus:
        refs = secrets.references(dump, schema)
        claude = name in secrets.CLAUDE_NAMES
        used = refs.get(name, []) + ([secrets.CLAUDE_PATH] if claude else [])
        # a feature switched off reads nothing: no restart for it (ask_claude, Home Assistant, a channel)
        live = [u for u in used if not any(get_in(dump, u[:i] + ["enabled"]) is False for i in range(1, len(u) + 1))]
        affects = secrets.restarts(dump, schema, live)
        return SecretStatus(name=name, set=secrets.is_set(name), used_by=used,
                            restarts=sorted(r for r in affects if r in sup.items or r == "control"))

    @app.get("/api/secrets", response_model=list[SecretStatus],
             summary="Variables in data/.env the config names, and the two Claude ones: set or not, never values")
    async def list_secrets():
        dump = _dump(app.state.cfg)
        names = sorted(secrets.references(dump, schema)) + [n for n in secrets.CLAUDE_NAMES]
        return [secret_status(n, dump) for n in dict.fromkeys(names)]

    async def secret_changed(name: str) -> SecretResult:
        dump = _dump(app.state.cfg)
        st = secret_status(name, dump)
        sup.mark_stale(r for r in st.restarts if r != "control")
        if "control" in st.restarts:
            control_secrets.add(name)
        if set(st.restarts) & set(BRAIN_CHANNELS) or name in secrets.CLAUDE_NAMES:
            await web.reset(app.state.cfg)          # the Talk page's brain reads keys when it is built
        return SecretResult(name=name, set=st.set, restarts=st.restarts)

    def writable(request: Request, name: str):
        """Checked before the body is read, so a refused request says why rather than how its body was wrong."""
        secrets.guard(request, [started["control"]["host"], app.state.cfg.control.host], request.method == "PUT")
        if secrets.denied(name):
            raise HTTPException(403, f"{name} is not a variable Winston sets")
        if not secrets.allowed(name, _dump(app.state.cfg), schema):
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

    @app.get("/api/tools/ask_claude/auth", response_model=ClaudeAuth,
             summary="What ask_claude signs in with, in the Claude CLI's order: API key, token, this machine's login")
    async def ask_claude_auth():
        source, variable = auth_source()
        return ClaudeAuth(source=source, variable=variable, label=CLAUDE_LABELS[source])

    @app.get("/api/presets", response_model=Presets,
             summary="Engine and model presets the UI offers per slot, and the hardware profiles")
    async def get_presets():
        return Presets(llm=LLM, tts=TTS, stt=stt_presets(), profiles=PROFILES)

    @app.get("/api/options/{name}", response_model=Options,
             summary="Live choices for a dropdown: " + ", ".join(OPTION_NAMES))
    async def get_options(name: str,
                          base_url: str | None = Query(None, description="Server to ask instead of the saved one, for an unsaved draft"),
                          domain: str | None = Query(None, description="ha_entities: comma-separated domains to keep"),
                          q: str | None = Query(None, description="discord_users: a user id to look up")):
        out = await options(name, app.state.cfg, {"base_url": base_url, "domain": domain, "q": q})
        if out is None:
            raise HTTPException(404, f"no options named {name}")
        return out

    @app.get("/api/providers/home_assistant/check", response_model=HomeAssistantCheck,
             summary="Whether Home Assistant answers at the URL with the configured token")
    async def ha_check(url: str | None = Query(None, description="URL to try instead of the saved one")):
        return await check_home_assistant(app.state.cfg, url)

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
