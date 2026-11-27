"""Okay Winston from your own voice: record takes through the box, train on them, flash the box.

    uv run python services/voice-pe/wake.py takes    # say Okay Winston around the room, as often as you like
    uv run python services/voice-pe/wake.py room     # the room's own sounds, so it learns what not to wake on
    uv run python services/voice-pe/wake.py train    # about an hour on the GPU; Winston is paused meanwhile
    uv run python services/voice-pe/wake.py flash    # the box gets the new model over the network

The box, its key and Winston's address come from data/config.yaml; with several boxes, --box names the room.
Takes are recorded from the box's second microphone channel, the one micro_wake_word listens on, at the
firmware's gain_factor 4, so a take is what the model hears. Each session gets its own folder under
data/wake/, and train uses all of them. See docs/voice-pe.md, "Okay Winston at a distance".
"""

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import httpx
import numpy as np
import soundfile as sf
from aioesphomeapi import APIClient, VoiceAssistantEventType as Ev
from pipecat.audio.vad.silero import SileroVADAnalyzer

from winston.config import data_dir, load

HERE = Path(__file__).parent
WAKE = data_dir() / "wake"      # takes/, room/ and the trained model: your voice, never the repo
WORD, PHRASE = "okay_winston", "okay winston"
RATE = 16000
GAIN = 4            # micro_wake_word's gain_factor in the factory firmware
FRAME = 512         # Silero's window at 16 kHz, 32 ms
PAUSE_SECS = 0.6    # a pause this long ends a take
PAD_SECS = 0.25
TAKE_SECS = (0.4, 2.5)
PIECE_SECS = 3.0

TRAINER = "ghcr.io/tatertotterson/microwakeword:v47.3"
TRAINER_DIR = Path.home() / ".cache" / "winston-wake-trainer"    # its environment, datasets and runs: many GB
TRAINER_URL = "http://127.0.0.1:8789"
CONTAINER = "winston-wake-trainer"
NEAR_MISSES = ["okay", "winston", "hey winston", "okay wilson", "okay listen", "okay, one moment", "winston churchill"]
# ESPHome refuses a manifest with keys it does not know
MANIFEST = {"type", "wake_word", "author", "website", "trained_languages", "version"}
MICRO = {"probability_cutoff", "sliding_window_size", "feature_step_size", "tensor_arena_size",
         "minimum_esphome_version"}

GUIDE = {
    "takes": 'Press the box\'s button once, then say "Okay Winston" with two seconds of quiet after each. Move '
             "around: the sofa, the kitchen, the doorway, at 1, 3 and 5 m; normal, tired, mumbled, facing away. "
             "50 to 100 in all, over as many sessions as you like. Ctrl-C ends this one.",
    "room": "Press the box's button once and let it hear the room: TV, music, people talking, \"okay\" and "
            '"Winston" said mid-sentence. Never "Okay Winston" itself: all of it teaches the model what not to '
            "wake on. Ctrl-C ends it.",
}


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


def box(room: str | None):
    """The box's address, port and encryption key, from the config."""
    devices = load().channels.satellite.devices
    if not devices:
        sys.exit("no box in data/config.yaml (channels.satellite.devices)")
    if room is None and len(devices) > 1:
        sys.exit(f"several boxes: name one with --box ({', '.join(devices)})")
    if (room := room or next(iter(devices))) not in devices:
        sys.exit(f"no box in {room!r}; the rooms are {', '.join(devices)}")
    dev = devices[room]
    return dev.host, dev.port, (os.environ.get(dev.key_env, "").strip() if dev.key_env else "") or None


@contextmanager
def paused(channel: str | None = None):
    """Winston's channel, or all his engines and channels, stopped meanwhile and started again after."""
    url = load().control.url
    try:
        up = [e["name"] for e in httpx.get(f"{url}/api/status", timeout=10).json()["engines"]
              if e["state"] != "stopped" and channel in (None, e["name"])]
    except httpx.HTTPError:
        up = []                                                                 # winstond is not running
    stop, start = ((f"/api/engines/{channel}/stop", f"/api/engines/{channel}/start") if channel else
                   ("/api/engines/stop_all", "/api/engines/start_all"))
    if up:
        httpx.post(url + stop, timeout=120).raise_for_status()
        print(f"Winston: {', '.join(up)} stopped meanwhile")
    try:
        yield
    finally:
        if up:
            httpx.post(url + start, timeout=30)
            print(f"Winston: {', '.join(up)} started again")


async def record(host: str, port: int, key: str | None, out: Path):
    out.mkdir(parents=True, exist_ok=True)
    chunks: list[np.ndarray] = []
    client = APIClient(host, port, None, noise_psk=key, client_info="Winston takes")
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


def session(kind: str, room: str | None):
    """Record one session of takes or room audio into a folder of its own."""
    out = WAKE / kind / time.strftime("%Y-%m-%d-%H%M%S")
    host, port, key = box(room)
    print(GUIDE[kind])
    with paused("satellite"):                       # the box streams to one voice client
        try:
            asyncio.run(record(host, port, key, out))
        except KeyboardInterrupt:
            pass
    if (out / "session.wav").is_file():
        split(out / "session.wav", kind == "room")
    n = len(list((WAKE / kind).glob("*/take_*.wav")))
    print(f"{n} {'takes' if kind == 'takes' else 'room pieces'} in all in {WAKE / kind}")


def stage(kind: str, dest: Path) -> int:
    """Every session's pieces of a kind into the trainer's folder, named by their session."""
    dest.mkdir(parents=True, exist_ok=True)
    for old in dest.glob("*.wav"):
        old.unlink()
    files = sorted((WAKE / kind).glob("*/take_*.wav"))
    for f in files:
        shutil.copy(f, dest / f"{f.parent.name}_{f.name}")
    return len(files)


