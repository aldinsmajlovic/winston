# Identity

<img src="images/logo.svg" alt="Winston's logo" width="120">

Winston's logo is also his presence: the mark in the Talk page header, the talk button that fills a
wall screen in dashboard mode, the favicon, and later the light pattern on a Voice PE. Every page draws him through
one component, `web/src/components/presence.tsx`, so he looks and moves the same everywhere.

## His name

`assistant.name` on the Assistant page is the one source of his name. The system prompt opens with it
("# Mira", then "Your name is Mira."), ahead of the persona's own rules, and the rest follows it: the
consent line for a cloned voice, the voice and message tool descriptions, the hint words, the Talk and
Memory headers, `winston talk` and the greeting. The product keeps its name: the package, the
`winston` command, `winstond` and this logo.

His persona is your text, so a rename does not change it silently. When you save a new name and
`persona.md` says the old one, the Save confirm lists those lines and offers to change them, checked
by default. Kept checked, the old name is rewritten as a whole word only ("Winston" and "Winston's",
never `winston` or `winstond`) and the previous file is kept as `persona.md.bak`. Unchecked, the file
stays as it is and the prompt still opens with the new name. A rename restarts the voice, satellite
and Discord channels. The wake word on a Voice PE is set per box and does not change with the name
([Voice PE](voice-pe.md)).

## The mark

A disc split in two by a soft W: sky above, charcoal below. The W takes the middle 70% of the width
and the line runs flat to the edge on both sides. An inset rim, as wide as the band outside it, is cut
out of the disc, so the mark sits on any background. Each field shades from top to bottom.

| Part | Light background | Dark background |
| --- | --- | --- |
| Sky, top to horizon | `#97BAD8` to `#C9DDEE` | same |
| Charcoal, horizon to bottom | `#40454F` to `#2A2E35` | `#6C7380` to `#59606C` |

On dark backgrounds the charcoal lifts to slate so the lower half does not disappear. The colours
are theme tokens (`--mark-*` in `web/src/index.css`); the geometry lives in `web/src/lib/mark.ts`
(100 by 100 viewBox: disc radius 46, rim 7, W depth 11, softness 0.85).

`web/public/favicon.svg` and `docs/images/logo.svg` are the mark at rest in full colour, generated
from the same geometry by `node scripts/mark-svg.ts` in `web/`; rerun it after changing the shape.

## How it moves

The drawing never changes; motion and brightness carry the state.

| State | What the mark does |
| --- | --- |
| Idle | Still, just under full colour. |
| Listening | Full colour; the W settles almost flat and a gentle wave runs through the line, left to right. |
| Thinking | Dims; a light runs along the line. |
| Speaking | The shape holds and the charcoal glows lighter in a slow swell, about every 1.5 s. |
| Announcing | The W at its deepest, the whole mark pulsing. Ready in the mark; the live feed has no announcing state yet. |

Changes between states ease rather than cut. With reduced motion turned on, each state is shown as
a still. Once Winston is idle and settled the animation stops, so an idle page costs nothing.

## Where it shows

- Talk page header, next to the name and the state.
- Dashboard mode, large, as the talk button.
- The browser tab (favicon) and the README.
- Planned: a Home Assistant card through the planned integration (`docs/plan.md`); the Voice PE's 12 lights, upper lights sky and lower lights charcoal,
  the lower ones brightening when he speaks (`docs/voice-pe.md`).
