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

TTS: list[Fragment] = [
    {
        "id": "chatterbox", "icon": "chatterbox", "title": "Chatterbox-Turbo on GPU", "needs": ["cuda"],
        "hint": "Voice cloning from any clip in the voices directory. About 3 GB of VRAM, CUDA only, its own venv.",
        "engine": {
            "driver": "subprocess", "port": 8880, "health": "/health", "startup_timeout": 180,
            "cwd": "{repo}/services/tts-chatterbox",
            "cmd": ["{repo}/services/tts-chatterbox/.venv/bin/python", "server.py",
                    "--voice", "{data}/voices/vctk/p254_023.wav", "--voices-dir", "{data}/voices", "--port", "8880"],
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
     "hint": "The reference setup: vLLM and Chatterbox share one card. About 1 s to the first word."},
    {"id": "ollama", "title": "Ollama, any GPU or CPU", "presets": {"llm": "ollama", "tts": "pocket"},
     "hint": "Ollama serves the model, Pocket TTS speaks on the CPU. Small cards, AMD, Apple silicon, no Docker."},
    {"id": "hosted", "icon": "cloud", "title": "Hosted LLM", "presets": {"llm": "hosted", "tts": "pocket"},
     "hint": "A cloud model over an OpenAI-compatible URL; hearing and speaking stay local."},
]


STT_HINTS = {
    "parakeet": "NVIDIA Parakeet TDT 0.6B on the CPU through onnx-asr: about 0.2 s per sentence, keeps the GPU "
                "free. v3 understands 25 European languages, v2 English only.",
}


def stt_presets() -> list[Fragment]:
    """STT adapters come from the registry; each is a preset with no engine (they run in-process)."""
    return [{"id": name, "icon": name, "title": name.capitalize(), "needs": [],
             "hint": STT_HINTS.get(name, f"Adapter {path}"), "engine": None,
             "settings": {"stt": {"provider": name}}} for name, path in sorted(available("stt").items())]


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
