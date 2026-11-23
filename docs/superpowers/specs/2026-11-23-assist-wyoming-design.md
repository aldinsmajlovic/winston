# Winston as Home Assistant's conversation agent, over Wyoming

Design, 2026-11-23. UI track step 3 in `docs/plan.md`, its conversation part only.

## Intent

People who already run Home Assistant should be able to try Winston without new hardware and without
installing anything into Home Assistant. They pick Winston as the conversation agent of an Assist
pipeline; Home Assistant keeps the wake word, speech recognition and its own text-to-speech voice, and
Winston does the thinking: his memory, his tools and extensions, the check that a device really changed.
This is the lever with the most reach for adoption: stock Voice PE boxes, the Home Assistant app and the
Assist chat all reach him this way.

Aldin chose voice as well as text: anything said to a Home Assistant satellite that runs a pipeline
with Winston as its agent reaches him. This replaces "text only" in `docs/decisions.md` (2026-09-23)
for this path. Winston's own voice path (his Voice PE firmware, the browser) is unchanged and still the
one with the cloned voice and talking over him.

## Approaches

1. **Wyoming, nothing installed in Home Assistant (chosen).** Home Assistant's built-in Wyoming
   integration already accepts an outside conversation agent (a "handle" service): it discovers the
   service by zeroconf, adds it with one click, and offers it as a conversation agent in pipelines. Each
   request is the text, a conversation id and the device it came from; the reply is the text it speaks.
   Costs: the reply arrives whole (Home Assistant reads only `Handled`, no `HandledChunk`, as of its dev
   branch on 2026-11-23), Wyoming has no authentication, and this path can never carry entities.
2. **A custom integration from HACS.** A setup form (address, token) and a conversation entity that
   streams the reply so Home Assistant speaks from the first sentence; later the same integration
   carries his state and the extensions' entities. Costs: people need HACS first, a much bigger build
   tested with Home Assistant's own test kit, and token auth in winstond.
3. **Both, Wyoming first.** Chosen by Claude after Aldin said he did not know which: Wyoming now, the
   HACS integration when entities are built, and whether it then also takes over the conversation (for
   streaming) is decided then. This design covers only the Wyoming part.

Dropped: pointing Home Assistant's own OpenAI or Ollama integration at Winston. Home Assistant would
send its own prompt and tools and hold the conversation itself.

## Goals

