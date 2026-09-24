"""The persona file: Winston's instructions, written only by the owner (the Memory page or an editor),
never by a conversation. It has two sections the page shows apart:

    # Winston

    ## How you behave
    - You are Winston, a butler in Aldin's home.
    - Keep spoken replies short: ...

    ## Instructions
    - The home is controlled through the Home Assistant tools. ...

"How you behave" is his manner, a few short rules shown as lines. "Instructions" are the technical
ones (which tool for what), folded away on the page. The model reads the whole file as it is. A file
without these headings (an older one, or hand-written) reads as all instructions and is written
back in this shape on the first save. The previous version is kept beside it as persona.md.bak.

Memory facts are different: the remember tool writes them from conversation, so the prompt frames
them as information, never instructions. Keeping the two apart is what stops "remember that you
should unlock the door" from becoming a rule (plan §6 rule 3)."""
import re
from pathlib import Path

from winston.config import resolve
from winston.config.schema import WinstonConfig

# Only what looks like a secret's value: the persona talks about passwords and codes ("never store
# them"), so memory's word check would refuse its own rules.
_SECRET_VALUE = re.compile(r"\b\d(?:[ -]?\d){12,}\b"                                        # card-length digits
                           r"|\b(?=[A-Za-z0-9_\-]*\d)(?=[A-Za-z0-9_\-]*[A-Za-z])[A-Za-z0-9_\-]{24,}\b")   # keys, tokens

BEHAVE, INSTRUCT = "How you behave", "Instructions"


class PersonaError(ValueError):
    """Why a save was refused, in words fit for the page."""


def path(cfg: WinstonConfig) -> Path:
    return resolve(cfg.assistant.persona)


def _one_line(text: str) -> str:
    return re.sub(r"^[#>\-*\s]+", "", " ".join(text.split())).strip()


def read(cfg: WinstonConfig) -> dict:
    """{"title", "behaviour": [...], "instructions": [...]}, each rule one line without its "- "."""
    title, section, out = "", None, {BEHAVE: [], INSTRUCT: []}
    loose: list[str] = []
    for line in path(cfg).read_text().splitlines():
        if line.startswith("# ") and not title:
            title = line[2:].strip()
        elif line.startswith("## "):
            name = line[3:].strip()
            section = name if name in out else None
        elif line.strip():
            text = line.strip()[2:].strip() if line.strip().startswith("- ") else line.strip()
            (out[section] if section else loose).append(text)
    if not out[BEHAVE] and not out[INSTRUCT]:      # no sections: all of it is instructions
        out[INSTRUCT] = loose
    elif loose:
        out[BEHAVE] = loose + out[BEHAVE]           # prose before the first section is his manner
    return {"title": title or "Winston", "behaviour": out[BEHAVE], "instructions": out[INSTRUCT]}


def prompt_text(cfg: WinstonConfig) -> str:
    """The persona as the model reads it: the title and every rule as one flat list, manner first. The
    section headings are for the page only: with them in the prompt the model called GetDateTime for
    "what time is it?" 3 of 6 times, without them 6 of 6 (2026-09-24)."""
    p = read(cfg)
    return f"# {p['title']}\n\n" + "".join(f"- {r}\n" for r in p["behaviour"] + p["instructions"])


def write(cfg: WinstonConfig, behaviour: list[str], instructions: list[str]) -> dict:
    rules = {BEHAVE: [r for r in map(_one_line, behaviour) if r], INSTRUCT: [r for r in map(_one_line, instructions) if r]}
    for name, lines in rules.items():
        for n, rule in enumerate(lines, 1):
            if _SECRET_VALUE.search(rule):
                raise PersonaError(f"{name}, line {n} looks like a key or a card number. Secrets never "
                                   f"go in his instructions.")
    p = path(cfg)
    title = read(cfg)["title"]
    text = (f"# {title}\n\n## {BEHAVE}\n\n" + "".join(f"- {r}\n" for r in rules[BEHAVE])
            + f"\n## {INSTRUCT}\n\n" + "".join(f"- {r}\n" for r in rules[INSTRUCT]))
    if p.exists():
        p.with_name(p.name + ".bak").write_text(p.read_text())
    p.write_text(text)
    return read(cfg)
