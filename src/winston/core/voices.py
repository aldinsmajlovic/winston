"""Cloned voices: clips recorded from a friend with their consent, under the data directory's
voices/friends/. Each clone is three files named by a slug of the friend's name:

    friends/<slug>.wav           the reference clip the speech server clones from
    friends/<slug>.consent.wav   the friend saying they agree, kept as the record of it
    friends/<slug>.json          name, when, what the consent recording was heard to say, and how
                                 close its voice came to the clip's (core/speaker)

The speech server resolves a voice name relative to voices/, so a clone's voice name is
"friends/<slug>". Nothing here touches the network; the voice channel records, this stores."""
import json
import re
import time
import unicodedata
import wave
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from winston.config import resolve

DIR = "voices/friends"
TARGET_RMS = 0.1        # about -20 dBFS of speech, what the reference clips sit at
PEAK = 0.89             # -1 dBFS
MAX_CLIP_SECS = 20.0    # Chatterbox conditions on the first 10 to 15 s; more only costs preparation time


@dataclass
class Clone:
    slug: str
    name: str
    created: float
    consent_text: str
    seconds: float
    # the same-voice check (core/speaker): cosine similarity of the consent recording and the clip, when it
    # ran and with which model; None on clones made before the check or while the model was not available
    consent_score: float | None = None
    consent_checked_at: float | None = None
    consent_model: str | None = None

    @property
    def voice(self) -> str:
        return f"friends/{self.slug}"


def friends_dir() -> Path:
    return resolve(DIR)


def slug(name: str) -> str:
    """"Erik Åberg" -> "erik-aberg"; empty when nothing usable is left."""
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:40]


def clones() -> list[Clone]:
    out = []
    for meta in sorted(friends_dir().glob("*.json")):
        try:
            c = Clone(**json.loads(meta.read_text()))
        except (OSError, ValueError, TypeError):
            continue
        if (friends_dir() / f"{c.slug}.wav").is_file():
            out.append(c)
    return out


def find(name: str) -> Clone | None:
    """A clone by the friend's name or slug, forgiving about case and accents."""
    s = slug(name)
    return next((c for c in clones() if c.slug == s or slug(c.name) == s), None)


def delete(s: str) -> bool:
    found = False
    for suffix in (".wav", ".consent.wav", ".json"):
        p = friends_dir() / f"{s}{suffix}"
        if p.is_file():
            p.unlink(); found = True
    return found


def write_wav(path: Path, pcm: np.ndarray, rate: int):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(pcm.astype("<i2").tobytes())


def speech_only(pcm: np.ndarray, voiced: np.ndarray, rate: int, frame: int, gap_secs: float = 0.3) -> np.ndarray:
    """Keep the voiced frames and at most gap_secs of each pause between them, so the clip is
    speech and not room: leading and trailing silence go, long pauses shrink."""
    idx = np.flatnonzero(voiced)
    if not len(idx):
        return pcm[:0]
    keep = np.zeros(len(voiced), bool)
    keep[idx] = True
    max_gap = int(gap_secs * rate / frame)
    for a, b in zip(idx[:-1], idx[1:]):           # a pause keeps its edges, the middle of a long one goes
        k = min(b - a - 1, max_gap)
        keep[a + 1:a + 1 + k // 2] = True
        keep[b - (k - k // 2):b] = True
    return np.concatenate([pcm[i * frame:(i + 1) * frame] for i in np.flatnonzero(keep)])


def level(pcm: np.ndarray) -> np.ndarray:
    """Speech to about -20 dBFS RMS, peaks held under -1 dBFS."""
    x = pcm.astype(np.float32) / 32768.0
    rms = float(np.sqrt(np.mean(x ** 2))) if len(x) else 0.0
    if rms > 0:
        x *= min(TARGET_RMS / rms, PEAK / max(float(np.abs(x).max()), 1e-6))
    return (np.clip(x, -1, 1) * 32767).astype(np.int16)


def save(name: str, clip: np.ndarray, consent: np.ndarray, rate: int, consent_text: str,
         score: float | None = None, model: str | None = None) -> Clone:
    """Store a clone, replacing an earlier one of the same name, with the same-voice score when the check
    ran (score None: not checked). The clip is written last and atomically, since the speech server may
    read it the moment it exists."""
    s = slug(name)
    if not s:
        raise ValueError(f"no usable name in {name!r}")
    clip = clip[:int(MAX_CLIP_SECS * rate)]
    c = Clone(slug=s, name=name.strip(), created=time.time(), consent_text=consent_text,
              seconds=round(len(clip) / rate, 1), consent_score=score,
              consent_checked_at=time.time() if score is not None else None,
              consent_model=model if score is not None else None)
    d = friends_dir()
    write_wav(d / f"{s}.consent.wav", consent, rate)
    (d / f"{s}.json").write_text(json.dumps(asdict(c), indent=2) + "\n")
    tmp = d / f".{s}.wav.tmp"
    write_wav(tmp, clip, rate)
    tmp.replace(d / f"{s}.wav")
    return c
