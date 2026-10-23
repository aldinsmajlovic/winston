"""Typed configuration. The YAML in the data directory is validated against this, the web UI
generates its forms from the JSON Schema, and every field carries which engine a change restarts
(json_schema_extra: restarts; engine fields also list the drivers they apply to).

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
- `enabled_by` (on a section): the sibling switch that turns the section on; while it is off the other
  fields wait for it but stay editable.
- `hidden`: never drawn, config.yaml only (a variable's name, an address, a port, a path, a Literal with one
  value, a value another control sets, or a measured timing; docs/running.md lists them).
  Every other field is drawn where its section is.
- `secret`: {"default": "<variable name>", "label": "<what it holds>", "help": "<where to get it>"} on a field
  that names a variable in data/.env. That name field is hidden, while the form draws a field to
  paste the key into; /api/secrets writes it.
- keyed maps: `key_title`, `value_title`, `key_options` with `key_options_query`; value options sit on
  additionalProperties.
- `icon`: a logo or kind for a section's title.
- `help`: one visible line under the field, at most 140 characters (tests/schema_copy_cases.py); on a section,
  its lead line. `description` is the (i): how it works and its trade-offs.
- `unit`: the suffix on a number ("s", "min", "MB"); titles carry none. `labels` on a number: words for
  special values, keyed by the value as text ({"0": "Off"}).
- `widget`: "slider" for a number felt rather than typed, "args" for a command line (one argument per line),
  "time", "time_range", "hint_words".
- `group`: fields of one section with the same group get one heading before the first of them."""
import os
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def ui(*, restarts: list[str] | None = None, **extra: Any) -> dict[str, Any]:
    """A field's form metadata. Keyword-only: a stale level passed by position fails at import."""
    d: dict[str, Any] = dict(extra)
    if restarts:
        d["restarts"] = restarts
    return d


def section(*restarts: str, **extra: Any) -> ConfigDict:
    """Model config for a section whose fields all restart the given engines, plus section metadata."""
    return ConfigDict(extra="forbid", json_schema_extra={"restarts": list(restarts), **extra})


def secret(default: str, label: str, help: str | None = None, optional: bool = False) -> dict[str, object]:
    return {"default": default, "label": label, **({"help": help} if help else {}),
            **({"optional": True} if optional else {})}


HOSTED = {"engines.{slot}.driver": ["external", None]}     # an engine Winston does not run, or none at all
DISCORD_ID = r"^\d{17,20}$"


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AssistantConfig(Model):
    model_config = section("{brains}")
    name: str = Field("Winston", title="Name",
                      description="His name in the prompt, the consent line and the names he should hear right. "
                                  "Renaming offers to change it in his personality too.",
                      json_schema_extra=ui(help="What he is called. A room box's wake word is set under Ways to "
                                                "talk."))
    persona: str = Field("persona.md", title="Personality file",
                         description="The file in the data folder his personality is read from. Edit its words "
                                     "under Assistant, Personality.",
                         json_schema_extra=ui(hidden=True))
    language: str = Field("English", title="Reply language",
                          json_schema_extra=ui(help="He always answers in this language, whatever you speak to him "
                                                    "in. Check on Voice that his voice speaks it."))


class RegionConfig(Model):
    model_config = section("{brains}", help="How he says temperatures, units, times and dates.")
    temperature: Literal["auto", "celsius", "fahrenheit"] = Field(
        "auto", title="Temperature unit", description="Same as Home Assistant goes by its unit system.",
        json_schema_extra=ui(labels={"auto": "Same as Home Assistant", "celsius": "Celsius", "fahrenheit": "Fahrenheit"}))
    distance: Literal["auto", "metric", "imperial"] = Field(
        "auto", title="Units", description="Kilometres, kilograms and litres, or miles, pounds and gallons.",
        json_schema_extra=ui(labels={"auto": "Same as Home Assistant", "metric": "Metric (km, kg, litres)",
                                     "imperial": "Imperial (miles, pounds, gallons)"}))
    time_format: Literal["auto", "24h", "12h"] = Field(
        "auto", title="Clock", description="How he says the time. Same as Home Assistant goes by the country set "
                                           "there.",
        json_schema_extra=ui(labels={"auto": "Same as Home Assistant", "24h": "24-hour (seventeen thirty)",
                                     "12h": "12-hour (half past five)"}))
    date_format: Literal["auto", "day_month", "month_day"] = Field(
        "auto", title="Dates", description="Day or month first. Same as Home Assistant goes by the country set there.",
        json_schema_extra=ui(labels={"auto": "Same as Home Assistant", "day_month": "24 September",
                                     "month_day": "September 24"}))
    currency: str = Field("", description="A currency code for prices, like EUR or USD.",
                          json_schema_extra=ui(placeholder="Same as Home Assistant"))


