"""The updates waiting on this computer, as its own package tool tells any user: which are for security,
and the CVE ids they fix where the tool says. Read at most hourly; nothing is ever installed, and
nothing needs root. Each parser takes the tool's output as text, so the tests read recordings."""
from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import threading
import time
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

from winston_computer.system import Refused, linux_only, os_name

CVE = re.compile(r"\bCVE-\d{4}-\d{4,}\b")
KEEP_SECS = 3600
RUN_SECS = 300
_lock = threading.Lock()
_cache: tuple[float, dict] | None = None


def run(cmd: list[str]) -> tuple[int, str, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=RUN_SECS, check=False,
                           env={**os.environ, "LC_ALL": "C", "LANG": "C"})
    except subprocess.TimeoutExpired:
        return 124, "", f"{cmd[0]} took longer than {RUN_SECS} s"
    except OSError as e:
        return 127, "", str(e)
    return r.returncode, r.stdout, r.stderr


def _row(package: str, available: str, installed: str | None = None) -> dict:
    return {"package": package, "installed": installed, "available": available, "security": None, "cves": []}


# pacman (Arch)

def parse_pacman(text: str) -> list[dict]:
    """`checkupdates` and `pacman -Qu`: "name old -> new"."""
    rows = []
    for line in text.splitlines():
        m = re.match(r"^(\S+)\s+(\S+)\s+->\s+(\S+)", line)
        if m:
            rows.append(_row(m.group(1), m.group(3), m.group(2)))
    return rows


def parse_arch_audit(text: str) -> dict[str, list[str]]:
    """`arch-audit -u --json`: package -> CVE ids, for vulnerable packages with a fix out."""
    out: dict[str, list[str]] = {}
    for group in json.loads(text or "[]"):
        for pkg in group.get("packages", []):
            out.setdefault(pkg, []).extend(c for c in group.get("issues", []) if CVE.fullmatch(c))
    return out


def _pacman() -> dict:
    note = ("Arch's security tracker, which arch-audit reads, has been mostly quiet since July 2025, so an update "
            "it does not flag may still fix a security issue.")
    code, out, err = run(["checkupdates"]) if shutil.which("checkupdates") else (127, "", "not installed")
    if code not in (0, 2):                              # 2: nothing waiting
        code, out, err = run(["pacman", "-Qu"])
        if code not in (0, 1):
            raise Refused(f"pacman could not list updates: {err.strip()}")
        note += " checkupdates did not run, so this is as of the system's last sync."
    rows = parse_pacman(out)
    if shutil.which("arch-audit"):
        code, out, err = run(["arch-audit", "-u", "--json"])
        flagged = parse_arch_audit(out) if code == 0 else {}
        for r in rows:
            r["security"] = r["package"] in flagged
            r["cves"] = flagged.get(r["package"], [])
        data = "partial"
    else:
        note = "Arch has no current security feed and arch-audit is not installed: any waiting update may matter."
        data = "none"
    return {"tool": "pacman", "updates": rows, "security_data": data, "note": note}


# apt (Debian, Ubuntu)

APT = re.compile(r"^(\S+?)/(\S+)\s+(\S+)\s+\S+(?:\s+\[upgradable from: ([^\]]+)\])?")


def parse_apt(text: str) -> list[dict]:
    """`apt list --upgradable`: "name/suite,suite version arch [upgradable from: old]"; a suite ending in
    -security marks a security update."""
    rows = []
    for line in text.splitlines():
        m = APT.match(line)
        if m:
            r = _row(m.group(1), m.group(3), m.group(4))
            r["security"] = any(s.endswith("-security") for s in m.group(2).split(","))
            rows.append(r)
    return rows


def parse_debsecan(text: str) -> dict[str, list[str]]:
    """`debsecan --suite X --only-fixed`: "CVE-... package (fixed...)" lines."""
    out: dict[str, list[str]] = {}
    for line in text.splitlines():
        m = re.match(r"^(CVE-\d{4}-\d{4,})\s+(\S+)", line)
        if m:
            out.setdefault(m.group(2), []).append(m.group(1))
    return out


