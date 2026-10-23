"""Engine installs (engines/install.py): what an engine needs, with sizes read before the first byte, and
getting it, as winstond and the supervisor do. Fakes for the Hugging Face Hub, a registry that wants a
token, Ollama and Docker; a real `uv sync` of a service with no packages. Nothing reaches the network and
data/ is not touched.

    uv run python tests/install_cases.py
"""
import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections import namedtuple
from pathlib import Path

from harness import check, finish_checks, free_port, scratch_data

scratch = scratch_data("winston-install-data-")
tmp = Path(tempfile.mkdtemp(prefix="winston-install-"))
port = free_port()
os.environ["HF_ENDPOINT"] = f"http://127.0.0.1:{port}"     # read when huggingface_hub is imported
os.environ["HF_HOME"] = str(tmp / "hf")

import docker  # noqa: E402
import yaml  # noqa: E402
from aiohttp import web  # noqa: E402

from winston.config import repo_root  # noqa: E402
from winston.config.schema import WinstonConfig  # noqa: E402
from winston.engines import install  # noqa: E402
from winston.engines.supervisor import Supervisor  # noqa: E402
from huggingface_hub import constants  # noqa: E402

constants.DOWNLOAD_CHUNK_SIZE = 40_000      # progress comes once per chunk (10 MB): small files need small ones

# ---- The fakes -----------------------------------------------------------------------------------------

SHA = "0123456789abcdef0123456789abcdef01234567"
REPOS = {
    "fake/model-GGUF": {"model-Q4_K_M.gguf": 300_000, "model-UD-Q4_K_M.gguf": 200_000, "README.md": 10},
    "fake/vllm-model": {"model-00001-of-00002.safetensors": 120_000, "model-00002-of-00002.safetensors": 80_000,
                        "config.json": 50, "tokenizer.json": 70, "original/consolidated.safetensors": 999_999,
                        "pytorch_model.bin": 888_888},
}
body = {(r, f): bytes((i * 7 + len(f)) % 251 for i in range(n)) for r, fs in REPOS.items() for f, n in fs.items()}
seen = {"ranges": [], "cut": set()}
CUT = ("fake/vllm-model", "model-00001-of-00002.safetensors")      # its first download breaks off halfway


async def model_info(request):
    repo = f"{request.match_info['org']}/{request.match_info['name']}"
    if repo not in REPOS:
        return web.json_response({"error": "not found"}, status=404)
    return web.json_response({"id": repo, "sha": SHA, "siblings": [{"rfilename": f, "size": n} for f, n in REPOS[repo].items()]})


async def resolve(request):
    repo, f = f"{request.match_info['org']}/{request.match_info['name']}", request.match_info["path"]
    data = body[(repo, f)]
    headers = {"X-Repo-Commit": SHA, "ETag": f'"{hashlib.sha256(data).hexdigest()}"', "Accept-Ranges": "bytes"}
    if request.method == "HEAD":
        return web.Response(headers={**headers, "Content-Length": str(len(data))})
    start = 0
    if rng := request.headers.get("Range"):
        seen["ranges"].append((f, rng))
        start = int(rng.split("=")[1].split("-")[0])
    resp = web.StreamResponse(status=206 if start else 200,
                              headers={**headers, "Content-Length": str(len(data) - start)})
    await resp.prepare(request)
    for i in range(start, len(data), 20_000):
        if (repo, f) == CUT and CUT not in seen["cut"] and i >= len(data) // 2:
            seen["cut"].add(CUT)
            request.transport.abort()               # as if the connection dropped
            return resp
        await resp.write(data[i:i + 20_000])
        await asyncio.sleep(0.02)
    return resp


MANIFESTS = {
    "1.0": {"manifests": [{"digest": "sha256:arm", "platform": {"os": "linux", "architecture": "arm64"}},
                          {"digest": "sha256:amd", "platform": {"os": "linux", "architecture": "amd64"}}]},
    "sha256:amd": {"config": {"size": 1_000}, "layers": [{"size": 4_000_000}, {"size": 6_000_000}]},
    "sha256:arm": {"config": {"size": 1_000}, "layers": [{"size": 1}]},
    "2": {"config": {"size": 10}, "layers": [{"size": 100}, {"size": 50}]},
}


