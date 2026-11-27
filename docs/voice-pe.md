# Voice PE room boxes

Home Assistant's Voice Preview Edition (Voice PE) as Winston's microphone and speaker in a room: setting
one up, how a request goes, what ends one, Winston's own firmware, and getting "Okay Winston" to wake
from across the room. What a room shares with the screens in it is on [rooms](rooms.md).

## Why it fits

- **A good microphone and speaker for about 60 dollars**: two microphones behind an XMOS XU316 with
  hardware echo cancellation, a mute switch, a dial, an LED ring and a 3.5 mm jack for a better speaker.
- **Cheaper and smaller than the plan's satellite**, a Raspberry Pi with a conference speakerphone, and
  no Linux audio plumbing.
- **It overlaps Home Assistant's Assist only on the voice.** Home Assistant stays the hands (Winston
  uses its MCP server), Assist stays what it is, and Winston is the voice on top.

## Setting one up

The wizard does it all for a box on USB-C. By hand there are two ways in, and without Home Assistant is
the quicker one.

### From the wizard

On a computer set up with `winston setup`, plug the box in with USB-C. The wizard's A room box step
([setup](setup.md#the-steps)) then:

1. puts Winston's firmware on the box, from Winston's release. This erases the box, its key included.
2. gives the box Wi-Fi over Improv Serial, from the networks it sees.
3. checks that Winston reaches it there and gives it a key of his own, kept in `data/.env` as
   `VOICE_PE_KEY_` and the end of its MAC (`VOICE_PE_KEY_0A1B2C`).
4. adds it as a room box, with Listen through room boxes on.
5. with Use it in Home Assistant too on (the default when Home Assistant is connected), gives Home
   Assistant the same key and switches off its voice assistant and firmware update for the box
   ([sharing it](#sharing-it-with-home-assistant)). Off, Home Assistant's old entry for the box is
   disabled.

A box already on the network is added with Add a box, in Settings, Ways to talk, Room boxes.

### Without Home Assistant

A box that was never adopted has no key, so Winston needs none and nothing else takes the microphone. The
wizard gives a box a key; this way leaves it without one, open to anything on the network.

1. Plug the box into the Winston machine with USB-C. It shows up as `/dev/ttyACM0` (Espressif USB
   JTAG/serial).
2. Give it Wi-Fi over that cable with Improv Serial, for example from https://web.esphome.io
   ("Connect", then "Configure Wi-Fi"). Its serial log then prints its IP address.
3. In Settings, Ways to talk, Room boxes, use Add a box and leave Encryption key empty. Switch on
   Listen through room boxes and save.
4. When Home Assistant offers the discovered box, ignore it. Adopting it gives the box a key and makes
   Home Assistant the client that takes the microphone.

### With Home Assistant

Only one client can hold a box's voice assistant, first come first served. While Home Assistant's
voice assistant for the box is on, Winston gets nothing. Both can use the box when it is off there
([sharing it](#sharing-it-with-home-assistant)).

1. Set the box up with the Home Assistant app as usual (Wi-Fi, adoption, firmware update) and talk to
   Assist once, so the hardware is known good.
2. Get its API encryption key. Home Assistant makes it during adoption and keeps it in
   `.storage/core.config_entries`, as the ESPHome entry's `noise_psk`. Read it with the File editor or
   SSH add-on.
3. In Home Assistant, disable the box's Assist satellite entity (Settings, Devices, ESPHome, the box),
   so Winston gets the microphone, or the whole ESPHome entry. The LEDs, dial and button keep working,
   since they run on the box.
4. In Settings, Ways to talk, Room boxes, use Add a box: its room, its address, the encryption key (kept
   on this computer and never shown again) and its wake word. Switch on Listen through room boxes and
   save. Saving starts the room boxes, or offers to restart them when they already run, and the box's
   row then says Connected.

By hand, the same goes in `data/config.yaml`, with the key in `data/.env` (`VOICE_PE_KEY=...`); then
restart Winston. `winston status` lists the satellite, and its log says when a box connects.

```yaml
channels:
  satellite:
    enabled: true
    devices:
      living room: {host: 192.168.1.50, key_env: VOICE_PE_KEY, wake_word: hey_jarvis}
```

### Sharing it with Home Assistant

The box can be Winston's room box and Home Assistant's speaker at once: Home Assistant plays sounds,
text to speech and media on it, and Winston keeps the microphone. Both need the box's key. The wizard
sets this up after it gives the box his key:

- **Home Assistant had the box:** its old key no longer matches, so Home Assistant asks for the box's
  key again (a reauthentication prompt), and Winston answers it. A prompt left from before the flash is
  deleted first, and a disabled entry is enabled.
- **Home Assistant never had it:** Winston adds the box there with the key, after deleting Home
  Assistant's own discovery of it.
- **Then, on Home Assistant's side:** the box's Assist satellite is switched off, so Winston gets the
  microphone, and its firmware update too, which would put the stock firmware back.

Home Assistant must reach the box on TCP 6053, and the box must reach Home Assistant on its own port
(8123) to fetch what Home Assistant plays. When Home Assistant does not ask for the key within 30 s, the
step says so and goes on with the box saved.

### The network

Winston connects to the box, and the box fetches what it plays from Winston. With the box on its own
VLAN, the router and this machine's firewall both need a rule for each port.

| Port | From, to | What for | Key in config.yaml |
| --- | --- | --- | --- |
| TCP 6053 | This machine to the box | The box's ESPHome API | `port` on the box's entry |
| TCP 8702 | The box to this machine | Replies, announcements and music, on every interface | `channels.satellite.http_port` |
| TCP 3232 | This machine to the box | Firmware updates over the network | none |

For example, with firewalld:

```sh
firewall-cmd --permanent --zone=public --add-rich-rule='rule family="ipv4" source address="<box subnet>" port port="8702" protocol="tcp" accept'
```

The box reaches this computer at `channels.satellite.public_host`. Unset, Winston finds it from the
route to each box. Set it when the boxes hear you but play nothing, on a VPN, under Docker or with
several network interfaces. These three are config.yaml only
([running](running.md#settings-only-in-configyaml)).

## Talking to it

Say the wake word, then the request. The box hears its wake word itself and then streams its microphone
to Winston, who decides when you have finished, answers, and hands the box a link to play the reply
from while he is still speaking.

```mermaid
sequenceDiagram
  participant You
  participant Box as Voice PE
  participant W as Winston
  You->>Box: "Okay Winston"
  Box->>W: wake, then its microphone
  W->>Box: run start, with the reply's link
  Note over W: brings the room's conversation up to date
  You->>Box: the request, then a pause
  Note over W: transcribes and judges the pause
  W->>Box: end of speech, the box stops its microphone
  W->>Box: start fetching, at his first sentence
  Box->>W: fetches the reply on TCP 8702
  Box->>You: plays it as he speaks
  W->>Box: listen on, without the wake word
```

- **The conversation is ready before the transcript.** A wake starts bringing the room's conversation
  up to date, rebuilt and warmed when it went stale, while you speak. The request waits for it only if
  it is not done.
- **An idle box keeps the next one warm.** Every minute it checks whether a new conversation's prompt
  changed (the diary after midnight, a remembered fact, an extension's tools) and builds and warms one,
  unless someone is talking on any channel. When the brain's server comes back (a restart, a model
  switch), it warms the conversation the box has. Ready before the first question (`llm.warm_up`) off
  warms nothing.
- **A request starts with a quarter of a second of voice**, so the wake word's own tail is not taken for
  it (that tail once ended 8 of 95 wakes during music with nothing heard).
- **It gives up after 8 s** with no speech after the wake word, and a request stops at 60 s.
- **The Talk page shows him speaking while the box plays.** The box plays his reply from its first
  audio, back to back, so Winston knows when each part is heard: speaking when it starts, how loud he is
  every 50 ms for his mark, and ready once it has played. How late the box starts playing is
  `PLAYS_AFTER_SECS` in `channels/satellite.py`; the log says "the box began playing ... ms after his
  first audio" on each reply, to set it from.

### When a request ends

When you pause for 0.3 s, the box starts transcribing what you said and Smart Turn judges whether it
sounds finished. If you go on, both are thrown away. Which silence then ends the request depends on what
you said and on his last reply. All three are config.yaml only, in `channels.satellite`.

| Setting | Key | Default | When it applies |
| --- | --- | --- | --- |
| Silence before he answers | `end_silence_secs` | 0.8 s | A request that sounds finished and ends a sentence |
| Silence mid-sentence | `unfinished_silence_secs` | 2.5 s | It sounds unfinished ("what to have in my living room because"), or has no full stop ("Tell me about"). Raise it if he cuts you off mid-thought |
| Pause in a conversation | `chat_silence_secs` | 1.5 s | A finished sentence in answer to a reply that only talked, so you can stop to think. After a reply that acted, a quick "even warmer" still ends after `end_silence_secs` |

Each verdict is in the log (`sounds finished 0.01`, `no full stop`). With hold to talk, the request ends
when you let go of the button instead. How the browser judges the same pause is on
[understanding](understanding.md#3-hearing).

### Listening after a reply

After every reply the box listens again without the wake word, so a conversation carries on without
"Okay Winston" each time. If nothing is said, it goes back to waiting for the wake word.

- **How long**: Listen after a reply on Room boxes in Ways to talk (`channels.satellite.follow_up_secs`,
  6 s), and at least 8 s after a question. 0 listens on only after a question.
- **A question anywhere in the reply counts**, not only at its end: "How about Elliott Smith? Those are
  soothing."
- **While music plays on the box**, it listens on only after a question, since the room is rarely quiet.
- **Never in button only.**

### When he speaks first

Announcements and nudges from extensions ([speaking up](speaking-up.md)) play on the box as
a reply does, the chime first. A nudge goes to the box of the room you last talked in. After one, the
box listens on, so "thanks, I know" or "remind me after work" needs no wake word (not in button only).

### Music

Extensions play streams on the box's media player, the one replies do not use
([`ctx.speakers`](extension-sdk.md#speakers); [Spotify](spotify.md) is the built-in one).

- **The stream**: the satellite channel turns it into 48 kHz 16-bit stereo FLAC with ffmpeg, the box's
  own output rate, and the box fetches it on TCP 8702 like a reply.
- **"Okay Winston" works over the music.** The box turns media down 20 dB while it listens and while he
  talks, and its echo cancellation takes out what it plays, through its speaker or its jack.
- **There is no pause** on that player in the stock firmware, so a pause stops the stream.
- **A restart of the satellite channel** cuts the stream. winstond keeps what is playing and hands it
  back when the channel returns, so the music picks up again and the next song still reaches the box.
- **One volume**, what the dial sets, for his voice and everything the box plays. The channel reports
  each change, the dial's too, and sets it when an extension asks (`ctx.speakers.volume`). Spotify turns
  the box up a step when asked for louder with its own level at full.

## Ending a conversation, and the button

| You | What happens |
| --- | --- |
| "Stop", "that's all", "never mind", "be quiet", "not you" or "nothing", in the listening window | The conversation ends with no reply, and the box waits for the wake word. "No" and "thank you" on their own are answers and get one |
| "Stop", "stop it", "stop that", "quiet", "be quiet" or "shut up", with music playing | The music stops at once too, and the extension pauses at its source (`@ext.on_speakers_stop`), so "resume" carries on. "That's all", "never mind" and "not you" leave it playing |
| "Stop", or a press of the button, while he talks | He stops, and the box does not listen again (the stock firmware's stop word) |
| The wake word while he talks | He stops, and what you say after the name is the next request: "Okay Winston, what time is it?" Winston's firmware |
| Hold the button | Hold to talk. Winston's firmware |
| Double press | Button only, on or off |
| The mute switch | The microphones are cut off altogether |

- **Hold to talk**: hold the button. After a second the box plays its long press sound and listens.
  Talk, and let go when you are done: the request ends there however long you pause in it, plus 0.3 s
  for a last word cut short. For a request with pauses to think in, or a noisy room. On stock firmware
  a hold does nothing.
- **A hold never resets the box.** The stock factory reset (its warning at 10 s, the reset on letting go
  after 22 s) is skipped while the button is in use. To reset the box, switch the mute on first, then
  hold the button.
- **Button only** is for a party or the TV: the wake word is off, a short press or a hold starts a
  request, and he never listens after a reply. He says "Very well. I shall listen only when you press
  the button." The box keeps the choice through restarts of it and of Winston.
- **A reply stopped while it still streams in** has its connection reset (`Run.cut`), since the audio
  left queued for it can crash the box.
- **The LEDs**: two red LEDs by the microphones mean the mute switch is on. A red ring slowly twinkling
  means Winston is not connected.

### Talking over him

Off by default. With Winston's firmware the box keeps streaming its microphone through a reply, and
speech over his voice can stop him within about half a second, with what you said as the next request.
On the Voice PE his own echo is as loud there as you are and stops him too, so the wake word over a
reply does this job instead. The room tests are in [the
plan](plan.md#m3-next-steps-added-2026-09-25-in-this-order).

To try it anyway, set these in `channels.satellite` in config.yaml. It never applies in button only,
where the TV or a party would stop him, and a box on stock firmware stays turn based.

| Setting | Key | Default | What it does |
| --- | --- | --- | --- |
| Talk over him for at least | `barge_in_secs` | 0 (off) | Speech this long over his voice stops him |
| How loud you must be | `barge_in_volume` | 0.6 | Lower it if talking across the room does not stop him, raise it if his own voice does |
| Boost on the second microphone | `barge_in_gain_db` | 30 | Winston listens on the box's quieter second channel, this much louder |
| Not at the start of a reply | `barge_in_grace_secs` | 3 s | The start of a reply where talking over does not count, while the echo cancelling settles |

Each reply logs how much speech the box heard over it and how loud (`2.4 s heard over the reply, speech
0.00 s at most, volume 0.31 at most`): the numbers to set them by.

## The wake word

Each box has its own, Wake word in its dialog on Ways to talk (`wake_word`). It does not follow his
name: renaming him (`assistant.name`) changes what he calls himself, not what wakes a box.

- **Stock firmware**: Okay Nabu, Hey Jarvis or Hey Mycroft. Okay Winston needs
  [Winston's firmware](#winstons-firmware-okay-winston).
- **One at a time** on the Voice PE. `wake_word` takes a comma separated list for a box that can run
  more, and Winston keeps as many as the box allows.

Wake word sensitivity in the same dialog (`wake_sensitivity`) sets the box's own setting of that name.
On stock firmware it covers the three stock words. Winston's firmware adds Okay Winston:

| Wake word sensitivity | Okay Winston wakes at | |
| --- | --- | --- |
| Slightly sensitive | 0.95 | Fewest false wakes |
| Moderately sensitive | 0.85 | |
| Very sensitive | 0.70 | Fewest misses |
| Empty | | Keeps what the box has |

## When something goes wrong

- **He hears you but the box stays silent**: the log shows the wake word, what was heard and what he
  said, but no `reply ... fetched by <box>` line. TCP 8702 is blocked ([the network](#the-network)), or
  `public_host` is needed.
- **A red ring slowly twinkling**: Winston is not connected to the box. Check that Home Assistant is
  not holding it and that TCP 6053 is open.
- **A request that ended early, or a run that went away**: the log says when the box ended or aborted a
  request, and a run stopped from outside sets the Talk page back to Ready.
- **A run that hangs**: `kill -USR1 <satellite pid>` writes every thread's stack to the log.

## What the stock firmware does

Read from its source and checked on the box. Winston works with all of it;
[its own firmware](#winstons-firmware-okay-winston) changes only the turn taking and the button.

- **The wake word is on the box**: microWakeWord with okay_nabu, hey_jarvis and hey_mycroft. It never
  streams for a server-side wake word (`use_wake_word: false`).
- **One wake word at a time.** It reports `max_active_wake_words` 1, so a box cannot listen for Okay
  Winston and Okay Nabu together.
- **Only the voice client may ask.** The box lists and sets its wake words only for the connection that
  subscribed to its voice assistant; anyone else gets an empty list. Winston subscribes first, then sets
  the wake word.
- **Replies are played from a link**, as with Home Assistant's streaming TTS. RUN_START carries the
  link, and INTENT_PROGRESS with `tts_start_streaming` tells the box to start fetching. Its decoder
  takes FLAC and MP3, not WAV, so Winston serves a 48 kHz FLAC that grows while he speaks.
- **Turn based.** The box stops its microphone at STT_VAD_END and plays the reply; during it, only the
  stop word and the wake word work, on the box. Winston's firmware keeps the microphone on.
- **A stop from the box is an abort.** `voice_assistant.stop` while it listens aborts the run, and the
  box goes idle without playing a reply. So for hold to talk Winston's firmware starts the run without
  silence detection (no `USE_VAD` flag) and reports the button, and Winston ends the request on release.
- **No pause on the media player.**

## Winston's firmware: "Okay Winston"

`services/voice-pe/voice-pe-winston.yaml` is the official factory firmware at a pinned release, plus:

- **The `okay_winston` model** from `assets/wake/`, with its three sensitivities.
- **Hold to talk.** The stock long press also starts a request without silence detection and sets the
  firmware's `dial_touched`, which keeps the factory reset off until the button is let go. The center
  button is named, so the box reports it.
- **Talking over him and the wake word over a reply**, through a copy of ESPHome's `voice_assistant`
  (below).
- **Wi-Fi that never naps.** In light power save the box heard `tts_start_streaming` up to 0.3 s late.
  This costs about 0.2 W.

Nothing else changes, and Wi-Fi set over USB survives the flash. On stock firmware a double press
still switches button only.

### Building and flashing

Back up the box first; writing the file back at 0 restores it exactly.

```sh
uvx esptool --port /dev/ttyACM0 read-flash 0 0x1000000 backup.bin
```

Then build and flash with the box on USB. The first build takes 15 to 30 min and downloads the
toolchain.

```sh
cd services/voice-pe
uvx esphome@2026.9.1 compile voice-pe-winston.yaml
uvx esphome@2026.9.1 upload voice-pe-winston.yaml --device /dev/ttyACM0
```

Then set the box's wake word to `okay_winston`. Later builds go over the network in about 17 s, with
TCP 3232 open from this machine to the box (`wake.py flash` does the same, with your own model when
there is one):

```sh
uvx esphome@2026.9.1 upload voice-pe-winston.yaml --device <box address>
```

### The voice_assistant copy

`services/voice-pe/components/` holds a copy of ESPHome's `voice_assistant` component from ESPHome
2026.9.1, changed only where it says `Winston:`. It keeps ESPHome's license (GPLv3 for the C++, MIT for
the Python) in `components/LICENSE`, not the repo's Apache 2.0. A build must use ESPHome 2026.9.1;
moving to a newer one means copying its component again and redoing the changes.

- **The microphone stays on.** The stock component stops it when the request ends (STT_VAD_END) and
  streams nothing while it waits for the reply and plays it. The copy keeps streaming its echo-cancelled
  audio to Winston in those two states, and stops when the run goes idle. Home Assistant as the client
  would get the extra audio and has no use for it.
- **The wake word over a reply.** `stopped_reply()` and `listen_on()`, used by `on_wake_word_detected`
  in `voice-pe-winston.yaml`, listen on after the wake word has stopped a reply, and the
  `Wake word over reply` event tells Winston.
- **Talking over him**, when it is on: Winston listens to that audio with the browser's voice detection
  (Silero, at Pipecat's thresholds, with `barge_in_volume` as the floor). On `barge_in_secs` of speech
  he tells the box to listen on after the reply, stops its playback (the same media player stop as the
  box's own "Stop") and cuts the reply and the brain behind it. The audio from half a second before he
  was sure goes to the next run, so the first word is not lost.

### The model

The current model was made with the
[microWakeWord trainer](https://github.com/TaterTotterson/microWakeWord-Trainer-Nvidia-Docker) v17
(Docker, NVIDIA): 50,000 Piper samples of the phrase and 40,000 steps, 36 min on an RTX 3090 after an
hour of one-off dataset downloads. ESPHome rejects a manifest with keys it does not know, so
`okay_winston.json` keeps only its schema's fields. The trainer's full report, with the calibration, is
`okay_winston_eval.json`.

If the box ever cannot take a firmware change, the fallback is the plan's original satellite, a Pi with
a speakerphone, where talking over him works as in the browser. Its wake word is ready:
`assets/wake/hey_winston.onnx`, "Hey Winston" for openWakeWord, trained by `services/wakeword/` (in git
history at commit 1aa216d).

## Why it could carry the project

Voice PE owners hit Assist's ceiling within a week: the Piper voice, one command per wake word, an
experimental LLM layer, cloud speech recognition for accuracy. "Keep your Voice PE, get a real voice and
a real conversation, all local" is a one-sentence pitch that community understands. Doing it properly,
in order of importance:

- **No firmware step for the first hour.** Install, point it at the box, talk. Talking over him is the
  optional later step.
- **Do not fight Home Assistant.** Winston is the voice on top of it, never an Assist replacement. A
  rival on Nabu Casa's hardware gets a cold reception; an upgrade to it gets shared.
- **Solve the hardware people have.** Most owners run Home Assistant on a small box with no GPU. The
  `ollama` and `hosted` profiles cover hearing and voice on the CPU; the brain is the sticking point. A
  4B model on a CPU is too slow for conversation, so the honest path is a small GPU with Ollama, or a
  hosted brain while the microphone never leaves the house. Measure both on modest hardware before
  publishing numbers.
- **Room awareness**: with two boxes, "turn off the lights" means this room ([rooms](rooms.md)).
- **One page and one video**: a fifteen-minute setup page and a ninety-second clip of the voice, a tool
  call and an interruption.

Where the people are: the Voice Assistant category on the Home Assistant community forum, the Home
Assistant subreddit, the ESPHome contributors. Accept going in: support load, first users on hardware
never seen (what the profiles and the Models page are for), and the chance Nabu Casa ships something
similar. Being first and cooperative is the defensible position.

## Okay Winston at a distance

Okay Winston misses from across the room. The model is the likely cause, not the box: it learned from
Piper voices only and was scored on more of the same, never on a real voice at a distance. Okay Nabu's
thresholds were set on real far-field recordings (DipCo), and it has real voices in its training. What
to try, in order:

1. **Model or box.** Set the box's wake word to `okay_nabu` and say it from the spots where Okay
   Winston misses. If Nabu wakes and Winston does not, it is the model. If both miss, look at the
   placement (shelf, corner) and the noise (fan, TV).
2. **Very sensitive (0.70).** The firmware can go lower, with more false wakes from household talk; the
   Swedish "hej" followed by a name is the one to watch.
3. **Retrain with real takes from the box** ([below](#retraining-with-your-takes)). 50 to 100 "Okay
   Winston" said at 1, 3 and 5 m from the sofa, kitchen and doorway, normal and tired or mumbled, plus
   room audio as negatives (TV, music, household talk in Swedish and English, near misses such as
   "okay" and "Winston" mid-sentence). The trainer takes them as personal and negative samples.

### Retraining with your takes

`services/voice-pe/wake.py` does the whole round. It finds the box, its key and Winston in
`data/config.yaml` (`--box` names the room when there are several), and pauses what it needs of
Winston while it runs.

```sh
uv run python services/voice-pe/wake.py takes    # "Okay Winston", around the room
uv run python services/voice-pe/wake.py room     # the room's own sounds, as negatives
uv run python services/voice-pe/wake.py train    # about an hour on the GPU
uv run python services/voice-pe/wake.py flash    # the box gets the new model
```

- **takes**: press the box's button once, then say the wake word with about two seconds of quiet
  after each, moving around the room. Ctrl-C ends the session. Each pause cuts a take (0.4 to 2.5 s of
  speech). Every session gets its own folder in `data/wake/takes/`, and it says how many there are in
  all.
- **room**: the same, cut into 3 s pieces in `data/wake/room/`. Never say the wake word in it.
- **train**: hands every take and piece to the trainer, stops Winston's engines for the GPU, runs it in
  Docker (it needs NVIDIA's toolkit) and starts them again. The model lands in `data/wake/` with
  ESPHome's manifest and the trainer's report. If the trainer does not take the start over its API, it
  says so: press Train on its page, `http://127.0.0.1:8789` (from another computer through
  `ssh -L 8789:127.0.0.1:8789`), and it waits on.
- **flash**: builds Winston's firmware with the model from `data/wake/`, or the repo's when there is
  none, and flashes the box over the network. To go back, delete `data/wake/okay_winston.*` and flash
  again.

A take is recorded from the box's second microphone channel (the one micro_wake_word listens on, which
the box streams beside the first) at the firmware's `gain_factor` 4, so it is what the model hears. Not
the turn record, which keeps the first channel, and not hold to talk, which keeps you at the box. While
it records, Winston's satellite channel is stopped, since the box streams to one voice client, and the
box is button only, so the wake word does not end the recording. Both are put back at the end.

The whole session stays as `session.wav`; `wake.py split <session.wav>` cuts it again. Takes recorded
before `wake.py` go in `data/wake/takes/`, a folder per session. The takes are your voice: they and the
model stay in `data/`, never in the repo.

**First measurement, 2026-10-27**: 27 takes from around the living room, scored offline the way the
box does (mean of 5 frames). As one stream, the current model woke on 21 (78%) at `Very sensitive`
(0.70) and 15 (56%) at 0.95; two takes scored near zero. The takes reach the model at -49 to -66 dBFS
RMS. A higher gain does not help: at ×8 and ×16 it still woke on 21 at 0.70, since the front end
normalizes level. The model is the lever, not the box.

The trainer is now v47.3 (`ghcr.io/tatertotterson/microwakeword:v47.3`, its data in `~/.cache/winston-wake-trainer`). Over
v17 it generates the positives with four voice engines (OmniVoice, Qwen3-TTS, MOSS-TTS-Nano and Piper,
with a mix of English accents) instead of Piper alone, trains 80,000 steps, weights personal samples
3× and reviewed negatives as hard negatives, and takes generated near-miss phrases. None of its engines
does Swedish-accented English; only real takes cover that.

Sources: [Voice PE](https://www.home-assistant.io/voice-pe/),
[ESPHome's voice_assistant](https://esphome.io/components/voice_assistant/),
[aioesphomeapi](https://github.com/esphome/aioesphomeapi), and
[linux-voice-assistant](https://github.com/OHF-Voice/linux-voice-assistant), a Linux satellite that
speaks the same protocol.
