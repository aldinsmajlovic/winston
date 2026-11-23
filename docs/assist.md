# Home Assistant Assist

Home Assistant's own Assist can use Winston as its conversation agent. Its app, its Assist chat and
its voice satellites (a stock Voice PE, for one) then reach his brain: Home Assistant keeps the wake
word, the speech recognition and its own voice, and Winston does the thinking, with his memory, his
tools and extensions, and the check that a device really changed. Nothing is installed in Home
Assistant: it talks to him over its built-in Wyoming integration.

Winston's own voice path ([Voice PE](voice-pe.md), the Talk page) is separate and unchanged.

## Switching it on

Set `channels.assist.enabled: true` in `data/config.yaml` and restart winstond.

| Setting | Key | Default | What it does |
| --- | --- | --- | --- |
| Answer Home Assistant's Assist | `channels.assist.enabled` | off | Opens the port and announces him on the network |
| Port | `channels.assist.port` | 10800 | The TCP port Home Assistant connects to |

Once on, the port opens on every network address of this computer and Winston announces himself over
zeroconf. A save through the settings API applies a change at once, with no restart; switched off, the
port closes and the announcement is withdrawn.

**The port has no password.** Wyoming has none (Home Assistant's Piper and Whisper are the same), so
anyone on your network can then ask him to act, and spend any paid budget he has. That is why it is
off by default. Keep it on a network you trust.

## Adding him to Home Assistant

1. In Home Assistant, open Settings, Devices and services. Winston shows under Discovered; select Add
   and confirm.
2. Not discovered (another subnet, or mDNS blocked)? Select Add integration, pick Wyoming Protocol,
   and give this computer's address and the port.
3. Open Settings, Voice assistants, and edit a pipeline (or add one). Under Conversation agent, pick
   Winston.

Every device that uses that pipeline now talks to him: the app's Assist, the Assist chat in the
browser, and each satellite set to it.

## Rooms

A request from a device in an area joins that room's conversation, shared with any Winston box or
screen set to that room ([rooms](rooms.md)), and "the lights" means that room's lights. The area is
the device's own, else its satellite entity's, as Home Assistant's registry has them.

A device with no area, and a typed request from the app, get one conversation per Home Assistant
conversation, with no room. Home Assistant keeps its conversation across turns, so a follow-up
continues it; a new chat starts afresh.

Every conversation also starts fresh after the Talk page's New conversation after of quiet
(`channels.web.session_idle_mins`), and when settings are saved.

Without Home Assistant connected (no token), requests are still answered, with no room.

## What works and what does not

| Works | Does not |
| --- | --- |
| His memory, tools and extensions, as on every channel | His cloned voice: Home Assistant's own voice speaks |
| The state check after an action | Talking over him |
| A question in his reply: the satellite listens again with no wake word | Hearing the reply as it is written: it arrives whole |
| His reminders | Home Assistant's own timers ringing on a satellite |

- **Every reply is treated as spoken**: he thinks before a home action and keeps it short, as on his
  own boxes.
- **"Prefer handling commands locally" is not offered** for him: every request goes through Winston,
  so his memory and the state check apply.
- **Home Assistant's timers** cannot ring through him (its timer tools need the device, which his
  calls do not carry). Ask for a reminder instead.
- **A failure is said.** When his brain is stopped or fails, Home Assistant says "Sorry, I could not
  reach my brain." or "Sorry, something went wrong while I answered.", and the Talk page shows the
  error as for a typed message.
