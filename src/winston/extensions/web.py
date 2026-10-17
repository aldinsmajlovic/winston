# /// script
# [tool.winston]
# id = "web"
# name = "Web search"
# description = "Current facts from the web: Gemini searches Google and answers in a sentence or two. Only the question leaves the house."
# icon = "simple-icons:googlegemini"
# api = 1
# permissions = { network = ["generativelanguage.googleapis.com"] }
# ///
"""Web search: current facts the local model cannot know (populations, prices, scores, news).

Gemini with Google Search grounding reads the pages and answers in a sentence or two, so the brain
gets a short answer, not raw web text. Only the query leaves the house (plan §6 rule 2), and the
answer comes back as outside information (rule 3). Capped per day; grounding needs billing enabled
on the key."""
from __future__ import annotations

from typing import Literal

from winston_sdk import Extension, Field, Secret, Settings

ext = Extension()
API = "https://generativelanguage.googleapis.com/v1beta/models"
INSTRUCTION = ("Always run a Google search first, even if you think you know; then answer from the results in at most "
               "two short sentences suited to being read aloud: the fact itself, with the year or date it refers to "
               "when it changes over time. No lists, no markdown, no URLs. If the results do not answer it, say so.")
TIMEOUT = 15


class WebSettings(Settings):
    api_key: str = Secret("GEMINI_API_KEY", label="API key",
                          description="From aistudio.google.com. Grounding needs billing enabled on the key; the "
                                      "paid tier includes a monthly grounding quota and does not train on prompts.")
    max_per_day: int = Field(100, ge=0, title="Searches per day",
                             description="Hard cap; past it he says he cannot search until midnight.")
    model: str = Field("gemini-3.5-flash-lite", level="advanced",
                       description="The Gemini model that searches. Flash-Lite answered in about 1.4 s, Flash in 2.3 s "
                                   "(minimal thinking, measured 2026-09-23).")
    thinking: Literal["minimal", "low", "medium", "high"] = Field(
        "minimal", level="advanced", description="Reasoning at the search model. Each step up costs about a second.")


ext.settings(WebSettings)


@ext.tool(tier="read", timeout=TIMEOUT + 5, filler="Let me look that up.", filler_after=0)
async def search(ctx, query: str) -> dict:
    """Look up current facts on the internet: populations, prices, exchange rates, sports results, news, opening
    hours, anything that changes or that you are unsure of. Not for the state of the home. Answer in one sentence
    from its result.

    Args:
        query: The question, self-contained, with no personal details or anything about the home, e.g. "current
            population of Bosnia".
    """
    query = query.strip()
    if not query:
        raise ValueError("empty query")
    s = ctx.settings
    if not s.api_key:
        raise RuntimeError("no Gemini API key is set")
    if not await ctx.quota("searches", s.max_per_day):
        raise RuntimeError(f"the daily web search limit ({s.max_per_day}) is reached")
    r = await ctx.http.post(f"{API}/{s.model}:generateContent", headers={"x-goog-api-key": s.api_key}, timeout=TIMEOUT,
                            json={"contents": [{"role": "user", "parts": [{"text": query}]}],
                                  "systemInstruction": {"parts": [{"text": INSTRUCTION}]},
                                  "tools": [{"googleSearch": {}}],
                                  "generationConfig": {"thinkingConfig": {"thinkingLevel": s.thinking.upper()}}})
    if r.status_code == 429:
        raise RuntimeError("the search service is unavailable right now")
    if r.status_code != 200:
        raise RuntimeError(f"Gemini answered {r.status_code}: {r.text[:200]}")
    candidate = (r.json().get("candidates") or [{}])[0]
    answer = "".join(p.get("text", "") for p in candidate.get("content", {}).get("parts", [])).strip()
    grounding = candidate.get("groundingMetadata") or {}
    sources: list[str] = []
    for chunk in grounding.get("groundingChunks") or []:
        title = (chunk.get("web") or {}).get("title")
        if title and title not in sources:
            sources.append(title)
    return {"answer": answer, "sources": sources[:3], "searched": bool(grounding.get("webSearchQueries"))}
