# Running Winston

One command runs everything: `winston up` starts the engines from `data/config.yaml`, waits for them
to be healthy, starts the voice channel and serves the control API. Ctrl-C stops all of it.

```sh
uv run winston init            # once: creates data/ from the examples (--profile ollama | hosted; --list)
uv run winston up              # engines + voice channel + API on :8700
uv run winston status          # health of every engine, GPU memory
uv run winston restart tts     # one engine or the voice channel
uv run winston down            # stop engines and channel; winstond keeps serving
uv run winston talk            # text chat, same brain and tools
uv run winston ha expose       # make Home Assistant's exposure match config
uv run winston check           # unit (no hardware), then the m0, m1, m2 acceptance checks on real hardware
```

Logs: engine subprocesses write to `data/logs/<engine>.log`; the LLM container to `docker logs
winston-llm`; every tool call to `data/logs/tool_calls.jsonl`.

## Web UI

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="images/status-dark.png">
  <img src="images/status-light.png" alt="The Status page: engines, their health and the GPU" width="720">
</picture>

`winston up` serves the UI at http://localhost:8700 once `web/dist` exists. Status lists every
engine with icon actions and the GPU. Models picks the brain, voice and hearing from presets, with
the model list read from the brain's server and the voices from the voices directory and the TTS
server; Advanced shows the raw fields. The other settings pages are generated from the config schema:
basic fields first, Advanced behind a toggle. Saving validates on the server, says which engines
the change restarts and roughly how long each takes, and offers to restart them.

Build it once (Node 22 or newer, pnpm):

```sh
cd web && pnpm install && pnpm build   # guardrails, lint, type check, bundle into web/dist
```

`pnpm dev` runs Vite on :5173 with `/api` proxied to :8700. `pnpm api` regenerates the typed client
from `winston api schema` after the API changes.

## Engines

Declared under `engines:` in config with a driver:

- `docker`: image, args, env, volumes, gpu. Used for the vLLM server. With Docker's `--gpus` path
  broken by a stale CDI spec on some hosts, set `runtime: nvidia` explicitly.
- `subprocess`: cmd and cwd, with `{data}` and `{repo}` expanding. Used for the Chatterbox TTS server,
  which has its own venv because it pins torch 2.6.
- `external`: a URL you already run; Winston only health-checks it.

## Exposure

Home Assistant's Assist exposure is a strict allowlist under `providers.home_assistant.expose`, with
friendly names and the aliases people actually say. `winston ha expose` applies it, un-exposes anything
else, and turns off HA's auto-expose. The model sees exactly that list.

## Announcements, Discord, tools

Winston speaking on its own is in [announcements](announcements.md), the Discord channel in
[discord](discord.md), and memory, web search, `ask_claude` and `send_message` in [tools](tools.md).

## Memory of what was measured

