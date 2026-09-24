"""Typed configuration. The YAML in the data directory is validated against this, the web UI
generates its forms from the JSON Schema, and every field carries its UI level and which
engine a change restarts (json_schema_extra: level, restarts; engine fields also list the drivers they apply to).

Restart metadata lives on the field when one field is special, else on the model: a section's
`restarts` applies to every field in it, nested sections included, unless a field says otherwise.
"{engine}" stands for the engine an EngineConfig belongs to."""
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


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
    model_config = section("voice", "discord")
    name: str = Field("Winston", description="What the assistant calls itself.")
    persona: str = Field("persona.md", title="Persona file",
                         description="Persona file, relative to the data directory.",
                         json_schema_extra=ui("advanced", options="personas"))
    language: str = Field("English", title="Reply language",
                          description="The language he always answers in, whatever language he is spoken to in. "
                                      "Match it to the voice: Chatterbox-Turbo speaks only English.")


class EngineConfig(Model):
    """A heavy process Winston depends on: LLM server, TTS server, later STT. Managed by the supervisor."""
    model_config = section("{engine}")
    driver: Literal["docker", "subprocess", "external"] = Field(
        description="How the engine is run. Docker container: Winston runs an image. Process: Winston runs a "
                    "command on this machine. External server: something you run yourself; Winston only checks it.",
        json_schema_extra=ui(labels={"docker": "Docker container", "subprocess": "Process", "external": "External server"},
                             icons={"docker": "docker", "subprocess": "process", "external": "external"}))
    enabled: bool = Field(True, description="Start it with winston up. Off keeps the entry without running it.")
    port: int = Field(description="Local port the engine listens on.")
    health: str = Field("/health", title="Health path",
                        description="HTTP path that returns 200 when ready. Empty: no check, the engine counts as up "
                                    "whenever its process is (hosted APIs).",
                        json_schema_extra=ui("advanced"))
    startup_timeout: int = Field(600, title="Startup timeout (s)",
                                 description="Seconds to wait for health before giving up.",
                                 json_schema_extra=ui("advanced"))
    # docker
    image: str | None = Field(None,
                              description="Container image (docker driver).", json_schema_extra=ui(drivers=["docker"]))
    args: list[str] = Field(default_factory=list, title="Arguments",
                            description="Command arguments (docker driver). {model} expands to the brain's model, "
                                        "{section.field} (say {tts.streaming.chunk_secs}) to that config value.",
                            json_schema_extra=ui("advanced", drivers=["docker"]))
    env: dict[str, str] = Field(default_factory=dict, title="Environment",
                                description="Environment variables for the engine's process or container.",
                                json_schema_extra=ui("advanced", drivers=["docker", "subprocess"]))
    volumes: dict[str, str] = Field(default_factory=dict, description="host path -> container path",
                                    json_schema_extra=ui("advanced", drivers=["docker"]))
    gpu: bool = Field(False, title="GPU",
                      description="Give the container the GPU.", json_schema_extra=ui(drivers=["docker"]))
    runtime: str | None = Field(None, description="Container runtime, e.g. nvidia.",
                                json_schema_extra=ui("advanced", drivers=["docker"]))
    ipc_host: bool = Field(False, title="IPC host",
                           description="Share the host's IPC namespace; vLLM needs it for shared memory between "
                                       "workers.",
                           json_schema_extra=ui("advanced", drivers=["docker"]))
    # subprocess
    cmd: list[str] = Field(default_factory=list, title="Command",
                           description="Command line (subprocess driver). {data}, {repo}, {model} and "
                                       "{section.field} (say {tts.streaming.chunk_secs}) expand.",
                           json_schema_extra=ui("advanced", drivers=["subprocess"]))
    cwd: str | None = Field(None, title="Working directory", description="Working directory, {repo}-relative allowed.",
                            json_schema_extra=ui("advanced", drivers=["subprocess"]))
    # external
    url: str | None = Field(None, title="URL", description="Base URL of an engine you run yourself (external driver).",
                            json_schema_extra=ui(drivers=["external"]))


