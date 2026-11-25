"""`winston setup`: reads the computer, picks the profile, makes the data directory, runs what needs root
(each command shown first, run on yes) and installs the systemd user service that runs `winston up`. Also
`winston update` and `winston uninstall` for an install from a release (scripts/install.sh).
The engines are not installed here: the wizard or their first start does it (engines/install.py).
The design is docs/setup.md."""
from __future__ import annotations

import grp
import hashlib
import io
import json
import os
import pwd
import re
import shlex
import shutil
import socket
import subprocess
import sys
import tarfile
from dataclasses import dataclass, field
from pathlib import Path

import typer
import yaml

from winston.config import data_dir, repo_root

HEADROOM_MIB = 1536        # a desktop on the same card, and spikes
# The smallest card (MiB) for each profile, largest first: its brain and voice's peak with both working at once,
# measured on the RTX 3090 on 2026-11-25 (docs/hardware.md), plus the headroom. The brain comes first, until it is
# Granite 4.2 8B: the next step up, the 27B, needs a 24 GB card, so a card between spends the rest on a better voice.
FITS = [("nvidia-24gb", 23000),                   # the 24 GB cards report 24564 or 24576 MiB
        ("nvidia-16gb", 12173 + HEADROOM_MIB),    # Granite 8B and Breeze TTS 2
        ("nvidia-12gb", 10157 + HEADROOM_MIB),    # Granite 8B and Chatterbox-Turbo
        ("ollama", 6796 + HEADROOM_MIB)]          # Granite 8B, Pocket on the CPU


# ---- Talking in the terminal ------------------------------------------------------------------------------
# Colour only on a terminal (click strips it otherwise) and never with NO_COLOR set.

def say(text: str = "", **style) -> None:
    typer.echo(typer.style(text, **style) if style else text, color=False if os.environ.get("NO_COLOR") else None)


def heading(text: str) -> None:
    say()
    say(text, bold=True)


def item(ok: bool | None, text: str) -> None:
    """A line under a heading: a green tick, a yellow '!' for something missing, or nothing."""
    mark = {True: typer.style("✓", fg="green"), False: typer.style("!", fg="yellow"), None: " "}[ok]
    say(f"  {mark} {text}")


def note(text: str, indent: int = 2, **style) -> None:
    """Dim help text, wrapped to the terminal."""
    import textwrap
    width = max(40, min(shutil.get_terminal_size().columns, 100)) - indent
    for line in textwrap.wrap(text, width) or [""]:
        say(" " * indent + line, **({"dim": True} | style))
FAMILIES = {"debian": "apt", "ubuntu": "apt", "fedora": "dnf", "rhel": "dnf", "centos": "dnf",
            "opensuse": "zypper", "arch": "pacman"}
DRIVER_GUIDES = {
    "debian": "https://wiki.debian.org/NvidiaGraphicsDrivers",
    "ubuntu": "https://documentation.ubuntu.com/server/how-to/graphics/install-nvidia-drivers/",
    "fedora": "https://rpmfusion.org/Howto/NVIDIA",
    "opensuse": "https://en.opensuse.org/SDB:NVIDIA_drivers",
    "arch": "https://wiki.archlinux.org/title/NVIDIA",
}
_NV = "https://nvidia.github.io/libnvidia-container"
_KEYRING = "/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg"
# Each as NVIDIA's install guide has it (toolkit 1.20.1, checked 2026-10-23), unpinned; Arch packages its own.
DOCKER = {
    "apt": ["apt-get update", "apt-get install -y docker.io"],
    "dnf": ["dnf install -y moby-engine"],
    "zypper": ["zypper --non-interactive install docker"],
    "pacman": ["pacman -S --needed --noconfirm docker"],
}
TOOLKIT = {
    "apt": ["apt-get update", "apt-get install -y --no-install-recommends ca-certificates curl gnupg2",
            f"curl -fsSL {_NV}/gpgkey | gpg --dearmor --yes -o {_KEYRING}",
            (f"curl -fsSL {_NV}/stable/deb/nvidia-container-toolkit.list | sed 's#deb https://#deb [signed-by={_KEYRING}] "
             "https://#g' > /etc/apt/sources.list.d/nvidia-container-toolkit.list"),
            "apt-get update", "apt-get install -y nvidia-container-toolkit"],
    "dnf": [f"curl -fsSL {_NV}/stable/rpm/nvidia-container-toolkit.repo -o /etc/yum.repos.d/nvidia-container-toolkit.repo",
            "dnf install -y nvidia-container-toolkit"],
    "zypper": [(f"zypper lr nvidia-container-toolkit >/dev/null 2>&1 || zypper --non-interactive ar "
                f"{_NV}/stable/rpm/nvidia-container-toolkit.repo"),
               "zypper --non-interactive --gpg-auto-import-keys install nvidia-container-toolkit"],
    "pacman": ["pacman -S --needed --noconfirm nvidia-container-toolkit"],
}
CONFIGURE = ["nvidia-ctk runtime configure --runtime=docker", "systemctl restart docker"]
# glibc's getaddrinfo then sorts IPv4 first for every program, so nothing waits out an IPv6 address first.
GAI = ("grep -qs '^precedence ::ffff:0:0/96' /etc/gai.conf || "
       "echo 'precedence ::ffff:0:0/96  100' >> /etc/gai.conf")


