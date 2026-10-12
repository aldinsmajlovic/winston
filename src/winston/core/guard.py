"""Tool tiers (plan §6 rule 4) and the small facts about a turn that several parts read: whether a tool
looks or acts, whether its result is a failure, what names a home request, and which user-role messages
the house wrote. Until 2026-10-11 it was also a claim guard on the reply's wording (docs/decisions.md)."""
import re

from winston.config.schema import WinstonConfig
from winston.core.common import entity_names, words_pattern

READ, ACT = "read", "act"

# Tools that say their own tier (extensions declare one per tool), by the name the brain sees.
DECLARED: dict[str, str] = {}


def set_tier(name: str, tier: str) -> None:
    DECLARED[name] = tier


def tool_tier(name: str) -> str:
    """A tool's declared tier when it has one (an extension's); otherwise read tools only look
    (GetLiveContext, GetDateTime) and everything else acts."""
    if name in DECLARED:
        return DECLARED[name]
    base = name.split("__")[-1].lower()
    return READ if base.startswith("get") else ACT


def result_ok(result) -> bool:
    """HA's MCP server reports failures as a normal result string starting with "Error"."""
    if isinstance(result, dict):
        return not (result.get("error") or result.get("is_error"))
    return not str(result).lstrip().lower().startswith("error")


# Always a target, whatever Home Assistant exposes.
GENERIC_TARGETS = ("light", "lights", "lamp", "lamps", "brightness")


def targets(cfg: WinstonConfig | None = None) -> re.Pattern:
    """The things he can act on: the generic words plus every name an exposed Home Assistant entity
    answers to (common.entity_names)."""
    ha = cfg.providers.home_assistant if cfg else None
    return words_pattern([*GENERIC_TARGETS, *(n for eid, e in (ha.expose if ha else {}).items()
                                              for n in entity_names(eid, e))])


NOTE_MARK = "not said by the user"


def is_note(text: str) -> bool:
    """A user-role message the house wrote, not the user: an announcement. The mark comes near the
    start, after the clock's stamp when it has one ("[06:22, room: Main Room] [Home notification, ...")."""
    return text.lstrip().startswith("[") and NOTE_MARK in text[:160]
