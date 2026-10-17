# /// script
# [tool.winston]
# id = "spotify"
# name = "Spotify"
# description = "Spotify on your speakers: a Spotify Connect speaker for the app, and play, pause and skip by voice."
# icon = "music"
# api = 1
# permissions = { network = ["127.0.0.1", "api.spotify.com", "accounts.spotify.com", "github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com", "api.listenbrainz.org", "musicbrainz.org"], home_assistant = "act", speakers = true, presence = true }
# ///
"""Spotify on the speakers (the satellite box and whatever is plugged into its jack).

It runs go-librespot (github.com/devgianlu/go-librespot, pinned below and checked against its release
checksum), an open Spotify Connect receiver: the speaker shows up in the Spotify app under
`name`, and plays what you send it there or ask for by voice. It needs Spotify Premium, and signs in
once with a code at spotify.com/pair (shown on the card). Spotify gives such receivers its 320 kbps
stream; lossless stays in Spotify's own apps for now.

go-librespot writes the audio as fast as it is read, so this relay reads it at playback speed, two
seconds ahead, and serves it on 127.0.0.1 as a WAV stream for the speakers (`ctx.speakers`), which
turn it into 48 kHz FLAC for the box. Nothing else may run ahead: the pipe holds a tenth of a second
and the relay reads only what it sends (a 2 MB read buffer once let Spotify run 12 s ahead of the
box, so a pause took 4 s to be heard and lost the rest). When Spotify pauses or stops, the speakers
stop at once, and on a pause Spotify goes back by what the box had not played yet; when it plays,
they start again, and the relay waits for the box before it reads on.

Finding what to play: first your own library (playlists, saved albums, top and followed artists, top and
liked songs), read hourly from Home Assistant's Spotify player when you pick it; then all of Spotify,
with your own Spotify app's client ID and secret when you add them. A mix for a mood comes from
ListenBrainz's LB Radio (a free account's token): two of your artists that fit it and artists like them. Not with the receiver's session
token: Spotify's Web API answered every call with it with 429, too many requests, on 2026-09-30 (it is
the shared client of every librespot)."""
from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import os
import random
import re
import stat
import struct
import tarfile
import time
import unicodedata
from collections import deque
from typing import Literal

from winston_sdk import Entity, Extension, Field, Secret, Settings

ext = Extension()

VERSION = "0.10.4"
ARCHIVE = f"https://github.com/devgianlu/go-librespot/releases/download/v{VERSION}/go-librespot_linux_x86_64.tar.gz"
SHA256 = "fedd2efd64188800ea5cf8b49ecacfb88bb7379f3d56146c3241f5a3e3b92a3b"   # GitHub's digest, 2026-10-16
RATE, CHANNELS = 44100, 2
BPS = RATE * CHANNELS * 2           # s16le
LEAD = 1.5                          # seconds the relay reads ahead of playback: the box's buffer
PIPE = 16384                        # bytes the receiver may write ahead of the relay: a tenth of a second
CHUNK = 8192                        # bytes per read
BOX_WAIT = 5.0                      # seconds the relay holds the music for a box that is connecting


class SpotifySettings(Settings):
    name: str = Field("Winston", description="The speaker's name in the Spotify app.")
    library: str | None = Entity(domain="media_player", default=None, title="Your library from",
                                 description="Home Assistant's Spotify player: your playlists, albums, artists and "
                                             "songs by name, read hourly.")
    client_id: str = Secret("SPOTIFY_CLIENT_ID", title="Client ID variable", label="Client ID",
                            description="From your app at developer.spotify.com (the one Home Assistant's Spotify "
                                        "integration uses will do), with the secret below: search all of Spotify.")
    client_secret: str = Secret("SPOTIFY_CLIENT_SECRET", title="Client secret variable", label="Client secret")
    listenbrainz_token: str = Secret("LISTENBRAINZ_TOKEN", title="ListenBrainz token variable",
                                     label="ListenBrainz token",
                                     description="From listenbrainz.org/settings (a free account), with the app above: "
                                                 "a fresh mix for a mood, from your artists and ones like them.")
    names: list[str] = Field(default_factory=list, title="Your names for them",
                             description="What you call your playlists, albums or artists, one per line: the "
                                         "name, =, then your words for it (🥁 = drum and bass, drums). An emoji "
                                         "is already known by its own name (\"drum emoji\").")
    mix_playlist: str = Field("", level="advanced", title="Mixes go to",
                              description="A playlist of yours, by name, that mixes are added to and played from (make "
                                          "an empty one in Spotify, Winston, say). A mix is then one list: what you ask "
                                          "for next replaces it, and previous works. Empty: Spotify's queue, where a "
                                          "mix's songs follow whatever you ask for next.")
    not_now_hours: float = Field(24, ge=0, level="advanced", title="\"Not now\" lasts (hours)",
                                 description="\"Not Elliott Smith\": how long an artist stays out of what plays, "
                                             "unless the speakers have been quiet for the hours below first.")
    quiet_resets_hours: float = Field(1, ge=0, level="advanced", title="Quiet ends \"not now\" after (hours)",
                                      description="Speakers quiet this long end every \"not now\" (a new session).")
    sleep_fade_minutes: float = Field(30, ge=1, le=120, level="advanced", title="Sleep fade (minutes)",
                                      description="A sleep timer fades the music out over this long, then pauses it "
                                                  "without a word; a longer timer plays as it was first.")
    first_volume: int = Field(60, ge=0, le=100, level="advanced", title="First music of the day at",
                              description="The volume the first music plays at after the speakers have been quiet "
                                          "for the hours below (0: as it was left).")
    first_after_hours: float = Field(6, ge=0, level="advanced", title="Quiet before \"first music\" (hours)")
    listens: bool = Field(False, title="Send what plays to ListenBrainz",
                          description="Each song the speakers play for half its length or four minutes goes to your "
                                      "ListenBrainz profile (public; the token above), so its statistics and "
                                      "recommendations include it. Spotify itself never hears what this receiver plays.")
    pause_on_leave: bool = Field(False, title="Pause when you leave",
                                 description="The music pauses when you leave home (Home Assistant's person, under "
                                             "Speaking up).")
    mood_mixes: Literal["listenbrainz", "spotify_radio", "both_in_turn"] = Field(
        "listenbrainz", level="advanced", title="A mood plays as",
        description="listenbrainz: a list from two of your artists that fit and artists like them. spotify_radio: "
                    "Spotify's own radio from one of them, shaped by your listening; no searches. both_in_turn: one "
                    "then the other, said the same way, for a blind test (which was which is in its store).")
    bitrate: Literal[320, 160, 96] = Field(320, level="advanced",
                                           description="Spotify's stream: 320 kbps is its best for speakers.")
    api_port: int = Field(3678, ge=1024, le=65535, level="advanced", title="Receiver port",
                          description="go-librespot's API, on 127.0.0.1 only.")
    stream_port: int = Field(3679, ge=1024, le=65535, level="advanced", title="Stream port",
                             description="Where the speakers fetch the audio, on 127.0.0.1 only.")


ext.settings(SpotifySettings)


class State:
    """What the background tasks share, for the life of the process."""

    def __init__(self):
        self.clients: set[asyncio.Queue] = set()
        self.ring: deque[bytes] = deque()
        self.ring_bytes = 0
        self.on = False                     # the speakers are playing our stream, or about to
        self.until = 0.0                    # when the last byte sent will have played (monotonic)
        self.fd: int | None = None          # the pipe, for dropping what nobody heard
        self.now: dict = {}                 # the track, from the receiver's metadata
        self.pending: dict | None = None    # a party armed by party(), to fire when the music starts
        self.party: dict | None = None      # the running party's lights, to stop and restore when it ends
        self.deferred_stop: asyncio.Task | None = None
        self.listen: dict | None = None     # the song being heard, for ListenBrainz: when, how long, its timer


state = State()


def api(ctx) -> str:
    return f"http://127.0.0.1:{ctx.settings.api_port}"


def wav_header() -> bytes:
    """A WAV header for a stream without end: ffmpeg reads until the connection closes."""
    size = 0x7FFFFFF0
    return (b"RIFF" + struct.pack("<I", size + 36) + b"WAVEfmt " +
            struct.pack("<IHHIIHH", 16, 1, CHANNELS, RATE, BPS, CHANNELS * 2, 16) + b"data" + struct.pack("<I", size))


# the receiver

async def binary(ctx) -> str:
    """go-librespot in the extension's folder, downloaded once and checked against its checksum."""
    folder = ctx.folder / f"go-librespot-{VERSION}"
    path = folder / "go-librespot"
    if path.is_file():
        return str(path)
    ctx.log.info(f"downloading go-librespot {VERSION}")
    r = await ctx.http.get(ARCHIVE, timeout=120)
    r.raise_for_status()
    if hashlib.sha256(r.content).hexdigest() != SHA256:
        raise RuntimeError("the go-librespot download does not match its checksum; not run")
    folder.mkdir(parents=True, exist_ok=True)
    archive = folder / "archive.tar.gz"
    archive.write_bytes(r.content)
    with tarfile.open(archive) as tar:
        tar.extract("go-librespot", folder, filter="data")
    archive.unlink()
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


def fifo(ctx):
    path = ctx.folder / "audio.fifo"
    if not path.exists():
        os.mkfifo(path, 0o600)
    return path


def config(ctx) -> dict:
    s = ctx.settings
    return {"log_level": "info", "device_name": s.name, "device_type": "speaker", "audio_backend": "pipe",
            "audio_output_pipe": str(fifo(ctx)), "audio_output_pipe_format": "s16le",
            "audio_output_pipe_wait_for_reader": True, "bitrate": s.bitrate, "zeroconf_enabled": False,
            "credentials": {"type": "device_auth"}, "volume_steps": 100, "initial_volume": 60,
            "metadata": {"enabled": True},          # the next song in /status: "what's next?"
            "server": {"enabled": True, "address": "127.0.0.1", "port": s.api_port}}


