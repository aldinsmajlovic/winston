"""Satellite cases: one conversation build at a time, a wake's build surviving a run cut short, and when the
prewarm builds a box's next conversation. Conversation, stale() and the prompt key are stand-ins: no box,
no LLM, no Home Assistant.

    uv run python tests/satellite_cases.py
"""
import asyncio
from types import SimpleNamespace

from harness import check, finish_checks

from winston.channels import satellite as sat
from winston.config.schema import WinstonConfig

KEY = ["prompt 1"]          # what a conversation built now gets (Conversation.key)
STALE: set = set()          # conversations stale() calls stale


class FakeConversation:
    built, delay = 0, 0.02

    def __init__(self, *args):
        FakeConversation.built += 1
        self.used, self.closed, self.warmed, self._key = False, False, 0, KEY[0]
        self.brain = SimpleNamespace(warm=self._warm)

    async def start(self):
        await asyncio.sleep(0.02)
        return self

    async def _warm(self):
        await asyncio.sleep(FakeConversation.delay)
        self.warmed += 1

    def key(self):
        return self._key

    async def close(self):
        self.closed = True


async def fake_stale(c, idle):
    return c in STALE


sat.Conversation, sat.stale, sat.PREWARM_SECS = FakeConversation, fake_stale, 0.01


def box() -> sat.Satellite:
    s = sat.Satellite.__new__(sat.Satellite)
    s.cfg, s.room, s.feed, s._last_turn = WinstonConfig.model_validate({"llm": {"model": "x"}}), "test", None, None
    s.conversation = s._building = s._warmed = s.task = None
    s._uses = 0
    s._lock = asyncio.Lock()

    async def next_key():
        return KEY[0]
    s._next_key = next_key
    return s


async def main():
    # An announcement and a wake at once: one build, both get it.
    s = box()
    a, b = await asyncio.gather(s._conversation(), s._conversation())
    check("one build for two callers", FakeConversation.built, 1)
    check("both get the same conversation", a is b is s.conversation, True)
    check("handed out means used", a.used, True)
    check("an announcement's build is not warmed", (a.warmed, s._warmed), (0, None))

    # A wake's build goes on when its run is cut short, and the next request uses it.
    s, FakeConversation.built = box(), 0
    building = s._build(warm=True)
    run = asyncio.create_task(s._conversation(building))
    await asyncio.sleep(0.005)
    run.cancel()
    await asyncio.gather(run, return_exceptions=True)
    c = await building
    check("cut short: the build finished", (s.conversation is c, c.warmed, s._warmed), (True, 1, "prompt 1"))
    check("cut short: not counted as used", c.used, False)
    check("next request reuses it", (await s._conversation()) is c and FakeConversation.built == 1, True)

    # A stale one is closed and replaced at the wake.
    STALE.add(c)
    new = await s._conversation(s._build(warm=True))
    check("stale: closed and rebuilt warm", (c.closed, new is not c, new.warmed), (True, True, 1))

    # The prewarm: never over a conversation in use, only on a changed prompt, and it replaces an idle one.
    loop = asyncio.create_task(s._prewarm())
    KEY[0] = "prompt 2"                                 # a remembered fact, say
    await asyncio.sleep(0.1)
    check("in use and not stale: left alone", (s.conversation is new, FakeConversation.built), (True, 2))
    STALE.add(new)                                      # idled past session_idle_mins
    await asyncio.sleep(0.15)
    pre = s.conversation
    check("stale and changed: replaced warm", (pre is not new, new.closed, pre.warmed, s._warmed),
          (True, True, 1, "prompt 2"))
    await asyncio.sleep(0.1)
    check("unchanged: no more builds, though idle", (s.conversation is pre, FakeConversation.built), (True, 3))
    async with s._lock:                                 # a request under way
        KEY[0] = "prompt 3"
        await asyncio.sleep(0.1)
        check("busy: no prewarm", FakeConversation.built, 3)
    await asyncio.sleep(0.15)
    check("unused and changed: replaced", (s.conversation is not pre, pre.closed, s._warmed), (True, True, "prompt 3"))

    # A wake while a prewarm warms keeps the conversation it has, at once; the prewarm then drops its own.
    cur, FakeConversation.delay, KEY[0] = s.conversation, 0.3, "prompt 4"
    await asyncio.sleep(0.1)
    got = await asyncio.wait_for(s._conversation(s._build(warm=True)), 0.1)
    check("wake during a prewarm: no wait, same conversation", got is cur, True)
    await asyncio.sleep(0.4)
    check("prewarm dropped its build after the use", (s.conversation is cur, cur.closed), (True, False))
    loop.cancel()
    await asyncio.gather(loop, return_exceptions=True)
    FakeConversation.delay = 0.02

    # A wake's build that fails (Home Assistant restarting) is tried once more when the request needs it.
    s, start = box(), FakeConversation.start
    failing = [True]

    async def flaky(self):
        if failing.pop() if failing else False:
            raise RuntimeError("HA MCP down")
        return await start(self)
    FakeConversation.start = flaky
    c = await s._conversation(s._build(warm=True))
    FakeConversation.start = start
    check("failed build: tried once more", (c.used, s.conversation is c), (True, True))


asyncio.run(main())
finish_checks("satellite: one build at a time, a cut-short wake's build kept, prewarm only on a changed prompt "
              "and never in a wake's way, a failed build tried again ok")
