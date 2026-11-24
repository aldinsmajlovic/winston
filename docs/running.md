# Running Winston

One command runs everything: `winston up` starts the engines from `data/config.yaml`, waits for them
to be healthy, starts the voice channel and serves the control API. Ctrl-C stops all of it.

```sh
uv run winston setup           # once: reads the computer, root steps on yes, the profile, data/, the service
uv run winston setup link      # a new setup link for another device while the wizard is not done
uv run winston init            # or only data/, from the examples (--profile ollama | hosted; --list)
uv run winston up              # engines + voice channel + API on :8700
uv run winston status          # health of every engine, GPU memory
uv run winston restart tts     # one engine or the voice channel
uv run winston down            # stop engines and channel; winstond keeps serving
uv run winston talk            # text chat, same brain and tools
uv run winston ha expose       # apply the Home Assistant list (Save on the page does it too); --list shows the plan
uv run winston check           # unit (no hardware), ui, then the m0, m1, m2 acceptance checks on real hardware
uv run winston check unit      # the no-hardware checks, each file run even when one fails; FAILED names each
uv run winston check ui        # the UI smoke test in Chromium on a throwaway winstond (needs pnpm build)
uv run winston check brain     # the configured brain on the trial requests: tool calls and speed
uv run winston check clone     # clone a synthetic friend's voice headless and switch back (about 90 s)
uv run winston ext list        # extensions: on or off, running or not, their status line
uv run winston ext new NAME    # a single-file extension in data/extensions/, with a test beside it
uv run winston ext test NAME   # its tests, no winstond needed; then `winston ext enable NAME`
uv run winston ext call ID TOOL '{"arg": 1}'   # a tool as the brain would call it; also run, logs, restart, disable
uv run winston ext check ID KIND  # what a kind's check would say now, saying nothing
uv run winston ext nudges [ID]    # where nudges stand and why each waits; every step is in logs/nudges.jsonl
uv run winston turns [--day DAY]  # the turn record: each spoken turn, heard, done and said (logging.turns)
uv run winston turns mark ID KIND # mark one as a mistake (misheard, not_addressed, wrong_action, too_slow); unmark, marked
uv run winston check turns [--with FILE]  # the marked turns replayed through this setup or a candidate, scored
```

## Running as a service

Set up by `winston setup`, he runs as `winston.service` in your user's systemd ([setup](setup.md)):

```sh
systemctl --user restart winston       # restart all of him
journalctl --user -u winston -f        # follow his log
```

