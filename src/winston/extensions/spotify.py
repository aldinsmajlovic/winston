# /// script
# [tool.winston]
# id = "spotify"
# name = "Spotify"
# description = "Spotify on your speakers: a Spotify Connect speaker for the app, and play, pause and skip by voice."
# icon = "music"
# api = 1
# permissions = { network = ["127.0.0.1", "api.spotify.com", "accounts.spotify.com", "github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"], home_assistant = "act", speakers = true }
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
with your own Spotify app's client ID and secret when you add them. Not with the receiver's session
token: Spotify's Web API answered every call with it with 429, too many requests, on 2026-09-30 (it is
the shared client of every librespot)."""
from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import os
import re
import stat
import struct
import tarfile
import time
from collections import deque
from typing import Literal

from winston_sdk import Entity, Extension, Field, Secret, Settings

ext = Extension()

VERSION = "0.10.2"
ARCHIVE = f"https://github.com/devgianlu/go-librespot/releases/download/v{VERSION}/go-librespot_linux_x86_64.tar.gz"
SHA256 = "9d2b8b2c85606f5c8e75f9f48910d711cab7406f3582091126b3f3063ee0282e"   # GitHub's digest, 2026-09-30
RATE, CHANNELS = 44100, 2
BPS = RATE * CHANNELS * 2           # s16le
LEAD = 2.0                          # seconds the relay reads ahead of playback: the box's buffer
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
    A new library player: his library is read again now."""
    if event.old.library != ctx.settings.library:
        ctx.store["library"] = {}
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


async def handle_event(ctx, ev: dict) -> None:
    kind, data = ev.get("type"), ev.get("data") or {}
    if kind == "metadata":
        state.now = {"name": data.get("name"), "artists": data.get("artist_names") or [], "album": data.get("album_name")}
        track = (state.party or {}).get("track")
        if track and data.get("uri") and data["uri"] != track:     # the party's song is over: the next one plays
            await end_party(ctx)
    elif kind == "playing":
        if not state.on:
            title = f"{state.now.get('name')} by {', '.join(state.now.get('artists', []))}" if state.now else "Spotify"
            state.on = True                 # before the box connects, so the relay waits for it
            state.on = await ctx.speakers.play(f"http://127.0.0.1:{ctx.settings.stream_port}/stream.wav", title) > 0
        if state.pending is not None:       # a party is armed: fire its lights only if a box took the sound,
            pending, state.pending = state.pending, None    # so it never strobes in silence (no box, or no stream)
            if state.on:
                state.party = pending
                asyncio.create_task(start_party_lights(ctx, state.party))    # after its delay, off the events loop
            else:
                ctx.log.warning("party: no box took the stream; not starting the lights")
    elif kind in ("paused", "stopped", "inactive") and state.on:    # a skip or the next song is "playing" alone
        state.on = False
        unheard = max(0.0, state.until - time.monotonic()) + flush() / BPS
        state.ring.clear()
        state.ring_bytes = 0
        for q in list(state.clients):       # the box's stream ends here; the next one starts clean
            end(q)
        await ctx.speakers.stop()
        if kind == "paused" and unheard >= 0.2:
            # The stop dropped what the box had not played: Spotify goes back to where he stopped hearing.
            await call(ctx, "POST", "/player/seek", position=-round(unheard * 1000), relative=True)
        await end_party(ctx)                # the party's music stopped: end the light show and put the room back


@ext.background
async def events(ctx):
    from websockets.asyncio.client import connect
    await asyncio.sleep(2)
    async with connect(f"ws://127.0.0.1:{ctx.settings.api_port}/events") as ws:
        async for raw in ws:
            await handle_event(ctx, json.loads(raw))


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


LIBRARY = {"playlist": ["current_user_playlists"], "album": ["current_user_saved_albums"],
           "artist": ["current_user_top_artists", "current_user_followed_artists"],
           "track": ["current_user_top_tracks", "current_user_saved_tracks"]}


@ext.every("1h")
async def library(ctx):
    """His Spotify library by name, from Home Assistant's Spotify player, for playing without a search."""
    player = ctx.settings.library
    if not player:
        return
    found: dict[str, list[dict]] = {}
    for kind, ids in LIBRARY.items():
        for cid in ids:
            answer = await ctx.ha.query("media_player", "browse_media", target={"entity_id": player},
                                        data={"media_content_type": f"spotify://{cid}", "media_content_id": cid})
            node = ((answer or {}).get("service_response") or answer or {}).get(player) or {}
            for child in node.get("children") or []:
                uri = child.get("media_content_id") or ""
                if uri.startswith("spotify:") and child.get("title"):
                    found.setdefault(kind, []).append({"type": kind, "name": child["title"], "uri": uri})
    ctx.store["library"] = found


def in_library(ctx, what: str, kind: str | None, exact: bool) -> dict | None:
    want = _norm(re.sub(r"\b(my|playlist|album|songs?)\b", " ", what, flags=re.IGNORECASE)) if not exact else _norm(what)
    if not want:
        return None
    for k in [kind] if kind else ["playlist", "artist", "album", "track"]:
        for item in (ctx.store.get("library") or {}).get(k, []):
            name = _norm(item["name"])
            if name == want or (not exact and want in name):
                return item
    return None


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


