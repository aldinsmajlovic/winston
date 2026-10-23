"""The Setup track's step 3 check on each distribution, in rootless Podman (docs/plan.md).

Each image starts bare but for curl and CA certificates, gets uv from its installer and this tree as a
release has it (the files git tracks or would, and the built UI), then runs `winston setup --yes --profile
hosted` and `winston up` against tests/fake_openai.py, with Pocket TTS and Parakeet, until every engine is
healthy and `winston talk` gets the fake brain's reply. Then, as root, the commands setup would run there for an NVIDIA profile: Docker, NVIDIA's
container toolkit (no GPU needed to install it) and the serial group for a new user. Last, a container
running systemd: setup as a user with sudo, the session ended, and the service still answering; then
without lingering, stopped by the next logout.

The uv and Hugging Face caches are shared between the distributions, so the first one downloads. The
systemd container's user has its own. Logs go to the folder given (default ~/.cache/winston-setup-distros).

    uv run python tests/setup_distros.py [debian-13 ubuntu-24.04 fedora-44 leap-16.0 archlinux systemd]
"""
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
UV = "0.10.6"                    # the uv this tree was built with
IMAGES = {
    "debian-13": ("docker.io/library/debian:13", "apt-get update && apt-get install -y curl ca-certificates"),
    "ubuntu-24.04": ("docker.io/library/ubuntu:24.04", "apt-get update && apt-get install -y curl ca-certificates"),
    "fedora-44": ("registry.fedoraproject.org/fedora:44", "dnf install -y curl ca-certificates"),
    "leap-16.0": ("registry.opensuse.org/opensuse/leap:16.0", "zypper --non-interactive install curl ca-certificates"),
    "archlinux": ("docker.io/library/archlinux:latest", "pacman -Sy --noconfirm --needed curl ca-certificates"),
}
APP = "/root/.local/share/winston/app/dev"
OUT = Path(sys.argv[sys.argv.index("--out") + 1]) if "--out" in sys.argv else Path.home() / ".cache/winston-setup-distros"
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


def stage() -> Path:
    tree = OUT / "tree"
    shutil.rmtree(tree, ignore_errors=True)
    files = subprocess.run(["git", "-C", str(REPO), "ls-files", "-z", "-co", "--exclude-standard"],
                           capture_output=True, text=True, check=True).stdout.split("\0")
    for f in files:
        if f and not f.startswith("data/") and (REPO / f).is_file():
            (tree / f).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(REPO / f, tree / f)
    shutil.copytree(REPO / "web/dist", tree / "web/dist")
    return tree


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


def distro(key: str, tree: Path) -> dict:
    image, prep = IMAGES[key]
    name, caches = f"winston-setup-{key}", OUT / "caches"
    (caches / "uv").mkdir(parents=True, exist_ok=True)
    (caches / "hf").mkdir(parents=True, exist_ok=True)
    result = {"distro": key}
    with (OUT / f"{key}.log").open("w") as f:
        subprocess.run(["podman", "rm", "-f", name], capture_output=True, check=False)
        subprocess.run(["podman", "run", "-d", "--name", name, "-v", f"{caches / 'uv'}:/root/.cache/uv",
                        "-v", f"{caches / 'hf'}:/root/.cache/huggingface", image, "sleep", "infinity"],
                       capture_output=True, check=True)
        try:
            t0 = time.monotonic()
            ok, _ = timed(f, name, "curl and CA certificates", prep)
            ex(name, f"mkdir -p {Path(APP).parent}")
            subprocess.run(["podman", "cp", str(tree), f"{name}:{APP}"], check=True)
            ok = ok and timed(f, name, "uv", f"curl -LsSf https://astral.sh/uv/{UV}/install.sh | sh")[0]
            ok = ok and timed(f, name, "uv sync", f"cd {APP} && /root/.local/bin/uv sync --frozen")[0]
            ok, said = timed(f, name, "winston setup --yes --profile hosted",
                             f"cd {APP} && PATH=/root/.local/bin:$PATH .venv/bin/winston setup --yes --profile hosted") \
                if ok else (False, "")
            result["setup"] = said
            if ok:
                ex(name, f"{APP}/.venv/bin/python -c '{FAKE_BRAIN}' /root/.local/share/winston")
                subprocess.run(["podman", "exec", "-d", name, f"{APP}/.venv/bin/python", f"{APP}/tests/fake_openai.py",
                                "9999"], check=True)
                subprocess.run(["podman", "exec", "-d", name, "sh", "-c",
                                f"cd {APP} && PATH=/root/.local/bin:$PATH .venv/bin/winston up > /root/up.log 2>&1"],
                               check=True)
                log(f, "--- winston up, until every engine is healthy")
                ok, result["states"] = wait_healthy(f, name, f"{APP}/.venv/bin/python", 20)
                result["healthy_mins"] = round((time.monotonic() - t0) / 60, 1)
                timed(f, name, "winston status", f"cd {APP} && .venv/bin/winston status")
                said = timed(f, name, "winston talk", f"cd {APP} && .venv/bin/winston talk 'Good evening'")[1]
                ok = ok and "Everything is in order" in said      # tests/fake_openai.py's reply, through his brain
                if not ok:
                    timed(f, name, "up.log", "tail -60 /root/up.log")
            result["ok"] = ok

            # The root commands for an NVIDIA profile, as setup gives them here.
            got, cmds = timed(f, name, "root steps for an NVIDIA profile", f"{APP}/.venv/bin/python -c '{ROOT_STEPS}'")
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


