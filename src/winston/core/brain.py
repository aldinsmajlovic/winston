"""Winston's brain: the LLM service plus its tools, shared by every channel. Home Assistant is
reached through its MCP server, registered with Pipecat's MCPClient so the LLM service runs the
tool-call loop itself, streaming. Local tools sit next to the MCP ones.

    brain = Brain(cfg, channel="voice"); await brain.start()
    context = brain.new_context()
    ... pipeline uses brain.llm ...
    await brain.close()
"""
import json
import os
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from loguru import logger
from mcp.client.session_group import StreamableHttpParameters
from openai.types.chat.chat_completion_chunk import (ChatCompletionChunk, ChoiceDelta, ChoiceDeltaToolCall,
                                                     ChoiceDeltaToolCallFunction)
from openai.types.chat.chat_completion_chunk import Choice as ChunkChoice
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.frames.frames import (Frame, FunctionCallResultFrame, FunctionCallsStartedFrame, LLMContextFrame,
                                   TTSSpeakFrame)
from pipecat.observers.base_observer import BaseObserver, FramePushed
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.mcp_service import MCPClient
from pipecat.services.openai.llm import OpenAILLMService

from winston.config import resolve
from winston.core import pipecat_fixes  # noqa: F401  (patches Pipecat on import)
from winston.config.schema import WinstonConfig
from winston.core import persona as persona_file
from winston.core import directory, guard, region
from winston.core.common import Once, entity_names, key, log_event, rewrap, words_pattern
from winston.core.memory import make_memory_tools, prompt_block
from winston.core.rooms import plain_words, scoped_by_name
from winston.core.tools.ask_claude import make_ask_claude_tool
from winston.core.tools.clone_hint import make_clone_hint_tool
from winston.core.tools.messaging import make_send_message_tool
from winston.core.tools.sensors import make_sensor_tool
from winston.core.tools.web_search import make_web_search_tool
from winston.extension_host import client as extension_tools
from winston.extension_host import texts as extension_texts
from winston.core.verify import Verifier


def today() -> str:
    """The date, read when the conversation starts. Asked "what day is it?", the model skipped
    GetDateTime 4 of 6 times and guessed ("Wednesday, 24 June" on a Thursday in September), even
    told it never knows the date; with the date here it has no reason to guess. The clock comes
    with each message (Clock)."""
    now = datetime.now()
    return (f"# Today\n\nThis conversation started on {now.strftime('%A')} {now.day} {now.strftime('%B %Y')}. "
            f"Each message from the user starts with the time it was said, like [06:22]: that is the time now. "
            f"When it also names a room, like [06:22, room: Kitchen], the user is in that room: a request that names "
            f"no place (\"turn off the lights\", \"is it warm in here?\") means that room, so pass it as the area. "
            f"When the user names a device (\"the window lamps\") or another room, target that name or room and "
            f"leave this room out: a named device may be in another room.")


class Clock(FrameProcessor):
    """Puts the time in front of the newest user message, between the user aggregator and the LLM.
    With thinking off (the spoken channels) the model called GetDateTime for "what time is it?" 3 of
    6 times and made up the rest (15:44 at 06:22); a satellite said 19:52 at dawn. The system prompt
    cannot carry the clock: changing it every turn throws away the server's prompt cache. The stamp
    stays in the history, so the cached prefix does not change either. In a room the stamp names it
    ("[06:22, room: Main Room]"), so "the lights" means that room's (core/rooms.py)."""

    def __init__(self, room: str | None = None):
        super().__init__()
        self._suffix = f", room: {room}" if room else ""

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMContextFrame):
            msgs = frame.context.get_messages()
            last = msgs[-1] if msgs else None
            if (isinstance(last, dict) and last.get("role") == "user" and isinstance(last.get("content"), str)
                    and not _STAMP.match(last["content"])):
                last["content"] = f"[{datetime.now():%H:%M}{self._suffix}] {last['content']}"
        await self.push_frame(frame, direction)


_STAMP = re.compile(r"\[\d\d:\d\d(, room: [^\]]+)?\] ")


