"""Takes of the lines he says while he thinks (voice.prerender): played in turn from memory, never through the
speech server; other text, and the same line in another voice, go to the server as before; a hosted voice is
asked with tts.model and its key. No hardware.

    uv run python tests/takes_cases.py
"""
import asyncio
import json
import os
import shutil

import httpx

from harness import REPO, check, finish_checks, scratch_data

# the example config, not the instance's: its voice may be a hosted one whose key is elsewhere
shutil.copy(REPO / "examples" / "config.yaml", scratch_data("winston-takes-") / "config.yaml")

from winston.channels import voice as vp  # noqa: E402
from winston.core.brain import HOME_LINES  # noqa: E402


class Asked(Exception):
    pass


async def main():
    tts = vp.build_tts()
    tts._sample_rate = vp.CFG.tts.sample_rate          # a pipeline's StartFrame sets it

    def ask_server(**kwargs):
        raise Asked(kwargs["voice"])
    tts._client.audio.speech.with_streaming_response.create = ask_server

    async def frames(text: str) -> list:
        return [f async for f in tts.run_tts(text, "c")]

    line, voice = HOME_LINES[0], tts._settings.voice
    vp._takes[(voice, line)] = [b"\1\0" * 12000, b"\2\0" * 12000]
    a, b, c = await frames(line), await frames(f" {line}"), await frames(line)
    check("takes in turn", (a[0].audio[:2], b[0].audio[:2], c[0].audio[:2]), (b"\1\0", b"\2\0", b"\1\0"))
    check("the whole take, in even pieces of the chunk size",
          (sum(len(f.audio) for f in a), all(len(f.audio) % 2 == 0 and len(f.audio) <= tts.chunk_size for f in a)),
          (24000, True))
    for text, why in ((HOME_LINES[1], "a line with no takes"), ("Done, sir.", "other text")):
        try:
            await frames(text)
            check(f"{why} goes to the server", "served from takes", "asked the server")
        except Asked:
            check(f"{why} goes to the server", "asked the server", "asked the server")
    tts._settings.voice = "friends/erik"
    try:
        await frames(line)
        check("the line in another voice goes to the server", "served from takes", "asked the server")
    except Asked as e:
        check("the line in another voice goes to the server", str(e), "friends/erik")

    # a hosted voice: tts.model and the key reach the server, live and when lines are read ahead
    cfg = vp.CFG.model_copy(deep=True)
    cfg.tts.model, cfg.tts.voice, cfg.tts.api_key_env = "elevenlabs/eleven-flash-v2.5", "george", "TAKES_TEST_KEY"
    os.environ["TAKES_TEST_KEY"] = "k"
    hosted = vp.build_tts(cfg)
    hosted._sample_rate = cfg.tts.sample_rate
    hosted._client.audio.speech.with_streaming_response.create = lambda **kw: (_ for _ in ()).throw(Asked(kw["model"]))
    try:
        [f async for f in hosted.run_tts("Done, sir.", "c")]
        check("a hosted voice is asked with tts.model", "server not asked", "elevenlabs/eleven-flash-v2.5")
    except Asked as e:
        check("a hosted voice is asked with tts.model", str(e), "elevenlabs/eleven-flash-v2.5")
    seen = []

    def serve(req: httpx.Request) -> httpx.Response:
        seen.append((req.headers["authorization"], json.loads(req.content)["model"]))
        return httpx.Response(200, content=b"\0\0" * 240)
    real = httpx.AsyncClient
    vp.httpx.AsyncClient = lambda **kw: real(transport=httpx.MockTransport(serve), **kw)
    try:
        await vp.prerender(cfg)
    finally:
        vp.httpx.AsyncClient = real
    check("lines read ahead send the key and tts.model", seen[0], ("Bearer k", "elevenlabs/eleven-flash-v2.5"))


asyncio.run(main())
finish_checks("takes: thinking lines played in turn from memory, the rest from the speech server ok")
