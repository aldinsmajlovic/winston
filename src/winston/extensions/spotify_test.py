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


def harness(rx: Receiver, keys: bool = True, library: bool = False, names: list[str] | None = None) -> Harness:
    return Harness(HERE, settings={**({"library": PLAYER} if library else {}), **({"names": names} if names else {})},
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
        assert "not in his Spotify library" in str(e) and "client ID and secret" in str(e), e
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
        assert "Radio from one of his artists still plays" in str(e) and not any(c[0] == "LB" for c in rx.calls), e
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
    assert await h.tool("volume", level=150) == "Music volume 100 of 100."
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
    assert said.startswith("He has no playlist called 'my liked songs' in his library, so nothing was played."), said
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
        assert "three to six of his artists" in str(e), e
    assert await h.tool("play") == "Nothing to play was named: ask him what he would like, and suggest a mix that fits."
    await h.close()

