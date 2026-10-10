# /// script
# [tool.winston]
# id = "inventory"
# name = "Inventory"
# description = "Reads Home Assistant's devices and calls the servers picked in its settings, for the host's tests."
# api = 1
# permissions = { devices = true }
# ///
from winston_sdk import Extension, Extensions, Settings

ext = Extension()


class InventorySettings(Settings):
    machines: list[str] = Extensions("status", default=[], description="Servers whose status it reads.")
    pokers: list[str] = Extensions("poke", default=[], description="Picked for a tool that is not read.")


ext.settings(InventorySettings)


@ext.tool(tier="read")
async def devices(ctx) -> list:
    """Every device Home Assistant knows, as the devices permission shows them."""
    return [vars(d) for d in await ctx.devices()]


@ext.tool(tier="read")
async def call(ctx, server: str, tool: str) -> dict:
    """Calls another extension's or MCP server's tool."""
    return {"result": await ctx.ext(server).call(tool)}
