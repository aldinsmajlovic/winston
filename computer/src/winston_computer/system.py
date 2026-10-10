"""What the tools do on this computer: open an app, an address or a file; say how it is doing; read an
allowed log. Linux for now; on another system each says it is not built there yet.

Nothing here takes a command: an app comes from the system's own list of desktop entries and starts
with no arguments, an address must be http or https, and a file must sit in an allowed folder and not
be a program."""
from __future__ import annotations

import configparser
import difflib
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import psutil

from winston_computer.config import Config

# Never opened, wherever they are: they run as programs, or install or launch one.
PROGRAMS = {".desktop", ".sh", ".bash", ".zsh", ".fish", ".csh", ".py", ".pyw", ".pl", ".rb", ".php", ".lua",
            ".appimage", ".run", ".bin", ".elf", ".out", ".deb", ".rpm", ".pkg", ".apk", ".flatpak", ".flatpakref",
            ".snap", ".jar", ".exe", ".msi", ".bat", ".cmd", ".com", ".ps1", ".vbs", ".vbe", ".wsf", ".scr", ".lnk",
            ".url", ".reg", ".app", ".command", ".scpt", ".workflow", ".dmg"}
CANDIDATES = 6
FILE_DEPTH, FILE_SCAN = 6, 20000


class Refused(ValueError):
    """Asked for something the tool does not do; the message says why, for him to say."""


def linux_only() -> None:
    if not sys.platform.startswith("linux"):
        raise Refused(f"This is not built for {platform.system()} yet, only Linux.")


def spawn(argv: list[str]) -> str | None:
    """Starts a launcher in its own session, so what it opens outlives this server. None when it
    started; the error when it failed within two seconds."""
    try:
        proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.PIPE, start_new_session=True)
    except OSError as e:
        return str(e)
    try:
        code = proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        return None                                     # still starting: a launcher that waits for its app
    if code:
        return (proc.stderr.read().decode(errors="replace").strip() if proc.stderr else "") or f"exit {code}"
    return None


# apps

@dataclass
class App:
    id: str            # the desktop file id ("org.mozilla.firefox.desktop")
    name: str
    path: Path
    words: str         # generic name and keywords, for a looser match


def _data_dirs() -> list[Path]:
    """XDG_DATA_HOME, then XDG_DATA_DIRS; without those set, the defaults with Flatpak's and Snap's folders,
    which a session that sets them already lists."""
    home = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    if listed := os.environ.get("XDG_DATA_DIRS"):
        return list(dict.fromkeys([home, *(Path(d) for d in listed.split(":") if d)]))
    return [home, home / "flatpak" / "exports" / "share", Path("/var/lib/flatpak/exports/share"),
            Path("/usr/local/share"), Path("/usr/share"), Path("/var/lib/snapd/desktop")]


def apps() -> list[App]:
    """The apps the desktop shows, from the XDG data folders; the first folder wins for an id, and
    for two with the same name."""
    seen_ids, seen_names, out = set(), set(), []
    for base in _data_dirs():
        folder = base / "applications"
        if not folder.is_dir():
            continue
        for path in sorted(folder.rglob("*.desktop")):
            app_id = str(path.relative_to(folder)).replace("/", "-")
            if app_id in seen_ids:
                continue
            seen_ids.add(app_id)
            parser = configparser.ConfigParser(interpolation=None, strict=False)
            parser.optionxform = str
            try:
                parser.read(path, encoding="utf-8")
                entry = parser["Desktop Entry"]
            except (configparser.Error, KeyError, UnicodeDecodeError):
                continue
            if entry.get("Type") != "Application" or "true" in (entry.get("NoDisplay"), entry.get("Hidden")):
                continue
            if (try_exec := entry.get("TryExec")) and not shutil.which(try_exec):
                continue
            name = entry.get("Name", "").strip()
            if not name or name.casefold() in seen_names:
                continue
            seen_names.add(name.casefold())
            out.append(App(app_id, name, path, " ".join([entry.get("GenericName", ""), entry.get("Keywords", "")])))
    return out


def _norm(text: str) -> str:
    return re.sub(r"[^\w]+", " ", text.casefold()).strip()


def find_app(asked: str, listed: list[App]) -> tuple[App | None, list[App]]:
    """(the app, []) when one fits, else (None, the candidates)."""
    q = _norm(asked)
    stem = lambda a: _norm(a.id.removesuffix(".desktop").rsplit(".", 1)[-1])
    for test in (lambda a: _norm(a.name) == q or stem(a) == q,
                 lambda a: re.search(rf"\b{re.escape(q)}\b", _norm(a.name)) is not None,
                 lambda a: q in _norm(a.name),
                 lambda a: re.search(rf"\b{re.escape(q)}\b", _norm(a.words)) is not None):
        hits = [a for a in listed if test(a)]
        if len(hits) == 1:
            return hits[0], []
        if hits:
            return None, hits[:CANDIDATES]
    close = difflib.get_close_matches(q, [_norm(a.name) for a in listed], n=CANDIDATES, cutoff=0.7)
    return None, [a for a in listed if _norm(a.name) in close]


