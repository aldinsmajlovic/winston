# /// script
# dependencies = ["claude-agent-sdk==0.2.158"]
# [tool.winston]
# id = "claude"
# name = "Ask Claude"
# description = "Hard questions go to Claude in the cloud: reasoning, explanations, writing, planning. Only the question leaves the house."
# icon = "message-circle-question"
# api = 1
# permissions = { network = ["api.anthropic.com"] }
# ///
"""Ask Claude: a hard question goes to Claude when the local model is out of its depth.

Runs the Claude Code CLI bundled with the Claude Agent SDK with no tools, no settings and an empty
working directory, one turn, so Claude sees only the question (plan §6 rule 2). It signs in with
the key when one is set (a token from `claude setup-token` uses the Claude plan's credit, an API
key is billed per question), else with this machine's Claude Code login, which the CLI reads from
~/.claude itself. Spend is capped per day from the cost the SDK reports."""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Literal

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKError,
    ResultMessage,
    TextBlock,
    query,
)
from winston_sdk import Extension, Field, Secret, Settings

ext = Extension()
SYSTEM = ("You answer questions passed on by a home voice assistant. Reply in plain spoken English, at most four "
          "short sentences unless the question asks for more; no markdown, lists, code blocks or URLs. If the "
          "question depends on current events you cannot know, say so.")
KINDS = {"sk-ant-oat": "CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-api": "ANTHROPIC_API_KEY"}
WORKDIR = tempfile.mkdtemp(prefix="winston-claude-")        # empty: nothing on disk for the CLI to pick up


class ClaudeSettings(Settings):
    key: str = Secret("CLAUDE_CODE_OAUTH_TOKEN", label="Key",
                      description="A token from claude setup-token (sk-ant-oat...) uses your Claude plan's credit; an "
                                  "API key (sk-ant-api...) is billed per question. Empty: the Claude Code login on "
                                  "this machine.")
    max_usd_per_day: float = Field(3.0, ge=0, title="Budget per day (USD)",
                                   description="Past it he says Claude's budget is used up until midnight. A Max 5x "
                                               "plan's monthly Agent SDK credit is 100 USD.")
    model: str = Field("claude-opus-5", level="advanced",
                       description="The Claude model the question goes to. Opus is best at hard questions, Sonnet "
                                   "and Haiku are faster and cheaper.")
    effort: Literal["low", "medium", "high"] = Field("low", level="advanced",
                                                     description="Reasoning effort. Low answered in about 3 to 5 s; "
                                                                 "each step up is slower.")
    max_usd_per_question: float = Field(0.5, gt=0, title="Budget per question (USD)", level="advanced",
                                        description="Hard cap for one question; a longer answer is cut off.")


ext.settings(ClaudeSettings)


def sign_in(key: str) -> tuple[str | None, dict[str, str]]:
    """What the CLI signs in with, and the variable that carries the key to it."""
    for prefix, var in KINDS.items():
        if key.startswith(prefix):
            return ("a token from claude setup-token" if var == "CLAUDE_CODE_OAUTH_TOKEN" else "an API key"), {var: key}
    if key:
        return None, {}
    if (Path.home() / ".claude" / ".credentials.json").is_file():
        return "the Claude Code login on this machine", {}
    return None, {}


def spent(ctx) -> float:
    return float(ctx.store.get("spent", {}).get(ctx.today().isoformat(), 0.0))


@ext.tool(tier="read", timeout=60, filler="Let me think about that.", filler_after=0)
async def ask(ctx, question: str) -> dict:
    """Ask Claude, a much stronger AI model in the cloud, when a question needs careful reasoning, explanation,
    writing, planning, maths or knowledge you are unsure of, rather than guessing. Not for current facts and not
    for the home. Relay its answer briefly in your own words.

    Args:
        question: The full question, self-contained, without personal details unless the user asked to include
            them.
    """
    question = question.strip()
    if not question:
        raise ValueError("empty question")
    s = ctx.settings
    how, env = sign_in(s.key)
    if how is None:
        raise RuntimeError("Claude has no way to sign in: the key is not a Claude key, or no key or login is set")
    if spent(ctx) >= s.max_usd_per_day:
        raise RuntimeError(f"the daily Claude budget (${s.max_usd_per_day:.2f}) is used up")
    opts = ClaudeAgentOptions(tools=[], setting_sources=[], max_turns=1, model=s.model, effort=s.effort,
                              system_prompt=SYSTEM, cwd=WORKDIR, max_budget_usd=s.max_usd_per_question, env=env)
    text, cost, failed = [], 0.0, False
    try:
        async for m in query(prompt=question, options=opts):
            if isinstance(m, AssistantMessage):
                text += [b.text for b in m.content if isinstance(b, TextBlock)]
            elif isinstance(m, ResultMessage):
                cost, failed = m.total_cost_usd or 0.0, m.is_error
    except ClaudeSDKError as e:
        raise RuntimeError(f"Claude is unreachable: {str(e)[:200]}") from e
    today = ctx.today().isoformat()
    ctx.store["spent"] = {today: round(spent(ctx) + cost, 6)}
    ctx.log.info(f"${cost:.4f}, today ${spent(ctx):.2f} of ${s.max_usd_per_day:.2f}")
    answer = " ".join(text).strip()
    if failed or not answer:
        raise RuntimeError("Claude did not answer")
    return {"answer": answer, "note": "Claude's answer: relay it in your own words, briefly."}


@ext.status
async def status(ctx) -> str:
    how, _ = sign_in(ctx.settings.key)
    if how is None:
        return "No way to sign in: the key is not a Claude key, or no key or Claude Code login is set"
    return f"Signs in with {how}; ${spent(ctx):.2f} of ${ctx.settings.max_usd_per_day:.2f} spent today"
