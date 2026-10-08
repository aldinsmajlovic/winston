"""The winston command. Everything here goes through config and the control API, nothing
touches processes directly."""
import asyncio
import os
import shutil
import subprocess
import sys
from pathlib import Path

import typer
from loguru import logger

from winston import __version__
from winston.cli.ext import _call, _ok, ext_app
from winston.config import data_dir, load, repo_root

app = typer.Typer(help="Winston, a self-hosted voice assistant.", no_args_is_help=True, add_completion=False)
config_app = typer.Typer(help="Inspect the configuration.")
ha_app = typer.Typer(help="Home Assistant integration.")
api_app = typer.Typer(help="The control API.")
app.add_typer(config_app, name="config")
app.add_typer(ha_app, name="ha")
app.add_typer(api_app, name="api")
app.add_typer(ext_app, name="ext")


def _quiet():
    logger.remove(); logger.add(sys.stderr, level="WARNING")


@app.command()
def version():
    print(__version__)


@app.command()
def init(force: bool = typer.Option(False, help="Overwrite existing files."),
         profile: str = typer.Option("nvidia-24gb", help="Hardware profile for the config; see --list."),
         list_profiles: bool = typer.Option(False, "--list", help="Show the profiles and exit.")):
    """Create the data directory from the examples (config for your hardware, persona, bundled voices)."""
    from winston.config.presets import PROFILES, apply_profile, profile as get_profile
    if list_profiles:
        for p in PROFILES:
            print(f"{p['id']:<16} {p['title']}: {p['hint']}")
        return
    get_profile(profile)                           # fail early on a typo
    d = data_dir()
    d.mkdir(parents=True, exist_ok=True)
    ex = repo_root() / "examples"
    for name in ("config.yaml", "persona.md", ".env"):
        src, dst = ex / (name if name != ".env" else "env.example"), d / name
        if dst.exists() and not force:
            print(f"keep   {dst}")
        elif name == "config.yaml" and profile != "nvidia-24gb":
            import yaml
            from winston.config.schema import WinstonConfig
            cfg = WinstonConfig.model_validate(apply_profile(yaml.safe_load(src.read_text()), profile))
            dst.write_text(yaml.safe_dump(cfg.model_dump(mode="json", exclude_none=True), sort_keys=False,
                                          allow_unicode=True))
            print(f"create {dst} (profile {profile})")
        else:
            shutil.copy(src, dst); print(f"create {dst}")
    for sub in ("memory", "logs", "voices"):
        (d / sub).mkdir(exist_ok=True)
    for tmpl in (ex / "memory").glob("*.md"):
        if not (d / "memory" / tmpl.name).exists():
            shutil.copy(tmpl, d / "memory" / tmpl.name); print(f"create {d / 'memory' / tmpl.name}")
    bundled = repo_root() / "assets/voices/vctk"
    if bundled.is_dir() and not (d / "voices/vctk").exists():
        shutil.copytree(bundled, d / "voices/vctk"); print(f"create {d / 'voices/vctk'} (CC BY 4.0 VCTK samples)")
    print(f"\ndata directory: {d}\nnext: edit {d / 'config.yaml'} and {d / '.env'}, then `winston up`")


@app.command()
def up(host: str = typer.Option(None), port: int = typer.Option(None),
       no_start: bool = typer.Option(False, "--no-start", help="Run the control service without starting engines.")):
    """Run winstond: start the engines and the channels, serve the control API. Ctrl-C stops everything."""
    import uvicorn
    from winston.api.app import create_app
    cfg = load()
    application = create_app(cfg, autostart=not no_start)
    uvicorn.run(application, host=host or cfg.control.host, port=port or cfg.control.port, log_level="warning",
                timeout_graceful_shutdown=5)   # open SSE streams (status, live) would otherwise hold shutdown forever


@app.command()
def status():
    """Engines, channel and GPU, from the running winstond."""
    s = _ok(_call("GET", "/api/status", timeout=10))
    icons = {"healthy": "●", "starting": "◐", "unhealthy": "◑", "stopped": "○", "error": "✕"}
    for e in s["engines"]:
        port = f":{e['port']}" if e["port"] else ""
        if e["state"] == "error":           # the reason is the point: on its own line, never cut
            print(f"  {icons['error']} {e['name']:<8} {e['state']:<10} {port}\n      {e['detail'] or 'no detail'}")
        else:
            print(f"  {icons.get(e['state'], '?')} {e['name']:<8} {e['state']:<10} {port:<6}  {e['detail']}")
    if g := s.get("gpu"):
        print(f"  ▮ gpu      {g['used_mb']} / {g['total_mb']} MB  {g['name']}")
    for x in s.get("extensions", []):
        icon = {"running": "●", "starting": "◐", "restarting": "◑", "failed": "✕"}.get(x["state"], "○")
        print(f"  {icon} {x['id']:<8} {x['state']:<10} extension  {x['detail']}")


