#!/usr/bin/env bash
# security_updates for real, in a container of each Linux family: the package installed from this
# folder, the metadata refreshed as root (as the system's own timer would), then asked as a normal
# user. Needs Docker and the network. Prints one summary line per system.
#   bash computer/tests/containers.sh [image ...]
set -uo pipefail
src="$(cd "$(dirname "$0")/.." && pwd)"
images=("$@")
[ ${#images[@]} -gt 0 ] || images=(debian:13.0 ubuntu:26.04 ubuntu:noble-20250404 fedora:44 opensuse/leap:16.0 archlinux:latest)

ask='WINSTON_COMPUTER_CONFIG=/tmp/wc /v/bin/python -m winston_computer init >/dev/null &&
     WINSTON_COMPUTER_CONFIG=/tmp/wc WINSTON_COMPUTER_STATE=/tmp/ws /v/bin/python -m winston_computer call security_updates'
copy='mkdir -p /tmp/src && tar -C /src --exclude=.venv --exclude=__pycache__ -cf - . | tar -C /tmp/src -xf -'
venv='python3 -m venv /v && /v/bin/pip install -q /tmp/src'

for image in "${images[@]}"; do
  case "$image" in
    debian*) setup="apt-get update -qq && apt-get install -y -qq python3-venv debsecan >/dev/null" ;;
    ubuntu*) setup="apt-get update -qq && apt-get install -y -qq python3-venv >/dev/null" ;;
    fedora*) setup="dnf -y -q install python3 util-linux >/dev/null && dnf -q makecache >/dev/null" ;;
    opensuse*) setup="zypper -n -q refresh >/dev/null && zypper -n -q install python3 tar shadow util-linux >/dev/null" ;;
    archlinux*) setup="pacman -Sy --noconfirm --needed -q python pacman-contrib fakeroot >/dev/null 2>&1" ;;
    *) echo "$image: not a family this script knows"; continue ;;
  esac
  (
    out=$(docker run --rm -v "$src:/src:ro" "$image" bash -c "$setup && $copy && $venv && useradd -m u &&
          su u -c '$ask'" 2>/dev/null)
    echo "$out" | python3 -I -c '
import json, sys
image = sys.argv[1]
try:
    d = json.loads(sys.stdin.read())
except ValueError:
    print(f"{image}: FAILED, no answer"); sys.exit(1)
print(f"{image}: {d["tool"]}, {d["waiting"]} waiting, security {d["security_updates"]}, {len(d["cves"])} CVE ids, "
      f"data {d["security_data"]}, restart {d["restart_needed"]}, {d["took_s"]} s")' "$image"
  ) &
done
wait