async def manifest(request):
    if request.headers.get("Authorization") != "Bearer t0k3n":
        realm = f"http://127.0.0.1:{port}/token"
        return web.Response(status=401, headers={"WWW-Authenticate": f'Bearer realm="{realm}",service="fake",'
                                                                     f'scope="repository:{request.match_info["repo"]}:pull"'})
    return web.json_response(MANIFESTS[request.match_info["ref"]])


async def token(request):
    ok = request.query.get("service") == "fake" and request.query.get("scope", "").endswith(":pull")
    return web.json_response({"token": "t0k3n" if ok else "wrong"})


async def tags(request):
    return web.json_response({"models": [{"name": "have:latest", "model": "have:latest"}]})


async def pull(request):
    resp = web.StreamResponse(headers={"Content-Type": "application/x-ndjson"})
    await resp.prepare(request)
    for ev in ({"status": "pulling manifest"}, {"digest": "d1", "total": 100, "completed": 50},
               {"digest": "d1", "total": 100, "completed": 100}, {"digest": "d2", "total": 50, "completed": 50},
               {"status": "success"}):
        await resp.write((json.dumps(ev) + "\n").encode())
        await asyncio.sleep(0.05)
    return resp


app = web.Application()
app.router.add_get("/api/models/{org}/{name}", model_info)
app.router.add_get("/{org}/{name}/resolve/{rev}/{path:.+}", resolve)
app.router.add_get("/v2/{repo:.+}/manifests/{ref}", manifest)
app.router.add_get("/token", token)
app.router.add_get("/ollama/api/tags", tags)        # under /ollama, so the brain's address names it
app.router.add_post("/ollama/api/pull", pull)
threading.Thread(target=lambda: web.run_app(app, host="127.0.0.1", port=port, handle_signals=False, print=None),
                 daemon=True).start()
time.sleep(0.5)


IMAGES: set[str] = set()        # what the fake Docker has pulled


class Images:
    def get(self, ref):
        if ref not in IMAGES:
            raise docker.errors.ImageNotFound(ref)


class API:
    def pull(self, name, tag, stream, decode):
        for cur in (0, 2_000_000, 4_000_000):
            yield {"status": "Downloading", "id": "l1", "progressDetail": {"current": cur, "total": 4_000_000}}
            time.sleep(0.2)
        yield {"status": "Download complete", "id": "l1"}
        yield {"status": "Downloading", "id": "l2", "progressDetail": {"current": 6_000_000, "total": 6_000_000}}
        yield {"status": "Pull complete", "id": "l2"}
        IMAGES.add(f"{name}:{tag}")


class Client:
    images, api = Images(), API()

    def info(self):
        return {"DockerRootDir": str(tmp / "docker")}


docker.from_env = lambda: Client()
base = yaml.safe_load((repo_root() / "examples/config.yaml").read_text())
base["channels"] = {"voice": {"enabled": False}}


def config(engines: dict, model: str = "x") -> WinstonConfig:
    return WinstonConfig.model_validate({**base, "engines": engines, "llm": {**base["llm"], "model": model}})


async def watch(job, until=60):
    """The bytes a job reports while it runs, so a test can see it move."""
    marks, t = [], time.time()
    while job.state == "running" and time.time() - t < until:
        marks.append(job.done)
        await asyncio.sleep(0.05)
    return marks


hub = tmp / "hf" / "hub"
# Two services with no packages, locked but never synced.
svc, other = tmp / "services" / "tts-tiny", tmp / "services" / "tts-other"
svc.mkdir(parents=True)
(svc / "pyproject.toml").write_text('[project]\nname = "tiny"\nversion = "0"\nrequires-python = ">=3.11"\n'
                                    'dependencies = []\n\n[tool.winston]\ninstalled-gb = 0.03\n')
