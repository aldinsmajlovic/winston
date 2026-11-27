"""Reading a time as the user said it: "at six", "in 20 minutes", "tomorrow morning", "half past
seven", "Monday at 17:30". Done in code rather than by the model, so the time he says back is the
time that was set.

A bare hour with no day means its next occurrence ("at six" at 10:00 is 18:00 today). With a day and
no am or pm, 7 to 11 are the morning and 1 to 6 the afternoon or evening ("tomorrow at seven" is
07:00, "Friday at six" 18:00). A day with no time takes the morning. The parts of the day are
settings in the core (`speak_up`); these are the defaults."""
from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta

DAY_PARTS: dict[str, time] = {"morning": time(8), "afternoon": time(15), "evening": time(19),
                              "tonight": time(20), "noon": time(12), "midday": time(12), "lunch": time(12),
                              "midnight": time(0)}

_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_WEEKDAY_ALIASES = {w[:3]: i for i, w in enumerate(_WEEKDAYS)} | {w: i for i, w in enumerate(_WEEKDAYS)} | \
    {"tues": 1, "thur": 3, "thurs": 3}
_MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
           "november", "december"]
_MONTH_ALIASES = {m: i + 1 for i, m in enumerate(_MONTHS)} | {m[:3]: i + 1 for i, m in enumerate(_MONTHS)} | \
    {"sept": 9}
_UNITS = {"second": 1, "sec": 1, "minute": 60, "min": 60, "hour": 3600, "hr": 3600, "day": 86400,
          "week": 604800}
_ONES = {"zero": 0, "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
         "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
         "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "couple": 2,
         "few": 3}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80,
         "ninety": 90}
_FILLER = {"at", "on", "by", "the", "o'clock", "oclock", "please", "around", "about", "of", "in"}


# Swedish and German, rewritten into the English above before it is read: the brain often passes a time as it
# was said ("fredag halv sex", "morgen um halb sieben"), and gets "halv sex" (5:30) wrong when it translates.
_NUMBERS = {"noll": 0, "en": 1, "ett": 1, "två": 2, "tre": 3, "fyra": 4, "fem": 5, "sex": 6, "sju": 7, "åtta": 8,
            "nio": 9, "tio": 10, "elva": 11, "tolv": 12, "tretton": 13, "fjorton": 14, "femton": 15, "sexton": 16,
            "sjutton": 17, "arton": 18, "nitton": 19, "tjugo": 20, "trettio": 30, "fyrtio": 40, "femtio": 50,
            "null": 0, "ein": 1, "eine": 1, "eins": 1, "einer": 1, "einem": 1, "zwei": 2, "drei": 3, "vier": 4,
            "fünf": 5, "sechs": 6, "sieben": 7, "acht": 8, "neun": 9, "zehn": 10, "elf": 11, "zwölf": 12,
            "dreizehn": 13, "vierzehn": 14, "fünfzehn": 15, "sechzehn": 16, "siebzehn": 17, "achtzehn": 18,
            "neunzehn": 19, "zwanzig": 20, "dreißig": 30, "vierzig": 40, "fünfzig": 50}
