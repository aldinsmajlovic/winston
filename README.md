<img src="docs/images/logo.svg" alt="Winston's logo" width="72">

# Winston

[![Licence](https://img.shields.io/badge/licence-Apache--2.0-blue)](LICENSE)
[![Python](https://img.shields.io/badge/python-uv-de5fe9?logo=python&logoColor=white)](https://docs.astral.sh/uv/)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-MCP-18bcf2?logo=homeassistant&logoColor=white)](https://www.home-assistant.io/integrations/mcp_server/)

A self-hosted voice assistant for Home Assistant. Talk to your home; Winston answers in a natural
voice, does what you asked, and can be interrupted mid-sentence. Everything runs on your own machine
by default, and any part can be swapped for a hosted service.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/images/talk-dark.png">
  <img src="docs/images/talk-light.png" alt="The Talk page: an announcement, a typed follow-up and a web search" width="720">
</picture>

## Features

- **Local first.** Speech recognition, the language model and the voice run on one GPU, or on Ollama
  and the CPU. Each can point at a hosted API instead.
- **Fast.** About 1.3 s from the end of your sentence to his first word, and you can talk over him.
- **Controls your home** through Home Assistant's MCP server, and checks a change happened before
  saying it did.
- **In every room.** The browser, a wall tablet, and Home Assistant's Voice PE with an
  "Okay Winston" wake word. Start a conversation at one and finish it at another.
- **Remembers** what you tell him, in plain Markdown you can read and edit.
- **Speaks up.** Announcements from Home Assistant and reminders from extensions reach the room you
  are in or your phone, never during quiet hours.
- **Extensible.** Add a tool in one Python file or connect any MCP server. Hevy, news, reminders and
  Spotify come built in.
- **Borrows a voice.** A friend agrees out loud and talks for fifteen seconds; Winston can then speak
  as them.

## Status

Early. Voice, Home Assistant, the web UI, memory, extensions and the Voice PE work; Discord is built
but not yet tested against Discord. Progress is tracked in [the plan](docs/plan.md).

## Quickstart

Needs Linux, [uv](https://docs.astral.sh/uv/), Home Assistant, and one of the
[hardware profiles](docs/hardware.md): a 24 GB NVIDIA card with Docker, Ollama on any GPU or CPU, or
a hosted model.

```sh
git clone https://github.com/aldinsmajlovic/winston && cd winston
uv sync
(cd services/tts-chatterbox && uv sync)   # GPU voice (nvidia-24gb profiles)
(cd services/tts-pocket && uv sync)       # CPU voice (ollama and hosted profiles)
(cd web && pnpm install && pnpm build)    # the web UI
uv run winston init                       # creates ./data; --list shows the profiles
uv run winston up                         # everything; Ctrl-C stops it
```

Open http://localhost:8700, paste your Home Assistant URL and token in settings, and talk.
Optional keys (web search, Claude, Discord) are listed in [tools](docs/tools.md).

## Documentation

- **Using it:** [running](docs/running.md), [Home Assistant](docs/home-assistant.md),
  [tools](docs/tools.md), [voices](docs/voices.md), [announcements](docs/announcements.md),
  [Discord](docs/discord.md)
- **Rooms:** [Voice PE satellite](docs/voice-pe.md), [rooms](docs/rooms.md)
- **Extending it:** [extensions](docs/extensions.md), [built-in extensions](docs/built-in-extensions.md)
- **How it works:** [architecture](docs/architecture.md), [hardware](docs/hardware.md),
  [decisions](docs/decisions.md)
- **Contributing:** [UI guide](docs/ui-guide.md), [identity](docs/identity.md),
  [writing docs](docs/writing-docs.md)

## Licence

Apache-2.0. See `LICENSE` and `NOTICE`. "Winston" is the name of this project; forks are welcome
under a different name.
