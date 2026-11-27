# Languages

How Winston copes with a household that does not speak English. The models decide most of it, since
they are what hears and speaks. This page covers what Winston's own code does around them.

## What he answers in

Reply language (`assistant.language`, under Who he is on the Assistant page) is the last line of his
prompt: he understands what you say in any language his hearing knows and answers in this one. A line
earlier in the prompt lost to the language he was spoken to every time, so it stays last. It does not
change his voice or his hearing.

## Hearing

No language is passed to the hearing engines. Fluid Speech Pro and Parakeet v3 (25 European
languages) and Qwen3-ASR detect it themselves. Names in another language, such as Home Assistant's
room names, are best added as [hint words](voices.md#hint-words).

## His voice

No language is passed to the voice either: pick one that speaks your reply language
([voices](voices.md)). Nothing checks the pick, and an English-only voice reads other languages badly.

## Times

Before his voice reads a reply, clock times are put in words in the region's clock ("seventeen
thirty"), because some voices read "17:30" as a number. The words are English, so this happens only
when the reply language is English (it starts with "en": English, en-GB, Engelska). In any other
language the time is left as written for the voice to read.

## Secrets in memory

`remember` refuses a fact that looks like a secret. Besides English it knows the words for password
and door or PIN code in Swedish, Norwegian, Danish, German, Dutch, French, Spanish, Finnish and Polish,
inside compound words too ("wifilösenordet", "Türcode"). In another language only the tool's own rule
holds: he is told never to store passwords, codes or keys.

## Lines said by code

Some lines are said by code, not worded by him: the short lines while he sees to a home request
("One moment."), the one when a conversation grew too long, "Winston online." in the browser, the
box's lines after a double press, corrections when a device did not respond, an extension's filler
while it works, Assist's error lines, the steps of cloning a voice, and a nudge's plain line when his own wording does not come in
time, which is also what your phone and Discord get, with the phone's buttons.

In another reply language each is translated by his brain's model the first time it is needed, with
no tools and on the slot background calls use, and kept in `data/state/lines.json`, so it costs
nothing after that. A line with names in it, such as a correction, is a line of its own. The lines
said while he thinks are translated when a room box's channel starts or a browser connects, before
their takes are rendered, so a request never waits for one. When no translation comes in time (his brain is loading or down) the
line is said in English, and the translation still lands for next time. How good a translation is
follows his brain: a 4B model makes mistakes a larger one does not. Change a translation you do
not like in `lines.json`, or delete the file to have them all made again, and restart Winston.

## Words that steer him

Some words are matched on what was heard before the model reads it, because they must act at once or
decide how he answers:

- ending a conversation on a room box ("stop", "that's all", "never mind") and stopping the music;
- the words around a shortcut's name, so "filmkväll i vardagsrummet, tack" still runs it;
- the words that make a request a home request, so he thinks first ("tänd lamporna");
- asking for the briefing ("what's new?").

These are written by hand, not translated, so a word that would end a conversation by mistake is
never made up: "nej" and "tack" on their own stay answers, as "no" and "thank you" do. English,
Swedish and German are understood together, whatever the reply language, since a household may speak
its own language to him and have him answer in English. Another language is a line added to each
list (`rooms.py`, `brain.py`, `guard.py`, `nudges.py`).

## Cloning a voice

In another reply language he says the consent line in it ("Jag är Maria, och jag godkänner att
Winston kopierar min röst."), and the person agrees by saying it back: at least 60% of its words heard,
and no "not" that the line does not have (inte, nicht, pas and the like). In English it is the word
"agree" without a "don't", as before.

## Still in English

Times given to reminders and follow-ups ("in ten minutes", "imorgon kl 7") are understood in English
only, as are words that steer him in languages other than those above, and the browser's "okay
Winston" or "hey Winston" in a room. The work on each is the Languages track in [the plan](plan.md).
