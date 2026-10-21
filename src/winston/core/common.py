"""Small helpers more than one part of the core shares: how names are folded and matched, the tool-call
log, observers that act on a frame once, re-wrapping a tool's handler, reading winstond's server-sent
events and saying in words why a server could not be reached."""
import asyncio
import json
import re
import time
from collections import OrderedDict
from collections.abc import AsyncIterator, Callable, Iterable
from pathlib import Path

import httpx
from loguru import logger


def key(name: str) -> str:
    """A name folded for matching: rooms, entity names and aliases, what was said. Case and spacing do not count."""
    return " ".join(name.split()).casefold()


def entity_names(eid: str, ent) -> list[str]:
    """The names an exposed entity answers to, folded: its object id as words ("light.shelf_lamp" is "shelf
    lamp"), its name and its aliases (an ExposedEntity's)."""
    names = (eid.split(".", 1)[-1].replace("_", " "), ent.name, *ent.aliases)
    return list(dict.fromkeys(k for n in names if n and (k := key(n))))


def words_pattern(names: Iterable[str]) -> re.Pattern:
    """Any of names as whole words in folded text, the longest first, so "shelf lamp" wins over "lamp"."""
    folded = sorted({key(n) for n in names} - {""}, key=len, reverse=True)
    return re.compile(r"\b(?:" + "|".join(map(re.escape, folded)) + r")\b")


def _leaf(e: BaseException) -> BaseException:
    """MCP clients wrap their errors in exception groups; the first leaf is the one that says what happened."""
    while isinstance(e, BaseExceptionGroup) and e.exceptions:
        e = e.exceptions[0]
    return e


def failure_text(e: BaseException) -> str:
    """What failed as it was raised, for a log: what an error in words was raised from, when it was."""
    e = _leaf(e)
    e = e.__cause__ or e
    return f"{type(e).__name__}: {e}"


def failure_words(e: BaseException) -> str:
    """Why a server could not be reached, in words."""
    e = _leaf(e)
    if isinstance(e, (TimeoutError, httpx.TimeoutException)):
        return "it did not answer in time"
    if status := getattr(getattr(e, "response", None), "status_code", None):
        return f"it answered {status}"
    if "Connect" in type(e).__name__:
        return "nothing answered at that address"
    return str(e) or type(e).__name__


class Once:
    """True the first time a key is seen. Observers see a frame once per pipeline hop; a frame's hops come
    close together, so the last `size` keys are enough to act on each frame once."""

    def __init__(self, size: int = 512):
        self._seen: OrderedDict = OrderedDict()
        self._size = size

    def __call__(self, item) -> bool:
        if item in self._seen:
            return False
        self._seen[item] = None
        if len(self._seen) > self._size:
            self._seen.popitem(last=False)
        return True


def log_event(path: Path, channel: str, **rec) -> None:
    """One line in the tool-call log (logging.tool_calls): {"ts", "channel", **rec}. A log that cannot be
    written is a warning, never a failed turn."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            f.write(json.dumps({"ts": time.time(), "channel": channel, **rec}, default=str, ensure_ascii=False) + "\n")
    except OSError as e:
        logger.warning(f"tool-call log: {e}")


def rewrap(llm, name: str, wrap: Callable) -> bool:
    """Register tool `name` again with wrap(its handler), keeping Pipecat's cleanup, interruption and timeout
    settings. False when llm has no such tool."""
    item = llm._functions.get(name)
    if item is None:
        return False
    handler = wrap(item.handler)
    if cleanup := getattr(item.handler, "_pipecat_cleanup", None):
        handler._pipecat_cleanup = cleanup
    llm.register_function(name, handler, cancel_on_interruption=item.cancel_on_interruption,
                          timeout_secs=item.timeout_secs)
    return True


async def sse(url: str, params: dict | None = None) -> AsyncIterator[dict | None]:
    """Each event's data from one of winstond's server-sent event streams, reconnecting for as long as the
    caller iterates; None each time the stream is lost."""
    delay = 1.0
    while True:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(5, read=None)) as c, \
                    c.stream("GET", url, params=params) as r:
                r.raise_for_status()
                delay = 1.0
                async for line in r.aiter_lines():
                    if line.startswith("data: "):
                        yield json.loads(line[6:])
        except (httpx.HTTPError, ValueError) as e:
            logger.debug(f"{url}: {e}; retrying in {delay:.0f} s")
            yield None
        await asyncio.sleep(delay)
        delay = min(delay * 2, 30)
