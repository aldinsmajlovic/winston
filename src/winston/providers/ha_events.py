"""Home Assistant event listener: winstond subscribes to `announce.ha_event` over HA's websocket
API, so an automation announces through Winston without Winston listening on the LAN. The
automation action:

    - event: winston_announce
      event_data:
        text: "The washing machine is done."
        context: {started: "{{ ... }}", finished: "{{ now().strftime('%H:%M') }}"}

HA only lets admin users subscribe to custom event types; with a non-admin token the
subscription is refused and logged."""
import asyncio
import json
import os
from typing import Callable

import websockets
from loguru import logger
from pydantic import ValidationError

from winston.config.schema import WinstonConfig
from winston.core.announce import Announcement


async def listen(cfg: WinstonConfig, publish: Callable[[Announcement], object]):
    """Run until cancelled, reconnecting with backoff."""
    ha = cfg.providers.ha
    if ha is None or not cfg.announce.enabled or not cfg.announce.ha_event:
        return
    url = ha.url.rstrip("/").replace("http", "ws", 1) + "/api/websocket"
    delay = 2.0
    while True:
        try:
            async with websockets.connect(url, max_size=2**20) as ws:
                await ws.recv()                                             # auth_required
                await ws.send(json.dumps({"type": "auth", "access_token": os.environ.get(ha.token_env, "")}))
                if json.loads(await ws.recv()).get("type") != "auth_ok":
                    logger.error("Home Assistant refused the token; announcements from HA are off")
                    return
                await ws.send(json.dumps({"id": 1, "type": "subscribe_events", "event_type": cfg.announce.ha_event}))
                res = json.loads(await ws.recv())
                if not res.get("success"):
                    logger.error(f"cannot subscribe to {cfg.announce.ha_event} (a non-admin token?): {res.get('error')}")
                    return
                logger.info(f"listening for Home Assistant event {cfg.announce.ha_event}")
                delay = 2.0
                async for raw in ws:
                    msg = json.loads(raw)
                    if msg.get("type") != "event":
                        continue
                    data = msg["event"].get("data") or {}
                    try:
                        publish(Announcement(text=str(data.get("text", "")).strip(),
                                             context=data.get("context") or {}, source="home_assistant"))
                    except ValidationError as e:
                        logger.warning(f"ignored {cfg.announce.ha_event} with bad data {data}: {e.errors()[0]['msg']}")
        except (OSError, websockets.WebSocketException, ValueError) as e:
            logger.warning(f"Home Assistant events: {e}; reconnecting in {delay:.0f} s")
        await asyncio.sleep(delay)
        delay = min(delay * 2, 60)
