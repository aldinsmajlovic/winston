"""winstond's routes for extensions: what the Extensions page, the CLI and the channels' brains use
(/api/extensions..., /api/nudges...), what extensions call back into (/api/ext/v1/<op>, with their own
token), and their hooks (/hooks/<id>/<name>)."""
from __future__ import annotations

from collections import deque
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query, Request
from pydantic import BaseModel, Field

from winston.extension_host.host import Host, brain_name
from winston.extension_host.services import BadCall, Failed, Refused, Services


class ToolView(BaseModel):
    name: str
    brain_name: str
    description: str
    tier: str
    declared_tier: str
    outside: bool
    enabled: bool
    offer: bool


class KindView(BaseModel):
    kind: str
    title: str
    checked: bool
    deliver: Any
    where: Any
    declared_deliver: Any = None
    declared_where: Any = None
    requested: bool
    may_be_urgent: bool
    repeat: float | None = Field(None, description="Seconds after which a checked nudge is said again")
    triggers: list[str] = Field(default_factory=list, description="When its check runs, in words")
    enabled: bool
    muted_until: float | None = Field(None, description="Muted: 0 until switched back on, else when it ends")


class NudgeView(BaseModel):
    id: str
    extension: str
    kind: str
    title: str
    about: str
    level: int
    say: str | None = None
    facts: dict[str, Any] = Field(default_factory=dict)
    source: str = Field(description="checked or pushed")
    state: str = Field(description="waiting, held, said, dismissed or ended")
    why: str | None = Field(None, description="Why it waits or is held")
    via: str | None = Field(None, description="Where it was said: speakers, phone, discord, conversation, silent")
    said_at: str | None = None
    held_until: str | None = None
    put_off: str | None = Field(None, description="Put off until, as he was told")
    reason: str | None = None


class FollowUp(BaseModel):
    action: str = Field(description="later, dismiss, mute, why, or one of the extension's actions")
    when: str | None = None
    reason: str | None = None
    channel: str | None = Field(None, description="The conversation it comes from; a restricted action from one "
                                                  "waits for your confirmation")


class CheckResult(BaseModel):
    ok: bool
    nudges: list[dict[str, Any]] = Field(default_factory=list)
    error: str | None = None


class EntityValue(BaseModel):
    entry: str | None
    value: Any
    at: float


class EntityView(BaseModel):
    key: str
    kind: str
    name: str
    unit: str | None = None
    per: str | None = None
    values: list[EntityValue] = Field(default_factory=list)


class JobView(BaseModel):
    name: str
    trigger: dict[str, Any]
    next_run: float | None = None


class ExitView(BaseModel):
    at: float
    why: str


class ErrorView(BaseModel):
    at: float
    where: str
    error: str


class ExtensionView(BaseModel):
    id: str
    name: str
    description: str = ""
    icon: str = "puzzle"
    origin: str
    target: str
    enabled: bool
    state: str = Field(description="stopped, starting, running, restarting, failed; or problem when it cannot run")
    detail: str = ""
    problem: str | None = None
    api: int | None = None
    sdk: str | None = None
    permissions: dict[str, Any] = Field(default_factory=dict)
    secrets: dict[str, str] = Field(default_factory=dict, description="Its key fields and the variable each names")
    needs: list[str] = Field(default_factory=list, description="Variables it names that are not set in data/.env")
    status: str | None = None
    memory_mb: float = 0
    peak_mb: float = 0
    memory_limit_mb: int = 500
    started_at: float | None = None
    tools: list[ToolView] = Field(default_factory=list)
    kinds: list[KindView] = Field(default_factory=list)
    nudges: list[NudgeView] = Field(default_factory=list, description="Its nudges now and those ended lately")
    entities: list[EntityView] = Field(default_factory=list)
    jobs: list[JobView] = Field(default_factory=list)
    prompt_chars: int = 0
    errors: list[ErrorView] = Field(default_factory=list)
    exits: list[ExitView] = Field(default_factory=list, description="Why it stopped lately, newest last")


