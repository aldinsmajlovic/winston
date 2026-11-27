# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "onnxruntime"]
# ///
"""Audio to face curves at 30 fps, one JSON per clip:
- shapes: the 52 ARKit face shapes from wav2arkit_cpu (Apache-2.0), which moves the mouth;
- emphasis: brow (0..1, the voice's pitch rising above its usual) and nod (0..1, stressed syllables),
  for the upper face and head, which the model leaves still.
uv run arkit.py MODEL_DIR OUT_DIR clip.wav ...   (needs ffmpeg)"""
import json, subprocess, sys, time
from pathlib import Path
import numpy as np

SR, FPS = 16000, 30


def emphasis(x, n):
    """Loudness and pitch per frame, from plain autocorrelation (a male voice, 70 to 300 Hz)."""
    hop, win, lo, hi = SR / FPS, 640, SR // 300, SR // 70
    db, f0 = np.full(n, -90.0), np.zeros(n)
    for i in range(n):
        c = int(i * hop + hop / 2)
        seg = x[max(0, c - win // 2):c + win // 2]
        if len(seg) < win // 2:
            continue
        seg = seg - seg.mean()
        db[i] = 20 * np.log10(np.sqrt(np.mean(seg ** 2)) + 1e-9)
        ac = np.correlate(seg, seg, 'full')[len(seg) - 1:]
        k = lo + int(np.argmax(ac[lo:hi]))
        if db[i] > -40 and ac[k] > 0.45 * ac[0]:
            f0[i] = SR / k
    def smooth(v, w):
        return np.convolve(v, np.hanning(w + 2)[1:-1] / np.hanning(w + 2)[1:-1].sum(), 'same')
    voiced = f0 > 0
    if voiced.sum() < 5:
        return np.zeros(n), np.zeros(n)
    # Brows: pitch above the clip's median, in semitones; only clear rises count.
    st = np.where(voiced, 12 * np.log2(np.maximum(f0, 1) / np.median(f0[voiced])), 0)
    brow = smooth(np.clip((np.clip(st, -8, 8) - 1.5) / 5, 0, 1), 7)
    # Nods: loudness peaks that rise well above the valley before them, at most one per 0.3 s,
    # each a small dip of the head that starts just before the syllable and returns in 0.4 s.
    loud, med = smooth(db, 5), np.median(db[voiced])
    peaks = [i for i in range(1, n - 1) if loud[i] >= loud[i - 1] and loud[i] > loud[i + 1]
             and loud[i] > med + 1 and loud[i] - loud[max(0, i - 8):i].min() > 6]
    kept = []
    for i in sorted(peaks, key=lambda i: -loud[i]):
        if all(abs(i - j) >= 9 for j in kept):
            kept.append(i)
    nod, bump = np.zeros(n), np.sin(np.linspace(0, np.pi, 12)) ** 2
    for i in kept:
        s, a = np.clip((loud[i] - med) / 8, 0.3, 1), max(0, i - 2)
        nod[a:a + 12] += s * bump[:len(nod[a:a + 12])]
    return np.clip(brow, 0, 1), np.clip(nod, 0, 1)


if __name__ == '__main__':
    import onnxruntime as ort
    model, out = Path(sys.argv[1]), Path(sys.argv[2])
    names = json.loads((model / 'config.json').read_text())['blendshape_names']
    session = ort.InferenceSession(str(model / 'wav2arkit_cpu.onnx'), providers=['CPUExecutionProvider'])
    for wav in map(Path, sys.argv[3:]):
        pcm = subprocess.run(['ffmpeg', '-loglevel', 'error', '-i', wav, '-ac', '1', '-ar', str(SR), '-f', 'f32le', '-'],
                             capture_output=True, check=True).stdout
        audio = np.frombuffer(pcm, np.float32).reshape(1, -1)
        t = time.perf_counter()
        shapes = session.run(None, {'audio_waveform': audio})[0][0]
        brow, nod = emphasis(audio[0], len(shapes))
        dt = time.perf_counter() - t
        print(f'{wav.name}: {audio.shape[1] / SR:.2f} s audio -> {len(shapes)} frames in {dt * 1000:.0f} ms; '
              f'{int((np.diff(np.r_[0, nod > 0.05].astype(int)) == 1).sum())} nods, brows up {(brow > 0.1).mean():.0%} of the time')
        used = [i for i in range(len(names)) if shapes[:, i].max() > 0.02]
        def r(v):
            return [round(float(x), 3) for x in v]
        (out / (wav.stem + '.json')).write_text(json.dumps(
            {'fps': FPS, 'shapes': {names[i]: r(shapes[:, i]) for i in used}, 'emphasis': {'brow': r(brow), 'nod': r(nod)}},
            separators=(',', ':')))
