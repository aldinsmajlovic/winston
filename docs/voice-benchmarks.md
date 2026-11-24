# Voice benchmarks

A record of what was measured on Winston's voices and hearing, run by run, with the date of each run
so a number can be traced to it. Which voice to pick today, and how they compare, is on
[Voices](voices.md).

## Running a benchmark

A voice to replace or sit beside Chatterbox-Turbo has to sound more human, start talking as soon,
and fit on the card next to the 27B brain: about 6.3 GB once Chatterbox (3.6 GB) is stopped, since
the brain keeps 16.9 GB and 2.8 GB is free while both run.

`tests/tts_bench/` measures any speech server on Winston's own lines in the same three voices and
builds a blind A/B listening page against Turbo; [its README](../tests/tts_bench/README.md) is the
run procedure. Each candidate runs in its own venv as a small engine on the shared speech server
(`services/speech_server.py`) and is a Voice preset as well; `tests/tts_bench/candidate.sh` runs one
at a time.

## Speaking engines

### Candidates, 24 September

| Candidate | Licence | Weights | Clones from | Streams | Out |
| --- | --- | --- | --- | --- | --- |
| Qwen3-TTS 1.7B Base (`qwen-tts` 0.1.1) | Apache-2.0 | 4.3 GB | a clip and its words | frame by frame, hooked in the server | 24 kHz |
| VoxCPM2 (`voxcpm` 2.0.3) | Apache-2.0 | 4.7 GB | a clip and its words | yes, the package's own | 48 kHz |
| Gepard 1.1 and 1.0 (nineninesix) | Apache-2.0, codec under NVIDIA's Open Model License | 1.1 GB each, codec 0.4 GB | a clip alone | frame by frame, hooked in the server | 22.05 kHz |
| Pocket TTS (`services/tts-pocket`) | MIT code, CC BY 4.0 weights | 0.4 GB | a clip, with Kyutai's gated weights | yes | 24 kHz, CPU |

- **Qwen3-TTS.** Its package returns audio only at the end; the server takes each 80 ms frame from
  the model as it is made and decodes as it goes, with the clip's own frames as left context. It has
  no tags; its 0.6B sibling was left out for quality. The package was last released in February.
- **VoxCPM2** is the largest; upstream lists about 8 GB of VRAM, so it might not fit. It has no
  bracket tags either, but takes a style in parentheses.
- **Gepard** leads its own naturalness table (NISQA 4.25 against Chatterbox 4.19 on Seed-TTS-eval)
  but trails on speaker similarity (0.59 against 0.80), so it may sound natural and less like the
  clip. 1.1 drops and invents fewer words than 1.0 at the same quality. Upstream serves it with vLLM;
  the server runs the plain transformers loop, so its speed here is a floor.
- **Chatterbox-Flash** was left out: by Resemble's own numbers it is faster than Turbo, not more
  natural.

On the CPU (a 5800X, while the live engines held the GPU) all three new servers loaded, cloned
`vctk/p254_023` and spoke streamed and whole, and Parakeet heard the words back: Gepard at a
real-time factor of about 1.1 with the first audio after 0.75 s, Qwen3-TTS at about 6 and VoxCPM2 at
about 20. These prove the servers, not the GPU speed the benchmark is for.

### Results, 24 September

Run on the RTX 3090 next to the 27B brain (the live Chatterbox stopped for the GPU candidates), 20 of
Winston's lines in three voices; first sound is when a listener would hear him start. Then 36 blind
A/B pairs against Turbo as it ran (unstreamed), judged by Aldin.

| Engine | First sound | Word errors | GPU memory | Preferred over Turbo |
| --- | --- | --- | --- | --- |
| Chatterbox-Turbo, unstreamed | 0.96 s | 3.1% | 4.0 GB | (the baseline) |
| Chatterbox-Turbo, streamed | 0.45 s | 3.4% | 4.2 GB | 4 of 6 |
| Pocket TTS, built-in voice george | 0.71 s | 2.1% | none (CPU) | 4 of 6 |
| VoxCPM2 | 0.87 s | 3.6% | 6.9 GB | 3 of 6 (1 same) |
| Qwen3-TTS 1.7B | 0.93 s | 3.6% | 6.5 GB | 2 of 6 |
| Gepard 1.0 | 0.46 s | 9.4% | 2.2 GB | 1 of 6 (1 same) |
| Gepard 1.1 | 0.49 s | 14.4% | 2.2 GB | 0 of 6 |

