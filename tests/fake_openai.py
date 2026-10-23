"""A made-up OpenAI-compatible brain for checks that need one answering: /v1/models and
/v1/chat/completions (streamed or not), always the same reply. Standard library only, so it runs in a
bare container with any Python.

    python tests/fake_openai.py 9999
"""
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REPLY = "Good evening. Everything is in order."


class Handler(BaseHTTPRequestHandler):
    def _json(self, body: dict):
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._json({"object": "list", "data": [{"id": "fake", "object": "model", "owned_by": "test"}]})

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        base = {"id": "fake-1", "created": 0, "model": req.get("model", "fake")}
        if not req.get("stream"):
            self._json({**base, "object": "chat.completion", "choices": [
                {"index": 0, "message": {"role": "assistant", "content": REPLY}, "finish_reason": "stop"}]})
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for delta, finish in (({"role": "assistant", "content": REPLY}, None), ({}, "stop")):
            chunk = {**base, "object": "chat.completion.chunk",
                     "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1]) if len(sys.argv) > 1 else 9999), Handler).serve_forever()
