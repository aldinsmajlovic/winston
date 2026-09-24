"""Fixes to Pipecat 1.11 applied at import (the brain imports this, so every channel gets them).

Parallel tool calls ran the LLM twice. The assistant aggregator runs inference after a tool result
when no sibling call is still in progress, but a sibling whose FunctionCallInProgressFrame has not
reached the aggregator yet is stored as None and skipped by that check. Home Assistant answers in
milliseconds, so "boot my PC, set the shelf lamp to forty and what's the weather" had the second
result start a reply with the third missing, and the third start another: the lamp was set twice
and everything was said twice (tests/conversations.py, "multi", 2026-09-24). Pushing the context
only when no call at all is outstanding waits for the whole batch; cancelled calls leave the map,
so nothing waits forever."""
from loguru import logger
from pipecat.processors.aggregators.llm_response_universal import LLMAssistantAggregator

_push = LLMAssistantAggregator._maybe_push_context_after_function_result


async def _push_when_batch_done(self: LLMAssistantAggregator) -> None:
    if self._function_calls_in_progress:
        logger.debug(f"{self}: {len(self._function_calls_in_progress)} tool calls still out, not running the LLM yet")
        return
    await _push(self)


LLMAssistantAggregator._maybe_push_context_after_function_result = _push_when_batch_done