def open_app(asked: str) -> dict:
    linux_only()
    app, candidates = find_app(asked, apps())
    if app is None:
        if not candidates:
            raise Refused(f"No app called {asked!r} is installed here.")
        return {"opened": None, "which": [a.name for a in candidates],
                "note": "More than one fits; nothing was opened. Ask which."}
    launcher = ["gio", "launch", str(app.path)] if shutil.which("gio") else \
        ["gtk-launch", app.id] if shutil.which("gtk-launch") else None
    if launcher is None:
        raise Refused("Neither gio nor gtk-launch is installed, so apps cannot be started.")
    if error := spawn(launcher):
        raise Refused(f"{app.name} did not start: {error}")
    return {"opened": app.name}


# addresses

def web_address(asked: str) -> str:
    text = asked.strip()
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", text):
        text = "https://" + text                        # "youtube.com"
    parts = urlsplit(text)
    if parts.scheme not in ("http", "https") or not parts.hostname or any(c.isspace() or ord(c) < 32 for c in text):
        raise Refused("Only web addresses (http or https) are opened.")
    return text


def _opener() -> str:
    for cmd in ("xdg-open", "gio"):
        if shutil.which(cmd):
            return cmd
    raise Refused("Neither xdg-open nor gio is installed, so nothing can be opened.")


def open_url(asked: str) -> dict:
    linux_only()
    url = web_address(asked)
    opener = _opener()
    if error := spawn([opener, url] if opener == "xdg-open" else [opener, "open", url]):
        raise Refused(f"The browser did not open: {error}")
    return {"opened": url}


# files

def _roots(cfg: Config) -> list[Path]:
    return [f.resolve() for f in cfg.folders if f.is_dir()]


def _inside(path: Path, roots: list[Path]) -> bool:
    return any(path.is_relative_to(r) for r in roots)


def program(path: Path) -> bool:
    return path.suffix.casefold() in PROGRAMS or os.access(path, os.X_OK)


def find_files(asked: str, cfg: Config) -> list[Path]:
    """Files in the allowed folders that fit: a path inside one, or a name (part of it is enough);
    each resolved through symlinks and kept only if it is still inside."""
    roots = _roots(cfg)
    if not roots:
        raise Refused("No folders are allowed on this computer, so no files open.")
    text = asked.strip()
    if "/" in text or text.startswith("~"):
        p = Path(text).expanduser()
        tries = [p] if p.is_absolute() else [r / p for r in roots]
        found = [t.resolve() for t in tries if t.exists()]
        found = [f for f in found if f.is_file() and _inside(f, roots)]
        if not found:
            raise Refused(f"{asked} is not a file in the folders allowed here.")
        return found
    q, exact, partial, scanned = _norm(Path(text).stem), [], [], 0
    for root in roots:
        for folder, dirs, files in os.walk(root):
            depth = len(Path(folder).relative_to(root).parts)
            dirs[:] = [] if depth >= FILE_DEPTH else [d for d in dirs if not d.startswith(".")]
            for name in files:
                scanned += 1
                if name.startswith(".") or scanned > FILE_SCAN:
                    continue
                stem = _norm(Path(name).stem)
                if stem == q or _norm(name) == _norm(text):
                    exact.append(Path(folder) / name)
                elif q and q in stem:
                    partial.append(Path(folder) / name)
    out = []
    for f in exact or partial:
        r = f.resolve()
        if r.is_file() and _inside(r, roots) and r not in out:
            out.append(r)
    return out


def open_file(asked: str, cfg: Config) -> dict:
    linux_only()
    found = find_files(asked, cfg)
    if not found:
        raise Refused(f"No file like {asked!r} is in the folders allowed here.")
    if len(found) > 1:
        return {"opened": None, "which": [str(f) for f in found[:CANDIDATES]],
                "note": "More than one fits; nothing was opened. Ask which."}
    f = found[0]
    if program(f):
        raise Refused(f"{f.name} is a program or launches one, and those are never opened.")
    opener = _opener()
    if error := spawn([opener, str(f)] if opener == "xdg-open" else [opener, "open", str(f)]):
        raise Refused(f"{f.name} did not open: {error}")
    return {"opened": str(f)}


# status

def _systemctl_failed(user: bool) -> list[str]:
    if not shutil.which("systemctl"):
        return []
    cmd = ["systemctl", *(["--user"] if user else []), "--failed", "--no-legend", "--plain", "--no-pager"]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=10, check=False).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [line.split()[0] for line in out.splitlines() if line.strip()]