# ---- Reading the computer ----------------------------------------------------------------------------

def os_release(text: str) -> dict[str, str]:
    out = {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep and not key.startswith("#"):
            out[key.strip()] = " ".join(shlex.split(value))
    return out


def family(release: dict[str, str]) -> tuple[str, str]:
    """(package manager, the distribution it follows): ("apt", "ubuntu") for Linux Mint."""
    for d in [release.get("ID", ""), *release.get("ID_LIKE", "").split()]:
        base = "opensuse" if d.startswith("opensuse") or d == "suse" else d
        if base in FAMILIES:
            return FAMILIES[base], base
    return "", ""


def gpus(csv: str) -> list[tuple[str, int, str]]:
    """(name, MiB, driver) per card, from `nvidia-smi --query-gpu=name,memory.total,driver_version
    --format=csv,noheader,nounits`."""
    out = []
    for line in csv.splitlines():
        cells = [c.strip() for c in line.split(",")]
        if len(cells) == 3 and cells[1].isdigit():
            out.append((cells[0], int(cells[1]), cells[2]))
    return out


def docker_state(out: str, err: str) -> tuple[str, list[str], str]:
    """("ok" | "denied" | "down", runtimes, root folder) from `docker info --format '{{json .}}'`, which
    exits 0 with ServerErrors when it cannot reach the daemon."""
    try:
        info = json.loads(out)
    except ValueError:
        info = {}
    if info.get("ServerErrors") or not info.get("ServerVersion"):
        return ("denied" if "permission denied" in err.lower() else "down"), [], ""
    return "ok", sorted(info.get("Runtimes") or {}), info.get("DockerRootDir") or ""


def _nvidia_on_pci() -> bool:
    for dev in Path("/sys/bus/pci/devices").glob("*"):
        try:
            if (dev / "vendor").read_text().strip() == "0x10de" and (dev / "class").read_text().startswith("0x03"):
                return True
        except OSError:
            pass
    return False


def _ipv6_broken(host: str = "huggingface.co") -> bool:
    """IPv6 addresses sort first and never connect while IPv4 does, as behind a VPN that drops IPv6:
    Python's plain HTTP then waits out each IPv6 address before it tries IPv4."""
    try:
        found = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError:
        return False
    if not found or found[0][0] != socket.AF_INET6:
        return False

    def connects(fam) -> bool:
        addr = next((a[4][:2] for a in found if a[0] == fam), None)
        if not addr:
            return False
        try:
            socket.create_connection(addr, timeout=3).close()
            return True
        except OSError:
            return False
    return not connects(socket.AF_INET6) and connects(socket.AF_INET)


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        return subprocess.CompletedProcess(cmd, 127, "", str(e))


def _member(group: str, user: str) -> bool:
    """In the group as /etc/group has it, which a login takes up only when it starts."""
    try:
        g = grp.getgrnam(group)
        return user in g.gr_mem or pwd.getpwnam(user).pw_gid == g.gr_gid
    except KeyError:
        return False


def _answers(port: int) -> bool:
    try:
        socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
        return True
    except OSError:
        return False


@dataclass
class Machine:
    user: str
    root: bool
    sudo: bool
    release: dict[str, str]
    pm: str                      # apt | dnf | zypper | pacman, "" for one setup has no commands for
    base: str                    # the distribution it follows, for the driver guide
    gpus: list[tuple[str, int, str]] = field(default_factory=list)
    nvidia_card: bool = False    # an NVIDIA display device, driver or not
    docker_bin: bool = False
    docker: str = "down"         # ok | denied | down
    runtimes: list[str] = field(default_factory=list)
    docker_root: str = ""
    docker_member: bool = False  # in the docker group, maybe not in this login yet
    ollama: bool = False
    serial_group: str = "dialout"
    in_serial: bool = False
    systemd: bool = False
    linger: bool = False
    ipv6_broken: bool = False


def read() -> Machine:
    user = pwd.getpwuid(os.getuid()).pw_name
    try:
        release = os_release(Path("/etc/os-release").read_text())
    except OSError:
        release = {}
    pm, base = family(release)
    m = Machine(user, os.geteuid() == 0, bool(shutil.which("sudo")), release, pm, base)
    if shutil.which("nvidia-smi"):
        m.gpus = gpus(_run(["nvidia-smi", "--query-gpu=name,memory.total,driver_version",
                            "--format=csv,noheader,nounits"]).stdout)
    m.nvidia_card = bool(m.gpus) or _nvidia_on_pci()
    m.docker_bin = bool(shutil.which("docker"))
    if m.docker_bin:
        r = _run(["docker", "info", "--format", "{{json .}}"])
        m.docker, m.runtimes, m.docker_root = docker_state(r.stdout, r.stderr)
    try:
        m.runtimes += ["nvidia"] if "nvidia" in Path("/etc/docker/daemon.json").read_text() else []
    except OSError:
        pass
    m.docker_member = m.root or _member("docker", user)
    m.ollama = bool(shutil.which("ollama")) or _answers(11434)
    m.serial_group = next((g for g in ("dialout", "uucp") if _group_exists(g)), "dialout")
    m.in_serial = m.root or _member(m.serial_group, user)
    m.systemd = Path("/run/systemd/system").is_dir() and bool(shutil.which("systemctl"))
    m.linger = Path(f"/var/lib/systemd/linger/{user}").exists()
    m.ipv6_broken = _ipv6_broken()
    return m


def have_now() -> set[str]:
    """What presets can need (their `needs`) that this computer has now, for the pages to warn about the rest.
    A quick read: no network, no group lookups."""
    out = {"internet"}
    if shutil.which("nvidia-smi") and gpus(_run(["nvidia-smi", "--query-gpu=name,memory.total,driver_version",
                                                 "--format=csv,noheader,nounits"]).stdout):
        out.add("cuda")
    if shutil.which("docker"):
        r = _run(["docker", "info", "--format", "{{json .}}"])
        state, runtimes, _ = docker_state(r.stdout, r.stderr)
        out |= {"docker"} if state == "ok" else set()
        out |= {"nvidia"} if state == "ok" and "nvidia" in runtimes else set()
    if shutil.which("ollama") or _answers(11434):
        out.add("ollama")
    from winston.config.memory import amd
    if amd() and Path("/dev/dri").exists():
        out.add("amd")
    return out


def _group_exists(name: str) -> bool:
    try:
        grp.getgrnam(name)
        return True
    except KeyError:
        return False


def _free(path: Path) -> str:
    while not path.exists():
        path = path.parent
    return f"{shutil.disk_usage(path).free / 1e9:.0f} GB free"


def _home(p: Path | str) -> str:
    """A path with the home directory as ~, shorter to read."""
    p, home = str(p), str(Path.home())
    return "~" + p[len(home):] if p == home or p.startswith(home + "/") else p


def describe(m: Machine) -> list[tuple[bool | None, str]]:
    """What setup found, each with whether it is fine (True), missing (False) or just so (None)."""
    lines: list[tuple[bool | None, str]] = [(None, m.release.get("PRETTY_NAME") or "An unknown Linux")]
    for name, mib, driver in m.gpus:
        lines.append((True, f"{name}, {mib / 1024:.0f} GB, driver {driver}"))
    if m.nvidia_card and not m.gpus:
        lines.append((False, "An NVIDIA graphics card without its driver (no nvidia-smi)"))
    if not m.nvidia_card:
        lines.append((None, "No NVIDIA graphics card"))
    if not m.docker_bin:
        lines.append((None, "Docker: not installed"))
    else:
        lines.append({"ok": (True, "Docker" + (", with the NVIDIA runtime" if "nvidia" in m.runtimes else "")
                             + (f", images on {m.docker_root} ({_free(Path(m.docker_root))})" if m.docker_root else "")),
                      "denied": (False, f"Docker: installed, but {m.user} may not use it"),
                      "down": (False, "Docker: installed, not running")}[m.docker])
    lines.append((True, "Ollama") if m.ollama else (None, "Ollama: not installed"))
    hf = Path(os.environ.get("HF_HOME") or Path.home() / ".cache/huggingface")
    lines.append((True, f"Models go to {_home(hf)} ({_free(hf)})"))
    lines.append((True, "USB serial, for setting up a Voice PE") if m.in_serial
                 else (False, f"USB serial: {m.user} is not in the {m.serial_group} group"))
    if not m.systemd:
        lines.append((False, "No systemd running: no service, start him with `winston up`"))
    elif not m.linger:
        lines.append((False, "Lingering is off, so a service stops when you log out"))
    if m.ipv6_broken:
        lines.append((False, "IPv6: its addresses come first here and never connect"))
    return lines


# ---- What to do --------------------------------------------------------------------------------------

def pick(m: Machine) -> str:
    """The profile for the largest NVIDIA card; without one, Ollama, which sizes itself to what it finds."""
    return pick_for(max(mib for _, mib, _ in m.gpus)) if m.gpus else "ollama"


def pick_for(card: int) -> str:
    """The profile for an NVIDIA card of card MiB."""
    return next((p for p, need in FITS if card >= need), "nvidia-8gb")


def needs(config: dict) -> set[str]:
    """What the config's engines need from the computer: docker, nvidia, ollama."""
    from winston.api.options import llm_engine
    out, engines = set(), config.get("engines") or {}
    for e in engines.values():
        if e.get("enabled", True) and e.get("driver") == "docker":
            out.add("docker")
            if e.get("gpu") or e.get("runtime") == "nvidia":
                out.add("nvidia")
    if (llm := engines.get("llm")) and llm.get("enabled", True) and llm_engine(llm) == "ollama":
        out.add("ollama")
    return out


@dataclass
class Step:
    key: str
    title: str
    cmds: list[str]
    skipped: str     # what then does not work


def steps(m: Machine, need: set[str]) -> list[Step]:
    out, pm, user = [], m.pm, m.user
    if m.ipv6_broken:
        out.append(Step("ipv6", "Prefer IPv4 on this computer", [GAI],
                        "every download waits over a minute first, for IPv6 addresses that never connect"))
    if "docker" in need:
        if not m.docker_bin and pm:
            out.append(Step("docker", "Install Docker", [*DOCKER[pm], "systemctl enable --now docker"],
                            "the brain cannot run: it runs in Docker"))
        elif m.docker_bin and m.docker == "down":
            out.append(Step("docker-start", "Start Docker", ["systemctl enable --now docker"],
                            "the brain cannot run: it runs in Docker"))
        if not m.docker_member:
            out.append(Step("docker-group", f"Let {user} use Docker", [f"usermod -aG docker {user}"],
                            "Winston cannot start the brain"))
    if "nvidia" in need and "nvidia" not in m.runtimes and pm:
        out.append(Step("nvidia", "Install NVIDIA's container toolkit", [*TOOLKIT[pm], *CONFIGURE],
                        "the brain cannot use the graphics card"))
    if "ollama" in need and not m.ollama:
        out.append(Step("ollama", "Install Ollama", ["curl -fsSL https://ollama.com/install.sh | sh"],
                        "the brain has nothing to run on"))
    if not m.in_serial:
        g = m.serial_group
        out.append(Step("serial", f"Let {user} use USB serial devices",
                        [f"getent group {g} >/dev/null || groupadd -r {g}", f"usermod -aG {g} {user}"],
                        "a Voice PE cannot be set up over USB"))
    if m.systemd and not m.linger:
        out.append(Step("linger", f"Keep {user}'s services running after logging out", [f"loginctl enable-linger {user}"],
                        "Winston stops when you log out"))
    return out


def _do(step: Step, m: Machine, yes: bool) -> bool:
    heading(step.title)
    note("Needs root. This runs:")
    for c in step.cmds:
        say(f"    {c}", fg="cyan")
    if not m.root and not m.sudo:
        note(f"There is no sudo here: run those as root, then `winston setup` again. Until then {step.skipped}.",
             fg="yellow")
        return False
    if not (yes or typer.confirm("  Run it?", default=True)):
        note(f"Skipped, so {step.skipped}.", fg="yellow")
        return False
    for c in step.cmds:
        if c.startswith("systemctl") and not m.systemd:
            note(f"(no systemd here, so not: {c})")
            continue
        r = subprocess.run(["sh", "-c", c] if m.root else ["sudo", "sh", "-c", c], check=False)
        if r.returncode:
            note(f"That failed ({c}, exit {r.returncode}), so {step.skipped}.", fg="yellow")
            return False
    item(True, "Done")
    return True


# ---- The data directory and the service -----------------------------------------------------------------

def target() -> Path:
    """$WINSTON_DATA, else ./data in a clone, else ~/.local/share/winston."""
    if env := os.environ.get("WINSTON_DATA"):
        return Path(env).expanduser().resolve()
    if (repo_root() / ".git").exists():
        return repo_root() / "data"
    return Path.home() / ".local/share/winston"


def make_data_dir(profile: str, force: bool = False, print=print, llm: dict | None = None) -> Path:   # setup: quiet
    """The data directory from the examples: the profile's config, the persona, the bundled voices."""
    from winston.config.presets import apply_profile
    from winston.config.schema import WinstonConfig
    d = data_dir()
    d.mkdir(parents=True, exist_ok=True)
    ex = repo_root() / "examples"
    for name in ("config.yaml", "persona.md", ".env"):
        src, dst = ex / (name if name != ".env" else "env.example"), d / name
        if dst.exists() and not force:
            print(f"keep   {dst}")
        elif name == "config.yaml" and (profile != "nvidia-24gb" or llm):
            cfg = apply_profile(yaml.safe_load(src.read_text()), profile)
            if llm:                                         # the model and context memory.recommend picked here
                cfg = {**cfg, "llm": {**cfg.get("llm", {}), **llm}}
            cfg = WinstonConfig.model_validate(cfg)
            dst.write_text(yaml.safe_dump(cfg.model_dump(mode="json", exclude_none=True), sort_keys=False,
                                          allow_unicode=True))
            print(f"create {dst} (profile {profile})")
        else:
            shutil.copy(src, dst)
            print(f"create {dst}")
    for sub in ("memory", "logs", "voices"):
        (d / sub).mkdir(exist_ok=True)
    for tmpl in (ex / "memory").glob("*.md"):
        if not (d / "memory" / tmpl.name).exists():
            shutil.copy(tmpl, d / "memory" / tmpl.name)
            print(f"create {d / 'memory' / tmpl.name}")
    bundled = repo_root() / "assets/voices/vctk"
    if bundled.is_dir() and not (d / "voices/vctk").exists():
        shutil.copytree(bundled, d / "voices/vctk")
        print(f"create {d / 'voices/vctk'} (CC BY 4.0 VCTK samples)")
    return d


def release_app() -> Path | None:
    """Where install.sh puts each release (~/.local/share/winston-app), when this is one; None in a clone.
    `current` may already point at another release beside this one: `winston update` moves it, then goes on."""
    current = repo_root().parent / "current"
    return current.parent if current.is_symlink() and current.resolve().parent == current.parent else None


def unit(data: Path) -> str:
    exe, work = Path(sys.executable).with_name("winston"), repo_root()    # this venv's, as `uv run` has it
    if app := release_app():                     # through `current`, which `winston update` moves
        work = app / "current"
        exe = work / ".venv/bin/winston"
    return (
        "[Unit]\nDescription=Winston, a self-hosted voice assistant\n"
        "Documentation=https://github.com/aldinsmajlovic/winston/blob/main/docs/setup.md\n\n"
        f'[Service]\nExecStart="{exe}" up\nWorkingDirectory={work}\n'
        f'Environment="WINSTON_DATA={data}"\n'
        f'Environment="PATH={os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")}"\n'   # uv, docker, ollama
        "Restart=on-failure\nRestartSec=5\n\n"
        "[Install]\nWantedBy=default.target\n")


def _unit_path() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "systemd/user/winston.service"


def _service(data: Path, start: bool) -> bool:
    path = _unit_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(unit(data))
    for cmd in (["systemctl", "--user", "daemon-reload"],
                ["systemctl", "--user", "enable", *(["--now"] if start else []), "winston.service"]):
        r = _run(cmd)
        if r.returncode:
            item(False, f"The service is in {_home(path)}, but `{' '.join(cmd)}` failed: "
                        f"{(r.stderr or r.stdout).strip()}")
            return False
    item(True, f"The service {path.name} is enabled" + (" and starting." if start else "."))
    return True


def _lan_address() -> str | None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))           # no packet is sent; it picks the outgoing address
            return s.getsockname()[0]
    except OSError:
        return None


