"""Builds a release into a folder, as CI does on a `v*` tag (.github/workflows/release.yml, docs/setup.md):
winston-<version>.tar.gz (the files git tracks, the built UI in web/dist, the version stamped in), install.sh,
the Voice PE firmware when given, and SHA256SUMS over them.

    python3 scripts/release.py 0.2.0 dist [--firmware services/voice-pe/.esphome/build/.../firmware.factory.bin]
"""
import argparse
import hashlib
import io
import re
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
STAMPS = {"pyproject.toml": "version = ", "src/winston/__init__.py": "__version__ = "}   # the line the version is on


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
            data = (REPO / f).read_bytes()
            if f in STAMPS:
                prefix = STAMPS[f]
                text, n = re.subn(rf'(?m)^{prefix}"[^"]+"$', f'{prefix}"{version}"', data.decode())
                if n != 1:
                    sys.exit(f"{f}: no line {prefix}\"...\" for the version")
                data = text.encode()
                info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return path


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("version", help="like 0.2.0, the tag without its v")
    ap.add_argument("out", type=Path)
    ap.add_argument("--firmware", type=Path, help="ESPHome's firmware.factory.bin for the Voice PE")
    a = ap.parse_args()
    if not re.fullmatch(r"\d+\.\d+\.\d+([.-]?[0-9A-Za-z.]+)?", a.version):
        sys.exit(f"{a.version} is not a version like 0.2.0")
    a.out.mkdir(parents=True, exist_ok=True)
    made = [tarball(a.version, a.out), Path(shutil.copy(REPO / "scripts/install.sh", a.out / "install.sh"))]
    if a.firmware:
        made.append(Path(shutil.copy(a.firmware, a.out / f"voice-pe-winston-{a.version}.factory.bin")))
    sums = "".join(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n" for p in made)
    (a.out / "SHA256SUMS").write_text(sums)
    print(sums, end="")


if __name__ == "__main__":
    main()
