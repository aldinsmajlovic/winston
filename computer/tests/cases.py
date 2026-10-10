"""winston-computer cases: its settings and token, every refusal of `open`, apps from made-up desktop
entries, logs, each package tool's answer read from a recording (tests/fixtures, recorded in containers
on 2026-10-10), the tools as an MCP client sees them, and HTTP with the token and allow_from. Nothing
here opens anything: the launcher is replaced by a recorder.

    uv run --project computer python computer/tests/cases.py
"""
import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).parent
FIX = HERE / "fixtures"
TMP = Path(tempfile.mkdtemp(prefix="winston-computer-cases-"))
os.environ["WINSTON_COMPUTER_CONFIG"] = str(TMP / "config")
os.environ["WINSTON_COMPUTER_STATE"] = str(TMP / "state")

from winston_computer import config, system, updates  # noqa: E402
from winston_computer.config import ConfigError, parse  # noqa: E402

failed = 0


def expect(name: str, ok: bool, detail=""):
    global failed
    failed += not ok
    print(f"{'ok  ' if ok else 'FAIL'} {name}{'' if ok else f' {detail}'}")


def refused(fn, *args) -> str:
    try:
        fn(*args)
    except system.Refused as e:
        return str(e)
    return ""


launched: list[list[str]] = []
system.spawn = lambda argv: launched.append(argv)      # never start anything for real


def settings():
    cfg = parse(config.TEMPLATE)
    expect("the template allows nothing", not cfg.folders and not cfg.logs and not cfg.allow_from
           and cfg.listen == ("127.0.0.1", 8711), cfg)
    for bad, word in (('listen = "nowhere"', "listen"), ('allow_from = ["not-an-address"]', "allow_from"),
                      ('[logs]\nx = { journal = "a", file = "b" }', "logs.x")):
        try:
            parse(bad)
            expect(f"a bad setting is refused: {word}", False)
        except ConfigError as e:
            expect(f"a bad setting is refused, naming it: {word}", word in str(e), e)
    cfg = parse('allow_from = ["192.0.2.0/24", "198.51.100.7"]')
    expect("allow_from takes addresses and networks", cfg.allowed("192.0.2.44") and cfg.allowed("198.51.100.7")
           and not cfg.allowed("203.0.113.1") and not cfg.allowed("junk"))
    token = config.new_token()
    stored = config.token_hash()
    expect("a token: only its hash is kept, readable by you alone", token not in config.token_path().read_text()
           and config.token_ok(token, stored) and not config.token_ok("guess", stored)
           and oct(config.token_path().stat().st_mode & 0o777) == "0o600", oct(config.token_path().stat().st_mode))
    expect("no token kept: nothing passes", not config.token_ok("", None) and not config.token_ok("x", None))


def addresses():
    expect("a web address opens as given", system.web_address("https://example.com/a?b=1") == "https://example.com/a?b=1")
    expect("a bare domain gets https", system.web_address("youtube.com") == "https://youtube.com")
    for bad in ("file:///etc/passwd", "javascript:alert(1)", "ftp://example.com", "http://", "https://exa mple.com",
                "ms-settings:display", "data:text/html,<b>"):
        expect(f"refused: {bad}", "Only web addresses" in refused(system.web_address, bad))
    launched.clear()
    system.open_url("example.com")
    expect("an address goes to the default opener", launched and launched[-1][-1] == "https://example.com", launched)


