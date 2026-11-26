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
    "--max-model-len", "{llm.context}",
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
    "-c", "{llm.context_both}", "-np", "2",      # llm.context per slot: his prompt alone is 10.5k
    "-ctk", "q8_0", "-ctv", "q8_0",
    "--no-mmproj",                               # text only; the vision projector is another 0.9 GB
    "--jinja",
    "--cache-ram", "2048",                       # old prompts in host RAM (default 8 GB); the talk stays on the card
    "--spec-type", "draft-mtp", "--spec-draft-n-max", "2",   # the GGUF's own MTP layer: 44 -> 60-80 tokens/s
]

LLM: list[Fragment] = [
    {
        "id": "vllm", "icon": "vllm", "title": "vLLM on NVIDIA (Docker)", "needs": ["docker", "nvidia"],
        "hint": "The fastest at actions, and he can see images. Needs Docker with NVIDIA's toolkit and a 16 GB card, "
                "and takes a few minutes to start.",
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
        "hint": "Fits the biggest brain on one card, so he gets the most requests right. Needs Docker with NVIDIA's "
                "toolkit; he cannot see images.",
        "engine": {
            "driver": "docker", "image": "ghcr.io/ggml-org/llama.cpp:server-cuda-v1.0.0-b12359-b1b770d", "port": 8000,
            "health": "/health", "startup_timeout": 900, "gpu": True, "runtime": "nvidia",
            "env": {"NVIDIA_VISIBLE_DEVICES": "all", "NVIDIA_DRIVER_CAPABILITIES": "compute,utility"},
            "volumes": {"~/.cache/huggingface": "/root/.cache/huggingface"},   # llama.cpp reads the HF cache
            "args": list(_LLAMACPP_ARGS),
        },
        "settings": {"llm": {"base_url": "http://127.0.0.1:8000/v1", "api_key_env": None,
                             "model": "unsloth/Qwen3.8-27B-GGUF:UD-Q4_K_S", "thinking": {"control": "chat_template"}}},
    },
    {
        "id": "llamacpp-amd", "icon": "llamacpp", "title": "llama.cpp on AMD (Docker)", "needs": ["docker", "amd"],
        "hint": "The same as on NVIDIA, on nearly any AMD card. Needs Docker; not yet tried on one, where Ollama is "
                "the tried way.",
        "engine": {
            "driver": "docker", "image": "ghcr.io/ggml-org/llama.cpp:server-vulkan-v1.0.0-b12359-b1b770d",
            "port": 8000, "health": "/health", "startup_timeout": 900, "devices": ["/dev/dri"],
            "volumes": {"~/.cache/huggingface": "/root/.cache/huggingface"},
            "args": list(_LLAMACPP_ARGS),
        },
        "settings": {"llm": {"base_url": "http://127.0.0.1:8000/v1", "api_key_env": None,
                             "model": "unsloth/Qwen3.8-27B-GGUF:UD-Q4_K_S", "thinking": {"control": "chat_template"}}},
    },
    {
        "id": "ollama", "icon": "ollama", "title": "Ollama (local)", "needs": ["ollama"],
        "hint": "The simplest, on any graphics card or on the processor without one. Needs Ollama; on a small card, "
                "pick a smaller model below.",
        "engine": {"driver": "external", "url": "http://127.0.0.1:11434", "port": 11434, "health": "/",
                   "startup_timeout": 60},
        "settings": {"llm": {"base_url": "http://127.0.0.1:11434/v1", "api_key_env": None,
                             "model": "granite4.2:8b", "thinking": {"control": "reasoning_effort"}}},
    },
    {
        "id": "network", "icon": "server", "title": "A server on your network", "needs": [],
        "hint": "A brain you already run on another computer, such as Ollama, vLLM or llama.cpp. Needs its address "
                "below, and a key only if it asks for one.",
        # No address of its own: the API address on the card is the one checked (WinstonConfig.engine_at).
        "engine": {"driver": "external", "port": 8000, "health": "/v1/models", "startup_timeout": 30},
        "settings": {"llm": {"base_url": "http://192.168.1.10:8000/v1", "api_key_env": None, "model": "",
                             "thinking": {"control": "chat_template"}}},
    },
    {
        "id": "hosted", "icon": "cloud", "title": "Hosted (OpenAI-compatible URL)", "needs": ["internet"],
        "hint": "A cloud model, with nothing to run here. Needs the provider's key, and your requests leave your "
                "network.",
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
        "id": "chatterbox", "icon": "chatterbox", "title": "Chatterbox-Turbo on the graphics card", "needs": ["cuda"],
        "hint": "Clones a voice from a short clip, in English only. Needs an NVIDIA card.",
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
        "id": "pocket", "icon": "pocket", "title": "Pocket TTS on the processor", "needs": [],
        "hint": "Leaves the graphics card free, with 26 voices to pick from. Cloning a voice needs its terms "
                "accepted on Hugging Face.",
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
        ("qwen3", "Qwen3-TTS 1.7B on the graphics card", 24000,
         ("Clones a voice from a clip and its words. Needs an NVIDIA card; on one shared with the brain it can "
          "fall behind its own speech.")),
        ("voxcpm2", "VoxCPM2 on the graphics card", 48000,
         "Clones a voice from a clip and its words, in the finest sound. Needs an NVIDIA card."),
        ("gepard", "Gepard 1.1 on the graphics card", 22050,
         ("Clones a voice from a clip alone, and starts speaking soonest. Needs an NVIDIA card; it drops and "
          "garbles more words than the others.")),
    )),
    {
        "id": "breeze2", "icon": "huggingface", "title": "Breeze TTS 2 on the graphics card", "needs": ["cuda"],
        "hint": "The most natural voice that runs here, cloned from a clip and its words. Needs an NVIDIA card, "
                "and is for non-commercial use only.",
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
        "hint": "A cloud voice, OpenRouter by default, with one key for ElevenLabs, Gemini and others. Needs the "
                "provider's key, and what he says leaves your network.",
        # ponytail: model, voice and rate not yet measured against OpenRouter (docs/voices.md, Hosted voice)
        "engine": {"driver": "external", "url": "https://openrouter.ai", "port": 443, "health": "",
                   "startup_timeout": 10},
        "settings": {"tts": {"base_url": "https://openrouter.ai/api/v1", "api_key_env": "TTS_API_KEY",
                             "model": "elevenlabs/eleven-flash-v2.5", "voice": "george", "sample_rate": 24000}},
    },
]

