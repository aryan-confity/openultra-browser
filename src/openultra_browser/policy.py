"""Deterministic safety shield for model-proposed browser actions."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from urllib.parse import urlparse

from .models import (
    ActionContext,
    ActionKind,
    BrowserSnapshot,
    CandidateAction,
    ModelDecision,
    PolicyDecision,
    StepRecord,
)

RISKY_PATTERN = re.compile(
    r"\b(delete|remove|purchase|buy|pay|checkout|transfer|send money|confirm order|"
    r"publish|post|submit application|close account|unsubscribe)\b",
    re.IGNORECASE,
)


def _terminal_rejection(
    action: CandidateAction,
    decision: ModelDecision,
    actions: Sequence[CandidateAction],
    history: Sequence[StepRecord],
) -> str | None:
    if action.kind == ActionKind.DONE:
        if not any(row.changed for row in history) and any(
            candidate.kind != ActionKind.DONE for candidate in actions
        ):
            return "no observable task progress has occurred"
        if any(candidate.kind != ActionKind.DONE and candidate.goal_match for candidate in actions):
            return "a visible goal-progress action remains"
        if decision.goal_probability < 0.75 and any(
            candidate.kind != ActionKind.DONE for candidate in actions
        ):
            return "independent completion confidence is below threshold"
    if action.kind == ActionKind.BLOCKED and decision.stuck_probability < 0.75:
        return "independent stuck confidence is below threshold"
    return None


def _ranked_action_ids(
    decision: ModelDecision, actions: Sequence[CandidateAction]
) -> list[str]:
    proposed = next(
        (action for action in actions if action.action_id == decision.proposed_action), None
    )
    direct_progress = [
        action.action_id
        for action in actions
        if action.goal_match and action.action_id != decision.proposed_action
    ]
    fallback = sorted(
        decision.probabilities,
        key=lambda action_id: decision.probabilities[action_id],
        reverse=True,
    )
    return [
        *([decision.proposed_action] if proposed and proposed.goal_match else []),
        *direct_progress,
        *([decision.proposed_action] if not proposed or not proposed.goal_match else []),
        *(
            action_id
            for action_id in fallback
            if action_id != decision.proposed_action and action_id not in direct_progress
        ),
    ]


def _navigation_rejection(
    action: CandidateAction,
    allowed_domains: frozenset[str],
    allow_external_navigation: bool,
) -> str | None:
    if not action.target_url:
        return None
    target = urlparse(action.target_url)
    if target.scheme not in {"http", "https"}:
        return "non-HTTP navigation is blocked"
    if not allow_external_navigation and target.hostname not in allowed_domains:
        return f"navigation to unapproved domain {target.hostname!r} is blocked"
    return None


class SafetyPolicy:
    def __init__(
        self,
        allowed_domains: frozenset[str],
        *,
        allow_risky: bool = False,
        allow_external_navigation: bool = False,
    ) -> None:
        self.allowed_domains = allowed_domains
        self.allow_risky = allow_risky
        self.allow_external_navigation = allow_external_navigation

    def _reason_blocked(
        self,
        action: CandidateAction,
        snapshot: BrowserSnapshot,
        elements: Mapping[str, object],
    ) -> str | None:
        navigation_rejection = _navigation_rejection(
            action, self.allowed_domains, self.allow_external_navigation
        )
        if navigation_rejection:
            return navigation_rejection
        if action.element_id:
            element = elements.get(action.element_id)
            if element is None:
                return "selected element is absent from the current observation"
            input_type = getattr(element, "input_type", "")
            if input_type in {"password", "file"}:
                return f"{input_type} inputs are never controlled by this runtime"
        if not self.allow_risky and RISKY_PATTERN.search(action.description):
            return "destructive or financial action requires explicit --allow-risky"
        if action.kind in {ActionKind.DONE, ActionKind.BLOCKED}:
            return None
        if (
            not self.allow_external_navigation
            and urlparse(snapshot.url).hostname not in self.allowed_domains
        ):
            return "the current page is outside the approved domains"
        return None

    def _candidate_rejection(
        self,
        action_id: str,
        *,
        by_id: Mapping[str, CandidateAction],
        elements: Mapping[str, object],
        decision: ModelDecision,
        actions: Sequence[CandidateAction],
        snapshot: BrowserSnapshot,
        history: Sequence[StepRecord],
        correction_target: str | None,
        recent_noops: set[str],
        ranked_count: int,
    ) -> str | None:
        action = by_id.get(action_id)
        if action is None:
            return "not in observed action space"
        terminal_reason = _terminal_rejection(action, decision, actions, history)
        if terminal_reason:
            return terminal_reason
        if correction_target == action_id and len(actions) > 1:
            return "the updated task rejects the previous target"
        reason = self._reason_blocked(action, snapshot, elements)
        if reason:
            return reason
        if action_id in recent_noops and ranked_count > 1:
            return "recent no-op"
        return None

    def choose(
        self,
        *,
        decision: ModelDecision,
        actions: Sequence[CandidateAction],
        snapshot: BrowserSnapshot,
        history: Sequence[StepRecord],
        context_actions: Sequence[ActionContext] = (),
    ) -> PolicyDecision:
        by_id = {action.action_id: action for action in actions}
        elements = {element.element_id: element for element in snapshot.elements}
        recent_noops = {
            row.executed_action for row in history[-3:] if row.executed_action and not row.changed
        }
        correction_target = (
            context_actions[-1].action_id
            if context_actions and decision.correction_probability >= 0.6
            else None
        )
        ranked = _ranked_action_ids(decision, actions)
        rejected: list[str] = []
        for action_id in ranked:
            reason = self._candidate_rejection(
                action_id,
                by_id=by_id,
                elements=elements,
                decision=decision,
                actions=actions,
                snapshot=snapshot,
                history=history,
                correction_target=correction_target,
                recent_noops=recent_noops,
                ranked_count=len(ranked),
            )
            if reason:
                rejected.append(f"{action_id}: {reason}")
                continue
            action = by_id[action_id]
            policy_reason = "; ".join(rejected) if rejected else None
            if not policy_reason and action_id != decision.proposed_action and action.goal_match:
                policy_reason = "Visible deterministic goal progress takes precedence"
            return PolicyDecision(
                proposed_action=decision.proposed_action,
                executed_action=action_id,
                intervened=action_id != decision.proposed_action,
                reason=policy_reason,
            )
        return PolicyDecision(
            proposed_action=decision.proposed_action,
            executed_action=None,
            intervened=True,
            reason="No policy-approved action remained: " + "; ".join(rejected),
        )
