"""Text the TTS reads aloud, made speakable. A TTS text filter, so it rewrites each sentence just
before it is spoken and never what is shown or kept in the conversation.

Clock times: with thinking off the model writes "1:09 AM" even when the region block asks for the
24-hour clock, and Chatterbox reads "1:09" as "one thousand nine". Every time is spoken in the
region's clock, in words: 24-hour the military way ("zero one zero nine", "thirteen hundred",
"seventeen thirty"), 12-hour as "one oh nine AM"."""
import re

from pipecat.utils.text.base_text_filter import BaseTextFilter

from winston.config.schema import WinstonConfig
from winston.core import region

_ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
         "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
_TENS = {2: "twenty", 3: "thirty", 4: "forty", 5: "fifty"}

# 13:05, 1:09 AM, 1:09 a.m., 5 PM, 5pm; not 3:1 or ratios inside longer numbers
_TIME = re.compile(r"(?<![\d:.])(\d{1,2})(?::(\d{2}))?(?:\s*([AaPp])\.?\s?[Mm]\.?(?![A-Za-z]))?(?![\d:])")


def _words(n: int) -> str:
    """0 to 59 in words."""
    return _ONES[n] if n < 20 else _TENS[n // 10] + ("" if n % 10 == 0 else "-" + _ONES[n % 10])


def say_24h(h: int, m: int) -> str:
    hour = f"zero {_ONES[h]}" if h < 10 else _words(h)
    if m == 0:
        return f"{hour} hundred"
    return f"{hour} {'zero ' + _ONES[m] if m < 10 else _words(m)}"


def say_12h(h: int, m: int, suffix: str) -> str:
    minutes = "" if m == 0 else f" oh {_ONES[m]}" if m < 10 else f" {_words(m)}"
    return f"{_words(h)}{minutes} {suffix}"


def speak_times(text: str, clock: str) -> str:
    def repl(mt: re.Match) -> str:
        hs, ms, ap = mt.group(1), mt.group(2), mt.group(3)
        if ms is None and ap is None:              # a bare number is not a time
            return mt.group(0)
        h, m = int(hs), int(ms or 0)
        if m > 59 or (ap and not 1 <= h <= 12) or h > 23:
            return mt.group(0)
        if ap:
            pm = ap.lower() == "p"
            h24 = (h % 12) + (12 if pm else 0)
        else:
            h24, pm = h, h >= 12
        said = say_12h(h24 % 12 or 12, m, "PM" if pm else "AM") if clock == "12h" else say_24h(h24, m)
        # "a.m." at the end of a sentence carries its full stop
        return said + "." if mt.group(0).endswith(".") and mt.end() == len(text) else said
    return _TIME.sub(repl, text)


class SpokenText(BaseTextFilter):
    def __init__(self, cfg: WinstonConfig):
        self._clock = region.resolve(cfg)["time_format"]

    async def filter(self, text: str) -> str:
        return speak_times(text, self._clock)
