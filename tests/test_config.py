import pytest

from laya_browser.config import RunConfig, default_model_path


def test_start_domain_is_default_allowlist():
    config = RunConfig(goal="Read docs", start_url="https://example.com/start")
    assert config.effective_allowed_domains == frozenset({"example.com"})


def test_rejects_invalid_url():
    with pytest.raises(ValueError, match="absolute HTTP"):
        RunConfig(goal="Read docs", start_url="file:///tmp/page.html")


def test_explicit_allowlist_is_preserved():
    config = RunConfig(
        goal="Read docs",
        start_url="https://example.com",
        allowed_domains=frozenset({"example.com", "docs.example.com"}),
    )
    assert config.effective_allowed_domains == frozenset({"example.com", "docs.example.com"})


def test_model_environment_override_is_honored(monkeypatch):
    monkeypatch.setenv("LAYA_MODEL_PATH", "/models/pinned-laya")
    assert default_model_path() == "/models/pinned-laya"


def test_rejects_relative_success_url_prefix():
    with pytest.raises(ValueError, match="absolute HTTP"):
        RunConfig(
            goal="Read docs",
            start_url="https://example.com",
            success_url_prefix="/docs",
        )
