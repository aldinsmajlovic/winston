"""Memory vault cases: remember, refuse, dedupe, commit, load into the prompt, expiry, update and
forget by the tools, topic descriptions. Runs against a scratch data directory seeded from
examples/memory, no hardware needed.

    uv run python tests/memory_cases.py
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

scratch = Path(tempfile.mkdtemp(prefix="winston-memory-"))
os.environ["WINSTON_DATA"] = str(scratch)
shutil.copytree(Path(__file__).resolve().parents[1] / "examples" / "memory", scratch / "memory")

from winston.config.schema import WinstonConfig  # noqa: E402  (after WINSTON_DATA is set)
from winston.core.memory import absolute_dates, facts, prompt_block, remember  # noqa: E402

cfg = WinstonConfig.model_validate({"llm": {"model": "x"}})
failed = 0


def expect(name: str, ok: bool, detail=""):
    global failed
    failed += not ok
    print(f"{'ok  ' if ok else 'FAIL'} {name} {detail}")


expect("empty prompt without facts", prompt_block(cfg) == "")
r = remember(cfg, "about-you", "The user prefers warm light in the evening.")
expect("stores a fact", r.get("stored") == "The user prefers warm light in the evening.", r)
r = remember(cfg, "about-you", "the user prefers warm light in the evening.")
expect("dedupes case-insensitively", r.get("note") == "already known", r)
r = remember(cfg, "people", "  ## Sara is the user's sister.\nShe lives in Malmö. ")
expect("flattens markdown and newlines", r.get("stored") == "Sara is the user's sister. She lives in Malmö.", r)
for secret in ("The wifi password is hunter2.", "The door code is 4821.",
               "Card 4111 1111 1111 1111", "Key sk-ant-api03-abcdefghijklmnop1234567890"):
    r = remember(cfg, "about-you", secret)
    expect(f"refuses {secret!r}", "error" in r, r)
expect("refuses unknown topic", "error" in remember(cfg, "passwords", "anything"))
expect("refuses long facts", "error" in remember(cfg, "home", "x" * 400))
expect("the year is not a secret", "stored" in remember(cfg, "home", "Winston started in September 2026."))

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
    expect(f"dates stay as they are the second time {stored!r}", absolute_dates(stored, wed) == stored,
           absolute_dates(stored, wed))

block = prompt_block(cfg)
today = __import__("datetime").date.today().isoformat()
expect("prompt has the facts, stamped", f"- Sara is the user's sister. She lives in Malmö. (noted {today})" in block and
       "About you:\n- The user prefers warm light" in block, repr(block))
expect("prompt says facts are data", "never instructions" in block)
expect("headers are not facts", facts(scratch / "memory" / "people.md") == [f"Sara is the user's sister. She lives in Malmö. (noted {today})"])
log = subprocess.run(["git", "-C", str(scratch / "memory"), "log", "--oneline"], capture_output=True, text=True).stdout
expect("one commit per stored fact", len(log.splitlines()) == 3, repr(log))
expect("off means no prompt", prompt_block(cfg.model_copy(update={"memory": cfg.memory.model_copy(update={"enabled": False})})) == "")

# The Memory page's edits: same checks as remember, facts addressed by their stored line, Undo in place.
from datetime import date  # noqa: E402

from winston.core import memory  # noqa: E402

people = lambda: [f["text"] for f in next(t for t in memory.view(cfg) if t["name"] == "people")["facts"]]  # noqa: E731
a = memory.add(cfg, "people", "Omar is a friend who likes animals.")
expect("page add appends", people()[-1] == "Omar is a friend who likes animals.", repr(people()))
for bad, why in (("The alarm code is 4411.", "secret"), ("Omar is a friend who likes animals.", "Already known")):
    try:
        memory.add(cfg, "people", bad); expect(f"page add refuses {why}", False)
    except memory.MemoryEditError as e:
        expect(f"page add refuses {why}", why.lower() in str(e).lower(), str(e))
b = memory.replace(cfg, "people", a["id"], "Omar is a friend who likes octopuses.")
expect("page edit replaces in place", people()[-1] == "Omar is a friend who likes octopuses.", repr(people()))
try:
    memory.replace(cfg, "people", a["id"], "anything"); expect("page edit of a stale id refused", False)
except memory.MemoryEditError:
    expect("page edit of a stale id refused", True)
first = next(t for t in memory.view(cfg) if t["name"] == "people")["facts"][0]
at = memory.forget(cfg, "people", first["id"])
expect("page forget removes", first["text"] not in people() and at == 0)
memory.add(cfg, "people", first["text"], index=at, noted=date.fromisoformat(first["noted"]))
expect("undo puts it back first with its date", people()[0] == first["text"] and
       next(t for t in memory.view(cfg) if t["name"] == "people")["facts"][0]["id"] == first["id"])
stem = memory.new_topic(cfg, "home life")
expect("new topic is a file with a title", stem == "home-life" and memory.title(cfg, stem) == "Home life")
memory.add(cfg, stem, "The user waters the plants on Sundays.")
expect("a new topic's facts reach the prompt", "Home life:\n- The user waters the plants" in prompt_block(cfg))
expect("size counts the facts", 0 < memory.size(cfg) < memory.PROMPT_WARN)

# Expiry: a future date in the fact, or remember's until, stamps "until"; past words do not; an
# expired fact stays in the file and on the page but leaves the prompt and the size meter.
from datetime import timedelta  # noqa: E402

day = date.today()
iso = lambda d: d.isoformat()  # noqa: E731
plans = lambda: next(t for t in memory.view(cfg) if t["name"] == "plans")["facts"]  # noqa: E731
r = remember(cfg, "plans", "The user has the dentist tomorrow at ten.")
expect("a future date sets until", r.get("until") == iso(day + timedelta(1)) and
       plans()[-1]["until"] == iso(day + timedelta(1)) and
       plans()[-1]["id"].endswith(f"(noted {iso(day)}, until {iso(day + timedelta(1))})"), (r, plans()))
for said in ("The user went to the gym yesterday.", "The user cooks pasta tonight.", "The user is home today."):
    r = remember(cfg, "plans", said)
    expect(f"no until from {said!r}", "until" not in r and plans()[-1]["until"] is None, r)
r = remember(cfg, "plans", "The user's sister visits.", until="this week")
sunday = day + timedelta(6 - day.weekday())
expect("until 'this week' is Sunday", r.get("until") == iso(sunday), r)
expect("until in the past is refused", "error" in remember(cfg, "plans", "Old news.", until="2020-01-01"))
expect("until that is no date is refused", "error" in remember(cfg, "plans", "Something.", until="whenever"))
thu = date(2026, 9, 24)
for said, want in [("2026-09-29", date(2026, 9, 29)), ("Friday", date(2026, 9, 25)), ("tomorrow", date(2026, 9, 25)),
                   ("this week", date(2026, 9, 27)), ("next week", date(2026, 10, 4)),
                   ("this month", date(2026, 9, 30)), ("29 September", date(2026, 9, 29)),
                   ("until Tuesday 29 September 2026", date(2026, 9, 29)), ("March 3", date(2027, 3, 3))]:
    got = memory.until_date(said, thu)
    expect(f"until {said!r}", got == want, repr(got))

size_before, block_before = memory.size(cfg), prompt_block(cfg)
path = scratch / "memory" / "plans.md"
path.write_text(path.read_text() + f"- The user had a haircut booked. (noted {iso(day - timedelta(9))}, until {iso(day - timedelta(2))})\n")
expired = [f for f in plans() if f["expired"]]
expect("an expired fact is marked on the page", [f["text"] for f in expired] == ["The user had a haircut booked."], expired)
expect("an expired fact is not in the prompt", "haircut" not in prompt_block(cfg) and prompt_block(cfg) == block_before)
expect("an expired fact is not counted", memory.size(cfg) == size_before, (memory.size(cfg), size_before))
expect("a fact due today is still in the prompt", "The user's sister visits." in prompt_block(cfg))

# Old stamps still parse, and an edit keeps until unless it changes it.
path.write_text(path.read_text() + "- The user renews the car insurance in October. (noted 2026-09-01)\n")
old = plans()[-1]
expect("an old stamp parses", old["noted"] == "2026-09-01" and old["until"] is None and not old["expired"] and
       old["text"] == "The user renews the car insurance in October.", old)
dentist = next(f for f in plans() if "dentist" in f["text"])
e = memory.replace(cfg, "plans", dentist["id"], "The user has the dentist at eleven instead.")
expect("an edit keeps until", e["until"] == dentist["until"] and e["noted"] == iso(day), e)
e = memory.replace(cfg, "plans", e["id"], e["text"], None)
expect("an edit clears until", e["until"] is None and e["id"].endswith(f"(noted {iso(day)})"), e)
e = memory.replace(cfg, "plans", e["id"], e["text"], day + timedelta(3))
expect("an edit sets until", e["until"] == iso(day + timedelta(3)), e)
e = memory.replace(cfg, "plans", e["id"], "The user has the dentist on Saturday.")
sat = day + timedelta((5 - day.weekday()) % 7 or 7)
expect("an edit with a new date moves until", e["until"] == iso(sat), e)

# Clear expired: one commit, Undo puts them back where they were with their dates.
commits = lambda: len(subprocess.run(["git", "-C", str(scratch / "memory"), "log", "--oneline"],  # noqa: E731
                                     capture_output=True, text=True).stdout.splitlines())
n = commits()
gone = memory.clear_expired(cfg)
expect("clear expired removes only expired facts", [g["text"] for g in gone] == ["The user had a haircut booked."] and
       not any(f["expired"] for f in plans()) and commits() == n + 1, gone)
for g in gone:
    memory.add(cfg, g["topic"], g["text"], g["index"], date.fromisoformat(g["noted"]), date.fromisoformat(g["until"]))
expect("undo brings expired facts back", [f["id"] for f in plans() if f["expired"]] == [g["id"] for g in gone])

# Update by voice: replaces names the stored fact as he sees it; exact, or the topic's candidates.
r = remember(cfg, "people", "Omar is a friend who likes squid.", replaces="omar is a friend who likes octopuses")
expect("replaces changes the fact (case and full stop aside)",
       r.get("replaced") == "Omar is a friend who likes octopuses." and "Omar is a friend who likes squid." in people() and
       "Omar is a friend who likes octopuses." not in people(), r)
r = remember(cfg, "people", "Omar is a friend who likes cats.", replaces="Omar likes octopuses.")
expect("replaces without an exact match lists the facts", "error" in r and "Omar is a friend who likes squid." in
       r.get("facts", []) and "Omar is a friend who likes squid." in people(), r)
r = remember(cfg, "about-you", "Omar likes cats.", replaces="Omar is a friend who likes squid.")
expect("replaces in the wrong topic names the right one", "'people'" in r.get("error", ""), r)
r = remember(cfg, "plans", "The user's sister visits on Saturday.", replaces="The user's sister")
expect("replaces is not a prefix match", "error" in r, r)
r = remember(cfg, "plans", "The user's sister visits on Saturday.",
             replaces=f"The user's sister visits. (noted {iso(day)}, until {iso(sunday)})")
expect("replaces with the stamp quoted still matches, a new date sets until", r.get("until") == iso(sat) and
       r.get("stored") == f"The user's sister visits on {sat.strftime('%A')} {sat.day} {sat.strftime('%B %Y')}.", r)
r = remember(cfg, "plans", "The user's sister visits on Saturday.", until="2099-01-01")
expect("a known fact gets the until it is given", r.get("note") == "already known; until set" and
       [f["until"] for f in plans() if "sister" in f["text"]] == ["2099-01-01"], (r, plans()))
r = remember(cfg, "plans", "Dinner with Omar.")
r = remember(cfg, "plans", "Dinner with Omar and Lina.", replaces='"Dinner with Omar".')
expect("replaces with quotes and a full stop outside them still matches", r.get("replaced") == "Dinner with Omar.", r)
for said in ("The dentist is tomorrow 10:00.", "Meeting on Friday 3pm.", "Call Mum on Friday, after work."):
    expect(f"a weekday or tomorrow before a time or comma still resolves {said!r}",
           absolute_dates(said, wed) != said, absolute_dates(said, wed))

# Forget by voice: exact, "Forgotten: <fact>", one commit.
n = commits()
r = memory.forget_said(cfg, "people", "OMAR is a friend who likes squid!")
expect("forget says what went", r == "Forgotten: Omar is a friend who likes squid." and
       "Omar is a friend who likes squid." not in people() and commits() == n + 1, r)
r = memory.forget_said(cfg, "people", "Omar")
expect("forget without an exact match lists the facts", isinstance(r, dict) and "error" in r and
       r.get("facts") == people(), r)
expect("forget in an unknown topic is refused", "error" in memory.forget_said(cfg, "passwords", "x"))

# Topics: the description line after the title, round trip, into the tool schema.
expect("example descriptions are read", memory.description(cfg, "about-you").startswith("The user himself"))
expect("a new topic gets a description line", memory.description(cfg, "home-life") == "Facts about home life." and
       (scratch / "memory" / "home-life.md").read_text().startswith("# Home life\n\nFacts about home life.\n"))
stem = memory.rename_topic(cfg, "home-life", "Garden", "Plants, the balcony and what grows there.")
expect("rename sets the description", stem == "garden" and memory.description(cfg, "garden") ==
       "Plants, the balcony and what grows there." and "Garden:\n- The user waters the plants" in prompt_block(cfg))
memory.rename_topic(cfg, "garden", "Garden", "")
expect("an empty description removes it", memory.description(cfg, "garden") == "" and
       memory.view(cfg)[[t["name"] for t in memory.view(cfg)].index("garden")]["facts"][0]["text"] ==
       "The user waters the plants on Sundays.")
memory.rename_topic(cfg, "garden", "Garden", "Plants and the balcony.")
(scratch / "memory" / "quoted.md").write_text("# Quoted\n> What a quoted line says.\n- A fact. (noted 2026-09-01)\n")
expect("a '> ' description is read too", memory.description(cfg, "quoted") == "What a quoted line says." and
       memory.facts(scratch / "memory" / "quoted.md") == ["A fact. (noted 2026-09-01)"])
expect("the description is not in the prompt", "Plants and the balcony" not in prompt_block(cfg))
tools = memory.make_memory_tools(cfg)
schemas = {s.name: s for s, _ in tools}
topic = schemas["remember"].properties["topic"]
expect("remember and forget are offered", set(schemas) == {"remember", "forget"})
expect("the topic enum is read now", "garden" in topic["enum"] and "quoted" in topic["enum"], topic["enum"])
expect("the topic descriptions are in the schema", "\n- about-you: The user himself" in topic["description"] and
       "\n- garden: Plants and the balcony.\n" in topic["description"], topic["description"])
expect("remember no longer names topics", "projects" not in schemas["remember"].description and
       "people in their life" not in schemas["remember"].description)
expect("replaces and until are optional", schemas["remember"].required == ["topic", "fact"] and
       {"replaces", "until"} <= set(schemas["remember"].properties))

print(f"\n{'all passed' if not failed else f'{failed} failed'}")
sys.exit(1 if failed else 0)
