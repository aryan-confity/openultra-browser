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
        if action.target_url:
            target = urlparse(action.target_url)
            if target.scheme not in {"http", "https"}:
                return "non-HTTP navigation is blocked"
            if not self.allow_external_navigation and target.hostname not in self.allowed_domains:
                return f"navigation to unapproved domain {target.hostname!r} is blocked"
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
        fallback = sorted(
            decision.probabilities,
            key=lambda action_id: decision.probabilities[action_id],
            reverse=True,
        )
        direct_progress = [
            action.action_id
            for action in actions
            if action.goal_match and action.action_id != decision.proposed_action
        ]
        proposed = by_id.get(decision.proposed_action)
        proposed_is_progress = bool(proposed and proposed.goal_match)
        ranked = [
            *([decision.proposed_action] if proposed_is_progress else []),
            *direct_progress,
            *([decision.proposed_action] if not proposed_is_progress else []),
            *(
                action_id
                for action_id in fallback
                if action_id != decision.proposed_action and action_id not in direct_progress
            ),
        ]
        rejected: list[str] = []
        for action_id in ranked:
            action = by_id.get(action_id)
            if action is None:
                rejected.append(f"{action_id}: not in observed action space")
                continue
            if action.kind == ActionKind.DONE and not any(row.changed for row in history):
                rejected.append(f"{action_id}: no observable task progress has occurred")
                continue
            if action.kind == ActionKind.DONE and any(
                candidate.kind != ActionKind.DONE and candidate.goal_match for candidate in actions
            ):
                rejected.append(f"{action_id}: a visible goal-progress action remains")
                continue
            if (
                action.kind == ActionKind.DONE
                and decision.goal_probability < 0.75
                and any(candidate.kind != ActionKind.DONE for candidate in actions)
            ):
                rejected.append(
                    f"{action_id}: independent completion confidence is below threshold"
                )
                continue
            if action.kind == ActionKind.BLOCKED and decision.stuck_probability < 0.75:
                rejected.append(f"{action_id}: independent stuck confidence is below threshold")
                continue
            if (
                correction_target
                and action_id == correction_target
                and any(candidate.action_id != correction_target for candidate in actions)
            ):
                rejected.append(f"{action_id}: the updated task rejects the previous target")
                continue
            reason = self._reason_blocked(action, snapshot, elements)
            if reason:
                rejected.append(f"{action_id}: {reason}")
                continue
            if action_id in recent_noops and len(ranked) > 1:
                rejected.append(f"{action_id}: recent no-op")
                continue
            return PolicyDecision(
                proposed_action=decision.proposed_action,
                executed_action=action_id,
                intervened=action_id != decision.proposed_action,
                reason=(
                    "; ".join(rejected)
                    if rejected
                    else (
                        "Visible deterministic goal progress takes precedence"
                        if action_id != decision.proposed_action and action.goal_match
                        else None
                    )
                ),
            )
        return PolicyDecision(
            proposed_action=decision.proposed_action,
            executed_action=None,
            intervened=True,
            reason="No policy-approved action remained: " + "; ".join(rejected),
        )
