"""Turn check: the marked turns replayed through a setup and scored, so a change (another speech engine,
another model, another prompt) is measured on your own mistakes before it is kept
(docs/understanding.md, part 1). Needs the engines up (`winston up`).

    uv run winston check turns                       # the current setup
    uv run winston check turns --with candidate.yaml  # the config with candidate.yaml laid over it
    uv run winston check turns --phrases tests/music_phrases.yaml --runs 5   # typed requests, five runs each

Each marked turn's audio goes through the setup's speech to text, then its words through a fresh
conversation with the brain of the channel it came from, in its room. Every tool that acts is
stubbed, by its tier (core/guard.py), whatever it is called: nothing in the house moves, no music
plays, no message is sent, nothing is remembered. Reads run for real, within their own daily caps. The
brain runs at temperature 0, so two runs of one setup agree.

Scores:
- heard: words wrong against what you said (the mark's `said`, else what was heard then).
- for him: a turn marked not_addressed is right when he stays silent, any other when he answers.
- right thing: a mark with expect or forbid (the brain trials' substrings, "a|b" for either) passes
  when every expected tool is called and no forbidden one; a mark without them is listed, not scored.
- time: milliseconds to the text and to the first token.

With --phrases, typed requests from a file instead of marked turns, for a kind of request rather than
one mistake: each `say` (after its `before` lines, in the same conversation) scored on the tools the
brain calls for it, by `expect` and `forbid`. One run is at temperature 0; with --runs above one, at
the live temperature, so a request that goes wrong one time in three shows as a rate. The stubs answer
"done", so judge a phrase by its calls and their arguments (kept in the results), not by the reply.
Generic phrasings live in tests/ (music_phrases.yaml); your own, which are personal, in data/. A case's `at`
("23:30") stamps its messages with that time, for a request that belongs to an hour.

Every run waits while a conversation runs on any channel (winstond's talking), and keeps to the
server's last slot (brain.server_slot): on 2026-10-16 an unpinned trial with the box's prompt took the
box's slot, and his next request waited 18 s.

Results go to the turn record's checks/ folder, beside the marked turns they were run on, and the
previous run's summary is shown beside this one's."""
import argparse
import asyncio
import collections
import json
import re
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx
import soundfile as sf
import yaml
from loguru import logger

from winston.channels.text import TextSession
from winston.config import load
from winston.config.schema import WinstonConfig
from winston.core.guard import READ, tool_tier
from winston.core.hints import Hearing
from winston.core.rooms import area
from winston.core.turns import Turn, TurnObserver, TurnStore

p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
p.add_argument("--with", dest="overlay", type=Path, help="a config file laid over the current one")
p.add_argument("--only", help="replay only the turn with this id")
p.add_argument("--phrases", type=Path, help="typed requests to score instead of the marked turns (YAML)")
p.add_argument("--runs", type=int, default=1, help="--phrases: runs per request; above one at the live temperature")
args = p.parse_args()
logger.remove()
logger.add(sys.stderr, level="WARNING")


def merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        out[k] = merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def setup() -> tuple[WinstonConfig, str]:
    cfg = load()
    name = "current"
    if args.overlay:
        over = yaml.safe_load(args.overlay.read_text()) or {}
        cfg = WinstonConfig.model_validate(merge(cfg.model_dump(exclude_none=True), over))
        name = args.overlay.name
    if args.runs == 1:
        cfg.llm.sampling.temperature = 0.0
    return cfg, name


async def quiet(cfg: WinstonConfig):
    """Waits while a conversation runs on any channel: a check's prefill would slow its reply."""
    said = False
    async with httpx.AsyncClient(base_url=cfg.control.url, timeout=3) as h:
        while True:
            try:
                if not (await h.get("/api/status")).json().get("talking"):
                    return
            except (httpx.HTTPError, ValueError):
                return                                  # no winstond: nobody to wait for
            if not said:
                print("    (waiting while a conversation runs)")
                said = True
            await asyncio.sleep(2)


def words(text: str) -> list[str]:
    return re.findall(r"[\w']+", (text or "").lower())


def wer(heard: str, ref: str) -> float:
    """Word error rate: word edits from ref to heard, over ref's length."""
    a, b = words(ref), words(heard)
    if not a:
        return 0.0 if not b else 1.0
    d = list(range(len(b) + 1))
    for i, wa in enumerate(a, 1):
        prev, d[0] = d[0], i
        for j, wb in enumerate(b, 1):
            prev, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, prev + (wa != wb))
    return d[len(b)] / len(a)


