"""Where the instance lives and how its config is read.

Data directory resolution: $WINSTON_DATA, else ./data if it holds a config.yaml, else
~/.local/share/winston. It contains config.yaml, .env (secrets), persona.md, memory/,
voices/ and logs/. Nothing in it is ever committed to the repo."""
import os
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


def save(cfg: WinstonConfig, path: Path | None = None) -> Path:
    path = path or data_dir() / "config.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(cfg.model_dump(mode="json", exclude_none=True), sort_keys=False, allow_unicode=True))
    return path
