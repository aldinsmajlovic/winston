"""Brain health cases: a failed brain call in words, noted for the Brain's status and kept in its Log, and
cleared by one that works, a restart or a save. Nothing real is asked; the supervisor's brain is external.

    uv run python tests/brain_health_cases.py
"""
import asyncio

import anthropic
import httpx
import openai
from harness import check, finish_checks, scratch_data

data = scratch_data("winston-brain-health-")        # no state/ or logs/ yet: a fresh install

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

finish_checks("brain health: failures in words, the note as the Brain's status, brain.log as its Log, cleared by a "
              "call that works, a restart or a save")
