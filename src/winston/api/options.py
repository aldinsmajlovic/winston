"""Live choices for the web UI's dropdowns. A config field names its list in the schema
(`options: "<name>"`) and the UI asks `/api/options/<name>`; every list is fetched from where the
truth lives (the brain's server, Home Assistant, the data directory, a provider's model list), so
nothing is typed by hand that a machine already knows. Secrets never leave: the env list names
variables and says whether they hold a value, nothing more. Every provider answers with a reason in
`source` rather than an error, so a dropdown still takes a typed value when its source is down."""
import inspect
import json
import os
import re
from collections import Counter
from pathlib import Path
from typing import get_args

import httpx
from dotenv import dotenv_values
from pydantic import BaseModel

from winston.api.secrets import env_file
from winston.config import data_dir, repo_root
from winston.config.registry import available
from winston.config.schema import WinstonConfig
from winston.core import memory
from winston.core.voices import clones
from winston.providers.home_assistant import HomeAssistant, state_text

TIMEOUT = 4

# Fallback when the Anthropic model list cannot be read (no API key: ask_claude runs on a login).
CLAUDE_MODELS = ["claude-opus-5", "claude-opus-5-5", "claude-sonnet-5", "claude-fable-5-1", "claude-haiku-4-5"]


class Choice(BaseModel):
    value: str
    label: str
    hint: str | None = None
    icon: str | None = None     # a brand or kind the UI draws: an HA domain, "set" or "missing" for env
    group: str | None = None
    disabled: bool = False      # listed for the record but not pickable, the hint says why


class Options(BaseModel):
    name: str
    choices: list[Choice]
    source: str = ""            # where the list came from, or why it is empty
    ok: bool = True             # False when the source could not be reached
    served_by: str | None = None  # llm_models: the engine the Downloaded group was read for, if known


class HomeAssistantCheck(BaseModel):
    ok: bool
    detail: str = ""
    version: str | None = None
    location: str | None = None


def _secret(name: str | None) -> str:
    return os.environ.get(name, "") if name else ""


def _env(cfg: WinstonConfig, params: dict) -> Options:
    values = dotenv_values(env_file()) if env_file().is_file() else {}
    choices = []
    for name, value in values.items():
        has = bool(value or os.environ.get(name))
        choices.append(Choice(value=name, label=name, icon="set" if has else "missing"))
    return Options(name="env", choices=choices, source=str(env_file()))


def _personas(cfg: WinstonConfig, params: dict) -> Options:
    d = data_dir()
    files = sorted(p.name for p in d.glob("*.md"))
    return Options(name="personas", choices=[Choice(value=f, label=f.removesuffix(".md")) for f in files], source=str(d))


def _sounds(cfg: WinstonConfig, params: dict) -> Options:
    choices = [Choice(value="", label="None")]
    for base, token in ((repo_root() / "assets/sounds", "{repo}/assets/sounds"), (data_dir() / "sounds", "{data}/sounds")):
        for f in sorted(base.glob("*.wav")) if base.is_dir() else []:
            choices.append(Choice(value=f"{token}/{f.name}", label=f.stem.capitalize(),
                                  group="Bundled" if token.startswith("{repo}") else "Yours"))
    return Options(name="sounds", choices=choices, source="assets/sounds and the data directory's sounds/")


def _stt_adapters(cfg: WinstonConfig, params: dict) -> Options:
    return Options(name="stt_adapters", source="registry",
                   choices=[Choice(value=n, label=n.capitalize(), hint=path, icon=n)
                            for n, path in sorted(available("stt").items())])


