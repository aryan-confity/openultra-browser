"""Runtime configuration and input validation."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse


def default_model_path() -> str:
    configured = os.environ.get("LAYA_MODEL_PATH", "").strip()
    if configured:
        return configured
    sibling = Path(__file__).resolve().parents[3] / "laya-mlx/models/hub/laya-mlx"
    if sibling.is_dir():
        return str(sibling)
    return "aac6fef/laya-mlx"


@dataclass(frozen=True)
class RunConfig:
    goal: str
    start_url: str
    model: str = field(default_factory=default_model_path)
    allowed_domains: frozenset[str] = field(default_factory=frozenset)
    prepared_inputs: dict[str, str] = field(default_factory=dict)
    success_text: str | None = None
    success_url_prefix: str | None = None
    success_url_regex: str | None = None
    max_steps: int = 20
    max_seconds: float = 60
    max_candidates: int = 18
    visible_text_chars: int = 2_000
    keep_open_seconds: float = 0
    allow_risky: bool = False
    optimize: bool = False
    trace_path: Path | None = None

    def __post_init__(self) -> None:
        if not self.goal.strip():
            raise ValueError("goal must not be empty")
        parsed = urlparse(self.start_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("start_url must be an absolute HTTP(S) URL")
        if self.max_steps < 1:
            raise ValueError("max_steps must be positive")
        if self.max_seconds <= 0:
            raise ValueError("max_seconds must be positive")
        if not 6 <= self.max_candidates <= 40:
            raise ValueError("max_candidates must be between 6 and 40")
        if self.success_url_prefix:
            success_url = urlparse(self.success_url_prefix)
            if success_url.scheme not in {"http", "https"} or not success_url.hostname:
                raise ValueError("success_url_prefix must be an absolute HTTP(S) URL")
        if self.success_url_regex:
            try:
                re.compile(self.success_url_regex)
            except re.error as error:
                raise ValueError("success_url_regex must be a valid regular expression") from error

    def matches_success_url(self, url: str) -> bool:
        prefix_matches = not self.success_url_prefix or url.startswith(self.success_url_prefix)
        regex_matches = not self.success_url_regex or bool(re.search(self.success_url_regex, url))
        return prefix_matches and regex_matches

    @property
    def effective_allowed_domains(self) -> frozenset[str]:
        hostname = urlparse(self.start_url).hostname
        domains = {hostname, *self.allowed_domains}
        aliases = {
            domain.removeprefix("www.") if domain.startswith("www.") else f"www.{domain}"
            for domain in domains
        }
        return frozenset({*domains, *aliases})

    @property
    def preferred_domains(self) -> frozenset[str]:
        """Explicit non-start domains that can advance a cross-site task."""
        hostname = urlparse(self.start_url).hostname
        start_aliases = {
            hostname,
            hostname.removeprefix("www.") if hostname.startswith("www.") else f"www.{hostname}",
        }
        return self.effective_allowed_domains - start_aliases
