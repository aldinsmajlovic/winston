"""Where extensions come from, and what each offers.

Three sources, one format (docs/extensions.md): built in (src/winston/extensions/), packages with the
entry point group `winston.extensions`, and personal ones in data/extensions/. An MCP server named in
config is a fourth, tools only. What an extension offers comes from `python -m winston_sdk describe`,
run without secrets, a token or the network, and cached until its code changes."""
from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from dataclasses import dataclass, field
from importlib import metadata
from importlib.util import find_spec
from pathlib import Path
from typing import Any, Literal

from loguru import logger
from packaging.requirements import InvalidRequirement, Requirement
from winston_sdk import __version__ as SDK_VERSION
from winston_sdk import manifest as manifest_mod

from winston.config import data_dir
from winston.config.schema import WinstonConfig

BUILTIN = Path(__file__).resolve().parent.parent / "extensions"
SDK_DIR = Path(manifest_mod.__file__).resolve().parents[2]         # sdk/ in a checkout
Origin = Literal["built-in", "package", "personal", "mcp"]


def personal_dir() -> Path:
    return data_dir() / "extensions"


def state_dir(ext_id: str) -> Path:
    return data_dir() / "state" / "extensions" / ext_id


def read_json(path: Path, default: Any) -> Any:
    """A JSON file's value, or `default` when it is missing or broken."""
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def write_json(path: Path, value: Any) -> None:
    """Written whole through a temporary file, so a crash mid-write leaves the old one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, default=str))
    tmp.replace(path)


@dataclass
class Source:
    id: str
    origin: Origin
    target: str                                    # a file, a folder, or module:attr; "" for an MCP server
    manifest: manifest_mod.Manifest | None = None  # None for an MCP server
    stamp: str = ""                                # changes when its code changes
    error: str | None = None
    paths: list[Path] = field(default_factory=list)   # watched for reload (personal ones)

    @property
    def name(self) -> str:
        return self.manifest.name if self.manifest else self.id


def _stamp(paths: list[Path]) -> str:
    h = hashlib.sha256()
    for p in sorted(paths):
        try:
            st = p.stat()
            h.update(f"{p}:{st.st_mtime_ns}:{st.st_size}".encode())
        except OSError:
            h.update(f"{p}:missing".encode())
    return h.hexdigest()[:16]


def _files(folder: Path) -> list[Path]:
    return [p for p in folder.rglob("*") if p.suffix in (".py", ".toml") and "__pycache__" not in p.parts]


def _from_path(path: Path, origin: Origin) -> Source | None:
    if path.name.startswith(("_", ".")) or path.name.endswith("_test.py"):
        return None
    if path.is_file() and path.suffix != ".py":
        return None
    if path.is_dir() and not ((path / "pyproject.toml").is_file() or (path / "__init__.py").is_file()):
        return None
    files = [path] if path.is_file() else _files(path)
    try:
        m = manifest_mod.read(path)
    except (manifest_mod.ManifestError, OSError, ValueError) as e:
        return Source(id=path.stem, origin=origin, target=str(path), error=str(e), stamp=_stamp(files), paths=files)
    return Source(id=m.id, origin=origin, target=str(path), manifest=m, stamp=_stamp(files), paths=files)


def discover(cfg: WinstonConfig) -> dict[str, Source]:
    """Every extension there is, by id. An id found twice is an error on the second."""
    found: dict[str, Source] = {}

    def add(src: Source | None):
        if src is None:
            return
        if src.id in found:
            first = found[src.id]
            logger.warning(f"extension id {src.id!r} twice: {first.target} and {src.target}; the second is left out")
            found[f"{src.id}~{src.origin}"] = Source(id=src.id, origin=src.origin, target=src.target,
                                                    error=f"another extension already has the id {src.id!r} "
                                                          f"({first.origin}: {first.target})")
            return
        found[src.id] = src

    if BUILTIN.is_dir():
        for p in sorted(BUILTIN.iterdir()):
            add(_from_path(p, "built-in"))
    for ep in metadata.entry_points(group="winston.extensions"):
        module = ep.value.partition(":")[0]
        try:
            spec = find_spec(module)
            origin_file = Path(spec.origin) if spec and spec.origin else None
            m = manifest_mod.read(origin_file) if origin_file else None
        except Exception as e:                                          # noqa: BLE001  (a broken package)
            add(Source(id=ep.name, origin="package", target=ep.value, error=str(e)))
            continue
        if m is None:
            add(Source(id=ep.name, origin="package", target=ep.value, error=f"cannot find {module}"))
            continue
        try:
            version = ep.dist.version if ep.dist else ""
        except Exception:                                               # noqa: BLE001
            version = ""
        add(Source(id=m.id, origin="package", target=ep.value, manifest=m,
                   stamp=f"{version}:{_stamp([origin_file])}", paths=[origin_file]))
    personal = personal_dir()
    if personal.is_dir():
        for p in sorted(personal.iterdir()):
            add(_from_path(p, "personal"))
    for ext_id, ec in cfg.extensions.items():
        if ec.mcp is not None:
            add(Source(id=ext_id, origin="mcp", target="", stamp=json.dumps(ec.mcp.model_dump(), sort_keys=True)))
    return found


def missing_requirements(m: manifest_mod.Manifest) -> list[str]:
    """Its dependencies that Winston's own environment does not have."""
    out = []
    for dep in m.dependencies:
        try:
            req = Requirement(dep)
            have = metadata.version(req.name)
            if req.specifier and not req.specifier.contains(have, prereleases=True):
                out.append(dep)
        except (metadata.PackageNotFoundError, InvalidRequirement):
            out.append(dep)
    return out


