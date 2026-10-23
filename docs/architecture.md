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
  whether the form draws it (`hidden`) and which engine a change restarts. Forms and docs come from the schema.
  Restart lists name engines, `{engine}` (the engine a block belongs to) or `{brains}`, every channel that
  runs a brain (voice, Discord, satellite), so a new channel joins every brain setting in one place.
  Speech and hearing settings restart voice and satellite; the web channel is rebuilt on every save.
- **Presets over hand-written engine blocks.** `config/presets.py` names the engine and model choices per
  slot (vLLM, llama.cpp, Ollama, hosted; Chatterbox, Pocket TTS, Qwen3-TTS, VoxCPM2, Gepard, Breeze TTS 2, hosted) and composes them into hardware profiles.
  The UI offers them as dropdowns, `winston init --profile` writes one, and the config stays the truth.
- **Choices come from where the truth lives.** A field names a live list in the schema (`options`);
  `api/options.py` fills it from the brain's server, Home Assistant, the data directory, a provider's
  model list or a running extension's own list (`extension_choices`, asked of its process), so the UI
  never asks for something a machine already knows.
- **Policy in code, prompt for tone.** Tool tiers (read, act, restricted), allow lists and logging are
  mechanisms, not prompt text.

## Shape

```mermaid
flowchart TB
  ui([Web UI<br/>React, shadcn]) --> api
  cli([winston CLI<br/>typer]) --> api
  api[winstond<br/>FastAPI: REST + SSE] --> sup[Supervisor]
  api --> brain[Brain<br/>LLM, tools, policy]
  api --> ch[Channels<br/>voice, text, Discord, satellites]
  ch --> brain
  sup --> drv[Drivers<br/>docker, subprocess, external URL]
  brain --> mcp[MCP servers<br/>Home Assistant is one entry]
  brain --> local[Local tool plugins]
  api --> ext[Extension host<br/>one process per extension]
  brain -. extension tools .-> api
```

- **Engines** (`winston/engines`): heavy processes declared in config with a driver. `docker` runs the
  LLM server, `subprocess` runs the TTS venv service and the voice channel, `external` points at
  something you already run. The supervisor owns start, stop, health and restart-on-change. Before a
  start, `engines/install.py` gets what the engine lacks here (its image, its service venv, the brain's
  model), sized before the first byte and shared between engines that need the same part.
