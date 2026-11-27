"""Brain health cases: a failed brain call in words, noted for the Brain's status and kept in its Log, and
cleared by one that works, a restart or a save. Nothing real is asked; the supervisor's brain is external.

    uv run python tests/brain_health_cases.py
"""
import asyncio

import anthropic
import httpx
import openai
from harness import REPO, check, finish_checks, scratch_data

data = scratch_data("winston-brain-health-")        # no state/ or logs/ yet: a fresh install
(data / "persona.md").write_text((REPO / "examples" / "persona.md").read_text())     # the brain's instructions

from winston.config.schema import Channels, EngineConfig, WinstonConfig  # noqa: E402  (after WINSTON_DATA is set)
from winston.engines.brain_health import clear, failed, failure, log_tail, why_failed, worked  # noqa: E402
from winston.engines.drivers import ExternalDriver  # noqa: E402
from winston.engines.supervisor import Managed, Supervisor  # noqa: E402

REQ = httpx.Request("POST", "http://x")


def status(code: int) -> openai.APIStatusError:
    return openai.APIStatusError(f"Error code: {code} - {{'message': 'Incorrect API key provided: sk-or-...abcd'}}",
                                 response=httpx.Response(code, request=REQ), body=None)


check("401", why_failed(status(401)), "Key refused (401)")
check("403", why_failed(status(403)), "Key refused (403)")
check("402", why_failed(status(402)), "Out of credit (402)")
check("404", why_failed(status(404)), "No such model, or a wrong API address (404)")
check("429", why_failed(status(429)), "Too many requests (429)")
check("another code with no words of its own", why_failed(status(500)), "Refused (500)")
# OpenRouter answers a model that does not exist with 400; the whole body also names the account.
openrouter = openai.APIStatusError(
    "Error code: 400 - {'error': {'message': 'deepseek/no-such-model is not a valid model ID', 'code': 400}, "
    "'user_id': 'user_abc'}", response=httpx.Response(400, request=REQ),
    body={"message": "deepseek/no-such-model is not a valid model ID", "code": 400})
check("other codes say only the provider's message", why_failed(openrouter),
      "Refused (400): deepseek/no-such-model is not a valid model ID")
claude = anthropic.APIStatusError("x", response=httpx.Response(400, request=REQ),
                                  body={"type": "error", "error": {"type": "invalid_request_error", "message": "bad"}})
check("Claude's message too", why_failed(claude), "Refused (400): bad")
check("Claude's errors too",
      why_failed(anthropic.APIStatusError("x", response=httpx.Response(401, request=REQ), body=None)),
      "Key refused (401)")
check("no connection", why_failed(openai.APIConnectionError(request=REQ)), "Not reachable")
check("no key", why_failed(RuntimeError("LLM_API_KEY is not set (data/.env)")), "LLM_API_KEY is not set (data/.env)")

check("fresh data: no state or logs folder yet", ((data / "state").exists(), (data / "logs").exists()), (False, False))
check("a failure returns its words", failed("talk", status(401)), "Key refused (401)")
check("and is the note", failure(), "Key refused (401)")
check("and the log's last line", log_tail(1)[0].endswith("  talk  Key refused (401)"), True)
worked("voice")
check("a call that works clears it", failure(), "")
check("and says so", log_tail(1)[0].endswith("  voice  works again"), True)
worked("voice")
check("working again and again adds nothing", len(log_tail(100)), 2)
worked("check", "works, answered in 12 ms")
check("a check's line is kept even with no note", log_tail(1)[0].endswith("  check  works, answered in 12 ms"), True)
failed("talk", status(401)); clear()
check("clear removes the note", failure(), "")

# Only a lasting failure is the brain's status; a passing one (busy, a hiccup, out of reach) is in the Log only,
# so one bad minute does not leave him Failed until someone talks.
from winston.core.brain import KeyNotSet  # noqa: E402

check("a passing failure still has its words", failed("talk", status(429)), "Too many requests (429)")
check("but is no note", failure(), "")
check("it is in the Log", log_tail(1)[0].endswith("  talk  Too many requests (429)"), True)
failed("talk", openai.APIConnectionError(request=REQ))
check("nor is out of reach", failure(), "")
failed("talk", status(503))
check("nor a server's hiccup", failure(), "")
failed("talk", status(400))
check("a bad request is lasting (OpenRouter's missing model)", failure(), "Refused (400)")
failed("talk", KeyNotSet("LLM_API_KEY is not set (data/.env)"))
check("so is no key", failure(), "LLM_API_KEY is not set (data/.env)")
clear()

# The Brain's status and Log, on a supervisor with a hosted brain (no health path: up while winstond runs).
here = WinstonConfig.model_validate({"llm": {"model": "m", "base_url": "https://openrouter.ai/api/v1"}})
sup = Supervisor.__new__(Supervisor)
sup.cfg = here.model_copy(update={"channels": Channels()})
ext = EngineConfig(driver="external", port=443, url="https://openrouter.ai", health="", startup_timeout=0)
llm = Managed("llm", ext, ExternalDriver("llm", ext))
sup.items = {"llm": llm}
sup.on_healthy = None

failed("talk", status(402)); asyncio.run(sup.refresh())
check("a hosted brain with a failure is Failed with it", (llm.state, llm.detail), ("error", "Out of credit (402)"))
check("but up: it answers, so channels start and background asks go ahead", (llm.refused, llm.up), (True, True))
worked("talk"); asyncio.run(sup.refresh())
check("and Running once one works", (llm.state, llm.refused), ("healthy", False))
failed("talk", status(401)); asyncio.run(sup.start("llm"))
check("a restart clears it", failure(), "")
failed("talk", status(401))
asyncio.run(sup.reload(sup.cfg.model_copy(update={"llm": sup.cfg.llm.model_copy(update={"model": "n"})})))
check("so does saving the brain's settings", failure(), "")
check("its Log is brain.log", ExternalDriver("llm", ext).tail(5), log_tail(5))
check("another external engine has none", ExternalDriver("tts", ext).tail(5), [])

