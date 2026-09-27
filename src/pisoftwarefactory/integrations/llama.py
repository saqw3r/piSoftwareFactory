"""llama.cpp backend probe (ADR-0010).

``sfactory init`` and ``sfactory doctor`` call :func:`probe_backend` to
autofill the real model id and context window instead of guessing, and to
verify the server reports the configured context size.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx


@dataclass
class BackendInfo:
    base_url: str
    models: list[str] = field(default_factory=list)
    context_window: int | None = None
    server_version: str | None = None

    @property
    def healthy(self) -> bool:
        return bool(self.models)


def probe_backend(base_url: str, timeout: float = 5.0) -> BackendInfo:
    """Query ``GET {base_url}/models`` (OpenAI-compatible) on a llama-server.

    llama.cpp extends each model entry with a ``meta`` object that carries
    ``n_ctx``; we surface the first model's value as the context window.
    Raises :class:`httpx.HTTPError` on connectivity failures.
    """
    url = f"{base_url.rstrip('/')}/models"
    with httpx.Client(timeout=timeout) as client:
        resp = client.get(url)
        resp.raise_for_status()
        payload = resp.json()

    data = payload.get("data") or payload.get("models") or []
    models = [
        str(entry.get("id") or entry.get("name") or "")
        for entry in data
        if entry.get("id") or entry.get("name")
    ]

    context_window: int | None = None
    for entry in data:
        meta = entry.get("meta") or {}
        if isinstance(meta.get("n_ctx"), int):
            context_window = meta["n_ctx"]
            break

    version = payload.get("server_version") or resp.headers.get("server")
    return BackendInfo(
        base_url=base_url,
        models=models,
        context_window=context_window,
        server_version=version,
    )
