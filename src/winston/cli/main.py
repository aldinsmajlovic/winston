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
from winston.config import data_dir, load, repo_root, resolve

app = typer.Typer(help="Winston, a self-hosted voice assistant.", no_args_is_help=True, add_completion=False)
config_app = typer.Typer(help="Inspect the configuration.")
ha_app = typer.Typer(help="Home Assistant integration.")
api_app = typer.Typer(help="The control API.")
app.add_typer(config_app, name="config")
app.add_typer(ha_app, name="ha")
app.add_typer(api_app, name="api")


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
def announce(text: str = typer.Argument(..., help="What Winston says."),
             context: list[str] = typer.Option(None, "--context", "-c",
                                               help="key=value details kept for follow-up questions; repeatable.")):
    """Make Winston say something on its own: chime and speech on connected speakers, Discord per config."""
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
    """Show what Winston remembers, as it goes into the prompt. Edit the files to change it."""
    from winston.core.memory import prompt_block, vault
    cfg = load()
    print(prompt_block(cfg) or "Nothing remembered yet.")
    print(f"\n({vault(cfg)})")


@app.command()
def check(which: str = typer.Argument("all", help="unit | m0 | m1 | m2 | brain | clone | all")):
    """Run the checks: unit needs nothing, the milestones need the engines up."""
    tests = repo_root() / "tests"
    unit = ["guard_cases.py", "memory_cases.py", "persona_cases.py", "announce_cases.py", "live_cases.py", "profiles_load.py"]
    plan = {"unit": ["bash", "-c", " && ".join(f"{sys.executable} {tests / t}" for t in unit)],
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
def ha_expose(list_only: bool = typer.Option(False, "--list", help="Show what is exposed, change nothing.")):
    """Make Home Assistant's Assist exposure match the allowlist in config."""
    from winston.providers.home_assistant import HomeAssistant
    r = asyncio.run(HomeAssistant(load()).apply_exposure(list_only))
    if "error" in r:
        print(r["error"]); raise typer.Exit(1)
    if r.get("unexposed"):
        print(f"un-exposed {r['unexposed']} entities not on the list")
    print(f"exposed ({len(r['exposed'])}):")
    for eid in r["exposed"]:
        print("  ", eid)


if __name__ == "__main__":
    app()
