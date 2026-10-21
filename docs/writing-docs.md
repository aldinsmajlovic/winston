# Writing docs

How the docs are kept current and how they should read. Applies to people and to Claude.

## Docs ship with the change

A change is not done until the docs say it exists. In the same commit as the code:

- **README**: only when the change alters what Winston is at a glance: a new channel, a new
  requirement, a quickstart step, a status change. Most features belong on their topic page alone.
  The feature list stays at about eight short lines; a new feature is folded into an existing line or
  replaces a weaker one, never appended. Reread the whole README and rewrite it as one page, not as a
  log of what was added last.
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
| `README.md` | Pitch, one screenshot, about eight features, status in two lines, quickstart, links |
| `docs/running.md` | Commands, web UI, engines, logs, exposure |
| `docs/architecture.md` | How the parts fit, the config API and its metadata, which settings page owns what, the repository, plugin contracts |
| `docs/ui-guide.md` | How the web UI looks and reads: its rules, words and templates |
| `docs/identity.md` | His name, his personality and his logo |
| `docs/hardware.md` | Profiles, GPUs, measured budgets |
| `docs/voices.md`, `docs/voice-pe.md`, `docs/rooms.md` | Their one subject |
| `docs/understanding.md` | How he hears, decides he is spoken to, and knows what a name means |
| `docs/tools.md` | Winston's own tools, their keys and limits, memory |
| `docs/computer.md` | The computer server: opening things, status, logs and updates on each computer |
| `docs/home-assistant.md`, `docs/announcements.md`, `docs/discord.md` | Their one subject |
| `docs/extensions.md` | The extension standard: the SDK, permissions, speaking up, writing one |
| `docs/built-in-extensions.md` | The extensions that come with Winston |
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

- **Screenshots over descriptions.** When the web UI changes visibly, retake the affected shot. Shots
  live in `docs/images/`, taken at 1400x860 in both themes and cropped to the content, and are shown with
  a `<picture>` element so GitHub picks light or dark. A shot of part of a page (an MCP server's actions)
  may be cut from a full-page capture at 1400 wide and shown at its own width. Paths are relative to the
  page: `images/...` from a page in `docs/`, `docs/images/...` from the README. The alt text says what
  the shot shows.
  ```html
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="images/brain-dark.png">
    <img src="images/brain-light.png" alt="The Brain page: what runs his brain, the model, and whether he thinks before answering" width="720">
  </picture>
  ```

  A shot is named for its page in lower case with dashes (`status`, `ways-to-talk`, `wall`), and
  `extensions-news` for an extension's page.

  Take them against a running `winstond` with Playwright and the system Chromium. Never capture
  anything from `data/` that is personal: entity names, phone numbers, memory facts, tokens.
  For the Home Assistant page, point a throwaway instance at `tests/fake_ha.py --seed home`, a made-up
  home with fake names and a fake token, never at the real Home Assistant.
- **Diagrams in Mermaid, never ASCII art.** GitHub renders a ```` ```mermaid ```` block as an image
  and it stays editable in the diff. Keep one diagram to one idea and under about a dozen nodes.
- **Icons and badges** come from real sources: shields.io badges in the README header, the project's
  own logo once it exists. Do not draw icons out of Unicode or emoji.
