"""The blind listening page: pairs of the baseline and each candidate on the same line in the same voice, or with
--abc, every named engine side by side on the same line (A/B/C).

    uv run python tests/tts_bench/listen.py                    # work/listen.html from work/results.json
    uv run python tests/tts_bench/listen.py --pairs 8 --baseline turbo --seed 7
    uv run python tests/tts_bench/listen.py --abc breeze2-voice,zonos3,chatterbox --pairs 12

For every candidate in results.json, --pairs sentences are drawn (each clip in turn), and each pair
plays the baseline and the candidate as A and B in a random order; the pairs are shuffled across
candidates. Every clip is levelled to the same loudness first, so the louder one does not win. The
page is one HTML file with the audio inside it: no requests, opens from disk. Answers are A, B or
Same; "Copy results" copies which engine each answer picked, per candidate, to paste back.
"""
import argparse
import base64
import html
import io
import json
import random
import wave
from pathlib import Path

import numpy as np
from bench import CLIPS, RESULTS, WORK, read_wav

TARGET_RMS = 10 ** (-23 / 20)      # speech level, dBFS over the voiced part


def levelled_wav(path: Path) -> str:
    """The clip at one loudness as a base64 wav: RMS over samples above the noise floor, peaks kept under 0.97."""
    a, sr = read_wav(path)
    voiced = a[np.abs(a) > 0.01]
    rms = float(np.sqrt(np.mean(voiced ** 2))) if len(voiced) else 0.0
    if rms > 0:
        a = a * min(TARGET_RMS / rms, 0.97 / max(float(np.abs(a).max()), 1e-6))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes((np.clip(a, -1, 1) * 32767).astype(np.int16).tobytes())
    return base64.b64encode(buf.getvalue()).decode()


def build_pairs(results: dict, baseline: str, per: int, rng: random.Random, only: set[str],
                lines: set[str] = frozenset()) -> list[dict]:
    """per pairs for each candidate, a line once across clips; with lines, only those, each in every clip."""
    base = {(r["clip"], r["sid"]): r for r in results[baseline]["rows"]}
    pairs = []
    for name, res in results.items():
        if name == baseline or not res.get("rows") or (only and name not in only):
            continue
        rows = res["rows"]
        builtin = res.get("builtin_voice")
        # (candidate row, baseline row) for every line and clip both have; the baseline's first clip for a built-in voice
        both: dict[str, list[tuple[dict, dict]]] = {}
        for r in rows:
            if lines and r["sid"] not in lines:
                continue
            ref = base.get((CLIPS[0] if builtin else r["clip"], r["sid"]))
            if ref is not None:
                both.setdefault(r["clip"], []).append((r, ref))
        clips = sorted(both, key=lambda c: CLIPS.index(c) if c in CLIPS else len(CLIPS))
        for c in clips:
            rng.shuffle(both[c])
        used: set[str] = set()
        for i in range(per * len(clips)):                  # each clip in turn, a line not used yet
            if sum(p["candidate"] == name for p in pairs) >= per or not clips:
                break
            clip = clips[i % len(clips)]
            pick = next(((cand, ref) for cand, ref in both[clip]
                         if (clip, cand["sid"]) not in used and (lines or cand["sid"] not in used)), None)
            if pick is None:
                continue
            (cand, ref), sid = pick, pick[0]["sid"]
            used.update({sid, (clip, sid)})
            opts = [(name, cand), (baseline, ref)]
            rng.shuffle(opts)
            pairs.append({"candidate": name, "sid": sid, "clip": clip, "text": ref["text"],
                          "files": [r["file"] for _, r in opts], "engines": [n for n, _ in opts],
                          "same_voice": not builtin})
    rng.shuffle(pairs)
    return pairs


