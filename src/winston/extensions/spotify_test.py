"""Tests for Spotify: `winston ext test spotify`. A fake receiver (go-librespot's API as documented in
its api-spec.yml), a fake Spotify search and a fake library in Home Assistant; nothing is run or
downloaded."""
import asyncio
import json
import os
import time
from pathlib import Path
from urllib.parse import parse_qsl

import httpx
from winston_sdk.testing import FakeHA, Harness

HERE = Path(__file__).parent / "spotify.py"
PLAYER = "media_player.spotify_someone"
KEYS = {"client_id": "app-id", "client_secret": "app-secret", "listenbrainz_token": "lb-token"}


class Receiver:
    def __init__(self):
        self.calls: list[tuple[str, str, dict | None]] = []
        self.pairing: dict | None = None
        self.volume = 60
        self.playing = True
        self.found = {"artists": {"items": [{"uri": "spotify:artist:dp", "name": "Daft Punk"}]},
                      "albums": {"items": [{"uri": "spotify:album:ram", "name": "Random Access Memories",
                                            "artists": [{"name": "Daft Punk"}]}]},
                      "playlists": {"items": [None, {"uri": "spotify:playlist:x", "name": "Daft Punk Mix",
                                                     "owner": {"display_name": "Spotify"}}]},
                      "tracks": {"items": [{"uri": "spotify:track:gl", "name": "Get Lucky",
                                            "artists": [{"name": "Daft Punk"}, {"name": "Pharrell Williams"}]}]}}

    def handle(self, request: httpx.Request) -> httpx.Response:
        form = request.headers.get("content-type", "").startswith("application/x-www-form-urlencoded")
        body = json.loads(request.content) if request.content and not form else None
        path = request.url.path
        self.calls.append((request.method, path, body))
        if request.url.host == "accounts.spotify.com":
            ok = request.headers["authorization"] == httpx.BasicAuth("app-id", "app-secret")._auth_header
            self.calls[-1] = ("APP TOKEN", path, dict(parse_qsl(request.content.decode())))
            return httpx.Response(200 if ok else 400, json={"access_token": "app", "expires_in": 3600})
        if request.url.host == "api.spotify.com":
            assert request.headers["authorization"] == "Bearer app"
            q = dict(parse_qsl(request.url.query.decode()))
            self.calls[-1] = ("SEARCH", q["type"], {"q": q["q"]})
            if q["q"].startswith("track:"):     # a mix's song: Spotify's best match is always by Daft Punk
                title = q["q"].removeprefix("track:").split(" artist:")[0]
                return httpx.Response(200, json={"tracks": {"items": [{"uri": f"spotify:track:{title}", "name": title,
                                                                       "artists": [{"name": "Daft Punk"}]}]}})
            return httpx.Response(200, json={f"{t}s": self.found.get(f"{t}s", {"items": []}) for t in q["type"].split(",")})
        if request.url.host == "api.listenbrainz.org":
            assert request.headers["authorization"] == "Token lb-token"
            prompt = dict(parse_qsl(request.url.query.decode()))["prompt"]
            self.calls[-1] = ("LB", prompt, None)
            if "artist:(Nobody)" in prompt:
                return httpx.Response(400, json={"code": 400, "error": "LB Radio generation failed: Artist Nobody "
                                                                       "could not be looked up. Please use exact spelling."})
            songs = [("Daft Punk feat. Pharrell Williams", "Get Lucky (radio edit)"), ("Daft Punk", "One More Time"),
                     ("Daft Punk", "One More Time [2001]"), ("Someone Else", "Not on Spotify")]
            return httpx.Response(200, json={"payload": {"jspf": {"playlist": {"track": [
                {"creator": c, "title": t} for c, t in songs]}}}})
        if path == "/auth/code":
            return httpx.Response(200, json=self.pairing) if self.pairing else httpx.Response(204)
        if path == "/":
            return httpx.Response(200, json={"playback_ready": True})
        if path == "/token":
            return httpx.Response(200, json={"token": "tok"})
        if path == "/player/volume" and request.method == "POST":
            self.volume = self.volume + body["volume"] if body.get("relative") else body["volume"]
            return httpx.Response(200)
        if path == "/player/volume":
            return httpx.Response(200, json={"value": self.volume, "max": 100})
        if path == "/status":
            return httpx.Response(200, json={"device_name": "Winston", "username": "someone", "paused": False,
                                             "stopped": False, "volume": self.volume,
                                             "track": {"name": "Get Lucky", "artist_names": ["Daft Punk"],
                                                       "album_name": "Random Access Memories"}
                                             if self.playing else None})
        if path.startswith("/player/"):
            return httpx.Response(200)
        return httpx.Response(404)


