"""Speaking up with the real brain (Extensions track step 3's accept, the parts only the model can show):
he words a nudge himself, "Yeah I know Winston, I'm training tomorrow, remind me after work" puts it
off to the right time through follow_up (a memory fact about when work ends, else arriving home), he
says the time back as set, and "why are you telling me this?" names the extension and the rule.

The brain is the voice channel's (thinking off, the spoken prompt) in a text pipeline, against the
running LLM; winstond is a throwaway one with the fake Home Assistant and the trainer fixture standing
in for Hevy. data/ is only read (config for the model, the persona).

Needs the LLM engine up.

    uv run python tests/speak_up_brain.py [trials]
"""
import asyncio
import json
import os
import re
import shutil
import socket
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

DATA = Path(tempfile.mkdtemp(prefix="winston-speak-up-brain-"))
REAL = Path(os.environ.get("WINSTON_DATA") or Path(__file__).resolve().parent.parent / "data")
os.environ["WINSTON_DATA"] = str(DATA)
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tests"))

import httpx  # noqa: E402
import uvicorn  # noqa: E402
import yaml  # noqa: E402
from fake_ha import FakeHA  # noqa: E402

WORK_FACT = "- The user finishes work at 17:00 on weekdays. (noted 2026-09-20)\n"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def setup(api_port: int, ha_port: int):
    real = yaml.safe_load((REAL / "config.yaml").read_text())
    cfg = {"assistant": real.get("assistant", {}), "llm": real["llm"], "engines": real.get("engines", {}),
           "channels": {c: {"enabled": False} for c in ("voice", "satellite", "discord")},
           "providers": {"home_assistant": {"url": f"http://127.0.0.1:{ha_port}", "token_env": "HA_TOKEN",
                                            "expose": {"light.taklampa": {}}}},
           "control": {"port": api_port, "host": "127.0.0.1"},
           "speak_up": {"home": "person.someone"},
           "extensions": {"trainer": {"enabled": True}}}
    (DATA / "config.yaml").write_text(yaml.safe_dump(cfg))
    shutil.copy(REAL / "persona.md", DATA / "persona.md")          # his real instructions
    shutil.copytree(REPO / "examples" / "memory", DATA / "memory")
    (DATA / "extensions").mkdir()
    shutil.copy(REPO / "tests" / "fixtures" / "extensions" / "trainer.py", DATA / "extensions" / "trainer.py")
    (DATA / ".env").write_text('HA_TOKEN="fake-ha-token"\n')
    os.chmod(DATA / ".env", 0o600)


def calls(since: float) -> list[dict]:
    path = DATA / "logs" / "tool_calls.jsonl"
    if not path.is_file():
        return []
    rows = [json.loads(ln) for ln in path.read_text().splitlines()]
    return [r for r in rows if r["ts"] >= since and r.get("event") == "call" and r.get("tool") == "follow_up"]


async def trial(app, api, work_fact: bool, n: int) -> dict:
    from pipecat.frames.frames import LLMMessagesAppendFrame
    import winston.channels.text as text_channel
    from winston.config import load
    from winston.core.announce import nudge_note
    host = app.state.extensions
    nudges = host.nudges
    about = f"2026-09-2{n}T18:00"
    await api.post("/api/extensions/trainer/tools/set_gap", json={"days": 3, "since": about})
    nid = None
    for _ in range(50):
        nid = next((r["id"] for r in nudges.records.values() if r["about"] == about and r["state"] != "ended"), None)
        if nid:
            break
        await asyncio.sleep(0.1)
    rec = nudges.records[nid]
    ev = nudges.payload(rec, nudges.kind("trainer", "rest"))
    about_you = DATA / "memory" / "about-you.md"
    base = (REPO / "examples" / "memory" / "about-you.md").read_text()
    about_you.write_text(base + ("\n" + WORK_FACT if work_fact else ""))

    brain_class = text_channel.Brain
    text_channel.Brain = lambda cfg, channel="text": brain_class(cfg, channel="voice")   # the spoken brain
    try:
        s = await text_channel.TextSession(load(), channel="voice-test").start()
    finally:
        text_channel.Brain = brain_class
    t0 = time.time()
    await s._worker.queue_frames([LLMMessagesAppendFrame(messages=[{"role": "user", "content": nudge_note(ev)}],
                                                         run_llm=True)])
    worded = await s.printer.wait_reply()
    first = calls(t0)
    t1 = time.time()
    reply = await s.ask("Yeah I know Winston, I'm training tomorrow, remind me after work.")
    later = calls(t1)
    rec = nudges.records[nid]
    t2 = time.time()
    why_reply = await s.ask("Why are you telling me this?")
    why = calls(t2)
    await s.close()
    return {"worded": worded, "tools_when_wording": [c["args"] for c in first], "reply": reply,
            "follow_up": [c["args"] for c in later], "put_off": (rec.get("later") or {}).get("text"),
            "until": rec.get("later", {}).get("until") if rec.get("later") else None,
            "home": bool((rec.get("later") or {}).get("home")), "reason": rec.get("reason"),
            "why_reply": why_reply, "why_calls": [c["args"] for c in why], "nid": nid}