def persona(cfg: WinstonConfig, house: str = "") -> str:
    """The persona file, the remembered facts, notes from running extensions, the map of the house
    (directory.house_map), the region and the reply language, read when a session's LLM is built.
    The language comes last: a line in the persona lost to the user's language every time (asked in
    Swedish, he answered in Swedish 8 of 8); at the end of the prompt it holds."""
    text = persona_file.prompt_text(cfg).strip()
    parts = [text, prompt_block(cfg), extension_texts.prompt_block(cfg), house, region.prompt_block(cfg), today(),
             f"The user may speak another language, like Swedish: understand it and do what they ask, as you "
             f"would in {cfg.assistant.language}. But always answer in {cfg.assistant.language}: your voice speaks "
             f"only {cfg.assistant.language}."]
    return "\n\n".join(p for p in parts if p)


SPOKEN = ("voice", "satellite")      # channels whose replies are heard, not read


def thinking_for(cfg: WinstonConfig, channel: str) -> bool:
    """WINSTON_THINKING=1|0 overrides the per-channel setting, for experiments."""
    default = cfg.llm.thinking.voice if channel in SPOKEN else cfg.llm.thinking.text
    v = os.environ.get("WINSTON_THINKING", str(default))
    return str(v).strip().lower() in ("1", "true", "yes")


def api_key(env_name: str | None) -> str:
    """The key named by config, or "local" for servers that want none. A named but unset variable is an error."""
    if not env_name:
        return "local"
    if not (key := os.environ.get(env_name)):
        raise RuntimeError(f"{env_name} is not set (data/.env)")
    return key


def thinking_body(cfg: WinstonConfig, channel: str) -> dict:
    """Request fields that switch reasoning on or off, per the server's dialect (llm.thinking.control)."""
    think, control = thinking_for(cfg, channel), cfg.llm.thinking.control
    if control == "chat_template":                       # vLLM, llama.cpp: Qwen's enable_thinking template flag
        return {"chat_template_kwargs": {"enable_thinking": think}}
    if control == "reasoning_effort" and not think:      # Ollama: "none" switches it off; on is the model default
        return {"reasoning_effort": "none"}
    return {}


# A request to change something ("turn it off", "dim it a bit more") names no device every time.
_HOME_VERBS = re.compile(r"\b(?:turn|switch|dim|dimmer|brighten|brighter|boot|warmer|cooler)\b")
# Said by code while he thinks about a home request, so the wait is not silence. One after the other.
HOME_LINES = ("One moment.", "Let me see to that.", "Certainly, one moment.", "Let me look at that.")
# The reply cap when he thinks about a home request: on the box "Can you turn on the lights in uh the living
# room?" spent all of max_tokens (512) thinking and said nothing after the line.
HOME_THINK_TOKENS = 2048


def home_request(cfg: WinstonConfig) -> re.Pattern:
    """What makes a spoken turn a home request: an exposed name or alias, a light word, or a change verb."""
    names = guard.targets(cfg).pattern
    return re.compile(names + "|" + _HOME_VERBS.pattern)


@dataclass
class Own:
    """An exposed entity (or a configured shortcut) with its own way of being answered: a first line
    (say_first), no reply once it has worked (quiet_after), run straight away when it is all that was said
    (shortcut). A shortcut's entity_id is its target: an HA script (script.x) or an extension tool
    (extension.tool)."""
    entity_id: str
    pattern: re.Pattern         # its names anywhere in what was said, whole words
    names: set[str]             # its names as plain words, for a request that is only the name
    line: str | None
    quiet: bool
    shortcut: bool
    tool: str | None = None     # the target's tool, once the brain has its tools (WinstonLLM.bind)
    args: dict = field(default_factory=dict)   # fixed arguments a shortcut runs its tool with


