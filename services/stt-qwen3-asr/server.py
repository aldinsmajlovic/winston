"""Winston STT engine: Qwen3-ASR (Qwen's qwen-asr runner on transformers) behind OpenAI's transcription
protocol, so `stt.provider: openai_transcription` talks to it as to any other server.

    .venv/bin/python server.py --model Qwen/Qwen3-ASR-0.6B --port 8001

- POST /v1/audio/transcriptions: multipart `file` (any audio soundfile reads), `model` (ignored: one model
  per server) and `prompt`, given to Qwen3-ASR as its context, so the names in it are expected before
  they are said. The language is detected. Answers {"text", "language"}, or the text alone with
  response_format text.
- GET /v1/models and GET /health.

One transcription at a time. The 0.6B takes about 2.2 GB of VRAM (measured 2026-10-08), which fits beside
the 27B brain and Chatterbox on a 24 GB card; vLLM's stock image has no audio libraries, and with them it
preallocates more than is left there.
"""
import argparse
import asyncio
import io
import logging
import threading
import time
from typing import Annotated

import numpy as np
import soundfile as sf
import torch
import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse

log = logging.getLogger("winston.stt")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--model", default="Qwen/Qwen3-ASR-0.6B", help="Hugging Face id or local dir")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8001)
    ap.add_argument("--device", default="cuda:0", help="cuda:0 or cpu")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    import transformers
    from qwen_asr import Qwen3ASRModel
    transformers.logging.set_verbosity_error()      # a pad-token notice on every request otherwise
    t0 = time.perf_counter()
    dtype = torch.bfloat16 if args.device.startswith("cuda") else torch.float32
    model = Qwen3ASRModel.from_pretrained(args.model, dtype=dtype, device_map=args.device,
                                          max_inference_batch_size=1, max_new_tokens=256)
    lock = threading.Lock()

    def run(audio: np.ndarray, rate: int, context: str):
        with lock, torch.inference_mode():
            return model.transcribe(audio=(audio, rate), context=context)[0]

    run(np.zeros(16000, np.float32), 16000, "")                     # warm up
    gpu = f", {torch.cuda.max_memory_allocated() / 1e9:.2f} GB peak on the GPU" if torch.cuda.is_available() else ""
    log.info("%s loaded on %s in %.1f s%s", args.model, args.device, time.perf_counter() - t0, gpu)

    app = FastAPI(title=f"Winston STT ({args.model})")

    @app.get("/health")
    async def health():
        return {"status": "ok", "model": args.model}

    @app.get("/v1/models")
    async def models():
        return {"object": "list", "data": [{"id": args.model, "object": "model", "owned_by": "winston"}]}

    @app.post("/v1/audio/transcriptions")
    async def transcriptions(file: Annotated[UploadFile, File()], model: Annotated[str, Form()] = "",
                             prompt: Annotated[str, Form()] = "", response_format: Annotated[str, Form()] = "json"):
        try:
            a, rate = sf.read(io.BytesIO(await file.read()), dtype="float32", always_2d=True)
        except Exception as e:                                      # noqa: BLE001, the client's file
            raise HTTPException(400, f"unreadable audio: {e}") from e
        t = time.perf_counter()
        r = await asyncio.to_thread(run, a.mean(axis=1), rate, prompt)
        log.info("%.2f s of audio in %.0f ms (%s): %r", len(a) / rate, (time.perf_counter() - t) * 1000,
                 r.language, r.text)
        if response_format == "text":
            return PlainTextResponse(r.text)
        return {"text": r.text, "language": r.language}

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
