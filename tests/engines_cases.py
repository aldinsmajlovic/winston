"""Engine driver cases: a Docker engine's bind-mount folders exist, owned by us, before `docker run`,
the Hugging Face cache's hub/ included (a root container made it root's, and the host voice could not
write its models there). Docker is faked; nothing runs. Then what a failed start says: an external
server that does not answer, a channel that is not ready, Discord without your user ID.

    uv run python tests/engines_cases.py
"""
import asyncio
import os
import shutil
import tempfile
from pathlib import Path

import docker
from harness import check, finish_checks, free_port, scratch_data

scratch = scratch_data("winston-engines-data-")         # an empty data folder: no .env

from winston.config.schema import Channels, DiscordChannel, EngineConfig, WinstonConfig  # noqa: E402  (after WINSTON_DATA is set)
from winston.engines.drivers import DockerDriver, ExternalDriver, SubprocessDriver  # noqa: E402
from winston.engines.supervisor import Managed, Supervisor  # noqa: E402

tmp = Path(tempfile.mkdtemp(prefix="winston-engines-"))
seen = {}


class Containers:
    def get(self, name):
        raise docker.errors.NotFound(name)

    def run(self, image, **kwargs):
        seen["hub"] = (tmp / "hf" / "hub").is_dir()
        seen["models"] = (tmp / "models").is_dir()
        seen["volumes"] = kwargs["volumes"]


class Client:
    containers = Containers()


docker.from_env = lambda: Client()
cfg = EngineConfig(driver="docker", image="made-up/engine:1", port=8000,
                   volumes={str(tmp / "hf"): "/root/.cache/huggingface", str(tmp / "models"): "/models"})
asyncio.run(DockerDriver("llm", cfg).start())
shutil.rmtree(tmp, ignore_errors=True)
check("hub/ of the huggingface volume exists before docker run", seen.get("hub"), True)
check("every other volume's host folder too", seen.get("models"), True)
check("and the volumes reach docker as before", seen.get("volumes", {}).get(str(tmp / "hf")),
      {"bind": "/root/.cache/huggingface", "mode": "rw"})

# What a start that fails says. Nothing listens on port 9.
for var in ("DISCORD_BOT_TOKEN", "DISCORD_ALLOWED_USER_ID"):
    os.environ.pop(var, None)
sup = Supervisor.__new__(Supervisor)
sup.cfg = WinstonConfig.model_construct(channels=Channels())
ext = EngineConfig(driver="external", port=9, url="http://127.0.0.1:9", startup_timeout=0)
chan = EngineConfig(driver="subprocess", port=9, startup_timeout=0)
d = DiscordChannel()
sup.items = {"llm": Managed("llm", ext, ExternalDriver("llm", ext)),
             "voice": Managed("voice", chan, ExternalDriver("voice", chan), kind="channel"),
             "discord": Managed("discord", chan, SubprocessDriver("discord", chan, ["true"]), kind="channel",
                                needs_env=[d.token_env, d.allowed_user_env])}
check("an external server not answering says where to look",
      asyncio.run(sup.start("llm")).detail,
      "not ready after 0 s; nothing answered at http://127.0.0.1:9/health, check that it runs there")
check("a channel has no Startup time limit to raise", asyncio.run(sup.start("voice")).detail, "not ready after 0 s")
check("Discord without your user ID names the field, not its variable", asyncio.run(sup.start("discord")).detail,
      "DISCORD_BOT_TOKEN missing from data/.env; Your Discord user ID is not set: add it on Ways to talk, Discord")

# A server on your network: no address of its own, so the one on the card is checked, and a 401 is up.
from aiohttp import web  # noqa: E402

from winston.api.options import served_model  # noqa: E402


def network(base_url, model="qwen3.5:9b"):
    return WinstonConfig.model_validate({"engines": {"llm": {"driver": "external", "port": 8000, "health": "/v1/models"}},
                                         "llm": {"base_url": base_url, "model": model}})


cfg = network("http://192.168.1.10:8000/v1/")
check("its address is the API address without /v1", cfg.engine_at("llm").url, "http://192.168.1.10:8000")
check("the saved block keeps none, so changing the API address moves it", cfg.engines["llm"].url, None)
check("the supervisor checks it there", Supervisor(cfg).items["llm"].health_url, "http://192.168.1.10:8000/v1/models")
check("one with its own address keeps it", WinstonConfig.model_validate(
    {"engines": {"llm": {"driver": "external", "port": 1, "url": "http://a:1"}}, "llm": {"model": "m"}}
).engine_at("llm").url, "http://a:1")
check("an Ollama on your network is Ollama: requests name his 16K copy",
      served_model(network("http://192.168.1.10:11434/v1")), "qwen3.5:9b-16k")
check("a vLLM or llama.cpp there is asked for the model as it is", served_model(network("http://box:8000/v1")),
      "qwen3.5:9b")


async def answers(status):
    app = web.Application()
    app.router.add_get("/v1/models", lambda _: web.Response(status=status))
    runner = web.AppRunner(app)
    await runner.setup()
    port = free_port()
    await web.TCPSite(runner, "127.0.0.1", port).start()
    sup = Supervisor(network(f"http://127.0.0.1:{port}/v1"))
    try:
        return await sup.healthy(sup.items["llm"])
    finally:
        await runner.cleanup()

check("a server that wants a key is up (401)", asyncio.run(answers(401)), True)
check("one that answers 200 is up", asyncio.run(answers(200)), True)
check("one that fails is not (500)", asyncio.run(answers(500)), False)

shutil.rmtree(scratch, ignore_errors=True)
finish_checks("engines: volume folders made before docker run, failed starts say what to do, a server on your "
              "network at its API address")
