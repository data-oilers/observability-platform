import pytest

from obs_backend.config import (
    ENVIRONMENTS,
    PLATFORM_NAMESPACES,
    project_for,
    watched_namespaces,
)


def test_known_envs_map_to_projects():
    assert project_for("qa") == "itmind-macro-ai-qa-0"
    assert project_for("prod") == "itmind-macro-ai-prod-0"


def test_unknown_env_raises():
    with pytest.raises(KeyError):
        project_for("staging")


def test_environments_listed():
    assert set(ENVIRONMENTS) == {"dev", "qa", "prod"}


def test_platform_namespaces_are_fixed_set():
    """Set fijo de infra compartida; excluye a propósito los namespaces GKE-owned."""
    assert PLATFORM_NAMESPACES == frozenset({"external-secrets", "kyverno", "kube-system"})
    assert not any(ns.startswith("gke-managed") for ns in PLATFORM_NAMESPACES)
    assert "gmp-system" not in PLATFORM_NAMESPACES


def test_watched_namespaces_is_app_plus_platform():
    """El radar observa los namespaces de app (por entorno) + el set fijo de plataforma."""
    for env in ("dev", "qa", "prod"):
        watched = watched_namespaces(env)
        assert set(ENVIRONMENTS[env]["namespaces"]) <= watched
        assert PLATFORM_NAMESPACES <= watched


def test_watched_namespaces_unknown_env_raises():
    with pytest.raises(KeyError):
        watched_namespaces("staging")
