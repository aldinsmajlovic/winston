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

## Still in English

Some lines are said by code, not by him, and stay English whatever the reply language: the short
lines while he sees to a home request, corrections when a device did not respond, the fillers while
an extension works, the steps of cloning a voice, and a nudge's plain line when his own wording does
not come in time. Words that steer him without the model, such as "stop" and "never mind" on a room
box, a shortcut's name said with other words, and times given to reminders ("in ten minutes"), are
understood in English only. The work on each is the Languages track in [the plan](plan.md).