@ext.background
async def receiver(ctx):
    """go-librespot, started again if it ends; its config (JSON is YAML) written from the settings."""
    exe = await binary(ctx)
    conf = ctx.folder / "go-librespot"
    conf.mkdir(exist_ok=True)
    (conf / "config.yml").write_text(json.dumps(config(ctx), indent=1))
    proc = await asyncio.create_subprocess_exec(exe, "--config_dir", str(conf), stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.STDOUT)
    ctx.store["pid"] = proc.pid
    try:
        async for raw in proc.stdout:
            line = raw.decode(errors="replace").strip()
            if re.search(r"level=(warn|error|fatal)|panic|spotify\.com/pair", line, re.IGNORECASE):
                ctx.log.warning(line[:300])
    finally:
        if proc.returncode is None:
            proc.kill()
        await proc.wait()
    raise RuntimeError(f"go-librespot exited ({proc.returncode})")


@ext.on_settings
async def settings_changed(ctx, event):
    """A new name or bitrate: the receiver starts again with them (its background task restarts it).
    A new library player: his library is read again now; a new playlist for mixes too, as it is likely one he
    has just made, which the hourly read would miss for up to an hour."""
    if event.old.library != ctx.settings.library:
        ctx.store["library"] = {}
    if (event.old.library, event.old.mix_playlist) != (ctx.settings.library, ctx.settings.mix_playlist):
        await library(ctx)
    pid = ctx.store.get("pid")
    if pid and (event.old.name, event.old.bitrate, event.old.api_port) != \
            (ctx.settings.name, ctx.settings.bitrate, ctx.settings.api_port):
        try:
            os.kill(pid, 15)
        except ProcessLookupError:
            pass


# the relay: the receiver's pipe, at playback speed, to whoever listens on 127.0.0.1

def end(q: asyncio.Queue) -> None:
    """Ends a listener's stream: an empty chunk, with room made for it if need be."""
    state.clients.discard(q)
    if q.full():
        q.get_nowait()
    q.put_nowait(b"")


def broadcast(chunk: bytes) -> None:
    state.ring.append(chunk)
    state.ring_bytes += len(chunk)
    while state.ring_bytes > (LEAD + 0.5) * BPS and len(state.ring) > 1:
        state.ring_bytes -= len(state.ring.popleft())
    for q in list(state.clients):
        try:
            q.put_nowait(chunk)
        except asyncio.QueueFull:          # a listener that stopped reading is let go
            end(q)


async def pace(read, sleep=asyncio.sleep, clock=time.monotonic) -> None:
    """Reads chunks and hands them on at playback speed, LEAD seconds ahead; after a pause (nothing to
    read), the lead is built again at once."""
    start, sent = None, 0.0
    while True:
        chunk = await read()
        if not chunk:
            return
        now = clock()
        if start is None or start + sent < now - 0.5:
            start, sent = now, 0.0
        broadcast(chunk)
        sent += len(chunk) / BPS
        state.until = start + sent
        ahead = start + sent - now - LEAD
        if ahead > 0:
            await sleep(ahead)


async def read_pipe(fd: int, n: int) -> bytes:
    """Up to n bytes from the pipe, waiting until there are some; nothing is read ahead."""
    loop = asyncio.get_running_loop()
    while True:
        try:
            return os.read(fd, n)
        except BlockingIOError:
            ready = loop.create_future()
            loop.add_reader(fd, lambda f=ready: f.done() or f.set_result(None))
            try:
                await ready
            finally:
                loop.remove_reader(fd)


def flush() -> int:
    """Drops what the receiver wrote and nobody heard; returns how many bytes."""
    dropped = 0
    while state.fd is not None:
        try:
            part = os.read(state.fd, 65536)
        except BlockingIOError:
            break
        if not part:
            break
        dropped += len(part)
    return dropped


@ext.background
async def relay(ctx):
    path = fifo(ctx)
    # Opened for reading and writing, so the pipe never reads as closed while the receiver has it shut.
    fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
    fcntl.fcntl(fd, fcntl.F_SETPIPE_SZ, PIPE)
    state.fd = fd

    async def read() -> bytes:
        waited = 0.0
        while state.on and not state.clients and waited < BOX_WAIT:   # the box is connecting: hold the music
            await asyncio.sleep(0.02)
            waited += 0.02
        return await read_pipe(fd, CHUNK)

    async def serve(r: asyncio.StreamReader, w: asyncio.StreamWriter):
        q: asyncio.Queue = asyncio.Queue(maxsize=int(10 * BPS / CHUNK) + 50)
        try:
            await r.readuntil(b"\r\n\r\n")
            w.write(b"HTTP/1.1 200 OK\r\nContent-Type: audio/wav\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n"
                    + wav_header())
            for chunk in list(state.ring):         # what the lead holds: the box starts with a buffer
                q.put_nowait(chunk)
            state.clients.add(q)
            while chunk := await q.get():
                w.write(chunk)
                await w.drain()
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            state.clients.discard(q)
            w.close()

    server = await asyncio.start_server(serve, "127.0.0.1", ctx.settings.stream_port)
    async with server:
        await pace(read)


# the receiver's events: the speakers follow Spotify

async def run_lights(ctx, action: str, script: str | None) -> None:
    """Run (turn_on) or stop (turn_off) a party's Home Assistant light script, logging a failure rather
    than breaking playback. The script must be exposed to Winston (ha.expose) for the call to be allowed."""
    if not script:
        return
    try:
        await ctx.ha.call("script", action, target={"entity_id": script})
    except Exception as e:                                              # noqa: BLE001
        ctx.log.warning(f"party lights {action} failed: {e}")


async def start_party_lights(ctx, party: dict) -> None:
    """Run a party's light script `delay` seconds after the receiver started, so the show lands on the
    sound the box plays, not the receiver's head start. Skips it if the party was stopped while waiting."""
    await asyncio.sleep(party.get("delay") or 0.0)
    if state.party is party:        # still the running party, not stopped during the wait
        await run_lights(ctx, "turn_on", party.get("lights"))


async def end_party(ctx) -> None:
    """Ends the running party: its light script stops and its restore scene puts the room back. Before
    anything else plays (another party, a song), so two light shows never fight over the same lamps."""
    if state.party is None:
        return
    party, state.party = state.party, None
    await run_lights(ctx, "turn_off", party.get("lights"))
    if party.get("restore"):
        try:
            await ctx.ha.call("scene", "turn_on", target={"entity_id": party["restore"]})
        except Exception as e:                                          # noqa: BLE001
            ctx.log.warning(f"party restore failed: {e}")


PLAYED = "played.jsonl"      # what played, a line a song, in its folder: "what was the song before this?"


def played(ctx, n: int = 10) -> list[dict]:
    """The last n songs that played, newest last."""
    try:
        lines = (ctx.folder / PLAYED).read_text().splitlines()[-n:]
    except OSError:
        return []
    return [json.loads(line) for line in lines if line.strip()]