def library_ha() -> FakeHA:
    """His library as Home Assistant's Spotify player browses it."""
    lists = {"current_user_playlists": [("spotify://playlist", "spotify:playlist:run1", "Running 2026")],
             "current_user_top_artists": [("spotify://artist", "spotify:artist:dp", "Daft Punk")],
             "current_user_saved_tracks": [("spotify://track", "spotify:track:omt", "One More Time")]}

    def browse(data, target):
        kids = [{"media_content_type": t, "media_content_id": i, "title": n}
                for t, i, n in lists.get(data["media_content_id"], [])]
        return {target["entity_id"]: {"title": data["media_content_id"], "children": kids}}
    return FakeHA(services={"media_player.browse_media": browse})


def harness(rx: Receiver, keys: bool = True, library: bool = False) -> Harness:
    return Harness(HERE, settings={"library": PLAYER} if library else {}, background=False,
                   secrets=KEYS if keys else {"client_id": "", "client_secret": "", "listenbrainz_token": ""},
                   http=httpx.MockTransport(rx.handle), ha=library_ha() if library else None)


def played(rx: Receiver) -> list:
    return [b["uri"] for m, p, b in rx.calls if p == "/player/play"]


async def test_an_artist_named_exactly_plays_the_artist():
    rx = Receiver()
    h = harness(rx)
    said = await h.tool("play", what="daft punk")
    assert said == "Playing Daft Punk." and played(rx) == ["spotify:artist:dp"], (said, rx.calls)
    await h.close()


async def test_a_song_plays_the_top_track_and_kind_narrows_the_search():
    rx = Receiver()
    h = harness(rx)
    said = await h.tool("play", what="get lucky")
    assert said == "Playing Get Lucky by Daft Punk, Pharrell Williams.", said
    said = await h.tool("play", what="random access", kind="album")
    assert said == "Playing the album Random Access Memories by Daft Punk." and played(rx)[-1] == "spotify:album:ram"
    assert ("SEARCH", "album", {"q": "random access"}) in rx.calls
    await h.close()


async def test_his_own_playlist_comes_first():
    rx = Receiver()
    h = harness(rx, library=True)
    await h.run("library")
    said = await h.tool("play", what="my running playlist")
    assert said == "Playing your playlist Running 2026." and played(rx) == ["spotify:playlist:run1"], said
    assert not any(c[0] == "SEARCH" for c in rx.calls)
    await h.close()


async def test_his_library_by_its_exact_name_needs_no_search():
    rx = Receiver()
    h = harness(rx, library=True)
    await h.run("library")
    assert await h.tool("play", what="One more time") == "Playing One More Time."
    assert played(rx) == ["spotify:track:omt"] and not any(c[0] == "SEARCH" for c in rx.calls), rx.calls
    await h.close()


async def test_without_the_app_keys_only_his_library_and_he_is_told_why():
    rx = Receiver()
    h = harness(rx, keys=False, library=True)
    await h.run("library")
    assert await h.tool("play", what="daft") == "Playing Daft Punk."
    try:
        await h.tool("play", what="Get Lucky")
        raise AssertionError("searched without the app's keys")
    except Exception as e:                                          # noqa: BLE001
        assert "not in his Spotify library" in str(e) and "client ID and secret" in str(e), e
    assert played(rx) == ["spotify:artist:dp"] and not any(c[0] in ("SEARCH", "APP TOKEN") for c in rx.calls)
    await h.close()


async def test_the_app_token_is_asked_for_once_with_his_keys():
    rx = Receiver()
    h = harness(rx)
    await h.tool("play", what="daft punk")
    await h.tool("play", what="get lucky")
    asked = [c for c in rx.calls if c[0] == "APP TOKEN"]
    assert asked == [("APP TOKEN", "/api/token", {"grant_type": "client_credentials"})], asked
    await h.close()


async def test_not_signed_in_says_how():
    rx = Receiver()
    rx.pairing = {"url": "https://www.spotify.com/pair?code=ABCD", "code": "ABCD", "expires_at": "2026-09-30T20:00:00Z"}
    h = harness(rx)
    try:
        await h.tool("play", what="daft punk")
        raise AssertionError("played while signed out")
    except Exception as e:                                          # noqa: BLE001
        assert "not signed in" in str(e) and "code ABCD" in str(e), e
    await h.close()


