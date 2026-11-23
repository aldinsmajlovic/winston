# UI track step 3, conversation part: Home Assistant Assist over Wyoming, Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** With one switch on, winstond answers Home Assistant's built-in Wyoming integration as a conversation agent: Home Assistant discovers "Winston", adds him with one click, and any Assist pipeline can pick him. Typed and spoken requests reach his brain in the room the request came from, and his announcements and nudges are spoken on Home Assistant's satellites in rooms with no Winston speaker connected.

**Architecture:** `src/winston/channels/assist.py` runs inside winstond, as the Talk page's typed chat does: a `wyoming` TCP server on every interface, a zeroconf advertisement, and an `Assist` object that keeps the conversations in a second `WebChat` (now named by channel), finds the room from Home Assistant's device registry, and speaks on Home Assistant's `assist_satellite` entities through `assist_satellite.announce`. The hub (`core/announce.py`) gets one hook for speakers Winston does not run. The brain treats channel `assist` as heard (thinking before a home action, shortcuts) but builds its prompt and picks its server slot as a typed channel. The Ways to talk page gets a card; the wizard's Home Assistant step gets the switch.

**Tech Stack:** Python, `wyoming` 1.11.0 (MIT, no runtime dependencies), `zeroconf` already installed (0.151.3, through aioesphomeapi), FastAPI, httpx; React with shadcn/ui.

**Spec:** `docs/superpowers/specs/2026-11-23-assist-wyoming-design.md` (approved 2026-11-23). Read `CLAUDE.md`, `docs/writing-docs.md` and `docs/ui-guide.md` first.

## Global Constraints

- The tree may hold Aldin's uncommitted work (on 2026-11-23: `docs/plan.md`, `docs/decisions.md`, `docs/hardware.md`, `docs/voice-pe.md`, `src/winston/config/presets.py`, `src/winston/engines/supervisor.py`, `tests/extensions_brain.py`, `tests/install_cases.py`, deleted `services/*/`, `services/voice-pe/record_takes.py`). Never stage it: `git add -p` and take only your own hunks in a file he has also changed; `git diff --cached` before each commit.
- If another Claude session on this PC is running brain trials, do not touch the GPU, the engines, or `winston up`/`down`. Every check here runs on fakes until the acceptance section.
- Commit straight onto `main`, signed off (`git commit -s`), no push unless Aldin asks. No em dashes anywhere (`just lint`). `uvx ruff check src tests sdk` clean.
- Tests never touch `data/` or the real Home Assistant (`harness.scratch_data`, `tests/fake_ha.py`). No zeroconf advertisement from a test: every test swaps the zeroconf factory for a recording fake, or a test winstond would show up as "Discovered" in the real Home Assistant.
- Docs, plan status and decisions in the same commit as the code they describe; one line per decision in `docs/decisions.md`.
- Protocol facts, as checked on 2026-11-23 against Home Assistant's `components/wyoming` (dev branch) and the `wyoming` 1.11.0 wheel: `Describe` is answered by `Info(handle=[HandleProgram])`; Home Assistant names the entry after the first installed handle program; each request is a new TCP connection with `Transcript(text, context={"conversation_id", "device_id"?, "satellite_id"?}, language)`; Home Assistant reads `Handled(text, continue_conversation)` or `NotHandled(text)` (it speaks the text of either) and never `HandledChunk`; an empty `languages` list in every model means Home Assistant offers the agent for every language (`MATCH_ALL`); `supports_home_control` sets the CONTROL feature. The server class is `wyoming.server.AsyncTcpServer(host, port)` with `start(handler_factory)` and `stop()`; a handler is an `AsyncEventHandler(reader, writer)` whose `handle_event` returns False to close.

## Builder's choices (the design left these to the builder)