def log_play(ctx, data: dict, skipped: str | None) -> None:
    """A song that came on, unless it is the one logged last (a resume, a second metadata event)."""
    last = played(ctx, 1)
    if not data.get("uri") or (last and last[-1].get("uri") == data["uri"]):
        return
    entry = {"at": ctx.now().isoformat(timespec="seconds"), "uri": data["uri"], "song": data.get("name"),
             "artists": data.get("artist_names") or []} | ({"skipped": skipped} if skipped else {})
    path = ctx.folder / PLAYED
    with path.open("a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    if path.stat().st_size > 400_000:           # about 2,000 songs: keep the newest 1,000
        path.write_text("\n".join(path.read_text().splitlines()[-1000:]) + "\n")


LISTEN_SECS = 240       # ListenBrainz's rule: a song counts once heard for half its length or four minutes
LISTENS = "https://api.listenbrainz.org/1/submit-listens"


def heard(ctx, playing: bool) -> None:
    """The song's listening clock, running only while a box plays it. Once it counts it goes to ListenBrainz,
    once (`listens`): go-librespot tells Spotify nothing (its event sender is a stub), so the box's music never
    reached his Spotify history, nor ListenBrainz through it."""
    listen = state.listen
    if listen is None or listen.get("sent") or not (ctx.settings.listens and ctx.settings.listenbrainz_token):
        return
    now = time.monotonic()
    if listen.get("since") is not None:
        listen["heard"] += now - listen["since"]
    listen["since"] = now if playing else None
    if listen.get("timer"):
        listen["timer"].cancel()
    listen["timer"] = asyncio.create_task(_counts(ctx, listen)) if playing else None


async def _counts(ctx, listen: dict) -> None:
    need = min(LISTEN_SECS, listen["ms"] / 2000) if listen.get("ms") else LISTEN_SECS
    await asyncio.sleep(max(0.0, need - listen["heard"]))
    listen["sent"], listen["timer"] = True, None
    await submit_listen(ctx, listen)


async def submit_listen(ctx, listen: dict) -> None:
    """A listen to ListenBrainz, with any that could not go before (kept in the store, the newest 500). One it
    refuses as malformed is dropped, so it never holds up the rest."""
    url = f"https://open.spotify.com/track/{listen['uri'].rpartition(':')[2]}"
    info = {"artist_names": listen["artists"], "spotify_id": url, "origin_url": url, "music_service": "spotify.com",
            "media_player": "go-librespot", "submission_client": "Winston"} | (
        {"duration_ms": listen["ms"]} if listen.get("ms") else {})
    batch = (ctx.store.get("lb_unsent") or []) + [{"listened_at": int(listen["at"]), "track_metadata": {
        "artist_name": ", ".join(listen["artists"]), "track_name": listen["name"],
        **({"release_name": listen["album"]} if listen.get("album") else {}), "additional_info": info}}]
    try:
        r = await ctx.http.post(LISTENS, headers={"Authorization": f"Token {ctx.settings.listenbrainz_token}"},
                                json={"listen_type": "single" if len(batch) == 1 else "import", "payload": batch},
                                timeout=10)
        keep = r.status_code == 429 or r.status_code >= 500
        if r.status_code != 200:
            ctx.log.warning(f"ListenBrainz took no listen: {r.status_code} {r.text[:150]}")
    except Exception as e:                                  # noqa: BLE001  (kept, sent with the next)
        ctx.log.warning(f"ListenBrainz not reached: {type(e).__name__}")
        keep = True
    ctx.store["lb_unsent"] = batch[-500:] if keep else []


def verdicts(ctx) -> dict:
    """His verdicts: banned songs ({uri, song, artists}), banned artists, and artists out for now ({artist,
    until}). "Not now" ends at its time, or once the speakers have been quiet for quiet_resets_hours (his rule:
    24 hours, or an hour without music, whichever comes first)."""
    t = ctx.store.setdefault("taste", {"songs": [], "artists": [], "for_now": []})
    quiet, now, music = ctx.settings.quiet_resets_hours * 3600, time.time(), ctx.store.get("last_music") or 0
    t["for_now"] = [a for a in t["for_now"] if a["until"] > now and (state.on or now - max(music, a["at"]) <= quiet)]
    return t


def unwanted(ctx, uri: str | None, artists: list[str]) -> str | None:
    """Why this song should not play now: banned, or by an artist he banned or put out for now. None if it may."""
    t = verdicts(ctx)
    if uri and uri in {s["uri"] for s in t["songs"]}:
        return "banned"
    keys = {_key(a) for a in artists}
    if keys & {_key(a) for a in t["artists"]}:
        return "artist banned"
    if keys & {_key(a["artist"]) for a in t["for_now"]}:
        return "artist not now"
    return None


def lift(ctx, item: dict) -> None:
    """He asked for it by name: his own request wins over an earlier "not now" or ban of it or its artist."""
    t = verdicts(ctx)
    names = {_key(a["name"]) for a in item.get("artists") or ([item] if item.get("name") else [])}
    t["songs"] = [x for x in t["songs"] if x["uri"] != item.get("uri")]
    t["artists"] = [a for a in t["artists"] if _key(a) not in names]
    t["for_now"] = [a for a in t["for_now"] if _key(a["artist"]) not in names]


def going(ctx, how: str | None) -> None:
    """What he hears in each message's stamp (ctx.ongoing): "playing Oh Dove by Men I Trust", "paused on ...",
    nothing once it stopped. In a new conversation "who is this?" got "It's me, sir. Winston." 2 of 2 times."""
    song = state.now.get("name") if how else None
    ctx.ongoing(f"{how} {song}" + (f" by {', '.join(state.now['artists'])}" if state.now.get("artists") else "")
                if song else None)


async def handle_event(ctx, ev: dict) -> None:
    kind, data = ev.get("type"), ev.get("data") or {}
    if kind == "metadata":
        state.now = {"name": data.get("name"), "artists": data.get("artist_names") or [], "album": data.get("album_name")}
        why = unwanted(ctx, data.get("uri"), data.get("artist_names") or [])
        log_play(ctx, data, why)
        if why:                                     # radio or a list brought one he does not want: the next one
            ctx.log.info(f"skipped {data.get('name')} ({why})")
            await call(ctx, "POST", "/player/next")
            return
        if data.get("uri") and data["uri"] != (state.listen or {}).get("uri"):
            heard(ctx, False)
            state.listen = {"uri": data["uri"], "name": data.get("name"), "artists": data.get("artist_names") or [],
                            "album": data.get("album_name"), "ms": data.get("duration"),
                            "at": ctx.now().timestamp(), "heard": 0.0}
            heard(ctx, state.on)
        if state.on:
            going(ctx, "playing")
            ctx.store["last_music"] = time.time()
        track = (state.party or {}).get("track")
        if track and data.get("uri") and data["uri"] != track:     # the party's song is over: the next one plays
            await end_party(ctx)
    elif kind == "playing":
        ctx.store["last_music"] = time.time()
        if state.deferred_stop and not state.deferred_stop.done():
            state.deferred_stop.cancel()
            state.deferred_stop = None
        going(ctx, "playing")
        if not state.on:
            title = f"{state.now.get('name')} by {', '.join(state.now.get('artists', []))}" if state.now else "Spotify"
            state.on = True                 # before the box connects, so the relay waits for it
            state.on = await ctx.speakers.play(f"http://127.0.0.1:{ctx.settings.stream_port}/stream.wav", title) > 0
        heard(ctx, state.on)
        if state.pending is not None:       # a party is armed: fire its lights only if a box took the sound,
            pending, state.pending = state.pending, None    # so it never strobes in silence (no box, or no stream)
            if state.on:
                state.party = pending
                asyncio.create_task(start_party_lights(ctx, state.party))    # after its delay, off the events loop
            else:
                ctx.log.warning("party: no box took the stream; not starting the lights")
    elif kind in ("paused", "stopped", "inactive") and state.on:
        heard(ctx, False)
        if kind == "paused":
            state.on = False
            going(ctx, "paused on")
            unheard = max(0.0, state.until - time.monotonic()) + flush() / BPS
            state.ring.clear()
            state.ring_bytes = 0
            for q in list(state.clients):
                end(q)
            await ctx.speakers.stop()
            if unheard >= 0.2:
                await call(ctx, "POST", "/player/seek", position=-round(unheard * 1000), relative=True)
            await end_party(ctx)
        else:
            # Deferred: a track change sends stopped then playing within ~200 ms
            async def _deferred():
                await asyncio.sleep(0.5)
                state.on = False
                going(ctx, None)
                flush()
                state.ring.clear()
                state.ring_bytes = 0
                for q in list(state.clients):
                    end(q)
                await ctx.speakers.stop()
                await end_party(ctx)
            if state.deferred_stop and not state.deferred_stop.done():
                state.deferred_stop.cancel()
            state.deferred_stop = asyncio.create_task(_deferred())


@ext.background
async def events(ctx):
    from websockets.asyncio.client import connect
    await asyncio.sleep(0.5)
    while True:
        try:
            s = await call(ctx, "GET", "/status")
            if s and not s.get("paused") and not s.get("stopped") and s.get("track"):
                await handle_event(ctx, {"type": "metadata", "data": s["track"]})
                await handle_event(ctx, {"type": "playing"})
        except NotReady:
            pass
        async with connect(f"ws://127.0.0.1:{ctx.settings.api_port}/events") as ws:
            async for raw in ws:
                await handle_event(ctx, json.loads(raw))
        await asyncio.sleep(2)


# tools

class NotReady(Exception):
    pass


async def call(ctx, method: str, path: str, **body):
    try:
        r = await ctx.http.request(method, f"{api(ctx)}{path}", json=body or None, timeout=8)
    except Exception as e:
        raise NotReady(f"the Spotify receiver is not answering ({type(e).__name__})") from e
    if r.status_code >= 400:
        raise NotReady(f"the Spotify receiver said {r.status_code}: {r.text[:120]}")
    return r.json() if r.content else None


async def pairing(ctx) -> str | None:
    """The sign-in code, while the receiver waits for one."""
    try:
        r = await ctx.http.get(f"{api(ctx)}/auth/code", timeout=3)
        if r.status_code == 200 and r.content:
            code = r.json()
            return f"Sign in: open {code['url']} (code {code['code']})"
    except Exception as e:                                              # noqa: BLE001
        ctx.log.info(f"no sign-in code: {type(e).__name__}")
    return None


async def ready(ctx) -> None:
    if (sign_in := await pairing(ctx)) is not None:
        raise NotReady(f"Spotify is not signed in yet. {sign_in}, with a Premium account.")
    root = await call(ctx, "GET", "/")
    if not (root or {}).get("playback_ready"):
        raise NotReady("the Spotify receiver is still starting")


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^\w ]", " ", text.lower()).split())


_NUMBERS = dict(zip(map(str, range(21)), ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
                                           "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
                                           "seventeen", "eighteen", "nineteen", "twenty"]))


def _key(text: str) -> str:
    """A name as words to compare: lower case, no punctuation, a number as its word ("7 Seconds" is "Seven
    Seconds", which once played a cover instead of Youssou N'Dour's), an emoji by its name (his 🥁 playlist is
    "drum with drumsticks")."""
    text = "".join(f" {unicodedata.name(c, '')} " if unicodedata.category(c) == "So" else c for c in text)
    return " ".join(_NUMBERS.get(w, w) for w in _norm(text.replace("&", " and ")).split())


def _title(name: str) -> str:
    """A song's title without what Spotify adds to it: "(Radio Edit)", "[feat. ...]", " - 2004 Remaster"."""
    return _key(re.sub(r"\s*[(\[].*?[)\]]|\s+-\s+.*$", "", name))


def _by(item: dict, artist: str) -> bool:
    """Whether item is by `artist` (one of its artists is, or a credit holds the name: "Madison Acid and TV Girl")."""
    want = f" {_key(artist)} "
    return any(want in f" {_key(a['name'])} " or f" {_key(a['name'])} " in want for a in item.get("artists") or [])


LIBRARY = {"playlist": ["current_user_playlists"], "album": ["current_user_saved_albums"],
           "artist": ["current_user_top_artists", "current_user_followed_artists"],
           "track": ["current_user_top_tracks", "current_user_saved_tracks"]}


