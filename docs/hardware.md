# Hardware and profiles

Winston's reference box is one NVIDIA card with 24 GB, but nothing in the core assumes it. Every
heavy part is an engine chosen in config, and a profile is one ready-made choice per slot for a
class of hardware. Pick one at `winston init --profile NAME`, or switch any slot later on the Models
page of the web UI. `winston init --list` prints the same table.

| Profile | Needs | Brain | Voice | Hearing |
|---|---|---|---|---|
| `nvidia-24gb` (default) | NVIDIA card with 24 GB, Docker with the NVIDIA container toolkit | vLLM in Docker, Qwen3.5-9B AWQ | Chatterbox-Turbo on the GPU, voice cloning from a clip | Parakeet on the CPU |
| `nvidia-24gb-27b` | The same | llama.cpp in Docker, Qwen3.8-27B GGUF at 4 bits | Chatterbox-Turbo on the GPU | Parakeet on the CPU |
| `ollama` | Ollama installed; any GPU, Apple silicon or a CPU | Ollama, `qwen3.5:4b` | Pocket TTS on the CPU | Parakeet on the CPU |
| `hosted` | Internet and an API key | Any OpenAI-compatible endpoint | Pocket TTS on the CPU | Parakeet on the CPU |

Hearing is Parakeet TDT 0.6B v3 through ONNX on the CPU in every profile. Voice activity, turn
taking and the wake word are CPU work too. Home control never leaves the LAN in any profile; the
hosted profile sends only the conversation text to the model provider.

## Measured (2026-09-23)

| | `nvidia-24gb` | `ollama` on the same box, GPU already full |
|---|---|---|
| End of speech to first audio | 1.2 to 1.3 s median (measured with the 27B brain, streamed TTS) | not measured by voice |
| Tool call, thinking off | 0.9 s | 3 s with the 4B model split between CPU and GPU |
| Tool call, thinking on | 3.5 s | 8 s, same split |
| TTS first audio | about 330 ms at any sentence length, streamed | 60 ms, streamed while it generates |
| TTS real-time factor | 0.3 on the GPU | 0.23 on four CPU threads (Ryzen 5800X) |
| Brain memory | 16 GB of VRAM | 8 GB for the 4B model with a 32K context |

The Ollama numbers come from a box whose GPU was already holding the vLLM stack, so Ollama put
half the model on the CPU. On a card with room, expect it to be closer to the vLLM column.

## Comparing brains

`winston check brain` runs the requests in `data/trials/brain.yaml` (template in
`examples/trials/brain.yaml`) through the text channel with thinking off, as over voice, each in a
fresh conversation. A case passes when every expected tool was called, no forbidden one was, and the
claim guard stayed quiet; lights the cases touch are put back. `--runs 3` repeats the set, results
land in `data/logs/brain_trials/`. Switch the brain on the Brain page, run it again, compare.

On the reference box (2026-09-23), 14 requests, three rounds each:

| | Qwen3.5-9B AWQ, vLLM | Qwen3.8-27B Q4_K_S, llama.cpp |
|---|---|---|
| Passed | 36 of 42 | 42 of 42 |
| Missed every round | "change the temp of the lights and check my phone battery" (asked about the lights, never read the battery); a fun fact sent to web search | none |
| First reply text, median | 0.87 s | 0.94 s with MTP (1.24 s without) |
| Tokens per second | not measured | 60 to 80 with MTP (44 without) |
| VRAM | 15.6 GB (vLLM takes 70 % of the card) | 17.2 GB |
| Start | about 3 minutes | 10 s once downloaded |
| Vision | yes | not in this preset |

Over voice, before Chatterbox streamed, both landed at about 2.2 to 2.5 s from the end of speech to
first audio on the same five questions, most of it Chatterbox rendering the first sentence whole. With
streaming the 27B measures 1.2 to 1.3 s median. The 9B also adds 8 to 9 s turns when it web-searches
trivia.

## Choosing

- **NVIDIA with 24 GB, and multi-part requests matter more than vision:** `nvidia-24gb-27b`.
- **NVIDIA with 16 GB or more:** `nvidia-24gb`. Below 24 GB, lower `--gpu-memory-utilization` in
  the vLLM arguments or pick a smaller model; the Chatterbox server needs 3 GB next to it.
- **A smaller card, AMD, Apple silicon, or no GPU:** `ollama`. Ollama handles the hardware itself.
  Pull any model whose Ollama page lists `tools`; the default is `qwen3.5:4b`. Set
  `OLLAMA_CONTEXT_LENGTH=8192` in Ollama's environment on a small card, since its default context
  of 32K costs a few gigabytes of memory.
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
lists them). The container downloads weights into `~/.cache/huggingface` on first start, which is
why the default profile takes minutes the first time and about 3 minutes after that.

**llama.cpp.** The official `server-cuda` image, pinned to a build, reading GGUF files from
`~/.cache/huggingface` (the same cache as vLLM). `llm.model` is a Hugging Face repo and quant
(`unsloth/Qwen3.8-27B-GGUF:UD-Q4_K_S`); the preset passes it as `-hf` and as the served name, and
downloads on first start. It runs two conversations at once with 16K of context each and a q8 cache,
and drafts with the model's own MTP layer (`--spec-type draft-mtp`), which needs a GGUF that keeps
that layer; remove those two arguments for one that does not. Thinking is switched with the same
`enable_thinking` template flag as vLLM. The first request after a start reads the 2,900-token
system prompt in about 2.3 s; after that it is cached.

**Ollama.** Speaks the OpenAI protocol at `/v1`. Thinking is switched per request with
`reasoning_effort`, which is what `llm.thinking.control: reasoning_effort` sends. Ollama ignores
`tool_choice`, which Winston does not use.

**Chatterbox-Turbo.** CUDA only, its own venv (`services/tts-chatterbox`) because it pins torch 2.6.
Clones any clip under `data/voices`; six CC BY 4.0 VCTK samples come bundled. Chatterbox itself
renders a sentence whole, at about 25 ms per character, so our server streams: it generates the
speech tokens one at a time and vocodes the prefix every 40 tokens (the first chunk after 24), sending
only the new audio with a 20 ms cross-fade. First audio arrives in about 330 ms whatever the sentence
length, and a sentence renders at about 0.3 of real time, so playback never waits. `--no-stream`
goes back to whole sentences; `--first`, `--step` and `--hold` tune the chunks.

**Pocket TTS.** Kyutai's 100M model, MIT code and CC BY 4.0 weights, its own venv
(`services/tts-pocket`) with a CPU-only torch. Streams audio as it generates. 26 built-in voices
work out of the box. Cloning from a clip needs the gated weights: accept the terms at
https://huggingface.co/kyutai/pocket-tts, run `uvx hf auth login` once, and the same clips as
Chatterbox work. Its `--threads` argument defaults to four.

**Hosted speech.** Any server speaking the OpenAI speech protocol, key in `TTS_API_KEY`. The
built-in voices dropdown then shows nothing, since there is no local server to ask; type the name.

## What is not covered

vLLM on AMD (ROCm images exist, untested here), Windows, and a brain smaller than 4B. Parakeet's
INT8 ONNX build wants a few CPU cores; on a Raspberry Pi class satellite the plan keeps hearing on
the brain box, not the satellite.
