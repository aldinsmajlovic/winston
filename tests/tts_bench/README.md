# Voice benchmark

Which voice engine should replace or stay beside Chatterbox-Turbo: the most human-sounding one that
starts talking quickly and fits on the card next to the 27B brain. Machines measure speed, memory
and whether the words come out right; Aldin's ears decide what sounds human, blind.

| File | What it is |
| --- | --- |
| `sentences.txt` | 20 lines Winston really said, made generic, two with Turbo's tags ([chuckle], [sigh]) |
| `bench.py` | speaks every line in every reference clip through each server, measures it, writes `work/results.json` and the audio |
| `candidate.sh` | one candidate start to finish: start its server on a spare port, wait, run `bench.py`, stop it |
| `listen.py` | the blind listening page, `work/listen.html`: one file, audio inside, no network |
| `work/` | copied clips, audio, results, logs and the page (gitignored) |

The candidates live in `services/tts-qwen3/`, `tts-voxcpm2/` and `tts-gepard/` (one venv each, weights
already in the Hugging Face cache), and are Voice presets too; [docs/voices.md](../../docs/voices.md#benchmark) says why these.
Breeze TTS 2 (`services/tts-breeze2/`) is the `breeze2` Voice preset since 10 October; measure it as it runs
with `bench.py breeze2-live=http://127.0.0.1:8880`. Its bf16 builds need more of the card than that, so `breeze2` and `breeze2-fast` stop the brain, voice and hearing (`/api/engines/{llm,tts,stt}/stop`).
`breeze2-int4` needs only the voice stopped, but leaves about 4.3 GB free, under the script's 5 GB
guard; start its server by hand with candidate.sh's arguments and run `bench.py breeze2-int4=http://127.0.0.1:8898`.

| Name | Engine | Port | Where it runs |
| --- | --- | --- | --- |
| `turbo` | Chatterbox-Turbo, the live server | 8880 | GPU, running now |
| `pocket` | Kyutai Pocket TTS, `services/tts-pocket` | 8895 | CPU |
| `qwen3` | Qwen3-TTS 1.7B Base | 8891 | GPU |
| `voxcpm2` | VoxCPM2 | 8892 | GPU |
| `gepard` | Gepard 1.1 | 8893 | GPU |
| `gepard-1.0` | Gepard 1.0 (optional) | 8894 | GPU |
| `breeze2` | Breeze TTS 2, eager | 8896 | GPU, about 8.6 GB |
| `breeze2-fast` | Breeze TTS 2, decoding as CUDA graphs | 8897 | GPU, about 9.1 GB |
| `breeze2-int4` | the same, 4-bit backbone and text encoder | 8898 | GPU, about 3.8 GB, beside the brain |

## What is measured

For three reference clips (`librivox/clifton_fatherbrown`, the voice Winston uses now;
`vctk/p254_023`, the default; `vctk/p226_023`, the other one used most) and all 20 lines:

- time to first audio (request sent to first byte back), time to first sound (when a listener
  would hear it: leading silence played out, a late chunk waited for), total time, real-time factor
- word error rate: Parakeet on the CPU, as Winston hears, against the line (tags left out)
- first use of a clip: the first request in it, which includes cloning the clip (not for Turbo,
  whose live server has these clips prepared already)
- peak GPU memory of the server's process and of the whole card, sampled every 0.2 s by nvidia-smi

The clips are copied out of `data/voices` into `work/voices` (read only), with Parakeet's
transcript beside each for the engines that clone from the words. Aldin's own clone is not used.

## The session

About 30 minutes: 5 with everything running, 15 to 20 with the live speech engine stopped (the brain
stays up, Winston cannot speak meanwhile), then 10 of listening, which can happen after he speaks
again. Run from the repo root.

1. With everything running, the baseline and the CPU candidate (about 4 minutes). Results merge by
   name into `work/results.json`; delete it first for a clean session.

   ```sh
   uv run python tests/tts_bench/bench.py turbo=http://127.0.0.1:8880 --stream false
   tests/tts_bench/candidate.sh pocket
   ```

   `--stream false` measures Turbo as Winston runs it now (`tts.streaming.enabled: false`). To also
   see Turbo streamed, add `turbo-stream=http://127.0.0.1:8880 --stream true` as a second run.
   Pocket clones only with Kyutai's gated weights (accept the terms at
   https://huggingface.co/kyutai/pocket-tts, then `uvx hf auth login`); without them it speaks its
   built-in voice george, and its pairs compare the voice as well as the engine.

2. Check the card: `nvidia-smi`. The brain uses about 16.9 GB, Chatterbox about 3.6 GB, about 2.8
   GB is free, so a candidate gets about 6.3 GB once Chatterbox stops.

3. Stop the speech engine: Settings, Status, the stop icon on the speech engine (`tts`), or

   ```sh
   curl -X POST http://127.0.0.1:8700/api/engines/tts/stop
   ```

4. Each GPU candidate, one at a time (each loads, speaks 63 lines and stops, 3 to 6 minutes):

   ```sh
   tests/tts_bench/candidate.sh qwen3
   tests/tts_bench/candidate.sh voxcpm2
   tests/tts_bench/candidate.sh gepard
   tests/tts_bench/candidate.sh gepard-1.0     # optional, if there is time
   ```

   The script refuses a GPU candidate while less than 5 GB is free. If one runs out of memory
   (VoxCPM2 is the one to watch, upstream says about 8 GB), its log in `work/logs/` says so; note it
   and go on. `--sentences 3` after the name makes a quick try.

5. Start the speech engine again: the start icon on Status, or

   ```sh
   curl -X POST http://127.0.0.1:8700/api/engines/tts/start
   ```

   and check `winston status` shows it healthy.

6. The numbers: `uv run python tests/tts_bench/bench.py --table`.

7. Listening (about 10 minutes, 8 pairs per candidate, about 20 seconds a pair):

   ```sh
   uv run python tests/tts_bench/listen.py
   xdg-open tests/tts_bench/work/listen.html
   ```

   Each pair is Turbo and one candidate on the same line in the same voice, A and B in random
   order, levelled to the same loudness. Answer A, B or Same (keys 1 and 2 play, a, b and s answer),
   then Copy results and paste them back. With five candidates, `--pairs 6` keeps it to 10 minutes;
   `--candidates qwen3,gepard` pairs only those; `--lines s15,s16,s18,s20` pairs only those lines, each in every clip.

Nothing here starts, stops or writes anything outside `tests/tts_bench/work/` except what steps 3
and 5 do by hand. A dry run (3 lines against the live Turbo, Pocket and Gepard on the CPU) was done
when this was written; the numbers from it are not the benchmark.
