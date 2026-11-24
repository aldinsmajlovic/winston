# Home Assistant integration

Winston's integration for Home Assistant shows what he is doing and what his extensions read as real
Home Assistant entities. They keep their history, can be put in areas and on dashboards, and can start
automations. Buttons and switches an extension declares work from Home Assistant too.

This is the third way Winston and Home Assistant meet:

| Page | Direction |
| --- | --- |
| [Home Assistant](home-assistant.md) | Winston controls the devices on his list |
| [Home Assistant Assist](assist.md) | Home Assistant's app, chat and satellites talk to his brain |
| This page | Home Assistant sees his state and his extensions' entities |

## Installing it

The integration lives in Winston's repository, `custom_components/winston`.

| Home Assistant | How |
| --- | --- |
| 2026.11 and newer | The Marketplace (Settings, Marketplace): add `https://github.com/aldinsmajlovic/winston` as a custom repository of type Integration, then download Winston |
| 2026.10 and older | HACS: the three dots, Custom repositories, the same address with type Integration, then download Winston and restart Home Assistant |

It is not in the store's default list yet, so the address has to be added by hand.

## Pairing

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="images/ha-integration-dark.png">
  <img src="images/ha-integration-light.png" alt="The Home Assistant integration card on Ways to talk: the switch, the port, and a six-digit pairing code with the time it stops working" width="720">
</picture>

1. On Winston's Ways to talk page, under Home Assistant integration, switch on Show him in Home
   Assistant and save. The port opens and he announces himself on the network.
2. Select Pair. A six-digit code shows for ten minutes.
3. In Home Assistant, open Settings, Devices and services. Winston shows under Discovered: add him and
   type the code. Not discovered? Discovery stays inside one subnet, so on another VLAN select Add
   integration, pick Winston, and give this computer's address, the port and the code.

Cannot connect means a firewall drops the port. Open it to Home Assistant's address only, on this
computer and on the router between the subnets, as for [Assist](assist.md#adding-him-to-home-assistant).

| Setting | Key | Default | What it does |
| --- | --- | --- | --- |
| Show him in Home Assistant | `channels.ha_integration.enabled` | off | Opens the port and announces him |
| Port | `channels.ha_integration.port` | 8710 | The TCP port the integration connects to |

Saving applies it at once, with no restart.

## What shows up

One device for Winston, and one for each extension that declares entities, linked to his.

| Entity | Example | What it is |
| --- | --- | --- |
| One per room box | `sensor.winston_kitchen_state` | idle, listening, thinking or speaking |
| One per engine and channel | `binary_sensor.winston_brain` | Up or down (diagnostic) |
| Announcement | `event.winston_announcement` | Fires with every announcement, with its `text` |
| An extension's nudges | `event.hevy_nudge` | Fires with every nudge it delivers, silent ones too: `kind`, `level`, `where`, `text` |
| An extension's own | `sensor.hevy_days_since_last_workout` | Whatever it declares, below |

Entity ids come from the names, so a renamed entity keeps its history: the unique ids are this
Winston's instance id (`data/instance`) and the extension's own keys.

## Extensions' entities

An extension declares them in its code (see the [SDK](extension-sdk.md#entities)); each kind becomes the
Home Assistant entity of the same name.

| Kind | In Home Assistant |
| --- | --- |
| `sensor`, `binary_sensor` | Shows what the extension sets, with its unit, device class and state class |
| `event` | Fires each time the extension sets it; the value is the event type when it is one of its options |
| `switch`, `number`, `select`, `text` | Setting it runs the extension's `on_change`; the new value shows once that returns |
| `button` | Pressing it runs the extension's `on_press` |

When a handler fails, Home Assistant shows its error and the value stays as it was.

An entity per entry (`per=From("chores")`) comes and goes with the entries on the extension's page.

Each extension's Readings card has a Show in Home Assistant switch, on by default. Off removes its
device from Home Assistant.

## While Winston is away

When winstond stops or the network drops, every entity shows as unavailable. The integration retries
within 30 s, and the entities come back with their latest values.

## Safety

- **Home Assistant gets its own token.** The pairing code gives it a token that winstond keeps in
  `data/.env` as `WINSTON_HA_TOKEN`. The port answers nothing without it, except a pairing attempt.
- **The token reaches only this.** The port serves the integration's three routes: pairing, the
  stream and setting an extension's entity. Winston's settings and the rest of his API stay on
  their own port, which listens on this computer only by default.
- **A code works once, for ten minutes.** Five wrong tries void it.
- **Forget Home Assistant** on the card ends the stream and refuses the token at once. Home
  Assistant then asks to pair again, with a new code.

## How it talks

For anyone changing either side. winstond's side is `src/winston/api/ha_link.py`; Home Assistant's is
`custom_components/winston`.

| Route | What it does |
| --- | --- |
| `POST /pair {code}` | Gives `{token, instance, name}` |
| `GET /stream` | Server-sent events: a `snapshot` of everything shown, whole, at the start and whenever it changes; an `event` for each announcement and nudge |
| `POST /set {extension, key, entry, value, press}` | Runs the extension's handler |

Discovery is zeroconf, `_winston._tcp.local.`, with the instance id in its properties.

Checks:

| Check | Covers |
| --- | --- |
| `tests/ha_link_cases.py` | winstond's side: pairing, the stream, handlers, publishing, unpairing (part of `winston check unit`) |
| `tests/ha_integration` | The integration in Home Assistant's own test kit, in its own venv: `cd tests/ha_integration && uv run pytest` |
| `.github/workflows/ha-integration.yml` | Those tests, Home Assistant's `hassfest` and the store's check, on every change to the integration |
