"""The consent record and its same-voice check (core/speaker, channels/clone). Runs against a scratch data
directory with the VCTK clips shipped in assets/voices, no hardware needed; the speaker model is
downloaded to the Hugging Face cache on the first run (26.5 MB).

    uv run python tests/consent_cases.py

Each VCTK clip is one speaker: its first 4 s stand in for the consent line and the rest (other words,
repeated to fifteen seconds of speech) for the voice sample. Same speaker must score at or above
SAME_VOICE, different speakers below it. clone_voice then runs whole with a scripted mic: a match is
stored with its score, a mismatch keeps nothing at all, and a missing model stores the clone as not checked."""
import asyncio
import json
import os
import tempfile
import wave
from itertools import combinations
from pathlib import Path
from types import SimpleNamespace

import numpy as np

scratch = Path(tempfile.mkdtemp(prefix="winston-consent-"))
os.environ["WINSTON_DATA"] = str(scratch)
ASSETS = Path(__file__).resolve().parent.parent / "assets" / "voices" / "vctk"

from winston.channels import clone as clone_channel  # noqa: E402  (after WINSTON_DATA is set)
from winston.config.schema import WinstonConfig  # noqa: E402
from winston.core import speaker, voices  # noqa: E402

RATE, SPLIT = 16000, 4 * 16000
failed = 0


def expect(name: str, ok: bool, detail=""):
    global failed
    failed += not ok
    print(f"{'ok  ' if ok else 'FAIL'} {name} {detail}")


def clip(speaker_id: str) -> np.ndarray:
    with wave.open(str(ASSETS / f"{speaker_id}_023.wav")) as w:
        x, r = np.frombuffer(w.readframes(w.getnframes()), "<i2"), w.getframerate()
    n = int(len(x) * RATE / r)
    return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.int16)


def consent_of(x: np.ndarray) -> np.ndarray:
    return x[:SPLIT]


def sample_of(x: np.ndarray) -> np.ndarray:
    return np.tile(x[SPLIT:], 3)            # other words than the consent part, past fifteen seconds of speech


clips = {s: clip(s) for s in ("p226", "p232", "p243", "p254", "p258", "p273")}

# the score itself
for s, x in clips.items():
    score = speaker.compare(consent_of(x), sample_of(x))
    expect(f"same speaker {s} passes", speaker.same_voice(score) is True, score)
for a, b in combinations(clips, 2):
    score = speaker.compare(consent_of(clips[a]), sample_of(clips[b]))
    expect(f"{a} agreeing, {b} recorded fails", speaker.same_voice(score) is False, score)
expect("too short to say is not checked", speaker.compare(clips["p226"][:RATE // 2], sample_of(clips["p226"])) is None)
expect("no score is neither same nor different", speaker.same_voice(None) is None)

# an old clone has no check and loads as not checked
friends = voices.friends_dir()
friends.mkdir(parents=True)
(friends / "old.json").write_text(json.dumps({"slug": "old", "name": "Old", "created": 0, "consent_text": "x",
                                              "seconds": 1}))
voices.write_wav(friends / "old.wav", np.zeros(RATE, np.int16), RATE)
old = voices.find("Old")
expect("an old clone loads as not checked",
       old is not None and (old.consent_score, old.consent_checked_at, old.consent_model) == (None, None, None), old)
voices.delete("old")


class ScriptedTap(clone_channel.MicTap):
    """The mic under the tap: each recording hands over the next scripted audio at once, then silence."""

    def __init__(self, recordings: list[np.ndarray]):
        super().__init__()
        self.recordings = list(recordings)

    def start_recording(self):
        self._rec = [self.recordings.pop(0) if self.recordings else np.zeros(RATE // 10, np.int16)]

    async def said(self, timeout: float = 60.0):
        pass


class FakeLLM:
    def __init__(self):
        self.said: list[str] = []

    async def push_frame(self, frame, direction=None):
        if text := getattr(frame, "text", None):
            self.said.append(text)


async def clone_run(name: str, agreeing: np.ndarray, talking: np.ndarray) -> tuple[dict, list[str]]:
    """clone_voice from start to end: the consent line, then the sample, with speech to text scripted."""
    cfg = WinstonConfig.model_validate({"llm": {"model": "x"}})
    silence = np.zeros(int(1.5 * RATE), np.int16)
    tap = ScriptedTap([np.concatenate([agreeing, silence]), talking])

    async def transcribe(pcm, extra):
        return f"I'm {name}, and I agree that {cfg.assistant.name} can copy my voice."

    tools = {s.name: h for s, h in clone_channel.make_voice_tools(cfg, tap, transcribe)}
    got: dict = {}
    llm = FakeLLM()

    async def result_callback(result, properties=None):
        got.update(result)

    await tools["clone_voice"](SimpleNamespace(arguments={"name": name}, llm=llm, result_callback=result_callback))
    return got, llm.said


def stored() -> list[str]:
    return sorted(p.name for p in friends.iterdir())


# the same voice agrees and talks: kept with its score, the time and the model
result, said = asyncio.run(clone_run("Erik", consent_of(clips["p243"]), sample_of(clips["p243"])))
erik = voices.find("Erik")
expect("same voice is kept", erik is not None and result.get("cloned") == "Erik", result)
expect("with its score, time and model", erik is not None and speaker.same_voice(erik.consent_score) is True
       and erik.consent_checked_at is not None and erik.consent_model == speaker.MODEL, erik)
expect("the score is in the tool result", result.get("consent_score") == (erik.consent_score if erik else None), result)
expect("the consent line names him from config, no weekday",
       any("I agree that Winston can copy my voice." in s for s in said), said)

# someone else talks after the friend agreed: nothing kept, not even the consent, and he says why
before = stored()
result, said = asyncio.run(clone_run("Sara", consent_of(clips["p226"]), sample_of(clips["p232"])))
expect("a different voice keeps nothing", stored() == before and voices.find("Sara") is None, stored())
expect("the refusal is spoken",
       "That doesn't sound like the person who agreed, so I won't keep it." in said, said)
expect("the score is logged with the tool call",
       "error" in result and speaker.same_voice(result.get("consent_score")) is False, result)

# without the model the clone is stored as not checked, never refused
from onnx_asr.loader import Manager  # noqa: E402


def no_model(*args, **kwargs):
    raise FileNotFoundError("model files not in the cache and no network")


Manager.create_se = no_model                # what an offline first use or a removed cache looks like
speaker._model, speaker._failed = None, None
result, said = asyncio.run(clone_run("Omar", consent_of(clips["p254"]), sample_of(clips["p232"])))
omar = voices.find("Omar")
expect("a missing model stores the clone", omar is not None and result.get("cloned") == "Omar", result)
expect("as not checked", omar is not None and omar.consent_score is None and omar.consent_checked_at is None
       and omar.consent_model is None and result.get("consent_score") is None, omar)

print(f"\n{'all passed' if not failed else f'{failed} FAILED'} (scratch data in {scratch})")
raise SystemExit(1 if failed else 0)