class EngineConfig(Model):
    """A heavy process Winston depends on: LLM server, TTS server, later STT. Managed by the supervisor."""
    model_config = section("{engine}")
    driver: Literal["docker", "subprocess", "external"] = Field(
        title="Runs as",
        description="Docker container: Winston runs an image. Process: Winston runs a command on this computer. "
                    "External server: something you run yourself; Winston only checks it.",
        json_schema_extra=ui(labels={"docker": "Docker container", "subprocess": "Process", "external": "External server"},
                             icons={"docker": "docker", "subprocess": "process", "external": "external"}))
    enabled: bool = Field(True, title="Start with Winston",
                          json_schema_extra=ui(help="Off stops it when you save, and Winston no longer starts it."))
    port: int = Field(ge=1, le=65535, description="The port the engine listens on.",
                      json_schema_extra=ui(drivers=["docker", "subprocess"],
                                           help="Must match any port in its arguments and the address that points "
                                                "at it."))
    image: str | None = Field(None,
                              description="Container image (docker driver).", json_schema_extra=ui(drivers=["docker"]))
    gpu: bool = Field(False, title="Use the graphics card", json_schema_extra=ui(drivers=["docker"]))
    args: list[str] = Field(default_factory=list, title="Arguments",
                            description="Arguments to the container's command. {model} becomes the model chosen on "
                                        "Brain, {section.field} that setting's value, say {tts.streaming.chunk_secs}.",
                            json_schema_extra=ui(drivers=["docker"], widget="args", help="One per line."))
    cmd: list[str] = Field(default_factory=list, title="Command",
                           description="{model}, {data}, {repo} and {section.field} become the model chosen on Brain, "
                                       "the data folder, the repo and that setting's value.",
                           json_schema_extra=ui(drivers=["subprocess"], widget="args", help="One per line."))
    cwd: str | None = Field(None, title="Working directory", description="Working directory, {repo}-relative allowed.",
                            json_schema_extra=ui(drivers=["subprocess"]))
    env: dict[str, str] = Field(default_factory=dict, title="Environment",
                                description="Environment variables for the engine's process or container.",
                                json_schema_extra=ui(drivers=["docker", "subprocess"],
                                                     key_title="Variable", value_title="Value"))
    volumes: dict[str, str] = Field(default_factory=dict, title="Volumes",
                                    description="Folders the container sees: a path on this computer and where it "
                                                "appears inside the container.",
                                    json_schema_extra=ui(drivers=["docker"], key_title="Host path",
                                                         value_title="Container path"))
    runtime: str | None = Field(None, title="Container runtime", description="Such as nvidia.",
                                json_schema_extra=ui(drivers=["docker"]))
    ipc_host: bool = Field(False, title="IPC host", description="Shares the host's IPC namespace with the container.",
                           json_schema_extra=ui(drivers=["docker"],
                                                help="Some servers need it to share memory between their workers."))
    url: str | None = Field(None, title="Address", description="Where an engine you run yourself answers.",
                            json_schema_extra=ui(drivers=["external"], help="Where Winston checks that it answers."))
    health: str = Field("/health", title="Health check path",
                        description="HTTP path that returns 200 when ready. Empty: no check, the engine counts as up "
                                    "whenever its process is (hosted APIs).",
                        json_schema_extra=ui(empty_means_off=True))
    startup_timeout: int = Field(600, title="Startup time limit",
                                 description="How long it may take to pass its health check before it counts as "
                                             "failed.",
                                 json_schema_extra=ui(unit="s", help="A first start that downloads the model "
                                                                     "can take much longer on a slow connection."))


class Sampling(Model):
    model_config = section("{brains}", help="The defaults suit actions. Change them when your model's page recommends "
                                            "other values.")
    temperature: float = Field(0.3, ge=0, le=2, title="Temperature", description="Lower is more reliable at actions.")
    top_p: float = Field(0.8, ge=0, le=1, title="Top-p",
                         description="Samples only from the most likely tokens that add up to this share. Lower is "
                                     "more focused.")
    presence_penalty: float = Field(0.0, ge=-2, le=2, title="Presence penalty",
                                    description="Positive values push him away from repeating himself; 0 calls "
                                                "actions more reliably.")
    max_tokens: int = Field(512, ge=16, title="Longest reply",
                            description="Spoken replies are short; this is a safety cap. A request he thinks on gets "
                                        "at least 2048, since the thinking counts too.",
                            json_schema_extra=ui(unit="tokens",
                                                 help="Raise it if he stops mid-sentence, or says nothing after "
                                                      "thinking."))


THINKS = {"llm.thinking.control": ["chat_template", "reasoning_effort"]}   # a server Winston can switch


class Thinking(Model):
    model_config = section("{brains}", help="Thinking gives better answers to harder requests, but he starts "
                                            "talking later.")
    spoken: Literal["never", "home", "always"] = Field(
        "home", title="When you talk to him",
        description="A home request names a device or asks to change something. Thinking there means fewer slips "
                    "when switching things.",
        json_schema_extra=ui(when=THINKS, restarts=["voice", "satellite"],   # the spoken channels
                             labels={"never": "Answer straight away",
                                     "home": "Think only for home requests (about 1 s slower)",
                                     "always": "Always think (a few seconds slower)"}))
    typed: Literal["never", "always"] = Field(
        "always", title="When you type to him",
        # Of the typed channels only Discord runs its own Brain; the Talk page's typing reads the saved config.
        json_schema_extra=ui(when=THINKS, restarts=["discord"],
                             labels={"never": "Answer straight away", "always": "Think first (a few seconds slower)"}))
    home_effort: Literal["", "low", "medium", "high", "xhigh"] = Field(
        "medium", title="Effort on home requests",
        description="Medium was as accurate as the highest setting and about twice as fast. Not every model takes "
                    "every level.",
        json_schema_extra=ui(when={"llm.thinking.control": ["chat_template"],
                                   "llm.thinking.spoken": ["home"]},
                             labels={"": "Model default", "low": "Low (fastest)", "medium": "Medium",
                                     "high": "High (slower)", "xhigh": "Extra high (slowest)"}))
    control: Literal["chat_template", "reasoning_effort", "none"] = Field(
        "chat_template", title="How thinking is switched",
        description="How Winston turns his thinking on and off on this server. No switch: the server decides by "
                    "itself.",
        json_schema_extra=ui(labels={"chat_template": "Chat template (vLLM, llama.cpp)",
                                     "reasoning_effort": "Reasoning effort (Ollama)", "none": "No switch"}))

    @model_validator(mode="before")
    @classmethod
    def _switches(cls, data: Any) -> Any:
        """Older files had three switches: voice (always think when spoken to), home (think on a home request)
        and text (think when typed to). They load as today's two choices; the next save writes those."""
        if isinstance(data, dict) and {"voice", "home", "text"} & data.keys():
            data = dict(data)
            voice, home, text = data.pop("voice", False), data.pop("home", True), data.pop("text", None)
            data.setdefault("spoken", "always" if voice else "home" if home else "never")
            if text is not None:
                data.setdefault("typed", "always" if text else "never")
        return data