def build_rounds(results: dict, names: list[str], per: int, rng: random.Random, lines: set[str] = frozenset()) -> list[dict]:
    """per rounds of every named engine on one line in one clip, in a random order: the A/B/C test. Each clip in
    turn, a line once across clips unless lines are given (then each in every clip)."""
    have = [{(r["clip"], r["sid"]): r for r in results[n]["rows"]} for n in names]
    shared = sorted(set.intersection(*(set(h) for h in have)), key=lambda k: (CLIPS.index(k[0]) if k[0] in CLIPS else 99, k[1]))
    if lines:
        shared = [k for k in shared if k[1] in lines]
    rng.shuffle(shared)
    clips = sorted({c for c, _ in shared}, key=lambda c: CLIPS.index(c) if c in CLIPS else 99)
    rounds, used = [], set()
    for i in range(per * max(1, len(clips))):
        if len(rounds) >= per or not clips:
            break
        clip = clips[i % len(clips)]
        k = next((k for k in shared if k[0] == clip and k not in used and (lines or k[1] not in used)), None)
        if k is None:
            continue
        used.update({k, k[1]})
        opts = [(n, h[k]) for n, h in zip(names, have)]
        rng.shuffle(opts)
        rounds.append({"candidate": "/".join(names), "sid": k[1], "clip": clip, "text": opts[0][1]["text"],
                       "files": [r["file"] for _, r in opts], "engines": [n for n, _ in opts], "same_voice": True})
    rng.shuffle(rounds)
    return rounds


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Voice listening test</title>
<style>
:root { --bg: #ffffff; --fg: #1b1d21; --muted: #6b7079; --line: #e3e5e8; --primary: #2f6fdb; --on-primary: #ffffff; }
@media (prefers-color-scheme: dark) { :root { --bg: #16181c; --fg: #e8eaed; --muted: #9aa0a8; --line: #2c3036;
  --primary: #6b9cf0; --on-primary: #10131a; } }
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--fg); font: 16px/1.5 system-ui, sans-serif; }
main { max-width: 640px; margin: 0 auto; padding: 32px 16px; }
h1 { font-size: 22px; font-weight: 600; margin: 0 0 4px; }
p { margin: 0 0 16px; color: var(--muted); }
.progress { height: 4px; background: var(--line); border-radius: 2px; margin: 16px 0 24px; }
.progress div { height: 100%; background: var(--primary); border-radius: 2px; width: 0; }
.line { font-size: 20px; margin: 8px 0 24px; min-height: 60px; }
.row { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 16px; }
button { font: inherit; padding: 10px 18px; border-radius: 8px; border: 1px solid var(--line); background: var(--bg);
  color: var(--fg); cursor: pointer; }
button:hover { border-color: var(--muted); }
button.play { min-width: 120px; }
button.chosen { background: var(--primary); border-color: var(--primary); color: var(--on-primary); }
button:disabled { opacity: 0.4; cursor: default; }
.nav { display: flex; justify-content: space-between; margin-top: 24px; }
textarea { width: 100%; min-height: 160px; margin-top: 16px; font: inherit; font-size: 14px; color: var(--fg);
  background: var(--bg); border: 1px solid var(--line); border-radius: 8px; padding: 8px; }
.hidden { display: none; }
</style></head>
<body><main>
<h1>Which sounds more like a person talking?</h1>
<p>Play each, then pick. Keys: 1, 2, 3 play A, B, C; a, b, c and s answer; arrows move.</p>
<div class="progress"><div id="bar"></div></div>
<div id="count"></div>
<div class="line" id="text"></div>
<div class="row" id="plays"></div>
<div class="row" id="answers"></div>
<div class="nav"><button id="prev">Back</button><button id="copy">Copy results</button><button id="next">Next</button></div>
<p id="done" class="hidden">All answered. Copy the results and paste them back.</p>
<textarea id="out" class="hidden" readonly></textarea>
</main>
<script>
const PAIRS = __PAIRS__;
const AUDIO = __AUDIO__;
// which engine is which, read only when the results are copied (base64, so a glance at the source does not tell)
const KEY = JSON.parse(atob("__KEY__"));
const answers = new Array(PAIRS.length).fill(null);
let i = 0, player = null;
const $ = id => document.getElementById(id);
const L = "ABCDEFGH";
function play(k) {
  if (k >= PAIRS[i].opts.length) return;
  if (player) player.pause();
  player = new Audio("data:audio/wav;base64," + AUDIO[PAIRS[i].opts[k]]);
  player.play();
}
function buttons() {
  const n = PAIRS[i].opts.length;
  $("plays").innerHTML = ""; $("answers").innerHTML = "";
  for (let k = 0; k < n; k++) {
    const b = document.createElement("button"); b.className = "play"; b.textContent = "Play " + L[k]; b.onclick = () => play(k);
    $("plays").appendChild(b);
    const c = document.createElement("button"); c.textContent = L[k]; c.onclick = () => answer(L[k]);
    c.classList.toggle("chosen", answers[i] === L[k]); $("answers").appendChild(c);
  }
  const s = document.createElement("button"); s.textContent = "Same"; s.onclick = () => answer("Same");
  s.classList.toggle("chosen", answers[i] === "Same"); $("answers").appendChild(s);
}
function show() {
  const p = PAIRS[i];
  $("count").textContent = (p.opts.length > 2 ? "Round " : "Pair ") + (i + 1) + " of " + PAIRS.length;
  $("text").textContent = p.text;
  $("bar").style.width = (100 * answers.filter(a => a).length / PAIRS.length) + "%";
  buttons();
  $("prev").disabled = i === 0;
  $("next").disabled = i === PAIRS.length - 1;
  $("done").classList.toggle("hidden", answers.some(a => !a));
}
function answer(v) {
  answers[i] = v;
  if (player) player.pause();
  const next = answers.findIndex((a, k) => !a && k > i);
  if (next >= 0) i = next; else { const any = answers.findIndex(a => !a); if (any >= 0) i = any; }
  show();
}
function results() {
  const per = {};
  const lines = [];
  KEY.forEach((p, k) => {
    const a = answers[k];
    const pick = !a ? "unanswered" : a === "Same" ? "same" : p.engines[L.indexOf(a)];
    const n = per[p.candidate] = per[p.candidate] || {};
    n[pick] = (n[pick] || 0) + 1;
    lines.push([p.candidate, p.sid, p.clip, p.same_voice ? "same voice" : "own voice", pick].join(" "));
  });
  const head = Object.entries(per).map(([c, n]) => c + ": " + Object.entries(n).map(([e, v]) => e + " " + v).join(", "));
  return "Listening results (" + new Date().toISOString().slice(0, 16) + ", baseline __BASELINE__)\\n"
    + head.join("\\n") + "\\n\\n" + lines.join("\\n") + "\\n";
}
async function copy() {
  const text = results();
  $("out").value = text;
  $("out").classList.remove("hidden");
  try { await navigator.clipboard.writeText(text); $("copy").textContent = "Copied"; }
  catch (e) { $("out").select(); document.execCommand("copy"); $("copy").textContent = "Copied"; }
  setTimeout(() => { $("copy").textContent = "Copy results"; }, 2000);
}
$("prev").onclick = () => { if (i > 0) { i--; show(); } };
$("next").onclick = () => { if (i < PAIRS.length - 1) { i++; show(); } };
$("copy").onclick = copy;
document.addEventListener("keydown", e => {
  if (e.target.tagName === "TEXTAREA") return;
  const k = e.key.toLowerCase();
  const n = PAIRS[i].opts.length;
  if (k >= "1" && k <= "8") play(+k - 1);
  else if (k === "s") answer("Same");
  else if (k.length === 1 && L.toLowerCase().indexOf(k) >= 0 && L.toLowerCase().indexOf(k) < n) answer(k.toUpperCase());
  else if (k === "arrowleft") $("prev").click(); else if (k === "arrowright") $("next").click();
});
show();
</script></body></html>
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--baseline", default="turbo", help="the name in results.json every candidate is paired with")
    ap.add_argument("--pairs", type=int, default=8, help="pairs per candidate (about 20 s each to judge)")
    ap.add_argument("--seed", type=int, default=None, help="for the same draw and order again")
    ap.add_argument("--candidates", default="", help="comma-separated names to pair; default every one but the baseline")
    ap.add_argument("--lines", default="", help="comma-separated line ids (s15,s18) to pair, each in every clip")
    ap.add_argument("--abc", default="", help="comma-separated names heard side by side on each line (A/B/C...), "
                                              "instead of pairs against the baseline; --pairs is then the rounds")
    ap.add_argument("--out", default=str(WORK / "listen.html"))
    args = ap.parse_args()
    results = json.loads(RESULTS.read_text())
    abc = [c.strip() for c in args.abc.split(",") if c.strip()]
    for n in abc or [args.baseline]:
        if n not in results:
            raise SystemExit(f"no {n!r} in {RESULTS}; run bench.py with it first")
    only = {c.strip() for c in args.candidates.split(",") if c.strip()}
    rng = random.Random(args.seed)
    lines = {x.strip() for x in args.lines.split(",") if x.strip()}
    pairs = (build_rounds(results, abc, args.pairs, rng, lines) if abc
             else build_pairs(results, args.baseline, args.pairs, rng, only, lines))
    if not pairs:
        raise SystemExit("no candidate shares a sentence and clip with the baseline")
    audio = {f: levelled_wav(WORK / f) for p in pairs for f in p["files"]}
    # the page only needs what it shows and what the copied results say; file names would give the engines away
    order = sorted(audio)
    rng.shuffle(order)             # ids in path order would sort the engines by name
    ids = {f: f"c{k}" for k, f in enumerate(order)}
    shown = [{"text": p["text"], "opts": [ids[f] for f in p["files"]]} for p in pairs]
    key = [{k: p[k] for k in ("candidate", "sid", "clip", "same_voice", "engines")} for p in pairs]
    page = (PAGE.replace("__PAIRS__", json.dumps(shown))
            .replace("__KEY__", base64.b64encode(json.dumps(key).encode()).decode())
            .replace("__AUDIO__", json.dumps({ids[f]: b for f, b in audio.items()}))
            .replace("__BASELINE__", html.escape("none, side by side" if abc else args.baseline)))
    Path(args.out).write_text(page, encoding="utf-8")
    per = {}
    for p in pairs:
        per[p["candidate"]] = per.get(p["candidate"], 0) + 1
    print(f"{len(pairs)} {'rounds' if abc else 'pairs'} ({', '.join(f'{c} {n}' for c, n in per.items())}), "
          f"{Path(args.out).stat().st_size / 1e6:.1f} MB: {args.out}")


if __name__ == "__main__":
    main()
