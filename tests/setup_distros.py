"""The Setup track's checks of steps 3 and 4 on each distribution, in rootless Podman (docs/plan.md).

Two releases of this tree (FIRST and NEXT) are built with scripts/release.py and served over HTTP from this
computer, laid out as GitHub serves them. Each image starts bare but for curl and CA certificates. There
install.sh is cut short at every byte before its last line (nothing may run) and given a tarball with one byte
changed (refused, nothing unpacked), then installs FIRST as the README says, `curl ... | sh`, with `--yes
--profile hosted` for `winston setup`. Then `winston up` against tests/fake_openai.py, with Pocket TTS and
Parakeet, until every engine is healthy and `winston talk` gets the fake brain's reply; and as root, the
commands setup would run there for an NVIDIA profile: Docker, NVIDIA's container toolkit (no GPU needed to
install it) and the serial group for a new user. Last, a container running systemd: installed as a user with
sudo, the session ended and the service still answering; `winston update` to NEXT, the service restarted on
NEXT and the data directory the same but for logs and caches the restarted Winston writes; back to FIRST with
the service stopped, the whole data directory byte for byte; without lingering, stopped by the next logout;
`winston uninstall`.

The uv and Hugging Face caches are shared between the distributions, so the first one downloads. The
systemd container's user has its own. Logs go to the folder given (default ~/.cache/winston-setup-distros).
The release server listens on every address of this computer while it runs (rootless Podman reaches the
host only that way), on PORT.

    uv run python tests/setup_distros.py [debian-13 ubuntu-24.04 fedora-44 leap-16.0 archlinux systemd]
"""
import functools
import http.server
import json
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FIRST, NEXT = "0.1.0", "0.1.1"
PORT = 8771
SERVER = f"http://host.containers.internal:{PORT}"
IMAGES = {
    "debian-13": ("docker.io/library/debian:13", "apt-get update && apt-get install -y curl ca-certificates"),
    "ubuntu-24.04": ("docker.io/library/ubuntu:24.04", "apt-get update && apt-get install -y curl ca-certificates"),
    "fedora-44": ("registry.fedoraproject.org/fedora:44", "dnf install -y curl ca-certificates"),
    "leap-16.0": ("registry.opensuse.org/opensuse/leap:16.0", "zypper --non-interactive install curl ca-certificates"),
    "archlinux": ("docker.io/library/archlinux:latest", "pacman -Sy --noconfirm --needed curl ca-certificates"),
}
OUT = Path(sys.argv[sys.argv.index("--out") + 1]) if "--out" in sys.argv else Path.home() / ".cache/winston-setup-distros"
INSTALL = f"curl --proto '=http' -fsSL {SERVER}/latest/download/install.sh | WINSTON_RELEASES={SERVER} sh -s --"
# The fake brain in place of the hosted one, as a user would edit it on the Brain page.
FAKE_BRAIN = """
import pathlib, sys, yaml
d = pathlib.Path(sys.argv[1]); p = d / "config.yaml"; c = yaml.safe_load(p.read_text())
c["engines"]["llm"].update(url="http://127.0.0.1:9999", port=9999, health="/v1/models")
c["llm"].update(base_url="http://127.0.0.1:9999/v1", model="fake")
p.write_text(yaml.safe_dump(c, sort_keys=False))
(d / ".env").open("a").write("\\nLLM_API_KEY=test\\n")
"""
HEALTHY = """
import httpx, json, sys
s = httpx.get("http://127.0.0.1:8700/api/status", timeout=5).json()
states = {e["name"]: e["state"] for e in s["engines"]}
print(json.dumps(states)); sys.exit(0 if states and all(v == "healthy" for v in states.values()) else 1)
"""
# For an NVIDIA profile on this container, as setup reads it, for a user who has none of it yet.
ROOT_STEPS = """
import json
from winston import setup as s
m = s.read(); m.user, m.root, m.docker_bin, m.runtimes, m.docker_member, m.in_serial = "tester", False, False, [], False, False
print(json.dumps([[x.key, x.cmds] for x in s.steps(m, {"docker", "nvidia"}) if x.key != "linger"]))
"""
# install.sh cut at every byte before its last line, the call to main: nothing may run, so the trace has
# no command of main's and HOME and TMPDIR stay empty.
CUT = """
s=/tmp/install.sh; n=$(( $(wc -c < $s) - $(tail -n 1 $s | wc -c) )); ran=0
for i in $(seq 0 $n); do
  h=/tmp/cut/$i; mkdir -p $h
  head -c $i $s | HOME=$h TMPDIR=$h WINSTON_RELEASES=http://127.0.0.1:9 SHELL_UNDER_TEST -x >/dev/null 2>$h.trace
  if [ -n "$(ls -A $h)" ] || grep -q 'set -eu' $h.trace; then ran=$((ran+1)); echo "ran when cut at $i"; fi
  rm -rf $h $h.trace
done
echo "cut at $((n+1)) places, ran $ran times"; [ $ran = 0 ]
"""
DATA_SUMS = "cd {d} && find . -type f {skip} -print0 | sort -z | xargs -0 sha256sum"
RUNTIME = "! -path './logs/*' ! -path './state/*'"      # what a running Winston writes itself: logs, caches


