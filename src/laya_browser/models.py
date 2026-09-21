"""Domain models shared across observation, decision, policy, and execution."""

from __future__ import annotations

import hashlib
import json
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
    href: str = ""
    disabled: bool = False
    checked: bool | None = None
    selected: bool | None = None
    expanded: bool | None = None
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
        return " | ".join(parts)

    @property
    def semantic_description(self) -> str:
        parts = [self.role or self.tag, self.name or "unlabelled"]
        if self.href:
            parts.append(f"destination: {self.href}")
        return " | ".join(parts)

    @property
    def goal_description(self) -> str:
        return " | ".join((self.role or self.tag, self.name or "unlabelled"))


@dataclass(frozen=True)
class BrowserSnapshot:
    url: str
    title: str
    visible_text: str
    elements: tuple[ObservedElement, ...]
    can_scroll_up: bool = False
    can_scroll_down: bool = False
    can_go_back: bool = False

    @property
    def fingerprint(self) -> str:
        stable = {
            "url": self.url,
            "title": self.title,
            "text": self.visible_text,
            "elements": [asdict(element) for element in self.elements],
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


@dataclass(frozen=True)
class PolicyDecision:
    proposed_action: str
    executed_action: str | None
    intervened: bool
    reason: str | None = None


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
    changed: bool = False
    policy_intervened: bool = False
    policy_reason: str | None = None
    action_error: str | None = None
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