class Sampling(Model):
    model_config = section("voice", "discord")
    temperature: float = Field(0.3, ge=0, le=2, description="Lower is more reliable at tool calls.",
                               json_schema_extra=ui("advanced"))
    top_p: float = Field(0.8, ge=0, le=1, title="Top-p",
                         description="Samples only from the most likely tokens that add up to this share. Lower is "
                                     "more focused.",
                         json_schema_extra=ui("advanced"))
    presence_penalty: float = Field(0.0, ge=-2, le=2, title="Presence penalty",
                                    description="Positive values push the model away from repeating itself. Qwen's "
                                                "chat default is 1.5; 0 calls tools more reliably.",
                                    json_schema_extra=ui("advanced"))
    max_tokens: int = Field(512, ge=16, title="Max tokens",
                            description="Longest reply in tokens. Spoken replies are short; this is a safety cap.",
                            json_schema_extra=ui("advanced"))


class Thinking(Model):
    model_config = section("voice", "discord")
    voice: bool = Field(False, description="Reasoning before answering on the voice channel. Costs seconds.")
    text: bool = Field(True, description="Reasoning on text channels. Much better at multi-part requests.")
    control: Literal["chat_template", "reasoning_effort", "none"] = Field(
        "chat_template", description="How the server is told to think or not: chat_template for vLLM and llama.cpp "
                                     "(enable_thinking), reasoning_effort for Ollama, none for servers without a switch.",
        json_schema_extra=ui("advanced", labels={"chat_template": "Chat template (vLLM, llama.cpp)",
                                                 "reasoning_effort": "Reasoning effort (Ollama)", "none": "No switch"}))


class LLMConfig(Model):
    model_config = section("voice", "discord")
    provider: Literal["openai_compatible"] = Field("openai_compatible",
                                                   description="Protocol the brain is spoken to with. Every "
                                                               "supported server speaks the OpenAI chat API.",
        json_schema_extra=ui(
        "advanced", labels={"openai_compatible": "OpenAI-compatible"}))
    base_url: str = Field("http://127.0.0.1:8000/v1", title="Base URL",
                          description="Where the brain's OpenAI-compatible API answers, ending in /v1. Presets set it.",
                          json_schema_extra=ui("advanced"))
    api_key_env: str | None = Field(None, title="API key variable",
                                    description="Environment variable holding the API key. Unset for local servers.",
                                    json_schema_extra=ui("advanced", options="env"))
    model: str = Field(description="Model name as the server knows it.",
                       json_schema_extra=ui(restarts=["llm", "voice", "discord"], options="llm_models"))
    sampling: Sampling = Sampling()
    thinking: Thinking = Thinking()


class STTConfig(Model):
    model_config = section("voice")
    provider: str = Field("parakeet",
                          description="Speech-to-text adapter.", json_schema_extra=ui(options="stt_adapters"))
    model: str = Field("nemo-parakeet-tdt-0.6b-v3",
                       description="Speech-to-text model. Parakeet TDT 0.6B v3 is multilingual; v2 is English only.",
                       json_schema_extra=ui("advanced", options="stt_models"))
    quantization: str | None = Field("int8",
                                     description="Weight precision. int8 is faster on the CPU at no audible "
                                                 "accuracy cost; empty for full precision.",
                                     json_schema_extra=ui("advanced"))


class TTSStreamingConfig(Model):
    """Reaches the speech server through `{tts.streaming.*}` in its engine command; the Chatterbox preset
    passes them. Pocket TTS streams on its own and has no such arguments."""
    model_config = section("tts")
    enabled: bool = Field(True, title="Stream speech",
                          description="Start speaking before a sentence is fully rendered: first audio in about "
                                      "0.3 s instead of up to 2 s for a long sentence, with a join between chunks "
                                      "that can sound a little choppy. Off renders whole sentences. For the "
                                      "Chatterbox server.")
    chunk_secs: float = Field(1.6, ge=0.8, le=6.0, title="Chunk length (s)",
                              description="Audio per chunk after the first. Longer means fewer joins and smoother "
                                          "speech; how soon he starts talking depends only on the first chunk.")
    first_secs: float = Field(0.96, ge=0.4, le=3.0, title="First chunk (s)",
                              description="Audio rendered before anything is sent. Shorter starts sooner; below "
                                          "about 0.9 s the second chunk can arrive late and leave a short gap.",
                              json_schema_extra=ui("advanced"))
    lookahead_secs: float = Field(0.32, ge=0.08, le=1.0, title="Look-ahead (s)",
                                  description="Audio held back at the end of each chunk until the sounds after it "
                                              "are known. More smooths the joins but makes the first chunk shorter.",
                                  json_schema_extra=ui("advanced"))

    @model_validator(mode="after")
    def _lookahead_inside_first(self):
        if self.lookahead_secs >= self.first_secs:
            raise ValueError("look-ahead must be shorter than the first chunk")
        return self


