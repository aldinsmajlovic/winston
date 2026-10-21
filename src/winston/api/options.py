"""Live choices for the web UI's dropdowns. A config field names its list in the schema
(`options: "<name>"`) and the UI asks `/api/options/<name>`; every list is fetched from where the
truth lives (the brain's server, Home Assistant, the data directory, a provider's model list), so
nothing is typed by hand that a machine already knows. Secrets never leave: no list holds a key's
value. Every provider answers with a reason in `source` rather than an error, so a dropdown still takes
a typed value when its source is down."""
import inspect
import json
import os
import re
import unicodedata
from collections import Counter
from pathlib import Path
from typing import get_args

import httpx
from pydantic import BaseModel

from winston.config import data_dir, repo_root
from winston.config.presets import LLM
from winston.config.schema import WinstonConfig
from winston.core import memory
from winston.core.common import failure_words
from winston.core.voices import clones
from winston.providers.home_assistant import HomeAssistant, state_text

TIMEOUT = 4

class Choice(BaseModel):
    value: str
    label: str
    hint: str | None = None
    icon: str | None = None     # a brand or kind the UI draws, such as an HA domain
    group: str | None = None
    disabled: bool = False      # listed for the record but not pickable, the hint says why
    image: str | None = None    # a picture's address (an extension's own list: a playlist's cover)


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


def _control_hosts(cfg: WinstonConfig, params: dict) -> Options:
    """Who can open the web UI, in his words; the field still takes a typed address."""
    return Options(name="control_hosts", choices=[Choice(value="127.0.0.1", label="This computer only"),
                                                  Choice(value="0.0.0.0", label="Every device on your network")])


