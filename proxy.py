"""DeepSeek responses→chat proxy for Codex CLI v0.133+ (SSE streaming)

Bridges Codex CLI's /v1/responses format with DeepSeek's /v1/chat/completions.
Zero dependencies — pure Python stdlib.

Usage:
    export DEEPSEEK_API_KEY="sk-your-key"
    python proxy.py
"""
import http.server
import json
import os
import urllib.request
import ssl
import sys

DEEPSEEK_URL = os.environ.get("DEEPSEEK_API_BASE", "https://api.deepseek.com/v1")
API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
PORT = int(os.environ.get("PROXY_PORT", "18888"))
LOG = os.environ.get("DEBUG", "").lower() in ("1", "true", "yes")


class Proxy(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self):
        path = self.path.rstrip("/")
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b"{}"
        req = json.loads(body)
        if LOG:
            print(f"[REQ] {path}  model={req.get('model','?')}  stream={req.get('stream','?')}")

        if path == "/v1/responses":
            self._proxy_responses(req)
        elif path == "/v1/chat/completions":
            self._proxy_chat(req)
        elif path == "/v1/models":
            self._json(200, {"object": "list", "data": [
                {"id": "deepseek-chat", "object": "model", "owned_by": "deepseek"},
                {"id": "deepseek-reasoner", "object": "model", "owned_by": "deepseek"},
            ]})
        else:
            if LOG:
                print(f"[UNKNOWN] {path}")
            self._json(404, {"error": f"unknown path: {path}"})

    def do_GET(self):
        self._json(200, {"object": "list", "data": [
            {"id": "deepseek-chat", "object": "model", "owned_by": "deepseek"},
            {"id": "deepseek-reasoner", "object": "model", "owned_by": "deepseek"},
        ]})

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.end_headers()

    def _json(self, status, body):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())

    def _make_chat_request(self, req):
        """Convert Codex /v1/responses format to /v1/chat/completions format."""
        model = req.get("model", "deepseek-chat")
        input_text = req.get("input", "")
        instructions = req.get("instructions", "")
        stream = req.get("stream", False)
        tools = req.get("tools", [])

        messages = []
        if instructions:
            messages.append({"role": "system", "content": instructions})

        if isinstance(input_text, str):
            messages.append({"role": "user", "content": input_text})
        elif isinstance(input_text, list):
            for item in input_text:
                if isinstance(item, dict):
                    role = item.get("role", "user")
                    content = item.get("content", "")
                    if role == "tool" and item.get("call_id"):
                        messages.append({
                            "role": "tool",
                            "tool_call_id": item["call_id"],
                            "content": str(content)
                        })
                    elif role == "assistant" and item.get("tool_calls"):
                        messages.append({
                            "role": "assistant",
                            "content": content if content else None,
                            "tool_calls": item["tool_calls"]
                        })
                    else:
                        messages.append({"role": role, "content": str(content)})
                else:
                    messages.append({"role": "user", "content": str(item)})

        chat_body = {"model": model, "messages": messages, "stream": stream}

        if tools:
            ds_tools = []
            for t in tools:
                ds_tools.append({
                    "type": "function",
                    "function": {
                        "name": t.get("name", ""),
                        "description": t.get("description", ""),
                        "parameters": t.get("parameters", {})
                    }
                })
            chat_body["tools"] = ds_tools

        return chat_body

    def _proxy_responses(self, req):
        chat_body = self._make_chat_request(req)
        stream = chat_body.get("stream", False)

        if stream:
            self._stream_response(chat_body)
        else:
            self._nonstream_response(chat_body)

    def _nonstream_response(self, chat_body):
        chat_body["stream"] = False
        chat_req = json.dumps(chat_body).encode()
        chat_url = f"{DEEPSEEK_URL}/chat/completions"

        try:
            http_req = urllib.request.Request(chat_url, data=chat_req, method="POST")
            http_req.add_header("Content-Type", "application/json")
            http_req.add_header("Authorization", f"Bearer {API_KEY}")
            ctx = ssl.create_default_context()
            with urllib.request.urlopen(http_req, timeout=120, context=ctx) as resp:
                ds_body = json.loads(resp.read())
                choice = ds_body.get("choices", [{}])[0].get("message", {})
                content = choice.get("content", "")
                tool_calls = choice.get("tool_calls", [])

                output = [{
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": content}]
                }]

                if tool_calls:
                    tc = []
                    for c in tool_calls:
                        fn = c.get("function", {})
                        tc.append({
                            "type": "function_call",
                            "id": c.get("id", ""),
                            "name": fn.get("name", ""),
                            "arguments": fn.get("arguments", "{}")
                        })
                    output.append({"type": "function_call", "calls": tc})

                codex_resp = {
                    "id": ds_body.get("id", ""),
                    "object": "response",
                    "model": ds_body.get("model", ""),
                    "output": output,
                    "usage": ds_body.get("usage", {}),
                    "status": "completed"
                }
                self._json(200, codex_resp)
        except Exception as e:
            if LOG:
                print(f"[ERR] {e}")
            self._json(502, {"error": str(e)})

    def _stream_response(self, chat_body):
        chat_body["stream"] = True
        chat_req = json.dumps(chat_body).encode()
        chat_url = f"{DEEPSEEK_URL}/chat/completions"

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        try:
            http_req = urllib.request.Request(chat_url, data=chat_req, method="POST")
            http_req.add_header("Content-Type", "application/json")
            http_req.add_header("Authorization", f"Bearer {API_KEY}")
            ctx = ssl.create_default_context()

            with urllib.request.urlopen(http_req, timeout=120, context=ctx) as resp:
                buffer = b""
                response_id = ""
                model = ""
                full_text = ""
                tool_calls = {}

                while True:
                    chunk = resp.read(4096)
                    if not chunk:
                        break
                    buffer += chunk

                    while b"\n" in buffer:
                        line_end = buffer.index(b"\n")
                        line = buffer[:line_end].decode(errors="ignore").strip()
                        buffer = buffer[line_end + 1:]

                        if not line or not line.startswith("data: "):
                            continue
                        data_str = line[6:]
                        if data_str == "[DONE]":
                            break

                        try:
                            data = json.loads(data_str)
                            choice = data.get("choices", [{}])[0]
                            delta = choice.get("delta", {})
                            finish = choice.get("finish_reason")

                            if not response_id:
                                response_id = data.get("id", "")
                                model = data.get("model", "")

                            # Tool call streaming
                            tc_delta = delta.get("tool_calls", [])
                            if tc_delta:
                                for tc in tc_delta:
                                    idx = tc.get("index", 0)
                                    if idx not in tool_calls:
                                        tool_calls[idx] = {"id": "", "name": "", "arguments": ""}
                                    if "id" in tc:
                                        tool_calls[idx]["id"] += tc["id"]
                                    fn = tc.get("function", {})
                                    if "name" in fn:
                                        tool_calls[idx]["name"] += fn["name"]
                                    if "arguments" in fn:
                                        tool_calls[idx]["arguments"] += fn["arguments"]
                            else:
                                content = delta.get("content", "")
                                if content:
                                    full_text += content
                                    event = json.dumps({
                                        "type": "response.output_text.delta",
                                        "response_id": response_id,
                                        "item_id": response_id,
                                        "output_index": 0,
                                        "content_index": 0,
                                        "delta": content
                                    })
                                    sse = f"event: response.output_text.delta\ndata: {event}\n\n"
                                    self.wfile.write(sse.encode())
                                    self.wfile.flush()

                            if finish == "stop":
                                break
                        except json.JSONDecodeError:
                            pass

                # Send complete event
                output_items = []
                if full_text:
                    output_items.append({
                        "id": response_id,
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{"type": "output_text", "text": full_text}]
                    })

                complete = json.dumps({
                    "type": "response.completed",
                    "response": {
                        "id": response_id,
                        "object": "response",
                        "model": model,
                        "output": output_items,
                        "status": "completed"
                    }
                })
                sse = f"event: response.completed\ndata: {complete}\n\n"
                self.wfile.write(sse.encode())
                self.wfile.flush()

        except Exception as e:
            if LOG:
                print(f"[STREAM ERR] {e}")
            err = json.dumps({"type": "error", "error": {"message": str(e)}})
            self.wfile.write(f"event: error\ndata: {err}\n\n".encode())
            self.wfile.flush()

    def _proxy_chat(self, req):
        """Direct passthrough for chat API."""
        chat_req = json.dumps(req).encode()
        chat_url = f"{DEEPSEEK_URL}/chat/completions"
        self.send_response(200)
        self.send_header(
            "Content-Type",
            "text/event-stream" if req.get("stream") else "application/json"
        )
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        try:
            http_req = urllib.request.Request(chat_url, data=chat_req, method="POST")
            http_req.add_header("Content-Type", "application/json")
            http_req.add_header("Authorization", f"Bearer {API_KEY}")
            ctx = ssl.create_default_context()
            with urllib.request.urlopen(http_req, timeout=120, context=ctx) as resp:
                while True:
                    chunk = resp.read(8192)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    self.wfile.flush()
        except Exception as e:
            self.wfile.write(json.dumps({"error": str(e)}).encode())

    def log_message(self, format, *args):
        if LOG:
            sys.stderr.write(f"[PROXY] {format % args}\n")


if __name__ == "__main__":
    if not API_KEY:
        print("ERROR: DEEPSEEK_API_KEY environment variable is required")
        print("  export DEEPSEEK_API_KEY='sk-your-key-here'")
        sys.exit(1)
    print(f"[Proxy] http://127.0.0.1:{PORT}  →  {DEEPSEEK_URL}")
    server = http.server.HTTPServer(("127.0.0.1", PORT), Proxy)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[Proxy] stopped")
