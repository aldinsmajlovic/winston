"""Setup mode and its link (src/winston/api/setup_link.py, docs/setup.md, The first run): a throwaway winstond
(`winston up --no-start`) in setup mode, reached from this computer's own network address, which it sees as
another device. Without the key refused; with the link's ?key= served, and then by the cookie that sets; a
key pasted from there; a step skipped, and still listed after the end; `winston setup link` making a new key,
the old one refused; with --host given, not opened to the network; after the wizard's Finish refused again,
while this computer is still served.

    uv run python tests/setup_link_cases.py
"""
import re
import subprocess
import sys
import time
from pathlib import Path

from harness import check, finish_checks, free_port, scratch_data, winstond_data

data = scratch_data("winston-setup-link-")

import httpx  # noqa: E402

from winston import setup  # noqa: E402
from winston.api import setup_link  # noqa: E402

ip = setup._lan_address()
if not ip:
    print("setup link: skipped, this computer has no network address to come from")
    sys.exit(0)

port = free_port()
winstond_data(data, {"llm": {"model": "fake", "base_url": "http://127.0.0.1:9/v1", "api_key_env": "LLM_API_KEY"},
                     "channels": {"voice": {"enabled": False}, "satellite": {"enabled": False},
                                  "discord": {"enabled": False}},
                     "providers": {"home_assistant": {"enabled": False, "url": "http://127.0.0.1:9"}},
                     "control": {"host": "127.0.0.1", "port": port}})
WINSTON = str(Path(sys.executable).with_name("winston"))
key = setup_link.new_key()
check("the setup key is kept only as its hash", (key in setup_link.path().read_text(), len(setup_link.state()["key_sha256"])),
      (False, 64))


def up(at: int, *args: str) -> subprocess.Popen:
    """`winston up --no-start` on port `at`, once it answers."""
    (data / "logs").mkdir(exist_ok=True)
    log = data / "logs" / f"up-{at}.log"
    proc = subprocess.Popen([WINSTON, "up", "--no-start", *args], stdout=log.open("w"), stderr=subprocess.STDOUT)
    for _ in range(80):
        try:
            if httpx.get(f"http://127.0.0.1:{at}/api/health", timeout=1).status_code == 200:
                return proc
        except httpx.HTTPError:
            pass
        time.sleep(0.25)
    proc.kill()
    sys.exit(f"winstond did not start: {log.read_text()[-2000:]}")


here, there = f"http://127.0.0.1:{port}", f"http://{ip}:{port}"
proc = up(port)
try:
    check("this computer needs no key", httpx.get(f"{here}/api/setup").json()["active"], True)
    r = httpx.get(f"{there}/api/setup")
    check("another address without the key is refused, and told what to open",
          (r.status_code, "setup link" in r.json()["detail"]), (403, True))
    check("a page, too, in words", (httpx.get(f"{there}/setup").status_code,
                                    "setup link" in httpx.get(f"{there}/setup").text), (403, True))
    check("a wrong key is refused", httpx.get(f"{there}/api/setup", params={"key": "2222-3333-4444"}).status_code, 403)

    phone = httpx.Client(base_url=there, timeout=10, headers={"Origin": there})
    r = phone.get("/api/setup", params={"key": key})
    check("with the setup link's key it is served, and knows it is another device",
          (r.status_code, r.json()["remote"], setup_link.COOKIE in r.cookies), (200, True, True))
    check("then by the cookie it set, without the key", phone.get("/api/status").status_code, 200)
    r = phone.put("/api/secrets/LLM_API_KEY", json={"value": "sk-test-from-the-phone"})
    check("a key pasted there is saved, as on this computer", (r.status_code, "sk-test-from-the-phone" in
                                                                (data / ".env").read_text()), (200, True))
    r = phone.put("/api/setup/steps/brain", json={"state": "skipped"})
    check("a step skipped", r.json()["steps"], {"brain": "skipped"})
    check("only the wizard's steps", phone.put("/api/setup/steps/nope", json={"state": "done"}).status_code, 404)

    # winston setup link: a new key, and the cookie of the old one no longer works.
    out = subprocess.run([WINSTON, "setup", "link"], capture_output=True, text=True, check=False).stdout
    new = re.search(r"\?key=([a-z0-9-]+)", out)
    check("winston setup link prints a new link with this computer's address",
          (bool(new), f"http://{ip}:{port}/setup?key=" in out, f"http://localhost:{port}/setup" in out), (True,) * 3)
    check("the old one is refused", phone.get("/api/status").status_code, 403)
    check("the new one is served", httpx.get(f"{there}/api/setup", params={"key": new and new.group(1)}).status_code,
          200)

    # Told where to listen, as the smoke test does, it stays on this computer.
    other = free_port()
    second = up(other, "--host", "127.0.0.1", "--port", str(other))
    try:
        httpx.get(f"http://{ip}:{other}/api/health", timeout=2)
        reached = True
    except httpx.HTTPError:
        reached = False
    check("with --host 127.0.0.1 it is not opened to the network", reached, False)
    second.terminate()
    second.wait(10)

    # The wizard's Finish: the page follows Who can open this page, which is this computer only.
    r = httpx.post(f"{here}/api/setup/finish")
    check("finished, the steps kept for Set up next", (r.json()["active"], r.json()["steps"]),
          (False, {"brain": "skipped"}))
    r = httpx.get(f"{there}/api/setup", params={"key": new and new.group(1)})
    check("after it, another address is refused even with the key", (r.status_code, "only" in r.json()["detail"]),
          (403, True))
    check("this computer is still served", httpx.get(f"{here}/api/status").status_code, 200)
    r = subprocess.run([WINSTON, "setup", "link"], capture_output=True, text=True, check=False)
    check("and winston setup link says there is no setup under way", (r.returncode, "No setup" in r.stderr), (1, True))
finally:
    proc.terminate()
    proc.wait(10)

finish_checks("setup link: another address refused without the key, served with it and its cookie, a key pasted, "
              "a step skipped, a new link from winston setup link, --host kept, refused again after Finish")
