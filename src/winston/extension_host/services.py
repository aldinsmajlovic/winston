"""What extensions call back into: `POST /api/ext/v1/<op>` with the extension's own token.

Every call is checked against that extension's manifest and logged with its id. Home Assistant is
reached only for entities exposed to Winston or picked in the extension's settings (picking one is
consent); reads take `home_assistant = "read"`, services that change something take "act" and must
name their entities. Memory goes through the same checks as the remember tool. The model is the
background model: the brain's server with thinking off, never one an extension picks. Its calls keep to
a slot of their own and yield to conversations: one waits while anyone is talking to him, and one under
way when a conversation starts is stopped and asked again after it."""
from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from loguru import logger
from openai import AsyncOpenAI
from winston_sdk.fields import picked
from winston_sdk.manifest import refusal, tool_refusal

from winston.config import resolve
from winston.config.schema import WinstonConfig
from winston.core import guard, memory
from winston.core.common import log_event
from winston.core.brain import api_key, server_slot, thinking_body
from winston.extension_host import accounts
from winston.extension_host.sources import read_json, state_dir, write_json


ASK_BUDGET = 45.0      # seconds a background call may wait for conversations; the SDK gives up at 60


class Refused(Exception):
    """403: outside the extension's permissions."""


class Failed(Exception):
    """502: Home Assistant (or another service) failed."""


class BadCall(Exception):
    """400: a call that makes no sense (an unknown operation, a missing argument)."""


def picked_entities(settings_schema: dict[str, Any], settings: dict[str, Any]) -> set[str]:
    """Entity ids the user picked in an extension's settings (fields made with Entity()), at any depth."""
    return {v for _, v in picked(settings_schema, settings, "entity") if "." in v}


def _ids(value: Any) -> list[str]:
    if value is None:
        return []
    return [value] if isinstance(value, str) else [str(v) for v in value]


