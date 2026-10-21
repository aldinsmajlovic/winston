"""Where the instance lives and how its config is read.

Data directory resolution: $WINSTON_DATA, else ./data if it holds a config.yaml, else
~/.local/share/winston. It contains config.yaml, .env (secrets), persona.md, memory/,
voices/ and logs/. Nothing in it is ever committed to the repo."""
import hashlib
import os
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

import yaml
from dotenv import load_dotenv

from winston.config.schema import WinstonConfig

_REPO = Path(__file__).resolve().parents[3]


def repo_root() -> Path:
    return _REPO


@lru_cache(maxsize=1)
def data_dir() -> Path:
    if env := os.environ.get("WINSTON_DATA"):
        return Path(env).expanduser().resolve()
    local = Path.cwd() / "data"
    if (local / "config.yaml").is_file():
        return local.resolve()
    return (Path.home() / ".local/share/winston").resolve()


def resolve(rel: str) -> Path:
    """A data-directory-relative path; absolute paths and {data}/{repo} placeholders pass through."""
    s = rel.replace("{data}", str(data_dir())).replace("{repo}", str(_REPO))
    p = Path(s).expanduser()
    return p if p.is_absolute() else data_dir() / p


def load(path: Path | None = None) -> WinstonConfig:
    d = data_dir()
    load_dotenv(d / ".env", override=False)
    path = path or d / "config.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"no config at {path}; run `winston init` or set WINSTON_DATA")
    raw = yaml.safe_load(path.read_text()) or {}
    return WinstonConfig.model_validate(raw)


def version(path: Path | None = None) -> str:
    """The saved file's version: a hash of its bytes, "" when there is none. An explicit "" in a field
    (quiet hours never, no health check) is kept through save and load like any other value."""
    path = path or data_dir() / "config.yaml"
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    except FileNotFoundError:
        return ""


def save(cfg: WinstonConfig, path: Path | None = None) -> Path:
    """Write the config in one step (a temporary file renamed over it), so a reader never sees half a file."""
    path = path or data_dir() / "config.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(yaml.safe_dump(cfg.model_dump(mode="json", exclude_none=True), sort_keys=False, allow_unicode=True))
    os.replace(tmp, path)
    return path


Path_ = list[str | int]
_MISSING = object()


def get_in(value, path: Path_, default=None):
    """The value at a path of keys (and list indexes), or default when any step is missing."""
    for key in path:
        try:
            value = value[key]
        except (KeyError, IndexError, TypeError):
            return default
    return value


def apply_changes(raw: dict, changes: list[tuple[Path_, object]]) -> dict:
    """A copy of a config dict with each (path, value) set; None removes the key, so the default applies.
    Missing sections on the way are created, except for a removal: a key under a section that is gone
    is already removed."""
    out = deepcopy(raw)
    for path, value in changes:
        if not path:
            raise ValueError("empty path")
        if value is None and not isinstance(get_in(out, path[:-1], _MISSING), dict | list):
            continue
        node = out
        for key in path[:-1]:
            nxt = get_in(node, [key], _MISSING)
            if not isinstance(nxt, dict | list):
                if isinstance(node, list):
                    raise ValueError(f"no item {key} in {'.'.join(map(str, path))}")  # noqa: TRY004
                nxt = node[key] = {}
            node = nxt
        last = path[-1]
        if value is None:
            if isinstance(node, dict):
                node.pop(last, None)
            else:
                raise ValueError(f"cannot remove list item {'.'.join(map(str, path))}; set the whole list")
        elif isinstance(node, list):
            if not isinstance(last, int) or not -len(node) <= last < len(node):
                raise ValueError(f"no item {last} in {'.'.join(map(str, path[:-1]))}")
            node[last] = value
        else:
            node[last] = value
    return out
