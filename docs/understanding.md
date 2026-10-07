# Understanding

How Winston hears, decides whether he is being spoken to, and knows what a name means. A design,
proposed 2026-10-07 after the first week of real use; not built yet. When it is built, this page
becomes the reference for it.

## Why

The box's log from 2026-09-25 to 2026-10-03 holds 172 requests. Lights mostly worked. What went wrong
comes down to four causes, each of which was being patched separately:

| What happened | Cause |
| --- | --- |
| A scene's phrase heard as a common word four times in one evening; artists' names heard as other words | He hears without context |
| Room talk after a reply added an item to the shopping list and played three songs nobody asked for; Swedish got "Yes, sir." | A timer decides he is spoken to |
| A song asked for by title and artist played another artist's song; a lamp asked for by its name took six lookups and stayed off | Names are matched to things by string tricks, separately in each place |
| All of the above was found by reading logs by hand; no audio is kept and no mistake can be marked | Nothing learns from use |

The patches it replaces: the misspelling fixer in `core/hints.py`, aliases for mishearings (a scene's
name as it is misheard), the keyword list that decides a home request (`core/brain.py`), the
follow-up timer as the only gate, and the string matching in the Spotify extension.

## The turn

```mermaid
flowchart LR
  audio([Audio]) --> hear[Hearing]
  dir[(Directory)] -. names .-> hear
  hear --> who{For him?}
  who -- no --> silent([Says nothing])
  who -- yes --> brain[Brain]
  dir -. map .-> brain
  brain --> act([Acts, answers])
  hear & who & brain --> rec[(Turn record)]
  marks([Your marks]) --> rec
  rec --> check[winston check turns]
```

Four parts, built in this order.

## 1. Turn record and marks

Every turn is kept, so mistakes can be found, marked and used to test the next change.

- **What is kept.** One folder per day under `data/turns/`. Per turn: the audio (FLAC, 16 kHz), and a
  JSON line with the channel and room, wake word or follow-up, what was heard and by which engine, the
  "for him?" decision with its signals, the tool calls with arguments and results, what he said, and
  the time each stage took. Today this is spread over `satellite.log`, `voice.log`, `tool_calls.jsonl`
  and `latency.jsonl`, without the audio.
- **How long.** `turns.keep_days`, 14 by default; a marked turn is kept until unmarked. The week above
  had 798 s of request audio, about 15 MB as FLAC.
- **Off by default for others.** Recording a home is personal. The setting is off in `examples/` and
  the first-run page asks; nothing leaves the machine either way.