async def _stt_models(cfg: WinstonConfig, params: dict) -> Options:
    """A speech-to-text server's models (base_url, the draft's or the saved one), else onnx-asr's for Parakeet."""
    saved = cfg.stt.base_url if cfg.stt.provider == "openai_transcription" else None
    if url := (params.get("base_url") or saved or "").rstrip("/"):
        headers = {"Authorization": f"Bearer {key}"} if (key := _secret(cfg.stt.api_key_env)) else {}
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as c:
                r = await c.get(url + "/models", headers=headers)
            r.raise_for_status()
            ids = sorted(m.get("id", "") for m in r.json().get("data", []) if m.get("id"))
            return Options(name="stt_models", source=url,
                           choices=[Choice(value=i, label=i, group="Running") for i in ids])
        except Exception as e:                                          # noqa: BLE001
            return Options(name="stt_models", source=f"{url}: {type(e).__name__}", ok=False,
                           choices=[Choice(value=cfg.stt.model, label=cfg.stt.model)])
    try:
        from onnx_asr.loader import AsrNames
        names = list(get_args(AsrNames))
    except Exception:                                                   # noqa: BLE001
        names = [cfg.stt.model]
    cache = hf_cache(None)
    cached = [p.name.lower() for p in cache.glob("models--*")] if cache.is_dir() else []
    choices = []
    for n in sorted(names, key=lambda n: ("parakeet" not in n, n)):
        stem = n.removeprefix("nemo-").removeprefix("onnx-community/").lower()
        here = any(stem in c for c in cached)
        choices.append(Choice(value=n, label=n, hint="downloaded" if here else None,
                              group="Parakeet" if "parakeet" in n else "Other"))
    return Options(name="stt_models", choices=choices, source="onnx-asr")


HINT_ICONS = {"assistant": "assistant", "voice": "voice", "contact": "discord", "home_assistant": "homeassistant",
              "extension": "extension"}


def _hint_words(cfg: WinstonConfig, params: dict) -> Options:
    """The hint words Winston adds by himself (core/hints.py), each with where it comes from. `source`
    says what the current engine does with hint words."""
    from winston.config.registry import get as get_adapter
    from winston.core.hints import SOURCES, automatic
    try:
        takes = bool(getattr(get_adapter("stt", cfg.stt.provider), "supports_hints", False))
    except Exception:                                                   # noqa: BLE001
        takes = False
    name = cfg.stt.provider.capitalize()
    note = (f"{name} is given these words before it listens." if takes else
            f"{name} cannot be told words in advance; {cfg.assistant.name} corrects close misspellings "
            "afterwards.")
    if not cfg.stt.fix_misspellings and not takes:
        note = f"{name} cannot be told words in advance, and Fix close misspellings is off, so they are not used."
    seen: set[str] = set()
    choices = []
    for h in automatic(cfg):
        if h.word.lower() not in seen:
            seen.add(h.word.lower())
            choices.append(Choice(value=h.word, label=h.word, icon=HINT_ICONS[h.source], hint=SOURCES[h.source],
                                  group=h.source))
    return Options(name="hint_words", choices=choices, source=note)


# The brain's engines by what they load: llama.cpp and Ollama serve GGUF files, vLLM safetensors repos.
GGUF_ENGINES = {"llamacpp", "ollama"}
ENGINE_NAMES = {"llamacpp": "llama.cpp", "ollama": "Ollama", "vllm": "vLLM"}
# A GGUF file's quant: the last part of its name, after the model's name (Qwen3.8-27B-UD-Q4_K_S, x.Q4_K_M).
QUANT = re.compile(r"[-._]((?:UD-)?(?:[IT]?Q\d+(?:_[A-Z0-9]+)*|B?F16|F32|MXFP4(?:_MOE)?))$", re.IGNORECASE)
SPLIT = re.compile(r"-\d{5}-of-(\d{5})$")                 # model-Q4_K_M-00001-of-00003.gguf is one quant
LLM_ARCH = ("ForCausalLM", "ForConditionalGeneration", "LMHeadModel")
# Speech models built on an LLM (a TTS on Qwen) have an LLM config; their model card says what they are.
PIPELINE = re.compile(r"^pipeline_tag:\s*[\"']?([\w-]+)", re.MULTILINE)
SPEECH = {"text-to-speech", "text-to-audio", "automatic-speech-recognition", "audio-to-audio", "audio-classification",
          "voice-activity-detection"}


def llm_engine(engine: dict | None) -> str | None:
    """Which server the brain's engine block runs, from its image, command or URL: "llamacpp", "vllm",
    "ollama", or None for a hosted API or a server this does not know."""
    if not engine:
        return None
    text = " ".join([str(engine.get("image") or ""), *map(str, engine.get("args") or []),
                     *map(str, engine.get("cmd") or []), str(engine.get("url") or "")]).lower()
    if any(k in text for k in ("llama.cpp", "llama-server", "llama_cpp")):
        return "llamacpp"
    if "vllm" in text:
        return "vllm"
    if "ollama" in text or ":11434" in text:
        return "ollama"
    return None


