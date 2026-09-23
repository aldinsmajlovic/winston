"""Typed configuration. The YAML in the data directory is validated against this, the web UI
generates its forms from the JSON Schema, and every field carries its UI level and which
engine a change restarts (json_schema_extra: level, restarts; engine fields also list the drivers they apply to).

Restart metadata lives on the field when one field is special, else on the model: a section's
`restarts` applies to every field in it, nested sections included, unless a field says otherwise.
"{engine}" stands for the engine an EngineConfig belongs to."""
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


def ui(level: str = "basic", restarts: list[str] | None = None, **extra: Any) -> dict[str, Any]:
    d: dict[str, Any] = {"level": level, **extra}
    if restarts:
        d["restarts"] = restarts
    return d


def section(*restarts: str) -> ConfigDict:
    """Model config for a section whose fields all restart the given engines."""
    return ConfigDict(extra="forbid", json_schema_extra={"restarts": list(restarts)})


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AssistantConfig(Model):
    model_config = section("voice")
    name: str = Field("Winston", description="What the assistant calls itself.")
    persona: str = Field("persona.md", title="Persona file", description="Persona file, relative to the data directory.",
                         json_schema_extra=ui("advanced"))


class EngineConfig(Model):
    """A heavy process Winston depends on: LLM server, TTS server, later STT. Managed by the supervisor."""
    model_config = section("{engine}")
    driver: Literal["docker", "subprocess", "external"] = Field(description="How the engine is run.")
    enabled: bool = True
    port: int = Field(description="Local port the engine listens on.")
    health: str = Field("/health", title="Health path",
                        description="HTTP path that returns 200 when ready. Empty: no check, the engine counts as up "
                                    "whenever its process is (hosted APIs).",
                        json_schema_extra=ui("advanced"))
    startup_timeout: int = Field(600, title="Startup timeout (s)", description="Seconds to wait for health before giving up.",
                                 json_schema_extra=ui("advanced"))
    # docker
    image: str | None = Field(None, description="Container image (docker driver).", json_schema_extra=ui(drivers=["docker"]))
    args: list[str] = Field(default_factory=list, title="Arguments",
                            description="Command arguments (docker driver). {model} expands to the brain's model.",
                            json_schema_extra=ui("advanced", drivers=["docker"]))
    env: dict[str, str] = Field(default_factory=dict, title="Environment",
                                json_schema_extra=ui("advanced", drivers=["docker", "subprocess"]))
    volumes: dict[str, str] = Field(default_factory=dict, description="host path -> container path",
                                    json_schema_extra=ui("advanced", drivers=["docker"]))
    gpu: bool = Field(False, title="GPU", description="Give the container the GPU.", json_schema_extra=ui(drivers=["docker"]))
    runtime: str | None = Field(None, description="Container runtime, e.g. nvidia.",
                                json_schema_extra=ui("advanced", drivers=["docker"]))
    ipc_host: bool = Field(False, title="IPC host", json_schema_extra=ui("advanced", drivers=["docker"]))
    # subprocess
    cmd: list[str] = Field(default_factory=list, title="Command",
                           description="Command line (subprocess driver). {data}, {repo} and {model} expand.",
                           json_schema_extra=ui("advanced", drivers=["subprocess"]))
    cwd: str | None = Field(None, title="Working directory", description="Working directory, {repo}-relative allowed.",
                            json_schema_extra=ui("advanced", drivers=["subprocess"]))
    # external
    url: str | None = Field(None, title="URL", description="Base URL of an engine you run yourself (external driver).",
                            json_schema_extra=ui(drivers=["external"]))


class Sampling(Model):
    model_config = section("voice")
    temperature: float = Field(0.3, ge=0, le=2, description="Lower is more reliable at tool calls.",
                               json_schema_extra=ui("advanced"))
    top_p: float = Field(0.8, ge=0, le=1, title="Top-p", json_schema_extra=ui("advanced"))
    presence_penalty: float = Field(0.0, ge=-2, le=2, title="Presence penalty", json_schema_extra=ui("advanced"))
    max_tokens: int = Field(512, ge=16, title="Max tokens", json_schema_extra=ui("advanced"))


class Thinking(Model):
    model_config = section("voice")
    voice: bool = Field(False, description="Reasoning before answering on the voice channel. Costs seconds.")
    text: bool = Field(True, description="Reasoning on text channels. Much better at multi-part requests.")
    control: Literal["chat_template", "reasoning_effort", "none"] = Field(
        "chat_template", description="How the server is told to think or not: chat_template for vLLM and llama.cpp "
                                     "(enable_thinking), reasoning_effort for Ollama, none for servers without a switch.",
        json_schema_extra=ui("advanced"))


class LLMConfig(Model):
    model_config = section("voice")
    provider: str = Field("openai_compatible", json_schema_extra=ui("advanced"))
    base_url: str = Field("http://127.0.0.1:8000/v1", title="Base URL", json_schema_extra=ui("advanced"))
    api_key_env: str | None = Field(None, title="API key variable",
                                    description="Environment variable holding the API key. Unset for local servers.",
                                    json_schema_extra=ui("advanced"))
    model: str = Field(description="Model name as the server knows it.", json_schema_extra=ui(restarts=["llm", "voice"]))
    sampling: Sampling = Sampling()
    thinking: Thinking = Thinking()


