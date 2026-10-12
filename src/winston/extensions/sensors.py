# /// script
# [tool.winston]
# id = "sensors"
# name = "Sensors"
# description = "Sensors you pick, such as phone batteries, read with how long ago each one last changed."
# icon = "gauge"
# api = 1
# permissions = { home_assistant = "read" }
# ///
"""Watched sensors with the age of each reading. Home Assistant's live context carries no
timestamps, so a value can be hours old without the brain knowing; this tool says how old."""
from __future__ import annotations

from winston_sdk import Entity, Extension, Field, Model, Settings

ext = Extension()


class Sensor(Model):
    level: str = Entity(domain="sensor", title="Value sensor", description="The sensor he reads, such as a phone's "
                                                                           "battery level.")
    state: str | None = Entity(domain=["sensor", "binary_sensor"], default=None, title="State sensor",
                               description="Optional companion state sensor (charging, etc.).")


class SensorsSettings(Settings):
    sensors: dict[str, Sensor] = Field(default_factory=dict, description="Watched sensors by label; readings come "
                                                                          "with their age.")


ext.settings(SensorsSettings)


def _age(ctx, when) -> str:
    if when is None:
        return "at an unknown time"
    mins = int((ctx.now() - when).total_seconds() // 60)
    return f"{mins} minutes ago" if mins < 120 else f"{mins // 60} hours ago"


@ext.tool(tier="read")
async def get_readings(ctx) -> dict:
    """Current value of watched sensors such as phone batteries, with how long ago each last changed. Use this for
    those instead of GetLiveContext, and mention how old a reading is when it is more than about thirty minutes."""
    lines = []
    for label, s in ctx.settings.sensors.items():
        level = await ctx.ha.state(s.level)
        if level is None:
            lines.append(f"{label}: unknown")
            continue
        unit = level.attributes.get("unit_of_measurement", "")
        state = await ctx.ha.state(s.state) if s.state else None
        extra = f" ({state.state})" if state else ""
        lines.append(f"{label}: {level.state}{unit}{extra}, last changed {_age(ctx, level.last_changed)}")
    if not lines:
        return {"readings": [], "note": "No sensors are watched yet; they are picked on the Sensors card."}
    return {"readings": lines, "note": "Readings older than about 30 minutes may be out of date; say so."}