async def search(ctx, what: str, kind: str | None) -> dict:
    """What to play: in his library by its exact name, else Spotify's best match (the thing named
    exactly, else the top track), else in his library by part of its name."""
    if (item := in_library(ctx, what, kind, exact=True)) is not None:
        return item
    token = await app_token(ctx)
    if token is None:
        if (item := in_library(ctx, what, kind, exact=False)) is not None:
            return item
        raise NotReady(f"{what!r} is not in his Spotify library; to search all of Spotify, add a Spotify app's "
                       "client ID and secret on the Spotify card")
    types = [kind] if kind else ["artist", "album", "playlist", "track"]
    r = await ctx.http.get("https://api.spotify.com/v1/search", params={"q": what, "type": ",".join(types), "limit": 5},
                           headers={"Authorization": f"Bearer {token}"}, timeout=8)
    if r.status_code != 200:
        raise NotReady(f"Spotify's search said {r.status_code}")
    found = r.json()
    want = _norm(what)
    for t in types:
        items = [i for i in (found.get(f"{t}s") or {}).get("items") or [] if i]
        exact = next((i for i in items if _norm(i.get("name", "")) == want), None)
        if exact or (kind == t and items):
            return {"type": t, **(exact or items[0])}
    tracks = [i for i in (found.get("tracks") or {}).get("items") or [] if i]
    if tracks:
        return {"type": "track", **tracks[0]}
    raise ValueError(f"nothing on Spotify for {what!r}")


def _describe(item: dict) -> str:
    """How he names it: a track with its artists, an album or playlist by its kind (from his library,
    without the artists)."""
    name, kind = item["name"], item["type"]
    if kind == "artist":
        return name
    by = ", ".join(a["name"] for a in item.get("artists") or [])
    owner = (item.get("owner") or {}).get("display_name")
    return (name if kind == "track" else f"the {kind} {name}") + (f" by {by}" if by else "") + (f" ({owner})" if owner else "")


@ext.tool(tier="act", filler="One moment.", timeout=15)
async def play(ctx, what: str, kind: Literal["track", "artist", "album", "playlist"] | None = None) -> str:
    """Play music from Spotify on the speakers: a song, an artist, an album, or a playlist (his own
    library first). It moves Spotify playback here from any other device.

    Args:
        what: What he asked for, as said: "Daft Punk", "Get Lucky", "my running playlist".
        kind: Only when he said which: a track, an artist, an album or a playlist.
    """
    await ready(ctx)
    state.pending = None                # another song: a party's lights stop with its music
    await end_party(ctx)
    if re.search(r"\bmy\b", what, re.IGNORECASE) and (own := in_library(ctx, what, kind, exact=False)) is not None:
        await call(ctx, "POST", "/player/play", uri=own["uri"])
        return f"Playing your {own['type']} {own['name']}."
    item = await search(ctx, what, kind)
    await call(ctx, "POST", "/player/play", uri=item["uri"])
    return f"Playing {_describe(item)}."


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
        item = await search(ctx, what, None)
        uri, desc = item["uri"], _describe(item)
    if volume is not None:
        await call(ctx, "POST", "/player/volume", volume=max(0, min(100, volume)))
    await end_party(ctx)                # a party already going stops and puts the room back first
    state.pending = {"lights": lights, "restore": restore, "delay": max(0.0, delay),   # fired on "playing"
                     "track": uri if uri.startswith("spotify:track:") else None}
    await call(ctx, "POST", "/player/play", uri=uri)
    return f"Starting {desc}."


@ext.tool(tier="act")
async def pause(ctx) -> str:
    """Pause the music."""
    await call(ctx, "POST", "/player/pause")
    return "Paused."


@ext.tool(tier="act")
async def resume(ctx) -> str:
    """Carry on playing the music after a pause."""
    await call(ctx, "POST", "/player/resume")
    return "Playing."


@ext.tool(tier="act")
async def next_track(ctx) -> str:
    """Skip to the next song."""
    await call(ctx, "POST", "/player/next")
    return "Next song."


@ext.tool(tier="act")
async def previous_track(ctx) -> str:
    """Go back to the previous song (or the start of this one)."""
    await call(ctx, "POST", "/player/prev")
    return "Previous song."


@ext.tool(tier="act")
async def volume(ctx, level: int | None = None, change: Literal["up", "down"] | None = None) -> str:
    """Set the music's volume (the speakers' own stays where it is): a level from 0 to 100, or up or down a step.

    Args:
        level: 0 to 100.
        change: "up" or "down", by 10, when he gave no number.
    """
    if level is not None:
        await call(ctx, "POST", "/player/volume", volume=max(0, min(100, level)))
    elif change:
        await call(ctx, "POST", "/player/volume", volume=10 if change == "up" else -10, relative=True)
    now = await call(ctx, "GET", "/player/volume")
    return f"Music volume {now.get('value')} of {now.get('max', 100)}."


@ext.tool(tier="read", outside=False)
async def now_playing(ctx) -> dict:
    """What Spotify is playing here now: the song, its artists and album, and whether it is paused."""
    s = await call(ctx, "GET", "/status")
    t = (s or {}).get("track") or {}
    if not t:
        return {"playing": None, "note": "Nothing is playing on this speaker."}
    return {"song": t.get("name"), "artists": t.get("artist_names"), "album": t.get("album_name"),
            "paused": s.get("paused"), "volume": s.get("volume"), "speaker": s.get("device_name")}


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
