"""Deterministic ordered progress for natural-language browser tasks."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from urllib.parse import urlparse

from .models import ActionKind, BrowserSnapshot, CandidateAction, ModelDecision, StepRecord

ACTION_VERBS = (
    r"go|open|click|select|choose|find|get|visit|navigate|search|show|read|view|fill|"
    r"submit|enter|type|press|pick|like|dislike|follow|unfollow|play|pause|save|"
    r"download|upload|send|share|add|remove|create|delete|change|update|set|inquire|"
    r"scroll|skip|sign\s+in|log\s+in"
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
NAVIGATION_PREFIX = re.compile(r"^\s*(?:go|open|visit|navigate)\b", re.IGNORECASE)
AUTHENTICATION_PATH = re.compile(
    r"/(?:account/)?(?:auth|login|log-in|signin|sign-in|signup|sign-up|oauth|authorize)(?:/|$)",
    re.IGNORECASE,
)
AUTHENTICATION_TITLE = re.compile(
    r"^\s*(?:sign|log)\s+(?:in|up)\b|^\s*(?:create|verify)\s+(?:an?\s+)?account\b|"
    r"^\s*authentication\b",
    re.IGNORECASE,
)
SKIP_AD_REQUEST = re.compile(r"\bskip\s+(?:the\s+)?(?:ads?|advertisements?)\b", re.IGNORECASE)
SKIP_AD_CONTROL = re.compile(
    r"\bskip\s+(?:this\s+|the\s+)?(?:ads?|advertisements?)\b", re.IGNORECASE
)


def step_requests_submission(step: str) -> bool:
    return bool(re.search(r"\b(?:search|find|lookup|submit)\b", step, re.IGNORECASE))


def planned_search_evidence(
    goal: str, prepared_inputs: dict[str, str], snapshot: BrowserSnapshot
) -> bool:
    """Do not equate opening a flight-search page with obtaining requested results."""
    required = {key: prepared_inputs.get(key) for key in ("where from", "where to", "departure")}
    if "flight" not in goal.casefold() or not all(required.values()):
        return True
    values = {element.name.casefold(): element.value.casefold() for element in snapshot.elements}
    if not all(
        any(key in label and expected.casefold() in value for label, value in values.items())
        for key, expected in required.items()
    ):
        return False
    text = snapshot.visible_text.casefold()
    return "top departing flights" in text


def requested_scroll_direction(step: str) -> ActionKind | None:
    """Use the latest explicit direction, including in spoken corrections."""
    matches = list(re.finditer(r"\bscroll\s+(up|down)\b", step, re.IGNORECASE))
    if not matches:
        return None
    return ActionKind.SCROLL_UP if matches[-1].group(1).lower() == "up" else ActionKind.SCROLL_DOWN


def requests_skip_ad(step: str) -> bool:
    return bool(SKIP_AD_REQUEST.search(step))


def split_task_steps(goal: str) -> tuple[str, ...]:
    """Split explicit ordered action clauses without inventing a plan."""
    raw = [part.strip(" ,.;") for part in STEP_BOUNDARY.split(goal) if part.strip(" ,.;")]
    steps: list[str] = []
    for part in raw:
        merge_form_step = bool(
            steps
            and re.match(r"^(?:submit|select|choose)\b", part, re.IGNORECASE)
            and re.match(r"^fill\b", steps[-1], re.IGNORECASE)
        )
        merge_search_step = bool(
            steps
            and re.match(r"^click\s+(?:on\s+)?search\b", part, re.IGNORECASE)
            and re.match(r"^search\b", steps[-1], re.IGNORECASE)
        )
        if merge_form_step or merge_search_step:
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
    if abs(after.scroll_y - before.scroll_y) >= 1:
        direction = "down" if after.scroll_y > before.scroll_y else "up"
        facts.append(f"Scrolled {direction} from y={before.scroll_y:g} to y={after.scroll_y:g}")
    if before.url != after.url:
        facts.append(f"URL changed: {before.url} -> {after.url}")
    old = {item.element_id: item for item in before.elements}
    new = {item.element_id: item for item in after.elements}
    changed: list[str] = []
    for element_id in old.keys() & new.keys():
        left, right = old[element_id], new[element_id]
        state = _changed_control_properties(left, right)
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


def _changed_control_properties(left: object, right: object) -> list[str]:
    return [
        f"{name}={getattr(left, name)!r}->{getattr(right, name)!r}"
        for name in ("value", "checked", "pressed", "selected", "expanded", "busy")
        if getattr(left, name) != getattr(right, name)
    ]


def click_outcome_evidence(
    action: CandidateAction, before: BrowserSnapshot, after: BrowserSnapshot
) -> str | None:
    """Report observable consequences of the selected click, not input dispatch alone."""
    if action.kind != ActionKind.CLICK or not action.element_id:
        return None
    target = next((item for item in before.elements if item.element_id == action.element_id), None)
    if not target:
        return None
    if before.url != after.url:
        return "Click outcome: page URL changed"
    current = next((item for item in after.elements if item.element_id == target.element_id), None)
    if current:
        changed = [
            name
            for name in ("value", "checked", "pressed", "selected", "expanded", "busy")
            if getattr(target, name) != getattr(current, name)
        ]
        if changed:
            return "Click outcome: selected control changed " + ", ".join(changed)
    elif not any(item.role == target.role and item.name == target.name for item in after.elements):
        return "Click outcome: selected control disappeared"
    previous_controls = Counter((item.role, item.name, item.tag) for item in before.elements)
    new_controls = []
    for item in after.elements:
        key = (item.role, item.name, item.tag)
        if previous_controls[key]:
            previous_controls[key] -= 1
        else:
            new_controls.append(item.name or item.role)
    if new_controls:
        return "Click outcome: new visible controls " + ", ".join(new_controls[:4])
    return None


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

    def sync_visible_state(self, snapshot: BrowserSnapshot, *, action_count: int = 0) -> None:
        while not self.complete and _navigation_step_is_visible(self.steps[self.index], snapshot):
            self.index += 1
            self.history_boundary = action_count

    def advance_current_step(self, snapshot: BrowserSnapshot, *, action_count: int = 0) -> None:
        if self.complete:
            return
        self.index += 1
        self.history_boundary = action_count
        self.sync_visible_state(snapshot, action_count=action_count)


def _latest_supports_step(step: str, latest: StepRecord) -> tuple[bool, bool]:
    generic = {
            "a",
            "an",
            "and",
            "any",
            "change",
            "choose",
            "click",
            "enter",
            "go",
            "in",
            "it",
            "navigate",
            "of",
            "on",
            "open",
            "page",
            "press",
            "select",
            "set",
            "the",
            "their",
            "then",
            "to",
            "update",
            "visit",
    }
    step_terms = {
        token
        for token in re.findall(r"[a-z0-9]+", step.casefold())
        if len(token) > 1 and token not in generic
    }
    evidence = " ".join((latest.description, latest.change_summary or "")).casefold()
    evidence_terms = set(re.findall(r"[a-z0-9]+", evidence))
    authoritative_target = any(
        marker in latest.description
        for marker in (
            "Direct goal match: YES",
            "Visible content-detail destination for the requested item: YES",
            "Destination matches the required success URL: YES",
            "Destination is an explicitly approved non-start domain: YES",
        )
    )
    return not step_terms or bool(step_terms & evidence_terms) or authoritative_target, authoritative_target


def _action_step_disposition(step: str, latest: StepRecord, supports_step: bool, authoritative: bool) -> str:
    navigation_step = bool(re.match(r"^\s*(?:click|open|visit|navigate|go)\b", step, re.IGNORECASE))
    if (
        latest.action_kind == ActionKind.CLICK.value
        and latest.changed
        and supports_step
        and latest.change_summary
        and "URL changed:" in latest.change_summary
        and navigation_step
    ):
        return "verified"
    if latest.action_kind == ActionKind.CLICK.value and authoritative and navigation_step:
        return "pending"
    if (
        latest.changed
        and latest.action_kind in {ActionKind.CLICK.value, ActionKind.PRESS_ENTER.value, ActionKind.SELECT.value}
        and "Direct goal match: YES" in latest.description
        and not step_requests_submission(step)
        and (latest.action_kind != ActionKind.CLICK.value or "Click outcome:" in (latest.change_summary or ""))
    ):
        return "verified"
    return "confirm"


def _submission_is_pending(step: str, latest: StepRecord) -> bool:
    if not step_requests_submission(step):
        return False
    if latest.action_kind == ActionKind.FILL.value:
        return True
    return not (
        latest.action_kind == ActionKind.PRESS_ENTER.value
        or (
            latest.action_kind == ActionKind.CLICK.value
            and re.search(r"\b(?:search|submit)\b", latest.description, re.IGNORECASE)
        )
    )


def step_completion_disposition(
    progress: TaskProgress,
    decision: ModelDecision,
    history: Sequence[StepRecord],
) -> str:
    """Return verified, confirm, or pending for the active atomic outcome."""
    step = progress.current_step
    if not step:
        return "pending"
    if verified_scroll_step(progress, history):
        return "verified"
    has_current_step_action = len(history) > progress.history_boundary
    latest_supports_step = False
    if has_current_step_action:
        latest = history[-1]
        if "visible calendar range" in latest.description:
            return "pending"
        latest_supports_step, authoritative = _latest_supports_step(step, latest)
        action_disposition = _action_step_disposition(step, latest, latest_supports_step, authoritative)
        if action_disposition != "confirm":
            return action_disposition
    return _probability_disposition(
        step, decision, history, has_current_step_action, latest_supports_step
    )


def _probability_disposition(
    step: str,
    decision: ModelDecision,
    history: Sequence[StepRecord],
    has_current_step_action: bool,
    latest_supports_step: bool,
) -> str:
    probability = max(
        decision.step_completion_probability,
        decision.step_completion_change_probability,
    )
    if has_current_step_action and _submission_is_pending(step, history[-1]):
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
        and (latest.action_kind != "click" or "Click outcome:" in (latest.change_summary or ""))
    ):
        return "confirm"
    return "pending"


def verified_scroll_step(progress: TaskProgress, history: Sequence[StepRecord]) -> bool:
    if not progress.current_step or not history:
        return False
    direction = requested_scroll_direction(progress.current_step)
    latest = history[-1]
    return bool(
        direction
        and latest.action_kind == direction.value
        and latest.changed
        and f"Scrolled {direction.value.removeprefix('scroll_')} from y="
        in (latest.change_summary or "")
    )


def verified_skip_ad_step(
    progress: TaskProgress,
    action: CandidateAction,
    before: BrowserSnapshot,
    after: BrowserSnapshot,
) -> bool:
    """A click attempt is insufficient; the observed skip control must disappear."""
    if not progress.current_step or not requests_skip_ad(progress.current_step):
        return False
    target = next((item for item in before.elements if item.element_id == action.element_id), None)
    return bool(
        action.kind == ActionKind.CLICK
        and target
        and SKIP_AD_CONTROL.search(target.name)
        and before.url == after.url
        and not any(SKIP_AD_CONTROL.search(item.name) for item in after.elements)
    )


def authentication_surface(snapshot: BrowserSnapshot) -> bool:
    """Require browser-owned evidence that the current document is an auth surface."""
    parsed = urlparse(snapshot.url)
    host_labels = set((parsed.hostname or "").casefold().split("."))
    if host_labels & {"account", "accounts", "auth", "login", "signin", "sso"}:
        return True
    if AUTHENTICATION_PATH.search(parsed.path):
        return True
    if AUTHENTICATION_TITLE.search(snapshot.title):
        return True
    return any(element.input_type == "password" for element in snapshot.elements)


def login_blocks_progress(
    decision: ModelDecision,
    snapshot: BrowserSnapshot,
    actions: Sequence[CandidateAction],
) -> bool:
    """Stop for authentication only when model intent and browser evidence agree."""
    if decision.login_probability < 0.7:
        return False
    proposed = next(
        (action for action in actions if action.action_id == decision.proposed_action), None
    )
    if (
        proposed
        and proposed.kind in {ActionKind.BACK, ActionKind.SWITCH_TAB}
        and decision.confidence >= 0.45
    ):
        return False
    return authentication_surface(snapshot)


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
    return not any(action.goal_match and action.kind in semantic_kinds for action in actions)
