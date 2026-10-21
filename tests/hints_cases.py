"""Hint words: which words are added by themselves, and correcting close misspellings of them after
speech to text. Runs against a scratch data directory, no hardware needed.

    uv run python tests/hints_cases.py

Precision: 200 ordinary English sentences, full of words near the hint words (win, wind, Wilson,
winter, Erica's cousins), must come through unchanged."""
import sys
import wave
from types import SimpleNamespace

import harness
from harness import expect, scratch_data

scratch = scratch_data("winston-hints-")
friends = scratch / "voices" / "friends"
friends.mkdir(parents=True)
(friends / "maria.json").write_text('{"slug": "maria", "name": "Maria", "created": 0, "consent_text": "", "seconds": 1}')
with wave.open(str(friends / "maria.wav"), "wb") as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(b"\0\0" * 160)

from loguru import logger  # noqa: E402
from winston.api.options import _hint_words  # noqa: E402
from winston.config.schema import ExtensionConfig, WinstonConfig  # noqa: E402  (after WINSTON_DATA is set)
from winston.core.hints import Hearing, correct, hints  # noqa: E402
from winston.extension_host import texts  # noqa: E402


cfg = WinstonConfig.model_validate({
    "llm": {"model": "x"},
    "stt": {"hint_words": ["Vardagsrummet", "Köket"]},
    "channels": {"discord": {"enabled": True, "contacts": {"Erik": "123456789012345678", "Mom": "123456789012345679"}}},
    "providers": {"home_assistant": {"url": "http://ha.local:8123", "expose": {
        "light.main": {"name": "Main room lights", "aliases": ["the lights", "main lights"]},
        "light.hektar": {"aliases": ["hektar lamp"]},
        "sensor.phone_battery": {"aliases": ["phone battery", "battery", "phone"]},
        "device_tracker.oneplus": {"aliases": ["the oneplus", "phone location"]},
        "button.pc": {"name": "PC", "aliases": ["my computer"]}}}},
})

# which words
got = hints(cfg)
words = [h.word for h in got]
by = {h.word: h.source for h in got}
expect("his name first, from assistant.name", words[:1] == ["Winston"] and by["Winston"] == "assistant", words)
expect("cloned voices", by.get("Maria") == "voice", by)
expect("Discord contacts, common words left out", by.get("Erik") == "contact" and "Mom" not in by, by)
expect("proper nouns from Home Assistant names", by.get("Hektar") == "home_assistant" and "Oneplus" in by, by)
expect("never whole phrases or common words",
       not {"Main", "Lights", "Battery", "Phone", "Location", "Computer", "PC", "Hektar lamp", "Main room lights"} & set(words),
       words)
expect("yours last, as typed", words[-2:] == ["Vardagsrummet", "Köket"] and by["Köket"] == "yours", words)
expect("an extra word for one transcription", "Omar" in [h.word for h in hints(cfg, ["Omar"])])
off = cfg.model_copy(deep=True)
off.channels.discord.enabled = False
expect("no contacts while Discord is off", "Erik" not in [h.word for h in hints(off)])
off = cfg.model_copy(deep=True)
off.stt.hint_words_removed = ["hektar", "Maria"]
expect("a removed automatic word is left out", not {"Hektar", "Maria"} & {h.word for h in hints(off)})
off.stt.hint_words.append("Hektar")
expect("and comes back as yours when you add it", any(h.word == "Hektar" and h.source == "yours" for h in hints(off)))

# an extension's words, and how the Voice page lists them
texts.write("music", None, ["Radiohead", "Bench Press"])
music = cfg.model_copy(deep=True)
music.extensions["music"] = ExtensionConfig(enabled=True)
ext = {(h.word, h.origin) for h in hints(music) if h.source == "extension"}
expect("an extension's words keep which extension asked", ext == {("Radiohead", "music"), ("Bench", "music"), ("Press", "music")},
       ext)
host = SimpleNamespace(described={"music": {"manifest": {"name": "Music", "icon": "simple-icons:spotify"}}})
shown = {c.value: c for c in _hint_words(music, {"extensions": host}).choices}
expect("listed under the extension, by its name and icon",
       (shown["Radiohead"].group, shown["Radiohead"].hint, shown["Radiohead"].icon)
       == ("extension:music", "Music", "simple-icons:spotify"), shown.get("Radiohead"))
expect("common words are not listed, since the correction leaves them alone", "Press" not in shown and "Bench" in shown,
       sorted(shown))
