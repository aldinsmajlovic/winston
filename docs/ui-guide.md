# UI guide

The web UI has two areas. **Winston** is the front: Talk, History, Memory and the wall display. It is
where you talk to him and see what he did. **Settings** is behind it: Status first, then who he is, how
he thinks, sounds and hears, the ways to talk to him, Home Assistant, extensions, his actions and when
he speaks up. Both use one component library and no hand-written CSS beyond theme tokens.

## Stack

React, Vite, TypeScript. shadcn/ui components on Tailwind. lucide icons. TanStack Query for data.
An OpenAPI-generated client for `winstond`. JSON-Schema forms with a shadcn theme for settings.
The voice button uses Pipecat's browser client (`@pipecat-ai/client-js` with the small WebRTC
transport), connecting through winstond to the voice channel.

## Rules for both areas

- One typeface (Hanken Grotesk), sentence case everywhere. No monospace, no small spaced-out capitals
  as labels, no second font for titles: mixed type is what makes a page read as machine-made.
- One accent colour (`--primary`). Semantic colours (ok, warning, error) are not the accent.
- No glows, gradients or decorative shadows. Light and dark come from the theme tokens, nothing
  hand-tuned per theme.
- Winston's logo is the one exception to both (other logos stay one colour): its own two colours
  (`--mark-*`) with a gentle shade in each field, and it stays out of the rest of the UI.
- The UI shows Winston only: what he heard, said, did and remembers, and his own parts and settings.
  It never controls the house. Switches and room dashboards belong to Home Assistant; "Turned on
  Kitchen lights" is a record of something he did, not a control.
- No marketing and no welcome screens. Every settings page, and Memory, opens with one sentence on
  what it is for; Talk, History and the wall display open on their content. Anything a label cannot
  say is said in a short line where it applies (see Copy).
- Two voices: the screens speak plainly, neutral and in the second person; the butler character lives
  only in what he says, since personality in a label gets in the way of its meaning.
- Nothing is hover-only, because the wall panel and a phone have no hover. Every fact and action can be
  reached by tap and by keyboard: row actions sit in a visible menu, the (i) and a state's detail are
  popovers that open on tap (`InfoTip`, `StatusTip`), and a tooltip only names an icon button. Two
  gaps are still open: the mark's words on Talk and a blocked preview's reason sit in tooltips.
- Pages work at 360 px wide with a 16 px gutter and no sideways scroll. The header follows its own
  width (the sidebar shares the window): two or more commands fold into one More menu (one stays a
  button) and the breadcrumb gives way when it is narrow, so the title keeps its room. Tables stack
  below `sm`, and Save moves to a bar along the bottom. A menu is as wide as its longest item, so no
  item wraps. Menu items are words without icons. The smoke test checks every page at 390 px.