async def test_pause_skip_volume_and_what_is_playing():
    rx = Receiver()
    h = harness(rx)
    assert await h.tool("pause") == "Paused."
    assert await h.tool("next_track") == "Next song."
    assert await h.tool("volume", change="up") == "Music volume 70 of 100."
    assert await h.tool("volume", level=150) == "Music volume 100 of 100."
    now = await h.tool("now_playing")
    assert now["song"] == "Get Lucky" and now["artists"] == ["Daft Punk"], now
    assert [p for m, p, b in rx.calls if m == "POST"][:2] == ["/player/pause", "/player/next"]
    await h.close()


async def test_the_speakers_follow_spotify_and_a_pause_loses_nothing():
    rx = Receiver()
    h = harness(rx)
    module = h.ext.tools["play"].fn.__globals__
    handle, state = module["handle_event"], module["state"]
    ctx = h.rt.ctx
    await h.status()                                                # starts the harness
    await handle(ctx, {"type": "metadata", "data": {"name": "Get Lucky", "artist_names": ["Daft Punk"]}})
    await handle(ctx, {"type": "playing", "data": {}})
    await handle(ctx, {"type": "playing", "data": {"resume": True}})
    assert h.speakers == [("play", "http://127.0.0.1:3679/stream.wav", "Get Lucky by Daft Punk")], h.speakers
    state.until = time.monotonic() + 1.5                            # the box has 1.5 s it has not played
    await handle(ctx, {"type": "paused", "data": {}})
    assert [a for a, *_ in h.speakers] == ["play", "stop"], h.speakers     # at once, not when the box runs dry
    seeks = [b for m, p, b in rx.calls if p == "/player/seek"]
    assert len(seeks) == 1 and seeks[0]["relative"] and -1500 <= seeks[0]["position"] <= -1400, seeks
    await handle(ctx, {"type": "playing", "data": {"resume": True}})
    await handle(ctx, {"type": "stopped", "data": {}})
    assert [a for a, *_ in h.speakers] == ["play", "stop", "play"], h.speakers    # deferred, not yet stopped
    await asyncio.sleep(0.6)
    assert [a for a, *_ in h.speakers] == ["play", "stop", "play", "stop"], h.speakers
    assert len([p for m, p, b in rx.calls if p == "/player/seek"]) == 1     # a stop has nothing to go back to
    await h.close()


async def test_a_track_change_keeps_the_speakers_connected():
    rx = Receiver()
    h = harness(rx)
    module = h.ext.tools["play"].fn.__globals__
    handle, state = module["handle_event"], module["state"]
    ctx = h.rt.ctx
    await h.status()
    await handle(ctx, {"type": "metadata", "data": {"name": "Get Lucky", "artist_names": ["Daft Punk"]}})
    await handle(ctx, {"type": "playing", "data": {}})
    assert [a for a, *_ in h.speakers] == ["play"], h.speakers
    await handle(ctx, {"type": "stopped", "data": {}})
    assert [a for a, *_ in h.speakers] == ["play"], h.speakers            # still connected: deferred
    await handle(ctx, {"type": "metadata", "data": {"name": "Harder", "artist_names": ["Daft Punk"]}})
    await handle(ctx, {"type": "playing", "data": {}})
    assert [a for a, *_ in h.speakers] == ["play"], h.speakers            # never disconnected
    await asyncio.sleep(0.6)
    assert [a for a, *_ in h.speakers] == ["play"], h.speakers            # the deferred stop was cancelled
    await h.close()


async def test_one_party_at_a_time_and_each_ends_with_its_song():
    rx, ha = Receiver(), FakeHA()
    h = Harness(HERE, secrets=KEYS, http=httpx.MockTransport(rx.handle), ha=ha, background=False)
    module = h.ext.tools["play"].fn.__globals__
    handle, ctx = module["handle_event"], h.rt.ctx
    await h.status()

    def lights() -> list:
        return [(c["service"], c["target"]["entity_id"]) for c in ha.calls]

    async def starts(uri: str, n: int):                 # the receiver plays it; its lights run off the events loop
        await handle(ctx, {"type": "metadata", "data": {"uri": uri, "name": "Song"}})
        await handle(ctx, {"type": "playing", "data": {}})
        for _ in range(100):
            if len(ha.calls) >= n:
                return
            await asyncio.sleep(0.01)

    await h.tool("party", what="spotify:track:rock", lights="script.rock", restore="scene.before")
    await starts("spotify:track:rock", 1)
    assert lights() == [("turn_on", "script.rock")], lights()
    # another party while the rock show runs: the rock show stops and the room is put back first
    await h.tool("party", what="spotify:track:slow", lights="script.slow")
    assert lights()[1:] == [("turn_off", "script.rock"), ("turn_on", "scene.before")], lights()
    await starts("spotify:track:slow", 4)
    assert lights()[3:] == [("turn_on", "script.slow")], lights()
    await handle(ctx, {"type": "metadata", "data": {"uri": "spotify:track:slow", "name": "Song"}})
    assert len(ha.calls) == 4                           # the same song again is not the end of it
    await handle(ctx, {"type": "metadata", "data": {"uri": "spotify:track:next", "name": "Next"}})
    assert lights()[4:] == [("turn_off", "script.slow")], lights()      # its song is over
    # a song asked for during a party ends it too
    await h.tool("party", what="spotify:track:rock", lights="script.rock", restore="scene.before")
    await starts("spotify:track:rock", 6)
    await h.tool("play", what="daft punk")
    assert lights()[-2:] == [("turn_off", "script.rock"), ("turn_on", "scene.before")], lights()
    await h.close()