class LLMConfig(Model):
    model_config = section("{brains}", slot="llm")
    provider: Literal["openai_compatible"] = Field("openai_compatible",
                                                   description="Protocol the brain is spoken to with. Every "
                                                               "supported server speaks the OpenAI chat API.",
        json_schema_extra=ui(hidden=True, labels={"openai_compatible": "OpenAI-compatible"}))
    base_url: str = Field("http://127.0.0.1:8000/v1", title="API address",
                          description="Where the brain's API answers. Runs on sets it.",
                          json_schema_extra=ui(help="Where Winston sends its requests, ending in /v1."))
    api_key_env: str | None = Field(None, title="Key variable",
                                    description="The variable in data/.env that holds the API key. Unset for local "
                                                "servers.",
                                    json_schema_extra=ui(hidden=True, when=HOSTED,
                                                         secret=secret("LLM_API_KEY", "API key",
                                                                       "From your provider's account page.")))
    model: str = Field(title="Model",
                       description="Downloaded lists the models the engine can load from this computer.",
                       json_schema_extra=ui(restarts=["llm", "{brains}"], options="llm_models",
                                            help="One not on this computer is downloaded before the brain starts. Download "
                                                 "gets it now, while he keeps answering."))
    warm_up: bool = Field(True, title="Ready before the first question",
                          description="He has the brain read his instructions and tools when it starts, when a box "
                                      "connects and when the Talk page microphone opens. Without it the first answer "
                                      "waits while they are read, seconds on a large prompt. A hosted brain bills each "
                                      "read.",
                          json_schema_extra=ui(restarts=["voice", "satellite"],    # winstond reads the saved config
                                               help="The first answer comes as quickly as the rest. Off saves a "
                                                    "hosted brain the extra reads."))
    sampling: Sampling = Field(Sampling(), title="How he picks his words")
    thinking: Thinking = Field(Thinking(), title="Thinking")


TRANSCRIPTION = {"stt.provider": ["openai_transcription"]}     # speech to text on a server


class STTConfig(Model):
    model_config = section("voice", "satellite", slot="stt")
    provider: str = Field("parakeet", title="Hearing adapter",
                          description="parakeet runs inside Winston on the CPU; openai_transcription sends the audio "
                                      "to a server. Runs on sets it.",
                          json_schema_extra=ui(hidden=True))
    base_url: str | None = Field(None, title="API address",
                                 description="Where the hearing server's OpenAI transcription API answers. Runs on "
                                             "sets it.",
                                 json_schema_extra=ui(when=TRANSCRIPTION,
                                                      help="Where Winston sends its requests, ending in /v1."))
    api_key_env: str | None = Field(None, title="Key variable",
                                    description="The variable in data/.env that holds the API key. Unset for local "
                                                "servers.",
                                    json_schema_extra=ui(hidden=True, when={**TRANSCRIPTION, **HOSTED},
                                                         secret=secret("STT_API_KEY", "API key",
                                                                       "From your provider's account page.")))
    model: str = Field("nemo-parakeet-tdt-0.6b-v3", title="Model",
                       description="For Parakeet, v3 understands 25 European languages and v2 only English; for a "
                                   "server, the model name it knows.",
                       json_schema_extra=ui(options="stt_models"))
    quantization: str | None = Field("int8", title="Precision",
                                     description="int8 is faster on the CPU at no audible accuracy cost; empty for "
                                                 "full precision.",
                                     json_schema_extra=ui(hidden=True, empty_means_off=True,
                                                          when={"stt.provider": ["parakeet", None]}))
    fix_misspellings: bool = Field(True, title="Correct misheard names",
                                   description="Every change is logged with what was heard first.",
                                   json_schema_extra=ui(help="A word that sounds like one of the names below becomes "
                                                             "that name. Common English words are never changed."))
    hint_words: list[str] = Field(default_factory=list, title="Names he should hear right",
                                  description="One word each, such as room names in another language. Remove an "
                                              "added one with its x.",
                                  json_schema_extra=ui(widget="hint_words",
                                                       help="His name, your voices, contacts and names from Home "
                                                            "Assistant and extensions are added for you."))
    hint_words_removed: list[str] = Field(default_factory=list, title="Removed names",
                                          description="Names he would add by himself that you took out; the names "
                                                      "field writes these.",
                                          json_schema_extra=ui(hidden=True))


class TTSStreamingConfig(Model):
    """Reaches the speech server through `{tts.streaming.*}` in its engine command; the Chatterbox preset
    passes them. Pocket TTS streams on its own and has no such arguments."""
    model_config = section("tts")
    enabled: bool = Field(True, title="Stream speech", description="Off renders whole sentences.",
                          json_schema_extra=ui(help="First sound in about 0.3 s instead of up to 2 s. Joins between "
                                                    "chunks can sound a little choppy."))
    chunk_secs: float = Field(1.6, ge=0.8, le=6.0, title="Chunk length",
                              description="Audio per chunk after the first. Longer means fewer joins and smoother "
                                          "speech; how soon he starts talking depends only on the first chunk.",
                              json_schema_extra=ui(unit="s"))
    first_secs: float = Field(0.96, ge=0.4, le=3.0, title="First chunk",
                              description="Audio rendered before anything is sent. Shorter starts sooner; below "
                                          "about 0.9 s the second chunk can arrive late and leave a short gap.",
                              json_schema_extra=ui(hidden=True, unit="s"))
    lookahead_secs: float = Field(0.32, ge=0.08, le=1.0, title="Look-ahead",
                                  description="Audio held back at the end of each chunk until the sounds after it "
                                              "are known. More smooths the joins but makes the first chunk shorter.",
                                  json_schema_extra=ui(hidden=True, unit="s"))

    @model_validator(mode="after")
    def _lookahead_inside_first(self):
        # Compared in the Chatterbox server's own units, 25 speech tokens a second, rounded as it rounds.
        if max(1, round(self.lookahead_secs * 25)) >= max(1, round(self.first_secs * 25)):
            raise ValueError("look-ahead must be shorter than the first chunk")
        return self


