"""Watched sensors with the age of each reading. Home Assistant's live context carries no
timestamps, so a value can be hours old without the model knowing. Configured under
tools.sensors; read-only (plan §6 rule 4)."""
import time
from datetime import datetime, timezone

from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.services.llm_service import FunctionCallParams

from winston.config.schema import WinstonConfig
from winston.providers.home_assistant import HomeAssistant


def _age(iso: str) -> str:
    mins = int((time.time() - datetime.fromisoformat(iso).astimezone(timezone.utc).timestamp()) // 60)
    return f"{mins} minutes ago" if mins < 120 else f"{mins // 60} hours ago"


def make_sensor_tool(cfg: WinstonConfig):
    if cfg.providers.ha is None or not cfg.tools.sensors:
        return None

    async def get_sensor_readings(params: FunctionCallParams):
        lines, ha = [], HomeAssistant(cfg)
        for label, pair in cfg.tools.sensors.items():
            lv = await ha.get(f"/api/states/{pair.level}", 10)
            unit = lv.get("attributes", {}).get("unit_of_measurement", "")
            extra = ""
            if pair.state:
                st = await ha.get(f"/api/states/{pair.state}", 10)
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
