# Built-in extensions

Four extensions come with Winston, in `src/winston/extensions/`, each written against the public
SDK alone ([extensions](extensions.md)): nothing in the core knows them by name. They are off until
you switch them on, on the Extensions page or with `winston ext enable ID`.

| Extension | Speaks up | His tools | Reaches |
| --- | --- | --- | --- |
| **Hevy** (`hevy`) | "Time to train": no workout for 3 days, when you are home; firmer from day 5 | `hevy_get_training` | api.hevyapp.com |
| **News** (`news`) | "Breaking news", "Crisis alerts", "Daily digest" | `news_get_news` | Any website (the feeds you pick), the background model |
| **Reminders** (`reminders`) | "Reminders", when you asked | `reminders_add`, `reminders_upcoming`, `reminders_cancel` | Nothing outside itself |
| **Spotify** (`spotify`) | Nothing | `spotify_play`, `spotify_party`, `spotify_pause`, `spotify_resume`, `spotify_next_track`, `spotify_previous_track`, `spotify_volume`, `spotify_now_playing` | Spotify, GitHub (once, for the receiver), Home Assistant (act: your library, and party light scripts), the speakers |

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

**Parties.** The `spotify_party` tool plays a track and runs a Home Assistant light show in time with it:
the lights start the instant the music does (on go-librespot's own `playing` event, not a guessed delay)
and the room is put back when it stops. Wire it to a phrase with a [shortcut](home-assistant.md): "party
time" plays a track on the speakers, the lights hit with the first beat, and when the song ends
the room returns to how it was. One party runs at a time: a new party, or any song asked for, first ends
the one going (its light script stops and its restore scene comes on), and a party also ends when its own
song does, so the next song Spotify plays does not run under a light show. It needs the extension's Home
Assistant permission set to act, and the light script and restore scene exposed to Winston.

Not Home Assistant's Spotify integration or Music Assistant: the integration is a remote for devices
that already are Spotify Connect speakers and cannot search, and the box is not one; Music Assistant
reaches a box through Home Assistant, which would take the box's microphone from Winston.

## Vulnerabilities

Approved 2026-10-10. Built: what it needs from the standard (below); the extension itself is not built
yet. The order is in the [plan](plan.md) (Computers and vulnerabilities track).

New vulnerabilities as they come out, and whether you have them. It never patches anything. Two
sources, both free and with no key:

- **CISA's [Known Exploited Vulnerabilities](https://www.cisa.gov/known-exploited-vulnerabilities-catalog)
  catalog** (KEV): one JSON file of bugs with evidence of being exploited. It held 1,739 on 8 October
  2026, with 21 to 43 added each month this year.
- **ENISA's [EU Vulnerability Database](https://euvd.enisa.europa.eu)** (EUVD): every new CVE, with
  its score and the vendor and product it names. In the week to 10 October, 2,787 CVEs were published,
  and 329 of them were scored 9 or more.

Stories about security are the News extension's Security preset; this is the vulnerabilities
themselves.

### What is new

- **Newly exploited**: every entry CISA adds, in the briefing: what it is, whose product, and whether
  ransomware uses it. About one a day.
- **Critical, in what you follow**: a new CVE scored 9 or more whose vendor or product is something you
  have (below) or a name on the card's Follow list ("OpenSSH", "Home Assistant", "WordPress"), in the
  briefing, at most three a day, highest score first. Following everything would be about 47 a day;
  Google alone had 26 in that week. The rest wait for the tool.
- **`vulns_recent`**: what was newly exploited and newly critical in the last days (7 unless asked),
  all of it or about one vendor or product. "Any new vulnerabilities?", "what got exploited this
  week?", "anything new about Chrome?".

Both kinds are pushed, one nudge per CVE, and expire after three days, as news goes stale. A CVE you
have is said as one you have (below), never twice.

### What you have

What you own comes from where it is already known:

- **Home Assistant's devices**: the maker, model and firmware of every device it knows, exposed or not
  (needs the new `devices` permission, below).
- **Your computers**: each computer server you pick, through its `security_updates`
  ([computers](computer.md)).
- **Anything else**: a list on the card, each entry a name, a vendor, a product and optionally a
  version, for what neither knows (a router with no Home Assistant integration, a NAS, a phone).

An exploited CVE (in the catalog) matches what you have in one of two ways:

- **Sure**: it is among the CVE ids of a computer's waiting updates. You have it, and the update that
  fixes it is waiting. Level 2.
- **Likely**: the catalog's vendor is the device's maker (compared without Inc, Ltd, GmbH and the
  like), and the background model, given the catalog entry and the device's maker, model and firmware,
  says yes, this is that product. Level 1. The catalog gives no versions, so a likely match can be one
  you already patched; a device's likely match ends when its firmware changes.

Word matching alone is not enough: in the 8 October catalog, 389 of the 1,739 entries are Microsoft's,
and products read "Multiple Products" (Apple), "Multiple Routers" (TP-Link, Netgear) or "Kernel"
(Linux). So a computer's CVE ids win where its package tool gives them, and elsewhere the model judges
the product. Computers whose tool gives no ids (Ubuntu's apt, Arch, macOS, Homebrew, winget) are
matched like devices, by their OS and their waiting updates. A critical CVE that is not (yet)
exploited is news, not a match: it reaches you through the Follow kind above.

- **"Exploited, and you have it"**: a checked kind, one nudge per CVE and thing, so it ends by itself
  when the update is installed (the CVE leaves the computer's list), and "that's not mine" dismisses it
  for good. Sure matches at the next talk, likely ones in the briefing; changeable per level on the
  card. What is already in the catalog when you switch it on is listed by the tool but not said, unless
  it is a sure match.
- **`vulns_to_patch`**: everything you have with a match, sure ones first: the thing, the CVE, what it
  is (the catalog's name and description), CISA's required action, when it was listed, whether it is
  known to be used by ransomware, and for a computer the waiting update that fixes it. "Anything I need
  to patch?", "is the router affected by anything?".

### How it runs

- **Every 6 hours** it reads the catalog, asking with If-Modified-Since, so an unchanged catalog costs
  one 304, and EUVD's search for CVEs published since its last read and scored 9 or more, a hundred
  at a time. Every hour it reads each picked computer's `security_updates`, and each day Home
  Assistant's devices. It keeps 14 days of new CVEs in its folder, and both tools answer from what was
  read, so a question never waits on the web.
- **Status**: the catalog's date, and how many sure and likely matches. **Entity**: the number of
  exploited vulnerabilities you have.
- EUVD's API is young, and its documentation page needs a browser to read. If it stops answering, the
  Follow kind says so on the card and the rest works on.

```toml
permissions = { network = ["www.cisa.gov", "euvdservices.enisa.europa.eu"], devices = true, llm = true }
```

Nothing about what you have or follow leaves the house: the catalog is downloaded whole, the search
asks only for a date and a score, and the matching happens here, by the background model where it
judges, which is the local brain unless you picked a cloud one. Text from the catalog and EUVD is from
outside: it reaches his brain only as facts marked as information, and the model's answer about it is
a yes or a no.

### What this needs from the standard

Two additions, each for any extension, not for this one. Built 2026-10-10 and in
[extensions](extensions.md) (permissions, settings and `ctx`).

1. **`devices = true`**, and `ctx.devices()`: every device in Home Assistant's device registry, as
   `Device(id, name, manufacturer, model, model_id, sw_version, hw_version, area, service)`, `service`
   being true for what Home Assistant lists as a service (an add-on, Home Assistant itself). It is not part of
   `home_assistant = "read"`, which is scoped on purpose to exposed and picked entities and reads their
   states (least privilege, plan §6 rule 5). An inventory needs every device, exposed or not (routers,
   cameras and storage are rarely exposed to a voice assistant), and none of their states. Network
   addresses, serial numbers and integration ids stay out: nothing that matches a vulnerability needs
   them. A firmware update reminder, a battery list or a warranty tracker would use the same
   permission. winstond already reads the registry for the map of the house.
2. **Picking another extension's tool in settings**: `Extensions(tool="security_updates")`, a picker of
   the extensions and MCP servers that are switched on and have a read tool of that name. Picking one is
   consent for this extension to call that tool through `ctx.ext(id).call(...)`, as picking an entity is
   consent to use it. `ctx.ext` then reaches an MCP server's read tools too (today only an SDK
   extension's), by the tier you set on that server's card. The manifest's `extensions = [...]` cannot
   do this: it names ids fixed when the extension is written, and computers get their names when you
   add them. A backup monitor over each computer's `logs`, or a disk-space nudge over each `status`,
   would use the same picker.

## With his brain

Checked with the 27B through the voice channel's brain (`tests/extensions_brain.py`, Reminders and
News against the real feeds): "Remind me to call Mum at six" set it for 18:00 and he said so; "every
weekday at half past seven" first landed on 19:30, which he saw in the answer and moved to the
morning himself; the reminders were listed, and "cancel the one about Mum" cancelled that one; "what's
in the news?" and "anything about Sweden?" were answered in English from `news_get_news`, the second
with the topic Sweden.
