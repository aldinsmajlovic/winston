"""Engine driver cases: a Docker engine's bind-mount folders exist, owned by us, before `docker run`,
the Hugging Face cache's hub/ included (a root container made it root's, and the host voice could not
write its models there). Docker is faked; nothing runs.

    uv run python tests/engines_cases.py
"""
import asyncio
import shutil
import tempfile
from pathlib import Path

import docker
from harness import check, finish_checks

from winston.config.schema import EngineConfig
from winston.engines.drivers import DockerDriver

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
finish_checks("engines: volume folders made before docker run")
