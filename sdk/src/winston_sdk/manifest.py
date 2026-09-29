"""The manifest: the `[tool.winston]` table, read without running the extension's code.

A single file carries it in its PEP 723 block, a package in its `pyproject.toml`. The same keys in
both. What an extension may reach is decided here, so the card can show it before anything runs."""
from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

API_MAJOR = 1
SDK_PACKAGE = "winston-sdk"

# PEP 723's reference expression for `# /// type` blocks.
_BLOCK = re.compile(r"(?m)^# /// (?P<type>[a-zA-Z0-9-]+)$\s(?P<content>(^#(| .*)$\s)+)^# ///$")


class Permissions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    network: list[str] = Field(default_factory=list, description="Hosts ctx.http may reach; [\"*\"] for any.")
    home_assistant: Literal["read", "act"] | None = None
    memory: Literal["read", "write"] | None = None
    presence: bool = False
    llm: bool = False
    extensions: list[str] = Field(default_factory=list, description="Extensions whose read tools it may call.")


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    name: str
    description: str = ""
    icon: str = "puzzle"
    api: int
    permissions: Permissions = Field(default_factory=Permissions)
    dependencies: list[str] = Field(default_factory=list,
                                    description="What it needs besides winston-sdk (the standard key).")
    source: str = Field("", description="The file or folder it was read from.")


class ManifestError(ValueError):
    pass


def _script_block(text: str) -> dict | None:
    blocks = [m for m in _BLOCK.finditer(text) if m.group("type") == "script"]
    if len(blocks) > 1:
        raise ManifestError("more than one `# /// script` block")
    if not blocks:
        return None
    body = "".join(line[2:] if line.startswith("# ") else line[1:]
                   for line in blocks[0].group("content").splitlines(keepends=True))
    return tomllib.loads(body)


def _extra_deps(deps: list[str]) -> list[str]:
    """Dependencies other than the SDK itself, which is always there."""
    return [d for d in deps if not re.match(rf"^{re.escape(SDK_PACKAGE)}\b", d.strip(), re.I)]


def read(path: str | Path) -> Manifest:
    """The manifest of a single-file extension, a package folder, or a file inside a package."""
    p = Path(path).resolve()
    if p.is_file() and p.suffix == ".py":
        data = _script_block(p.read_text(encoding="utf-8"))
        if data is not None:
            return _build(data.get("tool", {}).get("winston"), data.get("dependencies", []), p)
        p = p.parent                              # a module in a package: its pyproject is above it
    for folder in [p, *p.parents]:
        pyproject = folder / "pyproject.toml"
        if pyproject.is_file():
            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
            return _build(data.get("tool", {}).get("winston"), data.get("project", {}).get("dependencies", []),
                          folder)
    raise ManifestError(f"no [tool.winston] manifest for {path}")


def _build(table: dict | None, deps: list[str], source: Path) -> Manifest:
    if not table:
        raise ManifestError(f"{source}: no [tool.winston] table")
    try:
        return Manifest(**table, dependencies=_extra_deps(deps), source=str(source))
    except Exception as e:                       # pydantic's message names the key that is wrong
        raise ManifestError(f"{source}: {e}") from e
