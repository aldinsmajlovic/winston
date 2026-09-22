"""winstond: the control service. Supervises engines and channels, exposes the API the web UI
and the CLI use, and serves the built UI from web/dist. Bound to localhost by default; put it
behind auth before exposing it."""
import asyncio
import subprocess
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from winston import __version__
from winston.config import load, repo_root, save
from winston.config.restarts import restarts_for
from winston.config.schema import LLMConfig, WinstonConfig
from winston.engines.supervisor import Supervisor


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


class ConfigResult(BaseModel):
    saved: bool
    restarts: list[str]     # engines that must restart before the change is live


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

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if autostart:
            asyncio.create_task(sup.start_all())
        yield
        await sup.stop_all(owned_only=True)

    app = FastAPI(title="winstond", version=__version__, lifespan=lifespan)
    app.state.supervisor, app.state.cfg = sup, cfg

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
        restarts = sorted(restarts_for(_dump(app.state.cfg), _dump(new), WinstonConfig.model_json_schema()))
        if dry_run:
            return ConfigResult(saved=False, restarts=restarts)
        save(new)
        app.state.cfg = new
        await sup.reload(new)
        return ConfigResult(saved=True, restarts=restarts)

    @app.get("/api/config/schema", summary="JSON Schema of the config, with level and restarts per field")
    async def get_schema() -> dict:
        return WinstonConfig.model_json_schema()

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
