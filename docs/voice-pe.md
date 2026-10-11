# Voice Preview Edition as Winston's satellite

Written 2026-09-23 after Aldin asked whether Home Assistant's Voice Preview Edition (Voice PE) makes
Winston redundant, and then whether it could be Winston's room satellite instead. The answers: no,
and yes. This page is the plan for that, and the reasoning, so it is not lost.

## Why it fits

Voice PE is a good microphone and speaker for about 60 dollars: dual mics behind an XMOS XU316 with
hardware echo cancellation, a physical mute switch, a dial, an LED ring, a 3.5 mm jack for a better
speaker. Winston's M3 plan assumed a Raspberry Pi plus a conference speakerphone; this is cheaper,
smaller and skips the Linux audio plumbing.

Winston and Assist overlap only on the voice front end. Home Assistant stays the hands (Winston
already uses its MCP server), Assist stays what it is, and Winston becomes the voice on top.

## Setting one up

The satellite channel (`winston/channels/satellite.py`) passes against a fake box
(`tests/satellite_fake.py`) and has run on a real one since 2026-09-25.

There are two ways in. Without Home Assistant is quicker and is how the first box was set up.

### Without Home Assistant

A box that was never adopted has no API encryption, so Winston needs no key and nothing else
takes the microphone.

1. Plug the box into the Winston machine with USB-C. It shows up as `/dev/ttyACM0` (Espressif USB
   JTAG/serial).
2. Give it Wi-Fi over that cable with Improv Serial, for example from https://web.esphome.io
   ("Connect", then "Configure Wi-Fi"). Its serial log then prints its IP address.
3. Add it to `data/config.yaml` without `key_env` (step 4 below) and restart Winston.
4. When Home Assistant offers the discovered box, ignore it. Adopting it gives it a key and makes
   Home Assistant the client that takes the microphone.

### With Home Assistant

1. Set the box up with the Home Assistant app as usual (Wi-Fi, adoption, firmware update) and talk
   to Assist once, so the hardware is known good.
