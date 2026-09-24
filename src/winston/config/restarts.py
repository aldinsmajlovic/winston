"""Which engines a configuration change restarts, read from the schema's `restarts` metadata:
a field's own list if it has one, else the enclosing model's. `PUT /api/config` reports the
result, so the web UI and the CLI never compute it themselves. "{brains}" expands to every channel
that runs a brain; the web channel is not among them because winstond rebuilds it on every save."""
from typing import Any

ENGINE = "{engine}"
BRAINS = "{brains}"
BRAIN_CHANNELS = ("voice", "discord", "satellite")    # every channel process that builds a Brain


def _deref(schema: dict, root: dict) -> dict:
    while "$ref" in schema:
        name = schema["$ref"].rsplit("/", 1)[-1]
        schema = {**root["$defs"][name], **{k: v for k, v in schema.items() if k != "$ref"}}
    if "anyOf" in schema:   # Optional[T] is anyOf [T, null]; the metadata sits on T
        alts = [a for a in schema["anyOf"] if a.get("type") != "null"]
        if len(alts) == 1:
            inner = _deref(alts[0], root)
            schema = {**inner, **{k: v for k, v in schema.items() if k != "anyOf"}}
    return schema


def restarts_for(old: Any, new: Any, schema: dict, root: dict | None = None,
                 inherited: list[str] | None = None, path: tuple[str, ...] = (), engine: str | None = None) -> set[str]:
    """Engines to restart so that a running instance reflects `new` instead of `old`."""
    if old == new:
        return set()
    root = root or schema
    schema = _deref(schema, root)
    own: list[str] = schema.get("restarts", inherited or [])
    out: set[str] = set()
    props = schema.get("properties")
    if props and isinstance(old, dict) != isinstance(new, dict) and None in (old, new):
        old, new = old or {}, new or {}     # a section added or removed: every field in it changed
    if props and isinstance(old, dict) and isinstance(new, dict):
        for key in set(old) | set(new):
            if key in props:
                out |= restarts_for(old.get(key), new.get(key), props[key], root, own, path + (key,), engine)
        return out
    extra = schema.get("additionalProperties")
    if isinstance(extra, dict) and isinstance(old, dict) and isinstance(new, dict):
        for key in set(old) | set(new):
            eng = key if path == ("engines",) else engine
            out |= restarts_for(old.get(key), new.get(key), extra, root, own, path + (key,), eng)
        return out
    out = {engine if r == ENGINE and engine else r for r in own} - {ENGINE}
    if BRAINS in out:
        out = (out - {BRAINS}) | set(BRAIN_CHANNELS)
    return out
