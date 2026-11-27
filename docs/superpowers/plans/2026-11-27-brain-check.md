# Brain Check Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Check button that says whether the brain answers and why not, and real brain failures that turn
the Brain's status red with the reason and show up in its Log.

**Architecture:** A small leaf module, `engines/brain_health.py`, turns a failed call into words and keeps
the outcome in two files, the way `config/memory.py` keeps the out-of-memory note: a note in
`data/state/` that the supervisor shows as the status, and lines in `data/logs/brain.log` that an
external brain's Log shows. `WinstonLLM`, `Brain.warm()` and a new check endpoint report to it from
whatever process they run in.

**Tech Stack:** Python 3.12 with uv, Pipecat (`OpenAILLMService`), openai and anthropic clients, FastAPI;
React + TanStack Query (`$api`) in `web/`.

**Spec:** `docs/superpowers/specs/2026-11-27-brain-check-design.md`

## Global Constraints

- No em dashes anywhere (`just lint` checks).
- Commits: `git commit -s`, message ending with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Checks are `tests/*_cases.py` scripts using `harness.check(name, got, want)` and `finish_checks(line)`;
  `uv run winston check unit` runs every one. Call `scratch_data(prefix)` before importing winston.
- No key value in any log line, status, note or response.
- A check is one request: the message `.`, `max_tokens: 1`, no system prompt, no tools.
- Docs per `docs/writing-docs.md`; status and results go in `docs/plan.md`, never the README.

## Review Focus

1. He is talked over mid-request (the call is cancelled): not a failure, status unchanged. Test in Task 2.
2. A 401 or 403 whose provider message echoes a masked key: the words are fixed, the provider text is
   left out. Test in Task 1 (exact strings).
3. Credit topped up while the status is red: the next real call or a Check turns it back. Test in Task 3.
4. A local brain whose server is down, with an old failure note: shows its usual Not responding, not the
   note. Test in Task 1.
5. A fresh install with no `data/state/` or `data/logs/`: the first failure still writes. Test in Task 1
   (the scratch data folder has neither).

---

### Task 1: brain_health, the status and the Log

**Files:**
- Create: `src/winston/engines/brain_health.py`
- Modify: `src/winston/engines/supervisor.py` (`refresh()` near line 283, `start()` near 303, `reload()` near 208)
- Modify: `src/winston/engines/drivers.py` (`ExternalDriver.tail`, line 163)
- Test: `tests/brain_health_cases.py`

**Interfaces:**
- Produces, all in `winston.engines.brain_health`, importing nothing heavier than httpx at module level
  (openai, anthropic and `core.brain.unreachable` imported inside functions, as `memory.out_of_memory` does):
  - `why_failed(e: BaseException) -> str`
  - `failed(channel: str, e: BaseException) -> str`: writes the note, appends a line, returns the words.
  - `worked(channel: str, say: str = "") -> None`: deletes the note; appends `say`, or "works again" when a
    note was there; appends nothing when neither.
  - `failure() -> str`: the note's text, or "".
  - `clear() -> None`: deletes the note, no line.
  - `log_tail(n: int) -> list[str]`: the last n lines of the log, [] when there is none.
- Files: note `data_dir() / "state" / "brain_failure.txt"`, log `data_dir() / "logs" / "brain.log"`.
- Line format: `f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {channel}  {text}"`.

- [ ] **Step 1: Write the failing checks** in `tests/brain_health_cases.py`

Build errors with `REQ = httpx.Request("POST", "http://x")` and
`openai.APIStatusError("x", response=httpx.Response(code, request=REQ), body=None)`.

```python
check("401", why_failed(status(401)), "Key refused (401)")
check("403", why_failed(status(403)), "Key refused (403)")
check("402", why_failed(status(402)), "Out of credit (402)")
check("404", why_failed(status(404)), "No such model, or a wrong API address (404)")
check("429", why_failed(status(429)), "Too many requests (429)")
check("other codes say the provider's words", why_failed(status(500)).startswith("Refused (500): "), True)
check("Claude's errors too", why_failed(anthropic.APIStatusError("x", response=httpx.Response(401, request=REQ), body=None)),
      "Key refused (401)")
check("no connection", why_failed(openai.APIConnectionError(request=REQ)), "Not reachable")
check("no key", why_failed(RuntimeError("LLM_API_KEY is not set (data/.env)")), "LLM_API_KEY is not set (data/.env)")

check("fresh data: no state or logs folder yet", (data / "state").exists(), False)
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
```

