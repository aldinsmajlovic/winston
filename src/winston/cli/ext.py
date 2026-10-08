"""`winston ext`: extensions from the command line. Everything goes through winstond's API, except
`new` and `test`, which work on files and need no winstond."""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

import httpx
import typer

from winston.config import load

ext_app = typer.Typer(help="Extensions: list, switch on and off, call, run, read logs, write and test your own.",
                      no_args_is_help=True)

ICONS = {"running": "●", "starting": "◐", "restarting": "◑", "stopped": "○", "failed": "✕", "problem": "✕"}

TEMPLATE = '''# /// script
# [tool.winston]
# id = "{id}"
# name = "{name}"
# description = "What it does, in one sentence."
# api = 1
# permissions = {{}}
# ///
"""{name}: a starting point. The whole guide is docs/extensions.md."""
from winston_sdk import Extension, Field, Settings

ext = Extension()


class {cls}Settings(Settings):
    greeting: str = Field("Good evening", description="How he greets someone.")


ext.settings({cls}Settings)


@ext.tool(tier="read", outside=False)
async def hello(ctx, name: str) -> str:
    """Greets someone by name.

    Args:
        name: Who to greet.
    """
    ctx.store["greeted"] = ctx.store.get("greeted", 0) + 1
    return f"{{ctx.settings.greeting}}, {{name}}."


@ext.status
async def status(ctx) -> str:
    return f"Greeted {{ctx.store.get('greeted', 0)}} times"
'''

TEST_TEMPLATE = '''"""Tests for {name}: `winston ext test {id}` runs every test_ function here."""
from pathlib import Path

from winston_sdk.testing import Harness

HERE = Path(__file__).parent


async def test_hello():
    h = Harness(HERE / "{id}.py", settings={{"greeting": "Hello"}})
    assert await h.tool("hello", name="Ada") == "Hello, Ada."
    assert await h.status() == "Greeted 1 times"
    await h.close()
'''


def _call(method: str, path: str, **kw) -> httpx.Response:
    """A call to winstond; when it is not running, says so and exits."""
    try:
        return httpx.request(method, load().control.url + path, timeout=kw.pop("timeout", 30), **kw)
    except httpx.HTTPError:
        print("winstond is not running (start it with `winston up`)")
        raise typer.Exit(1)


def _ok(r: httpx.Response) -> dict | list:
    """Its JSON; an error's detail is printed and exits."""
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail", r.text)
        except ValueError:
            detail = r.text
        print(detail if isinstance(detail, str) else json.dumps(detail, indent=2))
        raise typer.Exit(1)
    return r.json()


def _line(e: dict) -> str:
    state = e["state"]
    why = e["problem"] or e["detail"] or e.get("status") or ""
    needs = f"  (needs {', '.join(e['needs'])})" if e.get("needs") and e["enabled"] else ""
    on = "on " if e["enabled"] else "off"
    return f"  {ICONS.get(state, '?')} {e['id']:<14} {on} {state:<10} {e['origin']:<9} {why}{needs}"


@ext_app.command("list")
def list_():
    """Every extension found: on or off, whether it runs, where it comes from, its status line."""
    rows = _ok(_call("GET", "/api/extensions"))
    if not rows:
        print("no extensions yet; `winston ext new NAME` makes one in data/extensions/")
    for e in rows:
        print(_line(e))


def _switch(ext_id: str, on: bool):
    r = _call("PATCH", "/api/config", json={"changes": [{"path": ["extensions", ext_id, "enabled"], "value": on}]})
    _ok(r)
    for _ in range(40 if on else 10):
        e = _ok(_call("GET", f"/api/extensions/{ext_id}"))
        if (on and e["state"] in ("running", "failed", "problem")) or (not on and e["state"] == "stopped"):
            break
        time.sleep(0.25)
    print(_line(e))


@ext_app.command()
def enable(ext_id: str = typer.Argument(..., metavar="ID")):
    """Switch an extension on (saved in config.yaml); it starts at once."""
    e = _ok(_call("GET", f"/api/extensions/{ext_id}"))
    perms = {k: v for k, v in e["permissions"].items() if v}
    if perms:
        print(f"{e['name']} may reach: {json.dumps(perms)}")
    _switch(ext_id, True)


@ext_app.command()
def disable(ext_id: str = typer.Argument(..., metavar="ID")):
    """Switch an extension off; it stops and its tools leave the list."""
    _switch(ext_id, False)


@ext_app.command()
def restart(ext_id: str = typer.Argument(..., metavar="ID")):
    """Start an extension again."""
    print(_line(_ok(_call("POST", f"/api/extensions/{ext_id}/restart"))))


@ext_app.command()
def call(ext_id: str = typer.Argument(..., metavar="ID"), tool: str = typer.Argument(...),
         args: str = typer.Argument("{}", help="The arguments as JSON, e.g. '{\"name\": \"Ada\"}'.")):
    """Call a tool as the brain would, and print what it answers."""
    try:
        body = json.loads(args)
    except ValueError as e:
        print(f"the arguments are not JSON: {e}")
        raise typer.Exit(2)
    r = _ok(_call("POST", f"/api/extensions/{ext_id}/tools/{tool}", json=body, timeout=90))
    if r["ok"]:
        print(r["result"] if isinstance(r["result"], str) else json.dumps(r["result"], indent=2, ensure_ascii=False))
    else:
        print(f"error: {r['error']}")
        raise typer.Exit(1)


