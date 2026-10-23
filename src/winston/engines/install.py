"""What an engine needs on this computer before it can start, and getting it: its Docker image, its venv
under services/, and the brain's model (Hugging Face files, or an Ollama model). Each is a Part with its
size, read before the first byte where the source says it. Installing one is a Job in winstond, which goes
on with the page closed; two engines that need the same part share its job. A voice's own weights are not
a part: its library picks the files on its first start, and the supervisor waits while they arrive."""
from __future__ import annotations

import asyncio
import json
import os
import platform
import re
import shutil
import subprocess
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from loguru import logger

from winston.config import data_dir
from winston.config.schema import EngineConfig, WinstonConfig

GB = 1e9
ARCH = {"x86_64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(platform.machine(), platform.machine())
MANIFESTS = ("application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json, "
             "application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json")
# What vLLM and transformers read from a model repo besides the weights.
SMALL = (".json", ".txt", ".model", ".jinja", ".py", ".tiktoken")


@dataclass
class Part:
    key: str                 # what it is on disk: image:<ref>, venv:<dir>, hf:<repo>[:<quant>], ollama:<name>
    title: str               # how a sentence names it: "its Docker image", "the model"
    where: str | None        # the folder it lands in, for the free-space check; None when Winston cannot know
    size: int | None         # bytes still to get, None when that cannot be known first
    installed: bool
    files: list[str] = field(default_factory=list)      # Hugging Face: the files the engine loads
    source: str = ""                                     # Ollama: the server that pulls it


@dataclass
class Job:
    part: Part
    state: str = "running"   # running | done | error
    done: int = 0            # bytes so far
    error: str = ""


JOBS: dict[str, Job] = {}
_TASKS: set[asyncio.Task] = set()


def gb(n: float) -> str:
    return f"{n / GB:.1f} GB" if n >= 0.1 * GB else f"{max(1, round(n / 1e6)) if n else 0} MB"


# ---- What an engine needs ------------------------------------------------------------------------------

async def parts(name: str, cfg: WinstonConfig) -> list[Part]:
    """What engine `name` of cfg needs here, each installed or not. The network is asked for sizes only;
    offline, what is on disk counts and the rest has no size. A part that cannot be read (Docker not
    running, Ollama not answering) is left out: the engine's own start says what is wrong."""
    e = cfg.engines.get(name)
    if not e or not e.enabled:
        return []
    from winston.api.options import llm_engine
    found: list[Part | None] = []
    try:
        if e.driver == "docker" and e.image:
            found.append(await _image(e.image))
        if e.driver == "subprocess" and (svc := _service(e.cmd)):
            found.append(await asyncio.to_thread(_venv, svc))
        if name == "llm":
            kind = llm_engine(e.model_dump())
            if kind in ("vllm", "llamacpp"):
                found.append(await _hf(cfg.llm.model, kind, e))
            elif kind == "ollama":
                found.append(await _ollama(cfg.llm.model, (e.url or f"http://127.0.0.1:{e.port}").rstrip("/")))
    except Exception as x:  # noqa: BLE001
        logger.info(f"{name}: what it needs could not be read ({x}); its start will say")
    return [p for p in found if p]


async def _image(ref: str) -> Part:
    def local():
        import docker
        client = docker.from_env()
        try:
            client.images.get(ref)
            return True, client.info().get("DockerRootDir")
        except docker.errors.ImageNotFound:
            return False, client.info().get("DockerRootDir")
    have, root = await asyncio.to_thread(local)
    return Part(f"image:{ref}", "its Docker image", root, None if have else await _manifest_size(*_split_ref(ref)), have)


def _split_ref(ref: str) -> tuple[str, str, str]:
    """("registry-1.docker.io", "vllm/vllm-openai", "v0.29.0") for vllm/vllm-openai:v0.29.0."""
    name, tag = (ref.rsplit("@", 1) if "@" in ref else ref.rsplit(":", 1) if ":" in ref.rsplit("/", 1)[-1]
                 else (ref, "latest"))
    first, _, rest = name.partition("/")
    if rest and ("." in first or ":" in first or first == "localhost"):
        return first, rest, tag
    return "registry-1.docker.io", name if rest else f"library/{name}", tag


# Looked up once per winstond. Async on purpose: httpx's async client tries IPv6 and IPv4 side by side, and the
# sync one waits out every IPv6 address first, minutes behind a VPN that drops IPv6.
_SIZES: dict[str, int | None] = {}
_FILES: dict[str, dict[str, int]] = {}


async def _manifest_size(host: str, repo: str, tag: str, agent: str = "winston") -> int | None:
    """Compressed bytes of an image (or an Ollama model, the same format) for this machine, from its
    registry's manifest: anonymous, with the token the registry's 401 points to."""
    key = f"{host}/{repo}:{tag}"
    if key in _SIZES:
        return _SIZES[key]
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as c:
            headers = {"Accept": MANIFESTS, "User-Agent": agent}
            scheme = "http" if host.split(":")[0] in ("localhost", "127.0.0.1") else "https"   # as Docker treats them
            url = f"{scheme}://{host}/v2/{repo}/manifests/"
            r = await c.get(url + tag, headers=headers)
            if r.status_code == 401 and (challenge := r.headers.get("www-authenticate", "")).startswith("Bearer "):
                params = dict(re.findall(r'(\w+)="([^"]*)"', challenge))
                t = (await c.get(params.pop("realm"), params=params)).json()
                headers["Authorization"] = f"Bearer {t.get('token') or t.get('access_token')}"
                r = await c.get(url + tag, headers=headers)
            m = r.raise_for_status().json()
            if "manifests" in m:            # one per platform
                d = next(x for x in m["manifests"]
                         if x.get("platform", {}).get("os") == "linux" and x["platform"].get("architecture") == ARCH)
                m = (await c.get(url + d["digest"], headers=headers)).raise_for_status().json()
            _SIZES[key] = sum(x["size"] for x in m["layers"]) + m["config"]["size"]
            return _SIZES[key]
    except Exception as e:  # noqa: BLE001
        logger.info(f"size of {key} not known: {e}")
        return None


def _service(cmd: list[str]) -> Path | None:
    """The services/ folder whose venv runs the command: <dir>/.venv/bin/python with a uv.lock in <dir>,
    never Winston's own venv."""
    from winston.config import repo_root
    from winston.engines.drivers import _expand
    if not cmd:
        return None
    exe = Path(_expand(cmd[0]))
    svc = exe.parent.parent.parent
    ok = (exe.parent.name == "bin" and exe.parent.parent.name == ".venv" and (svc / "uv.lock").is_file()
          and svc.resolve() != repo_root().resolve())
    return svc if ok else None


def _uv() -> str | None:
    return os.environ.get("UV") or shutil.which("uv")


def _venv_env() -> dict[str, str]:
    # winstond runs in its own venv; a service's uv must not take that one for its project's.
    return {k: v for k, v in os.environ.items() if k not in ("VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT")}


def _venv(svc: Path) -> Part:
    uv, key = _uv(), f"venv:{svc}"
    # uv holds the project's lock while it syncs, so asking it then would wait out the whole install.
    syncing = (job := JOBS.get(key)) is not None and job.state == "running"
    have = not syncing and (svc / ".venv/bin/python").exists() and bool(uv) and subprocess.run(
        [uv, "sync", "--frozen", "--check"], cwd=svc, env=_venv_env(), capture_output=True, check=False).returncode == 0
    # ponytail: measured once per service (pyproject's tool.winston.installed-gb); re-measure when uv.lock changes.
    size = tomllib.loads((svc / "pyproject.toml").read_text()).get("tool", {}).get("winston", {}).get("installed-gb")
    return Part(key, "its Python packages", str(svc), None if have or size is None else int(size * GB), have)


async def _repo_files(repo: str) -> dict[str, int]:
    """The repo's files and their sizes, from the Hub's model API (its token, for a gated repo)."""
    if repo not in _FILES:
        from huggingface_hub import constants
        from huggingface_hub.utils import build_hf_headers
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as c:
            r = await c.get(f"{constants.ENDPOINT}/api/models/{repo}", params={"blobs": "true"}, headers=build_hf_headers())
        _FILES[repo] = {s["rfilename"]: s.get("size") or 0 for s in r.raise_for_status().json().get("siblings") or []}
    return _FILES[repo]


def _gguf(files: list[str], quant: str) -> list[str]:
    """The files of one quant, every part of a split one: those whose name ends in it ("UD-DW-Q4_K_S"). When
    "Q4_K_S" also ends "UD-Q4_K_S", the shortest name, which is the plain quant."""
    from winston.api.options import SPLIT
    q, stems = quant.lower(), {}
    for f in files:
        name = Path(f).name
        if not name.endswith(".gguf") or "mmproj" in name.lower():
            continue
        stem = SPLIT.sub("", name[:-5]).lower()
        if stem == q or (stem.endswith(q) and stem[-len(q) - 1] in "-._"):
            stems.setdefault(stem, []).append(f)
    return sorted(stems[min(stems, key=lambda s: (len(s), s))]) if stems else []


def _weights(files: list[str]) -> list[str]:
    """What vLLM loads: the safetensors (else the .bin weights) and the small files beside them."""
    files = [f for f in files if not f.startswith("original/")]
    heavy = [f for f in files if f.endswith(".safetensors")] or [f for f in files if f.endswith(".bin")]
    return sorted(heavy + [f for f in files if f.endswith(SMALL)])


async def _hf(model: str, kind: str, e: EngineConfig) -> Part | None:
    from huggingface_hub import try_to_load_from_cache

    from winston.api.options import downloaded, hf_cache
    repo, _, quant = model.partition(":")
    if repo.count("/") != 1 or repo.startswith(("/", ".", "~")) or (kind == "llamacpp" and not quant):
        return None          # a local folder, or a pick only the engine itself resolves
    hub = hf_cache(e.model_dump())
    try:
        files = await _repo_files(repo)
    except Exception as x:  # noqa: BLE001  (offline, or no such repo: the cache decides)
        logger.info(f"{model}: the Hub did not answer ({x}); reading the cache")
        have = await asyncio.to_thread(lambda: any(v == model for _, v, _ in downloaded(hub)))
        return Part(f"hf:{model}", "the model", str(hub), None, have)
    pick = _gguf(list(files), quant) if kind == "llamacpp" else _weights(list(files))
    if not pick:
        return None
    missing = await asyncio.to_thread(
        lambda: [f for f in pick if not isinstance(try_to_load_from_cache(repo, f, cache_dir=hub), str)])
    return Part(f"hf:{model}", "the model", str(hub), sum(files[f] for f in missing), not missing, files=pick)


async def _ollama(model: str, base: str) -> Part | None:
    name = model if ":" in model.rsplit("/", 1)[-1] else f"{model}:latest"
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            tags = (await c.get(base + "/api/tags")).raise_for_status().json().get("models") or []
    except Exception:  # noqa: BLE001
        return None
    have = any(name in (t.get("name"), t.get("model")) for t in tags)
    size = None
    if not have:
        path, tag = name.rsplit(":", 1)
        first, _, rest = path.partition("/")
        host, repo = (first, rest) if "." in first else ("registry.ollama.ai", path if rest else f"library/{path}")
        size = await _manifest_size(host, repo, tag, "ollama")
    return Part(f"ollama:{name}", "the model", None, size, have, source=base)


# ---- Space ---------------------------------------------------------------------------------------------

def _existing(p: Path) -> Path:
    p = p.expanduser()
    while not p.exists() and p != p.parent:
        p = p.parent
    return p


def free_bytes(where: str) -> int:
    return shutil.disk_usage(_existing(Path(where))).free


def short_of_space(missing: list[Part]) -> str:
    """What does not fit, per disk: "needs 19.4 GB on /home, 12.1 GB free", or ""."""
    need: dict[int, list] = {}
    for p in missing:
        if p.size and p.where:
            path = _existing(Path(p.where))
            need.setdefault(path.stat().st_dev, [path, 0])[1] += p.size
    for path, n in need.values():
        if n > (free := shutil.disk_usage(path).free):
            return f"needs {gb(n)} on {path}, {gb(free)} free"
    return ""


# ---- Getting it ----------------------------------------------------------------------------------------

def start(part: Part) -> Job:
    """Installs part in the background, or returns the install of it already under way."""
    job = JOBS.get(part.key)
    if job and job.state == "running":
        return job
    job = JOBS[part.key] = Job(part)
    task = asyncio.create_task(_run(job))
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return job


async def _run(job: Job) -> None:
    kind = job.part.key.split(":", 1)[0]
    logger.info(f"installing {job.part.key}" + (f" ({gb(job.part.size)})" if job.part.size else ""))
    try:
        await {"image": _pull, "venv": _sync, "hf": _download, "ollama": _ollama_pull}[kind](job)
        job.state = "done"
        logger.info(f"installed {job.part.key}")
    except Exception as e:  # noqa: BLE001
        job.state, job.error = "error", str(e) or type(e).__name__
        logger.error(f"installing {job.part.key} failed: {job.error}")


def line(job: Job) -> str:
    """"the model, 6.2 of 16.4 GB" for a status line."""
    p = job.part
    if job.state == "done":
        return f"{p.title}, done"
    if job.state == "error":
        return f"{p.title}: {job.error}"
    if p.size:
        return f"{p.title}, {gb(min(job.done, p.size))} of {gb(p.size)}"
    return f"{p.title}, {gb(job.done)} so far" if job.done else p.title


async def ensure(needs: list[Part], report) -> str:
    """Install what is missing, calling report(text) as it goes. "" once all is there, else why not."""
    missing = [p for p in needs if not p.installed]
    if not missing:
        return ""
    if short := short_of_space(missing):
        return short
    jobs = [start(p) for p in missing]
    while any(j.state == "running" for j in jobs):
        report("; ".join(line(j) for j in jobs))
        await asyncio.sleep(2)
    return "; ".join(line(j) for j in jobs if j.state == "error")


async def _pull(job: Job) -> None:
    import docker
    ref = job.part.key.removeprefix("image:")
    name, tag = ref.rsplit(":", 1) if ":" in ref.rsplit("/", 1)[-1] else (ref, "latest")

    def go():
        layers: dict[str, list[int]] = {}           # id: [bytes so far, size]
        for ev in docker.from_env().api.pull(name, tag=tag, stream=True, decode=True):
            if "error" in ev:
                raise RuntimeError(ev["error"])
            d, lid = ev.get("progressDetail") or {}, ev.get("id", "")
            if ev.get("status") == "Downloading" and d.get("total"):
                layers[lid] = [d.get("current", 0), d["total"]]
            elif ev.get("status") in ("Download complete", "Pull complete") and lid in layers:
                layers[lid][0] = layers[lid][1]
            job.done = sum(x[0] for x in layers.values())
    await asyncio.to_thread(go)


def _tree_bytes(path: Path) -> int:
    total = 0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                total += os.lstat(os.path.join(root, f)).st_size
            except OSError:
                pass
    return total


async def _sync(job: Job) -> None:
    svc = Path(job.part.key.removeprefix("venv:"))
    if not (uv := _uv()):
        raise RuntimeError("uv is not installed")
    log = data_dir() / "logs" / f"install-{svc.name}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "ab") as f:     # noqa: ASYNC230  (a local file; the process keeps its own copy)
        proc = await asyncio.create_subprocess_exec(uv, "sync", "--frozen", cwd=svc, env=_venv_env(), stdout=f, stderr=f)
    try:
        while proc.returncode is None:
            try:
                await asyncio.wait_for(proc.wait(), 3)
            except TimeoutError:
                pass
            job.done = await asyncio.to_thread(_tree_bytes, svc / ".venv")
    except asyncio.CancelledError:
        proc.kill()                 # winstond is stopping; the next sync picks up where this one was
        raise
    if proc.returncode:
        lines = [x for x in log.read_text(errors="replace").splitlines() if x.strip()]
        raise RuntimeError(f"uv sync exited with code {proc.returncode}" + (f": {lines[-1][:300]}" if lines else ""))


async def _download(job: Job) -> None:
    from huggingface_hub import hf_hub_download
    from tqdm.auto import tqdm
    repo = job.part.key.removeprefix("hf:").partition(":")[0]

    class Bytes(tqdm):          # every file's progress, added to the job; nothing printed
        def __init__(self, *a, **k):
            # Xet opens a second bar for the bytes on the wire beside the file's own; the file's is the size.
            self.counts = not str(k.get("desc", "")).endswith("downloading bytes")
            super().__init__(*a, **{**k, "disable": True})
            if self.counts:
                job.done += k.get("initial") or 0   # a resumed file's bytes already there

        def update(self, n=1):
            if self.counts:
                job.done += n or 0

    for f in job.part.files:
        await asyncio.to_thread(hf_hub_download, repo, f, cache_dir=job.part.where, tqdm_class=Bytes)


async def _ollama_pull(job: Job) -> None:
    name = job.part.key.removeprefix("ollama:")
    got: dict[str, int] = {}
    async with (httpx.AsyncClient(timeout=httpx.Timeout(30, read=None)) as c,
                c.stream("POST", job.part.source + "/api/pull", json={"model": name}) as r):
        r.raise_for_status()
        async for text in r.aiter_lines():
            ev = json.loads(text) if text.strip() else {}
            if ev.get("error"):
                raise RuntimeError(ev["error"])
            if ev.get("digest") and ev.get("completed") is not None:
                got[ev["digest"]] = ev["completed"]
                job.done = sum(got.values())


# ---- A first start that downloads ----------------------------------------------------------------------

def hub_bytes(hub: Path | None) -> int:
    """Bytes in a Hugging Face cache and the Xet cache beside it, partial files included: when this grows
    while an engine starts, it is downloading, not stuck."""
    if not hub:
        return 0
    return _tree_bytes(hub) + _tree_bytes(hub.parent / "xet")
