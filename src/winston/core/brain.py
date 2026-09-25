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
from datetime import datetime
from pathlib import Path

from loguru import logger
from mcp.client.session_group import StreamableHttpParameters
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.frames.frames import Frame, FunctionCallResultFrame, FunctionCallsStartedFrame, LLMContextFrame
from pipecat.observers.base_observer import BaseObserver, FramePushed
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.mcp_service import MCPClient
from pipecat.services.openai.llm import OpenAILLMService

from winston.config import resolve
from winston.core import pipecat_fixes  # noqa: F401  (patches Pipecat on import)
from winston.config.schema import WinstonConfig
from winston.core import persona as persona_file
from winston.core import region
from winston.core.memory import make_memory_tools, prompt_block
from winston.core.tools.ask_claude import make_ask_claude_tool
from winston.core.tools.clone_hint import make_clone_hint_tool
from winston.core.tools.messaging import make_send_message_tool
from winston.core.tools.sensors import make_sensor_tool
from winston.core.tools.web_search import make_web_search_tool
from winston.core.verify import Verifier


def today() -> str:
    """The date, read when the conversation starts. Asked "what day is it?", the model skipped
    GetDateTime 4 of 6 times and guessed ("Wednesday, 24 June" on a Thursday in September), even
    told it never knows the date; with the date here it has no reason to guess. The clock comes
    with each message (Clock)."""
    now = datetime.now()
    return (f"# Today\n\nThis conversation started on {now.strftime('%A')} {now.day} {now.strftime('%B %Y')}. "
            f"Each message from the user starts with the time it was said, like [06:22]: that is the time now.")


class Clock(FrameProcessor):
    """Puts the time in front of the newest user message, between the user aggregator and the LLM.
    With thinking off (the spoken channels) the model called GetDateTime for "what time is it?" 3 of
    6 times and made up the rest (15:44 at 06:22); a satellite said 19:52 at dawn. The system prompt
    cannot carry the clock: changing it every turn throws away the server's prompt cache. The stamp
    stays in the history, so the cached prefix does not change either."""

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMContextFrame):
            msgs = frame.context.get_messages()
            last = msgs[-1] if msgs else None
            if (isinstance(last, dict) and last.get("role") == "user" and isinstance(last.get("content"), str)
                    and not _STAMP.match(last["content"])):
                last["content"] = f"[{datetime.now():%H:%M}] {last['content']}"
        await self.push_frame(frame, direction)


_STAMP = re.compile(r"\[\d\d:\d\d\] ")


def persona(cfg: WinstonConfig) -> str:
    """The persona file, the remembered facts, the region and the reply language, read when a session's
    LLM is built.
    The language comes last: a line in the persona lost to the user's language every time (asked in
    Swedish, he answered in Swedish 8 of 8); at the end of the prompt it holds."""
    text = persona_file.prompt_text(cfg).strip()
    parts = [text, prompt_block(cfg), region.prompt_block(cfg), today(),
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


def build_llm(cfg: WinstonConfig, channel: str = "voice") -> OpenAILLMService:
    c = cfg.llm
    if c.provider != "openai_compatible":
        raise ValueError(f"unsupported llm provider {c.provider}")
    s = c.sampling
    body = thinking_body(cfg, channel)
    return OpenAILLMService(
        api_key=api_key(c.api_key_env), base_url=c.base_url,
        settings=OpenAILLMService.Settings(
            model=c.model,
            system_instruction=persona(cfg),   # must live in Settings; a bare kwarg is ignored when settings= is given
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

    async def start(self):
        ha = self.cfg.providers.ha
        if ha is not None:
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
        logger.info(f"tools: {[t.name for t in self.tools.standard_tools]}")

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
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._channel = channel
        self._seen: set[tuple[str, str]] = set()   # observers see a frame once per pipeline hop

    def _write(self, **rec):
        with self._path.open("a") as f:
            f.write(json.dumps({"ts": time.time(), "channel": self._channel, **rec}, default=str) + "\n")

    async def on_push_frame(self, data: FramePushed):
        f = data.frame
        if isinstance(f, FunctionCallsStartedFrame):
            for c in f.function_calls:
                if ("call", c.tool_call_id) in self._seen:
                    continue
                self._seen.add(("call", c.tool_call_id))
                self._write(event="call", tool=c.function_name, id=c.tool_call_id, args=c.arguments)
                logger.info(f"tool call {c.function_name}({json.dumps(c.arguments)})")
        elif isinstance(f, FunctionCallResultFrame):
            if ("result", f.tool_call_id) in self._seen:
                return
            self._seen.add(("result", f.tool_call_id))
            res = f.result if isinstance(f.result, str) else json.dumps(f.result, default=str)
            self._write(event="result", tool=f.function_name, id=f.tool_call_id, result=res[:2000])
            logger.info(f"tool result {f.function_name}: {res[:200]}")
