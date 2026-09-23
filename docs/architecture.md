# Architecture

Winston is built so that the core stays small and everything specific to one home, one model or
one platform is a plugin or a line of config. This page is the map; `decisions.md` is the history.

## Principles

- **Core stays small; specifics are plugins or config.** The repo ships no personal data. An instance
  lives in a data directory (`config.yaml`, `.env`, `persona.md`, `memory/`, `voices/`, `logs/`).
- **Standard protocols at every seam.** LLM engines speak OpenAI-compatible HTTP, TTS engines speak the
  OpenAI speech protocol, tools speak MCP. Anything that speaks those plugs in.
- **One API, two clients.** `winstond` exposes REST and SSE. The web UI and the `winston` CLI both use it.
  Nothing exists only in the UI.
- **Typed, versioned config.** Pydantic models with defaults and a JSON Schema export. Every field carries
  its UI level (basic or advanced) and which engine a change restarts. Forms and docs come from the schema.
- **Presets over hand-written engine blocks.** `config/presets.py` names the engine and model choices per
  slot (vLLM, Ollama, hosted; Chatterbox, Pocket TTS, hosted) and composes them into hardware profiles.
  The UI offers them as dropdowns, `winston init --profile` writes one, and the config stays the truth.
- **Policy in code, prompt for tone.** Tool tiers (read, act, restricted), allow lists and logging are
  mechanisms, not prompt text.

## Shape

```
              web UI (React, shadcn)        winston CLI (typer)
                          \                    /
                       winstond  (FastAPI: REST + SSE)
                          |
        +-----------------+------------------+
        |                 |                  |
   supervisor          brain              channels
   (engines)     (LLM + tools + policy)   voice (Pipecat WebRTC), text, telegram, satellites
        |                 |
   drivers:          tool sources:
   docker,           MCP servers (Home Assistant is one entry),
   subprocess,       local tool plugins
   external URL
```

- **Engines** (`winston/engines`): heavy processes declared in config with a driver. `docker` runs the
  LLM server, `subprocess` runs the TTS venv service and the voice channel, `external` points at
  something you already run. The supervisor owns start, stop, health and restart-on-change.
- **Brain** (`winston/core`): one Pipecat LLM service plus its tools, shared by every channel. Tools come
  from MCP servers and local plugins. The persona is the system instruction. A claim guard sits after
  the LLM in every channel: a reply that claims an action or a state no tool call in that turn backs is
  retracted aloud and in the context. Remembered facts (`data/memory/`) are appended to the persona when a session
  starts; the `remember` tool adds to them.
- **Adapters** (`winston/plugins`): Pipecat services with a settings model, discovered through Python
  entry points (`winston.stt`, `winston.tts`, ...). A third-party package adds one without touching core.
- **Channels** (`winston/channels`): feed the same brain. Voice is the Pipecat pipeline with VAD,
  barge-in and latency observers; text is the CLI today and Telegram and web chat next.
- **Providers** (`winston/providers`): platform helpers, currently Home Assistant's exposure allowlist.
- **Control** (`winston/api`, `winston/cli`): `winstond` and the `winston` command.

## Repository

```
src/winston/        config/ core/ engines/ plugins/ channels/ providers/ api/ cli/
services/           engines with their own environments (tts-chatterbox pins torch 2.6)
web/                the UI (Vite, React, shadcn/ui); built assets are served by winstond
assets/             bundled voice samples (CC BY 4.0)
examples/           config.yaml, persona.md, env.example; `winston init` copies them
tests/              acceptance checks against real hardware, run by `winston check`
docs/               plan, decisions, this page, running, voices, ui-guide
data/               your instance; gitignored
```

## Plugin contract

An adapter is a class with a small contract and a settings model. For STT: a static `load_model(name,
quantization)` returning a reusable model object, and a constructor taking it as `model=` so one load
serves every connection. Contracts stay tiny on purpose; that is what keeps extension cheap and
versioning sane.

## Non-goals

Multi-user auth on the voice channel, cloud hosting, telemetry of any kind.
