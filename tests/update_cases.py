"""Updates (src/winston/setup.py, src/winston/api/updates.py, scripts/release.py, docs/setup.md#updating), with
nothing real: releases served over HTTP as GitHub lays them out, an install's folders in a scratch directory, a
fake `uv`, and a throwaway winstond from this clone.

CHANGELOG.md read as the page and `winston update` show it; what is newer than this version; the latest release
and its CHANGELOG.md checked against SHA256SUMS; a release's notes for GitHub; an update installed beside this
one with `current` moved, a tarball that does not match refused; the page's update through its stages; and
GET /api/update in a clone, where Update is refused.

    uv run python tests/update_cases.py
"""
import asyncio
import functools
import hashlib
import http.server
import io
import json
import os
import runpy
import subprocess
import sys
import tarfile
import tempfile
import threading
from pathlib import Path

import httpx
from harness import REPO, check, finish_checks, free_port, scratch_data, winstond_data

data = scratch_data("winston-update-")
tmp = Path(tempfile.mkdtemp(prefix="winston-update-tree-"))

from winston import __version__, setup  # noqa: E402
from winston.api import updates  # noqa: E402

LOG = f"""# Changes

Intro text, not a release.

## 9.9.9 (2026-12-01)

- One change that is long enough
  to wrap onto a second line.
- Another.

## 9.9.8 (2026-11-30)

- A fix.

## {__version__} (2026-11-01)

- Where this one started.
"""

# ---- CHANGELOG.md -----------------------------------------------------------------------------------------
releases = setup.changes(LOG)
check("each release with its date and its changes, a wrapped bullet joined",
      releases[0], {"version": "9.9.9", "date": "2026-12-01",
                    "items": ["One change that is long enough to wrap onto a second line.", "Another."]})
check("newest first, the intro skipped", [r["version"] for r in releases], ["9.9.9", "9.9.8", __version__])
check("the releases newer than this one", [r["version"] for r in setup.after(releases, __version__)], ["9.9.9", "9.9.8"])
check("none when going back to an older one", setup.after(releases[1:], "9.9.9"), [])
check("none for a version not listed (a pre-release)", setup.after(releases, "0.0.1-rc.1"), [])
real = setup.changes((REPO / "CHANGELOG.md").read_text())
check("the repo's CHANGELOG.md: each release dated or unreleased, each with changes",
      [bool(r["date"]) and bool(r["items"]) for r in real], [True] * len(real))

release = runpy.run_path(str(REPO / "scripts/release.py"))
notes = release["notes"]
manifest = "custom_components/winston/manifest.json"
check("the integration's version is stamped as Winston's",
      json.loads(release["stamped"](manifest, (REPO / manifest).read_bytes(), "9.9.9"))["version"], "9.9.9")
check("the integration zip is what HACS installs (hacs.json)",
      {k: v for k, v in json.loads((REPO / "hacs.json").read_text()).items() if k in ("zip_release", "filename")},
      {"zip_release": True, "filename": "winston-ha.zip"})
dated = next(r for r in real if r["date"][:1].isdigit())
check("a release's GitHub notes are its entry, without the heading",
      notes(dated["version"]).startswith("- "), True)
check("a pre-release without an entry gets a line", notes("0.0.1-rc.1"), "A test build of the next release.\n")
try:
    notes("0.0.1")
    refused = None
except SystemExit as e:
    refused = str(e)
check("a release without a dated entry is refused", refused,
      'CHANGELOG.md has no dated entry "## 0.0.1 (YYYY-MM-DD)": `just release 0.0.1` writes the date')


# ---- Releases served as GitHub lays them out ----------------------------------------------------------------
def tarball(version: str) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        body = LOG.encode()
        info = tarfile.TarInfo(f"winston-{version}/CHANGELOG.md")
        info.size = len(body)
        tar.addfile(info, io.BytesIO(body))
    return buf.getvalue()


def publish(root: Path, version: str, changelog: bytes | None, bad_digest: bool = False, bad_tar: bool = False):
    d = root / "download" / f"v{version}"
    d.mkdir(parents=True)
    files = {f"winston-{version}.tar.gz": tarball(version)}
    if changelog is not None:
        files["CHANGELOG.md"] = changelog
    sums = ""
    for name, body in files.items():
        (d / name).write_bytes(body + (b"x" if bad_tar and name.endswith(".tar.gz") else b""))
        digest = "0" * 64 if bad_digest and name == "CHANGELOG.md" else hashlib.sha256(body).hexdigest()
        sums += f"{digest}  {name}\n"
    (d / "SHA256SUMS").write_text(sums)


