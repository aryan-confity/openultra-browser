from openultra_browser.models import (
    ActionKind,
    BrowserSnapshot,
    ObservedElement,
    ObservedOption,
    ObservedTab,
)
from openultra_browser.observation import OBSERVE_SCRIPT, build_actions


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


def test_search_already_represented_by_url_is_not_submitted_again():
    current = BrowserSnapshot(
        "https://www.youtube.com/results?search_query=Neon+AI+stream",
        "Results",
        "Neon AI stream",
        (
            ObservedElement(
                "e1",
                "combobox",
                "Search",
                "input",
                value="Neon AI stream",
                submit_on_enter=True,
            ),
            ObservedElement(
                "e2",
                "link",
                "Neon AI Stream Is A Disaster",
                "a",
                href="/watch?v=video",
            ),
        ),
    )

    result = build_actions(
        current,
        "click on it",
        {"search query": "Neon AI stream"},
        8,
        False,
        context_goal=(
            "Go to youtube.com and search for Neon AI stream and click on it "
            "and dislike his video"
        ),
    )

    action_ids = {action.action_id for action in result}
    assert "submit_e1" not in action_ids
    assert "click_e2" in action_ids
    assert "click_e1" not in action_ids


def test_retained_search_field_cannot_hijack_a_later_click_step():
    current = BrowserSnapshot(
        "https://www.youtube.com/watch?v=video",
        "Video",
        "Video page",
        (
            ObservedElement(
                "search",
                "combobox",
                "Search",
                "input",
                value="Neon AI stream",
                submit_on_enter=True,
            ),
            ObservedElement("dislike", "button", "Dislike", "button", pressed=False),
        ),
    )

    result = build_actions(
        current,
        "click on it",
        {"search query": "Neon AI stream"},
        20,
        False,
        context_goal=(
            "Go to youtube.com and search for Neon AI stream and click on it "
            "and dislike his video"
        ),
    )

    assert not any(action.kind == ActionKind.PRESS_ENTER for action in result)
    assert any(action.element_id == "dislike" for action in result)


def test_matching_home_recommendation_cannot_erase_pending_search_fill():
    current = BrowserSnapshot(
        "https://www.youtube.com/",
        "YouTube",
        "Mr Robot recommendations",
        (
            ObservedElement(
                "search",
                "combobox",
                "Search",
                "input",
                submit_on_enter=True,
            ),
            ObservedElement(
                "recommendation",
                "link",
                "Mr Robot Review",
                "a",
                href="/watch?v=review",
            ),
        ),
        can_scroll_down=True,
    )

    result = build_actions(
        current,
        "search for mr robot and click on search",
        {"search query": "mr robot"},
        8,
        False,
    )

    assert [action.action_id for action in result if action.kind == ActionKind.FILL] == [
        "fill_search_search query"
    ]
    assert not any(action.element_id == "recommendation" for action in result)


def test_filled_search_submit_excludes_suggestions_and_recommendations():
    current = BrowserSnapshot(
        "https://www.youtube.com/",
        "YouTube",
        "Mr Robot suggestions and recommendations",
        (
            ObservedElement(
                "search",
                "combobox",
                "Search",
                "input",
                value="mr robot",
                submit_on_enter=True,
            ),
            ObservedElement("suggestion", "button", "Mr. Robot", "button"),
            ObservedElement(
                "recommendation",
                "link",
                "Mr Robot Review",
                "a",
                href="/watch?v=review",
            ),
        ),
        can_scroll_down=True,
    )

    result = build_actions(
        current,
        "search for mr robot and click on search",
        {"search query": "mr robot"},
        8,
        False,
    )

    assert result[0].action_id == "submit_search"
    assert not any(action.kind == ActionKind.CLICK for action in result)


def test_observer_marks_form_associated_search_textareas_as_submittable():
    assert "['INPUT', 'TEXTAREA'].includes(node.tagName)" in OBSERVE_SCRIPT
    assert "(role === 'combobox' && label.toLowerCase().includes('search'))" in OBSERVE_SCRIPT


def test_observer_includes_broad_interactive_surfaces_and_rejects_covered_targets():
    assert "[onclick]" in OBSERVE_SCRIPT
    assert "[tabindex]:not([tabindex=\"-1\"])" in OBSERVE_SCRIPT
    assert "getComputedStyle(node).cursor !== 'pointer'" in OBSERVE_SCRIPT
    assert "!node.contains(top)" in OBSERVE_SCRIPT


