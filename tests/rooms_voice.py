"""Rooms, end to end: a box and a browser in the same room hear the same request, and he answers once.

A fake Voice PE (tests/satellite_fake.py) runs the real satellite channel in this process, set up for a
room of its own ("test room" and a random suffix, so no earlier run's conversation is in it) with its
actions stubbed. A WebRTC client (aiortc) joins the running voice channel through winstond's
/api/voice/offer with {"room": ...}, like the dashboard at /dashboard?room=....
Needs `winston up` (winstond, LLM, TTS, voice).

    uv run python tests/rooms_voice.py

The browser session is the running voice channel with its real tools, so nothing said here asks for an
action: if the pause failed, he would answer a question, not switch something. The test stops before
speaking unless winstond shows the browser session in the room.

1. "Okay Winston, what's the capital of Norway?" reaches the browser's mic whole, and the box's mic from
   the end of the wake word, as a real box would stream it. The box's reply is played into the browser's
   mic as it streams, as its speaker would be heard in the room.
2. Then the browser asks "How many people live there?", which only the box's turn explains.

Pass: the box answered and the browser session said nothing and took no turn while the box had the mic
(the live feed shows no voice turn; the mic was held and let go); the browser's answer is about Oslo or
Norway; the room's conversation has both turns."""
import asyncio
import json
import sys
import tempfile
import time
import uuid
from pathlib import Path

import httpx
import numpy as np
from aiortc import RTCPeerConnection, RTCSessionDescription

sys.path.insert(0, str(Path(__file__).parent))
from announce_voice import Mic, speech
from conversations import ACT_STUBS, stub
from m2_headless_roundtrip import synth_user_audio
from satellite_fake import FakeVoicePE, USER
from winston.channels import satellite as sat
from winston.config import load
from winston.config.schema import SatelliteDevice
from winston.core import brain as brain_mod
from winston.core.hints import Hearing

ROOM = f"test room {uuid.uuid4().hex[:6]}"      # its own conversation, not a leftover of the last run
PORT, HTTP_PORT = 16054, 18703
RATE = 48000


class Room(Mic):
    """The browser's mic in the room: what the user says, and the box's speaker as it plays."""

    def __init__(self):
        super().__init__()
        self._rest, self._header = b"", 44

    def play(self, part: bytes):
        """WAV bytes as they arrive from the box's reply URL (48 kHz 16-bit mono after a 44-byte header)."""
        skip = min(self._header, len(part))
        part, self._header = part[skip:], self._header - skip
        data = self._rest + part
        n = len(data) // 2 * 2
        self._rest = data[n:]
        self.queue = np.concatenate([self.queue, np.frombuffer(data[:n], dtype="<i2")])


class Live:
    """The live feed from winstond, from now on."""

    def __init__(self, api: str):
        self.api, self.events, self.t0 = api, [], time.time()

    async def run(self):
        async with (httpx.AsyncClient(timeout=httpx.Timeout(5, read=None)) as c,
                    c.stream("GET", f"{self.api}/api/live") as r):
                async for line in r.aiter_lines():
                    if line.startswith("data: "):
                        ev = json.loads(line[6:])
                        if ev.get("ts", 0) >= self.t0:
                            self.events.append(ev)

    def since(self, t: float, **match) -> list[dict]:
        return [e for e in self.events if e["ts"] >= t and all(e.get(k) == v for k, v in match.items())]

    def said(self, t: float, channel: str) -> str:
        return "".join(e.get("text", "") for e in self.since(t, channel=channel, type="reply"))


