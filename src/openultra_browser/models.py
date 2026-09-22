"""Domain models shared across observation, decision, policy, and execution."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class ActionKind(StrEnum):
    CLICK = "click"
    FILL = "fill"
    PRESS_ENTER = "press_enter"
    SELECT = "select"
    SCROLL_UP = "scroll_up"
    SCROLL_DOWN = "scroll_down"
    BACK = "back"
    SWITCH_TAB = "switch_tab"
    WAIT = "wait"
    DONE = "done"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class ObservedOption:
    label: str
    value: str


@dataclass(frozen=True)
class ObservedElement:
    element_id: str
    role: str
    name: str
    tag: str
    input_type: str = ""
    value: str = ""
    date_value: str = ""
    href: str = ""
    disabled: bool = False
    checked: bool | None = None
    pressed: bool | None = None
    selected: bool | None = None
    expanded: bool | None = None
    busy: bool | None = None
    submit_on_enter: bool = False
    options: tuple[ObservedOption, ...] = ()
    x: float = 0
    y: float = 0
    width: float = 0
    height: float = 0
    guard: str = ""

    @property
    def description(self) -> str:
        parts = [self.role or self.tag, self.name or "unlabelled"]
        if self.value:
            parts.append(f"current value: {self.value}")
        if self.href:
            parts.append(f"destination: {self.href}")
        if self.date_value:
            parts.append(f"date: {self.date_value}")
        for name in ("checked", "pressed", "selected", "expanded", "busy"):
            value = getattr(self, name)
            if value is not None:
                parts.append(f"{name}: {str(value).lower()}")
        return " | ".join(parts)

    @property
    def semantic_description(self) -> str:
        parts = [self.role or self.tag, self.name or "unlabelled"]
        if self.href:
            parts.append(f"destination: {self.href}")
        if self.date_value:
            parts.append(f"date: {self.date_value}")
        return " | ".join(parts)

    @property
    def goal_description(self) -> str:
        parts = [self.role or self.tag, self.name or "unlabelled"]
        date_types = {"date", "datetime-local", "month", "time", "week"}
        date_name = bool(
            re.search(
                r"\b(?:date|departure|depart|return|arrival|check[ -]?in|check[ -]?out)\b",
                self.name,
                re.IGNORECASE,
            )
        )
        date_value = bool(
            re.search(
                r"(?:\b\d{4}-\d{2}(?:-\d{2})?\b|\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|"
                r"apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|"
                r"nov(?:ember)?|dec(?:ember)?)\b)",
                self.value,
                re.IGNORECASE,
            )
        )
        if self.value and (self.input_type in date_types or (date_name and date_value)):
            parts.append(f"current date: {self.value}")
        if self.date_value:
            parts.append(f"date: {self.date_value}")
        return " | ".join(parts)


@dataclass(frozen=True)
class ObservedTab:
    target_id: str
    title: str
    url: str
    active: bool = False


@dataclass(frozen=True)
class BrowserSnapshot:
    url: str
    title: str
    visible_text: str
    elements: tuple[ObservedElement, ...]
    can_scroll_up: bool = False
    can_scroll_down: bool = False
    can_go_back: bool = False
    alerts: tuple[str, ...] = ()
    tabs: tuple[ObservedTab, ...] = ()
    scroll_y: float = 0.0

    @property
    def fingerprint(self) -> str:
        stable = {
            "url": self.url,
            "title": self.title,
            "text": self.visible_text,
            "scroll_y": self.scroll_y,
            "elements": [asdict(element) for element in self.elements],
            "tabs": [asdict(tab) for tab in self.tabs],
        }
        return hashlib.sha256(
            json.dumps(stable, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    @property
    def semantic_fingerprint(self) -> str:
        """Stable progress identity that excludes ephemeral DOM node handles."""
        elements = [
            {
                key: value
                for key, value in asdict(element).items()
                if key not in {"element_id", "guard", "x", "y", "width", "height"}
            }
            for element in self.elements
        ]
        stable = {
            "url": self.url,
            "title": self.title,
            "text": self.visible_text,
            "elements": elements,
            "tabs": [
                {"title": tab.title, "url": tab.url, "active": tab.active} for tab in self.tabs
            ],
        }
        return hashlib.sha256(
            json.dumps(stable, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()


@dataclass(frozen=True)
class CandidateAction:
    action_id: str
    kind: ActionKind
    description: str
    element_id: str | None = None
    input_key: str | None = None
    option: str | None = None
    option_value: str | None = None
    target_url: str | None = None
    browser_target_id: str | None = None
    goal_match: bool = False


@dataclass(frozen=True)
class ModelDecision:
    proposed_action: str
    probabilities: dict[str, float]
    confidence: float
    goal_probability: float
    stuck_probability: float
    inference_ms: float
    input_tokens: int
    operation: str = ""
    operation_probabilities: dict[str, float] = field(default_factory=dict)
    target_probabilities: dict[str, float] = field(default_factory=dict)
    completion_change_probability: float = 0.0
    error_probability: float = 0.0
    loading_probability: float = 0.0
    login_probability: float = 0.0
    step_completion_probability: float = 0.0
    step_completion_change_probability: float = 0.0
    correction_probability: float = 0.0


@dataclass(frozen=True)
class PolicyDecision:
    proposed_action: str
    executed_action: str | None
    intervened: bool
    reason: str | None = None


@dataclass(frozen=True)
class PageContext:
    url: str
    title: str


@dataclass(frozen=True)
class ActionContext:
    action_id: str
    action_kind: str | None
    description: str
    source_url: str
    result_url: str
    outcome: str
    succeeded: bool
    at_epoch_ms: int


@dataclass
class StepRecord:
    step: int
    url: str
    proposed_action: str
    executed_action: str | None
    description: str
    confidence: float
    goal_probability: float
    stuck_probability: float
    inference_ms: float
    action_kind: str | None = None
    changed: bool = False
    policy_intervened: bool = False
    policy_reason: str | None = None
    action_error: str | None = None
    change_summary: str | None = None
    elapsed_ms: float = 0


@dataclass
class RunResult:
    status: str
    reason: str
    final_url: str
    steps: list[StepRecord] = field(default_factory=list)
    elapsed_ms: float = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