1. **Port 10800.** Next in the Wyoming family's range, clear of Piper 10200, Whisper 10300, openWakeWord 10400 and satellites 10700, and of winstond's 8700 and the panel's planned 8710.
2. **Zeroconf:** type `_wyoming._tcp.local.`, instance name `assistant.name` (`allow_name_change=True`, so a second Winston on the network gets "Winston (2)"), the address of the interface that routes to the mDNS group (as `wyoming.zeroconf` finds it). The handle program is named `assistant.name`, so Home Assistant shows "Discovered: Winston".
3. **Channel `assist`.** In `core/brain.py`, `HEARD = (*SPOKEN, "assist")` replaces `SPOKEN` where a reply being heard matters (`thinking_for`, the home-request thinking and its shortcuts in `build_llm`). `SPOKEN` stays for what needs his own voice: the Breeze vocal events (Home Assistant's text-to-speech would say "sigh") and the server slot. So `assist` keeps to the typed slot, its prompt is the typed one `warm_typed` already warms, and the box's cached prompt in slot 0 is never pushed out. The home lines ("One moment.") go nowhere on this channel, as the reply arrives whole; an entity with `quiet_after` gives an empty `Handled`, as it gives silence on a box.
4. **A question:** `"?" in reply`, the rule the room box uses to listen again (`channels/satellite.py`, "anywhere: ... asked one").
5. **Languages: every one** (`languages=[]` on the model). He understands other languages and answers in `assistant.language`; a household whose pipelines are in their own language can still pick him.
6. **Speaking on Home Assistant's satellites is part of the switch.** With Assist off, nothing new speaks; an upgrade does not make Home Assistant's satellites start announcing.
7. **Conversations** live in a second `WebChat`, so stale ends, notes of what he said on his own, and corrections on the Talk page work as for typed chat. They start fresh after the Talk page's "New conversation after" (`channels.web.session_idle_mins`); `docs/assist.md` says so.
8. **The card:** a section form like Discord's, the switch in its header (`enabled_by`), Port under it, and one status line: "Home Assistant reached him at 14:02", "Not reached since Winston started. In Home Assistant: Settings, Devices and services, then Winston under Discovered.", or "Port 10800 is in use by another program."; when on, "Speaks on 2 Home Assistant satellites" when it found any. The warning about no password is the switch's help line.

## Review Focus

1. **The brain fails** (unreachable, an error, or no reply within 60 s): `NotHandled("Sorry, I could not reach my brain.")` or `NotHandled("Sorry, something went wrong while I answered.")`, never an empty answer or a dropped connection; the Talk page shows the error as for typed chat. Test in Task 3.
2. **A device without an area, or the app with no device:** one conversation per Home Assistant `conversation_id`, no room in the stamp; two requests with the same id share it, a new id starts afresh. Test in Task 3.
3. **A room with a Winston box connected and a Home Assistant satellite:** an announcement is said once, on the box; the satellite says nothing. The box goes away: the next one is said on the satellite. Test in Task 4.
4. **A Winston Voice PE still listed in Home Assistant** (its old entry not disabled, so its `assist_satellite` is `unavailable`): never a target. Test in Task 4.
5. **Port taken** when the switch goes on: the save succeeds, the card says the port is in use, nothing is advertised, and the next save with a free port starts it. Switched off: the port closes and the advertisement is withdrawn. Test in Task 3.
6. **Home Assistant not connected** (no token): requests still answer, with no room; announcements and nudges go only to Winston's own speakers. Test in Task 3.

---

### Task 1: The Wyoming server and the advertisement

**Files:**
- Modify: `pyproject.toml`, `uv.lock` (`uv add "wyoming==1.11.0"`; never the `zeroconf` extra, which pins zeroconf 0.88.0 against the installed 0.151.3)
- Create: `src/winston/channels/assist.py` (its protocol half)
- Test: `tests/assist_cases.py` (its protocol half)
- Modify: `docs/decisions.md` (one line: the dependency and why not its extra)

**Interfaces:**
- Produces, in `channels/assist.py`:
  - `SERVICE = "_wyoming._tcp.local."`
  - `info(cfg: WinstonConfig) -> Info`: one `HandleProgram(name=cfg.assistant.name, attribution=Attribution(name="Winston", url="https://github.com/aldinsmajlovic/winston"), installed=True, description=..., version=winston.__version__, models=[HandleModel(name=cfg.assistant.name, ..., languages=[])], supports_handled_streaming=False, supports_home_control=False)`.
  - `asks(reply: str) -> bool`: `"?" in reply`.
  - `@dataclass Request(text: str, conversation_id: str, device_id: str | None, satellite_id: str | None, language: str | None)`, `Request.of(t: Transcript)`; a missing `conversation_id` gets a new uuid hex.
  - `class Handler(AsyncEventHandler)`: built with `answer: Callable[[Request], Awaitable[tuple[str, bool]]]` and `seen: Callable[[], None]`. Calls `seen()` on every event; `Describe` gets `info(cfg)`; `Transcript` gets `Handled(text, continue_conversation=asks)` from `answer`, or `NotHandled(text)` when `answer` raises `Unanswered(text)`; returns False after either, so the connection closes. Anything else is ignored.
  - `class Unanswered(Exception)`: its message is what Home Assistant says.
  - `def local_address() -> str | None`: the IPv4 of the interface that routes to 224.0.0.251 (a UDP socket's `connect`, nothing sent); None without a route.
  - `class Listener`: `async start(cfg, port, answer, seen)` binds `AsyncTcpServer("0.0.0.0", port)` and registers `AsyncServiceInfo(SERVICE, f"{cfg.assistant.name}.{SERVICE}", addresses=[...], port=port)` through `ZEROCONF()` (module-level `ZEROCONF = AsyncZeroconf`, swapped in tests) with `allow_name_change=True`; no address, no advertisement (logged). `async stop()` unregisters, closes zeroconf, stops the server. A bind error raises `OSError` before anything is advertised.

- [x] **Step 1: Re-check before installing** (CLAUDE.md): `wyoming` on PyPI is still 1.11.0, MIT, no runtime dependencies, and Home Assistant's `components/wyoming/manifest.json` on dev still pins `wyoming==1.11.0`. If either moved, take the pinned one and note it in `docs/decisions.md`. Then `uv add "wyoming==1.11.0"`.

- [x] **Step 2: Write the failing checks in `tests/assist_cases.py`** (harness style, `expect`), protocol half: start a `Listener` on `free_port()` with a stub `answer` (returns `("Done.", False)` for "lights", `("Which room, sir?", True)` for "ask", raises `Unanswered("Sorry, I could not reach my brain.")` for "fail") and `ZEROCONF` swapped for a fake that records `async_register_service`/`async_unregister_service`/`async_close`. Through `wyoming.client.AsyncTcpClient`:

```python
expect("Describe: one installed handle program, named after him", [(h.name, h.installed, h.supports_home_control) for h in info.handle] == [("Winston", True, False)])
expect("every language", info.handle[0].models[0].languages == [])
expect("a request gets the reply", (ev.type, ev.data.get("text")) == ("handled", "Done."))
expect("a question listens again", Handled.from_event(ev2).continue_conversation is True)
expect("a failure is said, not dropped", (ev3.type, ev3.data.get("text")) == ("not-handled", "Sorry, I could not reach my brain."))
expect("advertised as a Wyoming service on its port", (zc.info.type, zc.info.port) == (SERVICE, port))
expect("stopped: withdrawn and closed", zc.calls[-2:] == ["unregister", "close"])
expect("a taken port raises before advertising", raised_oserror and zc2.calls == [])
```

- [x] **Step 3: Run it to see it fail.** `uv run python tests/assist_cases.py` fails with `ModuleNotFoundError: winston.channels.assist`.

- [x] **Step 4: Implement the protocol half of `channels/assist.py`** to the interfaces, with a module docstring that names the protocol facts above and links `docs/assist.md`.

- [x] **Step 5: Run it to see it pass**, then `uv run winston check unit`.

- [x] **Step 6: Commit** with the decisions line. `git commit -s -m "Assist: a Wyoming handle service and its zeroconf advertisement"`.

---

### Task 2: What the brain and the conversations need

**Files:**
- Modify: `src/winston/core/brain.py` (`HEARD`)
- Modify: `src/winston/channels/web.py` (a channel name, a conversation key, `reply()`)
- Modify: `src/winston/core/rooms.py` (`room_for_area`)
- Modify: `src/winston/providers/home_assistant.py` (`snapshot` reads the registry entries of `assist_satellite.*`)
- Test: `tests/assist_cases.py` (pure checks), the existing `tests/home_cases.py` and web chat checks unchanged

**Interfaces:**
- `HEARD = (*SPOKEN, "assist")` in `core/brain.py`: `thinking_for` and `build_llm`'s `home=` and `own=` read `channel in HEARD`; `_vocal_events` and `TextSession`'s `spoken=` keep `SPOKEN`.
- `WebChat(cfg, feed, rooms, channel: str = "web")`: every `"web"` literal in it becomes `self.channel`; the log lines say the channel. Its sessions' brain channel is `"text"` for `web` (as before) and the channel's own name otherwise, so `HEARD` reaches `assist` (found while building: `TextSession` defaults to `brain_channel="text"`). The `user` event carries the room, as the box's does. `tests/ui_data_cases.py` stubs `current` and `ask`, so its stubs take the new arguments.
  - `async current(room: str | None = None, conversation: str | None = None) -> TextSession`: keyed by the room's key, else `"#" + conversation` when given, else None (the household's).
  - `async reply(text, room=None, conversation=None, notify=None) -> str`: what `ask` does today up to the answer (stale end, the `conversation` event, the `user` event with `room`, the `thinking` state), sets `s.notify = notify` when given, returns `await s.ask(text, timeout=60)` and raises what it raises. `ask` becomes `reply` inside its try, unchanged for the Talk page.
- `room_for_area(cfg, area_name: str) -> str` in `core/rooms.py`: the configured room whose `area(cfg, room)` matches by `key`, else the area's own name. Docstring: a request from a Home Assistant device in that area joins that room's conversation.
- `HomeAssistant.snapshot(ids)`: with ids, the registry read also takes every `assist_satellite.*` entity in `states`, so `snap.area(eid)` knows each satellite's area. Nothing else in the snapshot changes.

- [x] **Step 1: Failing pure checks in `tests/assist_cases.py`:**

```python
expect("assist thinks before a home action, as a spoken channel", thinking_for(cfg_home, "assist") == thinking_for(cfg_home, "satellite"))
expect("assist's prompt has no vocal events", "[sigh]" not in persona(cfg_breeze, "", "assist"))
expect("a configured room by its area", room_for_area(cfg_box_in_area, "Living room") == "main room")
expect("else the area itself", room_for_area(cfg, "Kitchen") == "Kitchen")
```

  Where `cfg_box_in_area` has `channels.satellite.devices: {"main room": {"area": "Living room", ...}}` (generic names, as `tests/fake_ha.py` uses), and `cfg_breeze` has a `tts` engine whose `cwd` names `tts-breeze2`.

- [x] **Step 2: Run to see them fail; implement; run to see them pass.**

- [x] **Step 3: Nothing else moved.** `uv run winston check unit` passes, including the web chat, home and extensions cases; `uv run python tests/extensions_cases.py` still sees the satellite's slot 0 and the typed last slot.

- [x] **Step 4: Commit.** `git commit -s -m "Assist groundwork: a heard channel, web chat by channel and conversation, rooms from areas"`.

---

### Task 3: Answering in winstond, and the switch

**Files:**
- Modify: `src/winston/channels/assist.py` (the `Assist` object)
- Modify: `src/winston/config/schema.py` (`AssistChannel`, `Channels.assist`)
- Modify: `src/winston/api/app.py` (create, apply on save, close, `/api/assist`, the confirm wording)
- Modify: `tests/fake_ha.py` (a satellite in an area, a device with none)
- Create: `docs/assist.md` (how to add him and pick him, what works and what does not)
- Modify: `docs/decisions.md` (the builder's choices 1 to 5 and 7, one line each)
- Test: `tests/assist_cases.py` (a winstond on a spare port)

**Interfaces:**
- `class AssistChannel(Model)` in `config/schema.py`, after `DiscordChannel`'s pattern: `model_config = section(enabled_by="enabled", icon="homeassistant", help=...)` with no restarts (applied on save); `enabled: bool = False` titled "Answer Home Assistant's Assist", its help line saying anyone on the network can then ask him to act, as the port has no password; `port: int = 10800` (1024 to 65535) titled "Port". `Channels.assist: AssistChannel = Field(AssistChannel(), title="Home Assistant Assist")`. Every title and help line passes `tests/schema_copy_cases.py`.
- `class Assist` in `channels/assist.py`:
  - `Assist(cfg, feed: Feed, rooms: Rooms, hub: Hub, ha: Callable[[], httpx.AsyncClient])`, `ha` being `extension_services._ha_client` (the client `to_phone` already uses). `self.chat = WebChat(cfg, feed, rooms, channel="assist")`.
  - `async apply(cfg) -> None`: starts, restarts on a new port or stops the `Listener` to match `cfg.channels.assist`; a bind error is kept in `self.error` ("Port N is in use by another program.") and logged, never raised. Sets `hub.outside = self` while listening, else None. Resets `self.chat` with the new config.
  - `last_seen: float | None` (epoch seconds), `listening: bool`, `error: str | None`.
  - `async where(req: Request) -> str | None`: `directory.home(cfg)`; the area of `snap.devices[req.device_id]["area_id"]`, else `snap.area(req.satellite_id)`; then `room_for_area`. None without Home Assistant, a registry, or an area.
  - `async answer(req) -> tuple[str, bool]`: `room = await self.where(req)`; `text = await self.chat.reply(req.text, room, None if room else req.conversation_id, notify=...)`; returns `(text, asks(text))`. Errors become `Unanswered(...)` with the two sentences of Review Focus 1 (`openai.APIConnectionError` and its kin anywhere in the cause chain, as `channels/text.error_text` reads it, give the first), and the feed gets the `error` event and `idle` state as typed chat does.
  - `async close()`.
- `GET /api/assist` returns `AssistState(enabled: bool, listening: bool, port: int, error: str | None, last_seen: float | None, satellites: int)`.
- `app.py`: `assist = Assist(...)` beside `web`; `await assist.apply(cfg)` in `lifespan` (both `autostart` and not: a watching winstond does not listen, so only when `autostart`), `await assist.apply(new)` next to each `await web.reset(new)` (lines ~1382 and ~1454), `await assist.close()` beside `web.close()`; `confirm_on_phone`'s `where` gains `"assist": "through Home Assistant"`.
- `tests/fake_ha.py`, default seed: device `dev_satellite` "Kitchen satellite" in `kitchen`, entity `assist_satellite.kitchen_satellite` (state `idle`, not exposed); device `dev_old_box` in `living_room` with `assist_satellite.old_box` state `unavailable`. Run `winston check unit` after: a check that counts the seed's entities may need its number moved, and only that. (It was `tests/extensions_cases.py`'s device count, 5 to 7.) `/api/rooms` lists only configured rooms and those a screen is in, so the room check reads `app.state.rooms` instead. Until Task 5 draws the card, `docs/assist.md` says to switch it on in config.yaml; Task 4 and 5 add their parts to it.

- [x] **Step 1: Extend `tests/fake_ha.py`** as above.

- [x] **Step 2: Failing end-to-end checks in `tests/assist_cases.py`.** A winstond from `harness.winstond_data` on a spare port with the fake Home Assistant and an in-test streaming fake brain (aiohttp, `text/event-stream` chunks then `[DONE]`; it records every request and answers by the newest user message: "lights" gives "Done, sir.", "colour" gives "Which colour, sir?", "break" gives HTTP 500, anything with "Speaking up" gives "Your parcel has arrived, sir."). `ZEROCONF` swapped as in Task 1. Assist switched on with `PATCH /api/config` (`channels.assist.enabled: true`, `port: <free>`), then through `AsyncTcpClient`:

```python
expect("switched on by a save, listening", (await api.get("/api/assist")).json()["listening"])
expect("a typed request from the app is answered", handled("turn on the lights", conversation_id="app-1") == ("handled", "Done, sir."))
expect("Home Assistant was seen", (await api.get("/api/assist")).json()["last_seen"] is not None)
expect("from a satellite in an area: stamped with that room", "room: Kitchen" in last_user(llm_seen))   # satellite_id assist_satellite.kitchen_satellite, device_id dev_satellite
expect("and in that room's conversation", rooms_state("kitchen")["messages"] > 0)
expect("no area: its own conversation per id", "room:" not in last_user(llm_seen))
expect("the same id continues it", "turn on the lights" in transcript_of(llm_seen[-1]))      # second request, conversation_id app-1
expect("a question listens again", continue_of("what colour?") is True)
expect("a failing brain is said", handled("break it") == ("not-handled", "Sorry, something went wrong while I answered."))
expect("the Talk page shows the turn under assist", any(e["channel"] == "assist" and e["type"] == "user" for e in feed_events))
expect("a taken port: saved, said, not advertised", (st["listening"], st["error"], zc_registered_after) == (False, "Port N is in use by another program.", False))
expect("switched off: closed and withdrawn", connect_refused and zc.calls[-2:] == ["unregister", "close"])
```

  And once with the token variable unset in the throwaway `.env`: a request is answered with no room (Review Focus 6).

- [x] **Step 3: Run it to see it fail; implement; run it to see it pass.** Then `uv run winston check unit`, `uvx ruff check src tests sdk`, `just lint`.

- [x] **Step 4: Write `docs/assist.md`** (writing-docs style, no intro that restates the heading): switching it on and what that opens (no password, the whole network); adding him (Discovered, or Wyoming Protocol by host and port); picking him in an Assist pipeline; rooms from the device's area and the one-conversation-per-id rule; what works and what does not (his memory, tools, extensions and state check work; no cloned voice, no talking over him, the reply arrives whole, "Prefer handling commands locally" is not offered, Home Assistant's own timers do not ring through him and his reminders do that job); conversations start fresh after the Talk page's "New conversation after". Link it from `docs/home-assistant.md` and add it to the map in `docs/writing-docs.md`.

