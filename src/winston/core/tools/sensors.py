"""Watched sensors with the age of each reading. Home Assistant's live context carries no
timestamps, so a value can be hours old without the model knowing. Configured under
tools.sensors; read-only (plan §6 rule 4)."""
import os
import time
from datetime import datetime, timezone

import httpx
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.services.llm_service import FunctionCallParams

from winston.config.schema import WinstonConfig


def _age(iso: str) -> str:
    mins = int((time.time() - datetime.fromisoformat(iso).astimezone(timezone.utc).timestamp()) // 60)
    return f"{mins} minutes ago" if mins < 120 else f"{mins // 60} hours ago"


def make_sensor_tool(cfg: WinstonConfig):
    ha = cfg.providers.ha
    if ha is None or not cfg.tools.sensors:
        return None
    url, token = ha.url.rstrip("/"), os.environ.get(ha.token_env, "")

    async def get_sensor_readings(params: FunctionCallParams):
        lines = []
        async with httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=10) as c:
            for label, pair in cfg.tools.sensors.items():
                lv = (await c.get(f"{url}/api/states/{pair.level}")).json()
                unit = lv.get("attributes", {}).get("unit_of_measurement", "")
                extra = ""
                if pair.state:
                    st = (await c.get(f"{url}/api/states/{pair.state}")).json()
                    extra = f" ({st['state']})"
                lines.append(f"{label}: {lv['state']}{unit}{extra}, reported {_age(lv['last_updated'])}")
        await params.result_callback({"readings": lines,
                                      "note": "Readings older than about 30 minutes may be out of date; say so."})

    schema = FunctionSchema(
        name="get_sensor_readings",
        description="Current value and age of the last report for watched sensors such as phone batteries. "
                    "Use this for those instead of GetLiveContext.",
        properties={}, required=[])
    return schema, get_sensor_readings
