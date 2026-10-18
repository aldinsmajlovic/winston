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
        self.refusing = 0                       # Spotify's search answers 429 with this Retry-After
        self.answers: dict[str, dict] = {}      # Spotify's answer for one query, else `found`
        self.lb_slow = False                    # ListenBrainz does not answer
        self.playlists: list[dict] | None = None    # the receiver's /library/playlists (go-librespot 0.10.3 on)
        self.lb_songs: list[tuple[str, str]] | None = None     # LB Radio's songs, (creator, title), else its usual
        self.lb_status = 200                    # ListenBrainz's answer to a listen
        self.records: dict[str, list[dict]] = {}    # Spotify's list of an artist's records, by group
        self.next_track: dict | None = None     # the receiver's next song (metadata.enabled)
        self.modes: dict = {}                   # repeat_track, repeat_context, shuffle_context in its status
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
            if path.startswith("/v1/artists/"):
                self.calls[-1] = ("RECORDS", path, {"group": q["include_groups"]})
                return httpx.Response(200, json={"items": self.records.get(q["include_groups"], [])})
            self.calls[-1] = ("SEARCH", q["type"], {"q": q["q"]})
            if self.refusing:
                return httpx.Response(429, headers={"retry-after": str(self.refusing)}, json={"error": {"status": 429}})
            if q["q"] in self.answers:
                return httpx.Response(200, json=self.answers[q["q"]])
            if q["q"].startswith("track:"):     # a mix's song: Spotify's best match is always by Daft Punk
                title = q["q"].removeprefix("track:").split(" artist:")[0]
                return httpx.Response(200, json={"tracks": {"items": [{"uri": f"spotify:track:{title}", "name": title,
                                                                       "artists": [{"name": "Daft Punk"}]}]}})
            return httpx.Response(200, json={f"{t}s": self.found.get(f"{t}s", {"items": []}) for t in q["type"].split(",")})
        if request.url.host == "musicbrainz.org":
            self.calls[-1] = ("GENRES", path, None)
            return httpx.Response(200, text="classic rock\ndrum and bass\njazz\n")
        if request.url.host == "api.listenbrainz.org":
            if self.lb_slow:
                raise httpx.ReadTimeout("no answer", request=request)
            assert request.headers["authorization"] == "Token lb-token"
            if path == "/1/submit-listens":
                self.calls[-1] = ("LISTENS", path, body)
                return httpx.Response(self.lb_status, json={"status": "ok"})
            prompt = dict(parse_qsl(request.url.query.decode()))["prompt"]
            self.calls[-1] = ("LB", prompt, None)
            if "artist:(Nobody)" in prompt:
                return httpx.Response(400, json={"code": 400, "error": "LB Radio generation failed: Artist Nobody "
                                                                       "could not be looked up. Please use exact spelling."})
            songs = self.lb_songs or [("Daft Punk feat. Pharrell Williams", "Get Lucky (radio edit)"),
                                      ("Daft Punk", "One More Time"), ("Daft Punk", "One More Time [2001]"),
                                      ("Someone Else", "Not on Spotify")]
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
                                             "stopped": False, "volume": self.volume, "next_track": self.next_track,
                                             **self.modes,
                                             "track": {"name": "Get Lucky", "artist_names": ["Daft Punk"],
                                                       "uri": "spotify:track:gl",
                                                       "album_name": "Random Access Memories"}
                                             if self.playing else None})
        if path == "/library/playlists" and self.playlists is not None:
            return httpx.Response(200, json={"items": self.playlists, "total": len(self.playlists)})
        if path.startswith("/library/") and request.method == "POST":
            return httpx.Response(200)
        if path.startswith("/player/"):
            return httpx.Response(200)
        return httpx.Response(404)


def library_ha() -> FakeHA:
    """His library as Home Assistant's Spotify player browses it."""
    lists = {"current_user_playlists": [("spotify://playlist", "spotify:playlist:run1", "Running 2026"),
                                        ("spotify://playlist", "spotify:playlist:djset", "DJ Someone - Setlist"),
                                        ("spotify://playlist", "spotify:playlist:dj", "Dj"),
                                        ("spotify://playlist", "spotify:playlist:drums", "\U0001f941"),
                                        ("spotify://playlist", "spotify:playlist:plane", "\u2708\ufe0f")],
             "current_user_top_artists": [("spotify://artist", "spotify:artist:dp", "Daft Punk"),
                                          ("spotify://artist", "spotify:artist:rh", "Radiohead")],
             "current_user_saved_tracks": [("spotify://track", "spotify:track:omt", "One More Time"),
                                           ("spotify://track", "spotify:track:cppm", "Ca Plane Pour Moi")]}

    def browse(data, target):
        kids = [{"media_content_type": t, "media_content_id": i, "title": n}
                for t, i, n in lists.get(data["media_content_id"], [])]
        return {target["entity_id"]: {"title": data["media_content_id"], "children": kids}}
    return FakeHA(services={"media_player.browse_media": browse})


