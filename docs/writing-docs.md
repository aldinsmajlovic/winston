# Writing docs

How the docs are kept current and how they should read. Applies to people and to Claude.

## Docs ship with the change

A change is not done until the docs say it exists. In the same commit as the code:

- **README**: a new user-visible feature, command, channel, tool or requirement gets a line (or its
  line changes). The Status paragraph matches `docs/plan.md`.
- **The topic page**: the page that owns the subject gets the detail (see the map below).
- **`docs/decisions.md`**: one line for any deviation from the plan, as always.

Before committing, read the README top to bottom once and ask: would someone cloning today be
surprised by anything? Remove what is no longer true; stale lines are worse than missing ones.

## Where things go

The README is the front page, not the manual. It answers what Winston is, what it looks like, how to
start it, and where to read more. Anything longer than a line or two goes to a page and the README
links to it.

| Page | Owns |
| --- | --- |
| `README.md` | Pitch, one screenshot, features (one line each), status, quickstart, links |
| `docs/running.md` | Commands, web UI, engines, logs, exposure |
| `docs/architecture.md` | How the parts fit, the repository, plugin contracts |
| `docs/hardware.md` | Profiles, GPUs, measured budgets |
| `docs/voices.md`, `docs/voice-pe.md` | Their one subject |
| `docs/plan.md` | Milestones and their status |
| `docs/decisions.md` | What changed and why, one line each |

A subject that outgrows a section of its page (a new channel with setup steps, a tool with keys and
limits) gets its own page in `docs/`, linked from the README and from the page it left. Prefer several
short pages over one long one.

## Style

- Short. Say what it does and how to use it; the why goes in `decisions.md`. No intros that restate
  the heading, no closing summaries, no "Overview" sections.
- Plain words, first-hand tone. Numbers that were measured, not adjectives.
- No em dashes. No emoji in prose.
- Code blocks only for things someone types or pastes.

## Pictures

- **Screenshots over descriptions.** When the web UI changes visibly, retake the affected shot. Shots live
  in `docs/images/`, taken at 1400x860 in both themes and cropped to the content, and are shown with a
  `<picture>` element so GitHub picks light or dark:

  ```html
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/images/models-dark.png">
    <img src="docs/images/models-light.png" alt="The Models page" width="720">
  </picture>
  ```

  Take them against a running `winstond` with Playwright and the system Chromium. Never capture
  anything from `data/` that is personal: entity names, phone numbers, memory facts, tokens.
- **Diagrams in Mermaid, never ASCII art.** GitHub renders a ```` ```mermaid ```` block as an image
  and it stays editable in the diff. Keep one diagram to one idea and under about a dozen nodes.
- **Icons and badges** come from real sources: shields.io badges in the README header, the project's
  own logo once it exists. Do not draw icons out of Unicode or emoji.