def _winstond(port: int) -> bool:
    import httpx
    try:
        return "engines" in httpx.get(f"http://127.0.0.1:{port}/api/status", timeout=2).json()
    except (httpx.HTTPError, ValueError):
        return False


def run(yes: bool, profile: str | None) -> None:
    m = read()
    say("Winston setup", bold=True)
    heading("This computer")
    for ok, line in describe(m):
        item(ok, line)

    data = target()
    os.environ["WINSTON_DATA"] = str(data)
    data_dir.cache_clear()
    from winston.api import setup_link
    config = data / "config.yaml"
    fresh = not config.is_file()
    if not fresh:
        heading("Your settings")
        item(True, f"Kept, in {_home(data)}")
        note("The brain and voice stay as they are; change them on his Brain and Voice pages. To start over with "
             "the wizard instead: `winston uninstall --data`, then install again.")
    else:
        from winston.config.presets import profile as get_profile
        chosen = get_profile(profile or pick(m))              # a typo in --profile fails before anything is made
        llm = None
        if not profile:          # the brain sized and the context filled last, for the card or memory free now
            from winston.config import memory
            mem = memory.here()
            rec = memory.recommend(mem.total, memory.other(mem) or 0, mem.kind)
            llm = {"model": rec["model"], "context": rec["context"]}
        make_data_dir(chosen["id"], print=lambda *a, **k: None, llm=llm)
        heading("Your settings")
        item(True, f"Made in {_home(data)}, starting from {chosen['title']}"
                   + ("" if profile else ", which suits this computer"))
        note("You pick the brain, the voice and the hearing on the setup page. They can be changed any time "
             "later on the Brain and Voice pages; if a pick there needs something installed as root, the page "
             "says so and `winston setup system` installs it.")
        if m.nvidia_card and not m.gpus:
            guide = DRIVER_GUIDES.get(m.base, "your distribution's guide")
            note(f"This NVIDIA card needs its driver for a local brain, and setup does not install drivers: {guide}. "
                 "Until then Ollama runs on the processor, or a hosted brain works anywhere.", fg="yellow")
    # Setup mode for a new data directory, and a new link each run until the wizard is done. Before the service
    # starts, which then listens on the network for it.
    key = setup_link.new_key() if fresh or setup_link.active() else None
    cfg = yaml.safe_load(config.read_text()) or {}

    need = needs(cfg)
    done = {s.key for s in steps(m, need) if _do(s, m, yes)}
    if "docker" in need and not m.root and ("docker-group" in done or (m.docker == "denied" and m.docker_member)):
        say()
        note("You can use Docker from your next login. Log out and back in (or restart the computer), then run "
             "`winston setup` again: it carries on from here.", fg="yellow")
        return

    control = cfg.get("control") or {}
    port, host = control.get("port", 8700), control.get("host", "127.0.0.1")
    running, taken = _winstond(port), _answers(port)
    heading("Running him")
    if running:
        item(True, f"Winston is already running on port {port}, so the service waits for the next start.")
    elif taken:
        item(False, f"Something else answers on port {port}; Winston needs it (control.port in {_home(config)}).")
    else:
        busy = [f"{p} ({name})" for name, e in (cfg.get("engines") or {}).items()
                if e.get("enabled", True) and e.get("driver") != "external" and (p := e.get("port")) and _answers(p)]
        if busy:
            item(False, "Already in use, so these cannot start until they are free: " + ", ".join(busy))
    service = m.systemd and _service(data, start=not taken)

    heading("Next: open the setup page" if key else "Next: open Winston")
    if not (service or running):
        note("Start him with `winston up` first.")
    if key:
        for line in _links(key, port):
            say(f"  {line}")
        return
    say("  " + typer.style(f"http://localhost:{port}", fg="cyan", bold=True) + "  on this computer")
    if host not in ("127.0.0.1", "localhost", "::1") and (ip := _lan_address()):
        say("  " + typer.style(f"http://{ip}:{port}", fg="cyan", bold=True) + "  from another device here")