# Spotify's own playlists by what they are, from their covers: his daylist is renamed every few hours.
_KNOWN = {"daylist.spotifycdn.com": "daylist", "blend-playlist-covers.spotifycdn.com": "blend"}
LIKED = {"type": "playlist", "name": "Liked Songs", "uri": "spotify:collection:tracks"}


@ext.every("1h")
async def library(ctx):
    """His Spotify library by name, for playing without a search: his playlists from the receiver (Spotify's own
    among them: Discover Weekly, his daylist by that word, a Blend; Home Assistant's list had none of them) and
    Liked Songs; his artists, albums and songs from Home Assistant's Spotify player. An empty playlist that is not
    his (the DJ's) is left out; his own may be empty (the one mixes go to)."""
    found: dict[str, list[dict]] = {}
    player = ctx.settings.library
    for kind, ids in LIBRARY.items() if player else ():
        for cid in ids:
            answer = await ctx.ha.query("media_player", "browse_media", target={"entity_id": player},
                                        data={"media_content_type": f"spotify://{cid}", "media_content_id": cid})
            node = ((answer or {}).get("service_response") or answer or {}).get(player) or {}
            for child in node.get("children") or []:
                uri = child.get("media_content_id") or ""
                if uri.startswith("spotify:") and child.get("title"):
                    found.setdefault(kind, []).append({"type": kind, "name": child["title"], "uri": uri})
    try:
        listed = (await call(ctx, "GET", "/library/playlists?limit=500") or {}).get("items") or []
    except NotReady:
        listed = []
    if listed:
        found["playlist"] = [{"type": "playlist", "name": i["name"], "uri": i["uri"], "editable": bool(i.get("can_edit")),
                              "aka": [w for host, w in _KNOWN.items() if host in (i.get("image_url") or "")]}
                             for i in listed if i.get("uri") and (i.get("length") or i.get("can_edit"))] + [LIKED]
    ctx.store["library"] = found


_MINE = re.compile(r"\b(my|emoji)\b", re.IGNORECASE)
_NOT_NAME = {"my", "the", "playlist", "album", "song", "songs", "emoji"}


def _names(ctx, item: dict) -> list[str]:
    """What he may call item: its name, what it is (his daylist), and his own words for it (the `names` setting)."""
    out = [_key(item["name"])] + [_key(w) for w in item.get("aka") or []]
    for line in ctx.settings.names or []:
        name, _, words = line.partition("=")
        if words and _key(name) == _key(item["name"]):
            out += [_key(w) for w in words.split(",") if _key(w)]
    return out


def in_library(ctx, what: str, kind: str | None, partly: bool | None = None) -> dict | None:
    """His own by its whole name or one of his names for it first, of any kind; with "my" or "emoji" (or
    `partly`), also a playlist, album or artist one of whose names has all his words ("my drum playlist", "the
    drum emoji"), never a song (the plane emoji was "Ca Plane Pour Moi"). "Emoji" means a name with one, and
    then a word may be part of the emoji's ("plane" of "airplane"). Not the first name that contains his
    words: "my DJ playlist" played a setlist with DJ in its name instead of his playlist called Dj."""
    lib = ctx.store.get("library") or {}
    kinds = [kind] if kind else ["playlist", "artist", "album", "track"]
    mine = bool(_MINE.search(what)) if partly is None else partly
    emoji = bool(re.search(r"\bemoji\b", what, re.IGNORECASE))
    want = " ".join(w for w in _key(what).split() if not (mine and w in _NOT_NAME))
    if not want:
        return None
    items = [i for k in kinds for i in lib.get(k, [])]
    if (exact := next((i for i in items if want in _names(ctx, i)), None)) is not None:
        return exact
    if not mine:
        return None
    words = want.split()

    def has(name: str, item: dict) -> bool:
        if emoji:
            spelled = name.split()
            return _key(item["name"]) != _norm(item["name"]) and all(any(w in n for n in spelled) for w in words)
        return set(words) <= set(name.split())
    return next((i for k in kinds if k != "track" for i in lib.get(k, []) for n in _names(ctx, i) if has(n, i)), None)


async def genres(ctx) -> set[str]:
    """MusicBrainz's genres (about 2,200: "jazz", "drum and bass", "city pop"), fetched once into its folder, to
    tell a genre said as a name: "play some jazz" went to play as a name and played Queen's album Jazz."""
    path = ctx.folder / "genres.txt"
    if not path.exists():
        try:
            r = await ctx.http.get("https://musicbrainz.org/ws/2/genre/all", params={"fmt": "txt"}, timeout=8,
                                   headers={"User-Agent": "Winston Spotify extension (a home assistant)"})
            if r.status_code == 200 and r.text.strip():
                path.write_text(r.text)
        except Exception as e:                                          # noqa: BLE001  (no guard, not no music)
            ctx.log.info(f"MusicBrainz's genres not read: {type(e).__name__}")
    try:
        return {_key(line) for line in path.read_text().splitlines() if line.strip()}
    except OSError:
        return set()


def _his(ctx, item: dict) -> bool:
    """Whether item is one of his artists' (or is one of his artists)."""
    names = {_key(a["name"]) for a in (ctx.store.get("library") or {}).get("artist", [])}
    return any(_key(a["name"]) in names for a in item.get("artists") or [item])


@ext.tool(tier="read")
async def my_music(ctx) -> dict:
    """The user's Spotify library: playlists, top artists, saved albums and liked songs. Call this when the user
    asks what they have, wants to browse their library, or asks for a mood or a vibe ("something chill", "play
    something energetic") so you can pick their artists that fit, for spotify_play(mood=..., artists=[...])."""
    lib = ctx.store.get("library") or {}
    named = lambda n: n if _key(n) == _norm(n) else f"{n} ({_key(n)})"     # an emoji said by its name
    return {k: [named(item["name"]) for item in items] for k, items in lib.items()}


_app_token: dict = {}


async def app_token(ctx) -> str | None:
    """A token for your own Spotify app (client credentials), when its keys are set."""
    s = ctx.settings
    if not s.client_id or not s.client_secret:
        return None
    if _app_token.get("key") == s.client_id and _app_token.get("until", 0) > time.time() + 60:
        return _app_token["token"]
    r = await ctx.http.post("https://accounts.spotify.com/api/token", data={"grant_type": "client_credentials"},
                            auth=(s.client_id, s.client_secret), timeout=8)
    if r.status_code != 200:
        raise NotReady(f"Spotify refused the app's client ID and secret ({r.status_code})")
    body = r.json()
    _app_token.update(key=s.client_id, token=body["access_token"], until=time.time() + int(body.get("expires_in", 3600)))
    return _app_token["token"]


_search_cache: dict[tuple, tuple[dict, float]] = {}
_SEARCH_TTL = 300


def refusing(ctx) -> str | None:
    """Why Spotify's search will not answer, while it refuses this app (a 429's Retry-After, kept in the store
    so a restart does not ask again): on 2026-10-16 a few hundred searches in an hour used up the app's daily
    quota and it said 429 for 22 hours, and the brain asked again four times in four seconds."""
    until = ctx.store.get("search_refused_until") or 0
    if until <= time.time():
        return None
    at = time.strftime("%H:%M" if time.localtime(until).tm_yday == time.localtime().tm_yday else "%A %H:%M",
                       time.localtime(until))
    return (f"Spotify is refusing this app's searches until about {at} (its limit for the app); asking again will "
            "not help. Songs, artists and playlists in the user's library still play by name.")


async def _search(ctx, q: str, types: list[str]) -> dict:
    """Spotify's answer for q (kept five minutes: the app's quota is small)."""
    key = (q, tuple(types))
    if (hit := _search_cache.get(key)) and time.time() - hit[1] < _SEARCH_TTL:
        return hit[0]
    if (why := refusing(ctx)) is not None:
        raise NotReady(why)
    token = await app_token(ctx)
    r = await ctx.http.get("https://api.spotify.com/v1/search", params={"q": q, "type": ",".join(types), "limit": 5},
                           headers={"Authorization": f"Bearer {token}"}, timeout=8)
    if r.status_code == 429:
        ctx.store["search_refused_until"] = time.time() + int(r.headers.get("retry-after") or 60)
        raise NotReady(refusing(ctx))
    if r.status_code != 200:
        raise NotReady(f"Spotify's search said {r.status_code}")
    _search_cache[key] = (r.json(), time.time())
    return _search_cache[key][0]


def _items(found: dict, kind: str) -> list[dict]:
    return [{"type": kind, **i} for i in (found.get(f"{kind}s") or {}).get("items") or [] if i]


async def find_track(ctx, title: str, by: str, thorough: bool = True) -> tuple[dict | None, str | None]:
    """The song called `title` by `by`, and a line when it is someone else's. Spotify's field search is not
    to be trusted alone ("track:One artist:U2" answered One Sweet Day by Mariah Carey), so a song counts only
    with that title and that artist. Thorough, it asks twice more, and when Spotify's first song of that title
    is by someone else, plays that one and says whose it is ("Big Bad John" is Jimmy Dean's, not Johnny
    Horton's; Aldin's pick). (None, None) when nothing has that title."""
    want = _title(title)
    other = None
    for q in [f"track:{title} artist:{by}"] + ([f"{title} {by}", title] if thorough else []):
        named = [t for t in _items(await _search(ctx, q, ["track"]), "track") if _title(t["name"]) == want]
        if (song := next((t for t in named if _by(t, by)), None)) is not None:
            return song, None
        if q == title and named:
            other = named[0]
    if other is not None:
        return other, f"{other['name']} is by {', '.join(a['name'] for a in other.get('artists') or [])}, not {by}"
    return None, None


