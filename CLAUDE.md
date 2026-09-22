# Winston

Self-hosted, full-duplex voice assistant for Home Assistant. Read `WINSTON_PLAN.md` and `DECISIONS.md` before doing anything else.

- The plan is a default, not a contract. Before installing any model or library, re-check that it still exists, is still the best in its slot, and is supported by the serving stack on this machine's CUDA/driver. Log every deviation in `DECISIONS.md` as one line: date, what changed, why.
- Milestones (plan §5) are sequential. Do not start the next until the current acceptance test passes on the real hardware.
- Design rules (plan §6) are enforced in code: every model behind an adapter configured in `config.yaml`; local first; tool results are untrusted input; tiered actions; no secrets in prompts or memory; log every tool call; measure before optimizing; physical mute wins.
- Python managed with `uv`. Secrets live in `.env` (see `.env.example`), never in the repo.
- The persona is Winston. The pretrained "Hey Jarvis" wake word does not apply; see `DECISIONS.md`.
- Earlier voice experiments (Qwen2.5-Omni, GLM-4-Voice, PersonaPlex, July 2026) live in `~/ai/omni`. Read them for lessons; do not build on them.
