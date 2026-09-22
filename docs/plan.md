# Winston, Self-hosted full-duplex voice assistant for Home Assistant

## Read this first (about this plan)

This plan was written by Claude (an AI) on 2026-09-21, based on one conversation with Aldin and about a dozen web searches that day. Treat it accordingly:

- **The name.** This was drafted as "Jarvis". Aldin renamed it **Winston** on 2026-09-22. Persona, wake word and repo use Winston. "Jarvis" still appears below only as a fact about third-party products: HA Voice PE and openWakeWord ship a pretrained "Hey Jarvis" wake word, which Winston cannot reuse (see §3 and `DECISIONS.md`).
- **I may have misread the intent.** Aldin described a "glorified Google Home with Home Assistant integration and tool calling" that will grow over time. If something below contradicts what Aldin actually wants, that wins. Ask rather than guess.
- **Model picks have a short shelf life.** Speech and LLM models are moving monthly. Every model named here is a *default that was reasonable in September 2026*, not a commitment. Before installing any model, check whether a newer one in the same slot exists and whether the local serving stack (vLLM, vllm-omni, llama.cpp) actually supports it.
- **The architecture is the durable part.** The decision to build a modular cascade (STT → local LLM with tools → TTS, with barge-in), keep Home Assistant as the "hands," and hand hard tasks to a cloud model should survive model swaps. Design every component behind a swappable interface so replacing a model is a config change.
- **Numbers are estimates.** VRAM figures and latencies come from vendor docs and community reports, not from this hardware. Measure everything on the actual box before optimizing.
- **Change freely, but log why.** Keep a `DECISIONS.md` in the repo. When you deviate from this plan, write one line: what changed, why, date.

---

## 1. Goal

A voice assistant Aldin talks to at home, in English, that feels close to the movie version: always listening for a wake word, natural voice, interruptible, under ~2 seconds to respond, controlling the home through Home Assistant and answering general questions. Runs on a dedicated box. Hard tasks can go to a cloud model (Claude).

### Example interactions it must handle (from Aldin)

- "Hey Winston, put the lights in my living room at 50% brightness and make them warm."
- "Hey Winston, tell my friend a fun fact."
- "Hey Winston, can you boot my PC? Also, what's my phone's battery level?"
- "Hey Winston, turn on my TV and dim the lights."
- "Hey Winston, who is at the door? Check the camera."

### Requirements (from the interview)

| Area | Requirement |
|---|---|
| Hosting | Dedicated box with an RTX 3090 (24 GB). Separate from the main Arch PC. LAN-only for now. |
| Privacy | Voice-to-voice and home control stay local. Cloud handoff acceptable for hard tasks. |
| Interaction | Room mic, wake word, physical mute/override. Full duplex (can interrupt it). Latency < 2 s, must not feel unnatural. |
| Voice | Natural tone and cadence. British voice or, better, a cloned/custom voice. English only. |
| Text channel | Some way to send/receive text (Telegram or similar). |
| Integrations | Home Assistant is the priority. PC control is nice-to-have, not a focus. Camera/vision is a later goal. |
| Memory | Distilled facts only (preferences, people, projects). No conversation-log memory. |
| Users | Single user, no voice verification. Personal use only. |
| Budget | GPU: already owned. API spend: max ~500 SEK/month. |
| Timeline | Something working first, then grow long term. |
| Tooling | Built with Claude Code. Language is whatever fits (Python is the path of least resistance for the voice stack). |

### Non-goals (for now)

- Multi-user, speaker identification, or auth on the voice channel.
- Screen-driving "computer use" on the Arch PC.
- Proton Mail integration (deprioritized by Aldin).
- Swedish voice.
- Exposing anything to the public internet.

---

## 2. Why this architecture (and why not the obvious alternatives)

### Chosen: modular "controlled duplex" cascade

