# Hardware and profiles

Winston's reference box is one NVIDIA card with 24 GB, but nothing in the core assumes it. Every
heavy part is an engine chosen in config, and a profile is one ready-made choice per slot for a
class of hardware. Pick one at `winston init --profile NAME`, or switch any slot later on the Models
page of the web UI. `winston init --list` prints the same table.

| Profile | Needs | Brain | Voice | Hearing |
|---|---|---|---|---|
| `nvidia-24gb` (default) | NVIDIA card with 24 GB, Docker with the NVIDIA container toolkit | vLLM in Docker, Qwen3.5-9B AWQ | Chatterbox-Turbo on the GPU, voice cloning from a clip | Parakeet on the CPU |
| `ollama` | Ollama installed; any GPU, Apple silicon or a CPU | Ollama, `qwen3.5:4b` | Pocket TTS on the CPU | Parakeet on the CPU |
| `hosted` | Internet and an API key | Any OpenAI-compatible endpoint | Pocket TTS on the CPU | Parakeet on the CPU |

Hearing is Parakeet TDT 0.6B v3 through ONNX on the CPU in every profile. Voice activity, turn
taking and the wake word are CPU work too. Home control never leaves the LAN in any profile; the
hosted profile sends only the conversation text to the model provider.

## Measured (2026-09-23)

| | `nvidia-24gb` | `ollama` on the same box, GPU already full |
|---|---|---|
| End of speech to first word | about 1 s | not measured by voice |
| Tool call, thinking off | 0.9 s | 3 s with the 4B model split between CPU and GPU |
| Tool call, thinking on | 3.5 s | 8 s, same split |
| TTS first audio, short sentence | 325 ms, sentence at a time | 60 ms, streamed while it generates |
| TTS real-time factor | 0.3 on the GPU | 0.23 on four CPU threads (Ryzen 5800X) |
| Brain memory | 16 GB of VRAM | 8 GB for the 4B model with a 32K context |

The Ollama numbers come from a box whose GPU was already holding the vLLM stack, so Ollama put
half the model on the CPU. On a card with room, expect it to be closer to the vLLM column.

## Choosing

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

Mixing is normal. The plan's bigger brain (a 35B mixture model that fills the whole card) is the
vLLM engine with Pocket TTS on the CPU: pick the vLLM preset for the brain and the Pocket preset
for the voice on the Voice page.

## Notes per engine

**vLLM.** Fastest tool calls and the only path with vision. The tool-call and reasoning parser
flags in the preset are for Qwen models; another family needs its own parser (`vllm serve --help`
lists them). The container downloads weights into `~/.cache/huggingface` on first start, which is
why the default profile takes minutes the first time and about 3 minutes after that.

**Ollama.** Speaks the OpenAI protocol at `/v1`. Thinking is switched per request with
`reasoning_effort`, which is what `llm.thinking.control: reasoning_effort` sends. Ollama ignores
`tool_choice`, which Winston does not use.

**Chatterbox-Turbo.** CUDA only, its own venv (`services/tts-chatterbox`) because it pins torch 2.6.
Clones any clip under `data/voices`; six CC BY 4.0 VCTK samples come bundled.

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
