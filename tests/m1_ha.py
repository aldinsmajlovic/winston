"""M1 acceptance (WINSTON_PLAN.md §5): typed commands change real Home Assistant state.
Runs the three plan commands through the text channel (same brain as voice), checks HA's own
state afterwards, then puts the lights back the way they were.

    .venv/bin/python tests/m1_ha.py
"""
import asyncio
import os
import sys
import time

import httpx
from loguru import logger

from winston.channels.text import run
from winston.config import load

CFG = load()
HA = CFG.providers.ha.url.rstrip("/"); H = {"Authorization": f"Bearer {os.environ['HA_TOKEN']}"}
LIGHT, PHONE, WOL = "light.living_room_all", "sensor.pixel_11_pro_battery_level", "button.wake_on_lan_04_7c_16_70_ca_bc"


def state(eid):
    return httpx.get(f"{HA}/api/states/{eid}", headers=H, timeout=10).json()


before = state(LIGHT); wol_before = state(WOL)["state"]; battery = state(PHONE)["state"]
print(f"before: light {before['state']} brightness={before['attributes'].get('brightness')} "
      f"kelvin={before['attributes'].get('color_temp_kelvin')} | phone battery {battery}% | wol {wol_before}")

logger.remove(); logger.add(sys.stderr, level="WARNING")
t0 = time.perf_counter()
replies = asyncio.run(run(CFG, ["Set the living room lights to 50% and warm.",
                           "What's my phone battery?",
                           "Boot my PC."]))
print(f"\n3 turns in {time.perf_counter() - t0:.1f}s")

time.sleep(1.0)
after = state(LIGHT); a = after["attributes"]; wol_after = state(WOL)["state"]
checks = {
    "light ~50%": after["state"] == "on" and a.get("brightness") is not None and abs(a["brightness"] - 128) <= 8,
    "light warm (<=3200 K)": (a.get("color_temp_kelvin") or 9999) <= 3200,
    "battery answered": battery in replies[1],
    "wol button pressed": wol_after != wol_before,
}
print(f"after:  light {after['state']} brightness={a.get('brightness')} kelvin={a.get('color_temp_kelvin')} | wol {wol_after}")
for k, v in checks.items():
    print(f"  {'PASS' if v else 'FAIL'}  {k}")

# restore
if before["state"] == "on":
    httpx.post(f"{HA}/api/services/light/turn_on", headers=H, timeout=10, json={
        "entity_id": LIGHT, "brightness": before["attributes"].get("brightness", 255),
        **({"color_temp_kelvin": before["attributes"]["color_temp_kelvin"]} if before["attributes"].get("color_temp_kelvin") else {})})
else:
    httpx.post(f"{HA}/api/services/light/turn_off", headers=H, timeout=10, json={"entity_id": LIGHT})
time.sleep(1.0); r = state(LIGHT)
print(f"restored: light {r['state']} brightness={r['attributes'].get('brightness')} kelvin={r['attributes'].get('color_temp_kelvin')}")
sys.exit(0 if all(checks.values()) else 1)
