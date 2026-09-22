"""Winston's brain: the LLM service plus its tools, shared by every channel (voice pipeline,
text CLI, later Telegram). Home Assistant is reached through its MCP server, registered
with Pipecat's MCPClient so the LLM service runs the tool-call loop itself, streaming.

    brain = Brain(); await brain.start()
    context = brain.new_context()          # persona + tools
    ... pipeline uses brain.llm ...
    await brain.close()
"""
import json
import os
import time
from pathlib import Path

import yaml
from dotenv import load_dotenv
from loguru import logger
from mcp.client.session_group import StreamableHttpParameters
from pipecat.frames.frames import FunctionCallResultFrame, FunctionCallsStartedFrame
from pipecat.observers.base_observer import BaseObserver, FramePushed
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.services.mcp_service import MCPClient
from pipecat.services.openai.llm import OpenAILLMService

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
CFG = yaml.safe_load((ROOT / "config.yaml").read_text())


def persona() -> str:
    return (ROOT / CFG["persona"]["prompt"]).read_text().strip()


def build_llm() -> OpenAILLMService:
    c = CFG["llm"]
    assert c["provider"] == "openai_compatible", f"unsupported llm provider {c['provider']}"
    return OpenAILLMService(
        api_key="local", base_url=c["base_url"],
        settings=OpenAILLMService.Settings(
            model=c["model"],
            system_instruction=persona(),      # must live in Settings; a bare kwarg is ignored when settings= is given
            temperature=c.get("temperature", 0.7), top_p=c.get("top_p", 0.8),
            presence_penalty=c.get("presence_penalty", 0.0),
            max_completion_tokens=c.get("max_tokens", 512),
            extra={"extra_body": {"chat_template_kwargs": {"enable_thinking": c.get("enable_thinking", False)}}},
        ),
    )


class Brain:
    def __init__(self):
        self.llm = build_llm()
        self.tools = None
        self._mcp: MCPClient | None = None

    async def start(self):
        ha = CFG["home_assistant"]
        url = os.environ[ha["url_env"]].rstrip("/") + ha["mcp_path"]
        token = os.environ[ha["token_env"]]           # never logged, never in the prompt (plan §6 rule 6)
        self._mcp = MCPClient(server_params=StreamableHttpParameters(
            url=url, headers={"Authorization": f"Bearer {token}"}))
        t0 = time.perf_counter()
        await self._mcp.start()
        self.tools = await self._mcp.register_tools(self.llm)
        names = [t.name for t in self.tools.standard_tools]
        logger.info(f"Home Assistant MCP at {url}: {len(names)} tools in {(time.perf_counter() - t0) * 1000:.0f} ms: {names}")

    async def close(self):
        if self._mcp:
            await self._mcp.close()

    def new_context(self) -> LLMContext:
        return LLMContext([], tools=self.tools)   # the persona rides on the LLM service as system_instruction


class ToolCallLogObserver(BaseObserver):
    """Plan §6 rule 7: every tool call (what/when/args/result) goes to logs/tool_calls.jsonl."""

    def __init__(self, path: Path | None = None, channel: str = "?"):
        super().__init__()
        self._path = path or ROOT / CFG["logging"]["tool_calls"]
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