def _sounds(cfg: WinstonConfig, params: dict) -> Options:
    choices = [Choice(value="", label="None")]
    for base, token in ((repo_root() / "assets/sounds", "{repo}/assets/sounds"), (data_dir() / "sounds", "{data}/sounds")):
        for f in sorted(base.glob("*.wav")) if base.is_dir() else []:
            choices.append(Choice(value=f"{token}/{f.name}", label=f.stem.capitalize(),
                                  group="Bundled" if token.startswith("{repo}") else "Yours"))
    return Options(name="sounds", choices=choices, source="assets/sounds and the data directory's sounds/")


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
            return Options(name="stt_models", source=f"Can't reach the hearing server: {failure_words(e)}", ok=False,
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
    """The hint words Winston adds by himself (core/hints.py), grouped by where they come from: an
    extension's words under `extension:<id>`, with its name as the hint and its icon. Common words are
    left out, since the correction never touches them. `source` says what hearing does with the words."""
    from winston.config.registry import get as get_adapter
    from winston.core.hints import SOURCES, automatic, is_common
    try:
        takes = bool(getattr(get_adapter("stt", cfg.stt.provider), "supports_hints", False))
    except Exception:                                                   # noqa: BLE001
        takes = False
    note = ("Hearing is given these words before it listens." if takes else
            "" if cfg.stt.fix_misspellings else "Correct misheard names is off, so these names are not used.")
    host = params.get("extensions")
    seen: set[str] = set()
    choices = []
    for h in automatic(cfg):
        if h.word.lower() in seen or is_common(h.word):
            continue
        seen.add(h.word.lower())
        manifest = host.described.get(h.origin, {}).get("manifest", {}) if host and h.origin else {}
        choices.append(Choice(value=h.word, label=h.word, icon=manifest.get("icon") or HINT_ICONS[h.source],
                              hint=manifest.get("name") or h.origin or SOURCES[h.source],
                              group=f"extension:{h.origin}" if h.origin else h.source))
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
    """What the brain's server serves now (Running), what the engine could load from the Hugging
    Face cache without a download (Downloaded), and the presets' models for that engine (Suggested), so a
    stopped engine with nothing downloaded still offers a pick. `engine` is the draft engine block as JSON,
    else the saved one. `served_by` names the engine the downloads were read for; None when the engine is
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
        source, ok = f"Can't reach the brain's server: {failure_words(e)}", False
    here = {c.value for c in running}
    choices = running + [c for c in local if c.value not in here]
    have = {c.value for c in choices}
    for p in LLM:
        m = p["settings"]["llm"]["model"]
        if kind and llm_engine(p.get("engine")) == kind and m not in have:
            choices.append(Choice(value=m, label=m, hint="not downloaded yet", group="Suggested"))
    if kind and ok:
        source += f"; {ENGINE_NAMES[kind]} models in {cache}"
    return Options(name="llm_models", source=source, choices=choices, ok=ok, served_by=kind)


async def _voices(cfg: WinstonConfig, params: dict) -> Options:
    url = (params.get("base_url") or cfg.tts.base_url).rstrip("/").removesuffix("/v1")
    local = url.startswith(("http://127.", "http://localhost"))
    health: dict = {}
    trouble = None
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.get(url + "/health")
        health = r.json() if r.status_code == 200 else {}
    except Exception as e:                                              # noqa: BLE001
        trouble = f"Can't reach the voice server: {failure_words(e)}"
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
        # A clip in a folder is listed by its file name under the folder's name.
        folder, _, stem = rel.rpartition("/")
        choices.append(Choice(value=rel, label=stem, group=folder or "Your clips",
                              hint="server default" if rel == default else None))
    for v, c in cloned.items():
        choices.append(Choice(value=v, label=c.name, group="Your voices",
                              hint="server default" if v == default else None))
    if local:
        # Local servers map OpenAI's voice names to their default clip.
        builtin = health.get("builtin_voices", [])
        name = cloned[default].name if default in cloned else \
            default.capitalize() if default in builtin else default and default.rpartition("/")[2]
        choices.insert(0, Choice(value="onyx", label=f"Server default: {name}" if name else "Server default"))
    for v in health.get("builtin_voices", []):
        choices.append(Choice(value=v, label=v.capitalize(), group="Built in",
                              hint="server default" if v == default else None))
    return Options(name="voices", choices=choices, source=trouble or f"{voices} and {url}", ok=trouble is None)


def _ha(cfg: WinstonConfig):
    """The Home Assistant settings, or None when there are none or they are switched off."""
    return cfg.providers.ha


def _ha_off(cfg: WinstonConfig) -> bool:
    return cfg.providers.home_assistant is not None and cfg.providers.ha is None


def _ha_unset(cfg: WinstonConfig) -> str:
    return "Home Assistant is switched off" if _ha_off(cfg) else "Connect Home Assistant on its page first."


class HAUnset(Exception):
    """Home Assistant is switched off or not set up; the text says which."""


def _ha_trouble(e: Exception) -> str:
    """Why a Home Assistant list could not be read, as what to do about it."""
    if isinstance(e, HAUnset):
        return str(e)
    if str(e).endswith(" is not set"):                                  # HomeAssistant(): no token
        return "Paste the access token on the Home Assistant page first."
    if str(e) == "Token rejected":
        return "Home Assistant refused the access token. Make a new one and paste it on the Home Assistant page."
    return f"Can't reach Home Assistant: {failure_words(e)}"


async def _ha_states(cfg: WinstonConfig, params: dict) -> tuple[list[dict], str]:
    # Switched off, the saved URL is not used; a URL the UI passes for a draft still is.
    url = params.get("base_url") or (cfg.providers.ha.url if _ha(cfg) else "")
    if not url:
        raise HAUnset(_ha_unset(cfg))
    ha = HomeAssistant(cfg, url)
    return await ha.get("/api/states"), ha.url


async def _ha_entities(cfg: WinstonConfig, params: dict) -> Options:
    domains = {d for d in (params.get("domain") or "").split(",") if d}
    try:
        states, url = await _ha_states(cfg, params)
    except Exception as e:                                              # noqa: BLE001
        return Options(name="ha_entities", choices=[], source=_ha_trouble(e), ok=False)
    choices = []
    for s in sorted(states, key=lambda s: s["entity_id"]):
        domain = s["entity_id"].split(".")[0]
        if domains and domain not in domains:
            continue
        choices.append(Choice(value=s["entity_id"], label=s.get("attributes", {}).get("friendly_name") or s["entity_id"],
                              hint=state_text(s) or "", icon=domain, group=domain))
    return Options(name="ha_entities", choices=choices, source=url)


def _ha_slug(name: str) -> str:
    """Home Assistant's slugify: "Galaxy S24" is "galaxy_s24", as in the companion app's notify service."""
    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "_", plain).strip("_")


async def _ha_notify(cfg: WinstonConfig, params: dict) -> Options:
    """Home Assistant's notify services (the companion app's is the phone), from its service list. A phone is
    named as its device is in Home Assistant ("Galaxy S24") when the token may read the device registry, else
    by its service's name in words."""
    ha = _ha(cfg)
    if ha is None:
        return Options(name="ha_notify", choices=[], source=_ha_unset(cfg), ok=False)
    try:
        listing = await HomeAssistant(cfg).get("/api/services")
    except Exception as e:                                              # noqa: BLE001
        return Options(name="ha_notify", choices=[], source=_ha_trouble(e), ok=False)
    try:
        devices, _ = await HomeAssistant(cfg).devices()
    except Exception:                                                   # noqa: BLE001  (not an admin token)
        devices = []
    # The app's service is named after the device as it registered (`name`); a rename in HA is `name_by_user`.
    phones = {f"mobile_app_{_ha_slug(d['name'])}": d.get("name_by_user") or d["name"] for d in devices
              if d.get("name") and any(i[0] == "mobile_app" for i in d.get("identifiers") or [])}
    # Home Assistant's own notifications panel is no way to reach a person.
    names = sorted(s for d in listing if d.get("domain") == "notify" for s in d.get("services", {})
                   if s != "persistent_notification")
    return Options(name="ha_notify", source=ha.url, choices=[
        Choice(value=f"notify.{n}", label=phones.get(n) or n.removeprefix("mobile_app_").replace("_", " ").capitalize(),
               hint=f"notify.{n}", icon="phone" if n.startswith("mobile_app_") else "notify") for n in names])


