from laya_browser.models import (
    ActionKind,
    BrowserSnapshot,
    CandidateAction,
    ModelDecision,
    ObservedElement,
    StepRecord,
)
from laya_browser.policy import SafetyPolicy


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
