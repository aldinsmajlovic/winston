"""The lockdown state (core/lockdown) and secrets.this_computer. No hardware needed.

    uv run python tests/lockdown_cases.py
"""
import time
from types import SimpleNamespace

import harness
from harness import expect, scratch_data

DATA = scratch_data("winston-lockdown-")

from winston.api.secrets import this_computer  # noqa: E402  (after WINSTON_DATA is set)
from winston.core import lockdown as L  # noqa: E402

expect("off by default", L.state() is None and not L.on())
expect("on", L.turn_on("voice") and L.on() and L.state()["by"] == "voice")
expect("on twice is a no-op", L.turn_on("cli") is False and L.state()["by"] == "voice")
(DATA / "lockdown.json").write_text("{not json")
expect("an unreadable file counts as on", L.on())
expect("off", L.turn_off("his computer") and not L.on() and L.turn_off("x") is False)
expect("guarded places", [L.guarded(p, False) for p in ("voice", "satellite", "assist", "web", "discord", "text")]
       == [True, True, True, False, False, False] and L.guarded("discord", True))
w = L.windows(0, time.time() + 1)
expect("one closed window from the log", len(w) == 1 and w[0][0] < w[0][1], w)
L.turn_on("voice")
expect("an open window ends at end", L.windows(0, 5e9)[-1][1] == 5e9)
L.turn_off("cli")


def req(client, host):
    return SimpleNamespace(client=SimpleNamespace(host=client), headers={"host": host})


expect("this computer", this_computer(req("127.0.0.1", "127.0.0.1:8700")))
expect("a reverse proxy is not", not this_computer(req("127.0.0.1", "winston.lan")))
expect("another machine is not", not this_computer(req("192.168.1.20", "localhost")))

print(f"\n{'all passed' if not harness.failed else f'{harness.failed} FAILED'} (scratch data in {DATA})")
raise SystemExit(1 if harness.failed else 0)
