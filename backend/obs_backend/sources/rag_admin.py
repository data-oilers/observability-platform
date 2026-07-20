"""Read-only proxy to the RAG admin/analytics/governance HTTP API.

Generic by design: one authenticated GET that unwraps the RAG {data,error,meta}
envelope. Defensive — any error or unexpected shape degrades to None so the
observed system can never break obs.
"""
from typing import Callable

import httpx

from obs_backend.config import ENVIRONMENTS
from obs_backend.sources._rag import default_rag_client_factory, rag_token_for


class RagAdminSource:
    def __init__(
        self,
        client_factory: Callable[[str, str | None], httpx.Client] = default_rag_client_factory,
        token_for: Callable[[str], str | None] = rag_token_for,
    ):
        self._client_factory = client_factory
        self._token_for = token_for

    def get(self, env: str, path: str, params: dict | None = None) -> object:
        base_url = ENVIRONMENTS[env]["rag_base_url"]
        token = self._token_for(env)
        try:
            with self._client_factory(base_url, token) as client:
                resp = client.get(path, params=params or {})
                resp.raise_for_status()
                payload = resp.json()
        except (httpx.HTTPError, ValueError):
            return None
        if not isinstance(payload, dict):
            return None
        return payload.get("data")