async def find(ctx, what: str, kind: str | None, by: str | None = None) -> tuple[dict | None, str | None, list[dict]]:
    """What to play from all of Spotify: (item, a line about it, nearest candidates when nothing fits). Only a
    name that is the name: a song with that title, an artist with that name, an album or playlist only when he
    said which; never Spotify's top song for the words ("drum and bass" played "Drum and Bass Father") or a
    stranger's playlist that happens to be called that ("Something Jolly"). When a song and an artist both
    have the name, the song if it is one of his artists', else the artist when Spotify's songs for the name are
    the artist's own ("Daft Punk"), else the song ("Wonderwall" is Oasis's song, not the band Wonderwall). With
    a kind, the first of that kind whose name has all his words ("random access" is Random Access Memories)."""
    if await app_token(ctx) is None:
        if (own := in_library(ctx, what, kind, partly=True)) is not None:      # his library is all there is
            return own, None, []
        raise NotReady(f"{what!r} is not in the user's Spotify library; to search all of Spotify, add a Spotify app's "
                       "client ID and secret on the Spotify card")
    if by and kind in (None, "track"):
        song, note = await find_track(ctx, what, by)
        if song is None:            # "Currents by Tame Impala" is an album, though the brain said a track 8 of 10
            album = next((a for a in _items(await _search(ctx, f"album:{what} artist:{by}", ["album"]), "album")
                          if _key(a["name"]) == _key(what) and _by(a, by)), None)
            if album is not None:
                return album, (f"No song of {by}'s is called {what}; this is the album" if kind else None), []
        return song, note, []
    found = await _search(ctx, what, [kind] if kind else ["artist", "album", "playlist", "track"])
    want = _key(what)
    tracks, artists = _items(found, "track"), _items(found, "artist")
    if kind:
        words = set(want.split())
        pick = next((i for i in _items(found, kind) if (_title(i["name"]) if kind == "track" else _key(i["name"])) == want),
                    None) or next((i for i in _items(found, kind) if words <= set(_key(i["name"]).split())), None)
        if pick is not None:
            return pick, None, []
    else:
        named = [t for t in tracks if _title(t["name"]) == want]
        song = next((t for t in named if _his(ctx, t)), named[0] if named else None)
        artist = next((a for a in artists if _key(a["name"]) == want), None)
        if song and artist:
            own = any(_by(t, artist["name"]) for t in tracks)
            return (song if _his(ctx, song) or not own else artist), None, []
        if song or artist:
            return song or artist, None, []
        if " by " in what:              # "One by U2", said as one name
            title, _, artist_name = what.rpartition(" by ")
            song, note = await find_track(ctx, title, artist_name)
            if song is not None:
                return song, note, []
    near = [i for t in ("track", "artist", "album") for i in _items(found, t)[:1]]
    return None, None, near


def _describe(item: dict) -> str:
    """How he names it: a track with its artists, an album or playlist by its kind (from his library,
    without the artists)."""
    name, kind = item["name"], item["type"]
    if kind == "artist":
        return name
    by = ", ".join(a["name"] for a in item.get("artists") or [])
    owner = (item.get("owner") or {}).get("display_name")
    return (name if kind == "track" else f"the {kind} {name}") + (f" by {by}" if by else "") + (f" ({owner})" if owner else "")


def _radio_uri(uri: str) -> str | None:
    """spotify:artist:X -> spotify:station:artist:X (and likewise for tracks)."""
    m = re.match(r"spotify:(artist|track):(.+)", uri)
    return f"spotify:station:{m.group(1)}:{m.group(2)}" if m else None


async def start(ctx, mix: bool = False) -> None:
    """Before other music: back from muted, the first music of the day at first_volume (it was left at 96, so a
    07:00 mix would have started near full), and a party's lights stop with its music. A song on repeat ends with
    the next thing asked for, and a mix plays in its own order: shuffle left on in the receiver would bring the
    mix playlist's older mixes in among it."""
    st = await call(ctx, "GET", "/status") or {}
    if st.get("repeat_track"):
        await call(ctx, "POST", "/player/repeat_track", repeat_track=False)
    if mix and st.get("shuffle_context"):
        await call(ctx, "POST", "/player/shuffle_context", shuffle_context=False)
    vol = (await call(ctx, "GET", "/player/volume") or {}).get("value", 60)
    s = ctx.settings
    first = s.first_volume and time.time() - (ctx.store.get("last_music") or 0) > s.first_after_hours * 3600
    if first or vol == 0:
        await call(ctx, "POST", "/player/volume", volume=s.first_volume or 60)
    state.pending = None
    await end_party(ctx)


@ext.tool(tier="act", filler="One moment.", timeout=30)
async def play(ctx, what: str | None = None, kind: Literal["track", "artist", "album", "playlist"] | None = None,
               by: str | None = None, genre: str | None = None, mood: str | None = None,
               artists: list[str] | None = None, like: list[str] | None = None, next: bool = False,
               radio: bool = False, fresh: bool = False, latest: bool = False) -> str:
    """Play music on the speakers, whatever the user asks for: something by name (a song, an artist, an album, a
    playlist; their own first), a genre, a mood or a vibe. Fill in only what they said; it works out how to play
    it. A request to play is music only: never lights or a scene unless they ask for those too. When they only
    say to change the music or play something else, without what: pause, ask what they would like, and suggest
    a mix that fits. A name plays only when something is called that; otherwise it answers with the nearest
    names, to ask the user about.

    Args:
        what: A name, as said: "Daft Punk", "Get Lucky", "my running playlist", "drum emoji". Never a genre, a
            mood or a description of a kind of music.
        kind: Only when the user said which: a track, an artist, an album or a playlist.
        by: The artist, when the user named one with a song: "Get Lucky by Daft Punk" is what="Get Lucky",
            by="Daft Punk".
        genre: A genre or an era the user named, as MusicBrainz tags it, in lower case: "jazz", "drum and bass",
            "80s", "liquid funk".
        mood: A mood, a vibe, an activity, or a kind of music described rather than named, in the user's words:
            "chill", "something to fall asleep to", "poker night", "the songs everyone played in 2012". With it,
            pass artists.
        artists: For a mood: three to six of the user's artists that fit it, from spotify_my_music, as listed there.
        like: Artists the user named as the kind of thing they want: "something like Frank Ocean" is like=["Frank Ocean"].
        next: Play it after the current song instead of now (a song): "add X to the queue". A song queued
            cannot be taken out again here; "clear the queue" is not possible, say so.
        radio: After a name, keep going with music like it (Spotify's radio from it).
        fresh: Music new to the user ("something new", "something I don't know", "surprise me"): artists like
            theirs that they have not played. With it, pass artists (or a genre).
        latest: The newest release of the artist in what, instead of the artist: "the new Tame Impala album" is
            what="Tame Impala", kind="album", latest=true; "Fred again..'s latest" is latest=true.
    """
    await ready(ctx)
    if what and not (by or kind or genre or mood or artists or like) and in_library(ctx, what, None) is None \
            and _key(what) in await genres(ctx):
        what, genre = None, what.lower()        # a genre said as a name: Queen's album Jazz is not "some jazz"
    if latest and what:
        return await _newest(ctx, what, kind)
    if genre or mood or artists or like or fresh:
        return await _mix(ctx, artists or [], genre, like or [], mood, fresh)
    if not what:
        return "Nothing to play was named: ask the user what they would like, and suggest a mix that fits."
    if next:
        return await _queue(ctx, what, by)
    item, note = in_library(ctx, what, kind), None
    if item is None and _MINE.search(what):
        names = [i["name"] for i in (ctx.store.get("library") or {}).get(kind or "playlist", [])]
        return (f"The user has no {kind or 'playlist'} called {what!r} in their library, so nothing was played. "
                f"Their {kind or 'playlist'}s: {', '.join(names[:20]) or 'none'}.")
    if item is None:
        item, note, near = await find(ctx, what, kind, by)
        if item is None:
            return (f"Nothing on Spotify is called {what!r}{f' by {by}' if by else ''}, so nothing was played."
                    + (f" Nearest: {'; '.join(_describe(i) for i in near)}." if near else "")
                    + " A mood or a genre is a mix.")
    lift(ctx, item)
    await start(ctx)
    uri = item.get("uri") or item.get("id", "")
    station = _radio_uri(uri) if radio else None
    await call(ctx, "POST", "/player/play", uri=station or uri)
    if station:
        said = f"radio from {_describe(item)}"
    elif _MINE.search(what) and item["type"] != "artist" and item is in_library(ctx, what, kind):
        said = f"your {item['type']} {item['name']}"
    else:
        said = _describe(item)
    return f"Playing {said}." + (f" {note}." if note else "")


async def _newest(ctx, artist: str, kind: str | None) -> str:
    """The artist's newest album (kind album) or newest album or single, from Spotify's list of their records."""
    item = in_library(ctx, artist, "artist") or (await find(ctx, artist, "artist"))[0]
    if item is None or not (item.get("uri") or "").startswith("spotify:artist:"):
        return f"Spotify has no artist called {artist!r}, so nothing was played."
    if (token := await app_token(ctx)) is None:
        raise NotReady("the newest record needs a Spotify app's client ID and secret on the Spotify card")
    records = []
    for group in ["album"] if kind == "album" else ["album", "single"]:
        r = await ctx.http.get(f"https://api.spotify.com/v1/artists/{item['uri'].rpartition(':')[2]}/albums",
                               params={"include_groups": group, "limit": 5},
                               headers={"Authorization": f"Bearer {token}"}, timeout=8)
        if r.status_code != 200:
            raise NotReady(f"Spotify's list of {item['name']}'s records said {r.status_code}")
        records += r.json().get("items") or []
    if not records:
        return f"Spotify lists no {kind or 'record'} by {item['name']}, so nothing was played."
    new = max(records, key=lambda a: a.get("release_date") or "")      # "2025", "2025-03" and "2025-03-07" all sort
    lift(ctx, item)
    await start(ctx)
    await call(ctx, "POST", "/player/play", uri=new["uri"])
    return f"Playing {new.get('album_type') or 'album'} {new['name']} by {item['name']}, released {new.get('release_date')}."