expect("the other sources keep their own groups",
       (shown["Winston"].group, shown["Maria"].group, shown["Erik"].group, shown["Hektar"].group)
       == ("assistant", "voice", "contact", "home_assistant"), sorted(shown))
bare = {c.value: c for c in _hint_words(music, {}).choices}
expect("an extension the host has not described is named by its id", bare["Radiohead"].hint == "music",
       bare.get("Radiohead"))
texts.clear("music")

# corrections
W = words + ["Aldin", "Sara", "Omar"]
for heard, want in [
    ("Hey Winson.", "Hey Winston."),
    ("Windsun can copy my voice", "Winston can copy my voice"),
    ("Turn on Vardag's Rummet.", "Turn on Vardagsrummet."),
    ("Turn off the lights in vardag's rummet", "Turn off the lights in Vardagsrummet"),
    ("I'm Maria and I agree that Winsten can copy my voice.", "I'm Maria and I agree that Winston can copy my voice."),
    ("Winson's voice is nice.", "Winston's voice is nice."),
    ("HEY WINSON!", "HEY WINSTON!"),
    ("Hey Win ston, what time is it?", "Hey Winston, what time is it?"),
    ("Turn on the Hectar lamp.", "Turn on the Hektar lamp."),
]:
    fixed, changes = correct(heard, W)
    expect(f"corrects {heard!r}", fixed == want, f"-> {fixed!r} {changes}")

for heard in ["wind", "The wind is strong today.", "Wilson", "Ask Wilson about it.", "They win some.",
              "We win some and lose some.", "Hey Winston.", "Winston's voice", "Turn on the lights.",
              "Eric is coming over.", "Marie called.", "Is Alden here?", "Mario Kart tonight?", "one plus two",
              "Main room lights on", "I modeled in clay."]:
    fixed, changes = correct(heard, W)
    expect(f"leaves {heard!r}", fixed == heard, f"-> {fixed!r} {changes}")

expect("text without a change is returned as it came", correct("Turn  on\nthe lights ", W) == ("Turn  on\nthe lights ", []))
expect("nothing to correct without hint words", correct("Hey Winson.", []) == ("Hey Winson.", []))

# the log line, and the switch
lines: list[str] = []
sink = logger.add(lambda m: lines.append(str(m)), format="{message}")
h = Hearing(cfg)
out = h.finish("Hey Winson.", 1.2, 180)
expect("the STT line has before -> after", out == "Hey Winston." and "'Hey Winson.' -> 'Hey Winston.' (Winson -> Winston)"
       in lines[-1], lines[-1:])
out = h.finish("Is Jonathan here?", 1.0, 150, extra=["Jonatan"])
expect("an extra word is used for that call", out == "Is Jonatan here?", out)
expect("and only for that call", h.finish("Is Jonathan here?", 1.0, 150) == "Is Jonathan here?")
off = cfg.model_copy(deep=True)
off.stt.fix_misspellings = False
out = Hearing(off).finish("Hey Winson.", 1.2, 180)
expect("fix_misspellings off leaves the text", out == "Hey Winson." and lines[-1].strip().endswith("'Hey Winson.' in 180 ms"),
       lines[-1:])
logger.remove(sink)

