"""The satellite channel against a fake Voice Preview Edition: a small server speaking the ESPHome
native API (plaintext framing) the way the box does. The real channel connects to it, subscribes
to the voice assistant and runs the brain, STT and TTS; the fake wakes up, streams a synthetic
request as 16 kHz microphone audio until Winston sends STT_VAD_END, fetches the reply URL when told
to and follows up when continue_conversation is set. Needs the LLM (:8000) and the TTS (:8880).

    .venv/bin/python tests/satellite_fake.py

Actions are stubbed as in tests/conversations.py. Passes when every request gets a spoken reply
fetched from the URL, the events come in the box's order, every reply keeps the box listening
without a wake word (channels.satellite.follow_up_secs), an answer there is taken as the next turn
and silence there ends the run with the no-speech error, which takes the real box back to idle. A
request with a pause to think in it (1.5 s after "because") is heard whole.
Then: "that's all" there ends the conversation without a reply; a nudge (speaking up) is played as an
announcement with the chime first and his own wording after it, and the box listens on, so an answer
needs no wake word; a double press switches the box to button only (no wake word, a spoken line, a press
gets a reply without a follow-up); a hold to talk (no silence detection) with a 3 s pause in it ends only
when the button is let go and is heard whole; a reconnect finds it still button only, and a second
double press switches the wake word back on. Every turn is kept in a turn record in a temporary folder,
with its audio, how it started (wake word, follow-up, button, hold) and how it ended.
Barge-in, as with Winston's firmware (the mic streams on through the reply, its second channel 30 dB
quieter): "What time is it?" said 4 s into a long reply stops it within about half a second (a media
STOP), the box is told to listen on and not that the reply is over, and the next run hears the question
whole and answers it."""
import asyncio
import json
import sys
import tempfile
import time
import uuid
from pathlib import Path

import aiohttp
import numpy as np
from aioesphomeapi import VoiceAssistantEventType as Ev
from aioesphomeapi import VoiceAssistantFeature as Feature
from aioesphomeapi import api_pb2 as pb
from aioesphomeapi.core import MESSAGE_TYPE_TO_PROTO

sys.path.insert(0, str(Path(__file__).parent))
from conversations import ACT_STUBS, stub  # noqa: E402


def second_channel(piece: np.ndarray) -> bytes:
    """The Voice PE's second microphone channel: the same voice, 30 dB quieter (barge_in_gain_db)."""
    return (piece * 10 ** (-30 / 20)).astype(np.int16).tobytes()
from m2_headless_roundtrip import synth_user_audio  # noqa: E402
from winston.channels import satellite as sat  # noqa: E402
from winston.config import load  # noqa: E402
from winston.config.schema import SatelliteDevice  # noqa: E402
from winston.core import brain as brain_mod  # noqa: E402
from winston.core.hints import Hearing  # noqa: E402
from winston.core.turns import TurnLog  # noqa: E402

from loguru import logger  # noqa: E402