class TTSConfig(Model):
    model_config = section("voice")
    provider: Literal["openai_speech"] = Field("openai_speech", description="Text-to-speech adapter.",
                                              json_schema_extra=ui("advanced", labels={"openai_speech": "OpenAI speech protocol"}))
    base_url: str = Field("http://127.0.0.1:8880/v1", title="Base URL",
                          description="Where the speech server's OpenAI speech API answers, ending in /v1. Presets "
                                      "set it.",
                          json_schema_extra=ui("advanced"))
    api_key_env: str | None = Field(None, title="API key variable",
                                    description="Environment variable holding the API key. Unset for local servers.",
                                    json_schema_extra=ui("advanced", options="env"))
    voice: str = Field("onyx",
                       description="Voice name sent to the server. OpenAI names map to the server's default clip.",
                       json_schema_extra=ui(options="voices"))
    sample_rate: int = Field(24000, title="Sample rate", description="Audio rate the speech server returns, in Hz.",
                             json_schema_extra=ui("advanced"))
    streaming: TTSStreamingConfig = Field(default_factory=TTSStreamingConfig, title="Streaming")


class VADConfig(Model):
    model_config = section("voice")
    provider: Literal["silero"] = Field("silero",
                                        description="Voice activity detector that decides when you are speaking.",
                                        json_schema_extra=ui("advanced", labels={"silero": "Silero"}))
    stop_secs: float = Field(0.3, ge=0.1, le=2.0, title="End of speech (s)",
                             description="Silence after speech before the utterance is sent.",
                             json_schema_extra=ui("advanced"))
    unfinished_stop_secs: float = Field(1.0, ge=0.2, le=5.0, title="Pause when unfinished (s)",
                                        description="Extra wait after silence when the turn detector thinks the "
                                                    "sentence is unfinished. One-word answers pay this in full.",
                                        json_schema_extra=ui("advanced"))


class VoiceChannel(Model):
    model_config = section("voice")
    enabled: bool = Field(True, description="Talk to Winston by voice from the browser and, later, satellites.")
    host: str = Field("127.0.0.1",
                      description="Address the voice channel listens on. 127.0.0.1 keeps it on this machine; "
                                  "winstond passes the browser's offer to it.",
                      json_schema_extra=ui("advanced"))
    port: int = Field(7860,
                      description="Port the voice channel listens on; Pipecat's own test page is served here too.",
                      json_schema_extra=ui("advanced"))


class DiscordChannel(Model):
    model_config = section("discord")
    enabled: bool = Field(False, description="Talk to Winston in Discord DMs and get announcements there.")
    token_env: str = Field("DISCORD_BOT_TOKEN", title="Token variable",
                           description="Environment variable holding the Discord bot token.",
                           json_schema_extra=ui("advanced", options="env"))
    allowed_user_env: str = Field("DISCORD_ALLOWED_USER_ID", title="Allowed user variable",
                                  description="Environment variable with the one Discord user id Winston answers.",
                                  json_schema_extra=ui("advanced", options="env"))
    session_idle_mins: int = Field(30, ge=1, title="Conversation resets after (min)",
                                   description="Idle minutes before a DM conversation starts fresh (and re-reads memory).",
                                   json_schema_extra=ui("advanced"))
    contacts: dict[str, str] = Field(default_factory=dict, json_schema_extra=ui(restarts=["voice", "discord"]),
                                     description="People Winston may message, name -> Discord user id. The bot can "
                                                 "only reach people who share a server with it.")


