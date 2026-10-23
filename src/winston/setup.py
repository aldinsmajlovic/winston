"""`winston setup`: reads the computer, picks the profile, makes the data directory, runs what needs root
(each command shown first, run on yes) and installs the systemd user service that runs `winston up`.
The engines are not installed here: the wizard or their first start does it (engines/install.py).
The design is docs/setup.md."""
from __future__ import annotations

import grp
import json
import os
import pwd
import shlex
import shutil
import socket
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import typer
import yaml

from winston.config import data_dir, repo_root

GPU_24GB_MIB = 23000       # the 24 GB cards report 24564 or 24576 MiB
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


def describe(m: Machine) -> list[str]:
    lines = [m.release.get("PRETTY_NAME") or "An unknown Linux"]
    for name, mib, driver in m.gpus:
        lines.append(f"{name}, {mib / 1024:.0f} GB, driver {driver}")
    if m.nvidia_card and not m.gpus:
        lines.append("An NVIDIA graphics card without its driver (no nvidia-smi)")
    if not m.nvidia_card:
        lines.append("No NVIDIA graphics card")
    if not m.docker_bin:
        lines.append("Docker: not installed")
    else:
        lines.append({"ok": "Docker" + (", with the NVIDIA runtime" if "nvidia" in m.runtimes else ", no NVIDIA runtime")
                            + (f", images on {m.docker_root} ({_free(Path(m.docker_root))})" if m.docker_root else ""),
                      "denied": f"Docker: installed, but {m.user} may not use it",
                      "down": "Docker: installed, not running"}[m.docker])
    lines.append("Ollama: " + ("there" if m.ollama else "not installed"))
    hf = Path(os.environ.get("HF_HOME") or Path.home() / ".cache/huggingface")
    lines.append(f"Models go to {hf} ({_free(hf)})")
    lines.append(f"USB serial ({m.serial_group}): " + ("yes" if m.in_serial else f"{m.user} is not in the group"))
    if not m.systemd:
        lines.append("No systemd running: no service, start him with `winston up`")
    elif not m.linger:
        lines.append("Lingering: off, so a service stops when you log out")
    if m.ipv6_broken:
        lines.append("IPv6: its addresses come first here and never connect")
    return lines


# ---- What to do --------------------------------------------------------------------------------------

def pick(m: Machine) -> str:
    return "nvidia-24gb-27b" if any(mib >= GPU_24GB_MIB for _, mib, _ in m.gpus) else "ollama"


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
    print(f"\n{step.title}. As root, this runs:")
    for c in step.cmds:
        print(f"    {c}")
    if not m.root and not m.sudo:
        print(f"  There is no sudo here: run those as root, then `winston setup` again. Until then {step.skipped}.")
        return False
    if not (yes or typer.confirm("  Run it?", default=True)):
        print(f"  Skipped: {step.skipped}.")
        return False
    for c in step.cmds:
        if c.startswith("systemctl") and not m.systemd:
            print(f"  (no systemd here, so not: {c})")
            continue
        r = subprocess.run(["sh", "-c", c] if m.root else ["sudo", "sh", "-c", c], check=False)
        if r.returncode:
            print(f"  That failed ({c}, exit {r.returncode}), so {step.skipped}.")
            return False
    return True


def choose(m: Machine, yes: bool) -> str:
    from winston.config.presets import PROFILES
    rec = pick(m)
    if m.nvidia_card and not m.gpus:
        guide = DRIVER_GUIDES.get(m.base, "your distribution's guide")
        print(f"\nThis NVIDIA card needs its driver for a local brain, and setup does not install drivers: {guide}. "
              "Until then Ollama runs on the processor, or a hosted brain works anywhere.")
    if yes:
        return rec
    print("\nWhich setup?")
    for i, p in enumerate(PROFILES, 1):
        print(f"  {i}. {p['title']}{' (recommended)' if p['id'] == rec else ''}: {p['hint']}")
    default = next(i for i, p in enumerate(PROFILES, 1) if p["id"] == rec)
    while True:
        n = typer.prompt("Number", default=str(default))
        if n.isdigit() and 1 <= int(n) <= len(PROFILES):
            return PROFILES[int(n) - 1]["id"]