```
[Room satellite: mic+speaker with hardware AEC, wake word, mute button]
        │  audio stream (WebRTC/WebSocket over LAN)
        ▼
[Brain box: RTX 3090]
  Pipecat pipeline
    ├─ VAD + barge-in (stop TTS when user speaks)
    ├─ STT: streaming, CPU (Parakeet TDT 0.6B)
    ├─ LLM: local Qwen3.5-9B via vLLM, OpenAI-compatible, tool calling
    │     ├─ tools: Home Assistant MCP server (/api/mcp/assist)
    │     ├─ tool: ask_claude (cloud handoff for hard tasks)
    │     ├─ tool: memory read/write (markdown vault)
    │     └─ tool: describe_camera (snapshot → local VLM)
    └─ TTS: streaming, GPU (Chatterbox-Turbo, cloned voice)
        │
        ▼
[Speaker on the satellite]

[Text channel: Telegram bot → same agent loop, no STT/TTS]
[Home Assistant: the "hands", lights, TV, WoL, camera, phone sensors]
```

"Controlled duplex" means: the assistant streams speech while listening, stops immediately when you talk over it, and starts speaking before the LLM has finished generating. It is not the model literally speaking and listening in one forward pass. For a home assistant this is indistinguishable in practice and it's what production voice agents do.

### Rejected for now: a single end-to-end omni model (Qwen3.5-Omni)

Qwen3.5-Omni (30B MoE, 3B active, released 2026-03-30) is the obvious "assistant in one model" candidate, and Aldin originally leaned toward it. Reasons to defer:

- Its semantic-interruption, tool-use, and voice-cloning features are documented for Alibaba's cloud Realtime API. Local serving through vllm-omni was, as of September 2026, still working on "automatic turns first, then reliable interruption" (vllm-omni RFC #7055). Locally it behaves half-duplex.
- Quantization/serving support for the speech-output ("talker") path lagged behind text models.
- Aldin's previous attempt failed on exactly this kind of "the demo works, the local stack doesn't" gap.

**Revisit trigger:** when vllm-omni (or another local server) ships working barge-in for a Qwen omni model on a 24 GB card, build a branch that swaps the STT+LLM+TTS trio for the omni model behind the same Pipecat interface. Keep the cascade as fallback.

### Rejected for now: a true full-duplex speech model (NVIDIA PersonaPlex / Moshi)

PersonaPlex-7B (Jan 2026, built on Kyutai Moshi) is the most "movie-like" open model: it speaks while it listens, turn-taking latency ~0.17 s, interruption response ~0.24 s, persona via text prompt, voice via audio prompt. Reasons to defer:

- It has no text interface to hook tools into. It emits audio tokens plus one inner-monologue text token every 80 ms; there's no assistant message to attach a tool result to. Community hacks exist (drip-feeding results token by token) but the core use case here *is* tool calling.
- The 7B brain is weak and trained largely on old English phone-call data.
- Validated on A100/H100; 16 GB+ with offload is reported, but it's a research artifact.

**Revisit trigger:** if a PersonaPlex-class model ships with native function calling and a stronger backbone, evaluate it for the conversational front end, keeping the current LLM as the "back office" for tools.

### Prior art on disk

`~/ai/omni` (July 2026, not a git repo) holds Aldin's earlier experiments: a Gradio live-chat app on Qwen2.5-Omni-7B (`app.py`, with latency benchmarks in `bench.py`/`bench2.py`), plus GLM-4-Voice and PersonaPlex checkouts with their own venvs. Mine it for measured numbers and gotchas on this GPU; don't build on it.

---

## 3. Component choices (defaults, all swappable)

Every row lists the default and the fallback. Claude Code: before installing anything, re-check that the default still exists, is still the best in its slot, and is supported by the serving stack on the current CUDA/driver versions.