class WebChannel(Model):
    session_idle_mins: int = Field(30, ge=1, title="Conversation resets after (min)",
                                   description="Idle minutes before the Talk page's typed conversation starts fresh "
                                               "(and re-reads memory).",
                                   json_schema_extra=ui("advanced"))


class Channels(Model):
    voice: VoiceChannel = VoiceChannel()
    discord: DiscordChannel = DiscordChannel()
    web: WebChannel = WebChannel()


class ExposedEntity(Model):
    model_config = section("voice", "discord")
    name: str | None = Field(None, description="Friendly name to set in Home Assistant.")
    aliases: list[str] = Field(default_factory=list, description="Other names people say for it.")


class HomeAssistantConfig(Model):
    model_config = section("voice", "discord")
    url: str = Field(title="URL", description="Home Assistant base URL on the LAN.")
    token_env: str = Field("HA_TOKEN", title="Token variable",
                           description="Environment variable holding the long-lived token.",
                           json_schema_extra=ui("advanced", options="env"))
    mcp_path: str = Field("/api/mcp", title="MCP path", description="Path of Home Assistant's MCP server integration.",
                          json_schema_extra=ui("advanced"))
    expose: dict[str, ExposedEntity] = Field(default_factory=dict, title="Entities",
                                             description="Entities Winston may see and control, with the names people say for them. Nothing else in "
                                                         "Home Assistant is visible to him; winston ha expose applies the list.")
    expose_domains: list[str] = Field(default_factory=lambda: ["person", "weather", "todo"], title="Domains",
                                      description="Whole domains to expose, without aliases.",
                                      json_schema_extra=ui("advanced", options="ha_domains"))


class Providers(Model):
    home_assistant: HomeAssistantConfig | None = Field(None, title="Home Assistant")


class SensorPair(Model):
    model_config = section("voice", "discord")
    level: str = Field(title="Value sensor", description="Entity id of the value sensor.",
                       json_schema_extra=ui(options="ha_entities", options_query={"domain": "sensor"}))
    state: str | None = Field(None, title="State sensor",
                              description="Optional companion state sensor (charging, etc.).",
                              json_schema_extra=ui(options="ha_entities",
                                                   options_query={"domain": "sensor,binary_sensor"}))


class WebSearchConfig(Model):
    model_config = section("voice", "discord")
    enabled: bool = Field(False, description="Let Winston look up current facts on the web. Only the search question "
                                             "leaves the house.")
    provider: Literal["gemini"] = Field("gemini", description="Search backend. gemini: Gemini with Google Search "
                                                              "grounding (needs billing enabled on the key).",
                                        json_schema_extra=ui(labels={"gemini": "Gemini with Google Search"},
                                                             icons={"gemini": "gemini"}))
    model: str = Field("gemini-3.5-flash-lite", description="Flash-Lite answers in about 1.4 s, Flash in 2.3 s "
                                                             "(minimal thinking, measured 2026-09-23).",
                       json_schema_extra=ui("advanced", options="gemini_models"))
    thinking: Literal["minimal", "low", "medium", "high"] = Field("minimal", description="Reasoning at the search "
                                                                  "model. Each step up costs about a second.",
                                                                  json_schema_extra=ui("advanced"))
    api_key_env: str = Field("GEMINI_API_KEY", title="API key variable",
                             description="Environment variable holding the Gemini API key.",
                             json_schema_extra=ui("advanced", options="env"))
    max_per_day: int = Field(100, ge=0, title="Searches per day",
                             description="Hard cap; past it the tool refuses until midnight.")
    timeout: float = Field(15.0, ge=1, title="Timeout (s)",
                           description="Seconds before a search gives up and Winston says it could not look it up.",
                           json_schema_extra=ui("advanced"))