- **Marking a mistake.** By voice: "that's not what I said", "I wasn't talking to you", "that was the
  wrong song". On the Talk page: each turn gets a mark with the kind (misheard, not for him, wrong thing,
  too slow) and a box for what you actually said. A turn you corrected in the next breath ("no, the
  living room, not the main room") is offered for marking on its own.
- **The test.** `winston check turns` replays the marked turns through the current setup or a
  candidate (another speech engine, another model, another prompt), with actions stubbed so nothing in
  the house moves. It scores four things: heard right (word error rate against what you typed), for him
  right, did the right thing (tool and arguments), and time to the first word. A change ships when it
  beats the current score. `winston check brain` keeps doing the same for the brain on written requests.

## 2. Directory

One list of what is in his world, built from what Winston already reads, and used everywhere a name
has to mean something.

- **Entries.** A name, its aliases, its kind (light, scene, room, shortcut, artist, playlist, person,
  voice), the room it is in, and what it resolves to: an entity, a Spotify URI, a tool with arguments.
- **Sources.** Home Assistant's exposed entities, areas and aliases; rooms; shortcuts; contacts and
  cloned voices; his own name. Extensions add theirs through the SDK, growing `@ext.hint_words` from
  bare words into entries (Spotify: the artists and playlists in your library).
- **Used three ways.**
  - Hearing gets the names as context (part 3), so an artist in your library is expected before
    it is said.
  - The brain gets a short map of the house up front: rooms, what is in each, what each can do. A lamp
    named in the map needs no lookup.
  - For lists too long to put in front of the brain (a music library, all of Spotify), a lookup returns
    a few ranked candidates and the brain picks. Spotify's search returns tracks with their artists and
    popularity instead of playing the first of five results.

## 3. Hearing

Hearing stays a separate speech engine in front of the brain, and that engine is given context.

**Why not one model that hears and answers.** Researched 2026-10-07:

- **A weaker brain.** The best open model that hears and fits, Qwen3-Omni-30B-A3B, scores 6 on
  [Artificial Analysis's index](https://artificialanalysis.ai/models/qwen3-omni-30b-a3b-instruct).
  Qwen3.8-27B scores 20 with thinking off and 34 at xhigh. Qwen3.5-Omni and Qwen3.8-Omni-Flash are API
  only.
- **No better at "for him?".** Commands spoken by a bystander still got tool calls. Qwen3-Omni,
  Nemotron Omni and Step-Audio held back 0 to 1% of the time, and the best, Kimi-Audio, 14%
  ([arXiv 2609.32536](https://arxiv.org/html/2609.32536v1), 2026-09-26).
- **Not servable as needed.** No local server is confirmed to take audio and tools in one request, and
  none reads audio while you are still talking. Qwen3-Omni at 4 bits (18.6 GB plus 2.2 GB) does not fit
  next to the voice.
- **Little faster.** It would save 0.1 to 0.2 s at best, and the 0.8 s silence wait would stay.

Revisit when both hold: an open model that hears is at least as good a brain as the current one and
fits beside the voice (watch Qwen 4 27B), and a local server takes audio and tools in one request.

**A swappable slot, like the brain and the voice.** Today speech to text is the one slot built into
Winston: Parakeet runs inside the voice process and is the only choice. It becomes an engine like the
other two:

- **A protocol, not a vendor.** The provider is `openai_transcription`: OpenAI's
  `/v1/audio/transcriptions`, which vLLM, other local servers and hosted services speak. Per request it
  sends the audio, the context as the `prompt` field, and asks for the language back. Any model behind
  any server that speaks it works without code.
- **Run by the supervisor.** An `engines.stt` entry (Docker, process or external), started by
  `winston up` and shown with the others.
- **Presets and dropdowns.** Picked on the settings page the way the brain is: a preset per kind of
  machine, the model from a dropdown filled from the server, raw arguments behind Advanced. Presets to
  start: speech models on vLLM (NVIDIA), Parakeet built in (CPU, no server, the default for a machine
  without a GPU), and Hosted (a URL and a key).
- **Capabilities, not names.** An engine declares whether it takes context, reports the language, and
  streams. Fields that do not apply stay hidden, and Winston uses what is there: no context means the
  misspelling fixer stays on for that engine.
- **Compared like brains.** `winston check stt A B` runs two engines on the recorded turns and shows
  words wrong, names wrong, Swedish caught, and time per request.

The contract: `transcribe(audio, rate, context=None)` returns the text, the language, and a confidence
when the engine gives one. The context is the directory's names, the room, and the question he just
asked. The language goes to part 4.

**The pick.** Qwen3-ASR 1.7B leads, to be confirmed on the recorded turns before it replaces Parakeet:

| Model | Average WER (Open ASR) | Takes context | Hears Swedish as Swedish | Licence |
| --- | --- | --- | --- | --- |
| Parakeet TDT 0.6B v3 (today) | 6.34 | No | Yes, language not reported | CC-BY-4.0 |
| [Qwen3-ASR 1.7B](https://huggingface.co/Qwen/Qwen3-ASR-1.7B) (2026-01) | 5.76 | Yes: vLLM puts the `prompt` in its context turn (vLLM docs, 2026-10-03) | Yes, and reports it (98.7% on FLEURS) | Apache-2.0 |
| [Granite Speech 4.1 2B](https://huggingface.co/ibm-granite/granite-speech-4.1-2b) (2026-04) | 5.33, the best | Yes, a keyword list | No: six languages, no Swedish | Apache-2.0 |

Qwen3-ASR leads because the two things it adds are the ones the week was missing: names given in
advance, and Swedish recognised as Swedish so it can be left alone. Granite is scored beside it as the
best English-only engine. The accuracy gap to Parakeet on clean benchmarks is small; the gain is
expected from context, which only the recorded turns can show.

Room on the GPU: 3.7 GB is left beside the 27B brain (17.2 GB) and the voice (3.1 GB). Qwen3-ASR 1.7B
must be measured there; 0.6B is the fallback if it does not fit.

**End of speech.** The box waits for 0.8 s of silence, which is most of the 1.05 s (median) from the
end of speech to the text. A turn model decides instead, as in the browser, and is measured on the
recorded turns.

## 4. For him?

Every turn gets a decision before the brain sees it: for him, not for him, or unsure.

- **Signals.** The wake word (decides it alone). Whether the voice is the one that woke him, from the
  speaker model already loaded for voice cloning (WeSpeaker ResNet34). The language: the one you speak
  to him is for him, another (Swedish, here) is the household talking. Length: a 14 second stretch of
  chatter is not a request. Whether he just asked a question. The words themselves: a request, an
  answer, or talk between people.
- **The rule.** A wake word turn is for him. A follow-up needs the same voice and his language, and
  must read as a request or an answer. Unsure means silent: he does not act on a turn he is not sure was
  meant for him.
- **Allowed to say nothing.** "Oh," and Swedish get no "Yes, sir?". Not answering is a normal outcome,
  not a failure.
- **Tuned on marks.** The decision and its signals are in the turn record, so the thresholds are set on
  turns marked "I wasn't talking to you", not guessed.

## Order

1. Turn record, marks and `winston check turns`. Nothing else changes; then a week of recorded use.
2. Directory: the brain's map of the house, extensions' entries, Spotify candidates.
3. Hearing: the engines in the table scored on the recorded week, the best one picked; end of speech
   decided by a turn model.
4. For him: built and tuned on the marked turns.

## Settled defaults

- A mark by voice is confirmed with a short line ("Noted, sir."), so you know it was taken.
- Turns are kept from every channel with a microphone: the boxes, the browser and the wall panel.
