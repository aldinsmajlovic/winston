"""Hint words: the names speech-to-text should hear right (his name, cloned voices, contacts, the
proper nouns among Home Assistant's names, and words the user adds under stt.hint_words).

An engine that can be told words in advance gets them through the adapter contract (`supports_hints`,
`hints=`): Whisper's initial prompt or hotwords, a cloud engine's keyterms. Parakeet cannot, so with
`stt.fix_misspellings` Winston corrects close misspellings afterwards: a word, or two words run
together ("Vardag's Rummet"), that sounds like a hint word and is spelled close to it becomes that
word. A common English word is never touched, so "wind" and "win some" stay as they are. Each change
is logged in the STT line as before -> after.

`Hearing` wraps the one STT model a process loads, so the voice pipeline, the satellites and the
clone consent check all transcribe and correct the same way."""
import asyncio
import re
import time
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from functools import cache
from pathlib import Path

import numpy as np
from loguru import logger

from winston.config.schema import WinstonConfig

MIN_LETTERS = 3             # shorter words are never hint words
SIMILAR = 0.7               # spelling similarity (difflib ratio) a correction needs at least,
SIMILAR_SHORT = 0.85        # and for a word of five letters or fewer, so Marie, Eric and Alden stay themselves
SOURCES = {"assistant": "His name", "voice": "A cloned voice", "contact": "A Discord contact",
           "home_assistant": "A name in the Home Assistant list", "extension": "Asked for by an extension",
           "yours": "Added by you"}


@dataclass(frozen=True)
class Hint:
    word: str
    source: str             # a key of SOURCES


@cache
def common_words() -> frozenset[str]:
    text = (Path(__file__).parent / "common_words.txt").read_text()
    return frozenset(w for line in text.splitlines() if not line.startswith("#") for w in line.split())


def _fold(s: str) -> str:
    """Lower case letters only, accents folded: "Vardag's" -> "vardags", "Köket" -> "koket"."""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z]", "", s)


def is_common(word: str) -> bool:
    w = word.lower().replace("’", "'")
    return w in common_words() or w.removesuffix("'s") in common_words() or _fold(w) in common_words()


def _proper(word: str) -> bool:
    """A word worth hinting: letters only (an inner apostrophe or hyphen allowed), long enough, not common."""
    return len(_fold(word)) >= MIN_LETTERS and bool(_WORD.fullmatch(word)) and not is_common(word)


def _display(word: str) -> str:
    return word[:1].upper() + word[1:] if word.islower() else word


def automatic(cfg: WinstonConfig, clone_names: list[str] | None = None) -> list[Hint]:
    """The words Winston adds by himself, from config and the stored clones. Never whole phrases: a name
    or alias gives its proper nouns one by one ("hektar lamp" gives Hektar, "the lights" nothing)."""
    from winston.core import voices
    out: list[Hint] = []

    def add(text: str, source: str, only_proper: bool = True):
        for w in str(text).split():
            w = w.strip(".,!?;:\"()")
            if w and (_proper(w) if only_proper else len(_fold(w)) >= MIN_LETTERS):
                out.append(Hint(_display(w), source))

    add(cfg.assistant.name, "assistant", only_proper=False)
    for n in clone_names if clone_names is not None else [c.name for c in voices.clones()]:
        add(n, "voice")
    if cfg.channels.discord.enabled:
        for n in cfg.channels.discord.contacts:
            add(n, "contact")
    if ha := cfg.providers.ha:
        for e in ha.expose.values():
            for n in [e.name or "", *e.aliases]:
                add(n, "home_assistant")
    from winston.extension_host import texts
    for w in texts.hint_words(cfg):                       # words running extensions asked for, whole
        add(w, "extension", only_proper=False)
    return out


def hints(cfg: WinstonConfig, extra: list[str] | tuple[str, ...] = (), clone_names: list[str] | None = None) -> list[Hint]:
    """Every hint word once (the first source wins), the user's own last."""
    seen: set[str] = set()
    out: list[Hint] = []
    mine = [Hint(w.strip(), "yours") for w in cfg.stt.hint_words if w.strip()]
    extras = [Hint(_display(w), "voice") for n in extra for w in n.split() if len(_fold(w)) >= MIN_LETTERS]
    for h in [*automatic(cfg, clone_names), *extras, *mine]:
        if (k := _fold(h.word)) and k not in seen:
            seen.add(k)
            out.append(h)
    return out


