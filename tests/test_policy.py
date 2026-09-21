from openultra_browser.models import (
    ActionKind,
    BrowserSnapshot,
    CandidateAction,
    ModelDecision,
    ObservedElement,
    StepRecord,
)
from openultra_browser.policy import SafetyPolicy


def model(proposed, probabilities):
    return ModelDecision(proposed, probabilities, 0.9, 0.1, 0.1, 5, 100)


def page(*elements):
    return BrowserSnapshot("https://example.com", "Page", "Text", tuple(elements))


def test_cross_domain_action_falls_back_to_safe_choice():
    unsafe = CandidateAction(
        "external", ActionKind.CLICK, "Open external", "e0", target_url="https://evil.test"
    )
    safe = CandidateAction("scroll", ActionKind.SCROLL_DOWN, "Scroll down")
    decision = SafetyPolicy(frozenset({"example.com"})).choose(
        decision=model("external", {"external": 0.8, "scroll": 0.2}),
        actions=(unsafe, safe),
        snapshot=page(ObservedElement("e0", "link", "External", "a")),
        history=(),
    )
    assert decision.executed_action == "scroll"
    assert decision.intervened


def test_task_first_policy_allows_observed_https_navigation_only():
    external = CandidateAction(
        "external",
        ActionKind.CLICK,
        "Open official documentation",
        "e0",
        target_url="https://docs.example.test/guide",
    )
    decision = SafetyPolicy(
        frozenset({"example.com"}), allow_external_navigation=True
    ).choose(
        decision=model("external", {"external": 1.0}),
        actions=(external,),
        snapshot=page(ObservedElement("e0", "link", "Documentation", "a")),
        history=(),
    )

    assert decision.executed_action == "external"


def test_task_first_policy_still_rejects_non_http_navigation():
    external = CandidateAction(
        "external", ActionKind.CLICK, "Open mail", "e0", target_url="mailto:test@example.com"
    )
    decision = SafetyPolicy(
        frozenset({"example.com"}), allow_external_navigation=True
    ).choose(
        decision=model("external", {"external": 1.0}),
        actions=(external,),
        snapshot=page(ObservedElement("e0", "link", "Mail", "a")),
        history=(),
    )

    assert decision.executed_action is None


def test_password_input_is_always_blocked():
    fill = CandidateAction("fill", ActionKind.FILL, "Fill password", "e0", input_key="password")
    decision = SafetyPolicy(frozenset({"example.com"}), allow_risky=True).choose(
        decision=model("fill", {"fill": 1.0}),
        actions=(fill,),
        snapshot=page(ObservedElement("e0", "textbox", "Password", "input", input_type="password")),
        history=(),
    )
    assert decision.executed_action is None


def test_recent_noop_uses_next_probability():
    click = CandidateAction("click", ActionKind.CLICK, "Open docs", "e0")
    scroll = CandidateAction("scroll", ActionKind.SCROLL_DOWN, "Scroll down")
    history = [
        StepRecord(1, "https://example.com", "click", "click", "Open docs", 0.9, 0.1, 0.1, 5)
    ]
    decision = SafetyPolicy(frozenset({"example.com"})).choose(
        decision=model("click", {"click": 0.8, "scroll": 0.2}),
        actions=(click, scroll),
        snapshot=page(ObservedElement("e0", "link", "Docs", "a")),
        history=history,
    )
    assert decision.executed_action == "scroll"


def test_safe_model_proposal_wins_before_cross_operation_fallback():
    click = CandidateAction("click", ActionKind.CLICK, "Open docs", "e0")
    back = CandidateAction("back", ActionKind.BACK, "Go back")
    decision = SafetyPolicy(frozenset({"example.com"})).choose(
        decision=model("click", {"click": 0.4, "back": 0.6}),
        actions=(click, back),
        snapshot=page(ObservedElement("e0", "link", "Docs", "a")),
        history=(),
    )

    assert decision.executed_action == "click"
    assert not decision.intervened


def test_done_requires_independent_completion_confidence():
    done = CandidateAction("done", ActionKind.DONE, "Finish")
    scroll = CandidateAction("scroll", ActionKind.SCROLL_DOWN, "Scroll down")
    decision = SafetyPolicy(frozenset({"example.com"})).choose(
        decision=model("done", {"done": 0.8, "scroll": 0.2}),
        actions=(done, scroll),
        snapshot=page(),
        history=[
            StepRecord(
                1,
                "https://example.com/start",
                "scroll",
                "scroll",
                "Scroll down",
                0.8,
                0.1,
                0.1,
                4,
                changed=True,
            )
        ],
    )

    assert decision.executed_action == "scroll"
    assert decision.intervened
    assert "completion confidence" in decision.reason


def test_done_requires_observed_progress_and_no_visible_goal_action():
    done = CandidateAction("done", ActionKind.DONE, "Finish")
    click = CandidateAction(
        "click", ActionKind.CLICK, "Open final result", "e0", goal_match=True
    )
    confident_done = ModelDecision(
        "done", {"done": 0.9, "click": 0.1}, 0.9, 0.95, 0.0, 5, 100
    )
    policy = SafetyPolicy(frozenset({"example.com"}))

    no_progress = policy.choose(
        decision=confident_done,
        actions=(done, click),
        snapshot=page(ObservedElement("e0", "link", "Final result", "a")),
        history=(),
    )
    after_progress = policy.choose(
        decision=confident_done,
        actions=(done, click),
        snapshot=page(ObservedElement("e0", "link", "Final result", "a")),
        history=[
            StepRecord(
                1,
                "https://example.com/start",
                "scroll",
                "scroll",
                "Scroll down",
                0.8,
                0.1,
                0.1,
                4,
                changed=True,
            )
        ],
    )

    assert no_progress.executed_action == "click"
    assert "no observable task progress" in no_progress.reason
    assert after_progress.executed_action == "click"
    assert "goal-progress action remains" in after_progress.reason
