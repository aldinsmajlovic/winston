"""Winston as Home Assistant's conversation agent: Assist's chat, the app and voice satellites reach his brain.
The request goes to winstond's /converse with the device's area, and his reply streams back piece by piece, so
a satellite starts speaking before he has finished."""
import json
import logging
from collections.abc import AsyncIterator
from typing import Any, Literal

import aiohttp

from homeassistant.components import conversation
from homeassistant.const import MATCH_ALL
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar, device_registry as dr, entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .hub import DOMAIN, Hub

_LOGGER = logging.getLogger(__name__)
ANSWER_SECS = 90        # winstond gives up on the brain after 60 s and says so
OUT_OF_REACH = "Sorry, I could not reach Winston."


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities) -> None:
    async_add_entities([WinstonConversation(entry.runtime_data)])


def area_of(hass: HomeAssistant, device_id: str | None, satellite_id: str | None) -> str | None:
    """The area's name: the device's, else its satellite entity's (as Assist over Wyoming reads it)."""
    devs, areas = dr.async_get(hass), ar.async_get(hass)
    aid = (d.area_id if device_id and (d := devs.async_get(device_id)) else None)
    if not aid and satellite_id and (e := er.async_get(hass).async_get(satellite_id)):
        aid = e.area_id or (d.area_id if e.device_id and (d := devs.async_get(e.device_id)) else None)
    return a.name if aid and (a := areas.async_get_area(aid)) else None


class WinstonConversation(conversation.ConversationEntity):
    _attr_has_entity_name = True
    _attr_name = None               # the device's name: "Winston" in the agent picker
    _attr_should_poll = False
    _attr_supports_streaming = True

    def __init__(self, hub: Hub):
        self.hub = hub
        self._attr_unique_id = f"{hub.instance}#conversation"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, hub.instance)})

    @property
    def supported_languages(self) -> list[str] | Literal["*"]:
        return MATCH_ALL

    @property
    def available(self) -> bool:
        return self.hub.available

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(async_dispatcher_connect(self.hass, self.hub.signal, self.async_write_ha_state))

    async def _async_handle_message(self, user_input: conversation.ConversationInput,
                                    chat_log: conversation.ChatLog) -> conversation.ConversationResult:
        body = {"text": user_input.text, "conversation_id": chat_log.conversation_id,
                "device_id": user_input.device_id, "satellite_id": user_input.satellite_id,
                "language": user_input.language,
                "area": area_of(self.hass, user_input.device_id, user_input.satellite_id)}
        end: dict[str, Any] = {}
        said = [c async for c in chat_log.async_add_delta_content_stream(self.entity_id, self._answer(body, end))]
        if not any(isinstance(c, conversation.AssistantContent) and c.content for c in said):
            # Nothing streamed: his whole reply (a scene that speaks for itself has none), or what to say instead.
            chat_log.async_add_assistant_content_without_tools(conversation.AssistantContent(
                agent_id=self.entity_id, content=end.get("error") or end.get("text") or ""))
        return conversation.async_get_result_from_chat_log(user_input, chat_log)

    async def _answer(self, body: dict[str, Any], end: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        """His reply as Home Assistant's deltas; the closing line (or the error) is left in `end`."""
        yield {"role": "assistant"}
        try:
            async with async_get_clientsession(self.hass).post(
                    self.hub.url / "converse", json=body, headers=self.hub.headers,
                    timeout=aiohttp.ClientTimeout(total=ANSWER_SECS, sock_connect=10)) as r:
                if r.status == 401:
                    self.hub.entry.async_start_reauth(self.hass)
                    end["error"] = "Winston no longer knows this Home Assistant. Pair it again."
                    return
                if r.status == 404:
                    end["error"] = "This Winston is too old to answer here. Update him."
                    return
                r.raise_for_status()
                async for raw in r.content:
                    line = json.loads(raw)
                    if "text" in line and len(line) == 1:
                        yield {"content": line["text"]}
                    else:
                        end.update(line)
        except (aiohttp.ClientError, TimeoutError, ValueError) as e:
            _LOGGER.warning("Winston at %s did not answer: %s", self.hub.url, e or type(e).__name__)
            end["error"] = OUT_OF_REACH
        if end.get("error"):
            _LOGGER.info("Winston could not answer: %s", end["error"])
