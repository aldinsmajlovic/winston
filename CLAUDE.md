# Winston

Self-hosted, full-duplex voice assistant for Home Assistant. Read `docs/architecture.md`, `docs/plan.md`
(milestones and tracks with status) and `docs/decisions.md` (what changed and why) before doing anything else.

## Layout

- `src/winston/`: config (typed schema, loader, registry), core (brain, tools), engines (drivers,
  supervisor), channels (voice, text), providers (Home Assistant), api (winstond), cli,
  extension_host (runs extensions, nudges), extensions (the built-in ones, tests beside each).
- `sdk/`: `winston.sdk`, the only import an extension needs. Its own package and tests.
- `services/`: things with their own venv or build: `speech_server.py` (the voices' shared protocol),
  `tts-chatterbox` (GPU voice, torch pins), `tts-pocket` (CPU voice), `tts-qwen3`, `tts-voxcpm2`,
  `tts-gepard` (other GPU voices), `stt-qwen3-asr` (Qwen3-ASR hearing), `voice-pe` (the satellite
  firmware config).
- `web/`: the UI (Vite + React + shadcn/ui), following `docs/ui-guide.md`. `pnpm api` regenerates
  the client after an API change.
- `data/`: the instance (config.yaml, .env, persona.md, memory/, voices/, extensions/, logs/).
  Gitignored. Never put anything from it in the repo. `examples/` holds the generic templates.
- `tests/`: acceptance checks, run by `winston check`; `tests/fake_ha.py` is a made-up Home Assistant
  for tests and screenshots. `web/tests/`: the Playwright smoke test (`pnpm smoke`, `winston check ui`).

## Daily use

`uv run winston up` starts the engines and voice channel and serves the API and UI on :8700.
Also `status`, `restart`, `down`, `talk`, `announce`, `memory`, `check`, `ha`, `config`, `ext`.
Winston may already be running from an earlier session; check `winston status` first.

## Rules

- The plan is a default, not a contract. Before installing any model or library, re-check it still
  exists and is supported; log every deviation in `docs/decisions.md` as one line: date, what, why.
- A milestone or track step is done when its acceptance check passes on hardware, not before.
- Design rules (plan §6) are enforced in code: adapters behind config, local first (not local only),
  tool results are untrusted, tiered actions, no secrets in prompts, log every tool call, measure
  before optimizing.
- Personal data (entities, phones, phrasing) lives in `data/`, never in code, docs or screenshots.
- Commits are signed off (`git commit -s`, DCO). No em dashes anywhere (`just lint` checks).
- Python with `uv`. Secrets in `data/.env` only.

## Docs

A change is not done until the docs say it exists. Follow `docs/writing-docs.md`. In the same commit:

- **The topic page** that owns the subject gets the detail. A new subject gets its own page.
- **`docs/plan.md`** gets the status and measurements. Status, dates and test results go there,
  never in the README.
- **Screenshots** in `docs/images/` (light and dark) are retaken for any visible UI change.

## The README is a front page, not a changelog

Most changes do not touch `README.md`. Touch it only when the change alters what Winston is at a glance:
a new channel, a new requirement, a quickstart step, a change in what Status says works.

- The Features list stays at about eight lines, each one sentence or two about what the user gets.
  A new feature is folded into an existing line or replaces a weaker one. Never append a bullet for it.
- No implementation detail (timings per step, config keys, dates, test names, how a guard works).
  That belongs on the topic page.
- Status is two lines. The plan holds the rest.
- When you do edit it, reread the whole file and rewrite it as one page someone cloning today would
  read, the way a well-kept open source README reads. Remove anything no longer true.
