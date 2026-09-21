"""Deterministic ordered progress for natural-language browser tasks."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

from .models import ActionKind, BrowserSnapshot, CandidateAction

STEP_BOUNDARY = re.compile(
    r"(?:,\s*|\s+)(?:and\s+then|then|and)\s+"
    r"(?=(?:go|open|click|select|choose|find|get|visit|navigate|search|show|read|view|"
    r"submit|enter|type|press|pick)\b)",
    re.IGNORECASE,
)
URL_PATTERN = re.compile(r"https?://[^\s,]+", re.IGNORECASE)
DOMAIN_PATTERN = re.compile(
    r"(?<![@\w])((?:www\.)?[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+)",
    re.IGNORECASE,
)
NAVIGATION_PREFIX = re.compile(r"^\s*(?:go|visit|navigate)\b", re.IGNORECASE)


def split_task_steps(goal: str) -> tuple[str, ...]:
    """Split explicit ordered action clauses without inventing a plan."""
    steps = tuple(part.strip(" ,.;") for part in STEP_BOUNDARY.split(goal) if part.strip(" ,.;"))
    return steps or (goal.strip(),)


def _requested_hostname(step: str) -> str | None:
    match = URL_PATTERN.search(step)
    if match:
        return urlparse(match.group(0).rstrip(".,;:!?)]}")).hostname
    match = DOMAIN_PATTERN.search(step)
    return match.group(1).lower().removeprefix("www.") if match else None


def _navigation_step_is_visible(step: str, snapshot: BrowserSnapshot) -> bool:
    if not NAVIGATION_PREFIX.search(step):
        return False
    requested = _requested_hostname(step)
    current = (urlparse(snapshot.url).hostname or "").lower().removeprefix("www.")
    return bool(requested and current == requested)


@dataclass
class TaskProgress:
    steps: tuple[str, ...]
    index: int = 0

    @classmethod
    def from_goal(cls, goal: str) -> TaskProgress:
        return cls(split_task_steps(goal))

    @property
    def complete(self) -> bool:
        return self.index >= len(self.steps)

    @property
    def current_step(self) -> str | None:
        return None if self.complete else self.steps[self.index]

    @property
    def completed_steps(self) -> tuple[str, ...]:
        return self.steps[: self.index]

    def sync_visible_state(self, snapshot: BrowserSnapshot) -> None:
        while not self.complete and _navigation_step_is_visible(
            self.steps[self.index], snapshot
        ):
            self.index += 1

    def record_verified_action(
        self,
        action: CandidateAction,
        before: BrowserSnapshot,
        after: BrowserSnapshot,
    ) -> None:
        semantic_actions = {
            ActionKind.CLICK,
            ActionKind.FILL,
            ActionKind.PRESS_ENTER,
            ActionKind.SELECT,
        }
        if (
            self.complete
            or action.kind not in semantic_actions
            or before.fingerprint == after.fingerprint
            or not action.goal_match
        ):
            return
        self.index += 1
        self.sync_visible_state(after)
