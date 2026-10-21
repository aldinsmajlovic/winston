"""Tests for Hevy: `winston ext test hevy`. Hevy's answers are made up here, shaped as its API
documents them (api.hevyapp.com/docs); the fake clock is in Stockholm."""
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qsl

import httpx
from winston_sdk.testing import FakeClock, Harness, HandlerFailed

HERE = Path(__file__).parent / "hevy.py"
KEY = "00000000-0000-0000-0000-000000000000"


def workout(wid: str, start: str, title: str = "Push day", exercises: list | None = None) -> dict:
    t = datetime.fromisoformat(start)
    return {"id": wid, "title": title, "start_time": start, "end_time": (t + timedelta(hours=1)).isoformat(),
            "exercises": exercises if exercises is not None else [
                {"index": 0, "title": "Bench Press (Barbell)", "exercise_template_id": "BENCH",
                 "sets": [{"index": 0, "type": "warmup", "weight_kg": 40, "reps": 10},
                          {"index": 1, "type": "normal", "weight_kg": 80, "reps": 5},
                          {"index": 2, "type": "normal", "weight_kg": 82.5, "reps": 3}]},
                {"index": 1, "title": "Pull Up", "exercise_template_id": "PULLUP",
                 "sets": [{"index": 0, "type": "normal", "weight_kg": None, "reps": 9}]}]}


