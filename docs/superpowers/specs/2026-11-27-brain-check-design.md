# Brain check: is the brain answering, and why not

Short design, 2026-11-27. Approach picked by Aldin: the Check button plus real failures (option 2).

## Why

With a hosted brain (OpenRouter) and a wrong key pasted, the Brain card said Running, talking did nothing,
and the Brain's Log was empty. The cause, a 401 from OpenRouter, was only in winstond's journal. You want
to know whether the brain works without having to talk to him, and to see why when it does not, including
when it breaks later (credit runs out mid-week).

## How it works today

- An external engine with no health path (every hosted brain) counts as healthy whenever winstond runs
  (`ExternalDriver.running()` is always true). Nothing ever asks the provider.
- An external engine's Log is empty (`ExternalDriver.tail()` returns nothing): there is no process.
- Every conversation's brain call goes through `WinstonLLM.get_chat_completions` (core/brain.py), in
  winstond (Talk page, Assist) and in the channel processes (voice, satellite, Discord). Failures raise into
  Pipecat and land in that process's log.
- The warm-up (`warm()`, a one-token request) runs when the brain comes up and after a save; its failure is
  a warning in the log.

## Goals

- A **Check** button on the Brain card: one tiny request to the saved model with the saved key, answered in
  words: Works (with how long it took), Key refused, Out of credit, No such model, Too many requests, Not
  reachable, or the provider's own message.
- A brain call that fails for real (a conversation or a warm-up) turns the Brain's status to Not working
  with that reason, until a call works again, the brain restarts, or its settings or key are saved.
- The Brain's Log shows, for a brain with no log of its own, the recent outcomes: each check, each failure
  (time, where: Talk, voice, a room, Discord; and why) and the first call that worked after one.

## Non-goals

- A brain Winston runs here keeps its own process log; failures still turn its status red, but are not
  added to that log.
- Background calls (diary, extensions' asks through `chat_client`) are not reported.
- No checking on a timer: each check is a billed request, so checks happen on the button, the warm-up and
  real calls only.
- OpenRouter's own key endpoint (credit left) is not used: it is OpenRouter only.

## Anti-goals

- A key in any log line, status or response.
- A check that costs more than a few tokens, or one that runs without you or a real call asking.
- Status flapping: one failure turns it red, one success turns it back, nothing in between.
- A cancelled call (he was talked over) counted as a failure.

## Approaches

1. **Check button only.** Small, but says nothing when a key or credit fails later. Not picked.
2. **Check button plus real failures** (picked). Catches what breaks later, at the cost of the channel
   processes reporting outcomes to winstond.
3. **A health check that asks the provider every few seconds.** Rejected: a billed request each time, and
   OpenRouter's model list answers without a key, so a free probe cannot test the key.

## Design

- `why_failed(e) -> str` in engines/brain_health.py turns an exception into the words above (401 and 403 Key
  refused, 402 Out of credit, 404 No such model, 429 Too many requests, unreachable Not reachable, else the
  provider's message, shortened). Works for both the OpenAI and the Anthropic client.
- Outcomes cross processes the way the out-of-memory note already does (config/memory.py): a failure writes
  `data/state/brain_failure.txt` and appends a line to `data/logs/brain.log`; the first call that works
  after one deletes the note and appends a line. Every process that calls the brain can do this, so no new
  HTTP path. (Changed from the first draft, which sent a new live-feed event: the file is what the code
  already does for the same kind of status, and it survives a winstond restart.)
- `WinstonLLM.get_chat_completions` and `Brain.warm()` report each call's outcome that way.
- `refresh()` shows a healthy llm with a failure note as Failed with the note as its detail, after the
  out-of-memory note. A brain restart, a save of llm settings, or a save of its key variable deletes it.
- `ExternalDriver.tail()` for the llm returns the end of `brain.log`, so the existing Log button shows it.
- `POST /api/engines/llm/check` sends `.` with `max_tokens: 1`, no system prompt and no tools, through
  `chat_client(cfg)`, records the outcome like a real call, and returns `{ok, text, ms}`.
- The Brain card gets a Check button beside Log and Restart, with the answer under it as a `StatusLine`
  (as the Home Assistant connection check does).

## Acceptance (on hardware)

- OpenRouter with a made-up key: Check says Key refused; the status turns red with it; the Log has the line.
- The real key saved: Check says Works with a time; the status is Running again.
- A model name that does not exist: No such model.
- Talking on the Talk page with a bad key turns the status red without pressing Check.
- A unit test in `winston check` for `why_failed` on each status code, and for one failure and one
  success through the supervisor.

## Left to the builder

- Exact wording and layout of the button and the answer, following docs/ui-guide.md.
- Docs: web-ui.md (Brain page), the topic page for engines and status, plan.md with the result;
  Brain page screenshots retaken, light and dark.
