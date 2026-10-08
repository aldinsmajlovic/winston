# Wake word models

`okay_winston.tflite` with its manifest `okay_winston.json`: microWakeWord model of "Okay
Winston", for the Voice PE (built into its firmware by `services/voice-pe/`). Trained 2026-09-25
with the microWakeWord trainer v17 on 50,000 Piper samples (LibriTTS-R voices), 40,000 steps.
`okay_winston_eval.json` is the trainer's report: recall 98.6% on its synthetic validation set at
cutoff 0.95, 0 false accepts over 9.7 h of ambient audio. Both are its own synthetic data, so the
room is the real test.

`hey_winston.onnx`: detects "Hey Winston", for the Pi satellite path; the Voice PE wakes on Okay
Winston instead. Trained 2026-09-22 with livekit-wakeword (openWakeWord-compatible front end, small
conv-attention head, 158 KB). Input is 16 kHz mono PCM in about 2 second chunks; the output is one
score. `hey_winston_eval.json` holds the synthetic validation numbers: 0 false positives per hour
over 20 h, recall 84% at threshold 0.5 and 95% at 0.22. Tune the threshold per room on a real mic;
"hey winter" and bare "winston" are the known near misses. The trainer, with its configs, a scorer
and a live mic listener, is in git history at commit 746e994 (`services/wakeword/`).

The model is Apache-2.0 like the rest of Winston. It was trained on synthetic speech only, no
recordings of anyone: positives from Piper VITS voices built on LibriTTS-R (CC BY 4.0), negatives
from the ACAV100M feature set published for openWakeWord, MUSAN noise (CC BY 4.0) and the MIT
impulse response survey (CC BY 4.0) for room simulation.
