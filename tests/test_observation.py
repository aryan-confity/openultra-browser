from laya_browser.models import BrowserSnapshot, ObservedElement, ObservedOption
from laya_browser.observation import OBSERVE_SCRIPT, build_actions


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


def test_goal_matching_prepared_value_is_explicit_progress_without_disclosure():
    result = build_actions(
        snapshot(ObservedElement("e0", "searchbox", "Search", "input")),
        "Search for WDX Properties",
        {"query": "WDX Properties"},
        6,
        False,
    )

    fill = result[0]
    assert fill.goal_match
    assert "directly advances the goal: YES" in fill.description
    assert "WDX Properties" not in fill.description
    assert "wait" not in {action.action_id for action in result}


def test_satisfied_prepared_fill_is_pruned_without_exposing_its_value():
    result = build_actions(
        snapshot(
            ObservedElement(
                "e0",
                "combobox",
                "Search",
                "input",
                value="private exact query",
                submit_on_enter=True,
            )
        ),
        "Search the site",
        {"query": "private exact query"},
        6,
        False,
    )

    assert all(action.kind.value != "fill" for action in result)
    assert all("private exact query" not in action.description for action in result)
    assert result[0].kind.value == "press_enter"
    assert "still needs submission: YES" in result[0].description


def test_observer_marks_form_associated_search_textareas_as_submittable():
    assert "['INPUT', 'TEXTAREA'].includes(node.tagName)" in OBSERVE_SCRIPT
    assert "(role === 'combobox' && label.toLowerCase().includes('search'))" in OBSERVE_SCRIPT


def test_single_prepared_value_is_deterministic_progress_for_search_field():
    result = build_actions(
        snapshot(
            ObservedElement(
                "e0",
                "combobox",
                "Search",
                "textarea",
                submit_on_enter=True,
            )
        ),
        "Show the matching catalogue result",
        {"query": "private prepared phrase"},
        6,
        False,
    )

    assert result[0].kind.value == "fill"
    assert result[0].goal_match
    assert "directly advances the goal: YES" in result[0].description
    assert "private prepared phrase" not in result[0].description


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


def test_unexplored_content_marks_scroll_as_progress_instead_of_waiting():
    current = BrowserSnapshot(
        "https://example.com/results",
        "Results",
        "Results continue below",
        (ObservedElement("e1", "link", "Logo", "a", href="/"),),
        can_scroll_down=True,
    )

    result = build_actions(current, "Open the first listing details", {}, 6, False)
    by_id = {action.action_id: action for action in result}
    assert by_id["scroll_down"].goal_match
    assert "unexplored content exists below: YES" in by_id["scroll_down"].description
    assert "wait" not in by_id


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


def test_direct_goal_match_prunes_unrelated_targets():
    result = build_actions(
        snapshot(
            ObservedElement("e1", "link", "Logo", "a", href="/"),
            ObservedElement("e2", "button", "Rent", "button"),
            ObservedElement("e3", "link", "Sell your property", "a", href="/sell"),
        ),
        "Open the Rent page for WDX Properties",
        {},
        6,
        False,
    )

    action_ids = {action.action_id for action in result}
    assert "click_e2" in action_ids
    assert "click_e1" not in action_ids
    assert "click_e3" not in action_ids


def test_generic_navigation_words_do_not_create_a_direct_match():
    result = build_actions(
        snapshot(ObservedElement("e1", "link", "Search Google Images", "a")),
        "Search Google for WDX Properties and open the official website",
        {},
        6,
        False,
    )

    click = next(action for action in result if action.action_id == "click_e1")
    assert not click.goal_match
    assert "wait" in {action.action_id for action in result}


def test_query_terms_in_destination_do_not_create_a_direct_match():
    result = build_actions(
        snapshot(
            ObservedElement(
                "e1",
                "link",
                "AI Mode",
                "a",
                href="https://example.com/search?q=wdx+properties",
            ),
            ObservedElement(
                "e2",
                "link",
                "WDX Properties official website",
                "a",
                href="https://wdxproperties.com",
            ),
        ),
        "Open the official WDX Properties website",
        {},
        6,
        False,
    )

    by_id = {action.action_id: action for action in result}
    assert "click_e1" not in by_id
    assert by_id["click_e2"].goal_match


def test_success_url_destination_is_deterministic_progress():
    result = build_actions(
        snapshot(
            ObservedElement(
                "e1",
                "link",
                "Map of WDX Properties",
                "a",
                href="https://maps.example/wdx",
            ),
            ObservedElement(
                "e2",
                "link",
                "Website",
                "a",
                href="https://wdxproperties.com/",
            ),
        ),
        "Open the official WDX Properties website",
        {},
        6,
        False,
        success_url_prefix="https://wdxproperties.com",
    )

    assert result[0].action_id == "click_e2"
    assert result[0].goal_match
    assert "matches the required success URL: YES" in result[0].description


def test_success_url_regex_prioritizes_detail_over_index():
    result = build_actions(
        snapshot(
            ObservedElement(
                "e1",
                "link",
                "Rent",
                "a",
                href="https://wdxproperties.com/properties/rent/Bangkok",
            ),
            ObservedElement(
                "e2",
                "link",
                "View details for Circle Condominium",
                "a",
                href="https://wdxproperties.com/properties/circle-condominium-123",
            ),
        ),
        "Open the first rental listing details",
        {},
        6,
        False,
        success_url_regex=r"^https://wdxproperties\.com/properties/(?!rent/|buy/)[^/?#]+$",
    )

    assert result[0].action_id == "click_e2"
    assert result[0].goal_match


def test_explicit_non_start_domain_prunes_same_site_search_distractions():
    result = build_actions(
        snapshot(
            ObservedElement(
                "e1",
                "link",
                "Map of WDX Properties",
                "a",
                href="https://www.google.com/maps/place/WDX+Properties",
            ),
            ObservedElement(
                "e2",
                "link",
                "WDX Properties",
                "a",
                href="https://wdxproperties.com/",
            ),
        ),
        "Open the official WDX Properties website",
        {},
        6,
        False,
        preferred_domains=frozenset({"wdxproperties.com", "www.wdxproperties.com"}),
    )

    assert [action.action_id for action in result if action.kind.value == "click"] == ["click_e2"]
    assert "explicitly approved non-start domain: YES" in result[0].description


def test_pending_cross_site_transition_prefers_exploration_to_same_site_distraction():
    current = BrowserSnapshot(
        "https://www.google.com/search?q=wdx",
        "Results",
        "Search results",
        (
            ObservedElement(
                "e1",
                "link",
                "Map of WDX Properties",
                "a",
                href="https://www.google.com/maps/place/WDX+Properties",
            ),
        ),
        can_scroll_down=True,
    )

    result = build_actions(
        current,
        "Open the official WDX Properties website",
        {},
        6,
        False,
        preferred_domains=frozenset({"wdxproperties.com", "www.wdxproperties.com"}),
    )

    assert "click_e1" not in {action.action_id for action in result}
    assert next(action for action in result if action.action_id == "scroll_down").goal_match