- [x] **Step 5: Commit** code, fake, tests, `docs/assist.md` and the decisions lines. `git commit -s -m "Assist: winstond answers Home Assistant's conversation requests, in the room they came from"`.

---

### Task 4: Speaking on Home Assistant's satellites

**Files:**
- Modify: `src/winston/core/announce.py` (`Hub.outside`, `publish`, `publish_nudge`)
- Modify: `src/winston/channels/assist.py` (`rooms()`, `say()`, `nudge()`, the correction)
- Modify: `src/winston/api/app.py` (`publish_announcement` and `deliver_nudge` also note into `assist.chat`)
- Modify: `docs/announcements.md` (where announcements and nudges are spoken), `docs/rooms.md` (a Home Assistant satellite's area), `docs/assist.md` (speaking up, the correction after the reply)
- Modify: `docs/decisions.md` (builder's choice 6)
- Test: `tests/assist_cases.py`

**Interfaces:**
- `Hub.outside: Assist | None = None` (typed under `TYPE_CHECKING`). `Hub._free() -> dict[str, list[str]]`: `outside.rooms()` without the rooms that have a Winston speaker subscribed (any channel in `SPEAKERS` with that room key); `{}` without `outside`.
- `Hub.publish(a)`: `speakers` counts the free satellites too, so `route()` (quiet hours, the Discord fallback) treats them as speakers; when it speaks, `outside.say(all free satellite ids, a.text)`. `Delivery.spoken_on` includes them.
- `Hub.publish_nudge(ev, room)`: the room has a Winston speaker: as today. Else the room has free satellites: `outside.nudge(ids, ev, room)` only, counted. Else (no room known, or a room with neither): as today to every Winston speaker, plus `outside.nudge` for every free room. Returns the count, so `deliver_nudge`'s phone and Discord fallbacks apply only when nothing spoke (unchanged code there).
- In `Assist`:
  - `rooms() -> dict[str, list[str]]`: room name to satellite entity ids, from the last read of `directory.home` (`assist_satellite.*` in `snap.states` whose state is not `unavailable` or `unknown`, with an area, through `room_for_area`); synchronous, and starts a re-read in the background when the last is older than `directory.READ_SECS`.
  - `say(ids, text) -> None`: spawns `POST /api/services/assist_satellite/announce` with `{"entity_id": ids, "message": text}` through `self.ha()`; a failure is logged with the status, never raised.
  - `nudge(ids, ev, room) -> None`: spawns: the room's session words it (`self.chat.current(room)`, `ask(nudge_note(ev, follow_up available), timeout=WORDING_SECS)` with `WORDING_SECS = 20`), then `say(ids, worded or ev["say"])`; the wording failing or timing out says the plain line, as a box does.
  - The `notify` passed to `chat.reply` in `answer`: the Talk page's correction line (the chat's own) and, when the request named a satellite, `say([req.satellite_id], text)`. A typed request from the app shows it on the Talk page only (design, Claude's calls).
- `app.py`: `publish_announcement` also spawns `assist.chat.announced(...)`; `deliver_nudge` also spawns `assist.chat.noted(...)` when speakers took it. Built: `Assist` stops keeping the `Rooms` object, which would shadow `rooms()`; `rooms()` is empty without Home Assistant's token, so a cached read never counts a satellite that cannot be reached (Review Focus 6); `speaking-up.md` says where a nudge goes now. The box subscriber in the checks is the hub's own `subscribe`, in process.

- [x] **Step 1: Failing checks in `tests/assist_cases.py`,** on the same winstond:

```python
expect("an announcement with only a satellite in the kitchen is said there", ha_writes_of("assist_satellite.announce") == [{"entity_id": ["assist_satellite.kitchen_satellite"], "message": "The washing machine is done."}])
expect("and counts as spoken, so Discord is not the fallback", delivery["spoken_on"] == 1 and not delivery["discord"])
expect("an unavailable satellite is never a target", "assist_satellite.old_box" not in all_announced_ids)
expect("a room with a Winston speaker: said there, not on the satellite", (box_got, new_ha_writes) == (1, []))   # a subscriber on /api/events?channel=satellite&room=Kitchen
expect("the speaker gone: the satellite again", new_ha_writes_after_unsubscribe == 1)
expect("a nudge in the room last talked in is worded, then said on its satellite", nudge_write["message"] == "Your parcel has arrived, sir.")
expect("quiet hours: nothing said on a satellite", ha_writes_during_quiet == [])
expect("a correction is said on the satellite the request came from", correction_write["entity_id"] == ["assist_satellite.kitchen_satellite"])   # the session's notify called with a correction
```

  And pure checks on `Hub.publish_nudge` with a stub `outside`: room with a box, room with only a satellite, no room.

- [x] **Step 2: Run to see them fail; implement; run to see them pass.** `uv run winston check unit` (the announcement and speaking-up cases unchanged), ruff, `just lint`.

- [x] **Step 3: Docs** as listed under Files.

- [x] **Step 4: Commit.** `git commit -s -m "Assist: announcements, nudges and corrections spoken on Home Assistant's satellites"`.

---

### Task 5: The card and the wizard's switch

**Files:**
- Modify: `web/src/pages/ways-to-talk.tsx` (the card, after Discord)
- Modify: `web/src/pages/setup.tsx` (`HomeAssistantStep`: the Assist section once connected)
- Modify: `web/src/api/status.ts` (`PARTS.assist: ['Home Assistant Assist', 'Home Assistant Assist']`), so the Talk page names the place of an `assist` line
- Generated: `pnpm api` after Task 3's schema and endpoint
- Modify: `docs/ui-guide.md` only if the card adds a pattern it lacks; `docs/setup.md` (the Home Assistant step offers the switch); `docs/images/` (Ways to talk, light and dark; the setup Home Assistant step if a shot of it exists)

**Interfaces:**
- `<SectionForm page={page} path={['channels', 'assist']} title="Home Assistant Assist">` with an `AssistStatus` child that reads `GET /api/assist` (`$api.useQuery`, refetched every 10 s while the page is open) and draws the one line of builder's choice 8, with the time in the page's clock format.
- In the wizard: the same `SectionForm` under `HomeAssistantSections`, only when `connected`; its Save and continue saves it with the rest.
- Built: `AssistCard` is exported from `ways-to-talk.tsx` and used by both. `pnpm smoke` 26 of 26; its copy switches `assist` off with the other channels, and the new case switches it on and off in the draft only, since a save would open the copy's port and announce it on the real network. The Ways to talk shot shows the page's top, which did not change, and the wizard's Home Assistant shot shows it before connecting, where the card is not drawn, so neither was retaken; the card has its own shot in `docs/assist.md` (`docs/images/assist-*.png`), taken on a throwaway winstond with the fake Home Assistant and a do-nothing zeroconf. Looked at in Chromium at 1400 and 390 wide, light and dark: no page errors, no sideways scroll, nothing to save on load.

- [x] **Step 1: `pnpm api`,** then the card and the wizard section, by `docs/ui-guide.md` (effect-named labels, one visible help line only where the label cannot say it, no uppercase labels, no mono).

- [x] **Step 2: Check:** `pnpm build`, `pnpm smoke` (every page opens with nothing to save; Ways to talk with Assist on and off), and look at it in Chromium on a throwaway winstond from `examples/` with `tests/fake_ha.py`, at 1400 and 390 wide, light and dark. Retake the shots with generic data only.

- [x] **Step 3: Commit** with `docs/setup.md` and the shots. `git commit -s -m "Assist: its card on Ways to talk and its switch in the wizard"`.

---

### Task 6: Acceptance on hardware, then the plan's status

Needs Aldin's go: it switches Assist on in his instance, adds an integration to his Home Assistant, and speaks on his satellites. Back up `data/` first (`tests-never-touch-real-data`), and check `winston status` and that no other session runs brain trials before using the brain.

- [ ] **Step 1:** Switch Assist on on the Ways to talk page. Home Assistant shows "Discovered: Winston"; confirm adds it. Pick Winston as the conversation agent of a test pipeline.
- [ ] **Step 2:** A typed request in Home Assistant's Assist chat is answered and acts (a living-room light); a spoken one through the Home Assistant app is answered and acts; a request from a Home Assistant satellite in an area lands in that room (the Talk page shows the room; "the lights" means that room's).
- [ ] **Step 3:** `winston announce "Test"` with no Winston speaker in that satellite's room: said there, once.
- [ ] **Step 4:** Timed from the end of speech to the first sound on a Home Assistant satellite, against his own Voice PE path, five requests each, median and range.
- [ ] **Step 5:** Record in `docs/plan.md` UI track step 3: what was built (the conversation part over Wyoming), the measured times, what was not checked. Reread `README.md` top to bottom: fold "Home Assistant's own Assist (its app, chat and stock Voice PE boxes) can pick him as its agent" into the existing Home Assistant or Voice PE line, never a new bullet, and link `docs/assist.md` with the other docs. Restore anything the check changed that Aldin did not want kept.
- [ ] **Step 6: Commit.** `git commit -s -m "Assist: checked against the real Home Assistant"`.
