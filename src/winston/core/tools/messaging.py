"""send_message: Winston messages a person you listed ("tell Omar a fun fact"). Discord only for
now, through its REST API with the bot token, so any channel's brain can send without the Discord
channel process. Recipients are the names in channels.discord.contacts plus "me"; the model can
never message anyone else (plan §6 rule 4: an act tool, logged like every call).

The bot can only DM people who share a server with it, a Discord rule."""
import os

import httpx
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.services.llm_service import FunctionCallParams

from winston.config.schema import WinstonConfig

API = "https://discord.com/api/v10"
MAX = 1500


def recipients(cfg: WinstonConfig) -> dict[str, str]:
    """Lower-cased name -> Discord user id."""
    c = cfg.channels.discord
    out = {name.strip().lower(): uid.strip() for name, uid in c.contacts.items() if uid.strip()}
    if me := c.allowed_id():
        out.setdefault("me", me)
    return out


async def send_discord(token: str, user_id: str, text: str):
    async with httpx.AsyncClient(base_url=API, timeout=10, headers={"Authorization": f"Bot {token}"}) as c:
        r = await c.post("/users/@me/channels", json={"recipient_id": user_id})
        r.raise_for_status()
        r = await c.post(f"/channels/{r.json()['id']}/messages", json={"content": text})
        r.raise_for_status()


def make_send_message_tool(cfg: WinstonConfig):
    c = cfg.channels.discord
    token = os.environ.get(c.token_env, "")
    people = recipients(cfg)
    if not token or not people:
        return None
    names = sorted(people)

    async def send_message(params: FunctionCallParams):
        to = str(params.arguments.get("to", "")).strip().lower()
        text = str(params.arguments.get("text", "")).strip()
        if to not in people:
            return await params.result_callback({"error": f"{to!r} is not a contact; contacts are {names}"})
        if not text or len(text) > MAX:
            return await params.result_callback({"error": f"the message must be 1 to {MAX} characters"})
        try:
            await send_discord(token, people[to], text)
        except httpx.HTTPStatusError as e:
            why = ("they do not share a server with the bot" if e.response.status_code in (400, 403)
                   else f"Discord answered {e.response.status_code}")
            return await params.result_callback({"error": f"could not message {to}: {why}"})
        except httpx.HTTPError as e:
            return await params.result_callback({"error": f"could not reach Discord: {e}"})
        await params.result_callback({"sent_to": to, "text": text})

    schema = FunctionSchema(
        name="send_message",
        description="Send a Discord message to one of the user's contacts, or to the user themself (\"me\"): "
                    "\"tell my friend a fun fact\", \"send me that\". Write the full message text yourself; it is "
                    f"sent exactly as written, from {cfg.assistant.name}. Then confirm briefly.",
        properties={"to": {"type": "string", "enum": names, "description": "Who gets it."},
                    "text": {"type": "string", "description": "The message, as it should arrive."}},
        required=["to", "text"])
    return schema, send_message
