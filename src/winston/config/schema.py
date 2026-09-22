"""Typed configuration. The YAML in the data directory is validated against this, the web UI
generates its forms from the JSON Schema, and every field carries its UI level and which
engine a change restarts (json_schema_extra: level, restarts)."""
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


def ui(level: str = "basic", restarts: list[str] | None = None, **extra: Any) -> dict[str, Any]:
    return {"level": level, "restarts": restarts or [], **extra}


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AssistantConfig(Model):
    name: str = Field("Winston", description="What the assistant calls itself.")
    persona: str = Field("persona.md", description="Persona file, relative to the data directory.",
                         json_schema_extra=ui("advanced"))


class EngineConfig(Model):
    """A heavy process Winston depends on: LLM server, TTS server, later STT. Managed by the supervisor."""
    driver: Literal["docker", "subprocess", "external"] = Field(description="How the engine is run.")
    enabled: bool = True
    port: int = Field(description="Local port the engine listens on.")
    health: str = Field("/health", description="HTTP path that returns 200 when ready.", json_schema_extra=ui("advanced"))
    startup_timeout: int = Field(600, description="Seconds to wait for health before giving up.",
                                 json_schema_extra=ui("advanced"))
    # docker
    image: str | None = Field(None, description="Container image (docker driver).")
    args: list[str] = Field(default_factory=list, description="Command arguments (docker driver).",
                            json_schema_extra=ui("advanced"))
    env: dict[str, str] = Field(default_factory=dict, json_schema_extra=ui("advanced"))
    volumes: dict[str, str] = Field(default_factory=dict, description="host path -> container path",
                                    json_schema_extra=ui("advanced"))
    gpu: bool = Field(False, description="Give the container the GPU.")
    runtime: str | None = Field(None, description="Container runtime, e.g. nvidia.", json_schema_extra=ui("advanced"))
    ipc_host: bool = Field(False, json_schema_extra=ui("advanced"))
    # subprocess
    cmd: list[str] = Field(default_factory=list, description="Command line (subprocess driver). {data} and {repo} expand.",
                           json_schema_extra=ui("advanced"))
    cwd: str | None = Field(None, description="Working directory, {repo}-relative allowed.", json_schema_extra=ui("advanced"))
    # external
    url: str | None = Field(None, description="Base URL of an engine you run yourself (external driver).")


class Sampling(Model):
    temperature: float = Field(0.3, ge=0, le=2, description="Lower is more reliable at tool calls.",
                               json_schema_extra=ui("advanced"))
    top_p: float = Field(0.8, ge=0, le=1, json_schema_extra=ui("advanced"))
    presence_penalty: float = Field(0.0, ge=-2, le=2, json_schema_extra=ui("advanced"))
    max_tokens: int = Field(512, ge=16, json_schema_extra=ui("advanced"))


class Thinking(Model):
    voice: bool = Field(False, description="Reasoning before answering on the voice channel. Costs seconds.")
    text: bool = Field(True, description="Reasoning on text channels. Much better at multi-part requests.")


class LLMConfig(Model):
    provider: str = Field("openai_compatible", json_schema_extra=ui("advanced"))
    base_url: str = Field("http://127.0.0.1:8000/v1", json_schema_extra=ui("advanced"))
    model: str = Field(description="Model name as the server knows it.", json_schema_extra=ui(restarts=["llm"]))
    sampling: Sampling = Sampling()
    thinking: Thinking = Thinking()


class STTConfig(Model):
    provider: str = Field("parakeet", description="Speech-to-text adapter.")
    model: str = Field("nemo-parakeet-tdt-0.6b-v3", json_schema_extra=ui("advanced", ["voice"]))
    quantization: str | None = Field("int8", json_schema_extra=ui("advanced", ["voice"]))