logger.remove(); logger.add(sys.stderr, level="INFO")
PORT, HTTP_PORT = 16053, 18702
BUTTON_KEY, CENTER_KEY, MEDIA_KEY = 4242, 4243, 4244
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
        self.states = False                                 # states subscribed: button events go out
        self.announced: list[dict] = []
        self.stopped = asyncio.Event()                      # the reply was stopped (a media STOP)

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
            self.send(pb.ListEntitiesEventResponse(object_id="button_press", key=BUTTON_KEY, name="Button press",
                                                   event_types=["double_press", "triple_press", "long_press"]))
            self.send(pb.ListEntitiesBinarySensorResponse(object_id="center_button", key=CENTER_KEY,
                                                          name="Center button"))
            self.send(pb.ListEntitiesMediaPlayerResponse(object_id="media_player", key=MEDIA_KEY,
                                                         name="Media Player", supports_pause=True))
            self.send(pb.ListEntitiesDoneResponse())
        elif isinstance(msg, pb.SubscribeStatesRequest):
            self.states = True
        elif isinstance(msg, pb.VoiceAssistantAnnounceRequest):
            asyncio.get_running_loop().create_task(self.announce(msg.media_id, msg.text, msg.start_conversation))
        elif isinstance(msg, pb.MediaPlayerCommandRequest):
            # A reply's stop is an announcement's; music the brain chose to play is stopped without it.
            if msg.has_command and msg.command == pb.MEDIA_PLAYER_COMMAND_STOP and msg.announcement:
                self.stopped.set()
        elif isinstance(msg, pb.PingRequest):
            self.send(pb.PingResponse())
        elif isinstance(msg, pb.DisconnectRequest):
            self.send(pb.DisconnectResponse())
        elif isinstance(msg, pb.SubscribeVoiceAssistantRequest):
            (self.subscribed.set if msg.subscribe else self.subscribed.clear)()
        elif isinstance(msg, pb.VoiceAssistantConfigurationRequest):
            # Like ESPHome: only the subscribed voice client gets the list, and a Voice PE runs one at a time
            if not self.subscribed.is_set():
                self.send(pb.VoiceAssistantConfigurationResponse())
                return
            words = [pb.VoiceAssistantWakeWord(id=i, wake_word=w, trained_languages=["en"]) for i, w in
                     (("okay_nabu", "Okay Nabu"), ("hey_jarvis", "Hey Jarvis"), ("hey_mycroft", "Hey Mycroft"))]
            self.send(pb.VoiceAssistantConfigurationResponse(available_wake_words=words,
                                                             active_wake_words=self.active_wake_words,
                                                             max_active_wake_words=1))
        elif isinstance(msg, pb.VoiceAssistantSetConfiguration):
            if self.subscribed.is_set():
                self.active_wake_words = list(msg.active_wake_words)
        elif isinstance(msg, pb.VoiceAssistantResponse):
            self.response.set()
        elif isinstance(msg, pb.VoiceAssistantEventResponse):
            self.events.append((time.perf_counter(), Ev(msg.event_type), {d.name: d.value for d in msg.data}))
            self.new_event.set()

    async def announce(self, url: str, text: str, start_conversation: bool = False):
        """Plays an announcement as the box does: fetches the URL, then says it has finished."""
        async with aiohttp.ClientSession() as s, s.get(url.replace(".flac", ".wav")) as r:
            wav = await r.read()
        pcm = np.frombuffer(wav[44:len(wav) - (len(wav) - 44) % 2], dtype="<i2")
        self.announced.append({"text": text, "audio_secs": len(pcm) / sat.OUT_RATE, "pcm": pcm,
                               "start_conversation": start_conversation})
        self.send(pb.VoiceAssistantAnnounceFinished(success=True))

    def double_press(self):
        self.send(pb.EventResponse(key=BUTTON_KEY, event_type="double_press"))

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

    async def converse(self, audio: np.ndarray, wake_word: str | None, on_audio=None, hold: bool = False) -> dict:
        """One run as the box does it: request start, stream the mic until STT_VAD_END, play the reply.
        on_audio gets the reply's WAV bytes as they arrive (tests/rooms_voice.py plays them into a room).
        hold: a hold to talk, started without silence detection and let go when the audio has been said."""
        since = len(self.events)
        self.response.clear()
        self.send(pb.VoiceAssistantRequest(start=True, conversation_id="", flags=0 if hold else 1,   # 1: USE_VAD
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
                if hold:
                    self.send(pb.BinarySensorStateResponse(key=CENTER_KEY, state=False))
            self.send(pb.VoiceAssistantAudio(data=piece.tobytes(), data2=second_channel(piece)))
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
            # the WAV variant of the reply the box gets as FLAC, so the bytes can be counted and played
            async with aiohttp.ClientSession() as s, s.get(run_start["url"].replace(".flac", ".wav")) as r:
                async for part in r.content.iter_any():
                    if t_first is None and len(wav) + len(part) > 44:
                        t_first = time.perf_counter()
                    wav += part
                    if on_audio:
                        on_audio(part)
        end = await self.wait_event(Ev.VOICE_ASSISTANT_INTENT_END, since) or {}
        spoken = await self.wait_event(Ev.VOICE_ASSISTANT_TTS_START, since) or {}
        await self.wait_event(Ev.VOICE_ASSISTANT_RUN_END, since)
        return {"heard": (heard or {}).get("text"), "said": spoken.get("text"),
                "follow_up": end.get("continue_conversation") == "1",
                "audio_secs": max(0, len(wav) - 44) / 2 / sat.OUT_RATE,
                "first_audio_ms": round((t_first - t_end) * 1000) if t_first and t_end else None,
                "order": [e.name.removeprefix("VOICE_ASSISTANT_") for _, e, _ in self.events[since:]]}

    async def talk_over(self, request: np.ndarray, over: np.ndarray, after: float) -> dict:
        """Winston's firmware: a run whose microphone streams on after the request, through his thinking and
        his reply, in real time. over is said after s into the reply as played. When the reply is stopped,
        the box ends it and, told to listen on, starts the next run without a wake word, holding the mic's
        audio meanwhile as its ring buffer does, then plays that run's reply."""
        chunk, pending, held = 512, np.concatenate([np.zeros(4000, np.int16), request]), []
        paused = False

        async def mic():
            nonlocal pending
            while True:
                piece = pending[:chunk] if len(pending) else np.zeros(chunk, np.int16)
                pending = pending[chunk:]
                if len(piece) < chunk:
                    piece = np.concatenate([piece, np.zeros(chunk - len(piece), np.int16)])
                if paused:
                    held.append(piece)
                else:
                    self.send(pb.VoiceAssistantAudio(data=piece.tobytes(), data2=second_channel(piece)))
                await asyncio.sleep(chunk / 16000)

        async def play(url: str, first: asyncio.Event):
            async with aiohttp.ClientSession() as s, s.get(url.replace(".flac", ".wav")) as r:
                async for part in r.content.iter_any():
                    first.set()

        since = len(self.events)
        self.response.clear(); self.stopped.clear()
        self.send(pb.VoiceAssistantRequest(start=True, conversation_id="", flags=1, wake_word_phrase="hey_jarvis",
                                           audio_settings=pb.VoiceAssistantAudioSettings()))
        await asyncio.wait_for(self.response.wait(), 10)
        streaming = asyncio.create_task(mic())
        try:
            await self.wait_event(Ev.VOICE_ASSISTANT_INTENT_PROGRESS, since)
            url = next(d for _, e, d in self.events[since:] if e == Ev.VOICE_ASSISTANT_RUN_START)["url"]
            first = asyncio.Event()
            playing = asyncio.create_task(play(url, first))
            await asyncio.wait_for(first.wait(), 30)
            await asyncio.sleep(after)
            lead = int(np.argmax(np.abs(over) > 1000))      # where the voice starts in the clip
            pending = over
            t_voice = time.perf_counter() + lead / 16000
            try:
                await asyncio.wait_for(self.stopped.wait(), 5)
                stop_ms = round((time.perf_counter() - t_voice) * 1000)
            except TimeoutError:
                stop_ms = None
            playing.cancel()
            first_run = [(e, d) for _, e, d in self.events[since:]]
            listen_on = any(e == Ev.VOICE_ASSISTANT_INTENT_END and d.get("continue_conversation") == "1"
                            for e, d in first_run)
            if stop_ms is None or not listen_on:
                return {"stop_ms": stop_ms, "listen_on": listen_on, "first_run": first_run}
            paused = True                                   # the box ends the reply and starts the next run
            await asyncio.sleep(0.05)
            since2 = len(self.events)
            self.response.clear()
            self.send(pb.VoiceAssistantRequest(start=True, conversation_id="", flags=1, wake_word_phrase="",
                                               audio_settings=pb.VoiceAssistantAudioSettings()))
            await asyncio.wait_for(self.response.wait(), 10)
            for piece in held:
                self.send(pb.VoiceAssistantAudio(data=piece.tobytes(), data2=second_channel(piece)))
            paused = False
            heard = await self.wait_event(Ev.VOICE_ASSISTANT_STT_END, since2)
            if await self.wait_event(Ev.VOICE_ASSISTANT_INTENT_PROGRESS, since2):
                await play(next(d for _, e, d in self.events[since2:] if e == Ev.VOICE_ASSISTANT_RUN_START)["url"],
                           asyncio.Event())
            spoken = await self.wait_event(Ev.VOICE_ASSISTANT_TTS_START, since2) or {}
            return {"stop_ms": stop_ms, "listen_on": listen_on, "first_run": first_run,
                    "heard": (heard or {}).get("text"), "said": spoken.get("text")}
        finally:
            streaming.cancel()


async def main() -> int:
    cfg = load()
    room = f"test room {uuid.uuid4().hex[:6]}"          # its own conversation, not a leftover of the last run
    cfg.channels.satellite.devices = {room: SatelliteDevice(host="127.0.0.1", port=PORT, wake_word="hey_jarvis")}
    cfg.channels.satellite.http_port, cfg.channels.satellite.public_host = HTTP_PORT, "127.0.0.1"
    for f in ("barge_in_secs", "barge_in_volume", "barge_in_gain_db", "barge_in_grace_secs"):   # even if off here
        setattr(cfg.channels.satellite, f, type(cfg.channels.satellite).model_fields[f].default)
    turns_dir = Path(tempfile.mkdtemp(prefix="winston-turns-"))      # never the real data/turns
    cfg.logging.turns.enabled, cfg.logging.turns.dir = True, str(turns_dir)
    cfg.logging.latency = str(turns_dir / "latency.jsonl")             # never the real data/logs
    cfg.logging.tool_calls = str(turns_dir / "tool_calls.jsonl")       # nor its tool-call log
    turns = TurnLog(cfg, "satellite")

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
    box.active_wake_words = ["okay_nabu"]                  # the stock box's choice before Winston sets his
    server = await asyncio.start_server(box.serve, "127.0.0.1", PORT)
    audio = sat.AudioServer(cfg); await audio.start()
    stt = Hearing.load(cfg)
    satellite = sat.Satellite(cfg, room, cfg.channels.satellite.devices[room], audio, stt, NoFeed(), turns)
    await satellite.connect()
    await asyncio.wait_for(box.subscribed.wait(), 15)
    for _ in range(50):                  # the wake word is set just after subscribing
        if box.active_wake_words == ["hey_jarvis"]:
            break
        await asyncio.sleep(0.1)
    print(f"subscribed; wake words set to {box.active_wake_words}")

    ok = box.active_wake_words == ["hey_jarvis"]
    script = [("What time is it?", "hey_jarvis"), ("Turn off the shelf lamp.", "hey_jarvis"),
              ("Set the main room lights to a nice colour temperature.", "hey_jarvis")]
    for text, wake in script:
        r = await box.converse(synth_user_audio(text, USER)[0], wake)
        print(f"\n> {text}\n  heard {r['heard']!r}\n  said  {r['said']!r}\n  {r['audio_secs']:.1f} s of audio, first "
              f"audio {r['first_audio_ms']} ms after speech ended, follow-up {r['follow_up']}\n  {' '.join(r['order'])}")
        ok &= bool(r["said"]) and r["audio_secs"] > 0.5 and r["order"][0] == "RUN_START" and r["order"][-1] == "RUN_END"
        ok &= r["follow_up"]                                # every reply keeps the box listening
        if text == script[0][0]:                            # nothing more to say: closes quietly
            t0 = time.perf_counter()
            r2 = await box.converse(np.zeros(16000 * 12, np.int16), None)
            print(f"  > (silence after the reply)\n    {' '.join(r2['order'])} after {time.perf_counter() - t0:.1f} s")
            ok &= r2["order"][-2:] == ["ERROR", "RUN_END"] and not r2["said"]   # the error takes the box to idle
        elif r["said"].rstrip().endswith("?"):              # a question: the answer needs no wake word
            r2 = await box.converse(synth_user_audio("Warm, please.", USER)[0], None)
            print(f"  > Warm, please.\n    heard {r2['heard']!r}\n    said  {r2['said']!r}\n    {r2['audio_secs']:.1f} s "
                  f"of audio, first audio {r2['first_audio_ms']} ms")
            ok &= bool(r2["said"]) and r2["audio_secs"] > 0.5

    satellite._follow_until = 0.0                          # long after the last reply: a real wake, not a follow-up
    r = await box.converse(np.zeros(16000 * 3, np.int16), "hey_jarvis")      # woken, then nothing said
    print(f"\n> (silence)\n  {' '.join(r['order'])}")
    ok &= "ERROR" in r["order"] and r["order"][-1] == "RUN_END"

    r = await box.converse(synth_user_audio("What time is it?", USER)[0], "hey_jarvis")
    # Not "thanks": some of the TTS's renditions of it are heard as "things".
    r2 = await box.converse(synth_user_audio("That's all, thank you.", USER)[0], None)   # in the follow-up window
    print(f"\n> That's all, thank you. (after {r['said']!r})\n  heard {r2['heard']!r}, said {r2['said']!r}\n"
          f"  {' '.join(r2['order'])}")
    ok &= r["follow_up"] and not r2["said"] and r2["order"][-2:] == ["ERROR", "RUN_END"]

    # A pause to think mid-sentence, longer than end_silence_secs: Smart Turn calls it unfinished, so the box
    # waits for the rest instead of answering half a request.
    satellite._follow_until = 0.0
    first, rest = "Can you tell me what to have in my living room because", "I want it to feel cozy."
    pause = np.zeros(int(16000 * 1.5), np.int16)
    r = await box.converse(np.concatenate([synth_user_audio(first, USER)[0], pause, synth_user_audio(rest, USER)[0]]),
                           "hey_jarvis")
    print(f"\n> {first} (1.5 s pause) {rest}\n  heard {r['heard']!r}\n  said  {r['said']!r}")
    ok &= "cozy" in (r["heard"] or "").lower() and bool(r["said"])

    # Speaking up: a nudge from an extension, as winstond's hub hands it to the box.
    chime = audio.chime()
    ev = {"type": "nudge", "id": "ntest", "extension": "Trainer", "kind": "Time to train",
          "say": "It has been three days since your last workout, sir.", "facts": {"days_since": 3},
          "outside": True, "reason": None, "actions": [], "urgent": False, "level": 1, "again": False}
    n = len(box.announced)
    satellite._follow_until = 0.0
    t0 = time.perf_counter()
    await satellite.speak(ev)
    said = box.announced[n] if len(box.announced) > n else {}
    chime_secs = len(chime) / 2 / sat.OUT_RATE
    starts_with_chime = bool(chime) and len(said.get("pcm", [])) > len(chime) // 2 and np.array_equal(
        said["pcm"][:len(chime) // 2], np.frombuffer(chime, dtype="<i2"))
    worded = satellite.conversation.speaker.text if satellite.conversation else []
    print(f"\n> (a nudge: {ev['say']!r})\n  {said.get('audio_secs', 0):.1f} s of audio in {time.perf_counter() - t0:.1f} s, "
          f"the chime first {starts_with_chime}, then listening {said.get('start_conversation')}")
    ok &= starts_with_chime and said.get("audio_secs", 0) > chime_secs + 1 and said.get("start_conversation")
    r = await box.converse(synth_user_audio("Thanks, I know.", USER)[0], None)       # no wake word
    print(f"  > Thanks, I know. (no wake word)\n    heard {r['heard']!r}\n    said  {r['said']!r}")
    ok &= bool(r["heard"]) and bool(r["said"])

    # Barge-in: talked over 4 s into a long reply (after barge_in_grace_secs). Stopped within about half a second,
    # the box told to listen on and never that the reply is over, and the question carried whole into the next run.
    satellite._follow_until = 0.0
    r = await box.talk_over(synth_user_audio("Tell me a bedtime story about a lighthouse, in four sentences.", USER)[0],
                            synth_user_audio("What time is it?", USER)[0], after=4.0)
    order = [e.name.removeprefix("VOICE_ASSISTANT_") for e, _ in r["first_run"]]
    print(f"\n> (a long reply, talked over after 4 s: What time is it?)\n  stopped {r['stop_ms']} ms after the "
          f"voice began, listen on {r['listen_on']}\n  {' '.join(order)}\n  then heard {r.get('heard')!r}\n"
          f"  said  {r.get('said')!r}")
    ok &= r["stop_ms"] is not None and r["stop_ms"] < 700 and r["listen_on"]
    ok &= "TTS_END" not in order and "RUN_END" not in order
    ok &= "what time" in (r.get("heard") or "").lower() and bool(r.get("said"))

    async def pressed(want: list[str], line: str) -> bool:
        n = len(box.announced)
        box.double_press()
        for _ in range(300):
            if len(box.announced) > n:
                break
            await asyncio.sleep(0.1)
        said = box.announced[n] if len(box.announced) > n else {}
        print(f"\n> (double press)\n  wake words {box.active_wake_words}, said {said.get('text')!r}, "
              f"{said.get('audio_secs', 0):.1f} s of audio")
        return box.active_wake_words == want and said.get("text") == line and said.get("audio_secs", 0) > 0.5

    ok &= await pressed([], sat.BUTTON_ONLY_LINE)
    satellite._follow_until = 0.0
    r = await box.converse(synth_user_audio("What time is it?", USER)[0], None)            # the button
    print(f"\n> What time is it? (button)\n  said {r['said']!r}, follow-up {r['follow_up']}")
    ok &= bool(r["said"]) and not r["follow_up"]

    # Hold to talk: a pause longer than any end of speech, and the request still ends only when let go.
    first, rest = "What time is it", "in London?"
    r = await box.converse(np.concatenate([synth_user_audio(first, USER)[0], np.zeros(16000 * 3, np.int16),
                                           synth_user_audio(rest, USER)[0]]), None, hold=True)
    print(f"\n> {first} (3 s pause) {rest} (hold to talk)\n  heard {r['heard']!r}\n  said  {r['said']!r}")
    ok &= "london" in (r["heard"] or "").lower() and bool(r["said"])

    await satellite.stop()
    satellite = sat.Satellite(cfg, room, cfg.channels.satellite.devices[room], audio, stt, NoFeed())
    await satellite.connect()
    for _ in range(50):
        if satellite._button is not None:
            break
        await asyncio.sleep(0.1)
    print(f"\n> (reconnect)\n  button only {satellite.button_only}, wake words {box.active_wake_words}")
    ok &= satellite.button_only and box.active_wake_words == []
    ok &= await pressed(["hey_jarvis"], sat.WAKE_WORD_LINE)

    await satellite.stop()
    server.close()
    ok &= recorded(turns_dir)
    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1



def recorded(folder: Path) -> bool:
    """The turn record: one line per request that had speech in it, each with its audio and timings,
    started and ended the way the run did."""
    lines = [json.loads(x) for f in sorted(folder.glob("*/satellite.jsonl")) for x in f.read_text().splitlines()]
    print(f"\n> (turn record, {len(lines)} turns in {folder})")
    for t in lines:
        print(f"  {t['start']:<9} {t['outcome']:<13} {t['secs']:>4.1f} s  {t['heard']!r} -> {t['said'][:50]!r}"
              f"  tools {[c['name'] for c in t['tools']]}  stt {t['stt_ms']} ms, first audio {t['first_audio_ms']} ms")

    def has(words: str, start: str, outcome: str) -> bool:
        return any(words in t["heard"].lower() and t["start"] == start and t["outcome"] == outcome for t in lines)
    audio_ok = bool(lines) and all(t.get("audio") and next(folder.glob(f"*/{t['audio']}"), None) for t in lines)
    ok = audio_ok and has("what time", "wake", "answered") and has("that's all", "follow_up", "dismissed") \
        and has("thanks", "follow_up", "answered")         # "Thanks, I know." after the nudge; "I know" is often misheard
    if any("warm" in t["heard"].lower() for t in lines):    # asked only when he answered with a question
        ok &= has("warm", "follow_up", "answered")
    lamp = next((t for t in lines if "shelf lamp" in t["heard"].lower()), {})
    ok &= any(c.get("ok") for c in lamp.get("tools", []))                     # the stubbed action, with its result
    ok &= any(t["start"] == "button" and t["said"] for t in lines)
    ok &= has("london", "hold", "answered")
    ok &= has("lighthouse", "wake", "interrupted") and has("what time", "barge_in", "answered")
    ok &= all(t["first_audio_ms"] for t in lines if t["outcome"] == "answered" and t["said"])
    print(f"  {'ok' if ok else 'NOT as expected'}")
    return ok


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
