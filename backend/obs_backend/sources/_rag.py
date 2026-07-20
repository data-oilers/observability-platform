"""Shared utilities for the read-only RAG admin proxy source.

The RAG accepts auth ONLY via the ``access_token`` cookie (no Bearer). obs holds
a service-JWT per environment (seeded service-user with the union of admin roles)
and sends it as that cookie. The token is server-side only and never returned to
the obs frontend.
"""
import os

import httpx


def rag_token_for(env: str) -> str | None:
    """Return the RAG service-JWT for ``env`` from RAG_OBS_TOKEN_<ENV>, or None."""
    raw = os.environ.get(f"RAG_OBS_TOKEN_{env.upper()}", "").strip()
    return raw or None


def default_rag_client_factory(base_url: str, token: str | None) -> httpx.Client:
    timeout = float(os.environ.get("RAG_OBS_TIMEOUT_SECONDS", "15"))
    headers = {"Cookie": f"access_token={token}"} if token else {}
    return httpx.Client(base_url=base_url, headers=headers, timeout=timeout)
