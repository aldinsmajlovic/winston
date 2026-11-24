# Web UI

`winston up` serves the web UI at http://localhost:8700: the Talk page, History, Memory, the wall
display and the settings pages. This page goes through each one and how settings are saved. How the UI
looks and reads is in the [UI guide](ui-guide.md).

Browsers only give the microphone, and keep a screen on, for `localhost` or HTTPS, so talking from
another device needs HTTPS on the LAN. Who may open the pages at all is the Web address on
[Status](#status).

## Talk page

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="images/talk-dark.png">
  <img src="images/talk-light.png" alt="The Talk page: an announcement from Home Assistant, a typed request that dims the lights, a spoken reminder from a room box, and a question about the weather" width="720">
</picture>

The home page shows everything Winston hears, says and does, on any channel:

- each action as one line;
- a divider for each new conversation, and why it started;
- what he brought up on his own, as one line with the time, what he said and from whom;
- where a line came from, when it is another place ("Kitchen box", "Discord").

Under his name a line says whether he can answer: Ready, Listening, Thinking, Speaking, Starting, or
Can't answer with a link to Status. History, Memory and Settings are at the top right; Settings carries
a warning mark while something needs you.

- **Talk and type.** Type and press Enter, or press the round button to talk: it connects the
  browser's mic to the voice channel through winstond, with no wake word, so he hears everything until
  you press it again.
- **Read aloud.** The speaker icon in the message field reads typed replies aloud in Winston's voice,
  a sentence at a time as the reply streams in. It is off by default and remembered per browser; spoken
  turns are always read aloud.
- **New conversation** is the round button left of the field. Typed messages form one conversation,
  which starts fresh after New conversation after (30 min of quiet) and after a settings save.
- **The line above the field** holds the room picker, which puts this browser in a room with a Voice
  PE box: one conversation and one mic for both ([rooms](rooms.md)). At its right it says what matters
  now, such as "Microphone paused while the Kitchen box has it".
- **Not an archive.** The page keeps the last few hundred events while winstond runs; the tool log is
  the record.

Its settings are on Ways to talk, in the Talk page card:

| Setting | Key | Default | What it does |
| --- | --- | --- | --- |
| Talk by voice | `channels.voice.enabled` | on | Off leaves typing, and the page says the microphone is off |
| Wall display | none, it makes a link | | Takes a room, then Open or Copy link for a [wall display](#wall-display) |
| New conversation after | `channels.web.session_idle_mins` | 30 min | Quiet after which typed messages start a new conversation |

How long he waits in silence before answering in the browser is config.yaml only: raise
`vad.unfinished_stop_secs` if he cuts you off mid-sentence
([settings only in config.yaml](running.md#settings-only-in-configyaml)).

## History

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="images/history-dark.png">
  <img src="images/history-light.png" alt="The History page: his diary entry for the day, then that day's spoken requests, each with a header in words and Play; one heard first as something else, one marked as not for him" width="720">
</picture>

History, at `/history` (`/turns` and `/diary` lead there), shows the spoken requests kept while
recordings are on, one day at a time under his diary entry for that day, or only the ones marked as
mistakes. Recordings and marks are in [understanding](understanding.md#1-turn-record-and-marks).

## Memory

Memory, beside History, shows what he remembers by topic, with a search and New topic. It is described
with his memory in [tools](tools.md#memory).

## Wall display

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="images/wall-dark.png">
  <img src="images/wall-light.png" alt="The wall display: Winston's logo large, speaking, with the question and his answer under it" width="720">
</picture>

The wall display, at http://localhost:8700/wall, is the same feed full screen for a wall tablet.
`/dashboard` leads there, keeping its room. To set one up:

1. In Settings, Ways to talk, give the Wall display row on the Talk page card a room: a box's room or a
   Home Assistant area ([rooms](rooms.md)).
2. Open or Copy link, once per tablet. The link carries the room, as in `/wall?room=main room`.
3. On the tablet, tap the logo to talk and again to hang up.

What it shows:

- its room at the top left and Close at the top right;
- under the logo, a line that follows his state ("Tap to talk", or why he cannot answer);
- what you said and his answer, while he is busy and for 20 seconds after;
- what he says aloud on his own (an announcement, a message on the speakers), for a minute, until you
  tap it or answer it.

The page asks the browser to keep the screen on, which, like the mic, works only on `localhost` or
HTTPS. To show it in Home Assistant, add a Webpage card pointing at the wall display's address. If Home
Assistant itself is on HTTPS, the browser blocks a plain `http://` page inside it, so Winston needs
HTTPS too.

## Settings

Settings opens on Status. The sidebar groups the pages; the theme and the version are at its foot.

| Page | What it sets | More in |
| --- | --- | --- |
| Status | Engines, logs and the web address | [below](#status) |
| Assistant | His personality, region, memory and recordings of what you say | [identity](identity.md#his-personality), [tools](tools.md), [understanding](understanding.md#1-turn-record-and-marks) |
| Brain | What runs his brain and how he thinks | [below](#brain) |
| Voice | Speaking and hearing | [voices](voices.md) |
| Ways to talk | The Talk page, the wall display, room boxes and Discord | [rooms](rooms.md), [Voice PE](voice-pe.md), [Discord](discord.md) |
| Home Assistant | The connection and the devices he may use | [Home Assistant](home-assistant.md#the-page) |
| Extensions | Each extension, its settings and actions | [extensions](extensions.md#the-extensions-and-actions-pages) |
| Actions | What each action may do on its own | [extensions](extensions.md#the-extensions-and-actions-pages) |
| Speaking up | Quiet hours, announcements and what extensions may bring up | [announcements](announcements.md#settings), [speaking up](speaking-up.md) |

### Status

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="images/status-dark.png">
  <img src="images/status-light.png" alt="The Status page: his parts by role with what runs each and its state, and the graphics memory" width="720">
</picture>

From the top:

- **Needs you**, only when something does: an engine or channel that failed or stopped responding,
  with Log, Restart and a link to its page; an extension that needs a key or is on but not running,
  with a link to it; Home Assistant set up but out of reach. Saved settings that wait for a restart are
  said once, in the header, with one Restart.
- **Set up next**, only while one applies: connect Home Assistant, choose the devices he may use, add
  a room box.
- **His parts**: Brain, Speaking, Hearing, Talk page microphone, Room boxes, Discord, Home Assistant
  and Extensions. Each row is described below the list.
- **Graphics memory**: "23.4 of 24.0 GB in use" and the card's name. The brain and the voices share
  it; one that does not fit will not start. Above 90% the bar is in the warning colour and the card
  says "Nearly full: a bigger model or voice will not fit."
- **Custom engines**, once there is one: servers Winston starts and keeps running, which nothing uses
  until a setting points at their address. Each row opens its page at `/status/engines/<name>`, which
  shows How it runs open.
- **Web address**: Who can open this page, This computer only or Every device on your network
  (`control.host`). Open to the network, a warning says anyone on it can control him (and your home,
  once Home Assistant is set up), and a line says keys cannot be pasted on these pages then. It takes
  effect when Winston restarts. API reference opens `/docs`.

A row in His parts says:

- what runs it, such as "2 boxes, 1 connected", or the brain's model by its own name ("Qwen3.8-27B"),
  without its maker, file format or quantization, which the Brain page shows whole;
- its state in words with how long ("Running, up 15 min", "Starting, 40 s of about 2 min"), and the
  reason under it when it is not running.

Its command is Restart while it runs, Start while it does not, Turn on for one switched off (a draft
change, for Save) and Set up for Discord. Its menu has Stop and Log, and the row opens the page that
sets it up. Stop and Restart say what stops and for how long ("Stop the brain? He cannot answer
anywhere until it starts again, about 24 s.").

The header has Start all (while something is stopped), Stop all (while something runs, after asking)
and Add a custom engine, which asks a name and how it runs. Log is described with
[status and errors](running.md#status-and-errors).

### Brain

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="images/brain-dark.png">
  <img src="images/brain-light.png" alt="The Brain page: llama.cpp with the 27B, Ready before the first question switched on, and Think before answering" width="720">
</picture>

The What runs his brain card has, from the top:

- **Runs on**: presets (vLLM, llama.cpp, Ollama, hosted), each with a line on what it is good at and
  what it needs where its name does not say. Picking one rewrites the engine block and the settings
  that go with it, and asks first when you had edited that block.
- **Model**, from three lists: Running, what the brain's server reports; Downloaded, what the chosen
  engine can load from the Hugging Face cache on this machine; and Suggested, the presets' models for
  that engine, not downloaded yet. Typing searches for more, and a pasted model link is read as its
  name ([finding a model](#finding-a-model)). A pick that is not on this computer says what it needs downloaded,
  its size and the free space, with Download, which gets it while the brain that runs now keeps
  answering ([setup](setup.md#winston-installs-his-engines)).
- **Ready before the first question** (`llm.warm_up`, on): the brain reads his instructions and tools
  before anyone asks. See [below](#ready-before-the-first-question).
- **How it runs**, closed at the bottom: the engine block (Runs as, Start with Winston, port, image,
  arguments and the rest), its state with Log and Restart while it runs, Reset to the preset while the
  block differs from the preset, the API address while Winston runs the engine, and How thinking is
  switched. A problem with the engine shows above it, never folded away.

Think before answering and How he picks his words are cards of their own. The second holds temperature,
top-p, presence penalty and the longest reply, for when your model's page recommends other values.

#### Which models are listed

Downloaded is read from the disk, never the Hub: the host side of the engine's huggingface volume,
else `HF_HOME`, else `~/.cache/huggingface`.

| Engine | What Downloaded lists |
| --- | --- |
| llama.cpp | Each GGUF quant as `repo:quant`; a split file is one quant, listed once every part is there |
| Ollama | The same quants as `hf.co/repo:quant`, as Ollama pulls its own copy |
| vLLM | Safetensors repos with a language model config |

A model the engine cannot load is greyed with the reason ("vLLM only"). Speech models (Chatterbox,
Parakeet, Pocket TTS, a model card tagged text-to-speech) are left out. When Save changes the brain's
model to one that is neither running nor downloaded, the confirm says the restart downloads it first,
size unknown, and may take many minutes; the brain is offline until the download ends.

#### Finding a model

Typing two or more letters in Model's search searches for models the engine can download, and the
box says so ("Search, or paste a link"). Anything typed can still be used as is ("Use ...").

| Engine | What typing finds |
| --- | --- |
| llama.cpp | The Hub's most downloaded GGUF repos matching every word, each at its 4-bit size (`Q4_K_M`, else the nearest), with downloads and how many sizes it has |
| Ollama | Ollama's library first (`granite4.1`), then the same Hub repos as `hf.co/repo:quant` |
| vLLM | The Hub's safetensors repos; Apple's MLX repos are left out |

- **A repo named exactly** (`unsloth/Qwen3.5-9B-GGUF`) or pasted as a link (`huggingface.co/...`,
  `hf.co/...`, `ollama.com/library/...`) lists every size of it, smallest first, each with its size.
- **Other sizes of this model**: with nothing typed, the GGUF repo picked now lists its other sizes, so
  a search result's 4-bit size can be changed.
- Ollama's library has no search API, so its search page's links are read. If that page changes, it
  finds nothing there and the Hub still answers.
- Offline, typing finds nothing and says nothing; a typed name still works.
- What is typed goes to the Hub (and to ollama.com for Ollama), and nothing else does.

Checked by `tests/model_search_cases.py`, against a fake Hub and Ollama page.

#### Ready before the first question

Reading his prompt cold takes a large model seconds, so with this on the brain reads it ahead:

- typed conversations (the Talk page and Discord share one prompt) when the brain's server comes up;
- a box when it connects, and again a minute after a brain restart;
- the Talk page microphone when it opens.

Off, nothing is read ahead, which saves a hosted brain the extra requests.

### Voice

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="images/voice-dark.png">
  <img src="images/voice-light.png" alt="The Voice page: Speaking with its voice, streaming and its closed How it runs, then Your voices, with Hearing below" width="720">
</picture>

Voice does for speaking and hearing what Brain does for the brain: what runs each, its model or voice,
and How it runs. The voices, cloning and hearing engines are in [voices](voices.md).

## How settings pages work

### How a setting shows

The settings pages are drawn from the config schema: a label that says what the setting does, a line
under it where the label cannot say it all, and an (i) beside it for the background. There are no view
modes: a setting is on its page or, for what nobody sets by hand, only in config.yaml
([settings only in config.yaml](running.md#settings-only-in-configyaml)).

- **A field that does not apply is not shown**: an API key only for a hosted brain or voice (an engine
  Winston does not run, at an address off this computer, so not Ollama), the voice's Model only for a
  hosted one, the Streaming settings only when the speech engine's command
  uses them, engine fields only for their driver.
- **A feature switched off** (a channel, memory, announcements) keeps its fields visible and editable
  at full contrast. Its switch's line says they wait until it is on, and its keys do not warn.
- **Keys are never shown.** A key field says Saved or Not set, takes a pasted key that Save writes to
  `data/.env`, and Remove deletes the stored one at once, after asking. The variable it is stored under
  is config.yaml only, and a key refused while the page is open to your network names it.
- **Maps** (Discord contacts, an engine's environment and volumes) are tables with Add, a dialog per
  entry and trash with Undo.
- **A card about something that runs** (the Talk page, Room boxes, Discord) shows its state in its
  title while it is not running.
- **The Home Assistant page** checks the address and token before anything is saved
  ([Home Assistant](home-assistant.md#the-page)).

### Choices filled for you

Anything a machine already knows is a searchable dropdown filled from `/api/options/{name}`, and a
typed value still works when the list is empty. A list that cannot be fetched (no token yet, the server
down) leaves a plain text box with the reason in a line under it.

| Field | Filled from |
| --- | --- |
| Brain model | Running: the brain's server (`/models`), following an unsaved base URL. Downloaded: the Hugging Face cache the engine reads, with each model's size on disk ([which models are listed](#which-models-are-listed)). Suggested: the presets' models for that engine. Typed: the Hub and Ollama's library ([finding a model](#finding-a-model)) |
| Voice | Clips in `data/voices/`, your cloned voices and the TTS server's built-in voices |
| Speech-to-text model | The models onnx-asr knows, downloaded ones marked |
| Home Assistant entities, sensors, domains | Home Assistant's states, with each entity's name, state and an icon for its type |
| Your Discord user ID, contacts | The members of the bot's servers, a pasted id looked up ([Discord](discord.md)) |
| Sound before an announcement | None, and the sounds in `assets/sounds/` and `data/sounds/` |
| Who can open this page | This computer only, Every device on your network |

### Saving

Edits are one draft across all pages until saved or discarded. A dot in the sidebar marks each page
with unsaved edits, and closing the tab asks first. On a phone, Discard and Save sit in a bar along the
bottom while there are unsaved changes.

Save sends only the changed fields (`PATCH /api/config` with the version the edits started from). It
asks first when something starts, stops or restarts, or when the edits reach past the current page. Its
confirm lists:

- the changes, by page;
- what the save starts, stops and restarts, with roughly how long each takes;
- a model download;
- what Home Assistant would change.

The confirm's title says it ("Save and start the room boxes and restart the brain?"). Pasted keys are
written after the config. A field the server refuses is marked where it is.

If something else saved one of the same fields meanwhile (a voice switched by asking), nothing is
overwritten: the page reloads, keeps your edits on top, names the field in a message, and you save
again. Discard asks when edits reach past the current page, offering that page alone.

### Waiting for a restart

While a saved change waits for a restart, every settings page says so beside Save ("Waiting for a
restart: the brain, about 24 s"; the icon lists each part and its cost), with a Restart button that
restarts only those. What only winstond reads (the web address, announcements, Home Assistant) says it
takes effect when Winston restarts, the next `winston up`, and has no button.

## Building and testing it

Build it once (Node 22 or newer, pnpm); `winston up` serves it once `web/dist` exists.

```sh
cd web && pnpm install && pnpm build   # guardrails, lint, type check, bundle into web/dist
```

| Command | What it does |
| --- | --- |
| `pnpm dev` | Vite on :5173 with `/api` proxied to :8700 |
| `pnpm api` | Regenerates the typed client from `winston api schema` after the API changes |
| `pnpm smoke`, or `winston check ui` | Tests the built pages with Playwright |

The smoke test runs in the system Chromium, or Chrome where there is none (or set `CHROMIUM`). It
starts a throwaway `winston up --no-start` on a free port and deletes it all afterwards. Nothing it does
reaches the running brain, voice server, channels, MCP servers or Spotify receiver:

- it copies `config.yaml` and `persona.md` from the data directory Winston uses (or `WINSTON_SOURCE`)
  to a temp directory, with fake keys;
- every engine and channel is off, Home Assistant points at a fake one (`tests/fake_ha.py`), every
  other address points nowhere and every port is moved to a free one.

It checks that:

- every settings page loads with nothing to save;
- every page fits a 390 px phone with every How it runs open, without sideways scroll or a card's
  content wider than the card;
- a rename survives a reload;
- a pasted key shows as Saved and never comes back from the server, and an MCP server's token lands
  in `data/.env` alone;
- a hosted-only field is left out for a local engine;
- a refused save opens the How it runs that holds the field;
- Status lists the custom engines and who can open the page;
- Save only leaves a Restart button;
- the Brain, Voice, Home Assistant, Memory, History and Talk pages' own cases pass.