class Services:
    def __init__(self, host):
        self.host = host
        self._ha_services: tuple[float, dict[str, dict[str, Any]]] | None = None
        self.talking: Callable[[], bool] = lambda: False                # set by winstond: a conversation runs
        self.llm_up: Callable[[], bool] = lambda: True                  # set by winstond: the brain's server is healthy

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

    def picked_tools(self, ext_id: str) -> set[tuple[str, str]]:
        """(extension, tool) pairs picked in its settings with Extensions(tool=...): consent to call them."""
        d = self.host.described.get(ext_id)
        if not d:
            return set()
        return {(other, tool) for tool, other in picked(d["settings"]["schema"], self.host.config(ext_id).settings,
                                                        "extension_tool")}

    async def _dispatch(self, ext_id: str, perms, op: str, p: dict[str, Any]) -> Any:
        if (why := refusal(perms, op, p, self.picked_tools(ext_id) if op == "ext.call" else ())) is not None:
            raise Refused(why)
        if op.startswith("ha."):
            return await self._ha(ext_id, op, p)
        if op == "memory.facts":
            return self._facts(p.get("topic"))
        if op == "memory.diary":
            from winston.core.diary import Diary
            since = (date.today() - timedelta(days=int(p.get("days") or 7))).isoformat()
            return [{"day": d, "text": t} for d, t in Diary(self.cfg).entries(60) if d >= since]
        if op == "memory.remember":
            r = memory.remember(self.cfg, p["topic"], p["text"], until=p.get("until"))
            if "error" in r:
                raise BadCall(r["error"])
            return f"Remembered: {r['stored']}"
        if op == "presence":
            return await self.presence()
        if op == "llm.ask":
            return await self._ask(p["prompt"], p.get("schema"), int(p.get("max_tokens") or 500))
        if op == "devices":
            return await self._devices()
        if op == "tools.list":
            return [{"name": n, "tier": t["tier"], "description": t["description"]}
                    for n, t in (await self._brain_tools(ext_id)).items()]
        if op == "tools.call":
            return await self._call_brain_tool(ext_id, perms, p["name"], p.get("args") or {})
        if op == "ext.call":
            # An SDK extension's tools, or an MCP server's allowed ones, at the tier you gave them.
            other = p["extension"]
            if self.host.tier(other, p["tool"]) != "read":
                raise Refused(f"{other} has no read tool {p['tool']}")
            r = await self.host.call_tool(other, p["tool"], p.get("args") or {})
            if not r["ok"]:
                raise Failed(r["error"])
            return r["result"]
        if op.startswith("timer."):
            return self._timers(ext_id, op, p)
        if op == "quota":
            return self._quota(ext_id, p["name"], int(p["per_day"]))
        if op == "account.token":
            try:
                return await accounts.token(self.host, ext_id, str(p.get("field")))
            except accounts.AccountError as e:
                raise Failed(str(e)) from e
        if op == "entity.set":
            keys = {e["key"] for e in self.host.described.get(ext_id, {}).get("entities", [])}
            if p["key"] not in keys:
                raise BadCall(f"no entity {p['key']!r} declared")
            self.host.set_entity(ext_id, p["key"], p.get("entry"), p.get("value"), p.get("attributes") or {})
            return None
        if op in ("speakers.play", "speakers.stop", "speakers.volume"):
            if self.host.speakers is None:
                raise Failed("this winstond has no speakers to play on")
            if op == "speakers.stop":
                self.host.speakers.stop(ext_id)
                return None
            if op == "speakers.volume":
                level = p.get("level")
                return {"volume": self.host.speakers.volume(None if level is None else float(level),
                                                            float(p.get("change") or 0))}
            url = httpx.URL(str(p["url"]))
            local = url.host in ("127.0.0.1", "localhost", "::1")
            if url.scheme not in ("http", "https") or not (local or url.host in perms.network or "*" in perms.network):
                raise Refused("a stream must be served on this machine (127.0.0.1) or from a host it declares")
            return {"speakers": self.host.speakers.play(ext_id, str(url), p.get("title"))}
        if op in ("poke", "nudge.push", "nudge.withdraw", "nudge.list"):
            if self.host.nudges is None:
                raise Failed("speaking up is not running in this winstond; nothing was said")
            try:
                return await self.host.nudges.handle(ext_id, op, p)
            except (KeyError, ValueError) as e:
                raise BadCall(str(e)) from e
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

    async def _ha(self, ext_id: str, op: str, p: dict[str, Any]) -> Any:
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
                    return await self._service(ext_id, op, p, c)
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
        def spec() -> dict | None:
            return self._ha_services[1].get(domain, {}).get(service) if self._ha_services else None
        now = time.monotonic()
        # read again after ten minutes, or when the service is missing (an integration added since)
        if self._ha_services is None or now - self._ha_services[0] > 600 or spec() is None:
            listing = self._json(await c.get("/api/services"))
            self._ha_services = (now, {d["domain"]: d.get("services", {}) for d in listing})
        found = spec()
        return bool(found and isinstance(found.get("response"), dict) and found["response"].get("optional") is False)

    async def _service(self, ext_id: str, op: str, p: dict[str, Any], c: httpx.AsyncClient) -> Any:
        domain, service = p["domain"], p["service"]
        data, target = dict(p.get("data") or {}), dict(p.get("target") or {})
        if any(k in target or k in data for k in ("area_id", "device_id", "floor_id", "label_id")):
            raise Refused("name entities by id; areas, devices and labels cannot be checked against permissions")
        entities = _ids(data.pop("entity_id", None)) + _ids(target.pop("entity_id", None))
        if not entities:
            raise Refused("a Home Assistant call from an extension names its entities (entity_id)")
        for e in entities:
            self._check_entity(ext_id, e)
        if op == "ha.query" and not await self._response_only(c, domain, service):
            raise Refused(f"{domain}.{service} can change things; read permission covers only services "
                          "that just return data")
        body = {**data, "entity_id": entities}
        query = "?return_response" if op == "ha.query" else ""
        r = await c.post(f"/api/services/{domain}/{service}{query}", json=body)
        out = self._json(r)
        logger.info(f"extension {ext_id}: {domain}.{service} on {', '.join(entities)}")
        return out.get("service_response", out) if isinstance(out, dict) else out

    async def _brain_tools(self, ext_id: str) -> dict[str, dict[str, Any]]:
        """The brain's tools from the other extensions and Home Assistant, by the names it sees."""
        out = {t["name"]: {"tier": t["tier"], "description": t["description"], "extension": t["extension"],
                           "tool": t["tool"]} for t in self.host.brain_tools() if t["extension"] != ext_id}
        if self.cfg.providers.ha is not None:
            from winston.providers.home_assistant import HomeAssistant
            try:
                rows = await HomeAssistant(self.cfg).tools()
            except Exception as e:                                      # said to the extension as its error
                raise Failed(f"Home Assistant did not list its tools: {e}") from e
            for row in rows:
                for n in row.names:
                    out[n] = {"tier": guard.tool_tier(n), "description": row.description}
        return out

    async def _call_brain_tool(self, ext_id: str, perms, name: str, args: dict[str, Any]) -> Any:
        """One of the brain's tools, at its tier and logged like the brain's own calls (plan §6 rule 7)."""
        t = (await self._brain_tools(ext_id)).get(name)
        if t is None:
            raise BadCall(f"the brain has no tool {name}")
        if (why := tool_refusal(perms, name, t["tier"])) is not None:
            raise Refused(why)
        log = resolve(self.cfg.logging.tool_calls)
        log_event(log, f"extension:{ext_id}", event="call", tool=name, args=args)
        if "extension" in t:
            r = await self.host.call_tool(t["extension"], t["tool"], args)
            ok, result = r["ok"], r.get("result", r.get("error"))
        else:
            from winston.providers.home_assistant import HomeAssistant
            try:
                result = await HomeAssistant(self.cfg).call_tool(name, args)
            except Exception as e:                                      # noqa: BLE001  (said back as its error)
                result = f"Error: {e or type(e).__name__}"
            ok = guard.result_ok(result)
        text = result if isinstance(result, str) else json.dumps(result, default=str)
        log_event(log, f"extension:{ext_id}", event="result", tool=name, result=text[:2000])
        logger.info(f"extension {ext_id}: tool {name} {'ok' if ok else 'failed'}")
        if not ok:
            raise Failed(text[:300])
        return result

    async def _devices(self) -> list[dict[str, Any]]:
        """Home Assistant's device registry, every device exposed or not, cut to what `devices` allows:
        no addresses, serial numbers, identifiers or integration entries."""
        from winston.providers.home_assistant import HomeAssistant
        try:
            devices, areas = await HomeAssistant(self.cfg).devices()
        except Exception as e:                                          # said to the extension as its error
            raise Failed(f"Home Assistant did not list its devices: {e}") from e
        return [{"id": d["id"], "name": d.get("name_by_user") or d.get("name") or "",
                 "manufacturer": d.get("manufacturer"), "model": d.get("model"), "model_id": d.get("model_id"),
                 "sw_version": d.get("sw_version"), "hw_version": d.get("hw_version"),
                 "area": areas.get(d.get("area_id") or ""), "service": d.get("entry_type") == "service"}
                for d in devices]

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

    async def background_slot(self) -> int | None:
        """The server slot background calls keep to (brain.server_slot), never the one that holds the box's
        cached prompt. Measured with the 27B and an extension asking one story after another: a spoken turn's
        median first audio went from 982 ms to 2147 ms unpinned, the first turn 8.4 s (its prompt evicted)."""
        return await server_slot(self.cfg, spoken=False)

    async def _ask(self, prompt: str, schema: dict | None, max_tokens: int) -> Any:
        """Waits for the brain's server to be up (it loads for seconds after winstond starts it).
        Yields to conversations: waits while one runs, and a call under way when one starts is
        stopped (the server drops it) and asked again once it is over. Measured with the slot alone: a
        spoken turn's median first audio still went from 892 ms to 1782 ms while an extension asked."""
        deadline = time.monotonic() + ASK_BUDGET
        while True:
            while self.talking() or not self.llm_up():
                if time.monotonic() > deadline:
                    raise Failed("the model is not up yet; try again later" if not self.llm_up() else
                                 "a conversation is running and background calls wait for it; try again later")
                await asyncio.sleep(0.2)
            ask = asyncio.create_task(self._ask_once(prompt, schema, max_tokens))
            while not ask.done() and not self.talking():
                await asyncio.wait({ask}, timeout=0.05)
            if ask.done():
                return ask.result()
            ask.cancel()
            await asyncio.gather(ask, return_exceptions=True)
            logger.info("background model call stopped for a conversation; asked again after it")

    async def _ask_once(self, prompt: str, schema: dict | None, max_tokens: int) -> Any:
        c = self.cfg.llm
        client = AsyncOpenAI(base_url=c.base_url, api_key=api_key(c.api_key_env), timeout=60)
        extra: dict[str, Any] = dict(thinking_body(self.cfg, "voice"))
        if (slot := await self.background_slot()) is not None:
            extra["id_slot"] = slot
        kwargs: dict[str, Any] = {}
        if schema:
            kwargs["response_format"] = {"type": "json_schema",
                                         "json_schema": {"name": "answer", "schema": schema}}
        try:
            r = await client.chat.completions.create(
                model=c.model, messages=[{"role": "user", "content": prompt}], max_tokens=max_tokens,
                temperature=0.2, extra_body=extra or None, **kwargs)
        except Exception as e:
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

    def timers(self, ext_id: str) -> list[dict[str, Any]]:
        return read_json(self._file(ext_id, "timers.json"), [])

    def set_timers(self, ext_id: str, rows: list[dict[str, Any]]) -> None:
        write_json(self._file(ext_id, "timers.json"), rows)

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
        counts = read_json(path, {})
        counts = {today: counts.get(today, {})}
        used = counts[today].get(name, 0)
        if used >= per_day:
            return False
        counts[today][name] = used + 1
        write_json(path, counts)
        return True
