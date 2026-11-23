# Setup track step 8: A room box over USB, Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The wizard's fourth step finds a Voice PE plugged in by USB-C, puts Winston's release firmware on it, gives it Wi-Fi from the networks it sees, checks Winston can reach it there, offers to disable Home Assistant's old entry for it, and puts it in a room.

**Architecture:** An Improv Serial client on the stdlib (`providers/improv.py`) talks to the box over its USB serial port. `api/room_box.py` finds the port by USB id in sysfs, downloads and checks the firmware from the release, runs `uvx esptool` with progress, reaches the box over the ESPHome API and finds its Home Assistant entry. winstond exposes it under `/api/setup/box`, and `web/src/pages/setup-box.tsx` is the step. Software only: the real box is checked when Aldin is home (last section).

**Tech Stack:** Python 3 stdlib (`termios`, `tty`, `asyncio`), aioesphomeapi (already a dependency), httpx, FastAPI; esptool 5.6.0 through `uvx` (GPL-2.0, never imported); React with shadcn/ui.

**Spec:** `docs/setup.md` (The steps, item 4) and `docs/plan.md` Setup track step 8. Read `CLAUDE.md`, `docs/writing-docs.md` and `docs/ui-guide.md` first.

## Global Constraints

- The box is found by USB id `303a:1001` (Espressif USB JTAG/serial), from `/sys/class/tty/*/device/../idVendor` and `idProduct`; its device path is `/dev/` plus `DEVNAME` from that entry's `uevent`.
- Firmware: `voice-pe-winston-<version>.factory.bin` from the release `v<version>` of `winston.__version__`, checked against that release's `SHA256SUMS`; base address from `WINSTON_RELEASES` as `setup.release()` reads it. Kept in `~/.cache/winston/firmware/` (`$XDG_CACHE_HOME` when set).
- Flashing: `uvx esptool@5.6.0 --chip esp32s3 --port <port> write-flash --erase-all 0x0 <file>`. Never esptool as a dependency (decisions 2026-10-23).
- The Wi-Fi password is never logged, stored or returned by any API.
- Wake word: `okay_winston` when `assistant.name` is Winston (case-insensitive); else one of `okay_nabu`, `hey_jarvis`, `hey_mycroft`, picked in the step.
- Commits are signed off (`git commit -s`). No em dashes anywhere (`just lint`). Work on branch `setup-step-8`.
- The step id is `box`, after `who` and before Done; its title "A room box".

## Review Focus

