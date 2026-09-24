"""ask_claude: hand a hard question to Claude (reasoning, explanations, writing, planning) when the
local model is out of its depth. Runs through the Claude Agent SDK, which authenticates with a
Claude subscription (the plan's monthly Agent SDK credit) or an API key, whichever the CLI finds first:
ANTHROPIC_API_KEY, then CLAUDE_CODE_OAUTH_TOKEN from `claude setup-token`, then the machine's Claude Code
login (auth_source says which, for the Tools page).

Plan §6 rule 2: only the question the local model writes leaves the house. Claude gets no tools,
no settings and no files (an empty scratch directory), just the question. Spend is capped per day
from the cost the SDK reports. Configured under tools.ask_claude."""
import json
import os
import tempfile
import time
from datetime import date
from pathlib import Path
from typing import Literal

from loguru import logger
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.services.llm_service import FunctionCallParams

from winston.config import resolve
from winston.config.schema import WinstonConfig

SYSTEM = ("You answer questions passed on by a home voice assistant. Reply in plain spoken English, at most four "
          "short sentences unless the question asks for more; no markdown, lists, code blocks or URLs. If the "
          "question depends on current events you cannot know, say so.")


AuthSource = Literal["api_key", "oauth_token", "login", "none"]


def auth_source(env: dict[str, str] | None = None) -> tuple[AuthSource, str | None]:
    """What the bundled Claude CLI will sign in with, in its own order, and the variable it reads.
    The SDK hands the CLI this process's environment; an empty ANTHROPIC_API_KEY is removed first
    (make_ask_claude_tool), so empty counts as unset. The login is the credentials file Claude Code
    writes on Linux; it is looked for, never read."""
    env = os.environ if env is None else env
    if env.get("ANTHROPIC_API_KEY", "").strip():
        return "api_key", "ANTHROPIC_API_KEY"
    if env.get("CLAUDE_CODE_OAUTH_TOKEN", "").strip():
        return "oauth_token", "CLAUDE_CODE_OAUTH_TOKEN"
    config_dir = Path(env.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude").expanduser()
    if (config_dir / ".credentials.json").is_file():
        return "login", None
    return "none", None


class DailySpend:
    """Dollars spent per calendar day, from the SDK's reported cost; kept in the logs directory."""

    def __init__(self, cfg: WinstonConfig):
        self._path = resolve(cfg.logging.tool_calls).parent / "ask_claude_spend.json"
        self.limit = cfg.tools.ask_claude.max_usd_per_day

    def spent(self) -> float:
        try:
            return float(json.loads(self._path.read_text()).get(date.today().isoformat(), 0.0))
        except (OSError, ValueError):
            return 0.0

    def add(self, usd: float):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps({date.today().isoformat(): round(self.spent() + usd, 6)}))


def make_ask_claude_tool(cfg: WinstonConfig):
    c = cfg.tools.ask_claude
    if not c.enabled:
        return None
    if os.environ.get("ANTHROPIC_API_KEY") == "":      # an empty line in .env would shadow the subscription login
        del os.environ["ANTHROPIC_API_KEY"]
    spend = DailySpend(cfg)
    workdir = tempfile.mkdtemp(prefix="winston-claude-")   # empty: nothing on disk for the CLI to pick up

    async def ask_claude(params: FunctionCallParams):
        from claude_agent_sdk import (AssistantMessage, ClaudeAgentOptions, ClaudeSDKError, ResultMessage,
                                      TextBlock, query)
        question = str(params.arguments.get("question", "")).strip()
        if not question:
            return await params.result_callback({"error": "empty question"})
        if spend.spent() >= spend.limit:
            return await params.result_callback({"error": f"daily Claude budget (${spend.limit:.2f}) is used up"})
        opts = ClaudeAgentOptions(tools=[], setting_sources=[], max_turns=1, model=c.model, effort=c.effort,
                                  system_prompt=SYSTEM, cwd=workdir, max_budget_usd=c.max_usd_per_question)
        t0, text, cost, failed = time.perf_counter(), [], 0.0, False
        try:
            async for m in query(prompt=question, options=opts):
                if isinstance(m, AssistantMessage):
                    text += [b.text for b in m.content if isinstance(b, TextBlock)]
                elif isinstance(m, ResultMessage):
                    cost, failed = m.total_cost_usd or 0.0, m.is_error
        except ClaudeSDKError as e:
            logger.warning(f"ask_claude failed: {e}")
            return await params.result_callback({"error": f"Claude is unreachable: {str(e)[:200]}"})
        spend.add(cost)
        logger.info(f"ask_claude in {(time.perf_counter() - t0) * 1000:.0f} ms, ${cost:.4f} "
                    f"(today ${spend.spent():.2f} of ${spend.limit:.2f})")
        answer = " ".join(text).strip()
        if failed or not answer:
            return await params.result_callback({"error": "Claude did not answer"})
        await params.result_callback({"answer": answer,
                                      "note": "Claude's answer: relay it in your own words, briefly."})

    schema = FunctionSchema(
        name="ask_claude",
        description="Ask Claude, a much stronger AI model in the cloud, when a question needs careful reasoning, "
                    "explanation, writing, planning, maths or knowledge you are unsure of. Not for current facts "
                    "(use web_search) and not for the home. Send only the question itself, self-contained, without "
                    "personal details unless the user asked to include them.",
        properties={"question": {"type": "string", "description": "The full question, self-contained."}},
        required=["question"])
    return schema, ask_claude
