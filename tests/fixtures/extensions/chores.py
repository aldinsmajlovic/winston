# /// script
# [tool.winston]
# id = "chores"
# name = "Chores"
# description = "Entities of every kind Home Assistant can set, for the integration's tests."
# api = 1
# ///
from winston_sdk import Extension, Field, From, Settings

ext = Extension()


class ChoreSettings(Settings):
    chores: dict[str, str] = Field({"Dishes": "daily", "Laundry": "weekly"})


ext.settings(ChoreSettings)
done = ext.entity("done", kind="button", name="{entry} done", per=From("chores"))
charging = ext.entity("charging", kind="switch", name="Cheap charging")
count = ext.entity("count", kind="sensor", name="Chores done", state_class="measurement")


@done.on_press
async def pressed(ctx, event):
    ctx.store["count"] = ctx.store.get("count", 0) + 1
    ctx.store["last"] = event.entry
    await count.set(ctx.store["count"])


@charging.on_change
async def charging_changed(ctx, event):
    if event.value == "refuse":
        raise ValueError("not now")
    ctx.store["charging"] = event.value


@ext.tool(tier="read", outside=False)
async def store(ctx) -> dict:
    """Everything it has stored."""
    return dict(ctx.store)
