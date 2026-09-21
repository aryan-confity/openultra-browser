"""Interactive predict/execute state machine for the local inspector."""

from __future__ import annotations

import time
from dataclasses import asdict
from typing import Any

from .browser import Browser, ExecutionUncertain, StalePage
from .config import RunConfig
from .decision import OpenUltraDecisionEngine
from .models import (
    ActionContext,
    ActionKind,
    BrowserSnapshot,
    CandidateAction,
    ModelDecision,
    PageContext,
    StepRecord,
)
from .observation import build_actions
from .policy import SafetyPolicy
from .progress import repeats_action_cycle
from .task_progress import (
    TaskProgress,
    error_blocks_progress,
    login_blocks_progress,
    step_completion_disposition,
    summarize_page_change,
)

TERMINAL_STATUSES = {
    "completed",
    "blocked",
    "needs_verification",
    "needs_login",
    "max_steps",
    "timeout",
    "stuck",
    "error",
}
MAX_CONTEXT_ACTIONS = 3


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
        self.engine = decision_engine or OpenUltraDecisionEngine(
            config.model, optimize=config.optimize
        )
        self.policy = SafetyPolicy(
            config.effective_allowed_domains,
            allow_risky=config.allow_risky,
            allow_external_navigation=config.allow_external_navigation,
        )
        self.started_at_epoch_ms = round(time.time() * 1_000)
        self.started_at = time.perf_counter()
        self.history: list[StepRecord] = []
        self.context_actions: list[ActionContext] = []
        self.previous_page: PageContext | None = None
        self.decision: ModelDecision | None = None
        self.policy_result = None
        self.actions: tuple[CandidateAction, ...] = ()
        self.status = "ready"
        self.reason = "Page observed and ready for a decision"
        self.progress = TaskProgress.from_goal(config.goal)
        self.browser = browser_factory(config.start_url, text_limit=config.visible_text_chars)
        try:
            self.snapshot = self.browser.observe()
            self.progress.sync_visible_state(self.snapshot, action_count=len(self.history))
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
        self.progress.sync_visible_state(self.snapshot, action_count=len(self.history))
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
            self.progress.current_step or self.config.goal,
            self.config.prepared_inputs,
            self.config.max_candidates,
            include_done=not has_verifier and self.progress.complete,
            success_url_prefix=self.config.success_url_prefix,
            success_url_regex=self.config.success_url_regex,
            preferred_domains=self.config.preferred_domains,
            context_goal=self.config.goal,
        )

    def predict(self) -> dict[str, Any]:
        if self.terminal:
            raise ValueError("This run has stopped. Start a new task.")
        while True:
            self.snapshot = self.browser.observe()
            self.screenshot = self._screenshot()
            self._refresh()
            if self.terminal:
                return self.state()
            self.decision = self.engine.decide(
                goal=self.config.goal,
                current_step=self.progress.current_step,
                snapshot=self.snapshot,
                actions=self.actions,
                history=self.history,
                context_actions=self.context_actions,
                previous_page=self.previous_page,
            )
            if self.history and error_blocks_progress(self.decision, self.snapshot, self.actions):
                self.status = "error"
                self.reason = "The current page visibly rejects or errors after the last action"
                self.actions = ()
                return self.state()
            disposition = step_completion_disposition(self.progress, self.decision, self.history)
            step_verified = disposition == "verified"
            confirm_step = getattr(self.engine, "confirm_step_completion", None)
            if disposition == "confirm" and self.progress.current_step and callable(confirm_step):
                step_verified = (
                    confirm_step(
                        goal=self.config.goal,
                        current_step=self.progress.current_step,
                        snapshot=self.snapshot,
                        history=self.history,
                    )
                    >= 0.45
                )
            if self.progress.current_step and step_verified:
                self.progress.advance_current_step(self.snapshot, action_count=len(self.history))
                self.decision = None
                self.policy_result = None
                continue
            if login_blocks_progress(self.decision, self.snapshot, self.actions):
                self.status = "needs_login"
                self.reason = (
                    "The current outcome requires authentication in the isolated browser profile"
                )
                self.actions = ()
                return self.state()
            break
        self.policy_result = self.policy.choose(
            decision=self.decision,
            actions=self.actions,
            snapshot=self.snapshot,
            history=self.history,
            context_actions=self.context_actions,
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
            action_kind=chosen.kind.value,
            policy_intervened=policy_result.intervened,
            policy_reason=policy_result.reason,
        )
        step_started = time.perf_counter()
        source_snapshot = self.snapshot
        if chosen.kind == ActionKind.DONE:
            self.history.append(record)
            confirm = getattr(self.engine, "confirm_completion", None)
            confirmation = (
                confirm(
                    goal=self.config.goal,
                    snapshot=self.snapshot,
                    history=self.history,
                )
                if callable(confirm)
                else 0.0
            )
            if confirmation >= 0.45:
                self.status = "completed"
                self.reason = (
                    "Verified atomic outcomes accepted by the independent completion check"
                )
            else:
                self.status = "needs_verification"
                self.reason = (
                    "The model proposed completion without sufficient independent evidence"
                )
            return self.state()

        if chosen.kind == ActionKind.BLOCKED:
            self.status, self.reason = "blocked", "No supported observed action can make progress"
            self.history.append(record)
            return self.state()

        try:
            transport_verified = bool(
                self.browser.act(chosen, self.snapshot, self.config.prepared_inputs)
            )
            evidence = getattr(self.browser, "last_action_evidence", None)
            if evidence:
                record.description += f" Execution evidence: {evidence}."
            next_snapshot = self.browser.observe()
            record.change_summary = summarize_page_change(self.snapshot, next_snapshot)
            record.changed = (
                next_snapshot.semantic_fingerprint != self.snapshot.semantic_fingerprint
                or transport_verified
            )
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
            self.reason = str(error)
        except Exception as error:
            record.action_error = f"{type(error).__name__}: {error}"
            self.status = "ready"
            self.reason = "Action failed before confirmed progress; inspect and choose again"
        record.elapsed_ms = (time.perf_counter() - step_started) * 1_000
        self.history.append(record)
        self._remember_action(record, source_snapshot, self.snapshot)
        self.screenshot = self._screenshot()
        if (
            self.status == "ready"
            and len(self.history) >= 3
            and all(not row.changed and row.executed_action != "wait" for row in self.history[-3:])
        ):
            self.status = "stuck"
            self.reason = "Three consecutive actions produced no observable change"
        if self.status == "ready" and repeats_action_cycle(self.history):
            self.status = "stuck"
            self.reason = "A repeated action sequence produced no durable task progress"
        if self.status == "ready":
            self._refresh()
        return self.state()

    def _remember_action(
        self,
        record: StepRecord,
        source: BrowserSnapshot,
        result: BrowserSnapshot,
    ) -> None:
        if not record.executed_action:
            return
        if result.url != source.url:
            self.previous_page = PageContext(url=source.url, title=source.title)
        outcome = record.action_error or record.change_summary
        if not outcome:
            outcome = (
                "Completed with no verified page change" if record.changed else "No page change"
            )
        self.context_actions.append(
            ActionContext(
                action_id=record.executed_action,
                action_kind=record.action_kind,
                description=record.description,
                source_url=source.url,
                result_url=result.url,
                outcome=outcome,
                succeeded=not bool(record.action_error),
                at_epoch_ms=round(time.time() * 1_000),
            )
        )
        del self.context_actions[:-MAX_CONTEXT_ACTIONS]

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

    def continuation_url(self) -> str:
        """Resolve a valid owned page for a follow-up task without replacing browser state."""
        recover = getattr(self.browser, "ensure_active_http_target", None)
        if callable(recover):
            return str(recover())
        return self.snapshot.url

    def retask(self, config: RunConfig) -> dict[str, Any]:
        """Start a new task state while preserving the current browser target."""
        snapshot = self.browser.observe()
        screenshot = self._screenshot()
        progress = TaskProgress.from_goal(config.goal)
        progress.sync_visible_state(snapshot)

        self.config = config
        self.policy = SafetyPolicy(
            config.effective_allowed_domains,
            allow_risky=config.allow_risky,
            allow_external_navigation=config.allow_external_navigation,
        )
        self.started_at_epoch_ms = round(time.time() * 1_000)
        self.started_at = time.perf_counter()
        self.history = []
        self.decision = None
        self.policy_result = None
        self.actions = ()
        self.status = "ready"
        self.reason = "Updated task ready on the current page"
        self.progress = progress
        self.snapshot = snapshot
        self.screenshot = screenshot
        self._refresh()
        return self.state()

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
        tabs = [
            {
                "title": tab.title,
                "url": tab.url,
                "active": tab.active,
            }
            for tab in self.snapshot.tabs
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
                "tabs": tabs,
            },
            "actions": actions,
            "decision": decision,
            "policy": policy,
            "history": [asdict(row) for row in self.history],
            "task_steps": list(self.progress.steps),
            "completed_steps": list(self.progress.completed_steps),
            "current_step": self.progress.current_step,
            "elapsed_ms": round(self.elapsed_ms),
            "started_at_epoch_ms": self.started_at_epoch_ms,
            "max_steps": self.config.max_steps,
            "max_seconds": self.config.max_seconds,
            "model": self.config.model,
            "network_model_calls": 0,
        }

    def close(self) -> None:
        self.browser.close()
