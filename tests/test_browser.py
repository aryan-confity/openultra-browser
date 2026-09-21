from laya_browser.browser import Browser


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

    browser._wait_for_navigation("https://example.com/start", timeout_seconds=0.2)

    assert next(values, "finished") == "finished"
