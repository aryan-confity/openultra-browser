"""Interactive predict/execute state machine for the local inspector."""

from __future__ import annotations

import time
from dataclasses import asdict
from typing import Any

from .browser import Browser, ExecutionUncertain, StalePage
from .config import RunConfig
from .decision import OpenUltraDecisionEngine
from .models import ActionKind, BrowserSnapshot, CandidateAction, ModelDecision, StepRecord
from .observation import build_actions
from .policy import SafetyPolicy
from .progress import repeats_action_cycle

TERMINAL_STATUSES = {
    "completed",
    "blocked",
    "needs_verification",
    "max_steps",
    "timeout",
    "stuck",
    "error",
}


class InteractiveAgent:
    """A single browser run whose prediction and execution can be inspected separately."""

    def __init__(
        self,
        config: RunConfig,
        *,
        decision_engine: OpenUltraDecisionEngine | None = None,
        browser_factory=Browser,
    ) -> None:
        self.config = config
        self.engine = decision_engine or OpenUltraDecisionEngine(config.model, optimize=config.optimize)
        self.policy = SafetyPolicy(
            config.effective_allowed_domains,
            allow_risky=config.allow_risky,
            allow_external_navigation=config.allow_external_navigation,
        )
        self.started_at_epoch_ms = round(time.time() * 1_000)
        self.started_at = time.perf_counter()
        self.history: list[StepRecord] = []
        self.decision: ModelDecision | None = None
        self.policy_result = None
        self.actions: tuple[CandidateAction, ...] = ()
        self.status = "ready"
        self.reason = "Page observed and ready for a decision"
        self.browser = browser_factory(config.start_url, text_limit=config.visible_text_chars)
        try:
            self.snapshot = self.browser.observe()
            self.screenshot = self._screenshot()
            self._refresh()
        except BaseException:
            self.browser.close()
            raise

    @property
    def elapsed_ms(self) -> float:
        return (time.perf_counter() - self.started_at) * 1_000

    @property
    def terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    def _screenshot(self) -> str | None:
        capture = getattr(self.browser, "screenshot", None)
        return capture() if capture else None

    def _success(self, snapshot: BrowserSnapshot) -> tuple[bool, str]:
        text_ok = not self.config.success_text or (
            self.config.success_text.casefold() in snapshot.visible_text.casefold()
        )
        url_ok = self.config.matches_success_url(snapshot.url)
        has_check = bool(
            self.config.success_text
            or self.config.success_url_prefix
            or self.config.success_url_regex
        )
        if not (has_check and text_ok and url_ok):
            return False, ""
        checks = []
        if self.config.success_text:
            checks.append(f"text {self.config.success_text!r}")
        if self.config.success_url_prefix:
            checks.append(f"URL prefix {self.config.success_url_prefix!r}")
        if self.config.success_url_regex:
            checks.append(f"URL pattern {self.config.success_url_regex!r}")
        return True, "Verified " + " and ".join(checks)

    def _refresh(self) -> None:
        verified, reason = self._success(self.snapshot)
        if verified:
            self.status, self.reason, self.actions = "completed", reason, ()
            return
        if len(self.history) >= self.config.max_steps:
            self.status = "max_steps"
            self.reason = f"Reached the {self.config.max_steps}-step limit"
            self.actions = ()
            return
        if self.elapsed_ms >= self.config.max_seconds * 1_000:
            self.status, self.reason, self.actions = "timeout", "Run deadline elapsed", ()
            return
        has_verifier = bool(
            self.config.success_text
            or self.config.success_url_prefix
            or self.config.success_url_regex
        )
        self.actions = build_actions(
            self.snapshot,
            self.config.goal,
            self.config.prepared_inputs,
            self.config.max_candidates,
            include_done=not has_verifier,
            success_url_prefix=self.config.success_url_prefix,
            success_url_regex=self.config.success_url_regex,
            preferred_domains=self.config.preferred_domains,
        )

    def predict(self) -> dict[str, Any]:
        if self.terminal:
            raise ValueError("This run has stopped. Start a new task.")
        self.snapshot = self.browser.observe()
        self.screenshot = self._screenshot()
        self._refresh()
        if self.terminal:
            return self.state()
        self.decision = self.engine.decide(
            goal=self.config.goal,
            snapshot=self.snapshot,
            actions=self.actions,
            history=self.history,
        )
        self.policy_result = self.policy.choose(
            decision=self.decision,
            actions=self.actions,
            snapshot=self.snapshot,
            history=self.history,
        )
        self.status = "predicted"
        self.reason = "Choice ready for inspection or execution"
        return self.state()

    def act(self, fingerprint: str) -> dict[str, Any]:
        if self.status != "predicted" or not self.decision or not self.policy_result:
            raise ValueError("Choose the next action before executing it.")
        if fingerprint != self.snapshot.fingerprint:
            raise StalePage("The displayed observation is stale. Choose again.")

        decision = self.decision
        policy_result = self.policy_result
        self.decision = None
        self.policy_result = None
        by_id = {action.action_id: action for action in self.actions}
        chosen = by_id.get(policy_result.executed_action or "")
        if chosen is None:
            self.status = "blocked"
            self.reason = policy_result.reason or "No executable action remained"
            return self.state()

        record = StepRecord(
            step=len(self.history) + 1,
            url=self.snapshot.url,
            proposed_action=decision.proposed_action,
            executed_action=policy_result.executed_action,
            description=chosen.description,
            confidence=decision.confidence,
            goal_probability=decision.goal_probability,
            stuck_probability=decision.stuck_probability,
            inference_ms=decision.inference_ms,
            policy_intervened=policy_result.intervened,
            policy_reason=policy_result.reason,
        )
        step_started = time.perf_counter()
        if chosen.kind == ActionKind.DONE:
            self.history.append(record)
            confirm = getattr(self.engine, "confirm_completion", None)
            confirmation = (
                confirm(
                    goal=self.config.goal,
                    snapshot=self.snapshot,
                    history=self.history,
                )
                if decision.goal_probability >= 0.75 and callable(confirm)
                else 0.0
            )
            if confirmation >= 0.75:
                self.status = "completed"
                self.reason = "Completion confirmed by two independent local checks"
            else:
                self.status = "needs_verification"
                self.reason = "The model proposed completion without sufficient independent evidence"
            return self.state()

        if chosen.kind == ActionKind.BLOCKED:
            self.status, self.reason = "blocked", "No supported observed action can make progress"
            self.history.append(record)
            return self.state()

        try:
            self.browser.act(chosen, self.snapshot, self.config.prepared_inputs)
            next_snapshot = self.browser.observe()
            record.changed = next_snapshot.fingerprint != self.snapshot.fingerprint
            self.snapshot = next_snapshot
            self.status = "ready"
            self.reason = "Page observed and ready for a decision"
        except StalePage as error:
            record.executed_action = None
            record.action_error = f"{type(error).__name__}: {error}"
            self.snapshot = self.browser.observe()
            self.status = "ready"
            self.reason = "Page changed before input; observation refreshed"
        except ExecutionUncertain as error:
            record.action_error = f"{type(error).__name__}: {error}"
            self.status = "needs_verification"
            self.reason = "Browser input may have executed; automatic retry is unsafe"
        except Exception as error:
            record.action_error = f"{type(error).__name__}: {error}"
            self.status = "ready"
            self.reason = "Action failed before confirmed progress; inspect and choose again"
        record.elapsed_ms = (time.perf_counter() - step_started) * 1_000
        self.history.append(record)
        self.screenshot = self._screenshot()
        if self.status == "ready" and len(self.history) >= 3 and all(
            not row.changed and row.executed_action != "wait" for row in self.history[-3:]
        ):
            self.status = "stuck"
            self.reason = "Three consecutive actions produced no observable change"
        if self.status == "ready" and repeats_action_cycle(self.history):
            self.status = "stuck"
            self.reason = "A repeated action sequence produced no durable task progress"
        if self.status == "ready":
            self._refresh()
        return self.state()

    def capture_frame(self) -> dict[str, Any]:
        """Capture pixels without observing controls or changing the decision state."""
        try:
            screenshot = self._screenshot()
        except RuntimeError:
            screenshot = None
        if screenshot:
            self.screenshot = screenshot
        return {
            "screenshot": self.screenshot,
            "captured_at_epoch_ms": round(time.time() * 1_000),
            "fresh": bool(screenshot),
        }

    def tick(self) -> dict[str, Any]:
        self.predict()
        if self.status == "predicted":
            return self.act(self.snapshot.fingerprint)
        return self.state()

    def state(self) -> dict[str, Any]:
        decision = asdict(self.decision) if self.decision else None
        policy = asdict(self.policy_result) if self.policy_result else None
        elements = [
            {
                "element_id": item.element_id,
                "role": item.role,
                "name": item.name,
                "tag": item.tag,
                "href": item.href,
                "x": item.x,
                "y": item.y,
                "width": item.width,
                "height": item.height,
            }
            for item in self.snapshot.elements
        ]
        actions = [
            {
                "action_id": item.action_id,
                "kind": item.kind.value,
                "description": item.description,
                "element_id": item.element_id,
                "goal_match": item.goal_match,
            }
            for item in self.actions
        ]
        return {
            "status": self.status,
            "reason": self.reason,
            "goal": self.config.goal,
            "page": {
                "url": self.snapshot.url,
                "title": self.snapshot.title,
                "visible_text": self.snapshot.visible_text,
                "fingerprint": self.snapshot.fingerprint,
                "screenshot": self.screenshot,
                "width": 1120,
                "height": 780,
                "elements": elements,
            },
            "actions": actions,
            "decision": decision,
            "policy": policy,
            "history": [asdict(row) for row in self.history],
            "elapsed_ms": round(self.elapsed_ms),
            "started_at_epoch_ms": self.started_at_epoch_ms,
            "max_steps": self.config.max_steps,
            "max_seconds": self.config.max_seconds,
            "model": self.config.model,
            "network_model_calls": 0,
        }

    def close(self) -> None:
        self.browser.close()
