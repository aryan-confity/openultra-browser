import pytest

from openultra_browser.config import RunConfig, default_model_path


def test_start_domain_is_default_allowlist():
    config = RunConfig(goal="Read docs", start_url="https://example.com/start")
    assert config.effective_allowed_domains == frozenset({"example.com", "www.example.com"})


def test_rejects_invalid_url():
    with pytest.raises(ValueError, match="absolute HTTP"):
        RunConfig(goal="Read docs", start_url="file:///tmp/page.html")


def test_explicit_allowlist_is_preserved():
    config = RunConfig(
        goal="Read docs",
        start_url="https://example.com",
        allowed_domains=frozenset({"example.com", "docs.example.com"}),
    )
    assert config.effective_allowed_domains == frozenset(
        {"example.com", "www.example.com", "docs.example.com", "www.docs.example.com"}
    )
    assert config.preferred_domains == frozenset({"docs.example.com", "www.docs.example.com"})


def test_www_start_domain_allows_apex_alias():
    config = RunConfig(goal="Search", start_url="https://www.google.com")
    assert "google.com" in config.effective_allowed_domains


def test_url_regex_verifier_matches_listing_but_not_index():
    config = RunConfig(
        goal="Open a listing",
        start_url="https://wdxproperties.com/properties/rent/Bangkok",
        success_url_regex=r"^https://wdxproperties\.com/properties/(?!rent/|buy/)[^/?#]+$",
    )

    assert config.matches_success_url(
        "https://wdxproperties.com/properties/2-br-condo-circle-condominium-356560"
    )
    assert not config.matches_success_url(
        "https://wdxproperties.com/properties/rent/Bangkok"
    )


def test_invalid_url_regex_is_rejected():
    with pytest.raises(ValueError, match="valid regular expression"):
        RunConfig(
            goal="Open a listing",
            start_url="https://example.com",
            success_url_regex="[",
        )


def test_model_environment_override_is_honored(monkeypatch):
    monkeypatch.setenv("OPENULTRA_MODEL_PATH", "/models/pinned-laya")
    assert default_model_path() == "/models/pinned-laya"


def test_rejects_relative_success_url_prefix():
    with pytest.raises(ValueError, match="absolute HTTP"):
        RunConfig(
            goal="Read docs",
            start_url="https://example.com",
            success_url_prefix="/docs",
        )


def test_semif_candidate_limit_is_checked_before_browser_start():
    with pytest.raises(ValueError, match="at most 26 candidates"):
        RunConfig(
            goal="Open a result",
            start_url="https://example.com",
            model="semif-mlx",
            max_candidates=27,
        )
    assert RunConfig(
        goal="Open a result",
        start_url="https://example.com",
        model="semif-mlx",
        max_candidates=26,
    ).max_candidates == 26
