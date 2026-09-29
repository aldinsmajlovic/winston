"""Typed configuration. The YAML in the data directory is validated against this, the web UI
generates its forms from the JSON Schema, and every field carries its UI level and which
engine a change restarts (json_schema_extra: level, restarts; engine fields also list the drivers they apply to).

Restart metadata lives on the field when one field is special, else on the model: a section's
`restarts` applies to every field in it, nested sections included, unless a field says otherwise.
"{engine}" stands for the engine an EngineConfig belongs to, "{brains}" for every channel that runs a brain
(voice, discord, satellite); the web channel lives in winstond and is rebuilt on every save.

The rest of the metadata tells the form what to draw (docs/architecture.md, "Config metadata"):
- `empty_means_off`: a cleared text field saves "" (which means none or never), not the default.
- `when`: {"<dotted config path>": [values]} or {"cmd_uses": "<prefix>"}; the field or section is left out
  of the form unless every condition holds. "{slot}" in a path is the section's `slot` (llm, tts, stt);
  null in the values matches a missing key. cmd_uses holds when the slot's engine args or cmd contain
  "{<prefix>".
- `enabled_by` (on a section): the sibling switch that turns the section on; while it is off every other
  field is greyed but stays editable.
- `hidden`: never drawn (a Literal with one value: nothing to choose).
- `secret`: {"default": "<variable name>", "label": "<what it holds>"} on a field that names a variable in
  data/.env; the form draws a field for the value (basic) beside the name (advanced). /api/secrets writes it.
- keyed maps: `key_title`, `value_title`, `key_options`; value options sit on additionalProperties.
- `icon`: a logo or kind for a section's title."""
import os
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


def ui(level: str = "basic", restarts: list[str] | None = None, **extra: Any) -> dict[str, Any]:
    d: dict[str, Any] = {"level": level, **extra}
    if restarts:
        d["restarts"] = restarts
    return d


def section(*restarts: str, **extra: Any) -> ConfigDict:
    """Model config for a section whose fields all restart the given engines, plus section metadata."""
    return ConfigDict(extra="forbid", json_schema_extra={"restarts": list(restarts), **extra})


def secret(default: str, label: str) -> dict[str, str]:
    return {"default": default, "label": label}


HOSTED = {"engines.{slot}.driver": ["external", None]}     # an engine Winston does not run, or none at all
DISCORD_ID = r"^\d{17,20}$"


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AssistantConfig(Model):
    model_config = section("{brains}")
    name: str = Field("Winston", description="What he is called: his name in the prompt, the "
                                  "consent line and the hint words. Renaming offers to change it in "
                                  "his persona too.")
    persona: str = Field("persona.md", title="Persona file",
                         description="Persona file, relative to the data directory.",
                         json_schema_extra=ui("advanced", options="personas"))
    language: str = Field("English", title="Reply language",
                          description="The language he always answers in, whatever language he is spoken to in. "
                                      "Match it to the voice: Chatterbox-Turbo speaks only English.")


class RegionConfig(Model):
    model_config = section("{brains}")
    temperature: Literal["auto", "celsius", "fahrenheit"] = Field(
        "auto", description="Degrees he speaks in. Auto follows Home Assistant's unit system.",
        json_schema_extra=ui(labels={"auto": "Same as Home Assistant", "celsius": "Celsius", "fahrenheit": "Fahrenheit"}))
    distance: Literal["auto", "metric", "imperial"] = Field(
        "auto", title="Units", description="Kilometres, kilograms and litres, or miles, pounds and gallons.",
        json_schema_extra=ui(labels={"auto": "Same as Home Assistant", "metric": "Metric (km, kg, litres)",
                                     "imperial": "Imperial (miles, pounds, gallons)"}))
    time_format: Literal["auto", "24h", "12h"] = Field(
        "auto", title="Clock", description="How he says the time. Auto follows the country set in Home Assistant.",
        json_schema_extra=ui(labels={"auto": "Same as the country", "24h": "24-hour (seventeen thirty)",
                                     "12h": "12-hour (half past five)"}))
    date_format: Literal["auto", "day_month", "month_day"] = Field(
        "auto", title="Dates", description="Day or month first. Auto follows the country set in Home Assistant.",
        json_schema_extra=ui(labels={"auto": "Same as the country", "day_month": "24 September",
                                     "month_day": "September 24"}))
    currency: str = Field("", description="Currency for prices, like SEK or EUR. Empty follows Home Assistant.")


