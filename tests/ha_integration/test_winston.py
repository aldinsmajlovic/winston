"""custom_components/winston in Home Assistant's own test kit, against a fake winstond listener on 127.0.0.1
that speaks as src/winston/api/ha_link.py does (tests/ha_link_cases.py checks that side).

    cd tests/ha_integration && uv run pytest
"""
import asyncio
import copy
import json
from ipaddress import ip_address

import pytest
from aiohttp import web

from homeassistant import config_entries
from homeassistant.const import CONF_CODE, CONF_HOST, CONF_PORT, CONF_TOKEN, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from custom_components.winston import hub as hub_mod
from custom_components.winston.hub import DOMAIN

INSTANCE = "0123456789abcdef0123456789abcdef"
CODE, TOKEN = "123456", "token-made-up-for-tests"
SNAPSHOT = {
    "instance": INSTANCE, "name": "Winston", "version": "0.1.0",
    "rooms": [{"name": "Kitchen", "state": "idle"}],
    "engines": [{"name": "llm", "title": "Brain", "up": True}],
    "extensions": [{"id": "chores", "name": "Chores", "entities": [
        {"key": "done", "kind": "button", "entry": "Dishes", "name": "Dishes done", "takes": "press", "value": None},
        {"key": "done", "kind": "button", "entry": "Laundry", "name": "Laundry done", "takes": "press", "value": None},
        {"key": "charging", "kind": "switch", "entry": None, "name": "Cheap charging", "takes": "change", "value": False},
        {"key": "count", "kind": "sensor", "entry": None, "name": "Chores done", "state_class": "measurement",
         "value": 3, "attributes": {"last": "Dishes"}},
        {"key": "level", "kind": "number", "entry": None, "name": "Level", "min": 0, "max": 10, "step": 1, "value": 4},
        {"key": "mode", "kind": "select", "entry": None, "name": "Mode", "options": ["calm", "keen"], "value": "calm"},
        {"key": "due", "kind": "sensor", "entry": None, "name": "Next due", "device_class": "timestamp",
         "value": "2026-11-24T18:00:00+01:00"},
        {"key": "done_today", "kind": "event", "entry": None, "name": "Chore done", "options": ["done"],
         "value": None, "at": None}]}]}


class FakeWinston:
    """/pair, /stream and /set, as winstond's listener answers them."""

    def __init__(self):
        self.snapshot = copy.deepcopy(SNAPSHOT)
        self.token = TOKEN
        self.sets: list[dict] = []
        self.set_answer = {"ok": True}
        self.queues: list[asyncio.Queue] = []
        self.runner = None
        self.port = 0

    async def start(self):
        app = web.Application()
        app.add_routes([web.post("/pair", self.pair), web.get("/stream", self.stream), web.post("/set", self.set)])
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        self.port = site._server.sockets[0].getsockname()[1]

    async def stop(self):
        for q in self.queues:
            q.put_nowait(None)
        await self.runner.cleanup()

    def authorized(self, request) -> bool:
        return request.headers.get("Authorization") == f"Bearer {self.token}"

    async def pair(self, request):
        if (await request.json()).get("code") != CODE:
            return web.json_response({"error": "wrong"}, status=403)
        return web.json_response({"token": TOKEN, "instance": INSTANCE, "name": "Winston"})

    async def stream(self, request):
        if not self.authorized(request):
            return web.json_response({"error": "not paired"}, status=401)
        r = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await r.prepare(request)
        q: asyncio.Queue = asyncio.Queue()
        self.queues.append(q)
        await r.write(f"event: snapshot\ndata: {json.dumps(self.snapshot)}\n\n".encode())
        while (item := await q.get()) is not None:
            await r.write(f"event: {item[0]}\ndata: {json.dumps(item[1])}\n\n".encode())
        self.queues.remove(q)
        return r

    def push(self, kind: str, data: dict):
        for q in self.queues:
            q.put_nowait((kind, data))

    def close_streams(self):
        for q in list(self.queues):
            q.put_nowait(None)

    async def set(self, request):
        if not self.authorized(request):
            return web.json_response({"error": "not paired"}, status=401)
        self.sets.append(await request.json())
        return web.json_response(self.set_answer, status=200 if self.set_answer.get("ok") else 500)


@pytest.fixture
async def fake(socket_enabled):
    f = FakeWinston()
    await f.start()
    yield f
    await f.stop()


@pytest.fixture(autouse=True)
def quick_retries(monkeypatch):
    monkeypatch.setattr(hub_mod, "BACKOFF", (0.05,))


async def until(pred, secs: float = 5) -> bool:
    for _ in range(int(secs / 0.05)):
        if pred():
            return True
        await asyncio.sleep(0.05)
    return pred()


async def add(hass: HomeAssistant, fake: FakeWinston):
    from pytest_homeassistant_custom_component.common import MockConfigEntry
    entry = MockConfigEntry(domain=DOMAIN, unique_id=INSTANCE, title="Winston",
                            data={CONF_HOST: "127.0.0.1", CONF_PORT: fake.port, CONF_TOKEN: TOKEN})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    assert await until(lambda: hass.states.get("button.chores_dishes_done") is not None)
    await hass.async_block_till_done()
    return entry


