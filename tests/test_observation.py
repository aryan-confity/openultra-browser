from laya_browser.models import BrowserSnapshot, ObservedElement, ObservedOption
from laya_browser.observation import build_actions


def snapshot(*elements):
    return BrowserSnapshot(
        url="https://example.com",
        title="Docs",
        visible_text="Documentation and pricing",
        elements=tuple(elements),
    )


def test_goal_relevant_control_is_prioritized():
    result = build_actions(
        snapshot(
            ObservedElement("e0", "link", "Pricing", "a", href="/pricing"),
            ObservedElement("e1", "link", "Documentation", "a", href="/docs"),
        ),
        "Open documentation",
        {},
        6,
        False,
    )
    assert result[0].element_id == "e1"


def test_prepared_value_is_named_but_not_exposed():
    result = build_actions(
        snapshot(ObservedElement("e0", "searchbox", "Search", "input", input_type="search")),
        "Search the site",
        {"query": "secret search value"},
        6,
        False,
    )
    fill = result[0]
    assert fill.input_key == "query"
    assert "secret search value" not in fill.description
    assert "prepared 'query'" in fill.description


def test_candidate_count_is_bounded():
    elements = [ObservedElement(f"e{i}", "button", f"Button {i}", "button") for i in range(50)]
    result = build_actions(snapshot(*elements), "Click a button", {}, 12, False)
    assert len(result) == 12


def test_scroll_controls_match_observed_page_capability():
    current = snapshot()
    current = BrowserSnapshot(
        current.url,
        current.title,
        current.visible_text,
        current.elements,
        can_scroll_down=True,
    )

    result = build_actions(current, "Read more", {}, 6, False)

    assert "scroll_down" in {action.action_id for action in result}
    assert "scroll_up" not in {action.action_id for action in result}


def test_select_action_keeps_exact_observed_value():
    result = build_actions(
        snapshot(
            ObservedElement(
                "e1",
                "combobox",
                "Region",
                "select",
                options=(ObservedOption("United States", "us"),),
            )
        ),
        "Select United States",
        {},
        6,
        False,
    )

    selected = result[0]
    assert selected.option == "United States"
    assert selected.option_value == "us"


def test_direct_goal_match_prunes_wait_as_non_progress():
    result = build_actions(
        snapshot(ObservedElement("e1", "link", "Documentation", "a")),
        "Open Documentation",
        {},
        6,
        False,
    )

    assert "wait" not in {action.action_id for action in result}