class EngineConfig(Model):
    """A heavy process Winston depends on: LLM server, TTS server, later STT. Managed by the supervisor."""
    model_config = section("{engine}")
    driver: Literal["docker", "subprocess", "external"] = Field(
        description="How the engine is run. Docker container: Winston runs an image. Process: Winston runs a "
                    "command on this machine. External server: something you run yourself; Winston only checks it.",
        json_schema_extra=ui(labels={"docker": "Docker container", "subprocess": "Process", "external": "External server"},
                             icons={"docker": "docker", "subprocess": "process", "external": "external"}))
    enabled: bool = Field(True, description="Start it with winston up. Off keeps the entry without running it.")
    port: int = Field(description="Local port the engine listens on.",
                      json_schema_extra=ui(drivers=["docker", "subprocess"]))
    health: str = Field("/health", title="Health path",
                        description="HTTP path that returns 200 when ready. Empty: no check, the engine counts as up "
                                    "whenever its process is (hosted APIs).",
                        json_schema_extra=ui("advanced", empty_means_off=True))
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
                                json_schema_extra=ui("advanced", drivers=["docker", "subprocess"],
                                                     key_title="Variable", value_title="Value"))
    volumes: dict[str, str] = Field(default_factory=dict, title="Volumes",
                                    description="Folders the container sees: a path on this machine and where it "
                                                "appears inside the container.",
                                    json_schema_extra=ui("advanced", drivers=["docker"], key_title="Host path",
                                                         value_title="Container path"))
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
    model_config = section("{brains}")
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
    model_config = section("{brains}")
    voice: bool = Field(False, description="Reasoning before answering on the voice channel. Costs seconds.")
    text: bool = Field(True, description="Reasoning on text channels. Much better at multi-part requests.")
    home: bool = Field(True, title="Home requests",
                       description="Reasoning on a spoken request about the home (a light, a lamp, the PC), even "
                                   "with voice off. About 1 s slower at medium effort; without it he sometimes "
                                   "said a light was on without switching it.")
    home_effort: str = Field("medium", title="Home request effort",
                             description="How long he reasons about a home request, for chat templates that take "
                                         "a reasoning effort (Qwen3.8: low, medium, xhigh; gpt-oss: low, medium, "
                                         "high). Medium was as right as xhigh on the 27B and about twice as fast. "
                                         "Empty uses the template's default.",
                             json_schema_extra=ui("advanced", when={"llm.thinking.control": ["chat_template"]}))
    control: Literal["chat_template", "reasoning_effort", "none"] = Field(
        "chat_template", description="How the server is told to think or not: chat_template for vLLM and llama.cpp "
                                     "(enable_thinking), reasoning_effort for Ollama, none for servers without a switch.",
        json_schema_extra=ui("advanced", labels={"chat_template": "Chat template (vLLM, llama.cpp)",
                                                 "reasoning_effort": "Reasoning effort (Ollama)", "none": "No switch"}))


