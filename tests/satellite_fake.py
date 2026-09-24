"""The satellite channel against a fake Voice Preview Edition: a small server speaking the ESPHome
native API (plaintext framing) the way the box does. The real channel connects to it, subscribes
to the voice assistant and runs the brain, STT and TTS; the fake wakes up, streams a synthetic
request as 16 kHz microphone audio until Winston sends STT_VAD_END, fetches the reply URL when told
to and follows up when continue_conversation is set. Needs the LLM (:8000) and the TTS (:8880).

    .venv/bin/python tests/satellite_fake.py

Actions are stubbed as in tests/conversations.py. Passes when every request gets a spoken reply
fetched from the URL, the events come in the box's order, and a question keeps the conversation
open without a wake word."""
import asyncio
import sys
import time
from pathlib import Path

import aiohttp
import numpy as np
from aioesphomeapi import VoiceAssistantEventType as Ev
from aioesphomeapi import VoiceAssistantFeature as Feature
from aioesphomeapi import api_pb2 as pb
from aioesphomeapi.core import MESSAGE_TYPE_TO_PROTO

sys.path.insert(0, str(Path(__file__).parent))
from conversations import ACT_STUBS, stub  # noqa: E402
from m2_headless_roundtrip import synth_user_audio  # noqa: E402
from winston.channels import satellite as sat  # noqa: E402
from winston.config import load  # noqa: E402
from winston.config.registry import get as get_adapter  # noqa: E402
from winston.config.schema import SatelliteDevice  # noqa: E402
from winston.core import brain as brain_mod  # noqa: E402

from loguru import logger  # noqa: E402

logger.remove(); logger.add(sys.stderr, level="INFO")
PORT, HTTP_PORT = 16053, 18702
TYPE_OF = {cls: n for n, cls in MESSAGE_TYPE_TO_PROTO.items()}
USER = "vctk/p226_023"
FLAGS = (Feature.VOICE_ASSISTANT | Feature.API_AUDIO | Feature.ANNOUNCE | Feature.START_CONVERSATION
         | Feature.TIMERS | Feature.MULTI_CHANNEL_AUDIO)


def varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b, n = n & 0x7F, n >> 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


async def read_varint(r: asyncio.StreamReader) -> int:
    n = shift = 0
    while True:
        b = (await r.readexactly(1))[0]
        n |= (b & 0x7F) << shift
        shift += 7
        if not b & 0x80:
            return n


