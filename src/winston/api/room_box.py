"""A room box over USB (docs/setup.md, the wizard's A room box): a Voice PE plugged into this computer is found
by its USB id, gets Winston's firmware from the release this Winston came from (checked against its
SHA256SUMS, flashed with esptool through `uvx`, never imported: it is GPL-2.0), then Wi-Fi over Improv Serial
(providers/improv.py). Winston reaches it at the address it joined with and gives it a key of his own, kept in
data/.env. Home Assistant then gets the same key, with its voice assistant for the box switched off so Winston
keeps the microphone, or its old entry for the box is disabled.

One thing at a time talks to the port: a flash, a scan, a join or a look at what the box runs. A look while
the port is busy answers with what was seen last, so the page's polling never cuts into esptool."""
import asyncio
import base64
import hashlib
import os
import re
import secrets as keys
import shutil
from dataclasses import dataclass
from pathlib import Path

import httpx
import aiohttp
from aioesphomeapi import (ZERO_NOISE_PSK, APIClient, APIConnectionError, DeviceInfo, InvalidEncryptionKeyAPIError,
                           RequiresEncryptionAPIError)
from loguru import logger

from winston import __version__, setup
from winston.api import secrets
from winston.config.schema import WinstonConfig
from winston.providers import improv

SYS_TTY = Path(os.environ.get("WINSTON_SYS_TTY", "/sys/class/tty"))     # elsewhere for the checks
USB_ID = ("303a", "1001")                                               # Espressif USB JTAG/serial
ESPTOOL = "esptool@5.6.0"
START_SECS = 20             # for the box to answer again after the flash resets it
REACH_SECS = 10
SCAN_SECS = 15              # a box just flashed finishes its first scan about 8 s after it answers
SHARE_SECS = 30             # for Home Assistant to reconnect to the box and ask for its key
UNKNOWN_KEY = "The box has a key Winston does not know. Flash it again, which erases it."
PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s*%")


class BoxError(Exception):
    """Its message is the sentence the page shows."""


@dataclass
class Flash:
    port: str
    stage: str = "downloading"          # downloading | writing | starting | done | error
    percent: int = 0
    error: str = ""


FLASH: Flash | None = None
_lock = asyncio.Lock()
_seen: dict[str, improv.Info] = {}      # port: what it ran when last asked
_tasks: set[asyncio.Task] = set()


def find() -> list[str]:
    """The Voice PEs plugged in, as /dev paths, from sysfs: a tty whose USB device has the box's ids."""
    out = []
    try:
        ttys = sorted(SYS_TTY.iterdir())
    except OSError:
        return []
    for tty in ttys:
        try:
            usb = tty / "device" / ".."           # the kernel follows the link, then goes up to the device
            ids = ((usb / "idVendor").read_text().strip(), (usb / "idProduct").read_text().strip())
            if ids != USB_ID:
                continue
            uevent = dict(line.partition("=")[::2] for line in (tty / "uevent").read_text().splitlines())
            out.append(f"/dev/{uevent.get('DEVNAME') or tty.name}")
        except OSError:
            continue
    return out


async def probe() -> tuple[list[str], improv.Info | None]:
    """The boxes plugged in and what the first one runs: asked once per port, or what was seen last while
    the port is busy."""
    ports = find()
    if not ports:
        _seen.clear()
        return [], None
    port = ports[0]
    if port in _seen or _lock.locked():
        return ports, _seen.get(port)
    async with _lock:
        try:
            async with improv.Box(port) as box:
                _seen[port] = await box.info()
        except improv.ImprovError as e:
            logger.info(f"room box: {e}")
    return ports, _seen.get(port)


def _cache() -> Path:
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "winston/firmware"


async def firmware(version: str = __version__, progress: Flash | None = None) -> Path:
    """Winston's Voice PE firmware for `version`, from its release: kept once it matched its SHA256SUMS."""
    name = f"voice-pe-winston-{version}.factory.bin"
    path = _cache() / name
    if path.exists():
        return path
    missing = BoxError(f"No firmware for {version} in its release. In a clone, build it as docs/voice-pe.md says.")
    try:
        url, files = await asyncio.to_thread(setup.sums, version)
    except httpx.HTTPStatusError:
        raise missing from None
    except httpx.HTTPError as e:
        raise BoxError(f"Could not download the firmware: {e}.") from None
    if name not in files:
        raise missing
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp, digest = path.with_suffix(".part"), hashlib.sha256()
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=60) as client, \
                client.stream("GET", f"{url}/{name}") as r:
            r.raise_for_status()
            size, done = int(r.headers.get("content-length") or 0), 0
            with tmp.open("wb") as out:
                async for chunk in r.aiter_bytes():
                    out.write(chunk)
                    digest.update(chunk)
                    done += len(chunk)
                    if progress and size:
                        progress.percent = done * 100 // size
    except httpx.HTTPError as e:
        tmp.unlink(missing_ok=True)
        raise BoxError(f"Could not download the firmware: {e}.") from None
    if digest.hexdigest() != files[name]:
        tmp.unlink(missing_ok=True)
        raise BoxError(f"The firmware for {version} does not match its SHA256SUMS, so nothing was flashed.")
    tmp.replace(path)
    return path