class LLMConfig(Model):
    model_config = section("{brains}", slot="llm")
    provider: Literal["openai_compatible"] = Field("openai_compatible",
                                                   description="Protocol the brain is spoken to with. Every "
                                                               "supported server speaks the OpenAI chat API.",
        json_schema_extra=ui(
        "advanced", hidden=True, labels={"openai_compatible": "OpenAI-compatible"}))
    base_url: str = Field("http://127.0.0.1:8000/v1", title="Base URL",
                          description="Where the brain's OpenAI-compatible API answers, ending in /v1. Presets set it.",
                          json_schema_extra=ui("advanced"))
    api_key_env: str | None = Field(None, title="Key variable",
                                    description="The variable in data/.env that holds the API key. Unset for local "
                                                "servers.",
                                    json_schema_extra=ui("advanced", when=HOSTED,
                                                         secret=secret("LLM_API_KEY", "API key")))
    model: str = Field(description="Model name as the server knows it. Downloaded lists what the engine can load "
                                   "from this machine; any other model is downloaded when the brain restarts, and "
                                   "the brain is offline until the download ends.",
                       json_schema_extra=ui(restarts=["llm", "{brains}"], options="llm_models"))
    sampling: Sampling = Field(Sampling(), title="Sampling",
                               description="How the brain picks its words. The defaults suit tool calls; change "
                                           "them only to fix a specific habit.")
    thinking: Thinking = Field(Thinking(), title="Thinking",
                               description="Whether the brain reasons before it answers, per kind of channel. "
                                           "Better answers to hard requests, at the cost of time.")


class STTConfig(Model):
    model_config = section("voice", "satellite", slot="stt")
    provider: str = Field("parakeet",
                          description="Speech-to-text adapter.", json_schema_extra=ui(options="stt_adapters"))
    model: str = Field("nemo-parakeet-tdt-0.6b-v3",
                       description="Speech-to-text model. Parakeet TDT 0.6B v3 is multilingual; v2 is English only.",
                       json_schema_extra=ui("advanced", options="stt_models"))
    quantization: str | None = Field("int8",
                                     description="Weight precision. int8 is faster on the CPU at no audible "
                                                 "accuracy cost; empty for full precision.",
                                     json_schema_extra=ui("advanced", empty_means_off=True))
    hint_words: list[str] = Field(default_factory=list, title="Hint words",
                                  description="Names and words he should hear right. His name, cloned voices, "
                                              "Discord contacts and names from the Home Assistant list are added by "
                                              "themselves; add the rest here, one word each, such as room names in "
                                              "another language.",
                                  json_schema_extra=ui(widget="hint_words"))
    fix_misspellings: bool = Field(True, title="Fix close misspellings",
                                   description="After speech to text, a word that sounds like a hint word and is "
                                               "spelled close to it becomes that word, so a misheard name comes "
                                               "out right. Common English words are never changed, and "
                                               "every change is logged as before and after.")


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
        # Compared in the Chatterbox server's own units, 25 speech tokens a second, rounded as it rounds.
        if max(1, round(self.lookahead_secs * 25)) >= max(1, round(self.first_secs * 25)):
            raise ValueError("look-ahead must be shorter than the first chunk")
        return self


class TTSConfig(Model):
    model_config = section("voice", "satellite", slot="tts")
    provider: Literal["openai_speech"] = Field("openai_speech", description="Text-to-speech adapter.",
                                              json_schema_extra=ui("advanced", hidden=True,
                                                                   labels={"openai_speech": "OpenAI speech protocol"}))
    base_url: str = Field("http://127.0.0.1:8880/v1", title="Base URL",
                          description="Where the speech server's OpenAI speech API answers, ending in /v1. Presets "
                                      "set it.",
                          json_schema_extra=ui("advanced"))
    api_key_env: str | None = Field(None, title="Key variable",
                                    description="The variable in data/.env that holds the API key. Unset for local "
                                                "servers.",
                                    json_schema_extra=ui("advanced", when=HOSTED,
                                                         secret=secret("TTS_API_KEY", "API key")))
    voice: str = Field("onyx",
                       description="Voice name sent to the server. OpenAI names map to the server's default clip.",
                       json_schema_extra=ui(options="voices"))
    sample_rate: int = Field(24000, title="Sample rate", description="Audio rate the speech server returns, in Hz.",
                             json_schema_extra=ui("advanced"))
    streaming: TTSStreamingConfig = Field(default_factory=TTSStreamingConfig, title="Streaming",
                                          description="How speech is cut into chunks so he starts talking "
                                                      "before a sentence is fully rendered.",
                                          # the settings reach the server through its command (decisions, 09-23)
                                          json_schema_extra=ui(when={"cmd_uses": "tts.streaming."}))


