"""winstond: the control service. Supervises engines and channels, exposes the API the web UI
and the CLI use, and serves the built UI from web/dist. Bound to localhost by default; put it
behind auth before exposing it."""
import asyncio
import json
from datetime import date
import subprocess
from contextlib import asynccontextmanager
from typing import Any, Literal

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from winston import __version__
from winston.api.options import PROVIDERS as OPTION_NAMES, HomeAssistantCheck, Options, check_home_assistant, options
from winston.config import load, repo_root, save
from winston.config.presets import LLM, PROFILES, TTS, stt_presets
from winston.config.restarts import restarts_for
from winston.config.schema import LLMConfig, WinstonConfig
from winston.channels.web import WebChat
from winston.core.announce import Announcement, Delivery, Hub
from winston.core.brain import api_key
from winston.core import memory, region, voices
from winston.core.live import Feed, LiveEvent
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
    config_changed: bool = False        # a newer config waits for a restart


class GpuStatus(BaseModel):
    name: str
    used_mb: int
    total_mb: int


class Status(BaseModel):
    version: str
    assistant: str
    engines: list[EngineStatus]
    gpu: GpuStatus | None = None


class Ok(BaseModel):
    ok: bool = True


class ChatMessage(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class SpeakRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


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


class Forgotten(BaseModel):
    index: int              # where it was, for Undo


class ConfigResult(BaseModel):
    saved: bool
    restarts: list[str]     # engines that must restart before the change is live


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

    def status_payload() -> Status:
        return Status(
            version=__version__, assistant=app.state.cfg.assistant.name,
            engines=[EngineStatus(name=m.name, kind=m.kind, driver=m.cfg.driver, state=m.state, detail=m.detail,
                                  since=m.since, port=m.cfg.port, startup_timeout=m.cfg.startup_timeout,
                                  started_in=m.started_in,
                                  config_changed=bool(m.pending or m.retired)) for m in sup.items.values()],
            gpu=gpu_status(),
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
              responses={200: {"content": {"audio/pcm": {}}, "description": "16-bit mono PCM, rate in X-Sample-Rate"}},
              summary="Text in Winston's voice as streamed 16-bit mono PCM, for the Talk page's read aloud")
    async def speak(s: SpeakRequest):
        t = app.state.cfg.tts
        c = httpx.AsyncClient(timeout=httpx.Timeout(10, read=60))
        req = c.build_request("POST", f"{t.base_url.rstrip('/')}/audio/speech",
                              headers={"Authorization": f"Bearer {api_key(t.api_key_env)}"},
                              json={"model": "winston-tts", "input": s.text.strip(), "voice": t.voice,
                                    "response_format": "pcm"})
        try:
            r = await c.send(req, stream=True)
        except httpx.HTTPError as e:
            await c.aclose()
            raise HTTPException(503, f"speech server not reachable: {type(e).__name__}")
        if r.status_code != 200:
            detail = (await r.aread()).decode(errors="replace")[:200]
            await r.aclose(); await c.aclose()
            raise HTTPException(502, f"speech server answered {r.status_code}: {detail}")

        async def gen():
            try:
                async for chunk in r.aiter_bytes():
                    yield chunk
            finally:
                await r.aclose(); await c.aclose()
        return StreamingResponse(gen(), media_type="audio/pcm", headers={"X-Sample-Rate": str(t.sample_rate)})

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

    @app.get("/api/config", response_model=WinstonConfig, response_model_exclude_none=True)
    async def get_config():
        return app.state.cfg      # no secrets live in config, only the names of env variables

    @app.put("/api/config", response_model=ConfigResult,
             summary="Validate and save the config; dry_run only reports which engines a change restarts")
    async def put_config(new: WinstonConfig, dry_run: bool = Query(False)):
        restarts = sorted(r for r in restarts_for(_dump(app.state.cfg), _dump(new), WinstonConfig.model_json_schema())
                          if r in sup.items or r == "control")      # only what runs here
        if dry_run:
            return ConfigResult(saved=False, restarts=restarts)
        save(new)
        app.state.cfg = new
        await sup.reload(new)
        await web.reset(new)
        return ConfigResult(saved=True, restarts=restarts)

    @app.get("/api/config/schema", summary="JSON Schema of the config, with level and restarts per field")
    async def get_schema() -> dict:
        return WinstonConfig.model_json_schema()

    @app.get("/api/presets", response_model=Presets,
             summary="Engine and model presets the UI offers per slot, and the hardware profiles")
    async def get_presets():
        return Presets(llm=LLM, tts=TTS, stt=stt_presets(), profiles=PROFILES)

    @app.get("/api/options/{name}", response_model=Options,
             summary="Live choices for a dropdown: " + ", ".join(OPTION_NAMES))
    async def get_options(name: str,
                          base_url: str | None = Query(None, description="Server to ask instead of the saved one, for an unsaved draft"),
                          domain: str | None = Query(None, description="ha_entities: comma-separated domains to keep")):
        out = await options(name, app.state.cfg, {"base_url": base_url, "domain": domain})
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
