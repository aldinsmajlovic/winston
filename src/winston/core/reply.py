"""The reply on its way from the LLM to the TTS (or printer), a sentence at a time. With thinking off the
model sometimes writes a stray </think>, and now and then answers, closes a think block it never opened
and answers again ("Oslo, Sir. </think> Oslo, Sir."): the tag is never said, and the answer once.

Only words are said. On 2026-10-16 the box said "[zero seven forty-one] <tool_call> <function=spotify_play>
..." aloud: the model copied the clock's stamp from his message, then wrote a tool call as text, which
llama.cpp did not take as a call behind the stamp. A stamp is dropped wherever it is, and a response is cut
where a tool call written as text begins (the voice's own tags, "[sigh]", stay).

    Pipeline([..., brain.llm, ReplyFilter(), tts, ...])

A sentence goes on once more text follows it that is not a </think>, so ReplySentences, the TTS's
aggregator, speaks a sentence the moment it arrives."""
import re

from loguru import logger
from pipecat.frames.frames import (Frame, InterruptionFrame, LLMFullResponseEndFrame, LLMFullResponseStartFrame,
                                   LLMTextFrame)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.utils.string import match_endofsentence
from pipecat.utils.text.base_text_aggregator import Aggregation, AggregationType
from pipecat.utils.text.simple_text_aggregator import SimpleTextAggregator

# A sentence ends at . ! ? (and any closing quote or bracket) followed by white space, or at a line
# break. "10.6 million" does not end one; the last sentence ends with the response.
_SENTENCE_END = re.compile(r"[.!?][\"')\]]*\s+|\n+")
_THINK_TAG = re.compile(r"</?think>")
_CLOSE_THINK = "</think>"
_CALL = re.compile(r"<tool_call>|<function=")                  # a tool call written as text (Qwen's form)
_STAMP = re.compile(r"\[\d{1,2}:\d\d(?:, room: [^\];]*)?(?:; [^\]]*)?\]\s*")   # the clock's stamp (brain.Clock), copied


def _words(text: str) -> str:
    return " ".join(re.sub(r"\W+", " ", text).split()).casefold()


class ReplyFilter(FrameProcessor):
    """Sits between the LLM and the TTS (or printer) and passes the reply on a sentence at a time,
    without think tags."""

    def __init__(self):
        super().__init__()
        self._buf = ""                             # text since the last sentence end
        # The last finished sentence waits for what follows it, and what came before a stray </think>
        # waits for the first sentence after it: said once, not twice.
        self._last: str | None = None
        self._before_tag: str | None = None
        self._cut = False                          # a tool call was written as text: the rest is not said

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, (LLMFullResponseStartFrame, InterruptionFrame)):
            self._buf, self._last, self._before_tag, self._cut = "", None, None, False
        elif isinstance(frame, LLMTextFrame) and direction == FrameDirection.DOWNSTREAM:
            if self._cut:
                return
            self._buf += frame.text
            if m := _CALL.search(self._buf):
                logger.warning(f"reply: a tool call written as text, not said: {self._buf[m.start():][:80]!r}")
                self._buf, self._cut = self._buf[:m.start()], True
            while _CLOSE_THINK in self._buf:
                before, self._buf = self._buf.split(_CLOSE_THINK, 1)
                self._before_tag = (self._before_tag or "") + (self._last or "") + _THINK_TAG.sub("", before)
                self._last = None
            await self._sentences()
            return
        elif isinstance(frame, LLMFullResponseEndFrame):
            last = _THINK_TAG.sub("", self._buf)
            self._buf, self._cut = "", False
            await self._after_tag(last)
            await self._settle_last()
            if last.strip():
                await self._say(last)
        await self.push_frame(frame, direction)

    async def _sentences(self):
        """Passes on every finished sentence in the buffer; the rest waits. The last one goes once more
        text follows that is not a </think>."""
        while m := _SENTENCE_END.search(self._buf):
            sentence, self._buf = self._buf[:m.end()], self._buf[m.end():]
            sentence = _THINK_TAG.sub("", sentence)
            if not sentence.strip():                # spacing stays with what it follows
                if self._last is not None:
                    self._last += sentence
                elif self._before_tag is not None:
                    self._before_tag += sentence
                else:
                    await self._say(sentence)
                continue
            await self._after_tag(sentence)
            await self._settle_last()
            self._last = sentence
        rest = self._buf.strip()
        if rest and not _CLOSE_THINK.startswith(rest):
            await self._settle_last()

    async def _settle_last(self):
        if self._last is not None:
            s, self._last = self._last, None
            await self._say(s)

    async def _after_tag(self, first: str):
        """What came before a stray </think>, now that the first sentence after it is known: dropped when
        that sentence says it again, otherwise passed on."""
        if self._before_tag is None:
            return
        before, self._before_tag = self._before_tag, None
        if _words(before) and _words(before) in _words(first):
            logger.info(f"reply: said once, not again after a stray </think>: {before.strip()!r}")
        elif before.strip():
            await self._say(before)

    async def _say(self, text: str):
        if text := _STAMP.sub("", text):
            await self.push_frame(LLMTextFrame(text=text))


class ReplySentences(SimpleTextAggregator):
    """The TTS's sentence aggregator behind a ReplyFilter. The filter passes on a sentence only once it has
    seen what follows, ending in white space, so one that ends there is spoken at once. Pipecat's own
    waits for the next letter, which comes only with the filter's next sentence: the first sentence was
    spoken after the second was written. " X" stands in for that letter, so NLTK still keeps "Mr. "
    with its name; a piece not ending in white space ("It's 10.") waits as before."""

    async def aggregate(self, text: str):
        async for a in super().aggregate(text):
            yield a
        done = self._text.strip()
        if done and self._text[-1].isspace() and match_endofsentence(done + " X") == len(done):
            self._text, self._needs_lookahead = "", False
            yield Aggregation(text=done, type=AggregationType.SENTENCE)