- **Brain** (`winston/core`): one Pipecat LLM service plus its tools, shared by every channel. Tools
  come from MCP servers and local plugins. The persona is the system instruction, flattened to one list
  of rules (the file's sections are for the Personality page), under a title and an identity line made
  from `assistant.name`. A reply filter (`core/reply.py`) sits after the LLM in every channel and passes
  the reply on a sentence at a time, dropping a stray `</think>` the model sometimes writes with
  thinking off and saying the answer once when he repeats it after one (each sentence waits until the
  next text shows up). The voice speaks each sentence the filter passes on at once (`ReplySentences`),
  not after the next one is written. Nothing checks the reply's wording against the tool calls: the
  claim guard that did was removed on 2026-10-11 (decisions.md). Remembered facts (`data/memory/`) and
  the reply language (`assistant.language`) are appended to the persona when a session starts; the
  `remember` tool adds to them. The diary (`core/diary.py`) writes a few lines about each day from the
  turn record using the background LLM after midnight; the last two days go into the prompt and a
  `get_diary` tool answers questions about older ones. Thinking is set for spoken and typed requests
  (`llm.thinking.spoken`, `llm.thinking.typed`); on the spoken channels, where it is off by default, a
  request about the home (an exposed name or alias, a light word, or a verb like "turn" or "dim") thinks
  anyway (`llm.thinking.spoken: home`, at `llm.thinking.home_effort`, medium by default) while it
  decides what to call, not while it answers from the result, and he first says a butler's line by code
  ("One moment.", "Let me see to that."), pushed straight to the TTS so it covers the thinking. The
  lines are rendered ahead, three takes each played in turn (`voice.prerender`), so their synthesis does
  not share the GPU with the thinking. A request right after one that acted thinks too, without the line
  (it may only be "thanks"): a tweak like "not that, they're not calm" has no name or verb in it, and
  without thinking got "let me find something softer" and no call 7 times in 10. Without thinking the 27B sometimes said a light was on without
  calling a tool, and on 2026-10-11 it still got 38 of 43 home requests right against 43 of 43. Home
  Assistant tools that take a name and an area get the area dropped when the named device is in another
  one (`rooms.scoped_by_name`).
- **Adapters** (`winston/plugins`): speech to text, found through the `winston.stt` entry point group, so a
  third-party package adds one without touching core. `parakeet` runs in the voice process;
  `openai_transcription` sends the audio to a server (Qwen3-ASR in `services/stt-qwen3-asr`, a hosted one)
  that runs as `engines.stt`, as the brain and the voice run behind OpenAI-compatible HTTP.
- **Channels** (`winston/channels`): feed the same brain. Voice is the Pipecat pipeline with VAD,
  barge-in and latency observers; text is `winston talk`, the Discord channel (DMs) and web chat,
  which runs inside winstond for the Talk page.
- **Live feed** (`core/live.py`): what Winston hears, says and does, as events (state, user, reply
  pieces, tool calls, announcements). A `LiveObserver` in a channel's pipeline turns frames into
  events; the voice process posts them to `POST /api/live`, web chat publishes in process, and pages
  read `GET /api/live` (recent history, then new events). The browser's WebRTC offer goes through
  `/api/voice/offer` to the voice channel, so the UI needs only winstond's origin. `POST /api/speak`
  passes text to the speech server and streams its PCM back, for the Talk page's read aloud.
- **Rooms** (`core/rooms.py`): a box and the browser sessions set to its room share one conversation,
  which winstond keeps; each session links its own context to it before the LLM runs and after each
  reply. A box that wakes holds the room's mic, and a browser session in the room hears silence until
  its reply has played. The room goes in the time stamp of each message, so "the lights" means its
  lights. See [rooms](rooms.md).
- **Directory** (`core/directory.py`): what the names in his world mean. Each brain reads Home
  Assistant's exposed entities, areas and aliases when it starts (at most every ten minutes) and puts a
  map of the house in its prompt, rooms with what is in each, so naming a device needs no lookup. The
  same read tells which area a device is in. See [understanding](understanding.md#2-directory).
- **Announcements** (`core/announce.py`): winstond is the hub. Home Assistant events, the API and the
  CLI publish; channel sessions (browser voice, each satellite box, Discord) subscribe to
  `/api/events` with the room they are in and deliver (chime and speech, or a DM). An announcement
  joins the conversation with its context, so replies to it work.
- **Extensions** (`winston/extension_host`, the SDK in `sdk/`): each extension is its own process that
  speaks MCP to winstond, with a Winston extension to the protocol for jobs, checks and settings, and
  calls back into winstond's `/api/ext/v1/` with a token scoped to its manifest. winstond finds,
  describes, starts and watches them, owns their schedules and Home Assistant triggers, grafts their
  settings into the config schema (so the form, key fields and field errors cover them), and hands
  every channel's brain the same list of their tools. A restricted tool waits for a confirmation on
  the Talk page. See [extensions](extensions.md).
- **Speaking up** (`extension_host/nudges.py`, `core/speak_up.py`): winstond's nudge engine keeps
  every nudge from extensions in `data/state/nudges.json`, applies the rules (quiet hours, the daily
  limit, never mid-conversation, deliveries, put off, dismissed, muted) and sends what is due through
  the announcement hub to the speakers in the room you are in, or to your phone. The channel words it
  with its own brain after the chime; `Mentions` adds next-talk nudges and the briefing to your next
  message; `follow_up` is the one tool for answers to any of them.
- **Providers** (`winston/providers`): platform helpers, currently Home Assistant: one client
  (`HomeAssistant`) for its REST reads, websocket API and MCP tool list, used by the settings pages,
  the state check, the sensor tool and `winston ha`, and the exposure allowlist. Its events (state
  changes, announcements, phone buttons) arrive over one websocket that `extension_host/scheduler.py`
  keeps, with or without extensions.
- **Shared helpers** (`core/common.py`): how names are folded and matched (rooms, an exposed entity's
  names, what was said: case and spacing do not count), the tool-call log line, acting on a frame once
  in an observer, and reading winstond's event streams. Clients reach winstond at `control.url`.
- **Control** (`winston/api`, `winston/cli`): `winstond` and the `winston` command.
- **Setup** (`winston/setup.py`): `winston setup`, which reads the computer, runs what needs root on yes,
  makes the data directory and the systemd user service, and `winston update` and `winston uninstall`
  for an install from a release. `scripts/install.sh` installs one, and `scripts/release.py` builds one,
  as `.github/workflows/release.yml` does on a tag ([setup](setup.md)). Setup mode
  (`winston/api/setup_link.py`): until the wizard (`web/src/pages/setup.tsx`) is done, winstond serves
  another device only with the setup link's key, and starts no engine before Brain and voice.

## Config API

- **Safe saves.** `GET /api/config` returns the config with its version in `ETag` (a hash of
  `config.yaml`). `PATCH /api/config` takes `{"changes": [{"path": ["tts", "voice"], "value": "onyx"}]}`
  (null removes a key, so its default applies) and applies it to the file as it is now, so two savers
  keep each other's fields. With `If-Match: "<version>"`, a changed path that also changed on disk since
  that version is refused with 409 and `{detail, paths, version}`; other changes merge. `?dry_run=true`
  saves nothing and reports the restarts; `?applied=voice` names a process that already runs the change
  (the voice channel after a live voice switch), so it is not marked for a restart. It answers
  `{saved, restarts, version}`; a 422 lists `{loc, msg, type}` with `loc` counted from the config root
  (`["vad", "stop_secs"]`). A dry run also says what Home Assistant would expose or stop exposing
  (`ha_plan`, or `ha_plan_error` when it cannot be read) and which persona lines a rename would change;
  a save that changed Home Assistant's list says what it applied there (`ha_apply`). The CLI saves the
  same way (`winston ext enable` patches one path).
- **Pending restarts.** A save or a new secret marks each running process in its restart list; the
  status shows `config_changed` until it is restarted, or until a later save puts the settings it reads
  back to what it runs with (a secret's mark stays until the restart). What only winstond reads at start
  (`control` sections, the Home Assistant token) is listed in the status as `pending_control` and
  applies at the next `winston up`. An engine or channel a save adds (picking a preset that brings an
  engine, a channel switched on) starts at once; one the config drops or switches off stops when the
  save lands. The save's confirm says both, and a slot whose preset changed is named by the preset
  ("Hearing: Qwen3-ASR 0.6B on GPU").
- **Secrets.** Config names variables, `data/.env` holds them. `GET /api/secrets` lists every variable
  the config names, plus `ANTHROPIC_API_KEY` and `CLAUDE_CODE_OAUTH_TOKEN`, with set or not, the paths
  that name it and what restarts; never a value. `PUT /api/secrets/{name}` `{value}` and `DELETE` write
  the file (mode 600) and winstond's environment, only for a name a secret field holds or defaults to,
  only on a loopback winstond, from a loopback client with a localhost Host header and a JSON body.
  Each write is logged by name. An extension's `Account` setting is a secret set by signing in
  (`extension_host/accounts.py`): `POST /api/extensions/{id}/accounts/{field}` gives the service's
  sign-in address with a one-time state, under the same guard, and `winston ext connect` makes one on a
  winstond that listens beyond this machine; the state is a file in `data/state/accounts/` (mode 600),
  which only this machine can write. The service sends the browser back to `GET /api/accounts/callback`,
  which takes only a loopback client and trades the code for a refresh token in `data/.env`. Only a
  state made in the last ten minutes finishes, once. Access tokens are renewed in winstond and handed to
  the extension (`ctx.token`); `DELETE /api/secrets/{name}` disconnects.

## Config metadata

Each field's `json_schema_extra` tells the form what to draw (`ui(...)` and `section(...)` in
`config/schema.py`; `Field(...)` and `Secret(...)` in the SDK, see [extensions](extensions.md#settings)):

- **Restarts**: `restarts` (above).
- **What it says**: the title is the label, naming the effect; `help` is one visible line under the
  control, at most 140 characters, and on a section or a nested object its lead line; `description` is
  the (i), for how it works and its trade-offs. `placeholder` is what an empty field, picker or optional
  choice shows (an optional choice also gets a first item for none). `tests/schema_copy_cases.py` fails
  on a banned word in a title or help, a switch or key without help, or help that runs long.
- **Numbers and choices**: `unit` is the suffix on a number (`"s"`, `"min"`, `"MB"`); titles carry none.
  `labels` gives a choice's values words, and on a number names special values, keyed by the value as
  text (`{"0": "Off"}`). `options` names a live list, `options_query` what to ask it. `icons` marks a
  choice, `icon` a section's title.
- **Controls**: a number is a number input unless `widget` asks for more: `slider` for a value felt
  rather than typed, `args` for a command line (one argument per line, a repeated flag warned),
  `time`, `time_range` and `hint_words`. `group` gives the fields of a section that share it one heading
  before the first of them.
- **When it shows**: `drivers` on engine fields; `when` leaves a field out unless
  `{"engines.{slot}.driver": ["external", null]}`, any other dotted path, or
  `{"cmd_uses": "tts.streaming."}` holds (`{slot}` is the section's `slot`); `hidden` never draws it,
  so it is set in config.yaml only: a variable's name, a port, a path, a Literal with one value, or a
  measured timing no label could explain ([running](running.md#settings-only-in-configyaml) lists
  them); `empty_means_off` saves a cleared field as `""`.
- **Section switches**: `enabled_by` on a section names its switch. The form marks that switch
  `section_switch` and, while it is off, the other fields (and every setting of an extension that is
  off) `waits`: they keep full contrast and stay editable, the switch's help reads "Off. These settings
  wait until you switch it on." unless it has its own, and their key fields show no "Not set".
- **Keys**: `secret` (`{default, label, help, optional}`) on a field that names a variable in
  `data/.env` draws a field to paste the key; the variable's name is not drawn. `help` says where
  to get the key; an `optional` one never warns, and an extension's stays out of its `needs`.
- **Maps**: `key_title`, `value_title` and `key_options` with `key_options_query`, drawn as a table
  with Add and a dialog.

Which settings page edits which part of the config is `settingsPages` in `web/src/hooks/use-settings.ts`,
for the sidebar's dots and the Save confirm. `*` in a section matches any one key (an extension's id):

| Page | Sections |
| --- | --- |
| Status | `engines`, `logging`, `control`, `version` |
| Assistant | `assistant`, `region`, `memory`, `logging.turns` |
| Brain | `llm`, `engines.llm` |
| Voice | `tts`, `stt`, `engines.tts`, `engines.stt` |
| Ways to talk | `channels`, `vad` |
| Home Assistant | `providers` |
| Extensions | `extensions`; views of `extensions.*.tools` and `extensions.*.kinds` |
| Actions | `extensions.*.tools` |
| Speaking up | `speak_up`, `announce`, `extensions.*.kinds` |

A path belongs to the page whose matching section is longest, so `engines.llm.args` is Brain's, not
Status's, and `extensions.spotify.tools.play.tier` is Actions'; ties keep every tied page. `views` are
another page's editor shown filtered (an extension's actions and messages on its own page): their dot
lights too, and the confirm names the owner. One config object is never split across two pages.

## Repository

```
src/winston/        config/ core/ engines/ plugins/ channels/ providers/ api/ cli/ extension_host/ extensions/
sdk/                winston-sdk: the extension SDK, its own package (a uv workspace member)
services/           engines with their own environments (tts-chatterbox pins torch 2.6)
web/                the UI (Vite, React, shadcn/ui); built assets are served by winstond
assets/             bundled voice samples (CC BY 4.0)
examples/           config.yaml, persona.md, env.example; `winston init` copies them
tests/              acceptance checks against real hardware, run by `winston check`
docs/               plan, decisions, this page, running, voices, ui-guide, writing-docs; images/ holds screenshots
data/               your instance; gitignored
```

## Plugin contract

An adapter is a class with a small contract and a settings model. For STT: a static `load_model(stt)`
that takes the `stt` settings and returns a reusable model object (a loaded model, or a client for a
server), so one load serves every connection; `Hearing.load(cfg)` calls it. And a static
`transcribe(model, pcm, rate, hints=None)` that turns int16 audio into text. `supports_hints` says
whether the engine can be told words in advance; one that can maps `hints` to its own setting
(Whisper's initial prompt or hotwords, a cloud engine's keyterms). Parakeet cannot. That is all an
engine does: the shared `core.hints.Hearing` corrects close misspellings of the hint words, logs the
STT line and hands the audio to the turn record, and in the voice pipeline one service for every
engine (`HearingSTT`) cuts the utterances; see [voices](voices.md#hint-words). Contracts
stay tiny on purpose; that is what keeps extension cheap and versioning sane.

Extensions have their own, larger contract: [extensions](extensions.md).

## Non-goals

Multi-user auth on the voice channel, cloud hosting, telemetry of any kind.
