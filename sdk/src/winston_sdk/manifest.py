"""The manifest: the `[tool.winston]` table, read without running the extension's code.

A single file carries it in its PEP 723 block, a package in its `pyproject.toml`. The same keys in
both. What an extension may reach is decided here, so the card can show it before anything runs."""
from __future__ import annotations

import re
import tomllib
from collections.abc import Collection
from pathlib import Path
from typing import Any, Literal

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
    speakers: bool = Field(False, description="Play an audio stream on your speakers (ctx.speakers).")
    devices: bool = Field(False, description="Every device Home Assistant knows: name, maker, model, firmware and "
                                             "area (ctx.devices), with no states, addresses or serial numbers.")
    extensions: list[str] = Field(default_factory=list, description="Extensions whose read tools it may call.")


def refusal(perms: Permissions, op: str, params: dict[str, Any],
            picked: Collection[tuple[str, str]] = ()) -> str | None:
    """Why these permissions do not cover one of winstond's operations (`ctx.ha.call` is "ha.call"), or
    None. `picked` holds the (extension, tool) pairs picked in its settings with `Extensions`, which may be
    called as if listed. winstond and the test harness both ask; the entities a Home Assistant call names
    are winstond's to check, and `network` is ctx.http's."""
    if op.startswith("ha.") and perms.home_assistant is None:
        return "this extension has no home_assistant permission"
    if op == "ha.call" and perms.home_assistant != "act":
        return "calling services needs home_assistant = \"act\""
    if op.startswith("memory.") and perms.memory is None:
        return "this extension has no memory permission"
    if op == "memory.remember" and perms.memory != "write":
        return "remembering needs memory = \"write\""
    if op == "presence" and not perms.presence:
        return "this extension has no presence permission"
    if op == "llm.ask" and not perms.llm:
        return "this extension has no llm permission"
    if op == "devices" and not perms.devices:
        return "this extension has no devices permission"
    if op == "ext.call" and params["extension"] not in perms.extensions and \
            (params["extension"], params["tool"]) not in picked:
        return f"this extension may not call {params['extension']}"
    if op.startswith("speakers.") and not perms.speakers:
        return "this extension has no speakers permission"
    return None


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
    if "dependencies" in table:
        raise ManifestError(f"{source}: dependencies go on their own line before [tool.winston] (PEP 723's "
                            f"`dependencies = [...]`, or [project] dependencies in pyproject.toml), not in it")
    try:
        return Manifest(**table, dependencies=_extra_deps(deps), source=str(source))
    except Exception as e:                       # pydantic's message names the key that is wrong
        raise ManifestError(f"{source}: {e}") from e
