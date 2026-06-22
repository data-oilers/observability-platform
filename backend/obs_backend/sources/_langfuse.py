"""Shared utilities for Langfuse-based sources."""
import os

import httpx


def build_auth() -> tuple[str, str] | None:
    """Reads LANGFUSE_OBS_KEY, strips whitespace, splits on first ':'.
    Returns None if empty. Raises ValueError if non-empty and no ':' found.
    """
    raw_key = os.environ.get("LANGFUSE_OBS_KEY", "").strip()
    if not raw_key:
        return None
    if ":" not in raw_key:
        raise ValueError("LANGFUSE_OBS_KEY debe tener formato 'public:secret'")
    public, secret = raw_key.split(":", 1)
    return (public, secret)


def default_client_factory(base_url: str, auth: tuple[str, str] | None) -> httpx.Client:
    # Langfuse self-hosted: /api/public/traces tarda ~5s, justo en el borde del
    # default de httpx (5s) -> ReadTimeout intermitente. Timeout holgado y
    # tuneable por env (LANGFUSE_TIMEOUT_SECONDS) sin rebuild.
    timeout = float(os.environ.get("LANGFUSE_TIMEOUT_SECONDS", "15"))
    return httpx.Client(base_url=base_url, auth=auth, timeout=timeout)