_PHRASES = [  # in order: the longer phrase first
    (r"\bi övermorgon\b|\bövermorgon\b|\bübermorgen\b", "day after tomorrow"),
    (r"\bi förrgår\b|\bförrgår\b|\bvorgestern\b", "day before yesterday"),
    (r"\bi går\b|\bigår\b|\bgestern\b", "yesterday"),
    (r"\bheute abend\b|\bi kväll\b|\bikväll\b|\bi natt\b|\binatt\b", "tonight"),
    (r"\b(?:på|am) morgonen\b|\bam morgen\b|\bpå morgonen\b", "morning"),
    (r"\bi morgon\b|\bimorgon\b", "tomorrow"),
    (r"\bheute (?:morgen|früh)\b", "this morning"),
    (r"\bi dag\b|\bidag\b|\bheute\b", "today"),
    (r"\bi (mån|tis|ons|tors|fre|lör|sön)dags\b",                     # i tisdags: the Tuesday just gone
     lambda m: {"mån": "monday", "tis": "tuesday", "ons": "wednesday", "tors": "thursday", "fre": "friday",
                "lör": "saturday", "sön": "sunday"}[m.group(1)]),
    (r"\bi eftermiddag\b", "afternoon"), (r"\bi förmiddag\b", "morning"),
    (r"\b(?:den här|denna) veckan?\b|\bdiese woche\b", "this week"),
    (r"\b(?:zu|nach) hause\b", "home"),
    (r"\ben halvtimme\b|\bhalvtimme\b|\ben halv timme\b|\beine halbe stunde\b|\bhalbe stunde\b", "half an hour"),
    (r"\bein paar\b|\bett par\b", "a couple"), (r"\bnågra\b", "a few"),
    (r"\b(\d{1,2})\.(?= )", r"\1"), (r"\bam (?=\d)", "on "),                      # am 29. Mai
    (r"\bam (?=(?:montag|dienstag|mittwoch|donnerstag|freitag|samstag|sonntag|sonnabend|wochenende)\b)", "on "),
    (r"\b(tjugo|trettio|fyrtio|femtio)(en|ett|två|tre|fyra|fem|sex|sju|åtta|nio)\b",
     lambda m: str(_NUMBERS[m.group(1)] + _NUMBERS[m.group(2)])),
    (r"\b(ein|zwei|drei|vier|fünf|sechs|sieben|acht|neun)und(zwanzig|dreißig|vierzig|fünfzig)\b",
     lambda m: str(_NUMBERS[m.group(1)] + _NUMBERS[m.group(2)])),
    (r"\b(" + "|".join(_NUMBERS) + r")\b", lambda m: str(_NUMBERS[m.group(1)])),
    (r"\b(\d{1,2}) uhr (\d{2})\b", r"\1:\2"),
    (r"\b(?:på|i) (?=\d+ (?:sekund|minut|timm|dag|veck))", "in "),            # på tio minuter, i 10 minuter
    (r"\bhal[vb] (\d{1,2})\b", lambda m: f"{(int(m.group(1)) - 2) % 12 + 1}:30"),     # halv sex, halb sechs: 5:30
    (r"\b(?:kvart över|viertel nach) (\d{1,2})\b", r"quarter past \1"),
    (r"\b(?:kvart i|viertel vor) (\d{1,2})\b", r"quarter to \1"),
    (r"\b(\d{1,2}) (?:minuter )?(?:över|nach) (\d{1,2})\b", r"\1 past \2"),
    (r"\b(\d{1,2}) (?:minuter )?(?:i|vor) (\d{1,2})\b", r"\1 to \2"),
]
_WORDS = {"måndag": "monday", "tisdag": "tuesday", "onsdag": "wednesday", "torsdag": "thursday", "fredag": "friday",
          "lördag": "saturday", "söndag": "sunday", "montag": "monday", "dienstag": "tuesday", "mittwoch": "wednesday",
          "donnerstag": "thursday", "freitag": "friday", "samstag": "saturday", "sonnabend": "saturday",
          "sonntag": "sunday",
          "januari": "january", "februari": "february", "mars": "march", "maj": "may", "juni": "june",
          "juli": "july", "augusti": "august", "oktober": "october", "januar": "january", "februar": "february",
          "märz": "march", "mai": "may", "dezember": "december",
          "morgon": "morning", "bitti": "morning", "förmiddag": "morning", "förmiddagen": "morning",
          "eftermiddag": "afternoon", "eftermiddagen": "afternoon", "kväll": "evening", "kvällen": "evening",
          "midnatt": "midnight", "morgen": "tomorrow", "früh": "morning", "morgens": "morning", "vormittag": "morning",
          "vormittags": "morning", "nachmittag": "afternoon", "nachmittags": "afternoon", "abend": "evening",
          "abends": "evening", "mittag": "noon", "mittags": "noon", "mitternacht": "midnight",
          "sekund": "second", "sekunder": "seconds", "minut": "minute", "minuter": "minutes", "timme": "hour",
          "timmar": "hours", "dag": "day", "dagar": "days", "vecka": "week", "veckor": "weeks", "sekunde": "second",
          "sekunden": "seconds", "minuten": "minutes", "stunde": "hour", "stunden": "hours", "tag": "day",
          "tage": "days", "tagen": "days", "woche": "week", "wochen": "weeks",
          "om": "in", "klockan": "at", "kl": "at", "på": "on", "um": "at", "uhr": "", "och": "and", "und": "and",
          "nästa": "next", "kommande": "next", "nächste": "next", "nächsten": "next", "nächster": "next",
          "förra": "last", "letzte": "last", "letzten": "last", "letzter": "last", "vergangenen": "last",
          "den": "", "der": "", "denna": "this", "diese": "this", "dieser": "this", "diesen": "this", "kommenden": "next", "hemma": "home", "hem": "home", "daheim": "home"}


# Added to every time argument's description. Measured with qwen3.5:4b on six Swedish and German reminders:
# without it 20 of 24 right (it worked out "halv sex" itself, as 18:00); with it 36 of 36 (2026-11-27).
WHEN_WORDS = ("Keep their words in the language they said them, without working out a clock time yourself: "
              "\"tisdag kvart i sju\" is passed as it is.")


