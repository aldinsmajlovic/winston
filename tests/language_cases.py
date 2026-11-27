"""Other languages in the code around the models: what holds when the household does not speak English.
No hardware needed.

    uv run python tests/language_cases.py
"""
import asyncio
import json
import os
import tempfile
from pathlib import Path

from harness import expect, finish

data = Path(tempfile.mkdtemp(prefix="winston-language-"))
os.environ["WINSTON_DATA"] = str(data)      # before winston reads it: the kept lines go to a scratch folder

from winston.config.schema import WinstonConfig  # noqa: E402
from winston.core import lines  # noqa: E402
from winston.core.memory import check  # noqa: E402
from winston.core.spoken import SpokenText  # noqa: E402
from winston.extension_host.services import Services  # noqa: E402

# A secret said in another language is refused as in English, compounds and inflections included.
for fact in ("Wifi-lösenordet är hunter2.", "Das Passwort ist hunter2.", "Portkoden är 1234.", "Der Türcode ist 4711.",
             "Het wachtwoord is hunter2.", "Le mot de passe est hunter2.", "The wifi password is hunter2."):
    expect(f"refused: {fact}", check(fact) is not None)
for fact in ("Hon tränar på tisdagar.", "Er mag warmes Licht am Abend.", "The user prefers warm light."):
    expect(f"kept: {fact}", check(fact) is None, check(fact))


def spoken(language: str, text: str) -> str:
    cfg = WinstonConfig.model_validate({"llm": {"model": "x"}, "assistant": {"language": language},
                                        "region": {"time_format": "24h"}})
    return asyncio.run(SpokenText(cfg).filter(text))


expect("English: a time in words", spoken("English", "It's 17:30 now.") == "It's seventeen thirty now.")
expect("another language: the time as written", spoken("Swedish", "Klockan är 17:30 nu.") == "Klockan är 17:30 nu.")
expect("a lone full stop is still dropped", spoken("Swedish", ".") == "")

# Lines said by code (core/lines.py), with a made-up model: translated once, kept on disk, English meanwhile.
asked: list[str] = []
SWEDISH = {"One moment.": "Ett ögonblick.", "Done": "Klart"}


async def fake_ask(self, prompt, schema, max_tokens):
    line = prompt.rsplit("<line>", 1)[-1].removesuffix("</line>")
    asked.append(line)
    await asyncio.sleep(0.05)
    if line == "Fails.":
        raise RuntimeError("the model did not answer")
    return "Sure! " * 50 if line == "Rambles." else f'"{SWEDISH[line]}"'


Services._ask_once = fake_ask
swedish = WinstonConfig.model_validate({"llm": {"model": "x"}, "assistant": {"language": "Swedish"}})
english = WinstonConfig.model_validate({"llm": {"model": "x"}, "assistant": {"language": "en-GB"}})


async def said_lines():
    expect("in English a line is said as written, nothing asked",
           await lines.say(english, "One moment.") == "One moment." and asked == [])
    expect("wait=0: English while the translation is asked for", await lines.say(swedish, "One moment.", wait=0) == "One moment.")
    await asyncio.sleep(0.1)
    expect("then the kept translation, its quotes taken off", await lines.say(swedish, "One moment.", wait=0) == "Ett ögonblick.")
    expect("asked once", asked == ["One moment."], asked)
    kept = json.loads((data / "state" / "lines.json").read_text())
    expect("kept on disk by language", kept == {"Swedish": {"One moment.": "Ett ögonblick."}}, kept)
    lines._kept.clear()
    expect("read back after a restart, nothing asked", await lines.say(swedish, "One moment.") == "Ett ögonblick."
           and asked == ["One moment."], asked)
    expect("a model that fails: English", await lines.say(swedish, "Fails.") == "Fails.")
    expect("a ramble is no translation", await lines.say(swedish, "Rambles.") == "Rambles.")
    expect("neither kept", "Fails." not in lines._kept["Swedish"] and "Rambles." not in lines._kept["Swedish"])
    await lines.warm(swedish, ["Done"])
    expect("warm keeps a line", await lines.say(swedish, "Done", wait=0) == "Klart")


asyncio.run(said_lines())

finish()
