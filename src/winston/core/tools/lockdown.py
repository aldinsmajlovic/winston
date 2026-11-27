"""lockdown: "lock down", "lockdown off". Through winstond (api/lockdown.py), always with the conversation it
was asked in, so on is at once and off waits for the owner's phone: said aloud it never unlocks on its own."""
import httpx
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.services.llm_service import FunctionCallParams

from winston.config.schema import WinstonConfig
from winston.core import lockdown


def make_lockdown_tool(cfg: WinstonConfig, place: str):
    async def lockdown_tool(params: FunctionCallParams):
        args = params.arguments if isinstance(params.arguments, dict) else {}
        on = args.get("on") in (True, 1, "true", "True", "1")   # anything else is off, which waits for the phone
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.post(f"{cfg.control.url}/api/lockdown", json={"on": on, "channel": place})
            r.raise_for_status()
            a = r.json()
        except Exception:                                               # noqa: BLE001
            return await params.result_callback({"error": lockdown.UNAVAILABLE})
        await params.result_callback({"waiting": True, "note": a["note"]} if a.get("waiting")
                                     else {"locked_down": bool(a["on"])})

    schema = FunctionSchema(
        name="lockdown",
        description="Turns lockdown on or off. On, actions wait for the owner's phone and he keeps private things "
                    "to himself; anyone may turn it on. Off waits for the owner's confirmation.",
        properties={"on": {"type": "boolean"}}, required=["on"])
    return schema, lockdown_tool
