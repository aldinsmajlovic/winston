# /// script
# [tool.winston]
# id = "hevy"
# name = "Hevy"
# description = "Your workouts from Hevy, and a word when you have not trained for a while."
# icon = "dumbbell"
# api = 1
# permissions = { network = ["api.hevyapp.com"] }
# ///
"""Hevy (api.hevyapp.com, read only; needs Hevy Pro and its API key).

Every hour it keeps a copy of the last 120 days of workouts: the first time by paging through them,
then only what changed (`/v1/workouts/events?since=`). Hevy asks callers not to poll exactly on the
hour; an interval job runs from when the extension started, so it does not. The kind "rest" says
when you have not trained for `rest_days`, about the gap since your last workout, so putting it off
follows that gap and a workout ends it."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from winston_sdk import Extension, Field, Nudge, Secret, Settings, SettingsError

ext = Extension()
API = "https://api.hevyapp.com/v1"
KEEP_DAYS = 120
PAGE = 10                                  # Hevy's largest page


class HevySettings(Settings):
    api_key: str = Secret("HEVY_API_KEY", description="From hevy.com/settings?developer (needs Hevy Pro).")
    rest_days: int = Field(3, ge=1, le=30, description="Days without a workout before he mentions it.")
    firmer_after: int = Field(2, ge=1, le=30, level="advanced",
                              description="Days after that before he says it more firmly.")


ext.settings(HevySettings)
days_since = ext.entity("days_since_workout", kind="sensor", name="Days since last workout", unit="d",
                        state_class="measurement", icon="mdi:dumbbell")
this_week = ext.entity("workouts_this_week", kind="sensor", name="Workouts this week", state_class="measurement",
                       icon="mdi:calendar-check")


class HevyError(Exception):
    pass


async def _get(ctx, path: str, key: str | None = None, **params) -> dict:
    r = await ctx.http.get(f"{API}{path}", params=params, headers={"api-key": key or ctx.settings.api_key})
    if r.status_code == 401:
        raise HevyError("Hevy refused the API key (it needs Hevy Pro; the key is at hevy.com/settings?developer)")
    if r.status_code == 429:
        raise HevyError("Hevy says too many requests; the next hourly poll tries again")
    r.raise_for_status()
    return r.json()


def _utc(iso: str) -> str:
    """One form for a time, whatever form Hevy wrote it in ("Z" or "+00:00"): a nudge's about stays put."""
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(timezone.utc).isoformat()


def _slim(w: dict) -> dict:
    """What is kept of a workout: its title, times, and each exercise's sets as [type, kg, reps, s, m]."""
    return {"title": w.get("title") or "Workout", "start": _utc(w["start_time"]), "end": w.get("end_time"),
            "exercises": [{"title": e.get("title", ""), "template": e.get("exercise_template_id"),
                           "sets": [[s.get("type", "normal"), s.get("weight_kg"), s.get("reps"),
                                     s.get("duration_seconds"), s.get("distance_meters")]
                                    for s in e.get("sets", [])]}
                          for e in sorted(w.get("exercises", []), key=lambda e: e.get("index", 0))]}