def hf_cache(engine: dict | None) -> Path:
    """The Hugging Face hub cache the engine reads: the host side of a container's huggingface volume,
    else HF_HOME, else the default."""
    for host, inside in ((engine or {}).get("volumes") or {}).items():
        if str(inside).rstrip("/").endswith("huggingface"):
            return Path(host).expanduser() / "hub"
    return Path(os.environ.get("HF_HOME") or Path.home() / ".cache/huggingface").expanduser() / "hub"


def _size(n: int) -> str:
    return f"{n / 1e9:.1f} GB" if n >= 1e9 else f"{max(1, round(n / 1e6))} MB"


def _snapshot(repo: Path) -> Path | None:
    """The snapshot refs/main names, else the newest one."""
    ref = repo / "refs" / "main"
    if ref.is_file() and (snap := repo / "snapshots" / ref.read_text().strip()).is_dir():
        return snap
    snaps = sorted((repo / "snapshots").glob("*"), key=lambda p: p.stat().st_mtime) if (repo / "snapshots").is_dir() else []
    return snaps[-1] if snaps else None


def _bytes(files: list[Path]) -> int:
    """Size on disk of files that are really there (a snapshot links to blobs; a broken link is not)."""
    seen: set[Path] = set()
    total = 0
    for f in files:
        try:
            real = f.resolve(strict=True)
        except OSError:
            continue
        if real not in seen:
            seen.add(real)
            total += real.stat().st_size
    return total


def _is_llm(snap: Path) -> bool:
    try:
        arch = json.loads((snap / "config.json").read_text()).get("architectures") or []
    except Exception:                                                   # noqa: BLE001
        return False
    return any(str(a).endswith(LLM_ARCH) for a in arch)


def _speech(snap: Path) -> bool:
    try:
        with open(snap / "README.md", encoding="utf-8", errors="replace") as f:
            m = PIPELINE.search(f.read(4096))
    except OSError:
        return False
    return bool(m) and m.group(1) in SPEECH


def _complete(snap: Path) -> bool:
    """Every weight file is there: the shards the index names, and no link to a missing blob."""
    try:
        shards = set(json.loads((snap / "model.safetensors.index.json").read_text())["weight_map"].values())
    except Exception:                                                   # noqa: BLE001
        shards = set()
    return (all((snap / s).exists() for s in shards)
            and all(f.exists() for f in snap.rglob("*.safetensors")))


def downloaded(cache: Path) -> list[tuple[str, str, int]]:
    """Language models in the Hugging Face cache, no network: ("gguf", "repo:quant", bytes) for each
    quant present (split files as one), ("safetensors", "repo", bytes) for a repo with an LLM config.
    Speech models (no LLM config and no GGUF, or a model card that says speech) are left out."""
    out: list[tuple[str, str, int]] = []
    for repo in sorted(cache.glob("models--*")) if cache.is_dir() else []:
        name = repo.name.removeprefix("models--").replace("--", "/")
        if not (snap := _snapshot(repo)) or _speech(snap):
            continue
        quants: dict[str, list[Path]] = {}
        parts: dict[str, int] = {}             # how many files a split quant has; one when it is not split
        for f in snap.rglob("*.gguf"):
            split = SPLIT.search(f.name[:-5])
            stem = SPLIT.sub("", f.name[:-5])
            if "mmproj" in stem.lower():
                continue                       # a vision projector, not a model
            m = QUANT.search(stem)
            quant = m.group(1) if m else stem
            quants.setdefault(quant, []).append(f)
            parts[quant] = max(parts.get(quant, 1), int(split.group(1)) if split else 1)
        for quant, files in sorted(quants.items()):
            # A quant half downloaded (a split part missing, a link to no blob) is not on disk yet.
            if len(files) >= parts[quant] and all(f.exists() for f in files) and (size := _bytes(files)):
                out.append(("gguf", f"{name}:{quant}", size))
        if (not quants and any(snap.rglob("*.safetensors")) and _is_llm(snap) and _complete(snap)
                and (size := _bytes([f for f in snap.rglob("*") if f.is_file()]))):
            out.append(("safetensors", name, size))
    return out


