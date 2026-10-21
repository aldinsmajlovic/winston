"""Schema copy cases: every title and help line in the config schema follows the copy rules (docs/ui-guide.md,
Copy): a title names the effect ("Enabled" does not) and carries no unit, help is one short line without
internal words, and every switch and key has help (but a switch whose title says it all). A hidden field is never
drawn, so only its key's help is held to them; no field carries a level, and no description sends you to an
Advanced that is gone. The built-in extensions' settings follow the same rules. Then an older file's thinking
switches load as today's two choices, and a typed wake word becomes the box's id. No hardware needed.

    uv run python tests/schema_copy_cases.py
"""
import re
from pathlib import Path

from harness import check, finish_checks
from winston_sdk.loader import load

from winston.config.restarts import _deref
from winston.config.schema import SatelliteDevice, WinstonConfig

BARE = {"Enabled", "Url", "Env"}                    # pydantic's titles for a field nobody named
UNITS = ("(s)", "(min)", "(days)")                  # units are `unit`, never part of a title
PLAIN = {"Use the graphics card"}                   # switches whose title needs no help line
INTERNAL = re.compile(r"data/\.env|winstond|winston up|`|\d{4}-\d{2}-\d{2}|\u2014")
EXTENSIONS = Path(__file__).resolve().parent.parent / "src" / "winston" / "extensions"


def walk(node: dict, where: str, root: dict, hidden: bool = False):
    """Hidden (on the field or a section above it) is never drawn, so only its key's help is checked."""
    node = _deref(node, root)
    hidden = hidden or node.get("hidden") is True
    title, help_ = node.get("title", ""), node.get("help", "")
    secret, account = node.get("secret") or {}, node.get("account") or {}
    texts = [("key help", secret.get("help", "")), ("account help", account.get("help", ""))]
    texts += [] if hidden else [("title", title), ("help", help_)]
    for what, text in texts:
        check(f"{where} {what}: internal words", INTERNAL.findall(text), [])
        check(f"{where} {what}: over 140 characters", len(text) > 140, False)
    if not hidden:
        check(f"{where}: title {title!r}", title in BARE or any(u in title for u in UNITS), False)
        if node.get("type") == "boolean" and title not in PLAIN:       # an account's help is under Connect
            check(f"{where}: a switch without help", bool(help_ or account.get("help")), True)
    if "secret" in node:
        check(f"{where}: a key without help on where to get it", bool(secret.get("help")), True)
    check(f"{where}: carries a level", "level" in node, False)
    check(f"{where}: description sends you under Advanced", "under Advanced" in node.get("description", ""), False)
    for key, child in (node.get("properties") or {}).items():
        walk(child, f"{where}.{key}", root, hidden)
    for child in (node.get("additionalProperties"), node.get("items"), *(node.get("anyOf") or [])):
        if isinstance(child, dict):
            walk(child, f"{where}.*", root, hidden)


root = WinstonConfig.model_json_schema()
walk(root, "config", root)
for path in sorted(p for p in EXTENSIONS.glob("*.py") if not p.name.endswith("_test.py")):
    schema = load(str(path)).settings_model.model_json_schema()
    walk(schema, f"{path.stem}.settings", schema)


def thinking(old: dict) -> tuple[str, str]:
    t = WinstonConfig.model_validate({"llm": {"model": "x", "thinking": old}}).llm.thinking
    return t.spoken, t.typed


check("voice on: always thinks when spoken to", thinking({"voice": True, "home": False, "text": False}),
      ("always", "never"))
check("voice off, home on: thinks on home requests", thinking({"voice": False, "home": True, "text": True}),
      ("home", "always"))
check("home missing counts as on", thinking({"voice": False}), ("home", "always"))
check("both off: never", thinking({"voice": False, "home": False}), ("never", "always"))
check("today's keys stay as they are", thinking({"spoken": "never", "typed": "never"}), ("never", "never"))
check("a typed wake word is the box's id", SatelliteDevice(host="x", wake_word="Okay Nabu, hey jarvis,").wake_word,
      "okay_nabu,hey_jarvis")
check("no wake word keeps the box's choice", SatelliteDevice(host="x", wake_word=" , ").wake_word, None)
finish_checks("schema copy: titles, help lines and keys follow the copy rules, in the config and the built-in "
              "extensions' settings; old thinking switches and typed wake words load")
