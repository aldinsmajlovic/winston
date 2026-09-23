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

## Announcements

Winston can speak without being spoken to: a chime, then the text as given, on every connected
speaker (a browser tab on the voice page today, the Voice PE satellite later). Outside quiet hours
(`announce.quiet_hours`, default 22:00-07:00) it is spoken; `announce.discord` decides when it also
goes to Discord (`fallback`: only when nothing could speak it). You can answer: the announcement and
its `context` join the conversation, so "how long did it run?" works right after.

From a Home Assistant automation, fire an event (winstond is subscribed over HA's websocket, so
nothing listens on the LAN; the token must belong to an admin user to subscribe to custom events):

```yaml
actions:
  - event: winston_announce
    event_data:
      text: "The washing machine is done."
      context:
        started: "{{ as_timestamp(states.sensor.washer_state.last_changed) | timestamp_custom('%H:%M') }}"
        finished: "{{ now().strftime('%H:%M') }}"
```

From a shell: `winston announce "Dinner is ready" -c cook=oven`. From anything else:
`POST /api/announce {"text": ..., "context": {...}}`. Every announcement and where it went is in
`logs/announcements.jsonl`. `tests/announce_voice.py` checks the whole path with a WebRTC client.

## Discord

Talk to Winston in Discord DMs and get announcements there; `send_message` lets it message people
in `channels.discord.contacts` (name to user id) and you ("me").

1. discord.com/developers/applications: New Application, then Bot: Reset Token (copy it) and turn on
   Message Content Intent. Under Installation, allow user install or invite it to a private server.
   The bot can only DM people who share a server with it.
2. Discord: Settings, Advanced, Developer Mode on; right-click yourself, Copy User ID.
3. `data/.env`: `DISCORD_BOT_TOKEN=...` and `DISCORD_ALLOWED_USER_ID=...`, then
   `channels.discord.enabled: true` and restart winstond.

Only that one user is answered; DMs from anyone else are ignored and logged. A conversation starts
fresh after `session_idle_mins` of silence.

## Memory of what was measured

