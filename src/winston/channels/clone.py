"""Cloning a voice by asking, on the voice channel: "clone my friend Erik's voice", or "clone my voice".

The clone_voice tool asks the friend to agree out loud, checks the words with the channel's own
STT, has them talk for about fifteen seconds and keeps only the speech. Before anything is stored, a
speaker-embedding model (winston.core.speaker) checks that the voice agreeing is the voice in the
sample; when they differ nothing is kept, not even the consent. Otherwise it stores the clip with the
consent recording and the score (winston.core.voices) and switches the speech voice to it for the rest
of the conversation. While it records, MicTap holds the mic, so the friend is never taken for a turn and
cannot interrupt the tool; Winston says nothing over them. use_voice switches between stored
clones and back, and can keep a choice by saving it to config through winstond.

Plan §6 rule 4: both are act tools, logged like every call. Nothing is stored without the spoken
consent; the consent clip stays beside the voice. When the speaker model is not available the clone is
kept as not checked, which the Voice page shows."""
import asyncio
import re
import time
from collections.abc import Awaitable, Callable

import httpx
import numpy as np
from loguru import logger
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import (BotStartedSpeakingFrame, BotStoppedSpeakingFrame, Frame, FunctionCallResultProperties,
                                   InputAudioRawFrame, TTSSpeakFrame, TTSUpdateSettingsFrame)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.llm_service import FunctionCallParams
from pipecat.services.openai.tts import OpenAITTSService

from winston.config.schema import WinstonConfig
from winston.core import speaker, voices

RATE = 16000
VAD_FRAME = 512                  # Silero's window at 16 kHz, 32 ms
CONSENT_LINE = "I'm {name}, and I agree that {assistant} can copy my voice."   # {assistant}: assistant.name
WANT_SPEECH_SECS = 15.0          # speech to collect for the clip
MIN_SPEECH_SECS = 6.0            # below this the clone sounds like no one
_AGREE = re.compile(r"\bagree\b")
_REFUSE = re.compile(r"\b(?:don'?t|do not|not|never|disagree|no)\b[^.]*\bagree|\bdisagree\b")


class MicTap(FrameProcessor):
    """Sits right after the transport input. Normally it only watches Winston start and stop
    speaking (those frames travel upstream from the output). While muted, mic audio stops here,
    and while recording it is kept, resampled to 16 kHz."""

    def __init__(self):
        super().__init__()
        self.muted = False
        self._rec: list[np.ndarray] | None = None
        self._started = asyncio.Event()
        self._stopped = asyncio.Event()

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, InputAudioRawFrame) and self.muted:
            if self._rec is not None:
                self._rec.append(_mono16k(frame))
            return
        if isinstance(frame, BotStartedSpeakingFrame):
            self._stopped.clear(); self._started.set()
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._started.clear(); self._stopped.set()
        await self.push_frame(frame, direction)

    async def said(self, timeout: float = 60.0):
        """Wait until the speech queued just before has been played out."""
        try:
            await asyncio.wait_for(self._started.wait(), 10)
            await asyncio.wait_for(self._stopped.wait(), timeout)
        except TimeoutError:
            logger.warning("clone: Winston's speech did not start or stop in time")
        await asyncio.sleep(0.3)                    # the room's echo of the last word

    def start_recording(self):
        self._rec = []

    def take(self) -> np.ndarray:
        """Everything recorded since start_recording, as 16 kHz int16."""
        return np.concatenate(self._rec) if self._rec else np.zeros(0, np.int16)

    def stop_recording(self) -> np.ndarray:
        buf, self._rec = self.take(), None
        return buf


def _mono16k(f: InputAudioRawFrame) -> np.ndarray:
    x = np.frombuffer(f.audio, dtype="<i2")
    if f.num_channels > 1:
        x = x.reshape(-1, f.num_channels).mean(axis=1).astype(np.int16)
    if f.sample_rate != RATE:
        n = int(len(x) * RATE / f.sample_rate)
        x = np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.int16)
    return x


