# Wake word models

`hey_winston.onnx`: detects "Hey Winston". Trained 2026-09-22 with `services/wakeword/`
(livekit-wakeword, openWakeWord-compatible front end, small conv-attention head, 158 KB). Input is
16 kHz mono PCM in about 2 second chunks; the output is one score. `hey_winston_eval.json` holds the
synthetic validation numbers: 0 false positives per hour over 20 h, recall 84% at threshold 0.5 and
95% at 0.22. Tune the threshold per room with `services/wakeword/listen.py`; "hey winter" and bare
"winston" are the known near misses.

The model is Apache-2.0 like the rest of Winston. It was trained on synthetic speech only, no
recordings of anyone: positives from Piper VITS voices built on LibriTTS-R (CC BY 4.0), negatives
from the ACAV100M feature set published for openWakeWord, MUSAN noise (CC BY 4.0) and the MIT
impulse response survey (CC BY 4.0) for room simulation.