# adding him

async def test_user_flow_pairs_with_the_code(hass: HomeAssistant, fake):
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert r["type"] is FlowResultType.FORM and r["step_id"] == "user"
    r = await hass.config_entries.flow.async_configure(
        r["flow_id"], {CONF_HOST: "127.0.0.1", CONF_PORT: fake.port, CONF_CODE: "000000"})
    assert r["errors"] == {"base": "invalid_code"}
    with_patch = hass.config_entries.flow.async_configure(
        r["flow_id"], {CONF_HOST: "127.0.0.1", CONF_PORT: fake.port, CONF_CODE: f" {CODE} "})
    r = await with_patch
    assert r["type"] is FlowResultType.CREATE_ENTRY
    assert r["title"] == "Winston"
    assert r["data"] == {CONF_HOST: "127.0.0.1", CONF_PORT: fake.port, CONF_TOKEN: TOKEN}
    assert r["result"].unique_id == INSTANCE
    await hass.config_entries.async_unload(r["result"].entry_id)


async def test_user_flow_cannot_connect(hass: HomeAssistant, fake):
    port = fake.port
    await fake.stop()
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    r = await hass.config_entries.flow.async_configure(
        r["flow_id"], {CONF_HOST: "127.0.0.1", CONF_PORT: port, CONF_CODE: CODE})
    assert r["errors"] == {"base": "cannot_connect"}
    await fake.start()


def discovery(port: int, instance: str | None = INSTANCE) -> ZeroconfServiceInfo:
    return ZeroconfServiceInfo(ip_address=ip_address("127.0.0.1"), ip_addresses=[ip_address("127.0.0.1")],
                               hostname="winston.local.", name="Winston._winston._tcp.local.", port=port,
                               type="_winston._tcp.local.", properties={"instance": instance} if instance else {})


async def test_discovered_then_paired(hass: HomeAssistant, fake):
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_ZEROCONF},
                                                  data=discovery(fake.port))
    assert r["type"] is FlowResultType.FORM and r["step_id"] == "pair"
    assert r["description_placeholders"]["name"] == "Winston"
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {CONF_CODE: CODE})
    assert r["type"] is FlowResultType.CREATE_ENTRY and r["data"][CONF_PORT] == fake.port
    r2 = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_ZEROCONF},
                                                   data=discovery(fake.port))
    assert r2["type"] is FlowResultType.ABORT and r2["reason"] == "already_configured"
    r3 = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_ZEROCONF},
                                                   data=discovery(fake.port, None))
    assert r3["type"] is FlowResultType.ABORT and r3["reason"] == "not_winston"
    await hass.config_entries.async_unload(r["result"].entry_id)


# what he shows

async def test_entities_from_the_snapshot(hass: HomeAssistant, fake):
    entry = await add(hass, fake)
    assert hass.states.get("sensor.winston_kitchen_state").state == "idle"
    assert hass.states.get("sensor.winston_kitchen_state").attributes["options"] == hub_mod.STATES
    assert hass.states.get("binary_sensor.winston_brain").state == "on"
    assert er.async_get(hass).async_get("binary_sensor.winston_brain").entity_category == "diagnostic"
    assert hass.states.get("switch.chores_cheap_charging").state == "off"
    count = hass.states.get("sensor.chores_chores_done")
    assert (count.state, count.attributes["state_class"], count.attributes["last"]) == ("3", "measurement", "Dishes")
    assert hass.states.get("number.chores_level").state == "4"
    assert hass.states.get("select.chores_mode").state == "calm"
    assert hass.states.get("sensor.chores_next_due").state == "2026-11-24T17:00:00+00:00"
    assert hass.states.get("event.chores_nudge") is not None
    assert hass.states.get("event.winston_announcement") is not None
    devs = dr.async_get(hass)
    him = devs.async_get_device_by_identifier((DOMAIN, INSTANCE), entry.entry_id)
    chores = devs.async_get_device_by_identifier((DOMAIN, f"{INSTANCE}#chores"), entry.entry_id)
    assert (him.name, chores.name, chores.via_device_id) == ("Winston", "Chores", him.id)
    ents = er.async_get(hass)
    assert ents.async_get("button.chores_dishes_done").unique_id == f"{INSTANCE}#chores#done#Dishes"
    await hass.config_entries.async_unload(entry.entry_id)