async def test_the_relay_keeps_two_seconds_ahead_of_playback():
    h = harness(Receiver())
    module = h.ext.tools["play"].fn.__globals__
    pace, bps, state = module["pace"], module["BPS"], module["state"]
    now = [100.0]
    slept: list[float] = []
    chunks = [b"\0" * bps] * 5 + [None]                             # five seconds of audio, then done

    async def read():
        c = chunks.pop(0)
        return c or b""

    async def sleep(s):
        slept.append(round(s, 3))
        now[0] += s

    q: asyncio.Queue = asyncio.Queue()
    state.clients.add(q)
    await pace(read, sleep=sleep, clock=lambda: now[0])
    assert q.qsize() == 5
    assert slept == [0.5, 1.0, 1.0, 1.0], slept                     # the first 1.5 s at once, then real time
    assert state.ring_bytes <= 2.0 * bps + bps
    await h.close()


async def test_the_pipe_is_read_only_as_asked_and_a_pause_drops_the_rest():
    h = harness(Receiver())
    module = h.ext.tools["play"].fn.__globals__
    read_pipe, flush, state = module["read_pipe"], module["flush"], module["state"]
    r, w = os.pipe()
    os.set_blocking(r, False)
    try:
        os.write(w, b"\1" * 3000)
        assert await read_pipe(r, 1000) == b"\1" * 1000              # no more than asked: the rest waits
        later = asyncio.get_running_loop().call_later(0.05, os.write, w, b"\2" * 10)
        state.fd = r
        assert flush() == 2000 and flush() == 0
        assert await asyncio.wait_for(read_pipe(r, 1000), 1) == b"\2" * 10    # waits for the receiver
        later.cancel()
    finally:
        state.fd = None
        os.close(r)
        os.close(w)
    await h.close()


async def test_a_song_queued_plays_next_or_now_when_nothing_plays():
    rx = Receiver()
    h = harness(rx)
    said = await h.tool("queue", what="get lucky")
    assert said == "Queued Get Lucky by Daft Punk, Pharrell Williams.", said
    assert ("POST", "/player/add_to_queue", {"uri": "spotify:track:gl"}) in rx.calls and played(rx) == []
    assert ("SEARCH", "track", {"q": "get lucky"}) in rx.calls
    rx.playing = False
    said = await h.tool("queue", what="get lucky")
    assert said.startswith("Nothing was playing") and played(rx) == ["spotify:track:gl"], said
    await h.close()


async def test_a_mix_starts_from_two_fresh_artists_and_plays_only_their_songs_found_on_spotify():
    rx = Receiver()
    h = harness(rx)
    said = await h.tool("mix", artists=["Nobody", "Air"])
    lb = [p for m, p, b in rx.calls if m == "LB"]
    assert len(lb) == 2 and "artist:(Nobody)" in lb[0] and lb[1] == "artist:(Air)", lb
    assert said == "Playing a mix from Air and artists like them, 2 songs, starting with Get Lucky by Daft Punk.", said
    assert played(rx) == ["spotify:track:Get Lucky"], rx.calls
    assert [b["uri"] for m, p, b in rx.calls if p == "/player/add_to_queue"] == ["spotify:track:One More Time"]
    assert ("SEARCH", "track", {"q": "track:Get Lucky artist:Daft Punk"}) in rx.calls
    await h.tool("mix", artists=["Air", "Justice", "Daft Punk"])
    assert "Air" not in [p for m, p, b in rx.calls if m == "LB"][-1]
    await h.tool("mix", artists=["Air", "Justice", "Daft Punk"])     # all used: the longest ago go first
    assert "artist:(Air)" in [p for m, p, b in rx.calls if m == "LB"][-1]
    said = await h.tool("mix", artists=[], genre="drum and bass")    # a genre he has no artists of
    assert [p for m, p, b in rx.calls if m == "LB"][-1] == "tag:(drum and bass)::nosim"
    assert said.startswith("Playing a mix from drum and bass, 2 songs"), said
    await h.close()
