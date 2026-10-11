"""Takes of the lines he says while he thinks (voice.prerender): played in turn from memory, never through the
speech server; other text, and the same line in another voice, go to the server as before. No hardware.

    uv run python tests/takes_cases.py
"""
import asyncio

from harness import check, finish_checks

from winston.channels import voice as vp
from winston.core.brain import HOME_LINES


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


asyncio.run(main())
finish_checks("takes: thinking lines played in turn from memory, the rest from the speech server ok")