def _apt() -> dict:
    code, out, err = run(["apt", "list", "--upgradable"])
    if code:
        raise Refused(f"apt could not list updates: {err.strip()}")
    rows = parse_apt(out)
    release = _os_release()
    note, data = "apt's lists are as fresh as the system's own refresh.", "partial"
    if release.get("ID") == "debian" and shutil.which("debsecan") and release.get("VERSION_CODENAME"):
        code, out, _ = run(["debsecan", "--suite", release["VERSION_CODENAME"], "--only-fixed"])
        fixed = parse_debsecan(out) if code == 0 else {}
        for r in rows:
            r["cves"] = sorted(set(fixed.get(r["package"], [])))
        data = "full"
    elif release.get("ID") == "debian":
        note += " Install debsecan for the CVE ids."
    else:
        note += " Security updates are marked, without CVE ids."
    return {"tool": "apt", "updates": rows, "security_data": data, "note": note}


# dnf5 (Fedora)

def parse_dnf_check(text: str) -> list[dict]:
    """`dnf check-upgrade`: "name.arch  version  repo" under its headings."""
    rows = []
    for line in text.splitlines():
        m = re.match(r"^(\S+)\.(\w+)\s+(\S+)\s+(\S+)$", line)
        if m and m.group(1) not in ("Upgrades", "Obsoleting"):
            rows.append(_row(m.group(1), m.group(3)))
    return rows


def _nevra_name(nevra: str) -> str:
    """"openssl-libs-1:3.5.9-1.fc44.x86_64" -> "openssl-libs"."""
    return nevra.rsplit(".", 1)[0].rsplit("-", 2)[0]


def parse_dnf_advisories(text: str) -> dict[str, list[str]]:
    """`dnf advisory info --security --json`: package name -> CVE ids its security advisories name in their
    references (Fedora's often name none, only the advisory)."""
    out: dict[str, list[str]] = {}
    for adv in json.loads(text[text.find("["):] if "[" in text else "[]"):
        cves = sorted(set(CVE.findall(json.dumps(adv.get("references", [])))))
        for nevra in (adv.get("collections") or {}).get("packages", []):
            out.setdefault(_nevra_name(nevra), [])
            out[_nevra_name(nevra)] = sorted(set(out[_nevra_name(nevra)] + cves))
    return out


def _dnf() -> dict:
    code, out, err = run(["dnf", "-q", "check-upgrade"])
    if code not in (0, 100):                            # 100: updates waiting
        raise Refused(f"dnf could not list updates: {err.strip()}")
    rows = parse_dnf_check(out)
    code, out, _ = run(["dnf", "-q", "advisory", "info", "--security", "--json"])
    security = parse_dnf_advisories(out) if code == 0 else {}
    for r in rows:
        r["security"] = r["package"] in security
        r["cves"] = security.get(r["package"], [])
    return {"tool": "dnf", "updates": rows, "security_data": "partial",
            "note": "CVE ids come from the security advisories that name them; some name none."}


# zypper (openSUSE, SLE)

def _xml(text: str) -> ET.Element:
    return ET.fromstring(text[text.find("<?xml"):] if "<?xml" in text else text)


def parse_zypper_updates(text: str) -> list[dict]:
    """`zypper --xmlout list-updates`."""
    return [_row(u.get("name", ""), u.get("edition", ""), u.get("edition-old"))
            for u in _xml(text).iter("update") if u.get("kind") == "package"]


def parse_zypper_patches(text: str) -> tuple[int, list[str]]:
    """`zypper --xmlout list-patches`: how many security patches are needed, and the CVE ids they fix. The
    category is read from each patch, since `--category` is not applied to the list."""
    needed = [u for u in _xml(text).iter("update") if u.get("kind") == "patch"
              and u.get("category") == "security" and u.get("status") == "needed"]
    cves = {i.get("id", "") for u in needed for i in u.iter("issue") if i.get("type") == "cve"}
    return len(needed), sorted(c for c in cves if CVE.fullmatch(c))