def files():
    allowed, outside = TMP / "Documents", TMP / "Private"
    (allowed / "work").mkdir(parents=True)
    outside.mkdir()
    (allowed / "report.pdf").write_text("x")
    (allowed / "budget 2026.ods").write_text("x")
    (allowed / "work" / "budget-old.ods").write_text("x")
    (allowed / "tool.sh").write_text("#!/bin/sh\n")
    (allowed / "plain").write_text("x")
    (allowed / "plain").chmod(0o755)
    (allowed / "game.desktop").write_text("[Desktop Entry]\n")
    (outside / "secret.txt").write_text("x")
    (allowed / "shortcut.txt").symlink_to(outside / "secret.txt")
    cfg = parse(f'folders = ["{allowed}"]')
    launched.clear()
    expect("a file by part of its name opens", system.open_file("report", cfg)["opened"] == str(allowed / "report.pdf")
           and launched[-1][-1] == str(allowed / "report.pdf"), launched)
    r = system.open_file("budget", cfg)
    expect("two that fit: nothing opens, both are named", r["opened"] is None and len(r["which"]) == 2 and len(launched) == 1, r)
    expect("outside the folders: not found", "No file like" in refused(system.open_file, "secret", cfg))
    expect("outside the folders by path: refused", "not a file in the folders" in
           refused(system.open_file, str(outside / "secret.txt"), cfg))
    expect("out through ../: refused", "not a file in the folders" in refused(system.open_file, "../Private/secret.txt", cfg))
    expect("a symlink inside pointing out: refused", "No file like" in refused(system.open_file, "shortcut", cfg))
    expect("a script inside: refused", "is a program" in refused(system.open_file, "tool.sh", cfg))
    expect("an executable inside: refused", "is a program" in refused(system.open_file, "plain", cfg))
    expect("a .desktop file inside: refused", "is a program" in refused(system.open_file, "game.desktop", cfg))
    expect("no folders allowed: no files", "No folders are allowed" in refused(system.open_file, "x", parse("")))
    expect("nothing refused was opened", len(launched) == 1, launched)


def apps():
    os.environ["XDG_DATA_HOME"], os.environ["XDG_DATA_DIRS"] = str(TMP / "nothing"), str(FIX / "apps")
    listed = system.apps()
    names = sorted(a.name for a in listed)
    expect("apps: what the desktop shows, from its entries", names == ["Firefox", "Konsole", "Spotify", "Terminal", "kitty"],
           names)
    expect("an entry in a subfolder gets its id from the path", any(a.id == "kde-org.kde.konsole.desktop" for a in listed))
    for asked, want in (("firefox", "Firefox"), ("Spotify", "Spotify"), ("spotify", "Spotify"), ("konsole", "Konsole"),
                        ("web browser", "Firefox")):
        app, _ = system.find_app(asked, listed)
        expect(f"'{asked}' is {want}", app is not None and app.name == want, app)
    app, which = system.find_app("terminal", listed)
    expect("'terminal' is the app named so, not the ones that are terminals", app is not None and app.name == "Terminal",
           (app, which))
    app, which = system.find_app("kity", listed)
    expect("a near miss is offered, not opened", app is None and [a.name for a in which] == ["kitty"], which)
    launched.clear()
    r = system.open_app("firefox")
    expect("an app starts from its desktop entry, with no arguments of ours", r == {"opened": "Firefox"} and
           launched[-1][-1] in (str(FIX / "apps/applications/firefox.desktop"), "firefox.desktop"), launched)
    expect("an app not installed: said", "No app called" in refused(system.open_app, "photoshop"))


def logs():
    log = TMP / "app.log"
    log.write_text("".join(f"line {i} {'ERROR' if i % 10 == 0 else 'ok'}\n" for i in range(1, 501)))
    cfg = parse(f'[logs]\napp = {{ file = "{log}" }}\nsync = {{ user_journal = "made-up.service" }}')
    r = system.logs(cfg, "app", lines=5)
    expect("a file log: its last lines, newest last", r["lines"] == [f"line {i} ok" for i in range(496, 501)][:4] +
           ["line 500 ERROR"], r)
    r = system.logs(cfg, "app", match="error", lines=3)
    expect("only lines with a word, any case", r["lines"] == ["line 480 ERROR", "line 490 ERROR", "line 500 ERROR"], r)
    expect("at most 200 lines", len(system.logs(cfg, "app", lines=999)["lines"]) == 200)
    expect("a log not allowed: refused, naming those that are", "allowed: app, sync" in refused(system.logs, cfg, "secure"))
    expect("since as said", system.since_spec("30m") == "-30min" and system.since_spec("2 hours") == "-2h"
           and system.since_spec("1d") == "-1d")
    expect("a time it cannot read: refused", "Could not read" in refused(system.since_spec, "since lunch"))