Installed from a release (`install.sh`), `winston` is in `~/.local/bin` and runs without `uv run`.
Status says when a new release is out, with what is new and an Update button; `winston update` does
the same from a terminal. `winston uninstall` removes him and keeps the data directory
([setup](setup.md#updating-and-removing)).

## Test scripts

`winston check` gives the checks, and what they start, one temporary folder (`TMPDIR`) and removes it
when they end, so a check that forgets its own files leaves nothing in `/tmp`.

The scripts below are run by hand.

| Script | What it does | Needs | Leaves alone |
| --- | --- | --- | --- |
| `.venv/bin/python tests/satellite_fake.py` | Runs the satellite channel against a fake Voice PE on localhost: wake, request, streamed reply, a follow-up without the wake word, silence | The LLM and TTS engines | The house: actions are stubbed |
| `uv run python tests/rooms_voice.py` | Puts that fake box and a headless browser session in one room and checks he answers once and the conversation carries across ([rooms](rooms.md)) | `winston up` | The house: the box's actions are stubbed, and the browser session is only asked questions |
| `uv run python tests/room_trials.py` | Checks that "the lights" in a room means that room's | The LLM and Home Assistant | The house: actions are stubbed |
| `.venv/bin/python tests/conversations.py [name ...]` | Talks to the voice pipeline headless in eighteen scripted conversations (vague requests, things he cannot do, Swedish, talking over him, prompt injection, a promise he might not keep and more). It prints each transcript, with what speech-to-text heard, the tool calls, what he said and the time to first audio, and judges nothing | The engines | The house and `data/`: actions are stubbed and the tool log goes to the temp dir |
| `uv run python tests/announce_voice.py` | An announcement and a spoken follow-up, end to end ([announcements](announcements.md#checking-it)) | `winston up` | Nothing is saved, but the announcement plays on every connected box and browser too |
| `uv run python tests/extensions_voice.py` | Measures first audio on spoken turns while an extension misbehaves (blocks its loop, grows past its memory limit, crashes), in a throwaway winstond | The LLM and TTS engines | `data/`, which it only reads |
| `uv run python tests/extensions_demo.py` | Serves the docs' example extensions and an MCP server on :8790 against the fake Home Assistant, for looking at the Extensions page | Nothing | `data/` and the real Home Assistant |
| `uv run python tests/turns_demo.py` | Serves a made-up turn record on :8791, for looking at the History page and its marks | Nothing | `data/` |

## Logs

Paths are in the data folder.

| What | Where | Set by |
| --- | --- | --- |
| Each engine subprocess's output | `logs/<engine>.log` | |
| Installing a service's venv (`uv sync`) | `logs/install-<service>.log` | |
| The LLM container | `docker logs winston-llm` | |
| Each extension: what it printed, every failure with its traceback | `logs/extensions/<id>.log`, its state in `state/extensions/<id>/` | |
| Every step of speaking up: a nudge held, said or put off, and why | `logs/nudges.jsonl`, the nudges in `state/nudges.json` | |
| Every announcement and where it went | `announcements.jsonl`, beside the tool log | |
| Every tool call | `logs/tool_calls.jsonl` | `logging.tool_calls` |
| Each spoken turn's timings | `logs/latency.jsonl` | `logging.latency`; empty turns it off |
| Each spoken turn whole: its audio and what was heard, done and said | `turns/<day>/`, kept 14 days | Keep recordings of what you say, on Assistant (`logging.turns.enabled`, off); `logging.turns.keep_days` |

The voice and satellite channels write the latency line once his reply is over, with no words and no
audio. It holds the time from the end of your speech to the transcript (`stt_ms`), to the first token
(`first_token_ms`) and to the first audio (`first_audio_ms`), and the names of the tools the turn
called. The times are the turn record's. A request that never reached him (nothing heard, "that's
all") gets no line.

`uv run winston turns` lists a day of recordings ([understanding](understanding.md#1-turn-record-and-marks)).

## Status and errors

`winston status` shows each engine and channel as healthy, installing, starting, unhealthy (running,
health check failing), stopped or error; the Status page says Running, Installing, Starting, Not
responding, Stopped or Failed. An error says why, and stays until you start or stop that engine again:

- A channel whose variables are not set is not started: Discord without `DISCORD_BOT_TOKEN` shows
  `DISCORD_BOT_TOKEN empty in data/.env` (or `missing from`), naming the variables, never their values.
  The satellite channel checks each box's `key_env` the same way. Discord with Your Discord user ID
  empty (and no older `allowed_user_env` variable) shows `Your Discord user ID is not set: add it on
  Ways to talk, Discord`.
- A process that exits on its own, during startup or later, shows its exit code and the last line of
  its log, say `exited with code 1: discord.errors.LoginFailure: Improper token has been passed.`
  The full log is `data/logs/<name>.log`.
- A process with no health check (Discord) counts as up only after it has run for 2 seconds.
- An engine that lacks something it needs is installed first, and says Installing with how far it is
  ("the model, 6.2 GB of 16.4 GB"): its Docker image, its venv under `services/` (`uv sync`, logged to
  `data/logs/install-<service>.log`) and the brain's model. One that does not fit says how much it needs
  and how much is free, and is not started ([setup](setup.md#winston-installs-his-engines)).
- An engine that does not answer its health check within `startup_timeout` says so. While the Hugging
  Face cache grows during a start (a voice fetching its weights the first time), it waits on and says
  "downloading, 1.2 GB so far"; a download that stops says where it stopped. An external server says
  where nothing answered ("nothing answered at http://192.168.1.10:8000/v1/models"). A channel says only that it was not ready.

`winston status` prints an error's reason on the line under it. In the UI, Log shows the end of an
engine's or channel's log (`GET /api/engines/{name}/log?lines=200`): the log file of a process, the
container's log for Docker, nothing for an external server. It is on Status, in How it runs at the
bottom of the Brain and Voice slot cards (on the card's state line while the engine is not running),
and on a custom engine's page.

Off means off: a save that removes an engine, switches its Start with Winston off, or switches a
channel off (or leaves the room boxes with no box) stops it right away, and its confirm says so
("Discord: stops now"). One the save adds or switches on starts.

## Web UI

The Talk page, History, the wall display and every settings page, and how settings are saved, are in
[web UI](web-ui.md). `winston up` serves it at http://localhost:8700 once it is built.

## Settings only in config.yaml

The settings pages leave out what nobody needs to set by hand: ports, folders and files, timings
measured on real requests, and the variable each key is stored under. To change one, edit
`config.yaml` (`winston config path` prints where), then restart Winston.

| Setting | Default | What it is |
| --- | --- | --- |
| `assistant.persona` | `persona.md` | The file in the data folder his personality is read from; its words are edited on Assistant, Personality |
| `stt.quantization` | `int8` | Parakeet's precision: int8 is faster on the CPU at no audible cost, empty is full precision ([voices](voices.md#hearing-engines)) |
| `tts.streaming.first_secs` | 0.96 s | The first chunk of streamed speech; shorter starts sooner, below about 0.9 s the second can arrive late |
| `tts.streaming.lookahead_secs` | 0.32 s | Audio held back at the end of each chunk until what follows is known; shorter than the first chunk |
| `vad.stop_secs` | 0.3 s | Silence before he answers on the Talk page |
| `vad.unfinished_stop_secs` | 2.5 s | Added when what you said sounds unfinished; raise it if he cuts you off in the browser |
| `channels.voice.host`, `channels.voice.port` | `127.0.0.1`, 7860 | Where the browser's voice is received; winstond passes the browser's connection to it. 7860 is also Gradio's default |
| `channels.satellite.http_port` | 8702 | The port room boxes fetch his replies from ([Voice PE](voice-pe.md#the-network)) |
| `channels.satellite.public_host` | found from the route to each box | This computer's address as the boxes reach it, for a VPN, Docker or several network interfaces |
| `channels.satellite.devices.<room>.port` | 6053 | The box's ESPHome API port |
| `channels.satellite.end_silence_secs` | 0.8 s | Silence that ends a request that sounds finished |
| `channels.satellite.unfinished_silence_secs` | 2.5 s | Silence that ends one that does not; raise it if he cuts you off mid-thought |
| `channels.satellite.chat_silence_secs` | 1.5 s | Silence that ends a finished sentence in answer to a reply that only talked |
| `channels.satellite.barge_in_secs` | 0 (off) | Talking over him on a room box, with `barge_in_volume` (0.6), `barge_in_gain_db` (30) and `barge_in_grace_secs` (3 s) ([Voice PE](voice-pe.md#ending-a-conversation-and-the-button)) |
| `providers.home_assistant.mcp_path` | `/api/mcp` | Home Assistant's MCP server path; a proxy's prefix goes in the address |
| `memory.dir` | `memory` | The memory folder, in the data folder |
| `announce.ha_event` | `winston_announce` | The event an automation fires to make him speak; the example on Speaking up uses it |
| `logging.turns.dir` | `turns` | The recordings folder, in the data folder |
| `logging.tool_calls` | `logs/tool_calls.jsonl` | Every action with its arguments and result, in the data folder |
| `logging.latency` | `logs/latency.jsonl` | Each spoken request's timings; empty turns it off |
| `control.port` | 8700 | The port of these pages and the API; when it is taken the pages cannot load to fix it |
| `extensions.spotify.settings.api_port`, `stream_port` | 3678, 3679 | The Spotify receiver's API and the audio the speakers fetch, both on 127.0.0.1 |

Pasting a key on its page writes the variable's name for you; set one by hand to share a key or to
keep an older name:

| Key | Setting | Variable in `data/.env` |
| --- | --- | --- |
| A hosted brain's, voice's or hearing's API key | `llm.api_key_env`, `tts.api_key_env`, `stt.api_key_env` | `LLM_API_KEY`, `TTS_API_KEY`, `STT_API_KEY` |
| Discord bot token | `channels.discord.token_env` | `DISCORD_BOT_TOKEN` |
| Your Discord user ID, for older setups | `channels.discord.allowed_user_env` | `DISCORD_ALLOWED_USER_ID`, read while Your Discord user ID is empty |
| A room box's encryption key | `channels.satellite.devices.<room>.key_env` | `VOICE_PE_KEY_<ROOM>` |
| Home Assistant access token | `providers.home_assistant.token_env` | `HA_TOKEN` |
| An MCP server's token | `extensions.<id>.mcp.token_env` | `<ID>_MCP_TOKEN` |
| Web search, Ask Claude, Hevy | `extensions.web.settings.api_key`, `extensions.claude.settings.key`, `extensions.hevy.settings.api_key` | `GEMINI_API_KEY`, `CLAUDE_CODE_OAUTH_TOKEN`, `HEVY_API_KEY` |
| Spotify's keys | `extensions.spotify.settings.client_id`, `client_secret`, `listenbrainz_token` | `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET`, `LISTENBRAINZ_TOKEN` |

Not listed: `version`, which Winston sets; `llm.provider`, `stt.provider`, `tts.provider` and
`vad.provider`, which have one value or follow Runs on; and `stt.hint_words_removed`, which Names he
should hear right writes.

## Engines

Declared under `engines:` in config, each with a driver:

| Driver | What it takes | Used for |
| --- | --- | --- |
| `docker` | image, args, env, volumes, gpu | The vLLM and llama.cpp servers |
| `subprocess` | cmd and cwd, with `{data}` and `{repo}` expanding | The Chatterbox TTS server, which has its own venv because it pins torch 2.6 |
| `external` | A URL you already run; Winston only health-checks it | Something you run yourself |

For a `docker` engine:

- Where Docker's `--gpus` path is broken by a stale CDI spec, set `runtime: nvidia` explicitly.
- The container carries a label with a hash of its engine block. `winston up` replaces a running
  `winston-<name>` container whose label does not match, so a swapped engine never silently keeps the
  old one.
- Before it starts one, the host side of each volume is made as you, and `hub/` in a Hugging Face cache
  volume. The container runs as root, and a folder it made would be root's, which a voice running on
  the host then could not download its model into.

In args and cmd, `{model}` stands for `llm.model` and `{section.field}` for any other config value,
so a server's own options can be ordinary settings: the Chatterbox command passes
`--chunk-secs {tts.streaming.chunk_secs}` and friends, and changing the setting restarts the engine
with the new value.

## Exposure

Home Assistant's Assist exposure is a strict allowlist under `providers.home_assistant.expose`, with the
names people actually say as aliases. Saving it on the Home Assistant page applies it: exposes the list,
un-exposes anything else, adds aliases (never renaming Home Assistant's own entities) and turns off HA's
auto-expose. The Save confirm says what will change there first, and the save that first connects Home
Assistant changes nothing. `winston ha expose` does the same from the shell and `--list` prints the
plan. The model sees exactly that list. Details in [Home Assistant](home-assistant.md).

## Announcements, Discord, tools, extensions

Winston speaking on its own is in [announcements](announcements.md), the Discord channel in
[discord](discord.md), memory and `send_message` in [tools](tools.md), and web search, Ask Claude and
watched sensors in [built-in extensions](built-in-extensions.md).
Extensions, and the Extensions page under Settings, are in [extensions](extensions.md): each runs as
its own process, gets only its own keys, and its tools reach every channel's brain. What each action
may do on its own is set on Actions. An action ticked under Share with other AI apps (on Actions
while an extension is on, with the address to copy) is also offered to MCP apps on this computer, such
as Claude Code, at http://127.0.0.1:8700/mcp.