PROFILES: list[Fragment] = [
    {"id": "nvidia-24gb", "title": "NVIDIA, 24 GB", "presets": {"llm": "llamacpp", "tts": "breeze2"},
     "hint": "Qwen3.8-27B through llama.cpp and Breeze TTS 2 share one card (about 23 GB); hearing on the CPU."},
    {"id": "nvidia-16gb", "title": "NVIDIA, 16 GB", "presets": {"llm": "ollama", "tts": "breeze2"},
     "hint": "Granite 4.2 8B on Ollama and Breeze TTS 2 share one card (about 12 GB); hearing on the CPU."},
    {"id": "nvidia-12gb", "title": "NVIDIA, 12 GB", "presets": {"llm": "ollama", "tts": "chatterbox"},
     "hint": "Granite 4.2 8B on Ollama and Chatterbox-Turbo share one card (about 10 GB); hearing on the CPU."},
    {"id": "ollama", "title": "Ollama, any GPU or CPU", "presets": {"llm": "ollama", "tts": "pocket"},
     "hint": "Ollama serves the model, Pocket TTS speaks on the CPU. Small cards, AMD, Apple silicon, no Docker."},
    {"id": "nvidia-8gb", "title": "NVIDIA, 8 GB or less", "presets": {"llm": "ollama", "tts": "pocket"},
     "settings": {"llm": {"model": "qwen3.5:4b"}},
     "hint": "Qwen3.5 4B on Ollama (about 4 GB), Pocket TTS and hearing on the CPU."},
    {"id": "hosted", "icon": "cloud", "title": "Hosted LLM", "presets": {"llm": "hosted", "tts": "pocket"},
     "hint": "A cloud model over an OpenAI-compatible URL; hearing and speaking stay local."},
]


def _qwen3_asr(size: str, hint: str) -> Fragment:
    """Qwen3-ASR in services/stt-qwen3-asr (its own venv: `uv sync` there once), on port 8001."""
    return {
        "id": f"qwen3-asr-{size.lower()}", "icon": "huggingface", "title": f"Qwen3-ASR {size} on the graphics card",
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
        "id": "fluid-speech-pro", "icon": "huggingface", "title": "Fluid Speech Pro on the processor (built in)", "needs": [],
        "hint": "Hears 25 languages, and stays right when other people talk. Needs nothing more; the first start "
                "downloads it.",
        "engine": None,
        "settings": {"stt": {"provider": "parakeet", "base_url": None, "api_key_env": None,
                             "model": "FluidVoice-Labs/fluid-speech-pro-onnx", "quantization": "int8"}},
    },
    {
        "id": "parakeet", "icon": "parakeet", "title": "Parakeet on the processor (built in)", "needs": [],
        "hint": "Hears 25 European languages, the quickest. Needs nothing more; misheard names are corrected "
                "afterwards, not known in advance.",
        "engine": None,
        "settings": {"stt": {"provider": "parakeet", "base_url": None, "api_key_env": None,
                             "model": "nemo-parakeet-tdt-0.6b-v3", "quantization": "int8"}},
    },
    _qwen3_asr("0.6B", "Hears the names he should hear right, given up front, and tells languages apart. Needs an "
                       "NVIDIA card; weaker on some languages."),
    _qwen3_asr("1.7B", "Hears best with accents and in noisy rooms, with the names he should hear right given up "
                       "front. Needs an NVIDIA card."),
    {
        "id": "hosted", "icon": "cloud", "title": "Hosted (OpenAI transcription URL)", "needs": ["internet"],
        "hint": "A cloud service that hears for him. Needs the provider's key, and your voice leaves your network.",
        "engine": {"driver": "external", "url": "https://api.openai.com", "port": 443, "health": "",
                   "startup_timeout": 10},
        "settings": {"stt": {"provider": "openai_transcription", "base_url": "https://api.openai.com/v1",
                             "api_key_env": "STT_API_KEY", "model": "whisper-1", "quantization": None}},
    },
]


def stt_presets() -> list[Fragment]:
    """The presets above, and a plain one for any other adapter a package registers (it runs in-process)."""
    known = {"parakeet", "openai_transcription"}
    return STT + [{"id": name, "icon": name, "title": name.capitalize(), "needs": [],
                   "hint": "From an installed package. Runs inside Winston.",
                   "engine": None, "settings": {"stt": {"provider": name}}}
                  for name in sorted(available("stt")) if name not in known]


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
    p, out = profile(profile_id), config
    for kind, preset_id in p["presets"].items():
        out = apply(out, kind, preset_id)
    return _merge(out, p.get("settings") or {})
