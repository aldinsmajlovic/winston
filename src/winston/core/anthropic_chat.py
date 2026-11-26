"""The brain on Claude's own API (llm.provider: anthropic). Everything above it speaks OpenAI chat completions:
Pipecat's OpenAI service and WinstonLLM's shortcuts, lines and guards, the diary, extensions' background calls.
So this is a client with the one method they call, chat.completions.create, that asks the Messages API through
the official SDK and answers in OpenAI's shapes. Not Anthropic's OpenAI-compatible URL: it caches nothing and
ignores effort, so every request would read his ~12k-token prompt afresh and thinking could not be turned down.

Caching: the system prompt carries its own breakpoint, so conversations running side by side (the box, the Talk
page, Discord, background calls) share its read; the automatic breakpoint at the end of each request caches that
conversation up to there, so its next request reads only what is new.

Thinking: a Claude reply may start with thinking blocks, and a tool round has to send them back unchanged with
its results. OpenAI's messages have no place for them, so the content of each reply that called a tool is kept
here by its calls' ids and sent in place of what Pipecat wrote down for it (one assistant message per call, its
words before or after them). A reply without a tool call goes back as its words.

    client = AnthropicChat(api_key, "https://api.anthropic.com/v1")     # in place of AsyncOpenAI
    await client.chat.completions.create(model=..., messages=[...], stream=True)
"""
import time
import uuid
from collections import OrderedDict
from types import SimpleNamespace
from typing import Any

import anthropic
from loguru import logger
from openai.types.chat import ChatCompletion, ChatCompletionChunk, ChatCompletionMessage
from openai.types.chat.chat_completion import Choice
from openai.types.chat.chat_completion_chunk import Choice as ChunkChoice
from openai.types.chat.chat_completion_chunk import ChoiceDelta, ChoiceDeltaToolCall, ChoiceDeltaToolCallFunction
from openai.types.completion_usage import CompletionUsage, PromptTokensDetails
from pipecat.adapters.services.anthropic_adapter import AnthropicLLMAdapter
from pipecat.processors.aggregators.llm_context import LLMSpecificMessage

# A Claude reply may think at any effort, and the thinking counts against max_tokens: a spoken reply's 512, or an
# extension's 60 for a pick, could be spent before a word is said (brain.THINK_TOKENS). A cap of 1 (a warm-up) stays.
MIN_TOKENS = 2048
FINISH = {"end_turn": "stop", "stop_sequence": "stop", "pause_turn": "stop", "tool_use": "tool_calls",
          "max_tokens": "length", "refusal": "content_filter"}
# Tool calls whose reply is kept: dropping one from a conversation still going would change its history (a cache
# miss from there), so far more than a day's rounds.
KEEP = 2000
EPHEMERAL = {"type": "ephemeral"}
# Pipecat's own conversion (tool calls and results, merged turns, images, empty text); its leading underscore
# means a Pipecat upgrade may move it, which tests/anthropic_cases.py catches.
_ADAPTER = AnthropicLLMAdapter()


def _param(block: Any) -> dict | None:
    """A reply's content block as it goes back, unchanged, in the fields the API takes."""
    t = block.type
    if t == "thinking":
        return {"type": t, "thinking": block.thinking, "signature": block.signature}
    if t == "redacted_thinking":
        return {"type": t, "data": block.data}
    if t == "text":
        return {"type": t, "text": block.text}
    if t == "tool_use":
        return {"type": t, "id": block.id, "name": block.name, "input": block.input}
    return None


def _tool(t: dict) -> dict:
    f = t.get("function") or {}
    return {"name": f["name"], "description": f.get("description") or "",
            "input_schema": f.get("parameters") or {"type": "object", "properties": {}}}


def _closed(schema: Any) -> Any:
    """A JSON schema with every object closed (additionalProperties false), as structured outputs want."""
    if isinstance(schema, list):
        return [_closed(v) for v in schema]
    if not isinstance(schema, dict):
        return schema
    out = {k: _closed(v) for k, v in schema.items()}
    if out.get("type") == "object":
        out.setdefault("additionalProperties", False)
    return out


def _usage(u: Any) -> CompletionUsage:
    read, wrote = u.cache_read_input_tokens or 0, u.cache_creation_input_tokens or 0
    prompt = u.input_tokens + read + wrote
    return CompletionUsage(prompt_tokens=prompt, completion_tokens=u.output_tokens,
                           total_tokens=prompt + u.output_tokens,
                           prompt_tokens_details=PromptTokensDetails(cached_tokens=read, cache_write_tokens=wrote))


def _finish(reply: Any) -> str:
    if reply.stop_reason == "refusal":
        logger.warning(f"Claude declined to answer ({getattr(reply.stop_details, 'category', None)})")
    return FINISH.get(reply.stop_reason or "", "stop")


