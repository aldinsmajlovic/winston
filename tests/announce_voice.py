"""Announcement over voice, end to end, without a browser: a WebRTC client (aiortc) connects to the
running voice channel like the browser does, an announcement goes through winstond, and the
client records what Winston plays. Then it asks a follow-up out loud and records the answer.
Needs `winston up` (LLM, TTS, voice). Everything heard is transcribed with Parakeet.

    uv run python tests/announce_voice.py

Pass: the chime plays before the speech, the announcement is spoken word for word, and the
follow-up answer uses the context the announcement carried (here, the finish time)."""
import asyncio
import fractions
import sys
import time

import httpx
import numpy as np
from aiortc import MediaStreamTrack, RTCPeerConnection, RTCSessionDescription
from av import AudioFrame

from winston.config import load
from winston.plugins.stt.parakeet import ParakeetSTTService

CFG = load()
VOICE = f"http://{CFG.channels.voice.host}:{CFG.channels.voice.port}"
API = f"http://{CFG.control.host}:{CFG.control.port}"
RATE = 48000
FRAME = RATE // 50          # 20 ms


class Mic(MediaStreamTrack):
    """Silence, or queued speech, in real time."""
    kind = "audio"

    def __init__(self):
        super().__init__()
        self.queue = np.zeros(0, dtype=np.int16)
        self._t0, self._n = None, 0

    def say(self, pcm48: np.ndarray):
        self.queue = np.concatenate([self.queue, pcm48, np.zeros(RATE, dtype=np.int16)])

    async def recv(self):
        if self._t0 is None:
            self._t0 = time.monotonic()
        wait = self._t0 + self._n * FRAME / RATE - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        chunk, self.queue = self.queue[:FRAME], self.queue[FRAME:]
        chunk = np.pad(chunk, (0, FRAME - len(chunk)))
        f = AudioFrame.from_ndarray(chunk.reshape(1, -1), format="s16", layout="mono")
        f.sample_rate, f.pts, f.time_base = RATE, self._n * FRAME, fractions.Fraction(1, RATE)
        self._n += 1
        return f


class Ear:
    """Everything Winston plays, as 16 kHz mono float with wall-clock marks."""

    def __init__(self):
        self.chunks: list[np.ndarray] = []
        self.rate = None

    async def consume(self, track):
        while True:
            try:
                f = await track.recv()
            except Exception:
                return
            a = f.to_ndarray().astype(np.float32)
            a = a.reshape(-1, len(f.layout.channels)).mean(axis=1) if a.ndim > 1 or len(f.layout.channels) > 1 else a
            self.rate = f.sample_rate
            self.chunks.append(a.ravel())

    def mark(self) -> int:
        return len(self.chunks)

    def since(self, mark: int) -> np.ndarray:
        a = np.concatenate(self.chunks[mark:]) if len(self.chunks) > mark else np.zeros(1, np.float32)
        n = int(len(a) * 16000 / self.rate)
        return (np.interp(np.linspace(0, len(a) - 1, n), np.arange(len(a)), a) / 32768).astype(np.float32)


def speech(text: str, voice: str = "vctk/p226_023") -> np.ndarray:
    pcm = httpx.post(f"{CFG.tts.base_url}/audio/speech", json={"input": text, "voice": voice,
                                                              "response_format": "pcm"}, timeout=120).content
    a = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    n = int(len(a) * RATE / CFG.tts.sample_rate)
    return np.interp(np.linspace(0, len(a) - 1, n), np.arange(len(a)), a).astype(np.int16)


def chime_first(a: np.ndarray) -> bool:
    """The 880 Hz chime note dominates the first half second of sound."""
    start = int(np.argmax(np.abs(a) > 0.02))
    seg = a[start:start + 8000]
    spec = np.abs(np.fft.rfft(seg * np.hanning(len(seg))))
    freqs = np.fft.rfftfreq(len(seg), 1 / 16000)
    return abs(freqs[np.argmax(spec)] - 880) < 30 or abs(freqs[np.argmax(spec)] - 659) < 30


async def main():
    stt = ParakeetSTTService.load_model(CFG.stt.model, CFG.stt.quantization)
    hear = lambda a: stt.recognize(a, sample_rate=16000).strip()   # noqa: E731
    question = speech("How long did it run?")
    mic, ear = Mic(), Ear()
    pc = RTCPeerConnection()
    pc.addTrack(mic)

    @pc.on("track")
    def on_track(track):
        if track.kind == "audio":
            asyncio.ensure_future(ear.consume(track))

    await pc.setLocalDescription(await pc.createOffer())
    async with httpx.AsyncClient(timeout=30) as c:
        ans = (await c.post(f"{VOICE}/api/offer", json={"sdp": pc.localDescription.sdp,
                                                        "type": pc.localDescription.type})).json()
    await pc.setRemoteDescription(RTCSessionDescription(sdp=ans["sdp"], type=ans["type"]))
    await asyncio.sleep(6)
    print(f"greeting: {hear(ear.since(0))!r}")

    m = ear.mark()
    d = httpx.post(f"{API}/api/announce", json={"text": "The washing machine is done.", "source": "test",
                                                 "context": {"started": "12:58", "finished": "14:31"}}).json()
    print(f"announce: {d}")
    await asyncio.sleep(7)
    heard = ear.since(m)
    said = hear(heard)
    print(f"heard: {said!r}  chime first: {chime_first(heard)}")

    m = ear.mark()
    t0 = time.monotonic()
    mic.say(question)
    await asyncio.sleep(len(question) / RATE + 12)
    reply = hear(ear.since(m))
    print(f"asked 'How long did it run?', heard back ({time.monotonic() - t0:.0f} s window): {reply!r}")
    await pc.close()

    ok = ("washing machine is done" in said.lower() and chime_first(heard) and d["spoken_on"] >= 1
          and any(k in reply.lower() for k in ("hour", "minute", "33", "1:33", "one")))
    print("PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


asyncio.run(main())
