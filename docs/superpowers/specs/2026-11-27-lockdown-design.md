# Lockdown

Design draft, 2026-11-27.

## Intent

Winston trusts everyone in the house: voice has no authentication, so whoever is in the room is
treated as you. That is the right default (plan §1: single user, no voice verification) until people
you do not trust are over. Then anyone near a room box can say "open malware.xyz on the desk", unlock
an exposed lock, or ask "what do you remember about him?", and Winston obliges.

Lockdown is a switch for those evenings. While it is on, nothing said to him changes anything until
you approve it on your phone, and nothing reachable by guests knows or volunteers your private facts.
It sidesteps speaker identification (a non-goal in plan §1): it needs no idea of who is speaking,
only that right now nobody speaking is trusted.

## What exists today

- **Tiers** (`docs/extensions.md#tiers`): read, act, restricted. A restricted extension tool called
  from a conversation does nothing; it becomes a `Pending` in the extension host
  (`extension_host/host.py`), the phone gets an actionable notification through Home Assistant
  (`confirm_on_phone` in `api/app.py`), and the Talk page shows Confirm and Decline. Approved within
  five minutes, winstond runs it (`Host.confirm`). Voice alone never runs one.
- **Home Assistant's tools have no tier choice** (`docs/home-assistant.md#what-he-can-do-there`): one
  action switches lamps and locks alike, so they all run on their own.
- **Computers' `open` is act** (`docs/computer.md`): anyone at the mic can open a web address on a
  computer today.
- **One place every tool call passes**: `WinstonLLM.register_function` in `core/brain.py` wraps every
  tool (Home Assistant's, extensions', his own) in `_no_repeat`, which already branches on
  `guard.tool_tier`.
- **winstond can already run a Home Assistant tool itself**: `HomeAssistant.call_tool` in
  `providers/home_assistant.py`, a short-lived MCP session, used for extensions' `ctx.tools`.
- **Remembered facts and the diary are in the system prompt** (`persona()` in `core/brain.py`), read
  when a session starts. `get_diary` is a read tool over the diary. Extensions with
  `memory = "read"` reach facts and diary through `ctx.memory`.
- **Conversations are shared across channels.** A room's conversation is shared by its box, any Talk
  page set to that room and Discord after `/room` (`RoomLink`, `core/rooms.py`); the Talk page's
  household conversation is one session for every device (`WebChat.sessions[None]`).
- **He volunteers things**: nudges play on the box in the most recent room (`deliver_nudge`), the
  Mentions step puts pending nudges into the next conversation (`nudges.mentions`), and extensions'
  jobs write lines into every channel's prompt.
- **The UI has a "who may open" check** (`who_may_open` in `api/app.py`) and a strict "this computer"
  check for pasting keys (`secrets.guard`: loopback client and a localhost Host header, so a reverse
  proxy on the same machine does not count).

## Goals

1. **Every action asks your phone.** While locked down, any tool call that is not read tier, in a
   guarded conversation (below), does not run. It becomes a confirmation like a restricted tool's: the
   phone notification, five minutes, then expired. Approved, winstond runs it and the outcome shows
   where restricted outcomes show now. He says it is waiting, as he does for a restricted tool.
   Covers Home Assistant's tools, extensions' tools and computers' tools alike.
2. **What he knows about you stays out of guarded conversations.** No remembered facts and no diary
   in the prompt; `get_diary` answers that it is not available right now; lines extensions' jobs wrote
   from data stay out (their fixed tool notes stay in); an extension tool that can read memory asks
   your phone as an action does. Reads that are not personal keep running: the house, the weather,
   the time, web search.
3. **He volunteers nothing personal.** Nudges go to your phone instead of the speakers, and
   mentions wait until lockdown is off.
4. **The UI answers only his computer.** While locked down, winstond's UI and API answer only his
   computer, checked as `secrets.guard` checks it (loopback client and a localhost Host header).
   Every other device, wall panels included, gets a "Locked down" page or a 403. Home Assistant's
   phone notification buttons keep working (they come back as Home Assistant events, not HTTP to
   winstond), as do Home Assistant's own listeners (Assist, the integration), which are guarded
   channels.
5. **Anyone can lock, only you can unlock.** Turning it on needs nothing: by voice ("lock down"),
   from the Status page, or `winston lockdown on`. Turning it off by voice is a
   confirmation on your phone. At his computer (`winston lockdown off`, or the Status page) it turns
   off at once.