def own_answers(cfg: WinstonConfig) -> list[Own]:
    """The exposed entities with their own way of being answered, the one with the longest name first. The
    names are the ones it answers to (common.entity_names), as guard.targets reads them."""
    ha = cfg.providers.home_assistant
    found = []
    for entity_id, e in (ha.expose if ha else {}).items():
        names = entity_names(entity_id, e)
        line = (e.say_first or "").strip() or None
        if (line or e.quiet_after or e.shortcut) and names:
            found.append((max(map(len, names)), Own(entity_id, words_pattern(names), {plain_words(n) for n in names},
                                                    line, e.quiet_after, e.shortcut)))
    for sc in cfg.shortcuts:
        names = [k for p in sc.phrases if p and (k := key(p))]
        line = (sc.say_first or "").strip() or None
        if sc.tool and names:
            found.append((max(map(len, names)), Own(sc.tool, words_pattern(names), {plain_words(n) for n in names},
                                                    line, sc.quiet_after, True, args=dict(sc.args))))
    return [own for _, own in sorted(found, key=lambda f: -f[0])]


# Around a shortcut's name: "Okay Winston, please movie night in here, sir" is still only its name.
_SHORTCUT_LEAD = {"okay", "ok", "hey", "hi", "yeah", "please", "can", "could", "would", "you", "just", "now",
                  "uh", "um", "oh", "so"}
_SHORTCUT_TAIL = {"please", "sir", "now", "thanks"}
# A place or who it is for after the name: "movie night in the living room", "party time for us".
_SHORTCUT_PLACE = re.compile(r"(?: (?:in|for) (?:here|there|me|us|the(?: \w+){1,2}))+$")


def only_name(said: str, assistant: str) -> str:
    """What was said, as plain words, without the fillers and his name around it."""
    words, name = plain_words(said).split(), plain_words(assistant)
    while words and (words[0] in _SHORTCUT_LEAD or words[0] == name):
        words = words[1:]
    while words and (words[-1] in _SHORTCUT_TAIL or words[-1] == name):
        words = words[:-1]
    return " ".join(words)


def name_forms(bare: str) -> set[str]:
    """A bare request as said, and without a place after it: either can be a shortcut's name ("movie
    night in here" is one, "movie night in the living room" is "movie night")."""
    return {bare, _SHORTCUT_PLACE.sub("", bare)}


def acted(messages: list) -> bool:
    """Whether an acting tool has succeeded since the newest user message (the guard's notes aside)."""
    names, results = {}, []
    for m in reversed(messages):
        if not isinstance(m, dict):
            continue
        if m.get("role") == "user" and isinstance(m.get("content"), str) and not guard.is_note(m["content"]):
            break
        if m.get("role") == "tool":
            results.append((m.get("tool_call_id"), m.get("content")))
        for call in m.get("tool_calls") or []:
            names[call.get("id")] = (call.get("function") or {}).get("name", "")
    return any(guard.tool_tier(names.get(cid, "get")) == guard.ACT and guard.result_ok(content)
               for cid, content in results)


MAX_TOOL_ROUNDS = 8      # rounds of tool calls one request may take before it must answer


def tool_rounds(messages: list) -> int:
    """How many times the model has called tools since the newest user message (the house's notes
    aside): each assistant message with tool calls is one round."""
    rounds = 0
    for m in reversed(messages):
        if not isinstance(m, dict):
            continue
        if m.get("role") == "user" and isinstance(m.get("content"), str) and not guard.is_note(m["content"]):
            break
        if m.get("role") == "assistant" and m.get("tool_calls"):
            rounds += 1
    return rounds


async def _nothing():
    return
    yield


async def _calling(tool: str, args: dict | None = None):
    """A completion that only calls tool, for a shortcut: Pipecat runs it as if the model had asked. A
    shortcut may carry fixed arguments (shortcuts config), so a party plays the same track every time."""
    yield ChatCompletionChunk(
        id=f"shortcut-{uuid.uuid4().hex[:8]}", object="chat.completion.chunk", created=int(time.time()), model="",
        choices=[ChunkChoice(index=0, finish_reason="tool_calls", delta=ChoiceDelta(tool_calls=[ChoiceDeltaToolCall(
            index=0, id=f"call_{uuid.uuid4().hex[:24]}", type="function",
            function=ChoiceDeltaToolCallFunction(name=tool, arguments=json.dumps(args or {})))]))])