Then the supervisor and driver, set up as `tests/engines_cases.py` does (`Supervisor.__new__`, `sup.cfg`,
`sup.items` with `Managed("llm", ext, ExternalDriver("llm", ext))`, `ext` an external `EngineConfig` with
`health=""`):

```python
failed("talk", status(402)); asyncio.run(sup.refresh())
check("a hosted brain with a failure is Failed with it", (llm.state, llm.detail), ("error", "Out of credit (402)"))
worked("talk"); asyncio.run(sup.refresh())
check("and Running once one works", llm.state, "healthy")
failed("talk", status(401)); asyncio.run(sup.start("llm"))
check("a restart clears it", failure(), "")
failed("talk", status(401)); asyncio.run(sup.reload(<cfg with llm.model changed>))
check("so does saving the brain's settings", failure(), "")
check("its Log is brain.log", ExternalDriver("llm", ext).tail(5), log_tail(5))
check("another external engine has none", ExternalDriver("tts", ext).tail(5), [])
# Review Focus 4: a server on your network, down, with health "/v1/models" on port 9
failed("talk", status(401)); asyncio.run(sup.refresh())
check("a brain that is down says so, not the old note", down.state, "unhealthy")
```

- [ ] **Step 2: Run it to see it fail**

Run: `uv run python tests/brain_health_cases.py`
Expected: `ModuleNotFoundError: No module named 'winston.engines.brain_health'`

- [ ] **Step 3: Write `brain_health.py`**

`why_failed`: `status_code` read with `getattr` (both clients have it); 401 and 403 Key refused, 402 Out of
credit, 404 No such model, or a wrong API address, 429 Too many requests, each with ` (code)` and never
the provider's text; any other code `f"Refused ({code}): {str(e)[:200]}"`; `unreachable(e)` Not reachable;
anything else `str(e)[:200] or type(e).__name__`. `failed` and `worked` make the folders they write in.

- [ ] **Step 4: Wire the supervisor and driver**

`refresh()`: after the out-of-memory branch, `elif m.name == "llm" and (why := brain_health.failure()) and
await self.healthy(m): self._set(m, "error", why)`. `start("llm")` and `reload()` (when `cfg.llm !=
self.cfg.llm`) call `brain_health.clear()` beside their `memory.answered()`. `ExternalDriver.tail(n)`
returns `brain_health.log_tail(n)` when `self.name == "llm"`, else `[]`. Leave `log_path()` returning
None: `start()` treats a driver with a log path as a process that must survive its grace time.

- [ ] **Step 5: Run it to see it pass**

Run: `uv run python tests/brain_health_cases.py && uv run python tests/engines_cases.py`
Expected: both print their ok line.

- [ ] **Step 6: Commit**

```bash
git add src/winston/engines/brain_health.py src/winston/engines/supervisor.py src/winston/engines/drivers.py tests/brain_health_cases.py
git commit -s -m "Brain: a failed brain call is its status and its Log"
```

### Task 2: Conversations and the warm-up report

**Files:**
- Modify: `src/winston/core/brain.py` (`WinstonLLM.__init__` line 430, `get_chat_completions` line 529,
  `build_llm` line 605, `Brain.warm` line 747)
- Test: `tests/brain_health_cases.py` (append)

**Interfaces:**
- Consumes: `brain_health.failed`, `brain_health.worked` (Task 1).
- Produces: `WinstonLLM(..., channel: str = "?")`; `build_llm` passes its `channel`.

- [ ] **Step 1: Append the failing checks**

Replace `OpenAILLMService.get_chat_completions` with an async function that raises or returns what the
check sets, then call `WinstonLLM.get_chat_completions` on `build_llm(cfg, channel="text")` directly with
`LLMContext([{"role": "user", "content": "hi"}])`. `cfg` as `tests/anthropic_cases.py` builds one, with
`llm.base_url` a free port, `api_key_env: None`, `model: "fake"`.