def matches(expect: str, called: list) -> bool:
    """Whether any alternative ("a|b") was called. `called` holds names, or calls ({name, args}), where
    "tool(arg)" also needs that argument given: spotify_play(genre) is a genre, spotify_play(what) a name; a
    flag passed as false is not given (spotify_play(fresh))."""
    for alt in expect.split("|"):
        name, _, arg = alt.partition("(")
        for c in called:
            cname, args = (c, {}) if isinstance(c, str) else (c["name"], c.get("args") or {})
            v = args.get(arg.rstrip(")"))
            if name in cname and (not arg or (v is not False and v not in (None, "", [], {}))):
                return True
    return False


def stub(name: str):
    async def handler(params):
        await params.result_callback({"result": "done", "note": f"{name} is stubbed in a turn check"})
    return handler


async def replay(cfg: WinstonConfig, hearing: Hearing, t: dict, store: TurnStore) -> dict:
    mark = t["mark"]
    row = {"id": t["id"], "day": t["day"], "kind": mark["kind"], "then_heard": t["heard"]}
    path = store.audio(t["id"])
    if path is not None:
        pcm, rate = sf.read(path, dtype="int16")
        got = await hearing.hear(pcm, rate, label="check: STT")
        row.update(heard=got.text, stt_ms=got.ms)
    else:
        row.update(heard=t["heard"], stt_ms=None, note="no audio kept; replayed from what was heard then")
    ref = mark.get("said") or t["heard"]
    row["wer"] = round(wer(row["heard"], ref), 3)

    reply, turn = await ask(cfg, t["channel"], t.get("room"), [], row["heard"])
    called = [c["name"] for c in turn.tools]
    answered = bool((reply or "").strip() or called)
    row.update(said=reply, tools=called, first_token_ms=turn.first_token_ms,
               for_him_ok=(not answered) if mark["kind"] == "not_addressed" else answered)
    row.update(score(mark, called))
    return row


class _At(datetime):
    """The clock a case's `at` sets ("23:30"): its messages are stamped with that time, today."""
    hhmm = "12:00"

    @classmethod
    def now(cls, tz=None):
        h, m = map(int, cls.hhmm.split(":"))
        return datetime.now(tz).replace(hour=h, minute=m, second=0, microsecond=0)


async def ask(cfg: WinstonConfig, channel: str, room: str | None, before: list[str], said: str,
              at: str | None = None) -> tuple[str, Turn]:
    """`said` in a fresh conversation on `channel`'s brain, after `before`, every acting tool stubbed, at the
    time `at` when given (the brain's clock: "something I can sleep to" at 13:00 got "I'm not sure what you
    mean" 6 of 10 times); the reply and the turn record of `said` alone."""
    from winston.core import brain
    brain.datetime, _At.hhmm = (_At, at) if at else (datetime, _At.hhmm)
    await quiet(cfg)
    observer = TurnObserver()
    s = TextSession(cfg, mentions=False, channel="check", observers=[observer], area=area(cfg, room) if room else None,
                    brain_channel=channel)
    await s.start()
    for tool in s.brain.tools.standard_tools:
        if tool_tier(tool.name) != READ:
            s.brain.llm.register_function(tool.name, stub(tool.name))
    try:
        for line in before:
            observer.turn = Turn(ts=time.time())
            await s.ask(line)
        observer.turn = Turn(ts=time.time())
        reply = await s.ask(said) if said else ""
    finally:
        await s.close()
    return reply, observer.turn


def score(want: dict, called: list) -> dict:
    if not (want.get("expect") or want.get("forbid")):
        return {"right_thing": None}
    missing = [e for e in want.get("expect") or [] if not matches(e, called)]
    forbidden = [e for e in want.get("forbid") or [] if matches(e, called)]
    return {"right_thing": not missing and not forbidden, "missing": missing, "forbidden": forbidden}


