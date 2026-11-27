"""Lines said by code, not by him ("One moment.", a state check's correction, a nudge's plain line), in his
reply language (docs/languages.md). In English they are said as written. In another language the brain's
model translates each once, on the background slot and with no tools, and the translation is kept in
state/lines.json, so a line said again costs nothing. When no translation comes in time the line is said
in English, and one that comes later is still kept for next time.

    await lines.say(cfg, "One moment.", wait=0)     # only a kept translation, asked for if missing
    await lines.warm(cfg, HOME_LINES)               # at start, so the lines above are kept"""
import asyncio
from types import SimpleNamespace

from loguru import logger

from winston.config import data_dir
from winston.config.schema import WinstonConfig
from winston.extension_host.sources import read_json, write_json

# The line in tags: given last on its own, a one-word line ("Done") had the 4B translate the instruction instead.
PROMPT = ("Translate the line inside <line></line> into {language}, the way {name}, a voice assistant, would say "
          "it aloud to the person he is talking to: natural and idiomatic, as short and plain as the original, "
          "with names kept as they are. Answer with the translation only, without the tags.\n\n<line>{line}</line>")

_kept: dict[str, dict[str, str]] = {}               # language: {line: translation}
_asked: dict[tuple[str, str], asyncio.Task] = {}    # translations under way, shared by everyone waiting


def english(cfg: WinstonConfig) -> bool:
    """Whether he answers in English: "English", "en-GB", "Engelska"."""
    return cfg.assistant.language.strip().lower().startswith("en")


def _file():
    return data_dir() / "state" / "lines.json"


def _known(language: str) -> dict[str, str]:
    if language not in _kept:
        _kept[language] = read_json(_file(), {}).get(language, {})
    return _kept[language]


def _keep(language: str, line: str, said: str) -> None:
    _known(language)[line] = said
    # ponytail: read, add, write; two processes adding at once can lose one, which is then asked for again
    everything = read_json(_file(), {})
    everything.setdefault(language, {})[line] = said
    try:
        write_json(_file(), everything)
    except OSError as e:
        logger.warning(f"lines: translation not kept: {e}")


async def _translate(cfg: WinstonConfig, language: str, line: str) -> str:
    from winston.extension_host.services import Services
    prompt = PROMPT.format(language=language, name=cfg.assistant.name, line=line)
    try:
        text = await Services(SimpleNamespace(cfg=cfg))._ask_once(prompt, None, 200)
    except Exception as e:                                                  # noqa: BLE001
        logger.warning(f"lines: {line!r} not translated into {language}: {e}")
        return ""
    text = str(text).split("</think>")[-1].replace("<line>", "").replace("</line>", "").strip().strip("\"“”")
    if not text or len(text) > 3 * len(line) + 40:          # a refusal or a ramble is no translation
        logger.warning(f"lines: {line!r} into {language} gave {text[:80]!r}; said in English")
        return ""
    _keep(language, line, text)
    return text


async def say(cfg: WinstonConfig, line: str, wait: float = 3.0) -> str:
    """The line in his reply language. A translation not kept yet is asked for and waited on up to `wait`
    seconds (0: not at all), the line as written meanwhile."""
    language = cfg.assistant.language.strip()
    if english(cfg) or not line.strip():
        return line
    if kept := _known(language).get(line):
        return kept
    key = (language, line)
    if key not in _asked:
        _asked[key] = task = asyncio.create_task(_translate(cfg, language, line))
        task.add_done_callback(lambda _: _asked.pop(key, None))
    try:
        return await asyncio.wait_for(asyncio.shield(_asked[key]), wait) or line
    except TimeoutError:
        return line


async def warm(cfg: WinstonConfig, lines: tuple[str, ...] | list[str]) -> None:
    """Every line kept in the reply language, one after the other, so the brain's server is asked once at a time."""
    for line in lines:
        await say(cfg, line, wait=120)