6. **Guests do not write your history.** What was said in guarded conversations while locked down
   stays out of the nightly diary.
7. **It survives a restart.** A crash or `winston restart` comes back locked down.
8. **You can see it.** The Status page shows it on, with since when; `winston status` says so; the
   tool-call log marks each held call.

## Guarded and trusted conversations

While locked down, a conversation is guarded when a guarded channel takes part in it, and then it is
guarded for everyone in it.

| Where | While locked down |
| --- | --- |
| `voice`, `satellite` (room boxes, the Talk page microphone) | Guarded |
| `assist` (Home Assistant's Assist over Wyoming, and the integration's conversation agent) | Guarded |
| A room's conversation (its box, Talk pages set to the room, Discord after `/room`) | Guarded, for every channel in it |
| `web` from another device | Closed (goal 4) |
| `web` at his computer, not in a room | Trusted |
| `discord` DM, not in a room: already only your own user ID (`channels/discord.py`) | Trusted |
| `text` (`winston talk`, at the machine) | Trusted |
| A call with no conversation (`winston ext call`, the API at his computer without `channel`) | Trusted, as today |

## Non-goals

- Speaker identification or voice verification.
- A guest allow list (lights and music run on their own while the rest asks). Considered; one more
  list to keep. Add it if every lamp buzzing your phone turns out to be annoying.
- Gating reads beyond goal 2. Any read you consider private can already be set to Asks you first on
  Actions; that choice applies whether or not lockdown is on.
- Switching it on from Home Assistant (a switch entity, an automation on guest presence). Can be added
  later on top of the same state.
- Protecting settings when lockdown is off. Network-open mode stays as open as it is now; the Status
  page already warns about it.
- Checking state after an approved Home Assistant action (`core/verify.py`). The outcome is what Home
  Assistant answered.

## Anti-goals

- **A way to run an action without your phone** from a guarded conversation. Any path that skips the
  confirmation (a shortcut, a nudge action, a script exposed as a tool, a retry, a trusted channel
  joining a guarded room) is a bug.
- **Unlocking by voice.** "Lockdown off" said aloud must never take effect on its own, whatever the
  wording.
- **Leaking memory through another door**: the diary tool, an extension that reads memory, a nudge,
  a mention, a reply from a trusted channel synced into a guarded room, or a session that started
  before lockdown and keeps its old prompt.
- **Locking you out.** If the phone notification cannot be sent (no phone picked under speaking up,
  Home Assistant down), you can still unlock at his computer and approve on the Talk page there.

## Approaches

1. **One check in the wrapper every tool call passes (chosen).** `_no_repeat` in `core/brain.py`, for
   a non-read tool in a guarded conversation while locked down, hands the call to winstond's
   confirmation queue instead of running it. One place covers every source. On approval winstond runs
   a Home Assistant tool through `HomeAssistant.call_tool`, which exists, and an extension tool as it
   does now.
2. **A check per source.** Extension and computer actions become restricted while locked down (one
   line in `Host.tier`), and Home Assistant's are refused outright. Much less work, but Home
   Assistant drops to questions only, so a guest's lamp never reaches your phone. Rejected: not what
   was asked.
3. **Un-expose everything in Home Assistant during lockdown.** Uses Home Assistant's own exposure,
   but changes his tool list (a new conversation), breaks Home Assistant's own Assist for you, and a
   crash mid-lockdown leaves exposure half changed. Rejected.

## Decided

- **Approach 1**, the check in the wrapper.
- **Every action asks your phone**, Home Assistant's included (over refusing them, or a guest list).
- **Memory and the diary leave guarded prompts**; other reads run.
- **On by anyone, off by your phone or at his computer.**
- **Not switched from Home Assistant for now.**
- **A conversation with a guarded channel in it is guarded for everyone in it**, and switching
  lockdown on starts every room's conversation afresh.
- **The UI and API answer only his computer** while locked down.
- **Nudges go to the phone, mentions wait, data-written extension lines stay out, memory-reading
  extension tools ask.**

## Calls made by the designer (check these)