def _zypper() -> dict:
    code, out, err = run(["zypper", "-n", "--xmlout", "list-updates"])
    if code:
        raise Refused(f"zypper could not list updates: {err.strip() or out[-300:]}")
    rows = parse_zypper_updates(out)
    code, out, _ = run(["zypper", "-n", "--xmlout", "list-patches"])
    patches, cves = parse_zypper_patches(out) if code == 0 else (0, [])
    return {"tool": "zypper", "updates": rows, "security_data": "full", "security_patches": patches,
            "patch_cves": cves, "note": "Security comes in patches, each fixing several packages."}


# the check

def _os_release() -> dict:
    try:
        return platform.freedesktop_os_release()
    except OSError:
        return {}


def restart_needed() -> bool:
    """Debian and Ubuntu say so in a file; elsewhere a kernel newer than the running one has removed the
    running one's modules (Arch, Fedora's after cleanup)."""
    if Path("/run/reboot-required").exists():
        return True
    modules = Path("/usr/lib/modules")
    return modules.is_dir() and any(modules.iterdir()) and not (modules / os.uname().release).exists()


def detect() -> str:
    for tool, binary in (("pacman", "pacman"), ("apt", "apt"), ("dnf", "dnf"), ("zypper", "zypper")):
        if shutil.which(binary):
            return tool
    raise Refused("No package tool this server knows is installed (pacman, apt, dnf or zypper).")


def check(max_age: float = KEEP_SECS) -> dict:
    """The waiting updates, read again when the last read is older than `max_age` seconds."""
    global _cache
    linux_only()
    with _lock:
        if _cache and time.monotonic() - _cache[0] < max_age:
            return _cache[1]
        t0 = time.monotonic()
        found = {"pacman": _pacman, "apt": _apt, "dnf": _dnf, "zypper": _zypper}[detect()]()
        rows = found["updates"]
        cves = sorted({c for r in rows for c in r["cves"]} | set(found.pop("patch_cves", [])))
        if "security_patches" in found:                 # zypper counts patches, not packages
            security = found.pop("security_patches")
        elif any(r["security"] is not None for r in rows):
            security = sum(1 for r in rows if r["security"])
        else:
            security = None if rows else 0              # unknown: the tool does not say
        result = {"system": os_name(), "tool": found["tool"],
                  "checked": datetime.now(UTC).astimezone().isoformat(timespec="minutes"),
                  "updates": rows, "security_updates": security, "cves": cves, "security_data": found["security_data"], "restart_needed": restart_needed(),
                  "note": found["note"], "took_s": round(time.monotonic() - t0, 1)}
        _cache = (time.monotonic(), result)
        return result


def cached() -> dict | None:
    return _cache[1] if _cache else None


def view(result: dict, package: str | None = None, limit: int = 30) -> dict:
    """What the tool answers: the counts and every CVE id, and the updates that matter most (security
    first), or those whose name has `package` in it. Fields with nothing to say are left out, so a
    computer with hundreds waiting still fits in one answer."""
    rows = [r for r in result["updates"] if not package or package.casefold() in r["package"].casefold()]
    rows.sort(key=lambda r: (r["security"] is not True, r["package"]))
    shown = [{k: v for k, v in r.items() if v not in (None, [], False) and k != "installed"} for r in rows[:limit]]
    n, sec, cves = len(result["updates"]), result["security_updates"], result["cves"]
    summary = f"{n} update{'s' if n != 1 else ''} waiting" + (
        f", {sec} for security" if sec is not None else "; this system does not say which are for security")
    summary += (f", fixing {len(cves)} CVEs" if cves else "") + (". A restart is needed." if result["restart_needed"]
                                                                 else ".")
    out = {"summary": summary, **{k: v for k, v in result.items() if k != "updates"}}
    out["waiting"] = n
    out["updates"] = shown
    if len(rows) > limit:
        out["more"] = len(rows) - limit
    if package and not rows:
        out["note"] = f"No update waiting for {package}. " + (result["note"] or "")
    return out