def last_said(messages: list) -> str | None:
    """The newest thing the user said in a request's messages, skipping the guard's retry note; a tool
    result after it keeps the same turn, so a request's follow-up call thinks like its first."""
    for m in reversed(messages):
        if isinstance(m, dict) and m.get("role") == "user" and isinstance(m.get("content"), str):
            if not guard.is_note(m["content"]):
                return _STAMP.sub("", m["content"], count=1)
    return None


class WinstonLLM(OpenAILLMService):
    """The OpenAI-compatible LLM, with thinking switched on per request for a spoken home request
    (llm.thinking.home). Measured 2026-09-26 on the 27B with actions stubbed: thinking off called a
    tool 18 of 24 times ("Kitchen lights are on." with no call; once the main room lights for "turn off
    the living room lights"), on 24 of 24, about 3 s slower. The effort comes from
    llm.thinking.home_effort: on 2026-09-27 medium was as right as the template's default xhigh (21 of
    24 each) with a median turn of 3.3 s against 5.5 s. Once per user message it says one of
    HOME_LINES first (or the named entity's own say_first), pushed straight to the TTS: queued at the
    pipeline's start it would wait behind the thinking it is meant to cover. For an entity with
    quiet_after, once one of the turn's actions has worked there is no reply at all and `ended` is set,
    so the box does not listen on (his closing words about the song spoiled the scene it set). A
    shortcut script said on its own is called without the model: said a second time, the model answered
    "Done, sir. The mood is set." from the first time's call in the conversation and ran nothing. After
    MAX_TOOL_ROUNDS rounds of tool calls for one request, the next is asked without its tools, so it
    answers in words: on 2026-09-29 the 27B called GetLiveContext and GetDateTime about 80 times in one
    turn and every turn after it waited."""

    def __init__(self, *args, home: re.Pattern | None = None, control: str = "none", effort: str = "",
                 own: list[Own] | None = None, assistant: str = "", **kwargs):
        super().__init__(*args, **kwargs)
        self._home, self._control, self._effort = home, control, effort.strip()
        self._own, self._assistant = own or [], assistant
        self.ended = False          # the turn ended quietly on purpose (quiet_after); the channel resets it
        self._think = False
        self._line_for: str | None = None       # the user message a line was already said for
        self._lines = 0
        self._no_tools = False                  # this request has called enough: words now

    def bind(self, tools: list[str]):
        """The brain's tools are known: find each shortcut's tool. An HA script (script.x) is the MCP tool
        x or y__x; an extension tool (extension.tool) is its brain name, extension_tool (host.brain_name)."""
        for own in self._own:
            if not own.shortcut:
                continue
            if own.entity_id.startswith("script."):
                oid = own.entity_id.split(".", 1)[1]
                own.tool = next((t for t in tools if t == oid or t.endswith("__" + oid)), None)
            else:
                ext_id, _, name = own.entity_id.partition(".")
                want = re.sub(r"[^A-Za-z0-9_]", "_", f"{ext_id}_{name}")
                own.tool = next((t for t in tools if t == want or t == name or t.endswith("_" + name)), None)
            if own.tool is None:
                logger.warning(f"shortcut {own.entity_id}: no tool for it; is it exposed?")

    async def get_chat_completions(self, context: LLMContext):
        messages = context.get_messages()
        rounds = tool_rounds(messages)
        self._no_tools = rounds >= MAX_TOOL_ROUNDS
        if self._no_tools:
            logger.warning(f"{rounds} rounds of tool calls for one request: this one answers without tools")
        said = last_said(messages) if self._own or self._home else None
        self._think = bool(said and self._home and self._home.search(key(said)))
        own = next((o for o in self._own if said and o.pattern.search(key(said))), None)
        if own and own.quiet and acted(messages):
            self.ended, self._think = True, False
            return _nothing()
        if said and not acted(messages) and (short := next(
                (o for o in self._own if o.tool and o.names & name_forms(only_name(said, self._assistant))), None)):
            self._think, self._line_for = False, said
            if short.line:
                await self.push_frame(TTSSpeakFrame(short.line, append_to_context=False))
            logger.info(f"shortcut: {said!r} runs {short.tool}")
            return _calling(short.tool, short.args)
        if self._think and said != self._line_for:
            self._line_for = said
            line = own.line if own else None
            if line is None:
                self._lines += 1
                line = HOME_LINES[(self._lines - 1) % len(HOME_LINES)]
            await self.push_frame(TTSSpeakFrame(line, append_to_context=False))
        try:
            return await super().get_chat_completions(context)
        finally:
            self._think = self._no_tools = False

    def build_chat_completion_params(self, params_from_context) -> dict:
        params = super().build_chat_completion_params(params_from_context)
        if self._no_tools:
            # Without its tools, not with tool_choice "none": llama.cpp then lets the model write a tool
            # call as text, which would be spoken.
            params.pop("tools", None)
            params.pop("tool_choice", None)
        if self._think:
            body = dict(params.get("extra_body") or {})
            if self._control == "chat_template":
                effort = {"reasoning_effort": self._effort} if self._effort else {}
                body["chat_template_kwargs"] = {**body.get("chat_template_kwargs", {}), "enable_thinking": True,
                                                **effort}
            elif self._control == "reasoning_effort":
                body.pop("reasoning_effort", None)
            params["extra_body"] = body
            for k in ("max_tokens", "max_completion_tokens"):
                if isinstance(params.get(k), int):
                    params[k] = max(params[k], HOME_THINK_TOKENS)
        return params