- **His own tools that change your data or reach people are refused while locked down, not queued**:
  `remember`, `forget`, `send_message`, `clone_voice`, `use_voice`. They run inside the channel's
  process, and running them later from winstond is work nobody asked for; a guest has no reason to
  use them. `start_over` and `mark_mistake` keep running (harmless). `follow_up` is refused too: it
  runs in the channel's process, and with nudges off the speakers and mentions waiting, a guarded
  conversation has nothing to follow up. In general winstond refuses a held call it cannot run itself,
  so a tool only a channel has is refused, never left waiting.
- **State lives in a file in `data/`** (its presence means locked down, its content says since when
  and from where it was turned on), not in `config.yaml`: a config field would go through the
  settings save and restart channels. Every process reads it, so the voice subprocess needs no new
  connection to know.
- **Turning it on starts every guarded conversation afresh**: nothing said before carries into the
  guests' evening. **Turning it off** starts them afresh too (memory comes back), and what was said
  carries over, as when extensions change (`docs/extensions.md`).
- **His reply while waiting** reuses the restricted wording: "needs your confirmation first, on your
  phone, within five minutes. Nothing has happened yet." He does not name you or say why.
- **The notification says it is lockdown**: "Lockdown: Turn on the Kitchen light? Asked on a room
  box.", so you know why your phone buzzed for a lamp.
- **Lockdown off by voice is a confirmation like any other** (same queue, same phone buttons),
  labeled "Lockdown: turn off?".
- **"His computer" is `secrets.guard`'s definition**, not a bare loopback address, so a reverse proxy
  on the same machine is another device.
- **A held call keeps the arguments the model gave**, before `rooms.scoped_by_name`: the check sits in
  the outer wrapper. A device named with the wrong area then fails on approval and the outcome says so.
- **The diary skips guarded turns** by the lockdown windows: on and off are logged with times, and the
  diary writer leaves out turns from guarded channels inside a window.

## Left to the builder

- How the wrapper hands a call to winstond from the voice subprocess (an endpoint beside
  `/api/confirmations` is the obvious shape) and how `Pending` grows a Home Assistant kind next to
  extension tools and nudge actions.
- Whether an extension tool held by lockdown goes through the same new path or through
  `Host.call_tool` with a flag; either is fine as long as there is one decision point (the wrapper).
- How a room learns it is guarded (a flag on the room, set when a guarded channel joins it while
  locked down, or simply every room while locked down; rooms are where boxes are, so the second is
  likely the same thing and simpler).
- Telling job-written extension prompt lines from fixed tool notes, and how a memory-reading
  extension's tools are held.
- The voice tool's name and wording (`lockdown`, on or off), the Status page's control, and the
  "Locked down" page.
- What `winston check` runs for it (below).

## Acceptance (on hardware)

With lockdown on:

1. Through a room box, "open example.com on the desk": nothing opens; the phone shows Confirm and
   Decline; Confirm opens it. Decline or five minutes: nothing.
2. Through a room box, "turn on the kitchen light": the same, run by winstond through Home Assistant.
3. Through a room box, "what do you remember about me?": he has nothing to say; "what did we do
   yesterday?": the diary is not available.
4. From Discord with `/room` set to that box's room, an action asks the phone too, and what he says
   there has no remembered facts in it.
5. A wall panel or another phone opening the UI gets the Locked down page; his computer gets the UI.
6. A nudge that would play on the speakers goes to the phone; a mention does not come up at the box.
7. Through a room box, "lockdown off": phone confirmation; until approved it stays on.
8. `winston restart`: still on.
9. A Discord DM outside a room, or `winston talk`: runs at once, with memory.
10. With no phone picked, `winston lockdown off` at his computer still works.
11. The next diary entry has nothing from the guarded conversations.

A test in `tests/` with `tests/fake_ha.py` covers 1 to 6 and 9 without hardware; the on-hardware run
is what marks it done (CLAUDE.md).

## Docs

- A new page `docs/lockdown.md` (what it does, guarded conversations, turning it on and off, what it
  does not protect), linked from `docs/extensions.md#tiers`, `docs/home-assistant.md#what-he-can-do-there`
  and `docs/computer.md`.
- `docs/plan.md`: the track step, status and measurements.
- `docs/decisions.md`: one line, lockdown instead of speaker identification for guests.
- Screenshots of the Status page control and the Locked down page, light and dark.
- The README only if it fits an existing Features line.
