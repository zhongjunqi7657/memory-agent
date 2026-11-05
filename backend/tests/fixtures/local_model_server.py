"""Deterministic OpenAI-compatible fixture for a separately spawned Worker."""

import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MEMORY_CONTENT = "用户偏好先理解原理再看代码"


@contextmanager
def model_server():
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass  # Never print HTTP credentials or request bodies.

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append((self.path, body))
            if self.path == "/v1/chat/completions":
                result = {"memories": [{"content": MEMORY_CONTENT, "kind": "semantic",
                    "confidence": 0.99, "sensitivity": "normal", "canonical_key": "learning_style"}]}
                message = {"role": "assistant", "content": json.dumps(result, ensure_ascii=False)}
                if body.get("tools"):
                    message = {"role": "assistant", "content": None, "tool_calls": [{
                        "id": "fixture-extraction", "type": "function", "function": {
                            "name": body["tools"][0]["function"]["name"], "arguments": json.dumps(result)}}]}
                response = {"id": "fixture", "object": "chat.completion", "created": 1,
                    "model": body["model"], "choices": [{"index": 0, "message": message, "finish_reason": "stop"}]}
            elif self.path == "/v1/embeddings":
                response = {"object": "list", "model": body["model"], "data": [{
                    "object": "embedding", "index": 0, "embedding": [1.0] + [0.0] * 1023}]}
            else:
                self.send_error(404)
                return
            payload = json.dumps(response).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