| Slot | Default | Why | Fallback / alternative |
|---|---|---|---|
| Orchestration | **Pipecat** (Python) | Handles server-side VAD, barge-in, streaming between STT/LLM/TTS, WebRTC transport, provider switching. Reference builds hit ~2 s end-to-end with local models. | Kyutai **Unmute** (docker-compose, any OpenAI-compatible LLM, interruption built in; no native tool calling, ~750 ms TTS on one GPU). Good for a weekend proof-of-life. LiveKit Agents is the other serious option. |
| STT | **NVIDIA Parakeet TDT 0.6B** (ONNX INT8, CPU, ~0.2 s) | Keeps GPU free. Fast, accurate for English. | Kyutai STT (GPU, ~2.5 GB, semantic VAD), faster-whisper, Qwen3-ASR (newer, tooling still maturing). |
| LLM (local brain) | **Qwen3.5-9B** via vLLM with `--enable-auto-tool-choice` and the Qwen tool parser | Small, fast, multimodal (can describe camera snapshots), good tool calling, Apache 2.0. Fits with TTS on the GPU with headroom. | See §4 for the bigger-brain option. Qwen3.5-4B if latency needs cutting. |
| TTS | **Chatterbox-Turbo** (Resemble AI, MIT, English) | Zero-shot voice cloning from a short clip, emotion exaggeration control, benchmarked against ElevenLabs, 350M params with a one-step decoder so it's fast and light on VRAM. | **Qwen3-TTS-0.6B** (streaming ~97 ms, cloning, 10 languages), **Kyutai Pocket TTS** (100M, CPU-only, cloning, ~200 ms first chunk), **Fish Audio S2** (top of independent benchmarks; weights require a paid license for commercial use, personal use fine but check). |
| Wake word | **openWakeWord** on the satellite with a **custom-trained "Hey Winston"** model | Local. Custom wake words are trainable with openWakeWord's training notebook (synthetic samples via Piper TTS; budget an afternoon including threshold tuning). There is no pretrained "hey_winston"; the pretrained set includes "hey_jarvis", which no longer helps. | microWakeWord (ESP32 firmware, used by HA Voice PE, which ships with "Hey Jarvis"). A custom microWakeWord model is also trainable but is more work than openWakeWord. |
| Satellite hardware | Raspberry Pi 4/5 or mini PC + **USB conference speakerphone with hardware AEC** (Jabra Speak or Anker PowerConf class) | Full duplex fails without acoustic echo cancellation: the mic hears the speaker and the assistant interrupts itself. Hardware AEC is the cheap, reliable fix. | HA Voice Preview Edition reflashed with custom ESPHome firmware (later; its stock firmware targets HA's turn-based pipeline and its stock wake word is "Hey Jarvis"). |
| Home control | **Home Assistant MCP Server integration** (`/api/mcp/assist`, Streamable HTTP, long-lived token) | Native, maintained, entity exposure controlled from HA's "exposed entities" page. | An allowlist-gated external MCP server (e.g. `astromechza/ha-mcp-server`) if stricter control is wanted. |
| Cloud handoff | **Claude API** via an `ask_claude` tool | Hard reasoning, long answers, coding, anything the 9B can't do. Budget ~500 SEK/month is plenty at home volumes. | Any OpenAI-compatible provider; keep it behind one interface. |
| Text channel | **Telegram bot** → same agent loop | Simplest two-way text. | HA companion-app notifications (one-way), Signal, a tiny web UI. |
| Memory | **Markdown vault** in the repo (`memory/`), git-tracked | Distilled facts only; human-readable; diffable; no infra. | Add a graph/temporal memory layer (Graphiti) only if facts start changing often enough to need history. |
| Serving | **vLLM** for the LLM; small FastAPI/OpenAI-compatible wrappers for STT/TTS | Standard interfaces; everything speaks OpenAI-style HTTP or MCP. | llama.cpp / Ollama for the LLM if vLLM quantization support is a problem. |

---

## 4. GPU budget on the RTX 3090 (24 GB)

Ampere: no native FP8, so prefer AWQ/GPTQ INT4 or GGUF quants. Rough phase-1 layout:

| Component | Where | Est. VRAM |
|---|---|---|
| Qwen3.5-9B (QuantTrio AWQ: INT4 MLPs, BF16 attention/vision) + 3.2 GB KV cache (93K tokens) | GPU | **measured 16 GB** at `--gpu-memory-utilization 0.70` (weights 11.4 GB). Plan estimate was ~6–8 GB. |
| Chatterbox-Turbo | GPU | **measured 3.1 GB** |
| Parakeet STT | CPU | 0 |
| Camera VLM | same Qwen3.5-9B (multimodal) | 0 extra |
| Headroom | | ~4.5 GB (measured 2026-09-22: 16 + 3.1 of 24) |

**Bigger-brain option (phase 2, if the 9B feels dumb):** Qwen3.6-35B-A3B (MoE, 3B active) is roughly 22 GB at Q4 and has been community-tested around 100 tok/s on a 3090. That eats the whole card, so move TTS to CPU (Pocket TTS) and keep STT on CPU. This needs a decent CPU on the box (Pocket TTS uses ~2 cores, Parakeet ONNX wants a few more). Qwen3.8-27B (dense, Apache 2.0, Aug 2026) is another candidate but dense 27B on a 3090 is slower per token, which hurts a voice assistant more than a coding assistant.

Open question: what CPU and RAM does the brain box have? This decides whether phase 2 is realistic.

---

## 5. Milestones and acceptance criteria

Each milestone should end with something Aldin can demo. Don't start the next one until the current acceptance test passes on the real hardware.

### M0, Box baseline
- OS of choice (Arch or Ubuntu LTS), NVIDIA driver, CUDA, Docker + NVIDIA Container Toolkit, `uv` for Python.
- vLLM serving Qwen3.5-9B on an OpenAI-compatible endpoint with tool calling enabled.
- **Accept:** `curl` to `/v1/chat/completions` with a tool schema returns a well-formed tool call.
- **Status: passed 2026-09-22.** `tests/m0_tool_call.sh`: tool call in 896 ms, plain answer in 208 ms, ~71 tok/s decode, thinking disabled. Start/stop with `ops/docker-compose.yml` (loads in ~3 min). Docker, driver and `uv` were already on the box.

### M1, Text brain + Home Assistant
- Enable HA's MCP Server integration; expose ~5 entities (living room lights, TV, a WoL switch for the PC, phone battery sensor from the companion app).
- Python agent loop: MCP client → HA; LLM decides tool calls; loop until final answer. CLI chat interface.
- **Accept:** typed "set the living room lights to 50% and warm" changes the lights. "What's my phone battery?" reads the sensor. "Boot my PC" triggers WoL.
- **Status: passed 2026-09-22** (`tests/m1_ha.py`, verified against HA's own state: brightness 128 at 2700 K, battery 81% read via GetLiveContext, WoL button pressed; three turns in ~8 s). HA's MCP server is consumed through Pipecat's `MCPClient`, so the voice pipeline and the CLI share one brain (`brain/agent.py`). Exposure is a 13-entity allowlist (`ops/ha_expose.py`). Gaps: no TV integration, no camera, WoL untested across subnets.

### M2, Voice round trip in the browser
- Pipecat pipeline: browser mic (WebRTC) → Parakeet STT → M1 agent → Chatterbox-Turbo streaming → browser speaker.
- Voice: clone from a consented reference clip (Aldin's own voice, or a permissively licensed voice such as Kyutai's voice-donation set). Do not clone an actor.
- Instrument latency at each stage: end-of-speech → first STT text → first LLM token → first audio chunk.
- **Accept:** median end-of-speech → first audio under 2 s. Barge-in stops playback within ~300 ms.
- **Status: headless acceptance passed 2026-09-22; browser test with a real mic pending.** `tests/m2_headless_roundtrip.py` pushes synthetic speech through the real pipeline: STT 170–260 ms, first LLM token 250–400 ms, first audio median 1.30 s (0.94–2.02 s) over 4 turns. The gap between first token and first audio is the LLM finishing the first sentence plus Chatterbox synthesizing it (0.3–1.2 s depending on sentence length), so the persona asks for a short opening sentence. Barge-in relies on Pipecat's VAD interruption plus the browser's echo cancellation; not yet measured. With tools (after M1): read-only tool turns ~1.6 s to first audio; light-changing turns wait for Zigbee (2–6 s inside HA), see `DECISIONS.md` for the planned acknowledgement fix.

### M3, Room satellite
- Train the custom "Hey Winston" openWakeWord model first (can be done before the satellite hardware exists; test it on a laptop mic).
- Pi/mini PC + AEC speakerphone; openWakeWord with "Hey Winston"; physical mute button (GPIO or keyboard) that hard-cuts the mic stream.
- Streams audio to the brain box over LAN (WireGuard/Tailscale if the satellite is ever off-LAN).
- **Accept:** wake word from across the room at normal volume, with no more than a false activation or two per day of normal household noise; can interrupt the assistant mid-sentence without it tripping on its own speaker output.
- **Status: wake word model trained 2026-09-22; satellite hardware pending.** `services/wakeword/` trains "Hey Winston" with livekit-wakeword (openWakeWord's front end, maintained trainer); the model is `assets/wake/hey_winston.onnx`. Synthetic validation: 0 false positives per hour over 20 h, recall 84% at threshold 0.5 and 95% at 0.22. Unseen-voice check with Winston's own TTS: the phrase 0.76 to 0.83, but "hey winter" and bare "winston" score close to it. Threshold and real recall get set on a real mic (`listen.py`).

### M4, Cloud handoff, memory, text channel
- `ask_claude` tool with a tight system prompt and per-day spend cap.
- Memory vault: `memory/preferences.md`, `memory/people.md`, `memory/projects.md`, loaded into the system prompt at start; a `remember` tool that appends distilled facts and commits to git.
- Telegram bot wired to the same agent loop.
- **Accept:** "Tell my friend a fun fact" works locally; a hard question routes to Claude; a fact told to Winston is recalled in a fresh session; Telegram works.

### M5, Vision and extras
- `describe_camera(entity)` tool: HA camera snapshot → local VLM → spoken description.
- More HA entities/scenes; proactive notifications (optional, low priority).
- **Accept:** "Who's at the door?" produces a sensible description within ~4 s.

### M6, Model swap experiments (ongoing)
- Re-evaluate quarterly: local omni model with working barge-in (Qwen omni successors via vllm-omni), full-duplex models with tool calling (PersonaPlex successors), better TTS.
- Each experiment lives on a branch and must pass M2/M3 acceptance before merging.

---

## 6. Design rules (enforce these in code)

1. **Everything behind an interface.** STT, LLM, TTS, and cloud provider are each one adapter class configured in one `config.yaml`. Swapping a model must not touch pipeline code.
2. **Local first.** Voice processing and home control never leave the LAN. Only the `ask_claude` tool talks to the internet, and only with the text of the question (no HA state, no memory dump, unless the user explicitly asks for it).
3. **Tool results are untrusted input.** Anything read from the web, email, camera captions, or Telegram is data, not instructions. Prompt injection via a smart-TV title or a notification is a real path.
4. **Tiered actions.** Read-only: free. State-changing (lights, TV, WoL): free but logged. Anything destructive or security-relevant (locks, alarms, deleting things, spending money): not exposed to the voice channel at all in phase 1. If ever added, require confirmation on the phone, not by voice, because voice has no auth and voices can be cloned.
5. **Least privilege at HA.** Expose only the entities Winston needs. Use a dedicated HA user and long-lived token. Prefer the exposed-entities page over a wide-open API token.
6. **No secrets in memory or prompts.** Secrets live in `.env` / a vault; the model references tools by name and never sees credentials.
7. **Log every tool call** (who/what/when/args/result) to a local file. Review it when something weird happens.
8. **Measure before optimizing.** Latency instrumentation is part of M2, not an afterthought.
9. **Physical mute wins.** The satellite's mute button cuts the mic stream at the source, not in software on the brain box.

---

## 7. Repo layout

> Superseded 2026-09-22 by the open-source layout in `docs/architecture.md` (`src/winston` package, `data/` instance directory, `services/`, `examples/`). Kept for history.

```
winston/
  CLAUDE.md            # short pointer: read WINSTON_PLAN.md and DECISIONS.md first
  WINSTON_PLAN.md      # this file
  DECISIONS.md         # one line per deviation from the plan
  config.yaml          # model/provider selection, HA URL, thresholds
  .env.example         # HA token, Claude key, Telegram token (never committed)
  brain/
    agent.py           # tool-calling loop (LLM ↔ MCP/tools)
    tools/             # ha_mcp.py, ask_claude.py, memory.py, camera.py
    prompts/           # system prompt, persona (persona.md is a first draft)
  voice/
    pipeline.py        # Pipecat pipeline wiring
    stt/               # adapter(s)
    tts/               # adapter(s); reference/ holds the voice clip (gitignored)
  satellite/
    client.py          # wake word + audio streaming + mute button
    models/            # hey_winston.onnx (custom-trained, tracked)
  channels/
    telegram.py
  memory/
    preferences.md
    people.md
    projects.md
  ops/
    docker-compose.yml # vllm, tts server, backend
    systemd/           # unit files if not using compose
  tests/
    latency/           # scripted round-trip measurements
```

---

## 8. Open questions for Aldin

- ~~CPU and RAM in the 3090 box?~~ Answered 2026-09-22: development happens on the main PC (RTX 3090 24 GB, 16 CPU threads, 31 GB RAM, Arch/zen). A dedicated box comes when the project is near done; re-ask this question then, since it decides the bigger-brain option in §4.
- Which room, and is there a network drop or Wi-Fi only for the satellite?
- Whose voice to clone? (Own voice, a donated voice, or a designed voice.)
- Which HA entities exist today for: living room lights, TV (media_player or IR blaster?), PC (WoL integration set up?), phone (companion app installed?), door camera (integration name?).
- Preferred OS for the box (Arch to match the main PC, or Ubuntu LTS for driver/container stability)?
- Should the assistant be able to speak unprompted at all in phase 1, or strictly reactive?
- Persona beyond the name: dry British butler, or something else? This shapes the system prompt and the voice pick.

---

## 9. Sources consulted (September 2026)

- Home Assistant MCP Server integration docs: https://www.home-assistant.io/integrations/mcp_server/
- Home Assistant LLM API developer docs: https://developers.home-assistant.io/docs/core/llm/
- Kyutai Unmute: https://github.com/kyutai-labs/unmute
- Kyutai TTS / Pocket TTS: https://kyutai.org/tts/
- Pipecat + Parakeet + Qwen3-TTS reference pipeline: https://github.com/dingausmwald/Qwen3-TTS-Openai-Fastapi
- vllm-omni full-duplex tracking RFC: https://github.com/vllm-project/vllm-omni/issues/7055
- Qwen3.5-Omni deployment notes: https://www.spheron.network/blog/deploy-qwen3-5-omni-gpu-cloud/
- Qwen3.5 / Qwen3.6 local run guides: https://unsloth.ai/docs/models/qwen3.5 , https://unsloth.ai/docs/models/qwen3.6
- Qwen3.6-35B-A3B model card (vLLM tool-call flags): https://huggingface.co/Qwen/Qwen3.6-35B-A3B
- NVIDIA PersonaPlex: https://github.com/NVIDIA/PersonaPlex
- Full-duplex model list: https://github.com/Ruiqi-Yan/Awesome-Full-Duplex-SDM
- Open-source TTS comparisons: https://www.bentoml.com/blog/exploring-the-world-of-open-source-text-to-speech-models , https://www.tryspeakeasy.io/blog/open-source-text-to-speech-2026
- HA Voice Preview Edition: https://www.home-assistant.io/voice-pe/
- openWakeWord (custom wake word training): https://github.com/dscripka/openWakeWord
