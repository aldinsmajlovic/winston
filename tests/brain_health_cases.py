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
    return openai.APIStatusError("Error code: %d - {'message': 'Incorrect API key provided: sk-or-...abcd'}" % code,
                                 response=httpx.Response(code, request=REQ), body=None)


check("401", why_failed(status(401)), "Key refused (401)")
check("403", why_failed(status(403)), "Key refused (403)")
check("402", why_failed(status(402)), "Out of credit (402)")
check("404", why_failed(status(404)), "No such model, or a wrong API address (404)")
check("429", why_failed(status(429)), "Too many requests (429)")
check("other codes say the provider's words", why_failed(status(500)).startswith("Refused (500): "), True)
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
worked("talk"); asyncio.run(sup.refresh())
check("and Running once one works", llm.state, "healthy")
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
check("a brain that is down says so, not the old note", down.state, "unhealthy")

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