async def phrases(cfg: WinstonConfig, name: str, store: TurnStore) -> int:
    """--phrases: each request `args.runs` times, scored; the results beside the turn checks."""
    spec = yaml.safe_load(args.phrases.read_text()) or {}
    channel, room = spec.get("channel", "satellite"), spec.get("room")
    print(f"{len(spec.get('cases') or [])} requests x {args.runs} through {name} on the {channel} brain, "
          f"{cfg.llm.model} at temperature {cfg.llm.sampling.temperature:g}\n")
    rows = []
    for case in spec.get("cases") or []:
        runs = []
        for _ in range(args.runs):
            reply, turn = await ask(cfg, channel, room, case.get("before") or [], case["say"], case.get("at"))
            calls = [{"name": c["name"], "args": c.get("args")} for c in turn.tools]
            runs.append({"said": reply, "calls": calls, "first_token_ms": turn.first_token_ms, **score(case, calls)})
        right = sum(bool(r["right_thing"]) for r in runs)
        seen = collections.Counter(" ".join(c["name"] for c in r["calls"]) or "no tool" for r in runs)
        print(f"{right}/{len(runs)}  {case['say']!r}{'  after ' + str(case['before']) if case.get('before') else ''}\n"
              f"      {', '.join(f'{k} x{v}' for k, v in seen.most_common())}")
        rows.append({**case, "right": right, "runs": runs})
    total = sum(r["right"] for r in rows)
    print(f"\n{name}: right {total}/{len(rows) * args.runs} ({sum(r['right'] == args.runs for r in rows)}/{len(rows)} "
          f"requests right every run)")
    out = store.root / "checks"
    out.mkdir(parents=True, exist_ok=True)
    f = out / f"{datetime.now():%Y%m%d-%H%M%S}-{args.phrases.stem}.json"
    f.write_text(json.dumps({"setup": name, "phrases": str(args.phrases), "llm": cfg.llm.model, "runs": args.runs,
                             "temperature": cfg.llm.sampling.temperature, "right": total, "cases": rows},
                            ensure_ascii=False, indent=1))
    print(f"\n{f}")
    return 0


def summary(rows: list[dict]) -> dict:
    scored = [r for r in rows if r["right_thing"] is not None]
    stt = [r["stt_ms"] for r in rows if r.get("stt_ms") is not None]
    tok = [r["first_token_ms"] for r in rows if r.get("first_token_ms") is not None]
    return {"turns": len(rows),
            "heard_wer": round(statistics.mean(r["wer"] for r in rows), 3) if rows else None,
            "heard_exact": sum(r["wer"] == 0 for r in rows),
            "for_him": sum(r["for_him_ok"] for r in rows),
            "right_thing": sum(bool(r["right_thing"]) for r in scored), "right_thing_scored": len(scored),
            "stt_ms_median": round(statistics.median(stt)) if stt else None,
            "first_token_ms_median": round(statistics.median(tok)) if tok else None}


def show(name: str, s: dict) -> str:
    return (f"{name}: heard {s['heard_exact']}/{s['turns']} exact (word error {s['heard_wer']:.1%}), for him "
            f"{s['for_him']}/{s['turns']}, right thing {s['right_thing']}/{s['right_thing_scored']} scored, "
            f"text {s['stt_ms_median']} ms, first token {s['first_token_ms_median']} ms (medians)")


async def main() -> int:
    cfg, name = setup()
    store = TurnStore(cfg)
    if args.phrases:
        return await phrases(cfg, name, store)
    marked = [t for t in store.marked() if not args.only or t["id"] == args.only]
    if not marked:
        print(f"No marked turns in {store.marked_dir}. Mark some with `winston turns mark`, by voice or on the page.")
        return 0
    hearing = Hearing.load(cfg)
    print(f"{len(marked)} marked turns through {name}: {cfg.stt.provider}/{cfg.stt.model}, {cfg.llm.model}\n")
    rows = []
    for t in marked:
        r = await replay(cfg, hearing, t, store)
        rows.append(r)
        right = {True: "right thing", False: "wrong thing", None: "not scored"}[r["right_thing"]]
        print(f"{t['day']} {t['id']}  {r['kind']:<13} heard {r['heard']!r}  (word error {r['wer']:.0%})\n"
              f"    tools {r['tools'] or 'none'}, {right}, for him {'ok' if r['for_him_ok'] else 'wrong'}"
              f"{'  missing ' + str(r['missing']) if r.get('missing') else ''}"
              f"{'  forbidden ' + str(r['forbidden']) if r.get('forbidden') else ''}")
    s = summary(rows)
    out = store.root / "checks"                      # beside the marked turns they were run on
    out.mkdir(parents=True, exist_ok=True)
    previous = sorted(out.glob("*.json"))
    f = out / f"{datetime.now():%Y%m%d-%H%M%S}.json"
    f.write_text(json.dumps({"setup": name, "stt": f"{cfg.stt.provider}/{cfg.stt.model}", "llm": cfg.llm.model,
                             "summary": s, "turns": rows}, ensure_ascii=False, indent=1))
    print("\n" + show(name, s))
    if previous:
        last = json.loads(previous[-1].read_text())
        print(show(f"before ({last['setup']}, {previous[-1].stem})", last["summary"]))
    print(f"\n{f}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
