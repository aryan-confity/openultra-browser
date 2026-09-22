import pytest

from openultra_browser.browser import Browser, ExecutionUncertain, StalePage
from openultra_browser.models import (
    ActionKind,
    BrowserSnapshot,
    CandidateAction,
    ObservedElement,
)


def test_navigation_settlement_waits_for_new_document():
    browser = object.__new__(Browser)
    values = iter(
        [
            "https://example.com/start",
            "https://example.com/start",
            "https://example.com/docs",
            "complete",
        ]
    )
    browser.evaluate = lambda _expression: next(values)
    browser._wait_for_semantic_quiet = lambda **_kwargs: None

    assert browser._wait_for_navigation("https://example.com/start", timeout_seconds=0.2)

    assert next(values, "finished") == "finished"


def test_search_submit_uses_form_semantics_when_enter_does_not_navigate():
    browser = object.__new__(Browser)
    evaluations = []
    navigation_results = iter([False, True])

    def evaluate(expression, *, await_promise=False):
        evaluations.append((expression, await_promise))
        if "requestSubmit" in expression:
            return True
        return None

    browser.evaluate = evaluate
    browser._wait_for_navigation = lambda _url: next(navigation_results)

    browser._settle(
        ActionKind.PRESS_ENTER,
        before_url="https://example.com/search",
        expect_navigation=True,
    )

    assert any("requestSubmit" in expression for expression, _ in evaluations)


