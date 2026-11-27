# Extension SDK reference

Every settings helper, decorator, handler and `ctx` call in `winston_sdk`. How an extension fits
together, its manifest and its permissions are on [extensions](extensions.md); nudges are on
[speaking up](speaking-up.md).

## Imports

Everything comes from `winston_sdk`: `Extension`, `Settings`, `Model`, `Field`, `Secret`, `Account`,
`Entity`, `Topic`, `Extensions`, `TimeOfDay`, `Duration`, `From`, `Choices`, `Choice`, `When`,
`Nudge`, `Link`, `Status`, `SettingsError`, `NotPermitted`, `HomeAssistantError`.

## Settings

A settings model subclasses `Settings`, and `ext.settings(Model)` declares it. Its page draws the form
and saves the values under `extensions.<id>.settings` ([settings](extensions.md#settings)).

### Field types

| Field | Form | Value in code |
| --- | --- | --- |
| `str`, `int`, `float` with `Field(...)` | Text or number, with its limits and `unit` | As typed |
| `bool` | Switch | `bool` |
| `Literal["a", "b"]` or an `Enum` | Dropdown | The choice |
| `list[str]` | One box per entry, with Add | `list[str]` |
| `dict[str, str]` | A table of two columns, with Add | `dict[str, str]` |
| `dict[str, Model]` | Named entries in a table, each with its own form in a dialog (several plants, parcels, chores) | `dict[str, Model]`, keyed by the name shown on its page |
| `Secret("VAR")` | A key field that says Not set, Saved or Optional; the key goes to `data/.env`, the config keeps the variable name (config.yaml only) | The key |
| `Account("VAR", ...)` | Connect, or Connected with Disconnect; the sign-in goes to `data/.env`, never into the config | `bool`: connected (a change runs `@ext.on_settings`); `await ctx.token(field)` for its token |
| `Entity(domain="sensor" or [...], device_class=None)` | A picker of Home Assistant entities | The entity id |
| `Topic()` | A picker of memory topics | The topic name |
| `Extensions(tool="status")` | A picker of the switched-on extensions and MCP servers with a read tool of that name; picking one lets this extension call it | Their ids (`list[str]`, or `str` for one) |
| `TimeOfDay("07:00")` | A time picker | `datetime.time` |
| `Duration("30m")` | A duration | `datetime.timedelta` |
| `Field(choices="playlists")` | A picker of the extension's own list, with each item's picture and hint | As picked or typed |

- `Field(default, ...)` works on any type. `Field`, `Entity`, `Topic`, `Extensions`, `TimeOfDay` and
  `Duration` take `default=`: `None` makes the field optional, `[]` an empty list. `Secret` takes
  the variable name instead.
- An optional choice (`Literal[...] | None`) gets a first item for none, so it can be taken back.
- Each entry of a `dict` setting gets a stable id when it is added, so renaming it on its page keeps
  its entities and their history.

### Labels and help

Every helper but `Secret` takes these. All but `Account` also accept `level=` and ignore it, so a
package for api 1 that passes it still loads.

| Parameter | What it does |
| --- | --- |
| `title` | The label. Name the effect ("Days without training before he mentions it"), never "Enabled" |
| `description` | Behind the (i): how it works, trade-offs |
| `help` | One sentence shown under the field, for what the label cannot say: a format, a cost, where to get something |

`Field` also takes:

| Parameter | What it does |
| --- | --- |
| `placeholder` | What the form shows while it is empty ("None: no crisis alerts") |
| `unit` | The suffix on a number (`"min"`, `"%"`, `"h"`), so the title carries none |
| `labels` | Words for a choice's values, keyed by the value as text (`{"320": "320 kbps (best)"}`) |
| `widget` | `"slider"` for a number you feel rather than type |
| `group` | One heading over the fields that name it |
| `hidden` | `True` for a port or path nobody sets by hand: the form never draws it (config.yaml only) |
| `key_title`, `value_title` | For a `dict`, the titles of its two columns |
| `choices` | Names an `@ext.choices` function whose list the form offers for the value |
| `key_choices` | The same for a `dict`'s keys |

With `choices` or `key_choices` a typed value is still taken. While the extension is not running,
the field is a text box that says so.

### Keys

`Secret("VAR", ...)` is a key the extension needs. The form draws a field to paste the key and never
the variable's name, which is config.yaml only.

| Parameter | What it does |
| --- | --- |
| `label` | What the key is: "API key", "Client secret" |
| `help` | Where to get it |
| `description`, `group` | As on `Field` |
| `optional` | `True` for a key it works without: it never shows "Not set" and is left out of what the extension needs. Say in `help` what empty means |
| `title` | Only names the field in the schema ("Key variable" by default) |

A `Secret` inside a `dict` entry gets no key field, since an entry has no fixed path to store it
under.

### Accounts

`Account` is an account the user signs in to in the browser (OAuth 2's authorization code). Spotify's,
trimmed:

```python
account: bool = Account("SPOTIFY_ACCOUNT", authorize="https://accounts.spotify.com/authorize",
                        token="https://accounts.spotify.com/api/token",
                        scopes=["user-library-read", "playlist-read-private"],
                        client_id="client_id", client_secret="client_secret", label="Spotify account")
```

| Parameter | What it does |
| --- | --- |
| `"VAR"` | The variable in `data/.env` that keeps the refresh token |
| `authorize`, `token` | The service's two addresses, https, on hosts in its `network` permission |
| `scopes` | What it asks the service for |
| `client_id`, `client_secret` | The names of its own `Secret` fields for the app |
| `label` | What it is: "Spotify account" |
| `title`, `help`, `description`, `group` | As on `Field` |

winstond does the sign-in, and the extension never holds the refresh token:

1. Connect opens the service's page, which comes back to
   `http://127.0.0.1:<port>/api/accounts/callback`. The app must list that address; the page shows
   it.
2. The refresh token goes to `data/.env` under `VAR`.
3. `await ctx.token("field")` gives the extension an access token, renewed as it runs out. A new
   refresh token the service hands out is kept.
4. One the service no longer takes is removed, so the page says Connect again.

While winstond listens beyond this machine (`control.host: 0.0.0.0`) its page sets no keys. Connect
then says to run `winston ext connect ID` in a terminal on this machine, which opens the same
sign-in.

### Lists the extension supplies

A list only the extension knows (its playlists, its accounts' calendars) comes from an
`@ext.choices` function, named by the settings that offer it. It is asked while the extension runs,
with 5 s to answer, and returns `Choice(value, label, hint, image)` items or plain values:

```python
class SpotifySettings(Settings):
    mix_playlist: str | None = Field(None, title="Playlist for mixes", choices="playlists",
                                     placeholder="None: Spotify's queue")
    names: dict[str, str] = Field(default_factory=dict, title="Your names for playlists",
                                  key_title="Playlist", value_title="What you call it", key_choices="playlists")


@ext.choices
async def playlists(ctx) -> list[Choice]:
    return [Choice(value=p["name"], hint=f"{p['songs']} songs", image=p.get("cover"))
            for p in ctx.store.get("playlists", [])]
```

### A setting's value in a decorator

`From("name")` stands for a setting's value wherever a decorator takes one: a schedule, a trigger,
`per=` on an entity, `Choices` on a tool argument. `@ext.at(From("remind_at"))` follows the setting.

## Handlers

Every handler is `async` and takes `ctx`. It may take a second argument, `event`, describing what
started it. Tools take `ctx` and their own arguments instead.

All of an extension's handlers share one event loop and may interleave at each `await`. Never block
it: no `time.sleep`, no synchronous HTTP; wrap a synchronous library in `ctx.run_sync`. A handler that
raises is logged and shown on its page.

| Started by | `event` has |
| --- | --- |
| `@ext.every`, `@ext.at`, `@ext.cron`, `@ext.on_arrive`, `@ext.on_leave`, `@ext.on_start` | `time` |
| `@ext.on_state` | `entity_id`, `old`, `new` (each a `State`) |
| `@ext.on_event` | `type`, `data` |
| `@ext.hook` | `json`, `headers` |
| `@ext.job`, run by a timer from `ctx.later`, a hook or `winston ext run` | `time`, `data` |
| `@ext.on_settings` | `old` (the settings before the save) |
| `@ext.on_speakers_stop` | `time`: he said "stop" to a box while your `ctx.speakers` stream played. The box is silent already, so stop at your source (Spotify pauses) |
| `@ext.validate` | `settings` (the ones being saved; `ctx.settings` still holds the saved ones) |
| `@entity.on_change` | `value`, and `entry` for a `per` entity |
| `@entity.on_press` | `entry` for a `per` entity |
| `@ext.action` | `nudge` (`kind`, `about`, `level`, `facts`, `links`) |
| `@ext.on_nudge` | `what` (`said`, `dismissed`, `put_off`, `muted`, `ended`), `nudge`, and `until` or `where` where they apply |

Checks, `@ext.status`, `@ext.context` and `@ext.hint_words` take `ctx` only. The store is saved after
every handler, checks included.

## Jobs

| Decorator | Runs |
| --- | --- |
| `@ext.every(interval, align=False, catch_up=True)` | Every `"90s"`, `"15m"`, `"1h"`, `"1d"` or a `timedelta`, the first run at start. `align=True` runs on the clock instead (`"15m"` at :00, :15, :30, :45), the first at the next of those |
| `@ext.at(time, days=None, catch_up=True)` | At `"13:15"` or `From(...)`. `days` is `"mon-fri"`, `"sat,sun"` or `None` for every day |
| `@ext.cron(expr, catch_up=True)` | Five fields, in the home's time zone |
| `@ext.job` | No schedule: run by a timer from `ctx.later`, a hook's handler or `winston ext run` |
| `@ext.on_state(entity, to=None, from_=None, for_=None)` | When an entity changes: an entity id, a list, or `From(...)` |
| `@ext.on_event(event_type)` | On a Home Assistant event |
| `@ext.on_arrive`, `@ext.on_leave` | When you get home or leave (needs `presence`) |
| `@ext.on_start` | Once after each start, with settings loaded |
| `@ext.on_settings` | After a save, so it can drop what the old settings fetched |
| `@ext.hook(name)` | On a `POST` to winstond's `/hooks/<id>/<name>`, reachable where winstond is (loopback unless you open it); returns a dict |
| `@ext.background` | A task for the life of the process (a websocket, IMAP IDLE), started again if it raises; no time limit |

- Scheduled jobs that were missed while Winston was down run once when it is back. `catch_up=False`
  skips that.
- A job gets 120 s; `timeout=` raises it up to 600.

## Nudge decorators

| Decorator | What it declares |
| --- | --- |
| `@ext.nudge(kind, title, description=None, **options, **triggers)` | A checked kind and its check. `triggers` take the same values as the job decorators (`every=`, `at=`, `on_state=`, ...); a kind needs none. The check returns `None`, a `Nudge` or a list |
| `ext.kind(kind, title, description=None, **options)` | A pushed kind, for `ctx.nudge` |
| `Nudge(about, say, facts={}, level=1, urgent=False, not_before=None, expires=None, actions=None, links=[])` | One nudge; its fields are under [writing a nudge](speaking-up.md#writing-a-nudge) |
| `@ext.action(title, kinds=None, name=None, tier="act")` | Something you can say back to its nudges, for the kinds listed or all of them; returns a string or dict he reports |
| `@ext.on_nudge(kinds=None)` | Hears what happens to its nudges |

The options (`deliver`, `where`, `repeat`, `may_be_urgent`, `requested`, `outside`) are under
[kinds and their options](speaking-up.md#kinds-and-their-options).

## Tools

`@ext.tool(...)` goes on `async def f(ctx, arg: T, other: T | None = None)`. How calls are handled
is under [tools](extensions.md#tools).

| Parameter | Default | What it does |
| --- | --- | --- |
| `tier` | required | `read`, `act` or `restricted` ([tiers](extensions.md#tiers)) |
| `name` | The function's name | Lowercase letters, digits and underscores; the brain sees `<id>_<name>` |
| `outside` | `True` | Wraps the result as [outside information](extensions.md#outside-information) |
| `timeout` | 10 | Seconds, up to 60 |
| `filler` | None | A line said on the spoken channels while a slow call runs ("Let me check.") |
| `filler_after` | 1 | Seconds before the filler; 0 says it as the call starts |
| `title` | The name in words | A short verb phrase for people ("Play music") |
| `summary` | None | One sentence for people on what it does ("Plays a song, artist, album, playlist, genre or mood on your speakers.") |
| `shortcut_only` | `False` | For a tool a [shortcut](extensions.md#running-a-tool-from-a-phrase) runs with fixed arguments (Spotify's party). The model is not offered it, so his prompt does not carry its description |

The docstring's first paragraph describes the tool, and an `Args:` section each argument.

Arguments:

- **Types**: `str`, `int`, `float`, `bool`, a `Literal`, `date`, `datetime`, `When`, or a list of
  these. `T | None = None` makes one optional.
- **`When`** is read by the core as `follow_up` reads times ("at six", "in 20 minutes", "tomorrow
  morning"; the brain sees the date and time with every message) and arrives as an aware `datetime`.
  English, Swedish and German are read ("om tio minuter", "morgen um halb sieben"), and its
  description asks the brain to pass the words as said rather than work out a clock time
  ([languages](languages.md#times-you-give-him)).
- **Limits**: `Annotated[int, Field(ge=1)]` adds limits.
- **Names from a setting**: `Annotated[str, Choices(From("chores"))]` limits a text argument to the
  names in a `dict` setting, so the brain sees them. The tool list changes from the next
  conversation after a save.

It returns a dict, list or string. An exception's message goes to the brain as the tool's error
("Error: ..."), the traceback to the log.

## Entities

```python
ext.entity(key, kind, name, unit=None, device_class=None, state_class=None, icon=None, options=None,
           min=None, max=None, step=None, per=None)
```

| Parameter | What it does |
| --- | --- |
| `kind` | `sensor`, `binary_sensor`, `event`, `switch`, `button`, `number`, `select` or `text` |
| `state_class` | Home Assistant's (`measurement`, `total`, `total_increasing`), so a sensor keeps long-term statistics |
| `icon` | Home Assistant's form (`"mdi:dumbbell"`). Most entities need none: their device class gives one |
| `per` | `From("chores")` makes one entity for each entry of a `dict` setting, named with `{entry}` filled in (`name="{entry} done"`). Entries added or removed on its page add or remove entities |

- `await entity.set(value, **attributes)` updates it with a `str`, `int`, `float`, `bool`, an aware
  `datetime` (for a `timestamp` sensor) or `None` for unknown.
- For a `per` entity, `entity[entry]` is that entry's entity.
- `@entity.on_change` (switch, number, select, text) and `@entity.on_press` (button) handle what Home
  Assistant sets. The new value shows there once the handler returns without raising.

How entities reach Home Assistant is under [Home Assistant entities](extensions.md#home-assistant-entities).

## Other decorators

| Decorator | What it does |
| --- | --- |
| `ext.settings(Model)` | Its [settings](extensions.md#settings) |
| `@ext.status` | Returns the line on its page and in `winston status`, or a `Status(text, warning=None, note=None)`. The warning shows as a warning on its page (a feed not answering), the note as a quieter line under the line |
| `@ext.choices` | Returns a list for the settings that name it (`choices=`, `key_choices=`): `Choice(value, label, hint, image)` items or plain values. Asked while it runs, with 5 s to answer |
| `@ext.context` | Returns up to 300 characters for his prompt at the start of each conversation, or `None` for nothing |
| `@ext.hint_words` | Returns words speech to text should hear right (exercise names, band names) |
| `@ext.validate` | Runs on save while the extension runs (one that does not run is checked against its settings schema). May use the network, 10 s. `raise SettingsError("field", "Hevy rejected this key")` shows on that field |

## ctx

`ctx.settings`, `ctx.store`, `ctx.folder`, `ctx.now()`, `ctx.today()`, `ctx.language`, `ctx.log` and `ctx.ongoing()`
are not awaited. Everything else reaches winstond, Home Assistant or the network, and is awaited.

### Its own state and the clock

| Call | What it gives |
| --- | --- |
| `ctx.settings` | Its settings, typed; secret fields hold their values |
| `ctx.now()`, `ctx.today()` | The core's clock: an aware `datetime` in the home's time zone (a `ZoneInfo`, so arithmetic across daylight saving holds), and its `date`. Tests move it |
| `ctx.language` | His reply language by its English name ("English", "Swedish"), for text he will say or a service's language; a new one arrives when it changes. `Harness(language=...)` sets it in tests |
| `ctx.store` | A dict of JSON values in its state folder, saved (nested changes too) after every handler |
| `ctx.folder` | That folder, `data/state/extensions/<id>/`, for SQLite or files |
| `ctx.log.info(msg)`, `.warning`, `.error` | Its log, shown by Log on its page and by `winston ext logs` |
| `ctx.ongoing(text)` | What is going on now, below |

`ctx.ongoing(text)` tells him what is going on now with every message ("playing Oh Dove by Men I
Trust"), up to 160 characters, or `None` once it is over. `@ext.context` is for what holds for a
whole conversation.

- It goes in front of each thing said to him while it lasts (`[09:30; spotify: playing ...]`), read
  fresh each message, so it costs no prompt cache.
- It is cleared when the extension's process starts or stops.
- While it is set, a saved change to the extension's code waits to reload until it is over, so it
  does not cut the music. Other extensions reload meanwhile.

### The network and Home Assistant

| Call | What it gives |
| --- | --- |
| `ctx.http` | An `httpx.AsyncClient` for the declared hosts, 10 s timeout. It does not raise on 4xx or 5xx (check `status_code` or call `raise_for_status()`); an undeclared host raises `NotPermitted`. Each request is logged by method, host, path, status and time; never the query string, headers or body |
| `ctx.ha.state(id)`, `.states(domain)`, `.history(id, since)` | `State(entity_id, state, attributes, last_changed)`, a list of them, or `None` |
| `ctx.ha.query(domain, service, data=None, target=None)` | A response-only service's answer, as Home Assistant gives it (read) |
| `ctx.ha.call(domain, service, data=None, target=None)` | Any service on permitted entities (act); returns its response or `None` |
| `ctx.devices()` | `Device(id, name, manufacturer, model, model_id, sw_version, hw_version, area, service)` for every device Home Assistant knows (needs `devices`). `service` is true for what it lists as a service rather than hardware (an add-on, Home Assistant itself, a cloud account) |
| `ctx.presence.home()`, `.since()` | `bool`, `datetime` |

### Memory, the brain and other tools

| Call | What it gives |
| --- | --- |
| `ctx.memory.facts(topic=None)`, `.remember(text, topic, until=None)` | `Fact(text, topic, noted, until)` items; writing as `remember` |
| `ctx.memory.diary(days=7)` | His diary entries of the last `days` days, newest first, as `DiaryEntry(day, text)` (needs `memory`) |
| `ctx.llm.ask(prompt, schema=None, max_tokens=500)` | Text, or an instance of the Pydantic model `schema`. The background model is the local brain unless you pick a cloud model; an extension never chooses, so nothing leaves the house unless a setting says so. It [yields to conversations](extensions.md#the-background-model-yields-to-conversations) |
| `ctx.tools.list()`, `.call(name, args=None)` | The brain's tools from Home Assistant and the other extensions as `ToolInfo(name, tier, description)`, and one run as the brain would, with its arguments as one dict. A failure raises with its error (needs `tools`) |
| `ctx.ext(id).call(tool, **args)` | Another extension's or MCP server's read tool, with the permission you gave it (needs `extensions`, or `id` picked in an `Extensions(tool=...)` setting) |

### Speakers

Both need `speakers`.

| Call | What it does |
| --- | --- |
| `ctx.speakers.play(url, title=None)`, `.stop()` | Plays a stream (anything ffmpeg reads, served on 127.0.0.1 or a declared host) on the box of the room last talked in, else on every box, and returns how many play. A new stream replaces what plays, and `stop()` ends only its own |
| `ctx.speakers.volume(level=None, change=0)` | The box's own volume, 0 to 1 (what its dial sets: the voice and everything it plays). Sets it to `level`, moves it by `change`, or only reads it; returns it after, or `None` while no box has reported one |

The box turns a stream down while it listens and while he talks, and stops it when he says "stop"
(`@ext.on_speakers_stop`). Each box reports its volume on every change, the dial's too. Spotify uses
`volume` to turn the box up when asked for louder with its own level at full.

### Timers, nudges and the rest

| Call | What it does |
| --- | --- |
| `ctx.later(when, job, data=None)` | Runs one of its jobs once at a computed `datetime` or after a `timedelta`, kept across restarts; returns a timer with `cancel()`. `ctx.timers()` lists them |
| `ctx.nudge(kind, nudge)`, `ctx.withdraw(kind, about)` | Pushes a nudge of a pushed kind, or takes one back |
| `ctx.nudges(kind=None)` | Its nudges now and those ended in the last 7 days, each with `kind`, `about`, `level`, `facts`, `links`, `state` (waiting, held, said, dismissed, ended), `said_at`, `held_until` |
| `ctx.poke(kind)` | Asks the core to run a kind's check now |
| `ctx.parse_when(text)` | Reads a time as `When` does; `None` if it cannot |
| `ctx.token(field)` | An access token for its `Account` setting `field`, with a minute left at least, or `None` while it is not connected |
| `ctx.quota(name, per_day)` | Takes one use and returns `True`, or `False` once the day's are gone (for paid APIs) |
| `ctx.run_sync(fn, *args)` | Runs a synchronous function in a thread |

## Errors

| Error | Raised when |
| --- | --- |
| `NotPermitted` | A call is outside its permissions |
| `HomeAssistantError` | Home Assistant failed or refused, `ctx.devices()` included |
| `SettingsError(field, message)` | Raised by `@ext.validate`, shown on that field |
