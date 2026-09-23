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

## The technical path

1. **Stock firmware, Winston as the client.** The box speaks ESPHome's native API, a bidirectional
   TCP protocol; Home Assistant is the client that subscribes to the voice assistant, receives
   microphone audio, sends the listening/thinking/speaking events and streams reply audio back to
   the speaker. That client side is `aioesphomeapi`, the library Home Assistant itself uses. Winston
   implements that role as a channel next to the browser one: same brain, STT, TTS and guard.
   - Wake word on the brain box: the component has a mode that streams continuously and leaves wake
     word detection to the server, so `assets/wake/hey_winston.onnx` is used as is. No microWakeWord
     training.
   - Reply audio goes back as raw PCM over the API to the speaker component (resample 24 kHz to what
     the device wants). Not Piper.
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
   barge-in is the same as the browser path.

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
  big effect on how finished it feels.
- **One page and one video.** A fifteen-minute setup page and a ninety-second clip: the voice, a
  tool call, an interruption. The interruption is what sells it.

Where the people are: the Voice Assistant category on the Home Assistant community forum, the HA
subreddit, the ESPHome contributors.

Accept going in: support load, first users on hardware never seen (what the profiles and the Models
page are for), and the chance Nabu Casa ships something similar; being first and cooperative is the
defensible position.

## Next step

Buy one box. Build the stock-firmware channel, measure it, then attempt step two. Everything above
depends on that working.

Sources consulted: https://www.home-assistant.io/voice-pe/ ,
https://esphome.io/components/voice_assistant/ , https://github.com/esphome/aioesphomeapi ,
https://github.com/OHF-Voice/linux-voice-assistant (a Linux satellite speaking the same protocol).
