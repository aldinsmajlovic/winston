"""Presets and profiles: the named engine and model choices the web UI offers as dropdowns, and
that `winston init --profile` composes into a config.

A preset is a fragment for one slot (llm, tts, stt): the engine block that runs it plus the
section settings that go with it. A profile is one preset per slot for a class of hardware.
Everything here is a starting point the user edits afterwards; the config stays the truth."""
from copy import deepcopy
from typing import Any

from winston.config.registry import available

Fragment = dict[str, Any]

_VLLM_ARGS = [
    "{model}",                                   # expands to llm.model, so the model lives in one place
    "--max-model-len", "32768",
    "--max-num-seqs", "8",
    "--gpu-memory-utilization", "0.70",
    "--limit-mm-per-prompt", '{"image": 1, "video": 0}',
    "--default-chat-template-kwargs", '{"enable_thinking": false}',
    "--reasoning-parser", "qwen3",
    "--enable-auto-tool-choice",
    "--tool-call-parser", "qwen3_coder",
    "--trust-remote-code",
]

_LLAMACPP_ARGS = [
    "-hf", "{model}", "--alias", "{model}",      # repo:quant from the HF cache, served under the same name
    "--host", "0.0.0.0", "--port", "8000",
    "-ngl", "99", "-fa", "on",
    "-c", "32768", "-np", "2",                   # 16k per slot: the bare prompt is 9.4k, a long session 11.4k
    "-ctk", "q8_0", "-ctv", "q8_0",
    "--no-mmproj",                               # text only; the vision projector is another 0.9 GB
    "--jinja",
    "--cache-ram", "2048",                       # old prompts in host RAM (default 8 GB); the talk stays on the card
    "--spec-type", "draft-mtp", "--spec-draft-n-max", "2",   # the GGUF's own MTP layer: 44 -> 60-80 tokens/s
]

LLM: list[Fragment] = [
    {
        "id": "vllm", "icon": "vllm", "title": "vLLM on NVIDIA (Docker)", "needs": ["docker", "nvidia"],
        "hint": "Fastest tool calls and vision. Docker with the NVIDIA toolkit, 16 GB of VRAM for the default "
                "model, about 3 minutes to start.",
        "engine": {
            "driver": "docker", "image": "vllm/vllm-openai:v0.29.0", "port": 8000, "health": "/health",
            "startup_timeout": 600, "gpu": True, "runtime": "nvidia", "ipc_host": True,
            "env": {"NVIDIA_VISIBLE_DEVICES": "all", "NVIDIA_DRIVER_CAPABILITIES": "compute,utility"},
            "volumes": {"~/.cache/huggingface": "/root/.cache/huggingface"},
            "args": list(_VLLM_ARGS),
        },
        "settings": {"llm": {"base_url": "http://127.0.0.1:8000/v1", "api_key_env": None,
                             "model": "QuantTrio/Qwen3.5-9B-AWQ", "thinking": {"control": "chat_template"}}},
    },
    {
        "id": "llamacpp", "icon": "llamacpp", "title": "llama.cpp on NVIDIA (Docker)", "needs": ["docker", "nvidia"],
        "hint": "GGUF models, so a bigger brain fits: Qwen3.8-27B at 4 bits takes 17 GB next to Chatterbox and "
                "passes tool requests the 9B misses. No vision in this preset; starts in 10 s once downloaded.",
        "engine": {
            "driver": "docker", "image": "ghcr.io/ggml-org/llama.cpp:server-cuda-b11118", "port": 8000,
            "health": "/health", "startup_timeout": 900, "gpu": True, "runtime": "nvidia",
            "env": {"NVIDIA_VISIBLE_DEVICES": "all", "NVIDIA_DRIVER_CAPABILITIES": "compute,utility"},
            "volumes": {"~/.cache/huggingface": "/root/.cache/huggingface"},   # llama.cpp reads the HF cache
            "args": list(_LLAMACPP_ARGS),
        },
        "settings": {"llm": {"base_url": "http://127.0.0.1:8000/v1", "api_key_env": None,
                             "model": "unsloth/Qwen3.8-27B-GGUF:UD-Q4_K_S", "thinking": {"control": "chat_template"}}},
    },
    {
        "id": "ollama", "icon": "ollama", "title": "Ollama (local)", "needs": ["ollama"],
        "hint": "Any GPU or a CPU. Install Ollama, pull a model with tool support, done. Thinking is switched "
                "with reasoning_effort.",
        "engine": {"driver": "external", "url": "http://127.0.0.1:11434", "port": 11434, "health": "/",
                   "startup_timeout": 60},
        "settings": {"llm": {"base_url": "http://127.0.0.1:11434/v1", "api_key_env": None,
                             "model": "qwen3.5:4b", "thinking": {"control": "reasoning_effort"}}},
    },
    {
        "id": "hosted", "icon": "cloud", "title": "Hosted (OpenAI-compatible URL)", "needs": ["internet"],
        "hint": "A cloud model behind any OpenAI-compatible endpoint. The key goes in .env. Requests leave the LAN.",
        "engine": {"driver": "external", "url": "https://api.openai.com", "port": 443, "health": "",
                   "startup_timeout": 10},
        "settings": {"llm": {"base_url": "https://api.openai.com/v1", "api_key_env": "LLM_API_KEY",
                             "model": "your-provider-model", "thinking": {"control": "none"}}},
    },
]