class Hevy:
    """A fake api.hevyapp.com: answers by path (and page), records what was asked."""

    def __init__(self):
        self.workouts: list[dict] = []          # newest first, as Hevy lists them
        self.events: list[dict] = []
        self.history: dict[str, list[dict]] | None = {}
        self.status = 200
        self.asked: list[tuple[str, dict]] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        path, q = request.url.path, dict(parse_qsl(request.url.query.decode()))
        self.asked.append((path, q))
        if request.headers.get("api-key") != KEY or self.status == 401:
            return httpx.Response(401, json={"error": "Unauthorized"})
        page, size = int(q.get("page", 1)), int(q.get("pageSize", 5))
        if size > 10:
            return httpx.Response(400, text="Invalid page size")
        if path == "/v1/workouts":
            chunk = self.workouts[(page - 1) * size: page * size]
            return httpx.Response(200, json={"page": page, "page_count": max(1, -(-len(self.workouts) // size)),
                                             "workouts": chunk})
        if path == "/v1/workouts/count":
            return httpx.Response(200, json={"workout_count": 57})
        if path == "/v1/workouts/events":
            return httpx.Response(200, json={"page": 1, "page_count": 1, "events": self.events})
        if path == "/v1/user/info":
            return httpx.Response(200, json={"data": {"id": "u1", "name": "Someone", "url": ""}})
        if path.startswith("/v1/exercise_history/"):
            if self.history is None:
                return httpx.Response(500, text="Internal Server Error")
            return httpx.Response(200, json={"exercise_history": self.history.get(path.rsplit("/", 1)[1], [])})
        return httpx.Response(404, text="Not found")


def harness(hevy: Hevy, start: str = "2026-10-05T10:00:00", **settings) -> Harness:     # a Monday
    return Harness(HERE, settings={"rest_days": 3, **settings}, secrets={"api_key": KEY},
                   http=httpx.MockTransport(hevy.handle), clock=FakeClock(start, zone="Europe/Stockholm"))


def three_months(hevy: Hevy) -> None:
    """A workout every third day for 150 days, the last on Friday 2 October at 18:00 Stockholm time."""
    last = datetime(2026, 10, 2, 16, 0, tzinfo=UTC)
    hevy.workouts = [workout(f"w{i}", (last - timedelta(days=3 * i)).isoformat().replace("+00:00", "Z"))
                     for i in range(50)]


async def test_the_first_poll_keeps_the_last_120_days():
    hevy = Hevy()
    three_months(hevy)
    h = harness(hevy)
    await h.run("poll")
    kept = h.store["workouts"]
    assert 38 <= len(kept) <= 41, len(kept)                           # 120 days, every third day
    pages = [q["page"] for p, q in hevy.asked if p == "/v1/workouts"]
    assert pages == ["1", "2", "3", "4", "5"], pages                  # stops once a page reaches past 120 days
    assert h.store["count"] == 57
    assert h.entities == {"days_since_workout": 3, "workouts_this_week": 0}, h.entities
    await h.close()


async def test_rest_after_three_days_firmer_after_five_about_the_gap():
    hevy = Hevy()
    three_months(hevy)
    h = harness(hevy, start="2026-10-04T12:00:00")                    # Sunday: two days
    await h.run("poll")
    assert await h.check("rest") == []
    h.clock.set("2026-10-05T10:00:00")
    [n] = await h.check("rest")
    assert n.level == 1 and n.facts["days_since"] == 3 and n.about == "2026-10-02T16:00:00+00:00", n
    assert n.facts["last_workout"]["title"] == "Push day" and n.facts["last_workout"]["when"] == "Friday 2 October"
    h.clock.set("2026-10-07T10:00:00")
    [n2] = await h.check("rest")
    assert n2.level == 2 and n2.about == n.about, n2                  # the same gap, firmer
    await h.close()


async def test_a_new_workout_ends_the_gap():
    hevy = Hevy()
    three_months(hevy)
    h = harness(hevy)
    await h.run("poll")
    assert len(await h.check("rest")) == 1
    hevy.events = [{"type": "updated", "workout": workout("new", "2026-10-05T15:00:00Z", "Legs")}]
    h.clock.set("2026-10-05T18:00:00")
    await h.run("poll")
    since = [q["since"] for p, q in hevy.asked if p == "/v1/workouts/events"]
    assert since == ["2026-10-05T09:55:00+02:00"], since              # from the last poll, a little overlap
    assert await h.check("rest") == []
    assert h.entities["days_since_workout"] == 0 and h.entities["workouts_this_week"] == 1
    await h.close()


async def test_a_deleted_workout_is_forgotten():
    hevy = Hevy()
    three_months(hevy)
    h = harness(hevy)
    await h.run("poll")
    hevy.events = [{"type": "deleted", "id": "w0", "deleted_at": "2026-10-05T09:00:00Z"}]
    await h.run("poll")
    assert "w0" not in h.store["workouts"]
    [n] = await h.check("rest")
    assert n.facts["days_since"] == 6 and n.level == 2, n            # the one before it, 29 September
    await h.close()


async def test_get_training_counts_and_an_exercise_best_and_last():
    hevy = Hevy()
    three_months(hevy)
    hevy.history = {"BENCH": [
        {"workout_start_time": "2026-09-02T16:00:00Z", "set_type": "normal", "weight_kg": 90, "reps": 2},
        {"workout_start_time": "2026-10-02T16:00:00Z", "set_type": "normal", "weight_kg": 80, "reps": 5},
        {"workout_start_time": "2026-10-02T16:00:00Z", "set_type": "normal", "weight_kg": 82.5, "reps": 3}]}
    h = harness(hevy)
    await h.run("poll")
    r = await h.tool("get_training", exercise="bench press")
    assert r["days_since_last_workout"] == 3 and r["workouts_this_week"] == 0 and r["workouts_ever"] == 57, r
    assert r["workouts_this_month"] == 1, r                          # 2 October
    ex = r["exercise"]
    assert ex["exercise"] == "Bench Press (Barbell)", ex
    assert ex["last_time"] == {"when": "Friday 2 October", "sets": ["80 kg x 5 reps", "82.5 kg x 3 reps"]}, ex
    assert ex["best_set"] == {"set": "90 kg x 2 reps", "when": "Wednesday 2 September"}, ex
    await h.close()


async def test_without_hevy_history_the_kept_copy_answers():
    hevy = Hevy()
    three_months(hevy)
    hevy.history = None                                               # Hevy answers 500
    h = harness(hevy)
    await h.run("poll")
    ex = (await h.tool("get_training", exercise="pull ups"))["exercise"]
    assert ex["exercise"] == "Pull Up" and ex["best_set"]["set"] == "9 reps", ex
    await h.close()


async def test_an_unknown_exercise_lists_the_ones_there_are():
    hevy = Hevy()
    three_months(hevy)
    h = harness(hevy)
    await h.run("poll")
    ex = (await h.tool("get_training", exercise="zercher squat"))["exercise"]
    assert "error" in ex and ex["exercises"] == ["Bench Press (Barbell)", "Pull Up"], ex
    await h.close()


async def test_a_refused_key_says_why():
    hevy = Hevy()
    hevy.status = 401
    h = harness(hevy)
    try:
        await h.run("poll")
        raise AssertionError("a refused key passed")
    except HandlerFailed as e:
        assert "Hevy refused the API key" in str(e), e
    errors = await h.configure({"rest_days": 3}, secrets={"api_key": "wrong"})
    assert errors and errors[0]["field"] == "api_key" and "refused" in errors[0]["message"], errors
    assert await h.configure({"rest_days": 4}, secrets={"api_key": ""}) == []    # no key yet: nothing to try
    await h.close()


async def test_status_and_hint_words():
    hevy = Hevy()
    three_months(hevy)
    h = harness(hevy)
    assert await h.status() == "Not read from Hevy yet"
    await h.run("poll")
    assert await h.status() == "Last workout: Push day, 3 days ago"
    assert await h.hint_words() == ["Bench Press", "Pull Up"]
    await h.close()