def test_date_control_value_is_available_only_to_goal_matching():
    departure = ObservedElement(
        "e1",
        "textbox",
        "Departure",
        "input",
        input_type="text",
        value="Thu, Jan 28",
    )
    secret = ObservedElement(
        "e2",
        "textbox",
        "Account number",
        "input",
        input_type="text",
        value="99887766",
    )

    assert "Thu, Jan 28" in departure.goal_description
    assert "99887766" not in secret.goal_description
    result = build_actions(snapshot(departure, secret), "Click Thu, Jan 28", {}, 8, False)
    assert result[0].element_id == "e1"
    assert result[0].goal_match
    assert "Thu, Jan 28" not in result[0].description


def test_complete_semantic_match_prunes_weaker_partial_matches():
    current = snapshot(
        ObservedElement(
            "departure",
            "textbox",
            "Departure",
            "input",
            value="Thu, Jan 28",
        ),
        ObservedElement(
            "flight",
            "button",
            "Flight details for Thursday January 28",
            "button",
        ),
    )

    result = build_actions(current, "Click Thu Jan 28", {}, 12, False)

    assert [action.element_id for action in result] == ["departure"]


def test_calendar_planner_navigates_toward_an_offscreen_requested_date():
    current = snapshot(
        ObservedElement(
            "december",
            "button",
            "Monday, December 28, 2026",
            "div",
            date_value="2026-12-28",
        ),
        ObservedElement(
            "january",
            "button",
            "Thursday, January 28, 2027",
            "div",
            date_value="2027-01-28",
        ),
        ObservedElement("previous", "button", "Previous", "button"),
        ObservedElement("next", "button", "Next", "button"),
    )

    result = build_actions(current, "change the date to September 28th", {}, 12, False)

    assert [action.element_id for action in result] == ["previous"]
    assert "2026-09-28 is previous" in result[0].description


def test_calendar_planner_selects_the_exact_visible_date():
    current = snapshot(
        ObservedElement(
            "target",
            "button",
            "Monday, September 28, 2026",
            "div",
            date_value="2026-09-28",
        ),
        ObservedElement(
            "other",
            "button",
            "Wednesday, October 28, 2026",
            "div",
            date_value="2026-10-28",
        ),
        ObservedElement("previous", "button", "Previous", "button"),
    )

    result = build_actions(current, "change the date to September 28th", {}, 12, False)

    assert [action.element_id for action in result] == ["target"]
    assert "Exact requested calendar date 2026-09-28" in result[0].description


def test_calendar_transition_waits_instead_of_scrolling_or_confirming():
    current = BrowserSnapshot(
        "https://example.com/flights",
        "Flights",
        "Calendar is moving",
        (
            ObservedElement(
                "done",
                "button",
                "Done. Search for round trip flights",
                "button",
            ),
        ),
        can_scroll_down=True,
        can_go_back=True,
    )

    result = build_actions(current, "change the date to September 28th", {}, 12, False)

    assert [action.action_id for action in result] == ["wait"]
    assert result[0].goal_match


def test_offscreen_open_calendar_recovers_the_viewport_before_waiting():
    current = BrowserSnapshot(
        "https://example.com/flights",
        "Flights",
        "Calendar is above the viewport",
        (
            ObservedElement(
                "done",
                "button",
                "Done. Search for round trip flights",
                "button",
            ),
        ),
        can_scroll_up=True,
        can_scroll_down=True,
    )

    result = build_actions(current, "change the date to September 28th", {}, 12, False)

    assert [action.action_id for action in result] == ["scroll_up"]
    assert result[0].goal_match


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


def test_named_open_tab_is_a_direct_typed_target():
    current = BrowserSnapshot(
        url="https://accounts.google.com/signin",
        title="Sign in",
        visible_text="Sign in",
        elements=(),
        tabs=(
            ObservedTab("youtube", "YouTube", "https://www.youtube.com/results", False),
            ObservedTab("signin", "Sign in", "https://accounts.google.com/signin", True),
        ),
    )

    result = build_actions(current, "Switch to the YouTube tab", {}, 8, False)

    assert [action.kind for action in result] == [ActionKind.SWITCH_TAB]
    assert result[0].browser_target_id == "youtube"
    assert result[0].goal_match