def _downloaded_choices(kind: str, cache: Path) -> list[Choice]:
    ok, greyed = [], []
    for fmt, value, size in downloaded(cache):
        serves = (fmt == "gguf") == (kind in GGUF_ENGINES)
        if not serves:
            only = "vLLM only" if fmt == "safetensors" else "llama.cpp or Ollama"
            greyed.append(Choice(value=value, label=value, hint=f"{_size(size)}, {only}", group="Downloaded",
                                 disabled=True))
        elif kind == "ollama":
            # Ollama keeps its own store: it pulls the same file again from the Hub under hf.co/.
            ok.append(Choice(value=f"hf.co/{value}", label=value, hint=f"{_size(size)}, Ollama pulls its own copy",
                             group="Downloaded"))
        else:
            ok.append(Choice(value=value, label=value, hint=_size(size), group="Downloaded"))
    return ok + greyed


async def _llm_models(cfg: WinstonConfig, params: dict) -> Options:
    """What the brain's server serves now (Running) and what the engine could load from the Hugging
    Face cache without a download (Downloaded). `engine` is the draft engine block as JSON, else the
    saved one. `served_by` names the engine the downloads were read for; None when the engine is
    hosted or unknown, so nothing can be said about downloads."""
    url = (params.get("base_url") or cfg.llm.base_url).rstrip("/")
    try:
        engine = json.loads(params["engine"]) if params.get("engine") else None
    except ValueError:
        engine = None
    if not isinstance(engine, dict):
        engine = None
    elif not isinstance(engine.get("volumes") or {}, dict):
        engine = {**engine, "volumes": {}}
    saved = cfg.engines["llm"].model_dump() if "llm" in cfg.engines else None
    if engine is None:
        engine = saved
    kind = llm_engine(engine)
    # The folder read is the saved engine's, never one a request names: a GET must not list host paths.
    cache = hf_cache(saved)
    local = _downloaded_choices(kind, cache) if kind else []
    sizes = {c.value: c.hint for c in local if not c.disabled}
    headers = {"Authorization": f"Bearer {key}"} if (key := _secret(cfg.llm.api_key_env)) else {}
    running: list[Choice] = []
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.get(url + "/models", headers=headers)
        r.raise_for_status()
        ids = sorted(m.get("id", "") for m in r.json().get("data", []) if m.get("id"))
        running = [Choice(value=i, label=i, hint=sizes.get(i), group="Running") for i in ids]
        source, ok = url, True
    except Exception as e:                                              # noqa: BLE001
        source, ok = f"{url}: {type(e).__name__}", False
    here = {c.value for c in running}
    choices = running + [c for c in local if c.value not in here]
    if kind:
        source += f"; {ENGINE_NAMES[kind]} models in {cache}"
    return Options(name="llm_models", source=source, choices=choices, ok=ok, served_by=kind)


async def _voices(cfg: WinstonConfig, params: dict) -> Options:
    url = (params.get("base_url") or cfg.tts.base_url).rstrip("/").removesuffix("/v1")
    local = url.startswith(("http://127.", "http://localhost"))
    health: dict = {}
    ok = True
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.get(url + "/health")
        health = r.json() if r.status_code == 200 else {}
    except Exception:                                                   # noqa: BLE001
        ok = False
    default = health.get("default_voice") if local else None
    voices = data_dir() / "voices"
    # A clone is listed by the friend's name; its consent recording is a record, never a voice.
    cloned = {c.voice: c for c in clones()}
    consent = {f"friends/{c.slug}.consent" for c in cloned.values()}
    choices: list[Choice] = []
    for wav in sorted(voices.rglob("*.wav")) if voices.is_dir() else []:
        rel = wav.relative_to(voices).with_suffix("").as_posix()
        if rel in consent or rel in cloned or (rel.startswith("friends/") and rel.endswith(".consent")):
            continue
        choices.append(Choice(value=rel, label=rel, group="Your clips",
                              hint="server default" if rel == default else None))
    for v, c in cloned.items():
        choices.append(Choice(value=v, label=c.name, group="Cloned voices",
                              hint="cloned, server default" if v == default else "cloned"))
    if local:
        # Local servers map OpenAI's voice names to their default clip.
        builtin = health.get("builtin_voices", [])
        name = cloned[default].name if default in cloned else \
            default.capitalize() if default in builtin else default
        choices.insert(0, Choice(value="onyx", label=f"Server default: {name}" if name else "Server default",
                                 hint="onyx"))
    for v in health.get("builtin_voices", []):
        choices.append(Choice(value=v, label=v.capitalize(), group="Built in",
                              hint="server default" if v == default else None))
    return Options(name="voices", choices=choices, source=f"{voices} and {url}", ok=ok)


