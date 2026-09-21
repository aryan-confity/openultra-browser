"""Deterministic setup for task-first inspector runs."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

GOOGLE_START_URL = "https://www.google.com/?hl=en"

URL_PATTERN = re.compile(r"https?://[^\s<>'\"]+", re.IGNORECASE)
DOMAIN_PATTERN = re.compile(
    r"(?<![@\w])((?:www\.)?[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+(?::\d{2,5})?"
    r"(?:/[^\s<>'\"]*)?)",
    re.IGNORECASE,
)
SEARCH_PATTERN = re.compile(
    r"\b(?:search(?:\s+(?:google|the\s+web))?\s+for|look\s+up)\s+(.+?)"
    r"(?=\s*(?:,|\bthen\b|\band\s+(?:open|go|click|find|show|visit|select)\b|$))",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class TaskSetup:
    start_url: str
    prepared_inputs: dict[str, str] = field(default_factory=dict)


def _clean_destination(value: str) -> str:
    return value.rstrip(".,;:!?)]}")


def _explicit_destination(goal: str) -> str | None:
    match = URL_PATTERN.search(goal)
    if match:
        return _clean_destination(match.group(0))
    match = DOMAIN_PATTERN.search(goal)
    if not match:
        return None
    destination = _clean_destination(match.group(1))
    return f"https://{destination}"


def _search_text(goal: str) -> str | None:
    match = SEARCH_PATTERN.search(goal)
    if match:
        value = match.group(1).strip(" \t\r\n\"'.,;:!?")
        return value or None
    return None


def plan_task(goal: str) -> TaskSetup:
    """Choose a starting page and literal task-supplied text without model generation."""
    goal = goal.strip()
    if not goal:
        raise ValueError("goal must not be empty")

    search_text = _search_text(goal)
    asks_for_search = search_text is not None
    destination = _explicit_destination(goal)
    if asks_for_search:
        return TaskSetup(GOOGLE_START_URL, {"search query": search_text})
    if destination:
        parsed = urlparse(destination)
        if parsed.scheme in {"http", "https"} and parsed.hostname:
            return TaskSetup(destination)
    return TaskSetup(GOOGLE_START_URL, {"search query": goal})
