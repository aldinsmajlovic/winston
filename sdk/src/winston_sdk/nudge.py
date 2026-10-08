"""Nudges: what an extension would like him to say. The core decides whether, when and where."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Link(BaseModel):
    """A link that goes with a nudge: buttons on the phone, text on Discord, links on the Talk page.
    Never spoken."""
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=60)
    url: str = Field(pattern=r"^https?://", max_length=2000)


class Nudge(BaseModel):
    """One nudge. `about` names the one occurrence of the thing it is about (a workout gap by the
    workout's start, a plant by its sensor, a story by its link) and must stay the same while it is
    that occurrence: everything the core remembers about a nudge belongs to its kind and `about`."""
    model_config = ConfigDict(extra="forbid")
    about: str = Field(min_length=1, max_length=200)
    say: str = Field(min_length=1, max_length=300, description="Said as is when his own wording fails.")
    facts: dict[str, Any] = Field(default_factory=dict, description="What he words it from.")
    level: int = Field(1, ge=1, le=9)
    urgent: bool = False
    not_before: datetime | None = None
    expires: datetime | timedelta | None = None
    actions: list[str] | None = None
    links: list[Link] = Field(default_factory=list, max_length=3)


class NudgeInfo(BaseModel):
    """A nudge as handlers see it: in `event.nudge` for actions and `@ext.on_nudge`."""
    kind: str
    about: str
    level: int = 1
    facts: dict[str, Any] = Field(default_factory=dict)
    links: list[Link] = Field(default_factory=list)


class NudgeState(NudgeInfo):
    """Where one of its nudges stands, from `ctx.nudges()`."""
    state: Literal["waiting", "held", "said", "dismissed", "ended"]
    said_at: datetime | None = None
    held_until: datetime | None = None
