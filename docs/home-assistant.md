# Home Assistant

Winston sees and controls only what is on his list (`providers.home_assistant.expose`, the Home
Assistant page). Home Assistant's own MCP server turns that list into his tools and his picture of the
home.

## From a device to what he sees and does

```mermaid
flowchart LR
  I["Integration<br/>(Matter, Hue, Plex...)"] --> D[Device] --> E["Entity<br/>light.shelf_lamp"]
  E --> X{"Exposed to Assist?"}
  X -- yes --> M["HA's MCP server<br/>(mcp_server integration)"]
  M --> T["Tools:<br/>turn on/off, set light,<br/>live context, to-do..."]
  M --> C["GetLiveContext:<br/>names, aliases, areas, state"]
  T --> W((Winston))
  C --> W
  L["Winston's list<br/>(providers.home_assistant.expose)"] -. "Save on the page<br/>(or winston ha expose)" .-> X
```

- Only entities exposed to Assist reach the MCP server. Winston's list decides which those are; anything
  else exposed in Home Assistant is un-exposed on the next save and shows as drift until then.
- The tools depend on what is exposed: an exposed light brings "Set brightness and colour", a to-do list
  brings the to-do tools. A channel reads the tool list when it starts, which is why saving the list
  marks the brain channels for a restart.
- An exposed script becomes a tool of its own, described by the script's description, so a scene with
  several steps (lamps one after another, music) is one call. Say in the description when to run it and
  give it the phrases as aliases: an alias in what was said also makes a spoken request think first.
  `say_first` on the entry is what he says right away when a spoken request names it, in place of "One
  moment." ("Movie night it is."), and `quiet_after` makes him say nothing once it has worked and keeps
  the box from listening on, so a scene with music is not talked over or answered; a failure is still
  reported. `shortcut` runs a script straight away, without the model, when what was said is only one of
  its names ("Okay Winston, movie night"), so it does the same every time, twice in a row too; add what
  the speech recognition makes of the phrase as an alias ("make it six in here"). The Home Assistant
  page's edit dialog has them as "Said first", "Quiet after" and "Run when named".
- He learns names only from GetLiveContext: each entity's name, its aliases, its area and its state.

## Aliases matter more than rooms

Home Assistant matches a spoken name to an entity by its name or an exact alias. Targeting by room
failed twice in testing (a group light has no area of its own, and a misheard Swedish room name is not
an area at all), so his instructions say to target by name. Give each entity the names people actually
say, short forms included ("phone", "the lights"): they are what makes a request land.

Home Assistant's own names stay as they are, since they are the household's (often Swedish) names.
Winston never renames an entity. The name on his list is what you call it, and it is saved to Home
Assistant as one more alias.

## What Save does

Saving the list, its domains or the Home Assistant switch applies it to Home Assistant before the
channels reload, so they see the new tools:

1. Turns off Home Assistant's "expose new entities" for Assist, so nothing new appears on its own.
2. Exposes every listed entity and everything in the listed domains (`expose_domains`, by default
   person, weather and todo), and un-exposes the rest.
3. Adds the name and aliases from the list as aliases in Home Assistant.
4. Removes aliases that an earlier version of the list had and the saved one does not. Aliases someone
   added in Home Assistant itself are never removed (unless the list once named them too).

An entity id Home Assistant does not know is skipped and reported, and the rest still applies. If Home
Assistant is away or slow (5 s), the save still succeeds and the page shows the rows as not applied;
the Apply button on the page, `POST /api/providers/home_assistant/apply` or `winston ha expose` applies
the saved list later. Writing needs an admin token; with a non-admin token the page still reads states,
without areas or aliases.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="images/home-assistant-dark.png">
  <img src="images/home-assistant-light.png" alt="The Home Assistant page: the connection icons, and the list by room with each entity's aliases, state and an Applied icon" width="720">
</picture>

The page reads Home Assistant for each listed entity (exposed or not, its aliases, area, device and
state), what else Home Assistant exposes, whether the MCP server integration is loaded, and the tools it
offers. Adding an entity fills in its name from Home Assistant; its aliases in Home Assistant keep working
and stay out of the list, so taking it off the list later never removes them.

```sh
uv run winston ha expose --list   # what a save would change, and what is exposed now
uv run winston ha expose          # apply the saved list (adds and exposes; removes no aliases)
```

## Checking after an action

Home Assistant answers "done" about 0.2 s after a call whether or not the bulb acted. So Winston answers
first and checks afterwards: his reply is as fast as before, and when a device did not do what he said,
he corrects himself in a sentence of his own.

```mermaid
sequenceDiagram
  participant U as You
  participant W as Winston
  participant H as Home Assistant
  U->>W: Turn on the shelf lamp
  W->>H: HassTurnOn (shelf lamp)
  H-->>W: done (0.2 s)
  W->>U: Shelf lamp is on.
  loop at 0.5 s, 1.5 s and 3 s, until it matches
    W->>H: GET /api/states/light.shelf_lamp
  end
  W->>U: The shelf lamp did not respond. Home Assistant shows it as unavailable.
```

- **What is checked.** Turn on, turn off and set a light (brightness and colour temperature), for lights,
  switches, fans and input booleans. A light group is checked member by member. Brightness counts as
  reached within 3 of 255, colour temperature within 100 K.
- **What he says.** Short sentences made by code, never by the model, with the name from his list:
  "The ceiling lamp is still off.", "The ceiling lamp is at thirty percent, not fifty.", "Two of the
  lights are at fifty percent; the shelf lamp did not respond." Nothing when every target matches, and
  nothing when Home Assistant could not be read.
- **When.** After his reply has finished, never over it, and never while you speak or while his answer
  to what you just said is on its way: on the browser voice channel and a Voice PE box
  it is spoken like an announcement without the chime (on the box, in the same reply); on the Talk page,
  Discord and `winston talk` it arrives as a message of its own.
- **Follow-ups.** The correction goes into the conversation as his own words, so "try again" works.
- **Not said** when you interrupted him after the action, or when a newer action already targeted the same
  device (that one has its own check).
- **Logged.** Every check is a line in `logs/tool_calls.jsonl` with event `verify` (entity, expected,
  observed, ms); every correction is a `correction` line.

Settings: the After an action card on the Home Assistant page (`providers.home_assistant.verify`).
Check after an action (`enabled`, on by default) and, under Advanced, Give up after (`wait_secs`, 3 s):
how long a device has to show the change before he says it did not.