def build_llm(cfg: WinstonConfig, channel: str = "voice", house: str = "") -> OpenAILLMService:
    c = cfg.llm
    if c.provider != "openai_compatible":
        raise ValueError(f"unsupported llm provider {c.provider}")
    s = c.sampling
    body = thinking_body(cfg, channel)
    # Only where thinking is otherwise off: text channels already think every turn.
    home = home_request(cfg) if channel in SPOKEN and c.thinking.home and not thinking_for(cfg, channel) else None
    return WinstonLLM(
        home=home, control=c.thinking.control, effort=c.thinking.home_effort,
        own=own_answers(cfg) if channel in SPOKEN else None, assistant=cfg.assistant.name,
        api_key=api_key(c.api_key_env), base_url=c.base_url,
        settings=OpenAILLMService.Settings(
            model=c.model,
            system_instruction=persona(cfg, house),   # must live in Settings; a bare kwarg is ignored when settings= is given
            temperature=s.temperature, top_p=s.top_p, presence_penalty=s.presence_penalty,
            max_completion_tokens=s.max_tokens,
            extra={"extra_body": body} if body else {},
        ),
    )


class Brain:
    def __init__(self, cfg: WinstonConfig, channel: str = "voice"):
        self.cfg = cfg
        self.channel = channel
        self.llm = build_llm(cfg, channel)
        self.tools: ToolsSchema = ToolsSchema(standard_tools=[])
        self._mcp: MCPClient | None = None
        # Checks Home Assistant's state after an action; the channel sets verifier.deliver and adds
        # verifier.observer to its pipeline (core/verify.py).
        self.verifier = Verifier(cfg, channel)
        self._areas: dict[str, str] = {}
        # A spoken channel sets this to say a line (an extension tool's filler when it is slow).
        self.on_slow: Callable[[str], Awaitable[None]] | None = None
        self.tools_signature: str | None = None     # the extension tools it started with (extension_host.client)

    async def start(self):
        ha = self.cfg.providers.ha
        if ha is not None:
            # Built again with the map of the house, which needs Home Assistant read first.
            snap = await directory.home(self.cfg)
            self.llm = build_llm(self.cfg, self.channel, directory.house_map(self.cfg, snap))
            self._areas = directory.device_areas(self.cfg, snap)
            token = os.environ.get(ha.token_env)
            if not token:
                raise RuntimeError(f"{ha.token_env} is not set (data/.env)")
            self._mcp = MCPClient(server_params=StreamableHttpParameters(
                url=ha.url.rstrip("/") + ha.mcp_path, headers={"Authorization": f"Bearer {token}"}))
            t0 = time.perf_counter()
            await self._mcp.start()
            self.tools = await self._mcp.register_tools(self.llm)
            if checked := self.verifier.wrap(self.llm, [t.name for t in self.tools.standard_tools]):
                logger.info(f"state check after {checked}, up to {self.verifier.wait_secs:g} s")
            self._scope_by_name()
            logger.info(f"Home Assistant MCP: {len(self.tools.standard_tools)} tools in "
                        f"{(time.perf_counter() - t0) * 1000:.0f} ms")
        for local in (make_sensor_tool(self.cfg), *make_memory_tools(self.cfg), make_web_search_tool(self.cfg),
                      make_ask_claude_tool(self.cfg), make_send_message_tool(self.cfg)):
            if local:
                schema, handler = local
                self.llm.register_function(schema.name, handler)
                self.tools.standard_tools.append(schema)
        if self.channel != "voice":                 # the voice channel adds the real one
            self.add_tool(*make_clone_hint_tool())
        await extension_tools.register(self, self.channel)
        if isinstance(self.llm, WinstonLLM):
            self.llm.bind([t.name for t in self.tools.standard_tools])
        logger.info(f"tools: {[t.name for t in self.tools.standard_tools]}")

    def _scope_by_name(self):
        """Wrap the Home Assistant tools that take a name and an area, so a call that names a device keeps
        that device's own area (rooms.scoped_by_name)."""
        for t in self.tools.standard_tools:
            if {"name", "area"} <= set(t.properties or {}):
                rewrap(self.llm, t.name, self._scoped)

    def _scoped(self, original):
        async def handler(params):
            if isinstance(params.arguments, dict):
                scoped = scoped_by_name(params.arguments, self._areas)
                if scoped is not params.arguments:
                    logger.info(f"{params.function_name}: {params.arguments['name']!r} is not in "
                                f"{params.arguments['area']!r}, area left out")
                    params.arguments = scoped
            await original(params)
        return handler

    def add_tool(self, schema, handler):
        """A channel's own tool, registered before the first new_context()."""
        self.llm.register_function(schema.name, handler)
        self.tools.standard_tools.append(schema)

    async def close(self):
        await self.verifier.close()
        if self._mcp:
            await self._mcp.close()

    async def warm(self):
        """Have the LLM server read the instructions and tools now, so the first real turn does not.
        After a restart or a persona change that took 2.8 s (3,270 tokens) on top of a spoken
        "Hello"; the server keeps the prompt cached afterwards (llama.cpp --cache-ram)."""
        t0 = time.perf_counter()
        try:
            await self.llm.run_inference(self.new_context(), max_tokens=1)
            logger.info(f"brain warmed in {(time.perf_counter() - t0) * 1000:.0f} ms")
        except Exception as e:                                          # noqa: BLE001
            logger.warning(f"brain warm-up failed: {e}")

    def new_context(self) -> LLMContext:
        return LLMContext([], tools=self.tools if self.tools.standard_tools else None)


class ToolCallLogObserver(BaseObserver):
    """Plan §6 rule 7: every tool call (what, when, args, result) goes to the tool-call log."""

    def __init__(self, cfg: WinstonConfig, channel: str = "?"):
        super().__init__()
        self._path: Path = resolve(cfg.logging.tool_calls)
        self._channel = channel
        self._first = Once()

    async def on_push_frame(self, data: FramePushed):
        f = data.frame
        if isinstance(f, FunctionCallsStartedFrame):
            for c in f.function_calls:
                if self._first(("call", c.tool_call_id)):
                    log_event(self._path, self._channel, event="call", tool=c.function_name, id=c.tool_call_id,
                              args=c.arguments)
                    logger.info(f"tool call {c.function_name}({json.dumps(c.arguments)})")
        elif isinstance(f, FunctionCallResultFrame):
            if not self._first(("result", f.tool_call_id)):
                return
            res = f.result if isinstance(f.result, str) else json.dumps(f.result, default=str)
            log_event(self._path, self._channel, event="result", tool=f.function_name, id=f.tool_call_id,
                      result=res[:2000])
            logger.info(f"tool result {f.function_name}: {res[:200]}")