class FakeVoicePE:
    """The box's side of the API: handshake, device info, the voice assistant, events in, audio out."""

    def __init__(self):
        self.writer: asyncio.StreamWriter | None = None
        self.subscribed = asyncio.Event()
        self.events: list[tuple[float, Ev, dict]] = []
        self.new_event = asyncio.Event()
        self.response = asyncio.Event()
        self.active_wake_words: list[str] = []

    def send(self, msg):
        payload = msg.SerializeToString()
        self.writer.write(b"\x00" + varint(len(payload)) + varint(TYPE_OF[type(msg)]) + payload)

    async def serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self.writer = writer
        try:
            while True:
                if (await reader.readexactly(1)) != b"\x00":
                    raise ValueError("not a plaintext frame")
                size, kind = await read_varint(reader), await read_varint(reader)
                msg = MESSAGE_TYPE_TO_PROTO[kind]()
                msg.ParseFromString(await reader.readexactly(size))
                self.handle(msg)
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        except Exception as e:                                          # noqa: BLE001
            print(f"fake box: {type(e).__name__}: {e}", flush=True)
            raise

    def handle(self, msg):
        if isinstance(msg, pb.HelloRequest):
            self.send(pb.HelloResponse(api_version_major=1, api_version_minor=12, server_info="fake Voice PE",
                                       name="home-assistant-voice-fake"))
        elif isinstance(msg, pb.AuthenticationRequest):
            self.send(pb.AuthenticationResponse(invalid_password=False))
        elif isinstance(msg, pb.DeviceInfoRequest):
            self.send(pb.DeviceInfoResponse(name="home-assistant-voice-fake", friendly_name="Fake Voice PE",
                                            model="Home Assistant Voice PE", esphome_version="2026.9.0",
                                            mac_address="00:00:00:00:00:01", voice_assistant_feature_flags=FLAGS))
        elif isinstance(msg, pb.ListEntitiesRequest):
            self.send(pb.ListEntitiesDoneResponse())
        elif isinstance(msg, pb.PingRequest):
            self.send(pb.PingResponse())
        elif isinstance(msg, pb.DisconnectRequest):
            self.send(pb.DisconnectResponse())
        elif isinstance(msg, pb.SubscribeVoiceAssistantRequest):
            (self.subscribed.set if msg.subscribe else self.subscribed.clear)()
        elif isinstance(msg, pb.VoiceAssistantConfigurationRequest):
            words = [pb.VoiceAssistantWakeWord(id=i, wake_word=w, trained_languages=["en"]) for i, w in
                     (("okay_nabu", "Okay Nabu"), ("hey_jarvis", "Hey Jarvis"), ("hey_mycroft", "Hey Mycroft"))]
            self.send(pb.VoiceAssistantConfigurationResponse(available_wake_words=words, active_wake_words=["okay_nabu"],
                                                             max_active_wake_words=2))
        elif isinstance(msg, pb.VoiceAssistantSetConfiguration):
            self.active_wake_words = list(msg.active_wake_words)
        elif isinstance(msg, pb.VoiceAssistantResponse):
            self.response.set()
        elif isinstance(msg, pb.VoiceAssistantEventResponse):
            self.events.append((time.perf_counter(), Ev(msg.event_type), {d.name: d.value for d in msg.data}))
            self.new_event.set()

    async def wait_event(self, ev: Ev, since: int, timeout: float = 60) -> dict | None:
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < timeout:
            for _, e, data in self.events[since:]:
                if e == ev:
                    return data
                if e == Ev.VOICE_ASSISTANT_RUN_END and ev != e:
                    return None
            self.new_event.clear()
            try:
                await asyncio.wait_for(self.new_event.wait(), 0.5)
            except TimeoutError:
                pass
        return None

    async def converse(self, audio: np.ndarray, wake_word: str | None) -> dict:
        """One run as the box does it: request start, stream the mic until STT_VAD_END, play the reply."""
        since = len(self.events)
        self.response.clear()
        self.send(pb.VoiceAssistantRequest(start=True, conversation_id="", flags=1 if wake_word else 0,
                                           audio_settings=pb.VoiceAssistantAudioSettings(), wake_word_phrase=wake_word or ""))
        await asyncio.wait_for(self.response.wait(), 10)
        chunk = 512                                         # 32 ms at 16 kHz, like the box
        silence = np.zeros(chunk, np.int16)
        pcm = np.concatenate([np.zeros(4000, np.int16), audio])
        i, t_end = 0, None
        while not any(e == Ev.VOICE_ASSISTANT_STT_VAD_END for _, e, _ in self.events[since:]):
            piece = pcm[i:i + chunk] if i < len(pcm) else silence
            if i >= len(pcm) and t_end is None:
                t_end = time.perf_counter()
            self.send(pb.VoiceAssistantAudio(data=piece.tobytes(), data2=piece.tobytes()))
            i += chunk
            await asyncio.sleep(chunk / 16000)
            if i > len(pcm) + 16000 * 25:
                break
        heard = await self.wait_event(Ev.VOICE_ASSISTANT_STT_END, since)
        progress = await self.wait_event(Ev.VOICE_ASSISTANT_INTENT_PROGRESS, since)
        run_start = next((d for _, e, d in self.events[since:] if e == Ev.VOICE_ASSISTANT_RUN_START), {})
        t_fetch = time.perf_counter()
        wav, t_first = b"", None
        if progress and run_start.get("url"):
            async with aiohttp.ClientSession() as s, s.get(run_start["url"]) as r:
                async for part in r.content.iter_any():
                    if t_first is None and len(wav) + len(part) > 44:
                        t_first = time.perf_counter()
                    wav += part
        end = await self.wait_event(Ev.VOICE_ASSISTANT_INTENT_END, since) or {}
        spoken = await self.wait_event(Ev.VOICE_ASSISTANT_TTS_START, since) or {}
        await self.wait_event(Ev.VOICE_ASSISTANT_RUN_END, since)
        return {"heard": (heard or {}).get("text"), "said": spoken.get("text"),
                "follow_up": end.get("continue_conversation") == "1",
                "audio_secs": max(0, len(wav) - 44) / 2 / sat.OUT_RATE,
                "first_audio_ms": round((t_first - t_end) * 1000) if t_first and t_end else None,
                "order": [e.name.removeprefix("VOICE_ASSISTANT_") for _, e, _ in self.events[since:]]}