def in_english(text: str) -> str:
    """A time said in Swedish or German as the English this module reads: "om tio minuter" -> "in 10 minutes",
    "fredag halv sex" -> "friday 5:30", "morgen um halb sieben" -> "tomorrow at 6:30". English passes as it is."""
    text = text.lower()
    for pattern, english in _PHRASES:
        text = re.sub(pattern, english, text)
    return " ".join(w for w in (_WORDS.get(w, w) for w in text.split()) if w)


def _number(words: list[str]) -> float | None:
    """"20", "twenty", "twenty five", "twenty-five", "a", "half", "one and a half"."""
    text = " ".join(words).replace("-", " ").strip()
    if not text:
        return None
    if re.fullmatch(r"\d+(\.\d+)?", text):
        return float(text)
    half = 0.0
    if text.endswith(" and a half"):
        half, text = 0.5, text[: -len(" and a half")].strip()
    if text in ("half", "half a", "half an"):
        return 0.5
    total, seen = 0, False
    for w in text.split():
        if w in _TENS:
            total += _TENS[w]
        elif w in _ONES:
            total += _ONES[w]
        elif w.isdigit():
            total += int(w)
        else:
            return None
        seen = True
    return total + half if seen else None


def _clean(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[,!?]", " ", text)
    text = re.sub(r"(\d)\.(\d\d)\b", r"\1:\2", text)          # 17.30 -> 17:30
    text = re.sub(r"\b(\d{1,2})(am|pm|a\.m\.|p\.m\.)", r"\1 \2", text)
    text = text.replace("a.m.", "am").replace("p.m.", "pm")
    return re.sub(r"\s+", " ", text).strip().rstrip(".")


def _relative(text: str, now: datetime) -> datetime | None:
    """"in 20 minutes", "in an hour and a half", "in half an hour", "in 1 hour 30 minutes"."""
    if not text.startswith("in "):
        return None
    rest = text[3:].strip()
    if rest in ("half an hour", "half hour"):
        return now + timedelta(minutes=30)
    pieces = re.findall(r"((?:[\w-]+\s)*?[\w-]+?)\s+(second|sec|minute|min|hour|hr|day|week)s?(\s+and a half)?\b",
                        rest)
    if not pieces:
        return None
    covered = re.sub(r"\b(second|sec|minute|min|hour|hr|day|week)s?\b", "", rest)
    total = 0.0
    for words, unit, half in pieces:
        n = _number([w for w in words.split() if w != "and"])
        if n is None:
            return None
        total += (n + (0.5 if half else 0)) * _UNITS[unit]
    if re.search(r"[a-z]", re.sub(r"\b(and|a|an|half|[\w-]+)\b", "", covered)):
        return None
    return now + timedelta(seconds=total)


def _clock(words: list[str]) -> tuple[int, int, str | None] | None:
    """(hour, minute, "am" | "pm" | None) from "six", "6:30", "6 pm", "half past six", "quarter to
    seven", "18:00", "noon"."""
    text = " ".join(words)
    if text in ("noon", "midday"):
        return 12, 0, "pm"
    if text == "midnight":
        return 0, 0, "am"
    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?(?: (am|pm))?", text)
    if m:
        return int(m.group(1)), int(m.group(2) or 0), m.group(3)
    m = re.fullmatch(r"(.+?) (past|to|after) (.+?)(?: (am|pm))?", text)
    if m:
        minutes = {"half": 30, "quarter": 15}.get(m.group(1))
        if minutes is None:
            n = _number(m.group(1).replace(" minutes", "").replace(" minute", "").split())
            minutes = int(n) if n is not None else None
        hour = _number(m.group(3).split())
        if minutes is None or hour is None:
            return None
        h = int(hour)
        if m.group(2) == "to":
            return (h - 1) % 24, 60 - minutes, m.group(4)
        return h, minutes, m.group(4)
    m = re.fullmatch(r"(.+?)(?: (am|pm))?", text)
    if m:
        n = _number(m.group(1).split())
        if n is not None and n == int(n) and 0 <= n <= 24:
            return int(n), 0, m.group(2)
    return None


def _to_24(hour: int, ampm: str | None) -> int:
    if ampm == "pm" and hour < 12:
        return hour + 12
    if ampm == "am" and hour == 12:
        return 0
    return hour


def _next_weekday(today: date, weekday: int, force_next: bool) -> date:
    ahead = (weekday - today.weekday()) % 7
    if ahead == 0 and force_next:
        ahead = 7
    return today + timedelta(days=ahead)


def _date(words: list[str], today: date) -> tuple[date | None, list[str], time | None]:
    """Takes the day out of the words: (the day or None, the words left, a part of the day if the
    day word implied one, as "tonight" does)."""
    out, day, part = [], None, None
    i = 0
    while i < len(words):
        w = words[i]
        nxt = words[i + 1] if i + 1 < len(words) else ""
        if w == "today":
            day = today
        elif w == "tonight":
            day, part = today, DAY_PARTS["tonight"]
        elif w == "tomorrow":
            day = today + timedelta(days=1)
        elif w == "day" and nxt == "after" and words[i + 2:i + 3] == ["tomorrow"]:
            day, i = today + timedelta(days=2), i + 2
        elif w in ("this", "next") and nxt in _WEEKDAY_ALIASES:
            day, i = _next_weekday(today, _WEEKDAY_ALIASES[nxt], force_next=(w == "next")), i + 1
        elif w in ("this", "next") and nxt == "week":
            day, i = today + timedelta(days=7 if w == "next" else 0), i + 1
        elif w in _WEEKDAY_ALIASES:
            day = ("weekday", _WEEKDAY_ALIASES[w])                   # decided once the time is known
        elif w in _MONTH_ALIASES and nxt.rstrip("stndrh").isdigit():
            day, i = ("month", _MONTH_ALIASES[w], int(re.sub(r"\D", "", nxt))), i + 1
        elif re.fullmatch(r"\d{1,2}(st|nd|rd|th)?", w) and nxt in _MONTH_ALIASES:
            day, i = ("month", _MONTH_ALIASES[nxt], int(re.sub(r"\D", "", w))), i + 1
        else:
            out.append(w)
        i += 1
    return day, out, part


def parse_when(text: str, now: datetime, parts: dict[str, time] | None = None) -> datetime | None:
    """The time `text` names, in `now`'s time zone, or None when it cannot be read."""
    parts = {**DAY_PARTS, **(parts or {})}
    raw = text.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):                # a bare date: the morning, not midnight
        try:
            return datetime.combine(date.fromisoformat(raw), parts["morning"], tzinfo=now.tzinfo)
        except ValueError:
            return None
    try:                                                       # ISO, as a model or a program writes it
        iso = datetime.fromisoformat(raw)
        return iso if iso.tzinfo else iso.replace(tzinfo=now.tzinfo)
    except ValueError:
        pass
    text = in_english(_clean(raw))
    if (rel := _relative(text, now)) is not None:
        return rel
    today = now.date()
    day, words, part = _date(text.split(), today)
    words = [w for w in words if w not in ("this",)]
    # a part of the day: "morning", "in the evening", "this afternoon"
    for name in ("morning", "afternoon", "evening", "tonight", "noon", "midday", "lunch", "midnight"):
        if name in words and not (name in ("noon", "midday", "midnight") and len(words) == 1):
            part = parts[name]
            words = [w for w in words if w != name]
    words = [w for w in words if w not in _FILLER]
    clock = _clock(words) if words else None
    if words and clock is None:
        return None
    if isinstance(day, tuple) and day[0] == "month":
        _, month, dom = day
        try:
            day = date(today.year, month, dom)
        except ValueError:
            return None
        if day < today:
            day = date(today.year + 1, month, dom)
    if clock is None:
        at = part or (parts["morning"] if day is not None else None)
        if at is None:
            return None
        if isinstance(day, tuple):
            day = _next_weekday(today, day[1], force_next=datetime.combine(today, at, now.tzinfo) <= now)
        return datetime.combine(day or today, at, tzinfo=now.tzinfo)
    hour, minute, ampm = clock
    if not (0 <= hour <= 24 and 0 <= minute < 60):
        return None
    if ampm is None and part is not None and 1 <= hour <= 11 and part >= time(12):
        ampm = "pm"                                              # "six in the evening"
    if ampm is None and part is not None and part < time(12):
        ampm = "am"
    if ampm is not None or hour == 0 or hour >= 12:
        at = time(_to_24(hour, ampm) % 24, minute)
        if isinstance(day, tuple):
            candidate = datetime.combine(today, at, tzinfo=now.tzinfo)
            day = _next_weekday(today, day[1], force_next=candidate <= now)
        if day is not None:
            return datetime.combine(day, at, tzinfo=now.tzinfo)
        result = datetime.combine(today, at, tzinfo=now.tzinfo)
        return result if result > now else result + timedelta(days=1)
    # a bare hour 1 to 11 with no am or pm
    if day is None:
        options = [datetime.combine(today + timedelta(days=d), time(h, minute), tzinfo=now.tzinfo)
                   for d in (0, 1) for h in (hour, hour + 12)]
        return min(o for o in options if o > now)
    at = time(hour if hour >= 7 else hour + 12, minute)
    if isinstance(day, tuple):
        candidate = datetime.combine(today, at, tzinfo=now.tzinfo)
        day = _next_weekday(today, day[1], force_next=candidate <= now)
    return datetime.combine(day, at, tzinfo=now.tzinfo)