class TTSConfig(Model):
    model_config = section("voice", "satellite", slot="tts")
    provider: Literal["openai_speech"] = Field("openai_speech", description="Text-to-speech adapter.",
                                              json_schema_extra=ui(hidden=True,
                                                                   labels={"openai_speech": "OpenAI speech protocol"}))
    base_url: str = Field("http://127.0.0.1:8880/v1", title="API address",
                          description="Where the speech server's OpenAI speech API answers. Runs on sets it.",
                          json_schema_extra=ui(help="Where Winston sends its requests, ending in /v1."))
    api_key_env: str | None = Field(None, title="Key variable",
                                    description="The variable in data/.env that holds the API key. Unset for local "
                                                "servers.",
                                    json_schema_extra=ui(hidden=True, when=HOSTED,
                                                         secret=secret("TTS_API_KEY", "API key",
                                                                       "From your provider's account page.")))
    voice: str = Field("onyx",
                       description="The clip or built-in voice he speaks with. Server default is the clip in the "
                                   "engine's command.",
                       json_schema_extra=ui(options="voices"))
    sample_rate: int = Field(24000, title="Sample rate", description="The audio rate the speech server returns.",
                             json_schema_extra=ui(unit="Hz", help="Must match what the speech server sends, or "
                                                                  "he sounds too fast or too slow."))
    streaming: TTSStreamingConfig = Field(default_factory=TTSStreamingConfig, title="Streaming",
                                          description="Speech is cut into chunks: a short first one so he starts "
                                                      "soon, then longer ones that join smoothly.",
                                          # the settings reach the server through its command (decisions, 09-23)
                                          json_schema_extra=ui(when={"cmd_uses": "tts.streaming."}))


class VADConfig(Model):
    model_config = section("voice", help="How long he waits in silence before answering. Shorter feels snappier; "
                                         "too short cuts you off.")
    provider: Literal["silero"] = Field("silero",
                                        description="Voice activity detector that decides when you are speaking.",
                                        json_schema_extra=ui(hidden=True, labels={"silero": "Silero"}))
    stop_secs: float = Field(0.3, ge=0.1, le=2.0, title="Silence before he answers",
                             json_schema_extra=ui(hidden=True, unit="s"))
    unfinished_stop_secs: float = Field(2.5, ge=0.2, le=5.0, title="Extra wait mid-sentence",
                                        description="Added when what you said sounds unfinished. A one-word answer "
                                                    "waits it in full.",
                                        json_schema_extra=ui(hidden=True, unit="s"))


class VoiceChannel(Model):
    model_config = section("voice", enabled_by="enabled")
    enabled: bool = Field(True, title="Talk by voice",
                          json_schema_extra=ui(help="Uses the browser's microphone with no wake word: he hears "
                                                    "everything until you press it again. Typing works either way."))
    host: str = Field("127.0.0.1", title="Address",
                      description="Where the browser's voice is received. 127.0.0.1 keeps it on this computer; "
                                  "Winston passes the browser's connection to it.",
                      json_schema_extra=ui(hidden=True))
    port: int = Field(7860, ge=1, le=65535, title="Port", description="Pipecat's own test page is served here too.",
                      json_schema_extra=ui(hidden=True))


class DiscordChannel(Model):
    model_config = section("discord", enabled_by="enabled", icon="discord",
                           help="Talk to him in direct messages with your own Discord bot.")
    enabled: bool = Field(False, title="Talk to him on Discord",
                          json_schema_extra=ui(help="Messages he brings up can reach you there when you are away."))
    token_env: str = Field("DISCORD_BOT_TOKEN", title="Token variable",
                           description="The variable in data/.env that holds the Discord bot token.",
                           json_schema_extra=ui(hidden=True, restarts=["{brains}"],
                                                secret=secret("DISCORD_BOT_TOKEN", "Bot token",
                                                              "In the Discord Developer Portal: your app, Bot, Reset "
                                                              "Token. Turn on Message Content Intent.")))
    allowed_user: str = Field("", title="Your Discord user ID", pattern=r"^(\d{17,20})?$",
                              description="The one Discord user he answers and calls \"me\".",
                              json_schema_extra=ui(restarts=["{brains}"], options="discord_users",
                                                   help="In Discord: Settings, Advanced, turn on Developer Mode, then "
                                                        "right-click yourself and Copy User ID."))
    allowed_user_env: str = Field("DISCORD_ALLOWED_USER_ID", title="Variable for your user ID (older setups)",
                                  description="The variable in data/.env read for your user ID while the field "
                                              "above is empty.",
                                  json_schema_extra=ui(hidden=True, restarts=["{brains}"],
                                                       when={"channels.discord.allowed_user": ["", None]}))
    session_idle_mins: int = Field(30, ge=1, title="New conversation after",
                                   description="Quiet minutes before a direct-message conversation starts fresh and "
                                               "reads memory again.",
                                   json_schema_extra=ui(unit="min"))
    room_mins: int = Field(60, ge=1, title="Room set with /room lasts",
                           description="Minutes without a message from you before the room you set with /room is "
                                       "cleared.",
                           json_schema_extra=ui(unit="min",
                                                help="Type /room to say where you are: \"the lights\" then means "
                                                     "that room's, and you share its conversation with its box and "
                                                     "screens."))
    contacts: dict[str, Annotated[str, Field(pattern=DISCORD_ID, json_schema_extra=ui(options="discord_users"))]] = \
        Field(default_factory=dict, title="People he can message",
              description="By the name you say and their Discord user.",
              json_schema_extra=ui(restarts=["{brains}"], key_title="Person", value_title="Discord user",
                                   help="Only people who share a server with your bot."))

    def allowed_id(self) -> str:
        """The allowed user's id: the field, else the variable older configs name (data/.env)."""
        return self.allowed_user or os.environ.get(self.allowed_user_env, "").strip()


