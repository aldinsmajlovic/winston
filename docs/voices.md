# Voices

## Chatterbox-Turbo server

`services/tts-chatterbox/` is a small FastAPI app exposing OpenAI's `/v1/audio/speech` on port 8880,
returning 24 kHz mono PCM (or WAV). It has its own Python 3.11 venv because Chatterbox pins
torch 2.6. Start it from this directory:

```sh
cd services/tts-chatterbox
.venv/bin/python server.py --voice ../../data/voices/vctk/p254_023.wav --voices-dir ../../data/voices
```

Try another voice without restarting by naming a clip relative to `--voices-dir`, e.g.
`{"voice": "vctk/p226_023"}`; set the default clip in the `engines.tts` command in config. A name
that would reach outside `--voices-dir` (`..`, an absolute path, a backslash) is refused with a 400,
here and in the Pocket server.

A request may also carry its own streaming settings, which override the startup flags for that
request only: `stream` (true or false), `first_secs`, `chunk_secs` and `lookahead_secs`. Fields it
leaves out keep the startup values, and a look-ahead not shorter than the first chunk is refused
with a 422, as it is at startup. The server warms up the streaming path at startup even with
`--stream false`, so a streamed request to it is not slow the first time. Its answer says which way
it rendered in an `X-Streaming: on` or `off` header. The Pocket server ignores these fields, since
it always streams.

Preparing a clip the server has not used yet takes a moment on the GPU. It runs off the event loop,
so audio already being sent keeps flowing meanwhile.

## Trying a voice before saving

`POST /api/speak` on winstond speaks text through the configured speech server and streams 16-bit
mono PCM back. The Talk page uses it to read replies aloud. With `voice` and `streaming` it tries a
voice or streaming settings that are not saved yet, which is how the Voice page plays a sample:

```json
{"text": "Hello, I'm Winston.", "voice": "vctk/p226_023",
 "streaming": {"enabled": true, "chunk_secs": 2.4}}
```

- `voice` is any name the speech server knows. A name with `..`, a leading `/`, a backslash or a NUL
  is refused with a 422 before it reaches the server.
- `streaming` takes the fields of `tts.streaming` with the same limits; the ones left out are the
  saved values, and the whole is checked as the config is (look-ahead shorter than the first
  chunk), with a 422 when it is not. It reaches the speech server only when the `engines.tts`
  command uses `{tts.streaming.*}`, as the Chatterbox preset does, since a hosted server could refuse
  fields it does not know.
- The answer carries `X-Voice` (the voice sent, percent-encoded) and `X-Streaming`: `on` or `off`
  when the engine takes streaming settings, `ignored` when `streaming` was sent and did not apply
  (another engine, or a Chatterbox server started before it took per-request settings).

It speaks with the engine that is running. A changed engine in unsaved settings needs saving and a
restart of speaking before it can be heard.

On the Voice page, the play button beside Voice says "Hello, I'm Winston. This is how I sound." in
the voice picked, saved or not. When the Streaming settings show (the Chatterbox preset), "Play a
sample" on their heading speaks one long sentence with the unsaved switch, chunk length and, under
Advanced, first chunk and look-ahead, then shows the time from the click to the first sound beside
it until a streaming value changes. The sentence runs several seconds because chunks double from the
first one and start over with each sentence: a short line never reaches a long chunk length, so its
joins would not be heard. A warning icon beside the time means the running server did not use the
settings (`X-Streaming: ignored`). Both buttons are disabled, with "Save and restart speaking to hear
this engine", while the unsaved engine or server address differs from the saved one or speaking
waits for a restart; the sample also while look-ahead is not shorter than the first chunk.

`uv run python tests/preview_check.py` checks the same on the running Winston without a browser: it
asks for a voice other than the saved one, twice, and the sample unstreamed and streamed with 2.4 s
chunks, and prints the time to the first audio byte and to the end of each. It only listens; nothing
is saved.

## Voice dropdown

The voice dropdown on the Voice page lists, for a local server, "Server default" with the clip the
server started with (for instance "Server default: vctk/p254_023"; that clip is marked "server
default" below it), then your clips under `data/voices/`, then cloned voices by the friend's name,
marked "cloned". Consent recordings are never offered as a voice. A hosted server lists only your
clips, cloned voices and its built-in voices.

## Reference voices

`assets/voices/vctk/*.wav` (copied into `data/voices/vctk` by `winston init`) are sentence 23 from the CSTR VCTK Corpus (University of Edinburgh),
via Kyutai's `kyutai/tts-voices` mirror, licensed **CC BY 4.0**. Attribution: Yamagishi, J.,
Veaux, C., MacDonald, K. (2019), "CSTR VCTK Corpus: English Multi-speaker Corpus for CSTR
Voice Cloning Toolkit (version 0.92)", https://doi.org/10.7488/ds/2645. Speakers pulled as
British male candidates: p226 (Surrey), p232 (Southern England), p243 (London),
p254 (Surrey, **default**, chosen by Aldin 2026-09-22), p258 (Southern England), p273 (Suffolk).

Anything dropped into `data/voices/` (a personal clip, for instance) stays out of git.
Clone only from a permissively licensed clip or from someone who agreed to it, as below; never an actor.

## Cloned voices

Ask with the friend in the room: "Winston, clone my friend Maria's voice", or "clone my voice" for
your own. Winston then:

1. asks Maria to say "I'm Maria, and I agree that Winston can copy my voice." Speech-to-text checks
   the words; without "agree" (or with "don't agree") nothing is recorded.
2. asks her to talk about anything for about fifteen seconds, and stops at 15 s of speech, or at a
   2.5 s pause after 10 s.
3. keeps only the speech, levels it and switches to it: "Got it. This is me as Maria now." That
   sentence starts about 0.4 s after she stops (measured headless).

While he records, and until she stops after his "Got it", the mic goes to the recording only, so
nothing Maria says counts as a turn or cuts him off; Winston stays quiet while she talks. A voice
already cloned is switched to by name ("use Maria's voice") without recording again. Under 6 s of speech is too little and nothing is stored. "Back to your own
voice" reverts; the clone lasts the conversation unless you say to keep it, which saves it as
`tts.voice`. Asking again for the same name records over the old clone.

Each clone is `data/voices/friends/<name>.wav`, with the consent recording (`<name>.consent.wav`)
and what it was heard to say (`<name>.json`) beside it. The Voice page lists them, plays both
recordings and deletes them.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="images/voice-dark.png">
  <img src="images/voice-light.png" alt="The Voice page with one cloned voice listed" width="720">
</picture>

`winston check clone` runs the whole exchange headless with two VCTK voices as the user and the
friend. Recording from a satellite's far-field mic waits for M3.