subprocess.run([install._uv(), "lock", "--offline"], cwd=svc, check=True, capture_output=True,
               env={k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"})
shutil.copytree(svc, other)
LLAMA = {"driver": "docker", "image": f"127.0.0.1:{port}/made-up/llama.cpp:1.0", "port": 8000, "volumes": {},
         "args": ["-hf", "{model}"]}
VLLM = {**LLAMA, "image": f"127.0.0.1:{port}/made-up/vllm-openai:1.0", "args": ["{model}"]}


async def main():
    # ---- What an engine needs, sizes first ----
    parts = await install.parts("llm", config({"llm": LLAMA}, "fake/model-GGUF:Q4_K_M"))
    check("llama.cpp needs its image and the model", [p.key for p in parts],
          [f"image:127.0.0.1:{port}/made-up/llama.cpp:1.0", "hf:fake/model-GGUF:Q4_K_M"])
    check("the image's size is this platform's, read through the registry's token", parts[0].size, 10_001_000)
    check("the model is the one quant, not the UD one that also ends in Q4_K_M", parts[1].files, ["model-Q4_K_M.gguf"])
    check("and its size is read before downloading", (parts[1].size, parts[1].installed), (300_000, False))
    vparts = await install.parts("llm", config({"llm": VLLM}, "fake/vllm-model"))
    check("vLLM gets the safetensors and the small files, not original/ or the .bin", vparts[1].files,
          ["config.json", "model-00001-of-00002.safetensors", "model-00002-of-00002.safetensors", "tokenizer.json"])
    check("a local folder is not the Hub's", len(await install.parts("llm", config({"llm": VLLM}, "/models/mine"))), 1)
    ollama = {"driver": "external", "url": f"http://127.0.0.1:{port}/ollama", "port": port, "health": "/"}
    have = await install.parts("llm", config({"llm": ollama}, "have"))
    check("an Ollama model it has is installed", [(p.key, p.installed) for p in have], [("ollama:have:latest", True)])
    want = await install.parts("llm", config({"llm": ollama}, f"127.0.0.1:{port}/made-up/small:2"))
    check("one it lacks has its registry size", (want[0].installed, want[0].size), (False, 160))
    check("Ollama's store is not a folder Winston can check", want[0].where, None)

    # ---- Not enough space ----
    real = shutil.disk_usage
    install.shutil.disk_usage = lambda p: namedtuple("du", "total used free")(10**9, 10**9 - 1_000_000, 1_000_000)
    short = await install.ensure(parts, lambda text: None)
    install.shutil.disk_usage = real
    check("a pick bigger than the free space is refused with both numbers", (short.startswith("needs 10 MB on "),
                                                                             short.endswith(", 1 MB free")), (True, True))
    check("and nothing was started", install.JOBS, {})

    # ---- Getting it: progress per part ----
    jobs = [install.start(p) for p in parts]
    check("starting the same part again is the same job", install.start(parts[0]) is jobs[0], True)
    image_marks, model_marks = await asyncio.gather(watch(jobs[0]), watch(jobs[1]))
    check("both installed", [j.state for j in jobs], ["done", "done"])
    check("the image's pull reported its layers as they came", (len(set(image_marks)) > 1, jobs[0].done), (True, 10_000_000))
    check("the model's bytes went up as it downloaded", len(set(model_marks)) > 2, True)
    again = await install.parts("llm", config({"llm": LLAMA}, "fake/model-GGUF:Q4_K_M"))
    check("then both count as installed, the model from the cache", [p.installed for p in again], [True, True])
    snap = next(hub.glob("models--fake--model-GGUF/snapshots/*"))
    check("in the Hugging Face cache's own layout, as llama.cpp reads it", (snap / "model-Q4_K_M.gguf").read_bytes(),
          body[("fake/model-GGUF", "model-Q4_K_M.gguf")])

    # A connection that drops halfway: picked up from where it was, not from the start.
    job = install.start(vparts[1])
    await watch(job)
    check("a download that broke off still finishes", job.state, "done")
    asked = [int(r.split("=")[1].split("-")[0]) for f, r in seen["ranges"] if f == CUT[1]]
    check("by asking for the rest from what was on disk", len(asked) == 1 and 0 < asked[0] < len(body[CUT]), True)
    got = next(hub.glob("models--fake--vllm-model/snapshots/*")) / CUT[1]
    check("and the file is whole", got.read_bytes(), body[CUT])

    job = install.start(want[0])
    await watch(job)
    check("an Ollama pull adds up its layers", (job.state, job.done), ("done", 150))
    big = install.Part("hf:x", "the model", None, 16_400_000_000, False)
    check("a part's line says how far it is", install.line(install.Job(big, done=6_200_000_000)), "the model, 6.2 GB of 16.4 GB")
    check("and nothing yet is nothing", install.line(install.Job(big, done=0)), "the model, 0 MB of 16.4 GB")

    # ---- The supervisor: Installing, then Running ----
    p1, p2 = free_port(), free_port()
    tts = {"driver": "subprocess", "port": p1, "health": "/", "startup_timeout": 30, "cwd": str(svc),
           "cmd": [str(svc / ".venv/bin/python"), "-m", "http.server", str(p1), "--bind", "127.0.0.1"]}
    stt = {**tts, "port": p2, "cwd": str(other),
           "cmd": [str(other / ".venv/bin/python"), "-m", "http.server", str(p2), "--bind", "127.0.0.1"]}
    venv = await install.parts("tts", config({"tts": tts}))
    check("a service's venv is a part, with its measured size", [(p.title, p.size, p.installed) for p in venv],
          [("its Python packages", 30_000_000, False)])
    sup = Supervisor(config({"tts": tts, "stt": stt}))
    states = []
    task = asyncio.create_task(sup.start("tts"))
    while not task.done():
        states.append((sup.items["tts"].state, sup.items["tts"].detail))
        await asyncio.sleep(0.05)
    check("its start says Installing first", next(s for s, _ in states if s != "stopped"), "installing")
    check("naming what it gets", any(d.startswith("its Python packages") for s, d in states if s == "installing"), True)
    check("and Running once the venv is there", task.result().state, "healthy")
    check("its venv now counts as installed", (await install.parts("tts", sup.cfg))[0].installed, True)
    install.JOBS[f"venv:{svc}"].state = "running"
    check("while a sync runs, uv is not asked, which would wait out the sync's lock",
          (await install.parts("tts", sup.cfg))[0].installed, False)
    install.JOBS[f"venv:{svc}"].state = "done"
    await sup.stop("tts")

    task = asyncio.create_task(sup.start("stt"))
    while sup.items["stt"].state != "installing":
        await asyncio.sleep(0.01)
    await sup.stop("stt")
    await task
    check("stopped while installing, it does not start afterwards", (sup.items["stt"].state, await sup.items["stt"].driver.running()),
          ("stopped", False))
    await watch(install.JOBS[f"venv:{other}"])
    check("but the install itself went on", install.JOBS[f"venv:{other}"].state, "done")
    check("a command in Winston's own venv is never synced as a service",
          install._service([str(repo_root() / ".venv/bin/python"), "-m", "x"]), None)

    # ---- A first start that downloads, longer than its time limit ----
    p3 = free_port()
    blob = hub / "models--fake--slow" / "blobs" / "b.incomplete"
    grow = ("import pathlib, time, http.server, sys\n"
            f"b = pathlib.Path({str(blob)!r}); b.parent.mkdir(parents=True, exist_ok=True)\n"
            "for _ in range(16):\n    b.open('ab').write(b'x' * 100_000); time.sleep(0.5)\n"
            f"http.server.test(http.server.SimpleHTTPRequestHandler, port={p3}, bind='127.0.0.1')\n")
    slow = {"driver": "subprocess", "port": p3, "health": "/", "startup_timeout": 3, "cmd": [sys.executable, "-c", grow]}
    sup = Supervisor(config({"slow": slow}))
    details = []
    task = asyncio.create_task(sup.start("slow"))
    while not task.done():
        details.append(sup.items["slow"].detail)
        await asyncio.sleep(0.2)
    check("a start whose download keeps growing waits past its time limit", task.result().state, "healthy")
    check("saying how much has come", any(d.startswith("downloading, ") and d.endswith("so far") for d in details), True)
    check("and its time is not the usual restart time", task.result().started_in, None)
    await sup.stop("slow")


asyncio.run(main())
shutil.rmtree(tmp, ignore_errors=True)
shutil.rmtree(scratch, ignore_errors=True)
finish_checks("install: sizes before the first byte, space refused, progress per part, a broken download picked up, "
              "Installing before Running")
