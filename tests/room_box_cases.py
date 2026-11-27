"""A room box over USB in winstond (src/winston/api/room_box.py, docs/setup.md, A room box), with nothing real:
a sysfs tree with a Voice PE's USB id and another serial port, the fake box on a pty (tests/fake_box.py), a
release served over HTTP as GitHub lays it out, a fake `uvx` that records how esptool was run, a small fake of
the box's ESPHome API, and tests/fake_ha.py with the box's old ESPHome entry.

Found by its USB id; the firmware downloaded, checked and flashed with esptool as the release pins it, with
progress; one that does not match its SHA256SUMS refused before esptool runs; the port left alone while it
flashes; the box reached by its MAC, or said to be unreachable; Home Assistant's entry for it found and
disabled.

    uv run python tests/room_box_cases.py
"""
import asyncio
import base64
import functools
import subprocess
import hashlib
import http.server
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

import httpx
from dotenv import dotenv_values
from fake_box import FakeBox, Net
from harness import check, finish_checks, free_port, scratch_data, winstond_data

data = scratch_data("winston-room-box-")
tmp = Path(tempfile.mkdtemp(prefix="winston-room-box-tree-"))
os.environ["XDG_CACHE_HOME"] = str(tmp / "cache")

from aioesphomeapi import ZERO_NOISE_PSK  # noqa: E402
from aioesphomeapi import api_pb2 as pb  # noqa: E402
from aioesphomeapi.core import MESSAGE_TYPE_TO_PROTO  # noqa: E402

from fake_ha import TOKEN, FakeHA  # noqa: E402
from noise.connection import NoiseConnection  # noqa: E402
from winston.api import room_box  # noqa: E402
from winston.config import load  # noqa: E402

PW = "correct horse 22"
MAC = "00:00:00:00:00:01"
VERSION = "0.1.0"


def sysfs(port: str, folder: str = "sys") -> Path:
    """/sys/class/tty as the kernel lays it out: each tty's device is its USB interface, whose parent has the ids."""
    root = tmp / folder
    for name, devname, ids in (("ttyACM0", port.removeprefix("/dev/"), ("303a", "1001")),
                               ("ttyUSB0", "ttyUSB0", ("10c4", "ea60")), ("ttyS0", "ttyS0", None)):
        (root / "class/tty" / name).mkdir(parents=True)
        (root / "class/tty" / name / "uevent").write_text(f"MAJOR=166\nMINOR=0\nDEVNAME={devname}\n")
        if ids:
            iface = root / "devices" / name / "1-1" / "1-1:1.0"
            iface.mkdir(parents=True)
            (iface.parent / "idVendor").write_text(ids[0] + "\n")
            (iface.parent / "idProduct").write_text(ids[1] + "\n")
            (root / "class/tty" / name / "device").symlink_to(iface)
    return root / "class/tty"


def release_server() -> str:
    """Two releases: VERSION with its firmware, and 0.0.9 whose SHA256SUMS does not match its file."""
    root = tmp / "releases"
    for version, firmware, digest in ((VERSION, b"\xe9" + b"firmware" * 4096, None), ("0.0.9", b"\xe9 other", "0" * 64)):
        d = root / "download" / f"v{version}"
        d.mkdir(parents=True)
        name = f"voice-pe-winston-{version}.factory.bin"
        (d / name).write_bytes(firmware)
        (d / "SHA256SUMS").write_text(f"{digest or hashlib.sha256(firmware).hexdigest()}  {name}\n"
                                      f"{'1' * 64}  winston-{version}.tar.gz\n")

    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass
    port = free_port()
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), functools.partial(Quiet, directory=str(root)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{port}"


def fake_uvx() -> Path:
    """`uvx` first on PATH: writes its arguments, then esptool's progress as it prints it (\\r between)."""
    bin_ = tmp / "bin"
    bin_.mkdir()
    args = tmp / "uvx-args"
    (bin_ / "uvx").write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$@\" > {args}; printf '%s' \"$PYTHONUNBUFFERED\" > {args}-unbuffered\n"
        "printf 'esptool v5.6.0\\nConnected to ESP32-S3 on the port:\\n'\n"
        "printf 'Writing at 0x00010000 [=====>        ]  40.0%% 1000/2500 bytes... \\r'; sleep 0.4\n"
        "printf 'Writing at 0x00020000 [==============] 100.0%% 2500/2500 bytes... \\n'\n"
        "printf 'Hash of data verified.\\nHard resetting via RTS pin...\\n'\n")
    (bin_ / "uvx").chmod(0o755)
    os.environ["PATH"] = f"{bin_}:{os.environ['PATH']}"
    return args


