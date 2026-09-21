from laya_browser.decision import LayaDecisionEngine
from laya_browser.models import (
    ActionKind,
    BrowserSnapshot,
    CandidateAction,
    ObservedElement,
)


class FakeAgent:
    def __init__(self):
        self.calls = []

    def predict(self, state, questions):
        self.calls.append((state, questions))
        operation_ids = list(questions["operation"]["criteria"])
        operation_probabilities = {key: 0.0 for key in operation_ids}
        operation_probabilities["CLICK"] = 0.8
        operation_probabilities["SCROLL_DOWN"] = 0.2
        targets = list(questions["click_target"]["criteria"])
        return {
            "answers": {
                "operation": {
                    "choice": "CLICK",
                    "confidence": 0.9,
                    "probabilities": operation_probabilities,
                },
                "click_target": {
                    "choice": targets[1],
                    "confidence": 0.8,
                    "probabilities": {targets[0]: 0.1, targets[1]: 0.9},
                },
            },
            "usage": {"input_tokens": 123, "output_tokens": 0},
        }


def test_operation_and_compatible_target_share_one_local_batch():
    fake = FakeAgent()
    engine = LayaDecisionEngine("unused", agent=fake)
    snapshot = BrowserSnapshot(
        "https://example.com",
        "Home",
        "Open documentation",
        (
            ObservedElement("e1", "link", "Pricing", "a"),
            ObservedElement("e2", "link", "Documentation", "a"),
        ),
    )
    actions = (
        CandidateAction("click_e1", ActionKind.CLICK, "Open Pricing", "e1"),
        CandidateAction("click_e2", ActionKind.CLICK, "Open Documentation", "e2", goal_match=True),
        CandidateAction("scroll_down", ActionKind.SCROLL_DOWN, "Scroll down"),
    )
    result = engine.decide(
        goal="Open documentation", snapshot=snapshot, actions=actions, history=()
    )

    assert len(fake.calls) == 1
    assert set(fake.calls[0][1]) == {"operation", "click_target"}
    assert result.operation == "CLICK"
    assert result.proposed_action == "click_e2"
    assert result.probabilities["click_e2"] > result.probabilities["scroll_down"]
    assert abs(sum(result.probabilities.values()) - 1) < 1e-9
    operations = fake.calls[0][1]["operation"]["criteria"]
    assert "goal-progress action exists: YES" in operations["CLICK"]
    state = fake.calls[0][0]
    assert state.index("Trust boundary") < state.index("Visible page text")
    assert state.index("Recent actions") < state.index("Visible page text")