SYSTEM_KEYS = ("docker", "docker-start", "docker-group", "nvidia", "ollama")   # the steps engines need


def system(yes: bool) -> None:
    """`winston setup system`: the root steps for what the saved brain, voice and hearing run, after a pick on
    the pages that needs something this computer lacks. Settings are not touched."""
    config = target() / "config.yaml"
    if not config.is_file():
        sys.exit("Nothing is set up here yet: run `winston setup` first.")
    m = read()
    todo = [s for s in steps(m, needs(yaml.safe_load(config.read_text()) or {})) if s.key in SYSTEM_KEYS]
    if not todo:
        item(True, "This computer has what the saved brain and voice need.")
        return
    done = {s.key for s in todo if _do(s, m, yes)}
    say()
    if "docker-group" in done and not m.root:
        note("You can use Docker from your next login: restart the computer (the service needs it too), then "
             "start the brain on Status.", fg="yellow")
    elif done:
        item(True, "Done. Start the brain or voice again on Status.")


def _links(key: str, port: int) -> list[str]:
    """The setup link for this computer, which needs no key, and for another device."""
    url = lambda u: typer.style(u, fg="cyan", bold=True)                       # noqa: E731
    out = [url(f"http://localhost:{port}/setup") + "  on this computer"]
    if ip := _lan_address():
        out.append(url(f"http://{ip}:{port}/setup?key={key}") + "  from any device here")
    return out