_CHATTERBOX_STREAMING = [                        # the Voice page's Streaming settings, see TTSStreamingConfig
    "--stream", "{tts.streaming.enabled}", "--chunk-secs", "{tts.streaming.chunk_secs}",
    "--first-secs", "{tts.streaming.first_secs}", "--lookahead-secs", "{tts.streaming.lookahead_secs}",
]

TTS: list[Fragment] = [
    {
        "id": "chatterbox", "icon": "chatterbox", "title": "Chatterbox-Turbo on GPU", "needs": ["cuda"],
        "hint": "Voice cloning from any clip in the voices directory. About 3 GB of VRAM, CUDA only, its own venv.",
        "engine": {
            "driver": "subprocess", "port": 8880, "health": "/health", "startup_timeout": 180,
            "cwd": "{repo}/services/tts-chatterbox",
            "cmd": ["{repo}/services/tts-chatterbox/.venv/bin/python", "server.py",
                    "--voice", "{data}/voices/vctk/p254_023.wav", "--voices-dir", "{data}/voices", "--port", "8880",
                    *_CHATTERBOX_STREAMING],
        },
        "settings": {"tts": {"base_url": "http://127.0.0.1:8880/v1", "api_key_env": None,
                             "voice": "vctk/p254_023", "sample_rate": 24000}},
    },
    {
        "id": "pocket", "icon": "pocket", "title": "Pocket TTS on CPU", "needs": [],
        "hint": "Kyutai's 100M model on two CPU cores, streaming. 26 built-in voices; cloning from a clip after "
                "accepting the terms on Hugging Face.",
        "engine": {
            "driver": "subprocess", "port": 8880, "health": "/health", "startup_timeout": 300,
            "cwd": "{repo}/services/tts-pocket",
            "cmd": ["{repo}/services/tts-pocket/.venv/bin/python", "server.py",
                    "--voice", "george", "--voices-dir", "{data}/voices", "--port", "8880"],
        },
        "settings": {"tts": {"base_url": "http://127.0.0.1:8880/v1", "api_key_env": None,
                             "voice": "george", "sample_rate": 24000}},
    },
    *({
        "id": id_, "icon": "huggingface", "title": title, "needs": ["cuda"], "hint": hint,
        "engine": {
            "driver": "subprocess", "port": 8880, "health": "/health", "startup_timeout": 300,
            "cwd": f"{{repo}}/services/tts-{id_}",
            "cmd": [f"{{repo}}/services/tts-{id_}/.venv/bin/python", "server.py",
                    "--voice", "{data}/voices/vctk/p254_023.wav", "--voices-dir", "{data}/voices", "--port", "8880"],
        },
        "settings": {"tts": {"base_url": "http://127.0.0.1:8880/v1", "api_key_env": None,
                             "voice": "vctk/p254_023", "sample_rate": rate}},
    } for id_, title, rate, hint in (
        ("qwen3", "Qwen3-TTS 1.7B on GPU", 24000,
         ("Clones from a clip and its words. About 6.5 GB of VRAM; first sound 0.9 s, slower than real time on a "
          "3090 beside the 27B brain.")),
        ("voxcpm2", "VoxCPM2 on GPU", 48000,
         ("Clones from a clip and its words, 48 kHz. About 6.9 GB of VRAM, which nearly fills a 24 GB card beside "
          "the 27B brain; first sound 0.9 s.")),
        ("gepard", "Gepard 1.1 on GPU", 22050,
         ("Clones from a clip alone. About 2.2 GB of VRAM and first sound in 0.5 s, but it drops and garbles more "
          "words than the others.")),
    )),
    {
        "id": "breeze2", "icon": "huggingface", "title": "Breeze TTS 2 on GPU", "needs": ["cuda"],
        "hint": "Clones from a clip and its words; the most natural of the local voices in blind pairs. About 5.9 GB "
                "of VRAM: beside the 27B brain only with Parakeet hearing. Weights for non-commercial use only.",
        "engine": {
            "driver": "subprocess", "port": 8880, "health": "/health", "startup_timeout": 300,
            "cwd": "{repo}/services/tts-breeze2",
            "cmd": ["{repo}/services/tts-breeze2/.venv/bin/python", "server.py",
                    "--voice", "{data}/voices/vctk/p254_023.wav", "--voices-dir", "{data}/voices", "--port", "8880",
                    "--fast", "backbone_decode", "depth_decoder", "codec", "--text-encoder-bits", "8"],
        },
        "settings": {"tts": {"base_url": "http://127.0.0.1:8880/v1", "api_key_env": None,
                             "voice": "vctk/p254_023", "sample_rate": 24000}},
    },
    {
        "id": "hosted", "icon": "cloud", "title": "Hosted (OpenAI speech URL)", "needs": ["internet"],
        "hint": "Any server speaking the OpenAI speech protocol. The key goes in .env. Speech leaves the LAN.",
        "engine": {"driver": "external", "url": "https://api.openai.com", "port": 443, "health": "",
                   "startup_timeout": 10},
        "settings": {"tts": {"base_url": "https://api.openai.com/v1", "api_key_env": "TTS_API_KEY",
                             "voice": "onyx", "sample_rate": 24000}},
    },
]

