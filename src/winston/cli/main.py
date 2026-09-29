"""The winston command. Everything here goes through config and the control API, nothing
touches processes directly."""
import asyncio
import os
import shutil
import subprocess
import sys
from pathlib import Path

import httpx
import typer
from loguru import logger

from winston import __version__
from winston.cli.ext import ext_app
from winston.config import data_dir, load, repo_root, resolve

app = typer.Typer(help="Winston, a self-hosted voice assistant.", no_args_is_help=True, add_completion=False)
config_app = typer.Typer(help="Inspect the configuration.")
ha_app = typer.Typer(help="Home Assistant integration.")
api_app = typer.Typer(help="The control API.")
app.add_typer(config_app, name="config")
app.add_typer(ha_app, name="ha")
app.add_typer(api_app, name="api")
app.add_typer(ext_app, name="ext")


def _api() -> str:
    cfg = load()
    return f"http://{cfg.control.host}:{cfg.control.port}"


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
    try:
        s = httpx.get(f"{_api()}/api/status", timeout=10).json()
    except httpx.HTTPError:
        print("winstond is not running (start it with `winston up`)"); raise typer.Exit(1)
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
    r = httpx.post(f"{_api()}/api/engines/{name}/restart", timeout=30)
    print(r.json() if r.status_code == 200 else r.text)


@app.command()
def down():
    """Stop every engine and channel (winstond keeps serving)."""
    print(httpx.post(f"{_api()}/api/engines/stop_all", timeout=120).json())


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
    try:
        r = httpx.post(f"{_api()}/api/announce", json={"text": text, "context": ctx, "source": "cli"}, timeout=10)
    except httpx.HTTPError:
        print("winstond is not running (start it with `winston up`)"); raise typer.Exit(1)
    if r.status_code != 200:
        print(r.text); raise typer.Exit(1)
    d = r.json()
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


@app.command()
def check(which: str = typer.Argument("all", help="unit | ui | m0 | m1 | m2 | brain | clone | all")):
    """Run the checks: unit needs nothing, ui a built web UI (a throwaway winstond on a copy of data/),
    the milestones need the engines up."""
    tests = repo_root() / "tests"
    unit = ["guard_cases.py", "memory_cases.py", "persona_cases.py", "announce_cases.py", "live_cases.py",
            "verify_cases.py", "hints_cases.py", "consent_cases.py", "rooms_cases.py", "home_cases.py", "profiles_load.py",
            "extensions_cases.py"]
    unit_paths = [tests / t for t in unit] + [repo_root() / "sdk" / "tests" / "sdk_cases.py"]
    plan = {"unit": ["bash", "-c", " && ".join(f"{sys.executable} {t}" for t in unit_paths)],
            "ui": ["pnpm", "--dir", str(repo_root() / "web"), "smoke"],
            "m0": ["bash", str(tests / "m0_tool_call.sh")],
            "m1": [sys.executable, str(tests / "m1_ha.py")],
            "m2": [sys.executable, str(tests / "m2_components.py")],
            "brain": [sys.executable, str(tests / "brain_trials.py")],
            "clone": [sys.executable, str(tests / "clone_voice_headless.py")]}
    # brain is a comparison and clone takes a minute and a half; both run on purpose
    names = [n for n in plan if n not in ("brain", "clone")] if which == "all" else [which]
    failed = []
    for n in names:
        print(f"\n=== {n}")
        if subprocess.run(plan[n], cwd=repo_root()).returncode != 0:
            failed.append(n)
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
