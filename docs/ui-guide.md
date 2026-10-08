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
- Errors are never silent. A failed action shows its reason in a toast (the query client does this
  for every mutation that does not show its own error; `meta: { showsErrors: true }` opts out). A
  page that could not load shows `LoadError` in place of its skeleton: an error icon, the reason and
  Retry. `PageSkeleton` does this by itself when a query the page waits on failed.
- No inline styles, no custom CSS classes, no em dashes. The build fails on all three.

## Winston

- The conversation is the page. You on the right in a soft bubble, Winston on the left as plain text.
  Tool calls are one muted line with an icon ("Searched the web for ..."), announcements a line with
  a bell and the time.
- His presence is his logo, one component, `Presence`, driven by his state (idle, listening,
  thinking, speaking); see [identity](identity.md). Pages never draw their own version of it.
- Voice and typing sit side by side at the bottom: a text field and one round talk button.
- Links to Talk, Turns, Memory, Dashboard and Settings are plain text at the top right, not a sidebar,
  the current one in the text colour. On Talk they follow one speaker icon button that reads typed
  replies aloud, and before it the room picker (a house icon and the room's name, "No room" by
  default, plain text in the links' size), shown once a box is set up. `WinstonHeader` draws this for every page on Winston's side.
- Memory reads like his notes: "How he behaves" first (his rules, with "Only you can change these"
  and the technical instructions folded), then one heading per topic with an (i) for its
  description, facts as plain lines as stored, the date muted at the end and a muted outline "Until
  29 Sep" badge before it on a fact that ends. Edit and forget on a line's hover (a fact's edit row
  has an Until date field); edit all, rename (name and description) and delete on a heading's. A
  rounded field under each section adds, a pasted list adds each line. Facts past their day fold,
  greyed, under "Expired (n)" after the topics, with "Clear expired" (confirm once, Undo). Search at
  the top; a quiet size line at the bottom that counts only what is in his prompt. Forget, remove
  and edit all on "How he behaves" offer Undo in their toast. With memory off, one muted line under the search says he does not use these and
  links to Tools.
- A line you said aloud (browser or box) gets a flag on its hover once its turn is kept: Misheard (asks
  what you actually said), Not talking to him, Wrong thing, Too slow; the toast offers Undo. A marked line
  keeps its flag, in the destructive colour, the mark in its tooltip. `TurnMarkControl` draws it on Talk
  and Turns alike.
- Turns is the turn record as a conversation, one day at a time: a muted line per turn with the time,
  the room (or Browser), how it started and the time to his first word, and a play button for what he
  heard; then your words in the bubble with the flag, "Heard first as ..." when the hint-word fix
  changed them, the mark in muted text, the tools and his reply as on Talk, and a muted line when it
  ended otherwise (Nothing heard, He said nothing). Chevrons step through the days; Marked (n) at the right
  shows every marked turn instead, each with its day. With turns not kept, one muted line links to System.
- Dashboard mode shows the logo large as the talk button, one line for his state ("Mic paused while the
  box has it" while its room's box has the mic), the last exchange
  in large type and announcements across the screen; a plain Close link at the top right is its only
  control besides the logo.

## Settings

- Only shadcn components and the three page templates: settings page, status page, list page.
- Labels, values and controls. Every field's explanation sits behind an (i) beside its label, opened
  by hover or tap, never as text under the control. The schema's `description` is the text; a field
  without one is a gap to fill.
- Never ask for what a machine already knows. A field whose answers can be listed (models, voices,
  entities, env variables) names its list in the schema (`options`) and renders as a searchable
  dropdown that still takes a typed value. Enums carry `labels`; bounded numbers are sliders; a time
  of day (`widget: time`) is the browser's time field.
- Each setting lives on one page. Brain owns the model, Voice owns speaking and hearing.
- Makers' logos (Simple Icons) mark engines and services, drawn in the text colour like any icon.
  Home Assistant entities get a lucide icon for their domain.
- Icons for actions and state. A status is an icon plus a colour, never a sentence: `StatusTip`, its
  words in the tooltip.
- Basic first, Advanced behind a toggle on every page. The schema decides which is which.
- Destructive or slow actions confirm once, say what they cost ("restarts the brain, about 3 minutes").
- A cleared field saves what it shows where empty means something. A field marked `empty_means_off`
  (quiet hours, chime, health path, quantization) saves `''` when cleared, so the default does not
  quietly come back; any other cleared field falls back to its default.
- A field that does not apply is not there. The schema says when a field applies (`when`: the engine's
  driver, the engine command, another field), and the form leaves it out otherwise. A field under a
  feature switch that is off (`enabled_by`) stays visible and editable but greyed.
- Keys are pasted, never shown. A field holding a variable name (`secret`) gets a basic value field
  beside it: "Paste a key", a check or warning icon with Set or Not set in its tooltip, and a Remove
  icon once set. A pasted key waits with the draft and Save writes it to `data/.env`; the name field
  stays under Advanced.
- A map in a form (contacts, engine environment, volumes) is a table with an Add icon and a dialog,
  column titles from the schema (`key_title`, `value_title`), trash with Undo in its toast. A page keeps
  its own table only where it adds what the generic one cannot: Home Assistant's entities (below) and
  Tools' sensors (label and state sensor filled in from the picked value sensor).
- The Home Assistant page shows the path from Home Assistant to Winston as data, in four cards. It
  draws its own tables and never uses `KeyedMapField` for the list. Connection holds the form fields,
  with three icons beside the title: reachable (the version in the tooltip), MCP server loaded, and in
  sync (the tooltip lists what a save would still change and what Home Assistant exposes off the list).
  After them come Sync, which reads Home Assistant again, and Apply, shown only while it is out of sync.
  What he sees is the list grouped by Home Assistant's rooms, with 'No room' last. Each row has the
  domain icon, the name he hears over Home Assistant's own name (muted), the aliases as small badges
  (two, then +N), the state (Unavailable in the destructive colour) and an icon for Applied or Not
  applied yet, the words in its tooltip. A search box filters names, aliases and rooms. Whole domains
  are one muted line with an (i). Add from Home Assistant opens a picker: search, a type filter that
  hides noisy types by default (`NOISY_DOMAINS`), checkboxes grouped by room, listed ones left out,
  Add N. An added entity starts with Home Assistant's name and no aliases of its own (Home Assistant's
  still show). What he can do lists the tools Home Assistant's MCP server gives him. After an action is the state
  check's own form: the switch, and Give up after (s) under Advanced. It has no wait before his
  answer, since he answers first and corrects afterwards. After a save
  that touched the list, the toast says what Home Assistant got ("Saved; 1 exposed, 2 aliases
  added"), or "Saved; Home Assistant was not updated" and the reason.
- A list that cannot be fetched (no token yet, the server down) leaves a plain text box with a warning
  icon whose tooltip gives the reason; a value the list does not hold is looked up by itself when the
  list works, and shown with a warning icon when it is not found.
- Something a section needs that is not a config field (what Ask Claude signs in with, its key) is a
  row after that section's form (`SectionForm`'s children), with the same label and (i) as the fields.