class SatelliteDevice(Model):
    model_config = section("satellite")
    host: str = Field(title="Address",
                      json_schema_extra=ui(help="The box's IP address or .local name on your network."))
    port: int = Field(6053, ge=1, le=65535, description="ESPHome API port.", json_schema_extra=ui(hidden=True))
    key_env: str | None = Field(None, title="Key variable",
                                description="The variable in data/.env that holds the box's API encryption key. "
                                            "Unset for a box without encryption.",
                                json_schema_extra=ui(hidden=True, secret=secret(
                                    "VOICE_PE_KEY", "Encryption key", "The key Home Assistant shows for the box. "
                                                                      "Leave it empty for a box without encryption.", optional=True)))
    wake_word: str | None = Field(None, title="Wake word",
                                  description="Several, comma separated, if the box runs more than one at a time. "
                                              "A long press on the box's button "
                                              "switches it off and on again (button only).",
                                  json_schema_extra=ui(placeholder="The box's own",
                                                       help="Okay Nabu, Hey Jarvis or Hey Mycroft on a stock box; "
                                                            "Okay Winston with Winston's firmware."))
    wake_sensitivity: Literal["", "Slightly sensitive", "Moderately sensitive", "Very sensitive"] = Field(
        "", title="Wake word sensitivity",
        description="How readily the box wakes. More sensitive misses less and wakes by mistake more. On a stock "
                    "box it covers its own wake words; Okay Winston needs Winston's firmware. Empty keeps the "
                    "box's setting.",
        json_schema_extra=ui(labels={"": "The box's setting", "Slightly sensitive": "Slightly (fewest false wakes)",
                                     "Moderately sensitive": "Moderately", "Very sensitive": "Very (fewest misses)"}))
    area: str | None = Field(None, title="Home Assistant area",
                             description="Talk pages set to the same room use it too.",
                             json_schema_extra=ui(restarts=["{brains}"], options="ha_areas",
                                                  placeholder="Same as the room's name",
                                                  help="So \"the lights\" said here means this room's lights."))

    @field_validator("wake_word")
    @classmethod
    def _wake_ids(cls, value: str | None) -> str | None:
        """The box's ids for what you type: "Okay Nabu" is okay_nabu."""
        words = [w.strip().lower().replace(" ", "_") for w in (value or "").split(",")]
        return ",".join(w for w in words if w) or None


TURN_TAKING, TALKING_OVER = "Turn taking on the boxes", "Talking over him"     # groups of satellite settings


class SatelliteChannel(Model):
    model_config = section("satellite", enabled_by="enabled",
                           help="Speakers with a microphone, such as Home Assistant Voice PE. Each box is a room, so "
                                "\"the lights\" said there means that room's lights.")
    enabled: bool = Field(False, title="Listen through room boxes",
                          json_schema_extra=ui(help="Take a box out of Home Assistant's own voice assistant first: "
                                                    "only one can use its microphone."))
    devices: dict[str, SatelliteDevice] = Field(default_factory=dict, title="Room boxes",
                                                description="Each box by its room, like \"living room\". What you "
                                                            "say there shares one conversation.",
                                                json_schema_extra=ui(key_title="Room", icon="satellite"))
    follow_up_secs: float = Field(6.0, ge=0.0, le=20.0, title="Listen after a reply",
                                  description="At least 8 s after a question.",
                                  json_schema_extra=ui(unit="s", labels={"0": "Only after a question"},
                                                       help="He keeps listening this long without the wake word."))
    http_port: int = Field(8702, ge=1, le=65535, title="Audio port",
                           description="Port the boxes fetch his spoken replies from, on every interface.",
                           json_schema_extra=ui(hidden=True))
    public_host: str | None = Field(None, title="Address the boxes use",
                                    description="This computer's address as the boxes reach it. Unset finds it "
                                                "from the route to each box.",
                                    json_schema_extra=ui(hidden=True))
    session_idle_mins: int = Field(10, ge=1, title="New conversation after",
                                   description="Quiet minutes before a room's conversation starts fresh: its box "
                                               "and the Talk pages set to that room share one.",
                                   json_schema_extra=ui(unit="min"))
    end_silence_secs: float = Field(0.8, ge=0.3, le=3.0, title="Silence before he answers",
                                    json_schema_extra=ui(hidden=True, unit="s", group=TURN_TAKING))
    unfinished_silence_secs: float = Field(2.5, ge=0.5, le=5.0, title="Silence mid-sentence",
                                           description="Used instead when what you said sounds unfinished, so a "
                                                       "pause to think does not cut you off.",
                                           json_schema_extra=ui(hidden=True, unit="s", group=TURN_TAKING))
    chat_silence_secs: float = Field(1.5, ge=0.3, le=5.0, title="Pause in a conversation",
                                     description="Silence that ends a finished-sounding sentence when you "
                                                 "answer him after a reply that did nothing in the house, so "
                                                 "you can stop to think between sentences.",
                                     json_schema_extra=ui(hidden=True, unit="s", group=TURN_TAKING))
    barge_in_secs: float = Field(0.0, ge=0.0, le=2.0, title="Talk over him for at least",
                                 description="Longer if his own voice or the room stops him.",
                                 json_schema_extra=ui(hidden=True, unit="s", labels={"0": "Off"}, group=TALKING_OVER,
                                                      help="Talking over a reply stops him, and what you say becomes "
                                                           "the next request. Needs Winston's firmware on the box."))
    barge_in_volume: float = Field(0.6, ge=0.0, le=1.0, title="How loud you must be",
                                   description="Lower if speaking across the room does not stop him, higher if his "
                                               "own voice does.",
                                   json_schema_extra=ui(hidden=True, widget="slider", group=TALKING_OVER))
    barge_in_gain_db: float = Field(30.0, ge=0.0, le=60.0, title="Boost on the second microphone",
                                    description="A box with a second microphone channel (the Voice PE's carries "
                                                "little of his voice) is listened to there for talking over him, "
                                                "this much louder: you read -55 to -65 dBFS on it.",
                                    json_schema_extra=ui(hidden=True, unit="dB", group=TALKING_OVER))
    barge_in_grace_secs: float = Field(3.0, ge=0.0, le=10.0, title="Not at the start of a reply",
                                       description="The start of a reply that talking over does not stop: the "
                                                   "box's echo cancelling lets his voice through while it settles.",
                                       json_schema_extra=ui(hidden=True, unit="s", group=TALKING_OVER))


class WebChannel(Model):
    session_idle_mins: int = Field(30, ge=1, title="New conversation after",
                                   description="Quiet minutes before the Talk page's typed conversation starts fresh "
                                               "and reads memory again.",
                                   json_schema_extra=ui(unit="min"))