def test_close_is_idempotent_when_target_is_already_absent(monkeypatch):
    browser = object.__new__(Browser)
    browser.target = "closed-target"
    calls = 0

    def missing_target(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise RuntimeError({"code": -32602, "message": "No target with given id found"})

    monkeypatch.setattr("openultra_browser.browser.cdp", missing_target)

    browser.close()
    browser.close()

    assert calls == 1
    assert browser.target is None


def test_submit_transport_response_is_execution_evidence_without_retry():
    browser = object.__new__(Browser)
    browser.evaluate = lambda _expression, **_kwargs: {
        "verified": False,
        "status": 502,
        "path": "/api/listProperty",
    }

    assert browser._settle(
        ActionKind.CLICK,
        before_url="https://example.com/form",
        submission_started_at=100.0,
    )
    assert "HTTP 502" in browser.last_action_evidence
    assert "/api/listProperty" in browser.last_action_evidence


def test_submit_transport_success_is_execution_evidence():
    browser = object.__new__(Browser)
    browser.evaluate = lambda _expression, **_kwargs: {
        "verified": True,
        "status": 201,
        "path": "/api/forms",
    }

    assert browser._settle(
        ActionKind.CLICK,
        before_url="https://example.com/form",
        submission_started_at=100.0,
    )
    assert "HTTP 201" in browser.last_action_evidence


def test_new_child_tab_becomes_the_active_observation_target():
    browser = object.__new__(Browser)
    browser.targets = ["original"]
    browser.target = "original"
    browser.browser_context_id = "context-1"
    pages = iter(
        [
            {"original": {"targetId": "original", "type": "page"}},
            {
                "original": {"targetId": "original", "type": "page"},
                "popup": {
                    "targetId": "popup",
                    "type": "page",
                    "openerId": "original",
                    "url": "https://example.com/details",
                },
            },
        ]
    )
    browser._page_targets = lambda: next(pages)
    browser._bind_target = lambda target: setattr(browser, "target", target)
    browser.evaluate = lambda _expression: "complete"
    browser._wait_for_semantic_quiet = lambda **_kwargs: None

    assert browser._adopt_new_target({"original"}, timeout_seconds=0.2)
    assert browser.target == "popup"


def test_unrelated_existing_tabs_are_not_adopted():
    browser = object.__new__(Browser)
    browser.targets = ["original"]
    browser.target = "original"
    browser.browser_context_id = "context-1"
    browser._page_targets = lambda: {
        "original": {"targetId": "original", "type": "page"},
        "existing": {
            "targetId": "existing",
            "type": "page",
            "url": "https://example.net",
        },
    }

    assert not browser._adopt_new_target(
        {"original", "existing"}, timeout_seconds=0
    )
    assert browser.target == "original"


def test_blank_child_target_is_not_adopted_before_it_commits_a_url():
    browser = object.__new__(Browser)
    browser.targets = ["original"]
    browser.target = "original"
    browser.browser_context_id = "context-1"
    browser._page_targets = lambda: {
        "original": {
            "targetId": "original",
            "type": "page",
            "url": "https://example.com/flights",
        },
        "blank-popup": {
            "targetId": "blank-popup",
            "type": "page",
            "openerId": "original",
            "url": "about:blank",
        },
    }

    assert not browser._adopt_new_target({"original"}, timeout_seconds=0)
    assert browser.target == "original"


def test_blank_child_target_is_adopted_after_http_commit():
    browser = object.__new__(Browser)
    browser.targets = ["original"]
    browser.target = "original"
    browser.browser_context_id = "context-1"
    pages = iter(
        [
            {
                "original": {"targetId": "original", "url": "https://example.com"},
                "popup": {
                    "targetId": "popup",
                    "openerId": "original",
                    "url": "about:blank",
                },
            },
            {
                "original": {"targetId": "original", "url": "https://example.com"},
                "popup": {
                    "targetId": "popup",
                    "openerId": "original",
                    "url": "https://example.com/details",
                },
            },
        ]
    )
    browser._page_targets = lambda: next(pages)
    browser._bind_target = lambda target: setattr(browser, "target", target)
    browser.evaluate = lambda _expression: "complete"
    browser._wait_for_semantic_quiet = lambda **_kwargs: None

    assert browser._adopt_new_target({"original"}, timeout_seconds=0.1)
    assert browser.target == "popup"


def test_navigation_history_is_authoritative_for_back_capability():
    browser = object.__new__(Browser)
    browser.call = lambda _method: {
        "currentIndex": 2,
        "entries": [
            {"url": "about:blank"},
            {"url": "https://example.com/search"},
            {"url": "https://example.com/details"},
        ],
    }

    assert browser._can_go_back(False)


def test_internal_blank_history_does_not_offer_back_navigation():
    browser = object.__new__(Browser)
    browser.call = lambda _method: {
        "currentIndex": 1,
        "entries": [
            {"url": "about:blank"},
            {"url": "https://example.com"},
        ],
    }

    assert not browser._can_go_back(True)


def test_semantic_fingerprint_ignores_ephemeral_dom_identity():
    left = BrowserSnapshot(
        "https://example.com",
        "Example",
        "Visible content",
        (ObservedElement("e1", "button", "Next image", "button", guard="old"),),
    )
    right = BrowserSnapshot(
        "https://example.com",
        "Example",
        "Visible content",
        (ObservedElement("e9", "button", "Next image", "button", guard="new"),),
    )

    assert left.fingerprint != right.fingerprint
    assert left.semantic_fingerprint == right.semantic_fingerprint


def test_active_target_recovers_to_the_latest_owned_http_page():
    browser = object.__new__(Browser)
    browser.targets = ["original", "blank-popup"]
    browser.target = "blank-popup"
    browser.browser_context_id = "context-1"
    browser._page_targets = lambda: {
        "original": {
            "targetId": "original",
            "type": "page",
            "url": "https://example.com/flights",
        },
        "blank-popup": {
            "targetId": "blank-popup",
            "type": "page",
            "url": "about:blank",
        },
    }
    browser.evaluate = lambda _expression: (
        "about:blank" if browser.target == "blank-popup" else "https://example.com/flights"
    )
    browser._bind_target = lambda target: setattr(browser, "target", target)

    assert browser.ensure_active_http_target() == "https://example.com/flights"
    assert browser.target == "original"


def test_observed_tabs_keep_mru_order_and_exclude_blank_targets():
    browser = object.__new__(Browser)
    browser.targets = ["older", "blank", "active"]
    browser.target = "active"
    browser._page_targets = lambda: {
        "older": {
            "targetId": "older",
            "title": "YouTube",
            "url": "https://youtube.com/results",
        },
        "blank": {"targetId": "blank", "title": "", "url": "about:blank"},
        "active": {
            "targetId": "active",
            "title": "Flights",
            "url": "https://google.com/travel/flights",
        },
    }

    tabs = browser._observed_tabs()

    assert [tab.target_id for tab in tabs] == ["older", "active"]
    assert [tab.active for tab in tabs] == [False, True]


def test_switch_tab_executes_only_an_owned_committed_target():
    browser = object.__new__(Browser)
    browser.targets = ["older", "active"]
    browser.target = "active"
    browser.last_action_evidence = None
    browser._page_targets = lambda: {
        "older": {
            "targetId": "older",
            "title": "YouTube",
            "url": "https://youtube.com/results",
        },
        "active": {
            "targetId": "active",
            "title": "Flights",
            "url": "https://google.com/travel/flights",
        },
    }
    browser.evaluate = lambda _expression: "https://google.com/travel/flights"
    browser._bind_target = lambda target: setattr(browser, "target", target)
    browser._wait_for_semantic_quiet = lambda **_kwargs: None
    snapshot = BrowserSnapshot(
        "https://google.com/travel/flights", "Flights", "Flights", ()
    )
    action = CandidateAction(
        "switch_tab_older",
        ActionKind.SWITCH_TAB,
        "Focus YouTube",
        browser_target_id="older",
    )

    assert browser.act(action, snapshot, {})
    assert browser.target == "older"
    assert "selected existing browser tab" in browser.last_action_evidence


def _action_browser(snapshot):
    browser = object.__new__(Browser)
    calls = []
    browser.target = "current"
    browser.targets = ["current"]
    browser._page_targets = lambda: {"current": {"targetId": "current", "url": snapshot.url}}
    browser.call = lambda method, **params: calls.append((method, params)) or {}
    browser.evaluate = lambda expression, **_kwargs: (
        {"x": 30, "y": 40, "autocomplete": False}
        if "expectedGuard" in expression
        else snapshot.url
    )
    browser._settle = lambda *_args, **_kwargs: False
    browser._adopt_new_target = lambda *_args, **_kwargs: False
    return browser, calls


def test_observe_uses_one_observation_and_attaches_browser_history(monkeypatch):
    snapshot = BrowserSnapshot("https://example.com", "Page", "Page text", ())
    browser, _calls = _action_browser(snapshot)
    browser.text_limit = 1200
    browser.ensure_active_http_target = lambda: snapshot.url
    browser._can_go_back = lambda _fallback: True
    browser._observed_tabs = lambda: ()
    monkeypatch.setattr("openultra_browser.browser.decode_observation", lambda _raw: snapshot)

    observed = browser.observe()

    assert observed.can_go_back is True
    assert observed.visible_text == "Page text"


@pytest.mark.parametrize("kind,delta", [(ActionKind.SCROLL_DOWN, 560), (ActionKind.SCROLL_UP, -560)])
def test_scroll_actions_dispatch_directional_wheel(kind, delta):
    snapshot = BrowserSnapshot("https://example.com", "Page", "Text", ())
    browser, calls = _action_browser(snapshot)
    action = CandidateAction("scroll", kind, "Scroll")

    assert browser.act(action, snapshot, {}) is False
    assert calls[0][0] == "Input.dispatchMouseEvent"
    assert calls[0][1]["deltaY"] == delta


def test_back_wait_and_terminal_actions_do_not_click():
    snapshot = BrowserSnapshot("https://example.com", "Page", "Text", ())
    browser, calls = _action_browser(snapshot)
    browser.evaluate = lambda _expression, **_kwargs: snapshot.url
    for kind in (ActionKind.BACK, ActionKind.WAIT, ActionKind.DONE, ActionKind.BLOCKED):
        assert browser.act(CandidateAction(kind.value, kind, kind.value), snapshot, {}) is False
    assert calls == []


@pytest.mark.parametrize("kind", [ActionKind.CLICK, ActionKind.FILL, ActionKind.PRESS_ENTER, ActionKind.SELECT])
def test_element_actions_send_only_observed_target(kind):
    element = ObservedElement("e1", "button" if kind == ActionKind.CLICK else "textbox", "Target", "button", guard="guard")
    snapshot = BrowserSnapshot("https://example.com", "Page", "Text", (element,))
    browser, calls = _action_browser(snapshot)
    action = CandidateAction(
        kind.value, kind, "Target", "e1",
        input_key="name" if kind == ActionKind.FILL else None,
        option="Option" if kind == ActionKind.SELECT else None,
        option_value="value" if kind == ActionKind.SELECT else None,
    )

    assert browser.act(action, snapshot, {"name": "literal value"}) is False
    if kind == ActionKind.CLICK:
        assert [event[1]["type"] for event in calls] == ["mousePressed", "mouseReleased"]
    elif kind == ActionKind.FILL:
        assert ("Input.insertText", {"text": "literal value"}) in calls
    elif kind == ActionKind.PRESS_ENTER:
        assert [event[1]["type"] for event in calls] == ["rawKeyDown", "keyUp"]
    else:
        assert calls == []


def test_stale_and_missing_targets_fail_before_input():
    snapshot = BrowserSnapshot("https://example.com", "Page", "Text", ())
    browser, calls = _action_browser(snapshot)
    browser.evaluate = lambda _expression, **_kwargs: "https://example.com/changed"
    with pytest.raises(StalePage):
        browser.act(CandidateAction("click", ActionKind.CLICK, "Target", "e1"), snapshot, {})
    assert calls == []

    browser.evaluate = lambda _expression, **_kwargs: snapshot.url
    with pytest.raises(ValueError):
        browser.act(CandidateAction("click", ActionKind.CLICK, "Target"), snapshot, {})
    with pytest.raises(RuntimeError, match="absent"):
        browser.act(CandidateAction("click", ActionKind.CLICK, "Target", "e1"), snapshot, {})


def test_dispatch_failure_after_input_is_not_safe_to_retry():
    element = ObservedElement("e1", "button", "Next", "button", guard="guard")
    snapshot = BrowserSnapshot("https://example.com", "Page", "Text", (element,))
    browser, _calls = _action_browser(snapshot)
    browser.call = lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("transport lost"))
    with pytest.raises(ExecutionUncertain, match="completion could not be confirmed"):
        browser.act(CandidateAction("click", ActionKind.CLICK, "Next", "e1"), snapshot, {})
