"""Improv Serial (src/winston/providers/improv.py) against a fake Voice PE on a pty (tests/fake_box.py):
packets and the log lines between them, device info, the networks it sees, joining one at the address its
log gives, a wrong password and a try after it without a reflash, a port this user may not open and one
with nothing answering.

    uv run python tests/improv_cases.py
"""
import asyncio
import os

from fake_box import FakeBox, Net
from harness import check, finish_checks

from winston.providers import improv

PW = "correct horse 22"


async def why(coro) -> str:
    try:
        await coro
    except improv.ImprovError as e:
        return e.why
    return "no error"


async def main():
    check("a packet's checksum", improv.packet(2, b"\x03\x00")[-1], sum(b"IMPROV\x01\x02\x02\x03\x00") & 0xFF)
    s = improv.Stream()
    check("log text between packets is kept as lines",
          s.feed(b"\x1b[0;36m[D] hi\x1b[0m\r\n" + improv.packet(1, b"\x02") + b"\npart"),
          ["\x1b[0;36m[D] hi\x1b[0m", (1, b"\x02")])
    check("a packet in two pieces", s.feed(b" line\n" + improv.packet(4, b"\x03\x00")[:5]) + s.feed(improv.packet(4, b"\x03\x00")[5:]),
          ["part line", (4, b"\x03\x00")])
    bad = bytearray(improv.packet(1, b"\x02")); bad[-1] ^= 1
    check("a packet with a wrong checksum is dropped", s.feed(bytes(bad) + b"after\n"), ["after"])

    box = FakeBox([Net("Neighbour", -80), Net("Home", -50), Net("Home", -70), Net("Cafe", -60, secured=False)],
                  PW, "127.0.0.1")
    async with improv.Box(box.port) as b:
        check("device info", await b.info(),
              improv.Info("Home Assistant Voice PE", "25.10.0", "ESP32-S3", "home-assistant-voice-0a1b2c"))
        nets = await b.scan()
        check("networks, strongest first, one per name", [(n.ssid, n.rssi, n.secured) for n in nets],
              [("Home", -50, True), ("Cafe", -60, False), ("Neighbour", -80, True)])
        check("a wrong password", await why(b.join("Home", "wrong")), "wrong_password")
        check("joined after a wrong password, no reflash", await b.join("Home", PW), "127.0.0.1")
    check("the box was told the network", box.joined, "Home")
    box.close()

    denied = FakeBox([], PW, "127.0.0.1")
    os.chmod(denied.port, 0)
    check("a port this user may not open", await why(improv.Box(denied.port).__aenter__()), "denied")
    try:
        await improv.Box(denied.port).__aenter__()
    except improv.ImprovError as e:
        said = str(e)
    check("and what to do, which a service lingering on needs a restart for", said,
          f"This computer's user may not open {denied.port}. Run `winston setup` again, then restart this computer.")
    denied.close()

    fds = len(os.listdir("/proc/self/fd"))
    check("a port that is not a serial port", await why(improv.Box("/dev/null").__aenter__()), "no_answer")
    check("its file closed again", len(os.listdir("/proc/self/fd")), fds)

    # Unplugged while it joins: said at once, not at the join's deadline, and the loop not left spinning.
    gone = FakeBox([], PW, "127.0.0.1", answer=False)
    async with improv.Box(gone.port) as b:
        loop = asyncio.get_running_loop()
        loop.call_later(0.3, gone.close)
        start = loop.time()
        check("unplugged during a join", await why(b.join("Home", PW, timeout=10)), "no_answer")
        check("noticed when it happened", loop.time() - start < 2, True)
        check("asked again after it, said in words", await why(b.info()), "no_answer")

    improv.ANSWER_SECS = 0.5
    silent = FakeBox([], PW, "127.0.0.1", answer=False)
    async with improv.Box(silent.port) as b:
        check("a port with nothing answering", await why(b.info()), "no_answer")
    silent.close()


asyncio.run(main())
finish_checks("improv: all passed")
