"""Deterministic setup for task-first inspector runs."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

from .task_progress import split_task_steps

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
EMAIL_PATTERN = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
PHONE_PATTERN = re.compile(r"(?<!\w)(\+?\d[\d ()-]{6,}\d)(?!\w)")
NAME_PATTERN = re.compile(
    r"\bname(?:\s+is|[:=])?\s+([a-z][a-z .'-]{0,78})(?=,|;|\s+and\b|$)",
    re.IGNORECASE,
)
SUBMIT_SEQUENCE_PATTERN = re.compile(
    r"\bsubmit\s+([a-z][a-z .'-]{1,79}?)\s*,\s*(\+?\d[\d ()-]{6,}\d)",
    re.IGNORECASE,
)
LINE_ID_PATTERNS = (
    re.compile(r"\b([a-z0-9_.@-]{2,80})\s+line\s+id\b", re.IGNORECASE),
    re.compile(r"\bline\s+id\s*(?:is|:|=)\s*([a-z0-9_.@-]{2,80})", re.IGNORECASE),
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
    for step in split_task_steps(goal):
        match = SEARCH_PATTERN.search(step)
        if match:
            value = match.group(1).strip(" \t\r\n\"'.,;:!?")
            return value or None
    return None


def _form_inputs(goal: str) -> dict[str, str]:
    """Extract only explicit personal/form values; never invent missing data."""
    values: dict[str, str] = {}
    sequence = SUBMIT_SEQUENCE_PATTERN.search(goal)
    name = NAME_PATTERN.search(goal)
    phone = PHONE_PATTERN.search(goal)
    email = EMAIL_PATTERN.search(goal)
    if sequence:
        values["name"] = sequence.group(1).strip()
        values["phone"] = re.sub(r"[^+\d]", "", sequence.group(2))
    else:
        if name:
            values["name"] = name.group(1).strip()
        if phone:
            values["phone"] = re.sub(r"[^+\d]", "", phone.group(1))
    if email:
        values["email"] = email.group(0)

    for pattern in LINE_ID_PATTERNS:
        match = pattern.search(goal)
        if match:
            values["line id"] = match.group(1)
            break

    if "phone" in values and re.search(
        r"\b(?:whatsapp.{0,24}same|same.{0,24}whatsapp)\b", goal, re.IGNORECASE
    ):
        values["whatsapp number"] = values["phone"]

    intent = re.search(
        r"\b(?:looking\s+to|choose|select)\s+(?:looking\s+to\s+)?(rent|sell)\b",
        goal,
        re.IGNORECASE,
    )
    if intent:
        values["rent or sell"] = intent.group(1).title()
    return values


def plan_task(goal: str, *, continuation: bool = False) -> TaskSetup:
    """Choose a starting page and literal task-supplied text without model generation."""
    goal = goal.strip()
    if not goal:
        raise ValueError("goal must not be empty")

    destination = _explicit_destination(goal)
    form_inputs = _form_inputs(goal)
    search_text = _search_text(goal)
    if search_text:
        form_inputs["search query"] = search_text
    if destination:
        parsed = urlparse(destination)
        if parsed.scheme in {"http", "https"} and parsed.hostname:
            return TaskSetup(destination, form_inputs)
    if search_text:
        return TaskSetup(GOOGLE_START_URL, form_inputs)
    if continuation:
        return TaskSetup(GOOGLE_START_URL, form_inputs)
    return TaskSetup(GOOGLE_START_URL, {"search query": goal})
