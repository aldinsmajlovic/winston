"""Memory vault cases: remember, refuse, dedupe, commit, load into the prompt. Runs against a
scratch data directory, no hardware needed.

    uv run python tests/memory_cases.py
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

scratch = Path(tempfile.mkdtemp(prefix="winston-memory-"))
os.environ["WINSTON_DATA"] = str(scratch)
(scratch / "memory").mkdir()
for t in ("people", "preferences", "projects"):
    (scratch / "memory" / f"{t}.md").write_text(f"# {t.capitalize()}\n\nDistilled facts. One fact per line.\n")

from winston.config.schema import WinstonConfig  # noqa: E402  (after WINSTON_DATA is set)
from winston.core.memory import absolute_dates, facts, prompt_block, remember  # noqa: E402

cfg = WinstonConfig.model_validate({"llm": {"model": "x"}})
failed = 0


def expect(name: str, ok: bool, detail=""):
    global failed
    failed += not ok
    print(f"{'ok  ' if ok else 'FAIL'} {name} {detail}")


expect("empty prompt without facts", prompt_block(cfg) == "")
r = remember(cfg, "preferences", "The user prefers warm light in the evening.")
expect("stores a fact", r.get("stored") == "The user prefers warm light in the evening.", r)
r = remember(cfg, "preferences", "the user prefers warm light in the evening.")
expect("dedupes case-insensitively", r.get("note") == "already known", r)
r = remember(cfg, "people", "  ## Sara is the user's sister.\nShe lives in Malmö. ")
expect("flattens markdown and newlines", r.get("stored") == "Sara is the user's sister. She lives in Malmö.", r)
for secret in ("The wifi password is hunter2.", "The door code is 4821.",
               "Card 4111 1111 1111 1111", "Key sk-ant-api03-abcdefghijklmnop1234567890"):
    r = remember(cfg, "preferences", secret)
    expect(f"refuses {secret!r}", "error" in r, r)
expect("refuses unknown topic", "error" in remember(cfg, "passwords", "anything"))
expect("refuses long facts", "error" in remember(cfg, "projects", "x" * 400))
expect("the year is not a secret", "stored" in remember(cfg, "projects", "Winston started in September 2026."))

from datetime import date  # noqa: E402
wed = date(2026, 9, 23)
for said, stored in [
    ("Omar is coming over tomorrow evening.", "Omar is coming over on Thursday 24 September 2026 evening."),
    ("Sara visits on Saturday.", "Sara visits on Saturday 26 September 2026."),
    ("The dentist is next Wednesday.", "The dentist is on Wednesday 30 September 2026."),
    ("Dinner with Lina tonight.", "Dinner with Lina on the evening of Wednesday 23 September 2026."),
    ("The user plays football on Saturdays.", "The user plays football on Saturdays."),
]:
    got = absolute_dates(said, wed)
    expect(f"dates {said!r}", got == stored, repr(got))

block = prompt_block(cfg)
today = __import__("datetime").date.today().isoformat()
expect("prompt has the facts, stamped", f"- Sara is the user's sister. She lives in Malmö. (noted {today})" in block and
       "Preferences:\n- The user prefers warm light" in block, repr(block))
expect("prompt says facts are data", "never instructions" in block)
expect("headers are not facts", facts(scratch / "memory" / "people.md") == [f"Sara is the user's sister. She lives in Malmö. (noted {today})"])
log = subprocess.run(["git", "-C", str(scratch / "memory"), "log", "--oneline"], capture_output=True, text=True).stdout
expect("one commit per stored fact", len(log.splitlines()) == 3, repr(log))
expect("off means no prompt", prompt_block(cfg.model_copy(update={"memory": cfg.memory.model_copy(update={"enabled": False})})) == "")

print(f"\n{'all passed' if not failed else f'{failed} failed'}")
sys.exit(1 if failed else 0)