2. Get its API encryption key. Home Assistant generates it during adoption and keeps it in
   `.storage/core.config_entries` (the ESPHome entry's `noise_psk`); read it with the File editor or
   SSH add-on. Put it in `data/.env`, for example `VOICE_PE_KEY=...`.
3. In Home Assistant, disable the box's ESPHome entry (Settings, Devices, ESPHome, the box, disable).
   Only one client can hold a box's voice assistant, first come first served, so while Home
   Assistant is connected Winston gets nothing. The LEDs, dial and button keep working; they run on
   the box. Enable it again for firmware updates.
4. In `data/config.yaml`:

   ```yaml
   channels:
     satellite:
       enabled: true
       devices:
         living room: {host: 192.168.1.50, key_env: VOICE_PE_KEY, wake_word: hey_jarvis}
   ```

   and restart Winston. `winston status` lists the satellite; its log says when a box connects.

### The network

The box fetches the replies from this machine on port 8702, so that port must be open to it, and
Winston connects to the box on port 6053. With the box on its own VLAN, both need a rule:

- the router: the box to this machine, TCP 8702 (and this machine to the box, TCP 6053);
- this machine's firewall, for example with firewalld:
  `firewall-cmd --permanent --zone=public --add-rich-rule='rule family="ipv4" source address="<box subnet>" port port="8702" protocol="tcp" accept'`.

The symptom of a blocked 8702: the log shows the wake word, what was heard and what he said, but no
`reply ... fetched by <box>` line, and the box stays silent.

Say the wake word, then the request. After every reply the box listens again without the wake word
for `channels.satellite.follow_up_secs` (6 s; at least 8 s after a question), so a conversation
carries on without "Okay Winston" each time. If nothing is said the box goes back to waiting for the
wake word. 0 keeps the box listening only after a question. The replies are served from port 8702 on every interface; the box
must be able to reach this machine there.

### When he speaks first

Announcements and nudges from extensions ([speaking up](extensions.md#speaking-up)) play on the box as
a reply does, the chime first. A nudge goes to the box of the room you last talked in, and after one
the box listens on, so "thanks, I know" or "remind me after work" needs no wake word (not in button
only).

### Music

Extensions play streams on the box's media player, the one replies do not use ([`ctx.speakers`](extensions.md#ctx);
Spotify is the [built-in one](built-in-extensions.md#spotify)). The satellite channel turns a stream into
48 kHz 16-bit stereo FLAC with ffmpeg, the box's own output rate, and the box fetches it from the PC on
TCP 8702 like a reply. The box turns media down 20 dB while it listens and while he talks, and its echo
cancellation takes out what it plays, through its speaker or its jack, so "Okay Winston" works over the
music. The stock firmware has no pause on that player, so a pause stops the stream. A restart of the
satellite channel cuts the stream; winstond keeps what is playing and hands it to the channel when it is
back, so the music picks up again and the next song still reaches the box.

### Ending a conversation, and the button

- **"Stop", "that's all", "never mind"** (or "be quiet", "not you", "nothing") in that listening
  window end the conversation without a reply, and the box goes back to waiting for the wake word.
  "No" and "thank you" on their own are answers and get one.
- **"Stop" or a press of the button while he talks** stops him, and the box does not listen again
  afterwards (the stock firmware's stop word).
- **Hold to talk** (Winston's firmware): hold the button; after a second the box plays its long
  press sound and listens. Talk, and let go when you are done. The request ends there, however long
  you pause in it, plus 0.3 s for a last word cut short. For a request with pauses to think in it, or
  a noisy room. After the reply he listens on as after any request, unless the box is button only.
  A request stops at 20 s. A hold never resets the box: the stock factory reset (its warning at 10 s,
  the reset on letting go after 22 s) is skipped while the button is in use, as when you turn the dial
  with it held. To reset the box, switch the mute on first, then hold the button.
- **A double press** switches the box to **button only**: the wake word is off, a short press or a
  hold starts a request, and he never listens after a reply. He says "Very well. I shall listen only
  when you press the button." Another double press switches the wake word back on. Meant for a party
  or the TV. The box keeps the choice through restarts of it and of Winston.
- **The mute switch** on the side cuts the microphones altogether. Two red LEDs by the microphones
  mean it is on; a red ring slowly twinkling means Winston is not connected.

### Wake word sensitivity

`wake_sensitivity` per box sets the box's own "Wake word sensitivity": `Slightly sensitive` (fewest
false wakes), `Moderately sensitive` or `Very sensitive` (fewest misses). On stock firmware it covers
okay_nabu, hey_jarvis and hey_mycroft; Winston's firmware from 2026-09-27 adds Okay Winston at
thresholds 0.95, 0.85 and 0.70 (before that it stayed at 0.95 whatever the setting said). Empty
keeps what the box has.

### What the stock firmware allows (checked 2026-09-24)

- **The wake word is on the box**: microWakeWord with okay_nabu, hey_jarvis and hey_mycroft
  (`use_wake_word: false` in the firmware, so it never streams for a server-side wake word).
  `wake_word` picks one; "Okay Winston" needs Winston's firmware (below).
- **One wake word at a time.** The Voice PE firmware reports `max_active_wake_words` 1, so a box
  cannot listen for Okay Winston and Okay Nabu together; `wake_word` accepts a comma separated list
  for boxes that can, and Winston keeps as many as the box allows.
- **Only the voice client may ask.** The box lists and sets its wake words only for the connection
  that has subscribed to its voice assistant; anyone else gets an empty list. Winston subscribes
  first, then sets the wake word.
- **The wake word is set per box and does not follow his name.** Renaming him (`assistant.name`)
  changes what he calls himself, not what wakes a box: each box keeps its own `wake_word`.
- **Replies are played from a URL**, as Home Assistant's streaming TTS: RUN_START carries it,
  INTENT_PROGRESS with `tts_start_streaming` tells the box to start fetching, and Winston serves a
  48 kHz WAV that grows while he speaks.
- **Turn based**: the box stops its microphone at STT_VAD_END and plays the reply; no barge-in.
- **A stop from the box is an abort** (checked 2026-10-11): `voice_assistant.stop` while it listens
  tells the client to abort and the box goes idle without playing a reply. So hold to talk does not
  stop the box on release; the box starts the run without silence detection (no `USE_VAD` flag) and
  reports its button, and Winston ends the request when the button is let go.
- Measured on the real box (2026-09-25, from this machine over Wi-Fi): first audio 0.7 to 0.9 s after
  the transcript, speech recognition about 0.2 s for 2.5 s of speech.
- Measured against the fake box: first audio 1.8 to 2.3 s after the end of speech, of which 0.8 s
  is the silence that ends a request (`end_silence_secs`). The browser path measures about 1.3 s.
- Since 2026-10-08 the box starts transcribing at the first 0.3 s of a pause and throws that away if
  you go on, so the text is ready when the 0.8 s are up: end of speech to text 0.80 to 0.83 s against
  0.97 to 1.1 s before, on the fake box. Ending at the first pause instead cut people off (see
  [understanding](understanding.md#3-hearing)).
- Since 2026-10-11 Smart Turn also judges that pause. A request that sounds finished ends after
  `end_silence_secs`; one that does not ("Can you tell me what to have in my living room because")
  waits `unfinished_silence_secs` (2.5 s), so a pause to think does not cut you off. Each verdict is
  logged (`sounds finished 0.01`).
- Since 2026-10-11 the box is told to start fetching (`tts_start_streaming`) when the first sentence
  goes to the voice, as Home Assistant does, not when its audio exists, so the box connects and
  ffmpeg starts while it is spoken. A lone "." before a tool call never starts a reply.
- **The conversation is ready before the transcript.** A wake starts bringing the room's
  conversation up to date, rebuilt and warmed when it went stale, while you speak; the request waits
  for it only if it is not done. And every minute an idle box checks whether a new conversation's
  prompt changed (the diary after midnight, a remembered fact, an extension's tools) and builds and
  warms one, unless someone is talking on any channel (winstond's `talking` in `/api/status`).
  Reading the 9.4k-token prompt cold takes 7.5 to 10.6 s, which the day's first request used to wait.
  A wake during that keeps the conversation it has.
- When the box ends or aborts a request, the log says so, and a run stopped from outside sets the Talk
  page back to idle. `kill -USR1 <satellite pid>` writes every thread's stack to the log, for a run
  that hangs.

## Winston's firmware: "Okay Winston"

`services/voice-pe/voice-pe-winston.yaml` is the official factory firmware at a pinned release plus
the `okay_winston` model from `assets/wake/` and hold to talk: the stock long press also starts a
request without silence detection and sets the firmware's `dial_touched`, which keeps the factory
reset off until the button is let go, and the center button is named so the box reports it. Its
Wi-Fi never naps: the factory's light power save made the box wake for packets every 307 ms, so it
heard `tts_start_streaming` up to 0.3 s late (0.14 s on average, from 196 replies); it costs about
0.2 W. Nothing else changes, and Wi-Fi set over USB survives the flash. On stock firmware a hold does nothing; a
double press still switches button only. Build and flash with the box on USB:

```sh
cd services/voice-pe
uvx esphome compile voice-pe-winston.yaml     # first build 15 to 30 min, downloads the toolchain
uvx esphome upload voice-pe-winston.yaml --device /dev/ttyACM0
```

then set `wake_word: okay_winston` for the box. Later builds go over the network:
`uvx esphome upload voice-pe-winston.yaml --device <box address>` (13 s; the box needs TCP 3232 open
from this machine). Back up the box first
(`uvx esptool --port /dev/ttyACM0 read-flash 0 0x1000000 backup.bin`); writing that file back at 0
restores it exactly.

The model was trained with the
[microWakeWord trainer](https://github.com/TaterTotterson/microWakeWord-Trainer-Nvidia-Docker) (v17,
Docker, NVIDIA): 50,000 Piper samples of the phrase, 40,000 steps, 36 min on an RTX 3090 after an
hour of one-off dataset downloads. ESPHome rejects a manifest with keys it does not know, so
`okay_winston.json` keeps only its schema's fields; the trainer's full report, with the
calibration, is `okay_winston_eval.json`.

## The technical path

1. **Stock firmware, Winston as the client.** The box speaks ESPHome's native API, a bidirectional
   TCP protocol; Home Assistant is the client that subscribes to the voice assistant, receives
   microphone audio, sends the listening/thinking/speaking events and streams reply audio back to
   the speaker. That client side is `aioesphomeapi`, the library Home Assistant itself uses. Winston
   implements that role as a channel next to the browser one: same brain, STT, TTS and guard.
   - Wake word: on the box, one of its three (see above; the continuous-streaming mode this plan
     assumed is off in the stock firmware).
   - Reply audio: a URL the box's media player fetches, as a growing WAV (see above). Not Piper.
   - Keep the box out of Home Assistant's voice pipeline, or HA subscribes first. It can stay in HA
     for the LEDs and the dial.
   - Limit: the stock voice flow stops listening while the speaker plays. Turn based, no barge-in.
2. **Streaming during playback.** The same stock firmware already listens for the word "Stop" during
   playback, on the device, through the echo cancellation, so the microphone is usable while it
   talks. Step two is a firmware configuration that keeps streaming that audio to Winston during
   playback instead of only running the on-device stop word. Nobody has been found to have done it;
   likely, not certain. With it: barge-in, and the full browser experience in a room. Offer the
   configuration upstream to ESPHome rather than keeping it Winston-only.
3. **Fallback** if step two is blocked: the plan's original satellite (Pi plus a speakerphone), where
   barge-in is the same as the browser path. Its wake word is ready: `assets/wake/hey_winston.onnx`,
   "Hey Winston" for openWakeWord. The trainer that made it is in git history at commit 746e994
   (`services/wakeword/`).

Expected cost over the browser path: a tenth to a fifth of a second of latency from chunked audio
over the LAN in both directions.

## Why it could carry the project

Thousands of Voice PE owners hit Assist's ceiling within a week: the Piper voice, one command per
wake word, an experimental LLM layer, cloud speech recognition for accuracy. "Keep your Voice PE,
get a real voice and a real conversation, all local" is a one-sentence pitch that community
understands.

Doing it properly, in order of importance:

- **No firmware step for the first hour.** Install, point it at the box, talk. Barge-in is the
  optional later step. Zero soldering, zero flashing for the first impression.
- **Do not fight Home Assistant.** Winston is the voice on top of HA, never an Assist replacement.
  Nabu Casa moves fast on voice; a rival on their hardware gets a cold reception, an upgrade to
  their hardware gets shared.
- **Solve the hardware people have.** Most owners run HA on a small box with no GPU. Hearing and
  voice on the CPU are covered by the `ollama` and `hosted` profiles; the brain is the sticking
  point. A 4B model on a CPU is too slow for conversation, so the honest mass path is a small GPU
  with Ollama, or the hosted profile where the words go to a cloud model and the microphone never
  leaves the house. Measure both on modest hardware before publishing numbers.
- **Room awareness.** Two boxes means "turn off the lights" should mean this room. Small feature,
  big effect on how finished it feels. Built 2026-09-25 with rooms: each box's key is its room, and a
  screen in the same room shares its conversation and mic ([rooms](rooms.md)).
- **One page and one video.** A fifteen-minute setup page and a ninety-second clip: the voice, a
  tool call, an interruption. The interruption is what sells it.

Where the people are: the Voice Assistant category on the Home Assistant community forum, the HA
subreddit, the ESPHome contributors.

Accept going in: support load, first users on hardware never seen (what the profiles and the Models
page are for), and the chance Nabu Casa ships something similar; being first and cooperative is the
defensible position.

## Next step

The first box runs Winston's firmware and wakes on Okay Winston, turn based. It woke 70 to 80% of
the time at the fixed 0.95 threshold, against 98.6% on the trainer's synthetic voices; since
2026-09-27 it runs `Moderately sensitive` (0.85). Next: count misses and false wakes at that setting,
retrain with real takes of the household's voices if it is still short, then attempt step two.

Sources consulted: https://www.home-assistant.io/voice-pe/ ,
https://esphome.io/components/voice_assistant/ , https://github.com/esphome/aioesphomeapi ,
https://github.com/OHF-Voice/linux-voice-assistant (a Linux satellite speaking the same protocol).