Turbo stayed. Streamed, it halves the wait and was preferred over its unstreamed self, so the joins
are not a problem. No candidate was clearly more human:

- VoxCPM2 edges it but leaves the card at 24.0 of 24.6 GB.
- Qwen3-TTS runs slower than real time (RTF 1.25).
- Gepard drops and garbles words.
- Pocket won with a different voice, so its pairs compare voices more than engines; they suggest the
  voice Winston speaks in matters more than the engine.

Six pairs per engine is a leaning, not proof.

### Breeze TTS 2, 8 October

Breeze TTS 2 (BreezeBlue, 3B, August 2026) led the open-weights models on Artificial Analysis's
Speech Arena (1215 Elo, above ElevenLabs v3). Its weights are for research and non-commercial use
only, which a home assistant is; its inference code (Apache-2.0) is not a package, so
`services/tts-breeze2` clones it at a pinned commit. It clones from a clip and its words, streams
itself and takes tags in parentheses, "(laugh)", which the server writes Chatterbox's "[laugh]" into.
It was a benchmark candidate until it became the voice
([10 October](#breeze-tts-2-as-the-voice-10-october)).

Same 20 lines and three voices, run with the brain, voice and hearing stopped, since its bf16 weights
alone ran out of the 6.7 GB the voice and hearing leave:

| Engine | First sound | Real-time factor | Word errors | GPU memory |
| --- | --- | --- | --- | --- |
| Chatterbox-Turbo, streamed (24 September) | 0.45 s | 0.34 | 3.4% | 4.2 GB |
| Breeze TTS 2, eager (3 lines a voice) | 0.44 s | 2.16 | 0% | 8.6 GB |
| Breeze TTS 2, `--fast backbone_decode depth_decoder codec` | 0.15 s | 0.44 | 1.9% | 9.1 GB, 13.7 GB while capturing |
| Breeze TTS 2, the same with `--int4`, beside the brain | 0.20 s | 0.43 | 5.1% | 3.8 GB |

Eager it is twice as slow as it speaks, so a streamed reply would stall after its first second.
With its per-frame decoding captured as CUDA graphs it starts sooner than Turbo and makes the fewest
word errors of any GPU candidate. Its misses: "in ancient Egyptian tombs" said as "ancient
Egyptians" in all three voices, and "4500" once read as "four five hundred". In 10 blind pairs
against streamed Turbo Aldin preferred it 9 times.

**In 4 bits.** In bf16 it needs about twice the 4.3 GB the voice has beside the 27B and Qwen3-ASR.
`--int4` fits it there with 0.4 GB of the card to spare:

- 4-bit backbone and text encoder (torchao);
- the text lookup tables and the unused codec copy in RAM;
- the depth decoder kept in bf16;
- graphs captured only for the one guidance branch in use.

It is as fast, but its word errors went from 1.9% to 5.1%. Part of that is how Parakeet writes
numbers ("58%"), part is real: "Warmed them up a touch" heard as "Warned the Mapatouch" in one voice,
"[chuckle] Glad that amused you" garbled in two of three. In 10 blind pairs against streamed Turbo it
tied, 5 to 5 (Turbo won 3 of 4 in clifton_fatherbrown, the voice Winston used), so 4 bits cost what
made it better.

Which part costs the words, beside the brain with its context cut to 24k (60 lines each):

| `--backbone-bits` | `--text-encoder-bits` | `--int4-group` | Word errors | RTF | GPU memory |
| --- | --- | --- | --- | --- | --- |
| 4 | 4 | 128 | 5.1% | 0.43 | 4.1 GB |
| 4 | 8 | 128 | 5.0% | 0.41 | 4.0 GB |
| 4 | 8 | 32 | 4.1% | 0.39 | 4.1 GB |
| 8 | 8 | | not scored; captures since October, 27% slower than bf16 ([11 October](#starting-sooner-11-october)) | | |

The text encoder in 8 bits changes nothing, so the 4-bit backbone is what garbles words; finer
scales win back about a point. An 8-bit backbone would need its own capturable layer and about
4.65 GB, against the 4.55 GB free beside the 27B at 24k and Qwen3-ASR. The finer 4-bit build won 6 of
10 blind pairs against streamed Turbo: all 6 in the two VCTK voices, none of the 4 in
clifton_fatherbrown.

**Chaining sentences.** Aldin heard the voice change at a full stop in some of the pairs he did not
pick. Every sentence is a generation of its own: the speech server splits a request at sentence
ends, and Winston sends a reply a sentence at a time anyway, so each sentence starts again from the
clip, and Breeze's take on it differs more than Turbo's.

`--chain-secs` (30 s by default) gives a sentence the one before it in the same voice, its words and
frames after the clip's, as its reference, so it carries on from it; the clip stays first so the
voice cannot drift. Chained, first sound is 0.22 s (the longer reference) and word errors 4.8%,
within the noise of 4.1%.

Whether it stops the change was a blind run on the four lines of more than one sentence
(`listen.py --baseline breeze2-b4g32t8 --candidates breeze2-chain --lines s15,s16,s18,s20 --pairs 12`):
chained won 7 of 12, every pair of s20 and none of s16, so it helps some lines and is no clear fix.

Turbo stayed. The Breeze that fits beside the brain is about even with it, at the cost of a nearly
full card, a smaller brain context and weaker results in the voice Winston used; only near full
precision (about 6.5 GB) is it clearly better, which would mean giving up Qwen3-ASR's hearing.

### Breeze TTS 2 as the voice, 10 October

Aldin chose the near full precision Breeze over Qwen3-ASR's hearing, so the Voice page got a Breeze
TTS 2 preset and hearing went back to Parakeet. The preset keeps the backbone and depth decoder in
bf16 and puts only the text encoder in 8 bits (`--text-encoder-bits 8`, which changed nothing in the
4-bit runs). That builds the model in RAM and leaves the text lookup tables and the unused codec copy
there, so it took 5.9 GB on the card instead of the all-bf16 build's 9.1 GB. With Qwen3-ASR stopped,
the 27B at 32k leaves 6.5 GB. Sentences are chained as above.

Measured as it runs, on port 8880 beside the brain (`bench.py breeze2-live=http://127.0.0.1:8880`,
same 20 lines and three voices):

| Engine | First sound | Real-time factor | Word errors | GPU memory | Card at its peak |
| --- | --- | --- | --- | --- | --- |
| Chatterbox-Turbo, streamed | 0.45 s | 0.34 | 3.4% | 4.2 GB | 21.3 GB |
| Breeze TTS 2, the preset | 0.18 s | 0.44 | 3.5% | 6.1 GB | 24.0 GB of 24.6 |

It starts in less than half Turbo's time. Its word errors are more than the 1.9% of the all-bf16
build, and chaining is part of that: in the 4-bit runs it went from 4.1% to 4.8%. The card is left
with about 0.6 GB at its peak, so a bigger brain context or a GPU hearing engine does not fit beside
it. Chatterbox stays a preset; picking it and Qwen3-ASR 0.6B on the Voice page goes back.

**The pause at a full stop.** Aldin heard the next sentence start right on the last one's final
sound. Breeze ends a sentence about 0.04 s after its last sound, against Turbo's 0.16 s, and Winston
sends a reply a sentence at a time, so its pauses at full stops were mostly under 0.1 s (Turbo's 0.16
to 0.4 s). The server's `--pause-secs` adds 0.25 s of silence after each sentence by default, which
makes a full stop 0.30 to 0.32 s.

Measured again with the pause (`breeze2-pause`): first byte 0.14 s, first sound 0.18 s (slowest
tenth 0.26 s), RTF 0.41. The slowest lines start with "[sigh]", which Breeze breathes quietly before
the first word. Word errors read 5.8%, but the two lines Parakeet heard as nothing were said right:
moving the clip's end by a few hundredths of a second, it hears them whole. That is the
measurement's noise, not the voice.

### Starting sooner, 11 October

Two holds in Winston's own client came before every first sound, on the box and in the browser:

- **Half a second of audio first.** Pipecat reads the speech server's stream in 0.5 s pieces, and
  httpx yields nothing until a piece is full, so the first sound waited 220 to 250 ms past the
  server's first byte. `SpeechTTS` now reads 0.24 s, three of Breeze's 80 ms frames. Two frames ran
  dry while the brain read a 2000-token prompt beside it.
- **The second sentence.** Pipecat's sentence splitter waits for the next letter after a full stop,
  and behind the reply filter that letter came only with the next sentence, so the first sentence
  was spoken once the second was written, often at the end of the reply. `ReplySentences`
  (core/reply.py) speaks each sentence the filter passes on at once; NLTK still keeps "Mr." with its
  name and "10.6" whole. Over half of the box's replies have more than one sentence, and they gain
  150 to 400 ms.

On the fake box, from the transcript to the first audio, the same requests went from 619 to 297 ms
("One moment." on a home request), 655 to 348 ms and 1381 to 693 ms.

An audit the same day took back three changes made earlier that day:

- The pause after a sentence is 0.25 s again: 0.12 s shortened the full stops and started nothing
  sooner, since the next sentence's audio is ready before the pause ends.
- The backbone goes back to bf16: in 8 bits it generated 27% slower (0.57 against 0.45 s per second
  of speech, first byte 164 against 154 ms), and nobody had listened to it.
- The brain's value cache goes back to q8_0: q4_0 freed 192 MB, not the 370 to 750 MB claimed, and
  gave no speed on this card.

### Sounding like a person, 12 October

With Breeze as the voice, spoken channels get a line in the prompt (`core/brain.py:_vocal_events`)
saying the voice turns `[sigh]`, `[laugh]`, `[cough]` and `[clears throat]` into sounds. Asked to use
them "sparingly", the 27B wrote none: 0 of 159 turns on the box on 10 to 12 October, 0 of 28 chatty
test replies. The line now gives when each fits, two examples, and "most conversational replies have
room for one".

Fillers are the persona's: "Hm,", "Well,", "Oh," now and then, an "uh" where a word is reached for.
On the same 14 requests twice, thinking off: a tag in 6 of 28 (none on "Thanks, that's all"), an
opener or filler in 11 of 28. An "uh" or "um" is still rare (1 of 28 at best); asking harder made him
open every reply with "Oh," or "Well," and copy the example word for word.

At guidance 1.0 (Breeze's default, one branch) a tag takes most of the time: the same line with
`[sigh]` or `[clears throat]` ran 0.7 to 1.4 s longer in 6 of 6 tries, with `[laugh]` in 1 of 3.
Upstream says a guidance scale above 1 makes tags more reliable; it runs two branches, and the card
has about 1 GB free, so it was not tried.

The words themselves read as written: more "it is" and "I am" than contractions (19 to 13), 11 stock
lines ("sorry to hear that", "did you know", "truly") and 6 of 64 sentences under five words. Two
rules went into the persona (`examples/persona.md` has them):

- speak, don't write: always contract, mix a two-word sentence with a longer one, fragments and
  asides are fine;
- skip the stock lines, reacting to the thing itself instead.

Same 28 replies: 0 uncontracted to 42 contracted, 6 stock lines, median sentence 9 words instead of
12. An example reply in the rule was copied nearly word for word ("Dead again? You and that code,
sir."), so the rule describes the reaction instead of showing one.

### Room on the card, 15 October

From 12 October Breeze went silent in the clip voice now and then: a story, a briefing, a good night
story, every sentence an error. The abliterated 27B took 250 MB more than unsloth's, and preparing a
voice (encoding its 20 s clip to codec frames, once per voice per start) needs 407 MB more for a
moment. That did not fit beside the desktop, and a voice that failed to prepare was tried again on
every sentence. Short replies in the default voice, prepared at start, still played.

The codec (fp32, 586 MB) was split:

- Its encoder runs on the CPU: 0.9 s instead of 0.6 s per voice, no spike, and its codes match the
  card's exactly once the card's TF32 convolutions are off (with them on, 2.8% of the codes
  differed, all in the fine codebooks).
- The decoder runs in fp16: on the same codes its output is 52 dB below fp32's, where the codec's
  own reconstruction is 6.3 dB from the clip; bf16 was 34 dB and was not used.
- The server also sets torch's expandable segments.

| Breeze server, 27B card | GPU memory, steady | Preparing a voice |
| --- | --- | --- |
| Before | 5944 MB | 407 MB more |
| Encoder on the CPU, decoder in fp16 | 5480 MB | nothing more |
| Plus expandable segments | 5380 MB | nothing more |

With Winston up the card went from 24092 to 23387 of 24576 MB. Tried on the brain and not kept: an
8-bit draft cache for MTP (its cache 128 to 68 MB, its compute buffer 114 to 224 MB, a net loss) and
`-ub 256` (48 MB); both gave the same text at the same speed. The brain's 17.1 GB is 14.4 GB of
weights, 1.1 GB of attention cache, 0.9 GB of recurrent state for the two slots, and buffers.

### ZONOS3-Mini, 24 November

[ZONOS3-Mini](https://huggingface.co/Zyphra/ZONOS3-Mini) (`services/tts-zonos3`) against the Voice
preset and Chatterbox-Turbo streamed, each alone on the RTX 3090: 20 of Winston's lines in each of
three cloned voices (`tests/tts_bench/candidate.sh`).

| | Breeze TTS 2 (Voice preset) | Chatterbox-Turbo | ZONOS3-Mini |
| --- | --- | --- | --- |
| First sound, median | 0.18 s | 0.45 s | 0.46 s |
| Real-time factor | 0.38 | 0.35 | 0.07 |
| Word errors | 3.0% | 2.9% | 1.8% |
| GPU memory, peak | 5.5 GB | 4.1 GB | 19.0 GB |

ZONOS3 ran out of memory at its own default, half the card for its cache; it ran with
`--memory-ratio 0.12` and still peaked at 19 GB, so it does not fit beside the 27B brain.

In a blind ranking of 12 lines (ties allowed, the same loudness and the silence at each end trimmed),
Aldin's average places were Breeze 1.42, ZONOS3 1.75 and Chatterbox 2.00. In the Voice preset's own
clip the three tied in three of four rounds. Breeze stays the voice.

## Reference clips

### Clarity in noise, by reference voice, 8 October

Chatterbox-Turbo streamed: ten of Winston's lines in each clip in `vctk/` and `librivox/`, then mixed
with four other men talking (10 and 5 dB under the voice) and a fan-like hum (5 and 0 dB), with
Parakeet as the listener. Word errors averaged over the four mixes, 21 voices:

| Voice | In noise | Clean |
| --- | --- | --- |
| `vctk/p232_023` | 16.6% | 3.3% |
| `librivox/barnes_jekyll` | 18.6% | 4.2% |
| `librivox/yearsley_sussex` | 19.4% | 2.5% |
| `vctk/p254_023` (the presets' default) | 19.6% | 2.5% |
| `librivox/clifton_fatherbrown` | 27.9% (ninth) | 2.6% |
| `librivox/yearsley_ghost` (last) | 52.1% | 0.0% |

Every voice is clear in quiet; noise is where they part. Ten lines per voice is a leaning: a gap
under about five points is noise.

### VoxCPM2's denoise, 8 October

On `vctk/p254_023`, five lines each, with and without `--denoise`:

| Reference clip | Without | With `--denoise` |
| --- | --- | --- |
| Clean | voice match 0.87, 3% word errors | 0.82, 0% |
| A fan-like hum 10 dB under the voice | 0.73, 0%, the hum copied into his voice | 0.73, 0%, no hum |
| Four other men talking 10 dB under it, and the hum | 0.63, 8%, the room copied in | 0.62, 41% |

So it removes a hum or a hiss, garbles a clip with other voices in it, and blurs a clean one a
little; the preset leaves it off.

## Hearing engines

### Parakeet against Qwen3-ASR 0.6B, 8 October

Six requests in three VCTK voices, the brain and Chatterbox running, with a made-up scene name
("Zephyrine") as a hint word. Parakeet's figures in brackets are after the misspelling fix.

| | Parakeet | Qwen3-ASR 0.6B |
| --- | --- | --- |
| Clean | 3.0% word errors (1.6%) | 2.2% |
| Four other men talking 10 dB under the voice | 18.3% (15.5%) | 6.3% |
| A fan-like hum 5 dB under it | 13.8% (12.4%) | 4.5% |
| The hint word right | 6 of 9 | 9 of 9 |
| Time per request | about 0.2 s | about 0.29 s |

### Parakeet against Fluid Speech Pro, 24 November

Both int8 on the CPU through onnx-asr 0.12.0, 150 FLEURS sentences per row. Talkers are four other
FLEURS voices 10 dB under the speaker; the hum is 50 to 200 Hz with brown noise, 5 dB under.

| | Parakeet | Fluid Speech Pro |
| --- | --- | --- |
| English, clean | 6.5% word errors | 5.3% |
| English, four other people talking | 19.8% | 5.4% |
| English, under a hum | 6.8% | 5.2% |
| Swedish, clean | 20.3% | 14.0% |
| Time per request, 14 recorded box turns (median) | 0.21 s | 0.40 s |

On the box turns, with no reference text, the two transcripts were shown side by side, blind. Of six
that differed, Fluid had two right that Parakeet had wrong (a one-word "Stop." that Parakeet wrote as
"S.", and a dropped "to"), Parakeet had one right that Fluid misheard ("up" as "off"), and neither had
the one in Swedish right.