@app.command()
def restart(name: str):
    """Restart one engine or channel."""
    print(_ok(_call("POST", f"/api/engines/{name}/restart")))


@app.command()
def down():
    """Stop every engine and channel (winstond keeps serving)."""
    print(_ok(_call("POST", "/api/engines/stop_all", timeout=120)))


@app.command()
def talk(message: list[str] = typer.Argument(None, help="Messages to send; none means interactive.")):
    """Chat with the assistant in text, same brain and tools as voice."""
    from winston.channels.text import run
    _quiet()
    asyncio.run(run(load(), message or None))


@app.command()
def announce(text: str = typer.Argument(..., help="What the assistant says."),
             context: list[str] = typer.Option(None, "--context", "-c",
                                               help="key=value details kept for follow-up questions; repeatable.")):
    """Make the assistant say something on its own: chime and speech on connected speakers, Discord per config."""
    ctx = dict(kv.split("=", 1) for kv in context or [] if "=" in kv)
    d = _ok(_call("POST", "/api/announce", json={"text": text, "context": ctx, "source": "cli"}, timeout=10))
    where = [f"{d['spoken_on']} speaker(s)"] if d["spoken_on"] else []
    where += ["Discord"] if d["discord"] else []
    print(f"announced on {', '.join(where)}" if where else
          "delivered nowhere: no speaker connected" + (" (quiet hours)" if d["quiet"] else "") + " and Discord off")


@app.command()
def memory():
    """Show what the assistant remembers, as it goes into the prompt. Edit the files to change it."""
    from winston.core.memory import prompt_block, vault
    cfg = load()
    print(prompt_block(cfg) or "Nothing remembered yet.")
    print(f"\n({vault(cfg)})")


turns_app = typer.Typer(help="The turn record: each spoken turn with its audio (logging.turns), and its marks.",
                        invoke_without_command=True, no_args_is_help=False)
app.add_typer(turns_app, name="turns")


def _turn_line(t: dict) -> str:
    from datetime import datetime
    tools = ", ".join(c["name"].split("__")[-1] for c in t["tools"])
    mark = f"  [{t['mark']['kind']}]" if t.get("mark") else ""
    return (f"{datetime.fromtimestamp(t['ts']):%H:%M:%S}  {t['id']}  {t['channel']:<9} {t['start']:<9} {t['outcome']:<13} "
            f"{t['heard']!r}" + (f" [{tools}]" if tools else "") + (f" -> {t['said']!r}" if t["said"] else "") + mark)


@turns_app.callback()
def turns_list(ctx: typer.Context,
               day: str = typer.Option(None, help="The day to show, YYYY-MM-DD; the newest when left out."),
               last: int = typer.Option(30, help="How many of the day's turns, newest last.")):
    """Each spoken turn of a day: what was heard, what he did and said, and any mark."""
    if ctx.invoked_subcommand:
        return
    from winston.core.turns import TurnStore
    store = TurnStore(load())
    if not store.enabled:
        print("Turns are not being kept (logging.turns.enabled is off).")
    days = store.days()
    day = day or (days[0] if days else None)
    rows = store.day(day) if day else []
    if not rows:
        print(f"No turns{f' on {day}' if day else ''} in {store.root}."); return
    for t in rows[-last:]:
        print(_turn_line(t))
    print(f"\n{len(rows)} turns on {day}; other days: {', '.join(d for d in days if d != day)[:200] or 'none'}")


@turns_app.command("mark")
def turns_mark(turn_id: str,
               kind: str = typer.Argument(..., help="misheard | not_addressed | wrong_action | too_slow"),
               said: str = typer.Option(None, help="What you actually said."),
               wanted: str = typer.Option(None, help="What should have happened."),
               expect: list[str] = typer.Option(None, help="A tool it should call (substring, a|b for either)."),
               forbid: list[str] = typer.Option(None, help="A tool it must not call (substring)."),
               note: str = typer.Option(None)):
    """Mark a turn as a mistake; it is kept until unmarked and replayed by `winston check turns`."""
    from winston.core.turns import TurnStore
    try:
        t = TurnStore(load()).mark(turn_id, kind, said=said, wanted=wanted, expect=expect, forbid=forbid, note=note,
                                   by="cli")
    except (KeyError, ValueError) as e:
        print(e.args[0] if e.args else e); raise typer.Exit(1)
    print(_turn_line(t))


@turns_app.command("unmark")
def turns_unmark(turn_id: str):
    """Take a turn's mark off; it is then removed with its day."""
    from winston.core.turns import TurnStore
    try:
        found = TurnStore(load()).unmark(turn_id)
    except ValueError as e:
        print(e); raise typer.Exit(1)
    print("unmarked" if found else f"turn {turn_id} has no mark")