TYPE_OF = {cls: n for n, cls in MESSAGE_TYPE_TO_PROTO.items()}


def varint(n: int) -> bytes:
    out = b""
    while True:
        b, n = n & 0x7F, n >> 7
        out += bytes([b | (0x80 if n else 0)])
        if not n:
            return out


class ApiBox:
    """The box's ESPHome API as Winston's firmware speaks it, with the stock `encryption:`: without a key it speaks
    plaintext and Noise with the all-zeros key, and takes a key once (NoiseEncryptionSetKeyRequest); with one it
    speaks Noise with that key only. `can_key` False: a firmware without encryption. `takes` False: refuses a key."""

    def __init__(self, key: str | None = None, can_key: bool = True, takes: bool = True):
        self.key, self.can_key, self.takes = key, can_key, takes
        self.keys_set: list[str] = []
        self.server: asyncio.Server | None = None

    async def start(self, port: int, host: str = "127.0.0.1") -> "ApiBox":
        self.server = await asyncio.start_server(self.serve, host, port)
        return self

    def close(self):
        self.server.close()

    def answer(self, msg):
        if isinstance(msg, pb.HelloRequest):
            return pb.HelloResponse(api_version_major=1, api_version_minor=12, name="home-assistant-voice-0a1b2c")
        if isinstance(msg, pb.DeviceInfoRequest):
            return pb.DeviceInfoResponse(name="home-assistant-voice-0a1b2c", mac_address=MAC.upper(),
                                         api_encryption_supported=self.can_key,
                                         api_encryption_provisionable=self.can_key and self.key is None)
        if isinstance(msg, pb.NoiseEncryptionSetKeyRequest):
            ok = self.takes and self.key is None and len(base64.b64decode(msg.key)) == 32
            if ok:
                self.key = msg.key.decode()
                self.keys_set.append(self.key)
            return pb.NoiseEncryptionSetKeyResponse(success=ok)
        if isinstance(msg, pb.DisconnectRequest):
            return pb.DisconnectResponse()
        if isinstance(msg, pb.PingRequest):
            return pb.PingResponse()
        return None

    async def serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            first = (await reader.readexactly(1))[0]
            if first == 0x01 and self.can_key:
                await self.noise(reader, writer)
            elif first == 0x00 and (self.key is None or not self.can_key):
                await self.plain(reader, writer)
            else:                   # plaintext to a box with a key: it answers in Noise frames only
                writer.write(b"\x01\x00\x00")
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()

    async def plain(self, reader, writer):
        async def read_varint() -> int:
            n = shift = 0
            while True:
                b = (await reader.readexactly(1))[0]
                n |= (b & 0x7F) << shift
                shift += 7
                if not b & 0x80:
                    return n
        while True:
            size, kind = await read_varint(), await read_varint()
            msg = MESSAGE_TYPE_TO_PROTO[kind]()
            msg.ParseFromString(await reader.readexactly(size))
            if reply := self.answer(msg):
                payload = reply.SerializeToString()
                writer.write(b"\x00" + varint(len(payload)) + varint(TYPE_OF[type(reply)]) + payload)
            await reader.readexactly(1)

    async def noise(self, reader, writer):
        async def frame() -> bytes:
            head = await reader.readexactly(3)
            if head[0] != 0x01:
                raise ConnectionError("not a Noise frame")
            return await reader.readexactly((head[1] << 8) | head[2])

        def send(payload: bytes):
            writer.write(bytes((0x01, len(payload) >> 8, len(payload) & 0xFF)) + payload)
        await reader.readexactly(2)                         # the rest of the client's empty hello frame
        hello = await frame()
        send(b"\x01home-assistant-voice-0a1b2c\x00" + MAC.replace(":", "").lower().encode() + b"\x00")
        proto = NoiseConnection.from_name(b"Noise_NNpsk0_25519_ChaChaPoly_SHA256")
        proto.set_as_responder()
        proto.set_psks(base64.b64decode(self.key or ZERO_NOISE_PSK))
        proto.set_prologue(b"NoiseAPIInit\x00\x00")
        proto.start_handshake()
        try:
            proto.read_message(hello[1:])
        except Exception:           # noqa: BLE001  a key that is not the box's: as ESPHome says it
            send(b"\x01Handshake MAC failure")
            return
        send(b"\x00" + proto.write_message())
        while True:
            data = proto.decrypt(await frame())
            msg = MESSAGE_TYPE_TO_PROTO[(data[0] << 8) | data[1]]()
            msg.ParseFromString(data[4:])
            if reply := self.answer(msg):
                payload = reply.SerializeToString()
                kind = TYPE_OF[type(reply)]
                send(proto.encrypt(bytes((kind >> 8, kind & 0xFF, len(payload) >> 8, len(payload) & 0xFF)) + payload))