def start_flash(port: str) -> Flash:
    """Flashes the box on `port` in the background; FLASH says how far it is."""
    global FLASH
    if FLASH and FLASH.stage not in ("done", "error"):
        raise BoxError("The box is being flashed already.")
    if not os.access(port, os.R_OK | os.W_OK):         # not in the serial group yet: before any download
        raise BoxError(improv.DENIED.format(port=port))
    FLASH = Flash(port)
    task = asyncio.create_task(_flash(FLASH))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return FLASH


async def _flash(f: Flash):
    try:
        path = await firmware(progress=f)
        uvx = shutil.which("uvx")
        if not uvx:
            raise BoxError("uv is not installed here, so the box cannot be flashed.")
        async with _lock:
            f.stage, f.percent = "writing", 0
            _seen.pop(f.port, None)
            proc = await asyncio.create_subprocess_exec(
                uvx, ESPTOOL, "--chip", "esp32s3", "--port", f.port, "write-flash", "--erase-all", "0x0", str(path),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                env={**os.environ, "PYTHONUNBUFFERED": "1"})       # its progress as it goes, not in 8 KB lumps
            last, buf = "", b""
            while chunk := await proc.stdout.read(256):        # esptool redraws its progress after a \r
                *lines, buf = re.split(rb"[\r\n]", buf + chunk)
                for line in (ln.decode(errors="replace").strip() for ln in lines):
                    if line:
                        last = line
                    if line.startswith("Writing") and (m := PERCENT.search(line)):
                        f.percent = int(float(m.group(1)))
            if await proc.wait():
                raise BoxError(f"esptool stopped: {last or 'no output'}")
            logger.info(f"room box: flashed Winston's firmware {__version__} on {f.port}")
            f.stage, f.percent = "starting", 100
            loop = asyncio.get_running_loop()
            until = loop.time() + START_SECS
            while True:
                try:
                    async with improv.Box(f.port) as box:
                        _seen[f.port] = await box.info()
                    break
                except improv.ImprovError:
                    if loop.time() > until:
                        raise BoxError("The box did not start after its flash. Unplug it and plug it in again.") from None
                    await asyncio.sleep(1)
        f.stage = "done"
    except (BoxError, improv.ImprovError) as e:
        f.stage, f.error = "error", str(e)
    except Exception as e:                  # noqa: BLE001  a background task: the page must hear of any failure
        logger.exception("room box: flashing failed")
        f.stage, f.error = "error", f"Flashing stopped: {e}"


async def networks(port: str) -> list[improv.Network]:
    """What the box saw in its last scan. Until its first scan is done it has none, so an empty list is asked
    again for SCAN_SECS. A box already on Wi-Fi stops scanning and stays empty."""
    loop = asyncio.get_running_loop()
    until = loop.time() + SCAN_SECS
    while True:
        async with _lock, improv.Box(port) as box:
            found = await box.scan()
        if found or loop.time() > until:
            return found
        await asyncio.sleep(1)


async def join(port: str, ssid: str, password: str) -> str:
    """Gives the box this network and returns its address. The password goes to the box and nowhere else."""
    async with _lock, improv.Box(port) as box:
        address = await box.join(ssid, password)
    logger.info(f"room box: joined {ssid} at {address}")
    return address


def key_env(mac: str) -> str:
    """The variable in data/.env that holds a box's key: VOICE_PE_KEY and the end of its MAC, as its name ends."""
    return "VOICE_PE_KEY_" + mac.replace(":", "")[-6:].upper()


async def _client(host: str, port: int, psk: str | None) -> APIClient:
    client = APIClient(host, port, None, noise_psk=psk, client_info="Winston")
    try:
        await asyncio.wait_for(client.connect(login=True), REACH_SECS)
    except BaseException:
        await client.disconnect(force=True)
        raise
    return client


async def _info(host: str, port: int, psk: str | None) -> DeviceInfo:
    client = await _client(host, port, psk)
    try:
        return await asyncio.wait_for(client.device_info(), REACH_SECS)
    finally:
        await client.disconnect(force=True)