class STTConfig(Model):
    model_config = section("voice")
    provider: str = Field("parakeet", description="Speech-to-text adapter.")
    model: str = Field("nemo-parakeet-tdt-0.6b-v3", json_schema_extra=ui("advanced"))
    quantization: str | None = Field("int8", json_schema_extra=ui("advanced"))


class TTSConfig(Model):
    model_config = section("voice")
    provider: str = Field("openai_speech", description="Text-to-speech adapter (OpenAI speech protocol).",
                          json_schema_extra=ui("advanced"))
    base_url: str = Field("http://127.0.0.1:8880/v1", title="Base URL", json_schema_extra=ui("advanced"))
    api_key_env: str | None = Field(None, title="API key variable",
                                    description="Environment variable holding the API key. Unset for local servers.",
                                    json_schema_extra=ui("advanced"))
    voice: str = Field("onyx", description="Voice name sent to the server. OpenAI names map to the server's default clip.",
                       json_schema_extra=ui("advanced"))
    sample_rate: int = Field(24000, title="Sample rate", json_schema_extra=ui("advanced"))


class VADConfig(Model):
    model_config = section("voice")
    provider: str = Field("silero", json_schema_extra=ui("advanced"))
    stop_secs: float = Field(0.3, ge=0.1, le=2.0, title="End of speech (s)",
                             description="Silence after speech before the utterance is sent.",
                             json_schema_extra=ui("advanced"))
    unfinished_stop_secs: float = Field(1.0, ge=0.2, le=5.0, title="Pause when unfinished (s)",
                                        description="Extra wait after silence when the turn detector thinks the "
                                                    "sentence is unfinished. One-word answers pay this in full.",
                                        json_schema_extra=ui("advanced"))


class VoiceChannel(Model):
    model_config = section("voice")
    enabled: bool = True
    host: str = Field("127.0.0.1", json_schema_extra=ui("advanced"))
    port: int = Field(7860, json_schema_extra=ui("advanced"))


class TelegramChannel(Model):
    enabled: bool = False
    token_env: str = Field("TELEGRAM_BOT_TOKEN", title="Token variable", json_schema_extra=ui("advanced"))
    allowed_user_id_env: str = Field("TELEGRAM_ALLOWED_USER_ID", title="Allowed user variable",
                                     json_schema_extra=ui("advanced"))


class Channels(Model):
    voice: VoiceChannel = VoiceChannel()
    telegram: TelegramChannel = TelegramChannel()


class ExposedEntity(Model):
    model_config = section("voice")
    name: str | None = Field(None, description="Friendly name to set in Home Assistant.")
    aliases: list[str] = Field(default_factory=list, description="Other names people say for it.")


class HomeAssistantConfig(Model):
    model_config = section("voice")
    url: str = Field(title="URL", description="Home Assistant base URL on the LAN.")
    token_env: str = Field("HA_TOKEN", title="Token variable", description="Environment variable holding the long-lived token.",
                           json_schema_extra=ui("advanced"))
    mcp_path: str = Field("/api/mcp", title="MCP path", json_schema_extra=ui("advanced"))
    expose: dict[str, ExposedEntity] = Field(default_factory=dict, title="Entities",
                                             description="Entities Winston may see and control, with aliases.")
    expose_domains: list[str] = Field(default_factory=lambda: ["person", "weather", "todo"], title="Domains",
                                      description="Whole domains to expose, without aliases.",
                                      json_schema_extra=ui("advanced"))


class Providers(Model):
    home_assistant: HomeAssistantConfig | None = Field(None, title="Home Assistant")


class SensorPair(Model):
    model_config = section("voice")
    level: str = Field(title="Value sensor", description="Entity id of the value sensor.")
    state: str | None = Field(None, title="State sensor", description="Optional companion state sensor (charging, etc.).")


class ToolsConfig(Model):
    model_config = section("voice")
    sensors: dict[str, SensorPair] = Field(default_factory=dict,
                                           description="Watched sensors by label; readings come with their age.")


class MemoryConfig(Model):
    model_config = section("voice")
    enabled: bool = Field(True, description="Load remembered facts into every new conversation and offer the remember tool.")
    dir: str = Field("memory", title="Directory", json_schema_extra=ui("advanced"))
    commit_on_write: bool = Field(True, title="Commit on write", json_schema_extra=ui("advanced"))


class LoggingConfig(Model):
    model_config = section("voice")
    tool_calls: str = Field("logs/tool_calls.jsonl", title="Tool calls", json_schema_extra=ui("advanced"))
    latency: str = Field("logs/latency.jsonl", json_schema_extra=ui("advanced"))


class ControlConfig(Model):
    model_config = section("control")
    host: str = Field("127.0.0.1", json_schema_extra=ui("advanced"))
    port: int = Field(8700, json_schema_extra=ui("advanced"))


class WinstonConfig(Model):
    version: int = Field(1, json_schema_extra=ui("advanced"))
    assistant: AssistantConfig = AssistantConfig()
    engines: dict[str, EngineConfig] = Field(default_factory=dict)
    llm: LLMConfig = Field(title="Brain")
    stt: STTConfig = Field(STTConfig(), title="Speech to text")
    tts: TTSConfig = Field(TTSConfig(), title="Text to speech")
    vad: VADConfig = Field(VADConfig(), title="Voice activity")
    channels: Channels = Channels()
    providers: Providers = Providers()
    tools: ToolsConfig = ToolsConfig()
    memory: MemoryConfig = MemoryConfig()
    logging: LoggingConfig = LoggingConfig()
    control: ControlConfig = ControlConfig()
