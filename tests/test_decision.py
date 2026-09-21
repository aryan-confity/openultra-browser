from openultra_browser.decision import OpenUltraDecisionEngine
from openultra_browser.models import (
    ActionContext,
    ActionKind,
    BrowserSnapshot,
    CandidateAction,
    ObservedElement,
    PageContext,
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
                "completion_change": {"noul": 0.12},
                "stuck": {"noul": 0.03},
                "error": {"noul": 0.01},
                "loading": {"noul": 0.02},
                "login": {"noul": 0.04},
                "step_completion": {"noul": 0.18},
                "step_completion_change": {"noul": 0.22},
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
        goal="Open documentation",
        current_step="Open documentation",
        snapshot=snapshot,
        actions=actions,
        history=(),
    )

    assert len(fake.calls) == 1
    assert set(fake.calls[0][1]) == {
        "operation",
        "click_target",
        "completion",
        "completion_change",
        "stuck",
        "error",
        "loading",
        "login",
        "step_completion",
        "step_completion_change",
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
    assert result.goal_probability == 0.12
    assert result.completion_change_probability == 0.12
    assert result.stuck_probability == 0.03
    assert result.error_probability == 0.01
    assert result.loading_probability == 0.02
    assert result.login_probability == 0.04
    assert result.step_completion_probability == 0.18
    assert result.step_completion_change_probability == 0.22
    step_question = fake.calls[0][1]["step_completion"]
    assert "current required step" in step_question["instructions"]
    assert "control state" in step_question["instructions"]


def test_switch_tab_uses_a_typed_target_in_the_same_batch():
    class TabAgent:
        def predict(self, _state, questions):
            operation_ids = list(questions["operation"]["criteria"])
            target_ids = list(questions["switch_tab_target"]["criteria"])
            return {
                "answers": {
                    "operation": {
                        "choice": "SWITCH_TAB",
                        "confidence": 0.94,
                        "probabilities": {
                            key: float(key == "SWITCH_TAB") for key in operation_ids
                        },
                    },
                    "switch_tab_target": {
                        "choice": target_ids[0],
                        "confidence": 0.92,
                        "probabilities": {
                            key: float(key == target_ids[0]) for key in target_ids
                        },
                    },
                    "completion": {"noul": 0.02},
                    "completion_change": {"noul": 0.02},
                    "stuck": {"noul": 0.01},
                    "error": {"noul": 0.01},
                    "loading": {"noul": 0.01},
                    "login": {"noul": 0.95},
                    "step_completion": {"noul": 0.01},
                    "step_completion_change": {"noul": 0.01},
                },
                "usage": {"input_tokens": 40},
            }

    engine = OpenUltraDecisionEngine("unused", agent=TabAgent())
    action = CandidateAction(
        "switch_tab_youtube",
        ActionKind.SWITCH_TAB,
        "Focus browser tab | YouTube",
        browser_target_id="youtube",
        goal_match=True,
    )

    result = engine.decide(
        goal="Switch to the YouTube tab",
        current_step="Switch to the YouTube tab",
        snapshot=BrowserSnapshot("https://accounts.google.com", "Sign in", "Sign in", ()),
        actions=(action,),
        history=(),
    )

    assert result.operation == "SWITCH_TAB"
    assert result.proposed_action == "switch_tab_youtube"
    assert result.target_probabilities == {"switch_tab_youtube": 1.0}


def test_bounded_session_context_and_correction_share_the_main_batch():
    class ContextAgent(FakeAgent):
        def predict(self, state, questions):
            result = super().predict(state, questions)
            result["answers"]["correction"] = {"noul": 0.86}
            return result

    fake = ContextAgent()
    engine = OpenUltraDecisionEngine("unused", agent=fake)
    snapshot = BrowserSnapshot(
        "https://example.com/details",
        "Wrong result",
        "Wrong result details",
        (
            ObservedElement("e1", "link", "First result", "a"),
            ObservedElement("e2", "link", "Second result", "a"),
        ),
        can_go_back=True,
        can_scroll_down=True,
    )
    actions = (
        CandidateAction("click_e1", ActionKind.CLICK, "Open First result", "e1"),
        CandidateAction("click_e2", ActionKind.CLICK, "Open Second result", "e2"),
        CandidateAction("scroll_down", ActionKind.SCROLL_DOWN, "Scroll down"),
    )
    context = tuple(
        ActionContext(
            action_id=f"click_e{index}",
            action_kind="click",
            description=f"Open result {index}",
            source_url="https://example.com/results",
            result_url=f"https://example.com/details/{index}",
            outcome=f"URL changed to details/{index}",
            succeeded=True,
            at_epoch_ms=index,
        )
        for index in range(1, 5)
    )

    result = engine.decide(
        goal="Not that result. Open the other one",
        current_step="Open the other result",
        snapshot=snapshot,
        actions=actions,
        history=(),
        context_actions=context,
        previous_page=PageContext("https://example.com/results", "Results"),
    )

    state, questions = fake.calls[0]
    assert "Previous page before the latest navigation: Results" in state
    assert "Open result 1" not in state
    assert "Open result 2" in state
    assert "Open result 4" in state
    assert "correction" in questions
    assert result.correction_probability == 0.86


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


def test_voice_command_type_and_completeness_share_one_local_batch():
    class VoiceAgent:
        def predict(self, state, questions):
            assert "Current streaming speech transcript: open wikipedia and search" in state
            assert "First atomic browser command candidate: open wikipedia" in state
            assert "reversible closed-set browser command: YES" in state
            assert set(questions) == {"complete"}
            return {
                "answers": {
                    "complete": {"noul": 0.91},
                }
            }

    engine = OpenUltraDecisionEngine("unused", agent=VoiceAgent())
    result = engine.classify_voice_command(
        "open wikipedia and search",
        "open wikipedia",
    )

    assert result["kind"] == "reversible_closed_set"
    assert result["confidence"] == 0.91
    assert result["completeness"] == 0.91
    assert result["inference_ms"] >= 0


def test_step_completion_confirmation_requires_no_further_action():
    class StepAgent:
        def predict(self, state, questions):
            assert "Current required step: Open Condos for rent" in state
            assert "URL changed" in state
            assert set(questions) == {"step_complete"}
            assert "no further action" in questions["step_complete"]["instructions"]
            return {"answers": {"step_complete": {"noul": 0.77}}}

    engine = OpenUltraDecisionEngine("unused", agent=StepAgent())
    probability = engine.confirm_step_completion(
        goal="Open Condos for rent and click Inquire",
        current_step="Open Condos for rent",
        snapshot=BrowserSnapshot(
            "https://wdxproperties.com/properties/rent",
            "Rental listings",
            "Condos for rent",
            (),
        ),
        history=(
            StepRecord(
                step=1,
                url="https://wdxproperties.com/",
                proposed_action="click_condos",
                executed_action="click_condos",
                description="Activate Condos for rent",
                confidence=0.9,
                goal_probability=0.2,
                stuck_probability=0.0,
                inference_ms=4.0,
                changed=True,
                change_summary="URL changed to rental listings",
            ),
        ),
    )

    assert probability == 0.77
