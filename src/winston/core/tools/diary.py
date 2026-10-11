"""Diary tool: look up past diary entries by date or a question about what happened."""
from datetime import date, timedelta

from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.services.llm_service import FunctionCallParams

from winston.config.schema import WinstonConfig
from winston.core.diary import Diary
from winston.core.guard import set_tier


def make_diary_tool(cfg: WinstonConfig):
    diary = Diary(cfg)

    async def get_diary(params: FunctionCallParams):
        day = str(params.arguments.get("date", "")).strip()
        if not day:
            entries = diary.entries(7)
            if not entries:
                return await params.result_callback({"result": "No diary entries yet."})
            lines = [f"{d}: {t[:120]}" for d, t in entries]
            return await params.result_callback({"result": "\n".join(lines)})
        # resolve "yesterday", "last tuesday", etc.
        resolved = _resolve_day(day)
        if not resolved:
            return await params.result_callback({"error": f"Could not understand the date: {day}"})
        text = diary.entry(resolved)
        if text:
            return await params.result_callback(
                {"date": resolved, "entry": text,
                 "note": "This is a past diary entry. Recount it as a memory, not as current actions."})
        return await params.result_callback({"date": resolved, "result": "No diary entry for that day."})

    set_tier("get_diary", "act")
    schema = FunctionSchema(
        name="get_diary",
        description="Look up what happened on a past day: what was asked, what you did in the house, "
                    "who was mentioned. Use when the user asks about a past day, or what you did recently. "
                    "With no date, returns the last week's entries.",
        properties={"date": {"type": "string",
                             "description": "A date (2026-10-11), a day name (Tuesday, last Monday), "
                                            "or a word (yesterday, last week). Empty for recent entries."}},
        required=[])
    return schema, get_diary


_DAYS = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
         "friday": 4, "saturday": 5, "sunday": 6}


def _resolve_day(text: str) -> str | None:
    """Turn 'yesterday', 'last tuesday', 'tuesday', a date string into YYYY-MM-DD."""
    w = text.lower().strip().rstrip(".")
    today = date.today()
    if w in ("today", "now"):
        return today.isoformat()
    if w == "yesterday":
        return (today - timedelta(1)).isoformat()
    if w == "day before yesterday":
        return (today - timedelta(2)).isoformat()
    try:
        return date.fromisoformat(w).isoformat()
    except ValueError:
        pass
    last = w.startswith("last ")
    name = w.removeprefix("last ").strip()
    if name in _DAYS:
        target = _DAYS[name]
        diff = (today.weekday() - target) % 7
        if diff == 0:
            diff = 7 if last else 0
        if last and diff < 7:
            diff += 7
        return (today - timedelta(diff)).isoformat()
    return None