# A phonetic key in the spirit of Metaphone, tuned to what speech-to-text confuses: voiced and voiceless
# pairs are one sound (d/t, b/p, g/k, v/f, z/s), vowels after the first letter drop out, doubled
# sounds collapse. "Winston" -> WNSTN, "Winson" -> WNSN, "Windsun" -> WNTSN.
_DIGRAPHS = [("tch", "x"), ("sch", "sk"), ("ph", "f"), ("sh", "x"), ("ch", "x"), ("th", "0"), ("ck", "k"),
             ("gh", ""), ("wh", "w"), ("wr", "r"), ("kn", "n"), ("dg", "j"), ("qu", "kw"), ("tion", "xn")]
_SOUNDS = str.maketrans({"d": "t", "b": "p", "g": "k", "v": "f", "z": "s", "q": "k", "c": "k"})


def phonetic(word: str) -> str:
    w = _fold(word)
    if not w:
        return ""
    for a, b in _DIGRAPHS:
        w = w.replace(a, b)
    w = re.sub(r"c(?=[eiy])", "s", w)
    first, rest = w[0], w[1:]
    lead = "A" if first in "aeiouy" else first
    rest = re.sub(r"h(?![aeiou])", "", rest)                # a silent h
    rest = re.sub(r"[aeiouy]", "", rest)
    key = (lead + rest).translate(_SOUNDS).upper()
    return re.sub(r"(.)\1+", r"\1", key)


def _distance(a: str, b: str) -> int:
    """Edit distance counting a swap of two neighbours as one edit (optimal string alignment)."""
    d = [[max(i, j) if not i * j else 0 for j in range(len(b) + 1)] for i in range(len(a) + 1)]
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + (a[i - 1] != b[j - 1]))
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + 1)
    return d[-1][-1]


