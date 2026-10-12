"""Tests for Sensors: `winston ext test sensors`. A made-up phone in the fake Home Assistant."""
from datetime import timedelta
from pathlib import Path

from winston_sdk.testing import FakeClock, FakeHA, Harness

HERE = Path(__file__).parent / "sensors.py"
PHONE = {"Phone": {"level": "sensor.phone_battery_level", "state": "sensor.phone_battery_state"}}


def harness(sensors: dict, minutes_ago: int = 45) -> Harness:
    clock = FakeClock("2026-10-12T10:00:00", zone="Europe/Stockholm")
    ha = FakeHA()
    then = clock.now() - timedelta(minutes=minutes_ago)
    ha.set("sensor.phone_battery_level", "80", {"unit_of_measurement": "%"}, at=then)
    ha.set("sensor.phone_battery_state", "charging", at=then)
    return Harness(HERE, settings={"sensors": sensors}, ha=ha, clock=clock)


async def test_a_reading_comes_with_its_state_and_age():
    h = harness(PHONE)
    res = await h.tool("get_readings")
    assert res["readings"] == ["Phone: 80% (charging), last changed 45 minutes ago"], res
    await h.close()


async def test_old_readings_are_counted_in_hours():
    h = harness({"Phone": {"level": "sensor.phone_battery_level"}}, minutes_ago=200)
    res = await h.tool("get_readings")
    assert res["readings"] == ["Phone: 80%, last changed 3 hours ago"], res
    await h.close()


async def test_a_missing_sensor_is_unknown_and_none_picked_says_so():
    h = harness({"Old phone": {"level": "sensor.gone_battery_level"}})
    assert (await h.tool("get_readings"))["readings"] == ["Old phone: unknown"]
    await h.close()
    h = harness({})
    res = await h.tool("get_readings")
    assert res["readings"] == [] and "Sensors card" in res["note"], res
    await h.close()
