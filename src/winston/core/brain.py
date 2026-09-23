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
import time
from pathlib import Path

from loguru import logger
from mcp.client.session_group import StreamableHttpParameters
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.frames.frames import FunctionCallResultFrame, FunctionCallsStartedFrame
from pipecat.observers.base_observer import BaseObserver, FramePushed
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.services.mcp_service import MCPClient
from pipecat.services.openai.llm import OpenAILLMService

from winston.config import resolve
from winston.config.schema import WinstonConfig
from winston.core.tools.sensors import make_sensor_tool


def persona(cfg: WinstonConfig) -> str:
    return resolve(cfg.assistant.persona).read_text().strip()


def thinking_for(cfg: WinstonConfig, channel: str) -> bool:
    """WINSTON_THINKING=1|0 overrides the per-channel setting, for experiments."""
    default = cfg.llm.thinking.voice if channel == "voice" else cfg.llm.thinking.text
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

    async def start(self):
        ha = self.cfg.providers.home_assistant
        if ha is not None:
            token = os.environ.get(ha.token_env)
            if not token:
                raise RuntimeError(f"{ha.token_env} is not set (data/.env)")
            self._mcp = MCPClient(server_params=StreamableHttpParameters(
                url=ha.url.rstrip("/") + ha.mcp_path, headers={"Authorization": f"Bearer {token}"}))
            t0 = time.perf_counter()
            await self._mcp.start()
            self.tools = await self._mcp.register_tools(self.llm)
            logger.info(f"Home Assistant MCP: {len(self.tools.standard_tools)} tools in "
                        f"{(time.perf_counter() - t0) * 1000:.0f} ms")
        if local := make_sensor_tool(self.cfg):
            schema, handler = local
            self.llm.register_function(schema.name, handler)
            self.tools.standard_tools.append(schema)
        logger.info(f"tools: {[t.name for t in self.tools.standard_tools]}")

    async def close(self):
        if self._mcp:
            await self._mcp.close()

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