async def _gemini_models(cfg: WinstonConfig, params: dict) -> Options:
    key = _secret(cfg.tools.web_search.api_key_env)
    if not key:
        return Options(name="gemini_models", choices=[], source=f"{cfg.tools.web_search.api_key_env} is not set", ok=False)
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.get("https://generativelanguage.googleapis.com/v1beta/models",
                            params={"pageSize": 1000}, headers={"x-goog-api-key": key})
        r.raise_for_status()
        choices = []
        for m in r.json().get("models", []):
            name = m.get("name", "").removeprefix("models/")
            if not name.startswith("gemini") or "generateContent" not in m.get("supportedGenerationMethods", []):
                continue
            if any(k in name for k in ("image", "tts", "audio", "live", "embedding", "robotics", "computer-use")):
                continue            # not text models; search needs a plain chat model
            choices.append(Choice(value=name, label=m.get("displayName") or name, hint=name, icon="gemini",
                                  group="Flash-Lite" if "lite" in name else "Flash" if "flash" in name else "Pro"
                                  if "pro" in name else "Other"))
        choices.sort(key=lambda c: (c.group, c.value))
        return Options(name="gemini_models", choices=choices, source="Gemini API")
    except Exception as e:                                              # noqa: BLE001
        return Options(name="gemini_models", choices=[], source=f"Gemini API: {type(e).__name__}", ok=False)


async def _claude_models(cfg: WinstonConfig, params: dict) -> Options:
    fallback = Options(name="claude_models", source="built-in list",
                       choices=[Choice(value=m, label=m, icon="claude") for m in CLAUDE_MODELS])
    key = _secret("ANTHROPIC_API_KEY")
    if not key:
        return fallback
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.get("https://api.anthropic.com/v1/models", params={"limit": 100},
                            headers={"x-api-key": key, "anthropic-version": "2023-06-01"})
        r.raise_for_status()
        choices = [Choice(value=m["id"], label=m.get("display_name") or m["id"], hint=m["id"], icon="claude")
                   for m in r.json().get("data", [])]
        return Options(name="claude_models", choices=choices, source="Anthropic API") if choices else fallback
    except Exception:                                                   # noqa: BLE001
        return fallback


def _ha(cfg: WinstonConfig):
    """The Home Assistant settings, or None when there are none or they are switched off."""
    return cfg.providers.ha


def _ha_off(cfg: WinstonConfig) -> bool:
    return cfg.providers.home_assistant is not None and cfg.providers.ha is None


async def _ha_states(cfg: WinstonConfig, params: dict) -> tuple[list[dict], str]:
    # Switched off, the saved URL is not used; a URL the UI passes for a draft still is.
    url = params.get("base_url") or (cfg.providers.ha.url if _ha(cfg) else "")
    if not url:
        raise RuntimeError("Home Assistant is turned off" if _ha_off(cfg) else "Home Assistant is not configured")
    ha = HomeAssistant(cfg, url)
    return await ha.get("/api/states"), ha.url