def _local(ctx, iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(ctx.now().tzinfo)


def _trim(ctx) -> None:
    cutoff = ctx.now() - timedelta(days=KEEP_DAYS)
    ws = ctx.store.setdefault("workouts", {})
    for wid in [i for i, w in ws.items() if _local(ctx, w["start"]) < cutoff]:
        del ws[wid]


def _last(ctx) -> dict | None:
    ws = ctx.store.get("workouts", {})
    return max(ws.values(), key=lambda w: w["start"]) if ws else None


def _days_since(ctx, w: dict) -> int:
    return (ctx.today() - _local(ctx, w["start"]).date()).days


def _when(ctx, w: dict) -> str:
    start = _local(ctx, w["start"])
    days = (ctx.today() - start.date()).days
    return "today" if days == 0 else "yesterday" if days == 1 else f"{start:%A} {start.day} {start:%B}"


def _counts(ctx) -> tuple[int, int]:
    today = ctx.today()
    monday, first = today - timedelta(days=today.weekday()), today.replace(day=1)
    dates = [_local(ctx, w["start"]).date() for w in ctx.store.get("workouts", {}).values()]
    return sum(d >= monday for d in dates), sum(d >= first for d in dates)


@ext.every("1h")
async def poll(ctx):
    """The workouts since the last poll: all of the last 120 days the first time, then Hevy's
    events (updated or deleted) since then."""
    started = ctx.now()
    ws = ctx.store.setdefault("workouts", {})
    since = ctx.store.get("synced")
    if since is None:
        cutoff = started - timedelta(days=KEEP_DAYS)
        page, pages = 1, 1
        while page <= pages and page <= 50:
            data = await _get(ctx, "/workouts", page=page, pageSize=PAGE)
            pages = data.get("page_count", 1)
            batch = data.get("workouts", [])
            for w in batch:
                ws[w["id"]] = _slim(w)
            if not batch or min(_local(ctx, w["start_time"]) for w in batch) < cutoff:
                break
            page += 1
    else:
        page, pages = 1, 1
        while page <= pages and page <= 50:
            data = await _get(ctx, "/workouts/events", since=since, page=page, pageSize=PAGE)
            pages = data.get("page_count", 1)
            for ev in data.get("events", []):
                if ev.get("type") == "deleted":
                    ws.pop(ev.get("id"), None)
                elif ev.get("workout"):
                    ws[ev["workout"]["id"]] = _slim(ev["workout"])
            page += 1
    ctx.store["count"] = (await _get(ctx, "/workouts/count")).get("workout_count")
    ctx.store["synced"] = (started - timedelta(minutes=5)).isoformat()     # a little overlap is harmless
    _trim(ctx)
    last = _last(ctx)
    await days_since.set(_days_since(ctx, last) if last else None)
    await this_week.set(_counts(ctx)[0])


@ext.nudge("rest", title="Time to train", deliver="when_home", every="1h")
async def rest(ctx):
    """No workout for rest_days: about the gap since the last one, firmer firmer_after days later."""
    last = _last(ctx)
    if last is None:
        return None
    days = _days_since(ctx, last)
    s = ctx.settings
    if days < s.rest_days:
        return None
    return Nudge(about=last["start"], level=2 if days >= s.rest_days + s.firmer_after else 1,
                 say=f"It has been {days} days since your last workout.",
                 facts={"days_since": days, "last_workout": {"title": last["title"], "when": _when(ctx, last),
                                                             "exercises": [e["title"] for e in last["exercises"]]}})


def _words(text: str) -> set[str]:
    base = re.sub(r"\(.*?\)", "", text.lower())
    return {w.rstrip("s") for w in re.findall(r"[a-z0-9]+", base) if len(w) > 1}


def _find_exercise(ctx, name: str) -> tuple[str, str | None] | None:
    """The exercise from his workouts that the words name best: (title, template id)."""
    want = _words(name)
    best, score = None, 0.0
    for w in sorted(ctx.store.get("workouts", {}).values(), key=lambda w: w["start"], reverse=True):
        for e in w["exercises"]:
            have = _words(e["title"])
            s = len(want & have) / max(len(want), 1) + (0.5 if want == have else 0)
            if s > score:
                best, score = (e["title"], e["template"]), s
    return best if score >= 0.5 else None


def _set_text(s: list) -> str:
    kind, kg, reps, secs, metres = s
    parts = []
    if kg is not None:
        parts.append(f"{kg:g} kg")
    if reps is not None:
        parts.append(f"{int(reps)} reps")
    if secs is not None:
        parts.append(f"{int(secs) // 60} min {int(secs) % 60} s" if secs >= 60 else f"{int(secs)} s")
    if metres is not None:
        parts.append(f"{metres / 1000:g} km" if metres >= 1000 else f"{metres:g} m")
    return " x ".join(parts[:2]) + (", " + ", ".join(parts[2:]) if len(parts) > 2 else "") or kind


def _best(sets: list[list]) -> list | None:
    """The heaviest set (more reps breaks a tie); for bodyweight the most reps; then longest, farthest."""
    work = [s for s in sets if s[0] != "warmup"] or sets
    for key in (lambda s: (s[1] or 0, s[2] or 0), lambda s: s[2] or 0, lambda s: s[3] or 0, lambda s: s[4] or 0):
        top = max(work, key=key, default=None)
        if top is not None and key(top):
            return top
    return None


async def _exercise(ctx, name: str) -> dict:
    found = _find_exercise(ctx, name)
    if found is None:
        titles = sorted({e["title"] for w in ctx.store.get("workouts", {}).values() for e in w["exercises"]})
        return {"error": f"No exercise like {name!r} in the last {KEEP_DAYS} days", "exercises": titles[:40]}
    title, template = found
    history: list[list] = []
    whens: list[str] = []
    if template:
        try:
            data = await _get(ctx, f"/exercise_history/{template}")
            for h in data.get("exercise_history", []):
                history.append([h.get("set_type", "normal"), h.get("weight_kg"), h.get("reps"),
                                h.get("duration_seconds"), h.get("distance_meters")])
                whens.append(h.get("workout_start_time", ""))
        except Exception as e:                                          # noqa: BLE001  (the kept copy will do)
            ctx.log.warning(f"exercise history: {e}")
    if not history:                                                     # the copy of the last 120 days
        for w in ctx.store.get("workouts", {}).values():
            for e in w["exercises"]:
                if e["title"] == title:
                    history += e["sets"]
                    whens += [w["start"]] * len(e["sets"])
    if not history:
        return {"exercise": title, "error": "No sets logged"}
    latest = max(whens)
    last_sets = [s for s, w in zip(history, whens) if w == latest]
    best = _best(history)
    best_when = whens[history.index(best)] if best else None
    return {"exercise": title,
            "last_time": {"when": _when(ctx, {"start": latest}), "sets": [_set_text(s) for s in last_sets]},
            "best_set": {"set": _set_text(best), "when": _when(ctx, {"start": best_when})} if best else None,
            "sets_logged": len(history)}


@ext.tool(tier="read", filler="Let me look.")
async def get_training(ctx, exercise: str | None = None) -> dict:
    """The user's training from Hevy: days since the last workout, workouts this week and this month,
    and the last workout. With an exercise, its sets last time and the best set logged.

    Args:
        exercise: An exercise as the user said it ("bench press", "squats"), or leave it out.
    """
    last = _last(ctx)
    week, month = _counts(ctx)
    out: dict = {"days_since_last_workout": _days_since(ctx, last) if last else None,
                 "workouts_this_week": week, "workouts_this_month": month,
                 "workouts_ever": ctx.store.get("count")}
    if last:
        out["last_workout"] = {"title": last["title"], "when": _when(ctx, last),
                               "exercises": [e["title"] for e in last["exercises"]]}
    if exercise:
        out["exercise"] = await _exercise(ctx, exercise)
    if ctx.store.get("synced") is None:
        out["note"] = "Hevy has not been read yet"
    return out


@ext.status
async def status(ctx) -> str:
    last = _last(ctx)
    if last is None:
        return "No workout seen yet" if ctx.store.get("synced") else "Not read from Hevy yet"
    days = _days_since(ctx, last)
    ago = "today" if days == 0 else "yesterday" if days == 1 else f"{days} days ago"
    return f"Last workout: {last['title']}, {ago}"


@ext.hint_words
async def hint_words(ctx) -> list[str]:
    """Exercise names, so speech to text hears "Romanian deadlift" right."""
    titles = {re.sub(r"\s*\(.*?\)", "", e["title"]).strip()
              for w in ctx.store.get("workouts", {}).values() for e in w["exercises"]}
    return sorted(t for t in titles if t)[:60]


@ext.validate
async def check_key(ctx, event):
    """A key is tried on save; without one there is nothing to try (the card says it is missing)."""
    if not event.settings.api_key or event.settings.api_key == "HEVY_API_KEY":
        return
    try:
        await _get(ctx, "/user/info", key=event.settings.api_key)
    except HevyError as e:
        raise SettingsError("api_key", str(e)) from e
    except Exception as e:                                              # noqa: BLE001
        raise SettingsError("api_key", f"Hevy could not be reached to check the key: {e}") from e
