"""A fake LM Studio server for tests: answers like LM Studio, no GPU needed.

Run it alone to try the node in ComfyUI without LM Studio:
    python tests/fake_lmstudio.py --port 1234
"""

from __future__ import annotations

import argparse
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DEFAULT_REPLY = ("A cinematic photograph in warm evening light, shot at eye level with a 50mm lens. "
                 "A young woman in a red wool coat holds a paper lantern. She walks slowly along a quiet "
                 "harbour pier while gulls circle above the boats.")


class FakeLMStudio:
    def __init__(self, port: int = 0):
        self.models = [
            {"type": "llm", "key": "google/gemma-4-31b", "display_name": "Gemma 4 31B",
             "loaded_instances": [{"id": "google/gemma-4-31b"}], "capabilities": {"vision": True}},
            {"type": "llm", "key": "qwen/qwen3-8b", "display_name": "Qwen3 8B", "loaded_instances": [],
             "capabilities": {"vision": False}},
            {"type": "embedding", "key": "text-embedding-nomic", "loaded_instances": []},
        ]
        self.replies: list[str] = []  # queued replies; DEFAULT_REPLY when empty
        self.thinking = ""
        self.finish_reason = "stop"
        self.reject_fields: set[str] = set()  # 400 when any of these is in the body
        self.native = True  # False: /api/v1/* answers 404, like older versions or other servers
        self.delay = 0.0
        self.requests: list[tuple[str, dict]] = []
        self.token: str | None = None
        self.server = ThreadingHTTPServer(("127.0.0.1", port), self._handler())
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def start(self):
        self.thread.start()
        return self

    def stop(self):
        self.server.shutdown()
        self.server.server_close()

    def next_reply(self) -> str:
        return self.replies.pop(0) if self.replies else DEFAULT_REPLY

    def _handler(self):
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _json(self, status, data):
                raw = json.dumps(data).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _auth_ok(self):
                if fake.token and self.headers.get("Authorization") != f"Bearer {fake.token}":
                    self._json(401, {"error": "Unauthorized"})
                    return False
                return True

            def do_GET(self):
                if not self._auth_ok():
                    return
                if self.path == "/api/v1/models" and fake.native:
                    return self._json(200, {"models": fake.models})
                if self.path == "/v1/models":
                    return self._json(200, {"object": "list", "data": [{"id": m["key"], "object": "model"} for m in fake.models]})
                self._json(404, {"error": f"Unexpected endpoint {self.path}"})

            def do_POST(self):
                if not self._auth_ok():
                    return
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                fake.requests.append((self.path, body))
                bad = sorted(k for k in fake.reject_fields if k in body)
                if bad:
                    return self._json(400, {"error": f"Unrecognized key(s) in object: '{bad[0]}'"})
                if fake.delay:
                    time.sleep(fake.delay)
                if self.path == "/v1/chat/completions":
                    return self._openai(body)
                if self.path == "/api/v1/chat" and fake.native:
                    return self._native(body)
                self._json(404, {"error": f"Unexpected endpoint {self.path}"})

            def _openai(self, body):
                text = fake.next_reply()
                if body.get("stream"):
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.end_headers()
                    chunks = []
                    if fake.thinking:
                        chunks.append({"choices": [{"index": 0, "delta": {"reasoning_content": fake.thinking}}]})
                    words = text.split(" ")
                    for i, w in enumerate(words):
                        piece = w if i == 0 else " " + w
                        chunks.append({"choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}]})
                    chunks.append({"choices": [{"index": 0, "delta": {}, "finish_reason": fake.finish_reason}]})
                    chunks.append({"choices": [], "usage": {"prompt_tokens": 120, "completion_tokens": len(words)}})
                    for c in chunks:
                        self.wfile.write(f"data: {json.dumps(c)}\n\n".encode())
                        self.wfile.flush()
                        if fake.delay:
                            time.sleep(fake.delay / 10)
                    self.wfile.write(b"data: [DONE]\n\n")
                    return
                msg = {"role": "assistant", "content": text}
                if fake.thinking:
                    msg["reasoning_content"] = fake.thinking
                self._json(200, {
                    "id": "chatcmpl-1", "object": "chat.completion", "model": body.get("model"),
                    "choices": [{"index": 0, "message": msg, "finish_reason": fake.finish_reason}],
                    "usage": {"prompt_tokens": 120, "completion_tokens": len(text.split()), "total_tokens": 0},
                    "stats": {},
                })

            def _native(self, body):
                text = fake.next_reply()
                output = []
                if fake.thinking:
                    output.append({"type": "reasoning", "content": fake.thinking})
                output.append({"type": "message", "content": text})
                self._json(200, {
                    "model_instance_id": body.get("model"), "output": output,
                    "stats": {"input_tokens": 120, "total_output_tokens": len(text.split()),
                              "reasoning_output_tokens": len(fake.thinking.split()),
                              "tokens_per_second": 42.5, "time_to_first_token_seconds": 0.2,
                              "stop_reason": "maxPredictedTokensReached" if fake.finish_reason == "length" else "eosFound"},
                    "response_id": "resp_1",
                })

        return Handler


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=1234)
    parser.add_argument("--delay", type=float, default=0.0, help="seconds to wait before each reply")
    args = parser.parse_args()
    fake = FakeLMStudio(args.port)
    fake.delay = args.delay
    fake.start()
    print(f"Fake LM Studio at {fake.url} (Ctrl+C to stop)")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        fake.stop()
