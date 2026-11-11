from importlib import metadata

from headline_tuner import environment
from headline_tuner.environment import _version_tuple, environment_problems


def test_project_environment_is_ok():
    assert environment_problems() == []


def test_old_sdk_is_reported(monkeypatch):
    real = metadata.version
    monkeypatch.setattr(environment.metadata, "version",
                        lambda p: "0.125.0" if p == "anthropic" else real(p))
    problems = environment_problems()
    assert problems == ["anthropic 0.125.0 is older than 2.8, so analysis requests will fail"]


def test_version_parsing():
    assert _version_tuple("1.67.0") == (1, 67, 0) and _version_tuple("2.8.0rc1") >= (2, 8)