```python
parent_raises(status(401))
check("a conversation's failure is noted, with its channel", (failure(), log_tail(1)[0].endswith("  text  Key refused (401)")),
      ("Key refused (401)", True))
parent_raises(asyncio.CancelledError())
check("talked over: cancelled, not a failure", (raised(asyncio.CancelledError), log_tail(1)[0].endswith("Key refused (401)")),
      (True, True))
parent_returns("stream")
check("one that works clears it", (got, failure()), ("stream", ""))
```

Then the warm-up against two servers on free ports in threads: `tests/fake_openai.Handler`, and a handler
whose `do_POST` answers 401 with `{"error": {"message": "Missing Authentication header", "code": 401}}`:

```python
asyncio.run(Brain(cfg_at(refusing)).warm())
check("a warm-up refused is a failure", failure(), "Key refused (401)")
asyncio.run(Brain(cfg_at(answering)).warm())
check("one that answers clears it", failure(), "")
```

- [ ] **Step 2: Run it to see it fail**

Run: `uv run python tests/brain_health_cases.py`
Expected: FAIL on "a conversation's failure is noted" (`TypeError` for `channel`, or the note still "").

- [ ] **Step 3: Report in `get_chat_completions` and `warm`**

Wrap the existing body after the shortcut returns (the `try` around `super().get_chat_completions`):
`except Exception as e: brain_health.failed(self._channel, e); raise`, and `brain_health.worked(self._channel)`
once it returns. `except Exception` leaves `CancelledError` alone. Shortcut and quiet returns (`_nothing()`,
`_calling(...)`) made no request and report nothing. In `warm`, `worked(self.channel)` after
`run_inference` returns, `failed(self.channel, e)` in its `except`, keeping its warning.

- [ ] **Step 4: Run it to see it pass**

Run: `uv run python tests/brain_health_cases.py && uv run winston check unit`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/winston/core/brain.py tests/brain_health_cases.py
git commit -s -m "Brain: conversations and the warm-up report whether the brain answered"
```

### Task 3: The check, as a function and an endpoint

**Files:**
- Modify: `src/winston/core/brain.py` (beside `chat_client`, line 210)
- Modify: `src/winston/api/options.py` (`BrainCheck` beside `HomeAssistantCheck`, line 71)
- Modify: `src/winston/api/app.py` (the route beside the other `/api/engines/{name}` routes, line 1521;
  `secret_changed`, line 1688)
- Modify: `web/src/api/schema.d.ts` (generated)
- Test: `tests/brain_health_cases.py` (append)

**Interfaces:**
- Consumes: `chat_client(cfg, timeout)`, `served_model(cfg)`, `brain_health.failed/worked/clear`.
- Produces: `async def check_brain(cfg: WinstonConfig) -> BrainCheck` in `core/brain.py`;
  `class BrainCheck(BaseModel): ok: bool; text: str; ms: int | None = None` in `api/options.py`;
  `POST /api/engines/llm/check` returning `BrainCheck`.

- [ ] **Step 1: Append the failing checks**

```python
r = asyncio.run(check_brain(cfg_at(refusing)))
check("a check with a refused key", (r.ok, r.text, r.ms), (False, "Key refused (401)", None))
check("is noted like a real call", failure(), "Key refused (401)")
r = asyncio.run(check_brain(cfg_at(answering)))            # Review Focus 3: credit topped up
check("a check that answers", (r.ok, r.text.startswith("Works, answered in "), isinstance(r.ms, int)), (True, True, True))
check("clears the note", failure(), "")
check("and is in the Log", log_tail(1)[0].endswith(f"  check  works, answered in {r.ms} ms"), True)
r = asyncio.run(check_brain(cfg_at(answering, api_key_env="NOPE_KEY")))
check("no key saved", (r.ok, r.text), (False, "NOPE_KEY is not set (data/.env)"))
```

- [ ] **Step 2: Run it to see it fail**

Run: `uv run python tests/brain_health_cases.py`
Expected: `ImportError: cannot import name 'check_brain'`

- [ ] **Step 3: Write `check_brain`, the route and the clear on a key save**

`check_brain` imports `BrainCheck` inside the function, as `build_llm` imports `served_model` (core must not
import the API at module level). Inside one `try`, `chat_client(cfg, timeout=20)` then
`chat.completions.create(model=served_model(cfg), messages=[{"role": "user", "content": "."}], max_tokens=1)`;
on success `worked("check", f"works, answered in {ms} ms")` and
`BrainCheck(ok=True, text=f"Works, answered in {ms} ms", ms=ms)`; on `Exception`
`BrainCheck(ok=False, text=failed("check", e))`. The route: `summary="Ask the brain one tiny question with
the saved key and model: whether it answers, and why not"`, calling `check_brain(app.state.cfg)`. In
`secret_changed`, `brain_health.clear()` when `name == app.state.cfg.llm.api_key_env`.

