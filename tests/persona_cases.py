"""Persona file cases: the two sections, an older flat file, the flat prompt, secret-shaped values, the
name from config and a rename.
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
expect("the prompt opens with his name from config", prompt.startswith("# Winston\n\nYour name is Winston.\n\n- "),
       repr(prompt[:60]))

# A renamed config: the prompt follows assistant.name, not the file's title; the file is untouched.
mira = cfg.model_copy(update={"assistant": cfg.assistant.model_copy(update={"name": "Mira"})})
before = scratch.read_text()
prompt = persona.prompt_text(mira)
expect("a renamed prompt says the new name first", prompt.startswith("# Mira\n\nYour name is Mira.\n\n"), repr(prompt[:60]))
expect("the file's own rules still follow", "You are Winston, a butler." in prompt and "# Winston" not in prompt)
expect("reading the prompt never writes the file", scratch.read_text() == before)
expect("the page still shows the file's title", persona.read(mira)["title"] == "Winston")

scratch.write_text("# Winston\n\n## How you behave\n\n- You are Winston, a butler. Winston's voice is calm.\n"
                   "- Run `winston announce` or ask winstond; WinstonConfig and Winstons stay.\n\n"
                   "## Instructions\n\n- Call GetDateTime for the time.\n")
lines = persona.rename_lines(mira, "Winston")
expect("rename lists the lines with the old name as a whole word",
       lines == ["# Winston", "You are Winston, a butler. Winston's voice is calm."], repr(lines))
expect("a name the file never says lists nothing", persona.rename_lines(mira, "Jeeves") == [])
before = scratch.read_text()
n = persona.rename(mira, "Winston", "Mira")
text = scratch.read_text()
expect("rename rewrites every whole-word place", n == 3 and text.startswith("# Mira\n") and
       "You are Mira, a butler. Mira's voice is calm." in text, repr(text))
expect("rename leaves commands, identifiers and longer words alone",
       "`winston announce`" in text and "winstond" in text and "WinstonConfig" in text and "Winstons" in text)
expect("rename keeps the previous file as .bak", scratch.with_name("persona.md.bak").read_text() == before)
expect("a rename with nothing to change leaves the file alone", persona.rename(mira, "Jeeves", "Mira") == 0
       and scratch.read_text() == text)
expect("the page title follows the file after a rename", persona.read(mira)["title"] == "Mira")
scratch.write_text("- Keep replies short.\n")
expect("a file without a title shows his name", persona.read(mira)["title"] == "Mira")

try:
    persona.write(cfg, ["Use the key fake_key_0123456789abcdefghij to pay."], [])
    expect("a key-shaped value is refused", False)
except persona.PersonaError as e:
    expect("a key-shaped value is refused", "line 1" in str(e), str(e))
p = persona.write(cfg, [], ["Never store passwords, codes or keys; say you cannot keep those."])
expect("talking about secrets is fine", p["instructions"] == ["Never store passwords, codes or keys; say you cannot keep those."])

print(f"\n{'all passed' if not failed else f'{failed} failed'}")
sys.exit(1 if failed else 0)
