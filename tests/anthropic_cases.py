"""Claude's own API (llm.provider: anthropic, core/anthropic_chat.py): the brain's OpenAI-shaped requests become
Messages API requests and the replies come back as OpenAI chunks, with each tool round's thinking sent back as it
was given. Against a stand-in for the SDK's stream; no network, no key.

    uv run python tests/anthropic_cases.py
"""
import asyncio
import json
import os
from types import SimpleNamespace as NS

import harness
from harness import expect, finish, scratch_data

DATA = scratch_data("winston-anthropic-")
(DATA / "persona.md").write_text((harness.REPO / "examples" / "persona.md").read_text())
os.environ["WINSTON_ANTHROPIC_TEST_KEY"] = "sk-ant-test"
harness.detail_on_pass = False

from pipecat.adapters.schemas.function_schema import FunctionSchema  # noqa: E402
from pipecat.adapters.schemas.tools_schema import ToolsSchema  # noqa: E402
from pipecat.processors.aggregators.llm_context import LLMContext  # noqa: E402

from winston.config.presets import apply  # noqa: E402
from winston.config.schema import WinstonConfig  # noqa: E402
from winston.core.anthropic_chat import AnthropicChat  # noqa: E402
from winston.core.brain import build_llm, server_slot, thinking_body  # noqa: E402

THINKING = {"type": "thinking", "thinking": "", "signature": "sig-1"}


