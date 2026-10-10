"""Turn check: the marked turns replayed through a setup and scored, so a change (another speech engine,
another model, another prompt) is measured on your own mistakes before it is kept
(docs/understanding.md, part 1). Needs the engines up (`winston up`).

    uv run winston check turns                       # the current setup
    uv run winston check turns --with candidate.yaml  # the config with candidate.yaml laid over it

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

Results go to the turn record's checks/ folder, beside the marked turns they were run on, and the
previous run's summary is shown beside this one's."""
import argparse
import asyncio
import json
import re
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

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
    cfg.llm.sampling.temperature = 0.0
    return cfg, name


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


def matches(expect: str, called: list[str]) -> bool:
    return any(alt in name for alt in expect.split("|") for name in called)


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

    observer = TurnObserver()
    s = TextSession(cfg, mentions=False, channel="check", observers=[observer], area=area(cfg, t["room"]) if t.get("room") else None,
                    brain_channel=t["channel"])
    await s.start()
    for tool in s.brain.tools.standard_tools:
        if tool_tier(tool.name) != READ:
            s.brain.llm.register_function(tool.name, stub(tool.name))
    observer.turn = Turn(ts=time.time())
    try:
        reply = await s.ask(row["heard"]) if row["heard"] else ""
    finally:
        await s.close()
    called = [c["name"] for c in observer.turn.tools]
    answered = bool((reply or "").strip() or called)
    row.update(said=reply, tools=called, first_token_ms=observer.turn.first_token_ms,
               for_him_ok=(not answered) if mark["kind"] == "not_addressed" else answered)
    if mark.get("expect") or mark.get("forbid"):
        missing = [e for e in mark.get("expect") or [] if not matches(e, called)]
        forbidden = [e for e in mark.get("forbid") or [] if matches(e, called)]
        row.update(right_thing=not missing and not forbidden, missing=missing, forbidden=forbidden)
    else:
        row["right_thing"] = None
    return row


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
