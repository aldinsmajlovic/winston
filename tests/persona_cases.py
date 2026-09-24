"""Persona file cases: the two sections, an older flat file, the flat prompt, secret-shaped values.
Runs on a scratch file, no hardware needed.

    uv run python tests/persona_cases.py
"""
import sys
import tempfile
from pathlib import Path

from winston.config.schema import WinstonConfig
from winston.core import persona

scratch = Path(tempfile.mkdtemp(prefix="winston-persona-")) / "persona.md"
cfg = WinstonConfig.model_validate({"llm": {"model": "x"}, "assistant": {"persona": str(scratch)}})
failed = 0


def expect(name: str, ok: bool, detail=""):
    global failed
    failed += not ok
    print(f"{'ok  ' if ok else 'FAIL'} {name} {detail}")


scratch.write_text("# Winston\n\nYou are Winston.\n\n- Keep replies short.\n- Call GetDateTime for the time.\n")
p = persona.read(cfg)
expect("a flat file reads as all instructions", p["behaviour"] == [] and len(p["instructions"]) == 3, repr(p))

p = persona.write(cfg, ["You are Winston, a butler.", "  - Keep replies short.  "], ["Call GetDateTime for the time.", ""])
expect("write keeps two sections, one line each", p == {"title": "Winston", "behaviour": ["You are Winston, a butler.",
       "Keep replies short."], "instructions": ["Call GetDateTime for the time."]}, repr(p))
text = scratch.read_text()
expect("the file has both headings", "## How you behave" in text and "## Instructions" in text)
expect("the old version is kept", scratch.with_name("persona.md.bak").read_text().startswith("# Winston\n\nYou are Winston.\n"))
prompt = persona.prompt_text(cfg)
expect("the prompt is one flat list, no section headings", "##" not in prompt and prompt.count("\n- ") == 3, repr(prompt))
expect("manner comes first in the prompt", prompt.index("butler") < prompt.index("GetDateTime"))

try:
    persona.write(cfg, ["Use the key fake_key_0123456789abcdefghij to pay."], [])
    expect("a key-shaped value is refused", False)
except persona.PersonaError as e:
    expect("a key-shaped value is refused", "line 1" in str(e), str(e))
p = persona.write(cfg, [], ["Never store passwords, codes or keys; say you cannot keep those."])
expect("talking about secrets is fine", p["instructions"] == ["Never store passwords, codes or keys; say you cannot keep those."])

print(f"\n{'all passed' if not failed else f'{failed} failed'}")
sys.exit(1 if failed else 0)