async def test_a_new_snapshot_updates_adds_and_removes(hass: HomeAssistant, fake):
    entry = await add(hass, fake)
    snap = copy.deepcopy(SNAPSHOT)
    snap["rooms"][0]["state"] = "speaking"
    ents = snap["extensions"][0]["entities"]
    ents[3]["value"] = 4
    ents[:] = [e for e in ents if e.get("entry") != "Laundry"]
    ents.append({"key": "done", "kind": "button", "entry": "Hoovering", "name": "Hoovering done", "takes": "press"})
    fake.push("snapshot", snap)
    assert await until(lambda: hass.states.get("sensor.winston_kitchen_state").state == "speaking")
    await hass.async_block_till_done()
    assert hass.states.get("sensor.chores_chores_done").state == "4"
    assert hass.states.get("button.chores_hoovering_done") is not None
    assert er.async_get(hass).async_get("button.chores_laundry_done") is None
    assert hass.states.get("button.chores_laundry_done") is None

    snap["extensions"] = []                             # publishing switched off on its card
    fake.push("snapshot", snap)
    assert await until(lambda: hass.states.get("switch.chores_cheap_charging") is None)
    await hass.async_block_till_done()
    assert dr.async_get(hass).async_get_device_by_identifier((DOMAIN, f"{INSTANCE}#chores"), entry.entry_id) is None
    assert dr.async_get(hass).async_get_device_by_identifier((DOMAIN, INSTANCE), entry.entry_id) is not None
    await hass.config_entries.async_unload(entry.entry_id)


async def test_setting_from_home_assistant(hass: HomeAssistant, fake):
    entry = await add(hass, fake)
    await hass.services.async_call("button", "press", {"entity_id": "button.chores_dishes_done"}, blocking=True)
    await hass.services.async_call("switch", "turn_on", {"entity_id": "switch.chores_cheap_charging"}, blocking=True)
    await hass.services.async_call("number", "set_value", {"entity_id": "number.chores_level", "value": 7},
                                   blocking=True)
    await hass.services.async_call("select", "select_option", {"entity_id": "select.chores_mode", "option": "keen"},
                                   blocking=True)
    assert fake.sets == [
        {"extension": "chores", "key": "done", "entry": "Dishes", "value": None, "press": True},
        {"extension": "chores", "key": "charging", "entry": None, "value": True, "press": False},
        {"extension": "chores", "key": "level", "entry": None, "value": 7.0, "press": False},
        {"extension": "chores", "key": "mode", "entry": None, "value": "keen", "press": False}]
    fake.set_answer = {"ok": False, "error": "not now"}
    with pytest.raises(HomeAssistantError, match="not now"):
        await hass.services.async_call("switch", "turn_off", {"entity_id": "switch.chores_cheap_charging"},
                                       blocking=True)
    await hass.config_entries.async_unload(entry.entry_id)


async def test_events(hass: HomeAssistant, fake):
    entry = await add(hass, fake)
    before = hass.states.get("event.chores_chore_done").state
    fake.push("event", {"type": "announcement", "text": "Dinner is ready."})
    assert await until(lambda: hass.states.get("event.winston_announcement").attributes.get("text") == "Dinner is ready.")
    fake.push("event", {"type": "nudge", "extension": "chores", "kind": "due", "level": "normal", "where": "silent",
                        "text": "The dishes."})
    assert await until(lambda: hass.states.get("event.chores_nudge").attributes.get("text") == "The dishes.")
    n = hass.states.get("event.chores_nudge").attributes
    assert (n["event_type"], n["kind"], n["where"]) == ("nudge", "due", "silent")

    snap = copy.deepcopy(SNAPSHOT)
    snap["extensions"][0]["entities"][-1].update(value="done", at=1000.0)
    fake.push("snapshot", snap)
    assert await until(lambda: hass.states.get("event.chores_chore_done").state != before)
    assert hass.states.get("event.chores_chore_done").attributes["event_type"] == "done"
    fired = hass.states.get("event.chores_chore_done").state
    fake.push("snapshot", snap)                         # the same value again, not set again: not fired
    await asyncio.sleep(0.3)
    assert hass.states.get("event.chores_chore_done").state == fired
    await hass.config_entries.async_unload(entry.entry_id)


async def test_unavailable_while_away_then_back(hass: HomeAssistant, fake):
    entry = await add(hass, fake)
    fake.snapshot["rooms"][0]["state"] = "thinking"
    hub = entry.runtime_data
    real_set = hub._snapshot
    hub._snapshot = lambda data: None                   # hold the reconnect's snapshot, to see the gap
    fake.close_streams()
    assert await until(lambda: hass.states.get("switch.chores_cheap_charging").state == STATE_UNAVAILABLE)
    hub._snapshot = real_set
    assert await until(lambda: hass.states.get("sensor.winston_kitchen_state").state == "thinking")
    await hass.config_entries.async_unload(entry.entry_id)


async def test_unpaired_asks_to_pair_again(hass: HomeAssistant, fake):
    entry = await add(hass, fake)
    fake.token = "another"                              # forgotten on Ways to talk
    fake.close_streams()
    assert await until(lambda: bool(hass.config_entries.flow.async_progress_by_handler(DOMAIN)))
    flow = hass.config_entries.flow.async_progress_by_handler(DOMAIN)[0]
    assert flow["context"]["source"] == config_entries.SOURCE_REAUTH
    assert hass.states.get("switch.chores_cheap_charging").state == STATE_UNAVAILABLE
    fake.token = TOKEN
    r = await hass.config_entries.flow.async_configure(flow["flow_id"], {CONF_CODE: CODE})
    assert r["type"] is FlowResultType.ABORT and r["reason"] == "reauth_successful"
    await hass.async_block_till_done()
    assert await until(lambda: hass.states.get("switch.chores_cheap_charging").state == "off")
    await hass.config_entries.async_unload(entry.entry_id)
