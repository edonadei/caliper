#!/usr/bin/env python3
"""An authoritative deployment-window lookup, served as a stdio MCP server.

The one source of truth the demo agent is asked to use:

    lookup_deployment_window(service) -> JSON record

The records are deliberately specific (an odd window, a change ticket, a freeze
reason) so an answer can only be right by using the tool result, and a
classify: check can tell a grounded answer from a paraphrase that drifted.
Hand-rolled JSON-RPC 2.0 over stdin/stdout, no dependencies.
"""

from __future__ import annotations

import json
import sys

PROTOCOL_VERSION = "2024-11-05"

WINDOWS = {
    "checkout-api": {
        "service": "checkout-api",
        "status": "scheduled",
        "window_start": "2026-09-24T22:15:00Z",
        "window_end": "2026-09-24T23:40:00Z",
        "change_ticket": "CHG-4471",
        "rollback_plan": "blue/green switch back to v5.18.2",
    },
    "billing-worker": {
        "service": "billing-worker",
        "status": "frozen",
        "window_start": None,
        "window_end": None,
        "freeze_reason": "quarter-end close",
        "freeze_until": "2026-10-02T06:00:00Z",
    },
}

TOOLS = [
    {
        "name": "lookup_deployment_window",
        "description": (
            "The authoritative deployment calendar. Returns the approved "
            "deployment window (UTC) for a service, or its freeze."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"service": {"type": "string"}},
            "required": ["service"],
        },
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


def _lookup(arguments: dict) -> str:
    service = str(arguments.get("service", "")).strip()
    record = WINDOWS.get(service, {"service": service, "status": "unknown"})
    return json.dumps(record)


def _handle(request: dict) -> dict | None:
    method = request.get("method")
    request_id = request.get("id")
    if request_id is None:
        return None
    if method == "initialize":
        return _result(
            request_id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "deployments", "version": "0.1.0"},
            },
        )
    if method == "tools/list":
        return _result(request_id, {"tools": TOOLS})
    if method == "tools/call":
        params = request.get("params") or {}
        if params.get("name") == "lookup_deployment_window":
            text = _lookup(params.get("arguments") or {})
            return _result(request_id, {"content": [{"type": "text", "text": text}]})
        return _error(request_id, -32602, f"unknown tool: {params.get('name')}")
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
