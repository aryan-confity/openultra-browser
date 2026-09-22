from openultra_browser.agent import BrowserAgent
from openultra_browser.browser import ExecutionUncertain, StalePage
from openultra_browser.config import RunConfig
from openultra_browser.models import ActionKind, BrowserSnapshot, ModelDecision


class FakeBrowser:
    def __init__(self, _url, *, text_limit):
        self.snapshot = BrowserSnapshot(
            "https://example.com/done",
            "Done",
            "The requested result is visible",
            (),
        )

    @property
    def url(self):
        return self.snapshot.url

    def observe(self):
        return self.snapshot

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None


class DoneEngine:
    def decide(self, **_kwargs):
        return ModelDecision(
            proposed_action="done",
            probabilities={
                "scroll_down": 0.0,
                "scroll_up": 0.0,
                "back": 0.0,
                "wait": 0.0,
                "done": 1.0,
            },
            confidence=1.0,
            goal_probability=1.0,
            stuck_probability=0.0,
            inference_ms=1.0,
            input_tokens=10,
        )


class UnexpectedEngine:
    def decide(self, **_kwargs):
        raise AssertionError("A verified page must complete before model inference")


class BackEngine:
    def decide(self, **_kwargs):
        return ModelDecision(
            proposed_action="back",
            probabilities={"back": 1.0, "wait": 0.0, "done": 0.0},
            confidence=1.0,
            goal_probability=0.0,
            stuck_probability=0.0,
            inference_ms=1.0,
            input_tokens=10,
        )


class LoginEngine:
    def decide(self, **_kwargs):
        return ModelDecision(
            proposed_action="wait",
            probabilities={"wait": 1.0},
            confidence=1.0,
            goal_probability=0.0,
            stuck_probability=0.0,
            inference_ms=1.0,
            input_tokens=10,
            login_probability=0.95,
        )


class AuthenticationBrowser(FakeBrowser):
    def __init__(self, url, *, text_limit):
        super().__init__(url, text_limit=text_limit)
        self.snapshot = BrowserSnapshot(
            "https://accounts.example.com/signin",
            "Sign in",
            "Sign in to continue",
            (),
        )


class UncertainBrowser(FakeBrowser):
    def __init__(self, url, *, text_limit):
        super().__init__(url, text_limit=text_limit)
        self.snapshot = BrowserSnapshot(
            self.snapshot.url,
            self.snapshot.title,
            self.snapshot.visible_text,
            self.snapshot.elements,
            can_go_back=True,
        )

    def act(self, *_args):
        raise ExecutionUncertain("input may have executed")


class StaleThenVerifiedBrowser(UncertainBrowser):
    def __init__(self, url, *, text_limit):
        super().__init__(url, text_limit=text_limit)
        self.snapshot = BrowserSnapshot(
            "https://example.com/start",
            "Start",
            "Nothing completed yet",
            (),
            can_go_back=True,
        )

    def act(self, *_args):
        self.snapshot = BrowserSnapshot(
            "https://example.com/done",
            "Done",
            "The requested result is visible",
            (),
        )
        raise StalePage("page changed before input")


def test_done_proposal_never_claims_unverified_success():
    result = BrowserAgent(
        RunConfig(goal="Finish", start_url="https://example.com"),
        decision_engine=DoneEngine(),
        browser_factory=FakeBrowser,
    ).run()

    assert result.status != "completed"
    assert result.reason


def test_success_text_completes_before_model_inference():
    result = BrowserAgent(
        RunConfig(
            goal="Show the result",
            start_url="https://example.com",
            success_text="requested result",
        ),
        decision_engine=UnexpectedEngine(),
        browser_factory=FakeBrowser,
    ).run()

    assert result.status == "completed"


def test_all_configured_success_checks_must_pass():
    result = BrowserAgent(
        RunConfig(
            goal="Show the result",
            start_url="https://example.com",
            success_text="requested result",
            success_url_prefix="https://example.com/done",
        ),
        decision_engine=UnexpectedEngine(),
        browser_factory=FakeBrowser,
    ).run()

    assert result.status == "completed"
    assert "URL prefix" in result.reason


def test_uncertain_execution_stops_without_retry():
    result = BrowserAgent(
        RunConfig(goal="Go back", start_url="https://example.com"),
        decision_engine=BackEngine(),
        browser_factory=UncertainBrowser,
    ).run()

    assert result.status == "needs_verification"
    assert len(result.steps) == 1


def test_pre_input_staleness_reobserves_without_marking_action_executed():
    result = BrowserAgent(
        RunConfig(
            goal="Go back",
            start_url="https://example.com",
            success_text="requested result",
        ),
        decision_engine=BackEngine(),
        browser_factory=StaleThenVerifiedBrowser,
    ).run()

    assert result.status == "completed"
    assert len(result.steps) == 1
    assert result.steps[0].executed_action is None
    assert result.steps[0].action_error.startswith("StalePage:")


class ScrollingBrowser(FakeBrowser):
    def __init__(self, url, *, text_limit):
        super().__init__(url, text_limit=text_limit)
        self.snapshot = BrowserSnapshot(
            "https://example.com/results",
            "Results",
            "More content",
            (),
            can_scroll_up=True,
            can_scroll_down=True,
            scroll_y=1120,
        )

    def act(self, action, _snapshot, _prepared_inputs):
        assert action.kind == ActionKind.SCROLL_UP
        self.snapshot = BrowserSnapshot(
            self.snapshot.url,
            self.snapshot.title,
            self.snapshot.visible_text,
            (),
            can_scroll_up=True,
            can_scroll_down=True,
            scroll_y=560,
        )


class ScrollChoiceEngine:
    def decide(self, *, actions, **_kwargs):
        return ModelDecision(
            proposed_action="scroll_up",
            probabilities={
                action.action_id: float(action.action_id == "scroll_up") for action in actions
            },
            confidence=1.0,
            goal_probability=0.0,
            stuck_probability=0.0,
            inference_ms=1.0,
            input_tokens=10,
        )


def test_cli_scroll_correction_completes_after_one_verified_upward_move():
    result = BrowserAgent(
        RunConfig(
            goal="you're just scrolling down scroll up",
            start_url="https://example.com/results",
        ),
        decision_engine=ScrollChoiceEngine(),
        browser_factory=ScrollingBrowser,
    ).run()

    assert result.status == "completed"
    assert len(result.steps) == 1
    assert result.steps[0].executed_action == "scroll_up"


def test_authentication_boundary_stops_without_attempting_credentials():
    result = BrowserAgent(
        RunConfig(goal="Dislike this video", start_url="https://example.com/done"),
        decision_engine=LoginEngine(),
        browser_factory=AuthenticationBrowser,
    ).run()

    assert result.status == "needs_login"
    assert not result.steps