def recorded():
    rows = updates.parse_pacman((FIX / "pacman-checkupdates.txt").read_text())
    expect("pacman: checkupdates", len(rows) == 6 and rows[2] == {"package": "expat", "installed": "2.8.5-1",
           "available": "2.9.0-1", "security": None, "cves": []}, rows[:3])
    audit = updates.parse_arch_audit((FIX / "arch-audit.json").read_text())
    expect("arch-audit: package to CVE ids", audit["libxml2"] == ["CVE-2025-6170", "CVE-2025-49796", "CVE-2025-49795",
           "CVE-2025-49794"] and audit["openssl"] == ["CVE-2022-2068", "CVE-2025-4575"], audit)
    rows = updates.parse_apt((FIX / "apt-debian.txt").read_text())
    sec = [r["package"] for r in rows if r["security"]]
    expect("apt on Debian: the -security suite marks security", len(rows) == 25 and "liblzma5" in sec
           and "util-linux" in sec and "bash" not in sec and len(sec) == 11, (len(rows), sec))
    fixed = updates.parse_debsecan((FIX / "debsecan.txt").read_text())
    expect("debsecan: package to CVE ids, its TEMP- ids left out", fixed["gzip"] == ["CVE-2026-41991", "CVE-2026-41992"]
           and fixed["liblzma5"] == ["CVE-2026-34743"], fixed.get("liblzma5"))
    rows = updates.parse_apt((FIX / "apt-ubuntu.txt").read_text())
    expect("apt on Ubuntu", [(r["package"], r["security"]) for r in rows] == [("libssl3t64", True),
           ("openssl-provider-legacy", True)], rows)
    rows = updates.parse_dnf_check((FIX / "dnf-check-upgrade.txt").read_text())
    expect("dnf check-upgrade", len(rows) == 24 and rows[1]["package"] == "curl" and
           rows[1]["available"] == "8.18.0-12.fc44", rows[:2])
    adv = updates.parse_dnf_advisories((FIX / "dnf-advisory-info.json").read_text())
    expect("dnf advisories: CVE ids where the advisory names them", adv["curl"] == ["CVE-2026-18924", "CVE-2026-80229",
           "CVE-2026-80255"] and adv["openssl-libs"] == [] and "libevent" in adv, {k: adv[k] for k in list(adv)[:4]})
    rows = updates.parse_zypper_updates((FIX / "zypper-list-updates.xml").read_text())
    expect("zypper list-updates", [r["package"] for r in rows] == ["patterns-base-fips", "patterns-base-minimal_base"]
           and rows[0]["installed"] == "20241218-lp160.5.1", rows)
    count, cves = updates.parse_zypper_patches((FIX / "zypper-list-patches.xml").read_text())
    expect("zypper patches: needed security ones and their CVE ids", count == 1 and cves == ["CVE-2025-69720"], (count, cves))
    many = {"system": "x", "note": "n", "security_updates": 10, "cves": [f"CVE-2026-{i:05}" for i in range(7, 485, 50)],
            "restart_needed": True, "updates": [{"package": f"p{i:03}", "installed": "1", "available": "2",
            "security": i % 50 == 7, "cves": [f"CVE-2026-{i:05}"] if i % 50 == 7 else []} for i in range(485)]}
    v = updates.view(many)
    expect("it opens with one plain line", v["summary"] == "485 updates waiting, 10 for security, fixing 10 CVEs. "
           "A restart is needed.", v["summary"])
    expect("the answer fits: security first, then 30, and how many more", v["waiting"] == 485 and v["more"] == 455
           and v["updates"][0] == {"package": "p007", "available": "2", "security": True, "cves": ["CVE-2026-00007"]}
           and len(json.dumps(v)) < 4000, len(json.dumps(v)))
    v = updates.view(many, package="p12")
    expect("or only those with a name", v["waiting"] == 485 and [r["package"] for r in v["updates"]][:2] == ["p120", "p121"])