def test_go_back_uses_previous_tab_when_current_tab_has_no_history():
    current = BrowserSnapshot(
        url="https://accounts.google.com/signin",
        title="Sign in",
        visible_text="Sign in",
        elements=(),
        can_go_back=False,
        tabs=(
            ObservedTab("flights", "Google Flights", "https://google.com/travel/flights"),
            ObservedTab("signin", "Sign in", "https://accounts.google.com/signin", True),
        ),
    )

    result = build_actions(current, "Go back", {}, 8, False)

    assert [action.kind for action in result] == [ActionKind.SWITCH_TAB]
    assert result[0].browser_target_id == "flights"
    assert result[0].goal_match


def test_go_back_marks_browser_history_as_direct_progress():
    current = BrowserSnapshot(
        url="https://example.com/details",
        title="Details",
        visible_text="Details",
        elements=(),
        can_go_back=True,
    )

    result = build_actions(current, "Go back", {}, 8, False)

    back = next(action for action in result if action.kind == ActionKind.BACK)
    assert back.goal_match


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


def test_semantic_card_remains_eligible_when_lexical_goal_matching_misses():
    current = BrowserSnapshot(
        "https://wdxproperties.com/",
        "WDX Properties",
        "Homes for Rent in Bangkok Circle Condominium",
        (
            ObservedElement(
                "card",
                "link",
                "View details for 2-BR Condo Circle Condominium",
                "a",
                href="/properties/2-br-condo-circle-condominium-356560",
            ),
        ),
        can_scroll_down=True,
    )

    result = build_actions(current, "click on any of their listing page", {}, 6, False)
    by_id = {action.action_id: action for action in result}

    assert "click_card" in by_id
    assert "scroll_down" in by_id
    assert by_id["click_card"].goal_match
    assert "Visible content-detail destination" in by_id["click_card"].description
    assert not by_id["scroll_down"].goal_match
    assert "unexplored content exists below: YES" not in by_id["scroll_down"].description


def test_watch_link_is_a_content_target_for_any_video_request():
    current = BrowserSnapshot(
        "https://www.youtube.com/results?search_query=mr+robot",
        "YouTube",
        "Mr Robot results",
        (
            ObservedElement(
                "video",
                "link",
                "Kernel Panic On Adderall | Mr. Robot",
                "a",
                href="/watch?v=0eAhMeswQdg",
            ),
        ),
        can_scroll_down=True,
    )

    result = build_actions(current, "play any video", {}, 6, False)

    assert result[0].action_id == "click_video"
    assert result[0].goal_match


def test_watch_page_prioritizes_playback_control_over_recommendations():
    current = BrowserSnapshot(
        "https://www.youtube.com/watch?v=current",
        "YouTube",
        "Current video",
        (
            ObservedElement("play", "button", "Play (k)", "button"),
            ObservedElement(
                "like",
                "button",
                "like this video along with 100 other people",
                "button",
            ),
            ObservedElement(
                "recommendation",
                "link",
                "Another Mr Robot Clip",
                "a",
                href="/watch?v=other",
            ),
        ),
        can_scroll_down=True,
    )

    result = build_actions(current, "play any video", {}, 6, False)

    assert [action.action_id for action in result if action.kind == ActionKind.CLICK] == [
        "click_play"
    ]


def test_stateful_social_control_is_available_only_when_requested():
    current = snapshot(
        ObservedElement("like", "button", "like this video", "button"),
        ObservedElement("dislike", "button", "Dislike", "button"),
    )

    play_actions = build_actions(current, "play any video", {}, 6, False)
    dislike_actions = build_actions(current, "dislike this video", {}, 6, False)

    assert not any(action.element_id in {"like", "dislike"} for action in play_actions)
    assert [action.element_id for action in dislike_actions if action.kind == ActionKind.CLICK] == [
        "dislike"
    ]


def test_generic_logo_is_removed_when_meaningful_controls_exist():
    current = BrowserSnapshot(
        "https://wdxproperties.com/",
        "WDX Properties",
        "Find a home",
        (
            ObservedElement("logo", "link", "Logo", "a", href="/"),
            ObservedElement("rent", "button", "Rent", "button"),
            ObservedElement("condos", "button", "Condos for rent", "button"),
        ),
        can_scroll_down=True,
    )

    result = build_actions(current, "click on any of their listing page", {}, 8, False)

    assert "click_logo" not in {action.action_id for action in result}
    assert {"click_rent", "click_condos"} <= {action.action_id for action in result}