def harness(rx: Receiver, keys: bool = True, library: bool = False, names: list[str] | None = None,
            **settings) -> Harness:
    return Harness(HERE, settings={**({"library": PLAYER} if library else {}), **({"names": names} if names else {}),
                                   **settings},
                   background=False,
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
        assert "not in the user's Spotify library" in str(e) and "client ID and secret" in str(e), e
    assert played(rx) == ["spotify:artist:dp"] and not any(c[0] in ("SEARCH", "APP TOKEN") for c in rx.calls)
    await h.close()


async def test_controls_say_what_is_so_when_nothing_plays():
    rx = Receiver()
    rx.playing = False
    h = harness(rx)
    said = [await h.tool(t) for t in ("pause", "resume", "next_track", "previous_track")]
    assert said == ["Nothing is playing.", "Nothing is loaded to carry on with; something has to be asked for first.",
                    "Nothing is playing to skip.", "Nothing is playing."], said
    assert not [p for m, p, b in rx.calls if m == "POST" and p.startswith("/player/")], rx.calls
    rx.playing = True
    assert await h.tool("resume") == "Already playing."
    await h.close()


async def test_a_stop_on_the_box_pauses_spotify():
    rx = Receiver()
    h = harness(rx)
    await h.tool("play", what="daft punk")
    await h.run("stopped")
    assert ("POST", "/player/pause", None) in rx.calls, rx.calls
    await h.close()


async def test_a_refusing_search_is_said_plainly_and_not_asked_again_until_it_lifts():
    rx = Receiver()
    rx.refusing = 80056
    h = harness(rx, library=True)
    await h.run("library")
    for what in ("Push the Feeling On", "Push the Feeling On", "something else"):
        try:
            await h.tool("play", what=what)
            raise AssertionError("played while Spotify refuses")
        except AssertionError:
            raise
        except Exception as e:                                      # noqa: BLE001
            assert "refusing this app's searches until about" in str(e) and "will not help" in str(e), e
    assert [c[0] for c in rx.calls].count("SEARCH") == 1, rx.calls
    assert await h.tool("play", what="One more time") == "Playing One More Time."    # his library still plays
    try:
        await h.tool("play", artists=["Air"])
        raise AssertionError("mixed while Spotify refuses")
    except AssertionError:
        raise
    except Exception as e:                                          # noqa: BLE001
        assert "Radio from one of the user's artists still plays" in str(e) and not any(c[0] == "LB" for c in rx.calls), e
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
    assert await h.tool("volume", change="up") == "Music volume 80 of 100."
    assert (await h.tool("volume", level=150)).startswith("Music volume 100 of 100. The speaker itself is at 50")
    assert await h.tool("volume", change="down") == "Music volume 75 of 100."
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
    going = lambda: (h.rt.state_dir / "ongoing.txt").read_text() if (h.rt.state_dir / "ongoing.txt").exists() else None
    assert going() == "playing Get Lucky by Daft Punk", going()      # in each message's stamp
    state.until = time.monotonic() + 1.5                            # the box has 1.5 s it has not played
    await handle(ctx, {"type": "paused", "data": {}})
    assert [a for a, *_ in h.speakers] == ["play", "stop"], h.speakers     # at once, not when the box runs dry
    assert going() == "paused on Get Lucky by Daft Punk", going()
    seeks = [b for m, p, b in rx.calls if p == "/player/seek"]
    assert len(seeks) == 1 and seeks[0]["relative"] and -1500 <= seeks[0]["position"] <= -1400, seeks
    await handle(ctx, {"type": "playing", "data": {"resume": True}})
    await handle(ctx, {"type": "stopped", "data": {}})
    assert [a for a, *_ in h.speakers] == ["play", "stop", "play"], h.speakers    # deferred, not yet stopped
    await asyncio.sleep(0.6)
    assert [a for a, *_ in h.speakers] == ["play", "stop", "play", "stop"], h.speakers
    assert going() is None, going()
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
    said = await h.tool("play", what="get lucky", next=True)
    assert said == "Queued Get Lucky by Daft Punk, Pharrell Williams.", said
    assert ("POST", "/player/add_to_queue", {"uri": "spotify:track:gl"}) in rx.calls and played(rx) == []
    assert ("SEARCH", "track", {"q": "get lucky"}) in rx.calls
    rx.playing = False
    said = await h.tool("play", what="get lucky", next=True)
    assert said.startswith("Nothing was playing") and played(rx) == ["spotify:track:gl"], said
    await h.close()


async def test_a_mix_starts_from_two_fresh_artists_and_plays_only_their_songs_found_on_spotify():
    rx = Receiver()
    h = harness(rx)
    said = await h.tool("play", artists=["Nobody", "Air"])
    lb = [p for m, p, b in rx.calls if m == "LB"]
    assert len(lb) == 2 and "artist:(Nobody)" in lb[0] and lb[1] == "artist:(Air)", lb
    assert said == "Playing a mix from Air and artists like them, 2 songs, starting with Get Lucky by Daft Punk.", said
    assert played(rx) == ["spotify:track:Get Lucky"], rx.calls
    assert [b["uri"] for m, p, b in rx.calls if p == "/player/add_to_queue"] == ["spotify:track:One More Time"]
    assert ("SEARCH", "track", {"q": "track:Get Lucky artist:Daft Punk"}) in rx.calls
    await h.tool("play", artists=["Air", "Justice", "Daft Punk"])
    assert "Air" not in [p for m, p, b in rx.calls if m == "LB"][-1]
    await h.tool("play", artists=["Air", "Justice", "Daft Punk"])     # all used: the longest ago go first
    assert "artist:(Air)" in [p for m, p, b in rx.calls if m == "LB"][-1]
    said = await h.tool("play", artists=[], genre="drum and bass")    # a genre he has no artists of
    assert [p for m, p, b in rx.calls if m == "LB"][-1] == "tag:(drum and bass)::nosim"
    assert said.startswith("Playing a mix from drum and bass, 2 songs"), said
    await h.close()


def song(uri: str, name: str, *artists: str) -> dict:
    return {"uri": f"spotify:track:{uri}", "name": name, "artists": [{"name": a} for a in artists]}


def answer(tracks=(), artists=(), albums=(), playlists=()) -> dict:
    return {"tracks": {"items": list(tracks)}, "artists": {"items": list(artists)},
            "albums": {"items": list(albums)}, "playlists": {"items": list(playlists)}}


async def test_only_a_name_that_is_the_name_plays():
    """As recorded on 2026-10-16: an album, an artist or a stranger's playlist that only carries the words won."""
    rx = Receiver()
    rx.answers = {
        "Creep": answer([song("creep", "Creep", "Radiohead"), song("c2", "Creep", "Someone")],
                        albums=[{"uri": "spotify:album:cr", "name": "Creep", "artists": [{"name": "CR33PIA"}]}]),
        "Wonderwall": answer([song("ww", "Wonderwall - Remastered", "Oasis"), song("w2", "Wonderwall", "A Cover")],
                             artists=[{"uri": "spotify:artist:wwb", "name": "Wonderwall"}]),
        "drum and bass": answer([song("dbf", "Drum and Bass Father", "Devilman")],
                                albums=[{"uri": "spotify:album:dnb", "name": "Drum & Bass Arena", "artists": []}]),
        "something jolly": answer([song("sj", "Jolly Song", "Someone")],
                                  playlists=[{"uri": "spotify:playlist:sj", "name": "Something Jolly",
                                              "owner": {"display_name": "a stranger"}}]),
        "Seven Seconds": answer([song("7s", "7 Seconds (feat. Neneh Cherry)", "Youssou N'Dour"),
                                 song("7c", "Seven Seconds", "Tribal Strength")]),
    }
    h = harness(rx, library=True)
    await h.run("library")
    assert await h.tool("play", what="Creep") == "Playing Creep by Radiohead.", rx.calls
    assert await h.tool("play", what="Wonderwall") == "Playing Wonderwall - Remastered by Oasis."
    said = await h.tool("play", what="drum and bass")              # a genre, as MusicBrainz knows it: a mix
    assert said.startswith("Playing a mix from drum and bass,"), said
    said = await h.tool("play", what="something jolly")
    assert said.startswith("Nothing on Spotify is called 'something jolly', so nothing was played. Nearest:"), said
    assert await h.tool("play", what="Seven Seconds") == "Playing 7 Seconds (feat. Neneh Cherry) by Youssou N'Dour."
    assert played(rx) == ["spotify:track:creep", "spotify:track:ww", "spotify:track:Get Lucky", "spotify:track:7s"], \
        played(rx)
    await h.close()


async def test_a_song_by_an_artist_is_by_that_artist_or_he_is_told_whose_it_is():
    rx = Receiver()
    rx.answers = {
        "track:One artist:U2": answer([song("osd", "One Sweet Day", "Mariah Carey", "Boyz II Men")]),
        "One U2": answer([song("one", "One", "U2"), song("met", "One", "Metallica")]),
        "track:Big Bad John artist:Johnny Horton": answer([song("bbj", "Big Bad John", "Jimmy Dean")]),
        "Big Bad John Johnny Horton": answer([song("bbj", "Big Bad John", "Jimmy Dean")]),
        "Big Bad John": answer([song("bbj", "Big Bad John", "Jimmy Dean"), song("bbc", "Big Bad John", "A Cover")]),
        "One by U2": answer([song("so", "Stressed Out", "Twenty One Pilots")]),
    }
    h = harness(rx)
    assert await h.tool("play", what="One", by="U2") == "Playing One by U2."
    said = await h.tool("play", what="Big Bad John", by="Johnny Horton")
    assert said == "Playing Big Bad John by Jimmy Dean. Big Bad John is by Jimmy Dean, not Johnny Horton.", said
    assert await h.tool("play", what="One by U2") == "Playing One by U2."         # said as one name
    assert await h.tool("play", what="One", by="U2", next=True) == "Queued One by U2."
    await h.close()

async def test_a_song_said_a_little_off_is_the_artists_own_or_he_is_asked_with_their_songs():
    """As heard on 2026-10-18: "I'm Tired by Fugazi" played Labrinth's I'm Tired, not Fugazi's I'm So Tired;
    "Fugazis" was no Fugazi, and "Tied by Fugazi" named nothing to ask about."""
    rx = Receiver()
    tired, room = song("ist", "I'm So Tired", "Fugazi"), song("wr", "Waiting Room", "Fugazi")
    rx.answers = {
        "track:I'm Tired artist:Fugazi": answer([tired]),
        "I'm Tired Fugazi": answer([tired, room]),
        "I'm Tired": answer([song("lab", "I\u2019m Tired (with Zendaya) - Bonus Track", "Labrinth", "Zendaya")]),
        "track:I'm So Tired artist:Fugazis": answer([]),
        "I'm So Tired Fugazis": answer([tired]),
        "track:Tied artist:Fugazi": answer([]),
        "Tied Fugazi": answer([tired, room]),
        "Tied": answer([song("ty", "Tied Up", "Someone")]),
    }
    h = harness(rx)
    said = await h.tool("play", what="I'm Tired", by="Fugazi")
    assert said == "Playing I'm So Tired by Fugazi. Nothing of Fugazi's is called I'm Tired; I'm So Tired is the nearest.", said
    assert await h.tool("play", what="I'm So Tired", by="Fugazis") == "Playing I'm So Tired by Fugazi."
    said = await h.tool("play", what="Tied", by="Fugazi")
    assert said.startswith("Nothing on Spotify is called 'Tied' by Fugazi, so nothing was played. "
                           "Nearest: I'm So Tired by Fugazi; Waiting Room by Fugazi."), said
    assert played(rx) == ["spotify:track:ist", "spotify:track:ist"], rx.calls
    await h.close()


async def test_my_music_stays_in_his_library_and_an_emoji_is_said_by_its_name():
    rx = Receiver()
    h = harness(rx, library=True, names=["\U0001f941 = drum and bass, drum n bass"])
    await h.run("library")
    assert await h.tool("play", what="my DJ playlist") == "Playing your playlist Dj."
    assert await h.tool("play", what="my drum playlist") == "Playing your playlist \U0001f941."
    assert await h.tool("play", what="the drum emoji") == "Playing your playlist \U0001f941."
    assert await h.tool("play", what="my drum and bass playlist") == "Playing your playlist \U0001f941."
    assert await h.tool("play", what="the plane emoji") == "Playing your playlist \u2708\ufe0f."   # not the song
    said = await h.tool("play", what="my liked songs")
    assert said.startswith("The user has no playlist called 'my liked songs' in their library, so nothing was played."), said
    assert not any(c[0] == "SEARCH" for c in rx.calls), rx.calls
    assert "\U0001f941 (drum with drumsticks)" in (await h.tool("my_music"))["playlist"]
    await h.close()


async def test_a_genre_said_as_a_name_is_a_genre_and_named_artists_always_seed():
    rx = Receiver()
    h = harness(rx)
    said = await h.tool("play", what="Jazz")                      # once Queen's album Jazz
    assert said.startswith("Playing a mix from jazz,"), said
    assert [p for m, p, b in rx.calls if m == "LB"] == ["tag:(jazz)::nosim"], rx.calls
    assert await h.tool("play", what="Get Lucky") == "Playing Get Lucky by Daft Punk, Pharrell Williams."
    said = await h.tool("play", mood="chill", artists=["Air", "Justice", "Daft Punk"], like=["Frank Ocean"])
    assert said.startswith("Playing a chill mix from Frank Ocean and "), said
    assert [p for m, p, b in rx.calls if m == "LB"][-1].startswith("artist:(Frank Ocean) artist:("), rx.calls
    said = await h.tool("play", genre="drum and bass", artists=["Air", "Daft Punk"])    # his artists of other genres
    assert [p for m, p, b in rx.calls if m == "LB"][-1] == "tag:(drum and bass)::nosim", rx.calls
    try:
        await h.tool("play", mood="chill")
        raise AssertionError("a mood with nothing to start from played")
    except AssertionError:
        raise
    except Exception as e:                                          # noqa: BLE001
        assert "three to six of the user's artists" in str(e), e
    assert await h.tool("play") == "Nothing to play was named: ask the user what they would like, and suggest a mix that fits."
    await h.close()


async def test_a_mood_as_spotify_radio_needs_no_search_and_a_blind_test_takes_turns():
    rx = Receiver()
    h = harness(rx, library=True, mood_mixes="spotify_radio")
    await h.run("library")
    assert await h.tool("play", mood="chill", artists=["Daft Punk"]) == "Playing radio from Daft Punk, for chill."
    assert played(rx) == ["spotify:station:artist:dp"] and not any(c[0] in ("SEARCH", "LB") for c in rx.calls), rx.calls
    await h.close()
    rx = Receiver()
    h = harness(rx, library=True, mood_mixes="both_in_turn")
    await h.run("library")
    said = [await h.tool("play", mood="chill", artists=["Daft Punk", "Radiohead"]) for _ in range(3)]
    assert all(x.startswith("Playing a chill mix from ") and "songs" not in x and "radio" not in x for x in said), said
    log = h.rt.store["mood_log"]
    assert [e["recipe"] for e in log] == ["listenbrainz", "spotify_radio", "listenbrainz"], log
    assert log[0]["mood"] == "chill" and log[1]["seeds"], log
    await h.close()


async def test_a_slow_listenbrainz_plays_radio_and_what_was_found_or_unknown_is_kept():
    rx = Receiver()
    h = harness(rx, library=True)
    await h.run("library")
    await h.tool("play", artists=["Nobody", "Daft Punk"])            # Nobody is not known to ListenBrainz
    searches = len([c for c in rx.calls if c[0] == "SEARCH"])
    await h.tool("play", artists=["Nobody", "Daft Punk"])
    lb = [p for m, p, b in rx.calls if m == "LB"]
    assert "artist:(Nobody)" not in lb[-1], lb                       # remembered, not asked about again
    assert len([c for c in rx.calls if c[0] == "SEARCH"]) == searches, rx.calls     # its songs were kept
    rx.lb_slow = True
    assert await h.tool("play", mood="chill", artists=["Radiohead"]) == "Playing radio from Radiohead, for chill."
    assert played(rx)[-1] == "spotify:station:artist:rh", played(rx)
    rx.lb_slow, rx.refusing = False, 80056
    try:
        await h.tool("play", what="Push the Feeling On")              # Spotify starts refusing searches
    except Exception:                                               # noqa: BLE001
        pass
    assert await h.tool("play", mood="chill", artists=["Daft Punk"]) == "Playing radio from Daft Punk, for chill."
    assert [e["recipe"] for e in h.rt.store["mood_log"]][-2:] == ["spotify_radio", "spotify_radio"]   # what played
    await h.close()


def test_and_is_ampersand():
    module = Harness(HERE).ext.tools["play"].fn.__globals__
    assert module["_key"]("Demo & Cease") == module["_key"]("Demo and Cease"), module["_key"]("Demo & Cease")


def playlist(name: str, uri: str, length: int = 10, cover: str = "", mine: bool = False) -> dict:
    return {"name": name, "uri": f"spotify:playlist:{uri}", "length": length, "image_url": cover, "can_edit": mine,
            "owner_username": "someone" if mine else "spotify", "description": "", "folder": [], "collaborative": False}


async def test_his_playlists_come_from_the_receiver_spotifys_own_by_what_they_are():
    rx = Receiver()
    rx.playlists = [playlist("Discover Weekly", "dw", 30, "https://pickasso.spotifycdn.com/x/dw/cover/en"),
                    playlist("barbecue farmers market friday morning", "day", 50,
                             "https://daylist.spotifycdn.com/playlist-covers-mix/en/morning_default.jpg"),
                    playlist("DJ", "dj", 0), playlist("Winston", "win", 0, mine=True)]
    h = harness(rx, library=True)
    await h.run("library")
    assert await h.tool("play", what="my daylist") == "Playing your playlist barbecue farmers market friday morning."
    assert await h.tool("play", what="my Discover Weekly") == "Playing your playlist Discover Weekly."
    assert await h.tool("play", what="my liked songs") == "Playing your playlist Liked Songs."
    assert played(rx) == ["spotify:playlist:day", "spotify:playlist:dw", "spotify:collection:tracks"], played(rx)
    names = (await h.tool("my_music"))["playlist"]
    assert "DJ" not in names and "Running 2026" not in names, names     # empty ones out; the receiver's list wins
    await h.close()


async def test_a_mix_is_one_list_in_his_playlist_and_old_mixes_are_trimmed_after():
    rx = Receiver()
    rx.playlists = [playlist("Winston", "win", 0, mine=True)]
    h = harness(rx, library=True, mix_playlist="Winston")
    await h.run("library")
    h.ext.tools["play"].fn.__globals__["MIX_KEEP"] = 2
    await h.tool("play", artists=["Daft Punk"])
    adds = [b for m, p, b in rx.calls if p == "/library/playlists/add_tracks"]
    assert adds == [{"playlist_uri": "spotify:playlist:win", "uris": ["spotify:track:Get Lucky", "spotify:track:One More Time"]}], adds
    plays = [b for m, p, b in rx.calls if p == "/player/play"]
    assert plays[-1] == {"uri": "spotify:playlist:win", "skip_to_uri": "spotify:track:Get Lucky"}, plays
    assert not any(p == "/player/add_to_queue" for m, p, b in rx.calls), rx.calls     # nothing left in the queue
    await h.tool("play", artists=["Radiohead"])
    await asyncio.sleep(0.05)                                       # trimmed after the music starts
    removed = [b["uri"] for m, p, b in rx.calls if p == "/library/playlists/remove_track"]
    assert removed == ["spotify:track:Get Lucky", "spotify:track:One More Time"], removed
    await h.close()


async def test_he_is_remembered_liked_banned_and_not_now():
    rx = Receiver()
    h = harness(rx, library=True)
    await h.run("library")
    module = h.ext.tools["play"].fn.__globals__
    handle, ctx = module["handle_event"], h.rt.ctx
    song = lambda uri, name, *artists: {"type": "metadata", "data": {"uri": uri, "name": name, "artist_names": list(artists)}}
    nexts = lambda: [p for m, p, b in rx.calls if p == "/player/next"]

    assert await h.tool("feedback", verdict="like") == "Saved Get Lucky by Daft Punk to the user's Liked Songs."
    assert ("POST", "/library/liked", {"uris": ["spotify:track:gl"], "liked": True}) in rx.calls
    assert await h.tool("feedback", verdict="never") == "Get Lucky by Daft Punk will not play again; skipped."
    await handle(ctx, song("spotify:track:gl", "Get Lucky", "Daft Punk"))       # radio brings it back
    assert len(nexts()) == 2, rx.calls                              # skipped when banned, and when it came back

    said = await h.tool("feedback", verdict="not_now", artist="Radiohead")
    assert said.startswith("No Radiohead for now (24 hours"), said
    await handle(ctx, song("spotify:track:creep", "Creep", "Radiohead"))
    assert len(nexts()) == 3
    await h.tool("play", artists=["Radiohead", "Daft Punk"])
    assert "Radiohead" not in [p for m, p, b in rx.calls if m == "LB"][-1], rx.calls
    try:
        await h.tool("play", mood="sad", artists=["Radiohead"])
        raise AssertionError("a mood made only of who is out played")
    except AssertionError:
        raise
    except Exception as e:                                          # noqa: BLE001
        assert "out for now" in str(e), e

    await handle(ctx, song("spotify:track:omt", "One More Time", "Daft Punk"))   # unbanned song: plays on
    now = await h.tool("now_playing")
    assert [b["song"] for b in now["before"]] == ["One More Time"] and "Radiohead" in now["out_for_now"], now

    assert (await h.tool("play", what="Radiohead")).startswith("Playing Radiohead")   # asked by name: lifted
    await handle(ctx, song("spotify:track:creep2", "Creep", "Radiohead"))
    assert len(nexts()) == 3, rx.calls

    await h.tool("feedback", verdict="not_now", artist="Air")
    assert "Air" in (await h.tool("now_playing"))["out_for_now"]    # said with no music for hours: it holds
    h.rt.store["last_music"] = time.time() - 2 * 3600               # then the speakers quiet for two hours
    for a in h.rt.store["taste"]["for_now"]:
        a["at"] -= 2 * 3600
    assert "Air" not in (await h.tool("now_playing"))["out_for_now"]
    assert "Radiohead" in await h.hint_words() and "daylist" not in await h.hint_words()
    await h.close()


async def test_a_sleep_timer_fades_out_in_silence_and_the_first_music_of_the_day_starts_at_its_volume():
    rx = Receiver()
    h = harness(rx)
    module = h.ext.tools["play"].fn.__globals__
    fade_step = module["fade_step"]
    vols = lambda: [b["volume"] for m, p, b in rx.calls if p == "/player/volume" and m == "POST"]
    said = await h.tool("sleep", minutes=30)
    assert said.startswith("The music fades out over the last 30 minutes and stops in 30 minutes;"), said
    end = h.rt.store["sleep"]["end"]
    await fade_step(h.rt.ctx, end - 900)                          # half the fade left
    assert vols()[-1] == 30, vols()
    await fade_step(h.rt.ctx, end + 1)                            # over: paused, and the volume back
    assert ("POST", "/player/pause", None) in rx.calls and vols()[-1] == 60 and "sleep" not in h.rt.store, rx.calls
    await h.tool("sleep", minutes=60)                             # an hour: plays as it was for the first half
    n = len(vols())
    await fade_step(h.rt.ctx, h.rt.store["sleep"]["end"] - 3000)
    assert len(vols()) == n, vols()
    assert await h.tool("sleep", minutes=0) == "The sleep timer is off; the music plays on."
    assert "sleep" not in h.rt.store

    rx.volume = 96                                                # left loud the night before
    h.rt.store["last_music"] = time.time() - 7 * 3600
    await h.tool("play", what="daft punk")
    assert vols()[-1] == 60, vols()
    h.rt.store["last_music"] = time.time()                        # music a minute ago: as it was
    n = len(vols())
    await h.tool("play", what="daft punk")
    assert len(vols()) == n, vols()
    await h.close()


async def test_leaving_home_pauses_only_when_he_asked_for_it():
    for on, paused in ((False, False), (True, True)):
        rx = Receiver()
        h = harness(rx, pause_on_leave=on)
        await h.run("left_home")
        assert (("POST", "/player/pause", None) in rx.calls) == paused, (on, rx.calls)
        await h.close()



async def test_a_song_heard_long_enough_goes_to_listenbrainz_once_and_one_not_sent_waits_for_the_next():
    rx = Receiver()
    h = harness(rx, listens=True)
    module = h.ext.tools["play"].fn.__globals__
    handle, ctx = module["handle_event"], h.rt.ctx
    await h.status()
    listens = lambda: [b for m, p, b in rx.calls if m == "LISTENS"]
    song = lambda uri, name, ms: {"type": "metadata", "data": {"uri": uri, "name": name, "artist_names": ["Daft Punk"],
                                                               "album_name": "Discovery", "duration": ms}}
    await handle(ctx, song("spotify:track:omt", "One More Time", 200))      # counts after half: 0.1 s
    await handle(ctx, {"type": "playing", "data": {}})
    await asyncio.sleep(0.15)
    sent = listens()
    assert len(sent) == 1 and sent[0]["listen_type"] == "single", sent
    meta = sent[0]["payload"][0]["track_metadata"]
    assert meta["track_name"] == "One More Time" and meta["release_name"] == "Discovery", meta
    assert meta["additional_info"]["spotify_id"] == "https://open.spotify.com/track/omt", meta
    await handle(ctx, {"type": "playing", "data": {"resume": True}})
    await asyncio.sleep(0.15)
    assert len(listens()) == 1, listens()                           # once a song

    await handle(ctx, song("spotify:track:da", "Digital Love", 400))        # paused before it counts: not sent
    await asyncio.sleep(0.1)
    await handle(ctx, {"type": "paused", "data": {}})
    await asyncio.sleep(0.2)
    assert len(listens()) == 1, listens()
    rx.lb_status = 503                                              # resumed, it counts, ListenBrainz is down
    await handle(ctx, {"type": "playing", "data": {"resume": True}})
    await asyncio.sleep(0.15)
    assert len(listens()) == 2 and h.rt.store["lb_unsent"][0]["track_metadata"]["track_name"] == "Digital Love"
    rx.lb_status = 200                                              # the next one takes it along
    await handle(ctx, song("spotify:track:hbfs", "Harder, Better, Faster, Stronger", 200))
    await asyncio.sleep(0.15)
    last = listens()[-1]
    assert last["listen_type"] == "import" and [p["track_metadata"]["track_name"] for p in last["payload"]] == [
        "Digital Love", "Harder, Better, Faster, Stronger"], last
    assert h.rt.store["lb_unsent"] == []
    await h.close()

    rx = Receiver()                                                 # not asked for: nothing is sent
    h = harness(rx)
    module = h.ext.tools["play"].fn.__globals__
    await h.status()
    await module["handle_event"](h.rt.ctx, song("spotify:track:omt", "One More Time", 200))
    await module["handle_event"](h.rt.ctx, {"type": "playing", "data": {}})
    await asyncio.sleep(0.15)
    assert not [c for c in rx.calls if c[0] == "LISTENS"], rx.calls
    await h.close()


async def test_something_new_is_artists_like_theirs_that_they_have_not_played():
    rx = Receiver()
    rx.lb_songs = [("Daft Punk", "One More Time"), ("Justice", "D.A.N.C.E."), ("Air", "La femme d'argent"),
                   ("Radiohead feat. Someone", "Creep")]
    rx.answers["track:D.A.N.C.E. artist:Justice"] = answer(tracks=[song("dance", "D.A.N.C.E.", "Justice")])
    h = harness(rx, library=True, mood_mixes="both_in_turn")
    await h.run("library")                                          # Daft Punk and Radiohead are theirs
    (h.rt.ctx.folder / "played.jsonl").write_text(json.dumps({"at": "2026-10-16T20:00:00", "uri": "spotify:track:x",
                                                              "song": "Sexy Boy", "artists": ["Air"]}) + "\n")
    said = await h.tool("play", fresh=True, artists=["Daft Punk", "Radiohead"])
    assert played(rx) == ["spotify:track:dance"], rx.calls
    assert [b["q"] for m, t, b in rx.calls if m == "SEARCH"] == ["track:D.A.N.C.E. artist:Justice"], rx.calls
    assert "Daft Punk" in said and "Radiohead" in said and "new to the user, 1 song," in said, said
    assert not h.rt.store.get("mood_log"), h.rt.store                # not part of the blind test
    rx.lb_songs = [("Daft Punk", "One More Time")]
    try:
        await h.tool("play", fresh=True, artists=["Daft Punk"])
        raise AssertionError("played music the user knows as new")
    except AssertionError:
        raise
    except Exception as e:                                          # noqa: BLE001
        assert "one the user knows" in str(e), e
    await h.close()


async def test_the_newest_record_of_an_artist_plays_and_says_when_it_came_out():
    rx = Receiver()
    rx.found = {"artists": {"items": [{"uri": "spotify:artist:ti", "name": "Tame Impala"}]}}
    rx.records = {"album": [{"uri": "spotify:album:cur", "name": "Currents", "album_type": "album", "release_date": "2015-07-17"},
                            {"uri": "spotify:album:dead", "name": "Deadbeat", "album_type": "album", "release_date": "2025-10-17"}],
                  "single": [{"uri": "spotify:album:eos", "name": "End of Summer", "album_type": "single", "release_date": "2025-07"}]}
    h = harness(rx)
    said = await h.tool("play", what="Tame Impala", kind="album", latest=True)
    assert said == "Playing album Deadbeat by Tame Impala, released 2025-10-17.", said
    assert played(rx) == ["spotify:album:dead"] and [b["group"] for m, p, b in rx.calls if m == "RECORDS"] == ["album"]
    rx.records["single"][0]["release_date"] = "2025-11"             # a single since: the latest is the single
    said = await h.tool("play", what="Tame Impala", latest=True)
    assert played(rx)[-1] == "spotify:album:eos" and said.startswith("Playing single End of Summer"), said
    rx.found = {"artists": {"items": []}}
    assert (await h.tool("play", what="Nobody At All", latest=True)).startswith("Spotify has no artist called")
    await h.close()


async def test_an_album_named_with_its_artist_plays_when_no_song_has_the_name():
    rx = Receiver()
    rx.answers["album:Currents artist:Tame Impala"] = answer(albums=[
        {"uri": "spotify:album:cur", "name": "Currents", "artists": [{"name": "Tame Impala"}]}])
    h = harness(rx)
    said = await h.tool("play", what="Currents", by="Tame Impala")
    assert said == "Playing the album Currents by Tame Impala." and played(rx) == ["spotify:album:cur"], said
    said = await h.tool("play", what="Currents", by="Tame Impala", kind="track")    # a song was said: it says so
    assert said.endswith("No song of Tame Impala's is called Currents; this is the album."), said
    await h.close()


async def test_a_playlist_made_after_the_hourly_read_takes_mixes_once_it_is_named():
    rx = Receiver()
    rx.playlists = [playlist("Running 2026", "run", 10, mine=True)]
    h = harness(rx, library=True)
    await h.run("library")
    rx.playlists.append(playlist("Winston", "win", 0, mine=True))       # made in Spotify, then named on the card
    assert await h.configure({"library": PLAYER, "mix_playlist": "Winston"}) == []
    await h.tool("play", artists=["Daft Punk"])
    plays = [b for m, p, b in rx.calls if p == "/player/play"]
    assert plays[-1]["uri"] == "spotify:playlist:win", plays
    await h.close()


async def test_shuffle_repeat_seek_a_song_to_a_playlist_and_what_is_next():
    rx = Receiver()
    rx.playlists = [playlist("Running 2026", "run", 10, mine=True), playlist("Discover Weekly", "dw", 30)]
    rx.next_track = {"name": "Digital Love", "artist_names": ["Daft Punk"], "uri": "spotify:track:dl"}
    h = harness(rx, library=True)
    await h.run("library")
    posts = lambda path: [b for m, p, b in rx.calls if p == path]
    assert await h.tool("shuffle") == "Shuffling." and posts("/player/shuffle_context") == [{"shuffle_context": True}]
    assert await h.tool("repeat", what="song") == "Repeating this song."
    assert posts("/player/repeat_track")[-1] == {"repeat_track": True}
    assert posts("/player/repeat_context")[-1] == {"repeat_context": False}
    assert await h.tool("seek", seconds=30) == "Ahead 30 seconds."
    assert posts("/player/seek")[-1] == {"position": 30000, "relative": True}
    assert await h.tool("seek", seconds=0, relative=False) == "From the top."      # "play that again"
    said = await h.tool("feedback", verdict="like", playlist="my running playlist")
    assert said == "Added Get Lucky by Daft Punk to Running 2026.", said
    assert posts("/library/playlists/add_tracks")[-1] == {"playlist_uri": "spotify:playlist:run",
                                                          "uris": ["spotify:track:gl"]}
    assert "not the user's to change" in await h.tool("feedback", verdict="like", playlist="discover weekly")
    assert (await h.tool("feedback", verdict="like", playlist="gym")).startswith("The user has no playlist called 'gym'")
    assert (await h.tool("feedback", verdict="like", playlist="liked songs")).endswith("to the user's Liked Songs.")
    assert (await h.tool("now_playing"))["next"] == "Digital Love by Daft Punk"
    rx.playing = False
    assert (await h.tool("shuffle")).startswith("Nothing is playing to shuffle")
    await h.close()


async def test_a_new_request_ends_a_song_on_repeat_and_a_mix_plays_in_its_own_order():
    rx = Receiver()
    rx.playlists = [playlist("Winston", "win", 0, mine=True)]
    rx.modes = {"repeat_track": True, "shuffle_context": True}
    h = harness(rx, library=True, mix_playlist="Winston")
    await h.run("library")
    posts = lambda path: [b for m, p, b in rx.calls if p == path]
    await h.tool("play", what="Daft Punk")
    assert posts("/player/repeat_track") == [{"repeat_track": False}], rx.calls
    assert posts("/player/shuffle_context") == [], rx.calls          # a playlist asked for keeps their shuffle
    await h.tool("play", artists=["Daft Punk"])
    assert posts("/player/shuffle_context") == [{"shuffle_context": False}], rx.calls
    await h.close()


async def test_louder_past_spotifys_full_turns_the_speaker_itself_up():
    rx = Receiver()
    rx.volume = 100
    h = harness(rx)
    h.speaker_volume = 0.6
    said = await h.tool("volume", change="up")
    assert said.startswith("The music was at full, so the speaker itself is up to 70 of 100"), said
    assert h.speaker_volume == 0.7 and rx.volume == 100
    said = await h.tool("volume", level=100)                       # "to 100": the speaker below its top is said
    assert said == "Music volume 100 of 100. The speaker itself is at 70 of 100; turned up again, it goes up.", said
    h.speaker_volume = 1.0
    assert (await h.tool("volume", change="up")).startswith("Already as loud as it goes")
    h.speaker_volume = None                                         # no box has reported its volume
    assert "its dial turns it up" in await h.tool("volume", change="up")
    rx.volume = 60
    assert await h.tool("volume", change="up") == "Music volume 80 of 100." and h.speaker_volume is None
    assert await h.tool("volume", change="down") == "Music volume 60 of 100."   # down is the music's own
    await h.close()
