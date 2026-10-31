"""Hindsight memory client (ADR-0005).

Uses the MCP endpoint every Hindsight server ships per bank
(``{url}/mcp/{bank_id}/``) so retain/recall/reflect work with any recent
Hindsight version. Degrades gracefully: when the server is unreachable the
pipeline keeps running and memory files (the source of truth) stay complete.
"""

from __future__ import annotations

import json

import httpx

_MCP_PROTOCOL_VERSION = "2025-06-18"


class HindsightError(RuntimeError):
    pass


def _decode_body(resp: httpx.Response) -> dict:
    """Decode a JSON-RPC body that may arrive as SSE or plain JSON.

    We advertise `Accept: application/json, text/event-stream`, so servers
    may answer with SSE frames (`event: …` / `data: {…}`) that `resp.json()`
    chokes on (`Expecting value` at char 0). Unwrap the first JSON data
    frame; fall back to plain JSON.
    """
    text = resp.text
    if "text/event-stream" in resp.headers.get("content-type", "") or text.lstrip().startswith("event:"):
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                frame = line[5:].strip()
                if frame and frame != "[DONE]":
                    try:
                        parsed = json.loads(frame)
                    except ValueError:
                        continue
                    if isinstance(parsed, dict):
                        return parsed
        raise HindsightError("hindsight returned SSE with no JSON data frame")
    return resp.json()


def _rpc(client: httpx.Client, url: str, payload: dict) -> dict:
    resp = client.post(url, json=payload, headers={"Accept": "application/json, text/event-stream"})
    resp.raise_for_status()
    try:
        body = _decode_body(resp)
    except ValueError as exc:
        raise HindsightError(f"hindsight returned a non-JSON body: {exc}") from exc
    if body.get("error"):
        raise HindsightError(str(body["error"]))
    return body


def _ensure_session(client: httpx.Client, url: str) -> str | None:
    """Initialize the MCP session; returns the mcp-session-id header if issued."""
    init = _rpc(
        client,
        url,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": _MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "sfactory", "version": "0.1.0"},
            },
        },
    )
    session = init.get("id") and None  # session id arrives via header, not body
    client.post(
        url,
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        headers={"Accept": "application/json, text/event-stream"},
    )
    return session


def _call_tool(base_url: str, bank_id: str, tool: str, arguments: dict, timeout: float = 30.0) -> dict | None:
    url = f"{base_url.rstrip('/')}/mcp/{bank_id}/"
    with httpx.Client(timeout=timeout) as client:
        try:
            _ensure_session(client, url)
        except httpx.HTTPError as exc:
            raise HindsightError(f"hindsight unreachable at {base_url}: {exc}") from exc
        result = _rpc(
            client,
            url,
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": tool, "arguments": arguments}},
        )
    return result.get("result")


def retain(base_url: str, bank_id: str, content: str, context: str = "factory reflection") -> bool:
    """Store a memory; returns False (with no exception) when the server is absent or misbehaving."""
    try:
        _call_tool(base_url, bank_id, "retain", {"items": [{"content": content, "context": context}]})
        return True
    except (HindsightError, httpx.HTTPError, ValueError):
        return False


def recall(base_url: str, bank_id: str, query: str, top_k: int = 5) -> list[str]:
    """Recall memories as short strings; [] when the server is absent or misbehaving."""
    try:
        result = _call_tool(base_url, bank_id, "recall", {"query": query, "max_length": top_k})
    except (HindsightError, httpx.HTTPError, ValueError):
        return []
    if not result:
        return []
    texts: list[str] = []
    for item in result.get("content") or []:
        if isinstance(item, dict) and item.get("text"):
            texts.append(str(item["text"]))
    return texts[:top_k]


def is_up(base_url: str, timeout: float = 3.0) -> bool:
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(base_url.rstrip("/"))
            return resp.status_code < 500
    except httpx.HTTPError:
        return False
