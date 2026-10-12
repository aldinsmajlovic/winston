"""Tests for Web search: `winston ext test web`. Gemini's answers are made up here, shaped as its
generateContent API returns them; nothing reaches the network."""
import json
from pathlib import Path

import httpx
from winston_sdk.testing import Harness

HERE = Path(__file__).parent / "web.py"
KEY = "made-up-gemini-key"


class Gemini:
    """A fake generativelanguage.googleapis.com: one answer, and every request it was sent."""

    def __init__(self, status: int = 200):
        self.status, self.asked = status, []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.asked.append(request)
        if self.status != 200:
            return httpx.Response(self.status, json={"error": {"message": "made up"}})
        return httpx.Response(200, json={"candidates": [{
            "content": {"parts": [{"text": "About 275,000 people live in Sarajevo "}, {"text": "(2025)."}]},
            "groundingMetadata": {"webSearchQueries": ["population of Sarajevo"],
                                  "groundingChunks": [{"web": {"title": t}} for t in
                                                      ("wikipedia.org", "wikipedia.org", "census.ba", "a.ba", "b.ba")]}}]})


def harness(gemini: Gemini, **settings) -> Harness:
    return Harness(HERE, settings=settings, secrets={"api_key": KEY}, http=httpx.MockTransport(gemini.handle))


async def test_only_the_question_is_sent_and_a_short_answer_comes_back():
    g = Gemini()
    h = harness(g, thinking="low")
    res = await h.tool("search", query=" population of Sarajevo ")
    assert res == {"answer": "About 275,000 people live in Sarajevo (2025).",
                   "sources": ["wikipedia.org", "census.ba", "a.ba"], "searched": True}, res
    [req] = g.asked
    assert req.url.path == "/v1beta/models/gemini-3.5-flash-lite:generateContent", req.url
    assert req.headers["x-goog-api-key"] == KEY
    body = json.loads(req.content)
    assert body["contents"] == [{"role": "user", "parts": [{"text": "population of Sarajevo"}]}], body
    assert body["tools"] == [{"googleSearch": {}}]
    assert body["generationConfig"]["thinkingConfig"]["thinkingLevel"] == "LOW"
    await h.close()


async def test_the_daily_cap_refuses_before_asking():
    g = Gemini()
    h = harness(g, max_per_day=1)
    await h.tool("search", query="capital of Norway")
    try:
        await h.tool("search", query="capital of Sweden")
        raise AssertionError("a search past the cap was made")
    except Exception as e:                                          # noqa: BLE001
        assert "limit (1)" in str(e), e
    assert len(g.asked) == 1
    await h.close()


async def test_a_busy_service_is_said_plainly():
    h = harness(Gemini(status=429))
    try:
        await h.tool("search", query="capital of Norway")
        raise AssertionError("a 429 was taken as an answer")
    except Exception as e:                                          # noqa: BLE001
        assert "unavailable right now" in str(e), e
    await h.close()


async def test_its_line_is_said_as_the_search_starts():
    h = harness(Gemini())
    spec = h.ext.tools["search"]
    assert spec.filler == "Let me look that up." and spec.filler_after == 0
    await h.close()
