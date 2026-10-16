"""Reply filter cases: the reply on its way to the TTS, driven frame by frame as the LLM would push it.
Sentences are let through as they finish, a stray </think> is never said, nor a copied clock stamp or a
tool call written as text, and the TTS speaks a sentence as soon as the filter passes it on. No hardware
needed.

    uv run python tests/reply_cases.py
"""
import asyncio
import sys

from pipecat.frames.frames import InterruptionFrame, LLMFullResponseEndFrame, LLMFullResponseStartFrame, LLMTextFrame
from pipecat.processors.frame_processor import FrameDirection

from winston.core.reply import ReplyFilter, ReplySentences


class Rig:
    """One ReplyFilter with what it pushes recorded, and the TTS's aggregator behind it: `spoken` is
    what the TTS has been given so far, a request per sentence."""

    def __init__(self):
        self.f, self.agg = ReplyFilter(), ReplySentences()
        self.out, self.spoken = [], []

        async def record(frame, direction=FrameDirection.DOWNSTREAM):
            self.out.append(frame)
            if isinstance(frame, LLMTextFrame):
                self.spoken += [a.text async for a in self.agg.aggregate(frame.text)]
            elif isinstance(frame, LLMFullResponseEndFrame) and (a := await self.agg.flush()) and a.text.strip():
                self.spoken.append(a.text)
        async def nothing():
            pass
        self.f.push_frame = record
        self.f._start_interruption = nothing       # no task manager outside a pipeline

    async def llm(self, frame):
        await self.f.process_frame(frame, FrameDirection.DOWNSTREAM)

    async def reply(self, *pieces):
        await self.llm(LLMFullResponseStartFrame())
        for p in pieces:
            await self.llm(LLMTextFrame(p))
        await self.llm(LLMFullResponseEndFrame())

    async def tokens(self, *pieces) -> list[list[str]]:
        """One response streamed a token at a time: what has been spoken after each token, then at the end."""
        await self.llm(LLMFullResponseStartFrame())
        after = []
        for p in pieces:
            await self.llm(LLMTextFrame(p))
            after.append(list(self.spoken))
        await self.llm(LLMFullResponseEndFrame())
        return after + [self.spoken]

    def said(self) -> str:
        return "".join(f.text for f in self.out if isinstance(f, LLMTextFrame))


async def cases():
    got = {}
    r = Rig()
    await r.llm(LLMFullResponseStartFrame())
    await r.llm(LLMTextFrame("Done"))
    got["nothing before a sentence ends"] = (r.said(), "")
    await r.llm(LLMTextFrame(", the shelf lamp is off. It"))
    got["a finished sentence leaves at once"] = (r.said(), "Done, the shelf lamp is off. ")
    await r.llm(LLMTextFrame("'s 21.5 degrees too."))
    await r.llm(LLMFullResponseEndFrame())
    got["the reply passes whole"] = (r.said(), "Done, the shelf lamp is off. It's 21.5 degrees too.")

    # A stray think tag (the model writes one now and then with thinking off) is never said.
    r = Rig()
    await r.reply("Shelf lamp is off.\n</", "think>\n\nAnything else?</think>")
    got["a think tag is not said"] = (r.said(), "Shelf lamp is off.\n\n\nAnything else?")

    # An answer, a stray </think>, and the same answer again (the 27B on the box, 2026-10-08) is said once.
    r = Rig()
    await r.reply("Os", "lo", ",", " Sir", ".", "\n", "</think>", "\n\n", "Os", "lo", ",", " Sir", ".")
    got["a repeat after a stray think tag is said once"] = (r.said().strip(), "Oslo, Sir.")

    # The last sentence waits only until more follows, not for the end of the response.
    r = Rig()
    await r.llm(LLMFullResponseStartFrame())
    await r.llm(LLMTextFrame("It's ten. "))
    got["the last sentence waits for what follows"] = (r.said(), "")
    await r.llm(LLMTextFrame("Anything"))
    got["and goes once more follows"] = (r.said(), "It's ten. ")

    # The user talked over him: what waits is dropped.
    r = Rig()
    await r.llm(LLMFullResponseStartFrame())
    await r.llm(LLMTextFrame("It's ten. "))
    await r.llm(InterruptionFrame())
    await r.llm(LLMFullResponseEndFrame())
    got["an interruption drops what waits"] = (r.said(), "")

    # The TTS speaks a sentence as soon as the filter passes it on, not when the next one is written too.
    r = Rig()
    s = await r.tokens("Of", " course", ",", " Sir", ".", " It", " is", " raining", " in", " Oslo", ".")
    got["first sentence spoken at the next token"] = ((s[4], s[5], s[-2]), ([], ["Of course, Sir."], ["Of course, Sir."]))
    got["the last at the end"] = (s[-1], ["Of course, Sir.", "It is raining in Oslo."])
    r = Rig()
    s = await r.tokens("Dr", ".", " Dre", " made", " it", ",", " Sir", ".", " Lovely", ".")
    got["an abbreviation waits for its name"] = ((s[2], s[8]), ([], ["Dr. Dre made it, Sir."]))
    r = Rig()
    s = await r.tokens("About", " ", "1", "0", ".", "6", " million", ".", " Shall", " I", " go", " on", "?")
    got["a decimal stays whole"] = ((s[5], s[8], s[-1]), ([], ["About 10.6 million."],
                                                         ["About 10.6 million.", "Shall I go on?"]))
    got["the filter passes a decimal in one piece"] = ([f.text for f in r.out if isinstance(f, LLMTextFrame)],
                                                       ["About 10.6 million. ", "Shall I go on?"])
    r = Rig()
    got["no full stop: spoken at the end"] = ((await r.tokens("Very", " good", " Sir"))[-1], ["Very good Sir"])

    # Only words: the box said "[zero seven forty-one] <tool_call> <function=spotify_play> ..." (2026-10-16).
    r = Rig()
    await r.reply("[07:41]", " <tool", "_call>\n<function=spotify_play>\n<parameter=what>\nPush the Feeling On\n",
                  "</parameter>\n</function>\n</tool_call>")
    got["a copied stamp and a tool call written as text are not said"] = (r.said(), "")
    r = Rig()
    await r.reply("One moment, sir.\n<tool_call>\n<function=spotify_play>", "\n</function>\n</tool_call>\nPlaying.")
    got["the words before a tool call are, nothing after it"] = (r.said(), "One moment, sir.\n")
    r = Rig()
    await r.reply("[07:41, room: Main Room] The ", "lamp is off. [sigh] Long day?")
    got["a stamp anywhere is dropped, the voice's tags stay"] = (r.said(), "The lamp is off. [sigh] Long day?")
    r = Rig()
    await r.reply("Shall I?\n<function=x>", "")
    await r.reply("Very good.")
    got["the next response is said again"] = (r.said(), "Shall I?\nVery good.")
    return got


failed = 0
results = asyncio.run(cases())
for what, (g, e) in results.items():
    mark = "ok  " if g == e else "FAIL"
    failed += g != e
    print(f"{mark} {what}" + ("" if g == e else f": got {g!r}, expected {e!r}"))
print(f"\n{len(results) - failed}/{len(results)} passed")
sys.exit(1 if failed else 0)
