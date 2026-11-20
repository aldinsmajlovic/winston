"""A fake Voice PE on a pty, speaking Improv Serial (https://www.improv-wifi.com/serial/) the way ESPHome's
improv_serial does, with its log lines between the packets. Written from the protocol, not shared with
winston.providers.improv, so a mistake there is not repeated here. tests/improv_cases.py and
tests/room_box_cases.py use it; `.port` is the path a client opens.

On Wi-Fi settings with the right password it reports Provisioning, then Provisioned and an empty RPC result
(the factory firmware has no web server, so no URL), and only then logs its address, as the worst case.
A wrong password gets error 3 (unable to connect) after a second."""
import os
import threading
import time
from dataclasses import dataclass

HEADER = b"IMPROV"
STATE, ERROR, RPC, RESULT = 1, 2, 3, 4
READY, PROVISIONING, PROVISIONED = 2, 3, 4
WIFI, CURRENT_STATE, DEVICE_INFO, SCAN = 1, 2, 3, 4


@dataclass
class Net:
    ssid: str
    rssi: int
    secured: bool = True


def _packet(kind: int, data: bytes) -> bytes:
    body = HEADER + bytes([1, kind, len(data)]) + data
    return body + bytes([sum(body) & 0xFF]) + b"\n"


def _strings(*values: str) -> bytes:
    out = b"".join(bytes([len(v.encode())]) + v.encode() for v in values)
    return bytes([len(out)]) + out


class FakeBox:
    def __init__(self, networks: list[Net], password: str, address: str,
                 firmware: tuple[str, str] = ("Home Assistant Voice PE", "25.10.0"), answer: bool = True):
        self.networks, self.password, self.address, self.firmware = networks, password, address, firmware
        self.master, slave = os.openpty()
        self.port = os.ttyname(slave)
        os.close(slave)      # the client opens it by its path, as a real port
        self.state, self.joined = READY, None
        self.received: list[tuple[int, bytes]] = []
        self._stop = threading.Event()
        if answer:
            threading.Thread(target=self._serve, daemon=True).start()

    def reboot(self):
        """As after a flash: no Wi-Fi."""
        self.state, self.joined = READY, None

    def close(self):
        self._stop.set()
        os.close(self.master)

    def _write(self, data: bytes):
        os.write(self.master, data)

    def _log(self, line: str):
        self._write(f"\x1b[0;36m{line}\x1b[0m\r\n".encode())

    def _serve(self):
        buf = b""
        while not self._stop.is_set():
            try:
                chunk = os.read(self.master, 4096)
            except OSError:          # the client closed its side, or ours closed
                time.sleep(0.05)
                continue
            buf += chunk
            while (i := buf.find(HEADER)) >= 0 and len(buf) >= i + 9 and len(buf) >= i + 10 + buf[i + 8]:
                n = buf[i + 8]
                kind, data = buf[i + 7], buf[i + 9:i + 9 + n]
                buf = buf[i + 10 + n:]
                self.received.append((kind, data))
                if kind == RPC:
                    self._rpc(data[0], data[2:2 + data[1]])

    def _rpc(self, command: int, payload: bytes):
        if command == DEVICE_INFO:
            self._log("[D][improv_serial:100]: Received device info request")
            self._write(_packet(RESULT, bytes([DEVICE_INFO]) + _strings(*self.firmware, "ESP32-S3",
                                                                          "home-assistant-voice-0a1b2c")))
        elif command == SCAN:
            self._log("[D][wifi:123]: Scanning...")
            for n in self.networks:
                self._write(_packet(RESULT, bytes([SCAN]) + _strings(n.ssid, str(n.rssi), "YES" if n.secured else "NO")))
            self._write(_packet(RESULT, bytes([SCAN, 0])))
        elif command == CURRENT_STATE:
            self._write(_packet(STATE, bytes([self.state])))
        elif command == WIFI:
            ssid = payload[1:1 + payload[0]].decode()
            rest = payload[1 + payload[0]:]
            password = rest[1:1 + rest[0]].decode()
            self.state = PROVISIONING
            self._write(_packet(STATE, bytes([PROVISIONING])))
            if password != self.password:
                time.sleep(1)
                self.state = READY
                self._log(f"[W][wifi:300]: Connecting to '{ssid}' failed")
                self._write(_packet(ERROR, bytes([3])))
                return
            time.sleep(0.3)
            self.state, self.joined = PROVISIONED, ssid
            self._write(_packet(STATE, bytes([PROVISIONED])))
            self._write(_packet(RESULT, bytes([WIFI, 0])))
            time.sleep(0.2)
            self._log("[I][wifi:400]: WiFi Connected!")
            self._log(f"[C][wifi:456]:   IP Address: {self.address}")
