"""What extensions call back into: `POST /api/ext/v1/<op>` with the extension's own token.

Every call is checked against that extension's manifest and logged with its id. Home Assistant is
reached only for entities exposed to Winston or picked in the extension's settings (picking one is
consent); reads take `home_assistant = "read"`, services that change something take "act" and must
name their entities. Memory goes through the same checks as the remember tool. The model is the
background model: the brain's server with thinking off, never one an extension picks."""
from __future__ import annotations

import json
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any

import httpx
from loguru import logger
from openai import AsyncOpenAI

from winston.config.schema import WinstonConfig
from winston.core import memory
from winston.core.brain import api_key, thinking_body
from winston.extension_host.sources import state_dir


class Refused(Exception):
    """403: outside the extension's permissions."""


class Failed(Exception):
    """502: Home Assistant (or another service) failed."""


class BadCall(Exception):
    """400: a call that makes no sense (an unknown operation, a missing argument)."""


def _deref(node: dict, defs: dict) -> dict:
    while "$ref" in node:
        node = defs.get(node["$ref"].rsplit("/", 1)[-1], {})
    return node


def picked_entities(settings_schema: dict[str, Any], settings: dict[str, Any]) -> set[str]:
    """Entity ids the user picked in an extension's settings (fields made with Entity()), at any
    depth: one plant's sensor in a dict of plants counts."""
    defs, out = settings_schema.get("$defs", {}), set()

    def walk(node: dict, value: Any) -> None:
        node = _deref(node, defs)
        if node.get("entity"):
            for v in value if isinstance(value, list) else [value]:
                if isinstance(v, str) and "." in v:
                    out.add(v)
        for option in node.get("anyOf", []):
            walk(option, value)
        if isinstance(value, dict):
            props, extra = node.get("properties", {}), node.get("additionalProperties")
            for k, v in value.items():
                if k in props:
                    walk(props[k], v)
                elif isinstance(extra, dict):
                    walk(extra, v)
        elif isinstance(value, list) and isinstance(node.get("items"), dict):
            for v in value:
                walk(node["items"], v)

    walk(settings_schema, settings)
    return out


def _ids(value: Any) -> list[str]:
    if value is None:
        return []
    return [value] if isinstance(value, str) else [str(v) for v in value]