async def reach(host: str, ssid: str, port: int = 6053) -> tuple[str, str | None]:
    """The box's MAC over its ESPHome API, and the variable holding its key. A box without a key gets a new one,
    saved first under key_env(mac); a box with one must have the key saved there. A box that cannot take a key
    (not Winston's firmware) has none."""
    try:
        try:
            info = await _info(host, port, None)            # a box without a key speaks plaintext
        except RequiresEncryptionAPIError:
            mac = ""
            try:                                            # one that cannot be its key: the box says who it is
                await _info(host, port, ZERO_NOISE_PSK)
            except InvalidEncryptionKeyAPIError as e:
                mac = e.received_mac or ""
            name = key_env(mac)
            if not mac or not (key := os.environ.get(name)):
                raise BoxError(UNKNOWN_KEY) from None
            try:
                return (await _info(host, port, key)).mac_address, name
            except InvalidEncryptionKeyAPIError:
                raise BoxError(UNKNOWN_KEY) from None
        if not info.api_encryption_supported:
            return info.mac_address, None
        name, key = key_env(info.mac_address), base64.b64encode(keys.token_bytes(32)).decode()
        secrets.write(name, key)                            # kept before the box has it, so it is never lost
        # Over Noise with the zero key where the box offers it, so the key is not sent in the clear.
        client = await _client(host, port, ZERO_NOISE_PSK if info.api_encryption_provisionable else None)
        try:
            taken = await asyncio.wait_for(client.noise_encryption_set_key(key.encode()), REACH_SECS)
        finally:
            await client.disconnect(force=True)
        if not taken:
            raise BoxError("The box did not take Winston's key. Flash it again.")
        await _info(host, port, key)
        logger.info(f"room box: gave {info.mac_address} its key, kept as {name}")
        return info.mac_address, name
    except (APIConnectionError, OSError, TimeoutError) as e:
        logger.info(f"room box: {host}:{port} not reached: {type(e).__name__}: {e}")
        raise BoxError(f"The box joined {ssid} at {host}, but his computer cannot reach it there (port {port}).") from None


TAKEN_FROM_HA = ("assist_satellite", "update")      # its voice assistant would take the mic; its update the firmware


async def _device(ws, mac: str) -> tuple[dict | None, dict | None]:
    """The box's device in Home Assistant's registry and its ESPHome entry there, found by its MAC."""
    devices = await ws.call("config/device_registry/list") or []
    entries = {e["entry_id"]: e for e in await ws.call("config_entries/get", domain="esphome") or []}
    for d in devices:
        if any(kind == "mac" and value.lower() == mac.lower() for kind, value in d.get("connections") or []):
            for entry_id in d.get("config_entries") or []:
                if e := entries.get(entry_id):
                    return d, e
    return None, None


async def ha_entry(cfg: WinstonConfig, mac: str) -> dict | None:
    """Home Assistant's ESPHome entry for the box with this MAC, if it has one. Without Home Assistant connected it
    raises RuntimeError."""
    from winston.providers.home_assistant import HomeAssistant, _Socket
    ha = HomeAssistant(cfg)
    async with ha._session() as s, _Socket(s, ha.url, ha.token) as ws:
        _, e = await _device(ws, mac)
    return e and {"entry_id": e["entry_id"], "title": e.get("title") or "", "disabled": bool(e.get("disabled_by"))}