class Capture:
    """Speech detection over what MicTap records, 32 ms at a time."""

    def __init__(self, tap: MicTap, vad: SileroVADAnalyzer):
        self.tap, self.vad = tap, vad

    async def run(self, *, max_secs: float, start_timeout: float, want_speech_secs: float | None = None,
                  end_silence_secs: float | None = None, min_speech_secs: float = 0.0
                  ) -> tuple[np.ndarray, np.ndarray] | None:
        """Record until enough speech, a pause of end_silence_secs once min_speech_secs were spoken,
        or max_secs. Returns the audio and a voiced flag per VAD frame, or None when nobody spoke
        within start_timeout."""
        self.tap.start_recording()
        voiced: list[bool] = []
        t0, frame_secs = time.perf_counter(), VAD_FRAME / RATE
        try:
            while True:
                await asyncio.sleep(0.1)
                buf = self.tap.take()
                while (len(voiced) + 1) * VAD_FRAME <= len(buf):
                    i = len(voiced) * VAD_FRAME
                    voiced.append(self.vad.voice_confidence(buf[i:i + VAD_FRAME].tobytes()) >= 0.5)
                elapsed, speech = time.perf_counter() - t0, sum(voiced) * frame_secs
                last = max((i for i, v in enumerate(voiced) if v), default=None)
                if last is None:
                    if elapsed > start_timeout:
                        return None
                    continue
                silence = (len(voiced) - 1 - last) * frame_secs
                if (want_speech_secs and speech >= want_speech_secs) or elapsed >= max_secs or \
                        (end_silence_secs and silence >= end_silence_secs and speech >= min_speech_secs):
                    return buf[:len(voiced) * VAD_FRAME], np.array(voiced)
        finally:
            self.tap.stop_recording()


def agreed(text: str) -> bool:
    t = text.lower()
    return bool(_AGREE.search(t)) and not _REFUSE.search(t)


def own_voice(cfg: WinstonConfig) -> str:
    """His own voice: the configured one, unless a clone was kept there."""
    return cfg.tts.voice if not cfg.tts.voice.startswith("friends/") else "onyx"


async def keep_voice(cfg: WinstonConfig, voice: str) -> str | None:
    """Save the voice as tts.voice through winstond, so the UI and the next session agree. Error or None.
    A patch of that one field, so a settings page saved meanwhile is not overwritten; this channel already
    speaks in it, so it is not marked for a restart (the satellite still is)."""
    url = f"http://{cfg.control.host}:{cfg.control.port}/api/config"
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.patch(url, params={"applied": "voice"},
                              json={"changes": [{"path": ["tts", "voice"], "value": voice}]})
            r.raise_for_status()
    except httpx.HTTPError as e:
        return f"could not save it to the config: {type(e).__name__}"
    return None


