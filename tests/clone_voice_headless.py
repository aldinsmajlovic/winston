"""Clone a voice by asking, headless: the real voice pipeline (mic tap, STT, brain with its tools,
TTS) with a scripted microphone instead of a browser. Needs the LLM (:8000) and the TTS server (:8880).

    .venv/bin/python tests/clone_voice_headless.py [--keep] [--save-dir DIR]

The user asks in one VCTK voice to clone a friend; the "friend", another VCTK voice, says the
consent line and then talks, as audio only (no VAD frames, as a real mic under the tap sends
none); then the user asks for Winston's own voice back. Passes when the clone and its consent
are stored with a same-voice score at or above the threshold, Winston's next sentence starts within 2 s of the recording ending, and use_voice
reverts, with no turn taken from the friend's words after the recording stopped (the passage runs
past the 15 s the recording keeps, as a real friend mid-sentence does). The test clone is deleted afterwards unless --keep. The real clones are copied aside first
and put back after, so a misheard name can never cost one.
"""
import asyncio
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
from loguru import logger
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.workers.runner import WorkerRunner

sys.path.insert(0, str(Path(__file__).parent))
from m2_headless_roundtrip import Heard, Mic, Speaker, synth_user_audio, write_wav
from winston.channels import voice as vp
from winston.channels.clone import CONSENT_LINE, MicTap, make_voice_tools
from winston.config import resolve
from winston.core import speaker as same_voice, voices
from winston.core.brain import Brain, ToolCallLogObserver

USER_VOICE, FRIEND_VOICE, FRIEND = "vctk/p226_023", "vctk/p243_023", "Zelda"
PASSAGE = ("Well, today started slowly. I made coffee, read the news for a while and then walked down to the "
           "harbour, where the wind was cold but the light was lovely. After lunch I fixed the bike, which took "
           "far longer than it should have, and tonight I am cooking pasta for a few friends. Oh, and I still need to "
           "buy bread on the way home, the good sourdough from the corner bakery, if they have any left.")


async def wait_for(cond, timeout: float, what: str):
    t0 = time.perf_counter()
    while not cond():
        if time.perf_counter() - t0 > timeout:
            raise TimeoutError(what)
        await asyncio.sleep(0.05)


def tool_calls(since: float) -> list[dict]:
    lines = resolve(vp.CFG.logging.tool_calls).read_text().splitlines()
    return [r for r in map(json.loads, lines) if r["ts"] >= since and r.get("channel") == "clone-test"]


async def main(keep: bool, save_dir: Path | None) -> int:
    backup = Path(tempfile.mkdtemp(prefix="winston-clones-"))
    friends = voices.friends_dir()
    if friends.is_dir():
        shutil.copytree(friends, backup, dirs_exist_ok=True)
    try:
        return await run(keep, save_dir)
    finally:
        mine = {c.slug for c in voices.clones()} if keep else set()
        for f in list(friends.glob("*")) if friends.is_dir() else []:
            if not (backup / f.name).exists() and f.name.split(".")[0] not in mine:
                f.unlink()
        for f in backup.glob("*"):
            shutil.copy2(f, friends / f.name)
        shutil.rmtree(backup)


async def run(keep: bool, save_dir: Path | None) -> int:
    me = vp.CFG.assistant.name
    ask, _ = synth_user_audio(f"{me}, clone my friend {FRIEND}'s voice.", USER_VOICE)
    consent, _ = synth_user_audio(CONSENT_LINE.format(name=FRIEND, assistant=me), FRIEND_VOICE)
    talk, _ = synth_user_audio(PASSAGE, FRIEND_VOICE)
    back, _ = synth_user_audio("Thanks. Now go back to your own voice, please.", USER_VOICE)

    brain = Brain(vp.CFG, channel="voice"); await brain.start()
    stt, tts, tap, mic, speaker = vp.build_stt(), vp.build_tts(), MicTap(), Mic(), Speaker()

    for schema, handler in make_voice_tools(vp.CFG, tap, vp.transcribe):
        brain.add_tool(schema, handler)
    agg = LLMContextAggregatorPair(brain.new_context())
    heard = Heard()
    pipeline = Pipeline([mic, tap, stt, heard, agg.user(), brain.llm, tts, speaker, agg.assistant()])
    worker = PipelineWorker(pipeline, params=PipelineParams(), idle_timeout_secs=None,
                            observers=[ToolCallLogObserver(vp.CFG, channel="clone-test")])
    run = asyncio.create_task(WorkerRunner(handle_sigint=False).run(worker))
    t_start, ok = time.time(), True
    try:
        await asyncio.sleep(1)
        mic.say(ask, turn=True)
        await wait_for(lambda: tap._rec is not None, 30, "the consent recording never started")
        logger.info("test: friend says the consent line")
        mic.say(consent, turn=False)
        await wait_for(lambda: tap._rec is None, 20, "the consent recording never ended")
        await wait_for(lambda: tap._rec is not None, 30, "the talking recording never started")
        logger.info("test: friend talks")
        mic.say(talk, turn=False)
        await wait_for(lambda: tap._rec is None, 60, "the talking recording never ended")
        mark, speaker.first_audio = time.perf_counter(), None
        await wait_for(lambda: speaker.first_audio is not None, 10, "no speech after the recording")
        first = (speaker.first_audio - mark) * 1000
        named = next((c["args"]["name"] for c in tool_calls(t_start) if c["tool"] == "clone_voice"), FRIEND)
        clone = voices.find(named)                  # the name as the STT heard it
        print(f"\nclone stored: {clone}\nfirst audio after the recording ended: {first:.0f} ms")
        ok &= clone is not None and (voices.friends_dir() / f"{clone.slug}.consent.wav").is_file() and first < 2000
        # the friend's consent and passage are one voice, so the same-voice check must have run and passed
        ok &= clone is not None and same_voice.same_voice(clone.consent_score) is True
        await wait_for(lambda: not speaker.speaking and not tap.muted, 30, "Winston kept talking")
        await asyncio.sleep(1)
        leaked = [t for at, t in heard.texts if at > mark]
        print(f"turns taken from the friend after the recording: {leaked or 'none'}")
        ok &= not leaked
        mic.say(back, turn=True)
        await wait_for(lambda: any(c["tool"] == "use_voice" and c["event"] == "result" for c in tool_calls(t_start)),
                       30, "use_voice was not called")
        await asyncio.sleep(1)
        await wait_for(lambda: not speaker.speaking, 30, "Winston kept talking")
    except TimeoutError as e:
        logger.error(f"test: {e}"); ok = False
    finally:
        await worker.cancel(); await run; await brain.close()
    calls = tool_calls(t_start)
    for c in calls:
        print(f"  {c['event']:6} {c['tool']} {c.get('args') or c.get('result')}")
    reverted = any(c["tool"] == "use_voice" and c["event"] == "call" and "own" in json.dumps(c["args"]).lower()
                   for c in calls)
    ok &= reverted
    if save_dir:
        save_dir.mkdir(parents=True, exist_ok=True)
        write_wav(save_dir / "winston.wav", np.frombuffer(bytes(speaker.audio), np.int16))
        print(f"saved Winston's side to {save_dir}/winston.wav")
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    argv = sys.argv[1:]
    save = Path(argv[argv.index("--save-dir") + 1]) if "--save-dir" in argv else None
    sys.exit(asyncio.run(main("--keep" in argv, save)))
