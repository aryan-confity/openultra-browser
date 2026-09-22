"""Bounded observe-decide-shield-act-verify browser loop."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

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
    RunResult,
    StepRecord,
)
from .observation import build_actions
from .policy import SafetyPolicy
from .progress import exhausted_scroll_exploration, repeats_action_cycle
from .task_progress import (
    TaskProgress,
    click_outcome_evidence,
    error_blocks_progress,
    login_blocks_progress,
    step_completion_disposition,
    summarize_page_change,
    verified_scroll_step,
    verified_skip_ad_step,
)
from .trace import write_trace


@dataclass
class _RunState:
    started: float
    deadline: float
    progress: TaskProgress
    final_url: str
    status: str
    reason: str
    history: list[StepRecord] = field(default_factory=list)
    context_actions: list[ActionContext] = field(default_factory=list)
    previous_page: PageContext | None = None


class BrowserAgent:
    def __init__(
        self,
        config: RunConfig,
        *,
        decision_engine: OpenUltraDecisionEngine | None = None,
        browser_factory: Callable[..., Browser] = Browser,
        on_step: Callable[[StepRecord], None] | None = None,
    ) -> None:
        self.config = config
        self.engine = decision_engine or OpenUltraDecisionEngine(
            config.model, optimize=config.optimize
        )
        self.browser_factory = browser_factory
        self.policy = SafetyPolicy(
            config.effective_allowed_domains,
            allow_risky=config.allow_risky,
            allow_external_navigation=config.allow_external_navigation,
        )
        self.on_step = on_step or (lambda _record: None)

    def run(self) -> RunResult:
        started = time.perf_counter()
        state = _RunState(
            started=started,
            deadline=started + self.config.max_seconds,
            progress=TaskProgress.from_goal(self.config.goal),
            final_url=self.config.start_url,
            status="max_steps",
            reason=f"Reached the {self.config.max_steps}-step limit",
        )
        with self.browser_factory(
            self.config.start_url, text_limit=self.config.visible_text_chars
        ) as browser:
            for step_number in range(1, self.config.max_steps + 1):
                if time.perf_counter() >= state.deadline:
                    state.status, state.reason = "timeout", "Run deadline elapsed"
                    break
                if not self._run_step(browser, state, step_number):
                    break
            state.final_url = browser.url
            if self.config.keep_open_seconds > 0:
                time.sleep(self.config.keep_open_seconds)

        result = RunResult(
            status=state.status,
            reason=state.reason,
            final_url=state.final_url,
            steps=state.history,
            elapsed_ms=(time.perf_counter() - started) * 1_000,
            metadata={
                "model": self.config.model,
                "allowed_domains": sorted(self.config.effective_allowed_domains),
                "network_model_calls": 0,
                "task_steps": list(state.progress.steps),
                "completed_steps": list(state.progress.completed_steps),
            },
        )
        if self.config.trace_path:
            write_trace(self.config.trace_path, result)
        return result

    def _success_reason(self, snapshot: BrowserSnapshot) -> str | None:
        checks = []
        if self.config.success_text:
            if self.config.success_text.casefold() not in snapshot.visible_text.casefold():
                return None
            checks.append(f"text {self.config.success_text!r}")
        if self.config.success_url_prefix:
            checks.append(f"URL prefix {self.config.success_url_prefix!r}")
        if self.config.success_url_regex:
            checks.append(f"URL pattern {self.config.success_url_regex!r}")
        if not checks or not self.config.matches_success_url(snapshot.url):
            return None
        return "Verified " + " and ".join(checks)

    def _step_is_verified(
        self,
        state: _RunState,
        decision: ModelDecision,
        snapshot: BrowserSnapshot,
    ) -> bool:
        disposition = step_completion_disposition(state.progress, decision, state.history)
        if disposition == "verified":
            return True
        confirm = getattr(self.engine, "confirm_step_completion", None)
        if disposition != "confirm" or not state.progress.current_step or not callable(confirm):
            return False
        return (
            confirm(
                goal=self.config.goal,
                current_step=state.progress.current_step,
                snapshot=snapshot,
                history=state.history,
            )
            >= 0.45
        )

    def _decide_for_step(
        self, state: _RunState, snapshot: BrowserSnapshot
    ) -> tuple[tuple[CandidateAction, ...], ModelDecision] | None:
        has_verifier = bool(
            self.config.success_text or self.config.success_url_prefix
            or self.config.success_url_regex
        )
        while True:
            actions = build_actions(
                snapshot,
                state.progress.current_step or self.config.goal,
                self.config.prepared_inputs,
                self.config.max_candidates,
                include_done=not has_verifier and state.progress.complete,
                success_url_prefix=self.config.success_url_prefix,
                success_url_regex=self.config.success_url_regex,
                preferred_domains=self.config.preferred_domains,
                context_goal=self.config.goal,
            )
            decision = self.engine.decide(
                goal=self.config.goal,
                current_step=state.progress.current_step,
                snapshot=snapshot,
                actions=actions,
                history=state.history,
                context_actions=state.context_actions,
                previous_page=state.previous_page,
            )
            if state.history and error_blocks_progress(decision, snapshot, actions):
                state.status = "error"
                state.reason = "The current page visibly rejects or errors after the last action"
                return None
            if state.progress.current_step and self._step_is_verified(state, decision, snapshot):
                state.progress.advance_current_step(snapshot, action_count=len(state.history))
                continue
            if login_blocks_progress(decision, snapshot, actions):
                state.status = "needs_login"
                state.reason = "The current outcome requires authentication in the isolated browser profile"
                return None
            return actions, decision

    def _finish_choice(
        self,
        state: _RunState,
        chosen: CandidateAction | None,
        record: StepRecord,
        policy_reason: str | None,
        snapshot: BrowserSnapshot,
    ) -> bool:
        if chosen is not None and chosen.kind not in {ActionKind.DONE, ActionKind.BLOCKED}:
            return False
        state.history.append(record)
        self.on_step(record)
        if chosen is None:
            state.status, state.reason = "blocked", policy_reason or "No executable action"
        elif chosen.kind == ActionKind.BLOCKED:
            state.status, state.reason = "blocked", "No supported observed action can make progress"
        else:
            confirm = getattr(self.engine, "confirm_completion", None)
            confirmation = (
                confirm(goal=self.config.goal, snapshot=snapshot, history=state.history)
                if callable(confirm) else 0.0
            )
            if confirmation >= 0.45:
                state.status = "completed"
                state.reason = "Verified atomic outcomes accepted by the independent completion check"
            else:
                state.status = "needs_verification"
                state.reason = "The model proposed completion without sufficient independent evidence"
        return True

    def _execute_action(
        self,
        browser: Browser,
        state: _RunState,
        chosen: CandidateAction,
        record: StepRecord,
        snapshot: BrowserSnapshot,
    ) -> tuple[BrowserSnapshot, bool]:
        result_snapshot = snapshot
        skip_verified = False
        try:
            transport_verified = bool(
                browser.act(chosen, snapshot, self.config.prepared_inputs)
            )
            evidence = getattr(browser, "last_action_evidence", None)
            if evidence:
                record.description += f" Execution evidence: {evidence}."
            result_snapshot = browser.observe()
            record.change_summary = summarize_page_change(snapshot, result_snapshot)
            click_evidence = click_outcome_evidence(chosen, snapshot, result_snapshot)
            if click_evidence:
                record.change_summary += f" | {click_evidence}"
            skip_verified = verified_skip_ad_step(state.progress, chosen, snapshot, result_snapshot)
            if skip_verified:
                record.change_summary += " | Skip Ad control disappeared on the same page"
            record.changed = (
                result_snapshot.semantic_fingerprint != snapshot.semantic_fingerprint
                or transport_verified
                or abs(result_snapshot.scroll_y - snapshot.scroll_y) >= 1
            )
        except StalePage as error:
            record.action_error = f"{type(error).__name__}: {error}"
            record.executed_action = None
        except ExecutionUncertain as error:
            record.action_error = f"{type(error).__name__}: {error}"
            state.status, state.reason = "needs_verification", str(error)
        except Exception as error:
            record.action_error = f"{type(error).__name__}: {error}"
        return result_snapshot, skip_verified

    @staticmethod
    def _context_outcome(record: StepRecord) -> str:
        if record.action_error:
            return record.action_error
        if record.change_summary:
            return record.change_summary
        return "Completed with no verified page change" if record.changed else "No page change"

    def _remember_action(
        self,
        state: _RunState,
        record: StepRecord,
        before: BrowserSnapshot,
        after: BrowserSnapshot,
    ) -> None:
        if not record.executed_action:
            return
        if before.url != after.url:
            state.previous_page = PageContext(url=before.url, title=before.title)
        state.context_actions.append(
            ActionContext(
                action_id=record.executed_action,
                action_kind=record.action_kind,
                description=record.description,
                source_url=before.url,
                result_url=after.url,
                outcome=self._context_outcome(record),
                succeeded=not bool(record.action_error),
                at_epoch_ms=round(time.time() * 1_000),
            )
        )
        del state.context_actions[:-3]

    @staticmethod
    def _stuck_reason(history: list[StepRecord]) -> str | None:
        if exhausted_scroll_exploration(history):
            return "Paused after six same-direction exploration scrolls without a matching target"
        if len(history) >= 3 and all(
            not row.changed and row.executed_action != "wait" for row in history[-3:]
        ):
            return "Three consecutive actions produced no observable change"
        if repeats_action_cycle(history):
            return "A repeated action sequence produced no durable task progress"
        return None

    def _after_action(
        self, state: _RunState, result_snapshot: BrowserSnapshot, skip_verified: bool
    ) -> bool:
        if state.status == "needs_verification":
            return False
        has_verifier = bool(
            self.config.success_text or self.config.success_url_prefix
            or self.config.success_url_regex
        )
        if verified_scroll_step(state.progress, state.history):
            state.progress.advance_current_step(result_snapshot, action_count=len(state.history))
            if state.progress.complete and not has_verifier:
                state.status, state.reason = "completed", "Verified requested scroll direction and movement"
                return False
        if not state.history[-1].action_error and skip_verified:
            state.progress.advance_current_step(result_snapshot, action_count=len(state.history))
            if state.progress.complete and not has_verifier:
                state.status, state.reason = "completed", "Verified Skip Ad control disappeared"
                return False
        stuck = self._stuck_reason(state.history)
        if stuck:
            state.status, state.reason = "stuck", stuck
            return False
        return True

    def _run_step(self, browser: Browser, state: _RunState, step_number: int) -> bool:
        step_started = time.perf_counter()
        snapshot = browser.observe()
        state.progress.sync_visible_state(snapshot, action_count=len(state.history))
        state.final_url = snapshot.url
        success = self._success_reason(snapshot)
        if success:
            state.status, state.reason = "completed", success
            return False
        decided = self._decide_for_step(state, snapshot)
        if decided is None:
            return False
        actions, decision = decided
        policy = self.policy.choose(
            decision=decision,
            actions=actions,
            snapshot=snapshot,
            history=state.history,
            context_actions=state.context_actions,
        )
        chosen = next(
            (action for action in actions if action.action_id == policy.executed_action), None
        )
        record = StepRecord(
            step=step_number,
            url=snapshot.url,
            proposed_action=decision.proposed_action,
            executed_action=policy.executed_action,
            description=chosen.description if chosen else "No executable action",
            confidence=decision.confidence,
            goal_probability=decision.goal_probability,
            stuck_probability=decision.stuck_probability,
            inference_ms=decision.inference_ms,
            action_kind=chosen.kind.value if chosen else None,
            policy_intervened=policy.intervened,
            policy_reason=policy.reason,
        )
        if self._finish_choice(state, chosen, record, policy.reason, snapshot):
            return False
        result_snapshot, skip_verified = self._execute_action(
            browser, state, chosen, record, snapshot
        )
        record.elapsed_ms = (time.perf_counter() - step_started) * 1_000
        state.history.append(record)
        self._remember_action(state, record, snapshot, result_snapshot)
        self.on_step(record)
        return self._after_action(state, result_snapshot, skip_verified)
