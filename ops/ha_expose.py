"""Expose Winston's entities to Home Assistant's Assist (which is what the MCP server
serves) and give them plain-English aliases. Least privilege (plan §6 rule 5): only
what is listed here is visible to the model. Re-run after editing the list.

    .venv/bin/python ops/ha_expose.py           # apply
    .venv/bin/python ops/ha_expose.py --list    # show what is exposed now
"""
import asyncio
import os
import sys
from pathlib import Path

import aiohttp
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
URL, TOKEN = os.environ["HA_URL"], os.environ["HA_TOKEN"]

# entity_id -> aliases (names Aldin would say). Read-only sensors need no alias.
EXPOSE: dict[str, list[str]] = {
    "light.living_room_all": ["living room lights", "the lights"],
    "light.living_room_taklampa_kajplats_e27_ws_globe_1521lm": ["ceiling lamp"],
    "light.living_room_hektar_kajplats_e27_ws_globe_1521lm": ["hektar lamp"],
    "light.living_room_hylla_kajplats_e27_ws_g95_clear_806lm": ["shelf lamp"],
    "sensor.pixel_11_pro_battery_level": ["phone battery", "pixel battery", "phone", "battery", "my phone battery"],
    "sensor.pixel_11_pro_battery_state": ["phone charging state"],
    "sensor.oneplus_13r_battery_level": ["oneplus battery"],
    "device_tracker.pixel_11_pro": ["my phone", "phone location"],
    "device_tracker.oneplus_13r": ["the oneplus"],
    "button.wake_on_lan_04_7c_16_70_ca_bc": ["PC", "my PC", "the PC", "computer", "my computer"],  # wake_on_lan, Arch PC's MAC
}
EXPOSE_BY_DOMAIN = ("person", "weather", "todo")   # single entities; exposed without aliases
RENAME = {"button.wake_on_lan_04_7c_16_70_ca_bc": "PC", "light.living_room_all": "Living room lights"}


async def main(list_only: bool):
    async with aiohttp.ClientSession() as s:
        states = await (await s.get(f"{URL}/api/states", headers={"Authorization": f"Bearer {TOKEN}"})).json()
        ids = {e["entity_id"] for e in states}
        extra = [e["entity_id"] for e in states if e["entity_id"].split(".")[0] in EXPOSE_BY_DOMAIN]
        missing = [e for e in EXPOSE if e not in ids]
        if missing:
            print("not found in HA:", missing); return 1

        async with s.ws_connect(f"{URL}/api/websocket") as ws:
            assert (await ws.receive_json())["type"] == "auth_required"
            await ws.send_json({"type": "auth", "access_token": TOKEN})
            assert (await ws.receive_json())["type"] == "auth_ok"
            n = 0

            async def call(**msg):
                nonlocal n; n += 1
                await ws.send_json({"id": n, **msg})
                while True:
                    r = await ws.receive_json()
                    if r.get("id") == n:
                        if not r.get("success", True):
                            print("ERROR", msg.get("type"), r.get("error")); return None
                        return r.get("result")

            if not list_only:
                targets = list(EXPOSE) + extra
                current = await call(type="homeassistant/expose_entity/list") or {}
                stray = [eid for eid, v in current.get("exposed_entities", {}).items()
                         if v.get("conversation") and eid not in targets]
                if stray:   # HA auto-exposes whole domains by default; Winston gets an allowlist only
                    await call(type="homeassistant/expose_entity", assistants=["conversation"],
                               entity_ids=stray, should_expose=False)
                    print(f"un-exposed {len(stray)} entities that were not on the list")
                await call(type="homeassistant/expose_new_entities/set", assistant="conversation", expose_new=False)
                await call(type="homeassistant/expose_entity", assistants=["conversation"],
                           entity_ids=targets, should_expose=True)
                for eid, aliases in EXPOSE.items():
                    if aliases:
                        upd = {"aliases": aliases}
                        if eid in RENAME:
                            upd["name"] = RENAME[eid]
                        await call(type="config/entity_registry/update", entity_id=eid, **upd)
                print(f"exposed {len(targets)} entities, aliased {sum(1 for a in EXPOSE.values() if a)}")
            exposed = await call(type="homeassistant/expose_entity/list") or {}
            rows = [(eid, v.get("conversation")) for eid, v in exposed.get("exposed_entities", {}).items()
                    if v.get("conversation")]
            print(f"exposed to Assist now ({len(rows)}):")
            for eid, _ in sorted(rows):
                print("  ", eid)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main("--list" in sys.argv)))