1. **Not in the serial group:** opening the port fails with a permission error. Expect: "This computer's user may not open <port>. Run `winston setup` again, then log out and in." Test in Task 1 (a pty chmod 000).
2. **Wrong Wi-Fi password:** the box reports Improv error 3. Expect: "The box could not join <ssid>. Check the password." and a second try works without flashing again. Test in Task 1 and Task 3.
3. **A network Winston cannot reach** (the box joins an IoT VLAN): expect 409 "The box joined <ssid> at <address>, but this computer cannot reach it there (port 6053)." and nothing saved. Test in Task 2 and Task 3.
4. **A firmware file that does not match `SHA256SUMS`:** refused, esptool never runs, nothing cached. Test in Task 2.
5. **A network the scan does not show** (hidden, or the box's 2.4 GHz-only radio missing a 5 GHz one): a typed network name is accepted. Test in Task 3.

---

### Task 1: Improv Serial client, and a fake box on a pty

**Files:**
- Create: `src/winston/providers/improv.py`
- Create: `tests/fake_box.py`
- Test: `tests/improv_cases.py`

**Interfaces:**
- Produces:
  - `packet(kind: int, data: bytes) -> bytes`: `b"IMPROV" + bytes([1, kind, len(data)]) + data + bytes([checksum])`, checksum the low byte of the sum of every byte before it.
  - `class Stream`: `feed(chunk: bytes) -> list[tuple[int, bytes] | str]`: complete packets as `(kind, data)` and complete text lines (the box's log, `\r\n` stripped) in arrival order; a packet with a wrong checksum is dropped.
  - `@dataclass Info(firmware: str, version: str, chip: str, name: str)`, `@dataclass Network(ssid: str, rssi: int, secured: bool)`.
  - `class ImprovError(Exception)` with `.why: str` in `("denied", "no_answer", "wrong_password", "no_address")`.
  - `class Box`: `async with Box(port: str) as box:` opens the tty raw with `os.open(port, O_RDWR | O_NOCTTY | O_NONBLOCK)`, `tty.setraw`, 115200 baud, reading through `loop.add_reader`. Methods: `async info() -> Info`, `async scan() -> list[Network]` (strongest first, one entry per SSID), `async join(ssid: str, password: str, timeout: float = 40) -> str` returning the box's IPv4 address.
- Constants: packet kinds `STATE=1, ERROR=2, RPC=3, RESULT=4`; states `READY=2, PROVISIONING=3, PROVISIONED=4`; errors `UNABLE_TO_CONNECT=3`; RPC commands `WIFI=1, CURRENT_STATE=2, DEVICE_INFO=3, SCAN=4`. An RPC packet's data is `[command, len(rest)] + rest`, strings length-prefixed. A scan answers one RESULT per network `[ssid, rssi, "YES"|"NO"]`, then an empty RESULT.
- The address: the factory firmware has no `web_server`, so its Improv result carries no URL. `join` takes the first IPv4 from a RESULT URL if one comes, else from the log line matching `IP Address(?:es)?:\s*([0-9.]+)` within 15 s of PROVISIONED, else raises `no_address`.

- [ ] **Step 1: Write `tests/fake_box.py`.** `FakeBox(networks: list[Network], password: str, address: str, firmware=("Winston", "26.9.0-winston"))` opens a pty (`os.openpty`) and serves it from a thread: answers DEVICE_INFO, SCAN and CURRENT_STATE, writes log lines between packets ("[D][wifi:123]: Scanning..."), and on WIFI goes PROVISIONING, then PROVISIONED and an empty RESULT plus "[C][wifi:456]:   IP Address: <address>" for the right password, or ERROR 3 after 1 s for a wrong one. `.port` is the slave's path. `.reboot()` drops what it was told, as a flash does.

- [ ] **Step 2: Write the failing tests in `tests/improv_cases.py`** with `harness.check` like the other cases:

```python
check("a packet's checksum", improv.packet(2, b"\x03\x00")[-1], sum(b"IMPROV\x01\x02\x02\x03\x00") & 0xFF)
check("log text between packets is kept as lines", [x for x in s.feed(b"[D] hi\r\n" + improv.packet(1, b"\x02")) ], ["[D] hi", (1, b"\x02")])
check("device info", info, improv.Info("Winston", "26.9.0-winston", ..., ...))
check("networks, strongest first, one per name", [n.ssid for n in nets], ["Home", "Neighbour"])
check("joined, at the address from its log", address, "127.0.0.1")
check("a wrong password", why, "wrong_password")
check("joined after a wrong password, no reflash", address2, "127.0.0.1")
check("a port this user may not open", why_denied, "denied")   # os.chmod(fake.port, 0)
check("a port with nothing answering", why_silent, "no_answer")  # a pty with no fake behind it
```

- [ ] **Step 3: Run it to see it fail.** `uv run python tests/improv_cases.py` fails with `ModuleNotFoundError: winston.providers.improv`.

- [ ] **Step 4: Implement `src/winston/providers/improv.py`** to the interfaces above, with a module docstring naming the protocol (https://www.improv-wifi.com/serial/) and why the address comes from the log.

- [ ] **Step 5: Run it to see it pass.** `uv run python tests/improv_cases.py` passes every check, and `uv run winston check unit` picks it up.

- [ ] **Step 6: Commit.** `git commit -s -m "Improv Serial client for a Voice PE on USB, with a fake box"`.

---

### Task 2: The box's setup in winstond (find, firmware, flash, reach, Home Assistant)

**Files:**
- Create: `src/winston/api/room_box.py`
- Modify: `tests/fake_ha.py` (an ESPHome entry and its device, `config_entries/get`, `config_entries/disable`)
- Test: `tests/room_box_cases.py`

**Interfaces:**
- Consumes: `improv.Box`, `improv.Info`, `improv.ImprovError` (Task 1); `setup.RELEASES`; `providers.home_assistant._Socket`, `HomeAssistant`.
- Produces:
  - `find(sys_tty: Path = SYS_TTY) -> list[str]`, `SYS_TTY = Path(os.environ.get("WINSTON_SYS_TTY", "/sys/class/tty"))` (for the checks, as `WINSTON_RELEASES` is).
  - `async firmware(version: str = __version__) -> Path`: cached file, or downloaded and checked; raises `BoxError("The firmware for <version> does not match its SHA256SUMS, so nothing was flashed.")` or `BoxError("No firmware for <version> in its release. In a clone, build it as docs/voice-pe.md says.")`.
  - `@dataclass Flash(port: str, stage: str = "downloading", percent: int = 0, error: str = "")`, stage in `downloading | writing | starting | done | error`; module-level `FLASH: Flash | None`.
  - `start_flash(port: str) -> Flash`: one at a time (a second raises `BoxError("The box is being flashed already.")`), runs in a task: firmware, then esptool (percent from `(\d+(?:\.\d+)?)\s*%` on its output), then waits up to 20 s for `info()` to answer.
  - `async reach(host: str, port: int = 6053) -> str`: the box's MAC from `APIClient(host, port, None).device_info()`, 10 s; raises `BoxError` with the Review Focus 3 sentence.
  - `async ha_entry(cfg: WinstonConfig, mac: str) -> dict | None`: `{"entry_id", "title", "disabled"}` of the `esphome` config entry whose device has connection `["mac", mac]` (case-insensitive), else None; None too when Home Assistant is not connected.
  - `async disable(cfg: WinstonConfig, entry_id: str) -> None`: `config_entries/disable` with `disabled_by: "user"`.
  - `class BoxError(Exception)`: its message is the sentence the page shows.

- [ ] **Step 1: Extend `tests/fake_ha.py`:** a device `{"id": "dev_box", "name": "Home Assistant Voice 0a1b2c", "connections": [["mac", "00:00:00:00:00:01"]], "config_entries": ["entry_box"]}`, `config_entries/get` (with a `domain` filter) returning `entry_box` (domain `esphome`, `disabled_by: None`), and `config_entries/disable` recorded in `writes` and applied.

- [ ] **Step 2: Write the failing tests in `tests/room_box_cases.py`:** a temporary sysfs tree (`ttyACM0` with `idVendor 303a`, `idProduct 1001`, `uevent DEVNAME=<the fake box's pts name>`; `ttyUSB0` with `10c4:ea60`), a release folder served over HTTP (`SHA256SUMS` and the `.factory.bin`, as `tests/setup_distros.py` lays them out), a fake `uvx` script first on `PATH` that records its arguments and prints `Writing at 0x00010000 [====>    ] 40.0% ...` then `100.0%`, `FakeVoicePE` from `tests/satellite_fake.py` for the API, and `tests/fake_ha.py`.

```python
check("found by its USB id, not other serial ports", room_box.find(tree), [fake.port])
check("esptool as the release pins it", recorded_args[:7], ["esptool@5.6.0", "--chip", "esp32s3", "--port", fake.port, "write-flash", "--erase-all"])
check("progress, then done", (seen_percents[-1], flash.stage), (100, "done"))
check("a firmware file that does not match", (error, uvx_ran, cached), ("The firmware for 0.1.0 does not match its SHA256SUMS, so nothing was flashed.", False, False))
check("reached, by its MAC", await room_box.reach("127.0.0.1", fake_api_port), "00:00:00:00:00:01")
check("not reachable", str(err), "The box joined Home at 127.0.0.2, but this computer cannot reach it there (port 6053).")
check("Home Assistant's entry for it", await room_box.ha_entry(cfg, "00:00:00:00:00:01"), {"entry_id": "entry_box", "title": ..., "disabled": False})
check("disabled there", fake_ha.writes[-1]["type"], "config_entries/disable")
```

- [ ] **Step 3: Run it to see it fail.** `uv run python tests/room_box_cases.py` fails with `ModuleNotFoundError: winston.api.room_box`.

- [ ] **Step 4: Implement `src/winston/api/room_box.py`.** The download reuses `setup.release`'s way of reading `SHA256SUMS`: lift its SHA256SUMS read into `setup.sums(version) -> tuple[str, dict[str, str]]` (base URL, name to digest) so both use it, and keep `release()` as it was. `uvx` is found with `shutil.which`; when it is missing, `BoxError("uv is not installed here, so the box cannot be flashed.")`.

- [ ] **Step 5: Run it to see it pass.** `uv run python tests/room_box_cases.py` passes, and so do `tests/setup_cases.py` and `tests/ha_signin_cases.py`, unchanged.

- [ ] **Step 6: Commit.** `git commit -s -m "Room box setup in winstond: find, firmware, flash, reach, Home Assistant's entry"`.

---

### Task 3: The step in winstond's API

**Files:**
- Modify: `src/winston/api/setup_link.py:15` (`STEPS = ("brain", "home_assistant", "who", "box")`)
- Modify: `src/winston/api/app.py` (routes beside `/api/setup/finish`)
- Modify: `tests/room_box_cases.py` (a throwaway winstond)
- Modify: `tests/setup_link_cases.py` if it counts the steps

**Interfaces:**
- Consumes: all of Task 2.
- Produces (response models in `app.py` with `Field(description=...)` as the others):
  - `GET /api/setup/box` → `BoxState{ports: list[str], info: BoxInfo | None, flash: FlashState | None}`. `info` is `{firmware, version, winston: bool}` from `Box.info()` of the first port, `winston` true when the version ends in `-winston`; None when no port answers.
  - `POST /api/setup/box/flash` `{port}` → `FlashState{port, stage, percent, error}`; 404 for a port `find()` does not list, 409 while one runs.
  - `POST /api/setup/box/networks` `{port}` → `list[Network]`.
  - `POST /api/setup/box/wifi` `{port, ssid, password}` → `BoxJoined{host, mac, ha_entry: HAEntry | None}`; 422 with the `ImprovError` or `BoxError` sentence. The body is never logged, and the log line names only the SSID and the address.
  - `POST /api/setup/box/ha-disable` `{entry_id}` → 204.
  - All behind the same guard as the other setup routes (the setup key from another address; the same-site write guard on POSTs).

- [ ] **Step 1: Add the failing checks to `tests/room_box_cases.py`**, against `winston up --no-start` with `WINSTON_SYS_TTY`, `WINSTON_RELEASES` and `PATH` set as in Task 2:

```python
check("the step comes after Who he is", httpx.get(f"{here}/api/setup").json()["order"], ["brain", "home_assistant", "who", "box"])
check("a box found and asked", state["ports"], [fake.port]); check("its firmware", state["info"]["winston"], False)
check("a port not found is refused", post("/api/setup/box/flash", port="/dev/ttyS0").status_code, 404)
check("flashed through the API", poll_flash()["stage"], "done")
check("a wrong password, in words", (r.status_code, r.json()["detail"]), (422, "The box could not join Home. Check the password."))
check("a network the scan did not show", post_wifi(ssid="Hidden", password=PW).json()["host"], "127.0.0.1")
check("the password in no log", any(PW in f.read_text() for f in (data / "logs").rglob("*") if f.is_file()), False)
```

- [ ] **Step 2: Run it to see it fail.** It fails with 404 on `/api/setup/box`.

- [ ] **Step 3: Implement the routes and the step id.**

- [ ] **Step 4: Run it to see it pass.** `uv run python tests/room_box_cases.py` and `tests/setup_link_cases.py` pass.

- [ ] **Step 5: Commit.** `git commit -s -m "The room box step in winstond's API"`.

---

### Task 4: The wizard's step, its docs and screenshots

**Files:**
- Create: `web/src/pages/setup-box.tsx`
- Modify: `web/src/pages/setup.tsx:88-91` (`{step === 'box' ? <BoxStep page={s.page} next={next} /> : null}`)
- Modify: `web/src/lib/setup.ts` (`box: {title: 'A room box', intro: 'Plug a Voice PE into this computer with USB-C. He puts his firmware on it, gives it your Wi-Fi and puts it in a room.'}`)
- Modify: `web/src/api/schema.d.ts` (by `pnpm api`)
- Modify: `web/tests/setup.spec.ts`
- Modify: `docs/setup.md`, `docs/plan.md` (Setup track status), `docs/voice-pe.md` (Setting one up: from the wizard first), `docs/decisions.md`, `docs/architecture.md` (the Setup line)
- Create: `docs/images/setup-box-light.png`, `docs/images/setup-box-dark.png`

**Interfaces:**
- Consumes: the routes of Task 3 through `$api`; `StepActions`, `useBusy` and `StepProps` from `setup.tsx` (export them).
- Produces: `export function BoxStep({ page, next }: Pick<StepProps, 'page' | 'next'>)`.

The step, top to bottom, one stage at a time (`docs/ui-guide.md` for components and words):

1. **No box:** "Waiting for a Voice PE on USB-C." (`GET /api/setup/box` polled every 2 s), Skip for now.
2. **Found:** "Found a Voice PE on /dev/ttyACM0, running <firmware> <version>." Button "Put Winston's firmware on it". When `info.winston`: "It already runs Winston's firmware." with Continue, and Flash again as a secondary button. While flashing: "Downloading the firmware", "Writing the firmware, 42%" (a progress bar), "Starting the box"; on error, the sentence and Try again.
3. **Wi-Fi:** the networks (strongest first, a lock for a secured one), "Another network" for typing a name, the password, Connect. Under the list: "Only 2.4 GHz networks show: the box cannot use 5 GHz." Errors are shown in place, and Connect can be tried again.
4. **Room:** Room (text, required), Home Assistant area (the `ha_areas` options, as the box dialog on Ways to talk has), and the wake word only when his name is not Winston (Okay Nabu, Hey Jarvis, Hey Mycroft). When `ha_entry` is not disabled: "Home Assistant had this box, and its entry there would come back as a new device." with a switch "Disable it in Home Assistant", on. The button "Add the box and continue" sets `channels.satellite.devices[room] = {host, wake_word, area?}` and `channels.satellite.enabled = true`, saves through `page.save()`, posts `ha-disable` when the switch is on, then `next('done')`.

- [ ] **Step 1: Write the failing Playwright test** in `web/tests/setup.spec.ts`, at 1400 and 390 px like the others: step 4 is "A room box", it says "Waiting for a Voice PE on USB-C.", and Skip for now goes to Done with "A room box was skipped." listed there.

- [ ] **Step 2: Run it to see it fail.** `pnpm smoke` fails on the missing step.

- [ ] **Step 3: Implement the step**, run `pnpm api`, and build (`pnpm build`).

- [ ] **Step 4: Walk the whole step by hand** in Chromium against a throwaway winstond with the fakes of Task 3 (`WINSTON_SYS_TTY`, `WINSTON_RELEASES`, the fake `uvx`, `FakeBox`, `FakeVoicePE`, the fake Home Assistant): found, flashed with progress, a wrong password then the right one, the room set, Home Assistant's entry disabled, the box listed on Ways to talk. Take `setup-box-light.png` and `setup-box-dark.png` at the room stage.

- [ ] **Step 5: Run every check.** `pnpm smoke` (the setup spec passes at both widths), `uv run winston check unit`, `just lint`.

- [ ] **Step 6: Write the docs.**
  - `setup.md`: item 4 is built; the opening paragraph's "Built so far" gains it, and the Hardware check under it stays open.
  - `plan.md`: "Status 2026-11-20: step 8 built and checked on fakes; the box check waits for Aldin at home", with the checks and their counts.
  - `voice-pe.md`: "From the wizard" first under Setting one up.
  - `decisions.md`, one line each: stdlib `termios` instead of pyserial (no new dependency); the address from the box's log, since the factory firmware's Improv answer has no URL; `--erase-all`, so a key Home Assistant set does not survive the flash; esptool 5.6.0 pinned through `uvx`.

- [ ] **Step 7: Squash the branch into one commit,** as the other steps are: `git commit -s -m "Setup track step 8: a room box over USB, built and checked on fakes"`.

---

### Task 5: The README's Quickstart, ready for v0.1.0

**Files:**
- Modify: `README.md` (Quickstart, Status) on branch `release-v0.1.0`, not merged to main until v0.1.0 is published.

- [ ] **Step 1: Rewrite the Quickstart around the one line**, reading the whole README first (CLAUDE.md, The README is a front page):

```sh
curl --proto '=https' -fsSL https://github.com/aldinsmajlovic/winston/releases/latest/download/install.sh | sh
```

  Then one paragraph: it installs on Linux, asks before anything runs as root, and prints a link that opens the setup on any device. Needs: Linux, Home Assistant, and a 24 GB NVIDIA card, Ollama, or a hosted model. A line pointing to `docs/setup.md` for reading the script first and for a clone. The Status keeps two lines.

- [ ] **Step 2: Check it.** `just lint`, and every link in it opens.

- [ ] **Step 3: Commit on `release-v0.1.0`** with `git commit -s -m "README: the one-line install"`, not pushed and not merged.

---

## When Aldin is home (not in this plan's tasks)

- Record a real box's Improv exchange (scan, a wrong password, joined, the log's address line) and replay it in `tests/improv_cases.py` beside the fake, as step 8's acceptance asks.
- After a full flash backup (`uvx esptool@5.6.0 --port /dev/ttyACM0 read-flash 0 ALL voice-pe-backup.bin`): flashed and given Wi-Fi from the wizard, in a room, "Okay Winston" answered.
- Step 6's sign-in on the real Home Assistant, and step 9's timed run from nothing to talking.
