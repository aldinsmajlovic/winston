# Winston

Self-hosted, full-duplex voice assistant for Home Assistant. Read `docs/architecture.md`, `docs/plan.md`
(milestones with status) and `docs/decisions.md` (what changed and why) before doing anything else.

## Layout

- `src/winston/`: config (typed schema, loader, registry), core (brain, tools), engines (drivers,
  supervisor), channels (voice, text), providers (Home Assistant), api (winstond), cli.
- `services/tts-chatterbox/`: the TTS server, its own venv (torch 2.6 pins).
- `data/`: the instance (config.yaml, .env, persona.md, memory/, voices/, logs/). Gitignored. Never
  put anything from it in the repo. `examples/` holds the generic templates.
- `web/`: the UI (to be built: Vite + React + shadcn/ui; see `docs/ui-guide.md`).
- `tests/`: acceptance checks on real hardware, run by `winston check`.
- `web/tests/`: the settings smoke test (Playwright), run by `pnpm smoke` or `winston check ui`.

## Daily use

`uv run winston up` starts the LLM container, TTS server and voice channel and serves the API on :8700.
`winston status`, `winston talk`, `winston check`, `winston ha expose`, `winston config show`.
Winston may already be running from an earlier session; check `winston status` first.

## Rules

- The plan is a default, not a contract. Before installing any model or library, re-check it still
  exists and is supported; log every deviation in `docs/decisions.md` as one line: date, what, why.
- Milestones are sequential; the next starts when the current acceptance check passes on hardware.
- Design rules (plan §6) are enforced in code: adapters behind config, local first (not local only), tool results are
  untrusted, tiered actions, no secrets in prompts, log every tool call, measure before optimizing.
- Personal data (entities, phones, phrasing) lives in `data/`, never in code.
- Commits are signed off (`git commit -s`, DCO). No em dashes anywhere. UI follows `docs/ui-guide.md`.
- Python with `uv`. Secrets in `data/.env` only.

## Docs

A change is not done until the docs say it exists. Every feature, command, tool, channel or visible
UI change updates `README.md` and the page that owns the subject in the same commit, following
`docs/writing-docs.md`: README stays a short front page, detail goes to `docs/`, new subjects get their
own page, screenshots over prose, Mermaid over ASCII diagrams. Any visible UI change retakes the affected
screenshots in `docs/images/` (light and dark) in the same commit. Before committing, reread the README and
fix anything no longer true.