def call(cid: str, name: str, args: dict) -> dict:
    return {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def block(**kw):
    return NS(**kw)


class Stream:
    """The SDK's stream manager and stream in one: the events of a reply that thinks, says a few words and calls two
    tools, then the reply itself."""

    def __init__(self, sent: list):
        self.sent, self.closed = sent, False
        self.reply = NS(id="msg_1", model="claude-haiku-5-5", stop_reason="tool_use", stop_details=None,
                        usage=NS(input_tokens=40, output_tokens=30, cache_read_input_tokens=11000,
                                 cache_creation_input_tokens=200),
                        content=[block(type="thinking", thinking="", signature="sig-9"),
                                 block(type="text", text="Let me look."),
                                 block(type="tool_use", id="toolu_a", name="GetLiveContext", input={}),
                                 block(type="tool_use", id="toolu_b", name="HassTurnOn", input={"name": "lamp"})])

    def __call__(self, **req):
        self.sent.append(req)
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        self.closed = True

    def __aiter__(self):
        events = [NS(type="message_start", message=NS(model="claude-haiku-5-5")),
                  NS(type="content_block_start", index=0, content_block=NS(type="thinking")),
                  NS(type="content_block_start", index=1, content_block=NS(type="text")),
                  NS(type="content_block_delta", index=1, delta=NS(type="text_delta", text="Let me look.")),
                  NS(type="content_block_start", index=2, content_block=NS(type="tool_use", id="toolu_a",
                                                                           name="GetLiveContext")),
                  NS(type="content_block_start", index=3, content_block=NS(type="tool_use", id="toolu_b",
                                                                           name="HassTurnOn")),
                  NS(type="content_block_delta", index=3, delta=NS(type="input_json_delta",
                                                                   partial_json='{"name": ')),
                  NS(type="content_block_delta", index=3, delta=NS(type="input_json_delta", partial_json='"lamp"}')),
                  NS(type="message_delta", delta=NS(stop_reason="tool_use"))]

        async def gen():
            for e in events:
                yield e
        return gen()

    async def get_final_message(self):
        return self.reply


def config(**llm) -> WinstonConfig:
    return WinstonConfig.model_validate({"llm": {"provider": "anthropic", "base_url": "https://api.anthropic.com/v1",
                                                 "api_key_env": "WINSTON_ANTHROPIC_TEST_KEY",
                                                 "model": "claude-haiku-5-5", "thinking": {"control": "effort"},
                                                 **llm}})


def conversion():
    chat = AnthropicChat("sk-ant-test", "https://api.anthropic.com/v1")
    kept = [THINKING, {"type": "text", "text": "Let me look."},
            {"type": "tool_use", "id": "c1", "name": "GetLiveContext", "input": {}},
            {"type": "tool_use", "id": "c2", "name": "HassTurnOn", "input": {"name": "lamp"}}]
    chat._kept.update({"c1": kept, "c2": kept})
    # Pipecat's shape: one assistant message per call with its result after it, the reply's words written down
    # after the results.
    history = [{"role": "system", "content": "You are Winston."},
               {"role": "user", "content": "[06:22] is the lamp on?"},
               {"role": "assistant", "tool_calls": [call("c1", "GetLiveContext", {})]},
               {"role": "tool", "tool_call_id": "c1", "content": '{"lamp": "off"}'},
               {"role": "assistant", "tool_calls": [call("c2", "HassTurnOn", {"name": "lamp"})]},
               {"role": "tool", "tool_call_id": "c2", "content": '{"success": true}'},
               {"role": "assistant", "content": "Let me look."},
               {"role": "assistant", "content": "It's on now, sir."},
               {"role": "user", "content": "[06:23] thanks"}]
    p = chat.prompt(history)
    msgs = p["messages"]
    expect("the system prompt goes on its own, cached for every conversation",
           p.get("system") == [{"type": "text", "text": "You are Winston.", "cache_control": {"type": "ephemeral"}}],
           p.get("system"))
    expect("a tool round goes back as the reply was given: its thinking, words and both calls, once",
           [m["role"] for m in msgs] == ["user", "assistant", "user", "assistant", "user"]
           and msgs[1]["content"] == kept, [m["role"] for m in msgs])
    expect("both results answer the round in one user turn",
           [b.get("tool_use_id") for b in msgs[2]["content"]] == ["c1", "c2"], msgs[2]["content"])
    expect("its words written down again after the results are not sent twice",
           msgs[3]["content"] in ("It's on now, sir.", [{"type": "text", "text": "It's on now, sir."}]), msgs[3])
    expect("what was kept is not changed by sending it", chat._kept["c1"][0] == THINKING, chat._kept["c1"][0])

    plain = chat.prompt([{"role": "user", "content": "hi"},
                         {"role": "assistant", "tool_calls": [call("x1", "GetDateTime", {})]},
                         {"role": "tool", "tool_call_id": "x1", "content": "06:22"}])["messages"]
    expect("a round with nothing kept (a shortcut, or from before a restart) goes back as its calls",
           plain[1]["content"] == [{"type": "tool_use", "id": "x1", "name": "GetDateTime", "input": {}}], plain)
    first = chat.prompt([{"role": "assistant", "content": "Good morning, sir."}])["messages"]
    expect("a conversation he opened (a nudge) gets a user turn before and after: Claude wants both",
           [m["role"] for m in first] == ["user", "assistant", "user"], first)


async def requests():
    cfg = config()
    llm = build_llm(cfg, "voice")
    sent: list = []
    stream = Stream(sent)
    llm._client._client.messages.stream = stream
    context = LLMContext([{"role": "user", "content": "[06:22] turn on the lamp"}])
    chunks = [c async for c in await llm.get_chat_completions(context)]
    req = sent[0]
    expect("a spoken request asks for low effort", req.get("output_config") == {"effort": "low"}, req.get("output_config"))
    expect("sampling settings are left out (Claude refuses any but its own)",
           not {"temperature", "top_p", "presence_penalty", "extra_body"} & req.keys(), sorted(req))
    expect("the conversation is cached to its end", req.get("cache_control") == {"type": "ephemeral"}, req)
    expect("a reply has room to think", req["max_tokens"] >= 2048, req["max_tokens"])
    expect("the model is sent as named", req["model"] == "claude-haiku-5-5", req["model"])
    words = "".join(c.choices[0].delta.content or "" for c in chunks if c.choices)
    calls = [t for c in chunks if c.choices for t in c.choices[0].delta.tool_calls or []]
    expect("its words stream as content", words == "Let me look.", words)
    expect("each call has its own index, its arguments after it",
           [(t.index, t.id, t.function.name if t.function else None) for t in calls if t.id]
           == [(0, "toolu_a", "GetLiveContext"), (1, "toolu_b", "HassTurnOn")]
           and "".join(t.function.arguments or "" for t in calls if t.index == 1) == '{"name": "lamp"}', calls)
    finish_reason = [c.choices[0].finish_reason for c in chunks if c.choices and c.choices[0].finish_reason]
    expect("a reply that calls tools ends as tool_calls", finish_reason == ["tool_calls"], finish_reason)
    usage = chunks[-1].usage
    expect("the usage counts cached reads in the prompt, as Pipecat reports it",
           usage.prompt_tokens == 11240 and usage.prompt_tokens_details.cached_tokens == 11000, usage)
    expect("the stream is closed after its last chunk", stream.closed)
    expect("the round's reply is kept for both calls, thinking first",
           llm._client._kept.get("toolu_b", [{}])[0].get("signature") == "sig-9", llm._client._kept)

    rounds = [m for i in range(8) for m in ({"role": "assistant", "tool_calls": [call(f"r{i}", "GetDateTime", {})]},
                                            {"role": "tool", "tool_call_id": f"r{i}", "content": "06:22"})]
    tools = ToolsSchema(standard_tools=[FunctionSchema("GetDateTime", "The time.", {}, [])])
    llm._client._client.messages.stream = Stream(sent3 := [])
    [c async for c in await llm.get_chat_completions(
        LLMContext([{"role": "user", "content": "[06:22] what time is it?"}, *rounds], tools=tools))]
    expect("after too many rounds he answers in words with his tools still defined (Claude wants them beside "
           "the calls)", sent3[0].get("tool_choice") == {"type": "none"} and len(sent3[0].get("tools", [])) == 1,
           {k: sent3[0].get(k) for k in ("tool_choice", "tools")})

    typed = build_llm(config(), "discord")
    typed._client._client.messages.stream = Stream(sent2 := [])
    [c async for c in await typed.get_chat_completions(LLMContext([{"role": "user", "content": "hi"}]))]
    expect("a typed request thinks at the model's own effort", "output_config" not in sent2[0], sent2[0].get("output_config"))
    expect("effort stays the same for a whole conversation: no thinking for home requests on top",
           llm._home is None, llm._home)
    expect("no llama.cpp slot is looked for", await server_slot(cfg, True) is None)


async def one_off():
    chat = AnthropicChat("sk-ant-test")
    sent: list = []

    async def create(**req):
        sent.append(req)
        return NS(id="msg_2", model="claude-haiku-5-5", stop_reason="end_turn", stop_details=None,
                  usage=NS(input_tokens=5, output_tokens=4, cache_read_input_tokens=0, cache_creation_input_tokens=0),
                  content=[block(type="thinking", thinking="", signature="s"), block(type="text", text='{"breaking": true}')])
    chat._client.messages.create = create
    r = await chat.chat.completions.create(
        model="claude-haiku-5-5", messages=[{"role": "user", "content": "Is it breaking?"}], max_tokens=60,
        temperature=0.2, extra_body=thinking_body(config(), "voice"),
        response_format={"type": "json_schema", "json_schema": {"name": "answer", "schema": {
            "type": "object", "properties": {"breaking": {"type": "boolean"}}, "required": ["breaking"]}}})
    expect("a one-off answer comes back as the message's words", r.choices[0].message.content == '{"breaking": true}',
           r.choices[0].message)
    fmt = sent[0].get("output_config", {}).get("format", {})
    expect("an asked-for JSON shape goes as structured output, closed, beside the effort",
           fmt.get("schema", {}).get("additionalProperties") is False
           and sent[0]["output_config"].get("effort") == "low", sent[0].get("output_config"))


def presets():
    claude = apply({"llm": {"model": "x"}}, "llm", "anthropic")
    back = apply(claude, "llm", "ollama")
    expect("the Claude preset speaks Claude's API with effort", claude["llm"]["provider"] == "anthropic"
           and claude["llm"]["thinking"]["control"] == "effort", claude["llm"])
    expect("switching to another brain switches the protocol back", back["llm"]["provider"] == "openai_compatible",
           back["llm"])
    WinstonConfig.model_validate(claude)


conversion()
asyncio.run(requests())
asyncio.run(one_off())
presets()
finish()