def _battery() -> dict | None:
    """The battery that powers the computer; a wireless mouse's or headset's (scope Device) is not one."""
    def read(supply: Path, name: str) -> str:
        return (supply / name).read_text().strip() if (supply / name).is_file() else ""

    for s in sorted(Path("/sys/class/power_supply").glob("*")):
        if read(s, "type") == "Battery" and read(s, "scope") != "Device" and read(s, "capacity"):
            return {"percent": int(read(s, "capacity")), "charging": read(s, "status") in ("Charging", "Full")}
    return None


def os_name() -> str:
    try:
        return platform.freedesktop_os_release().get("PRETTY_NAME") or platform.platform()
    except OSError:
        return platform.platform()


def status(updates: dict | None) -> dict:
    linux_only()
    vm, swap = psutil.virtual_memory(), psutil.swap_memory()
    gb = lambda n: round(n / 1e9, 1)                                          
    disks, seen = [], set()
    for p in psutil.disk_partitions(all=False):
        if p.device in seen or p.fstype in ("squashfs", "overlay", "tmpfs") or p.mountpoint.startswith(("/snap", "/var/lib/docker")):
            continue
        seen.add(p.device)
        try:
            u = psutil.disk_usage(p.mountpoint)
        except OSError:
            continue
        if u.total >= 1e9:
            disks.append({"mount": p.mountpoint, "used_percent": round(u.percent), "free_gb": gb(u.free)})
    temps = {}
    for chip, readings in (psutil.sensors_temperatures() or {}).items():
        if readings:
            temps[chip] = round(max(r.current for r in readings))
    battery = _battery()
    up = int(time.time() - psutil.boot_time())
    out = {
        "name": socket.gethostname(), "system": os_name(),
        "up": f"{up // 86400} days {up % 86400 // 3600} hours" if up >= 86400 else f"{up // 3600} hours {up % 3600 // 60} minutes",
        "load": [round(x, 2) for x in os.getloadavg()], "cpus": psutil.cpu_count(),
        "memory": {"used_gb": gb(vm.total - vm.available), "total_gb": gb(vm.total), "used_percent": round(vm.percent)},
        "swap_used_gb": gb(swap.used), "disks": disks, "temperatures_c": temps,
        "failed_services": _systemctl_failed(False) + [f"{u} (user)" for u in _systemctl_failed(True)],
    }
    if battery is not None:
        out["battery"] = battery
    if updates:
        out["updates"] = {"waiting": len(updates["updates"]), "security": updates["security_updates"],
                          "restart_needed": updates["restart_needed"], "checked": updates["checked"]}
    else:
        out["updates"] = "Not checked yet; ask for its security updates."
    return out


# logs

SINCE = re.compile(r"^\s*(\d+)\s*(m|min|minutes?|h|hours?|d|days?)\s*$")


def since_spec(text: str) -> str:
    m = SINCE.match(text or "")
    if not m:
        raise Refused(f"Could not read {text!r} as a time back; use forms like 30m, 2h or 1d.")
    unit = {"m": "min", "h": "h", "d": "d"}[m.group(2)[0]]
    return f"-{m.group(1)}{unit}"


def logs(cfg: Config, source: str, since: str = "1d", match: str | None = None, lines: int = 50) -> dict:
    linux_only()
    if source not in cfg.logs:
        raise Refused(f"{source!r} is not a log allowed here; allowed: {', '.join(cfg.logs) or 'none'}.")
    kind, where = cfg.logs[source]
    lines = max(1, min(int(lines), 200))
    if kind == "file":
        try:
            with open(where, "rb") as f:
                start = max(0, f.seek(0, os.SEEK_END) - 512 * 1024)
                f.seek(start)
                text = f.read().decode(errors="replace")
        except OSError as e:
            raise Refused(f"The {source} log cannot be read: {e.strerror}.") from None
        rows = text.splitlines()[1 if start else 0:]          # the first line read mid-file is cut
    else:
        cmd = ["journalctl", "--no-pager", "-q", "-o", "short-iso", "--since", since_spec(since),
               "-n", str(5000 if match else lines), "--user-unit" if kind == "user_journal" else "-u", where]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=20, check=False)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise Refused(f"The {source} log cannot be read: {e}") from None
        if r.returncode:
            raise Refused(f"The {source} log cannot be read: {r.stderr.strip() or f'exit {r.returncode}'}")
        rows = r.stdout.splitlines()
    if match:
        rows = [row for row in rows if match.casefold() in row.casefold()]
    rows = rows[-lines:]
    return {"source": source, "lines": rows, "note": None if rows else "Nothing in that time."}