class VADConfig(Model):
    model_config = section("voice")
    provider: Literal["silero"] = Field("silero",
                                        description="Voice activity detector that decides when you are speaking.",
                                        json_schema_extra=ui("advanced", hidden=True, labels={"silero": "Silero"}))
    stop_secs: float = Field(0.3, ge=0.1, le=2.0, title="End of speech (s)",
                             description="Silence after speech before the utterance is sent.",
                             json_schema_extra=ui("advanced"))
    unfinished_stop_secs: float = Field(1.0, ge=0.2, le=5.0, title="Pause when unfinished (s)",
                                        description="Extra wait after silence when the turn detector thinks the "
                                                    "sentence is unfinished. One-word answers pay this in full.",
                                        json_schema_extra=ui("advanced"))


class VoiceChannel(Model):
    model_config = section("voice", enabled_by="enabled")
    enabled: bool = Field(True, description="Talk to him by voice from the browser. Room satellites have "
                                            "their own channel.")
    host: str = Field("127.0.0.1",
                      description="Address the voice channel listens on. 127.0.0.1 keeps it on this machine; "
                                  "winstond passes the browser's offer to it.",
                      json_schema_extra=ui("advanced"))
    port: int = Field(7860,
                      description="Port the voice channel listens on; Pipecat's own test page is served here too.",
                      json_schema_extra=ui("advanced"))


class DiscordChannel(Model):
    model_config = section("discord", enabled_by="enabled", icon="discord")
    enabled: bool = Field(False, description="Talk to him in Discord DMs and get announcements there.")
    token_env: str = Field("DISCORD_BOT_TOKEN", title="Token variable",
                           description="The variable in data/.env that holds the Discord bot token.",
                           json_schema_extra=ui("advanced", restarts=["{brains}"],
                                                secret=secret("DISCORD_BOT_TOKEN", "Bot token")))
    allowed_user: str = Field("", title="Your Discord user id", pattern=r"^(\d{17,20})?$",
                              description="The one Discord user he answers and calls \"me\". In Discord: "
                                          "Settings, Advanced, Developer Mode on, then right-click yourself, Copy "
                                          "User ID. Empty reads the variable under Advanced.",
                              json_schema_extra=ui(restarts=["{brains}"], options="discord_users"))
    allowed_user_env: str = Field("DISCORD_ALLOWED_USER_ID", title="User id variable",
                                  description="The variable in data/.env read for your user id while the field "
                                              "above is empty; older configs kept it there.",
                                  json_schema_extra=ui("advanced", restarts=["{brains}"], options="env",
                                                       when={"channels.discord.allowed_user": ["", None]}))
    session_idle_mins: int = Field(30, ge=1, title="Conversation resets after (min)",
                                   description="Idle minutes before a DM conversation starts fresh (and re-reads memory).",
                                   json_schema_extra=ui("advanced"))
    contacts: dict[str, Annotated[str, Field(pattern=DISCORD_ID, json_schema_extra=ui(options="discord_users"))]] = \
        Field(default_factory=dict, title="Contacts",
              description="People he may message, by the name you say and their Discord user. The bot can "
                          "only reach people who share a server with it.",
              json_schema_extra=ui(restarts=["{brains}"], key_title="Name", value_title="Discord user",
                                   icon="discord"))

    def allowed_id(self) -> str:
        """The allowed user's id: the field, else the variable older configs name (data/.env)."""
        return self.allowed_user or os.environ.get(self.allowed_user_env, "").strip()