# precision on ordinary English
SENTENCES = """\
Turn on the lights in the kitchen.
Can you turn off the bedroom lamp?
What's the weather like tomorrow?
Set a timer for ten minutes.
How long until the pasta is done?
Remind me to call my sister tonight.
I think we win some and lose some.
The wind is really strong outside.
Close the window before it starts raining.
Is it going to be windy this afternoon?
Winter is coming early this year.
Mr Wilson from next door dropped by.
Did we win the game last night?
She wins every time we play cards.
Who is winning the match right now?
Put the wine in the fridge, please.
Open the blinds in the living room.
Dim the lights to thirty percent.
Make it a bit warmer in here.
What's the temperature in the bedroom?
Play some quiet music.
Pause the music for a second.
Turn the volume down a little.
How much battery does my phone have?
Is my phone charging?
Where is my phone?
Wake up the computer.
Turn on the main lights.
Switch off everything in the hallway.
Lock the front door.
Is the back door locked?
What time is it?
What day is it today?
What's the date on Saturday?
Add milk to the shopping list.
Add eggs and bread to the list.
What's on my calendar tomorrow?
Tell me a fun fact.
Tell me a joke.
Can you tell me something interesting?
How far is the moon?
How tall is the tallest building in the world?
Who wrote the book I was reading?
Search the web for cheap flights to London.
What's the news today?
How are you doing?
Thanks, that's all.
Good night.
Good morning, how did you sleep?
I'm heading out now.
I'll be back in an hour.
Can you remember that I like my coffee black?
Forget what I told you about the dentist.
What do you remember about me?
Check your memory.
Use your own voice again.
Go back to your normal voice.
That was too loud.
Speak a bit slower, please.
Say that again.
I didn't catch that.
No, they're not.
Try again.
Turn them on again.
Turn them up.
Can you turn on my uh turn off my lights?
Turn on the lights. Uh turn on all the lights.
Hey, are you there?
Are you listening?
Stop talking.
Never mind.
Actually, cancel that.
Wait, not that one.
The other lamp, the one on the shelf.
The shelf lamp, not the ceiling lamp.
Make the ceiling lamp warm white.
Set the shelf lamp to blue.
Turn the lamp by the window on.
It's getting dark in here.
It's too bright.
Why is it so cold in the flat?
Is the heating on?
Should I bring an umbrella?
Will it snow this weekend?
What's the forecast for the week?
How windy will it be on Monday?
When does the sun set today?
What time is sunrise tomorrow?
I won the lottery, just kidding.
We won by two points.
The winner gets a free dinner.
Let's watch a movie tonight.
What should I cook for dinner?
Give me a recipe for pancakes.
How many grams in a cup of flour?
Convert fifty dollars to euros.
What's fifteen percent of eighty?
How do you spell necessary?
What does the word ubiquitous mean?
Translate good morning into French.
Read me the last message.
Send a message to my brother.
Text my friend that I'm running late.
I'm running late, sorry.
The train was delayed again.
Traffic was terrible this morning.
I went for a walk in the park.
The dog needs a walk.
Did I feed the cat?
The plants need water.
Water the plants on Sunday.
I'm going to the gym.
My back hurts a bit.
I have a headache.
I should drink more water.
I need to sleep earlier.
Wake me up at seven.
Set an alarm for half past six.
Snooze for five minutes.
What's the time in Tokyo?
How old is the universe?
Why is the sky blue?
How does a rainbow form?
What's the capital of Australia?
Who painted the Mona Lisa?
When did the war end?
What's a good name for a cat?
Recommend a book about history.
Recommend a podcast for the drive.
What's the best way to learn Swedish?
How do you say thank you in Swedish?
I'm learning to play the guitar.
My guitar is out of tune.
The window is stuck again.
The washing machine is making a noise.
The dishwasher is done.
Is the oven still on?
Turn off the TV.
Turn on the TV in the bedroom.
Is anyone home?
Who's at the door?
Somebody rang the doorbell.
The package arrived today.
I ordered a new desk lamp.
Put the new lamp in the office.
The office is too warm.
Open the window in the office.
It's a nice day for a bike ride.
The bike has a flat tyre.
I need to fix the bike.
Where did I put my keys?
I lost my wallet somewhere.
You're pretty smart.
You sound a bit different today.
Can you sound like me?
Clone my voice.
Clone my friend's voice.
Can you use Maria's voice?
Use my voice from now on.
Keep this voice.
That's creepy, go back.
What can you do?
Can you turn the lights on and off?
What devices can you control?
Which lights are on?
Are all the lights off?
Turn everything off, I'm going to bed.
That's wrong.
You made a mistake.
Let's try something else.
I'm bored.
Talk to me about something.
Tell me about the history of the internet.
How do computers work?
What is machine learning?
What's the difference between weather and climate?
How much does an elephant weigh?
Can dogs see colours?
How fast can a cheetah run?
What's the longest river in Europe?
How many people live in Sweden?
When is the next holiday?
Is the shop open on Sunday?
What time does the pharmacy close?
I have a meeting at nine.
Move my meeting to Thursday.
Cancel my dentist appointment.
The dentist is next Wednesday.
We're having friends over on Friday.
Make a note that the boiler was serviced.
The boiler is making a strange sound.
The Wi-Fi is slow again.
Restart the router.
Is the internet down?
My laptop is almost out of battery.
Plug in the charger, please.
Where's the remote?
The remote needs new batteries.
Winter tyres go on in November.
Windows need cleaning before the winter.
""".strip().splitlines()
expect("200 ordinary sentences", len(SENTENCES) == 200, len(SENTENCES))
false = [(s, correct(s, W)) for s in SENTENCES if correct(s, W)[0] != s]
expect("no false corrections in ordinary English", not false, false)

print(f"\n{'FAILED ' + str(harness.failed) if harness.failed else 'all passed'}")
sys.exit(1 if harness.failed else 0)
