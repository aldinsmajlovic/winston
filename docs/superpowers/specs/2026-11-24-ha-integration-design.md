# Winston's Home Assistant integration

Design draft, 2026-11-24. UI track step 3 in `docs/plan.md`, the part Wyoming cannot carry: his
state and the extensions' entities. Follows the Assist design (2026-11-23), which chose "Wyoming now,
an integration when entities are built".

## Intent

Things Winston knows become real Home Assistant entities: history, areas, dashboards, automations.
Hevy's "Days since last workout" sits on a dashboard. An automation flashes a lamp when he nudges
about training. A "chore done" button on a wall panel tells his extension. Today these values show
only on the extension's card and through winstond's API (`docs/extensions.md`).

## What changed since the Assist design

| | Then | Now |
| --- | --- | --- |
| Where people install it | HACS, a separate install | **The Marketplace**, built into Home Assistant from 2026.11 (released 2026-11-04). It is HACS brought into core: same catalog, same `hacs.json`. HACS cannot be installed on 2026.11 or newer |
| His Home Assistant | 2026.10.4 | Still 2026.10.4, so his acceptance runs through HACS; the files are the same |
| Streaming replies | Not possible over Wyoming | A conversation entity can stream through the chat log's delta API (2026.4 or newer) |

Publishing rules (hacs.xyz, read 2026-11-24): one integration per repository, under
`custom_components/<domain>/` at its root, `hacs.json` at the root (`name` required), a
`manifest.json` with `domain`, `documentation`, `issue_tracker`, `codeowners`, `name`, `version`,
brand assets (`brand/icon.png`). Anyone can add the repository by URL. The default list needs
Hassfest and the HACS check passing, a full GitHub release, and a review that "takes months".

## Approaches

1. **An integration in this repository (recommended).** `custom_components/winston/` at the root of
   the Winston repository, with `hacs.json` beside it. One repository, one version: the integration
   and winstond change together and are released together (`release.yml`). Adding the repository by
   URL works the day it is pushed. Cost: the default list wants a description, topics and a license
   for the whole repository, which it already has (Apache-2.0).
2. **A separate repository** (`winston-homeassistant`). Cleaner for the store's checks, but two
   versions to keep in step, and the API between them becomes a public contract on day one.
3. **MQTT discovery.** No install at all, but needs a broker this home does not run, and the plan
   already ruled it out (`docs/plan.md`, Extensions track).

## Goals

1. **Added in two steps.** Install from the Marketplace (or HACS, or the repository's URL); then
   Winston is discovered (zeroconf) and adding him asks for nothing more than confirming. Where
   discovery cannot reach (another VLAN, as in his home), the form asks for his address and a pairing
   code shown on his Ways to talk page.
2. **A token, scoped.** Pairing gives Home Assistant its own token. With it Home Assistant reaches
   only what the integration uses (below), on a second listener, never the control API on 8700.
   This is the panel endpoint's shape (UI track step 4): one outside listener, each token with a role.
   Revoking it is one button on Ways to talk.
3. **One device for Winston, one per extension.** Winston's device carries his own entities; each
   extension that declares entities gets its own device, linked to Winston's (`via_device`), named as
   the extension is. Unique ids are `<instance>_<extension>_<key>[_<entry>]`, with a per-instance id
   made once in `data/`, so renaming, areas and history survive restarts and reinstalls.
4. **Extensions' entities as declared.** Every kind the SDK has (`sensor`, `binary_sensor`, `event`,
   `switch`, `button`, `number`, `select`, `text`), with unit, device class, state class, icon,
   options and limits as declared. `per` entries added or removed on the extension's page add or
   remove entities. A switch flipped or a button pressed in Home Assistant runs the extension's
   `on_change` or `on_press`, and the value shows once the handler returns, as the SDK says now.
   Each extension also gets `event.<id>_nudge`, fired with every nudge it delivers, silent ones too.
5. **Live, not polled.** The integration holds one server-sent event stream; a value set by an
   extension reaches Home Assistant at once. When winstond is down, every entity shows unavailable,
   and they come back on reconnect with their latest values.
6. **Publishing is a permission.** On by default for an extension that declares entities, switched
   off per extension on its card (the plan says so); off removes its device.
7. **Docs.** A topic page (`docs/home-assistant-integration.md`, or a section of `docs/assist.md`):
   install, add, what appears, how to revoke. `docs/extensions.md` loses "until the integration
   exists".

## Non-goals (first version)

- A dashboard card.
- Replacing Wyoming. The Wyoming port stays the no-install path.
- Installing the integration from Winston's wizard (the Marketplace has a disclaimer the person
  should read).
- The default list submission: that waits for the first real users and a full release.

## Aldin's picks (2026-11-24)

1. **Winston's own entities: left to Claude.** On his device, only what winstond already knows:

   | Entity | Kind | What it is |
   | --- | --- | --- |
   | `<room> state` | `sensor` (enum), one per room | idle, listening, thinking or speaking; an automation can lower the music while he speaks |
   | `Announcement` | `event` | Fired with every announcement, with its text |
   | `<engine>` | `binary_sensor` (connectivity, diagnostic), one per engine | Up or down, for an alert when the brain stops |

   Dropped: the last room talked in (rarely useful in a one-person home, and the per-room state
   already says where he is busy). No new features, such as a "be quiet" switch.
2. **The conversation: a later step.** Entities first. A streaming conversation entity comes next
   in the same integration (Home Assistant speaks from his first sentence instead of the whole
   reply); Wyoming stays the no-install path.
3. **Pairing: a code shown on Ways to talk**, typed into Home Assistant's form. Winston does not
   add the integration to Home Assistant on his own.

## Constraints

- Home Assistant's integration rules: async only, `aiohttp` from Home Assistant's own session, a
  config flow, `iot_class: local_push`, no new requirements in `manifest.json` (the API is plain
  HTTP and server-sent events). Re-check the developer docs before building, per `CLAUDE.md`.
- Tested with `pytest-homeassistant-custom-component` against a fake winstond, plus Hassfest and the
  HACS action in CI. Acceptance on his Home Assistant (2026.10.4, through HACS).
- No personal data in the integration, its tests or its screenshots.
- Docs, plan status and decisions in the same commit as the code; no em dashes; `git commit -s`.

## Testing

- Unit, in Home Assistant's test kit: discovery and the pairing flow; a bad code refused; each kind
  of entity created with its attributes; a value pushed over the stream shows; a switch turned on
  calls winstond and shows the value after; a `per` entry removed removes its entity; winstond down
  makes everything unavailable and back restores it; a revoked token asks to pair again.
- winstond side, as `tests/extensions_cases.py` does: the second listener answers only the
  integration's routes, and only with the token; the control API stays on localhost.
- Acceptance on hardware: added on his Home Assistant across the VLAN; Hevy's and Reminders'
  sensors show with history after a restart of both; a nudge fires `event.hevy_nudge` and an
  automation reacts; revoking on Ways to talk makes the integration ask to pair again.
