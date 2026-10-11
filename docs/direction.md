# Direction

Written 2026-09-24 after Aldin found [pipecat-homeassistant](https://github.com/kyvaith/pipecat-homeassistant)
and asked whether Winston still has a point. Research across competitors, user complaints and what runs
locally on one 3090. Not yet applied to the plan; that rework comes next.

## The finding

Features are not the edge. The voice pipeline (STT, LLM, TTS, barge-in, satellites) is being built by
everyone at once:

- **Home Assistant itself.** Draft PR "Add support for Barge In to the Assist pipeline"
  ([core#182776](https://github.com/home-assistant/core/pull/182776), opened 2026-09-20) for Gemini Live
  and OpenAI Realtime on ESPHome satellites. Realtime speech-to-speech is an open design goal
  ([discussion #2623](https://github.com/orgs/home-assistant/discussions/2623)).
- **pipecat-homeassistant.** HA add-on, Gemini Live by default, composed pipelines with Ollama or any
  OpenAI-compatible endpoint (so local is a config away), barge-in on its Lovelace card and on its own
  ESPHome component `va_pipecat`. Session memory, no vision input. 53 stars, first release 2026-07-06.
- **Smaller realtime projects**:
  [voice-pe-realtime](https://github.com/jayzuccarelli/voice-pe-realtime) (cloud, custom Voice PE firmware),
  [LiveKit demos](https://github.com/Rishi8078/Livekit-Homeassistant),
  [TheBToby/voice-assistant](https://github.com/TheBToby/voice-assistant).

Whatever Winston does at the pipeline level gets matched within months, usually on cloud models.
Local barge-in is an edge today with a short shelf life.

What nobody has, local or not, is the layer above the pipeline: a brain that knows the house over time,
acts carefully, checks its work and speaks up with judgment. Assist runs one request at a time and
forgets. Gemini for Home does some of it, in the cloud, behind its Premium Advanced plan
([Google](https://support.google.com/googlehome/answer/15542305)).

## What would give the edge, in order

1. **Alexa basics, done properly.** Alarms (52 votes,
   [discussion #559](https://github.com/orgs/home-assistant/discussions/559)), named timers (33,
   [#2334](https://github.com/orgs/home-assistant/discussions/2334)) and reminders are the top missing
   features and none are native. They are why people keep an Echo in the living room. Winston has the
   pieces: memory, announcements, Discord; a scheduler is missing.
2. **Speaking up with judgment.** Rules decide when, the LLM only words it; the right room, quiet hours,
   tiered actions. Announcements are already the delivery half; the deciding half is missing. Closest
   open project: [home-generative-agent](https://github.com/goruck/home-generative-agent) (rules plus
   LLM triage, push notifications, not voice).
3. **Cameras through Frigate, not our own vision model.** [Frigate 0.17](https://docs.frigate.video/configuration/genai/genai_config/)
   does detection, faces, plates and descriptions, and can point at Winston's LLM. Winston adds
   narration plus memory: "the courier again, third time this week". No extra VRAM. Needs a camera in
   HA first (none today, `decisions.md` 2026-09-22).
4. **Automations drafted by voice.** "Turn the hallway light off if nobody moved for ten minutes": he
   drafts it, shows the diff on the Talk page, applies on yes. Later, suggestions from recorder history
   ("you turn the heater on at 6 every weekday"). Nothing mature exists; it is the tiered-action rule
   applied to automations.
5. **Honest outcomes as a headline.** "It says the music is playing and nothing plays" is a recurring
   complaint ([forum](https://community.home-assistant.io/t/why-does-assistant-say-its-playing-music-when-it-doesnt/937533));
   an XDA writer turned the local LLM off for most commands because it was unpredictable
   ([XDA](https://www.xda-developers.com/gave-home-assistant-local-llm-voice-control-turned-off-most-what-say/)).
   Winston already checks state after acting (`core/verify.py`); extend it to media and say so loudly.
6. **Satellite reliability over novelty.** One answer per wake word across the house, a "stop" that works,
   no hearing itself in follow-ups (the [ganiushin fork](https://github.com/ganiushin/voice-pe-firmware)
   shows the cheap fix). Then use HA's barge-in firmware work once it lands instead of writing our own.
   `va_pipecat`'s protocol (JSON control, PCM16 16 kHz up, 24 kHz down, interrupt frames) is MIT and a
   reference if we do.

## Parked or dropped

- **Speech-native full-duplex models** (PersonaPlex, Moshi, Qwen3-Omni): no reliable tool calling, and
  they need the whole card. Look again in 6 to 12 months.
- **Our own vision pipeline or Moondream 3**: about 19 GB, does not fit beside the stack. Frigate covers it.
- **Speaker ID**: 60 votes ([#527](https://github.com/orgs/home-assistant/discussions/527)), but a
  single-person home for now.
- **Latency chasing**: 1.3 s is competitive for local; most sub-500 ms figures online are cloud stacks.
  Cheap wins only: [Smart Turn v3.1](https://www.daily.co/blog/improved-accuracy-in-smart-turn-v3-1/)
  (8 MB, about 12 ms on CPU) and a trial of Chatterbox Turbo.

## Open question

If the edge is the brain, should Winston also plug into HA as a conversation agent? Then any Voice PE or
Assist setup gets Winston's memory, proactivity and checking without taking on its audio stack: a far
bigger audience and no fight with HA over firmware. The browser and satellite pipeline stays for local
barge-in. Decide before building 3 and 4.

## If starting over

Asked the same day: what would a rebuild from scratch look like? Build the brain first; voice is
something it plugs into.

1. **A house-brain service, no audio in it.** One long-running process that subscribes to HA's event
   stream and keeps its own picture of the house over time, holds memory with time on it (when a fact
   was learned and when it stopped being true), runs a scheduler (alarms, timers, reminders), runs a
   rules engine that decides when something is worth saying, and checks outcomes after every action,
   with tiered confirmation and a log of every tool call. The LLM does language only: understanding and
   wording. Everything that must be reliable stays deterministic.
2. **An HA conversation agent from day one.** Voice PE, wake words, which satellite answers, streaming TTS
   and soon barge-in come from Assist, and HA improves them for free. Anyone with Assist tries Winston in
   five minutes: a HACS integration plus one container, pointed at any OpenAI-compatible model, local by
   default.
3. **Tests that replay HA history.** Recorded event sequences ("door opened, nobody home, 23:40") played
   against the brain. Proactivity and outcome checks cannot be tested on a fake mic, and hardware
   checks are slow.
4. **Our own voice pipeline only where HA cannot go.** The browser Talk page with full duplex, and
   satellites with barge-in if HA's stays cloud-only. Months in, not first.

**Skipped or deferred:** the engine supervisor and model-switching settings (one OpenAI-compatible URL
is enough), our own TTS server, voice cloning, wake word training, Discord, the dashboard, and settings
polish before there is much to set.

**Kept from today's Winston:** `verify.py` (checking outcomes), memory as Markdown in git, tiered
actions, announcements with follow-ups, measure before optimizing, the docs discipline. Most of
it lifts across rather than being rewritten.

**How it went so far:** Winston went wide early. M4 started before M2's real-mic check and before M3 had
hardware, against the rule that milestones are sequential, and most effort went into the layer that
turned out to be common ground (pipeline, engines, settings). None of it is wasted, but it is why one
pipecat repo could make the project feel pointless: the differentiating parts were not built yet.

**Order:** month one, the agent, the scheduler and outcome checks, used daily through a stock Voice PE.
Month two, speaking up with judgment. Then Frigate narration and voice-drafted automations. No teardown
needed: the plan rework can make brain-as-agent the first milestone of a new phase and freeze the
pipeline work where it is.

## Top user complaints, for reference

Ranked by GitHub votes on [home-assistant-voice-pe](https://github.com/esphome/home-assistant-voice-pe/issues)
and HA feature requests; Reddit could not be read.

1. Wake word misses up close and false triggers from the TV (openWakeWord request #334, 67 votes).
2. Cannot pick the speaker out of background voices (#541; a maintainer says it needs new mic hardware).
3. Several satellites answer one wake word.
4. No alarms, named timers or reminders.
5. No speaker recognition.
6. Cannot interrupt; long replies after false triggers (#373, #560).
7. Follow-ups lose context.
8. The LLM is slow, unpredictable, or claims success that did not happen.
9. Music by voice is unreliable; the Voice PE speaker is quiet.
10. No intercom or drop-in ([#2222](https://github.com/orgs/home-assistant/discussions/2222), 22 votes).
