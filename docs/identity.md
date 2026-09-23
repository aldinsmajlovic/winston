# Identity

Winston's logo is also his presence: the mark in the corner of the Talk page, the thing that fills a
wall screen in dashboard mode, the favicon, and later the light pattern on a Voice PE. It is not
designed yet. Until it is, a ring of dots stands in (`web/src/components/presence.tsx`), and every
page draws Winston through that one component, so the logo replaces one file.

## What the mark has to do

It is an animation with five states, not a picture:

| State | When | What it should say |
| --- | --- | --- |
| Idle | Nothing happening | At rest, still there |
| Listening | You are speaking | Attention on you |
| Thinking | Between your words and his, tools running | Working on it |
| Speaking | His voice is playing | It is him talking |
| Announcing | He speaks unprompted (washing machine done) | Look up, this is new |

It must:

- read at 16 px in a browser tab and at half a wall screen, with the same shape;
- work in one colour on light and dark, with the accent as the only colour;
- change state by motion and brightness, not by swapping to a different drawing;
- map onto a ring of 12 LEDs, which is what the Voice PE has (`docs/voice-pe.md`);
- not look AI-made: the glowing orb and the waveform were tried in the first mockups and rejected.

## Where it will show

- Talk page header, small, next to the name and the state.
- Dashboard mode, large, as the talk button.
- Favicon and the sidebar header in Settings.
- The README header, once it exists.
- A Home Assistant card, through the planned integration (`docs/plan.md`).

## Placeholder

Twelve dots on a circle. Idle: all dim. Listening: all lit. Thinking: a light chasing round.
Speaking: the dots pulse out of step. It is deliberately plain so it is not mistaken for the logo.