def link() -> None:
    """`winston setup link`: a new key while setup is under way; the link before stops working."""
    from winston.api import setup_link
    data = target()
    os.environ["WINSTON_DATA"] = str(data)
    data_dir.cache_clear()
    if not setup_link.active():
        sys.exit("No setup is under way here, so there is no setup link: the page follows Who can open this page, "
                 "on Status.")
    try:
        port = (yaml.safe_load((data / "config.yaml").read_text()) or {}).get("control", {}).get("port", 8700)
    except OSError:
        port = 8700
    say("Open", bold=True)
    for line in _links(setup_link.new_key(), port):
        say(f"  {line}")
    note("The link before this one no longer works.")


# ---- Updating and removing --------------------------------------------------------------------------------

RELEASES = "https://github.com/aldinsmajlovic/winston/releases"
ROOT_LEFT = ("What setup ran as root stays, whichever of these it ran: Docker and NVIDIA's toolkit, the docker and "
             "serial groups, lingering (sudo loginctl disable-linger {user}), IPv4 first in /etc/gai.conf")


def sums(version: str | None) -> tuple[str, dict[str, str]]:
    """A release's address and its SHA256SUMS (file name: digest): the latest, or `version`.
    WINSTON_RELEASES gets it from somewhere other than GitHub, as for install.sh."""
    import httpx
    base = os.environ.get("WINSTON_RELEASES", RELEASES)
    url = f"{base}/download/v{version}" if version else f"{base}/latest/download"
    r = httpx.get(f"{url}/SHA256SUMS", follow_redirects=True, timeout=30)
    r.raise_for_status()
    return url, {name: digest for digest, _, name in (line.partition("  ") for line in r.text.splitlines()) if name}