- Settings, commands and documents look different, so nobody flips a switch, leaves, and believes it
  acted. Switches, selects and fields change the draft and wait for Save. Commands (Start, Restart,
  Apply now, Remove a saved key) act now: labelled buttons or menu items, never switches. Those that
  stop something or reach outside (Stop, an engine's Restart, Apply now, Remove) confirm their cost;
  Start runs at once. Documents (Memory, Personality) save each edit, with Undo in its toast.
- Off means off: switching a part off and saving stops it, as switching it on and saving starts it,
  and the Save confirm says which.
- Errors are never silent. A failed action shows its reason in a toast (the query client does this
  for every mutation that does not show its own error; `meta: { showsErrors: true }` opts out). A
  page that could not load shows `LoadError` in place of its skeleton: an error icon, the reason and
  Retry. `PageSkeleton` does this by itself when a query the page waits on failed. A page that fails
  to draw shows "This page hit a problem. Your unsaved changes are kept." and Try again (`PageError`
  in `App.tsx`), never a blank screen.
- Dates and times go through `useRegion()` (`hooks/use-region.ts`, over the formatters in
  `lib/format.ts`), so every page follows the Region settings. Time-of-day inputs stay the browser's
  own field, which cannot be forced.
- No inline styles, no custom CSS classes, no em dashes. The build fails on all three.

## Vocabulary

One word per concept, used by every page, confirm, toast and doc, so people can build one picture of
him. The role names come from `partName` and `engineLabel` (`api/status.ts`) and `SLOT_TITLES`
(`lib/presets.ts`); schema titles use the same words.

| Concept | Say | Never show |
| --- | --- | --- |
| The language model slot and its server | Brain; the weights are the model | llm, model server |
| Text to speech | Speaking (the page is Voice; a voice is what he speaks with) | tts, TTS |
| Speech to text | Hearing | stt, STT, ASR |
| Preset picker | Runs on | Engine (as the picker's label) |
| Engine block (driver, image, arguments) | How it runs | Engine card |
| Driver | Runs as: Docker container, Process, External server | driver |
| Engine `enabled` | Start with Winston | Enabled |
| Browser voice channel | Talk page microphone | voice channel |
| Satellite | Room box (help may say once that Home Assistant calls it a voice satellite) | satellite, Voice PE as a generic word |
| Satellite map key | Room | device |
| Channels | Ways to talk | channel |
| A turn | Spoken request; what is kept of them is recordings; the page is History | turn, turn record |
| Session | Conversation | session |
| Tool | Action | tool (except "MCP server") |
| Declared tier read, act | Only looks, Changes things | read, act, tier |
| Tier restricted | Asks you first | restricted |
| Tool enabled | On its own, Off | Brain (as a column) |
| Tool offer | Shared, under Share with other AI apps | Other apps (as a column), offer |
| Nudge, kind | Speaking up; one item is a message | nudge, kind |
| Announcement | Announcement: something an automation or app asked him to say | |
| Extension permissions | What it can reach | May reach, permissions |
| Secret | Key, or the service's own word (Bot token, Access token) | secret, variable, data/.env |
| winstond, the data folder | Winston, this computer | winstond, data/.env, winston up |
| A switch | Its effect as a verb phrase | Enabled |
| Dashboard | Wall display | dashboard |
| Presence `idle` | Ready | Idle |

Another product's own words stay where the user has to find them there: Assist, Long-lived access
tokens, Developer Mode, Developer Portal, MCP. How it runs is the one place a process's own words
appear: its command, arguments, environment and paths.

The pronoun is "he" (the butler persona). Copy that names him uses the configured name (`assistant`
from `/api/status`), never a hardcoded "Winston", so a renamed assistant reads right; "Winston" names
the program ("Start with Winston", "Takes effect when Winston restarts").

## Copy

- A label names the effect in your words: a noun phrase for a value ("Delete recordings after"), a
  verb phrase for a switch ("Remember what you tell him"), a verb for a button ("Add a box"). Never
  "Enabled", never the config key.
- Prefer a better label to a help line. A field gets `help`, one visible line under the control, only
  when the label cannot predict what it does or costs, or when it involves consent, privacy, money,
  data leaving the house, a format to learn or a setup step outside Winston. Help never repeats the
  label. Background, trade-offs and examples go in `description`, behind the (i), and do not repeat
  the help.
- Help is one sentence of at most about 140 characters, because walls of grey text fail as badly as
  no words. No versions, measurement dates, file paths, CLI commands, variable names or internal
  names in a label or help; `description` names them only when they are the choice itself.
  Measurements go to `docs/decisions.md`.
- Units are the schema's `unit`, drawn after the value, never part of a title.
- A page's intro (`intro`) is one sentence on what the page is for. A card that introduces a concept
  gets one lead line under its title: its section's `help`, or the page's own `CardDescription`.
  Never a welcome, never marketing, never a lead that repeats the title.
- An empty list says in one sentence what belongs there and offers the action that fills it ("No
  room boxes yet." with Add a box). A search that finds nothing says "Nothing matches." "None yet." is
  only for simple maps (environment, volumes).
- A status line (`StatusLine`) is an icon for its tone and one sentence, with what to do as a labelled
  button ("Hevy needs a key." and Open Hevy). The reason is in the words, never only in a
  tooltip. A suggestion (Set up next on Status) is muted, not a warning.
- A confirm's title is a question that names the action and the part by its role ("Stop the brain?",
  "Remove the saved bot token?"). Its lines say what stops working, for how long, and what changes
  outside Winston ("It is deleted from this computer now, without waiting for Save."). Its button
  repeats the verb ("Stop", "Run without asking"), never OK.
- A toast says what happened, in the confirm's words and the past tense, with Undo wherever it can be
  undone ("Forgot: ...", "Saved; added 1 name").
- An error says what happened and what to do, in plain words ("Can't reach Home Assistant: nothing
  answered at that address"). The exception goes to the log, never to the page.
- "This computer", never `data/.env` or winstond. "Key", never secret.
- `tests/schema_copy_cases.py` holds the config schema to these rules. It fails on `data/.env`,
  winstond, `winston up`, backticks, ISO dates or an em dash in a title or help, on help over 140
  characters, on a bare title ("Enabled") or a unit in a title, and on a switch or a key without help.

## Winston

- The conversation is the page. You on the right in a soft bubble, him on the left as plain text. An
  action is one muted line with its icon, worded from the action's title ("Turned on Kitchen lights",
  "Search the web: opening hours"). A divider marks every new conversation and why it started. Lines
  from another place say where on the first line of a run ("Kitchen box", "Discord"). What he brings
  up on his own is one line with a bell: the time, what he said, from whom and where it went. An error
  line gives the server's sentence and a Status link. A restricted action waiting for you is a line
  with Confirm and Decline, replaced by its outcome. While he thinks, a small `Presence` and Thinking
  sit where his reply will appear, until his words or a running action take over. The view follows new
  lines only while it is at the bottom; otherwise New messages jumps down.
- His presence is his logo, one component, `Presence`, driven by his state; see
  [identity](identity.md). Pages never draw their own version of it. The line under his name says
  whether he can answer (`notReady` in `api/live.ts`): Ready, Listening, Thinking, Speaking, "Starting:
  his brain is loading (about 2 min)", or "Can't answer: his brain is stopped" (or "Can't reach
  Winston") with a Status link.
- Voice and typing sit side by side at the bottom: New conversation as a quiet round icon button (it
  asks first), the text field with Read typed replies aloud at its end, and one round talk button that
  becomes Send once something is typed. Toggles keep their icon and show their state with a pressed
  style (`aria-pressed`); Read aloud also crosses its speaker out while off. One muted line above the field holds the room picker at the left once a room exists
  (each option says what choosing it does; it is locked while you talk) and the one fact that matters
  now at the right: the Talk page microphone is off, a box holds the microphone, "Listening. He hears
  everything until you press the microphone again.", or read aloud is on.
- An empty conversation says how to start and what he can use, from what is set up and running; with
  nothing connected it says so and links to Status. No example phrases, no chips.
- His logo, name and readiness at the top left lead back to Talk. History, Memory and Settings are
  links with an icon and their name at the top right (below `sm` the name stacks under the icon), the
  current one on a quiet background. Settings opens Status and carries a warning mark while something
  needs you. `WinstonHeader` draws this for every page on Winston's side, and each page sets its tab
  title with `useTitle` ("History · <name>", Talk the name alone).
- Memory is a document of what he remembers, nothing else; his manner is under Assistant,
  Personality, and a line links there. A topic is a heading with its description as a muted line under
  it, a fact is a row with when it was noted, each row's actions sit in its menu, and a rounded field
  under each topic adds. Every edit, forget and delete offers Undo. The labels are in
  [tools](tools.md#memory).
- A line you said aloud gets a flag once its recording is kept (`TurnMarkControl`, on Talk and History
  alike), always visible on touch and on hover or focus otherwise. A marked line keeps a filled flag in
  the text colour, and the toast offers Undo. The menu and its marks are in
  [understanding](understanding.md#1-turn-record-and-marks).
- History is one day at a time under his diary entry for that day, or Marked: two pressed-style
  buttons, and `?view=marked` opens the second. Each spoken request has a muted header in words
  ("14:25, Kitchen, after the wake word, answered in 1.5 s"), Play what he heard inside your bubble,
  and the rest as on Talk. With recordings off, it says what they would keep and links to Assistant.
- The wall display (`/wall?room=`) shows the logo large as the talk button, its room quietly at the
  top left, one line for his state ("Tap to talk", "Microphone paused while the box has it", or why he
  cannot answer), the last exchange in large type, and what he says on his own across the screen until
  it is tapped away. A plain Close link at the top right is its only other control. It is set up from
  Ways to talk, on the Talk page card.

## Settings

- Only shadcn components (`components/ui`) and the app's own components and templates, so a new page
  looks like the others without new CSS.
- Settings open on Status. The sidebar has four groups split by separators without labels: Status;
  Assistant, Brain, Voice; Ways to talk, Home Assistant, Extensions; Actions, Speaking up. His name at
  its top leads back to Talk; the theme menu and the version sit in its footer. On a phone it is a
  sheet that closes when you pick a page.
- A page's header: on a sub-page the parent as a breadcrumb ("Extensions / Spotify", `back`), the title,
  then page commands as outline text buttons (`commands`; in a narrow header two or more fold into one
  More menu and the breadcrumb gives way), what waits for a restart, Discard and Save. Below `sm`
  Discard and Save are a bar along the bottom ("2 unsaved changes") while there is something to save.
- A field is its label, the (i) for its `description`, the control, its `help` line (see Copy), then
  what a refused save said. A switch has its label, (i) and help beside it. No required asterisk.
- Fields follow the schema's order. Fields that share a `group` follow one heading, after the fields
  without one, and the section's own rows come last.
- Never ask for what a machine already knows. A field whose answers can be listed (models, voices,
  entities) names its list in the schema (`options`), or for an extension its own list
  (`Field(choices=...)`), and renders as a searchable dropdown (`Combobox`) that still takes a typed
  value; `placeholder` says what empty means ("Not set", "Same as Home Assistant"). And never show
  what the machine says to itself: ids, file paths and variable names are never drawn outside How it
  runs (`hidden`, config.yaml only), and what the model is told shows only for an action from an MCP
  server, a package or Home Assistant, inside its closed Details.
- Enums carry `labels`, and a choice with a trade-off says its cost in its option ("Always think (a few
  seconds slower)"). A number is an input with its `unit` after the value; `widget: slider` is opt-in,
  for values felt rather than typed; a value copied from elsewhere (a model page's sampling values) is
  an input, not a slider. `labels` on a number names a special value (`{"0": "Off"}`). Ports and ids
  are always inputs. A time of day (`widget: time`) is the browser's time field.
- Each setting has one editor, on the page `settingsPages` (`hooks/use-settings.ts`) names for its
  path, because a setting in two places gets changed in one and ignored in the other. The page with
  the longest matching section owns a path (`pagesOf`); another page may show the same editor filtered
  to one source as a `view` (an extension's actions and messages on its own page). One config object is
  never split across two pages. Where something is edited on another page, a row shows what it holds
  and links there (Personality on Assistant).
- Makers' logos (Simple Icons) mark engines, services and extensions of a maker's service (Claude,
  Hevy, Spotify, Gemini for web search, MCP for an MCP server), drawn in the text colour like any icon.
  Home Assistant entities get a lucide icon for their domain.
- Quiet when fine, words when not, since nineteen green checks say nothing. Running may be an icon
  alone in a long list; every other state is an icon plus a word from the one state map
  (`STATE_WORDS`: Running, Starting, Not responding, Stopped, Failed; extensions add Needs a key and
  Not running), drawn by `StateIcon` (`words`: `problems` by default, `always` on Status). Its reason is
  a visible line (`StatusLine`); the popover may add detail but never holds the only reason. A problem
  shows at the thing, under Needs you on Status, and as a warning mark on Status in the sidebar and on
  his Settings link (`needsAttention`). A card about something that runs carries its state in its title
  only when it is not Running (`SectionForm`'s `status`).
- No view modes: a field is drawn or it is `hidden`. How a process runs (runs as, image, arguments,
  environment, ports, paths) lives in one closed How it runs fold at the bottom of its slot card, its
  driver beside the title; a custom engine's page is itself its How it runs and draws it open. The fold
  starts closed on every load and is never remembered. Above it the card keeps Runs on with the
  preset's line, the slot's own fields, "Off, so he cannot answer." with Turn on, what the pick still
  needs downloaded with its size against the free space and Download (then its progress, or why it
  failed with Try again), and, while the engine is on but not Running, its state with Log and Restart
  or Start, so a problem is never folded away.
  Nothing else is folded to hide settings; a fold elsewhere only shortens a long list.
- Destructive, slow or outside-reaching actions confirm once (`useConfirm`, worded as in Copy). The
  Save confirm also lists what starts, restarts and stops ("Discord: stops now"), a model download, and
  Home Assistant's exposure plan, with Save only beside Save and restart.
- A cleared field saves what it shows where empty means something. A field marked `empty_means_off`
  (quiet hours, chime, health path, quantization) saves `''` when cleared, so the default does not
  quietly come back; any other cleared field falls back to its default.
- A field that does not apply is not there: the schema says when it applies (`when`: the engine's
  driver, the engine command, another field; `drivers`; `hidden`), and the form leaves it out
  otherwise. Under a section switch that is off (`enabled_by`), fields keep full contrast and stay
  editable, the switch's help says "Off. These settings wait until you switch it on." unless it has
  its own, and keys do not warn (prune marks the switch `section_switch` and the rest `waits`).
- Keys are pasted, never shown (`SecretField`). Beside the field a word says where it stands: "Saved",
  or "Not set" for a required key whose section is on; an optional key (`Secret(optional=True)`)
  never warns. Its placeholder reads "Paste the bot token", "Paste to replace it" or "Optional", and
  its help says where to get it (`secret.help`). Remove is a labelled command that deletes the key at
  once and says so. A pasted key waits with the draft and Save writes it to `data/.env`; the variable
  name is config.yaml only, and a key write refused while the page is open to the network names it.
- An account an extension signs in to (`AccountField`) is Connect, or "Connected" with Disconnect,
  both acting at once like Remove. Connect opens the service's sign-in in a new tab; under it a line
  gives the address the service's app must list, with Copy it. Back on the page, it reads again.
- A map in a form (contacts, engine environment, volumes) is a table with Add, an entry dialog, and
  trash with Undo in its toast (`KeyedMapField`), column titles from the schema (`key_title`,
  `value_title`). A key picked from a list (`key_options`) shows its cover or icon, as every picker
  does (`ChoicePicture`; never an empty box), and its hint ("34 songs"); a name made only of emoji is
  drawn larger (`ChoiceName`); when the list cannot be fetched the key is a text box with the reason
  under it. Pairs are never a text syntax. A page keeps its own table only where it adds what the
  generic one cannot: Home Assistant's devices, the room boxes (live state). A key already in the list
  keeps Add or Done off and says "Already in the list".
- A command line (engine arguments, a start command) is one text area, one argument per line
  (`widget: args`), with a warning when a flag is given twice and "Reset to the preset" where a preset
  owns it. Other plain lists are one box per entry, Add beside the list's label and Remove always
  visible; they have no order. Every text box is the app's own `Input`, never the form library's.
- A row that opens something is a real link with a chevron (`DataTable`'s `rowLink`). Row actions live
  in an always-visible quiet menu with worded items (`RowMenu`). Below `sm` a table stacks: each row a
  block, its other columns led by their titles. An empty table shows the page's `empty` sentence.
- A list that cannot be fetched (no token yet, the server down) leaves a plain text box with the reason
  as a visible line under it; a value the list does not hold is looked up by itself and marked when it
  is not found.
- Something a section needs that is not a config field (a connection check, a link row) is a child of
  its `SectionForm`, drawn after its fields. A control that acts on a field
  without being one (Play beside Voice, Reset to the preset) is an adornment: `SectionForm`'s
  `adornments`, keyed by the dotted path from the section. It shows only while the form shows that
  field, and reaches the form through a React context (`lib/adornments.ts`), not rjsf's `formContext`,
  so it follows the page's state.
- A preview of unsaved settings plays through `usePreview` (the same streamed playback as Talk's read
  aloud): Play turns into Stop while it plays, one preview plays at a time (`takePlayback`), and a
  preview the running server cannot give is disabled with the reason in its tooltip (`PreviewButton`).
- Actions from every source are drawn by `ActionList`: the title, the summary, "Only looks" or "Changes
  things" (for an MCP server or a package, what it says about itself: "Says it only looks", "Says it
  changes things" or "Not marked safe"), and one Permission: On its own, Asks you first, Off. Read is
  never a choice, so a destructive action cannot be lowered quietly, and choosing On its own for one
  its source marked as needing your OK confirms. A row from an MCP server, a package or Home Assistant
  has a closed Details under its summary: Name he sees and What he is told. Home Assistant's actions are listed without a
  Permission choice, since one of its actions covers lamps and locks alike. Messages an extension
  may bring up are drawn by `KindList` (When and Where; Off is the last When). Both take the titles,
  summaries and descriptions the SDK, MCP and Home Assistant provide, never the model's prompt.
- The Home Assistant page shows the path from Home Assistant to him as data, because saving it changes
  Home Assistant too. Not set up, it is one card whose checks run against the draft address and the
  pasted token. Set up, it is the connection, the devices he can use grouped by Home Assistant's areas
  with summary lines only for exceptions, and his actions there, listed without a Permission choice.
  The Save confirm lists the exposure plan, and the toast says what Home Assistant got. The labels are
  in [Home Assistant](home-assistant.md#the-page).
- The Extensions page is a store, so it does not grow into one long page: a search over names,
  descriptions and action titles, then tiles under Needs you, On and Off with their counts. A tile is
  the extension's icon and name with where it comes from beside it ("MCP server"), two lines of
  description, and a word only when something needs saying ("Needs a key", "Not running", "Slows his
  first word a little"); tiles side by side keep one height, and the whole tile links to its page. The
  store has no fields of its own, so it is a `Page` with Save; Add an MCP server
  is its command, and its last line says what the extensions that are on add to every request.
- An extension's page (`/extensions/:id`) has the breadcrumb, its description as the intro, Log and
  "Restart <its name>" ("Start <its name>" while stopped) as commands, and the same sections in the same
  order on every one, empty ones left out ([extensions](extensions.md#the-extensions-and-actions-pages)
  lists them). An extension's own icon is a lucide name, loaded by `DynamicIcon`, or a logo as
  `simple-icons:<slug>`, loaded on first use with the logos' map in its own chunk (`lib/logos.ts`), so
  the main bundle does not carry Simple Icons.
- A dot in the sidebar, and on a store tile, marks unsaved edits. A saved change that waits for a
  restart is a few words beside Save ("Waiting for a restart: the brain", or "2 parts") with one
  Restart whose confirm names each part and its cost; the icon opens the details. The words shorten
  before the title loses a letter, and only in a phone's width does the icon alone stand for them, as
  the Restart button while something can restart. On Status the waiting part's state also says it
  ("Running, up 9 min, waits for a restart"), not Needs you. What only Winston's own restart applies
  says "Takes effect when Winston restarts".
- A refused save puts each error beside its field (a section's own beside its title), opens the How
  it runs that holds the field, and scrolls to it; that fold stays open until the error is fixed or the
  draft discarded. A key refused by its check (an extension's) shows under the field it was pasted into.
  Any other hidden field is on no page, so its error is only in the "Not saved" toast, marked
  "(in config.yaml)". A conflict reloads the config, keeps your edits on top and names the fields that
  changed. A save that fails outright says "Not saved" and why.

## Build

`pnpm build` runs the guardrails (`scripts/check.mjs`: em dashes in any file, CSS outside
`src/index.css`, class selectors in it, inline styles in HTML), ESLint (`style` prop, classes Tailwind
does not know, em dashes written as escapes), the type check and Vite. shadcn's generated
`src/components/ui` is the library and is exempt. `pnpm api` regenerates the client from
`winston api schema`.

`pnpm smoke` then drives the built pages in Playwright against a throwaway winstond (see
[running](running.md)): every settings page opens with nothing to save, and a phone pass opens every
settings and Winston page at 390 px wide, with every How it runs fold open, and fails on any sideways
scroll, a card's content wider than the card, a title cut short or a More menu item on two lines; extension pages, Home Assistant and Status are also
checked at 1280 px beside the sidebar.
`winston check unit` runs `tests/schema_copy_cases.py`, which checks the schema's copy.

## Templates

- Talk page: `pages/talk.tsx`, fed by `useLive` (the `/api/live` stream), with `Presence` in
  `WinstonHeader`.
- History: `pages/turns.tsx` at `/history`. Memory: `pages/memory.tsx` at `/memory`. Wall display:
  `pages/dashboard.tsx` at `/wall`. All under the same `WinstonHeader` but the wall display.
- Settings page: `SettingsPage` (`intro`, `back`, `commands` and Save) with one
  `SectionForm` per config section, a form from the schema pruned against the whole draft (`when`,
  `hidden`, `drivers`, `enabled_by`, `secret`), so pages do not filter fields themselves. `useSettings`
  holds the draft, changed paths over the saved config shared across pages, and the save flow: a
  dry-run patch, the confirm by page with restarts and their cost, `PATCH /api/config` with If-Match,
  pasted keys (`useSecretsDraft`), restart.
- A page without fields of its own (the Extensions store) or a document (Personality,
  `pages/personality.tsx` at `/assistant/personality`, saving each edit) uses `Page` directly.
- Status: `pages/status.tsx` at `/status`, the landing: Needs you and Set up next while they apply,
  his parts by role (each a row linking to its page, with one text command and a `RowMenu`), graphics
  memory, custom engines once there is one, and the web address with its warning; Start all and Stop
  all as commands, each only while it applies, and Add a custom engine. Its engine page (`pages/engine.tsx`, `/status/engines/:name`) is for custom
  engines only.
- Brain and Voice: `SlotCard` (Runs on, the slot's fields and, last, its closed `HowItRuns`: the state
  with Log and Restart, the engine block, the slot's server fields), in `components/slot-card.tsx`,
  over `Fold` (`components/fold.tsx`).
- List: `DataTable` (rows as links, stacking below `sm`) plus `ItemDialog` for an entry. Maps inside a
  form are drawn by `KeyedMapField` from the schema, not by the page.
- Shared pieces: `RowMenu`, `StatusLine`, `StateIcon`, `LogDialog`, `SearchField`, `ActionList`,
  `KindList`, `PreviewButton`.
