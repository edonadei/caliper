#!/usr/bin/env python3
"""A minimal, dependency-free stdio MCP server for caliper's end-to-end test.

Speaks just enough of the Model Context Protocol over newline-delimited
JSON-RPC 2.0 on stdin/stdout to let a real agent connect and call one tool:

    secret_word() -> "caliper"

It exists so an eval can assert the transcript records an ``mcp__echo__secret_word``
call. Deliberately hand-rolled (no ``mcp`` SDK) to keep caliper's footprint at
zero extra dependencies and to double as a worked example of what a minimal
stdio MCP server looks like.
"""

from __future__ import annotations

import json
import sys

PROTOCOL_VERSION = "2024-11-05"

TOOLS = [
    {
        "name": "secret_word",
        "description": "Returns the secret word.",
        "inputSchema": {"type": "object", "properties": {}},
    }
]


def _result(request_id: object, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: object, code: int, message: str) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


def _handle(request: dict) -> dict | None:
    method = request.get("method")
    request_id = request.get("id")

    # Notifications carry no id and expect no response.
    if request_id is None:
        return None

    if method == "initialize":
        return _result(
            request_id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "echo", "version": "0.1.0"},
            },
        )

    if method == "tools/list":
        return _result(request_id, {"tools": TOOLS})

    if method == "tools/call":
        name = (request.get("params") or {}).get("name")
        if name == "secret_word":
            return _result(
                request_id, {"content": [{"type": "text", "text": "caliper"}]}
            )
        return _error(request_id, -32602, f"unknown tool: {name}")

    return _error(request_id, -32601, f"method not found: {method}")


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue
        response = _handle(request)
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
