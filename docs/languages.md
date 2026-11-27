# Languages

How Winston copes with a household that does not speak English. The models decide most of it, since
they are what hears and speaks. This page covers what Winston's own code does around them.

## What he answers in

Reply language (`assistant.language`, under Who he is on the Assistant page) is the last line of his
prompt: he understands what you say in any language his hearing knows and answers in this one. A line
earlier in the prompt lost to the language he was spoken to every time, so it stays last. It is written
by its English name ("Swedish", "German"; "en-GB" counts as English), which is how a voice is matched
to it. It does not change his hearing.

## Hearing

No language is passed to the hearing engines. Fluid Speech Pro and Parakeet v3 (25 European
languages) and Qwen3-ASR detect it themselves, so a household can speak to him in a language other
than the one he answers in; fixing hearing to the reply language would lose that. Names in another
language, such as Home Assistant's room names, are best added as [hint words](voices.md#hint-words).

## His voice

Pocket TTS and Qwen3-TTS are given the reply language (`--language {assistant.language}` in their
commands; an older command gets it when the config is read) and speak it when they can. Pocket loads its
model for the language (French is only its larger model) and keeps the voice, since every built-in voice
has a state for each; Qwen3-TTS is told the language, or Auto when it is not one of its own. The others
take the language from the text, or speak only theirs.

| Voice | Speaks |
| --- | --- |
| Pocket TTS | English, French, German, Italian, Portuguese, Spanish |
| Qwen3-TTS | Chinese, English, French, German, Italian, Japanese, Korean, Portuguese, Russian, Spanish |
| VoxCPM2 | 30 languages, Swedish, Danish, Norwegian and Finnish among them |
| Breeze TTS 2 | Chinese and English |
| Chatterbox-Turbo | English |
| Gepard, hosted | Not known here: it depends on the model |

The Speaking card on the Voice page and in setup says when the voice picked does not speak the reply
language ("This voice does not speak Swedish, his reply language, and reads it badly"). Pocket's German
model read a German sentence so that Parakeet heard 15 of its 16 words, its English model 5 of 16 (on
this project's machine, 2026-11-27). His own preview lines on the Voice page (Hear this voice, Play a
sample) are said in the reply language, as [lines said by code](#lines-said-by-code) are.

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

## Times you give him

A reminder, a "later", putting a nudge off and other times passed to a tool are read by code, not by
the model, so the time he says back is the time that was set. The reader takes English, Swedish and
German: "om tio minuter", "på fredag halv sex" (17:30), "imorgon klockan sju", "morgen um halb
sieben" (18:30), "viertel vor neun", "när jag kommer hem". The brain is asked to pass the words as
they were said rather than work out a clock time, since it gets "halv sex" wrong when it does (it is
half past five). When an extension cannot read a time it asks him for it in English, so another
language still works through him.

The diary's day lookup ("igår", "i tisdags", "letzten Montag") and a memory's last day ("fredag",
"nästa vecka") are read the same way. A fact in memory stays in the language it was said in, and its
days are made dates when it is stored, as in English: "Min syster kommer på lördag" is kept as "Min
syster kommer on Saturday 28 November 2026". The date is written in English, which he reads in any
language. "På lördagar" (a habit) and "i lördags" (last Saturday) are left as they are.

## Still in English

Words that steer him in languages other than those above, and the browser's "okay Winston" or "hey
Winston" in a room. The work on each is the Languages track in [the plan](plan.md).
