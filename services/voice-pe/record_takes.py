"""Record wake word training data through a Voice PE box, as its wake word model hears it.

The box's second microphone channel is the one micro_wake_word listens on; this keeps it at the firmware's
gain_factor 4, so a take is what the model got. Winston must be down: the box streams to one voice client.
For the session the box is button only, so saying the wake word does not end the recording, and its wake
words are put back at the end. Press the box's button once to start, Ctrl-C to finish.

    uv run python services/voice-pe/record_takes.py <box address> <out dir>          # takes of the wake word
    uv run python services/voice-pe/record_takes.py <box address> <out dir> --room   # room audio, negatives
    uv run python services/voice-pe/record_takes.py --split <out dir>/session.wav    # cut a session again

Takes are cut at each pause (take_NNN.wav, 16 kHz mono); --room cuts the session into 3 s pieces. The whole
session stays as session.wav. See docs/voice-pe.md, "Okay Winston at a distance".
"""

import argparse
import asyncio
from pathlib import Path

import numpy as np
import soundfile as sf
from aioesphomeapi import APIClient, VoiceAssistantEventType as Ev
from pipecat.audio.vad.silero import SileroVADAnalyzer

RATE = 16000
GAIN = 4            # micro_wake_word's gain_factor in the factory firmware
FRAME = 512         # Silero's window at 16 kHz, 32 ms
PAUSE_SECS = 0.6    # a pause this long ends a take
PAD_SECS = 0.25
TAKE_SECS = (0.4, 2.5)
PIECE_SECS = 3.0


def runs(speech: list[bool], gap: int) -> list[tuple[int, int]]:
    """Frame spans of speech, joined over pauses shorter than gap frames.

    >>> runs([0, 1, 1, 0, 1, 0, 0, 0, 1], gap=3)
    [(1, 5), (8, 9)]
    """
    out, start, quiet = [], None, 0
    for n, s in enumerate(list(speech) + [False] * gap):
        if s:
            start = n if start is None else start
            quiet = 0
        elif start is not None and (quiet := quiet + 1) >= gap:
            out.append((start, n - quiet + 1))
            start = None
    return out


def takes(audio: np.ndarray) -> list[np.ndarray]:
    """Cut at pauses: each run of speech TAKE_SECS long, PAD_SECS padded."""
    vad = SileroVADAnalyzer()
    vad.set_sample_rate(RATE)
    speech = [vad.voice_confidence(audio[i:i + FRAME].tobytes()) > 0.5
              for i in range(0, len(audio) - FRAME, FRAME)]
    pad, lo, hi = int(PAD_SECS * RATE), TAKE_SECS[0] * RATE / FRAME, TAKE_SECS[1] * RATE / FRAME
    return [audio[max(0, a * FRAME - pad):b * FRAME + pad]
            for a, b in runs(speech, int(PAUSE_SECS * RATE / FRAME)) if lo <= b - a <= hi]


def pieces(audio: np.ndarray) -> list[np.ndarray]:
    n = int(PIECE_SECS * RATE)
    return [audio[i:i + n] for i in range(0, len(audio) - n + 1, n)]


def split(session: Path, room: bool):
    audio, rate = sf.read(session, dtype="int16")
    assert rate == RATE, rate
    out = session.parent
    for old in out.glob("take_*.wav"):
        old.unlink()
    cut = pieces(audio) if room else takes(audio)
    for i, t in enumerate(cut):
        sf.write(out / f"take_{i:03d}.wav", t, RATE, subtype="PCM_16")
    print(f"{len(cut)} {'pieces' if room else 'takes'} from {len(audio) / RATE:.0f} s in {out}")


async def record(host: str, out: Path):
    out.mkdir(parents=True, exist_ok=True)
    chunks: list[np.ndarray] = []
    client = APIClient(host, 6053, None, client_info="Winston takes")
    await client.connect(login=True)

    async def start(_conversation: str, _flags: int, _settings, _wake: str | None) -> int:
        print("recording, Ctrl-C to finish")
        client.send_voice_assistant_event(Ev.VOICE_ASSISTANT_RUN_START, None)
        client.send_voice_assistant_event(Ev.VOICE_ASSISTANT_STT_START, None)
        return 0                                    # audio over the API connection

    async def stop(abort: bool):
        print(f"the box {'aborted' if abort else 'ended'} the run; press the button to go on")

    async def audio(data: bytes, data2: bytes | None):
        if data2:
            x = np.frombuffer(data2, dtype="<i2").astype(np.int32) * GAIN
            chunks.append(np.clip(x, -32768, 32767).astype(np.int16))

    unsub = client.subscribe_voice_assistant(handle_start=start, handle_stop=stop, handle_audio=audio)
    conf = await client.get_voice_assistant_configuration(5)
    saved = list(conf.active_wake_words)
    await client.set_voice_assistant_configuration([])
    print(f"connected; the box is button only until this ends (was {saved}). Press its button once.")
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        pass
    finally:
        client.send_voice_assistant_event(Ev.VOICE_ASSISTANT_RUN_END, None)
        await client.set_voice_assistant_configuration(saved)
        unsub()
        await client.disconnect()
        print(f"wake words back to {saved}")
    if chunks:
        sf.write(out / "session.wav", np.concatenate(chunks), RATE, subtype="PCM_16")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("host", nargs="?")
    p.add_argument("out", nargs="?", type=Path)
    p.add_argument("--room", action="store_true", help="room audio for negatives: 3 s pieces, not takes")
    p.add_argument("--split", type=Path, help="cut an existing session.wav again, no recording")
    a = p.parse_args()
    if a.split:
        return split(a.split, a.room)
    if not (a.host and a.out):
        p.error("give the box's address and an output folder")
    if (a.out / "session.wav").exists():
        p.error(f"{a.out} already holds a session; give each session its own folder")
    try:
        asyncio.run(record(a.host, a.out))
    except KeyboardInterrupt:
        pass
    if (a.out / "session.wav").is_file():
        split(a.out / "session.wav", a.room)


if __name__ == "__main__":
    main()