class Channels(Model):
    voice: VoiceChannel = Field(VoiceChannel(), title="Talk page")
    discord: DiscordChannel = DiscordChannel()
    satellite: SatelliteChannel = Field(SatelliteChannel(), title="Room boxes")
    web: WebChannel = WebChannel()


class ExposedEntity(Model):
    model_config = section("{brains}")
    name: str | None = Field(None, title="Main name",
                             json_schema_extra=ui(help="Added to Home Assistant as one more name. Its own name there "
                                                       "stays."))
    aliases: list[str] = Field(default_factory=list, title="Other names",
                               description="Other names people say for it, added to Home Assistant too.")
    say_first: str | None = Field(None, title="Say first",
                                  description="By name or another name, before he acts. For a script that sets a "
                                              "scene: \"Movie night it is.\"",
                                  json_schema_extra=ui(help="Said the moment you name it, instead of \"One "
                                                            "moment.\""))
    quiet_after: bool = Field(False, title="Stay quiet when it works",
                              description="A room box does not listen on either. A failure is still reported.",
                              json_schema_extra=ui(help="For a scene with music that speaks for itself: once it has "
                                                        "worked, he says nothing more."))
    shortcut: bool = Field(False, title="Run it as soon as you say its name",
                           description="Even twice in a row. Anything longer goes to the brain as usual.",
                           json_schema_extra=ui(help="For a script: saying just one of its names runs it without "
                                                     "asking the brain, the same every time."))


class VerifyConfig(Model):
    model_config = section("{brains}")
    enabled: bool = Field(True, title="Check the device after he acts",
                          description="After he turns something on or off or sets a light, he reads its state from "
                                      "Home Assistant (\"The shelf lamp did not respond.\").",
                          json_schema_extra=ui(help="He answers right away, then tells you if the device did not "
                                                    "change."))
    wait_secs: float = Field(3.0, ge=0.5, le=15, title="Give up after",
                             description="How long a device has to show the change before he says it did not. He "
                                         "reads it at 0.5 s, 1.5 s and this, and stops as soon as it matches.",
                             json_schema_extra=ui(unit="s", help="Raise it if he says a slow device did not respond "
                                                                 "when it did."))


