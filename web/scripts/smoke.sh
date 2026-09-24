#!/usr/bin/env bash
# The settings smoke test: a throwaway winstond on a copy of data/, driven by Playwright in the system
# Chromium. The copy has fake keys, every engine and channel switched off and Home Assistant pointed at
# a fake one (tests/fake_ha.py, or nowhere when it does not start), so nothing real is started, stopped
# or written. Needs a built UI (pnpm build).
# Usage: pnpm smoke [playwright args]
set -euo pipefail
web="$(cd "$(dirname "$0")/.." && pwd)"
repo="$(dirname "$web")"
src="${WINSTON_SOURCE:-$repo/data}"
[ -f "$web/dist/index.html" ] || { echo "no web/dist: run pnpm build first"; exit 1; }
[ -f "$src/config.yaml" ] || { echo "no config at $src"; exit 1; }

copy="$(mktemp -d "${TMPDIR:-/tmp}/winston-smoke.XXXXXX")"
pid=""
ha_pid=""
cleanup() {
  local status=$?
  if [ -n "$pid" ]; then kill "$pid" 2>/dev/null; wait "$pid" 2>/dev/null || true; fi
  if [ -n "$ha_pid" ]; then kill "$ha_pid" 2>/dev/null; wait "$ha_pid" 2>/dev/null || true; fi
  rm -rf "$copy"
  exit "$status"
}
trap cleanup EXIT INT TERM

free_port() { uv run --project "$repo" python -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])'; }

# A fake Home Assistant for the Home Assistant page to read and save to. Without it the copy points
# Home Assistant nowhere and the tests that need it are skipped.
fake_ha=""
ha_port="$(free_port)"
uv run --project "$repo" python "$repo/tests/fake_ha.py" --port "$ha_port" >"$copy/fake_ha.log" 2>&1 &
ha_pid=$!
for _ in $(seq 40); do
  if curl -sf "http://127.0.0.1:$ha_port/fake/log" >/dev/null; then fake_ha="http://127.0.0.1:$ha_port"; break; fi
  kill -0 "$ha_pid" 2>/dev/null || break
  sleep 0.25
done
if [ -z "$fake_ha" ]; then
  echo "fake Home Assistant did not start; its tests are skipped"; cat "$copy/fake_ha.log"
  kill "$ha_pid" 2>/dev/null || true; ha_pid=""
fi

# The config and persona only: no logs, voices, memory or real keys.
cp "$src/config.yaml" "$copy/"
[ -f "$src/persona.md" ] && cp "$src/persona.md" "$copy/"
port="$(uv run --project "$repo" python - "$copy" "$src" "$fake_ha" <<'EOF'
import os, re, socket, sys, yaml
copy, fake_ha = sys.argv[1], sys.argv[3]
path = os.path.join(copy, "config.yaml")
cfg = yaml.safe_load(open(path))
for e in cfg.get("engines", {}).values():
    e["enabled"] = False
for c in ("voice", "discord", "satellite"):
    cfg.setdefault("channels", {}).setdefault(c, {})["enabled"] = False
cfg.setdefault("announce", {})["enabled"] = False
ha = cfg.setdefault("providers", {}).setdefault("home_assistant", {})
ha["url"] = fake_ha or "http://127.0.0.1:9"
if fake_ha:
    # The fake's own entities, so the page has rooms, aliases and states to show.
    ha["enabled"] = True
    ha["expose"] = {"light.taklampa": {"name": "Ceiling lamp", "aliases": ["ceiling light"]},
                    "sensor.phone_battery": {"aliases": ["battery"]}}
    ha["expose_domains"] = ["person", "weather", "todo"]
# One engine the supervisor sees as running without starting anything: an external one nothing serves.
cfg["engines"]["smoke"] = {"driver": "external", "port": 9, "url": "http://127.0.0.1:9", "health": "/health"}
yaml.safe_dump(cfg, open(path, "w"), sort_keys=False)
# Every variable the real .env names, with a fake value, so Set and Not set look as they do at home.
names = []
real = os.path.join(sys.argv[2], ".env")
if os.path.isfile(real):
    names = [m.group(1) for m in re.finditer(r"^([A-Za-z_][A-Za-z0-9_]*)=", open(real).read(), re.M)]
token_env = ha.get("token_env") or "HA_TOKEN"
with open(os.path.join(copy, ".env"), "w") as f:
    f.writelines(f"{n}=fake-{n.lower()}\n" for n in names if n not in ("DISCORD_BOT_TOKEN", token_env))
    f.write(f"{token_env}=fake-ha-token\n")      # the token tests/fake_ha.py takes
os.chmod(os.path.join(copy, ".env"), 0o600)
s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()
EOF
)"

WINSTON_DATA="$copy" uv run --project "$repo" winston up --no-start --host 127.0.0.1 --port "$port" \
  >"$copy/winstond.log" 2>&1 &
pid=$!
for _ in $(seq 60); do
  curl -sf "http://127.0.0.1:$port/api/health" >/dev/null && break
  kill -0 "$pid" 2>/dev/null || { cat "$copy/winstond.log"; exit 1; }
  sleep 0.5
done

cd "$web"
WINSTON_URL="http://127.0.0.1:$port" WINSTON_DATA="$copy" FAKE_HA_URL="$fake_ha" pnpm exec playwright test "$@"