async def main() -> int:
    cfg = load()
    api = cfg.control.url
    cfg.channels.satellite.devices = {ROOM: SatelliteDevice(host="127.0.0.1", port=PORT, wake_word="hey_jarvis")}
    cfg.channels.satellite.http_port, cfg.channels.satellite.public_host = HTTP_PORT, "127.0.0.1"
    cfg.logging.tool_calls = str(Path(tempfile.gettempdir()) / "winston-rooms-voice.jsonl")    # not data/logs
    cfg.logging.latency = ""

    start = brain_mod.Brain.start

    async def stubbed_start(self):                          # the box's actions touch nothing
        await start(self)
        for t in self.tools.standard_tools:
            if t.name in ACT_STUBS:
                self.llm.register_function(t.name, stub(t.name))
    brain_mod.Brain.start = stubbed_start

    class NoFeed:
        def put(self, ev):
            pass

    live = Live(api)
    feed = asyncio.create_task(live.run())
    box = FakeVoicePE()
    server = await asyncio.start_server(box.serve, "127.0.0.1", PORT)
    audio = sat.AudioServer(cfg); await audio.start()
    stt = Hearing.load(cfg)
    satellite = sat.Satellite(cfg, ROOM, cfg.channels.satellite.devices[ROOM], audio, stt, NoFeed())
    await satellite.connect()
    await asyncio.wait_for(box.subscribed.wait(), 15)

    mic = Room()
    pc = RTCPeerConnection()
    pc.addTrack(mic)
    await pc.setLocalDescription(await pc.createOffer())
    async with httpx.AsyncClient(timeout=30) as c:
        ans = (await c.post(f"{api}/api/voice/offer", json={"sdp": pc.localDescription.sdp, "type": pc.localDescription.type,
                                                             "requestData": {"room": ROOM}})).json()
    await pc.setRemoteDescription(RTCSessionDescription(sdp=ans["sdp"], type=ans["type"]))
    await asyncio.sleep(8)                                  # the greeting, and the browser session settles
    rooms = (await asyncio.to_thread(httpx.get, f"{api}/api/rooms")).json()
    state = next((x for x in rooms if x["name"].casefold() == ROOM), {})
    if not state.get("screens"):
        print(f"FAIL: the browser session is not in {ROOM!r} ({state}); stopping before anything is said")
        await pc.close(); await satellite.stop(); server.close(); feed.cancel()
        return 1

    # 1. The request, heard by both mics; the box's reply played into the room.
    ask = "What's the capital of Norway?"
    wake, request = speech("Okay Winston."), speech(ask)
    t1 = time.time()
    mic.say(np.concatenate([wake, request]))
    await asyncio.sleep(len(wake) / RATE)                   # the box wakes as the wake word ends
    r = await box.converse(synth_user_audio(ask, USER)[0], "hey_jarvis", on_audio=mic.play)
    print(f"box heard {r['heard']!r}, said {r['said']!r} ({r['audio_secs']:.1f} s)")
    for _ in range(300):                                    # until the room's mic is free again
        freed = live.since(t1, type="mic", room=ROOM)
        if freed and not freed[-1].get("held_by"):
            break
        await asyncio.sleep(0.1)
    await asyncio.sleep(3)                                  # anything the browser took as a turn would show by now
    mics = [e.get("held_by") for e in live.since(t1, type="mic", room=ROOM)]
    browser_turns = live.since(t1, channel="voice", type="user") + live.since(t1, channel="voice", type="reply")
    print(f"mic: {mics}; browser turns while the box had it: {[(e['type'], e.get('text')) for e in browser_turns]}")

    # 2. The next question at the screen.
    t2 = time.time()
    mic.say(speech("How many people live there?"))
    for _ in range(300):
        if live.since(t2, channel="voice", type="reply_end"):
            break
        await asyncio.sleep(0.1)
    await asyncio.sleep(0.5)
    heard = [e.get("text") for e in live.since(t2, channel="voice", type="user")]
    answer = live.said(t2, "voice")
    print(f"browser heard {heard}, answered {answer!r}")
    rooms = (await asyncio.to_thread(httpx.get, f"{api}/api/rooms")).json()
    held = next((x for x in rooms if x["name"].casefold() == ROOM), {})
    print(f"room: {held}")

    await pc.close()
    await satellite.stop()
    server.close()
    feed.cancel()

    checks = {
        "the box answered": bool(r["said"]) and r["audio_secs"] > 0.5,
        "the mic was held, then let go": "box" in mics and mics[-1] is None,
        "the browser took no turn while the box had it": not browser_turns,
        "the browser heard the question": any("people" in (h or "").lower() for h in heard),
        "the answer comes from the box's turn": any(w in answer.lower() for w in ("oslo", "norway")),
        "the room has both turns": held.get("messages", 0) >= 4,
    }
    for name, ok in checks.items():
        print(f"{'ok  ' if ok else 'FAIL'} {name}")
    ok = all(checks.values())
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