class AskClaudeConfig(Model):
    model_config = section("voice", "discord")
    enabled: bool = Field(False,
                          description="Hand hard questions to Claude. Uses the Claude Code login on this machine, "
                                             "CLAUDE_CODE_OAUTH_TOKEN (claude setup-token) or ANTHROPIC_API_KEY. "
                                             "Only the question leaves the house.")
    model: str = Field("claude-opus-5",
                       description="Claude model the question goes to. Opus is best at hard questions, Sonnet and "
                                   "Haiku are faster and cheaper.",
                       json_schema_extra=ui("advanced", options="claude_models"))
    effort: Literal["low", "medium", "high"] = Field("low", description="Reasoning effort. Low answered in about 3 to "
                                                     "5 s; each step up is slower.", json_schema_extra=ui("advanced"))
    max_usd_per_day: float = Field(3.0, ge=0, title="Budget per day (USD)",
                                   description="Past it the tool refuses until midnight. A Max 5x plan's monthly "
                                               "Agent SDK credit is 100 USD.")
    max_usd_per_question: float = Field(0.5, gt=0, title="Budget per question (USD)",
                                        description="Hard cap for one question; a longer answer is cut off.",
                                        json_schema_extra=ui("advanced"))


class ToolsConfig(Model):
    model_config = section("voice", "discord")
    sensors: dict[str, SensorPair] = Field(default_factory=dict,
                                           description="Watched sensors by label; readings come with their age.")
    web_search: WebSearchConfig = Field(WebSearchConfig(), title="Web search")
    ask_claude: AskClaudeConfig = Field(AskClaudeConfig(), title="Ask Claude")


class MemoryConfig(Model):
    model_config = section("voice", "discord")
    enabled: bool = Field(True,
                          description="Load remembered facts into every new conversation and offer the remember tool.")
    dir: str = Field("memory", title="Directory",
                     description="Where the fact files live, relative to the data directory.",
                     json_schema_extra=ui("advanced"))
    commit_on_write: bool = Field(True, title="Commit on write",
                                  description="Commit every remembered fact to the vault's git repository, so "
                                              "changes can be reviewed and undone.",
                                  json_schema_extra=ui("advanced"))


class AnnounceConfig(Model):
    model_config = section("control")
    enabled: bool = Field(True, description="Let Winston speak on its own: Home Assistant automations, the API and "
                                            "`winston announce` send it something to say.")
    chime: str = Field("{repo}/assets/sounds/chime.wav", description="Sound played before an announcement, so it "
                       "is clearly a notification. Empty for none.",
                       json_schema_extra=ui("advanced", restarts=["voice"], options="sounds"))
    quiet_hours: str | None = Field("22:00-07:00", title="Quiet hours",
                                    description="HH:MM-HH:MM when announcements are not spoken, only sent to Discord. "
                                                "Empty for never quiet.",
                                    json_schema_extra=ui(widget="time_range"))
    discord: Literal["always", "fallback", "never"] = Field(
        "fallback", title="Also to Discord",
        description="always: every announcement; fallback: when nothing can speak it (quiet hours, no speaker "
                    "connected); never.",
        json_schema_extra=ui(labels={"always": "Always", "fallback": "When it cannot be spoken", "never": "Never"}))
    ha_event: str = Field("winston_announce", title="Home Assistant event",
                          description="Event type an automation fires with data {text, context} to announce.",
                          json_schema_extra=ui("advanced"))


class LoggingConfig(Model):
    model_config = section("voice", "discord")
    tool_calls: str = Field("logs/tool_calls.jsonl", title="Tool calls",
                            description="Every tool call with its arguments and result, one JSON line each, "
                                        "relative to the data directory.",
                            json_schema_extra=ui("advanced"))
    latency: str = Field("logs/latency.jsonl",
                         description="Per-turn timings (speech end, first token, first audio), one JSON line each.",
                         json_schema_extra=ui("advanced"))


class ControlConfig(Model):
    model_config = section("control")
    host: str = Field("127.0.0.1",
                      description="Address winstond listens on. 127.0.0.1 keeps the API and web UI on this machine; "
                                  "it has no login.",
                      json_schema_extra=ui("advanced"))
    port: int = Field(8700, description="Port for the API and the web UI.",
                      json_schema_extra=ui("advanced"))


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
    announce: AnnounceConfig = AnnounceConfig()
    logging: LoggingConfig = LoggingConfig()
    control: ControlConfig = ControlConfig()
