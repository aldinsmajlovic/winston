"""The extension host inside winstond: finds, describes, starts and stops extensions, applies config
saves to them, checks their settings before a save, hands each the values of its own secret fields
and no others, builds the brain's tool list and routes tool calls.

Tools are offered in a fixed order (extension id, then declaration) and the same list reaches every
channel, so the prompt cache holds. A stopped extension keeps its tools listed; they answer that it
is not running. Nudges, the scheduler and the services extensions call back into come in their own
modules; this one owns the processes and the tool path, and tells nudges when a handler ran (a check
may now have something else to say)."""
from __future__ import annotations

import asyncio
import copy
import json
import re
import secrets as pysecrets
import time
from collections.abc import Awaitable, Callable
from typing import Any

import jsonschema
from loguru import logger
from winston_sdk.context import local_zone

from winston.api.secrets import value as secret_value
from winston.config import resolve
from winston.config.schema import ExtensionConfig, WinstonConfig
from winston.extension_host import texts
from winston.extension_host.process import ExtensionProcess
from winston.extension_host.sources import Source, describe, discover

TOOL_NAME = re.compile(r"[^a-zA-Z0-9_-]")
RESULT_MAX = 4000
CONFIRM_SECS = 300
ERROR_PREFIX = re.compile(r"^Error executing tool [^:]+: ")


def brain_name(ext_id: str, tool: str) -> str:
    return TOOL_NAME.sub("_", f"{ext_id}_{tool}")[:64]


def mcp_tier(annotations: Any) -> str:
    """MCP's defaults: a tool is not read-only and is destructive unless it says otherwise."""
    if annotations is None:
        return "restricted"
    if annotations.read_only_hint:
        return "read"
    return "act" if annotations.destructive_hint is False else "restricted"


class Pending:
    """A restricted call waiting for you to confirm it outside the voice."""

    def __init__(self, ext_id: str, tool: str, args: dict[str, Any], label: str, channel: str,
                 nudge: str | None = None):
        self.id = pysecrets.token_hex(6)
        self.ext_id, self.tool, self.args, self.label, self.channel = ext_id, tool, args, label, channel
        self.nudge = nudge                              # an action on this nudge rather than a tool
        self.created = time.time()
        self.state = "waiting"
        self.outcome: str | None = None

    def view(self) -> dict[str, Any]:
        return {"id": self.id, "extension": self.ext_id, "tool": self.tool, "args": self.args, "label": self.label,
                "channel": self.channel, "created": self.created, "expires": self.created + CONFIRM_SECS,
                "state": self.state, "outcome": self.outcome}


