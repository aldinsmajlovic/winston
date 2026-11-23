# Spotify

The Spotify extension plays music on the speakers: the box in the room you are in, and whatever is
plugged into its jack. You ask by voice ("play Daft Punk", "something chill", "next song", "louder",
"who is this?") or use the Spotify app as usual. It needs Spotify Premium. It is one of the
[built-in extensions](built-in-extensions.md), off until you switch it on.

## How it works

- **A receiver of its own.** It runs [go-librespot](https://github.com/devgianlu/go-librespot) 0.10.4,
  an open Spotify Connect receiver, downloaded once from GitHub and checked against the release's
  checksum. A speaker named Winston shows up in the Spotify app.
- **One action for anything to hear.** **Play music** (`spotify_play`) takes what you said in its
  fields: a name, its artist, a genre, a mood, artists you named as the kind you want, "next",
  something new, the newest record. The code decides how to play it, so the brain cannot pick the
  wrong tool.
- **He knows what plays.** While something plays, every message he gets says so in its stamp
  ("spotify: playing Oh Dove by Men I Trust"), so "who is this?" or "not this one" knows the song, and
  a request to play is music only, never a light scene.
- **The sound.** Spotify gives such receivers its 320 kbps stream (**Stream quality**: 160 or 96 kbps
  for a slow or metered connection). The box gets it as 48 kHz 16-bit stereo FLAC
  ([Voice PE](voice-pe.md#music)).
- **The app and the box agree.** Pause, play and skip in the app or by voice, and the box follows. A
  pause stops it at once, and Spotify goes back by what the box had not played yet, so nothing is lost.

It is not Home Assistant's Spotify integration or Music Assistant. The integration is a remote for
devices that already are Spotify Connect speakers and cannot search, and the box is not one. Music
Assistant reaches a box through Home Assistant, which would take the box's microphone from Winston.

## Setting it up

1. Switch it on and save. Its status line shows a code: open spotify.com/pair and enter it, once.
2. Under **Your Spotify account in Home Assistant**, pick Home Assistant's Spotify player. Your saved
   albums, top and followed artists, and liked and top songs are read from it hourly, and he plays
   them by name without a search.
3. For everything else, make an app at developer.spotify.com (Web API). Paste its client ID and secret
   into **Client ID** and **Client secret** under Keys, or put them in `data/.env` as
   `SPOTIFY_CLIENT_ID` and `SPOTIFY_CLIENT_SECRET`. Without them he plays from your library only, and
   says so.
4. Under **Your whole library**, add the address it shows
   (`http://127.0.0.1:8700/api/accounts/callback` on the default port) to the app's redirect URIs in
   Spotify's dashboard, and press Connect. He then reads all your liked songs, your top and recent ones
   and your own playlists, with their artists. `SPOTIFY_ACCOUNT` in `data/.env` holds the sign-in.
5. For mixes, make a free account at [ListenBrainz](https://listenbrainz.org) and put the token from
   its settings page into **ListenBrainz token**, or in `data/.env` as `LISTENBRAINZ_TOKEN`. It needs
   the app from step 3.

When winstond listens beyond this machine (`control.host: 0.0.0.0`), the page sets no keys: run
`winston ext connect spotify` in a terminal on this machine for step 4 instead.

All three keys are optional, so none of them shows Not set. Your playlists come from the receiver
itself, Spotify's own among them: Discover Weekly, your daylist (also by that word, whatever Spotify
named it today) and a Blend (by that word too), plus Liked Songs. An empty one that is not yours (the
DJ's) is left out.

## Settings

Keys are under `extensions.spotify.settings` in config.yaml.

| On the page | Key | Default | What it does |
| --- | --- | --- | --- |
| Speaker name in Spotify | `name` | Winston | The speaker's name in the Spotify app |
| Your Spotify account in Home Assistant | `library` | None | Home Assistant's Spotify player: your library by name, read hourly |
| Client ID, Client secret | `client_id`, `client_secret` | Not set | Your app's keys, to search all of Spotify |
| Your whole library | `account` | Not connected | Signs in with your app, for every song you like, play and keep in playlists |
| ListenBrainz token | `listenbrainz_token` | Not set | Mixes for a mood or a genre |
| Your names for playlists | `names` | Empty | Your own words for a playlist, album or artist |
| Playlist for mixes | `mix_playlist` | None: Spotify's queue | Keeps each mix in one playlist ([a mix is one list](#a-mix-is-one-list)) |
| A mood plays as | `mood_mixes` | A ListenBrainz mix | Or Spotify's radio, or both in turn ([mixes](#mixes-for-a-mood-or-a-genre)) |
| When you say not now: Keeps the artist out for | `not_now_hours` | 24 h | How long "not this artist" lasts |
| When you say not now: Or until the music has been off for | `quiet_resets_hours` | 1 h | Ends it sooner after a quiet spell |
| Sleep fade | `sleep_fade_minutes` | 30 min | How long a sleep timer fades the music |
| First music of the day: Volume | `first_volume` | 60% | The volume the day's first music starts at; 0 leaves it as it was |
| First music of the day: After the music has been off for | `first_after_hours` | 6 h | What counts as the first music of the day |
| Send what plays to ListenBrainz | `listens` | Off | Sends what the speakers play to your public ListenBrainz profile |
| Pause when you leave | `pause_on_leave` | Off | Pauses when your person under Speaking up leaves home |
| Stream quality | `bitrate` | 320 kbps | 160 or 96 for a slow or metered connection |

Config.yaml only ([running](running.md#settings-only-in-configyaml)): the variables the keys are
stored under, and the receiver's two ports on 127.0.0.1, `api_port` (3678, its API) and `stream_port`
(3679, the audio the speakers fetch). Change the ports only for a clash.

## What he can do

| Action | Name for the brain | You say |
| --- | --- | --- |
| Play music | `spotify_play` | "play Daft Punk", "something chill", "play Get Lucky next" |
| Pause music | `spotify_pause` | "pause" |
| Resume music | `spotify_resume` | "resume", "bring the music back" |
| Skip to the next song | `spotify_next_track` | "next song" |
| Go back a song | `spotify_previous_track` | "previous song" |
| Shuffle the music | `spotify_shuffle` | "shuffle" |
| Set repeat | `spotify_repeat` | "put this song on repeat" |
| Move within the song | `spotify_seek` | "skip ahead 30 seconds", "play that again" |
| Set the volume | `spotify_volume` | "louder", "turn it down" |
| Check what is playing | `spotify_now_playing` | "who is this?", "what's next?" |
| Rate the music | `spotify_feedback` | "I like this", "not Elliott Smith", "add this to my running playlist" |
| Set a sleep timer | `spotify_sleep` | "stop the music in 30 minutes" |
| Start a party | `spotify_party` | Only from a shortcut ([parties](#parties)) |
| Read your music library | `spotify_my_music` | "what playlists do I have?" |

## What plays when you name something

What plays is what you named. When you name something, he tries in this order:

1. **Your library**, by its whole name or by one of your names for it (**Your names for playlists**).
2. **With "my" or "emoji"**, a playlist, album or artist of yours whose name has all your words. "My
   ..." never leaves your library: when nothing there has the name, he says so and names your
   playlists.
3. **A genre**: a name MusicBrainz lists as a genre, when nothing in your library has it, plays a
   [genre mix](#mixes-for-a-mood-or-a-genre).
4. **All of Spotify, by the exact name** (this needs the app from step 3). A song plays only when its
   title is the name, with numbers as words. An artist plays only when it is that artist.
5. **An album or a playlist** only when you said "the album" or "the playlist", then the first whose
   name has all your words. Never a stranger's playlist that happens to have the words.
6. **A song and an artist with the same name**: your artist's song wins. Then the artist, if
   Spotify's songs for the name are the artist's own. Else the song.
7. **Nothing called that**: he gets the nearest names and asks, or plays a mix for a mood or a genre.
   Nothing that only carries the words plays.

A song with its artist ("One by U2") must be by that artist, allowing what speech to text and memory
get wrong. He tries, in this order:

1. That song by that artist, or by an artist a letter off ("Fugazis").
2. An album of the artist's with that name.
3. The artist's song with every word you said, and he says its real title.
4. Spotify's song of that title by someone else, and he says whose it is.
5. Nothing fits: he names the artist's songs Spotify found and asks.

| You say | What plays |
| --- | --- |
| "play Daft Punk" | The artist, since Spotify's songs for the name are Daft Punk's own |
| "play Wonderwall" | Oasis's song, not a band called Wonderwall |
| "play Seven Seconds" | The song 7 Seconds |
| "play my drum playlist", "the drum emoji" | Your playlist named with a drum emoji |
| "play my running playlist", with no such playlist | Nothing; he says so and names your playlists |
| "play jazz" | A jazz mix, not Queen's album Jazz |
| "play drum and bass" | A drum and bass mix, never a song called "Drum and Bass Father" |
| "play the album Random Access" | Random Access Memories |
| "play One by U2" | U2's One |
| "play I'm Tired by Fugazi" | Fugazi's I'm So Tired, said by its real title |
| "play Currents by Tame Impala" | The album Currents |
| "play Big Bad John by Johnny Horton" | Jimmy Dean's song, and "Big Bad John is by Jimmy Dean, not Johnny Horton" |
| "play the new Tame Impala album" | The artist's newest album, said with its release date |
| "play Fred again..'s latest" | The newest album or single (`latest`) |
| "play Get Lucky next" | Get Lucky after the song playing; now, when nothing plays |

The newest record comes from Spotify's list of the artist's records, so it needs the app too. A song
asked for next goes into Spotify's queue.

## Mixes for a mood or a genre

A mood ("something chill", "something upbeat") is a mix from your own artists:

- He picks three to six artists from your library that fit it, the best fit first. The mix starts
  from two of the first three. Artists you named ("something like Frank Ocean") always seed it.
- The seeds start ListenBrainz's [LB Radio](https://troi.readthedocs.io/en/latest/lb_radio.html) in
  easy mode: their songs and songs by artists like them, a different list every time.
- Each song is found on Spotify by its title and artist, and one whose best match is by someone else
  is left out. The first plays and the rest are queued, about 20 songs.
- The last eight seeds are remembered. Of the three that fit best, a mix starts from those not used
  lately, else from those used longest ago, so the same artists take turns.

A genre ("some drum and bass") comes from the genre's MusicBrainz tag alone, with any artists you
named: songs tagged with it, not with similar tags. A genre always uses ListenBrainz, whose radio
follows a tag.

### How songs are found

- An artist MusicBrainz does not know by that name is skipped for the next, and remembered, so later
  mixes do not ask about it again.
- A song's credits count by their first artist ("Fred again.. x Brian Eno"), "&" is "and", and one
  song comes once.
- Songs already found on Spotify are kept in `songs.json` in the extension's folder, so a song that
  comes back in a later mix costs no search. Spotify gives the app a small daily quota.
- When ListenBrainz does not answer within 10 s, or Spotify is refusing searches, a mood plays
  Spotify's radio from one of your artists instead, which needs no search.

### Radio instead, or both in turn

**A mood plays as** picks how:

| Choice | Key | What plays |
| --- | --- | --- |
| A ListenBrainz mix from your artists | `listenbrainz` | The mix above |
| Spotify's radio | `spotify_radio` | Radio from one of your artists that fit, shaped by your listening, with no searches at all |
| Both, taking turns | `both_in_turn` | One then the other, said the same way, as a blind test |

For the blind test, which was which is logged in the extension's store (`mood_log`, with the time, the
mood and the seeds). Without a ListenBrainz token he plays Spotify's radio from one artist that fits.

LB Radio's `stats` and `recs` modes are not used: they need your listening history on ListenBrainz,
which is [sent from here](#sending-what-plays-to-listenbrainz) and worth trying once a few weeks of it
are in.

### A mix is one list

With **Playlist for mixes** set, picked from the playlists you can edit, a mix is one list. Make an
empty one in Spotify once. Saving the setting reads your library again, so a playlist made a minute
ago is found at once, not at the next hourly read.

Each mix is added to the playlist's end and played from its first new song. So what you ask for next
replaces it, previous goes back within it, and the oldest songs beyond the last 60 are removed while
the music plays.

With None, a mix goes into Spotify's queue, which go-librespot cannot clear: its songs follow
whatever you ask for next.

## Something new, something familiar

**Something new.** "Play something new" or "something I've never heard" (`fresh`) is a mix of artists
like yours that you have not played: ListenBrainz's list without your library's artists, the artists
of all your songs, and any that played here. They are left out before a song is looked up, so no
search is spent on them. A plain mood keeps some of your own artists in it; "some new ones" leaves
them out. It needs ListenBrainz: when that does not answer, or Spotify is refusing searches, he says
so instead of playing radio of artists you know.

**Something familiar.** "Something familiar", "songs I know" or "not these new songs" (`familiar`)
plays your library's own songs by the artists that fit, shuffled, as one list, never artists like
them.

- Said while a mix plays, without artists, it keeps to that mix's artists.
- With nothing playing and no artists, it is all of your songs, 50 of them in a list.
- When fewer than five of your songs are by those artists, the first of them plays as an artist:
  Spotify's own order of their popular songs, which needs no search.

Where your songs come from:

| Your account | Songs he knows |
| --- | --- |
| Connected (**Your whole library**) | All your liked songs, your top ones over six months and over years, and the songs of your own playlists, once a day; your newest 50 liked and last 50 played, each hour. Kept as `known` in the extension's store. Never the playlist mixes go to, whose songs are new to you. |
| Not connected | Home Assistant's list: your newest 48 liked and top 48 only, without artists. Each song's artists are looked up once from your app and kept (`track_artists`), up to 20 each time the library is read. |

## Correcting him

A correction is a request. "Not that" or "that's not calm" while a mix plays plays again with other
artists that fit better; "not these new songs" plays something familiar.

Such a request right after one that acted thinks first, as a request about the home does
([when he thinks](architecture.md#when-he-thinks)). A reply with a question anywhere in it keeps the
box listening ([Voice PE](voice-pe.md)).

## Controls

- **Stop.** "Stop" to the box while it plays pauses Spotify and ends the conversation
  ([Voice PE](voice-pe.md#ending-a-conversation-and-the-button)).
- **Pause, resume, skip** read the receiver first, so with nothing loaded he says so instead of
  "Playing."
- **On what plays**: "shuffle", "put this song on repeat" (or all of it, or off), "skip ahead 30
  seconds", "back ten seconds" and "play that again" (from the top).
- **Add to a playlist**: "add this to my running playlist" adds the song playing to a playlist of
  yours (`spotify_feedback` with a playlist).
- **What's next**: he names the next song, which the receiver knows with its track cache on
  (`metadata.enabled`).
- **The queue**: a queued song cannot be taken out again (go-librespot has no call for it), and he
  says so to "clear the queue".

Repeat and shuffle stay as set, as in Spotify's apps, with two exceptions. Whatever you ask for next
ends a song on repeat. A mix plays in its own order, since shuffle left on would bring in the older
mixes in its playlist.

**Volume.** "Louder" raises Spotify's own level, which is the music's alone. Once that is at full, the
box itself goes up a step (two clicks of its dial), which makes Winston's voice louder too. "Turn it
down" lowers the music again, never the voice.

## Rating and banning

**Rate the music** (`spotify_feedback`) keeps what you think of it:

| You say | What happens |
| --- | --- |
| "I like this" | The song is saved to your Liked Songs |
| "Never play this again" | The song is banned: skipped whenever it comes on, from radio, a mix or a list |
| "Not Elliott Smith" | The artist is out for a while: 24 hours, or until the speakers have been quiet for an hour, whichever comes first |
| "Never play Elliott Smith" | The artist is banned |
| "Play Elliott Smith", "you can play him again" | The artist is back |

A mix leaves out who is out. When it is not clear whether you mean the song or the artist ("this
sucks"), he asks.

What played is logged (`played.jsonl` in the extension's folder), so "what was the song before this?"
and "that song from earlier" have an answer. Your artists' and playlists' names, with your own words
for them, are hint words for speech to text.

## Bedtime and morning

- **Sleep timer.** "Stop the music in 30 minutes", "turn it off in an hour" or "something to sleep
  to" sets one (**Set a sleep timer**, `spotify_sleep`). He says good night then. The music fades out
  over the last 30 minutes (**Sleep fade**) and pauses with nothing said, and the volume goes back for
  the next time. The timer is kept in the extension's store, so a restart in the middle carries on.
- **First music of the day.** After six hours without any, music starts at 60%.
- **Pause when you leave.** When switched on, the music pauses as Home Assistant's person under
  Speaking up leaves home.

## Sending what plays to ListenBrainz

With **Send what plays to ListenBrainz** on, a song the speakers play for half its length or four
minutes goes to your profile once. Your listens there are public. One that could not be sent is kept
(the newest 500) and goes with the next.

go-librespot tells Spotify nothing about what it plays (its event sender is a stub). So the box's music
is missing from Spotify's history and recommendations, and from ListenBrainz's import of Spotify.
Connecting Spotify on ListenBrainz adds what you play elsewhere without counting the box twice.

## When another device takes it

Spotify can move the music to another device: picked in the app, or pulled there by Spotify's own
Android app, which can take it when a headset connects. The box stops with it, and the extension's
log names the device. When the box was playing the music, he says where it went, once (**Music moved
to another device**: on the speakers, while you are home, within two minutes).

"Resume", "play it here" or "bring the music back" then plays it here again:

- the song that device plays now (from Home Assistant's Spotify player, when it is set),
- in the playlist, album or artist it came from,
- from where that device is, while it is still the same song.

A radio comes back as that song, and Spotify's autoplay carries on after it. The music is played
afresh, not handed back: sending a radio the phone took back to the speakers from the app fails in
go-librespot (`unsupported context type: -`).

## Parties

**Start a party** (`spotify_party`) plays a track and runs a Home Assistant light show in time with
it. Only a shortcut runs it; he is not offered it himself. Wire it to a phrase with a
[shortcut](home-assistant.md#a-shortcut-to-any-tool): "party time" plays a track on the speakers, the
lights hit with the first beat, and when the song ends the room returns to how it was.

- The lights start the instant the music does, on go-librespot's own `playing` event, not a guessed
  delay.
- One party runs at a time. A new party, or any song asked for, first ends the one going: its light
  script stops and its restore scene comes on.
- A party also ends when its own song does, so the next song Spotify plays does not run under a
  light show.
- It needs the extension's Home Assistant permission set to act, and the light script and restore
  scene exposed to Winston.

## When Spotify refuses searches

Spotify limits how many searches the app may make. When it refuses them, he says until when and does
not ask again until then, and your library still plays by name.