PROFILES: list[Fragment] = [
    {"id": "nvidia-24gb", "title": "NVIDIA, 24 GB", "presets": {"llm": "vllm", "tts": "chatterbox"},
     "hint": "The reference setup: vLLM and Chatterbox share one card."},
    {"id": "nvidia-24gb-27b", "title": "NVIDIA, 24 GB, bigger brain", "presets": {"llm": "llamacpp", "tts": "chatterbox"},
     "hint": "Qwen3.8-27B through llama.cpp instead of the 9B through vLLM: better at multi-part requests, "
             "same speed to the first word, no vision."},
    {"id": "ollama", "title": "Ollama, any GPU or CPU", "presets": {"llm": "ollama", "tts": "pocket"},
     "hint": "Ollama serves the model, Pocket TTS speaks on the CPU. Small cards, AMD, Apple silicon, no Docker."},
    {"id": "hosted", "icon": "cloud", "title": "Hosted LLM", "presets": {"llm": "hosted", "tts": "pocket"},
     "hint": "A cloud model over an OpenAI-compatible URL; hearing and speaking stay local."},
]


def _qwen3_asr(size: str, hint: str) -> Fragment:
    """Qwen3-ASR in services/stt-qwen3-asr (its own venv: `uv sync` there once), on port 8001."""
    return {
        "id": f"qwen3-asr-{size.lower()}", "icon": "huggingface", "title": f"Qwen3-ASR {size} on GPU",
        "needs": ["cuda"], "hint": hint,
        "engine": {
            "driver": "subprocess", "port": 8001, "health": "/health", "startup_timeout": 300,
            "cwd": "{repo}/services/stt-qwen3-asr",
            "cmd": ["{repo}/services/stt-qwen3-asr/.venv/bin/python", "server.py", "--model", "{stt.model}",
                    "--port", "8001"],
        },
        "settings": {"stt": {"provider": "openai_transcription", "base_url": "http://127.0.0.1:8001/v1",
                             "api_key_env": None, "model": f"Qwen/Qwen3-ASR-{size}", "quantization": None}},
    }