# Domains as a person names them where the id in words is not that, as the Home Assistant page (kindName) does.
DOMAIN_NAMES = {"person": "People", "todo": "To-do lists", "zone": "Zones"}


async def _ha_domains(cfg: WinstonConfig, params: dict) -> Options:
    try:
        states, url = await _ha_states(cfg, params)
    except Exception as e:                                              # noqa: BLE001
        return Options(name="ha_domains", choices=[], source=_ha_trouble(e), ok=False)
    counts = Counter(s["entity_id"].split(".")[0] for s in states)
    return Options(name="ha_domains", source=url,
                   choices=[Choice(value=d, label=DOMAIN_NAMES.get(d) or d.replace("_", " ").capitalize(),
                                   hint=str(n), icon=d) for d, n in sorted(counts.items())])


async def _ha_areas(cfg: WinstonConfig, params: dict) -> Options:
    """Home Assistant's areas, from its registry (needs an admin token), with how many exposed entities each has."""
    ha = _ha(cfg)
    if ha is None:
        return Options(name="ha_areas", choices=[], ok=False, source=_ha_unset(cfg))
    try:
        snap = await HomeAssistant(cfg).snapshot()
    except Exception as e:                                              # noqa: BLE001
        return Options(name="ha_areas", choices=[], source=_ha_trouble(e), ok=False)
    if snap.registry is None:
        return Options(name="ha_areas", choices=[], source="Needs an administrator's token to read rooms.", ok=False)
    exposed = Counter(aid for eid in snap.exposed or () if (aid := snap.area(eid)[0]))
    return Options(name="ha_areas", source=ha.url, choices=[
        Choice(value=name, label=name, hint=f"{exposed[aid]} exposed")
        for aid, name in sorted(snap.areas.items(), key=lambda a: a[1].casefold())])


DISCORD_API = "https://discord.com/api/v10"
DISCORD_TIMEOUT = 3
REFUSED = "Discord refused the bot token. Reset it in the Developer Portal and paste it again."


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
        return Options(name=name, choices=[], ok=False, source="Paste the bot token first, so Discord can list people.")
    headers = {"Authorization": f"Bot {token}"}
    q = (params.get("q") or "").strip()
    try:
        async with httpx.AsyncClient(base_url=DISCORD_API, timeout=DISCORD_TIMEOUT, headers=headers) as c:
            if q:
                if not (q.isascii() and q.isdigit()):
                    return Options(name=name, choices=[], ok=False,
                                   source="A Discord user ID is a number. Copy it from their profile.")
                r = await c.get(f"/users/{q}")
                if r.status_code == 404:
                    return Options(name=name, choices=[], ok=False,
                                   source=f"Discord knows no user {q}. Copy the id from their profile again.")
                if r.status_code == 401:
                    return Options(name=name, choices=[], ok=False, source=REFUSED)
                r.raise_for_status()
                return Options(name=name, choices=[_discord_user_choice(r.json())], source="Discord")
            r = await c.get("/users/@me/guilds")
            if r.status_code == 401:
                return Options(name=name, choices=[], ok=False, source=REFUSED)
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
    except Exception:                                                   # noqa: BLE001
        return Options(name=name, choices=[], ok=False, source="Discord did not answer. Try again in a moment.")
    if not guilds:
        return Options(name=name, choices=[], ok=False, source="Invite the bot to a server first, so Discord can list people.")
    source = "Discord servers: " + ", ".join(g.get("name", g["id"]) for g in guilds)
    if closed:
        source = (f"Turn on Server Members Intent in the Developer Portal, so the bot can list people in "
                  f"{', '.join(closed)}."
                  if not choices else source + f" (no member list from {', '.join(closed)}: Server Members Intent)")
    return Options(name=name, choices=sorted(choices.values(), key=lambda c: (c.group or "", c.label.lower())),
                   source=source, ok=not closed or bool(choices))


