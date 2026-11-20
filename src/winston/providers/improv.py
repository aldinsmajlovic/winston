"""Improv Serial (https://www.improv-wifi.com/serial/), how a Voice PE on USB-C is given Wi-Fi (docs/setup.md,
A room box). Packets are "IMPROV", version 1, a kind, a length, the data and a checksum (the low byte of the
sum of every byte before it); ESPHome writes its log on the same port, so lines of text come between them.

The box's address: the factory firmware has no web server, so its answer to the Wi-Fi settings carries no
URL. It is taken from a URL when one comes, else from the box's own log line ("IP Address: 192.168.1.50").

The port is opened with the standard library (termios), as raw bytes at 115200 baud; the USB serial of the
ESP32-S3 ignores the rate."""
import asyncio
import os
import re
import termios
import tty
from dataclasses import dataclass

HEADER = b"IMPROV"
STATE, ERROR, RPC, RESULT = 1, 2, 3, 4                          # packet kinds
READY, PROVISIONING, PROVISIONED = 2, 3, 4                      # states
UNABLE_TO_CONNECT = 3                                           # errors
WIFI, CURRENT_STATE, DEVICE_INFO, SCAN = 1, 2, 3, 4             # RPC commands
ANSWER_SECS = 5             # for an answer to a request
ADDRESS_SECS = 15           # for the address line once the box says it joined
IPV4 = re.compile(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b")
ADDRESS_LINE = re.compile(r"IP Address(?:es)?:\s*(\d{1,3}(?:\.\d{1,3}){3})")
# A restart, not a new login: the service runs under systemd's user manager, which lingers past a logout
# with the groups it started with.
DENIED = "This computer's user may not open {port}. Run `winston setup` again, then restart this computer."


@dataclass
class Info:
    firmware: str
    version: str
    chip: str
    name: str


@dataclass
class Network:
    ssid: str
    rssi: int
    secured: bool


class ImprovError(Exception):
    """`why` is denied, no_answer, wrong_password or no_address; the message is the sentence a page shows."""

    def __init__(self, why: str, message: str):
        super().__init__(message)
        self.why = why


def packet(kind: int, data: bytes) -> bytes:
    body = HEADER + bytes([1, kind, len(data)]) + data
    return body + bytes([sum(body) & 0xFF])


def _strings(data: bytes) -> list[str]:
    out, i = [], 0
    while i < len(data):
        n = data[i]
        out.append(data[i + 1:i + 1 + n].decode(errors="replace"))
        i += 1 + n
    return out


def _encode(*values: str) -> bytes:
    return b"".join(bytes([len(v.encode())]) + v.encode() for v in values)


class Stream:
    """The port's bytes as they come: complete packets as (kind, data) and complete lines of text, in order.
    A packet with a wrong checksum is dropped; empty lines are skipped."""

    def __init__(self):
        self.buf = b""

    def feed(self, chunk: bytes) -> list[tuple[int, bytes] | str]:
        self.buf += chunk
        out: list[tuple[int, bytes] | str] = []
        while True:
            i = self.buf.find(HEADER)
            text_end = i if i >= 0 else self.buf.rfind(b"\n") + 1
            if text_end:
                out += [ln for ln in self.buf[:text_end].decode(errors="replace").replace("\r", "").split("\n") if ln]
                self.buf = self.buf[text_end:]
            if i < 0 or len(self.buf) < 9 or len(self.buf) < 10 + self.buf[8]:
                return out
            n = self.buf[8]
            body, check = self.buf[:9 + n], self.buf[9 + n]
            self.buf = self.buf[10 + n:]
            if sum(body) & 0xFF == check:
                out.append((body[7], body[9:]))


class Box:
    """A box on a serial port: `async with Box("/dev/ttyACM0") as box`."""

    def __init__(self, port: str):
        self.port = port
        self.fd = -1
        self.items: asyncio.Queue = asyncio.Queue()
        self.stream = Stream()

    async def __aenter__(self):
        try:
            self.fd = os.open(self.port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        except PermissionError:
            raise ImprovError("denied", DENIED.format(port=self.port)) from None
        except OSError as e:
            raise ImprovError("no_answer", f"Could not open {self.port}: {e.strerror}.") from None
        try:
            tty.setraw(self.fd)
            attrs = termios.tcgetattr(self.fd)
            attrs[4] = attrs[5] = termios.B115200
            termios.tcsetattr(self.fd, termios.TCSANOW, attrs)
        except termios.error:
            os.close(self.fd)
            raise ImprovError("no_answer", f"{self.port} is not a serial port.") from None
        asyncio.get_running_loop().add_reader(self.fd, self._readable)
        return self

    async def __aexit__(self, *exc):
        asyncio.get_running_loop().remove_reader(self.fd)
        os.close(self.fd)

    def _gone(self) -> ImprovError:
        return ImprovError("no_answer", f"The box on {self.port} did not answer. Unplug it and plug it in again.")

    def _readable(self):
        try:
            chunk = os.read(self.fd, 4096)
        except OSError:
            chunk = b""
        if not chunk:                       # unplugged: stop watching, or the loop spins on the hung-up port
            asyncio.get_running_loop().remove_reader(self.fd)
            self.items.put_nowait(None)
            return
        for item in self.stream.feed(chunk):
            self.items.put_nowait(item)

    def _send(self, command: int, payload: bytes = b""):
        try:
            os.write(self.fd, packet(RPC, bytes([command, len(payload)]) + payload))
        except OSError:
            raise self._gone() from None

    async def _next(self, until: float):
        left = until - asyncio.get_running_loop().time()
        try:
            item = await asyncio.wait_for(self.items.get(), max(left, 0))
        except TimeoutError:
            raise self._gone() from None
        if item is None:
            self.items.put_nowait(None)     # and for every later ask
            raise self._gone()
        return item

    def _deadline(self, secs: float) -> float:
        return asyncio.get_running_loop().time() + secs

    async def _result(self, command: int, until: float) -> bytes:
        """The data of the next answer to `command`, its strings after the length byte."""
        while True:
            item = await self._next(until)
            if isinstance(item, tuple) and item[0] == RESULT and item[1][:1] == bytes([command]):
                return item[1][2:2 + item[1][1]]
            if isinstance(item, tuple) and item[0] == ERROR and item[1][:1] != b"\x00":
                raise ImprovError("no_answer", f"The box on {self.port} answered with Improv error {item[1][0]}.")

    async def info(self) -> Info:
        self._send(DEVICE_INFO)
        return Info(*(_strings(await self._result(DEVICE_INFO, self._deadline(ANSWER_SECS))) + ["", "", "", ""])[:4])

    async def scan(self) -> list[Network]:
        """The networks the box sees, strongest first, each name once; hidden ones (no name) left out."""
        self._send(SCAN)
        until, best = self._deadline(ANSWER_SECS * 4), {}
        while data := await self._result(SCAN, until):
            ssid, rssi, auth = (_strings(data) + ["", "0", "YES"])[:3]
            n = Network(ssid, int(rssi or 0), auth.upper() != "NO")
            if ssid and (ssid not in best or n.rssi > best[ssid].rssi):
                best[ssid] = n
        return sorted(best.values(), key=lambda n: -n.rssi)

    async def join(self, ssid: str, password: str, timeout: float = 40) -> str:
        """Gives the box this network and returns its IPv4 address once it has joined. The password goes to
        the box only."""
        self._send(WIFI, _encode(ssid, password))
        until, joined, address = self._deadline(timeout), False, None
        while not (joined and address):
            try:
                item = await self._next(until)
            except ImprovError:
                if joined:
                    raise ImprovError("no_address", f"The box joined {ssid}, but did not say its address. "
                                                    "Unplug it and plug it in again.") from None
                raise
            if isinstance(item, str):
                if m := ADDRESS_LINE.search(item):
                    address = m.group(1)
            elif item[0] == STATE and item[1][:1] == bytes([PROVISIONED]):
                joined = True
                until = min(until, self._deadline(ADDRESS_SECS))
            elif item[0] == RESULT and item[1][:1] == bytes([WIFI]):
                address = next((m.group(1) for u in _strings(item[1][2:]) if (m := IPV4.search(u))), address)
            elif item[0] == ERROR and item[1][:1] == bytes([UNABLE_TO_CONNECT]):
                raise ImprovError("wrong_password", f"The box could not join {ssid}. Check the password.")
        return address
