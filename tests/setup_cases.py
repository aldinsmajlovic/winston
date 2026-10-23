"""`winston setup` (src/winston/setup.py): reading the computer from recorded `os-release` (each
distribution's image), `nvidia-smi` and `docker info` (this desktop's, and Docker's CLI with no daemon and
with a socket it may not open), the profile it picks, the root steps with each distribution's commands, the
data directory and the service file. Nothing here runs a root command; tests/setup_distros.py does, in
containers.

    uv run python tests/setup_cases.py
"""
import os
import sys
from pathlib import Path

from harness import check, finish_checks, scratch_data

scratch = scratch_data("winston-setup-data-")

import yaml  # noqa: E402

from winston import setup  # noqa: E402
from winston.config import repo_root  # noqa: E402
from winston.config.presets import apply_profile  # noqa: E402

FIX = Path(__file__).parent / "fixtures/setup"


def release(name: str) -> dict:
    return setup.os_release((FIX / f"os-release-{name}").read_text())


# ---- Which distribution ----
check("each distribution's package manager, and the one it follows",
      {n: setup.family(release(n)) for n in ("debian-13", "ubuntu-24.04", "fedora-44", "leap-16.0", "archlinux")},
      {"debian-13": ("apt", "debian"), "ubuntu-24.04": ("apt", "ubuntu"), "fedora-44": ("dnf", "fedora"),
       "leap-16.0": ("zypper", "opensuse"), "archlinux": ("pacman", "arch")})
check("a quoted name is read without its quotes", release("leap-16.0")["PRETTY_NAME"], "openSUSE Leap 16.0")
check("a derivative goes by what it is like", [setup.family(setup.os_release(t)) for t in (
    'ID=linuxmint\nID_LIKE="ubuntu debian"\n', "ID=cachyos\nID_LIKE=arch\n", "ID=nixos\n")],
    [("apt", "ubuntu"), ("pacman", "arch"), ("", "")])

# ---- The graphics card ----
desk = setup.gpus((FIX / "nvidia-smi-3090.csv").read_text())
check("nvidia-smi: name, memory and driver", desk, [("NVIDIA GeForce RTX 3090", 24576, "615.78.08")])
check("its error text is no card", setup.gpus("NVIDIA-SMI has failed because it couldn't communicate with the "
                                               "NVIDIA driver. Make sure that the latest NVIDIA driver is installed and running.\n"), [])


def machine(**kw) -> setup.Machine:
    base = {"debian": ("apt", "debian-13"), "arch": ("pacman", "archlinux")}[kw.pop("distro", "debian")]
    m = setup.Machine("tester", False, True, release(base[1]), base[0], kw.pop("base", base[1].split("-")[0]))
    m.docker_bin, m.docker, m.systemd = True, "ok", True
    for k, v in kw.items():
        setattr(m, k, v)
    return m


check("a 24 GB card gets the 27B", setup.pick(machine(gpus=desk)), "nvidia-24gb-27b")
check("so does a 4090's 24564 MiB", setup.pick(machine(gpus=[("NVIDIA GeForce RTX 4090", 24564, "580.95.05")])),
      "nvidia-24gb-27b")
check("a smaller card, or none, gets Ollama",
      [setup.pick(machine(gpus=[("NVIDIA GeForce RTX 4060", 8188, "580.95.05")])), setup.pick(machine())],
      ["ollama", "ollama"])

# ---- Docker ----
nvidia = setup.docker_state((FIX / "docker-info-nvidia.json").read_text(), "")
check("docker info: running, its runtimes and where its images go", nvidia,
      ("ok", ["io.containerd.runc.v2", "nvidia", "runc"], "/var/lib/docker"))
check("no daemon is down, though the CLI exits 0", setup.docker_state(
    (FIX / "docker-info-down.json").read_text(), (FIX / "docker-info-down.err").read_text()), ("down", [], ""))
check("a socket it may not open is denied", setup.docker_state(
    (FIX / "docker-info-denied.json").read_text(), (FIX / "docker-info-denied.err").read_text()), ("denied", [], ""))

# ---- What each profile needs, and the root steps ----
example = yaml.safe_load((repo_root() / "examples/config.yaml").read_text())
check("what each profile needs from the computer",
      {p: setup.needs(apply_profile(example, p)) for p in ("nvidia-24gb", "nvidia-24gb-27b", "ollama", "hosted")},
      {"nvidia-24gb": {"docker", "nvidia"}, "nvidia-24gb-27b": {"docker", "nvidia"}, "ollama": {"ollama"},
       "hosted": set()})

