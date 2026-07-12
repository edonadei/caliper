#!/usr/bin/env python3
"""A minimal, dependency-free HTTP MCP server with bearer-header auth.

The remote-transport sibling of ``echo_server.py``: it speaks just enough of the
Streamable-HTTP MCP transport (JSON-RPC 2.0 over HTTP POST to ``/mcp``, replying
``application/json``) to let a real agent connect and call one tool:

    secret_word() -> "caliper"

Every request must carry ``Authorization: Bearer <token>`` (the token comes from
``ECHO_MCP_TOKEN``, default ``s3cr3t``); anything else gets ``401``. That auth
gate is the point: because a missing or wrong header fails the request, a
*passing* eval proves the declared ``headers:`` were actually interpolated and
transmitted end-to-end — real coverage of the remote header-auth path, unlike a
no-auth public endpoint. Deliberately hand-rolled (stdlib only) to keep
caliper's footprint at zero extra dependencies.

Run it (backgrounded) from an eval's ``setup:``; see tests/mcp-header-smoke.eval.yaml.
"""

from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PROTOCOL_VERSION = "2024-11-05"
TOKEN = os.environ.get("ECHO_MCP_TOKEN", "s3cr3t")

TOOLS = [
    {
        "name": "secret_word",
        "description": "Returns the secret word.",
        "inputSchema": {"type": "object", "properties": {}},
    }
]


def _handle(request: dict) -> dict | None:
    method = request.get("method")
    request_id = request.get("id")

    # Notifications carry no id and expect no response.
    if request_id is None:
        return None

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "echo", "version": "0.1.0"},
            },
        }
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": TOOLS}}
    if method == "tools/call":
        name = (request.get("params") or {}).get("name")
        if name == "secret_word":
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {"content": [{"type": "text", "text": "caliper"}]},
            }
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32602, "message": f"unknown tool: {name}"},
        }
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": -32601, "message": f"method not found: {method}"},
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # quieter, prefixed access log
        sys.stderr.write("[echo-http] " + (fmt % args) + "\n")

    def _authed(self) -> bool:
        return self.headers.get("Authorization", "") == f"Bearer {TOKEN}"

    def _unauthorized(self) -> None:
        body = json.dumps({"error": "missing or bad bearer token"}).encode()
        self.send_response(401)
        self.send_header("Content-Type", "application/json")
        self.send_header("WWW-Authenticate", "Bearer")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        # Optional server->client SSE stream; this minimal server declines it.
        if not self._authed():
            return self._unauthorized()
        self.send_response(405)
        self.end_headers()

    def do_POST(self):
        if self.path.rstrip("/") not in ("/mcp", ""):
            self.send_response(404)
            self.end_headers()
            return
        if not self._authed():
            return self._unauthorized()
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b""
        try:
            request = json.loads(raw)
        except json.JSONDecodeError:
            self.send_response(400)
            self.end_headers()
            return
        response = _handle(request)
        if response is None:
            self.send_response(202)  # notification: accepted, no body
            self.send_header("Mcp-Session-Id", "echo-session")
            self.end_headers()
            return
        body = json.dumps(response).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Mcp-Session-Id", "echo-session")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    sys.stderr.write(f"[echo-http] listening on 127.0.0.1:{port}\n")
    server.serve_forever()


if __name__ == "__main__":
    main()
