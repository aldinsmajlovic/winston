"""The directory (docs/understanding.md, part 2): what is in his world and what a name means there. So far
Home Assistant's side, read from its registry: the map of the house the brain gets up front, so a device it
names needs no lookup, and the area each name is in (rooms.scoped_by_name)."""
from __future__ import annotations

import time
from typing import TYPE_CHECKING

from loguru import logger

from winston.config.schema import WinstonConfig
from winston.core.common import entity_names, key

if TYPE_CHECKING:
    from winston.providers.home_assistant import Snapshot

READ_SECS = 600.0
_read: tuple[float, tuple[str, ...], Snapshot | None] = (-READ_SECS, (), None)
COLOUR = {"hs", "xy", "rgb", "rgbw", "rgbww"}


async def home(cfg: WinstonConfig) -> Snapshot | None:
    """Home Assistant's names and areas for the exposed entities, read at most every ten minutes and again when
    the exposed list changes. When it cannot be read, the last read (None before any)."""
    global _read
    ha = cfg.providers.ha
    if ha is None:
        return None
    at, ids, snap = _read
    want = tuple(ha.expose)
    if ids == want and time.monotonic() - at < READ_SECS:
        return snap
    from winston.providers.home_assistant import HomeAssistant
    try:
        snap = await HomeAssistant(cfg).snapshot(list(want))
    except Exception as e:                                                  # noqa: BLE001
        logger.warning(f"Home Assistant's names and areas not read: {e}")
    _read = (time.monotonic(), want, snap)
    return snap


def device_areas(cfg: WinstonConfig, snap: Snapshot | None) -> dict[str, str]:
    """Every name an exposed device answers to (its Home Assistant name and aliases, common.entity_names), folded,
    mapped to the Home Assistant area it is in. Empty without the registry (no admin token)."""
    ha, found = cfg.providers.ha, {}
    for eid, ent in (ha.expose if ha and snap else {}).items():
        if where := snap.area(eid)[1]:
            for n in (snap.display_name(eid), *entity_names(eid, ent), *snap.aliases(eid)):
                if n:
                    found[key(n)] = where
    return found


def _can(state: dict | None) -> str:
    """What a light can do besides on and off, from its colour modes."""
    modes = set((state or {}).get("attributes", {}).get("supported_color_modes") or [])
    return ", ".join(w for w, ok in (("dims", modes - {"onoff"}), ("warm to cool", "color_temp" in modes),
                                     ("colour", modes & COLOUR)) if ok)


def _cleans(snap: Snapshot, eid: str) -> str:
    """The areas a vacuum can be sent to clean: those its rooms are mapped to in Home Assistant (the clean area
    feature), which need not hold anything else he controls."""
    mapping = ((((snap.registry or {}).get(eid) or {}).get("options") or {}).get("vacuum") or {}).get("area_mapping")
    names = [snap.areas[a] for a, rooms in (mapping or {}).items() if rooms and a in snap.areas]
    return f"cleans {', '.join(names)}" if names else ""


def house_map(cfg: WinstonConfig, snap: Snapshot | None) -> str:
    """The exposed entities by area: each by the name the user gave it (else Home Assistant's), its kind, a group's
    members, what a light can do and the other names it answers to. A group with no area of its own is in its
    members' area. No states, so the prompt stays the same between turns."""
    ha = cfg.providers.ha
    if ha is None or snap is None:
        return ""
    names: dict[str, list[str]] = {}            # exposed entity -> its names, the one to call it by first
    for eid, ent in ha.expose.items():
        if snap.known(eid):
            found: dict[str, str] = {}
            for n in (ent.name, *ent.aliases, snap.display_name(eid), *snap.aliases(eid)):
                if n:
                    found.setdefault(key(n), n)
            names[eid] = list(found.values()) or [eid.split(".", 1)[1].replace("_", " ")]

    def members(eid: str) -> list[str]:
        """A group's exposed members (a scene lists what it sets, which does not make it a group)."""
        ids = [] if eid.startswith("scene.") else (snap.states.get(eid) or {}).get("attributes", {}).get("entity_id")
        return [m for m in ids or [] if m in names and m != eid]

    def where(eid: str, seen: frozenset = frozenset()) -> str:
        rooms = {where(m, seen | {eid}) for m in members(eid) if m not in seen}
        return snap.area(eid)[1] or (rooms.pop() if len(rooms) == 1 else "")

    rooms: dict[str, list[tuple[bool, str]]] = {}
    for eid, (name, *others) in names.items():
        domain = eid.split(".", 1)[0]
        group = members(eid)
        parts = [f"{domain.replace('_', ' ')} group: {', '.join(names[m][0] for m in group)}" if group
                 else domain.replace("_", " ")]
        if domain == "light" and (can := _can(snap.states.get(eid))):
            parts.append(can)
        if cleans := _cleans(snap, eid):
            parts.append(cleans)
        if others:
            parts.append(f"also {', '.join(others)}")
        rooms.setdefault(where(eid), []).append((not group, f"{name} ({'; '.join(parts)})"))
    if not rooms:
        return ""
    lines = [f"- {room or 'In no room'}: {'; '.join(line for _, line in sorted(items, key=lambda i: i[0]))}"
             for room, items in sorted(rooms.items(), key=lambda r: (not r[0], r[0]))]
    return ("# The house\n\nWhat you can see and control, by Home Assistant area: each by the name the user gave it, "
            "its kind, and the other names it answers to. Call the tools with these names; you need no lookup to "
            "find a device. A group changes all its members in one call: for all the lights in a room, call its "
            "group once, not each light. Whether something is on, how bright, and the rest of its state are not "
            "here: for those call GetLiveContext.\n\n" + "\n".join(lines))
