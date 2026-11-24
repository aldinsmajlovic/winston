"""Adding Winston: found on the network (zeroconf) or by address, then paired with the code his Ways to talk
page shows. Pairing again (reauth) takes a new code."""
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import SOURCE_REAUTH, ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_CODE, CONF_HOST, CONF_PORT, CONF_TOKEN
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from .hub import DOMAIN, base_url

DEFAULT_PORT = 8710
CODE = vol.Schema({vol.Required(CONF_CODE): str})


class WinstonConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._host: str | None = None
        self._port = DEFAULT_PORT
        self._name = "Winston"

    async def _pair(self, code: str) -> tuple[dict[str, Any] | None, str | None]:
        """Winston's answer to the code: {token, instance, name}, or an error key."""
        try:
            async with async_get_clientsession(self.hass).post(
                    base_url(self._host or "", self._port) / "pair", json={"code": code.strip()},
                    timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status == 403:
                    return None, "invalid_code"
                r.raise_for_status()
                return await r.json(), None
        except (aiohttp.ClientError, TimeoutError, ValueError):
            return None, "cannot_connect"

    async def _finish(self, body: dict[str, Any]) -> ConfigFlowResult:
        data = {CONF_HOST: self._host, CONF_PORT: self._port, CONF_TOKEN: body["token"]}
        await self.async_set_unique_id(body["instance"])
        if self.source == SOURCE_REAUTH:
            self._abort_if_unique_id_mismatch(reason="wrong_winston")
            return self.async_update_reload_and_abort(self._get_reauth_entry(), data_updates=data)
        self._abort_if_unique_id_configured(updates=data)     # paired again: the new token and address
        return self.async_create_entry(title=body.get("name") or "Winston", data=data)

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._host, self._port = user_input[CONF_HOST].strip(), user_input[CONF_PORT]
            body, error = await self._pair(user_input[CONF_CODE])
            if body:
                return await self._finish(body)
            errors["base"] = error or "cannot_connect"
        schema = vol.Schema({vol.Required(CONF_HOST, default=self._host or vol.UNDEFINED): str,
                             vol.Required(CONF_PORT, default=self._port): int,
                             vol.Required(CONF_CODE): str})
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_zeroconf(self, discovery_info: ZeroconfServiceInfo) -> ConfigFlowResult:
        instance = discovery_info.properties.get("instance")
        if not instance:
            return self.async_abort(reason="not_winston")
        self._host, self._port = str(discovery_info.ip_address), discovery_info.port or DEFAULT_PORT
        await self.async_set_unique_id(instance)
        self._abort_if_unique_id_configured(updates={CONF_HOST: self._host, CONF_PORT: self._port})
        self._name = discovery_info.name.removesuffix("." + discovery_info.type) or "Winston"
        self.context["title_placeholders"] = {"name": self._name}
        return await self.async_step_pair()

    async def async_step_pair(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            body, error = await self._pair(user_input[CONF_CODE])
            if body:
                return await self._finish(body)
            errors["base"] = error or "cannot_connect"
        return self.async_show_form(step_id="pair", data_schema=CODE, errors=errors,
                                    description_placeholders={"name": self._name, "host": f"{self._host}:{self._port}"})

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        self._host, self._port = entry_data[CONF_HOST], entry_data[CONF_PORT]
        self._name = self._get_reauth_entry().title
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            body, error = await self._pair(user_input[CONF_CODE])
            if body:
                return await self._finish(body)
            errors["base"] = error or "cannot_connect"
        return self.async_show_form(step_id="reauth_confirm", data_schema=CODE, errors=errors,
                                    description_placeholders={"name": self._name})