async def over_mcp():
    from mcp.client.client import Client

    from winston_computer.server import build
    log = TMP / "app.log"
    cfg = parse(f'folders = ["{TMP / "Documents"}"]\n[logs]\napp = {{ file = "{log}" }}\nsync = {{ user_journal = "x" }}')
    async with Client(build(cfg)) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}
        expect("the four tools", sorted(tools) == ["logs", "open", "security_updates", "status"], sorted(tools))
        o, s = tools["open"].annotations, tools["status"].annotations
        expect("open acts, the rest only read; all are outside text", o.read_only_hint is False and
               o.destructive_hint is False and s.read_only_hint is True and
               all(t.annotations.open_world_hint for t in tools.values()))
        enum = tools["logs"].input_schema["properties"]["source"].get("enum")
        expect("the brain sees the log names, not their paths", enum == ["app", "sync"] and str(log) not in
               json.dumps(tools["logs"].input_schema), tools["logs"].input_schema["properties"]["source"])
        r = await c.call_tool("open", {"url": "javascript:alert(1)"})
        text = r.content[0].text
        expect("a refusal reaches the client in its own words", r.is_error and "Only web addresses" in text, text)
        r = await c.call_tool("open", {"app": "firefox", "url": "example.com"})
        expect("two things at once: refused", r.is_error and "exactly one" in r.content[0].text, r.content[0].text)
        r = await c.call_tool("logs", {"source": "secure"})
        expect("a log name not offered: refused before it runs", r.is_error, r.content)
    async with Client(build(parse(""))) as c:
        expect("no logs allowed: no logs tool", "logs" not in {t.name for t in (await c.list_tools()).tools})
        bare = c.instructions or ""
    async with Client(build(cfg)) as c:
        full = c.instructions or ""
    expect("it says which computer it is and what it reaches, for the client's prompt",
           full.startswith(f"The user's computer {socket.gethostname()} (") and "files from Documents" in full
           and "reads its app and sync logs" in full and len(full) <= 300, full)
    expect("and nothing it may not", "files" not in bare and "logs" not in bare and "updates" in bare, bare)
    calls = (TMP / "state" / "calls.log").read_text()
    expect("each call is in its log, with where it came from", "stdio open {'url': 'javascript:alert(1)'} refused" in calls,
           calls[-300:])


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def over_http():
    import httpx2
    from mcp.client.client import Client
    from mcp.client.streamable_http import streamable_http_client
    from mcp.shared._httpx_utils import create_mcp_http_client
    token = config.new_token()
    for allow, label in (("[]", "anyone with the token"), ('["192.0.2.1"]', "only 192.0.2.1")):
        port = free_port()
        (TMP / "config" / "config.toml").write_text(f'listen = "127.0.0.1:{port}"\nallow_from = {allow}\n')
        proc = subprocess.Popen([sys.executable, "-m", "winston_computer", "serve"], stderr=subprocess.DEVNULL)
        url = f"http://127.0.0.1:{port}/mcp"
        try:
            for _ in range(100):
                try:
                    socket.create_connection(("127.0.0.1", port), 0.2).close()
                    break
                except OSError:
                    time.sleep(0.1)
            async with httpx2.AsyncClient() as h:
                body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
                codes = [(await h.post(url, json=body, headers=hdr)).status_code for hdr in
                         ({}, {"Authorization": "Bearer wrong"}, {"Authorization": f"Bearer {token}"})]
            if label == "anyone with the token":
                expect("HTTP: no token and a wrong one get 401", codes[:2] == [401, 401], codes)
                async with create_mcp_http_client(headers={"Authorization": f"Bearer {token}"}) as http, \
                        Client(streamable_http_client(url, http_client=http)) as c:
                    names = sorted(t.name for t in (await c.list_tools()).tools)
                    r = await c.call_tool("status", {})
                expect("HTTP: with its token, the tools answer", names == ["open", "security_updates", "status"]
                       and not r.is_error, names)
                newer = config.new_token()
                async with httpx2.AsyncClient() as h:
                    old_now = (await h.post(url, json=body, headers={"Authorization": f"Bearer {token}"})).status_code
                    new_now = (await h.post(url, json=body, headers={"Authorization": f"Bearer {newer}",
                                                                     "Accept": "application/json, text/event-stream"})).status_code
                expect("a new token takes effect at once, and the old one stops", old_now == 401 and new_now != 401,
                       (old_now, new_now))
                token = newer
            else:
                expect("HTTP: from an address not in allow_from, 403 even with the token", codes == [403, 403, 403], codes)
        finally:
            proc.terminate()
            proc.wait(5)
    calls = (TMP / "state" / "calls.log").read_text()
    expect("refused requests are logged with their address", "127.0.0.1 refused with 401" in calls
           and "127.0.0.1 refused with 403" in calls)


settings()
addresses()
files()
apps()
logs()
recorded()
asyncio.run(over_mcp())
asyncio.run(over_http())
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{'all passed' if not failed else f'{failed} failed'}")
sys.exit(1 if failed else 0)