@ext_app.command()
def run(ext_id: str = typer.Argument(..., metavar="ID"), job: str = typer.Argument(...)):
    """Run one of its jobs now."""
    r = _ok(_call("POST", f"/api/extensions/{ext_id}/jobs/{job}", json={}, timeout=700))
    print("done" if r["ok"] else f"error: {r['error']}")
    if not r["ok"]:
        raise typer.Exit(1)


@ext_app.command()
def check(ext_id: str = typer.Argument(..., metavar="ID"), kind: str = typer.Argument(...)):
    """What a checked kind's check would say now, without recording or saying anything."""
    r = _ok(_call("POST", f"/api/extensions/{ext_id}/kinds/{kind}/check", timeout=30))
    if not r["ok"]:
        print(f"error: {r['error']}")
        raise typer.Exit(1)
    if not r["nudges"]:
        print("nothing to say now")
    for n in r["nudges"]:
        extra = "".join(f", {k} {n[k]}" for k in ("not_before", "expires") if n.get(k)) + \
            (", urgent" if n.get("urgent") else "")
        print(f"  {n['about']}  level {n['level']}{extra}\n    {n['say']}")
        if n.get("facts"):
            print(f"    facts: {json.dumps(n['facts'], ensure_ascii=False)}")


@ext_app.command()
def nudges(ext_id: str = typer.Argument(None, metavar="[ID]", help="One extension's; all when left out.")):
    """Where its nudges stand, and why each waits or was held (every step is in logs/nudges.jsonl)."""
    rows = _ok(_call("GET", "/api/nudges", params={"extension": ext_id} if ext_id else {}))
    if not rows:
        print("no nudges")
    for n in rows:
        why = n["put_off"] and f"put off until {n['put_off']}" or n["why"] or (f"via {n['via']}" if n["via"] else "")
        print(f"  {n['extension']}/{n['kind']:<12} {n['state']:<9} level {n['level']}  {n['about']}"
              f"{'  (' + why + ')' if why else ''}\n    {n['say']}  [{n['id']}]")


@ext_app.command()
def logs(ext_id: str = typer.Argument(..., metavar="ID"), lines: int = typer.Option(50, "-n", help="How many lines.")):
    """The end of its log: what it printed and each failure with its traceback."""
    for line in _ok(_call("GET", f"/api/extensions/{ext_id}/log", params={"lines": lines}))["lines"]:
        print(line)


@ext_app.command()
def new(name: str = typer.Argument(..., help="Its id: lowercase letters, digits and underscores.")):
    """A new single-file extension in data/extensions/, with a test file beside it."""
    from winston.extension_host.sources import personal_dir
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,39}", name):
        print("the id takes lowercase letters, digits and underscores, starting with a letter")
        raise typer.Exit(2)
    folder = personal_dir()
    folder.mkdir(parents=True, exist_ok=True)
    path, test = folder / f"{name}.py", folder / f"{name}_test.py"
    if path.exists():
        print(f"{path} already exists")
        raise typer.Exit(1)
    title = name.replace("_", " ").capitalize()
    cls = "".join(p.capitalize() for p in name.split("_"))
    path.write_text(TEMPLATE.format(id=name, name=title, cls=cls))
    test.write_text(TEST_TEMPLATE.format(id=name, name=title))
    print(f"create {path}\ncreate {test}")
    try:
        httpx.post(load().control.url + "/api/extensions/refresh", timeout=60).raise_for_status()
        print(f"\nwinstond found it. Next: `winston ext test {name}`, then `winston ext enable {name}`.")
    except Exception:                                                   # noqa: BLE001  (not running, no config yet)
        print(f"\nNext: `winston ext test {name}`; `winston up` will find it, then `winston ext enable {name}`.")


@ext_app.command()
def test(which: str = typer.Argument(..., metavar="ID_OR_PATH",
                                     help="An extension's id, or a test file or an extension's file or folder.")):
    """Run an extension's tests with the SDK's harness (no winstond needed), in the environment it runs
    in: Winston's, or its own made by uv when it needs packages Winston does not have."""
    import subprocess

    from winston_sdk import manifest as manifest_mod
    from winston_sdk.testing import tests_for

    from winston.extension_host.sources import Source, command, discover
    path = Path(which)
    if not path.exists():
        src = discover(load()).get(which)
        if src is None or not src.target or ":" in src.target and not Path(src.target).exists():
            print(f"no extension {which} with files to test here")
            raise typer.Exit(1)
        path = Path(src.target)
    if not tests_for(path):
        print(f"no tests for {which} (a {path.stem}_test.py beside it, or test_*.py in its folder)")
        raise typer.Exit(1)
    extension = path.with_name(path.name.removesuffix("_test.py") + ".py") if path.name.endswith("_test.py") else path
    try:
        m = manifest_mod.read(extension)
    except (manifest_mod.ManifestError, OSError) as e:
        print(f"cannot read its manifest: {e}")
        raise typer.Exit(1)
    run = command(Source(id=m.id, origin="personal", target=str(extension), manifest=m))
    sys.exit(subprocess.run([*run, "test", str(path)]).returncode)