class TTSConfig(Model):
    provider: str = Field("openai_speech", description="Text-to-speech adapter (OpenAI speech protocol).",
                          json_schema_extra=ui("advanced"))
    base_url: str = Field("http://127.0.0.1:8880/v1", json_schema_extra=ui("advanced"))
    voice: str = Field("onyx", description="Voice name sent to the server. OpenAI names map to the server's default clip.",
                       json_schema_extra=ui("advanced"))
    sample_rate: int = Field(24000, json_schema_extra=ui("advanced", ["voice"]))


class VADConfig(Model):
    provider: str = Field("silero", json_schema_extra=ui("advanced"))
    stop_secs: float = Field(0.3, ge=0.1, le=2.0, description="Silence after speech before the utterance is sent.",
                             json_schema_extra=ui("advanced", ["voice"]))


class VoiceChannel(Model):
    enabled: bool = True
    host: str = Field("127.0.0.1", json_schema_extra=ui("advanced", ["voice"]))
    port: int = Field(7860, json_schema_extra=ui("advanced", ["voice"]))


class TelegramChannel(Model):
    enabled: bool = False
    token_env: str = Field("TELEGRAM_BOT_TOKEN", json_schema_extra=ui("advanced"))
    allowed_user_id_env: str = Field("TELEGRAM_ALLOWED_USER_ID", json_schema_extra=ui("advanced"))


class Channels(Model):
    voice: VoiceChannel = VoiceChannel()
    telegram: TelegramChannel = TelegramChannel()


class ExposedEntity(Model):
    name: str | None = Field(None, description="Friendly name to set in Home Assistant.")
    aliases: list[str] = Field(default_factory=list, description="Other names people say for it.")


class HomeAssistantConfig(Model):
    url: str = Field(description="Home Assistant base URL on the LAN.")
    token_env: str = Field("HA_TOKEN", description="Environment variable holding the long-lived token.",
                           json_schema_extra=ui("advanced"))
    mcp_path: str = Field("/api/mcp", json_schema_extra=ui("advanced"))
    expose: dict[str, ExposedEntity] = Field(default_factory=dict,
                                             description="Entities Winston may see and control, with aliases.")
    expose_domains: list[str] = Field(default_factory=lambda: ["person", "weather", "todo"],
                                      description="Whole domains to expose, without aliases.",
                                      json_schema_extra=ui("advanced"))


class Providers(Model):
    home_assistant: HomeAssistantConfig | None = None


class SensorPair(Model):
    level: str = Field(description="Entity id of the value sensor.")
    state: str | None = Field(None, description="Optional companion state sensor (charging, etc.).")


class ToolsConfig(Model):
    sensors: dict[str, SensorPair] = Field(default_factory=dict,
                                           description="Watched sensors by label; readings come with their age.")


class MemoryConfig(Model):
    dir: str = Field("memory", json_schema_extra=ui("advanced"))
    commit_on_write: bool = Field(True, json_schema_extra=ui("advanced"))


class LoggingConfig(Model):
    tool_calls: str = Field("logs/tool_calls.jsonl", json_schema_extra=ui("advanced"))
    latency: str = Field("logs/latency.jsonl", json_schema_extra=ui("advanced"))


class ControlConfig(Model):
    host: str = Field("127.0.0.1", json_schema_extra=ui("advanced"))
    port: int = Field(8700, json_schema_extra=ui("advanced"))


class WinstonConfig(Model):
    version: int = 1
    assistant: AssistantConfig = AssistantConfig()
    engines: dict[str, EngineConfig] = Field(default_factory=dict)
    llm: LLMConfig
    stt: STTConfig = STTConfig()
    tts: TTSConfig = TTSConfig()
    vad: VADConfig = VADConfig()
    channels: Channels = Channels()
    providers: Providers = Providers()
    tools: ToolsConfig = ToolsConfig()
    memory: MemoryConfig = MemoryConfig()
    logging: LoggingConfig = LoggingConfig()
    control: ControlConfig = ControlConfig()
