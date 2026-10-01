# Built-in extensions

Four extensions come with Winston, in `src/winston/extensions/`, each written against the public
SDK alone ([extensions](extensions.md)): nothing in the core knows them by name. They are off until
you switch them on, on the Extensions page or with `winston ext enable ID`.

| Extension | Speaks up | His tools | Reaches |
| --- | --- | --- | --- |
| **Hevy** (`hevy`) | "Time to train": no workout for 3 days, when you are home; firmer from day 5 | `hevy_get_training` | api.hevyapp.com |
| **News** (`news`) | "Breaking news", "Crisis alerts", "Daily digest" | `news_get_news` | Any website (the feeds you pick), the background model |
| **Reminders** (`reminders`) | "Reminders", when you asked | `reminders_add`, `reminders_upcoming`, `reminders_cancel` | Nothing outside itself |
| **Spotify** (`spotify`) | Nothing | `spotify_play`, `spotify_pause`, `spotify_resume`, `spotify_next_track`, `spotify_previous_track`, `spotify_volume`, `spotify_now_playing` | Spotify, GitHub (once, for the receiver), your library in Home Assistant (read), the speakers |

Each has its tests beside it (`winston ext test hevy`, on made-up answers and the fake clock), run by
`winston check unit`.

## Hevy

Your workouts from [Hevy](https://www.hevyapp.com). Needs Hevy Pro and its API key, from
hevy.com/settings?developer: paste it into the card's key field (stored in `data/.env` as
`HEVY_API_KEY`); it is tried on save. Read only.

- **Every hour** it keeps a copy of the last 120 days of workouts: the first time by paging through
  them, then only what changed since the last poll. Hevy asks not to poll exactly on the hour, and it
  does not: the hour counts from when it started.
- **"Time to train"**: no workout for `rest_days` (3), said when you get home; after `firmer_after` (2)
  more days, firmer. It is about the gap since your last workout, so "I know, I'm training tomorrow,
  remind me after work" follows that gap, and a workout logged meanwhile ends it without a word.
- **`hevy_get_training`**: days since the last workout, workouts this week and month, the last workout,
  and for an exercise ("how was my bench press?") its sets last time and the best set logged.
- **Status**: the last workout. **Entities**: days since the last workout, workouts this week.
  **Hint words**: your exercise names, so "Romanian deadlift" is heard right.

## News

Feeds you pick, read every 15 minutes (a feed that keeps an ETag or a date answers "not modified"),
seven days of items in a SQLite file in its folder, and the same story grouped across sources by the
words of its headline and lead.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="images/extensions-news-dark.png">
  <img src="images/extensions-news-light.png" alt="The News card: four preset switches, your own feeds, topics, a county, breaking stories a day, its tool and kinds" width="720">
</picture>

- **Presets**, each a switch: Sweden in English (The Local, Radio Sweden), Swedish outlets (SVT, Ekot,
  DN, SvD, Aftonbladet, Expressen; he tells you in English), World (BBC, The Guardian, Al Jazeera, NPR,
  DW, France 24), Tech (Ars Technica, The Verge, TechCrunch, Hacker News) and Security
  (BleepingComputer, The Hacker News, Krebs on Security, The Record, SecurityWeek, Dark Reading, The
  Register). **Your feeds**: any RSS or Atom URL, with who publishes it, a region and a topic.
- **Topics**: what you care about; a story about any of them counts. Stories about them come first,
  and they count when deciding what is breaking. A story is about a topic when its words say so or a
  feed that carries it is about it: with "Sweden", every story from a Swedish outlet counts, in Swedish
  too.
- **Breaking news** (as soon as allowed): a story three or more independent sources ran within 90
  minutes (two feeds of one publisher count once), which the background model then judges, yes or no,
  worth interrupting for given your topics; at most `breaking_per_day` (2). What is already there on
  the first read, or when a feed is added, is never breaking.
- **Crisis alerts**: pick your county, and Krisinformation.se's news for it, in English, and every VMA
  for it or for all of Sweden are said as they come; a VMA is urgent, so it is said in quiet hours too.
- **Daily digest** (the briefing, your first conversation of the day): the background model picks it
  from the day's 25 leading stories, at most once an hour until it is said: what matters most, not a run
  of crime and accidents, never two stories about one event. Each of your topics with a story that day
  gets one, whatever the model picked; without the model, each topic's best story comes first. On
  2026-09-30 the plain ranking gave five Swedish crime and accident stories and no security story; the
  model's pick for the same day gave the government talks, a Cisco zero-day, Swedish jets in NATO's
  mission over Poland, Iran and the US, and the last US troops leaving Iraq.
- **Following up**: "tell me more" gives every outlet's report; "send me the links" sends up to three
  to your phone as buttons (Discord without a phone).
- **`news_get_news`** answers from what was read, so a question never waits on the web: the main stories
  of the last day, or those about a topic ("anything about Sweden?").

Feed text is from outside: it reaches his brain only as facts marked as information, and the
background model's answer about it is a yes or a no.

## Reminders

"Remind me to call Mum at six", "every weekday at 7:30, remind me to take my vitamins", "what reminders
do I have?", "cancel the one about Mum". Each time a reminder is due is a nudge of a kind you asked
for, so it comes at its time through quiet hours and the daily limit, can be put off ("remind me in ten
minutes"), and survives a restart. A repeating one (daily, weekdays, weekly) keeps its time on the
clock across daylight saving and moves on once said. The same reminder asked for twice is set once.
**Entity**: the next reminder, as a timestamp.

## Spotify

Spotify on the speakers: the box in the room you are in, and whatever is plugged into its jack. It runs
[go-librespot](https://github.com/devgianlu/go-librespot) 0.10.2, an open Spotify Connect receiver
(downloaded once from GitHub and checked against the release's checksum), so a speaker named Winston
shows up in the Spotify app. By voice: "play Daft Punk", "play my running playlist", "pause", "next
song", "louder", "what's playing?". It needs Spotify Premium.

1. Switch it on. The card shows a code: open spotify.com/pair and enter it, once.
2. Under **Your library from**, pick Home Assistant's Spotify player. Your playlists, saved albums, top
   and followed artists, and liked and top songs are read from it hourly, and he plays them by name
   without a search.
3. For everything else, make an app at developer.spotify.com (Web API; Spotify wants a redirect URI,
   `http://127.0.0.1:8700/callback` will do, it is never used) and put its client ID and secret on the
   card, or in `data/.env` as `SPOTIFY_CLIENT_ID` and `SPOTIFY_CLIENT_SECRET`. Without them he plays
   from your library only, and says so.

Spotify gives such receivers its 320 kbps stream; the box gets it as 48 kHz 16-bit stereo FLAC
([Voice PE](voice-pe.md#music)). Pause, play and skip in the app or by voice, and the box follows: a
pause stops it at once, and Spotify goes back by what the box had not played yet, so nothing is lost.

Not Home Assistant's Spotify integration or Music Assistant: the integration is a remote for devices
that already are Spotify Connect speakers and cannot search, and the box is not one; Music Assistant
reaches a box through Home Assistant, which would take the box's microphone from Winston.

## With his brain

Checked with the 27B through the voice channel's brain (`tests/extensions_brain.py`, Reminders and
News against the real feeds): "Remind me to call Mum at six" set it for 18:00 and he said so; "every
weekday at half past seven" first landed on 19:30, which he saw in the answer and moved to the
morning himself; the reminders were listed, and "cancel the one about Mum" cancelled that one; "what's
in the news?" and "anything about Sweden?" were answered in English from `news_get_news`, the second
with the topic Sweden.