- A control that acts on a field without being one (a play button beside Voice, "Play a sample" on the
  Streaming heading) is an adornment: the page passes `SectionForm` `adornments`, keyed by the dotted
  path from the section (`voice`, `streaming`). A field's sits right of its control, a nested
  section's at the right end of its heading, and it shows only while the form shows that field, so
  `when` and Advanced decide for it too. Adornments reach the form through a React context
  (`lib/adornments.ts`), not rjsf's `formContext`, so they follow the page's state.
- A preview of unsaved settings plays through `usePreview` (the same streamed PCM playback as Talk's
  read aloud): Play turns into Stop while it plays, and one preview plays at a time on a page
  (`takePlayback`, which the cloned voices' play buttons use too). A preview the running server
  cannot give (another engine in the draft, a restart pending) is disabled with the reason in its
  tooltip.
- A channel's card title carries its state icon from `/api/status`, the detail in the tooltip.
- The Extensions page is one card per extension, drawn from `/api/extensions`: state icon, a shield
  with a tooltip on anything not built in, Log and Restart icons and the switch in the title; what it
  may reach as outline badges, a problem or its last stop as an icon line, its settings as a
  `SectionForm` at `extensions.<id>.settings` (its schema is grafted into the config schema), and its
  tools as a table with a tier select and two switches. An extension's own icon is a lucide name,
  loaded by `DynamicIcon`.
- A dot in the sidebar marks each page with unsaved edits. A saved change that waits for a restart is
  the changed icon beside Save, its tooltip naming each process and its cost, plus a Restart text
  button while one of them runs; what only the next `winston up` applies has the icon and no button.
- A refused save puts each error beside its field (a section's own beside its title), turns Advanced on
  when the field is advanced and scrolls to it.
- A plain list in a form (engine arguments, Home Assistant domains) is one box per entry under the
  list's label, an Add icon beside the label, move icons on hover and a Remove icon per entry. Every
  text box is the app's own `Input`, never the form library's.
- An empty table shows one muted "None yet." row. A key already in a list (entity, sensor) keeps Done
  off and says "Already in the list" under the field.

## Build

`pnpm build` runs the guardrails (`scripts/check.mjs`: em dashes in any file, CSS outside
`src/index.css`, inline styles in HTML), ESLint (`style` prop, classes Tailwind does not know, em
dashes in code), the type check and Vite. shadcn's generated `src/components/ui` is the library and
is exempt. `pnpm api` regenerates the client from `winston api schema`.

`pnpm smoke` then drives the built settings pages in Playwright against a throwaway winstond (see
[running](running.md)).

## Templates

- Talk page: `pages/talk.tsx`, fed by `useLive` (the `/api/live` stream) with `Presence` in the
  header. A restricted extension call waiting for you is a line with Confirm and Decline, replaced by
  its outcome.
- Memory page: `pages/memory.tsx`, from `/api/memory`, under the same `WinstonHeader`.
- Settings page: `SettingsPage` with one `SectionForm` per config section, a form from the schema
  pruned against the whole draft (`when`, `hidden`, `drivers`, `enabled_by`, `secret`), so pages do
  not filter fields themselves. `useSettings` holds the draft, changed paths over the saved config
  shared across pages, and the save flow: a dry-run patch, confirm the changes by page and the
  restarts with their cost, `PATCH /api/config` with If-Match, write pasted keys (`useSecretsDraft`),
  restart. A conflict reloads and keeps the edits on top.
- Status page: `pages/status.tsx`. `StateIcon` for state, icon actions, one gauge.
- List page: `ListPage` or `DataTable` plus `ItemDialog` for keyed maps (entities, sensors, engines).
  Maps inside a form are drawn by `KeyedMapField` from the schema, not by the page.
