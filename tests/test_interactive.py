import json

from openultra_browser.browser import StalePage
from openultra_browser.config import RunConfig
from openultra_browser.interactive import InteractiveAgent
from openultra_browser.models import BrowserSnapshot, ModelDecision, ObservedElement


class FakeBrowser:
    def __init__(self, _url, *, text_limit):
        self.closed = False
        self.snapshot = BrowserSnapshot(
            "https://example.com/search",
            "Search",
            "Search the catalogue",
            (
                ObservedElement(
                    element_id="e1",
                    role="searchbox",
                    name="Search",
                    tag="input",
                    input_type="search",
                    submit_on_enter=True,
                    x=20,
                    y=30,
                    width=240,
                    height=40,
                    guard="guard",
                ),
            ),
        )
        self.executions = 0

    def observe(self):
        return self.snapshot

    def screenshot(self):
        return "c2NyZWVuc2hvdA=="

    def act(self, action, _snapshot, prepared_inputs):
        self.executions += 1
        assert action.input_key == "query"
        assert prepared_inputs["query"] == "private search phrase"
        self.snapshot = BrowserSnapshot(
            "https://example.com/result",
            "Result",
            "The requested result is visible",
            (),
        )

    def close(self):
        self.closed = True


class FillEngine:
    def decide(self, *, actions, **_kwargs):
        selected = next(
            action.action_id for action in actions if action.action_id.startswith("fill_")
        )
        return ModelDecision(
            proposed_action=selected,
            probabilities={
                action.action_id: float(action.action_id == selected) for action in actions
            },
            confidence=1.0,
            goal_probability=0.2,
            stuck_probability=0.0,
            inference_ms=4.2,
            input_tokens=20,
            operation="fill",
            operation_probabilities={"fill": 1.0},
        )


class UnexpectedEngine:
    def decide(self, **_kwargs):
        raise AssertionError("Verified success must stop before model inference")


class StaleBrowser(FakeBrowser):
    def act(self, *_args):
        self.executions += 1
        self.snapshot = BrowserSnapshot(
            "https://example.com/changed",
            "Changed",
            "The page changed",
            (),
        )
        raise StalePage("changed before input")


class SuccessBrowser(FakeBrowser):
    def __init__(self, url, *, text_limit):
        super().__init__(url, text_limit=text_limit)
        self.snapshot = BrowserSnapshot(
            "https://example.com/result",
            "Result",
            "The requested result is visible",
            (),
        )


def config(**overrides):
    values = {
        "goal": "Find the requested catalogue result",
        "start_url": "https://example.com/search",
        "prepared_inputs": {"query": "private search phrase"},
        "max_candidates": 10,
    }
    values.update(overrides)
    return RunConfig(**values)


def test_predict_is_observational_and_hides_prepared_values():
    agent = InteractiveAgent(
        config(),
        decision_engine=FillEngine(),
        browser_factory=FakeBrowser,
    )
    state = agent.predict()

    assert state["status"] == "predicted"
    assert agent.browser.executions == 0
    assert "private search phrase" not in json.dumps(state)
    assert state["decision"]["operation"] == "fill"
    agent.close()


def test_execute_consumes_the_bound_choice_once_and_verifies_result():
    agent = InteractiveAgent(
        config(success_text="requested result"),
        decision_engine=FillEngine(),
        browser_factory=FakeBrowser,
    )
    predicted = agent.predict()
    state = agent.act(predicted["page"]["fingerprint"])

    assert state["status"] == "completed"
    assert agent.browser.executions == 1
    assert len(state["history"]) == 1
    assert state["history"][0]["changed"] is True
    agent.close()


def test_deterministic_success_completes_before_prediction():
    agent = InteractiveAgent(
        config(
            start_url="https://example.com/result",
            success_text="requested result",
        ),
        decision_engine=UnexpectedEngine(),
        browser_factory=SuccessBrowser,
    )

    assert agent.state()["status"] == "completed"
    agent.close()


def test_stale_execution_reobserves_without_automatic_replay():
    agent = InteractiveAgent(
        config(),
        decision_engine=FillEngine(),
        browser_factory=StaleBrowser,
    )
    predicted = agent.predict()
    state = agent.act(predicted["page"]["fingerprint"])

    assert state["status"] == "ready"
    assert agent.browser.executions == 1
    assert state["history"][0]["executed_action"] is None
    assert state["history"][0]["action_error"].startswith("StalePage:")
    agent.close()


def test_live_frame_capture_does_not_change_observation_or_decision_state():
    agent = InteractiveAgent(
        config(),
        decision_engine=FillEngine(),
        browser_factory=FakeBrowser,
    )
    predicted = agent.predict()
    fingerprint = predicted["page"]["fingerprint"]

    frame = agent.capture_frame()
    after = agent.state()

    assert frame["screenshot"] == "c2NyZWVuc2hvdA=="
    assert frame["captured_at_epoch_ms"] > 0
    assert frame["fresh"] is True
    assert after["status"] == "predicted"
    assert after["page"]["fingerprint"] == fingerprint
    assert after["decision"] == predicted["decision"]
    agent.close()


def test_retask_preserves_browser_and_current_page_while_resetting_run_state():
    agent = InteractiveAgent(
        config(success_text="requested result"),
        decision_engine=FillEngine(),
        browser_factory=FakeBrowser,
    )
    predicted = agent.predict()
    completed = agent.act(predicted["page"]["fingerprint"])
    browser = agent.browser

    state = agent.retask(
        config(
            goal="Review the current result",
            start_url=completed["page"]["url"],
            prepared_inputs={},
            success_text=None,
        )
    )

    assert agent.browser is browser
    assert browser.closed is False
    assert state["goal"] == "Review the current result"
    assert state["page"]["url"] == "https://example.com/result"
    assert state["history"] == []
    assert len(agent.context_actions) == 1
    assert agent.context_actions[0].source_url == "https://example.com/search"
    assert agent.context_actions[0].result_url == "https://example.com/result"
    assert agent.previous_page.url == "https://example.com/search"
    assert state["elapsed_ms"] < 100
    agent.close()