async def share(cfg: WinstonConfig, mac: str, host: str, port: int = 6053) -> None:
    """Gives Home Assistant the box's key: its old entry through the key prompt Home Assistant opens once the key no
    longer matches, or a new entry. Then switches off its voice assistant for the box, so Winston keeps the
    microphone, and its firmware update, which would put the stock firmware back."""
    from winston.providers.home_assistant import HomeAssistant, _Socket
    if not (key := os.environ.get(key_env(mac))):
        raise BoxError("Winston has no key for this box. Connect it to Wi-Fi again.")
    loop = asyncio.get_running_loop()
    until = loop.time() + SHARE_SECS
    try:
        ha = HomeAssistant(cfg)
        async with ha._session(SHARE_SECS * 3) as s, _Socket(s, ha.url, ha.token) as ws:
            flows = f"{ha.url}/api/config/config_entries/flow"

            async def rest(method: str, url: str, body: dict | None = None) -> dict:
                async with s.request(method, url, json=body, headers=ha._headers) as r:
                    r.raise_for_status()
                    return await r.json()

            async def esphome_flows(**context) -> list[dict]:
                return [f for f in await ws.call("config_entries/flow/progress") or []
                        if f.get("handler") == "esphome"
                        and all(str(f["context"].get(k, "")).lower() == v.lower() for k, v in context.items())]

            device, entry = await _device(ws, mac)
            if entry:
                eid, started = entry["entry_id"], loop.time()
                for f in await esphome_flows(entry_id=eid):         # from before: it would hold the new prompt back
                    await rest("DELETE", f"{flows}/{f['flow_id']}")
                if entry.get("disabled_by"):
                    await ws.call("config_entries/disable", entry_id=eid, disabled_by=None)
                else:
                    await rest("POST", f"{ha.url}/api/config/config_entries/entry/{eid}/reload")
                while True:
                    if asked := [f for f in await esphome_flows(entry_id=eid) if f.get("step_id") == "reauth_confirm"]:
                        step = await rest("POST", f"{flows}/{asked[0]['flow_id']}", {"noise_psk": key})
                        if step.get("reason") != "reauth_successful":
                            raise BoxError(f"Home Assistant refused the box's key ({step.get('errors') or step.get('reason')}).")
                        break
                    # No prompt and the box's entities back: Home Assistant had this key already.
                    if loop.time() > started + 3 and await _available(ws, rest, ha.url, device):
                        break
                    if loop.time() > until:
                        raise BoxError(f"Home Assistant did not ask for the box's key within {SHARE_SECS} s. "
                                       "Check that it reaches the box (port 6053).")
                    await asyncio.sleep(1)
            else:
                for f in await esphome_flows(unique_id=mac):        # its discovery of the box would stop a new flow
                    await rest("DELETE", f"{flows}/{f['flow_id']}")
                step = await rest("POST", flows, {"handler": "esphome"})
                if step.get("step_id") == "user":
                    step = await rest("POST", f"{flows}/{step['flow_id']}", {"host": host, "port": port})
                if step.get("step_id") == "encryption_key":
                    step = await rest("POST", f"{flows}/{step['flow_id']}", {"noise_psk": key})
                if step.get("type") != "create_entry":
                    raise BoxError(f"Home Assistant did not add the box "
                                   f"({step.get('reason') or step.get('errors') or step.get('step_id')}).")
            while True:                                             # a new entry registers its entities once connected
                device, entry = await _device(ws, mac)
                mine = [e for e in await ws.call("config/entity_registry/list") or []
                        if device and e.get("device_id") == device["id"]]
                if any(e["entity_id"].startswith("assist_satellite.") for e in mine) or loop.time() > until:
                    break
                await asyncio.sleep(1)
            off = [e["entity_id"] for e in mine if e["entity_id"].split(".")[0] in TAKEN_FROM_HA and not e.get("disabled_by")]
            for entity_id in off:
                await ws.call("config/entity_registry/update", entity_id=entity_id, disabled_by="user")
            if off and entry:                                       # now, not in 30 s: it lets the mic go
                await rest("POST", f"{ha.url}/api/config/config_entries/entry/{entry['entry_id']}/reload")
    except BoxError:
        raise
    except (RuntimeError, OSError, TimeoutError, aiohttp.ClientError) as e:      # HAError is a RuntimeError
        raise BoxError(f"Home Assistant did not take the box's key ({e}).") from None
    logger.info(f"room box: Home Assistant has {mac}'s key; switched off there: {', '.join(off) or 'nothing'}")


async def _available(ws, rest, url: str, device: dict | None) -> bool:
    """Whether any of the device's entities has a state again."""
    for e in await ws.call("config/entity_registry/list") or []:
        if device and e.get("device_id") == device["id"] and not e.get("disabled_by"):
            try:
                if (await rest("GET", f"{url}/api/states/{e['entity_id']}")).get("state") != "unavailable":
                    return True
            except aiohttp.ClientResponseError:
                continue
    return False


async def disable(cfg: WinstonConfig, entry_id: str) -> None:
    """Disables Home Assistant's entry for the box, so it does not come back there as a new device."""
    from winston.providers.home_assistant import HomeAssistant, _Socket
    try:
        ha = HomeAssistant(cfg)
        async with ha._session() as s, _Socket(s, ha.url, ha.token) as ws:
            await ws.call("config_entries/disable", entry_id=entry_id, disabled_by="user")
    except (RuntimeError, OSError, TimeoutError, aiohttp.ClientError) as e:      # HAError is a RuntimeError
        raise BoxError(f"Home Assistant did not disable its old entry for the box ({e}). "
                       "Disable it there by hand, under ESPHome.") from None
    logger.info(f"room box: Home Assistant's entry {entry_id} disabled")