async def _ha_entities(cfg: WinstonConfig, params: dict) -> Options:
    domains = {d for d in (params.get("domain") or "").split(",") if d}
    try:
        states, url = await _ha_states(cfg, params)
    except Exception as e:                                              # noqa: BLE001
        return Options(name="ha_entities", choices=[], source=str(e) or type(e).__name__, ok=False)
    choices = []
    for s in sorted(states, key=lambda s: s["entity_id"]):
        domain = s["entity_id"].split(".")[0]
        if domains and domain not in domains:
            continue
        choices.append(Choice(value=s["entity_id"], label=s.get("attributes", {}).get("friendly_name") or s["entity_id"],
                              hint=state_text(s) or "", icon=domain, group=domain))
    return Options(name="ha_entities", choices=choices, source=url)


async def _ha_notify(cfg: WinstonConfig, params: dict) -> Options:
    """Home Assistant's notify services (the companion app's is the phone), from its service list."""
    ha = _ha(cfg)
    if ha is None:
        return Options(name="ha_notify", choices=[], source="Home Assistant is switched off", ok=False)
    try:
        listing = await HomeAssistant(cfg).get("/api/services")
    except Exception as e:                                              # noqa: BLE001
        return Options(name="ha_notify", choices=[], source=str(e) or type(e).__name__, ok=False)
    names = sorted(s for d in listing if d.get("domain") == "notify" for s in d.get("services", {}))
    return Options(name="ha_notify", source=ha.url, choices=[
        Choice(value=f"notify.{n}", label=n.removeprefix("mobile_app_").replace("_", " ").capitalize(),
               hint=f"notify.{n}", icon="phone" if n.startswith("mobile_app_") else "notify") for n in names])


async def _ha_domains(cfg: WinstonConfig, params: dict) -> Options:
    try:
        states, url = await _ha_states(cfg, params)
    except Exception as e:                                              # noqa: BLE001
        return Options(name="ha_domains", choices=[], source=str(e) or type(e).__name__, ok=False)
    counts = Counter(s["entity_id"].split(".")[0] for s in states)
    return Options(name="ha_domains", source=url,
                   choices=[Choice(value=d, label=d.replace("_", " ").capitalize(), hint=str(n), icon=d)
                            for d, n in sorted(counts.items())])


async def _ha_areas(cfg: WinstonConfig, params: dict) -> Options:
    """Home Assistant's areas, from its registry (needs an admin token), with how many exposed entities each has."""
    ha = _ha(cfg)
    if ha is None:
        return Options(name="ha_areas", choices=[], ok=False,
                       source="Home Assistant is turned off" if _ha_off(cfg) else "Home Assistant is not configured")
    try:
        snap = await HomeAssistant(cfg).snapshot()
    except Exception as e:                                              # noqa: BLE001
        return Options(name="ha_areas", choices=[], source=str(e) or type(e).__name__, ok=False)
    if snap.registry is None:
        return Options(name="ha_areas", choices=[], source="; ".join(snap.errors) or "no registry", ok=False)
    exposed = Counter(aid for eid in snap.exposed or () if (aid := snap.area(eid)[0]))
    return Options(name="ha_areas", source=ha.url, choices=[
        Choice(value=name, label=name, hint=f"{exposed[aid]} exposed")
        for aid, name in sorted(snap.areas.items(), key=lambda a: a[1].casefold())])


DISCORD_API = "https://discord.com/api/v10"
DISCORD_TIMEOUT = 3


def _discord_user_choice(u: dict, nick: str | None = None, group: str | None = None) -> Choice:
    username = u.get("username") or u.get("id", "")
    return Choice(value=str(u.get("id", "")), label=nick or u.get("global_name") or username, hint=username,
                  icon="discord", group=group)


