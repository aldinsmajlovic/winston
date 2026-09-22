# Running Winston

One command runs everything: `winston up` starts the engines from `data/config.yaml`, waits for them
to be healthy, starts the voice channel and serves the control API. Ctrl-C stops all of it.

```sh
uv run winston init            # once: creates data/ from the examples
uv run winston up              # engines + voice channel + API on :8700
uv run winston status          # health of every engine, GPU memory
uv run winston restart tts     # one engine or the voice channel
uv run winston down            # stop engines and channel; winstond keeps serving
uv run winston talk            # text chat, same brain and tools
uv run winston ha expose       # make Home Assistant's exposure match config
uv run winston check           # acceptance checks (m0, m1, m2) on real hardware
```

Logs: engine subprocesses write to `data/logs/<engine>.log`; the LLM container to `docker logs
winston-llm`; every tool call to `data/logs/tool_calls.jsonl`.

## Engines

Declared under `engines:` in config with a driver:

- `docker`: image, args, env, volumes, gpu. Used for the vLLM server. With Docker's `--gpus` path
  broken by a stale CDI spec on some hosts, set `runtime: nvidia` explicitly.
- `subprocess`: cmd and cwd, with `{data}` and `{repo}` expanding. Used for the Chatterbox TTS server,
  which has its own venv because it pins torch 2.6.
- `external`: a URL you already run; Winston only health-checks it.

## Exposure

Home Assistant's Assist exposure is a strict allowlist under `providers.home_assistant.expose`, with
friendly names and the aliases people actually say. `winston ha expose` applies it, un-exposes anything
else, and turns off HA's auto-expose. The model sees exactly that list.

## Memory of what was measured

