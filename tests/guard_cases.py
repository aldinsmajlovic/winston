"""Claim guard cases: pure checks on judge(), no hardware needed.

    uv run python tests/guard_cases.py
"""
import sys

from winston.core.guard import judge

ACT_OK = [("light__HassLightSet", True)]
ACT_FAILED = [("intent__HassTurnOn", False)]
READ_OK = [("homeassistant__GetLiveContext", True)]

CASES = [
    # (reply, tool calls this turn, expected verdict)
    ("Setting the living room lights to fifty percent brightness now.", [], "act"),
    ("I'll set the lights to warm.", [], "act"),
    ("Done.", [], "act"),
    ("The main room lights are now set to warm.", [], "act"),
    ("The main room lights are now set to warm.", ACT_FAILED, "act"),
    ("The main room lights are already on at one hundred percent.", [], "state"),
    ("The lights are currently on.", [], "state"),
    ("Living room lights at fifty percent, warm.", ACT_OK, None),
    ("The main room lights are now set to warm.", ACT_OK, None),
    ("The lights are now on.", READ_OK, None),                       # a state report after a read
    ("Your phone is currently at sixty four percent, read two hours ago.", READ_OK, None),
    ("It's ten past eight.", READ_OK, None),
    ("I couldn't find the living room lights. Could you tell me the exact name?", [], None),
    ("Would you like me to adjust the brightness or color temperature?", [], None),
    ("Hey Aldin. What can I help you with today?", [], None),
    ("Hello! I'm here to help you around the house.", [], None),
    ("I can't do that by voice.", [], None),
]

failed = 0
for text, tools, expected in CASES:
    got = judge(text, tools)
    mark = "ok  " if got == expected else "FAIL"
    failed += got != expected
    print(f"{mark} {got!s:5} expected {expected!s:5}  {text!r} with {tools}")
print(f"\n{len(CASES) - failed}/{len(CASES)} passed")
sys.exit(1 if failed else 0)