class SatelliteDevice(Model):
    model_config = section("satellite")
    host: str = Field(description="Address of the box on the LAN, an IP or its .local name.")
    port: int = Field(6053, ge=1, le=65535, description="ESPHome API port.", json_schema_extra=ui("advanced"))
    key_env: str | None = Field(None, title="Key variable",
                                description="The variable in data/.env that holds the box's API encryption key "
                                            "(the one Home Assistant shows for it). Unset for a box without "
                                            "encryption.",
                                json_schema_extra=ui("advanced", secret=secret("VOICE_PE_KEY", "Encryption key")))
    wake_word: str | None = Field(None, title="Wake word",
                                  description="On-device wake word to switch on, by id (okay_nabu, hey_jarvis, "
                                              "hey_mycroft on a stock Voice PE; okay_winston with Winston's "
                                              "firmware). Several, comma separated, if the box runs more than "
                                              "one at a time. Unset keeps the box's choice. A long press on "
                                              "the box's button switches it off and on again (button only).")
    wake_sensitivity: Literal["", "Slightly sensitive", "Moderately sensitive", "Very sensitive"] = Field(
        "", title="Wake word sensitivity",
        description="How readily the box wakes. More sensitive misses less and wakes by mistake more. On a stock "
                    "Voice PE it covers its own wake words; Okay Winston needs Winston's firmware from "
                    "2026-09-27 on (0.95, 0.85, 0.70). Empty keeps the box's setting.",
        json_schema_extra=ui(labels={"": "The box's setting", "Slightly sensitive": "Slightly (fewest false wakes)",
                                     "Moderately sensitive": "Moderately", "Very sensitive": "Very (fewest misses)"}))
    area: str | None = Field(None, title="Home Assistant area",
                             description="The area this room is in Home Assistant, so \"turn off the lights\" "
                                         "said here means its lights. Unset uses the room's name. Browser "
                                         "sessions set to the same room use it too.",
                             json_schema_extra=ui(restarts=["{brains}"], options="ha_areas"))


class SatelliteChannel(Model):
    model_config = section("satellite", enabled_by="enabled")
    enabled: bool = Field(False, description="Talk to him through room satellites such as Home Assistant's "
                                             "Voice Preview Edition. The box must not also be in Home Assistant's "
                                             "voice pipeline: only one client can have its microphone.")
    devices: dict[str, SatelliteDevice] = Field(default_factory=dict,
                                                description="Satellites by room or name, e.g. \"living room\".",
                                                json_schema_extra=ui(key_title="Room", icon="satellite"))
    http_port: int = Field(8702, ge=1, le=65535, title="Audio port",
                           description="Port the boxes fetch his spoken replies from, on every interface.",
                           json_schema_extra=ui("advanced"))
    public_host: str | None = Field(None, title="Address the boxes use",
                                    description="This machine's address as the boxes reach it. Unset finds it from "
                                                "the route to each box.",
                                    json_schema_extra=ui("advanced"))
    end_silence_secs: float = Field(0.8, ge=0.3, le=3.0, title="End of speech (s)",
                                    description="Silence after speech that ends a request.",
                                    json_schema_extra=ui("advanced"))
    follow_up_secs: float = Field(6.0, ge=0.0, le=20.0, title="Listen after a reply (s)",
                                  description="After every reply the box listens this long for more, without the "
                                              "wake word (at least 8 s after a question). 0: only after a question.")
    session_idle_mins: int = Field(10, ge=1, title="Conversation resets after (min)",
                                   description="Idle minutes before a room's conversation starts fresh: its box "
                                               "and the browser sessions set to that room share one.",
                                   json_schema_extra=ui("advanced"))


class WebChannel(Model):
    session_idle_mins: int = Field(30, ge=1, title="Conversation resets after (min)",
                                   description="Idle minutes before the Talk page's typed conversation starts fresh "
                                               "(and re-reads memory).",
                                   json_schema_extra=ui("advanced"))


class Channels(Model):
    voice: VoiceChannel = VoiceChannel()
    discord: DiscordChannel = DiscordChannel()
    satellite: SatelliteChannel = SatelliteChannel()
    web: WebChannel = WebChannel()


class ExposedEntity(Model):
    model_config = section("{brains}")
    name: str | None = Field(None, description="What you call it. Saved to Home Assistant as an alias; "
                                               "Home Assistant's own name is never changed.")
    aliases: list[str] = Field(default_factory=list, description="Other names people say for it, saved to Home "
                                                                 "Assistant as aliases.")
    say_first: str | None = Field(None, title="Said first",
                                  description="What he says as soon as a spoken request names it (by name or alias), "
                                              "in place of \"One moment.\", before he acts. For a script that sets a "
                                              "scene: \"Movie night it is.\"")
    quiet_after: bool = Field(False, title="Quiet after",
                              description="Once a spoken request that names it has worked, he says nothing more and "
                                          "the box does not listen on: for a scene with music that speaks for itself. "
                                          "A failure is still reported.")
    shortcut: bool = Field(False, title="Run when named",
                           description="For a script: saying just one of its names (\"Winston, movie night\") runs it "
                                       "straight away without asking the model, so it does the same every time, even "
                                       "twice in a row. Anything longer goes to the model as usual.")