def known_artists(ctx) -> set[str]:
    """The artists the user knows: their library's and every one that played here."""
    lib = ctx.store.get("library") or {}
    return ({_key(a["name"]) for a in lib.get("artist", [])}
            | {_key(a) for p in played(ctx, 1000) for a in p.get("artists") or []})


async def lb_radio(ctx, artists: list[str], genre: str | None) -> tuple[list[dict], list[str]]:
    """LB Radio's songs from the first two of these artists it knows and ones like them, and the genre's,
    and those two. Easy mode keeps the mix together: for artists, their best-known songs (medium let German
    industrial into a chill mix); for a genre, songs tagged with it (medium took whole releases, and half its
    drum and bass was not), and no similar tags (they took big beat to bossa nova). An artist MusicBrainz does
    not know by that name fails the whole prompt, so the next one stands in."""
    unknown_names = ctx.store.setdefault("lb_unknown", [])
    artists = [a for a in artists if a not in unknown_names]
    while artists or genre:
        seeds = artists[:2]
        terms = [f"artist:({a})" for a in seeds] + ([f"tag:({genre})::nosim"] if genre else [])
        r = await ctx.http.get("https://api.listenbrainz.org/1/explore/lb-radio",
                               params={"prompt": " ".join(terms), "mode": "easy"},
                               headers={"Authorization": f"Token {ctx.settings.listenbrainz_token}"}, timeout=10)
        if r.status_code == 200:
            return r.json()["payload"]["jspf"]["playlist"]["track"], seeds
        error = (r.json() if "json" in r.headers.get("content-type", "") else {}).get("error") or r.text[:120]
        unknown = re.search(r"Artist (.+) could not be looked up", error)
        if not unknown or unknown.group(1) not in seeds:
            raise NotReady(f"ListenBrainz said {r.status_code}: {error}")
        unknown_names.append(unknown.group(1))      # asked about again, every mix spent a round trip on it
        del unknown_names[:-100]
        artists = [a for a in artists if a != unknown.group(1)]
    return [], []


_CREDIT = re.compile(r"\s+(?:feat\.?|ft\.?|featuring|x|with)\s+", re.IGNORECASE)
SONGS = "songs.json"        # LB Radio's songs as found on Spotify (or not), in its folder: the app's search quota is small


def _found(ctx) -> dict:
    try:
        return json.loads((ctx.folder / SONGS).read_text())
    except (OSError, ValueError):
        return {}


async def on_spotify(ctx, song: dict, found: dict) -> dict | None:
    """One of LB Radio's songs on Spotify: the same title (a remix, a live take or a year aside) by its first
    credited artist ("Fred again.. x Brian Eno" is Fred again..), in one search, and kept in `found`, so a song
    that comes back in a later mix costs no search (a song not on Spotify is asked again after a week); None
    when it is not there."""
    title = re.sub(r"\s*[(\[].*?[)\]]", "", song.get("title") or "")
    by = _CREDIT.split(song.get("creator") or "")[0]
    key = f"{_title(title)}|{_key(by)}"
    if (hit := found.get(key)) is not None and (hit.get("uri") or time.time() - hit.get("none", 0) < 7 * 86400):
        return hit if hit.get("uri") else None
    try:
        item = (await find_track(ctx, title, by, thorough=False))[0]
    except NotReady:
        return None
    found[key] = ({"type": "track", "uri": item["uri"], "name": item["name"],
                   "artists": [{"name": a["name"]} for a in item.get("artists") or []]} if item else {"none": time.time()})
    return found[key] if item else None


async def _radio(ctx, artist: str, mood: str | None, blind: bool, search: bool = True) -> str | None:
    """Spotify's radio from one of his artists, shaped by his listening: no searches when the artist is his. None
    when Spotify has no such artist (or, without `search`, when the artist is not his)."""
    item = in_library(ctx, artist, "artist") or ((await find(ctx, artist, "artist"))[0] if search else None)
    if item is None or not (station := _radio_uri(item.get("uri") or "")):
        return None
    await start(ctx)
    await call(ctx, "POST", "/player/play", uri=station)
    if blind:
        return f"Playing a {f'{mood} ' if mood else ''}mix from {item['name']} and artists like them."
    return f"Playing radio from {item['name']}{f', for {mood}' if mood else ''}."


async def _mix(ctx, artists: list[str], genre: str | None, like: list[str], mood: str | None,
               fresh: bool = False) -> str:
    """A fresh mix: from two of his artists that fit (not the ones used last time) and artists like them, with the
    artists he named as a reference always among the seeds; a genre from its tag alone, with those named artists
    only: the brain gave drum and bass Aphex Twin, Fatboy Slim, Avicii and Calvin Harris in 6 of 6 trials, and
    the mix came out mostly pop. Different every time. A mood plays as the `mood_mixes` setting says: ListenBrainz's
    list, Spotify's radio from one of the artists (Aldin: "Elliott Smith radio ... worked good"; ListenBrainz's
    artists like Fatboy Slim and Avicii were pop he called "basic"), or both in turn for a blind test, logged in
    the store. When ListenBrainz does not answer in 10 s, radio plays instead. Fresh, it is ListenBrainz's list
    without the artists the user knows (Aldin's pick for a plain mood: "some new ones"; for "something new", new)."""
    if not (artists or genre or like):
        raise ValueError("a mood needs three to six of the user's artists that fit it (spotify_my_music), or artists they named")
    t = verdicts(ctx)
    out = {_key(a) for a in t["artists"]} | {_key(a["artist"]) for a in t["for_now"]}
    if artists and not like and not genre and not [a for a in artists if _key(a) not in out]:
        raise ValueError(f"all of {', '.join(artists)} are out for now (the user's verdict); pass others from spotify_my_music")
    artists = [a for a in artists if _key(a) not in out]
    recent = ctx.store.get("seeds") or []
    # never used first, then the longest ago: the same chill artists take turns; named ones first of all
    order = list(like) + ([] if genre else [a for a in sorted(random.sample(artists, len(artists)),
                                                              key=lambda a: recent.index(a) if a in recent else -1)
                                            if a not in like])
    recipe, blind = "listenbrainz" if genre or fresh else ctx.settings.mood_mixes, False
    if recipe == "both_in_turn":
        log = ctx.store.get("mood_log") or [{}]
        recipe, blind = ("spotify_radio" if log[-1].get("recipe") == "listenbrainz" else "listenbrainz"), True
    said = None
    if recipe == "spotify_radio":
        said = await _radio(ctx, order[0], mood, blind)
        seeds = order[:1]
    if said is None:
        said, seeds, recipe = await _lb_mix(ctx, order, genre, mood, blind, fresh)
    if not genre:
        ctx.store["seeds"] = ([a for a in recent if a not in seeds] + seeds)[-8:]
    if not (genre or fresh):
        ctx.store["mood_log"] = ((ctx.store.get("mood_log") or []) + [
            {"at": ctx.now().isoformat(timespec="minutes"), "mood": mood, "recipe": recipe, "seeds": seeds}])[-50:]
    return said


def mix_list(ctx) -> str | None:
    """The playlist of his that mixes go to (`mix_playlist`), when it is in his library and his to edit. A mix put
    in Spotify's queue stays there: go-librespot cannot clear it, so its songs followed whatever was asked for
    next (and "Get Lucky next" waited behind 14 of them). Played as a playlist, a mix is one context: the next
    thing asked for replaces it."""
    want = _key(ctx.settings.mix_playlist or "")
    if not want:
        return None
    lists = (ctx.store.get("library") or {}).get("playlist", [])
    return next((i["uri"] for i in lists if i.get("editable") and _key(i["name"]) == want), None)


MIX_KEEP = 60       # songs kept in the mix playlist: the newest mixes, so "previous" goes back into the last one


async def trim(ctx, target: str, added: list[str]) -> None:
    """After a mix is added and playing: its record grows, and the oldest songs beyond MIX_KEEP are removed, one
    call each, while the music plays (removing first would hold up the music by a call per song)."""
    kept = (ctx.store.get("mix_list") or {}).get(target, []) + added
    old, ctx.store.setdefault("mix_list", {})[target] = kept[:-MIX_KEEP], kept[-MIX_KEEP:]
    for uri in old:
        try:
            await call(ctx, "POST", "/library/playlists/remove_track", playlist_uri=target, uri=uri)
        except NotReady as e:
            ctx.log.warning(f"the mix playlist kept an old song: {e}"[:200])