def _tarball(url: str, files: dict[str, str]) -> tuple[str, str, str]:
    for name, digest in files.items():
        if name.startswith("winston-") and name.endswith(".tar.gz"):
            return name.removeprefix("winston-").removesuffix(".tar.gz"), f"{url}/{name}", digest
    raise ValueError(f"no Winston release in {url}/SHA256SUMS")


def release(version: str | None) -> tuple[str, str, str]:
    """(version, its tarball's address, its SHA-256) from a release's SHA256SUMS: the latest, or `version`."""
    return _tarball(*sums(version))


# ---- What changed: CHANGELOG.md -------------------------------------------------------------------------
_HEAD = re.compile(r"^## (\S+)(?: \(([^)]*)\))?[ \t]*$", re.MULTILINE)     # "## 0.1.2 (2026-11-24)"


def changes(text: str) -> list[dict]:
    """CHANGELOG.md's releases as written, newest first: {version, date, items}, each bullet one line."""
    heads = list(_HEAD.finditer(text))
    out = []
    for h, end in zip(heads, [n.start() for n in heads[1:]] + [len(text)]):
        items: list[str] = []
        for line in text[h.end():end].splitlines():
            if line.startswith("- "):
                items.append(line[2:].strip())
            elif items and line.startswith(" ") and line.strip():
                items[-1] += " " + line.strip()
        out.append({"version": h[1], "date": h[2] or "", "items": items})
    return out