class HomeAssistantConfig(Model):
    model_config = section("{brains}", enabled_by="enabled", icon="homeassistant")
    # winstond listens for announcement events with these at start, so they also restart control
    enabled: bool = Field(True, title="Use Home Assistant",
                          description="Its devices as his actions, its sensors and units, and automations that make "
                                      "him speak.",
                          json_schema_extra=ui(restarts=["{brains}", "control"],
                                               help="Off keeps these settings without using them."))
    url: str = Field(title="Address",
                     json_schema_extra=ui(restarts=["{brains}", "control"],
                                          placeholder="http://homeassistant.local:8123"))
    token_env: str = Field("HA_TOKEN", title="Token variable",
                           description="The variable in data/.env that holds the long-lived access token.",
                           json_schema_extra=ui(hidden=True, restarts=["{brains}", "control"],
                                                secret=secret("HA_TOKEN", "Access token",
                                                              "In Home Assistant: your profile, Security, Long-lived "
                                                              "access tokens. Use an administrator account.")))
    mcp_path: str = Field("/api/mcp", title="MCP server path",
                          description="Path of Home Assistant's Model Context Protocol Server integration.",
                          json_schema_extra=ui(hidden=True))
    expose: dict[str, ExposedEntity] = Field(default_factory=dict, title="Devices he can use",
                                             description="With the names people say for them. Nothing else in Home "
                                                         "Assistant is visible to him. Saving applies the list to "
                                                         "Home Assistant: it exposes these, stops exposing the rest "
                                                         "and adds their names.",
                                             json_schema_extra=ui(key_title="Entity", key_options="ha_entities"))
    expose_domains: list[str] = Field(default_factory=lambda: ["person", "weather", "todo"],
                                      title="Kinds",
                                      description="Kinds of things exposed whole, without names of their own, like "
                                                  "people and weather.",
                                      json_schema_extra=ui(options="ha_domains"))
    verify: VerifyConfig = Field(VerifyConfig(), title="Check it worked",
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


class MemoryConfig(Model):
    model_config = section("{brains}", enabled_by="enabled")
    enabled: bool = Field(True, title="Remember what you tell him",
                          description="Off also takes away his way to remember something new.",
                          json_schema_extra=ui(help="He reads it at the start of every conversation. Off keeps it all "
                                                    "but stops using it."))
    dir: str = Field("memory", title="Memory folder",
                     description="Where the fact files live, relative to the data folder.",
                     json_schema_extra=ui(hidden=True))
    commit_on_write: bool = Field(True, title="Keep a history of every change",
                                  json_schema_extra=ui(help="Every change stays in the memory folder's history, "
                                                            "forgotten facts too."))


class AnnounceConfig(Model):
    model_config = section("control", enabled_by="enabled")
    enabled: bool = Field(True, title="Let automations make him speak",
                          description="Through a Home Assistant event, Winston's API or the winston announce command.",
                          json_schema_extra=ui(help="Home Assistant automations and other apps can send him "
                                                    "something to say aloud."))
    chime: str = Field("{repo}/assets/sounds/chime.wav", title="Sound before an announcement",
                       description="So it is clearly an announcement. Empty for none.",
                       json_schema_extra=ui(restarts=["voice"], options="sounds", empty_means_off=True))
    quiet_hours: str | None = Field("22:00-07:00", title="Quiet hours", description="Empty is never quiet.",
                                    json_schema_extra=ui(widget="time_range", empty_means_off=True,
                                                         help="Nothing is spoken then, except what you asked for, "
                                                              "like reminders, and urgent alerts."))
    discord: Literal["always", "fallback", "never"] = Field(
        "fallback", title="Also send to Discord",
        description="It cannot be spoken in quiet hours, or with no speaker connected.",
        json_schema_extra=ui(labels={"always": "Always", "fallback": "When it cannot be spoken", "never": "Never"}))
    ha_event: str = Field("winston_announce", title="Home Assistant event name",
                          description="The event type an automation fires with data {text, context} to announce.",
                          json_schema_extra=ui(hidden=True))


class ExtensionKind(Model):
    enabled: bool = Field(True, title="Let him bring this up",
                          json_schema_extra=ui(help="Off keeps him quiet about it."))
    deliver: str | dict[str, str] | None = Field(None, title="When",
                                                 description="When it is said, over the extension's own choice; one "
                                                             "per level if you like.")
    where: str | dict[str, str] | None = Field(None, title="Where",
                                               description="Where it is said, over the extension's own choice.")


class ExtensionTool(Model):
    enabled: bool = Field(True, title="Let him use it",
                          json_schema_extra=ui(help="Off takes the action away from him."))
    tier: Literal["read", "act", "restricted"] | None = Field(
        None, title="Permission",
        description="Over the extension's own choice: read and act run on their own, restricted asks you first, "
                    "outside the voice.")
    offer: bool = Field(False, title="Shared with other apps",
                        description="Listed at Winston's /mcp address, for Claude Code and other MCP clients.",
                        json_schema_extra=ui(help="AI apps on this computer that speak MCP can use it."))


class McpServer(Model):
    title: str | None = Field(None, title="Name")
    command: list[str] | None = Field(None, title="Start command",
                                      description="For a server that talks over stdio.",
                                      json_schema_extra=ui(widget="args",
                                                           help="The command and its arguments, one per line."))
    url: str | None = Field(None, title="Address", description="Where it listens, for a server that talks over HTTP.")
    env: list[str] = Field(default_factory=list, title="Keys it needs",
                           description="The keys it gets from this computer, by the name it reads each one by; it "
                                       "gets no others.")
    token_env: str | None = Field(None, title="Token variable",
                                  description="For a server over HTTP: the variable in data/.env that holds its "
                                              "token, sent as Authorization: Bearer on every request.",
                                  json_schema_extra=ui(hidden=True, secret=secret(
                                      "MCP_TOKEN", "Token", "Only for a server that asks for one: the token it gave "
                                                            "you.", optional=True)))


class ExtensionConfig(Model):
    model_config = section()
    enabled: bool = Field(False, title="Use this extension",
                          json_schema_extra=ui(help="Off until you switch it on; its page shows what it can reach "
                                                    "first."))
    settings: dict[str, Any] = Field(default_factory=dict, description="Its own settings, drawn from what it "
                                                                         "describes.")
    kinds: dict[str, ExtensionKind] = Field(default_factory=dict, title="Speaking up",
                                            description="Your choices for each kind of message it brings up.")
    tools: dict[str, ExtensionTool] = Field(default_factory=dict, title="Actions",
                                            description="Your choices for each action, by its own name. For an MCP "
                                                        "server, only the actions listed here are offered.")
    memory_mb: int = Field(500, ge=50, le=16000, title="Memory limit",
                           json_schema_extra=ui(unit="MB", help="Past this, it is stopped and this page says why."))
    mcp: McpServer | None = Field(None, title="MCP server", description="Set for an MCP server added as a "
                                                                          "tools-only extension.")


PUT_OFF = "When you put something off"     # the times a put-off message is said at, apart from the rules


class SpeakUpConfig(Model):
    model_config = section()
    home: str | None = Field(None, title="Your person in Home Assistant",
                             description="For messages held until you get home, spoken at home and sent to your "
                                         "phone away, and for extensions allowed to know.",
                             json_schema_extra=ui(options="ha_entities", options_query={"domain": "person"},
                                                  placeholder="Not set", help="So he knows whether you are home."))
    phone: str | None = Field(None, title="Your phone",
                              description="The Home Assistant notify service that reaches your phone (the companion "
                                          "app's). Empty: Discord, when it is set up.",
                              json_schema_extra=ui(options="ha_notify", empty_means_off=True,
                                                   placeholder="Not set: Discord instead, if it is on",
                                                   help="Gets messages while you are away, and asks you to confirm "
                                                        "actions that need your OK."))
    max_per_day: int = Field(4, ge=0, le=50, title="Spoken messages a day",
                             json_schema_extra=ui(help="Past this, the rest wait for the morning briefing. What you "
                                                       "asked for does not count."))
    briefing_from: str = Field("06:00", title="Morning briefing from",
                               json_schema_extra=ui(widget="time",
                                                    help="Given at your first conversation after this time, or when "
                                                         "you ask what's new."))
    fallback_time: str = Field("18:00", title="When after work is",
                               description="\"Remind me after work\" when he does not know when you finish: "
                                           "when you get home that day, or at this time if you are home already.",
                               json_schema_extra=ui(widget="time", group=PUT_OFF))
    day_parts: dict[str, Annotated[str, Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")]] = Field(
        default_factory=lambda: {"morning": "08:00", "afternoon": "15:00", "evening": "19:00", "tonight": "20:00"},
        title="Parts of the day", description="What \"tomorrow morning\" and \"this evening\" mean when you put "
                                              "something off.",
        json_schema_extra=ui(key_title="Part of the day", value_title="Time", group=PUT_OFF,
                             help="A 24-hour time, like 19:00."))


class TurnsConfig(Model):
    """The turn record (core/turns.py, docs/understanding.md)."""
    model_config = section("voice", "satellite")
    enabled: bool = Field(False, title="Keep recordings of what you say",
                          description="Also how long each step took, to find and mark his mistakes and test changes "
                                      "on them.",
                          json_schema_extra=ui(help="Your audio, what he heard, did and said, kept on this computer. "
                                                    "History's details, the diary and marking mistakes need it."))
    keep_days: int = Field(14, ge=1, le=365, title="Delete recordings after", json_schema_extra=ui(unit="days"))
    dir: str = Field("turns", title="Recordings folder",
                     description="Where recordings are kept, relative to the data folder.",
                     json_schema_extra=ui(hidden=True))


class LoggingConfig(Model):
    model_config = section("{brains}")
    turns: TurnsConfig = Field(default_factory=TurnsConfig, title="Recordings",
                               description="Each spoken request kept with its audio, to find his mistakes and test "
                                           "changes.")
    tool_calls: str = Field("logs/tool_calls.jsonl", title="Action log file",
                            description="Every action with its arguments and result, one JSON line each, "
                                        "relative to the data folder.",
                            json_schema_extra=ui(hidden=True))
    latency: str = Field("logs/latency.jsonl", title="Timing log file",
                         description="Each request's timings (speech end, first token, first audio), one JSON line "
                                     "each.",
                         json_schema_extra=ui(hidden=True))


class ControlConfig(Model):
    model_config = section("control", help="Where this page and Winston's API are served. There is no login.")
    host: str = Field("127.0.0.1", title="Who can open this page",
                      description="0.0.0.0 opens it to your whole network.",
                      json_schema_extra=ui(options="control_hosts", help="Every device on your network lets a phone "
                                                                         "or a wall display open it."))
    port: int = Field(8700, ge=1, le=65535, title="Port", json_schema_extra=ui(hidden=True))

    @property
    def url(self) -> str:
        """winstond's address for a client on this machine: one listening on every address is reached at 127.0.0.1."""
        host = self.host if self.host not in ("0.0.0.0", "::", "") else "127.0.0.1"
        return f"http://{host}:{self.port}"


class Shortcut(Model):
    model_config = section("{brains}")
    phrases: list[str] = Field(default_factory=list, title="Phrases",
                               description="What you say to run it. Saying just one of these, his name and the "
                                           "fillers around it aside (\"Okay Winston, party time\"), runs the "
                                           "tool straight away without the model, the same every time.")
    tool: str = Field(title="Tool",
                      description="The tool it runs: an extension tool as \"extension.tool\" (\"spotify.party\"), a "
                                  "Home Assistant script as \"script.name\", or one of his own tools by its name "
                                  "(\"start_over\"). The tool must be available to the brain.")
    args: dict[str, Any] = Field(default_factory=dict, title="Arguments",
                                 description="Fixed arguments passed to the tool every time, e.g. which track a party "
                                             "plays and which light scene it runs.")
    say_first: str | None = Field(None, title="Said first",
                                  description="What he says the instant you say it, before it runs, in place of "
                                              "\"One moment.\". For a party: \"Here we go, sir.\"")
    quiet_after: bool = Field(False, title="Stay quiet when it works",
                              description="A room box does not listen on either. A failure is still reported.",
                              json_schema_extra=ui(help="For a scene with music that speaks for itself: once it has "
                                                        "worked, he says nothing more."))


def _tools_to_extensions(data: dict[str, Any]) -> dict[str, Any]:
    """Version 2 (2026-10-12): the tools section became the built-in extensions sensors, web and claude, with the
    same settings. A Claude API key in the environment, without a token, stays the key Ask Claude uses."""
    tools, ext = data.pop("tools", None) or {}, dict(data.get("extensions") or {})
    if tools.get("sensors"):
        ext.setdefault("sensors", {"enabled": True, "settings": {"sensors": tools["sensors"]}})
    if web := tools.get("web_search"):
        ext.setdefault("web", {"enabled": bool(web.get("enabled")), "settings": {
            "api_key": web.get("api_key_env", "GEMINI_API_KEY"),
            **{k: web[k] for k in ("model", "thinking", "max_per_day") if k in web}}})
    if claude := tools.get("ask_claude"):
        api_key = bool(os.environ.get("ANTHROPIC_API_KEY")) and not os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
        ext.setdefault("claude", {"enabled": bool(claude.get("enabled")), "settings": {
            **({"key": "ANTHROPIC_API_KEY"} if api_key else {}),
            **{k: claude[k] for k in ("model", "effort", "max_usd_per_day", "max_usd_per_question") if k in claude}}})
    return {**data, "extensions": ext, "version": 2}


class WinstonConfig(Model):
    version: int = Field(2, description="Config file format version. Winston sets it.",
                         json_schema_extra=ui(hidden=True))
    assistant: AssistantConfig = AssistantConfig()
    region: RegionConfig = Field(default_factory=RegionConfig, title="Region")
    engines: dict[str, EngineConfig] = Field(default_factory=dict, title="Engines",
                                             description="Heavy processes Winston runs or checks, by name: llm for "
                                                         "the brain's server, tts for the speech server.",
                                             json_schema_extra=ui(key_title="Name"))
    llm: LLMConfig = Field(title="Brain")
    stt: STTConfig = Field(STTConfig(), title="Hearing")
    tts: TTSConfig = Field(TTSConfig(), title="Speaking")
    vad: VADConfig = Field(VADConfig(), title="Turn taking in the browser")
    channels: Channels = Field(Channels(), title="Ways to talk")
    providers: Providers = Field(Providers(), title="Home Assistant")
    memory: MemoryConfig = MemoryConfig()
    announce: AnnounceConfig = Field(AnnounceConfig(), title="Announcements")
    extensions: dict[str, ExtensionConfig] = Field(default_factory=dict, title="Extensions",
                                                   description="Each extension by its id: whether it runs, its "
                                                               "settings, and your choices for its actions and "
                                                               "messages.",
                                                   json_schema_extra=ui(key_title="Extension"))
    speak_up: SpeakUpConfig = Field(SpeakUpConfig(), title="Speaking up")
    shortcuts: list[Shortcut] = Field(default_factory=list, title="Shortcuts",
                                      description="Spoken phrases that run a tool straight away, without the model: a "
                                                  "scene, a party. Each names a tool and the fixed arguments it runs "
                                                  "with.",
                                      json_schema_extra=ui(restarts=["{brains}"]))   # a list: its items' section is not read
    logging: LoggingConfig = LoggingConfig()
    control: ControlConfig = Field(ControlConfig(), title="Web address")

    @model_validator(mode="before")
    @classmethod
    def _migrate(cls, data: Any) -> Any:
        """An older file's settings in today's places, so it loads; the next save writes it as today's."""
        if isinstance(data, dict) and int(data.get("version", 1)) < 2:
            data = _tools_to_extensions(dict(data))
        return data
