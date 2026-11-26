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
from winston.engines.supervisor import Managed, Supervisor, _why  # noqa: E402

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

# The lines vLLM 0.29 logs before it exits, cut from real runs.
check("vLLM with no room for the context names the share it was given",
      _why([("(EngineCore pid=150) ERROR [core.py:1374] ValueError: No available memory for the cache blocks. "
             "Try increasing `gpu_memory_utilization`"), "RuntimeError: Engine core initialization failed."]).split(":")[0],
      "the model fills all the graphics memory it may use (--gpu-memory-utilization), with none left for its context")
check("vLLM with too long a context names --max-model-len", "--max-model-len" in _why(
      [("ValueError: To serve at least one request with the model's max seq len (32768), (5.00 GiB KV cache is "
        "needed, which is larger than the available KV cache memory (2.10 GiB).")]), True)
check("a CUDA out-of-memory error reads as one", _why(["torch.OutOfMemoryError: CUDA out of memory."]).startswith(
      "it ran out of graphics memory"), True)
check("any other failure is left to the log", _why(["RuntimeError: Engine core initialization failed."]), "")

# A brain on this computer stopped by the kernel for want of memory: the reply and the status say so.
from winston.channels.text import UNREACHABLE, error_text  # noqa: E402
from winston.config import memory  # noqa: E402

vmstat = scratch / "vmstat"
vmstat.write_text("nr_free_pages 1\noom_kill 88\npgfault 9\n")
check("the kernel's count of processes stopped for memory", memory.oom_kills(vmstat), 88)
check("none where it does not keep one", memory.oom_kills(scratch / "none"), None)

here = WinstonConfig.model_validate({"llm": {"model": "m", "base_url": "http://127.0.0.1:11434/v1", "context": 45056}})
hosted = WinstonConfig.model_validate({"llm": {"model": "m", "base_url": "https://openrouter.ai/api/v1"}})
import httpx  # noqa: E402

gone = httpx.ConnectError("All connection attempts failed")       # what the request saw
memory.oom_kills = lambda: 89
check("no count kept: nothing blamed", memory.out_of_memory(here, None, gone), None)
check("a brain elsewhere is not this computer's memory", memory.out_of_memory(hosted, 88, gone), None)
check("nothing stopped during the request: not memory", memory.out_of_memory(here, 89, gone), None)
check("a brain that answered with an error was not stopped", memory.out_of_memory(here, 88, RuntimeError("500")),
      None)
check("so the status is as it was", memory.stopped_for_memory(), "")
e = memory.out_of_memory(here, 88, gone)
check("one stopped during it: out of memory, with his context", str(e),
      "the computer ran out of memory with 44K of context: lower How much he keeps in mind on Brain, or close "
      "other programs")
e.__cause__ = httpx.ConnectError("All connection attempts failed")      # as TextSession.ask raises it
check("the reply says so, not that he could not reach it", error_text(e), f"His brain stopped because {e}.")
check("a dropped connection alone is still unreachable", error_text(httpx.ConnectError("x")), UNREACHABLE)
check("noted for the status", memory.stopped_for_memory(), str(e))
asyncio.run(sup.refresh())
check("the brain's status is the reason, not running", (sup.items["llm"].state, sup.items["llm"].detail),
      ("error", str(e)))
asyncio.run(sup.start("llm"))
check("a restart clears it", memory.stopped_for_memory(), "")
sup.cfg = here
memory.out_of_memory(here, 88, gone)
asyncio.run(sup.reload(here.model_copy(update={"llm": here.llm.model_copy(update={"context": 16384})})))
check("so does saving a smaller context, which then waits for a restart as any change",
      memory.stopped_for_memory(), "")

shutil.rmtree(scratch, ignore_errors=True)
finish_checks("engines: volume folders made before docker run, failed starts say what to do, a server on your "
              "network at its API address, graphics memory running out in words, a brain stopped for want of "
              "the computer's memory named in its reply and status")