- [ ] **Step 4: Regenerate the client**

Run: `cd web && pnpm api && grep -c "/api/engines/llm/check" src/api/schema.d.ts`
Expected: `1` or more.

- [ ] **Step 5: Run the checks**

Run: `uv run python tests/brain_health_cases.py && uv run winston check unit`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/winston/core/brain.py src/winston/api/options.py src/winston/api/app.py web/src/api/schema.d.ts tests/brain_health_cases.py
git commit -s -m "Brain: a check that asks the brain one tiny question"
```

### Task 4: The Check button and the docs

**Files:**
- Modify: `web/src/components/slot-card.tsx` (`HowItRuns`, line 287)
- Modify: `docs/web-ui.md` (the Brain page), `docs/running.md` (status and logs), `docs/decisions.md`,
  `docs/plan.md`
- Maybe: `docs/images/brain-light.png`, `docs/images/brain-dark.png`

**Interfaces:**
- Consumes: `POST /api/engines/llm/check` returning `{ok, text, ms}` (Task 3).

- [ ] **Step 1: The button**

In `HowItRuns`, `const check = $api.useMutation('post', '/api/engines/llm/check')`. For `kind === 'llm'`,
`stateLine` gets a first action `{ label: check.isPending ? 'Checking' : 'Check', onClick: () => check.mutate({}) }`,
and under the state line, wherever it is drawn, `check.data` as
`<StatusLine tone={check.data.ok ? 'ok' : 'error'}>{check.data.text}</StatusLine>`. Words and spacing per
`docs/ui-guide.md`.

- [ ] **Step 2: Build and smoke**

Run: `cd web && pnpm build && pnpm smoke`
Expected: both pass.

- [ ] **Step 3: Docs**

`web-ui.md`: the Brain card's Check and what each answer means; a failed call turns the status Failed with
the reason until one works, a restart or a save. `running.md`: a hosted brain's Log is `logs/brain.log`.
`decisions.md`, one line: `2026-11-27: Brain failures are a note in data/state and lines in logs/brain.log,
as the out-of-memory note, not a live-feed event. Why: every process that calls the brain can write it, and
it survives a winstond restart.` `plan.md`: the entry with what the checks cover, "not yet on hardware".
Retake the Brain shots, light and dark at 1400x860, only if the page as shot looks different.

- [ ] **Step 4: Lint and commit**

Run: `just lint`
Expected: ruff says "All checks passed!" and the em dash grep prints nothing.

```bash
git add web/src/components/slot-card.tsx docs/
git commit -s -m "Brain: a Check button, and the docs for brain failures"
```

### Task 5: Acceptance on hardware

The running Winston is the installed release (`~/.local/share/winston-app`, the `winston` user service),
not this checkout.

- [ ] **Step 1:** `systemctl --user stop winston`, then `uv run winston up` from the checkout.
- [ ] **Step 2:** Brain page, LLM_API_KEY set to a made-up value: Check says Key refused (401); the status is
  Failed: Key refused (401); Log shows the line.
- [ ] **Step 3:** Model set to `deepseek/no-such-model`: Check says No such model, or a wrong API address (404).
- [ ] **Step 4:** The real OpenRouter key and `deepseek/deepseek-v4.1-flash-1017`: Check says Works with a
  time; status Running.
- [ ] **Step 5:** A made-up key again, then a line typed on the Talk page, without pressing Check: the status
  turns Failed.
- [ ] **Step 6:** The real key back; stop the checkout's winstond, `systemctl --user start winston`.
- [ ] **Step 7:** Record the results and times in `docs/plan.md`'s entry, commit with `-s`.