# ---- The data directory and the service -----------------------------------------------------------------

def target() -> Path:
    """$WINSTON_DATA, else ./data in a clone, else ~/.local/share/winston."""
    if env := os.environ.get("WINSTON_DATA"):
        return Path(env).expanduser().resolve()
    if (repo_root() / ".git").exists():
        return repo_root() / "data"
    return Path.home() / ".local/share/winston"


def make_data_dir(profile: str, force: bool = False) -> Path:
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
        elif name == "config.yaml" and profile != "nvidia-24gb":
            cfg = WinstonConfig.model_validate(apply_profile(yaml.safe_load(src.read_text()), profile))
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


def unit(data: Path) -> str:
    exe = Path(sys.executable).with_name("winston")      # this venv's, as `uv run` and the release have it
    return (
        "[Unit]\nDescription=Winston, a self-hosted voice assistant\n"
        "Documentation=https://github.com/aldinsmajlovic/winston/blob/main/docs/setup.md\n\n"
        f'[Service]\nExecStart="{exe}" up\nWorkingDirectory={repo_root()}\n'
        f'Environment="WINSTON_DATA={data}"\n'
        f'Environment="PATH={os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")}"\n'   # uv, docker, ollama
        "Restart=on-failure\nRestartSec=5\n\n"
        "[Install]\nWantedBy=default.target\n")


def _service(data: Path, start: bool) -> bool:
    path = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "systemd/user/winston.service"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(unit(data))
    for cmd in (["systemctl", "--user", "daemon-reload"],
                ["systemctl", "--user", "enable", *(["--now"] if start else []), "winston.service"]):
        r = _run(cmd)
        if r.returncode:
            print(f"\nThe service is in {path}, but `{' '.join(cmd)}` failed: {(r.stderr or r.stdout).strip()}")
            return False
    print(f"\nThe service {path.name} is enabled" + (" and starting." if start else "."))
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
    print("This computer:\n" + "\n".join(f"  {line}" for line in describe(m)))

    data = target()
    os.environ["WINSTON_DATA"] = str(data)
    data_dir.cache_clear()
    config = data / "config.yaml"
    if config.is_file():
        print(f"\nKeeping {config}: the brain and voice stay as they are (change them on his pages).")
    else:
        from winston.config.presets import profile as get_profile
        if profile:
            get_profile(profile)               # a typo fails before anything is made
        print()
        make_data_dir(profile or choose(m, yes))
    cfg = yaml.safe_load(config.read_text()) or {}

    need = needs(cfg)
    done = {s.key for s in steps(m, need) if _do(s, m, yes)}
    if "docker" in need and not m.root and ("docker-group" in done or (m.docker == "denied" and m.docker_member)):
        print("\nYou can use Docker from your next login. Log out and back in (or restart the computer), then run "
              "`winston setup` again: it carries on from here.")
        return

    control = cfg.get("control") or {}
    port, host = control.get("port", 8700), control.get("host", "127.0.0.1")
    running, taken = _winstond(port), _answers(port)
    if running:
        print(f"\nWinston is already running on port {port}, so the service waits for the next start.")
    elif taken:
        print(f"\nSomething else answers on port {port}; Winston needs it (control.port in {config}).")
    else:
        busy = [f"{p} ({name})" for name, e in (cfg.get("engines") or {}).items()
                if e.get("enabled", True) and e.get("driver") != "external" and (p := e.get("port")) and _answers(p)]
        if busy:
            print("\nAlready in use, so these cannot start until they are free: " + ", ".join(busy))
    service = m.systemd and _service(data, start=not taken)

    also = ""
    if host not in ("127.0.0.1", "localhost", "::1") and (ip := _lan_address()):
        also = f", or http://{ip}:{port} from another device here"
    first = "Open" if service or running else "Start him with `winston up`, then open"
    print(f"\n{first} http://localhost:{port} on this computer{also}.")
