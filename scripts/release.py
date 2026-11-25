"""Builds a release into a folder, as CI does on a `v*` tag (.github/workflows/release.yml, docs/setup.md):
winston-<version>.tar.gz (the files git tracks, the built UI in web/dist, the version stamped in), winston-ha.zip
(the Home Assistant integration, custom_components/winston with the version stamped in: what HACS and the
Marketplace install, hacs.json), install.sh, CHANGELOG.md, the Voice PE firmware when given, and SHA256SUMS over them. A release needs its dated entry in
CHANGELOG.md (`just release` dates it); --notes writes that entry for the GitHub release's notes.

    python3 scripts/release.py 0.2.0 dist [--notes notes.md] [--firmware services/voice-pe/.esphome/build/.../firmware.factory.bin]
"""
import argparse
import hashlib
import io
import re
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
# the line the version is on, by its start (after any indent)
STAMPS = {"pyproject.toml": "version = ", "src/winston/__init__.py": "__version__ = ",
          "custom_components/winston/manifest.json": '"version": '}
INTEGRATION = "custom_components/winston"


def stamped(f: str, data: bytes, version: str) -> bytes:
    if f not in STAMPS:
        return data
    text, n = re.subn(rf'(?m)^(\s*{re.escape(STAMPS[f])})"[^"]+"', rf'\g<1>"{version}"', data.decode())
    if n != 1:
        sys.exit(f"{f}: no line {STAMPS[f]}\"...\" for the version")
    return text.encode()


def files() -> list[str]:
    """What git tracks (as it is on disk) and the built UI. Not untracked files: CI writes the release into the
    checkout, and the first one held a part of its own tarball."""
    out = subprocess.run(["git", "-C", str(REPO), "ls-files", "-z"], capture_output=True, text=True,
                         check=True).stdout.split("\0")
    dist = {str(p.relative_to(REPO)) for p in (REPO / "web/dist").rglob("*") if p.is_file()}
    if not dist:
        sys.exit("web/dist is empty: build the UI first (cd web && pnpm install && pnpm build)")
    return sorted({f for f in out if f and not f.startswith("data/") and (REPO / f).is_file()} | dist)


def tarball(version: str, out: Path) -> Path:
    path, top = out / f"winston-{version}.tar.gz", f"winston-{version}"
    with tarfile.open(path, "w:gz") as tar:
        for f in files():
            info = tar.gettarinfo(REPO / f, f"{top}/{f}")
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            data = stamped(f, (REPO / f).read_bytes(), version)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return path


def integration(version: str, out: Path) -> Path:
    """The integration's files at the zip's top, as HACS unpacks them into custom_components/winston."""
    path = out / "winston-ha.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files():
            if f.startswith(INTEGRATION + "/"):
                z.writestr(f.removeprefix(INTEGRATION + "/"), stamped(f, (REPO / f).read_bytes(), version))
    return path


def notes(version: str) -> str:
    """The version's entry in CHANGELOG.md, without its heading. A pre-release (a hyphen) may have none."""
    text = (REPO / "CHANGELOG.md").read_text()
    m = re.search(rf"^## {re.escape(version)} \((\d{{4}}-\d\d-\d\d)\)[ \t]*\n(.*?)(?=^## |\Z)", text, re.MULTILINE | re.DOTALL)
    if m:
        return m[2].strip() + "\n"
    if "-" in version:
        return "A test build of the next release.\n"
    sys.exit(f"CHANGELOG.md has no dated entry \"## {version} (YYYY-MM-DD)\": `just release {version}` writes the date")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("version", help="like 0.2.0, the tag without its v")
    ap.add_argument("out", type=Path)
    ap.add_argument("--notes", type=Path, help="where to write the version's CHANGELOG.md entry")
    ap.add_argument("--firmware", type=Path, help="ESPHome's firmware.factory.bin for the Voice PE")
    a = ap.parse_args()
    if not re.fullmatch(r"\d+\.\d+\.\d+([.-]?[0-9A-Za-z.]+)?", a.version):
        sys.exit(f"{a.version} is not a version like 0.2.0")
    entry = notes(a.version)
    if a.notes:
        a.notes.write_text(entry)
    a.out.mkdir(parents=True, exist_ok=True)
    made = [tarball(a.version, a.out), integration(a.version, a.out), Path(shutil.copy(REPO / "scripts/install.sh", a.out / "install.sh")),
            Path(shutil.copy(REPO / "CHANGELOG.md", a.out / "CHANGELOG.md"))]
    if a.firmware:
        made.append(Path(shutil.copy(a.firmware, a.out / f"voice-pe-winston-{a.version}.factory.bin")))
    sums = "".join(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n" for p in made)
    (a.out / "SHA256SUMS").write_text(sums)
    print(sums, end="")


if __name__ == "__main__":
    main()
