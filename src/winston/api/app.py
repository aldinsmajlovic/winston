"""winstond: the control service. Supervises engines and channels and exposes the API the web
UI and the CLI use. Bound to localhost by default; put it behind auth before exposing it."""
import asyncio
import json
import subprocess
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse

from winston import __version__
from winston.config import load
from winston.config.schema import WinstonConfig
from winston.engines.supervisor import Supervisor


def gpu_status() -> dict | None:
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.used,memory.total", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=3).stdout.strip().split(", ")
        return {"name": out[0], "used_mb": int(out[1]), "total_mb": int(out[2])}
    except Exception:
        return None


def create_app(cfg: WinstonConfig | None = None, autostart: bool = True) -> FastAPI:
    cfg = cfg or load()
    sup = Supervisor(cfg)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if autostart:
            asyncio.create_task(sup.start_all())
        yield
        await sup.stop_all()

    app = FastAPI(title="winstond", version=__version__, lifespan=lifespan)
    app.state.supervisor, app.state.cfg = sup, cfg

    def status_payload():
        return {
            "version": __version__,
            "assistant": cfg.assistant.name,
            "engines": [{"name": m.name, "kind": m.kind, "driver": m.cfg.driver, "state": m.state,
                         "detail": m.detail, "since": m.since, "port": m.cfg.port} for m in sup.items.values()],
            "gpu": gpu_status(),
        }

    @app.get("/api/health")
    async def health():
        return {"status": "ok"}

    @app.get("/api/status")
    async def status():
        await sup.refresh()
        return status_payload()

    @app.get("/api/status/stream")
    async def status_stream():
        async def gen():
            while True:
                await sup.refresh()
                yield f"data: {json.dumps(status_payload())}\n\n"
                await asyncio.sleep(2)
        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.post("/api/engines/{name}/start")
    async def start(name: str):
        if name not in sup.items:
            raise HTTPException(404, f"no engine {name}")
        asyncio.create_task(sup.start(name))
        return {"ok": True}

    @app.post("/api/engines/{name}/stop")
    async def stop(name: str):
        if name not in sup.items:
            raise HTTPException(404, f"no engine {name}")
        await sup.stop(name)
        return {"ok": True}

    @app.post("/api/engines/{name}/restart")
    async def restart(name: str):
        if name not in sup.items:
            raise HTTPException(404, f"no engine {name}")
        await sup.stop(name)
        asyncio.create_task(sup.start(name))
        return {"ok": True}

    @app.post("/api/engines/start_all")
    async def start_all():
        asyncio.create_task(sup.start_all())
        return {"ok": True}

    @app.post("/api/engines/stop_all")
    async def stop_all():
        await sup.stop_all()
        return {"ok": True}

    @app.get("/api/config")
    async def get_config():
        return cfg.model_dump(mode="json", exclude_none=True)   # no secrets live in config, only env names

    @app.get("/api/config/schema")
    async def get_schema():
        return WinstonConfig.model_json_schema()

    return app