def trainer(path: str, body: dict | None = None):
    """A call to the trainer's server: its JSON, or None when it fails."""
    try:
        r = httpx.request("GET" if body is None else "POST", TRAINER_URL + path, json=body, timeout=30)
        if r.is_success:
            return r.json()
        print(f"trainer {path}: {r.status_code} {r.text[:300]}")
    except (httpx.HTTPError, ValueError):
        pass
    return None


def finished(since: float) -> Path | None:
    """The newest model a run wrote after since."""
    models = [p for p in TRAINER_DIR.glob(f"output/*/{WORD}.tflite")
              if p.stat().st_mtime > since and p.with_suffix(".json").is_file()]
    return max(models, key=lambda p: p.stat().st_mtime, default=None)


def install(tflite: Path):
    """The model and its manifest, trimmed to ESPHome's fields, into data/wake/; the full report beside them."""
    report = tflite.with_suffix(".json")
    full = json.loads(report.read_text())
    if "micro" not in full:                         # the run's report; the server publishes ESPHome's own
        full = json.loads((TRAINER_DIR / "trained_wake_words" / f"{WORD}.esphome.json").read_text())
    manifest = {k: v for k, v in full.items() if k in MANIFEST} | {
        "model": f"{WORD}.tflite", "micro": {k: v for k, v in full["micro"].items() if k in MICRO}}
    WAKE.mkdir(parents=True, exist_ok=True)
    shutil.copy(tflite, WAKE / f"{WORD}.tflite")
    shutil.copy(report, WAKE / f"{WORD}_report.json")
    (WAKE / f"{WORD}.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"the new model is in {WAKE} (its own cutoff {manifest['micro']['probability_cutoff']}). Next: flash")


def train():
    n = stage("takes", TRAINER_DIR / "personal_samples")
    if not n:
        sys.exit(f"no takes in {WAKE / 'takes'}: record some first (takes)")
    print(f"{n} takes and {stage('room', TRAINER_DIR / 'negative_samples')} room pieces staged in {TRAINER_DIR}")
    print("The first run downloads the trainer's environment and datasets (many GB); later runs reuse them.")
    with paused():                                  # the trainer needs the GPU
        subprocess.run(["docker", "rm", "-f", CONTAINER], check=False, capture_output=True)
        subprocess.run(["docker", "run", "-d", "--name", CONTAINER, "--gpus", "all", "--shm-size=8g",
                        "-p", "127.0.0.1:8789:8789", "-v", f"{TRAINER_DIR}:/data", TRAINER], check=True)
        try:
            # the augmented takes are cached; new takes would not be picked up
            subprocess.run(["docker", "exec", CONTAINER, "rm", "-rf", "/data/work/personal_augmented_features",
                            "/data/work/reviewed_negative_features"], check=True)
            for _ in range(360):
                try:
                    httpx.get(TRAINER_URL, timeout=5)
                    break
                except httpx.HTTPError:
                    time.sleep(5)
            else:
                sys.exit(f"the trainer did not come up in 30 min: docker logs {CONTAINER}")
            since = time.time()
            trainer("/api/stop_session", {})        # one left from an earlier run keeps its phrase
            trainer("/api/start_session", {"phrase": PHRASE})
            if trainer("/api/train", {"train_microwakeword": True, "generated_negative_phrases": NEAR_MISSES}) is None:
                print(f"Start it by hand: open {TRAINER_URL} (from another computer: ssh -L 8789:127.0.0.1:8789 "
                      f"this one), start a session for \"{PHRASE}\" and press Train. The takes are in place.")
            print("training; this waits for the model, Ctrl-C stops it")
            while not (model := finished(since)):
                time.sleep(30)
                s = (trainer("/api/train_status") or {}).get("training") or {}
                if s.get("exit_code") is not None and not s.get("running") and not (model := finished(since)):
                    sys.exit(f"the trainer stopped (exit {s['exit_code']}) with no model: "
                             f"{TRAINER_DIR / 'recorder_training.log'}")
                if s.get("log_lines"):
                    print(f"  {(time.time() - since) / 60:3.0f} min  {str(s['log_lines'][-1])[:140]}")
            install(model)
        finally:
            subprocess.run(["docker", "rm", "-f", CONTAINER], check=False, capture_output=True)


def flash(room: str | None):
    host = box(room)[0]
    model = WAKE / f"{WORD}.json"
    mine = model.is_file()
    print(f"building with {'your model, ' + str(model) if mine else 'the repo model (no model of yours yet)'}; "
          "the first build takes 15 to 30 min, later ones a minute")
    subprocess.run(["uvx", "esphome@2026.11.1", *(["-s", "wake_model", str(model)] if mine else []),
                    "run", "voice-pe-winston.yaml", "--device", host, "--no-logs"], cwd=HERE, check=True)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    for kind in ("takes", "room"):
        sub.add_parser(kind, help=GUIDE[kind]).add_argument("--box", help="the box's room, with several")
    sub.add_parser("train", help="train on every session's takes and room pieces (Docker and the GPU)")
    sub.add_parser("flash", help="build the firmware with the new model and flash the box over the network"
                   ).add_argument("--box", help="the box's room, with several")
    s = sub.add_parser("split", help="cut a session.wav again")
    s.add_argument("session", type=Path)
    s.add_argument("--room", action="store_true", help="3 s pieces, as room audio")
    a = p.parse_args()
    if a.cmd in ("takes", "room"):
        session(a.cmd, a.box)
    elif a.cmd == "train":
        train()
    elif a.cmd == "flash":
        flash(a.box)
    else:
        split(a.session, a.room)


if __name__ == "__main__":
    main()