def serve(root: Path, latest: str) -> str:
    """`latest/download/X` redirects to the latest's folder, as GitHub's does."""
    class Handler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path.startswith("/latest/download/"):
                self.send_response(302)
                self.send_header("Location", f"/download/v{latest}/" + self.path.rsplit("/", 1)[1])
                self.end_headers()
                return
            super().do_GET()
    port = free_port()
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), functools.partial(Handler, directory=str(root)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{port}"


good = tmp / "good"
publish(good, "9.9.9", LOG.encode())
publish(good, "9.9.7", None, bad_tar=True)
os.environ["WINSTON_RELEASES"] = serve(good, "9.9.9")
version, listed = setup.latest()
check("the latest release and its CHANGELOG.md", (version, [r["version"] for r in listed]),
      ("9.9.9", ["9.9.9", "9.9.8", __version__]))

bad = tmp / "bad"
publish(bad, "9.9.9", LOG.encode(), bad_digest=True)
os.environ["WINSTON_RELEASES"] = serve(bad, "9.9.9")
try:
    setup.latest()
    said = None
except ValueError as e:
    said = str(e)
check("a CHANGELOG.md that does not match its SHA256SUMS is refused", said, "its CHANGELOG.md does not match its SHA256SUMS")

old = tmp / "old"
publish(old, "9.9.9", None)
os.environ["WINSTON_RELEASES"] = serve(old, "9.9.9")
check("a release from before CHANGELOG.md: its version, no changes", setup.latest(), ("9.9.9", []))
os.environ["WINSTON_RELEASES"] = serve(good, "9.9.9")

# ---- Installing beside this one ---------------------------------------------------------------------------
app = tmp / "winston-app"
(app / __version__).mkdir(parents=True)
(app / "current").symlink_to(__version__)
real_root = setup.repo_root
setup.repo_root = lambda: app / __version__
fake_uv = tmp / "uv"
fake_uv.write_text(f"#!/bin/sh\npwd > {tmp}/uv-ran-in\n")
fake_uv.chmod(0o755)
os.environ["UV"] = str(fake_uv)
steps: list[str] = []
check("an update installed", setup.install(None, steps.append), "9.9.9")
check("its steps said", steps, ["Downloading Winston 9.9.9", "Installing its packages"])
check("unpacked beside this one, packages synced there, `current` moved",
      ((app / "9.9.9/CHANGELOG.md").is_file(), Path(f"{tmp}/uv-ran-in").read_text().strip(),
       os.readlink(app / "current"), (app / __version__).is_dir()),
      (True, str(app / "9.9.9"), "9.9.9", True))
check("still a release once `current` points at the new one (update prints its notes, restarts)",
      setup.release_app(), app)
(app / "current").unlink()
(app / "current").symlink_to(__version__)
try:
    setup.install("9.9.7", steps.append)
    said = None
except setup.UpdateError as e:
    said = str(e)
check("a tarball that does not match its SHA256SUMS is refused, `current` unchanged",
      (said, os.readlink(app / "current")),
      ("winston-9.9.7.tar.gz does not match its SHA256SUMS, so nothing changed.", __version__))
check("this version is not installed again", setup.install(__version__), None)


# ---- The page's update, through its stages ------------------------------------------------------------------
async def stages():
    restarted = []
    setup.service_running = lambda: True
    setup.restart_service = lambda wait=True: restarted.append(wait) or ""
    setup.install = lambda to, say: (say("Downloading Winston 9.9.9"), "9.9.9")[1]
    updates.start(None)
    check("installing at once", updates.STATE.stage, "installing")
    try:
        updates.start(None)
        said = None
    except setup.UpdateError as e:
        said = str(e)
    check("a second update while one runs is refused", said, "An update is under way.")
    while updates.STATE.stage == "installing":
        await asyncio.sleep(0.05)
    check("restarting on the new version", (updates.STATE.stage, updates.STATE.detail), ("restarting", "9.9.9"))
    await asyncio.sleep(1.2)
    check("the service's restart queued, not waited on from inside it", restarted, [False])

    updates.STATE.stage = ""        # the restart would have ended this process
    setup.service_running = lambda: False
    updates.start(None)
    while updates.STATE.stage == "installing":
        await asyncio.sleep(0.05)
    check("without the service: installed, waiting for a restart by hand",
          (updates.STATE.stage, updates.STATE.detail), ("installed", "9.9.9"))

    def refuse(to, say):
        raise setup.UpdateError("Could not download it: nothing answered")
    setup.install = refuse
    updates.start(None)
    while updates.STATE.stage == "installing":
        await asyncio.sleep(0.05)
    check("a failed update says why", (updates.STATE.stage, updates.STATE.detail),
          ("error", "Could not download it: nothing answered"))

asyncio.run(stages())
setup.repo_root = real_root
try:
    updates.start(None)
    said = None
except setup.UpdateError as e:
    said = str(e)
check("a clone is not updated from the page", said, setup.CLONE)


# ---- GET and POST /api/update in a throwaway winstond from this clone ---------------------------------------
described = subprocess.run(["git", "-C", str(REPO), "describe", "--tags", "--dirty"],
                           capture_output=True, text=True, check=False).stdout.strip().removeprefix("v")


async def api():
    port = free_port()
    winstond_data(data, {"llm": {"model": "fake", "base_url": "http://127.0.0.1:9/v1"},
                         "channels": {"voice": {"enabled": False}, "satellite": {"enabled": False},
                                      "discord": {"enabled": False}},
                         "providers": {"home_assistant": {"enabled": False, "url": "http://127.0.0.1:9"}},
                         "control": {"host": "127.0.0.1", "port": port}})
    (data / "logs").mkdir(exist_ok=True)
    proc = subprocess.Popen([str(Path(sys.executable).with_name("winston")), "up", "--no-start"],  # noqa: ASYNC220
                            stdout=(data / "logs/up.log").open("w"), stderr=subprocess.STDOUT)
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=30) as c:
            for _ in range(80):
                try:
                    if (await c.get("/api/health")).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.25)
            u = (await c.get("/api/update")).json()
            check("a clone: its git version, the latest release, no list of what it lacks",
                  (u["version"], u["clone"], u["latest"], u["notes"], u["current"], u["stage"], bool(u["checked"])),
                  (described or __version__, True, "9.9.9", [], None, "", True))
            r = await c.post("/api/update", json={})
            check("Update in a clone is refused in words", (r.status_code, r.json()["detail"]), (409, setup.CLONE))
    finally:
        proc.terminate()
        proc.wait(timeout=30)

asyncio.run(api())

finish_checks("update: CHANGELOG.md read and its newer releases, the latest release checked, release notes, "
              "an update installed beside this one, the page's update through its stages, /api/update in a clone")
