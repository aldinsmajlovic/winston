"""A room box over USB (docs/setup.md, the wizard's A room box): a Voice PE plugged into this computer is found
by its USB id, gets Winston's firmware from the release this Winston came from (checked against its
SHA256SUMS, flashed with esptool through `uvx`, never imported: it is GPL-2.0), then Wi-Fi over Improv Serial
(providers/improv.py). Winston checks he can reach it at the address it joined with, and finds Home
Assistant's old entry for it, which would bring it back there as a new device.

One thing at a time talks to the port: a flash, a scan, a join or a look at what the box runs. A look while
the port is busy answers with what was seen last, so the page's polling never cuts into esptool."""
import asyncio
import hashlib
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

import httpx
import aiohttp
from aioesphomeapi import APIClient, APIConnectionError, InvalidEncryptionKeyAPIError, RequiresEncryptionAPIError
from loguru import logger

from winston import __version__, setup
from winston.config.schema import WinstonConfig
from winston.providers import improv

SYS_TTY = Path(os.environ.get("WINSTON_SYS_TTY", "/sys/class/tty"))     # elsewhere for the checks
USB_ID = ("303a", "1001")                                               # Espressif USB JTAG/serial
ESPTOOL = "esptool@5.6.0"
START_SECS = 20             # for the box to answer again after the flash resets it
REACH_SECS = 10
SCAN_SECS = 15              # a box just flashed finishes its first scan about 8 s after it answers
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


async def reach(host: str, ssid: str, port: int = 6053) -> str:
    """The box's MAC address over its ESPHome API, which is how Winston will talk to it."""
    client = APIClient(host, port, None, client_info="Winston")
    try:
        await asyncio.wait_for(client.connect(login=True), REACH_SECS)
        info = await asyncio.wait_for(client.device_info(), REACH_SECS)
    except (RequiresEncryptionAPIError, InvalidEncryptionKeyAPIError):
        raise BoxError("The box has a key from Home Assistant, so Winston cannot talk to it. "
                       "Flash it again, which erases the key.") from None
    except (APIConnectionError, OSError, TimeoutError) as e:
        logger.info(f"room box: {host}:{port} not reached: {type(e).__name__}: {e}")
        raise BoxError(f"The box joined {ssid} at {host}, but this computer cannot reach it there (port {port}).") from None
    finally:
        await client.disconnect(force=True)
    return info.mac_address


async def ha_entry(cfg: WinstonConfig, mac: str) -> dict | None:
    """Home Assistant's ESPHome entry for the box with this MAC, if Home Assistant is connected and has one."""
    from winston.providers.home_assistant import HomeAssistant, _Socket
    try:
        ha = HomeAssistant(cfg)
    except RuntimeError:
        return None
    async with ha._session() as s, _Socket(s, ha.url, ha.token) as ws:
        devices = await ws.call("config/device_registry/list") or []
        entries = {e["entry_id"]: e for e in await ws.call("config_entries/get", domain="esphome") or []}
    for d in devices:
        if any(kind == "mac" and value.lower() == mac.lower() for kind, value in d.get("connections") or []):
            for entry_id in d.get("config_entries") or []:
                if e := entries.get(entry_id):
                    return {"entry_id": entry_id, "title": e.get("title") or "", "disabled": bool(e.get("disabled_by"))}
    return None


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