class Host:
    def __init__(self, cfg: WinstonConfig, api: str, on_change: Callable[[], Awaitable[None]] | None = None):
        self.cfg = cfg
        self.api = api
        self.on_change = on_change
        self.sources: dict[str, Source] = {}
        self.described: dict[str, dict[str, Any]] = {}
        self.problems: dict[str, str] = {}              # found but not usable: why
        self.procs: dict[str, ExtensionProcess] = {}
        self.mcp_tools: dict[str, list[dict[str, Any]]] = {}
        self.tokens: dict[str, str] = {}                # token -> extension id
        self.entities: dict[tuple[str, str, str | None], dict[str, Any]] = {}
        self.errors: dict[str, list[dict[str, Any]]] = {}
        self.pending: dict[str, Pending] = {}
        self.on_feed: Callable[[dict[str, Any]], None] | None = None    # the live feed, set by winstond
        self.scheduler = None                                           # set by winstond
        self.nudges = None                                              # set by winstond (nudges.Nudges)
        self.on_pending: Callable[[Pending], None] | None = None        # set by winstond: to your phone too
        self.speakers = None                                            # set by winstond: play(ext, url, title), stop(ext)
        self._lock = asyncio.Lock()
        # One refresh at a time: two at once (winstond's own at start and another) let the second see
        # nothing new and start an extension whose description the first had not finished, so it got
        # its settings without its keys.
        self._refresh_lock = asyncio.Lock()

    # config

    def config(self, ext_id: str) -> ExtensionConfig:
        return self.cfg.extensions.get(ext_id) or ExtensionConfig()

    def enabled(self, ext_id: str) -> bool:
        return ext_id in self.sources and self.config(ext_id).enabled and ext_id not in self.problems

    def log_path(self, ext_id: str):
        return resolve(self.cfg.logging.tool_calls).parent / "extensions" / f"{ext_id}.log"

    def secret_fields(self, ext_id: str) -> dict[str, str]:
        """Field -> the variable it names, as saved (or its default)."""
        d = self.described.get(ext_id)
        if not d:
            return {}
        saved = self.config(ext_id).settings
        return {f: str(saved.get(f) or var) for f, var in d["settings"]["secrets"].items()}

    def secret_values(self, ext_id: str) -> dict[str, str]:
        """The keys, from the environment or data/.env as it is now: one added by hand takes on the
        extension's next start."""
        return {f: secret_value(var) for f, var in self.secret_fields(ext_id).items()}

    def missing_secrets(self, ext_id: str) -> list[str]:
        return [var for f, var in self.secret_fields(ext_id).items() if not secret_value(var)]

    # finding and describing

    async def refresh(self) -> set[str]:
        """Finds extensions again and describes what changed; returns the ids whose code changed."""
        async with self._refresh_lock:
            return await self._refresh()

    async def _refresh(self) -> set[str]:
        found = discover(self.cfg)
        changed = {i for i, s in found.items() if i not in self.sources or self.sources[i].stamp != s.stamp}
        gone = set(self.sources) - set(found)
        self.sources = {s.id: s for s in found.values() if "~" not in s.id or s.id not in found}
        self.problems = {s.id: s.error for s in found.values() if s.error}
        for ext_id in gone:
            self.described.pop(ext_id, None)
            if ext_id in self.procs:
                await self.procs.pop(ext_id).stop()
        sem = asyncio.Semaphore(4)

        async def one(src: Source):
            async with sem:
                try:
                    self.described[src.id] = await describe(src)
                    self.problems.pop(src.id, None)
                except Exception as e:                                  # noqa: BLE001  (its fault, shown on its card)
                    self.problems[src.id] = f"Could not describe it: {e}"
                    self.described.pop(src.id, None)

        await asyncio.gather(*(one(self.sources[i]) for i in changed
                               if i in self.sources and not self.sources[i].error and self.sources[i].origin != "mcp"))
        for i in changed:
            if i in self.sources and self.sources[i].origin == "mcp":
                self.described[i] = {"manifest": {"id": i, "name": i, "description": "An MCP server, tools only.",
                                                  "icon": "plug", "api": None, "permissions": {}},
                                     "tools": [], "kinds": [], "entities": [], "jobs": [], "actions": [],
                                     "settings": {"schema": {"type": "object", "properties": {}}, "secrets": {}},
                                     "has": {}}
        return changed

    async def start(self) -> None:
        await self.refresh()
        for ext_id in sorted(self.sources):
            if self.enabled(ext_id):
                self._start(ext_id)
        self._watcher = asyncio.create_task(self._watch_files(), name="extension files")

    async def reload(self) -> set[str]:
        """Finds extensions again; one whose code changed is described again and restarted. It counts
        as restarting from the moment its change is seen, so nothing calls the old code's process for
        a tool only the new code has."""
        for ext_id, src in discover(self.cfg).items():
            proc = self.procs.get(ext_id)
            if proc and proc.state == "running" and ext_id in self.sources and self.sources[ext_id].stamp != src.stamp:
                proc.state, proc.detail = "restarting", "its code changed"
        changed = await self.refresh()
        for ext_id in changed:
            proc = self.procs.get(ext_id)
            if proc and proc.state != "stopped":
                await proc.restart("its code changed")
            elif self.enabled(ext_id):
                self._start(ext_id)
        return changed

    async def _watch_files(self, every: float = 2.0) -> None:
        """A personal extension reloads when its file is saved (or a new one appears)."""
        while True:
            await asyncio.sleep(every)
            try:
                found = discover(self.cfg)
                now = {i: s.stamp for i, s in found.items() if "~" not in i}
                if now != {i: s.stamp for i, s in self.sources.items()}:
                    async with self._lock:
                        changed = await self.reload()
                    if changed:
                        logger.info(f"extensions changed on disk: {', '.join(sorted(changed))}")
            except Exception as e:                                      # noqa: BLE001  (keep watching)
                logger.warning(f"watching extension files: {e}")

    def _start(self, ext_id: str) -> None:
        if ext_id in self.procs:
            self.procs[ext_id].start()
            return
        src = self.sources[ext_id]
        token = pysecrets.token_urlsafe(24)
        self.tokens[token] = ext_id
        ec = self.config(ext_id)
        extra_env = {}
        if src.origin == "mcp" and ec.mcp is not None:
            extra_env = {v: secret_value(v) for v in ec.mcp.env}
        proc = ExtensionProcess(src, api=self.api, token=token, log_path=self.log_path(ext_id),
                                memory_mb=lambda i=ext_id: self.config(i).memory_mb, on_ready=self._ready,
                                extra_env=extra_env, mcp_command=ec.mcp.command if ec.mcp else None,
                                mcp_url=ec.mcp.url if ec.mcp else None, mcp_token=lambda i=ext_id: self.mcp_token(i))
        self.procs[ext_id] = proc
        proc.start()

    def mcp_token(self, ext_id: str) -> str:
        """An HTTP MCP server's token as data/.env holds it now, read at each connect: a key saved after
        the config that names it reaches the next try without a restart."""
        mcp = self.config(ext_id).mcp
        return secret_value(mcp.token_env) if mcp and mcp.token_env else ""

    async def _ready(self, proc: ExtensionProcess) -> None:
        """Just started: MCP servers list their tools, SDK extensions get their settings and run their
        start jobs."""
        if not proc.is_mcp_server and proc.id not in self.described:
            raise RuntimeError("it has not been described yet, so its keys are not known")
        if proc.is_mcp_server:
            self.mcp_tools[proc.id] = [
                {"name": t.name, "description": t.description or "", "tier": mcp_tier(t.annotations),
                 "outside": t.annotations.open_world_hint is not False if t.annotations else True,
                 "timeout": 30, "filler": None, "input_schema": t.input_schema}
                for t in await proc.list_tools()]
            # The server's own instructions (MCP's word for what it is and offers) are its prompt line, as
            # an extension's @ext.context is: so the brain knows what it reaches, with nothing in the core.
            texts.write(proc.id, (proc.client.instructions or "").strip() or None, None)
            return
        result = await proc.call("configure", settings=self.config(proc.id).settings,
                                 secrets=self.secret_values(proc.id), time_zone=local_zone().key)
        if not result.get("ok"):
            problems = "; ".join(f"{e.get('field') or 'settings'}: {e.get('message')}" for e in result.get("errors", []))
            raise RuntimeError(f"its settings were refused: {problems}")
        for job in self.described.get(proc.id, {}).get("jobs", []):
            if job["trigger"]["kind"] == "on_start":
                await self.run_job(proc.id, job["name"])
        await self.refresh_texts(proc.id)
        self.timers_changed()
        self.handled(proc.id)                           # its nudges as they stand now

    async def refresh_texts(self, ext_id: str) -> None:
        """Its prompt lines and hint words, as files the channels read when a conversation starts."""
        proc = self.procs.get(ext_id)
        has = self.described.get(ext_id, {}).get("has", {})
        if proc is None or proc.client is None or not (has.get("context") or has.get("hint_words")):
            return
        try:
            context = (await proc.call("context", timeout=5)).get("text") if has.get("context") else None
            words = (await proc.call("hint_words", timeout=5)).get("words") if has.get("hint_words") else None
            texts.write(ext_id, context, words)
        except Exception as e:                                          # noqa: BLE001
            self._note(ext_id, "context", str(e))

    def handled(self, ext_id: str) -> None:
        """A handler of the extension ran: its checks run, since what there is to say may have changed."""
        if self.nudges is not None:
            self.nudges.after_handler(ext_id)

    def timers_changed(self) -> None:
        """Something the scheduler should look at now: a new timer, an extension just started."""
        if self.scheduler is not None:
            self.scheduler.wake()

    async def stop_all(self) -> None:
        if getattr(self, "_watcher", None):
            self._watcher.cancel()
        await asyncio.gather(*(p.stop() for p in self.procs.values()), return_exceptions=True)

    # config saves

    async def validate(self, new: WinstonConfig) -> list[dict[str, Any]]:
        """Field errors for extension settings in `new`, located from the config root, before it is
        saved. A running extension checks its own (its @ext.validate included); for one that is not
        running, its described schema does."""
        errors: list[dict[str, Any]] = []
        for ext_id, ec in new.extensions.items():
            if ext_id not in self.described or ec.settings == self.config(ext_id).settings and \
                    ec.enabled == self.config(ext_id).enabled:
                continue
            loc = ["extensions", ext_id, "settings"]
            proc = self.procs.get(ext_id)
            if proc and proc.state == "running" and not proc.is_mcp_server:
                secrets_now = {f: secret_value(str(ec.settings.get(f) or var))
                               for f, var in self.described[ext_id]["settings"]["secrets"].items()}
                try:
                    r = await proc.call("configure", timeout=15, settings=ec.settings, secrets=secrets_now,
                                        validate_only=True)
                except Exception as e:                                  # noqa: BLE001
                    errors.append({"loc": loc, "msg": f"{ext_id} could not check its settings: {e}", "type": "value_error"})
                    continue
                for e in r.get("errors", []):
                    field = [p for p in str(e.get("field") or "").split(".") if p]
                    errors.append({"loc": loc + field, "msg": e.get("message", ""), "type": "value_error"})
            else:
                schema = self.described[ext_id]["settings"]["schema"]
                for e in jsonschema.Draft202012Validator(schema).iter_errors(ec.settings):
                    errors.append({"loc": loc + [str(p) for p in e.absolute_path], "msg": e.message,
                                   "type": "value_error"})
        return errors

    async def apply(self, old: WinstonConfig, new: WinstonConfig) -> None:
        """A saved config: start what was switched on, stop what was switched off, hand new settings
        to the ones that run, restart an MCP server whose command changed."""
        async with self._lock:
            self.cfg = new
            if {i for i, e in new.extensions.items() if e.mcp} != {i for i, e in old.extensions.items() if e.mcp} or \
                    any((old.extensions.get(i) or ExtensionConfig()).mcp != e.mcp for i, e in new.extensions.items()):
                await self.refresh()
            for ext_id in sorted(self.sources):
                was = (old.extensions.get(ext_id) or ExtensionConfig())
                now = self.config(ext_id)
                proc = self.procs.get(ext_id)
                if not self.enabled(ext_id):
                    if proc and proc.state != "stopped":
                        await proc.stop()
                    texts.clear(ext_id)
                    continue
                if not proc or proc.state in ("stopped", "failed"):
                    self._start(ext_id)
                    continue
                if proc.is_mcp_server and was.mcp != now.mcp:
                    await proc.restart("its command changed")
                elif not proc.is_mcp_server and was.settings != now.settings and proc.state == "running":
                    await self.reconfigure(ext_id)

    async def reconfigure(self, ext_id: str) -> None:
        proc = self.procs.get(ext_id)
        if not proc or proc.state != "running" or proc.is_mcp_server:
            return
        try:
            r = await proc.call("configure", settings=self.config(ext_id).settings, secrets=self.secret_values(ext_id))
            if not r.get("ok"):
                self._note(ext_id, "settings", "; ".join(e.get("message", "") for e in r.get("errors", [])))
        except Exception as e:                                          # noqa: BLE001
            self._note(ext_id, "settings", str(e))

    async def secret_changed(self, name: str) -> None:
        for ext_id in list(self.procs):
            if name in self.secret_fields(ext_id).values():
                await self.reconfigure(ext_id)
            ec = self.config(ext_id)
            if ec.mcp and name in ec.mcp.env:
                self.procs[ext_id].extra_env[name] = secret_value(name)
            if ec.mcp and name in (*ec.mcp.env, ec.mcp.token_env):
                await self.procs[ext_id].restart("a key it uses changed")

    def graft(self, schema: dict[str, Any]) -> dict[str, Any]:
        """The config schema with each described extension's settings under extensions.<id>.settings,
        so the settings form, key fields and field errors work for extensions as for the rest."""
        out = copy.deepcopy(schema)
        defs = out.setdefault("$defs", {})
        base = defs.get("ExtensionConfig")
        if base is None:
            return out
        props = {}
        for ext_id, d in sorted(self.described.items()):
            settings = copy.deepcopy(d["settings"]["schema"])
            prefix = f"Ext_{ext_id}_"
            inner = settings.pop("$defs", {})
            text = json.dumps(settings)
            for name in inner:
                text = text.replace(f'"#/$defs/{name}"', f'"#/$defs/{prefix}{name}"')
            settings = json.loads(text)
            for name, body in inner.items():
                body_text = json.dumps(body)
                for other in inner:
                    body_text = body_text.replace(f'"#/$defs/{other}"', f'"#/$defs/{prefix}{other}"')
                defs[prefix + name] = json.loads(body_text)
            entry = copy.deepcopy(base)
            entry["properties"]["settings"] = {**settings, "title": "Settings"}
            entry["title"] = d["manifest"]["name"]
            defs[f"Ext_{ext_id}"] = entry
            props[ext_id] = {"$ref": f"#/$defs/Ext_{ext_id}"}
        out["properties"]["extensions"]["properties"] = props
        return out

    # tools

    def _tools_of(self, ext_id: str) -> list[dict[str, Any]]:
        if self.sources.get(ext_id) and self.sources[ext_id].origin == "mcp":
            allow = self.config(ext_id).tools
            return [t for t in self.mcp_tools.get(ext_id, []) if t["name"] in allow]
        return self.described.get(ext_id, {}).get("tools", [])

    def _resolve_parameters(self, ext_id: str, schema: dict[str, Any]) -> dict[str, Any]:
        """`Choices(From("chores"))` becomes the names in that setting; the markers are dropped."""
        out = copy.deepcopy(schema)
        settings = self.config(ext_id).settings
        for prop in out.get("properties", {}).values():
            for node in (prop, *prop.get("anyOf", [])):
                choices = node.pop("x-winston-choices", None)
                if choices and isinstance(settings.get(choices.get("from")), dict):
                    names = sorted(settings[choices["from"]])
                    if names:
                        node["enum"] = names
            prop.pop("x-winston-when", None)
        return out

    def brain_tools(self) -> list[dict[str, Any]]:
        """What the brain is offered, identical in every channel."""
        out = []
        for ext_id in sorted(self.sources):
            if not self.enabled(ext_id):
                continue
            ec = self.config(ext_id)
            for t in self._tools_of(ext_id):
                choice = ec.tools.get(t["name"])
                if choice is not None and not choice.enabled:
                    continue
                manifest = self.described.get(ext_id, {}).get("manifest", {})
                out.append({"name": brain_name(ext_id, t["name"]), "extension": ext_id, "tool": t["name"],
                            "title": manifest.get("name") or ext_id, "icon": manifest.get("icon"),
                            "description": t["description"],
                            "parameters": self._resolve_parameters(ext_id, t["input_schema"]),
                            "tier": (choice.tier if choice and choice.tier else t["tier"]),
                            "outside": t["outside"], "filler": t.get("filler"), "filler_after": t.get("filler_after", 1),
                            "timeout": t.get("timeout", 10)})
        return out

    def offered_tools(self) -> list[dict[str, Any]]:
        """The tools marked "Offer to other apps", for winstond's /mcp."""
        return [t for t in self.brain_tools()
                if (choice := self.config(t["extension"]).tools.get(t["tool"])) is not None and choice.offer]

    def find_tool(self, name: str) -> dict[str, Any] | None:
        return next((t for t in self.brain_tools() if t["name"] == name), None)

    def tier(self, ext_id: str, tool: str) -> str | None:
        spec = next((t for t in self._tools_of(ext_id) if t["name"] == tool), None)
        if spec is None:
            return None
        choice = self.config(ext_id).tools.get(tool)
        return choice.tier if choice and choice.tier else spec["tier"]

    async def call_tool(self, ext_id: str, tool: str, args: dict[str, Any], channel: str | None = None,
                        confirmed: bool = False) -> dict[str, Any]:
        """{ok, result} or {ok: false, error}; the error is written for the brain to say. A restricted
        tool called from a conversation (`channel`) does nothing yet: it waits for you to confirm it
        outside the voice (plan §6 rule 4)."""
        name = self.described.get(ext_id, {}).get("manifest", {}).get("name", ext_id)
        spec = next((t for t in self._tools_of(ext_id) if t["name"] == tool), None)
        if spec is None:
            return {"ok": False, "error": f"{name} has no tool {tool}."}
        if channel is not None and not confirmed and self.tier(ext_id, tool) == "restricted":
            p = Pending(ext_id, tool, args, f"{name}: {spec['description'].rstrip('.')}", channel)
            self._add_pending(p)
            return {"ok": False, "waiting": True,
                    "error": f"{p.label} needs your confirmation first, on the Talk page, within five minutes. "
                             "Nothing has happened yet."}
        proc = self.procs.get(ext_id)
        if not self.enabled(ext_id) or proc is None or proc.state != "running":
            why = f" ({proc.detail})" if proc and proc.detail else ""
            return {"ok": False, "error": f"{name} is not running right now{why}."}
        timeout = float(spec.get("timeout") or 10)
        t0 = time.perf_counter()
        try:
            r = await proc.call_tool(tool, args, timeout + 2)
        except asyncio.TimeoutError:
            return self._fail(ext_id, tool, f"{name} did not answer within {timeout:g} s.")
        except Exception as e:                                          # noqa: BLE001
            return self._fail(ext_id, tool, f"{name} failed: {e}")
        text = "\n".join(getattr(c, "text", "") for c in r.content if getattr(c, "type", "") == "text")
        if r.is_error:
            return self._fail(ext_id, tool, ERROR_PREFIX.sub("", text) or f"{name} failed.")
        # The text is what every server sends (the Winston SDK sends one JSON text); structured content
        # is used only without it, since SDKs wrap plain values differently there ({"result": ...}).
        if text:
            try:
                result: Any = json.loads(text)
            except ValueError:
                result = text
        else:
            result = r.structured_content
        logger.info(f"extension {ext_id}: {tool} in {(time.perf_counter() - t0) * 1000:.0f} ms")
        if not proc.is_mcp_server:
            self.handled(ext_id)
        whole = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str)
        if len(whole) > RESULT_MAX:                     # a result is information for one turn, not a document
            result = whole[:RESULT_MAX] + f" [cut: {len(whole) - RESULT_MAX} more characters]"
        return {"ok": True, "result": result}

    def _add_pending(self, p: Pending) -> None:
        self.pending[p.id] = p
        self._feed_confirm(p)
        if self.on_pending is not None:
            self.on_pending(p)

    def _feed_confirm(self, p: Pending) -> None:
        if self.on_feed:
            self.on_feed({"channel": p.channel, "type": "confirm", "call": p.id, "name": brain_name(p.ext_id, p.tool),
                          "label": p.label, "status": p.state, "text": p.outcome})

    def sweep(self) -> None:
        """Confirmations past their five minutes expire; answered ones leave after an hour."""
        now = time.time()
        for p in list(self.pending.values()):
            if p.state == "waiting" and now - p.created > CONFIRM_SECS:
                p.state, p.outcome = "expired", "Not confirmed in time; nothing happened."
                self._feed_confirm(p)
            if p.state != "waiting" and now - p.created > 3600:
                self.pending.pop(p.id, None)

    async def confirm(self, cid: str, approve: bool) -> Pending:
        self.sweep()
        p = self.pending.get(cid)
        if p is None:
            raise KeyError(cid)
        if p.state != "waiting":
            return p
        if not approve:
            p.state, p.outcome = "declined", "Declined; nothing happened."
        else:
            if p.nudge is not None:
                r = await self._confirmed_action(p)
            else:
                r = await self.call_tool(p.ext_id, p.tool, p.args, channel=None, confirmed=True)
            p.state = "ok" if r["ok"] else "error"
            result = r.get("result") if r["ok"] else r.get("error")
            p.outcome = result if isinstance(result, str) else json.dumps(result, default=str)[:300]
        self._feed_confirm(p)
        return p

    def pend_action(self, rec: dict[str, Any], act: dict[str, Any], channel: str) -> dict[str, Any]:
        """A restricted nudge action asked for in a conversation: it waits for you, as a restricted tool does."""
        name = self.described.get(rec["ext"], {}).get("manifest", {}).get("name", rec["ext"])
        p = Pending(rec["ext"], act["name"], {}, f"{name}: {act['title']}", channel, nudge=rec["id"])
        self._add_pending(p)
        return {"waiting": True, "note": f"{p.label} needs your confirmation first, on the Talk page, within five "
                                         "minutes. Nothing has happened yet."}

    async def _confirmed_action(self, p: Pending) -> dict[str, Any]:
        rec = self.nudges.records.get(p.nudge) if self.nudges else None
        if rec is None:
            return {"ok": False, "error": "That nudge is gone."}
        spec = self.nudges.kind(rec["ext"], rec["kind"]) or {"title": rec["kind"]}
        r = await self.nudges.act(rec, spec, p.tool, channel=None, confirmed=True)
        return {"ok": "error" not in r, "result": r.get("result") or r.get("done"), "error": r.get("error")}

    def _fail(self, ext_id: str, where: str, message: str) -> dict[str, Any]:
        self._note(ext_id, where, message)
        return {"ok": False, "error": message}

    def _note(self, ext_id: str, where: str, message: str) -> None:
        self.errors[ext_id] = [*self.errors.get(ext_id, [])[-9:],
                               {"at": time.time(), "where": where, "error": message}]

    # jobs (the scheduler calls these; so do the CLI and the API)

    async def run_job(self, ext_id: str, job: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        proc = self.procs.get(ext_id)
        if proc is None or proc.client is None:
            return {"ok": False, "error": f"{ext_id} is not running"}
        spec = next((j for j in self.described.get(ext_id, {}).get("jobs", []) if j["name"] == job), None)
        timeout = (spec or {}).get("timeout") or 120
        try:
            r = await proc.call("run", timeout=timeout + 5, job=job, data=data or {})
        except asyncio.TimeoutError:
            r = {"ok": False, "error": f"{job} did not finish within {timeout:g} s"}
        except Exception as e:                                          # noqa: BLE001
            r = {"ok": False, "error": str(e)}
        if not r.get("ok"):
            self._note(ext_id, job, r.get("error", ""))
        else:
            await self.refresh_texts(ext_id)
            self.handled(ext_id)
        return r

    async def hook(self, ext_id: str, name: str, body: Any, headers: dict[str, str]) -> dict[str, Any]:
        proc = self.procs.get(ext_id)
        if proc is None or proc.state != "running":
            return {"ok": False, "error": f"{ext_id} is not running"}
        r = await proc.call("hook", timeout=35, name=name, body=body, headers=headers)
        if r.get("ok"):
            self.handled(ext_id)
        return r

    # what the card shows

    async def status_texts(self) -> dict[str, str | None]:
        async def one(ext_id: str, proc: ExtensionProcess):
            if proc.state != "running" or proc.is_mcp_server or not self.described.get(ext_id, {}).get("has", {}).get("status"):
                return ext_id, None
            try:
                return ext_id, (await proc.call("status", timeout=2)).get("text")
            except Exception:                                           # noqa: BLE001
                return ext_id, None
        pairs = await asyncio.gather(*(one(i, p) for i, p in self.procs.items()))
        return dict(pairs)

    def prompt_cost(self, ext_id: str) -> int:
        """Characters its tools and its prompt lines add to every conversation's prompt."""
        if not self.enabled(ext_id):
            return 0
        tools = sum(len(json.dumps({"name": t["name"], "description": t["description"],
                                    "parameters": t["parameters"]}))
                    for t in self.brain_tools() if t["extension"] == ext_id)
        context = dict(texts.contexts(self.cfg)).get(ext_id, "")
        return tools + (len(context) + len(ext_id) + 4 if context else 0)

    def set_entity(self, ext_id: str, key: str, entry: str | None, value: Any, attributes: dict[str, Any]) -> None:
        self.entities[(ext_id, key, entry)] = {"value": value, "attributes": attributes, "at": time.time()}
