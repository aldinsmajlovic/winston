"""Winston's binary sensor entities (entity.py)."""
from .entity import setup_platform

async_setup_entry = setup_platform("binary_sensor")