def make_voice_tools(cfg: WinstonConfig, tap: MicTap, transcribe: Callable[[np.ndarray, list[str]], Awaitable[str]]):
    """clone_voice and use_voice for the voice channel. transcribe turns 16 kHz int16 into text, with
    extra hint words (the friend's name) on top of the configured ones."""
    vad = SileroVADAnalyzer()
    vad.set_sample_rate(RATE)
    capture = Capture(tap, vad)
    me = cfg.assistant.name

    async def say(params: FunctionCallParams, text: str, wait: bool = True):
        # kept out of the context: "now talk to me for fifteen seconds" read back later as his own words
        # made him wait for the user to talk ("talk about something" got "Sure." and nothing more)
        await params.llm.push_frame(TTSSpeakFrame(text, append_to_context=False))
        if wait:
            await tap.said()

    async def switch(params: FunctionCallParams, voice: str):
        await params.llm.push_frame(TTSUpdateSettingsFrame(delta=OpenAITTSService.Settings(voice=voice)))

    async def clone_voice(params: FunctionCallParams):
        name = " ".join(str(params.arguments.get("name", "")).split()).strip()
        own = bool(params.arguments.get("own", False))
        if not voices.slug(name):
            return await params.result_callback({"error": "say whose voice it is, by first name"})
        speaker.warm()                  # loads while they talk
        tap.muted = True
        try:
            await say(params, ("Alright. So I know you agree, say: " if own else
                               f"Alright. {name}, if you agree, please say: ") + CONSENT_LINE.format(name=name, assistant=me))
            got = await capture.run(max_secs=12, start_timeout=8, end_silence_secs=1.0)
            if got is None:
                await say(params, "I didn't hear anything, so I'll leave it.")
                return await params.result_callback({"error": "nobody spoke, nothing recorded"},
                                                    properties=FunctionCallResultProperties(run_llm=False))
            consent, consent_voiced = got
            heard = (await transcribe(consent, [name])).strip()
            logger.info(f"clone: consent from {name!r} heard as {heard!r}")
            if not agreed(heard):
                await say(params, "That didn't sound like a yes, so I won't record anything.")
                return await params.result_callback({"error": f"no consent (heard {heard!r}), nothing recorded"},
                                                    properties=FunctionCallResultProperties(run_llm=False))
            await say(params, "Thanks. Now talk to me for about fifteen seconds, about anything at all. "
                              "I'll tell you when I have enough.")
            got = await capture.run(max_secs=40, start_timeout=10, want_speech_secs=WANT_SPEECH_SECS,
                                    end_silence_secs=2.5, min_speech_secs=10.0)
            speech = float(got[1].sum()) * VAD_FRAME / RATE if got else 0.0
            if got is None or speech < MIN_SPEECH_SECS:
                await say(params, "That was too short to copy a voice from, so I'll leave it for now.")
                return await params.result_callback({"error": f"only {speech:.0f} s of speech, nothing stored"},
                                                    properties=FunctionCallResultProperties(run_llm=False))
            t_end = time.perf_counter()
            clip = voices.level(voices.speech_only(got[0], got[1], RATE, VAD_FRAME))
            agreeing = voices.speech_only(consent, consent_voiced, RATE, VAD_FRAME)
            score = await asyncio.to_thread(speaker.compare, agreeing, clip)
            check = {"consent_score": score, "same_voice_at": speaker.SAME_VOICE}
            if speaker.same_voice(score) is False:
                logger.info(f"clone: {name!r} not kept, the voices differ (score {score}, needs {speaker.SAME_VOICE})")
                await say(params, "That doesn't sound like the person who agreed, so I won't keep it.")
                await capture.run(max_secs=5, start_timeout=0.7, end_silence_secs=0.7)   # until they stop talking
                return await params.result_callback(
                    {"error": "the voice in the sample is not the voice that agreed, nothing kept", **check},
                    properties=FunctionCallResultProperties(run_llm=False))
            clone = await asyncio.to_thread(voices.save, name, clip, consent, RATE, heard, score,
                                            speaker.MODEL if score is not None else None)
            await switch(params, clone.voice)
            checked = f"same voice, {score}" if score is not None else "not checked"
            logger.info(f"clone: stored {clone.voice} ({clone.seconds} s of {speech:.0f} s speech, {checked}), "
                        f"switched in {(time.perf_counter() - t_end) * 1000:.0f} ms")
            # still muted: the friend is usually mid-sentence when the recording stops, and their last words
            # would interrupt this line and become a turn of their own
            await say(params, "Got it. This is me in your voice now." if own else f"Got it. This is me as {name} now.")
            await capture.run(max_secs=5, start_timeout=0.7, end_silence_secs=0.7)   # until they stop talking
            await params.result_callback(
                {"cloned": name, "voice": clone.voice, "seconds": clone.seconds, **check,
                 "note": "Done and over: consent given, recording finished and saved, and you have already said "
                         "aloud that you now speak in this voice. Nobody needs to talk for it any more; carry on the "
                         "conversation normally, in this voice, until the user asks for your own voice back "
                         "(use_voice)."},
                properties=FunctionCallResultProperties(run_llm=False))
        finally:
            tap.muted = False

    async def use_voice(params: FunctionCallParams):
        name = str(params.arguments.get("name", "")).strip()
        keep = bool(params.arguments.get("keep", False))
        if voices.slug(name) in ("own", "your-own", "default", "yours", voices.slug(me)):
            voice, who = own_voice(cfg), "own"
        elif clone := voices.find(name):
            voice, who = clone.voice, clone.name
        else:
            known = [c.name for c in voices.clones()]
            return await params.result_callback({"error": f"no cloned voice called {name!r}; cloned voices are "
                                                          f"{known or 'none yet'}"})
        await switch(params, voice)
        result = {"voice": who, "kept": False}
        if keep:
            if err := await keep_voice(cfg, voice):
                result["error_keeping"] = err
            else:
                result["kept"] = True
        await params.result_callback(result)

    known = [c.name for c in voices.clones()]
    clone_schema = FunctionSchema(
        name="clone_voice",
        description="Clone a voice and switch to it: a friend's, or the user's own (\"clone my voice\"). Only this "
                    "tool can record; it speaks the instructions and records by itself. So whenever the user asks "
                    "to clone, copy or take a voice, call it at once and say nothing first: never ask anyone to "
                    "start talking yourself. For a voice already cloned, use use_voice instead unless the user asks "
                    "to record it again. Only for someone at the mic who agrees for themselves: never a celebrity, "
                    "an actor or anyone who is not here.",
        properties={"name": {"type": "string",
                             "description": "Whose voice, by first name; the user's own name for their own voice."},
                    "own": {"type": "boolean", "description": "True when it is the user's own voice."}},
        required=["name"])
    use_schema = FunctionSchema(
        name="use_voice",
        description="Switch the voice you speak in: a cloned voice by the person's name, or \"own\" for your own "
                    f"({me}'s) voice back. \"Use my voice\" from the user means their cloned voice, under their "
                    "name, not \"own\". The switch lasts this conversation; set keep only when the user asks to keep "
                    "it. Clones are listed and deleted on the Voice page of the web UI, not by voice. "
                    f"Cloned voices: {', '.join(known) if known else 'none yet'}.",
        properties={"name": {"type": "string", "description": "A cloned friend's name, or \"own\"."},
                    "keep": {"type": "boolean", "description": "Save it as your voice from now on."}},
        required=["name"])
    return [(clone_schema, clone_voice), (use_schema, use_voice)]
