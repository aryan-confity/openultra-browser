from openultra_browser.decision import OpenUltraDecisionEngine
from openultra_browser.models import (
    ActionKind,
    BrowserSnapshot,
    CandidateAction,
    ObservedElement,
    StepRecord,
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
                "completion": {"noul": 0.08},
                "stuck": {"noul": 0.03},
            },
            "usage": {"input_tokens": 123, "output_tokens": 0},
        }


def test_operation_and_compatible_target_share_one_local_batch():
    fake = FakeAgent()
    engine = OpenUltraDecisionEngine("unused", agent=fake)
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
    assert set(fake.calls[0][1]) == {
        "operation",
        "click_target",
        "completion",
        "stuck",
    }
    assert result.operation == "CLICK"
    assert result.proposed_action == "click_e2"
    assert result.probabilities["click_e2"] > result.probabilities["scroll_down"]
    assert abs(sum(result.probabilities.values()) - 1) < 1e-9
    operations = fake.calls[0][1]["operation"]["criteria"]
    assert "goal-progress action exists: YES" in operations["CLICK"]
    state = fake.calls[0][0]
    assert state.index("Trust boundary") < state.index("Visible page text")
    assert state.index("Recent actions") < state.index("Visible page text")
    assert result.goal_probability == 0.08
    assert result.stuck_probability == 0.03


def test_completion_confirmation_is_a_focused_boolean_check():
    class CompletionAgent:
        def predict(self, state, questions):
            assert "Requested task:\nOpen Documentation, then open Local inference" in state
            assert "Verified changed prior steps, in order" in state
            assert "Activate link | Documentation" in state
            assert "Ignored failed action" not in state
            assert "Resulting page URL: https://example.com/docs" in state
            assert "Current observed page:\nTitle: Local inference" in state
            assert set(questions) == {"completion_check"}
            instructions = questions["completion_check"]["instructions"]
            assert "verified changed prior steps" in instructions.lower()
            return {"answers": {"completion_check": {"noul": 0.91}}}

    engine = OpenUltraDecisionEngine("unused", agent=CompletionAgent())
    probability = engine.confirm_completion(
        goal="Open Documentation, then open Local inference",
        snapshot=BrowserSnapshot(
            "https://example.com/local",
            "Local inference",
            "Local inference is ready",
            (),
        ),
        history=(
            StepRecord(
                step=1,
                url="https://example.com",
                proposed_action="click_e1",
                executed_action="click_e1",
                description="Activate link | Documentation",
                confidence=0.9,
                goal_probability=0.2,
                stuck_probability=0.0,
                inference_ms=4.0,
                changed=True,
            ),
            StepRecord(
                step=2,
                url="https://example.com/docs",
                proposed_action="click_e2",
                executed_action="click_e2",
                description="Ignored failed action",
                confidence=0.9,
                goal_probability=0.2,
                stuck_probability=0.0,
                inference_ms=4.0,
                changed=False,
                action_error="StalePage: changed",
            ),
        ),
    )

    assert probability == 0.91