bare = machine(docker_bin=False, docker="down")
keys = lambda m, need: [s.key for s in setup.steps(m, need)]  # noqa: E731
check("an NVIDIA profile on a bare Debian", keys(bare, {"docker", "nvidia"}),
      ["docker", "docker-group", "nvidia", "serial", "linger"])
deb = {s.key: s.cmds for s in setup.steps(bare, {"docker", "nvidia"})}
check("Debian's Docker, enabled and started", deb["docker"],
      ["apt-get update", "apt-get install -y docker.io", "systemctl enable --now docker"])
check("NVIDIA's apt repository with its key, then Docker told about the runtime",
      (deb["nvidia"][2].startswith("curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | gpg --dearmor"),
       deb["nvidia"][-3:]),
      (True, ["apt-get install -y nvidia-container-toolkit", "nvidia-ctk runtime configure --runtime=docker",
              "systemctl restart docker"]))
check("the serial group is dialout, made if missing", deb["serial"],
      ["getent group dialout >/dev/null || groupadd -r dialout", "usermod -aG dialout tester"])
arch = {s.key: s.cmds for s in setup.steps(machine(distro="arch", docker_bin=False, serial_group="uucp"),
                                           {"docker", "nvidia"})}
check("Arch: pacman for both, and its serial group is uucp",
      (arch["docker"][0], arch["nvidia"][0], arch["serial"][1]),
      ("pacman -S --needed --noconfirm docker", "pacman -S --needed --noconfirm nvidia-container-toolkit",
       "usermod -aG uucp tester"))
check("each distribution has its commands",
      sorted(k for k in setup.DOCKER if setup.TOOLKIT.get(k)), ["apt", "dnf", "pacman", "zypper"])

ready = machine(runtimes=["nvidia", "runc"], docker_member=True, in_serial=True, linger=True)
check("nothing to do on a machine that has it all", keys(ready, {"docker", "nvidia"}), [])
check("hosted needs no root but the serial group and lingering", keys(machine(), set()), ["serial", "linger"])
check("Ollama missing for the Ollama profile", keys(machine(in_serial=True, linger=True), {"ollama"}), ["ollama"])
check("Docker there but stopped is started", keys(machine(docker="down", docker_member=True, runtimes=["nvidia"],
                                                          in_serial=True, linger=True), {"docker", "nvidia"}),
      ["docker-start"])
check("in the docker group from the next login: not added again",
      keys(machine(docker="denied", docker_member=True, runtimes=["nvidia"], in_serial=True, linger=True),
           {"docker", "nvidia"}), [])
check("IPv6 that never connects is fixed first", keys(machine(ipv6_broken=True, in_serial=True, linger=True), set()),
      ["ipv6"])
check("without systemd, no lingering", keys(machine(systemd=False, in_serial=True), set()), [])
check("a distribution without commands here gets no install steps",
      keys(machine(docker_bin=False, docker_member=True, in_serial=True, linger=True, pm=""), {"docker", "nvidia"}), [])

# ---- The data directory and the service ----
os.environ.pop("WINSTON_DATA")
check("from a clone, ./data", setup.target(), repo_root() / "data")
os.environ["WINSTON_DATA"] = str(scratch / "setup")
check("$WINSTON_DATA first", setup.target(), scratch / "setup")
setup.data_dir.cache_clear()
made = setup.make_data_dir("hosted")
cfg = yaml.safe_load((made / "config.yaml").read_text())
check("the data directory has the profile's config, the persona and the voices",
      (made, cfg["engines"]["llm"]["driver"], (made / "persona.md").is_file(), (made / "voices/vctk").is_dir()),
      (scratch / "setup", "external", True, True))
text = setup.unit(made)
check("the service runs this venv's winston up on that data directory, and starts at boot",
      [line in text.splitlines() for line in (
          f'ExecStart="{Path(sys.executable).with_name("winston")}" up', f'Environment="WINSTON_DATA={made}"',
          "Restart=on-failure", "WantedBy=default.target")], [True] * 4)

finish_checks("setup: each distribution read, the profile picked, the root steps per distribution, the data "
              "directory and the service file")