class BrainTool(BaseModel):
    name: str
    extension: str
    tool: str
    description: str
    parameters: dict[str, Any]
    tier: str
    outside: bool
    filler: str | None = None
    filler_after: float = 1
    title: str = Field("", description="Its extension's name, for the tool's line on Talk")
    icon: str | None = Field(None, description="Its extension's icon: a lucide name, or simple-icons:<slug> for a maker's logo")
    timeout: float = 10


class ToolResult(BaseModel):
    ok: bool
    result: Any = None
    error: str | None = None
    waiting: bool = Field(False, description="A restricted tool waiting for your confirmation")


class Confirmation(BaseModel):
    id: str
    extension: str
    tool: str
    args: dict[str, Any]
    label: str
    channel: str
    created: float
    expires: float
    state: str = Field(description="waiting, ok, error, declined or expired")
    outcome: str | None = None


class JobResult(BaseModel):
    ok: bool
    error: str | None = None


class LogLines(BaseModel):
    lines: list[str]


def view(host: Host, ext_id: str, status_text: str | None = None) -> ExtensionView:
    src = host.sources.get(ext_id)
    d = host.described.get(ext_id, {})
    m = d.get("manifest") or (src.manifest.model_dump() if src and src.manifest else {})
    ec = host.config(ext_id)
    proc = host.procs.get(ext_id)
    brain = {t["tool"]: t for t in host.brain_tools() if t["extension"] == ext_id}
    tools = []
    mcp = bool(src and src.origin == "mcp")
    # An MCP server's card lists every tool it has, so you can pick which the brain gets; only those
    # named in config are offered.
    for t in (host.mcp_tools.get(ext_id, []) if mcp else host._tools_of(ext_id) if src else []):
        choice = ec.tools.get(t["name"])
        on = (choice is not None and choice.enabled) if mcp else not (choice and not choice.enabled)
        tools.append(ToolView(name=t["name"], brain_name=brain.get(t["name"], {}).get("name", brain_name(ext_id, t["name"])),
                              description=t["description"],
                              tier=(choice.tier if choice and choice.tier else t["tier"]), declared_tier=t["tier"],
                              outside=t["outside"], enabled=on, offer=bool(choice and choice.offer)))
    kinds = []
    nudges = host.nudges
    for k in d.get("kinds", []):
        choice = ec.kinds.get(k["kind"])
        kinds.append(KindView(kind=k["kind"], title=k["title"], checked=k["checked"],
                              deliver=choice.deliver if choice and choice.deliver else k["deliver"],
                              where=choice.where if choice and choice.where else k["where"],
                              declared_deliver=k["deliver"], declared_where=k["where"],
                              requested=k["requested"], may_be_urgent=k["may_be_urgent"], repeat=k.get("repeat"),
                              triggers=nudges._trigger_words(k.get("triggers") or {}) if nudges else [],
                              enabled=not (choice and not choice.enabled),
                              muted_until=nudges.muted_until(ext_id, k["kind"]) if nudges else None))
    entities = []
    for e in d.get("entities", []):
        values = [EntityValue(entry=entry, value=v["value"], at=v["at"])
                  for (i, key, entry), v in host.entities.items() if i == ext_id and key == e["key"]]
        entities.append(EntityView(key=e["key"], kind=e["kind"], name=e["name"], unit=e.get("unit"),
                                   per=(e.get("per") or {}).get("from"), values=values))
    scheduler = getattr(host, "scheduler", None)
    jobs = [JobView(name=j["name"], trigger=j["trigger"],
                    next_run=scheduler.next_run(ext_id, j["name"]) if scheduler else None)
            for j in d.get("jobs", [])]
    problem = host.problems.get(ext_id)
    return ExtensionView(
        id=ext_id, name=m.get("name", ext_id), description=m.get("description", ""), icon=m.get("icon", "puzzle"),
        origin=src.origin if src else "unknown", target=src.target if src else "",
        enabled=ec.enabled, state="problem" if problem else (proc.state if proc else "stopped"),
        detail=proc.detail if proc else "", problem=problem, api=m.get("api"), sdk=d.get("sdk"),
        permissions=m.get("permissions", {}), secrets=host.secret_fields(ext_id), needs=host.missing_secrets(ext_id),
        status=status_text, memory_mb=round(proc.rss_mb, 1) if proc else 0,
        peak_mb=round(proc.peak_mb, 1) if proc else 0, memory_limit_mb=ec.memory_mb,
        started_at=proc.started_at if proc and proc.state == "running" else None,
        tools=tools, kinds=kinds, nudges=[NudgeView(**n) for n in nudges.listing(ext_id)] if nudges else [],
        entities=entities, jobs=jobs, prompt_chars=host.prompt_cost(ext_id),
        errors=[ErrorView(**e) for e in host.errors.get(ext_id, [])],
        exits=[ExitView(**e) for e in proc.exits] if proc else [])