async def _lb_mix(ctx, order: list[str], genre: str | None, mood: str | None,
                  blind: bool, fresh: bool = False) -> tuple[str, list[str], str]:
    """ListenBrainz's list from two of `order` and artists like them (or the genre's), each song found on Spotify;
    the first plays, the rest are queued. Radio from the first artist when ListenBrainz does not answer or Spotify
    refuses searches. What was said, the seeds, and which played (for the blind test's log)."""
    if not ctx.settings.listenbrainz_token or await app_token(ctx) is None:
        raise NotReady("a mix needs a ListenBrainz token and the Spotify app's client ID and secret on the "
                       "Spotify card; play radio from an artist instead")
    if (why := refusing(ctx)) is not None:            # a list's songs need searches; his artists' radio does not
        if fresh:
            raise NotReady(why + " Music new to the user needs them; radio from one of their artists still plays.")
        if not genre and order and (said := await _radio(ctx, order[0], mood, blind, search=False)) is not None:
            return said, order[:1], "spotify_radio"
        raise NotReady(why + " Radio from one of the user's artists still plays.")
    try:
        radio, seeds = await lb_radio(ctx, order, genre)
    except Exception as e:                  # slow, down or refusing: radio instead
        ctx.log.warning(f"ListenBrainz: {type(e).__name__}: {e}"[:200])
        if order and not fresh and (said := await _radio(ctx, order[0], mood, blind)) is not None:
            return said, order[:1], "spotify_radio"
        raise NotReady(f"ListenBrainz did not answer ({type(e).__name__}); play radio from an artist instead") from e
    if not radio:
        raise ValueError(f"ListenBrainz knows none of {', '.join(order)} by those names")
    if fresh:               # before any search: the app's are few
        known = known_artists(ctx)
        radio = [s for s in radio if _key(_CREDIT.split(s.get("creator") or "")[0]) not in known]
        if not radio:
            raise ValueError(f"every artist ListenBrainz found near {', '.join(order[:2] or [genre or ''])} is one "
                             "the user knows; pass other artists or a genre")
    found = _found(ctx)
    hits = await asyncio.gather(*(on_spotify(ctx, s, found) for s in radio[:25]))
    if len(found) > 3000:
        found = dict(list(found.items())[-3000:])
    (ctx.folder / SONGS).write_text(json.dumps(found, ensure_ascii=False))
    songs = list({(_title(s["name"]), _key((s.get("artists") or [{"name": ""}])[0]["name"])): s
                  for s in reversed([s for s in hits if s and not unwanted(ctx, s["uri"], [a["name"] for a in
                                                                                    s.get("artists") or []])])
                  }.values())[::-1]      # one song once, in LB's order, none he banned or put out for now
    if not songs:
        if (why := refusing(ctx)) is not None:          # it began refusing while the songs were looked up
            raise NotReady(why + " Radio from one of the user's artists still plays.")
        raise ValueError(f"no mix from {', '.join(order + [genre] if genre else order)}: none of LB Radio's "
                         "songs were found on Spotify")
    await start(ctx, mix=True)
    if (target := mix_list(ctx)) is not None:
        await call(ctx, "POST", "/library/playlists/add_tracks", playlist_uri=target, uris=[s["uri"] for s in songs][:50])
        await call(ctx, "POST", "/player/play", uri=target, skip_to_uri=songs[0]["uri"])
        asyncio.create_task(trim(ctx, target, [s["uri"] for s in songs][:50]))
    else:
        await call(ctx, "POST", "/player/play", uri=songs[0]["uri"])
        for s in songs[1:]:
            await call(ctx, "POST", "/player/add_to_queue", uri=s["uri"])
    source = [f"artists like {' and '.join(seeds)}, new to the user" if fresh else
              f"{' and '.join(seeds)} and artists like them"] if seeds else []
    source += [genre] if genre else []
    if blind:
        return f"Playing a {f'{mood} ' if mood else ''}mix from {' and '.join(source)}.", seeds, "listenbrainz"
    return (f"Playing a {f'{mood} ' if mood else ''}mix from {' and '.join(source)}, {len(songs)} "
            f"song{'' if len(songs) == 1 else 's'}, "
            f"starting with {_describe(songs[0])}."), seeds, "listenbrainz"


async def _queue(ctx, what: str, by: str | None) -> str:
    """A song after the current one ("play Get Lucky next"); when nothing is playing, now."""
    item, note = in_library(ctx, what, "track"), None
    if item is None:
        item, note, near = await find(ctx, what, "track", by)
        if item is None:
            return (f"No song on Spotify is called {what!r}{f' by {by}' if by else ''}, so nothing was queued."
                    + (f" Nearest: {'; '.join(_describe(i) for i in near)}." if near else ""))
    uri = item.get("uri") or ""
    if not uri.startswith("spotify:track:"):
        raise ValueError(f"{_describe(item)} is not a song; only songs can be queued")
    if not ((await call(ctx, "GET", "/status")) or {}).get("track"):
        await call(ctx, "POST", "/player/play", uri=uri)
        return f"Nothing was playing, so playing {_describe(item)} now." + (f" {note}." if note else "")
    await call(ctx, "POST", "/player/add_to_queue", uri=uri)
    return f"Queued {_describe(item)}." + (f" {note}." if note else "")


@ext.tool(tier="act", timeout=15)
async def party(ctx, what: str, lights: str | None = None, restore: str | None = None,
                volume: int | None = None, delay: float = 0.0) -> str:
    """Start a party scene: play something on the speakers and run a Home Assistant light show that begins
    the instant the music does, then put the room back when it stops. Meant for a shortcut (shortcuts
    config) so one phrase sets the whole scene off the same way every time.

    Args:
        what: A Spotify URI ("spotify:track:...") to play exactly, or what to play, as said.
        lights: A Home Assistant light script (entity_id) to run the moment the music starts.
        restore: A Home Assistant scene (entity_id) to turn on when the music stops.
        volume: 0 to 100, set before it starts.
        delay: Seconds to wait after the receiver starts a track before the lights run, so they land on
            the sound the box actually plays, not the receiver's head start (the relay's ~2 s lead plus
            the box's buffer). Tune it to taste; around 1.5 cancels the default lead.
    """
    await ready(ctx)
    if what.startswith("spotify:"):
        uri, desc = what, "the party"
    else:
        item = in_library(ctx, what, None) or (await find(ctx, what, None))[0]
        if item is None:
            raise ValueError(f"nothing on Spotify is called {what!r}")
        uri, desc = item["uri"], _describe(item)
    if volume is not None:
        await call(ctx, "POST", "/player/volume", volume=max(0, min(100, volume)))
    await end_party(ctx)                # a party already going stops and puts the room back first
    state.pending = {"lights": lights, "restore": restore, "delay": max(0.0, delay),   # fired on "playing"
                     "track": uri if uri.startswith("spotify:track:") else None}
    await call(ctx, "POST", "/player/play", uri=uri)
    return f"Starting {desc}."


@ext.on_speakers_stop
async def stopped(ctx):
    """He said "stop" to the box while it played: the box is silent already; Spotify pauses, so "resume"
    carries on from here, and a party ends with its music."""
    state.pending = None
    await end_party(ctx)
    await call(ctx, "POST", "/player/pause")


async def loaded(ctx) -> dict | None:
    """The receiver's state when a song is loaded, else None. go-librespot answers a pause, a resume or a skip
    with nothing loaded as if it worked, so after a restart "resume the music" said "Playing." into silence."""
    st = await call(ctx, "GET", "/status") or {}
    return st if st.get("track") else None


@ext.tool(tier="act")
async def pause(ctx) -> str:
    """Pause the music."""
    if (st := await loaded(ctx)) is None:
        return "Nothing is playing."
    if st.get("paused"):
        return "Already paused."
    await call(ctx, "POST", "/player/pause")
    return "Paused."


@ext.tool(tier="act")
async def resume(ctx) -> str:
    """Carry on playing the music after a pause."""
    if (st := await loaded(ctx)) is None:
        return "Nothing is loaded to carry on with; something has to be asked for first."
    if not st.get("paused"):
        return "Already playing."
    await call(ctx, "POST", "/player/resume")
    return "Playing."


@ext.tool(tier="act")
async def next_track(ctx) -> str:
    """Skip to the next song. Not for "clear the queue": a song queued cannot be taken out again here, say so."""
    if await loaded(ctx) is None:
        return "Nothing is playing to skip."
    await call(ctx, "POST", "/player/next")
    return "Next song."


@ext.tool(tier="act")
async def previous_track(ctx) -> str:
    """Go back to the previous song (or the start of this one)."""
    if await loaded(ctx) is None:
        return "Nothing is playing."
    await call(ctx, "POST", "/player/prev")
    return "Previous song."


@ext.tool(tier="act")
async def shuffle(ctx, on: bool = True) -> str:
    """Shuffle what is playing (a playlist, an album, a mix), or stop shuffling ("shuffle", "shuffle off").

    Args:
        on: false to play in order again.
    """
    if await loaded(ctx) is None:
        return "Nothing is playing to shuffle; ask what to play (spotify_play), then shuffle it."
    await call(ctx, "POST", "/player/shuffle_context", shuffle_context=on)
    return "Shuffling." if on else "Playing in order."


@ext.tool(tier="act")
async def repeat(ctx, what: Literal["song", "all", "off"]) -> str:
    """Repeat the song playing ("put this on repeat", "loop this song"), everything that is playing (the playlist
    or album), or stop repeating.

    Args:
        what: song, all, or off.
    """
    if await loaded(ctx) is None:
        return "Nothing is playing to repeat."
    await call(ctx, "POST", "/player/repeat_track", repeat_track=what == "song")
    await call(ctx, "POST", "/player/repeat_context", repeat_context=what == "all")
    return {"song": "Repeating this song.", "all": "Repeating all of it.", "off": "Not repeating."}[what]


@ext.tool(tier="act")
async def seek(ctx, seconds: float, relative: bool = True) -> str:
    """Move within the song playing: forward or back by seconds ("skip ahead 30 seconds", "back ten seconds"), or
    to a point in it ("from the top", "play that again": 0, not relative; "go to two minutes": 120). Not for the
    next or the previous song.

    Args:
        seconds: How far: positive ahead, negative back; or where in the song, when not relative.
        relative: false for a point in the song.
    """
    if await loaded(ctx) is None:
        return "Nothing is playing."
    await call(ctx, "POST", "/player/seek", position=round(seconds * 1000), relative=relative)
    if not relative:
        return "From the top." if seconds <= 0 else f"At {int(seconds) // 60}:{int(seconds) % 60:02d}."
    return f"{'Ahead' if seconds > 0 else 'Back'} {abs(seconds):g} seconds."


