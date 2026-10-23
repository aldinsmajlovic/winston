# Setup

Approved 2026-10-23. Built so far: Winston installing his engines and `winston setup`; the install
script, the releases and the wizard come next, in the order and with the checks in the
[plan](plan.md) (Setup track). Today's way in is the README's Quickstart, or `uv run winston setup` in
a clone.

One command installs Winston on a Linux computer, and a wizard on his page does the rest the first
time it opens: Home Assistant, the brain and voice for your hardware, who he is, and a room box.

## Install

```sh
curl --proto '=https' -fsSL https://github.com/aldinsmajlovic/winston/releases/latest/download/install.sh | sh
```

Or read it first:

```sh
curl --proto '=https' -fsSLO https://github.com/aldinsmajlovic/winston/releases/latest/download/install.sh
less install.sh && sh install.sh
```

`install.sh` does only what has to happen before Python is there, then hands over to `winston setup`:

1. Installs uv into `~/.local/bin` if it is missing, through uv's own installer at a pinned version.
2. Downloads the release and its `SHA256SUMS`, checks the file and unpacks it into
   `~/.local/share/winston/app/<version>`. A file that does not match stops the install.
3. Runs `uv sync` there and starts `winston setup`.

`winston setup` reads the computer (the distribution, the graphics card and its driver, Docker and
its runtimes, Ollama, free disk where the downloads land, the serial group, lingering, IPv6), says what
it found, and then:

1. Picks the profile for the hardware and creates the data directory (`~/.local/share/winston`, as
   `winston init` does). An existing one is kept with its config.
