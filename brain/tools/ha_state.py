"""Local tools that read Home Assistant's REST API directly, for things the MCP server's
GetLiveContext cannot express: how old a reading is. Registered on the LLM service next to
the MCP tools. Read-only (plan §6 rule 4).
"""
import os
import time
from datetime import datetime, timezone

import httpx
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.services.llm_service import FunctionCallParams

PHONES = {  # label -> (battery level sensor, battery state sensor)
    "Aldin's phone (Pixel 11 Pro)": ("sensor.pixel_11_pro_battery_level", "sensor.pixel_11_pro_battery_state"),
    "old spare phone (OnePlus 13R, usually off, not carried)": ("sensor.oneplus_13r_battery_level", "sensor.oneplus_13r_battery_state"),
}


def _age(iso: str) -> str:
    mins = int((time.time() - datetime.fromisoformat(iso).astimezone(timezone.utc).timestamp()) // 60)
    return f"{mins} minutes ago" if mins < 120 else f"{mins // 60} hours ago"


async def get_phone_battery(params: FunctionCallParams):
    url, token = os.environ["HA_URL"].rstrip("/"), os.environ["HA_TOKEN"]
    lines = []
    async with httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=10) as c:
        for name, (level, state) in PHONES.items():
            lv = (await c.get(f"{url}/api/states/{level}")).json()
            st = (await c.get(f"{url}/api/states/{state}")).json()
            lines.append(f"{name}: {lv['state']}% ({st['state']}), reported {_age(lv['last_updated'])}")
    await params.result_callback({"phones": lines,
                                  "note": "'My phone' means the Pixel. Readings older than about 30 minutes may be out of date; say so. "
                                          "Mention the old spare only if asked about it."})


get_phone_battery_schema = FunctionSchema(
    name="get_phone_battery",
    description="Battery level, charging state and age of the last report for every phone in the home. "
                "Use this for any question about a phone's battery instead of GetLiveContext.",
    properties={}, required=[])

LOCAL_TOOLS = [(get_phone_battery_schema, get_phone_battery)]