def after(releases: list[dict], version: str) -> list[dict]:
    """The releases newer than `version`; none when it is not among them (going back, or a pre-release)."""
    versions = [r["version"] for r in releases]
    return releases[:versions.index(version)] if version in versions else []


def local_changes() -> list[dict]:
    """This copy's CHANGELOG.md, which each release carries."""
    p = repo_root() / "CHANGELOG.md"
    return changes(p.read_text()) if p.is_file() else []


def latest() -> tuple[str, list[dict]]:
    """The latest release's version and its CHANGELOG.md, checked against its SHA256SUMS (no releases listed
    for one made before it shipped the file)."""
    import httpx
    url, files = sums(None)
    version, _, _ = _tarball(url, files)
    if not (digest := files.get("CHANGELOG.md")):
        return version, []
    r = httpx.get(f"{url}/CHANGELOG.md", follow_redirects=True, timeout=30)
    r.raise_for_status()
    if hashlib.sha256(r.content).hexdigest() != digest:
        raise ValueError("its CHANGELOG.md does not match its SHA256SUMS")
    return version, changes(r.text)


# ---- Updating -----------------------------------------------------------------------------------------------
CLONE = "This is a clone: update it with `git pull`, `uv sync` and `pnpm build` (docs/running.md)."


class UpdateError(Exception):
    """Why an update stopped, as the sentence to show; the running release is unchanged."""


def install(version: str | None, say=print) -> str | None:
    """The release beside this one (the latest, or `version`), its packages, and `current` moved to it.
    The data directory is not touched; the release before stays, for going back. Returns the version now
    in `current`, or None when it is this one already. `say` gets each step."""
    import httpx
    from winston import __version__
    from winston.engines.install import _uv, _venv_env
    app = release_app()
    if not app:
        raise UpdateError(CLONE)
    if version and (app / version).is_dir():
        new = version                                # going back to one still here
    else:
        try:
            new, url, digest = release(version)
        except (httpx.HTTPError, ValueError) as e:
            raise UpdateError(f"Could not read the release: {e}") from None
    if new == __version__:
        return None
    dest = app / new
    if not dest.is_dir():
        say(f"Downloading Winston {new}")
        try:
            r = httpx.get(url, follow_redirects=True, timeout=120)
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise UpdateError(f"Could not download it: {e}") from None
        if hashlib.sha256(r.content).hexdigest() != digest:
            raise UpdateError(f"{url.rsplit('/', 1)[1]} does not match its SHA256SUMS, so nothing changed.")
        tmp = app / f".{new}"
        shutil.rmtree(tmp, ignore_errors=True)
        with tarfile.open(fileobj=io.BytesIO(r.content)) as tar:
            tar.extractall(tmp, filter="data")
        (tmp / f"winston-{new}").rename(dest)
        tmp.rmdir()
    say("Installing its packages")
    uv = _uv() or str(Path.home() / ".local/bin/uv")
    if subprocess.run([uv, "sync", "--frozen", "--quiet"], cwd=dest, env=_venv_env(), check=False).returncode:
        raise UpdateError(f"Its packages did not install, so Winston {__version__} stays.")
    link = app / ".current"
    link.unlink(missing_ok=True)
    link.symlink_to(new)
    link.replace(app / "current")                    # at once: the service never sees no `current`
    old = repo_root().name
    for d in app.iterdir():
        if d.name not in (new, old, "current"):
            shutil.rmtree(d, ignore_errors=True)
    return new


def service_running() -> bool:
    return (_unit_path().is_file() and Path("/run/systemd/system").is_dir()
            and _run(["systemctl", "--user", "is-active", "winston.service"]).stdout.strip() == "active")


def restart_service(wait: bool = True) -> str:
    """Restarts the service; "" when it did, else why not. From inside it, `wait=False` only queues the
    restart, since stopping the service ends the process asking."""
    r = _run(["systemctl", "--user", "restart", *([] if wait else ["--no-block"]), "winston.service"])
    return (r.stderr or r.stdout).strip() if r.returncode else ""


