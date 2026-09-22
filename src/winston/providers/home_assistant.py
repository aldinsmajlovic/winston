"""Home Assistant provider: keeps HA's Assist exposure equal to the allowlist in config
(providers.home_assistant.expose), sets names and aliases, and turns off HA's auto-expose.
Least privilege (plan §6 rule 5): the model sees exactly what is listed, nothing else."""
import os

import aiohttp

from winston.config.schema import WinstonConfig


class HomeAssistant:
    def __init__(self, cfg: WinstonConfig):
        ha = cfg.providers.home_assistant
        if ha is None:
            raise RuntimeError("providers.home_assistant is not configured")
        self.cfg, self.ha = cfg, ha
        self.url, self.token = ha.url.rstrip("/"), os.environ.get(ha.token_env, "")
        if not self.token:
            raise RuntimeError(f"{ha.token_env} is not set")

    @property
    def _headers(self):
        return {"Authorization": f"Bearer {self.token}"}

    async def states(self) -> list[dict]:
        async with aiohttp.ClientSession() as s:
            return await (await s.get(f"{self.url}/api/states", headers=self._headers)).json()

    async def ping(self) -> dict:
        async with aiohttp.ClientSession() as s:
            r = await s.get(f"{self.url}/api/config", headers=self._headers)
            j = await r.json()
            return {"version": j.get("version"), "location": j.get("location_name"),
                    "mcp_server": "mcp_server" in j.get("components", [])}

    async def apply_exposure(self, list_only: bool = False) -> dict:
        states = await self.states()
        ids = {e["entity_id"] for e in states}
        extra = [e["entity_id"] for e in states if e["entity_id"].split(".")[0] in self.ha.expose_domains]
        missing = [e for e in self.ha.expose if e not in ids]
        if missing:
            return {"error": f"not found in HA: {missing}"}
        targets = list(self.ha.expose) + extra
        result = {"exposed": [], "unexposed": 0}
        async with aiohttp.ClientSession() as s, s.ws_connect(f"{self.url}/api/websocket") as ws:
            assert (await ws.receive_json())["type"] == "auth_required"
            await ws.send_json({"type": "auth", "access_token": self.token})
            assert (await ws.receive_json())["type"] == "auth_ok"
            n = 0

            async def call(**msg):
                nonlocal n; n += 1
                await ws.send_json({"id": n, **msg})
                while True:
                    r = await ws.receive_json()
                    if r.get("id") == n:
                        if not r.get("success", True):
                            raise RuntimeError(f"{msg.get('type')}: {r.get('error')}")
                        return r.get("result")

            if not list_only:
                current = await call(type="homeassistant/expose_entity/list") or {}
                stray = [eid for eid, v in current.get("exposed_entities", {}).items()
                         if v.get("conversation") and eid not in targets]
                if stray:
                    await call(type="homeassistant/expose_entity", assistants=["conversation"],
                               entity_ids=stray, should_expose=False)
                    result["unexposed"] = len(stray)
                await call(type="homeassistant/expose_new_entities/set", assistant="conversation", expose_new=False)
                await call(type="homeassistant/expose_entity", assistants=["conversation"],
                           entity_ids=targets, should_expose=True)
                for eid, ent in self.ha.expose.items():
                    upd = {}
                    if ent.aliases:
                        upd["aliases"] = ent.aliases
                    if ent.name:
                        upd["name"] = ent.name
                    if upd:
                        await call(type="config/entity_registry/update", entity_id=eid, **upd)
            exposed = await call(type="homeassistant/expose_entity/list") or {}
            result["exposed"] = sorted(eid for eid, v in exposed.get("exposed_entities", {}).items()
                                       if v.get("conversation"))
        return result