async def flashed(port: str) -> tuple[list[int], room_box.Flash, set[int]]:
    """Flashes the box on `port`, polling as the page does: the percents seen while writing, how it ended, and
    whether the box's port was opened by a probe meanwhile (it must not be)."""
    f = room_box.start_flash(port)
    seen, probed = [], set()
    while f.stage not in ("done", "error"):
        if f.stage == "writing":
            seen.append(f.percent)
            before = len(box.received)
            await room_box.probe()
            probed.add(len(box.received) - before)
        await asyncio.sleep(0.05)
    return seen, f, probed


async def main():
    global box
    box = FakeBox([Net("Home", -50)], PW, "127.0.0.1", firmware=("Home Assistant Voice PE", "25.10.0"))
    room_box.SYS_TTY = sysfs(box.port)
    os.environ["WINSTON_RELEASES"] = release_server()
    args = fake_uvx()

    check("found by its USB id, not other serial ports", room_box.find(), [box.port])
    ports, info = await room_box.probe()
    check("asked what it runs", (ports, info.firmware if info else None), ([box.port], "Home Assistant Voice PE"))

    try:
        await room_box.firmware("0.0.9")
        bad = "no error"
    except room_box.BoxError as e:
        bad = str(e)
    check("a firmware file that does not match", (bad, args.exists(), any((tmp / "cache").rglob("*.bin"))),
          ("The firmware for 0.0.9 does not match its SHA256SUMS, so nothing was flashed.", False, False))
    try:
        await room_box.firmware("0.0.1")
        missing = "no error"
    except room_box.BoxError as e:
        missing = str(e)
    check("a version with no release", missing,
          "No firmware for 0.0.1 in its release. In a clone, build it as docs/voice-pe.md says.")

    # Not yet in the serial group: said in words before anything is downloaded, and nothing run.
    os.chmod(box.port, 0)
    try:
        room_box.start_flash(box.port)
        denied = "no error"
    except room_box.BoxError as e:
        denied = str(e)
    os.chmod(box.port, 0o600)
    check("a port this user may not open", (denied, args.exists(), any((tmp / "cache").rglob("*.bin"))),
          (f"The user he runs as may not open {box.port}. Run `winston setup` again, then restart his computer.",
           False, False))

    seen, f, probed = await flashed(box.port)
    check("progress, then done", (40 in seen, f.stage, f.percent, f.error), (True, "done", 100, ""))
    check("the port left to esptool while it writes", probed, {0})
    check("esptool as the release pins it", args.read_text().split("\n")[:9],
          ["esptool@5.6.0", "--chip", "esp32s3", "--port", box.port, "write-flash", "--erase-all", "0x0",
           str(tmp / "cache/winston/firmware" / f"voice-pe-winston-{VERSION}.factory.bin")])
    check("esptool's output unbuffered, for its progress through the pipe",
          Path(f"{args}-unbuffered").read_text(), "1")
    check("one flash at a time", isinstance(room_box.start_flash(box.port), room_box.Flash), True)
    try:
        room_box.start_flash(box.port)
        twice = "no error"
    except room_box.BoxError as e:
        twice = str(e)
    check("a second flash while one runs", twice, "The box is being flashed already.")
    while room_box.FLASH.stage not in ("done", "error"):
        await asyncio.sleep(0.05)

    api_port = free_port()
    api = await ApiBox().start(api_port)
    name = "VOICE_PE_KEY_000001"
    check("reached, by its MAC, and given a key of Winston's", await room_box.reach("127.0.0.1", "Home", api_port),
          (MAC, name))
    key = os.environ.get(name)
    check("the key: 32 random bytes, the box's, and kept in data/.env",
          (len(base64.b64decode(key or "")), api.keys_set, dotenv_values(data / ".env").get(name) == key),
          (32, [key], True))
    check("reached again with it, and the key kept", (await room_box.reach("127.0.0.1", "Home", api_port), api.keys_set,
                                                      os.environ[name]), ((MAC, name), [key], key))
    try:
        await room_box.reach("127.0.0.2", "Home")
        far = "no error"
    except room_box.BoxError as e:
        far = str(e)
    check("not reachable", far, "The box joined Home at 127.0.0.2, but his computer cannot reach it there (port 6053).")
    api.close()

    async def why(box_api: ApiBox) -> str:
        await box_api.start(api_port)
        try:
            await room_box.reach("127.0.0.1", "Home", api_port)
            return "no error"
        except room_box.BoxError as e:
            return str(e)
        finally:
            box_api.close()
    unknown = "The box has a key Winston does not know. Flash it again, which erases it."
    check("a box with a key that is not Winston's, said as that", await why(ApiBox(key=base64.b64encode(bytes(range(32))).decode())),
          unknown)
    os.environ.pop(name)
    check("a box with Winston's key once it is gone from data/.env", await why(ApiBox(key=key)), unknown)
    check("a box that refuses the key", await why(ApiBox(takes=False)), "The box did not take Winston's key. Flash it again.")
    old = ApiBox(can_key=False)
    await old.start(api_port)
    check("a firmware without encryption: no key", await room_box.reach("127.0.0.1", "Home", api_port), (MAC, None))
    old.close()

    ha_port = free_port()
    winstond_data(data, {"llm": {"model": "fake", "base_url": "http://127.0.0.1:9/v1"},
                         "providers": {"home_assistant": {"enabled": True, "url": f"http://127.0.0.1:{ha_port}"}}})
    os.environ["HA_TOKEN"] = TOKEN
    cfg = load()
    async with FakeHA(ha_port) as ha:
        ha.devices.append({"id": "dev_box", "name": "Home Assistant Voice 0a1b2c", "name_by_user": None,
                           "area_id": None, "connections": [["mac", MAC]], "config_entries": ["entry_box"]})
        ha.config_entries += [{"entry_id": "entry_box", "domain": "esphome", "title": "Home Assistant Voice 0a1b2c",
                               "disabled_by": None},
                              {"entry_id": "entry_glimt", "domain": "fake", "title": "Glimt", "disabled_by": None}]
        check("Home Assistant's entry for it", await room_box.ha_entry(cfg, MAC.upper()),
              {"entry_id": "entry_box", "title": "Home Assistant Voice 0a1b2c", "disabled": False})
        check("no entry for another box", await room_box.ha_entry(cfg, "00:00:00:00:00:99"), None)
        await room_box.disable(cfg, "entry_box")
        check("disabled there", (ha.writes[-1]["type"], ha.writes[-1]["disabled_by"]), ("config_entries/disable", "user"))
        check("and said so", (await room_box.ha_entry(cfg, MAC))["disabled"], True)
        try:
            await room_box.disable(cfg, "entry_gone")
            refused = "no error"
        except room_box.BoxError as e:
            refused = str(e)
        check("an entry Home Assistant would not disable, in words", refused,
              "Home Assistant did not disable its old entry for the box (config_entries/disable: Config entry not "
              "found). Disable it there by hand, under ESPHome.")

        # Sharing the box: Home Assistant gets Winston's key, and Winston keeps the microphone.
        room_box.SHARE_SECS = 5
        mine, before = base64.b64encode(bytes(31) + b"\x01").decode(), base64.b64encode(bytes(range(32))).decode()
        os.environ[name] = mine
        box_off = {"assist_satellite.voice_box": "user", "update.voice_box_firmware": "user", "media_player.voice_box": None}

        async def shared() -> str:
            try:
                await room_box.share(cfg, MAC.upper(), "127.0.0.9")
                return "shared"
            except room_box.BoxError as e:
                return str(e)

        def box_entry() -> dict:
            return next(e for e in ha.config_entries if e["entry_id"] == "entry_box")
        ha.config_entries = [e for e in ha.config_entries if e["entry_id"] != "entry_box"]
        ha.devices = [d for d in ha.devices if d["id"] != "dev_box"]
        ha.add_box(MAC, "127.0.0.9", mine, entry_psk=before)           # the key it had before the flash
        stale = next(iter(ha.flows))
        ha.writes.clear()
        check("the old entry given Winston's key through Home Assistant's prompt", (await shared(), box_entry()["psk"]),
              ("shared", mine))
        check("the prompt left from before deleted first", ha.writes[0], {"type": "config_entries/flow_delete", "flow_id": stale})
        check("its voice assistant and firmware update off there, its speaker kept",
              {e: ha.registry[e]["disabled_by"] for e in FakeHA.BOX_ENTITIES}, box_off)
        check("and reloaded, so it lets the microphone go now", ha.writes[-1],
              {"type": "config_entries/reload", "entry_id": "entry_box"})
        ha.writes.clear()
        check("again: Home Assistant has the key, so nothing is asked", (await shared(), ha.flows,
              [w["type"] for w in ha.writes]), ("shared", {}, ["config_entries/reload"]))

        ha.add_box(MAC, "127.0.0.9", mine, entry_psk=before, disabled=True)
        check("a disabled entry enabled and given the key", (await shared(), box_entry()["psk"], box_entry()["disabled_by"]),
              ("shared", mine, None))

        ha.config_entries = [e for e in ha.config_entries if e["entry_id"] != "entry_box"]
        ha.devices = [d for d in ha.devices if d["id"] != "dev_box"]
        for eid in FakeHA.BOX_ENTITIES:
            ha.registry.pop(eid)
        ha.flows = {"flow_found": {"flow_id": "flow_found", "handler": "esphome", "step_id": "discovery_confirm",
                                   "context": {"source": "zeroconf", "unique_id": MAC.lower()}}}
        ha.writes.clear()
        check("a box Home Assistant never had: added with the key", (await shared(), box_entry()["psk"]), ("shared", mine))
        check("its discovery of the box deleted first, then added", [w["type"] for w in ha.writes][:2],
              ["config_entries/flow_delete", "config_entries/esphome"])
        check("and its voice assistant and update off", {e: ha.registry[e]["disabled_by"] for e in FakeHA.BOX_ENTITIES},
              box_off)

        ha.add_box(MAC, "127.0.0.9", before, entry_psk=base64.b64encode(bytes(32)).decode())
        check("a box with a key that is not Winston's: Home Assistant refuses his", await shared(),
              "Home Assistant refused the box's key ({'base': 'invalid_psk'}).")
        ha.add_box(MAC, "127.0.0.9", mine, entry_psk=before)
        ha.box["reachable"] = False
        ha.flows.clear()
        check("Home Assistant cannot reach the box", await shared(),
              "Home Assistant did not ask for the box's key within 5 s. Check that it reaches the box (port 6053).")
        os.environ.pop(name)
        check("no key for the box", await shared(), "Winston has no key for this box. Connect it to Wi-Fi again.")
    box.close()