def close(heard: str, word: str) -> bool:
    """Does `heard` (letters only, possibly two words run together) sound like and look like `word`?"""
    h, w = _fold(heard), _fold(word)
    if not h or not w or h == w:
        return False
    if abs(len(h) - len(w)) > max(2, len(w) // 3):
        return False
    kh, kw = phonetic(h), phonetic(w)
    allowed = 1 if len(kw) >= 4 else 0                  # a short key must match exactly
    if _distance(kh, kw) > allowed or kh[:1] != kw[:1]:
        return False
    return SequenceMatcher(None, h, w).ratio() >= (SIMILAR if len(w) > 5 else SIMILAR_SHORT)


_TOKEN = re.compile(r"^(\W*)(.*?)(\W*)$", re.DOTALL)
_WORD = re.compile(r"[^\W\d_]+(?:['\u2019-][^\W\d_]+)*")          # letters, an inner apostrophe or hyphen
_POSSESSIVE = re.compile(r"['\u2019]s$", re.IGNORECASE)


@dataclass
class _Tok:
    lead: str
    core: str
    tail: str


def _split(text: str) -> list[_Tok]:
    out = []
    for raw in text.split():
        m = _TOKEN.match(raw)
        out.append(_Tok(*m.groups()) if m else _Tok("", raw, ""))
    return out


def _shape(word: str, like: str) -> str:
    return word.upper() if like.isupper() and len(like) > 1 else word


def correct(text: str, words: list[str]) -> tuple[str, list[tuple[str, str]]]:
    """Replace close misspellings of the hint words in text. Returns the new text and each change as
    (heard, hint word). A token is compared alone and run together with its neighbour. A common word is
    never changed; two words run together are changed only when neither is common, or when together they
    spell the hint word exactly ("Vardag's Rummet"). A word that is a hint word with more after it
    (Erika for Erik, Winstons) stays, as does anything with digits or symbols inside."""
    targets = [w for w in words if len(_fold(w)) >= MIN_LETTERS and not is_common(w) and " " not in w.strip()]
    if not text or not targets:
        return text, []
    toks = _split(text)
    folded = {_fold(w) for w in targets}
    out: list[str] = []
    changes: list[tuple[str, str]] = []
    i = 0
    while i < len(toks):
        hit = None
        for n in (2, 1):                                    # the longer span first: "Vardag's Rummet"
            span = toks[i:i + n]
            if len(span) < n or not all(_WORD.fullmatch(t.core) for t in span):
                continue
            if n == 2 and (span[0].tail or span[1].lead):   # never across punctuation
                continue
            cores = [t.core for t in span]
            common = [is_common(c) for c in cores]
            if all(common) or any(_fold(c) in folded for c in cores):
                continue
            possessive = n == 1 and bool(_POSSESSIVE.search(cores[0]))
            heard = _fold(cores[0][:-2] if possessive else "".join(cores))
            for w in targets:
                fw = _fold(w)
                if heard == fw and n == 1:                  # "Winston's"
                    break
                if n == 2 and heard == fw:
                    hit = (n, w, possessive)
                elif heard.startswith(fw) or (n == 2 and any(common)):
                    continue
                elif close(heard, w):
                    hit = (n, w, possessive)
                if hit:
                    break
            if hit:
                break
        if hit:
            n, w, possessive = hit
            span = toks[i:i + n]
            before = " ".join(t.core for t in span)
            new = _shape(w, before) + (span[0].core[-2:] if possessive else "")
            out.append(span[0].lead + new + span[-1].tail)
            changes.append((before, w))
            i += n
        else:
            t = toks[i]
            out.append(t.lead + t.core + t.tail)
            i += 1
    return (" ".join(out), changes) if changes else (text, [])


class Hearing:
    """The shared STT: one model, the config's hint words, the correction and the log line. Used by
    every channel that transcribes (the voice pipeline's service, satellites, clone consent)."""

    CLONE_TTL = 10.0                                        # seconds the clone names are reused

    def __init__(self, cfg: WinstonConfig, adapter=None, model=None):
        self.cfg, self.adapter, self.model = cfg, adapter, model
        self._clones: tuple[float, list[str]] = (0.0, [])

    def _clone_names(self) -> list[str]:
        from winston.core import voices
        t, names = self._clones
        if time.monotonic() - t > self.CLONE_TTL:
            try:
                names = [c.name for c in voices.clones()]
            except OSError:
                names = []
            self._clones = (time.monotonic(), names)
        return names

    def words(self, extra: list[str] | tuple[str, ...] = ()) -> list[str]:
        return [h.word for h in hints(self.cfg, extra, self._clone_names())]

    def engine_hints(self, extra: list[str] | tuple[str, ...] = ()) -> list[str] | None:
        """The words for an engine that takes them in advance; None for one that cannot (Parakeet)."""
        return self.words(extra) if getattr(self.adapter, "supports_hints", False) else None

    @property
    def engine(self) -> str:
        """Which engine heard: "parakeet/nemo-parakeet-tdt-0.6b-v3", kept with each turn."""
        return f"{self.cfg.stt.provider}/{self.cfg.stt.model}"

    def heard(self, text: str, secs: float, ms: float, label: str = "STT", extra: list[str] | tuple[str, ...] = (),
              audio: np.ndarray | None = None, rate: int = 0) -> "Heard":
        """Correct what the engine heard and log it: "STT 2.1s audio -> 'Hey Winson.' -> 'Hey Winston.' in 190 ms".
        The audio, when given, travels with the result so the turn record can keep it (core/turns.py)."""
        text = (text or "").strip()
        fixed, changes = correct(text, self.words(extra)) if self.cfg.stt.fix_misspellings else (text, [])
        shown = f"{text!r} -> {fixed!r} (" + ", ".join(f"{a} -> {b}" for a, b in changes) + ")" if changes else repr(text)
        logger.info(f"{label} {secs:.2f}s audio -> {shown} in {ms:.0f} ms")
        return Heard(fixed, text, changes, secs, round(ms), self.engine, audio, rate)

    def finish(self, text: str, secs: float, ms: float, label: str = "STT",
               extra: list[str] | tuple[str, ...] = ()) -> str:
        return self.heard(text, secs, ms, label, extra).text

    async def hear(self, pcm: np.ndarray, rate: int, label: str = "STT",
                   extra: list[str] | tuple[str, ...] = ()) -> "Heard":
        """int16 audio to corrected text, off the event loop, with the adapter's static transcribe."""
        t0 = time.perf_counter()
        text = await asyncio.to_thread(self.adapter.transcribe, self.model, pcm, rate, self.engine_hints(extra))
        return self.heard(text, len(pcm) / rate, (time.perf_counter() - t0) * 1000, label, extra, pcm, rate)

    async def transcribe(self, pcm: np.ndarray, rate: int, label: str = "STT",
                         extra: list[str] | tuple[str, ...] = ()) -> str:
        return (await self.hear(pcm, rate, label, extra)).text


@dataclass
class Heard:
    """What the engine made of one utterance: the corrected text, what the engine said before the
    correction, each change (heard, hint word), seconds of audio, milliseconds it took, which engine, and
    the int16 audio itself when the caller passed it."""
    text: str
    raw: str
    changes: list[tuple[str, str]]
    secs: float
    ms: int
    engine: str
    audio: np.ndarray | None = None
    rate: int = 0