def judge(r: dict, work_fact: bool) -> list[str]:
    bad = []
    if r["tools_when_wording"]:
        bad.append(f"called a tool while wording it: {r['tools_when_wording']}")
    if not r["worded"] or r["worded"].strip() == "It has been 3 days since your last workout, sir.":
        bad.append("not worded in his own words")
    ok_call = [a for a in r["follow_up"] if a.get("action") == "later" and a.get("nudge") == r["nid"]]
    if not ok_call:
        bad.append(f"no follow_up later on the nudge: {r['follow_up']}")
    tomorrow = (datetime.now() + timedelta(days=1)).date()
    if work_fact:
        until = datetime.fromtimestamp(r["until"]) if r["until"] else None
        if not (until and until.date() == tomorrow and 17 <= until.hour <= 18 and not r["home"]):
            bad.append(f"with the work fact, not put off to tomorrow at work's end: {r['put_off']}")
    elif not (r["home"] and (r["put_off"] or "").startswith("when you get home tomorrow")):
        bad.append(f"without it, not put off to arriving home tomorrow: {r['put_off']}")
    if not r["reason"] or "train" not in r["reason"].lower():
        bad.append(f"reason not kept: {r['reason']}")
    spoken = r["reply"].lower()
    said_time = r["put_off"] and (re.search(r"\b(17|seventeen|five)\b", spoken) is not None if work_fact else
                                  "home" in spoken)
    if not said_time:
        bad.append(f"did not say the time back: {r['reply']!r}")
    if not any(a.get("action") == "why" for a in r["why_calls"]) or "trainer" not in r["why_reply"].lower():
        bad.append(f"why did not name the extension: {r['why_calls']} {r['why_reply']!r}")
    return bad


async def main(trials: int) -> bool:
    api_port, ha_port = free_port(), free_port()
    setup(api_port, ha_port)
    from winston.api.app import create_app
    from winston.config import load
    ok = True
    async with FakeHA(ha_port, seed="home"):
        app = create_app(load(), autostart=False)
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=api_port, log_level="warning"))
        serving = asyncio.create_task(server.serve())
        while not server.started:
            await asyncio.sleep(0.1)
        host = app.state.extensions
        await host.start()
        api = httpx.AsyncClient(base_url=f"http://127.0.0.1:{api_port}", timeout=60)
        for _ in range(100):
            if host.procs.get("trainer") and host.procs["trainer"].state == "running":
                break
            await asyncio.sleep(0.2)
        n = 0
        for work_fact in (False, True):
            passed = 0
            for i in range(trials):
                n += 1
                r = await trial(app, api, work_fact, n)
                bad = judge(r, work_fact)
                passed += not bad
                print(f"\n[{'work fact' if work_fact else 'no work fact'} {i + 1}] {'ok' if not bad else 'FAIL'}")
                print(f"  worded:   {r['worded']!r}")
                print(f"  reply:    {r['reply']!r}  (follow_up {r['follow_up']}, put off {r['put_off']!r})")
                print(f"  why:      {r['why_reply']!r}")
                for b in bad:
                    print(f"  - {b}")
            print(f"{'with' if work_fact else 'without'} a memory fact about work: {passed}/{trials}")
            ok = ok and passed == trials
        await host.stop_all()
        server.should_exit = True
        await serving
        await api.aclose()
    return ok


if __name__ == "__main__":
    try:
        ok = asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 3))
    finally:
        shutil.rmtree(DATA, ignore_errors=True)
    sys.exit(0 if ok else 1)
