# Decisions

One line per deviation from `WINSTON_PLAN.md`: date, what changed, why.

- 2026-09-22 — Renamed the project from Jarvis to Winston (Aldin). Consequence: openWakeWord and microWakeWord ship a pretrained "hey_jarvis" model but nothing for "Winston", so M3 needs a custom-trained wake word model. The plan's factual mention of HA Voice PE shipping "Hey Jarvis" is kept as is.
- 2026-09-22 — Repo created at `~/ai/winston` on a machine with an RTX 3090 (24 GB), 16 CPU threads, 31 GB RAM, Arch (zen kernel). Whether this is the dedicated brain box or the main PC is still an open question (plan §8).
- 2026-09-22 — Development runs on Aldin's main PC (the RTX 3090 machine this repo lives on). A dedicated brain box will be bought when the project is done or nearly done. Consequence: the GPU is shared with other work (ComfyUI etc.), so services must be start/stop scripts rather than always-on, and VRAM budgets in plan §4 apply only while nothing else runs.