async def api():
    """The same through a throwaway winstond, as the wizard's step uses it."""
    global box
    box = FakeBox([Net("Home", -50), Net("Cafe", -70, secured=False)], PW, "127.0.0.3")
    os.environ["WINSTON_SYS_TTY"] = str(sysfs(box.port, "sys-api"))
    api_box = await ApiBox().start(6053, "127.0.0.3")
    port = free_port()
    winstond_data(data, {"llm": {"model": "fake", "base_url": "http://127.0.0.1:9/v1"},
                         "channels": {"voice": {"enabled": False}, "satellite": {"enabled": False},
                                      "discord": {"enabled": False}},
                         "providers": {"home_assistant": {"enabled": False, "url": "http://127.0.0.1:9"}},
                         "control": {"host": "127.0.0.1", "port": port}})
    (data / "logs").mkdir(exist_ok=True)
    log = data / "logs" / "up.log"
    proc = subprocess.Popen([str(Path(sys.executable).with_name("winston")), "up", "--no-start"],  # noqa: ASYNC220
                            stdout=log.open("w"), stderr=subprocess.STDOUT)
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=60) as c:
            for _ in range(80):
                try:
                    if (await c.get("/api/health")).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.25)
            check("the step comes after Who he is", (await c.get("/api/setup")).json()["order"],
                  ["brain", "home_assistant", "who", "box"])
            state = (await c.get("/api/setup/box")).json()
            check("a box found and asked", (state["ports"], state["info"], state["flash"]),
                  ([box.port], {"firmware": "Home Assistant Voice PE", "version": "25.10.0", "winston": False}, None))
            check("a port not found is refused", (await c.post("/api/setup/box/flash", json={"port": "/dev/ttyS0"})).status_code, 404)
            r = await c.post("/api/setup/box/flash", json={"port": box.port})
            check("flashing", (r.status_code, r.json()["stage"]), (200, "downloading"))
            while (f := (await c.get("/api/setup/box")).json()["flash"])["stage"] not in ("done", "error"):
                await asyncio.sleep(0.1)
            check("flashed through the API", (f["stage"], f["percent"], f["error"]), ("done", 100, ""))
            box.scan_from = time.monotonic() + 2          # just flashed: its first scan not done yet
            nets = (await c.post("/api/setup/box/networks", json={"port": box.port})).json()
            check("the networks it sees", nets, [{"ssid": "Home", "rssi": -50, "secured": True},
                                                 {"ssid": "Cafe", "rssi": -70, "secured": False}])
            check("asked again until its first scan is done", time.monotonic() >= box.scan_from, True)
            r = await c.post("/api/setup/box/wifi", json={"port": box.port, "ssid": "Home", "password": "wrong one"})
            check("a wrong password, in words", (r.status_code, r.json()["detail"]),
                  (422, "The box could not join Home. Check the password."))
            r = await c.post("/api/setup/box/wifi", json={"port": box.port, "password": PW})
            check("a refused body does not echo the password", PW in r.text, False)
            r = await c.post("/api/setup/box/wifi", json={"port": box.port, "ssid": "Hidden", "password": PW})
            check("a network the scan did not show", (r.status_code, r.json()),
                  (200, {"host": "127.0.0.3", "mac": MAC.upper(), "key_env": "VOICE_PE_KEY_000001", "ha": False,
                         "ha_entry": None}))
            api_box.close()
            box.address = "127.0.0.4"
            r = await c.post("/api/setup/box/wifi", json={"port": box.port, "ssid": "Home", "password": PW})
            check("a box this computer cannot reach", (r.status_code, r.json()["detail"]),
                  (409, "The box joined Home at 127.0.0.4, but his computer cannot reach it there (port 6053)."))
            check("nothing saved", (await c.get("/api/config")).json()["channels"]["satellite"]["devices"], {})
            r = await c.post("/api/setup/box/ha-share", json={"mac": "not a mac", "host": "127.0.0.3"})
            check("a share for no MAC refused", r.status_code, 422)
            r = await c.post("/api/setup/box/ha-share", json={"mac": MAC, "host": "127.0.0.3"})
            check("a share without Home Assistant, in words", (r.status_code, r.json()["detail"].startswith(
                "Home Assistant did not take the box's key")), (502, True))
            r = await c.post("/api/setup/box/ha-disable", json={"entry_id": "entry_box"})
            check("Home Assistant not there to disable it, in words",
                  (r.status_code, r.json()["detail"].startswith("Home Assistant did not disable its old entry")),
                  (502, True))
    finally:
        proc.terminate()
        proc.wait(10)
        box.close()
    logs = [f.read_text(errors="replace") for f in (data / "logs").rglob("*") if f.is_file()]
    check("the password in no log", any(PW in t for t in logs), False)
    check("the log names the network and the address", any("joined Hidden at 127.0.0.3" in t for t in logs), True)


box: FakeBox
asyncio.run(main())
asyncio.run(api())
finish_checks("room box: all passed")
sys.exit(0)
