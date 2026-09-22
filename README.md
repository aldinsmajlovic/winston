# Winston

A self-hosted, full-duplex voice assistant for Home Assistant. Talk to your home; it answers in
a natural voice, can be interrupted, and keeps everything on your own machine.

- **Local voice-to-voice.** Speech recognition, the language model and the voice all run on one GPU.
- **Hands through Home Assistant.** Lights, sensors, scenes, anything you expose, over Home Assistant's own MCP server.
- **Interruptible and fast.** Around 1.3 s from the end of your sentence to the first spoken word.
- **Swappable parts.** Every model is an adapter chosen in config. Bring your own LLM server, TTS or STT.
- **One control service.** `winstond` supervises the engines and serves the web UI and API.

Status: early. Voice round trip, Home Assistant control, a text CLI and the web UI work. Memory,
cloud handoff, Telegram and room satellites are in progress. See `docs/plan.md`.

## Quickstart

Requirements: Linux, an NVIDIA GPU with 24 GB (a 3090 is the reference), Docker with the NVIDIA
container toolkit, [uv](https://docs.astral.sh/uv/), and a Home Assistant instance.

```sh
git clone https://github.com/aldinsmajlovic/winston && cd winston
uv sync                                   # the pipeline environment
(cd services/tts-chatterbox && uv sync)   # the TTS server, isolated for its torch pins
uv run winston init                       # creates ./data with example config, persona and voices
```

Edit `data/config.yaml` (Home Assistant URL, entities to expose) and `data/.env` (tokens), then:

```sh
uv run winston up            # starts the LLM server, TTS server and voice channel; Ctrl-C stops all
uv run winston status
uv run winston talk          # text chat with the same brain
```

Open http://localhost:7860 to talk. The control API is at http://localhost:8700/docs, and the web UI
at http://localhost:8700 after building it once (`cd web && pnpm install && pnpm build`; needs Node 22
or newer and pnpm).

## How it fits together

```
browser / satellite  ->  voice channel (Pipecat: VAD, STT, barge-in, TTS)  ->  brain (LLM + tools)
                                                                                   |
                                        Home Assistant MCP server, local tools  <--+
winstond: supervises the LLM engine (vLLM in Docker), the TTS engine, the voice channel; serves API + UI
```

`docs/architecture.md` has the full picture. `docs/decisions.md` is the log of what changed and why.

## Licence

Apache-2.0. See `LICENSE` and `NOTICE`. "Winston" is the name of this project; forks are welcome
under a different name.
