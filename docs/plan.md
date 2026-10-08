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
| Memory | Distilled facts only (about you, home, plans, people). No conversation-log memory. |
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
- **Status: headless acceptance passed 2026-09-22; browser test with a real mic pending.** `tests/m2_headless_roundtrip.py` pushes synthetic speech through the real pipeline: STT 170–260 ms, first LLM token 250–400 ms, first audio median 1.30 s (0.94–2.02 s) over 4 turns. The gap between first token and first audio is the LLM finishing the first sentence plus Chatterbox synthesizing it (0.3–1.2 s depending on sentence length), so the persona asks for a short opening sentence. Barge-in relies on Pipecat's VAD interruption plus the browser's echo cancellation; not yet measured. With tools (after M1): read-only tool turns ~1.6 s to first audio; light-changing turns wait for Zigbee (2–6 s inside HA), see `DECISIONS.md` for the planned acknowledgement fix. **Regressed 2026-09-23, then fixed:** with today's persona and tools the same test measured 2.15 to 2.5 s for both the 9B and the 27B brain, mostly Chatterbox rendering the first sentence whole; with the TTS server streaming, the 27B measures 1.2 to 1.3 s median over three runs of five questions.

### M3, Room satellite
- Train the custom "Hey Winston" openWakeWord model first (can be done before the satellite hardware exists; test it on a laptop mic).
- Pi/mini PC + AEC speakerphone; openWakeWord with "Hey Winston"; physical mute button (GPIO or keyboard) that hard-cuts the mic stream.
- Streams audio to the brain box over LAN (WireGuard/Tailscale if the satellite is ever off-LAN).
- **Accept:** wake word from across the room at normal volume, with no more than a false activation or two per day of normal household noise; can interrupt the assistant mid-sentence without it tripping on its own speaker output.
- **Status: wake word model trained 2026-09-22; satellite hardware pending.** `services/wakeword/` (removed 2026-10-07, in git history at 746e994) trained "Hey Winston" with livekit-wakeword (openWakeWord's front end, maintained trainer); the model is `assets/wake/hey_winston.onnx`. Synthetic validation: 0 false positives per hour over 20 h, recall 84% at threshold 0.5 and 95% at 0.22. Unseen-voice check with Winston's own TTS: the phrase 0.76 to 0.83, but "hey winter" and bare "winston" score close to it. Threshold and real recall get set on a real mic (`listen.py`).
- **Satellite channel built 2026-09-24, before the box arrived** (`channels/satellite.py`, `docs/voice-pe.md`): Winston as the box's ESPHome API client. Against a fake Voice PE speaking the native API (`tests/satellite_fake.py`): the box's event order, replies streamed from a URL while spoken, a question keeps the box listening, silence ends cleanly, first audio 1.8 to 2.3 s after speech ended. Not yet on the real box; the wake word is the box's own (hey_jarvis and friends) with stock firmware.
- **On the real box 2026-09-25**: Wi-Fi set over USB, not adopted in Home Assistant, so no key and Winston is its only client. Wake word, speech, reply and a light command work; first audio 0.7 to 0.9 s after the transcript. Its own firmware build (26.9.0 plus a microWakeWord "okay_winston" model) flashed the same day: the box wakes on Okay Winston; one wake word at a time.
- **Satellite hardware decision (2026-09-23):** Home Assistant's Voice Preview Edition instead of a Pi plus speakerphone, with its stock firmware and Winston as the ESPHome API client first, streaming-during-playback firmware second for barge-in. Reasoning, path and the growth angle in `docs/voice-pe.md`.

#### M3 next steps (added 2026-09-25, in this order)
1. **Rooms: one conversation and one mic per room.** Today a browser session and a box in the same room are two Winstons with separate conversations: the open browser mic hears "Okay Winston" said to the box and answers too, and it hears the box's speaker (browser echo cancellation removes only its own output) and takes his voice for a turn. The fix:
   - A room is a name shared by boxes (`channels.satellite.devices` keys) and browser clients (`/dashboard?room=main room`, and a room picker on the Talk page; unset means no room, as now).
   - One conversation per room, owned by winstond or a shared room registry, so a request started at the box can be continued at the screen and the other way round; the session idle reset (satellite 10 min) applies per room.
   - One mic per room at a time: when a box in the room wakes, a browser session in that room pauses its mic (and shows why) until the box's turn ends; while a browser session in the room is open, the box's wake word is taken into that session instead of starting a second conversation, or ignored.
   - Room-scoped actions: "turn off the lights" from a room means that room's lights (the Voice PE page's room awareness).
   - **Accept:** with the box and the dashboard open in the same room, "Okay Winston, lights off" gets exactly one reply; the next question at the dashboard continues the same conversation; nothing Winston says from the box starts a turn in the browser.
   - **Status: built 2026-09-25, headless acceptance passed; real room pending** ([rooms](rooms.md)). winstond keeps each room's conversation and every session links to it; a box's wake holds the room's mic until its reply has played, and a browser in the room hears silence meanwhile and ignores a bare wake word. The box's wake word is not moved into the browser session: with one shared conversation the box simply takes the turn. `tests/rooms_voice.py` (fake box plus a WebRTC browser session, the box's reply played into the browser's mic): one reply, no browser turn, the follow-up at the browser answered from the box's turn, three passes in a row. `tests/room_trials.py`: "the lights" in a room went to that room 12 of 12. Not yet checked: the real box and a real screen in the main room.
   - Found on the way, for later: (a) with thinking off the 27B sometimes writes its answer, a stray `</think>` and the answer again (2 of 12 without a room, all on one question); the guard drops the tag but the first copy is already spoken, so he says it twice. Fixed 2026-10-08: each
     sentence waits until the next text shows up, and an answer repeated after the tag is said once (the box said
     "Oslo, Sir." twice in every room test, once since). (b) The first box turn after a browser session used the LLM waits about 3.5 s instead of 1.2 s for the first audio: the two channels' tool lists differ, so the server reads the whole prompt again.