class AnthropicChat:
    """In place of AsyncOpenAI for the brain: client.chat.completions.create(**params), streamed or not."""

    def __init__(self, api_key: str, base_url: str | None = None, timeout: float | None = None):
        root = (base_url or "").rstrip("/").removesuffix("/v1") or None     # Winston's addresses end in /v1
        self._client = anthropic.AsyncAnthropic(api_key=api_key, base_url=root, **({"timeout": timeout} if timeout else {}))
        self._kept: OrderedDict[str, list[dict]] = OrderedDict()           # a tool call's id: its reply's content
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    async def create(self, *, model: str, messages: list, stream: bool = False, tools: Any = None,
                     tool_choice: Any = None, max_tokens: Any = None, max_completion_tokens: Any = None,
                     extra_body: Any = None, response_format: Any = None, **_: Any) -> Any:
        """OpenAI's request as Claude's. Sampling settings are left out: current Claude models refuse any but
        their own. extra_body's output_config (effort, from brain.thinking_body) goes through as it is."""
        n = next((v for v in (max_completion_tokens, max_tokens) if isinstance(v, int)), MIN_TOKENS)
        req: dict[str, Any] = {"model": model, "max_tokens": n if n <= 1 else max(n, MIN_TOKENS),
                               **self.prompt(messages), "cache_control": EPHEMERAL}
        if isinstance(tools, list) and tools:
            req["tools"] = [_tool(t) for t in tools]
            if tool_choice == "none":
                req["tool_choice"] = {"type": "none"}
        config = dict((extra_body or {}).get("output_config") or {}) if isinstance(extra_body, dict) else {}
        if isinstance(response_format, dict) and response_format.get("type") == "json_schema":
            config["format"] = {"type": "json_schema", "schema": _closed(response_format["json_schema"]["schema"])}
        if config:
            req["output_config"] = config
        if not stream:
            reply = await self._client.messages.create(**req)
            text = "".join(b.text for b in reply.content if b.type == "text")
            return ChatCompletion(
                id=reply.id, object="chat.completion", created=int(time.time()), model=reply.model,
                choices=[Choice(index=0, finish_reason=_finish(reply),
                                message=ChatCompletionMessage(role="assistant", content=text or None))],
                usage=_usage(reply.usage))
        manager = self._client.messages.stream(**req)
        # Opened here, not on the first chunk, so a refused request raises where WinstonLLM catches it.
        return self._chunks(manager, await manager.__aenter__())

    def prompt(self, messages: list) -> dict:
        """OpenAI messages as Claude's system and messages, each tool round's reply as it was given."""
        out: list[Any] = []
        sent: set[int] = set()
        said: set[str] = set()          # the words of replies sent from what was kept
        for m in messages:
            if not isinstance(m, dict) or m.get("role") != "assistant":
                out.append(m)
                continue
            calls = m.get("tool_calls") or []
            reply = next((self._kept[c.get("id")] for c in calls if c.get("id") in self._kept), None)
            if reply is None:
                words = m.get("content")
                if calls or not isinstance(words, str) or words.strip() not in said:
                    out.append(m)                   # (not its words again, written down after its results)
                continue
            if id(reply) in sent:                   # the same reply's next call
                continue
            sent.add(id(reply))
            while out and isinstance(out[-1], dict) and out[-1].get("role") == "assistant" \
                    and not out[-1].get("tool_calls"):
                out.pop()                           # its words, written down before its calls
            out.append(LLMSpecificMessage(llm="anthropic", message={"role": "assistant", "content": reply}))
            if words := "".join(b["text"] for b in reply if b["type"] == "text").strip():
                said.add(words)
        converted = _ADAPTER._from_universal_context_messages(out)
        msgs = converted.messages
        if msgs and msgs[0]["role"] == "assistant":     # he spoke first (a nudge): the API wants a user turn first
            msgs.insert(0, {"role": "user", "content": "."})
        _ADAPTER._ensure_last_message_is_user(msgs)
        system = converted.system if isinstance(converted.system, str) else None
        return {"messages": msgs, **({"system": [{"type": "text", "text": system, "cache_control": EPHEMERAL}]}
                                     if system else {})}

    async def _chunks(self, manager: Any, events: Any):
        """The reply as OpenAI's chunks: words as they come, each tool call by its own index, then the usage."""
        cid, created = f"chatcmpl-{uuid.uuid4().hex[:24]}", int(time.time())
        model, calls = "", {}                       # content block index: tool call index

        def chunk(delta: ChoiceDelta | None = None, finish: Any = None, usage: Any = None) -> ChatCompletionChunk:
            return ChatCompletionChunk(id=cid, object="chat.completion.chunk", created=created, model=model, usage=usage,
                                       choices=[] if delta is None else [ChunkChoice(index=0, delta=delta,
                                                                                     finish_reason=finish)])
        try:
            async for e in events:
                if e.type == "message_start":
                    model = e.message.model
                elif e.type == "content_block_start" and e.content_block.type == "tool_use":
                    calls[e.index] = len(calls)
                    yield chunk(ChoiceDelta(tool_calls=[ChoiceDeltaToolCall(
                        index=calls[e.index], id=e.content_block.id, type="function",
                        function=ChoiceDeltaToolCallFunction(name=e.content_block.name, arguments=""))]))
                elif e.type == "content_block_delta" and e.delta.type == "text_delta" and e.delta.text:
                    yield chunk(ChoiceDelta(content=e.delta.text))
                elif e.type == "content_block_delta" and e.delta.type == "input_json_delta" and e.delta.partial_json:
                    yield chunk(ChoiceDelta(tool_calls=[ChoiceDeltaToolCall(
                        index=calls[e.index], function=ChoiceDeltaToolCallFunction(arguments=e.delta.partial_json))]))
            reply = await events.get_final_message()
            if any(b.type == "tool_use" for b in reply.content):
                content = [p for b in reply.content if (p := _param(b))]
                self._kept.update((b.id, content) for b in reply.content if b.type == "tool_use")
                while len(self._kept) > KEEP:
                    self._kept.popitem(last=False)
            yield chunk(ChoiceDelta(), _finish(reply))
            yield chunk(usage=_usage(reply.usage))
        finally:
            await manager.__aexit__(None, None, None)
