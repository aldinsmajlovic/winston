"""Home Assistant's test kit, with custom_components/ from the repository's root importable."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture(autouse=True)
async def core(hass):
    """Home Assistant's own core integration, set up first as on a real one: conversation reads what is exposed."""
    from homeassistant.setup import async_setup_component
    assert await async_setup_component(hass, "homeassistant", {})
    yield