def command(src: Source) -> list[str]:
    """How to start its SDK: in Winston's environment, or in one of its own made by uv when it needs
    what Winston's does not have."""
    assert src.manifest is not None
    missing = missing_requirements(src.manifest)
    if not missing:
        return [sys.executable, "-m", "winston_sdk"]
    sdk = ["--with-editable", str(SDK_DIR)] if (SDK_DIR / "pyproject.toml").is_file() else ["--with", "winston-sdk"]
    withs = [a for d in missing for a in ("--with", d)]
    return ["uv", "run", "--no-project", "--quiet", "--python", f"{sys.version_info.major}.{sys.version_info.minor}",
            *sdk, *withs, "python", "-m", "winston_sdk"]


def sdk_stamp() -> str:
    """The SDK's version and, in a checkout, its files: a changed SDK describes every extension again."""
    files = list((SDK_DIR / "src").rglob("*.py")) if (SDK_DIR / "src").is_dir() else []
    return f"{SDK_VERSION}:{_stamp(files)}" if files else SDK_VERSION


def base_env() -> dict[str, str]:
    return {"PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1"}


async def describe(src: Source, timeout: float = 120) -> dict[str, Any]:
    """What it offers, cached by its code's stamp and the SDK's version."""
    cache = state_dir(src.id) / "describe.json"
    cached = read_json(cache, {})
    if cached.get("stamp") == src.stamp and cached.get("sdk") == sdk_stamp():
        return cached["describe"]
    proc = await asyncio.create_subprocess_exec(
        *command(src), "describe", src.target, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        env={**_path_env(), **base_env()}, cwd=str(data_dir()))
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        proc.kill()
        raise RuntimeError(f"describing it took longer than {timeout:g} s")
    if proc.returncode != 0:
        lines = [ln.removeprefix("error ").strip() for ln in err.decode(errors="replace").splitlines() if ln.strip()]
        raise RuntimeError(lines[-1] if lines else f"describe exited with {proc.returncode}")
    described = json.loads(out)
    write_json(cache, {"stamp": src.stamp, "sdk": sdk_stamp(), "describe": described})
    return described


def _path_env() -> dict[str, str]:
    """What a child needs to find programs and a home, and nothing else of winstond's environment
    (which holds every key in data/.env)."""
    import os
    return {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "USER", "LOGNAME", "LANG", "TERM",
                                                         "UV_CACHE_DIR", "XDG_CACHE_HOME")}
