"""The diary: every night Winston writes a few lines about the day, from the turn record, in his
own voice. Stored in data/memory/diary/<date>.md; recent entries go into the prompt so he can
reference yesterday naturally, and a tool lets him look up older ones when asked.

Written by the local LLM from the day's turns, yielding to conversations. Triggered after
midnight by winstond (write_pending), or by hand with `winston diary write`.

    diary = Diary(cfg)
    await diary.write("2026-10-11")           # write one day's entry
    await diary.write_pending()               # write any missing recent entries
    diary.recent()                            # the last few entries, for the prompt
    diary.entry("2026-10-11")                 # one entry by date
"""
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from loguru import logger
from openai import AsyncOpenAI

from winston.config import resolve
from winston.config.schema import WinstonConfig
from winston.core.turns import TurnStore, _read_lines


def diary_dir(cfg: WinstonConfig) -> Path:
    return resolve("memory/diary")


def _turns_for(cfg: WinstonConfig, day: str) -> list[dict]:
    """A day's turns, oldest first, from every channel."""
    turns_root = resolve(cfg.logging.turns.dir)
    folder = turns_root / day
    if not folder.is_dir():
        return []
    return sorted(_read_lines(folder), key=lambda t: t.get("ts", 0))


def _format_turns(turns: list[dict]) -> str:
    """The day's turns as a readable log for the LLM to summarize."""
    lines = []
    for t in turns:
        if t.get("outcome") == "nothing_heard":
            continue
        ts = datetime.fromtimestamp(t.get("ts", 0)).strftime("%H:%M")
        heard = t.get("heard", "").strip()
        said = t.get("said", "").strip()
        tools = [tc.get("name", "") for tc in t.get("tools", [])]
        room = t.get("room", "")
        outcome = t.get("outcome", "")
        parts = [f"[{ts}]"]
        if room:
            parts.append(f"({room})")
        if heard:
            parts.append(f"User: {heard}")
        if tools:
            parts.append(f"Tools: {', '.join(tools)}")
        if said:
            parts.append(f"Winston: {said}")
        if outcome == "silent":
            parts.append("(stayed silent)")
        if outcome == "dismissed":
            parts.append("(dismissed)")
        lines.append(" | ".join(parts))
    return "\n".join(lines)


DIARY_PROMPT = """\
You are Winston, a butler and voice assistant. Below is the log of every spoken interaction \
today ({date}, {weekday}). Write a diary entry: a few lines about the day in your own voice, \
as if reflecting at the end of the evening. What was asked, what you did in the house, \
anything you were asked to remember, and any moments that stood out.

Rules:
- Write in first person as Winston. Keep the butler character.
- Three to six sentences. No headers, no lists, no timestamps.
- Mention specific things (songs played, lights set, people mentioned) but naturally, not as a log.
- If very little happened, say so briefly. An empty day is one line.
- Do not invent anything not in the log.

The log:

{log}"""

RECENT_DAYS = 2


class Diary:
    def __init__(self, cfg: WinstonConfig):
        self.cfg = cfg
        self._dir = diary_dir(cfg)

    def entry(self, day: str) -> str | None:
        f = self._dir / f"{day}.md"
        return f.read_text().strip() if f.is_file() else None

    def entries(self, limit: int = 14) -> list[tuple[str, str]]:
        """Recent entries, newest first: (date, text)."""
        if not self._dir.is_dir():
            return []
        files = sorted(self._dir.glob("*.md"), reverse=True)[:limit]
        return [(f.stem, f.read_text().strip()) for f in files if f.stat().st_size > 0]

    def days_without(self, since: int = 7) -> list[str]:
        """Recent days that have turns but no diary entry yet, oldest first."""
        store = TurnStore(self.cfg)
        today = date.today().isoformat()
        missing = []
        for day in store.days():
            if day >= today:
                continue
            if day < (date.today() - timedelta(days=since)).isoformat():
                break
            if not (self._dir / f"{day}.md").is_file():
                missing.append(day)
        return list(reversed(missing))

    def recent(self) -> str:
        """The last few diary entries, for the prompt. Short enough to fit without bloating it."""
        pairs = self.entries(RECENT_DAYS)
        if not pairs:
            return ""
        lines = []
        for day, text in reversed(pairs):
            try:
                d = date.fromisoformat(day)
                label = f"{d.strftime('%A')} {d.day} {d.strftime('%B')}"
            except ValueError:
                label = day
            lines.append(f"**{label}:** {text}")
        return "# Recent days\n\nYour diary from the last few days. Reference it naturally when " \
               "it fits the conversation, never recite it.\n\n" + "\n\n".join(lines)

    async def write(self, day: str, *, talking: Any = None) -> str | None:
        """Write the diary for one day. Returns the entry, or None if there were no turns."""
        turns = _turns_for(self.cfg, day)
        answered = [t for t in turns if t.get("outcome") in ("answered", "silent", "dismissed")]
        if not answered:
            logger.info(f"diary: no turns on {day}, skipping")
            return None

        log_text = _format_turns(answered)
        try:
            d = date.fromisoformat(day)
            weekday = d.strftime("%A")
        except ValueError:
            weekday = ""

        prompt = DIARY_PROMPT.format(date=day, weekday=weekday, log=log_text)
        text = await self._ask(prompt, talking=talking)
        if not text:
            return None

        self._dir.mkdir(parents=True, exist_ok=True)
        (self._dir / f"{day}.md").write_text(text + "\n")
        logger.info(f"diary: wrote {day} ({len(text)} chars from {len(answered)} turns)")
        self._commit(day)
        return text

    def _commit(self, day: str):
        from winston.core.memory import _commit, vault
        if self.cfg.memory.commit_on_write:
            try:
                _commit(vault(self.cfg), day, "diary")
            except Exception as e:  # noqa: BLE001
                logger.warning(f"diary: commit failed: {e}")

    async def write_pending(self, *, talking: Any = None):
        """Write diary entries for any recent days that have turns but no entry yet."""
        for day in self.days_without():
            try:
                await self.write(day, talking=talking)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"diary: failed to write {day}: {e}")

    async def _ask(self, prompt: str, *, talking: Any = None) -> str | None:
        """Call the background model, yielding to conversations."""
        from winston.core.brain import api_key, thinking_body
        c = self.cfg.llm
        client = AsyncOpenAI(base_url=c.base_url, api_key=api_key(c.api_key_env), timeout=120)
        extra: dict[str, Any] = dict(thinking_body(self.cfg, "voice"))

        deadline = time.monotonic() + 120
        while True:
            if talking and talking():
                if time.monotonic() > deadline:
                    logger.warning("diary: timed out waiting for conversation to end")
                    return None
                import asyncio
                await asyncio.sleep(0.5)
                continue

            try:
                r = await client.chat.completions.create(
                    model=c.model, messages=[{"role": "user", "content": prompt}],
                    max_tokens=512, temperature=0.4, extra_body=extra or None)
                text = (r.choices[0].message.content or "").strip()
                if text.startswith("<think>"):
                    text = text.split("</think>", 1)[-1].strip()
                return text if text else None
            except Exception as e:  # noqa: BLE001
                logger.warning(f"diary: model call failed: {e}")
                return None
