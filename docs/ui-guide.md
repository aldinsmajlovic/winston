# UI guide

The web UI has two areas. **Winston** is the front: the Talk page and dashboard mode. It is
where you talk to him and see what he did. **Settings** is behind it: status, models, engines and the
config forms. Both use one component library and no hand-written CSS beyond theme tokens.

Winston's UI shows Winston only: what he heard, said, did and remembers, and his own engines and
settings. It never controls the house. Switches, sliders and room dashboards belong to Home
Assistant; a line like "Turned on Kitchen lights" is a record of something he did, not a control.

## Stack

React, Vite, TypeScript. shadcn/ui components on Tailwind. lucide icons. TanStack Query for data.
An OpenAPI-generated client for `winstond`. JSON-Schema forms with a shadcn theme for settings.
The voice button uses Pipecat's browser client (`@pipecat-ai/client-js` with the small WebRTC
transport), connecting through winstond to the voice channel.

## Rules for both areas

- One typeface (Hanken Grotesk), sentence case everywhere. No monospace, no small spaced-out capitals
  as labels, no second font for titles.
- One accent colour (`--primary`). Semantic colours (ok, warning, error) are not the accent.
- No glows, gradients or decorative shadows. Light and dark come from the theme tokens, nothing
  hand-tuned per theme.
- Winston's logo is the one exception to both (other logos stay one colour): its own two colours (`--mark-*`) with a gentle shade in
  each field, and it stays out of the rest of the UI.
- No welcome text, no explanations, no marketing.
- No inline styles, no custom CSS classes, no em dashes. The build fails on all three.

## Winston

- The conversation is the page. You on the right in a soft bubble, Winston on the left as plain text.
  Tool calls are one muted line with an icon ("Searched the web for ..."), announcements a line with
  a bell and the time.
- His presence is his logo, one component, `Presence`, driven by his state (idle, listening,
  thinking, speaking); see [identity](identity.md). Pages never draw their own version of it.
- Voice and typing sit side by side at the bottom: a text field and one round talk button.
- Links to Dashboard and Settings are plain text at the top right, not a sidebar, after one speaker
  icon button that reads typed replies aloud.
- Dashboard mode shows the logo large as the talk button, one line for his state, the last exchange
  in large type and announcements across the screen; a plain Close link at the top right is its only
  control besides the logo.

## Settings

- Only shadcn components and the three page templates: settings page, status page, list page.
- Labels, values and controls. Every field's explanation sits behind an (i) beside its label, opened
  by hover or tap, never as text under the control. The schema's `description` is the text; a field
  without one is a gap to fill.
- Never ask for what a machine already knows. A field whose answers can be listed (models, voices,
  entities, env variables) names its list in the schema (`options`) and renders as a searchable
  dropdown that still takes a typed value. Enums carry `labels`; bounded numbers are sliders.
- Each setting lives on one page. Brain owns the model, Voice owns speaking and hearing.
- Makers' logos (Simple Icons) mark engines and services, drawn in the text colour like any icon.
  Home Assistant entities get a lucide icon for their domain.
- Icons for actions and state. A status is an icon plus a colour, never a sentence.
- Basic first, Advanced behind a toggle on every page. The schema decides which is which.
- Destructive or slow actions confirm once, say what they cost ("restarts the brain, about 3 minutes").

## Build

`pnpm build` runs the guardrails (`scripts/check.mjs`: em dashes in any file, CSS outside
`src/index.css`, inline styles in HTML), ESLint (`style` prop, classes Tailwind does not know, em
dashes in code), the type check and Vite. shadcn's generated `src/components/ui` is the library and
is exempt. `pnpm api` regenerates the client from `winston api schema`.

## Templates

- Talk page: `pages/talk.tsx`, fed by `useLive` (the `/api/live` stream) with `Presence` in the
  header.
- Settings page: `SettingsPage` with one `SectionForm` per config section, a form from the pruned
  schema. `useSettings` holds the draft shared across pages and the save flow: dry run, confirm the
  restarts with their cost, save, restart.
- Status page: `pages/status.tsx`. `StateIcon` for state, icon actions, one gauge.
- List page: `ListPage` or `DataTable` plus `ItemDialog` for keyed maps (entities, sensors, engines).