class VerifyConfig(Model):
    model_config = section("{brains}")
    enabled: bool = Field(True, title="Check after an action",
                          description="After he turns something on or off or sets a light, he reads its state from "
                                      "Home Assistant and says so when it did not change (\"The shelf lamp did not "
                                      "respond.\"). His answer is not delayed; the correction follows it.")
    wait_secs: float = Field(3.0, ge=0.5, le=15, title="Give up after (s)",
                             description="Seconds a device has to show the change before he says it did not. He "
                                         "reads it at 0.5 s, 1.5 s and this, and stops as soon as it matches.",
                             json_schema_extra=ui("advanced"))


class HomeAssistantConfig(Model):
    model_config = section("{brains}", enabled_by="enabled", icon="homeassistant")
    # winstond listens for announcement events with these at start, so they also restart control
    enabled: bool = Field(True, description="Connect Winston to Home Assistant: its devices as tools, sensors, "
                                            "units and announcement events. Off keeps the settings without using "
                                            "them.",
                          json_schema_extra=ui(restarts=["{brains}", "control"]))
    url: str = Field(title="URL", description="Home Assistant base URL on the LAN.",
                     json_schema_extra=ui(restarts=["{brains}", "control"]))
    token_env: str = Field("HA_TOKEN", title="Token variable",
                           description="The variable in data/.env that holds the long-lived access token.",
                           json_schema_extra=ui("advanced", restarts=["{brains}", "control"],
                                                secret=secret("HA_TOKEN", "Access token")))
    mcp_path: str = Field("/api/mcp", title="MCP path", description="Path of Home Assistant's MCP server integration.",
                          json_schema_extra=ui("advanced"))
    expose: dict[str, ExposedEntity] = Field(default_factory=dict, title="Entities",
                                             description="Entities he may see and control, with the names people say for them. Nothing else in "
                                                         "Home Assistant is visible to him. Saving applies the list to Home "
                                                         "Assistant: exposes it, un-exposes the rest and adds the "
                                                         "aliases.",
                                             json_schema_extra=ui(key_title="Entity", key_options="ha_entities"))
    expose_domains: list[str] = Field(default_factory=lambda: ["person", "weather", "todo"], title="Domains",
                                      description="Whole domains to expose, without aliases.",
                                      json_schema_extra=ui("advanced", options="ha_domains"))
    verify: VerifyConfig = Field(VerifyConfig(), title="After an action",
                                 description="Home Assistant says done before a device has acted. He answers "
                                             "straight away, then checks and corrects himself if it did not happen.")


class Providers(Model):
    home_assistant: HomeAssistantConfig | None = Field(None, title="Home Assistant",
                                                        description="The Home Assistant he sees and controls. "
                                                                    "Unset: no smart home.")

    @property
    def ha(self) -> HomeAssistantConfig | None:
        """Home Assistant when it is configured and switched on, else None. Every consumer reads this."""
        ha = self.home_assistant
        return ha if ha is not None and ha.enabled else None


class SensorPair(Model):
    model_config = section("{brains}")
    level: str = Field(title="Value sensor", description="Entity id of the value sensor.",
                       json_schema_extra=ui(options="ha_entities", options_query={"domain": "sensor"}))
    state: str | None = Field(None, title="State sensor",
                              description="Optional companion state sensor (charging, etc.).",
                              json_schema_extra=ui(options="ha_entities",
                                                   options_query={"domain": "sensor,binary_sensor"}))