@ext.tool(tier="act")
async def volume(ctx, level: int | None = None, change: Literal["up", "down"] | None = None) -> str:
    """Set the music's volume (the speakers' own stays where it is): a level from 0 to 100, or up or down a step.

    Args:
        level: 0 to 100.
        change: "up" or "down" a step (about 5 dB), when no number was given.
    """
    if change and level is None:
        # go-librespot plays at the volume squared, so 10 down from 87 was 2 dB and not heard; a quarter of the
        # volume is about 5 dB at any level.
        now = (await call(ctx, "GET", "/player/volume")).get("value", 0)
        level = round(now * 0.75) if change == "down" else max(10, round(now / 0.75))
    if level is not None:
        await call(ctx, "POST", "/player/volume", volume=max(0, min(100, level)))
    now = await call(ctx, "GET", "/player/volume")
    return f"Music volume {now.get('value')} of {now.get('max', 100)}."


async def add_to(ctx, playlist: str, uri: str, song: str) -> str:
    """The song added to one of the user's playlists, found as `spotify_play` finds one of theirs by name."""
    item = in_library(ctx, playlist, "playlist", partly=True)
    if item is not None and item["uri"] == LIKED["uri"]:
        return await feedback_like(ctx, uri, song)
    if item is None:
        names = [i["name"] for i in (ctx.store.get("library") or {}).get("playlist", []) if i.get("editable")]
        return (f"The user has no playlist called {playlist!r} to add to, so nothing was added. Theirs: "
                f"{', '.join(names[:20]) or 'none'}.")
    if not item.get("editable"):
        return f"{item['name']} is not the user's to change (Spotify's own or someone else's), so nothing was added."
    await call(ctx, "POST", "/library/playlists/add_tracks", playlist_uri=item["uri"], uris=[uri])
    return f"Added {song} to {item['name']}."


async def feedback_like(ctx, uri: str, song: str) -> str:
    await call(ctx, "POST", "/library/liked", uris=[uri], liked=True)
    return f"Saved {song} to the user's Liked Songs."


@ext.tool(tier="act", timeout=15)
async def feedback(ctx, verdict: Literal["like", "never", "not_now", "allow"], artist: str | None = None,
                   playlist: str | None = None) -> str:
    """What the user thinks of the music, kept: "I like this" or "save this song" saves the song playing to their
    Liked Songs (like); "never play this again" bans the song, skipped whenever it comes on (never); "not Elliott
    Smith" or "not him for now" keeps the artist out of what plays for a while (not_now); "never play Elliott
    Smith" bans the artist (never, with the artist); "you can play X again" lifts it (allow). When it is unclear
    whether the user means the song or the artist ("this sucks"), ask which first. With "something else" after
    it ("not Elliott Smith, something else"), then also play a mix of other artists that fit (spotify_play).

    Args:
        verdict: like, never, not_now or allow.
        artist: The artist the user named; none when they mean the song playing ("this", "this song").
        playlist: With like: one of the user's playlists to add the song playing to, instead of Liked Songs ("add
            this to my running playlist", "put this in the drum emoji").
    """
    t, now = verdicts(ctx), ((await call(ctx, "GET", "/status") or {}).get("track") or {})
    song = f"{now.get('name')} by {', '.join(now.get('artist_names') or [])}" if now else None
    if verdict == "allow":
        lift(ctx, {"name": artist} if artist else {"uri": now.get("uri"), "artists": []})
        return f"{artist or song or 'It'} may play again."
    if artist is None and verdict != "not_now":
        if not now:
            return "Nothing is playing to judge; ask which song or artist the user means."
        if verdict == "like" and playlist:
            return await add_to(ctx, playlist, now["uri"], song)
        if verdict == "like":
            return await feedback_like(ctx, now["uri"], song)
        t["songs"] = (t["songs"] + [{"uri": now["uri"], "song": now.get("name"), "artists": now.get("artist_names")}])[-500:]
        await call(ctx, "POST", "/player/next")
        return f"{song} will not play again; skipped."
    if verdict == "like":
        return f"Liking an artist is not kept yet; {artist} plays when the user asks for them by name."
    who = artist or (now.get("artist_names") or [None])[0]
    if who is None:
        return "Nothing is playing; ask which artist the user means."
    if verdict == "never":
        t["artists"] = [a for a in t["artists"] if _key(a) != _key(who)] + [who]
    else:
        hours = ctx.settings.not_now_hours
        t["for_now"] = [a for a in t["for_now"] if _key(a["artist"]) != _key(who)] + [
            {"artist": who, "at": time.time(), "until": time.time() + hours * 3600}]
    if now and _key(who) in {_key(a) for a in now.get("artist_names") or []}:
        await call(ctx, "POST", "/player/next")
    return (f"No more {who}." if verdict == "never" else
            f"No {who} for now ({ctx.settings.not_now_hours:g} hours, or until the music has been off a while).")


@ext.tool(tier="act", timeout=15)
async def sleep(ctx, minutes: float | None = None) -> str:
    """A sleep timer: the music fades out and pauses, and nothing is said then ("stop the music in 30
    minutes", "turn it off in an hour", "a sleep timer"). For music to fall asleep to ("something to sleep
    to"), play it and set this. Say good night when setting it: nothing more is said when it stops. Without
    minutes it is the fade alone; 0 cancels it.

    Args:
        minutes: Until the music stops, as the user said; none for the usual fade.
    """
    if minutes == 0:
        if (old := ctx.store.pop("sleep", None)) is not None:
            await call(ctx, "POST", "/player/volume", volume=old["volume"])
        return "The sleep timer is off; the music plays on."
    fade = ctx.settings.sleep_fade_minutes * 60
    total = max(fade, (minutes or 0) * 60)
    vol = (await call(ctx, "GET", "/player/volume") or {}).get("value", 60)
    old = ctx.store.get("sleep") or {}
    ctx.store["sleep"] = {"end": time.time() + total, "fade": fade, "volume": old.get("volume", vol)}
    return (f"The music fades out over the last {fade / 60:g} minutes and stops in {total / 60:g} minutes; nothing "
            "will be said then. Say good night now.")


async def fade_step(ctx, now: float) -> None:
    """One step of the sleep timer's fade (the store says when it ends and the volume before it, so a restart
    carries on): the volume falls with the time left, then the music pauses and the volume goes back for the next
    time, without a word (Aldin: "say good night before, no talking after")."""
    if (sl := ctx.store.get("sleep")) is None:
        return
    left = sl["end"] - now
    if left <= 0:
        ctx.store.pop("sleep", None)
        await call(ctx, "POST", "/player/pause")
        await call(ctx, "POST", "/player/volume", volume=sl["volume"])
        ctx.log.info("sleep timer: the music faded out and paused")
    elif left < sl["fade"]:
        await call(ctx, "POST", "/player/volume", volume=max(1, round(sl["volume"] * left / sl["fade"])))


@ext.background
async def fader(ctx):
    while True:
        await asyncio.sleep(15)
        try:
            await fade_step(ctx, time.time())
        except NotReady as e:
            ctx.log.warning(f"sleep timer: {e}"[:200])


@ext.on_leave
async def left_home(ctx):
    """He left: the music pauses, when pause_on_leave says so."""
    if ctx.settings.pause_on_leave and await loaded(ctx) is not None:
        await call(ctx, "POST", "/player/pause")


@ext.tool(tier="read", outside=False)
async def now_playing(ctx) -> dict:
    """What Spotify is playing here now ("who is this?", "what's this song?"), what plays next ("what's next?") and
    what played before it ("what was the song before this?", "that song from earlier"): the song, its artists and
    album, whether it is paused, shuffled or on repeat, the next song when known, the songs before it with their
    times, and who is out for now. Each message's stamp already says what
    plays (spotify: playing ...)."""
    s = await call(ctx, "GET", "/status")
    t = (s or {}).get("track") or {}
    before = [{"at": p["at"][11:16], "song": p.get("song"), "artists": p.get("artists")}
              for p in played(ctx, 9) if p.get("uri") != t.get("uri") and not p.get("skipped")][-8:]
    v = verdicts(ctx)
    out = {"before": before, "out_for_now": [a["artist"] for a in v["for_now"]] + v["artists"]}
    if not t:
        return {"playing": None, "note": "Nothing is playing on this speaker.", **out}
    nxt = s.get("next_track") or {}
    return {"song": t.get("name"), "artists": t.get("artist_names"), "album": t.get("album_name"),
            "paused": s.get("paused"), "shuffled": s.get("shuffle_context"),
            "repeating": "song" if s.get("repeat_track") else "all" if s.get("repeat_context") else None,
            "next": f"{nxt.get('name')} by {', '.join(nxt.get('artist_names') or [])}" if nxt else None,
            "volume": s.get("volume"), "speaker": s.get("device_name"), **out}


@ext.hint_words
async def words(ctx) -> list[str]:
    """His artists' and playlists' names and his own words for them, for speech to text: "play some Daft Punk"
    was heard as "Dunk", and 4 of his requests that went wrong would have been heard right with them."""
    lib = ctx.store.get("library") or {}
    out = [i["name"] for i in lib.get("artist", [])] + [w for i in lib.get("playlist", []) for w in i.get("aka") or []]
    out += [w.strip() for line in ctx.settings.names or [] for w in line.partition("=")[2].split(",") if w.strip()]
    return [w for w in dict.fromkeys(out) if _key(w) == _norm(w)][:200]         # no emoji: they are not said


@ext.status
async def status(ctx) -> str:
    if (sign_in := await pairing(ctx)) is not None:
        return sign_in
    try:
        s = await call(ctx, "GET", "/status")
    except NotReady:
        return "Starting"
    t = (s or {}).get("track") or {}
    if t and not s.get("paused") and not s.get("stopped"):
        return f"Playing {t.get('name')} by {', '.join(t.get('artist_names') or [])}"
    return f"Ready as {s.get('device_name')} ({s.get('username') or 'signed in'})"
