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
import functools
import subprocess
import hashlib
import http.server
import os
import sys
import tempfile
import threading
from pathlib import Path

import httpx
from fake_box import FakeBox, Net
from harness import check, finish_checks, free_port, scratch_data, winstond_data

data = scratch_data("winston-room-box-")
tmp = Path(tempfile.mkdtemp(prefix="winston-room-box-tree-"))
os.environ["XDG_CACHE_HOME"] = str(tmp / "cache")

from aioesphomeapi import api_pb2 as pb  # noqa: E402
from aioesphomeapi.core import MESSAGE_TYPE_TO_PROTO  # noqa: E402

from fake_ha import TOKEN, FakeHA  # noqa: E402
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


async def esphome_api(port: int, host: str = "127.0.0.1", key: bool = False):
    """The box's ESPHome API, plaintext as a box without a key speaks it: the handshake and its device info."""
    async def serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        def send(msg):
            payload = msg.SerializeToString()
            writer.write(b"\x00" + varint(len(payload)) + varint(TYPE_OF[type(msg)]) + payload)

        async def read_varint() -> int:
            n = shift = 0
            while True:
                b = (await reader.readexactly(1))[0]
                n |= (b & 0x7F) << shift
                shift += 7
                if not b & 0x80:
                    return n
        if key:                     # a box with Home Assistant's key: answers in Noise frames only
            await reader.read(1)
            writer.write(b"\x01\x00\x00")
            return
        try:
            while True:
                await reader.readexactly(1)
                size, kind = await read_varint(), await read_varint()
                msg = MESSAGE_TYPE_TO_PROTO[kind]()
                msg.ParseFromString(await reader.readexactly(size))
                if isinstance(msg, pb.HelloRequest):
                    send(pb.HelloResponse(api_version_major=1, api_version_minor=12, name="home-assistant-voice-0a1b2c"))
                elif isinstance(msg, pb.DeviceInfoRequest):
                    send(pb.DeviceInfoResponse(name="home-assistant-voice-0a1b2c", mac_address=MAC.upper()))
                elif isinstance(msg, pb.DisconnectRequest):
                    send(pb.DisconnectResponse())
                elif isinstance(msg, pb.PingRequest):
                    send(pb.PingResponse())
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
    return await asyncio.start_server(serve, host, port)


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
          (f"This computer's user may not open {box.port}. Run `winston setup` again, then restart this computer.",
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
    server = await esphome_api(api_port)
    check("reached, by its MAC", await room_box.reach("127.0.0.1", "Home", api_port), MAC)
    try:
        await room_box.reach("127.0.0.2", "Home")
        far = "no error"
    except room_box.BoxError as e:
        far = str(e)
    check("not reachable", far, "The box joined Home at 127.0.0.2, but this computer cannot reach it there (port 6053).")
    server.close()
    keyed = await esphome_api(api_port, key=True)
    try:
        await room_box.reach("127.0.0.1", "Home", api_port)
        why_key = "no error"
    except room_box.BoxError as e:
        why_key = str(e)
    check("a box with Home Assistant's key, said as that", why_key,
          "The box has a key from Home Assistant, so Winston cannot talk to it. Flash it again, which erases the key.")
    keyed.close()

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
    box.close()


async def api():
    """The same through a throwaway winstond, as the wizard's step uses it."""
    global box
    box = FakeBox([Net("Home", -50), Net("Cafe", -70, secured=False)], PW, "127.0.0.3")
    os.environ["WINSTON_SYS_TTY"] = str(sysfs(box.port, "sys-api"))
    server = await esphome_api(6053, "127.0.0.3")
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
            nets = (await c.post("/api/setup/box/networks", json={"port": box.port})).json()
            check("the networks it sees", nets, [{"ssid": "Home", "rssi": -50, "secured": True},
                                                 {"ssid": "Cafe", "rssi": -70, "secured": False}])
            r = await c.post("/api/setup/box/wifi", json={"port": box.port, "ssid": "Home", "password": "wrong one"})
            check("a wrong password, in words", (r.status_code, r.json()["detail"]),
                  (422, "The box could not join Home. Check the password."))
            r = await c.post("/api/setup/box/wifi", json={"port": box.port, "password": PW})
            check("a refused body does not echo the password", PW in r.text, False)
            r = await c.post("/api/setup/box/wifi", json={"port": box.port, "ssid": "Hidden", "password": PW})
            check("a network the scan did not show", (r.status_code, r.json()),
                  (200, {"host": "127.0.0.3", "mac": MAC.upper(), "ha_entry": None}))
            server.close()
            box.address = "127.0.0.4"
            r = await c.post("/api/setup/box/wifi", json={"port": box.port, "ssid": "Home", "password": PW})
            check("a box this computer cannot reach", (r.status_code, r.json()["detail"]),
                  (409, "The box joined Home at 127.0.0.4, but this computer cannot reach it there (port 6053)."))
            check("nothing saved", (await c.get("/api/config")).json()["channels"]["satellite"]["devices"], {})
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
