# Rooms

A room is where a Voice PE box and a screen (the wall display on a tablet, the Talk page on a laptop)
are together. In a room they are one Winston: one conversation, one microphone at a time, and "the
lights" means that room's lights. A screen with no room works as before.

## Setting a room

- **A box** is added in Settings, Ways to talk, Room boxes: Add a box asks for its room and address,
  its encryption key for a box Home Assistant adopted (kept on this computer under a variable of the
  room's own, `VOICE_PE_KEY_MAIN_ROOM`, so two boxes never share one), its wake word and its Home
  Assistant area ([Voice PE](voice-pe.md) has the setup). The room is the box's key in `config.yaml`:

  ```yaml
  channels:
    satellite:
      devices:
        main room: {host: 192.168.1.50, wake_word: okay_winston, area: Main Room}
  ```

  `area` is the room's name in Home Assistant, for "turn off the lights". Unset, the room's own name is
  used. The field lists Home Assistant's areas (`/api/options/ha_areas`).
- **A screen** gets its room from the address, `/wall?room=main room` (a wall tablet's bookmark), or
  from the room picker on the line above the Talk page's message field. The browser remembers it. Names
  match without regard to case or spacing; the picker offers the boxes' rooms and Home Assistant's areas,
  and says what each choice shares. A screen can be in a room with no box (a wall tablet in a room of its
  own): "the lights" there means the Home Assistant area of the room's name.
- **Discord** has no room until you type `/room` in the DM; it clears after an hour without a message
  ([Discord](discord.md#saying-which-room-you-are-in)).

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="images/ways-to-talk-dark.png">
  <img src="images/ways-to-talk-light.png" alt="Ways to talk: the Talk page card with the wall display link, the room boxes with their state, and Discord" width="720">
</picture>

Each box on Room boxes says whether it is there now, with the same icons as Status: Connected,
Offline since 14:02, Never connected (not since Winston started), or Off while Listen through room
boxes is off. The satellite channel
reports each connect and disconnect (`PUT /api/rooms/{room}/box`), and when the channel stops its
boxes count as gone. A box's menu has Edit, Open wall display, Copy wall display link and Remove;
removing one asks first, and he stops listening there when you save. The Wall display row on the
Talk page card does the same for any room, a box's or a Home Assistant area: pick it, then Open, or
Copy link for the tablet.

## What a room does

- **One conversation.** A request started at the box can be continued at the screen and the other way
  round: "Okay Winston, what's the capital of Norway?" at the box, then "How many people live there?"
  at the wall display. Typed messages on the Talk page join the room's conversation too. It starts fresh
  after `channels.satellite.session_idle_mins` (10) without a new message, or when you start over:
  say "start over" or "new conversation" anywhere in the room, or press New conversation (left of the
  Talk page's text field). Either ends it for the box and every screen in the room; what he remembers
  stays. Without a room, saying it starts over that one conversation, and the button the household's
  typed one.
- **One mic at a time.** When the box wakes, a screen in the room stops listening until the box's reply
  has played (plus 4 s while the box listens again without the wake word, then for as long as it does).
  The screen shows "Microphone paused while the main room box has it". So the screen neither answers the
  request said to the box nor takes his voice from the box's speaker for a turn.
- **A wake word on its own is the box's.** People pause after "Okay Winston" to see the box light up;
  a screen in a room ignores a bare wake word ("okay", "ok", "hey" or "hi" and his name, or a box's own
  wake word), because it often hears it before the box has said it woke. A screen needs no wake word, so
  just talk to it. If the box misses the wake word, say it again.
- **Room-scoped actions.** In a room each message reaches him stamped with it, `[06:22, room: Main
  Room]`, so "turn off the lights" means the room's lights while "turn on the kitchen lights" still
  means the kitchen. A device named by its name keeps its own room: when the model sends a name with
  this room as the area and the device is elsewhere (the window lamps in the Living Room, asked from
  the Main Room), the area is dropped before Home Assistant sees the call, which it would refuse.

```mermaid
sequenceDiagram
  participant You
  participant Box as Voice PE
  participant Screen as Wall display
  participant W as winstond
  You->>Box: "Okay Winston, ..."
  Box->>W: holds the room's mic
  W-->>Screen: mic paused
  Box->>W: your request and his reply join the room
  W-->>Screen: mic back after the reply has played
  You->>Screen: "How many people live there?"
  Screen->>W: takes in the box's turn first
```

## How it works

winstond keeps each room's conversation. Every session in a room (the box's, a browser's voice session,
the Talk page's typed one) keeps its own context and links it to the room: before the model runs it
takes in what the others said since and hands over the new user message; after each reply it hands
over the reply. A question from another session with no answer yet (a tab closed mid-reply) waits
until an answer follows it, so the model never answers it in place of yours. The box holds the room's
mic with a lease it renews every 5 s during its turn; a browser session follows the room's mic and
turns its microphone into silence while the box has it. Details in `core/rooms.py`.

Each message the model sees starts with a stamp: the time, the room, and what extensions say is going on
now (`[09:30, room: Main Room; spotify: playing Oh Dove by Men I Trust]`, from `ctx.ongoing`), read fresh
each message. So "who is this?" or "not this one" in a new conversation knows the song, and the stamp stays
in the history, so the cached prompt does not change.

Each session keeps to a slot of the model server (llama.cpp with more than one, `brain.server_slot`):
spoken sessions (the box, a browser's voice) the first, typed ones, checks, trials and background calls
the last. Unpinned, llama.cpp gives a request the slot whose prompt shares the most with it, so a check
replaying a box request once took the box's slot, and his next request waited 18 s while his
conversation was read again. A session keeps the tools it started with; when an extension's tools
change (one switched on or off, or a tool's description or arguments), its next turn starts a new
session with them, and the room's conversation carries over.

| Endpoint | What |
| --- | --- |
| `GET /api/rooms` | The boxes' rooms and any room a screen is in: who has the mic, screens, messages, whether its box is connected and since when |
| `PUT /api/rooms/{room}/box` | `{connected}`: the satellite channel reports a box connecting or going away |
| `POST /api/rooms/{room}/sync` | A session hands over its new messages and takes in the others' |
| `POST /api/rooms/{room}/mic` | `{by, secs}`: hold the mic for secs; 0 lets go |
| `GET /api/rooms/{room}/mic` | Who has the mic, now and on every change (server-sent events) |
| `POST /api/chat/start-over` | `{room}`: a new conversation for the room's box and screens; no room, the household's typed one |

The live feed carries the mic too, as `mic` events with `room` and `held_by`.

## Measured

- `tests/rooms_voice.py` (headless, against `winston up`): a fake Voice PE and a WebRTC browser session
  in one room hear "Okay Winston, what's the capital of Norway?", the browser whole and the box from the
  end of the wake word, and the box's reply is played into the browser's mic as it streams. The box
  answers; the browser takes no turn (the mic pauses 6 ms after the wake, the bare wake word is dropped);
  "How many people live there?" at the browser is answered about Oslo. Passed three runs in a row.
- `tests/room_trials.py`: with the room in the stamp, "turn off the lights" and "dim the lights to 30
  percent" went to the Main Room's lights 8 of 8 times and "turn on the kitchen lights" to the kitchen
  4 of 4; without a room "turn off the lights" meant every light, 4 of 4.
- Named devices (2026-09-27, 8 home requests from the Main Room, actions stubbed): "turn on the window
  lamps" went out with `area: Main Room` 3 of 3 at every thinking setting; with the stamp's rule
  saying to leave the room out for a named device, 1 of 4, and that one is now caught in code
  (`rooms.scoped_by_name`, cases in `tests/rooms_cases.py`).

Not yet checked: a real box and a real wall tablet in the same room.