async def main() -> int:
    cfg = load()
    cfg.channels.satellite.devices = {"test room": SatelliteDevice(host="127.0.0.1", port=PORT, wake_word="hey_jarvis")}
    cfg.channels.satellite.http_port, cfg.channels.satellite.public_host = HTTP_PORT, "127.0.0.1"

    start = brain_mod.Brain.start

    async def stubbed_start(self):                          # the house is not touched
        await start(self)
        for t in self.tools.standard_tools:
            if t.name in ACT_STUBS:
                self.llm.register_function(t.name, stub(t.name))
    brain_mod.Brain.start = stubbed_start

    class NoFeed:
        def put(self, ev):
            pass

    box = FakeVoicePE()
    server = await asyncio.start_server(box.serve, "127.0.0.1", PORT)
    audio = sat.AudioServer(cfg); await audio.start()
    cls = get_adapter("stt", cfg.stt.provider)
    stt = (cls, cls.load_model(cfg.stt.model, cfg.stt.quantization))
    satellite = sat.Satellite(cfg, "test room", cfg.channels.satellite.devices["test room"], audio, stt, NoFeed())
    await satellite.connect()
    await asyncio.wait_for(box.subscribed.wait(), 15)
    print(f"subscribed; wake words set to {box.active_wake_words}")

    ok = box.active_wake_words == ["hey_jarvis"]
    script = [("What time is it?", "hey_jarvis"), ("Turn off the shelf lamp.", "hey_jarvis"),
              ("Set the main room lights to a nice colour temperature.", "hey_jarvis")]
    for text, wake in script:
        r = await box.converse(synth_user_audio(text, USER)[0], wake)
        print(f"\n> {text}\n  heard {r['heard']!r}\n  said  {r['said']!r}\n  {r['audio_secs']:.1f} s of audio, first "
              f"audio {r['first_audio_ms']} ms after speech ended, follow-up {r['follow_up']}\n  {' '.join(r['order'])}")
        ok &= bool(r["said"]) and r["audio_secs"] > 0.5 and r["order"][0] == "RUN_START" and r["order"][-1] == "RUN_END"
        if r["follow_up"]:                                  # the box listens again without a wake word
            r2 = await box.converse(synth_user_audio("Warm, please.", USER)[0], None)
            print(f"  > Warm, please.\n    heard {r2['heard']!r}\n    said  {r2['said']!r}\n    {r2['audio_secs']:.1f} s "
                  f"of audio, first audio {r2['first_audio_ms']} ms")
            ok &= bool(r2["said"]) and r2["audio_secs"] > 0.5

    r = await box.converse(np.zeros(16000 * 3, np.int16), "hey_jarvis")      # woken, then nothing said
    print(f"\n> (silence)\n  {' '.join(r['order'])}")
    ok &= "ERROR" in r["order"] and r["order"][-1] == "RUN_END"

    await satellite.stop()
    server.close()
    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
