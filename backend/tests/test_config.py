import pytest

from obs_backend.config import ENVIRONMENTS, project_for


def test_known_envs_map_to_projects():
    assert project_for("qa") == "itmind-macro-ai-qa-0"
    assert project_for("prod") == "itmind-macro-ai-prod-0"


def test_unknown_env_raises():
    with pytest.raises(KeyError):
        project_for("staging")


def test_environments_listed():
    assert set(ENVIRONMENTS) == {"qa", "prod"}
