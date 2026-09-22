# ops

Three processes make up the voice stack. Start them in this order from the repo root.

## 1. vLLM (local brain, :8000)

```sh
docker compose -f ops/docker-compose.yml up -d vllm      # ~3 min to healthy
docker compose -f ops/docker-compose.yml logs -f vllm    # watch startup
docker compose -f ops/docker-compose.yml down            # stop and free the GPU
curl -s localhost:8000/v1/models | jq                     # is it up?
```

Weights were pulled once with `hf download QuantTrio/Qwen3.5-9B-AWQ` into `~/.cache/huggingface`,
which is mounted into the container; it runs offline. `--gpu-memory-utilization 0.70` caps vLLM
at ~16 GB (11.4 GB weights + KV cache). Thinking mode is off server-side.

## 2. TTS server (Chatterbox-Turbo, :8880)

```sh
cd voice/tts/chatterbox_server
.venv/bin/python server.py --voice ../reference/vctk/p232_023.wav --voices-dir ../reference
```

~25 s to healthy, 3.1 GB VRAM. See `voice/tts/README.md` for voices.

## 3. Voice pipeline (Pipecat, :7860)

```sh
.venv/bin/python -m voice.pipeline        # then open http://localhost:7860
```

Loads Parakeet STT on the CPU (~10 s) and serves the browser client. Speak; the bot says
"Winston online." on connect. Per-turn latency and transcripts are logged to the console.

## Checks

- `tests/m0_tool_call.sh`: LLM tool calling (needs 1).
- `tests/m2_components.py`: STT on a known clip, TTS timing, TTS→STT intelligibility (needs 2).
- `tests/m2_headless_roundtrip.py`: synthetic speech through the real pipeline, measures
  end-of-speech → first audio (needs 1 and 2, not 3).

## Stopping everything

```sh
pkill -f "voice.pipeline"; pkill -f "chatterbox_server/server.py"
docker compose -f ops/docker-compose.yml down
```