# A server on your network that is down, with an old note: it says it is down, not the note.
near = EngineConfig(driver="external", port=9, health="/v1/models", startup_timeout=0)
down = Managed("llm", near, ExternalDriver("llm", near))
sup.items = {"llm": down}
failed("talk", status(401)); asyncio.run(sup.refresh())
check("a brain that is down says so, not the old note", (down.state, down.up), ("unhealthy", False))

# A key refused while the engines start (the warm-up, as the status stream refreshes) keeps no channel stopped.
llm = Managed("llm", ext, ExternalDriver("llm", ext))
slow = Managed("slow", ext, ExternalDriver("slow", ext))
voice = Managed("voice", ext, ExternalDriver("voice", ext), kind="channel")
sup.items = {"llm": llm, "slow": slow, "voice": voice}
started, start = [], sup.start


async def starting(name, wait=True):
    started.append(name)
    if name == "slow":                  # loads after the brain is up and has been refused
        await asyncio.sleep(0.2)
        failed("warm-up", status(401)); await sup.refresh()
        return slow
    return voice if name == "voice" else await start(name, wait)
sup.start = starting
asyncio.run(sup.start_all())
check("refused during startup", llm.state, "error")
check("the channels still start", "voice" in started, True)
sup.start = start

# Conversations report: WinstonLLM's request is the parent's, made to fail or answer here.
from pipecat.processors.aggregators.llm_context import LLMContext  # noqa: E402
from pipecat.services.openai.llm import OpenAILLMService  # noqa: E402

from winston.core.brain import Brain, build_llm  # noqa: E402


def cfg_at(port: int, **llm) -> WinstonConfig:
    return WinstonConfig.model_validate({"llm": {"base_url": f"http://127.0.0.1:{port}/v1", "api_key_env": None,
                                                 "model": "fake", **llm}})


def parent(outcome):
    async def get_chat_completions(self, context):
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome
    OpenAILLMService.get_chat_completions = get_chat_completions


def ask():
    """What WinstonLLM's request returned, or the exception it raised."""
    llm = build_llm(cfg_at(9), channel="text")
    try:
        return asyncio.run(llm.get_chat_completions(LLMContext([{"role": "user", "content": "hi"}])))
    except BaseException as e:      # noqa: BLE001  (CancelledError too)
        return e


clear()
original = OpenAILLMService.get_chat_completions
parent(status(401)); ask()
check("a conversation's failure is noted, with its channel",
      (failure(), log_tail(1)[0].endswith("  text  Key refused (401)")), ("Key refused (401)", True))
parent(asyncio.CancelledError())
check("talked over: cancelled, not a failure", (isinstance(ask(), asyncio.CancelledError),
                                                 log_tail(1)[0].endswith("  text  Key refused (401)")), (True, True))
parent("stream")
check("one that works clears it", (ask(), failure()), ("stream", ""))
OpenAILLMService.get_chat_completions = original

# The warm-up, against a brain that refuses the key and one that answers.
import threading  # noqa: E402
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer  # noqa: E402

import fake_openai  # noqa: E402
from pipecat.adapters.schemas.function_schema import FunctionSchema  # noqa: E402
from pipecat.adapters.schemas.tools_schema import ToolsSchema  # noqa: E402
from harness import free_port  # noqa: E402


class Refusing(BaseHTTPRequestHandler):
    def do_POST(self):
        body = b'{"error": {"message": "Missing Authentication header", "code": 401}}'
        self.send_response(401)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def serve(handler) -> int:
    port = free_port()
    threading.Thread(target=ThreadingHTTPServer(("127.0.0.1", port), handler).serve_forever, daemon=True).start()
    return port


def warm(port: int):
    """A Brain warmed as after start(), which always gives it tools (none fails Pipecat's own check)."""
    b = Brain(cfg_at(port))
    b.tools = ToolsSchema(standard_tools=[FunctionSchema(name="noop", description="Nothing.", properties={},
                                                         required=[])])
    asyncio.run(b.warm())


clear()
refusing, answering = serve(Refusing), serve(fake_openai.Handler)
warm(refusing)
check("a warm-up refused is a failure", failure(), "Key refused (401)")
warm(answering)
check("one that answers clears it", failure(), "")

# The Check button's request: one tiny question with the saved key and model.
from winston.core.brain import check_brain  # noqa: E402

clear()
r = asyncio.run(check_brain(cfg_at(refusing)))
check("a check with a refused key", (r.ok, r.text, r.ms), (False, "Key refused (401)", None))
check("is noted like a real call", failure(), "Key refused (401)")
r = asyncio.run(check_brain(cfg_at(answering)))            # credit topped up, or the key fixed
check("a check that answers", (r.ok, r.text.startswith("Works, answered in "), isinstance(r.ms, int)),
      (True, True, True))
check("clears the note", failure(), "")
check("and is in the Log", log_tail(1)[0].endswith(f"  check  works, answered in {r.ms} ms"), True)
r = asyncio.run(check_brain(cfg_at(answering, api_key_env="NOPE_KEY")))
check("no key saved", (r.ok, r.text), (False, "NOPE_KEY is not set (data/.env)"))

finish_checks("brain health: failures in words, the note as the Brain's status, brain.log as its Log, cleared by a "
              "call that works, a restart or a save; conversations and the warm-up report; the check")