2. **"Stop" ends his turn.** The stock firmware already cuts the speaker on the on-device word "Stop" during a reply. Winston does not notice yet: an early close of the reply stream should mark the turn as interrupted, so the unheard rest of the reply does not stay in the conversation as said. **Accept:** after "Stop", asking "what were you saying?" does not get the unheard part as if it was heard.
   - **Status 2026-09-27: partly.** "Stop", "that's all" or "never mind" in the listening window after a reply end the conversation with no reply and no further listening ([Voice PE](voice-pe.md#ending-a-conversation-and-button-only)); marking a reply cut short by the stop word as unheard is still open.
3. **Barge-in on the box (Voice PE step two).** ESPHome's `voice_assistant` states are exclusive: on `tts_start_streaming` it leaves STREAMING_MICROPHONE for STREAMING_RESPONSE, so no event timing from Winston keeps the mic open (checked in `voice_assistant.cpp`, ESPHome 2026.9.0). Needs a patched copy of the component through `external_components` in `services/voice-pe/voice-pe-winston.yaml` that keeps streaming the XMOS echo-cancelled mic during playback; Winston runs the browser path's VAD on it and cancels the reply on speech. Offer the patch upstream. **Accept:** interrupting him mid-sentence at normal volume stops him within about half a second, and his own voice at the loudest normal volume never interrupts him.
4. **Cloning on the box.** No firmware change: in clone mode Winston holds back STT_VAD_END until the consent line and the 15 s sample are in, then runs the browser's clone flow (`channels/clone.py`: consent by voice, same-voice check, nothing kept on a mismatch). Measure first: the box sends 16 kHz noise-suppressed audio, so compare a clone from the box with one from the browser of the same person before calling it done. **Accept:** a clone started with "Okay Winston, clone my voice" passes the same checks as in the browser and sounds no worse to Aldin in a blind pair.
   - **Status 2026-09-27: switching done, recording not.** The box has `use_voice`, so a voice cloned in the browser is used there ("Okay Winston, talk like Omar"); recording on the box is still to do.
- Added 2026-09-27 at Aldin's request, built: button only by a long press on the box (no wake word, no listening after replies), and a wake word sensitivity that covers Okay Winston (Winston's firmware, `wake_sensitivity` per box). Measure misses and false wakes at `Moderately sensitive` before retraining the wake word with real takes.

### M4, Cloud handoff, memory, text channel
- `ask_claude` tool with a tight system prompt and per-day spend cap.
- Memory vault: `memory/preferences.md`, `memory/people.md`, `memory/projects.md` (since 2026-09-24 `about-you.md`, `home.md`, `plans.md`, `people.md`), loaded into the system prompt at start; a `remember` tool that appends distilled facts and commits to git.
- Telegram bot wired to the same agent loop. (Changed 2026-09-23: Discord, see `decisions.md`.)
- **Accept:** "Tell my friend a fun fact" works locally; a hard question routes to Claude; a fact told to Winston is recalled in a fresh session; Telegram works.
- **Status: started 2026-09-23, ahead of M2/M3 acceptance (see `decisions.md`).** Memory vault done: `remember` stores, refuses secrets, commits; a fresh session recalls (typed test with the 9B, `tests/memory_cases.py` in `winston check unit`). Web search measured working (1.4 s at the search model). `ask_claude` works through the Claude Agent SDK on the Max plan's credit (about 6 s, 0.05 USD a question, 3 USD a day cap). Discord channel (replacing Telegram) and announcements (moved up from M5) built: an announcement from Home Assistant plays chime and speech in a connected browser and a spoken follow-up is answered from its context (`tests/announce_voice.py`); Discord waits for a bot token.

### M5, Vision and extras
- `describe_camera(entity)` tool: HA camera snapshot → local VLM → spoken description.
- More HA entities/scenes. (Proactive notifications moved to M4 on 2026-09-23 as announcements.)
- **Accept:** "Who's at the door?" produces a sensible description within ~4 s.

### M6, Model swap experiments (ongoing)
- Re-evaluate quarterly: local omni model with working barge-in (Qwen omni successors via vllm-omni), full-duplex models with tool calling (PersonaPlex successors), better TTS.
- Each experiment lives on a branch and must pass M2/M3 acceptance before merging.
- 2026-09-23: bigger brain. Qwen3.8-27B on llama.cpp fits next to Chatterbox and passes 42/42 brain trials against the 9B's 36/42 (`winston check brain`, `docs/hardware.md`); offered as the `nvidia-24gb-27b` profile.
- 2026-10-09: next, a smaller quant of the 27B for VRAM headroom (not benched yet). Candidate:
  `ISTA-DASLab/Qwen3.8-27B-GSQ-RCO-GGUF`, `IQ3_S-mtp` (12.1 GB against 15.4 GB for `UD-Q4_K_S`,
  about 3.3 GB freed; vendor numbers: wiki PPL 7.07 against BF16's 7.05, AIME25 and LCB v6 equal).
  Stretch: `IQ3_XXS-mtp` (10.4 GB, PPL 7.20). Take the `-mtp` builds, since the preset runs
  `--spec-type draft-mtp`; confirm the exact `-hf` tag from the repo's file names. No published
  tool-calling numbers exist at 3 bits, so on the 3090 run, against the current Q4 on the same day:
  `winston check brain` (Q4: 42/42), `tests/extensions_brain.py`, `tests/speak_up_brain.py`, first
  audio median from `logging.latency`, tokens/s and VRAM from nvidia-smi. Adopt only on equal passes
  and no latency loss; then the preset, `docs/hardware.md` and `docs/decisions.md`. Rejected: webAI's
  TwIL-LM3-Pro (3.6B formal-logic fine-tune of Granite 4.2, no tool use, non-commercial license).
- 2026-10-09: next, a second voice round (not benched yet), once the GPU has room: these need 8 to
  16 GB, so the brain moves to the `hosted` preset first (Haiku 5.5 or DeepSeek V4.1 Flash, a few USD
  a month by estimate; check `winston check brain` and first audio before keeping it) or the card is
  otherwise free. Candidates, all cloning and streaming, all non-commercial weights (fine for this
  instance; a preset must say so and the default stays Chatterbox), vendor numbers on H100-class GPUs:
  Breeze TTS 2 (`BreezeBlue/Breeze-TTS-2`, 3B, 7.7 GB, Artificial Analysis open-weights Elo 1222,
  first audio 134 ms median, RTF 0.32: on a 3090 it may land near real time, measure that first),
  Fish Audio S2 Pro (4.4B, about 16 GB bf16 or 8 GB fp8, Elo 1116, RTF 0.195, SGLang), Voxtral TTS
  (`mistralai/Voxtral-4B-TTS-2603`, 16 GB, Elo 1085, about 90 ms, vLLM-Omni). Each behind the shared
  server in `services/tts-candidates/`, run through `tests/tts_bench/` against streamed Turbo in the
  current clip plus one or two new ones (last round the voice mattered as much as the engine).
  Skipped: Magpie 357M and Supertonic 3 (no cloning in the open weights), Kokoro and Maya1 (no
  cloning), Step-Audio-EditX (an editor, no streaming), Higgs Audio v3 (Elo 1038, barely above
  Chatterbox's 1028), and last round's Qwen3-TTS, VoxCPM2 and Gepard. Results go to `docs/voices.md`.

### UI track (added 2026-09-23, runs next to M3 to M5)
The web UI started as settings only. Winston himself moves to the front; settings go behind him. Direction chosen from three rounds of mockups, see `decisions.md` and `docs/ui-guide.md`.
1. **Talk page.** The home page at `/`: the conversation (typed and spoken, tool calls, announcements), his presence and state, a text field and a talk button that connects the browser's mic to the voice channel. Needs a live event feed from winstond that every channel publishes to, and web chat as a channel. **Accept:** typing a question gets a streamed answer; a spoken conversation and an announcement show up in the same feed; tool calls show as one line each.
2. **Dashboard mode.** Full screen for a wall tablet: the presence large (it is the talk button), one line under it that follows his state, his words filling in while he speaks, announcements taking the screen. Same feed as the Talk page. Also shown inside Home Assistant through its built-in webpage card.
3. **Home Assistant integration, installable from HACS.** Winston as a conversation agent (HA's Assist chat and the HA app reach Winston's brain, text only; voice stays with Winston), his state as entities, the entities his extensions declare (Extensions track), later a native card. Needs winstond reachable from HA on the LAN with a token.
4. **Panel endpoint (added 2026-09-25).** A wall panel is a device anyone in the house can pick up, so it must not get winstond's control API (no login: config, engines, models, every tool). A second listener (say :8710) serves only what a panel uses: the dashboard page and its assets, the live feed and the voice offer, each request with a per-panel token from `data/.env`; everything else is 404. Revoking a stolen panel is deleting its token. First panel: an Alpine Linux Chromebook (Lenovo 500e class, touch, battery) on the IOT VLAN, with router and firewalld rules for that one port only; it also runs `rtl_433` to Home Assistant's MQTT and an HA dashboard as a non-admin user. Before building on it, check its speakers and mic work under Alpine (Chromebook audio often needs extra ALSA UCM profiles). **Accept:** from the panel, talking and the dashboard work; the control API, settings and any other route answer 404 or 401; the audio (WebRTC over UDP) connects through the VLAN firewalls with no UDP rule, or the rule it needs is written down.
- Winston's logo is his presence (built 2026-09-23, `docs/identity.md`): it replaced the placeholder ring of dots on the Talk page and in the favicon.
- Browsers only give the mic to HTTPS pages or localhost, so talking from a phone or tablet needs HTTPS on the LAN (a reverse proxy with a certificate). Showing the dashboard does not.
- **Status: Talk page and dashboard mode built 2026-09-23.** Dashboard at `/dashboard`: the logo large as the talk button, a state line, the last exchange while he is busy and 20 s after, announcements full screen for 60 s or until tapped; checked in headless Chromium in landscape and portrait against a scripted feed, not yet on a real wall tablet. Typed turns stream into the page with tool lines (a follow-up to an announcement answered from its context, 2.3 s; a web search turn 5.3 s); the talk button connects through winstond and the voice greeting drives the presence to speaking (checked in headless Chromium with a fake mic). Not yet checked: a spoken turn with a real mic showing in the feed. Discord and `winston talk` do not publish to the feed yet.

### Signature features track (added 2026-09-23)
Things that make Winston his own rather than another Assist clone. Aldin: "these are the kinda things I wanna build that's unique about my voice assistant." Each starts after the milestone it depends on passes.
1. **Clone a voice by asking.** "Hey Winston, clone my friend's voice": a `clone_voice` tool asks the friend to read a consent line with their name and a short English passage, records 15 to 20 s from the voice channel's mic while Winston stays silent and ignores it as a turn, trims and level-checks it, saves it to `data/voices/friends/<name>.wav` with the consent recording kept beside it, and switches the TTS voice mid-conversation (Chatterbox resolves the clip per request, Pipecat updates the voice setting live). "Back to your own voice" reverts; the switch lasts the conversation unless told to keep it. Confirm-first (tiered actions), every clone logged, cloned voices listed and deletable on the Voice page. Needs M2's real-mic check; recording from a satellite's far-field mic comes after M3. Amends the voices rule in `docs/voices.md` to allow voices recorded with the speaker's consent. **Accept:** a spoken request records a friend, the next sentence is in their voice within 2 s of the recording ending, "back to your own voice" reverts, and the clone and its consent clip show on the Voice page.
   - **Status: built 2026-09-23, ahead of M2's real-mic check (Aldin asked for it).** Tools `clone_voice` and `use_voice` on the voice channel, `docs/voices.md#cloned-voices`. Headless (`winston check clone`, two VCTK voices as user and friend): consent heard and checked, 16.5 s of speech kept, the next sentence in the clone 0.38 s after the recording ended (the speech server prepared the clip in 109 ms), "back to your own voice" reverted. Not yet checked: a real friend on a real mic, where echo cancellation and room noise decide how clean the clip is.
2. **Routines taught by talking.** "When I say movie night, dim the lights, turn on the TV and close the blinds": Winston reads the steps back, and once confirmed saves them as a Home Assistant script (created through HA's API, named and tagged as Winston's), so the phrase runs it from then on, from Winston or from HA itself. "What routines do I have?", "change movie night", "forget movie night". Only entities already exposed to Winston; creating, changing or deleting a routine is confirm-first and logged. Listed on a web page. **Accept:** a routine taught by voice runs on its phrase in a new conversation, shows in HA as a script, and is changed and removed by voice.
3. **Diary.** Every night Winston writes a few lines about the day into `data/memory/diary/<date>.md`: what was asked, what he did in the house, anything remembered, in his own voice. Written by the local LLM from the day's logs and feed, committed like the rest of the vault. "What did we do last Tuesday?" and "when did I last ask about the heating?" answer from it. A Diary page in the web UI to read it. **Accept:** after a day of use the entry exists by morning, reads true against the logs, and a question about that day is answered from it.
4. **Sound effects.** Short sounds in place of words where words add nothing: a soft click for a light turned on, a tone for "done", one for "didn't catch that", one when he starts thinking on a slow turn. The LLM picks from a small named set through its reply (or the tool result implies it), the voice channel plays them, and they are files in `data/sounds/` with defaults in `examples/`. A setting for sounds only, words only, or both. **Accept:** "turn on the lamp" answers with the click and no speech when set to sounds, and the effect starts within 300 ms of the tool result.
5. **Voices for moments (maybe).** A different voice for a different job: a narrator for a story, a cloned friend reading a birthday announcement. Builds on item 1; per-reply voice choice from a named set on the Voice page.

Later, when Winston serves more than one person or more devices (noted 2026-09-23, not scheduled): knowing who is talking (speaker recognition for "my lamp", "my calendar"; convenience only, never auth), whisper mode (answer quietly when spoken to quietly or at night), noticing things and speaking first (door left open, the morning briefing on entering the kitchen, welcome home), translating for Swedish-speaking guests, "what's this?" through a camera, and games played by voice.

### Extensions track (added 2026-09-29, runs next to M3)
A standard for adding capabilities to Winston, designed from what any extension needs rather than from today's features. Hevy, news and reminders are the first three written on it, as tests of the standard. Existing features move onto it only if they fit it unchanged; nothing is added to the standard for their sake.

**Design** (the full spec: [extensions](extensions.md)):
- **Prior art.** VS Code: a static manifest with contribution points, extensions in their own host process, a versioned API. Home Assistant integrations: a manifest, settings checked against the service on save, polling coordinators. OVOS and Mycroft skills: speak, ask and wait for an answer, scheduled events; and how API churn and in-process coupling hurt that ecosystem. Android notification channels: the user sets how each kind of notification behaves, and notifications carry actions. MCP: tools with schemas and annotations, and vendor extensions to the protocol.
- **Three sources, one format.** Built-in extensions in `src/winston/extensions/`, pip packages (entry point group `winston.extensions`), and personal ones in `data/extensions/`, where a single file is enough. No special path for built-ins.
- **A static manifest**: id, name, version, API version, description, icon, requirements, permissions, settings schema and nudge kinds, read without running any code, so the page shows what an extension may do before it is switched on.
- **Each extension is its own process**, run by the supervisor with only the secrets it declares in its environment (winstond's environment holds every key). A crash, a blocking call or a leak cannot stall winstond or a voice turn. An extension whose requirements clash with Winston's gets its own venv through uv.
- **Tools speak MCP.** An extension is an MCP server to Winston (spec 2026-07-28, Python SDK `mcp` 2.2). Tools carry schemas, output schemas and annotations; the core maps them to tiers (read, act, restricted) as far as it trusts that extension, and the user can override a tier. So any existing MCP server is a tools-only extension, added by command or URL with an allowlist, and winstond can offer the tools Aldin allows to other clients, Claude Code among them, at one MCP endpoint on loopback. What MCP has no word for (checks, nudge actions) goes through a Winston extension to MCP, negotiated the way the spec's extension mechanism provides.
- **Core services through winstond's API.** An extension reaches Home Assistant (read, or act, on exposed entities and on entities picked in its settings), presence and rooms, memory (read, or write through the same checks as `remember`), speaking up, the background model, its own store and a quota for paid APIs through winstond's REST API and event stream, with a token scoped to its manifest's permissions. Every call is checked and logged there. The background model is the local brain by default and a cloud model only if Aldin picks one; an extension never chooses, so nothing leaves the house unless a setting says so. Network hosts are declared and shown, not enforced in the first version.
- **The SDK hides all of it.** `winston.sdk` is the only import an extension needs: decorators for tools (type hints and the docstring make the schema), schedules (every, at, cron), and triggers (a Home Assistant state or event, arriving or leaving home, the first conversation of the day); a typed settings model whose pickers come from where the truth lives (an entity of a domain, a memory topic); a check on save; a status line; prompt context; hint words. Authors never touch Pipecat, MCP or HTTP.
- **Speaking up belongs to the core.** An extension declares its nudge kinds (title, default delivery per level). A kind is checked (for conditions: the core asks its check what there is to say) or pushed (for events and things you ask for: a reminder, a breaking story). Nudges carry what each is about (so a deferral or a dismissal follows the thing, not one message), a level that can rise, facts, a plain fallback line, whether it is urgent, how often it may repeat, a delivery (now, when home, when leaving, the next time you talk, the morning briefing, or silent) and where it goes (speakers at home, the phone when away), a time before which it waits, an optional expiry (a nudge otherwise lasts while its check returns it), and actions of its own ("send me the links"). The core decides whether and where: quiet hours, a daily attention budget, never mid-conversation, the room you are in. The channel that delivers it words it with its own brain inside the conversation, so it sounds like him and a follow-up has the context. Every nudge gets the same follow-ups with no code in the extension: later ("remind me after work": checked again then, with the reason kept), dismiss, mute this kind, and "why are you telling me this?" (which extension and which rule). Each kind's delivery is set on the Extensions page.
- **Results go back to Home Assistant.** An extension declares entities (sensors, binary sensors and events it sets; switches, buttons, numbers, selects and text that Home Assistant sets and the extension handles) and updates them through the SDK. They reach Home Assistant through Winston's own integration (UI track step 3) as one device per extension, with unique ids, so they keep their history and can be renamed, put in areas, shown on dashboards and used in automations. An event entity fires with each nudge, so an automation can react to one ("flash the hallway lamp when he mentions training"). Publishing is a permission shown on the card and can be switched off per extension. Until the integration exists, the entities show on the extension's card and through the API only; no stand-in through Home Assistant's `POST /api/states` (not real entities, gone after a restart) or MQTT (not set up in this home).
- **Restricted calls wait for you.** A tool or nudge action in the restricted tier does nothing until confirmed outside the voice (a button on the Talk page or a notification on the phone), so every channel keeps the same tool list and voice alone never runs one (rule 4).
- **Prompt cost is visible.** Each extension's tool schemas and prompt context are counted on its card and in a total, like memory's meter. Tools keep a fixed order and every channel gets the same list, so the prompt cache holds.
- **Versioned.** The manifest names the API version and the core supports the current and the previous major. v1 is frozen only after the three extensions in step 4 are built on it.
- Home Assistant stays core; extensions reach it through the SDK.

1. **The spec.** [`docs/extensions.md`](extensions.md): manifest, SDK surface, permissions, the MCP extension, speaking up, lifecycle and versioning, with a complete example of about 30 lines. **Accept:** Aldin approves it, and a fresh Claude session writes a working example extension from that page alone.
2. **The runtime.** Extensions as supervised processes, the MCP client in winstond, scoped tokens, secrets per process. The Extensions page, in the settings sidebar after Tools (Tools goes if step 5 empties it): a card per extension with its switch, permissions shown before switching on, settings drawn from the schema, status, nudge kinds, prompt cost (following `docs/ui-guide.md`, with screenshots). `winston ext new | list | enable | disable | call | logs | test`. Reload on save for `data/extensions/`. A test kit with a fake clock, the fake Home Assistant (`tests/fake_ha.py`) and recorded HTTP, so no extension test touches real data or real accounts.
   - **Accept:** `winston ext new` gives an extension that runs and shows on the page; one that raises, blocks for 30 s, exits or grows to 1 GB changes no voice turn's first audio (measured) and says so on its card; an extension cannot read a secret it did not declare, act without the act permission, or touch an entity that is not exposed; a plain MCP server works as a tools-only extension with an allowlist; Claude Code lists the allowed tools at winstond's MCP endpoint; memory per extension process is measured, and trusted extensions share a process only if that number calls for it.
3. **Speaking up.** Nudges, kinds, deliveries, the policy, the follow-ups (later, dismiss, mute, why), wording in the conversation, the morning briefing as a delivery, per-kind settings, and every decision in `logs/nudges.jsonl` (said, sent, held or dropped, and why). The claim guard counts "I'll remind you" as a promise, backed only by a call that sets one. Background model calls are measured against the voice channel's prompt cache and get their own server slot if a spoken turn's first audio rises.
   - **Accept:** with a fake extension and the fake clock: a nudge is said once, with the chime, in the room you are in, not in quiet hours and not during a conversation; "Yeah I know Winston, I'm training tomorrow, remind me after work" defers a nudge of any kind to the right time (a memory fact about when work ends, else arriving home), says nothing then if the check no longer holds and brings the reason back if it does; "why are you telling me this?" names the extension and the rule; a kind set to the morning briefing never interrupts.
4. **Three extensions on the public SDK only.** Each has a different shape: polling a service, heavy background work, timed items the user makes. A need one of them finds goes into the standard for every extension, and into the spec, never into that extension as a special case.
   - **Hevy.** Needs Hevy Pro and its API key. Read only. Hourly: the latest workout (`GET /v1/workouts`) and what changed (`GET /v1/workouts/events?since=`); no webhooks, which need a URL reachable from the internet. Kind "rest" (default: when home): no workout for `rest_days` (3) days, with the last workout as facts; firmer two days later. Tool `get_training`: days since the last workout, workouts this week and month, the last and best set of an exercise. Status: the last workout. Entities: days since the last workout, workouts this week, and the nudge event. Check the API docs again before building (checked 2026-09-29: the key reads workouts and the count).
   - **News.** Feeds (URL, label, region, topic) from presets (Sweden in English, Swedish outlets, world, tech) or by URL, and a short list of topics; Swedish feeds are fine, he answers in English. Plus Krisinformation.se's API v3 for the county, in English. Sources are chosen by Aldin, not the model, and feed text is untrusted. Every 15 minutes it fetches (feedparser 6.0.14, ETags honoured), stores items for 7 days and groups the same story across sources. Kind "breaking" (default: now, at most two a day): one story in three or more independent sources within 90 minutes, then one yes-or-no call to the background model given the topics. Kind "crisis": a Krisinformation item for the county, urgent for a VMA. Kind "digest": the morning briefing. Tool `get_news(topic)` returns prepared summaries, so a question never waits for a fetch; actions "tell me more" and "send me the links".
   - **Reminders.** "Remind me to call Mum at six", "what reminders do I have?", cancelling one. Timed, made by the user, no outside service. Entity: the next reminder, as a timestamp sensor. Alarms and named timers build on it later.
   - **Accept:** each passes its own checks in the test kit (recorded Hevy answers and feeds, the fake clock); none needed core code of its own; then a week of real use each; v1 is frozen.
5. **Existing features, only where they fit unchanged.** Web search, Ask Claude and watched sensors become extensions if they need nothing the standard lacks, and behave as before (`winston check` passes, same caps and log lines, a search turn's first audio within 50 ms of before). Whatever does not fit stays core.

Not in this track: Discord, which is a channel; the channel contract gets designed when a second messaging channel is wanted (Telegram, Signal), and Discord moves then. Voice cloning, which needs audio access; that gets designed with a feature that needs it for its own sake (intercom, sound detection), and M3 step 4 goes ahead without it. Home Assistant, which is core.
- **Status 2026-09-29: step 1 done.** The spec was written, tested three times by a fresh Claude session writing an extension from the page alone (electricity prices, chores, reminders; each round's guesses and contradictions fixed), and approved by Aldin.
- **Status 2026-09-29: step 2 done** ([extensions](extensions.md)). The SDK (`sdk/`, winston-sdk 1.0.0a1 on MCP 2026-07-28 with `mcp` 2.2), the host in winstond (`winston/extension_host`), the scheduler, the services under permissions, the brain's extension tools in every channel, confirmations for restricted calls on the Talk page, `winston ext`, the Extensions page, reload on save, MCP servers as tools-only extensions and winstond's own `/mcp`. Checked: `sdk/tests/sdk_cases.py` (the docs' examples exactly as written, every decorator, the harness, MCP in memory and over stdio: an extension starts in 0.58 s, a tool call takes 0.9 ms) and `tests/extensions_cases.py` (a real winstond with the fake Home Assistant and five extensions, one a plain MCP server: keys reach only their own extension, Home Assistant permissions hold, a field error on save, a blocked loop, a 400 MB hog and five crashes each contained and shown, triggers, timers across a restart, catch-up, reload in 2.6 s, the CLI, /mcp), both in `winston check unit`. Measured with the real 27B and speech engines (`tests/extensions_voice.py`): median first audio 892 ms with the probe extension quiet, 967 ms while it blocked, hogged and crashed 44 times; per question the two runs match within about 150 ms. Claude Code 2.1.284 listed exactly the one offered tool at /mcp and called it. `winston ext new` made an extension that tested, ran and showed on the page. Each extension process takes about 76 MB, so they stay one process each. Not built yet: speaking up (step 3), the phone confirmation (needs step 3's notify service), entities in Home Assistant (UI track step 3).
  - Found on the way: "What year did the Berlin wall fall?" takes 5.9 s to first audio on the 27B in both runs while other questions take under 1.2 s; not the home-request check (none of its words match). Worth a look at what the model does first.
- **Status 2026-09-29: step 3 done** ([speaking up](extensions.md#speaking-up)). The nudge engine in winstond (`extension_host/nudges.py`): checked and pushed kinds, every rule of the spec, `data/state/nudges.json`, `logs/nudges.jsonl`, `@ext.on_nudge`; kind triggers through the scheduler; delivery to the speakers in the room you are in (the browser voice channel and now each Voice PE box, which also speaks announcements), to the phone through Home Assistant's notify service with Done and Later buttons, or Discord; next-talk nudges and the morning briefing added to your next message; `follow_up` in every brain while an extension has kinds; the claim guard's reminder promises; restricted calls sent to the phone with Confirm; kinds and nudges on the Extensions page, nudges on the Talk page; `winston ext check` and `winston ext nudges`. Checked: `tests/nudges_cases.py` in `winston check unit` (the rules on a fake clock, 60 cases, and a real winstond (27) with the fake Home Assistant, the docs' plants and reminders and a trainer standing in for Hevy: said once in the room you are in and not in the other, held while talking, put off to the next day's arrival and checked again, a reminder on the phone and dismissed from its button, a restricted action confirmed from the phone, the plants' check on its sensor, next-talk and briefing mentioned once, the CLI). With the 27B (`tests/speak_up_brain.py`): he words the nudge, "Yeah I know Winston, I'm training tomorrow, remind me after work" becomes the right `follow_up` 6 of 6 (arriving home tomorrow or 18:00 without a memory fact, 17:00 with one), he says the time back, "why" names the extension. On the fake box (`tests/satellite_fake.py`): the chime, then the nudge, then an answer with no wake word. Background model calls measured against voice (`tests/background_llm_voice.py`): unpinned they took a spoken turn's median first audio from 982 to 2147 ms (8.4 s for a turn whose cached prompt they evicted); they now keep to their own slot and yield to conversations, 976 ms against 1004 ms with every turn starting while one was under way. Not checked yet: a nudge in a real browser voice session, and the phone and its buttons on a real phone.
- **Status 2026-09-29: step 4 built, in its week of real use** ([built-in extensions](built-in-extensions.md)). Hevy, News and Reminders in `src/winston/extensions/`, on the public SDK only, each with its tests beside it (Hevy 9, News 14, Reminders 10, on made-up answers and the fake clock; run by `winston check unit`). Hevy's API was checked again (the documented shapes match the key's answers; page size 10 at most). News: 25 preset feeds checked working on 2026-09-29 (Security added on Aldin's pick of topics), feedparser 6.0.14, Krisinformation's API v3 in English, filtered to the county by each item's area; on the real feeds the same story grouped across SVT, Ekot, SvD and Aftonbladet, and across BBC, Al Jazeera and NPR. With the 27B (`tests/extensions_brain.py`): reminders set, listed and cancelled by voice, a weekday reminder repeating, the news and a topic answered in English, all 6. Needs the standard found, put into it for every extension: links on a nudge (for "send me the links"), tests in an extension's own environment, a clear error for `dependencies` in the wrong place, a placeholder on a field and optional choices that can be cleared, and "dismissed" told for a pushed nudge. None needed core code of its own. The brain now stops a runaway request after eight rounds of tool calls (found here). Left: the week of real use each, then v1 is frozen.
- **Status 2026-10-01: Spotify, a fourth built-in, asked for during step 4** ([built-in extensions](built-in-extensions.md#spotify)). go-librespot 0.10.2 as a Spotify Connect receiver, its audio paced by a relay and played on the box through a new permission, `speakers` (`ctx.speakers`, the satellite's media player, 48 kHz FLAC), with tests (11). On the real box: Daft Punk played through the jack; a pause in the app now stops the box at once (it took 4 s, see decisions). Found on the way and fixed for every channel: a conversation keeps the extension tools it started with, so a channel starts a new one when they change (the box could not find `spotify_play`); the browser's "Winston online." is no longer put into the conversation; an extension's keys are read from `data/.env` when it starts. Next: speech-to-text hints from his library ("play some Daft Punk" was heard as "Dunk"); hear a pause, a resume and a skip on the box and time them; ask Aldin whether a follow-up (no wake word) should ignore Swedish, since the box answered household Swedish after a reply.

### Understanding track (added 2026-10-07, runs next to M3 and the extensions track)
How he hears, decides he is being spoken to, and knows what a name means, designed top-down in [understanding](understanding.md) after the first week of real use (the box's log from 2026-09-25 to 2026-10-03, 172 requests), in place of a patch per symptom. It takes over the open items above on hint words from extensions and on Swedish in a follow-up. One open model that hears and answers in text was researched and set aside for now; the reasons and when to look again are on that page.

1. **Turn record and marks.** Every turn with a microphone kept in `data/turns/` (the audio and one JSON line), for `turns.keep_days`; a mistake marked by voice or on the Talk page; `winston check turns` replays the marked turns through a setup with actions stubbed and scores heard right, for him right, the right thing done, and time to the first word.
   - **Accept:** a week of real use recorded on the box; a mark by voice and one on the Talk page land on the right turn; `winston check turns` scores the current setup on the marked turns, the same on a second run.
   - **Status 2026-10-07: the record is built, marks and `winston check turns` are not.** `core/turns.py`, `logging.turns` (off by default, switched on in this instance), `winston turns`. Every speech engine is recorded the same way: adapters now only turn audio into text, and one shared pipeline service (`HearingSTT`) does the rest. Checked: `tests/turns_cases.py` in `winston check unit` (a box turn and a browser turn written with their audio, a browser turn kept open while a tool runs, he speaks or a retry or state check is pending, old days removed, nothing written when off); the fake box with the real brain and voice (`tests/satellite_fake.py`, actions stubbed, turns in a temporary folder): 8 turns recorded with wake, follow-up and button starts and answered and dismissed outcomes, and it caught "Warm, please." heard as "Wong please." with the lights set to cool; the browser channel end to end (`tests/announce_voice.py`): its question recorded with audio, 511 ms to text, 1919 ms to first audio. Next: marks (by voice and on the Talk page), `winston check turns`, then the week of recorded use.
   - **Status 2026-10-07: marks by voice and from the command line, and `winston check turns`, are built; marks on the page are not.** `mark_mistake` on the box and in the browser marks the conversation's previous answered turn; `winston turns mark|unmark|marked`; `/api/turns` (a day, the marked, a turn's audio, mark and unmark) for the page to come. Checked: `tests/turns_cases.py` (marks kept with their audio past their day, bad ids and kinds refused, the voice tool, its "not for you" word); with the 27B, five phrasings ("That's not what I said.", "I wasn't talking to you.", "you did the wrong thing", "You misheard me, I said ...", "That took way too long.") each called `mark_mistake` with the right kind, and he said it was noted in his own words once the claim guard counted the tool as noting. `winston check turns --with` a scratch set of three marked turns from the fake box: heard 2/3 exact (the misheard one still misheard), right thing 1/1, for him 2/3 (the one marked not addressed still answered, as expected before part 4), the same scores on a second run.
   - **Status 2026-10-07: marks on the page built; step 1's code is done, its week of recorded use is next.** On Aldin's pick, both: a flag on the hover of any line said aloud on Talk (the box's words now show there, which they did not before) and a Turns page beside Talk (a day's turns with playback, a Marked view across days), one `TurnMarkControl` for both. Checked: `pnpm smoke` (13), the Turns page (a made-up turn marked misheard with what was said, kept, listed under Marked, unmarked) and Talk (a box line linked to its turn by the live feed's `turn` event, marked too slow); screenshots from `tests/turns_demo.py`, a throwaway winstond with a made-up record. Accept still open: a week of real use recorded on the box, and a mark by voice and one on the page made in that week.
2. **Directory.** One list of the names in his world (Home Assistant, rooms, shortcuts, contacts, voices, entries from extensions), the brain's map of the house up front, lookups that return ranked candidates, and Spotify's search returning candidates for the brain to pick from.
   - **Accept:** the turns marked "wrong thing" score better on `winston check turns`; a device named in the map needs no lookup; tool rounds per home request measured before and after.
3. **Hearing as a swappable engine.** The `openai_transcription` provider, an `engines.stt` entry run by the supervisor, presets (speech models on vLLM, Parakeet built in, hosted), the model from a dropdown, capabilities instead of names, `winston check stt A B`, context from the directory; on the box, end of speech decided by a turn model.
   - **Status 2026-10-08: the slot is built; picking Qwen3-ASR stays Aldin's call.** `openai_transcription` adapter, `engines.stt`, presets Parakeet / Qwen3-ASR 0.6B / 1.7B / hosted on the Voice page, the model listed from the server. Qwen3-ASR runs in `services/stt-qwen3-asr` (qwen-asr on transformers), not vLLM: the stock vLLM image has no audio libraries, and vLLM needed more than the 2.75 GB left beside the 27B brain and Chatterbox. Measured through the preset and the supervisor beside both: the 0.6B takes 2.2 GB, starts in 12 s, 0.29 s per request against Parakeet's 0.2 s, word errors 2.2% against 3.0% clean, 6.3% against 18.3% with people talking 10 dB under, 4.5% against 13.8% under a hum, a made-up hint word right 9 of 9 against 6 of 9 ([voices](voices.md#hearing-engines)). Not yet: the language into the turn record, capabilities, `winston check stt`, context from the directory. On the box the end of speech stays the 0.8 s pause, transcribed during it (a turn model cut requests short).
   - **Accept:** Qwen3-ASR 1.7B, Granite Speech 4.1 2B and Parakeet scored on the recorded turns; the pick has fewer names wrong than Parakeet at no more than 100 ms more per request; it fits beside the brain and the voice (measured), or its smaller size does; end of speech to text measured before and after the turn model.
4. **For him.** The decision before the brain (the wake word, the same voice, the language, length, a question just asked, the words), silent when unsure, tuned on the marked turns.
   - **Accept:** replayed on the recorded turns, those marked "not for him" are dropped and no wake word turn is; none of the first week's actions on room talk would have run.

---

## 6. Design rules (enforce these in code)

1. **Everything behind an interface.** STT, LLM, TTS, and cloud provider are each one adapter class configured in one `config.yaml`. Swapping a model must not touch pipeline code.
2. **Local first, not local only.** The default runs voice processing and home control on the LAN, and that is the main selling point. Any engine (LLM, TTS, STT) can be swapped for a hosted service by config when the user chooses it; nothing goes out unless a setting says so. Tools that reach the internet (`web_search`, `ask_claude`) send only the text of the question (no HA state, no memory dump, unless the user explicitly asks for it). (Amended 2026-09-23, see `decisions.md`.)
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
    about-you.md
    home.md
    plans.md
    people.md
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
