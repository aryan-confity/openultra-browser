"""Bounded observe-decide-shield-act-verify browser loop."""

from __future__ import annotations

import time
from collections.abc import Callable

from .browser import Browser, ExecutionUncertain, StalePage
from .config import RunConfig
from .decision import OpenUltraDecisionEngine
from .models import ActionKind, RunResult, StepRecord
from .observation import build_actions
from .policy import SafetyPolicy
from .progress import repeats_action_cycle
from .trace import write_trace


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
        self.engine = decision_engine or OpenUltraDecisionEngine(config.model, optimize=config.optimize)
        self.browser_factory = browser_factory
        self.policy = SafetyPolicy(
            config.effective_allowed_domains,
            allow_risky=config.allow_risky,
            allow_external_navigation=config.allow_external_navigation,
        )
        self.on_step = on_step or (lambda _record: None)

    def run(self) -> RunResult:
        started = time.perf_counter()
        deadline = started + self.config.max_seconds
        history: list[StepRecord] = []
        status = "max_steps"
        reason = f"Reached the {self.config.max_steps}-step limit"
        final_url = self.config.start_url

        with self.browser_factory(
            self.config.start_url, text_limit=self.config.visible_text_chars
        ) as browser:
            for step_number in range(1, self.config.max_steps + 1):
                if time.perf_counter() >= deadline:
                    status, reason = "timeout", "Run deadline elapsed"
                    break
                step_started = time.perf_counter()
                snapshot = browser.observe()
                final_url = snapshot.url
                text_verified = not self.config.success_text or (
                    self.config.success_text.casefold() in snapshot.visible_text.casefold()
                )
                url_verified = self.config.matches_success_url(snapshot.url)
                has_verifier = bool(
                    self.config.success_text
                    or self.config.success_url_prefix
                    or self.config.success_url_regex
                )
                if has_verifier and text_verified and url_verified:
                    status = "completed"
                    checks = []
                    if self.config.success_text:
                        checks.append(f"text {self.config.success_text!r}")
                    if self.config.success_url_prefix:
                        checks.append(f"URL prefix {self.config.success_url_prefix!r}")
                    if self.config.success_url_regex:
                        checks.append(f"URL pattern {self.config.success_url_regex!r}")
                    reason = "Verified " + " and ".join(checks)
                    break

                actions = build_actions(
                    snapshot,
                    self.config.goal,
                    self.config.prepared_inputs,
                    self.config.max_candidates,
                    include_done=not has_verifier,
                    success_url_prefix=self.config.success_url_prefix,
                    success_url_regex=self.config.success_url_regex,
                    preferred_domains=self.config.preferred_domains,
                )
                decision = self.engine.decide(
                    goal=self.config.goal,
                    snapshot=snapshot,
                    actions=actions,
                    history=history,
                )
                policy = self.policy.choose(
                    decision=decision,
                    actions=actions,
                    snapshot=snapshot,
                    history=history,
                )
                by_id = {action.action_id: action for action in actions}
                chosen = by_id.get(policy.executed_action or "")
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
                    policy_intervened=policy.intervened,
                    policy_reason=policy.reason,
                )
                if chosen is None:
                    status, reason = "blocked", policy.reason or "No executable action"
                    history.append(record)
                    self.on_step(record)
                    break
                if chosen.kind == ActionKind.DONE:
                    history.append(record)
                    self.on_step(record)
                    confirm = getattr(self.engine, "confirm_completion", None)
                    confirmation = (
                        confirm(goal=self.config.goal, snapshot=snapshot, history=history)
                        if decision.goal_probability >= 0.75 and callable(confirm)
                        else 0.0
                    )
                    if confirmation >= 0.75:
                        status = "completed"
                        reason = "Completion confirmed by two independent local checks"
                    else:
                        status = "needs_verification"
                        reason = (
                            "The model proposed completion without sufficient independent evidence"
                        )
                    break
                if chosen.kind == ActionKind.BLOCKED:
                    status, reason = "blocked", "No supported observed action can make progress"
                    history.append(record)
                    self.on_step(record)
                    break

                try:
                    browser.act(chosen, snapshot, self.config.prepared_inputs)
                    next_snapshot = browser.observe()
                    record.changed = next_snapshot.fingerprint != snapshot.fingerprint
                except StalePage as error:
                    record.action_error = f"{type(error).__name__}: {error}"
                    record.executed_action = None
                except ExecutionUncertain as error:
                    record.action_error = f"{type(error).__name__}: {error}"
                    status = "needs_verification"
                    reason = "Browser input may have executed; automatic retry is unsafe"
                except Exception as error:
                    record.action_error = f"{type(error).__name__}: {error}"
                record.elapsed_ms = (time.perf_counter() - step_started) * 1_000
                history.append(record)
                self.on_step(record)
                if status == "needs_verification":
                    break
                if len(history) >= 3 and all(
                    not row.changed and row.executed_action != "wait" for row in history[-3:]
                ):
                    status, reason = (
                        "stuck",
                        "Three consecutive actions produced no observable change",
                    )
                    break
                if repeats_action_cycle(history):
                    status, reason = (
                        "stuck",
                        "A repeated action sequence produced no durable task progress",
                    )
                    break
            final_url = browser.url
            if self.config.keep_open_seconds > 0:
                time.sleep(self.config.keep_open_seconds)

        result = RunResult(
            status=status,
            reason=reason,
            final_url=final_url,
            steps=history,
            elapsed_ms=(time.perf_counter() - started) * 1_000,
            metadata={
                "model": self.config.model,
                "allowed_domains": sorted(self.config.effective_allowed_domains),
                "network_model_calls": 0,
            },
        )
        if self.config.trace_path:
            write_trace(self.config.trace_path, result)
        return result