def update(version: str | None) -> None:
    """`winston update`: install(), what changed since this one, and the service restarted if it runs."""
    from winston import __version__
    try:
        new = install(version, say=lambda t: say(t, bold=True))
    except UpdateError as e:
        sys.exit(str(e))
    if not new:
        item(True, f"Winston {__version__} is the latest.")
        return
    app = release_app()
    for r in after(changes(t.read_text()) if (t := app / new / "CHANGELOG.md").is_file() else [], __version__):
        heading(f"New in {r['version']}")
        for i in r["items"]:
            note("- " + i, fg=None, dim=False)
    say()
    item(True, f"Winston {new} is installed.")
    note(f"{__version__} stays in {_home(app)}: `winston update {__version__}` goes back.")
    if not (_unit_path().is_file() and Path("/run/systemd/system").is_dir()):
        item(None, "Restart `winston up` to run it.")
    elif not service_running():
        item(None, f"The service is not running; its next start runs {new}.")
    else:
        err = restart_service()
        item(False, f"The service did not restart: {err}") if err else item(True, "The service restarted.")


def leftovers(cfg: dict, data: Path | None) -> list[str]:
    """What uninstalling leaves, each with its size."""
    import httpx
    from winston.api.options import llm_engine, ollama_copy
    from winston.config.presets import presets
    from winston.engines.install import _tree_bytes, gb
    out = [f"The data directory, {_home(data)}: {gb(_tree_bytes(data))} (config, memory, voices, logs)"] if data else []
    engines = cfg.get("engines") or {}
    images = {e.get("image") for e in engines.values()} | {
        (f.get("engine") or {}).get("image") for kind in ("llm", "tts", "stt") for f in presets(kind)}
    if shutil.which("docker"):
        for ref in sorted(i for i in images if i):
            r = _run(["docker", "image", "inspect", "--format", "{{.Size}}", ref])
            if not r.returncode and r.stdout.strip().isdigit():
                out.append(f"Docker's image {ref}: {gb(int(r.stdout))} (docker rmi {ref})")
    llm = engines.get("llm") or {}
    if llm and llm_engine(llm) == "ollama":
        model = (cfg.get("llm") or {}).get("model", "")
        base = (llm.get("url") or f"http://127.0.0.1:{llm.get('port', 11434)}").rstrip("/")
        try:
            tags = httpx.get(f"{base}/api/tags", timeout=3).json().get("models", [])
            copy = ollama_copy(model, (cfg.get("llm") or {}).get("context", 16384))   # his copy, the same files
            out += [f"Ollama's {m['name']}: {gb(m['size'])} (ollama rm {m['name']} {copy})"
                    for m in tags if m.get("name") in (model, f"{model}:latest")]
        except (httpx.HTTPError, ValueError):
            pass
    for what, p in (("The Hugging Face cache", Path(os.environ.get("HF_HOME") or Path.home() / ".cache/huggingface")),
                    ("uv's cache", Path(os.environ.get("UV_CACHE_DIR") or Path.home() / ".cache/uv")),
                    ("uv's Python", Path.home() / ".local/share/uv/python")):
        if p.is_dir():
            out.append(f"{what}, {_home(p)}: {gb(_tree_bytes(p))}" + (", his models and any other program's"
                                                              if what.endswith("Face cache") else ""))
    return out


def uninstall(remove_data: bool, yes: bool) -> None:
    """The service and the code go; the data directory stays unless asked; the rest is listed with its size."""
    app, data, path = release_app(), target(), _unit_path()
    try:
        cfg = yaml.safe_load((data / "config.yaml").read_text()) or {}
    except OSError:
        cfg = {}
    gone = [p for p in (path, app, data if remove_data else None) if p and p.exists()]
    if gone:
        say("This removes", bold=True)
        for p in gone:
            say(f"  {_home(p)}")
    else:
        say("Nothing of Winston's to remove here.")
    if not app:
        note(f"This clone stays: delete {_home(repo_root())} yourself.")
    if gone and not (yes or typer.confirm("Remove it?", default=False)):
        return
    systemd = Path("/run/systemd/system").is_dir()
    if path.exists() and systemd:
        _run(["systemctl", "--user", "disable", "--now", "winston.service"])
    port = (cfg.get("control") or {}).get("port", 8700)
    if _winstond(port):
        sys.exit(f"Winston still answers on port {port}, not as the service: stop `winston up`, then run this again.")
    left = leftovers(cfg, None if remove_data else data)       # now, while the code to measure them is here
    if path.exists():
        path.unlink()
        if systemd:
            _run(["systemctl", "--user", "daemon-reload"])
    if app:
        link = Path.home() / ".local/bin/winston"
        if link.is_symlink() and str(os.readlink(link)).startswith(str(app)):
            link.unlink()
        shutil.rmtree(app)
    if remove_data:
        shutil.rmtree(data, ignore_errors=True)
    item(True, "Removed.")
    heading("Left on this computer")
    note("Delete these yourself if nothing else needs them.")
    for line in left:
        say(f"  - {line}")
    say()
    note(ROOT_LEFT.format(user=pwd.getpwuid(os.getuid()).pw_name) + ".")