class Services:
    def __init__(self, host):
        self.host = host
        self._ha_services: tuple[float, dict[str, dict[str, Any]]] | None = None

    @property
    def cfg(self) -> WinstonConfig:
        return self.host.cfg

    # the one entry point

    async def handle(self, ext_id: str, op: str, p: dict[str, Any]) -> Any:
        src = self.host.sources.get(ext_id)
        if src is None or src.manifest is None:
            raise Refused("unknown extension")
        perms = src.manifest.permissions
        try:
            result = await self._dispatch(ext_id, perms, op, p)
        except (Refused, Failed, BadCall) as e:
            logger.info(f"extension {ext_id}: {op} refused or failed: {e}")
            raise
        logger.debug(f"extension {ext_id}: {op}")
        return result

    async def _dispatch(self, ext_id: str, perms, op: str, p: dict[str, Any]) -> Any:
        if op.startswith("ha."):
            if perms.home_assistant is None:
                raise Refused("this extension has no home_assistant permission")
            return await self._ha(ext_id, perms.home_assistant, op, p)
        if op.startswith("memory."):
            if perms.memory is None:
                raise Refused("this extension has no memory permission")
            if op == "memory.facts":
                return self._facts(p.get("topic"))
            if op == "memory.remember":
                if perms.memory != "write":
                    raise Refused("remembering needs memory = \"write\"")
                r = memory.remember(self.cfg, p["topic"], p["text"], until=p.get("until"))
                if "error" in r:
                    raise BadCall(r["error"])
                return f"Remembered: {r['stored']}"
        if op == "presence":
            if not perms.presence:
                raise Refused("this extension has no presence permission")
            return await self.presence()
        if op == "llm.ask":
            if not perms.llm:
                raise Refused("this extension has no llm permission")
            return await self._ask(p["prompt"], p.get("schema"), int(p.get("max_tokens") or 500))
        if op == "ext.call":
            other = p["extension"]
            if other not in perms.extensions:
                raise Refused(f"this extension may not call {other}")
            tool = next((t for t in self.host.described.get(other, {}).get("tools", []) if t["name"] == p["tool"]), None)
            if tool is None or tool["tier"] != "read":
                raise Refused(f"{other} has no read tool {p['tool']}")
            r = await self.host.call_tool(other, p["tool"], p.get("args") or {})
            if not r["ok"]:
                raise Failed(r["error"])
            return r["result"]
        if op.startswith("timer."):
            return self._timers(ext_id, op, p)
        if op == "quota":
            return self._quota(ext_id, p["name"], int(p["per_day"]))
        if op == "entity.set":
            keys = {e["key"] for e in self.host.described.get(ext_id, {}).get("entities", [])}
            if p["key"] not in keys:
                raise BadCall(f"no entity {p['key']!r} declared")
            self.host.set_entity(ext_id, p["key"], p.get("entry"), p.get("value"), p.get("attributes") or {})
            return None
        if op in ("poke", "nudge.push", "nudge.withdraw", "nudge.list"):
            nudges = getattr(self.host, "nudges", None)
            if nudges is None:
                raise Failed("speaking up is not built yet (Extensions track step 3); nothing was said")
            return await nudges.handle(ext_id, op, p)
        raise BadCall(f"unknown operation {op}")

    # Home Assistant

    def _ha_client(self) -> httpx.AsyncClient:
        ha = self.cfg.providers.ha
        if ha is None:
            raise Failed("Home Assistant is not configured or is switched off")
        import os
        token = os.environ.get(ha.token_env, "")
        if not token:
            raise Failed(f"{ha.token_env} is not set")
        return httpx.AsyncClient(base_url=ha.url.rstrip("/"), timeout=10,
                                 headers={"Authorization": f"Bearer {token}"})

    def allowed_entities(self, ext_id: str) -> tuple[set[str], set[str]]:
        """(entity ids, whole domains) this extension may reach."""
        ha = self.cfg.providers.ha
        exposed = set(ha.expose) if ha else set()
        domains = set(ha.expose_domains) if ha else set()
        d = self.host.described.get(ext_id)
        if d:
            exposed |= picked_entities(d["settings"]["schema"], self.host.config(ext_id).settings)
        return exposed, domains

    def _check_entity(self, ext_id: str, entity_id: str) -> None:
        ids, domains = self.allowed_entities(ext_id)
        if entity_id not in ids and entity_id.split(".")[0] not in domains:
            raise Refused(f"{entity_id} is neither exposed to Winston nor picked in this extension's settings")

    async def _ha(self, ext_id: str, level: str, op: str, p: dict[str, Any]) -> Any:
        async with self._ha_client() as c:
            try:
                if op == "ha.state":
                    self._check_entity(ext_id, p["entity_id"])
                    r = await c.get(f"/api/states/{p['entity_id']}")
                    return None if r.status_code == 404 else self._json(r)
                if op == "ha.states":
                    ids, domains = self.allowed_entities(ext_id)
                    states = self._json(await c.get("/api/states"))
                    return [s for s in states if s["entity_id"].split(".")[0] == p["domain"]
                            and (s["entity_id"] in ids or p["domain"] in domains)]
                if op == "ha.history":
                    self._check_entity(ext_id, p["entity_id"])
                    r = await c.get(f"/api/history/period/{p['since']}",
                                    params={"filter_entity_id": p["entity_id"], "no_attributes": ""})
                    rows = self._json(r)
                    return rows[0] if rows else []
                if op in ("ha.query", "ha.call"):
                    return await self._service(ext_id, level, op, p, c)
            except httpx.HTTPError as e:
                raise Failed(f"Home Assistant did not answer: {e}") from e
        raise BadCall(f"unknown operation {op}")

    @staticmethod
    def _json(r: httpx.Response) -> Any:
        if r.status_code == 401:
            raise Failed("Home Assistant refused the token")
        if r.status_code >= 400:
            raise Failed(f"Home Assistant answered {r.status_code}: {r.text[:200]}")
        return r.json()

    async def _response_only(self, c: httpx.AsyncClient, domain: str, service: str) -> bool:
        now = time.monotonic()
        if self._ha_services is None or now - self._ha_services[0] > 600:
            listing = self._json(await c.get("/api/services"))
            self._ha_services = (now, {d["domain"]: d.get("services", {}) for d in listing})
        spec = self._ha_services[1].get(domain, {}).get(service)
        return bool(spec and isinstance(spec.get("response"), dict) and spec["response"].get("optional") is False)

    async def _service(self, ext_id: str, level: str, op: str, p: dict[str, Any], c: httpx.AsyncClient) -> Any:
        domain, service = p["domain"], p["service"]
        data, target = dict(p.get("data") or {}), dict(p.get("target") or {})
        if any(k in target or k in data for k in ("area_id", "device_id", "floor_id", "label_id")):
            raise Refused("name entities by id; areas, devices and labels cannot be checked against permissions")
        entities = _ids(data.pop("entity_id", None)) + _ids(target.pop("entity_id", None))
        if not entities:
            raise Refused("a Home Assistant call from an extension names its entities (entity_id)")
        for e in entities:
            self._check_entity(ext_id, e)
        if op == "ha.query":
            if not await self._response_only(c, domain, service):
                raise Refused(f"{domain}.{service} can change things; read permission covers only services "
                              "that just return data")
        elif level != "act":
            raise Refused("calling services needs home_assistant = \"act\"")
        body = {**data, "entity_id": entities}
        query = "?return_response" if op == "ha.query" else ""
        r = await c.post(f"/api/services/{domain}/{service}{query}", json=body)
        out = self._json(r)
        logger.info(f"extension {ext_id}: {domain}.{service} on {', '.join(entities)}")
        return out.get("service_response", out) if isinstance(out, dict) else out

    async def presence(self) -> dict[str, Any]:
        person = self.cfg.speak_up.home
        if not person:
            raise Failed("no person entity is set under Speaking up, so nobody knows whether you are home")
        async with self._ha_client() as c:
            s = self._json(await c.get(f"/api/states/{person}"))
        return {"home": s.get("state") == "home", "since": s.get("last_changed")}

    # memory

    def _facts(self, topic: str | None) -> list[dict[str, Any]]:
        out = []
        for t in memory.view(self.cfg):
            if topic in (None, t["name"]):
                for f in t["facts"]:
                    if f.get("expired"):
                        continue
                    out.append({"text": f.get("text") or f.get("fact"), "topic": t["name"],
                                "noted": f.get("noted"), "until": f.get("until")})
        return out

    # the background model

    async def _ask(self, prompt: str, schema: dict | None, max_tokens: int) -> Any:
        c = self.cfg.llm
        client = AsyncOpenAI(base_url=c.base_url, api_key=api_key(c.api_key_env), timeout=60)
        extra: dict[str, Any] = dict(thinking_body(self.cfg, "voice"))
        kwargs: dict[str, Any] = {}
        if schema:
            kwargs["response_format"] = {"type": "json_schema",
                                         "json_schema": {"name": "answer", "schema": schema}}
        try:
            r = await client.chat.completions.create(
                model=c.model, messages=[{"role": "user", "content": prompt}], max_tokens=max_tokens,
                temperature=0.2, extra_body=extra or None, **kwargs)
        except Exception as e:                                          # noqa: BLE001
            raise Failed(f"the model did not answer: {e}") from e
        text = (r.choices[0].message.content or "").strip()
        if not schema:
            return text
        try:
            return json.loads(text[text.find("{"):text.rfind("}") + 1] if "{" in text else text)
        except ValueError as e:
            raise Failed(f"the model's answer was not the JSON asked for: {text[:200]}") from e

    # timers and quotas, kept in the extension's state folder

    def _file(self, ext_id: str, name: str) -> Path:
        return state_dir(ext_id) / name

    def _read(self, path: Path, default: Any) -> Any:
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError):
            return default

    def _write(self, path: Path, value: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(value))
        tmp.replace(path)

    def timers(self, ext_id: str) -> list[dict[str, Any]]:
        return self._read(self._file(ext_id, "timers.json"), [])

    def set_timers(self, ext_id: str, rows: list[dict[str, Any]]) -> None:
        self._write(self._file(ext_id, "timers.json"), rows)

    def _timers(self, ext_id: str, op: str, p: dict[str, Any]) -> Any:
        rows = self.timers(ext_id)
        if op == "timer.set":
            jobs = {j["name"] for j in self.host.described.get(ext_id, {}).get("jobs", [])}
            if p["job"] not in jobs:
                raise BadCall(f"no job named {p['job']!r}")
            datetime.fromisoformat(p["at"])
            row = {"id": f"t{int(time.time() * 1000):x}{len(rows)}", "job": p["job"], "at": p["at"],
                   "data": p.get("data")}
            self.set_timers(ext_id, [*rows, row][-500:])
            self.host.timers_changed()
            return row
        if op == "timer.list":
            return [r for r in rows if p.get("job") in (None, r["job"])]
        if op == "timer.cancel":
            self.set_timers(ext_id, [r for r in rows if r["id"] != p["id"]])
            return None
        raise BadCall(f"unknown operation {op}")

    def _quota(self, ext_id: str, name: str, per_day: int) -> bool:
        path = self._file(ext_id, "quota.json")
        today = date.today().isoformat()
        counts = self._read(path, {})
        counts = {today: counts.get(today, {})}
        used = counts[today].get(name, 0)
        if used >= per_day:
            return False
        counts[today][name] = used + 1
        self._write(path, counts)
        return True
