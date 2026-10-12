"""Tests for Ask Claude: `winston ext test claude`. The Claude Agent SDK's query is replaced by a fake
that answers at once, so nothing reaches Claude; the keys are made up."""
from dataclasses import dataclass, field
from pathlib import Path

from winston_sdk.testing import FakeClock, Harness

HERE = Path(__file__).parent / "claude.py"
TOKEN, API_KEY = "sk-ant-oat01-made-up", "sk-ant-api03-made-up"


@dataclass
class Text:
    text: str


@dataclass
class Said:
    content: list = field(default_factory=list)


@dataclass
class Result:
    total_cost_usd: float
    is_error: bool = False


def harness(key: str = TOKEN, answer: str = "Tides follow the moon.", cost: float = 0.05, **settings) -> Harness:
    h = Harness(HERE, settings=settings, secrets={"key": key}, clock=FakeClock("2026-10-12T10:00:00"))
    h.asked = []

    async def query(prompt, options):
        h.asked.append((prompt, options))
        yield Said([Text(answer)])
        yield Result(cost)

    module = h.ext.tools["ask"].fn.__globals__
    module.update(query=query, AssistantMessage=Said, ResultMessage=Result, TextBlock=Text)
    return h


async def test_only_the_question_goes_with_the_key_it_signs_in_with():
    h = harness()
    res = await h.tool("ask", question=" Why are there tides? ")
    assert res["answer"] == "Tides follow the moon.", res
    [(prompt, opts)] = h.asked
    assert prompt == "Why are there tides?"
    assert opts.env == {"CLAUDE_CODE_OAUTH_TOKEN": TOKEN} and opts.tools == [] and opts.setting_sources == []
    assert opts.max_turns == 1 and opts.model == "claude-opus-5" and opts.effort == "low"
    assert await h.status() == "Signs in with a token from claude setup-token; $0.05 of $3.00 spent today"
    await h.close()


async def test_an_api_key_goes_as_one():
    h = harness(API_KEY)
    await h.tool("ask", question="Why?")
    assert h.asked[0][1].env == {"ANTHROPIC_API_KEY": API_KEY}
    await h.close()


async def test_the_day_budget_refuses_before_asking():
    h = harness(cost=0.2, max_usd_per_day=0.3)
    await h.tool("ask", question="One?")
    await h.tool("ask", question="Two?")
    try:
        await h.tool("ask", question="Three?")
        raise AssertionError("a question past the budget was asked")
    except Exception as e:                                          # noqa: BLE001
        assert "budget ($0.30) is used up" in str(e), e
    assert len(h.asked) == 2
    await h.close()


async def test_a_key_that_is_not_claudes_is_said():
    h = harness("AIza-made-up")
    assert (await h.status()).startswith("No way to sign in"), await h.status()
    try:
        await h.tool("ask", question="Why?")
        raise AssertionError("asked with a key that is not Claude's")
    except Exception as e:                                          # noqa: BLE001
        assert "no way to sign in" in str(e), e
    assert h.asked == []
    await h.close()
