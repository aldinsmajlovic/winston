# Hardware and profiles

Winston's reference box is one NVIDIA card with 24 GB, but nothing in the core assumes it. Every
heavy part is an engine chosen in config, and a profile is one ready-made choice per slot for a
class of hardware. Pick one at `winston init --profile NAME`, or switch any slot later on the Models
page of the web UI. `winston init --list` prints the same table.

| Profile | Needs | Brain | Voice | Hearing |
|---|---|---|---|---|
| `nvidia-24gb` (default) | NVIDIA card with 24 GB, Docker with the NVIDIA container toolkit | vLLM in Docker, Qwen3.5-9B AWQ | Chatterbox-Turbo on the GPU, voice cloning from a clip | Parakeet on the CPU |
| `nvidia-24gb-27b` | The same | llama.cpp in Docker, Qwen3.8-27B GGUF at 4 bits | Chatterbox-Turbo on the GPU | Parakeet on the CPU |
| `ollama` | Ollama installed; any GPU, Apple silicon or a CPU | Ollama, `granite4.1:8b` | Pocket TTS on the CPU | Parakeet on the CPU |
| `hosted` | Internet and an API key | Any OpenAI-compatible endpoint | Pocket TTS on the CPU | Parakeet on the CPU |

Hearing is Parakeet TDT 0.6B v3 through ONNX on the CPU in every profile. Voice activity, turn
taking and the wake word are CPU work too. Home control never leaves the LAN in any profile; the
hosted profile sends only the conversation text to the model provider.

## Recommended on a 24 GB card

What the reference box (an RTX 3090) runs. Each slot was measured against what else fits beside the
other two:

| Slot | Pick | VRAM | Instead of | Why |
|---|---|---|---|---|
| Brain | Qwen3.8-27B at 4 bits on llama.cpp (`unsloth/Qwen3.8-27B-GGUF:UD-Q4_K_S`), MTP drafting, 32K context, thinking only before a home action | 17 GB | Qwen3.5-9B on vLLM | 42 of 42 brain trials against 36 of 42, first reply text in 0.94 s against 0.87 s. No vision. |
| Voice | Breeze TTS 2 (Voice preset `breeze2`) | 6 GB | Chatterbox-Turbo, streamed (3 GB) | Won 9 of 10 blind pairs (as an all-bf16 build); first sound in 0.18 s against 0.45 s ([voices](voices.md#breeze-tts-2-as-the-voice-10-october)). |
| Hearing | Parakeet TDT 0.6B v3, int8, on the CPU | none | Qwen3-ASR 0.6B (2.2 GB) | Qwen3-ASR hears better but does not fit beside Breeze. |

The card sits at 22.9 of 24.6 GB (the abliterated 27B and Breeze, 23 November). On the Voice PE box since 11 October, from the end of speech to
the first sound: 1.28 s median for a plain question (20 turns), 2.4 s when he uses a tool (39 turns),
and 2.3 s to confirm a home request.

No profile sets this up, since Breeze's weights are for research and non-commercial use only. Start
from `nvidia-24gb-27b`, clone its inference code with the command at the top of
`services/tts-breeze2/server.py`, and pick Breeze TTS 2 on the Voice page, which installs its
packages. The reference box also swaps in huihui-ai's abliterated build of the same 27B (refusals
removed, MTP layer kept): the same brain trials and speed, 0.25 GB more.

**With 2 GB more** (a 32 GB card, or a second GPU for hearing), add Qwen3-ASR 0.6B. Against
Parakeet it got a hint word right 9 times of 9 against 6, and had 6.3% word errors against 18.3%
with other people talking in the background, for 0.09 s more per request
([voices](voices.md#hearing-engines)).

## Measured

| | `nvidia-24gb` (2026-09-23) | `ollama`, GPU already full (2026-09-23) | `ollama`, GPU to itself (2026-11-23) |
|---|---|---|---|
| End of speech to first audio | 1.2 to 1.3 s median (measured with the 27B brain, streamed TTS) | not measured by voice | not measured by voice |
| Brain trials, thinking off | 36 of 42 | not run | 26 of 42 |
| Brain trials, thinking on | not run | not run | 11 of 14 |
| Tool call, thinking off | 0.9 s | 3 s with the 4B model split between CPU and GPU | 0.63 s to the first reply text (median) |
| Tool call, thinking on | 3.5 s | 8 s, same split | 1.84 s to the first reply text (median) |
| TTS first audio | about 330 ms at any sentence length, streamed | 60 ms, streamed while it generates | not measured |
| TTS real-time factor | 0.3 on the GPU | 0.23 on four CPU threads (Ryzen 5800X) | not measured |
| Brain memory | 16 GB of VRAM | 8 GB for the 4B model with a 32K context | 5.0 GB of VRAM with a 32K context |

The middle column comes from a box whose GPU was already holding the vLLM stack, so Ollama put half
the model on the CPU. The right column is the same RTX 3090 with nothing else on it: Ollama 0.40.17,
`qwen3.5:4b` (Q4_K_M) entirely on the GPU at Ollama's default 32K context, measured with
`tests/brain_trials.py` (`--runs 3` with thinking off, then once with `--think`). With the card to
itself the 4B is as fast as the vLLM 9B, but it gets far fewer requests right; see below.

## Comparing brains

`winston check brain` runs the requests in `data/trials/brain.yaml` (template in
`examples/trials/brain.yaml`) through the text channel with thinking off, as over voice, each in a
fresh conversation. A case passes when every expected tool was called and no forbidden one was;
lights the cases touch are put back. `--runs 3` repeats the set, results
land in `data/logs/brain_trials/`. Switch the brain on the Brain page, run it again, compare.

On the reference box (2026-09-23), 14 requests, three rounds each:

| | Qwen3.5-9B AWQ, vLLM | Qwen3.8-27B Q4_K_S, llama.cpp |
|---|---|---|
| Passed | 36 of 42 | 42 of 42 |
| Missed every round | a light change plus a phone reading in one request (asked about the lights, never read the phone); a fun fact sent to web search | none |
| First reply text, median | 0.87 s | 0.94 s with MTP (1.24 s without) |
| Tokens per second | not measured | 60 to 80 with MTP (44 without) |
| VRAM | 15.6 GB (vLLM takes 70 % of the card) | 17.2 GB |
| Start | about 3 minutes | 10 s once downloaded |
| Vision | yes | not in this preset |

Over voice, before Chatterbox streamed, both landed at about 2.2 to 2.5 s from the end of speech to
first audio on the same five questions, most of it Chatterbox rendering the first sentence whole. With
streaming the 27B measures 1.2 to 1.3 s median. The 9B also adds 8 to 9 s turns when it web-searches
trivia.

### Small brains on Ollama (2026-11-23)

The same 14 requests, three rounds with thinking off and one with it on, each model alone on the
RTX 3090 through Ollama 0.40.17. The 3B and 4B ran at Ollama's default 32K context, the 8B and 9B at
16K, which fits a 12 GB card next to the prompt. No Qwen newer than 3.5 comes below 27B; the others
were picked as the newest models of their size on Ollama with tool support. Until this day the trial
file expected the sensors tool by its name before 12 October, so the reading cases failed for every
model; the numbers below are scored from the logged calls against the current name.

| Model | Passed, off | Passed, on | First reply text, off / on | VRAM (context) |
|---|---|---|---|---|
| `granite4.1:8b` (the preset since) | 39 of 42 | no thinking mode | 0.80 s | 8.0 GB (16K) |
| `ministral-3:8b` | 33 of 42 | no thinking mode | 0.53 s | 8.3 GB (16K) |
| `nemotron-3.5-bolt:e4b` | 31 of 42 | 10 of 14 | 0.08 s (see below) / 4.46 s | 8.4 GB (32K) |
| `mimo-v2.6:9b` | 27 of 42 | 12 of 14 | 0.33 / 1.21 s | 7.1 GB (16K) |
| `qwen3.5:4b` (the preset until this day) | 26 of 42 | 11 of 14 | 0.52 / 1.84 s | 5.0 GB (32K) |
| `bonsai-2:9b` | 22 of 42 | 13 of 14 | 0.32 / 1.84 s | 4.2 GB (16K) |
| `granite4.1:3b` | 21 of 42 | no thinking mode | 0.48 s | 5.1 GB (32K) |
| `qwen3.5:9b` | 17 of 42 | 14 of 14 | 0.32 / 2.24 s | 7.1 GB (16K) |
| Qwen3.8-27B abliterated, llama.cpp v1.0.0 (that evening, after the prompt cuts) | 39 of 42 | | 0.79 s | 17.3 GB |

How they fail:

- **Replying as if done, with no tool called**: the Qwen 9B and Bonsai with thinking off, on nearly
  every light request and follow-up (3 of 3 each); the Qwen 4B 4 times. With thinking on, both 9Bs
  stop doing it, which is why they gain the most from it.
- **Something he should decline**: asked to lock or unlock a door, Granite (both sizes), Nemotron
  and the Qwen 4B tried, 3 of 3 each. That home has no lock, so nothing happened. Granite 8B's only
  misses are these three. Run again that evening, with the lock wording left out of the tools of a home
  without a lock, Granite 8B still tried, 3 of 3, through HassTurnOn instead of HassTurnOff.
- **The clock**: every model but Granite, the 27B too, answered the time from the turn's timestamp instead of the
  clock tool, a soft miss.
- **Ministral** used the wrong tool for a light asked for by name (3 of 3). **Granite 3B** gave an
  empty reply for the weather and answered a recent event from memory instead of searching (3 of 3
  each). **Nemotron** says "There you go, sir" before its tool call runs, sometimes before an action
  that then fails, which is why its first reply text comes so early.

One round with thinking on is 14 requests, so those columns are a first look, not a ranking.

## Choosing

- **NVIDIA with 24 GB, and multi-part requests matter more than vision:** `nvidia-24gb-27b`.
- **NVIDIA with 16 GB or more:** `nvidia-24gb`. Below 24 GB, lower `--gpu-memory-utilization` in
  the vLLM arguments or pick a smaller model; the Chatterbox server needs 3 GB next to it.
- **A smaller card, AMD, Apple silicon, or no GPU:** `ollama`. Ollama handles the hardware itself.
  Pull any model whose Ollama page lists `tools`; the default is `granite4.1:8b`, and `qwen3.5:4b`
  on a card with less than about 8 GB free. Expect less than the 24 GB profiles: the 4B passed 26 of 42 brain trials against the 27B's 42.
  On a 12 GB card an 8B fits at 16K context: `granite4.1:8b` passed 39 of 42 at 0.8 s and 8 GB
  ([small brains](#small-brains-on-ollama-2026-11-23)).
- **No local model:** `hosted`. Put the key in `data/.env` as `LLM_API_KEY`, set `llm.model` to
  the name your provider uses, and check the base URL. Thinking control is `none`, since hosted
  APIs have their own switches.

Two engines on one card must both fit at start: vLLM asks for its whole share up front and fails if
another process holds it, and Ollama keeps its last model loaded for five minutes after a request.
Trying the Ollama profile next to a running vLLM stack means waiting those minutes, or `ollama stop`,
before vLLM starts again.

Mixing is normal. A brain that fills the whole card (Qwen3.6-35B-A3B at 4 bits is 24 to 25 GB, or
the 27B through vLLM at 19.5 GB) needs Pocket TTS on the CPU: pick the brain's preset on the Brain
page and the Pocket preset on the Voice page.

## Notes per engine

**vLLM.** Fastest tool calls and the only path with vision. The tool-call and reasoning parser
flags in the preset are for Qwen models; another family needs its own parser (`vllm serve --help`
lists them). Winston downloads the weights into `~/.cache/huggingface` before the first start, with
progress on the Brain page and Status; a start then takes about 3 minutes.

**llama.cpp.** The official `server-cuda` image, pinned to a release (`server-cuda-v1.0.0-b12359-b1b770d`
since 18 November), reading GGUF files from
`~/.cache/huggingface` (the same cache as vLLM). `llm.model` is a Hugging Face repo and quant
(`unsloth/Qwen3.8-27B-GGUF:UD-Q4_K_S`); the preset passes it as `-hf` and as the served name. Winston
downloads that quant's files into the cache before the first start, where llama.cpp finds them. It runs two conversations at once with 16K of context each and a q8 cache,
and drafts with the model's own MTP layer (`--spec-type draft-mtp`), which needs a GGUF that keeps
that layer; remove those two arguments for one that does not. Thinking is switched with the same
`enable_thinking` template flag as vLLM. The system prompt with its tools was about 9.4k tokens in
October (12.3k by late November, 11.1k after the cuts of 23 November), and the 27B reads it cold in 7.5 to 10.6 s, so Winston has it read ahead: a Voice PE box when it connects
and again when the prompt changes while it is idle, the browser voice channel when you connect. The
first real turn then starts from the cache in about 0.15 s. On the v1.0.0 image the 27B writes 87
tokens a second (median, 23 November) with 80% of its drafts accepted, against 67 and 59% on the
October build. That image leaks host memory (llama.cpp #31392, open): the container grew from 1.9 to
6.4 GB over 182 requests, at most 2 GB of it the prompt cache (`--cache-ram 2048`).

**Ollama.** Speaks the OpenAI protocol at `/v1`. Thinking is switched per request with
`reasoning_effort`, which is what `llm.thinking.control: reasoning_effort` sends. Ollama ignores
`tool_choice`, which Winston does not use. Ollama gives a card under 24 GB a 4K context, and
Winston's system prompt with its tools is about 11k tokens, which its OpenAI API cannot change per
request. So when the brain starts, Winston makes a copy of the model, one you pulled yourself too, with a 16K context (`granite4.1:8b-16k`,
the same files, no extra space) and talks to that; Ollama's own docs give the same fix. The Brain
page lists the model, not the copy.

**Chatterbox-Turbo.** CUDA only, its own venv (`services/tts-chatterbox`) because it pins torch 2.6.
Clones any clip under `data/voices`; six CC BY 4.0 VCTK samples come bundled. Chatterbox itself
renders a sentence whole, at about 25 ms per character, so our server streams: it generates the
speech tokens one at a time and vocodes what it has in chunks, sending only the new audio with a 20 ms
cross-fade. First audio arrives in about 330 ms whatever the sentence length. The joins can sound a
little choppy, so it is a setting: under Voice, Streaming turns it off (whole sentences) or makes the
chunks longer (fewer joins, same start time, 1.6 s by default, up to 6 s); the first chunk (0.96 s)
and the look-ahead held back at each join (0.32 s) are set in config.yaml. Chunks double from the first up to
the chosen length, so playback never catches up at any length (measured at 1.6, 3.2 and 6 s).

**Pocket TTS.** Kyutai's 100M model, MIT code and CC BY 4.0 weights, its own venv
(`services/tts-pocket`) with a CPU-only torch. Streams audio as it generates. 26 built-in voices
work out of the box. Cloning from a clip needs the gated weights: accept the terms at
https://huggingface.co/kyutai/pocket-tts, run `uvx hf auth login` once, and the same clips as
Chatterbox work. Its `--threads` argument defaults to four.

**Hosted speech.** Any server speaking the OpenAI speech protocol, key in `TTS_API_KEY`; the preset
is OpenRouter with ElevenLabs Flash v2.5 ([voices](voices.md#hosted-voice)). The built-in voices
dropdown then shows nothing, since there is no local server to ask; type the name.

## What is not covered

vLLM on AMD (ROCm images exist, untested here), Windows, and a brain smaller than 4B. Parakeet's
INT8 ONNX build wants a few CPU cores; on a Raspberry Pi class satellite the plan keeps hearing on
the brain box, not the satellite.
