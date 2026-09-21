"""Deterministic ordered progress for natural-language browser tasks."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from urllib.parse import urlparse

from .models import ActionKind, BrowserSnapshot, CandidateAction, ModelDecision, StepRecord

ACTION_VERBS = (
    r"go|open|click|select|choose|find|get|visit|navigate|search|show|read|view|fill|"
    r"submit|enter|type|press|pick|like|dislike|follow|unfollow|play|pause|save|"
    r"download|upload|send|share|add|remove|create|delete|change|update|set|inquire|"
    r"sign\s+in|log\s+in"
)
STEP_BOUNDARY = re.compile(
    r"(?:\s*[,;]\s*|\s+(?:and\s+then|then|and)\s+)"
    rf"(?=(?:{ACTION_VERBS})\b)",
    re.IGNORECASE,
)
URL_PATTERN = re.compile(r"https?://[^\s,]+", re.IGNORECASE)
DOMAIN_PATTERN = re.compile(
    r"(?<![@\w])((?:www\.)?[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+)",
    re.IGNORECASE,
)
NAVIGATION_PREFIX = re.compile(r"^\s*(?:go|visit|navigate)\b", re.IGNORECASE)


def step_requests_submission(step: str) -> bool:
    return bool(
        re.search(r"\b(?:search|find|lookup|submit)\b", step, re.IGNORECASE)
    )


def split_task_steps(goal: str) -> tuple[str, ...]:
    """Split explicit ordered action clauses without inventing a plan."""
    raw = [part.strip(" ,.;") for part in STEP_BOUNDARY.split(goal) if part.strip(" ,.;")]
    steps: list[str] = []
    for part in raw:
        if steps and re.match(r"^(?:submit|select|choose)\b", part, re.IGNORECASE) and re.match(
            r"^fill\b", steps[-1], re.IGNORECASE
        ):
            steps[-1] += f" and {part}"
        else:
            steps.append(part)
    return tuple(steps) or (goal.strip(),)


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


def summarize_page_change(before: BrowserSnapshot, after: BrowserSnapshot) -> str:
    """Compact code-owned evidence for the next local decision batch."""
    facts: list[str] = []
    if before.url != after.url:
        facts.append(f"URL changed: {before.url} -> {after.url}")
    old = {item.element_id: item for item in before.elements}
    new = {item.element_id: item for item in after.elements}
    changed: list[str] = []
    for element_id in old.keys() & new.keys():
        left, right = old[element_id], new[element_id]
        state = []
        for name in ("value", "checked", "pressed", "selected", "expanded", "busy"):
            if getattr(left, name) != getattr(right, name):
                state.append(f"{name}={getattr(left, name)!r}->{getattr(right, name)!r}")
        if state:
            changed.append(f"{right.name or right.role}: {', '.join(state)}")
    if changed:
        facts.append("Changed controls: " + "; ".join(changed[:6]))
    added = [item.name or item.role for key, item in new.items() if key not in old]
    removed = [item.name or item.role for key, item in old.items() if key not in new]
    if added:
        facts.append("Added controls: " + ", ".join(added[:6]))
    if removed:
        facts.append("Removed controls: " + ", ".join(removed[:6]))
    return " | ".join(facts) or "No semantic page change observed"


@dataclass
class TaskProgress:
    steps: tuple[str, ...]
    index: int = 0
    history_boundary: int = 0

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

    def sync_visible_state(
        self, snapshot: BrowserSnapshot, *, action_count: int = 0
    ) -> None:
        while not self.complete and _navigation_step_is_visible(
            self.steps[self.index], snapshot
        ):
            self.index += 1
            self.history_boundary = action_count

    def advance_current_step(
        self, snapshot: BrowserSnapshot, *, action_count: int = 0
    ) -> None:
        if self.complete:
            return
        self.index += 1
        self.history_boundary = action_count
        self.sync_visible_state(snapshot, action_count=action_count)


def step_completion_disposition(
    progress: TaskProgress,
    decision: ModelDecision,
    history: Sequence[StepRecord],
) -> str:
    """Return verified, confirm, or pending for the active atomic outcome."""
    if not progress.current_step:
        return "pending"
    has_current_step_action = len(history) > progress.history_boundary
    latest_supports_step = False
    if has_current_step_action:
        latest = history[-1]
        if "visible calendar range" in latest.description:
            return "pending"
        generic = {
            "a", "an", "and", "change", "choose", "click", "enter", "go", "in",
            "it", "navigate", "of", "on", "open", "press", "select", "set", "the",
            "then", "to", "update", "visit",
        }
        step_terms = {
            token
            for token in re.findall(r"[a-z0-9]+", progress.current_step.casefold())
            if len(token) > 1 and token not in generic
        }
        evidence = " ".join((latest.description, latest.change_summary or "")).casefold()
        evidence_terms = set(re.findall(r"[a-z0-9]+", evidence))
        latest_supports_step = not step_terms or bool(step_terms & evidence_terms)
        if (
            latest.action_kind == ActionKind.CLICK.value
            and latest.changed
            and latest_supports_step
            and latest.change_summary
            and "URL changed:" in latest.change_summary
            and re.match(
                r"^\s*(?:click|open|visit|navigate|go)\b",
                progress.current_step,
                re.IGNORECASE,
            )
        ):
            return "verified"
    probability = max(
        decision.step_completion_probability,
        decision.step_completion_change_probability,
    )
    if (
        has_current_step_action
        and history[-1].action_kind == ActionKind.FILL.value
        and step_requests_submission(progress.current_step)
    ):
        return "pending"
    threshold = 0.85 if has_current_step_action else 0.9
    if probability >= threshold and latest_supports_step:
        return "verified"
    if not has_current_step_action or probability < 0.5:
        return "pending"
    latest = history[-1]
    if not latest_supports_step:
        return "pending"
    if (
        latest.changed
        and latest.action_kind in {"click", "fill", "press_enter", "select"}
    ):
        return "confirm"
    return "pending"


def login_blocks_progress(
    decision: ModelDecision, actions: Sequence[CandidateAction]
) -> bool:
    """Treat authentication as blocking only when no direct semantic action remains."""
    if decision.login_probability < 0.7:
        return False
    semantic_kinds = {
        ActionKind.CLICK,
        ActionKind.FILL,
        ActionKind.PRESS_ENTER,
        ActionKind.SELECT,
    }
    return not any(
        action.goal_match and action.kind in semantic_kinds for action in actions
    )


def error_blocks_progress(
    decision: ModelDecision,
    snapshot: BrowserSnapshot,
    actions: Sequence[CandidateAction],
) -> bool:
    """Require browser-owned rejection evidence before treating an error as terminal."""
    if decision.error_probability < 0.8 or not snapshot.alerts:
        return False
    semantic_kinds = {
        ActionKind.CLICK,
        ActionKind.FILL,
        ActionKind.PRESS_ENTER,
        ActionKind.SELECT,
    }
    return not any(
        action.goal_match and action.kind in semantic_kinds for action in actions
    )
