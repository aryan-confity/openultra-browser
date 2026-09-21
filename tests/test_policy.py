from openultra_browser.models import (
    ActionContext,
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
    decision = SafetyPolicy(frozenset({"example.com"}), allow_external_navigation=True).choose(
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
    decision = SafetyPolicy(frozenset({"example.com"}), allow_external_navigation=True).choose(
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
    click = CandidateAction("click", ActionKind.CLICK, "Open final result", "e0", goal_match=True)
    confident_done = ModelDecision("done", {"done": 0.9, "click": 0.1}, 0.9, 0.95, 0.0, 5, 100)
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
    assert "deterministic goal progress" in no_progress.reason
    assert after_progress.executed_action == "click"
    assert "deterministic goal progress" in after_progress.reason


def test_rejected_done_falls_back_to_first_ranked_direct_progress_target():
    exact = CandidateAction(
        "condos", ActionKind.CLICK, "Activate Condos for rent", "e1", goal_match=True
    )
    partial = CandidateAction("rent", ActionKind.CLICK, "Activate Rent", "e2", goal_match=True)
    scroll = CandidateAction("scroll", ActionKind.SCROLL_DOWN, "Scroll down")
    done = CandidateAction("done", ActionKind.DONE, "Finish")
    decision = ModelDecision(
        "done",
        {"condos": 0.08, "rent": 0.12, "scroll": 0.3, "done": 0.5},
        0.1,
        0.6,
        0.0,
        5,
        100,
    )

    result = SafetyPolicy(frozenset({"example.com"})).choose(
        decision=decision,
        actions=(exact, partial, scroll, done),
        snapshot=page(
            ObservedElement("e1", "button", "Condos for rent", "button"),
            ObservedElement("e2", "button", "Rent", "button"),
        ),
        history=(),
    )

    assert result.executed_action == "condos"
    assert result.intervened
    assert "deterministic goal progress" in result.reason


def test_deterministic_progress_precedes_unrelated_model_proposal():
    unrelated = CandidateAction("click_logo", ActionKind.CLICK, "Open logo", "e1")
    scroll = CandidateAction(
        "scroll_down",
        ActionKind.SCROLL_DOWN,
        "Reveal unexplored content",
        goal_match=True,
    )
    decision = ModelDecision(
        "click_logo",
        {"click_logo": 0.8, "scroll_down": 0.2},
        0.8,
        0.1,
        0.0,
        1.0,
        1,
    )

    result = SafetyPolicy(frozenset({"example.com"})).choose(
        decision=decision,
        actions=(unrelated, scroll),
        snapshot=page(ObservedElement("e1", "link", "Logo", "a")),
        history=(),
    )

    assert result.executed_action == "scroll_down"
    assert result.intervened


def test_confirmed_correction_excludes_the_previous_target():
    previous = CandidateAction(
        "click_first", ActionKind.CLICK, "Open First result", "e1", goal_match=True
    )
    alternative = CandidateAction(
        "click_second", ActionKind.CLICK, "Open Second result", "e2", goal_match=True
    )
    decision = ModelDecision(
        "click_first",
        {"click_first": 0.6, "click_second": 0.4},
        0.9,
        0.1,
        0.1,
        5,
        100,
        correction_probability=0.9,
    )
    context = (
        ActionContext(
            action_id="click_first",
            action_kind="click",
            description="Open First result",
            source_url="https://example.com/results",
            result_url="https://example.com/results",
            outcome="No page change",
            succeeded=True,
            at_epoch_ms=1,
        ),
    )

    result = SafetyPolicy(frozenset({"example.com"})).choose(
        decision=decision,
        actions=(previous, alternative),
        snapshot=page(
            ObservedElement("e1", "link", "First result", "a"),
            ObservedElement("e2", "link", "Second result", "a"),
        ),
        history=(),
        context_actions=context,
    )

    assert result.executed_action == "click_second"
    assert "rejects the previous target" in result.reason