def systemd(tree: Path) -> dict:
    """setup as a user, then logged out: the service lingers on."""
    name, home = "winston-setup-systemd", "/home/tester"
    app, data = f"{home}/.local/share/winston/app/dev", f"{home}/.local/share/winston"
    result = {"distro": "systemd"}
    with (OUT / "systemd.log").open("w") as f:
        subprocess.run(["podman", "build", "-t", "winston-setup-systemd", "-f", "-", str(OUT)],
                       input=SYSTEMD_IMAGE, text=True, capture_output=True, check=True)
        subprocess.run(["podman", "rm", "-f", name], capture_output=True, check=False)
        subprocess.run(["podman", "run", "-d", "--name", name, "--systemd=always", "winston-setup-systemd"],
                       capture_output=True, check=True)
        try:
            timed(f, name, "systemd up", "systemctl is-system-running --wait", timeout=120)
            ex(name, f"mkdir -p {Path(app).parent}")
            subprocess.run(["podman", "cp", str(tree), f"{name}:{app}"], check=True)
            ex(name, f"chown -R tester:tester {home}/.local")
            path = f"export PATH={home}/.local/bin:$PATH; cd {app}"
            ok = timed(f, name, "uv", f"curl -LsSf https://astral.sh/uv/{UV}/install.sh | sh", user="tester")[0]
            ok = ok and timed(f, name, "uv sync", f"{path} && uv sync --frozen", user="tester")[0]
            ok = ok and timed(f, name, "winston init --profile hosted, the fake brain",
                              f"{path} && .venv/bin/winston init --profile hosted && "
                              f".venv/bin/python -c '{FAKE_BRAIN}' {data}", user="tester")[0]
            subprocess.run(["podman", "exec", "-d", name, f"{app}/.venv/bin/python", f"{app}/tests/fake_openai.py", "9999"],
                           check=True)
            ok, result["setup"] = timed(f, name, "winston setup --yes, in a login session, which then ends",
                                        f"{path} && .venv/bin/winston setup --yes", user="tester") if ok else (False, "")
            time.sleep(20)                         # past the 10 s logind keeps a manager without lingering
            timed(f, name, "sessions after logging out", "loginctl list-sessions --no-legend; "
                  "loginctl show-user tester -p State -p Linger")
            # Lingering: no login left, only systemd's own manager session.
            gone = ex(name, "loginctl show-user tester -p State --value").stdout.strip() == "lingering"
            active = ex(name, "systemctl --user -M tester@ is-active winston").stdout.strip()
            log(f, f"--- logged out, lingering: {gone}; winston.service: {active}")
            ok, result["states"] = wait_healthy(f, name, f"{app}/.venv/bin/python", 20) if ok else (False, "")
            timed(f, name, "the unit", f"cat {home}/.config/systemd/user/winston.service")
            # The control: without lingering, logging out stops him.
            ex(name, "sudo loginctl disable-linger tester", user="tester")
            time.sleep(20)                         # logind keeps the user's manager 10 s (UserStopDelaySec)
            stopped = ex(name, "curl -s -m 3 http://127.0.0.1:8700/api/status").returncode != 0
            log(f, f"--- without lingering, logged out: stopped {stopped}")
            result["ok"] = ok and gone and active == "active" and stopped
            if not result["ok"]:
                timed(f, name, "its journal", "journalctl --user-unit winston -M tester@ --no-pager | tail -40; "
                      "journalctl -u user@1000 --no-pager | tail -20")
        finally:
            subprocess.run(["podman", "rm", "-f", "-t", "5", name], capture_output=True, check=False)
    return result


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    wanted = [a for a in sys.argv[1:] if not a.startswith("--") and a != str(OUT)] or [*IMAGES, "systemd"]
    tree = stage()
    results = [systemd(tree) if k == "systemd" else distro(k, tree) for k in wanted]
    (OUT / "results.json").write_text(json.dumps(results, indent=1))
    for r in results:
        print(f"{'ok  ' if r.get('ok') else 'FAIL'} {r['distro']}: {r.get('states', '')} {r.get('healthy_mins', '')} "
              f"min, root {r.get('root', '')}")
    sys.exit(0 if all(r.get("ok") for r in results) else 1)