2. Runs what needs root for that config, each command shown first and run only on yes
   ([below](#what-needs-root)).
3. Installs a systemd user service, `winston.service`, that runs `winston up`, and starts it. Without
   systemd (a container, say) there is no service and it says to run `winston up`.
4. Says where to open him: this computer's address, and the network's when Who can open this page
   allows it. The setup link for another device comes with the wizard.

Run it again and it does only what is still missing: a step skipped, or the `docker` group after
logging in again.

The engines (brain, voice, hearing) are not installed by setup. The wizard does it, with sizes and
progress, the same way the settings pages do later; until then each gets what it lacks when the
service first starts it.

`--yes` answers yes to everything and `--profile NAME` skips the pick, for scripts and tests.
The script's body is one function, so a download cut short runs nothing.

## What needs root

Shown with the exact commands for the distribution, run with `sudo` on yes, skipped on no with what
then will not work:

| Part | When | What it runs |
| --- | --- | --- |
| Docker | An NVIDIA profile, and Docker is missing | The distribution's own package (`docker.io` on Debian and Ubuntu, `moby-engine` on Fedora, `docker` on openSUSE and Arch), enabled and started |
| Docker started | Docker is there but not running | `systemctl enable --now docker` |
| Your user in the `docker` group | Docker is there and you cannot reach it | `usermod -aG docker`; it takes effect at your next login, so setup asks you to log out and in and run it again, and carries on where it stopped |
| NVIDIA container toolkit | An NVIDIA profile, and `docker info` lists no `nvidia` runtime | NVIDIA's package (from NVIDIA's repository; Arch has its own), `nvidia-ctk runtime configure --runtime=docker`, Docker restarted |
| Ollama | The Ollama profile, and Ollama is missing | Ollama's own install script |
| Your user in the serial group | Not in it yet | `usermod -aG dialout` (`uucp` on Arch, the group made if missing), so a Voice PE on USB can be set up |
| Lingering | With systemd | `loginctl enable-linger`, so the service runs without you logged in |
| IPv4 first | IPv6 addresses sort first and never connect, as behind a VPN that drops IPv6 | `precedence ::ffff:0:0/96 100` in `/etc/gai.conf`, so nothing waits out an IPv6 address before trying IPv4 |

Graphics drivers are never installed. Without `nvidia-smi` on an NVIDIA card, setup says so, links
the distribution's guide and offers the Ollama or hosted profile.

## Picking for your hardware

| Found | Profile |
| --- | --- |
| NVIDIA with 24 GB or more | `nvidia-24gb-27b` |
| A smaller NVIDIA card, another GPU, or none | `ollama` (a profile per smaller card size comes later, measured like the 24 GB one) |
| No usable local model, or your choice | `hosted` |

Free disk is read where the downloads land (the Hugging Face cache and Docker's root) and shown
against what the pick needs. Every pick can be changed in the wizard and on the settings pages later.

## Winston installs his engines

What an engine needs is read from its config: a Docker engine's image, a `services/` command's venv,
and for the brain its model (the quant's GGUF files for llama.cpp, the safetensors and the small files
beside them for vLLM, an Ollama model). Sizes come before the first byte: the registry's manifest, the
Hub's file list, Ollama's registry; a venv's size is measured once and kept beside its lock
(`tool.winston.installed-gb` in the service's `pyproject.toml`). Picking something not here, on the
Brain and Voice pages and later in the wizard, shows "Needs 16.4 GB downloaded before it can run: the
model (16.4 GB). 210 GB free." with Download, then the progress per part, or why it failed with Try
again. It downloads in winstond, so it carries on with the page closed, the engine that runs now keeps
running, and a cut download picks up where it was. A saved engine that still lacks something gets it
when it starts, and says Installing on Status, not Failed. One that does not fit is not started.

A voice's own weights are not sized first: its library chooses the files (Pocket loads a 0.2 GB model
from an 18 GB repo), so the server fetches them on its first start, and the supervisor waits while the
cache grows, saying "downloading, 1.2 GB so far".

This replaces the `uv sync` per voice in the Quickstart and the "raise Startup time limit" a first
download needed.

## The first run

Until the wizard is done, winstond listens on the network and serves the wizard to anyone with the
setup link, and to this computer without it:

```
Open http://192.168.1.20:8700/setup?key=7f3k-q9xm-2bvd from any device here,
or http://localhost:8700/setup on this computer.
```

Without the key, a request from another address is refused. The key is kept only as its hash, works
until setup ends, and `winston setup link` makes a new one. When the wizard ends, the page follows
Who can open this page (`control.host`) as now; its last step asks.

### The steps

Each step can be skipped and picked up later. A skipped one stays in Set up next on Settings.

1. **Brain and voice.** The profile's picks, preselected, each with its size; Install starts the
   downloads, which go on during the next steps.
2. **Home Assistant.** Found on the network (`_home-assistant._tcp`, then `homeassistant.local`), or
   typed: mDNS does not cross subnets, so a split network needs the address. Connect opens Home
   Assistant's own login; back on the wizard, winstond creates a long-lived token named Winston on that
   account (kept in `data/.env`, never shown) and switches on Home Assistant's MCP server if it is
   off. A user who is not an administrator is told what will not work (rooms, names, the device
   list). Pasting a token stays, under the address. Then the devices he can use, as on the Home
   Assistant page.
3. **Who he is.** His name, his personality and his voice, as on the Assistant and Voice pages: a
   bundled voice with a preview once the voice is installed, or one cloned from a clip.
4. **A room box.** Plug a Voice PE into this computer with USB-C. Winston finds it, flashes Winston's
   firmware ("Okay Winston") from the release, gives it Wi-Fi from the networks it sees, checks he can
   reach it there and puts it in a room. A box Home Assistant had adopted loses its key with the flash
   and would come back as a new device there, so the wizard offers to disable its ESPHome entry in
   Home Assistant (enable it again for a stock firmware update). When his name is not Winston, the box listens for a stock wake
   word instead (Okay Nabu, Hey Jarvis or Hey Mycroft), picked there.
5. **Done.** Who can open this page from now on, then Talk.

## Updating and removing

`winston update` downloads the next release, checks it, unpacks it beside the current one, syncs what
changed and restarts the service. The data directory is never touched. `winston uninstall` removes the
service and the code, keeps the data directory unless `--data`, and lists what it leaves (Docker
images, the Hugging Face cache, Ollama's models) with their sizes.

## Releases

A tag `v*` makes CI build the release: the code, the UI already built, Winston's Voice PE firmware
(ESPHome 2026.9.1, the version `services/voice-pe` pins), `install.sh` and `SHA256SUMS`, with GitHub's
build attestation. So installing needs neither Node nor a 30 minute firmware compile.

## From a clone

Developing stays as now (`uv sync`, `pnpm build`, `uv run winston up`), and `uv run winston setup`
works from a clone too, using `./data`.
