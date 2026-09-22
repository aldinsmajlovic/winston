# Wake word training

Trains the "Hey Winston" model with [livekit-wakeword](https://github.com/livekit/livekit-wakeword)
(Apache-2.0). It shares openWakeWord's front end (mel spectrogram, Google speech embedding), so the
exported ONNX runs on openWakeWord's runtime and on livekit-wakeword's own (numpy + onnxruntime).

```sh
uv sync
uv run livekit-wakeword setup --config hey_winston.yaml   # once, ~18 GB of downloads
uv run livekit-wakeword run hey_winston.yaml              # about 45 min on a GPU
uv run livekit-wakeword eval hey_winston.yaml
uv run python score.py <dir of wavs> [model.onnx]         # peak score per clip, streaming style
uv sync --extra mic && uv run python listen.py            # live mic, prints detections
```

The model is `output/hey_winston/hey_winston.onnx`; the bundled copy lives in `assets/wake/` with
its evaluation. `hey_winston_dnn.yaml` trains the classic DNN head instead, the only one that
exports to tflite for Wyoming-openwakeword.

## What to expect

The trainer's validation set is its own TTS, so always score clips from a voice it never heard
before trusting a model. Winston's Chatterbox server does that in one request per phrase. Include
near misses: for this phrase "hey winter", "hey wilson" and bare "winston" are the hard ones, and
the bundled model separates them only partly (phrase 0.76 to 0.83, near misses up to 0.82 on some
voices) while ordinary speech and noise stay near zero. Start at threshold 0.5 and tune on a real
mic in the room; raise it if it fires on its own, lower it if it misses you from across the room.
