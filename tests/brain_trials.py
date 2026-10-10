"""Brain trials: the same typed requests against whatever brain the config names, so two models
(or two servers) can be compared on tool use and speed. Each case runs in a fresh conversation
through the text channel with thinking off (as over voice), and passes when every expected tool
was called and the claim guard stayed quiet. Lights touched by the cases are put back afterwards.

Cases are personal (entity names, phrasing), so they live in data/trials/brain.yaml;
examples/trials/brain.yaml is the template. Results go to data/logs/brain_trials/.

    uv run python tests/brain_trials.py            # every case once
    uv run python tests/brain_trials.py --runs 3   # three rounds, for spread
    uv run python tests/brain_trials.py --think    # thinking on, as in the text channel
"""
import argparse
import asyncio
import json
import os
import statistics
import sys
import time

import httpx
import yaml
from loguru import logger
from pipecat.frames.frames import LLMTextFrame
from pipecat.observers.base_observer import BaseObserver, FramePushed

from winston.channels.text import TextSession
from winston.config import data_dir, load, repo_root
from winston.config.loader import resolve

p = argparse.ArgumentParser()
p.add_argument("--runs", type=int, default=1)
p.add_argument("--think", action="store_true")
p.add_argument("--only", help="run only cases whose name contains this")
args = p.parse_args()
os.environ["WINSTON_THINKING"] = "1" if args.think else "0"

CFG = load()
cases_file = data_dir() / "trials" / "brain.yaml"
if not cases_file.exists():
    cases_file = repo_root() / "examples" / "trials" / "brain.yaml"
SPEC = yaml.safe_load(cases_file.read_text())
CASES = [c for c in SPEC["cases"] if not args.only or args.only in c["name"]]

ha = CFG.providers.ha
HA, H = ha.url.rstrip("/"), {"Authorization": f"Bearer {os.environ[ha.token_env]}"}
RESTORE = SPEC.get("restore", [])        # entities put back after every case


def snapshot() -> dict:
    return {e: httpx.get(f"{HA}/api/states/{e}", headers=H, timeout=10).json() for e in RESTORE}


def restore(before: dict):
    for e, s in before.items():
        domain = e.split(".")[0]
        if s["state"] == "off":
            httpx.post(f"{HA}/api/services/{domain}/turn_off", headers=H, json={"entity_id": e}, timeout=10)
            continue
        a = s["attributes"]
        body = {"entity_id": e, **{k: a[k] for k in ("brightness", "color_temp_kelvin") if a.get(k) is not None}}
        httpx.post(f"{HA}/api/services/{domain}/turn_on", headers=H, json=body, timeout=10)


class Watch(BaseObserver):
    """First and last reply text after a request. The claim guard is read from its log records."""
    def __init__(self):
        super().__init__(); self.t0 = None; self.first = self.last = None

    def arm(self):
        self.t0 = time.perf_counter(); self.first = self.last = None

    async def on_push_frame(self, data: FramePushed):
        f = data.frame
        if isinstance(f, LLMTextFrame) and f.text.strip() and self.first is None and self.t0:
            self.first = time.perf_counter() - self.t0
        if isinstance(f, LLMTextFrame) and self.t0:
            self.last = time.perf_counter() - self.t0


def tool_log_since(ts: float) -> list[dict]:
    path = resolve(CFG.logging.tool_calls)
    out = []
    for line in path.read_text().splitlines()[-400:]:
        d = json.loads(line)
        if d["ts"] >= ts and d.get("channel") == "trial":
            out.append(d)
    return out


def matches(expect: str, called: list[str]) -> bool:
    return any(alt in name for alt in expect.split("|") for name in called)


async def run_case(case: dict) -> dict:
    before = snapshot()
    watch = Watch()
    s = TextSession(CFG, mentions=False, channel="trial", observers=[watch]); await s.start()
    ts, turns = time.time(), []
    try:
        for say in case["say"]:
            watch.arm()
            reply = await s.ask(say)
            turns.append({"say": say, "reply": reply, "first_text_s": watch.first, "done_s": watch.last})
    finally:
        await s.close()
    await asyncio.sleep(0.5)
    log = tool_log_since(ts)
    called = [d["tool"] for d in log if d["event"] == "call"]
    guard = any(d["event"] == "guard" for d in log)
    missing = [e for e in case.get("expect", []) if not matches(e, called)]
    forbidden = [e for e in case.get("forbid", []) if matches(e, called)]
    restore(before)
    return {"name": case["name"], "ok": not missing and not forbidden and not guard, "called": called,
            "missing": missing, "forbidden": forbidden, "guard": guard, "turns": turns}


async def main():
    logger.remove(); logger.add(sys.stderr, level="ERROR")
    model = CFG.llm.model
    print(f"brain: {model} at {CFG.llm.base_url}, thinking {'on' if args.think else 'off'}, "
          f"{len(CASES)} cases x {args.runs}\n")
    results = []
    for r in range(args.runs):
        for case in CASES:
            res = await run_case(case); res["run"] = r; results.append(res)
            last = res["turns"][-1]
            sec = lambda v: f"{v:.2f}s" if v else "  -  "
            why = ", ".join(filter(None, [f"missing {res['missing']}" if res["missing"] else "",
                                          f"forbidden {res['forbidden']}" if res["forbidden"] else "",
                                          "claim guard" if res["guard"] else ""]))
            print(f"{'PASS' if res['ok'] else 'FAIL'}  {res['name']:<28} first {sec(last['first_text_s'])}  done {sec(last['done_s'])}  "
                  f"{why}\n      tools {res['called']}\n      said  {last['reply'][:160]!r}")
    passed = sum(r["ok"] for r in results)
    print(f"\n{passed}/{len(results)} passed")
    for key, label in (("first_text_s", "first reply text"), ("done_s", "reply done")):
        xs = [t[key] for r in results for t in r["turns"] if t[key]]
        if xs:
            print(f"{label}: median {statistics.median(xs):.2f}s (min {min(xs):.2f}, max {max(xs):.2f})")
    out = data_dir() / "logs" / "brain_trials"
    out.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    (out / f"{stamp}-{model.replace('/', '_').replace(':', '_')}.json").write_text(json.dumps(
        {"model": model, "base_url": CFG.llm.base_url, "think": args.think, "results": results}, indent=1))
    sys.exit(0 if passed == len(results) else 1)


asyncio.run(main())
