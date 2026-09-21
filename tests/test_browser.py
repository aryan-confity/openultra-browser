from laya_browser.browser import Browser
from laya_browser.models import ActionKind


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

    monkeypatch.setattr("laya_browser.browser.cdp", missing_target)

    browser.close()
    browser.close()

    assert calls == 1
    assert browser.target is None
