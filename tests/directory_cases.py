"""Directory cases: the map of the house the brain gets up front, and the area each name is in, from a
made-up Home Assistant snapshot. No hardware needed.

    uv run python tests/directory_cases.py
"""
from harness import check, finish_checks

from winston.config.schema import WinstonConfig
from winston.core.directory import device_areas, house_map
from winston.providers.home_assistant import Snapshot

cfg = WinstonConfig.model_validate({"llm": {"model": "x"}, "providers": {"home_assistant": {
    "url": "http://ha.invalid:8123", "expose": {
        "light.desk": {"aliases": ["desk lamp", "Desk Lamp"]},
        "light.hall": {"name": "hall light"},
        "script.movie": {},
        "light.office": {"name": "office lights"},
        "light.loop": {},
        "switch.gone": {}}}}})
snap = Snapshot(
    states={"light.desk": {"attributes": {"friendly_name": "LAMPA 12 GU10", "supported_color_modes": ["color_temp"]}},
            "light.hall": {"attributes": {"friendly_name": "Hall", "supported_color_modes": ["onoff"]}},
            "script.movie": {"attributes": {"friendly_name": "Movie night"}},
            "light.office": {"attributes": {"entity_id": ["light.desk", "light.lamp_not_exposed"]}},
            "light.loop": {"attributes": {"entity_id": ["light.office", "light.loop"]}}},
    registry={"light.desk": {"area_id": "office", "aliases": ["the desk"]},
              "light.hall": {"device_id": "d1", "aliases": ["hall light"]},
              "script.movie": {}},
    areas={"office": "Office", "hall": "Hallway"}, devices={"d1": {"area_id": "hall"}})

m = house_map(cfg, snap)
lines = m.splitlines()
check("a room's line: groups first in their members' area, the user's name first, what it can do, other names once",
      next(x for x in lines if x.startswith("- Office")),
      "- Office: office lights (light group: desk lamp); loop (light group: office lights); desk lamp (light; dims, "
      "warm to cool; also LAMPA 12 GU10, the desk)")
check("a device's area through its device, and its own name before Home Assistant's",
      next(x for x in lines if x.startswith("- Hallway")), "- Hallway: hall light (light; also Hall)")
check("no area is listed last", lines[-1], "- In no room: Movie night (script)")
check("an entity Home Assistant does not have is left out", "gone" in m, False)
check("no Home Assistant read: no map", house_map(cfg, None), "")

areas = device_areas(cfg, snap)
check("every name of a device maps to its area", (areas["desk lamp"], areas["lampa 12 gu10"], areas["the desk"],
                                                  areas["hall light"]), ("Office", "Office", "Office", "Hallway"))
check("a device in no area is not in the areas", "movie night" in areas, False)

finish_checks("directory cases passed")