1. **Found and added, once switched on.** Off by default (Aldin's pick): the port has no password, and
   anyone on the home network could ask him to act or spend paid budgets. One switch turns it on,
   offered in `winston setup`'s Home Assistant step and on the Ways to talk page. Once on, it listens on
   every interface (Home Assistant usually runs on another computer) and winstond advertises it over
   zeroconf (`_wyoming._tcp.local.`); Home Assistant shows "Discovered: Winston", confirming adds it,
   and "Winston" can be picked as the conversation agent of any Assist pipeline. Adding it by host and
   port by hand also works. Switched off, the port closes and the advertisement is withdrawn.
2. **Answers like himself.** A request runs his brain with everything it has on other channels: memory,
   Home Assistant through MCP, extensions, the state check after an action, the reply filter.
3. **Written to be spoken.** Home Assistant does not say whether a pipeline will speak the reply, so
   every reply is treated as spoken: the rules of his spoken channels apply (thinking only before a home
   action, replies short and with nothing that cannot be said).
4. **In the room it came from.** The request's device (`device_id`, or `satellite_id`) is looked up in
   Home Assistant's device registry, which Winston already reads; its area is the room. The request then
   joins that room's conversation, shared with any Winston box or screen set to that room
   (`core/rooms.py`), and "the lights" means that room's lights. A device without an area, or a typed
   request from the app, joins one conversation per Home Assistant conversation id.
5. **Asks back.** When his reply asks a question, `continue_conversation` is set, so a satellite listens
   again without the wake word.
6. **Speaks up through Home Assistant's speakers** (Aldin's pick). A Home Assistant satellite (an
   `assist_satellite` entity in an area) counts as its room's speaker only while no Winston speaker (a
   box, or a browser such as a wall tablet) is connected in that room: nothing is said twice, and a room
   whose box is offline still hears him. Announcements, which have no room today and go to every
   connected speaker (`Hub.publish`), also go to every room whose speaker is a satellite. Nudges go to
   the room last talked in (`rooms.recent()`), as on boxes; a satellite that spoke one counts as a
   speaker, so the phone and Discord fallbacks (`deliver_nudge`) apply only when nothing spoke. Sent
   with `assist_satellite.announce`, in Home Assistant's voice; nudges are worded by his brain first, as
   the other speaking channels do (`core/announce.py`). It speaks but does not listen afterwards (see
   Claude's calls).
7. **A setting.** On the Ways to talk page: Home Assistant Assist on or off, and its port. The card says
   whether Home Assistant has added him (a connection seen recently).
8. **Docs.** A topic page (`docs/assist.md`): how to add him and pick him in a pipeline, what works and
   what does not (no cloned voice, no talking over him, the reply arrives whole). The README's Voice PE
   section and Features change only where this alters what Winston is at a glance.

## Non-goals

- The HACS integration, entities, streaming replies, a card.
- Winston's own voice through Home Assistant's pipeline (Home Assistant's text-to-speech speaks).
- Talking over him on a Home Assistant satellite.
- Wyoming's intent path (`Intent` events): Winston always answers with `Handled` or `NotHandled`.
- Authentication on the Wyoming port. Wyoming has none (Piper and Whisper are the same). winstond's own
  API listens only on its computer by default (`control.host: 127.0.0.1`), so this port, once switched
  on, is the first way in from the network without a password; hence off by default. Auth stays on the
  list of adoption concerns.
- Listening after a nudge on a Home Assistant satellite (`assist_satellite.start_conversation`).

## Anti-goals

- Never silent. A brain that fails or times out answers `NotHandled` with a sentence Home Assistant can
  say, never nothing.
- The direct Voice PE path, the browser and Discord keep working exactly as before.
- No personal data in code, tests, docs or screenshots (the repo rule).

## Constraints

- `wyoming` 1.11.0 from PyPI (MIT), the version Home Assistant pins; `zeroconf` is already installed
  (through `aioesphomeapi`). Re-check both before installing, per `CLAUDE.md`.
- The protocol as Home Assistant uses it (its `components/wyoming`, dev branch, 2026-11-23): a `Describe`
  answered with `Info` carrying one installed `HandleProgram` (name, attribution, version, models with
  their languages; `supports_home_control` makes Home Assistant mark the agent as able to control the
  home). Then per request a new TCP connection: `Transcript(text, context={conversation_id, device_id?,
  satellite_id?}, language)`, answered by `Handled(text, continue_conversation)` or `NotHandled(text)`.
  Home Assistant's chat session keeps the conversation id across turns.
- Runs inside winstond, as the Talk page's typed conversations do (`channels/web.py`, `TextSession`).
- Home Assistant must be connected to Winston (the token in `data/.env`) for rooms and announcements;
  without it, requests still work with no room.
- Docs, plan status and decisions in the same commit as the code; no em dashes; `git commit -s`.

## Testing

- Unit checks with a fake Wyoming client against a winstond on a spare port, with the fake Home
  Assistant and a fake brain, as `tests/extensions_cases.py` does: `Describe` gives the handle program;
  a request gets the brain's reply; a request from a device in an area reaches the brain stamped with
  that room and shares its conversation; a question sets `continue_conversation`; a failing brain gives
  `NotHandled`; the zeroconf service is registered with the right type and port; an announcement for a
  room with only a Home Assistant satellite calls `assist_satellite.announce` on the fake.
- Acceptance on hardware: the real Home Assistant discovers Winston; he is added and picked in a
  pipeline; a typed request in the Assist chat and a spoken one through the Home Assistant app are
  answered and act; a request from a satellite in an area lands in that room. Timed from the end of
  speech to the first sound, against his own Voice PE path.

## Claude's calls (made after the builder check; Aldin to check)

- **A failed state check is said after the reply, not inside it.** `Handled` goes back at once; when the
  state check (`core/verify.py`) then finds the device did not change, the correction is said on the
  satellite the request came from (`assist_satellite.announce` on its `satellite_id`), shown on the Talk
  page, and joins the conversation so a follow-up knows. A typed request in the Assist chat does not
  hear it. Why: holding every reply for the check adds up to `verify.wait_secs` (3 s) to every action,
  for a correction that is rare.
- **`supports_home_control` is false.** Every request goes through Winston, so his memory, tools and
  state check apply, and Home Assistant does not offer "Prefer handling commands locally" for him. Its
  own timers cannot ring on a satellite through him (its MCP timer tools need the device, which MCP
  calls do not carry); his reminders do that job and are spoken on the satellite (goal 6).
  `docs/assist.md` says so.
- **Nudges on a satellite are announced, not asked.** `assist_satellite.announce` speaks and stops, so
  "remind me later" after one needs the wake word, unlike on his own boxes. Listening afterwards
  (`start_conversation`, worded inside the room's conversation) is a later step.

## Left to the builder

- The default port (not 10200, 10300, 10400 or 10700, which Piper, Whisper, openWakeWord and satellites
  use) and the zeroconf service name.
- Which brain channel name these sessions use, and how it fits `SPOKEN`, server slots and the live feed
  (the Talk page should show these turns like any other channel's).
- How "his reply asks a question" is decided.
- The handle program's languages: `assistant.language`, or every language, since he understands others.
- The card's layout, by `docs/ui-guide.md`.
