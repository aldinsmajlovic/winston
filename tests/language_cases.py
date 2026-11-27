"""Other languages in the code around the models: what holds when the household does not speak English.
No hardware needed.

    uv run python tests/language_cases.py
"""
import asyncio

from harness import expect, finish

from winston.config.schema import WinstonConfig
from winston.core.memory import check
from winston.core.spoken import SpokenText

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

finish()
