"""Live choices for the web UI's dropdowns. A config field names its list in the schema
(`options: "<name>"`) and the UI asks `/api/options/<name>`; every list is fetched from where the
truth lives (the brain's server, Home Assistant, the data directory, a provider's model list), so
nothing is typed by hand that a machine already knows. Secrets never leave: the env list names
variables and says whether they hold a value, nothing more."""
import os
from datetime import datetime
from pathlib import Path
from typing import get_args

import httpx
from dotenv import dotenv_values
from pydantic import BaseModel

from winston.config import data_dir, repo_root
from winston.config.registry import available
from winston.config.schema import WinstonConfig
from winston.core.voices import clones

TIMEOUT = 4

# Fallback when the Anthropic model list cannot be read (no API key: ask_claude runs on a login).
CLAUDE_MODELS = ["claude-opus-5", "claude-opus-5-5", "claude-sonnet-5", "claude-fable-5-1", "claude-haiku-4-5"]


class Choice(BaseModel):
    value: str
    label: str
    hint: str | None = None
    icon: str | None = None     # a brand or kind the UI draws: an HA domain, "set" or "missing" for env
    group: str | None = None


class Options(BaseModel):
    name: str
    choices: list[Choice]
    source: str = ""            # where the list came from, or why it is empty
    ok: bool = True             # False when the source could not be reached


class HomeAssistantCheck(BaseModel):
    ok: bool
    detail: str = ""
    version: str | None = None
    location: str | None = None


def _env_file() -> Path:
    return data_dir() / ".env"


def _secret(name: str | None) -> str:
    return os.environ.get(name, "") if name else ""


def _env(cfg: WinstonConfig, params: dict) -> Options:
    values = dotenv_values(_env_file()) if _env_file().is_file() else {}
    choices = []
    for name, value in values.items():
        has = bool(value or os.environ.get(name))
        choices.append(Choice(value=name, label=name, icon="set" if has else "missing"))
    return Options(name="env", choices=choices, source=str(_env_file()))


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


def _stt_models(cfg: WinstonConfig, params: dict) -> Options:
    try:
        from onnx_asr.loader import AsrNames
        names = list(get_args(AsrNames))
    except Exception:                                                   # noqa: BLE001
        names = [cfg.stt.model]
    cache = Path(os.environ.get("HF_HOME", Path.home() / ".cache/huggingface")) / "hub"
    cached = [p.name.lower() for p in cache.glob("models--*")] if cache.is_dir() else []
    choices = []
    for n in sorted(names, key=lambda n: ("parakeet" not in n, n)):
        stem = n.removeprefix("nemo-").removeprefix("onnx-community/").lower()
        here = any(stem in c for c in cached)
        choices.append(Choice(value=n, label=n, hint="downloaded" if here else None,
                              group="Parakeet" if "parakeet" in n else "Other"))
    return Options(name="stt_models", choices=choices, source="onnx-asr")


async def _llm_models(cfg: WinstonConfig, params: dict) -> Options:
    url = (params.get("base_url") or cfg.llm.base_url).rstrip("/")
    headers = {"Authorization": f"Bearer {key}"} if (key := _secret(cfg.llm.api_key_env)) else {}
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.get(url + "/models", headers=headers)
        r.raise_for_status()
        ids = sorted(m.get("id", "") for m in r.json().get("data", []) if m.get("id"))
        return Options(name="llm_models", source=url, choices=[Choice(value=i, label=i) for i in ids])
    except Exception as e:                                              # noqa: BLE001
        return Options(name="llm_models", source=f"{url}: {type(e).__name__}", choices=[], ok=False)


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
    ha = cfg.providers.home_assistant
    url = (params.get("base_url") or (ha.url if _ha(cfg) else "")).rstrip("/")
    if not url:
        raise RuntimeError("Home Assistant is turned off" if _ha_off(cfg) else "Home Assistant is not configured")
    token = _secret(ha.token_env if ha else "HA_TOKEN")
    if not token:
        raise RuntimeError(f"{ha.token_env if ha else 'HA_TOKEN'} is not set")
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        r = await c.get(url + "/api/states", headers={"Authorization": f"Bearer {token}"})
    r.raise_for_status()
    return r.json(), url


def _state_hint(s: dict) -> str:
    state, unit = str(s.get("state")), s.get("attributes", {}).get("unit_of_measurement")
    try:        # buttons and timestamp sensors report an ISO time
        return datetime.fromisoformat(state).astimezone().strftime("%-d %b %H:%M")
    except ValueError:
        return f"{state} {unit}" if unit else state


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
                              hint=_state_hint(s), icon=domain, group=domain))
    return Options(name="ha_entities", choices=choices, source=url)


async def _ha_domains(cfg: WinstonConfig, params: dict) -> Options:
    try:
        states, url = await _ha_states(cfg, params)
    except Exception as e:                                              # noqa: BLE001
        return Options(name="ha_domains", choices=[], source=str(e) or type(e).__name__, ok=False)
    counts: dict[str, int] = {}
    for s in states:
        d = s["entity_id"].split(".")[0]
        counts[d] = counts.get(d, 0) + 1
    return Options(name="ha_domains", source=url,
                   choices=[Choice(value=d, label=d.replace("_", " ").capitalize(), hint=str(n), icon=d)
                            for d, n in sorted(counts.items())])


PROVIDERS = {
    "env": _env, "personas": _personas, "sounds": _sounds, "stt_adapters": _stt_adapters, "stt_models": _stt_models,
    "llm_models": _llm_models, "voices": _voices, "gemini_models": _gemini_models, "claude_models": _claude_models,
    "ha_entities": _ha_entities, "ha_domains": _ha_domains,
}


async def options(name: str, cfg: WinstonConfig, params: dict) -> Options | None:
    fn = PROVIDERS.get(name)
    if fn is None:
        return None
    out = fn(cfg, params)
    return await out if hasattr(out, "__await__") else out


async def check_home_assistant(cfg: WinstonConfig, url: str | None) -> HomeAssistantCheck:
    """Can Winston reach Home Assistant with its token, and which one is it."""
    ha = cfg.providers.home_assistant
    url = (url or (ha.url if _ha(cfg) else "")).rstrip("/")
    env = ha.token_env if ha else "HA_TOKEN"
    if not url:
        return HomeAssistantCheck(ok=False, detail="Turned off" if _ha_off(cfg) else "No URL")
    if not (token := _secret(env)):
        return HomeAssistantCheck(ok=False, detail=f"{env} is not set")
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.get(url + "/api/config", headers={"Authorization": f"Bearer {token}"})
        if r.status_code == 401:
            return HomeAssistantCheck(ok=False, detail="Token rejected")
        r.raise_for_status()
        j = r.json()
        return HomeAssistantCheck(ok=True, version=j.get("version"), location=j.get("location_name"),
                                  detail=f"{j.get('location_name')}, Home Assistant {j.get('version')}")
    except Exception as e:                                              # noqa: BLE001
        return HomeAssistantCheck(ok=False, detail=f"Not reachable ({type(e).__name__})")
