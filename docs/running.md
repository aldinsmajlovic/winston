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
uv run winston check brain     # the configured brain on the trial requests: tool calls and speed
uv run winston check clone     # clone a synthetic friend's voice headless and switch back (about 90 s)
```

Logs: engine subprocesses write to `data/logs/<engine>.log`; the LLM container to `docker logs
winston-llm`; every tool call to `data/logs/tool_calls.jsonl`.

## Web UI

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="images/talk-dark.png">
  <img src="images/talk-light.png" alt="The Talk page: an announcement, a typed follow-up and a web search" width="720">
</picture>

`winston up` serves the UI at http://localhost:8700 once `web/dist` exists. The home page is the
Talk page: everything Winston hears, says and does on any channel (typed here, spoken through the
talk button or the voice page), each tool call as one line, and announcements. Type and press Enter,
or press the round button to talk; it connects the browser's mic to the voice channel through
winstond, and press it again to hang up. The speaker button at the top reads typed replies aloud in
Winston's voice through the browser, a sentence at a time as the reply streams in (off by default,
remembered per browser); spoken turns already are. Typed messages form one conversation that starts fresh
after `channels.web.session_idle_mins` (30) and after a settings save. Browsers only give the mic to
`localhost` or HTTPS, so talking from another device needs HTTPS on the LAN. The page keeps the last
few hundred events while winstond runs; it is not a transcript archive (the tool log is the record).

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="images/dashboard-dark.png">
  <img src="images/dashboard-light.png" alt="Dashboard mode: Winston's logo large, speaking, with the question and his answer under it" width="720">
</picture>

Dashboard mode, at http://localhost:8700/dashboard (the Dashboard link on the Talk page), is the
same feed full screen for a wall tablet. Tap the logo to talk and again to hang up. The line under it
follows his state; what you said and his answer show while he is busy and for 20 seconds after. An
announcement takes the screen for a minute, until you tap it or until you answer it. The page asks the browser to keep
the screen on, which like the mic only works on `localhost` or HTTPS. To show it in Home Assistant,
add a Webpage card pointing at the dashboard URL; if Home Assistant itself is on HTTPS, the browser
blocks a plain `http://` page inside it, so Winston needs HTTPS too.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="images/status-dark.png">
  <img src="images/status-light.png" alt="The Status page: engines, their health and the GPU" width="720">
</picture>

Settings is the link at the top right. Status lists every engine with icon actions and the GPU.
Brain picks the engine from presets (vLLM, llama.cpp, Ollama, hosted) and the model from the list the brain's
server reports; Voice does the same for speaking (voices from `data/voices/` and the TTS server) and
hearing, and for Chatterbox has Streaming: on or off and the chunk length, longer being smoother. Picking a preset rewrites the engine block and the settings that go with it. The other
pages are generated from the config schema: basic fields first, Advanced behind a toggle, and an (i)
beside each label that explains the field (and, for an engine, what the chosen preset needs).

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="images/brain-dark.png">
  <img src="images/brain-light.png" alt="The Brain page: engine preset with its logo, the model picked from the server's list, thinking" width="720">
</picture>

Anything a machine already knows is a searchable dropdown filled from `/api/options/{name}`, and a
typed value still works when the list is empty:

| Field | Filled from |
| --- | --- |
| Brain model | the brain's server (`/models`), following an unsaved base URL |
| Voice | clips in `data/voices/` and the TTS server's built-in voices |
| Speech-to-text model | the models onnx-asr knows, downloaded ones marked |
| Web search and Claude models | the Gemini API; the Anthropic API when `ANTHROPIC_API_KEY` is set, else a built-in list |
| Home Assistant entities, sensors, domains | Home Assistant's states, with each entity's name, state and an icon for its type |
| Key and token variables | the names in `data/.env`, with an icon for set or empty; values never leave winstond |
| Persona, chime | `*.md` in the data directory, sounds in `assets/sounds/` and `data/sounds/` |

Home Assistant shows whether it answers at the URL with the token. Adding a sensor on the Tools
page fills its label from Home Assistant and finds its state sensor (`_level` to `_state`). Saving
validates on the server, says which engines the change restarts and roughly how long each takes,
and offers to restart them.

Build it once (Node 22 or newer, pnpm):

```sh
cd web && pnpm install && pnpm build   # guardrails, lint, type check, bundle into web/dist
```

`pnpm dev` runs Vite on :5173 with `/api` proxied to :8700. `pnpm api` regenerates the typed client
from `winston api schema` after the API changes.

## Engines

Declared under `engines:` in config with a driver:

- `docker`: image, args, env, volumes, gpu. Used for the vLLM and llama.cpp servers. With Docker's
  `--gpus` path broken by a stale CDI spec on some hosts, set `runtime: nvidia` explicitly. The
  container carries a label with a hash of its engine block; `winston up` replaces a running
  `winston-<name>` container whose label does not match, so a swapped engine never silently keeps
  the old one.
- `subprocess`: cmd and cwd, with `{data}` and `{repo}` expanding. Used for the Chatterbox TTS server,
  which has its own venv because it pins torch 2.6.
- `external`: a URL you already run; Winston only health-checks it.

In args and cmd, `{model}` stands for `llm.model` and `{section.field}` for any other config value,
so a server's own options can be ordinary settings: the Chatterbox command passes
`--chunk-secs {tts.streaming.chunk_secs}` and friends, and changing the setting restarts the engine
with the new value.

## Exposure

Home Assistant's Assist exposure is a strict allowlist under `providers.home_assistant.expose`, with
friendly names and the aliases people actually say. `winston ha expose` applies it, un-exposes anything
else, and turns off HA's auto-expose. The model sees exactly that list.

## Announcements, Discord, tools

Winston speaking on its own is in [announcements](announcements.md), the Discord channel in
[discord](discord.md), and memory, web search, `ask_claude` and `send_message` in [tools](tools.md).

## Memory of what was measured