@turns_app.command("marked")
def turns_marked():
    """Every marked turn, oldest first."""
    from winston.core.turns import TurnStore
    rows = TurnStore(load()).marked()
    for t in rows:
        print(f"{t['day']}  {_turn_line(t)}")
    print(f"{len(rows)} marked")


@app.command()
def check(which: str = typer.Argument("all", help="unit | ui | m0 | m1 | m2 | brain | clone | turns | all"),
          with_: Path = typer.Option(None, "--with", help="turns: a config file laid over the config, a candidate "
                                                            "setup to score")):
    """Run the checks: unit needs nothing, ui a built web UI (a throwaway winstond on a copy of data/),
    the milestones need the engines up."""
    tests = repo_root() / "tests"
    unit = sorted(tests.glob("*_cases.py")) + [tests / "profiles_load.py", repo_root() / "sdk" / "tests" / "sdk_cases.py"]
    builtin = sorted(p.name.removesuffix("_test.py") for p in (repo_root() / "src" / "winston" / "extensions").glob("*_test.py"))
    # each file is a step of its own, so one failing does not hide the rest
    unit_steps = {t.name: [sys.executable, str(t)] for t in unit} | \
        {f"{name}_test.py": [sys.executable, "-m", "winston.cli.main", "ext", "test", name]
         for name in builtin}   # built-in extensions' own tests
    plan = {"unit": unit_steps,
            "ui": ["pnpm", "--dir", str(repo_root() / "web"), "smoke"],
            "m0": ["bash", str(tests / "m0_tool_call.sh")],
            "m1": [sys.executable, str(tests / "m1_ha.py")],
            "m2": [sys.executable, str(tests / "m2_components.py")],
            "brain": [sys.executable, str(tests / "brain_trials.py")],
            "clone": [sys.executable, str(tests / "clone_voice_headless.py")],
            "turns": [sys.executable, str(tests / "turns_check.py"), *(["--with", str(with_)] if with_ else [])]}
    # brain and turns are comparisons and clone takes a minute and a half; they run on purpose
    names = [n for n in plan if n not in ("brain", "clone", "turns")] if which == "all" else [which]
    failed = []
    for n in names:
        print(f"\n=== {n}")
        for step, cmd in (plan[n] if n == "unit" else {n: plan[n]}).items():
            if subprocess.run(cmd, cwd=repo_root()).returncode != 0:
                failed.append(step)
    if failed:
        print(f"\nFAILED: {failed}"); raise typer.Exit(1)
    print("\nall passed")


@config_app.command("path")
def config_path():
    print(data_dir() / "config.yaml")


@config_app.command("show")
def config_show():
    import yaml
    print(yaml.safe_dump(load().model_dump(mode="json", exclude_none=True), sort_keys=False))


@config_app.command("schema")
def config_schema():
    import json
    from winston.config.schema import WinstonConfig
    print(json.dumps(WinstonConfig.model_json_schema(), indent=2))


@api_app.command("schema")
def api_schema():
    """Print the OpenAPI description of winstond (the web client is generated from it)."""
    import json
    from winston.api.app import openapi_spec
    print(json.dumps(openapi_spec(), indent=2))


@ha_app.command("ping")
def ha_ping():
    from winston.providers.home_assistant import HomeAssistant
    print(asyncio.run(HomeAssistant(load()).ping()))


@ha_app.command("expose")
def ha_expose(list_only: bool = typer.Option(False, "--list", help="Show the plan and what is exposed, change nothing.")):
    """Make Home Assistant's Assist exposure match the list in config (Save on the page does the same).
    Adds aliases, never renames; aliases are removed only by a save that drops them."""
    from winston.providers.home_assistant import HomeAssistant

    ha = HomeAssistant(load())

    async def run():
        p, snap = await ha.plan()
        if snap.errors:
            print("Home Assistant refused:", "; ".join(snap.errors))
        for eid in p.skipped:
            print(f"skip       {eid} (Home Assistant does not know it)")
        for eid in p.no_registry:
            print(f"no aliases {eid} (not in Home Assistant's entity registry)")
        if p.auto_expose_off:
            print("turn off   Home Assistant's auto-expose for Assist")
        for eid in p.expose:
            print(f"expose     {eid}")
        for eid in p.unexpose:
            print(f"un-expose  {eid}")
        for eid, add in p.aliases_add.items():
            print(f"aliases    {eid} + {', '.join(add)}")
        if p.empty:
            print("in sync: nothing to change")
        if list_only or p.empty:
            print(f"exposed ({len(snap.exposed or ())}):")
            for eid in sorted(snap.exposed or ()):
                print("  ", eid)
            return 0
        r = await ha.apply()
        if r.error:
            print("not applied:", r.error)
            return 1
        print(f"applied: exposed {len(r.exposed)}, un-exposed {len(r.unexposed)}, "
              f"aliases added on {len(r.aliases_added)} entities")
        return 0

    raise typer.Exit(asyncio.run(run()))


if __name__ == "__main__":
    app()