class WebSearchConfig(Model):
    model_config = section("{brains}", enabled_by="enabled", icon="gemini")
    enabled: bool = Field(False, description="Let him look up current facts on the web. Only the search question "
                                             "leaves the house.")
    provider: Literal["gemini"] = Field("gemini", description="Search backend. gemini: Gemini with Google Search "
                                                              "grounding (needs billing enabled on the key).",
                                        json_schema_extra=ui(hidden=True, labels={"gemini": "Gemini with Google Search"},
                                                             icons={"gemini": "gemini"}))
    model: str = Field("gemini-3.5-flash-lite", description="Flash-Lite answers in about 1.4 s, Flash in 2.3 s "
                                                             "(minimal thinking, measured 2026-09-23).",
                       json_schema_extra=ui("advanced", options="gemini_models"))
    thinking: Literal["minimal", "low", "medium", "high"] = Field("minimal", description="Reasoning at the search "
                                                                  "model. Each step up costs about a second.",
                                                                  json_schema_extra=ui("advanced"))
    api_key_env: str = Field("GEMINI_API_KEY", title="Key variable",
                             description="The variable in data/.env that holds the Gemini API key.",
                             json_schema_extra=ui("advanced", secret=secret("GEMINI_API_KEY", "API key")))
    max_per_day: int = Field(100, ge=0, title="Searches per day",
                             description="Hard cap; past it the tool refuses until midnight.")
    timeout: float = Field(15.0, ge=1, title="Timeout (s)",
                           description="Seconds before a search gives up and he says it could not look it up.",
                           json_schema_extra=ui("advanced"))


class AskClaudeConfig(Model):
    model_config = section("{brains}", enabled_by="enabled", icon="claude")
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
    model_config = section("{brains}")
    sensors: dict[str, SensorPair] = Field(default_factory=dict, title="Sensors",
                                           description="Watched sensors by label; readings come with their age.",
                                           json_schema_extra=ui(key_title="Label"))
    web_search: WebSearchConfig = Field(WebSearchConfig(), title="Web search",
                                        description="A cloud search for current facts: news, opening hours, prices.")
    ask_claude: AskClaudeConfig = Field(AskClaudeConfig(), title="Ask Claude",
                                        description="Claude in the cloud for questions too hard for the local brain.")


class MemoryConfig(Model):
    model_config = section("{brains}", enabled_by="enabled")
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
    model_config = section("control", enabled_by="enabled")
    enabled: bool = Field(True, description="Let him speak on his own: Home Assistant automations, the API and "
                                            "`winston announce` send him something to say.")
    chime: str = Field("{repo}/assets/sounds/chime.wav", description="Sound played before an announcement, so it "
                       "is clearly a notification. Empty for none.",
                       json_schema_extra=ui("advanced", restarts=["voice"], options="sounds", empty_means_off=True))
    quiet_hours: str | None = Field("22:00-07:00", title="Quiet hours",
                                    description="HH:MM-HH:MM when announcements are not spoken, only sent to Discord. "
                                                "Empty for never quiet.",
                                    json_schema_extra=ui(widget="time_range", empty_means_off=True))
    discord: Literal["always", "fallback", "never"] = Field(
        "fallback", title="Also to Discord",
        description="always: every announcement; fallback: when nothing can speak it (quiet hours, no speaker "
                    "connected); never.",
        json_schema_extra=ui(labels={"always": "Always", "fallback": "When it cannot be spoken", "never": "Never"}))
    ha_event: str = Field("winston_announce", title="Home Assistant event",
                          description="Event type an automation fires with data {text, context} to announce.",
                          json_schema_extra=ui("advanced"))


class LoggingConfig(Model):
    model_config = section("{brains}")
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
    version: int = Field(1, description="Config file format version. Winston sets it.",
                         json_schema_extra=ui("advanced"))
    assistant: AssistantConfig = AssistantConfig()
    region: RegionConfig = Field(default_factory=RegionConfig, title="Region")
    engines: dict[str, EngineConfig] = Field(default_factory=dict, title="Engines",
                                             description="Heavy processes Winston runs or checks, by name: llm for "
                                                         "the brain's server, tts for the speech server.",
                                             json_schema_extra=ui(key_title="Name"))
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