def log(f, text: str):
    print(text, flush=True)
    f.write(text + "\n")
    f.flush()


def ex(name: str, script: str, timeout: int = 1800, user: str | None = None) -> subprocess.CompletedProcess:
    cmd = ["podman", "exec", name, "sh", "-c", script] if not user else \
        ["podman", "exec", name, "su", "-", user, "-c", script]
    try:
        return subprocess.run(cmd, text=True, capture_output=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as e:
        return subprocess.CompletedProcess(cmd, 124, e.stdout or "", f"timed out after {timeout} s")


def timed(f, name: str, what: str, script: str, **kw) -> tuple[bool, str]:
    t = time.monotonic()
    r = ex(name, script, **kw)
    out = (r.stdout + r.stderr).strip()
    log(f, f"--- {what}: exit {r.returncode}, {time.monotonic() - t:.0f} s\n{out[-3000:]}")
    return r.returncode == 0, r.stdout


def releases() -> Path:
    """FIRST and NEXT under download/v<version>/, latest/download pointing at FIRST, and tampered/ with
    FIRST's tarball one byte changed."""
    root = OUT / "releases"
    shutil.rmtree(root, ignore_errors=True)
    for v in (FIRST, NEXT):
        subprocess.run([sys.executable, str(REPO / "scripts/release.py"), v, str(root / f"download/v{v}")],
                       check=True, capture_output=True)
    (root / "latest").mkdir()
    (root / "latest/download").symlink_to(f"../download/v{FIRST}")
    bad = root / "tampered/latest/download"
    shutil.copytree(root / f"download/v{FIRST}", bad)
    tar = bad / f"winston-{FIRST}.tar.gz"
    data = bytearray(tar.read_bytes())
    data[len(data) // 2] ^= 1
    tar.write_bytes(data)
    return root


def latest(root: Path, version: str):
    link = root / "latest/download"
    link.unlink()
    link.symlink_to(f"../download/v{version}")


def serve(root: Path):
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass
    server = http.server.ThreadingHTTPServer(("0.0.0.0", PORT), functools.partial(Quiet, directory=str(root)))
    threading.Thread(target=server.serve_forever, daemon=True).start()


def wait_healthy(f, name: str, py: str, mins: int, user: str | None = None) -> tuple[bool, str]:
    end, states = time.monotonic() + mins * 60, ""
    t = time.monotonic()
    while time.monotonic() < end:
        r = ex(name, f"{py} -c '{HEALTHY}'", timeout=30, user=user)
        if r.stdout.strip() != states:
            states = r.stdout.strip()
            log(f, f"    {time.monotonic() - t:.0f} s: {states or r.stderr.strip()[-200:]}")
        if r.returncode == 0:
            return True, states
        time.sleep(10)
    return False, states


def before_install(f, name: str, shell: str, user: str | None = None) -> dict:
    """Cut short, and a tarball with one byte changed: neither may leave anything behind."""
    subprocess.run(["podman", "cp", str(REPO / "scripts/install.sh"), f"{name}:/tmp/install.sh"], check=True)
    cut = timed(f, name, f"install.sh cut short, under {shell}", CUT.replace("SHELL_UNDER_TEST", shell))[0]
    said = timed(f, name, "a tarball with one byte changed",
                 f"{INSTALL.replace(SERVER, SERVER + '/tampered')} --yes --profile hosted 2>&1; echo exit $?; "
                 "ls -A ~/.local/share/winston-app ~/.local/bin 2>&1", user=user)[1]
    refused = "does not match its SHA256SUMS" in said and "exit 1" in said and said.count("No such file") == 2
    return {"cut_short": cut, "tampered": refused}


def distro(key: str) -> dict:
    image, prep = IMAGES[key]
    name, caches = f"winston-setup-{key}", OUT / "caches"
    (caches / "uv").mkdir(parents=True, exist_ok=True)
    (caches / "hf").mkdir(parents=True, exist_ok=True)
    app = f"/root/.local/share/winston-app/{FIRST}"
    result = {"distro": key}
    with (OUT / f"{key}.log").open("w") as f:
        subprocess.run(["podman", "rm", "-f", name], capture_output=True, check=False)
        subprocess.run(["podman", "run", "-d", "--name", name, "-v", f"{caches / 'uv'}:/root/.cache/uv",
                        "-v", f"{caches / 'hf'}:/root/.cache/huggingface", image, "sleep", "infinity"],
                       capture_output=True, check=True)
        try:
            t0 = time.monotonic()
            ok, _ = timed(f, name, "curl and CA certificates", prep)
            result.update(before_install(f, name, "sh"))
            ok, said = timed(f, name, "install.sh, then winston setup --yes --profile hosted",
                             f"{INSTALL} --yes --profile hosted") if ok else (False, "")
            result["setup"] = said
            result["install_mins"] = round((time.monotonic() - t0) / 60, 1)
            if ok:
                ex(name, f"{app}/.venv/bin/python -c '{FAKE_BRAIN}' /root/.local/share/winston")
                subprocess.run(["podman", "exec", "-d", name, f"{app}/.venv/bin/python", f"{app}/tests/fake_openai.py",
                                "9999"], check=True)
                subprocess.run(["podman", "exec", "-d", name, "sh", "-c",
                                "PATH=/root/.local/bin:$PATH winston up > /root/up.log 2>&1"], check=True)
                log(f, "--- winston up, until every engine is healthy")
                ok, result["states"] = wait_healthy(f, name, f"{app}/.venv/bin/python", 20)
                result["healthy_mins"] = round((time.monotonic() - t0) / 60, 1)
                timed(f, name, "winston status", "/root/.local/bin/winston status")
                said = timed(f, name, "winston talk", "/root/.local/bin/winston talk 'Good evening'")[1]
                ok = ok and "Everything is in order" in said      # tests/fake_openai.py's reply, through his brain
                if not ok:
                    timed(f, name, "up.log", "tail -60 /root/up.log")
            result["ok"] = ok and result["cut_short"] and result["tampered"]

            # The root commands for an NVIDIA profile, as setup gives them here.
            got, cmds = timed(f, name, "root steps for an NVIDIA profile", f"{app}/.venv/bin/python -c '{ROOT_STEPS}'")
            ex(name, "useradd -m tester")
            ran = {}
            for k, step in (json.loads(cmds) if got else []):
                ran[k] = all(timed(f, name, f"{k}: {c}", c)[0] for c in step if not c.startswith("systemctl"))
            checks = {"docker": "dockerd --version", "nvidia": "nvidia-ctk --version && grep -c nvidia /etc/docker/daemon.json",
                      "serial": "id -Gn tester"}
            result["root"] = {k: ran.get(k, False) and timed(f, name, f"after {k}", checks[k])[0] for k in checks}
        finally:
            subprocess.run(["podman", "rm", "-f", "-t", "5", name], capture_output=True, check=False)
    return result


SYSTEMD_IMAGE = """FROM docker.io/library/debian:13
RUN apt-get update && apt-get install -y systemd systemd-sysv dbus libpam-systemd sudo curl ca-certificates \\
    && useradd -m -s /bin/bash tester && echo 'tester ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/tester
CMD ["/sbin/init"]
"""


def systemd(root: Path) -> dict:
    """Installed as a user, then logged out: the service lingers on. Updated, then uninstalled."""
    name, home = "winston-setup-systemd", "/home/tester"
    apps, data = f"{home}/.local/share/winston-app", f"{home}/.local/share/winston"
    py, user = f"{apps}/current/.venv/bin/python", "tester"
    svc = "systemctl --user -M tester@"
    result = {"distro": "systemd"}
    with (OUT / "systemd.log").open("w") as f:
        subprocess.run(["podman", "build", "-t", "winston-setup-systemd", "-f", "-", str(OUT)],
                       input=SYSTEMD_IMAGE, text=True, capture_output=True, check=True)
        subprocess.run(["podman", "rm", "-f", name], capture_output=True, check=False)
        subprocess.run(["podman", "run", "-d", "--name", name, "--systemd=always", "winston-setup-systemd"],
                       capture_output=True, check=True)
        try:
            timed(f, name, "systemd up", "systemctl is-system-running --wait", timeout=120)
            result.update(before_install(f, name, "bash", user=user))
            ok, result["setup"] = timed(f, name, "install.sh as a user with sudo, in a login session, which then ends",
                                        f"{INSTALL} --yes --profile hosted", user=user)
            if ok:
                subprocess.run(["podman", "exec", "-d", name, py, f"{apps}/current/tests/fake_openai.py", "9999"],
                               check=True)
            ok = ok and timed(f, name, "the fake brain, and the service restarted on it",
                              f"{py} -c '{FAKE_BRAIN}' {data} && systemctl --user restart winston", user=user)[0]
            time.sleep(20)                         # past the 10 s logind keeps a manager without lingering
            timed(f, name, "sessions after logging out", "loginctl list-sessions --no-legend; "
                  "loginctl show-user tester -p State -p Linger")
            # Lingering: no login left, only systemd's own manager session.
            gone = ex(name, "loginctl show-user tester -p State --value").stdout.strip() == "lingering"
            active = ex(name, f"{svc} is-active winston").stdout.strip()
            log(f, f"--- logged out, lingering: {gone}; winston.service: {active}")
            ok, result["states"] = wait_healthy(f, name, py, 20) if ok else (False, "")
            timed(f, name, "the unit", f"cat {home}/.config/systemd/user/winston.service")
            result["installed"] = ok and gone and active == "active"

            # winston update: NEXT beside FIRST, the service restarted on NEXT, the data directory as it was but
            # for what the restarted Winston writes itself.
            latest(root, NEXT)
            pid = f"{svc} show winston -p MainPID --value"
            sums = lambda skip=RUNTIME: ex(name, DATA_SUMS.format(d=data, skip=skip)).stdout  # noqa: E731
            before = sums()
            first_pid = ex(name, pid).stdout.strip()
            timed(f, name, "winston update", f"WINSTON_RELEASES={SERVER} winston update", user=user)
            time.sleep(5)
            new_pid = ex(name, pid).stdout.strip()
            cwd = ex(name, f"readlink /proc/{new_pid}/cwd", user=user).stdout.strip()   # its own, not root's
            after = sums()
            same = after == before and bool(before)
            if not same:
                log(f, "--- changed in the data directory:\n" + "\n".join(sorted(set(before.splitlines()) ^ set(after.splitlines()))))
            healthy, _ = wait_healthy(f, name, py, 10)
            version = timed(f, name, "winston version", "winston version", user=user)[1].strip()
            timed(f, name, "after the update", f"ls -la {apps}; {svc} status winston --no-pager | head -5")
            log(f, f"--- updated: version {version}, pid {first_pid} -> {new_pid} in {cwd}, data the same: {same}, "
                   f"healthy: {healthy}")
            result["updated"] = (version == NEXT and new_pid not in ("", "0", first_pid) and cwd == f"{apps}/{NEXT}"
                                 and same and healthy)

            # Back to FIRST, which is still here, with the service stopped: nothing downloaded, and the whole data
            # directory byte for byte, logs and caches too. Then started again on FIRST, for the control.
            ex(name, "systemctl --user stop winston", user=user)
            strict = sums("")
            ok_back, said = timed(f, name, f"winston update {FIRST}, the service stopped", f"winston update {FIRST}",
                                  user=user)
            back = (ok_back and "Downloading" not in said and "not running" in said and sums("") == strict
                    and ex(name, f"readlink {apps}/current").stdout.strip() == FIRST)
            ex(name, "systemctl --user start winston", user=user)
            back = back and wait_healthy(f, name, py, 10)[0]
            log(f, f"--- back to {FIRST}: {back}")
            result["updated"] = result["updated"] and back

            # The control: without lingering, logging out stops him.
            ex(name, "sudo loginctl disable-linger tester", user=user)
            time.sleep(20)                         # logind keeps the user's manager 10 s (UserStopDelaySec)
            stopped = ex(name, "curl -s -m 3 http://127.0.0.1:8700/api/status").returncode != 0
            log(f, f"--- without lingering, logged out: stopped {stopped}")

            # winston uninstall: the code and the service gone, the data directory as it was, the rest listed.
            ok_un, said = timed(f, name, "winston uninstall --yes", "winston uninstall --yes", user=user)
            left = ex(name, f"ls -A {apps} {home}/.config/systemd/user/winston.service {home}/.local/bin/winston 2>&1").stdout
            last = sums()                                        # his login started the service once more
            kept = last == after
            if not kept:
                log(f, "--- changed by uninstall:\n" + "\n".join(sorted(set(after.splitlines()) ^ set(last.splitlines()))))
            log(f, f"--- after uninstall: {left.strip()}; data the same: {kept}")
            result["uninstalled"] = (ok_un and left.count("No such file") == 3 and kept
                                     and "Left on this computer" in said and " GB" in said)
            result["ok"] = (result["installed"] and result["updated"] and stopped and result["uninstalled"]
                            and result["cut_short"] and result["tampered"])
            if not result["ok"]:
                timed(f, name, "its journal", "journalctl --user-unit winston -M tester@ --no-pager | tail -40; "
                      "journalctl -u user@1000 --no-pager | tail -20")
        finally:
            subprocess.run(["podman", "rm", "-f", "-t", "5", name], capture_output=True, check=False)
    return result


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    wanted = [a for a in sys.argv[1:] if not a.startswith("--") and a != str(OUT)] or [*IMAGES, "systemd"]
    root = releases()
    serve(root)
    results = []
    for k in wanted:
        latest(root, FIRST)
        results.append(systemd(root) if k == "systemd" else distro(k))
    (OUT / "results.json").write_text(json.dumps(results, indent=1))
    for r in results:
        print(f"{'ok  ' if r.get('ok') else 'FAIL'} {r['distro']}: {r.get('states', '')} {r.get('healthy_mins', '')} "
              f"min, root {r.get('root', '')}, cut short {r.get('cut_short')}, tampered {r.get('tampered')}"
              + (f", installed {r.get('installed')}, updated {r.get('updated')}, uninstalled {r.get('uninstalled')}"
                 if r["distro"] == "systemd" else ""))
    sys.exit(0 if all(r.get("ok") for r in results) else 1)