def _memory_topics(cfg: WinstonConfig, params: dict) -> Options:
    """His memory's topics (core/memory.py): the file's name is the value, its title the label."""
    return Options(name="memory_topics", source=str(memory.vault(cfg)), choices=[
        Choice(value=t, label=memory.title(cfg, t), hint=memory.description(cfg, t) or None)
        for t in memory.topics(cfg)])


def _extension_tools(cfg: WinstonConfig, params: dict) -> Options:
    """The switched-on extensions and MCP servers with a read tool named `tool` (at the tier you gave it),
    for an extension's `Extensions(tool=...)` setting. An MCP server lists its tools once it runs."""
    host, tool = params.get("extensions"), params.get("tool") or ""
    if host is None or not tool:
        return Options(name="extension_tools", choices=[], source="no tool named", ok=False)
    choices = []
    for ext_id in sorted(host.sources):
        if host.enabled(ext_id) and host.tier(ext_id, tool) == "read":
            name = host.described.get(ext_id, {}).get("manifest", {}).get("name") or ext_id
            choices.append(Choice(value=ext_id, label=name, hint=ext_id if name != ext_id else None))
    return Options(name="extension_tools", choices=choices, source=f"switched on, with a read tool {tool}")


async def _extension_choices(cfg: WinstonConfig, params: dict) -> Options:
    """An extension's own list for one of its settings (`Field(choices=...)`), from its `@ext.choices` function
    while it runs. Stopped or failing, `source` says so and the field takes a typed value."""
    host, ext_id, name = params.get("extensions"), params.get("extension") or "", params.get("choices") or ""
    if host is None or not ext_id or not name:
        return Options(name="extension_choices", choices=[], source="no extension or list named", ok=False)
    r = await host.choices(ext_id, name)
    if not r["ok"]:
        return Options(name="extension_choices", choices=[], source=r["error"], ok=False)
    return Options(name="extension_choices", source=ext_id, choices=[Choice.model_validate(c) for c in r["choices"]])


PROVIDERS = {
    "control_hosts": _control_hosts, "sounds": _sounds, "stt_models": _stt_models,
    "hint_words": _hint_words, "memory_topics": _memory_topics, "extension_tools": _extension_tools,
    "extension_choices": _extension_choices,
    "llm_models": _llm_models, "voices": _voices,
    "ha_entities": _ha_entities, "ha_domains": _ha_domains, "ha_notify": _ha_notify, "ha_areas": _ha_areas, "discord_users": _discord_users,
}


async def options(name: str, cfg: WinstonConfig, params: dict) -> Options | None:
    fn = PROVIDERS.get(name)
    if fn is None:
        return None
    out = fn(cfg, params)
    return await out if inspect.isawaitable(out) else out


async def check_home_assistant(cfg: WinstonConfig, url: str | None,
                               token: str | None = None) -> HomeAssistantCheck:
    """Can Winston reach Home Assistant with its token (or `token`, pasted and not saved yet), and which one
    is it."""
    url = url or (cfg.providers.ha.url if _ha(cfg) else "")
    if not url:
        return HomeAssistantCheck(ok=False, detail="Turned off" if _ha_off(cfg) else "No URL")
    try:
        j = await HomeAssistant(cfg, url, token).ping()
    except RuntimeError as e:                                           # the token: not set, or rejected
        return HomeAssistantCheck(ok=False, detail=str(e))
    except Exception as e:                                              # noqa: BLE001
        return HomeAssistantCheck(ok=False, detail=f"Not reachable: {failure_words(e)}")
    return HomeAssistantCheck(ok=True, version=j["version"], location=j["location"],
                              detail=f"{j['location']}, Home Assistant {j['version']}")
