# Known issues

What does not work yet, what to do instead, and where the work stands. The detail and the measurements
are in [plan.md](plan.md); smaller rough edges in the web UI are in its UI track open items.

## Voice

| Issue | Do this instead | Where it stands |
| --- | --- | --- |
| **Talking over him on a Voice PE does not stop him.** The box hears his own voice as loudly as yours, so with talking over switched on, his echo stopped his replies. It is off by default. | Say "Okay Winston" over him: the wake word stops the reply and takes your question. "Stop" also cuts him off. | Off until his echo can be told apart from you. The setting is there to try (`barge_in_secs`, [Voice PE](voice-pe.md#ending-a-conversation-and-the-button)). |
| **Talking over him in the browser is not measured.** It relies on the browser's echo cancellation. | Headphones, or a speaker away from the microphone. | Owed: a test with a real microphone. |
| **Okay Winston is missed from across the room** unless said clearly and loudly. | Hold the box's button to talk, or move closer. | A retrain with recordings from a real box ([Okay Winston at a distance](voice-pe.md#okay-winston-at-a-distance)). |
| **A reply cut short by "Stop" counts as said.** Ask "what were you saying?" and he may answer as if you heard the rest. | Ask again in other words. | Open. |
| **Cloning a voice on the box is not built.** | Clone in the browser; the box can then speak in that voice. | Open. |

## Home Assistant

| Issue | Do this instead | Where it stands |
| --- | --- | --- |
| **He cannot ask first per device.** One Home Assistant action turns on a lamp or unlocks a lock, so a lock runs as freely as a lamp. | Leave out of his devices any you want asked about first ([Home Assistant](home-assistant.md)). | Needs a flag per device and a confirm step; open. |

## Languages

| Issue | Do this instead | Where it stands |
| --- | --- | --- |
| **Reminder times are understood in English only**, and "stop", "never mind" and the like in English, Swedish and German only ([languages](languages.md#still-in-english)). | Say those in English. | The plan's Languages track. |

## Reporting one

Open an [issue](https://github.com/aldinsmajlovic/winston/issues) with what you said or typed, what he
did, and the lines from `winston status` and `data/logs/` around it. Leave out anything personal.