async def _discord_users(cfg: WinstonConfig, params: dict) -> Options:
    """People the bot shares a server with (needs Server Members Intent), or with q a user id looked up alone.
    Every failure is a reason in `source`, never an error: the field still takes a pasted id."""
    name = "discord_users"
    env = cfg.channels.discord.token_env
    if not (token := _secret(env).strip()):
        return Options(name=name, choices=[], ok=False, source=f"{env} is not set, so Discord cannot be asked")
    headers = {"Authorization": f"Bot {token}"}
    q = (params.get("q") or "").strip()
    try:
        async with httpx.AsyncClient(base_url=DISCORD_API, timeout=DISCORD_TIMEOUT, headers=headers) as c:
            if q:
                if not (q.isascii() and q.isdigit()):
                    return Options(name=name, choices=[], ok=False, source="A Discord user id is a number")
                r = await c.get(f"/users/{q}")
                if r.status_code == 404:
                    return Options(name=name, choices=[], ok=False, source=f"Discord knows no user {q}")
                if r.status_code == 401:
                    return Options(name=name, choices=[], ok=False, source=f"Discord refused the bot token ({env})")
                r.raise_for_status()
                return Options(name=name, choices=[_discord_user_choice(r.json())], source="Discord")
            r = await c.get("/users/@me/guilds")
            if r.status_code == 401:
                return Options(name=name, choices=[], ok=False, source=f"Discord refused the bot token ({env})")
            r.raise_for_status()
            guilds = r.json()
            choices: dict[str, Choice] = {}
            closed: list[str] = []
            for g in guilds:
                m = await c.get(f"/guilds/{g['id']}/members", params={"limit": 1000})
                if m.status_code == 403:
                    closed.append(g.get("name", g["id"]))
                    continue
                m.raise_for_status()
                for member in m.json():
                    u = member.get("user") or {}
                    if u.get("bot") or not u.get("id"):
                        continue
                    choices.setdefault(str(u["id"]), _discord_user_choice(u, member.get("nick"), g.get("name")))
    except httpx.TimeoutException:
        return Options(name=name, choices=[], ok=False, source="Discord did not answer in time")
    except Exception as e:                                              # noqa: BLE001
        return Options(name=name, choices=[], ok=False, source=f"Discord: {type(e).__name__}")
    if not guilds:
        return Options(name=name, choices=[], ok=False, source="The bot is in no server yet; invite it to one")
    source = "Discord servers: " + ", ".join(g.get("name", g["id"]) for g in guilds)
    if closed:
        source = (f"Turn on Server Members Intent for the bot to list people in {', '.join(closed)}"
                  if not choices else source + f" (no member list from {', '.join(closed)}: Server Members Intent)")
    return Options(name=name, choices=sorted(choices.values(), key=lambda c: (c.group or "", c.label.lower())),
                   source=source, ok=not closed or bool(choices))


def _memory_topics(cfg: WinstonConfig, params: dict) -> Options:
    """His memory's topics (core/memory.py): the file's name is the value, its title the label."""
    return Options(name="memory_topics", source=str(memory.vault(cfg)), choices=[
        Choice(value=t, label=memory.title(cfg, t), hint=memory.description(cfg, t) or None)
        for t in memory.topics(cfg)])


PROVIDERS = {
    "env": _env, "personas": _personas, "sounds": _sounds, "stt_adapters": _stt_adapters, "stt_models": _stt_models,
    "hint_words": _hint_words, "memory_topics": _memory_topics,
    "llm_models": _llm_models, "voices": _voices, "gemini_models": _gemini_models, "claude_models": _claude_models,
    "ha_entities": _ha_entities, "ha_domains": _ha_domains, "ha_notify": _ha_notify, "ha_areas": _ha_areas, "discord_users": _discord_users,
}


async def options(name: str, cfg: WinstonConfig, params: dict) -> Options | None:
    fn = PROVIDERS.get(name)
    if fn is None:
        return None
    out = fn(cfg, params)
    return await out if inspect.isawaitable(out) else out


async def check_home_assistant(cfg: WinstonConfig, url: str | None) -> HomeAssistantCheck:
    """Can Winston reach Home Assistant with its token, and which one is it."""
    url = url or (cfg.providers.ha.url if _ha(cfg) else "")
    if not url:
        return HomeAssistantCheck(ok=False, detail="Turned off" if _ha_off(cfg) else "No URL")
    try:
        j = await HomeAssistant(cfg, url).ping()
    except RuntimeError as e:                                           # the token: not set, or rejected
        return HomeAssistantCheck(ok=False, detail=str(e))
    except Exception as e:                                              # noqa: BLE001
        return HomeAssistantCheck(ok=False, detail=f"Not reachable ({type(e).__name__})")
    return HomeAssistantCheck(ok=True, version=j["version"], location=j["location"],
                              detail=f"{j['location']}, Home Assistant {j['version']}")