def test_visible_busy_state_offers_wait_without_hiding_ready_targets():
    current = snapshot(
        ObservedElement("e1", "button", "Load results", "button", busy=True),
        ObservedElement("e2", "link", "Documentation", "a", href="/docs"),
    )

    result = build_actions(current, "Open Documentation", {}, 8, False)

    assert "click_e2" in {action.action_id for action in result}
    assert "wait" in {action.action_id for action in result}


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


def test_weak_relation_words_do_not_make_a_backlink_goal_progress():
    result = build_actions(
        snapshot(
            ObservedElement(
                "e1",
                "link",
                "Browse rentals in Bangkok",
                "a",
                href="/properties/rent/Bangkok",
            )
        ),
        "get the first option details page and the final page",
        {},
        6,
        False,
    )

    click = next(action for action in result if action.action_id == "click_e1")
    assert not click.goal_match


def test_first_requested_result_marks_first_dom_matching_target():
    result = build_actions(
        snapshot(
            ObservedElement("e1", "link", "View details for Alpha", "a", href="/alpha"),
            ObservedElement("e2", "link", "View details for Beta", "a", href="/beta"),
        ),
        "Open the first listing details",
        {},
        6,
        False,
    )

    assert result[0].action_id == "click_e1"
    assert "FIRST visible matching target: YES" in result[0].description
    assert "click_e2" not in {action.action_id for action in result}


def test_completed_ordered_task_exposes_only_completion():
    result = build_actions(
        snapshot(ObservedElement("e1", "link", "Browse rentals", "a", href="/rent")),
        "Open the listing details",
        {},
        6,
        True,
    )

    assert [action.action_id for action in result] == ["done"]


def test_form_values_bind_only_to_matching_fields_and_hold_submit():
    result = build_actions(
        snapshot(
            ObservedElement("e1", "textbox", "Name*", "input"),
            ObservedElement("e2", "textbox", "Phone*", "input"),
            ObservedElement("e3", "textbox", "WhatsApp Number", "input"),
            ObservedElement("e4", "textbox", "Line ID", "input"),
            ObservedElement("e5", "button", "Send", "button"),
        ),
        "Fill this form and submit it",
        {
            "name": "Aryan",
            "phone": "0637859636",
            "whatsapp number": "0637859636",
            "line id": "aryan",
        },
        12,
        False,
    )

    by_id = {action.action_id: action for action in result}
    assert "fill_e1_name" in by_id
    assert "fill_e2_phone" in by_id
    assert "fill_e3_whatsapp number" in by_id
    assert "fill_e4_line id" in by_id
    assert "click_e5" not in by_id
    assert all(action.goal_match for action in result if action.kind.value == "fill")


def test_requested_native_option_is_selected_and_submit_waits():
    result = build_actions(
        snapshot(
            ObservedElement(
                "e1",
                "combobox",
                "Rent or Sell",
                "select",
                options=(ObservedOption("Choose", ""), ObservedOption("Rent", "rent"), ObservedOption("Sell", "sell")),
            ),
            ObservedElement("e2", "button", "Send", "button"),
        ),
        "Choose looking to rent and submit",
        {"rent or sell": "Rent"},
        8,
        False,
    )

    assert [action.action_id for action in result] == ["select_e1_1"]
    assert result[0].option == "Rent"


def test_submit_becomes_progress_after_requested_fields_are_satisfied():
    result = build_actions(
        snapshot(
            ObservedElement("e1", "textbox", "Name*", "input", value="Aryan"),
            ObservedElement("e2", "button", "Send", "button"),
        ),
        "Fill this form and submit it",
        {"name": "Aryan"},
        8,
        False,
    )

    assert [action.action_id for action in result] == ["click_e2"]
    assert result[0].goal_match
    assert "Matched terms: submit" in result[0].description


def test_phone_widget_country_normalization_does_not_repeat_a_satisfied_fill():
    result = build_actions(
        snapshot(
            ObservedElement("e1", "textbox", "Phone*", "input", value="637859636"),
            ObservedElement("e2", "button", "Send", "button"),
        ),
        "Fill this form and submit it",
        {"phone": "0637859636"},
        8,
        False,
    )

    assert [action.action_id for action in result] == ["click_e2"]


def test_pending_form_values_remove_unrelated_matching_contact_links():
    result = build_actions(
        snapshot(
            ObservedElement("e1", "link", "Line Chat", "a", href="https://line.me/chat"),
            ObservedElement("e2", "textbox", "Line ID", "input"),
        ),
        "Fill the form with my line ID and submit",
        {"line id": "aryan"},
        8,
        False,
    )

    assert [action.action_id for action in result] == ["fill_e2_line id"]


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