STT: list[Fragment] = [
    {
        "id": "parakeet", "icon": "parakeet", "title": "Parakeet (built in, CPU)", "needs": [],
        "hint": "NVIDIA Parakeet TDT 0.6B on the CPU through onnx-asr: about 0.2 s per sentence, keeps the GPU "
                "free. Understands 25 European languages; takes no hint words in advance.",
        "engine": None,
        "settings": {"stt": {"provider": "parakeet", "base_url": None, "api_key_env": None,
                             "model": "nemo-parakeet-tdt-0.6b-v3", "quantization": "int8"}},
    },
    _qwen3_asr("0.6B", "Hears with the house's names given up front, and tells English from other languages. "
                       "About 2.2 GB of VRAM; in a noisy room a third of Parakeet's errors, weaker on Swedish. The first "
                       "start downloads the model (1.9 GB)."),
    _qwen3_asr("1.7B", "The more accurate Qwen3-ASR, also with accents and in noisy rooms, with the same names "
                       "up front. About 5 GB of VRAM: beside the 27B brain it needs Pocket TTS instead of "
                       "Chatterbox, or a bigger card. The first start downloads the model (about 4.7 GB)."),
    {
        "id": "hosted", "icon": "cloud", "title": "Hosted (OpenAI transcription URL)", "needs": ["internet"],
        "hint": "Any server speaking OpenAI's transcription API. The key goes in .env. Your speech leaves the LAN.",
        "engine": {"driver": "external", "url": "https://api.openai.com", "port": 443, "health": "",
                   "startup_timeout": 10},
        "settings": {"stt": {"provider": "openai_transcription", "base_url": "https://api.openai.com/v1",
                             "api_key_env": "STT_API_KEY", "model": "whisper-1", "quantization": None}},
    },
]


def stt_presets() -> list[Fragment]:
    """The presets above, and a plain one for any other adapter a package registers (it runs in-process)."""
    known = {"parakeet", "openai_transcription"}
    return STT + [{"id": name, "icon": name, "title": name.capitalize(), "needs": [], "hint": f"Adapter {path}",
                   "engine": None, "settings": {"stt": {"provider": name}}}
                  for name, path in sorted(available("stt").items()) if name not in known]


def presets(kind: str) -> list[Fragment]:
    return {"llm": LLM, "tts": TTS, "stt": stt_presets()}[kind]


def preset(kind: str, preset_id: str) -> Fragment:
    for p in presets(kind):
        if p["id"] == preset_id:
            return p
    raise KeyError(f"no {kind} preset {preset_id!r}; available: {[p['id'] for p in presets(kind)]}")


def profile(profile_id: str) -> Fragment:
    for p in PROFILES:
        if p["id"] == profile_id:
            return p
    raise KeyError(f"no profile {profile_id!r}; available: {[p['id'] for p in PROFILES]}")


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else deepcopy(v)
    return out


def apply(config: dict, kind: str, preset_id: str) -> dict:
    """The config (as a plain dict) with the preset's engine block and settings in place of the current ones."""
    p = preset(kind, preset_id)
    out = deepcopy(config)
    engines = dict(out.get("engines") or {})
    if p.get("engine"):
        engines[kind] = deepcopy(p["engine"])
    else:
        engines.pop(kind, None)
    out["engines"] = engines
    return _merge(out, p.get("settings") or {})


def apply_profile(config: dict, profile_id: str) -> dict:
    out = config
    for kind, preset_id in profile(profile_id)["presets"].items():
        out = apply(out, kind, preset_id)
    return out