def router(host: Host, services: Services) -> APIRouter:
    r = APIRouter()

    def known(ext_id: str):
        if ext_id not in host.sources and ext_id not in host.problems:
            raise HTTPException(404, f"no extension {ext_id}")

    @r.get("/api/extensions", response_model=list[ExtensionView], tags=["extensions"],
           summary="Every extension found: where it comes from, whether it runs, what it offers and may reach")
    async def list_extensions():
        texts = await host.status_texts()
        ids = sorted(set(host.sources) | {k for k in host.problems if "~" in k})
        return [view(host, i, texts.get(i)) for i in ids]

    @r.get("/api/extensions/tools", response_model=list[BrainTool], tags=["extensions"],
           summary="The tools the brain is offered, in a fixed order, the same for every channel")
    async def brain_tools():
        return host.brain_tools()

    @r.post("/api/extensions/refresh", response_model=list[ExtensionView], tags=["extensions"],
            summary="Look for extensions again and describe what changed (a new file in data/extensions/)")
    async def refresh():
        await host.reload()
        return await list_extensions()

    @r.get("/api/extensions/{ext_id}", response_model=ExtensionView, tags=["extensions"])
    async def one(ext_id: str):
        known(ext_id)
        return view(host, ext_id, (await host.status_texts()).get(ext_id))

    @r.post("/api/extensions/{ext_id}/tools/{tool}", response_model=ToolResult, tags=["extensions"],
            summary="Call a tool as the brain would; the answer or an error written for him to say")
    async def call_tool(ext_id: str, tool: str, args: dict[str, Any] = Body(default_factory=dict),
                        channel: str | None = Query(None, description="The conversation calling it; a restricted "
                                                                      "tool called from one waits for your "
                                                                      "confirmation")):
        known(ext_id)
        return await host.call_tool(ext_id, tool, args, channel=channel)

    @r.get("/api/confirmations", response_model=list[Confirmation], tags=["extensions"],
           summary="Restricted calls waiting for you, and ones answered in the last hour")
    async def confirmations():
        host.sweep()
        return [p.view() for p in host.pending.values()]

    @r.post("/api/confirmations/{cid}", response_model=Confirmation, tags=["extensions"],
            summary="Confirm (approve true) or decline a restricted call; confirmed, it runs now")
    async def answer(cid: str, approve: bool = Body(..., embed=True)):
        try:
            return (await host.confirm(cid, approve)).view()
        except KeyError:
            raise HTTPException(404, "no such confirmation (it may have expired)")

    @r.post("/api/extensions/{ext_id}/jobs/{job}", response_model=JobResult, tags=["extensions"],
            summary="Run one of its jobs now")
    async def run_job(ext_id: str, job: str, data: dict[str, Any] = Body(default_factory=dict)):
        known(ext_id)
        res = await host.run_job(ext_id, job, data)
        return JobResult(ok=bool(res.get("ok")), error=res.get("error"))

    @r.post("/api/extensions/{ext_id}/restart", response_model=ExtensionView, tags=["extensions"])
    async def restart(ext_id: str):
        known(ext_id)
        if not host.enabled(ext_id):
            raise HTTPException(409, f"{ext_id} is switched off")
        proc = host.procs.get(ext_id)
        if proc:
            await proc.restart("restarted by hand")
        else:
            host._start(ext_id)
        return view(host, ext_id)

    @r.get("/api/extensions/{ext_id}/log", response_model=LogLines, tags=["extensions"],
           summary="The end of its log: what it printed and every failure with its traceback")
    async def log(ext_id: str, lines: int = Query(200, ge=1, le=5000)):
        known(ext_id)
        path = host.log_path(ext_id)
        if not path.is_file():
            return LogLines(lines=[])
        with open(path, encoding="utf-8", errors="replace") as f:
            return LogLines(lines=[ln.rstrip("\n") for ln in deque(f, maxlen=lines)])

    # speaking up

    def speaking():
        if host.nudges is None:
            raise HTTPException(409, "speaking up is not running in this winstond")
        return host.nudges

    @r.get("/api/nudges", response_model=list[NudgeView], tags=["speaking up"],
           summary="Every nudge: where it stands and why it waits, newest first")
    async def list_nudges(extension: str | None = None):
        return [NudgeView(**n) for n in speaking().listing(extension)]

    @r.get("/api/nudges/kinds", tags=["speaking up"],
           summary="How many kinds of nudges the extensions switched on have (the brain gets follow_up when any)")
    async def nudge_kinds():
        # Switched on and described, running or not yet: a conversation keeps its tools, and one that
        # starts while its extensions are still starting would otherwise have no follow_up all along.
        n = sum(len(d.get("kinds", [])) for i, d in host.described.items() if host.enabled(i))
        return {"kinds": n if host.nudges is not None else 0}

    @r.post("/api/nudges/mentions", response_model=list[dict[str, Any]], tags=["speaking up"],
            summary="What to mention in the conversation going on now (next-talk nudges, the briefing); each once")
    async def mentions(text: str = Body("", embed=True)):
        if host.nudges is None:
            return []
        return await host.nudges.mentions(text)

    @r.post("/api/nudges/{nid}/follow_up", tags=["speaking up"],
            summary="The follow_up tool: later, dismiss, mute, why, or one of the extension's actions")
    async def follow_up(nid: str, f: FollowUp):
        return await speaking().follow_up(nid, f.action, when=f.when, reason=f.reason, channel=f.channel)

    @r.post("/api/extensions/{ext_id}/kinds/{kind}/check", response_model=CheckResult, tags=["speaking up"],
            summary="What its check would say now, without recording or saying anything")
    async def check_kind(ext_id: str, kind: str):
        known(ext_id)
        res = await speaking().ask_check(ext_id, kind)
        return CheckResult(ok=bool(res.get("ok")), nudges=res.get("nudges", []), error=res.get("error"))

    @r.post("/api/extensions/{ext_id}/kinds/{kind}/unmute", response_model=ExtensionView, tags=["speaking up"],
            summary="Switch a muted kind back on")
    async def unmute(ext_id: str, kind: str):
        known(ext_id)
        speaking().unmute(ext_id, kind)
        return view(host, ext_id)

    @r.post("/api/ext/v1/{op}", include_in_schema=False)
    async def ext_call(op: str, request: Request):
        auth = request.headers.get("authorization", "")
        ext_id = host.tokens.get(auth.removeprefix("Bearer ").strip())
        if ext_id is None:
            raise HTTPException(401, "not an extension's token")
        try:
            params = await request.json()
        except ValueError:
            params = {}
        try:
            return {"result": await services.handle(ext_id, op, params if isinstance(params, dict) else {})}
        except Refused as e:
            raise HTTPException(403, str(e))
        except Failed as e:
            raise HTTPException(502, str(e))
        except (BadCall, KeyError, ValueError, TypeError) as e:
            raise HTTPException(400, str(e))

    @r.post("/hooks/{ext_id}/{name}", tags=["extensions"],
            summary="A hook of an extension; reachable wherever winstond is, never opened to the internet by Winston")
    async def hook(ext_id: str, name: str, request: Request):
        known(ext_id)
        try:
            body = await request.json()
        except ValueError:
            body = (await request.body()).decode(errors="replace")
        headers = {k: v for k, v in request.headers.items() if k.lower() not in ("authorization", "cookie")}
        res = await host.hook(ext_id, name, body, headers)
        if not res.get("ok"):
            raise HTTPException(502, res.get("error") or "the hook failed")
        return res.get("result")

    return r
