#!/usr/bin/env bash
# One benchmark candidate, start to finish: start its server on a spare port with the benchmark's
# copied clips, wait until it answers, run bench.py against it, stop it. Its log goes to
# work/logs/NAME.log. Extra arguments go to bench.py (--sentences 3 for a quick try).
#
#   tests/tts_bench/candidate.sh qwen3 | voxcpm2 | gepard | gepard-1.0 | pocket [bench.py args]
#
# The GPU candidates need the live Chatterbox stopped first (see README.md): the script refuses to
# start one while less than 5 GB of the card is free, so it never pushes the brain out of memory.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
work="$here/work"
voices="$work/voices"
name="${1:?usage: candidate.sh qwen3|voxcpm2|gepard|gepard-1.0|pocket [bench.py args]}"
shift
default="$voices/librivox/clifton_fatherbrown.wav"   # the voice Winston speaks in now
gpu=1
bench_name="$name"
case "$name" in
  qwen3)      dir=qwen3;     port=8891; args=() ;;
  voxcpm2)    dir=voxcpm2;   port=8892; args=() ;;
  gepard)     dir=gepard;    port=8893; args=() ;;
  gepard-1.0) dir=gepard;    port=8894; args=(--checkpoint nineninesix/gepard-1.0) ;;
  pocket)     dir=pocket;    port=8895; gpu=0 ;;
  *) echo "unknown candidate $name"; exit 2 ;;
esac

# the clips and their transcripts, copied read-only from data/voices
[ -f "$default" ] || (cd "$repo" && uv run python tests/tts_bench/bench.py --prepare)

if [ "$gpu" = 1 ]; then
  free_mb="$(nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits | head -1 | tr -d ' ')"
  if [ "${free_mb:-0}" -lt 5000 ]; then
    echo "only ${free_mb} MB free on the GPU; stop the live speech engine first (README.md, step 3)"
    exit 1
  fi
fi

mkdir -p "$work/logs"
log="$work/logs/$bench_name.log"
if [ "$name" = pocket ]; then
  # Pocket's cloning weights are gated (accept Kyutai's terms on Hugging Face, then `uvx hf auth login`);
  # without them it speaks its built-in george, and bench.py is told so
  cd "$repo/services/tts-pocket"
  export CUDA_VISIBLE_DEVICES=""       # CPU only: never a CUDA context beside the live engines
  .venv/bin/python server.py --voice "$default" --voices-dir "$voices" --port "$port" >"$log" 2>&1 &
  pid=$!
  sleep 5
  if ! kill -0 "$pid" 2>/dev/null; then
    .venv/bin/python server.py --voice george --voices-dir "$voices" --port "$port" >"$log" 2>&1 &
    pid=$!
    url="http://127.0.0.1:$port#george"
  else
    url="http://127.0.0.1:$port"
  fi
else
  cd "$repo/services/tts-$dir"
  .venv/bin/python server.py --voice "$default" --voices-dir "$voices" --port "$port" "${args[@]}" >"$log" 2>&1 &
  pid=$!
  url="http://127.0.0.1:$port"
fi
trap 'kill "$pid" 2>/dev/null; wait "$pid" 2>/dev/null || true' EXIT INT TERM

echo "starting $name on :$port (log $log)"
t0=$(date +%s)
until curl -sf "http://127.0.0.1:$port/health" >/dev/null; do
  kill -0 "$pid" 2>/dev/null || { echo "$name exited while starting:"; tail -n 20 "$log"; exit 1; }
  [ $(( $(date +%s) - t0 )) -lt 600 ] || { echo "$name did not answer in 10 minutes"; exit 1; }
  sleep 2
done
echo "$name up in $(( $(date +%s) - t0 )) s"

cd "$repo"
uv run python tests/tts_bench/bench.py "$bench_name=$url" "$@"
grep -E "gpu memory|loaded" "$log" | tail -n 3 || true
