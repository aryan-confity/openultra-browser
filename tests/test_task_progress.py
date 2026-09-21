from dataclasses import replace

from openultra_browser.models import (
    ActionKind,
    BrowserSnapshot,
    CandidateAction,
    ModelDecision,
    ObservedElement,
    StepRecord,
)
from openultra_browser.task_progress import (
    TaskProgress,
    error_blocks_progress,
    login_blocks_progress,
    split_task_steps,
    step_completion_disposition,
    summarize_page_change,
)


def snapshot(url: str, text: str = "") -> BrowserSnapshot:
    return BrowserSnapshot(url, "Page", text, ())


def test_explicit_action_clauses_become_ordered_steps():
    steps = split_task_steps(
        "Go to wdxproperties.com and click Condos for rent on the home page "
        "and get the first option details page and the final page"
    )

    assert steps == (
        "Go to wdxproperties.com",
        "click Condos for rent on the home page",
        "get the first option details page and the final page",
    )


def test_form_fill_and_submit_remain_one_observable_outcome():
    steps = split_task_steps(
        "Go to wdxproperties.com and go to Sell, fill this form with my details "
        "and submit Aryan, 0637859636, and choose looking to rent"
    )

    assert steps == (
        "Go to wdxproperties.com",
        "go to Sell",
        "fill this form with my details and submit Aryan, 0637859636 and choose looking to rent",
    )


def test_stateful_action_is_its_own_atomic_step():
    assert split_task_steps(
        "Go to youtube.com and search for Neon AI stream and dislike his video"
    ) == (
        "Go to youtube.com",
        "search for Neon AI stream",
        "dislike his video",
    )


def test_date_open_and_date_change_are_separate_atomic_outcomes():
    assert split_task_steps(
        "Click on Thu, Jan 28 and change the date to September 28th"
    ) == (
        "Click on Thu, Jan 28",
        "change the date to September 28th",
    )


def test_visible_start_destination_advances_without_model_inference():
    progress = TaskProgress.from_goal(
        "Go to wdxproperties.com and click Condos for rent"
    )

    progress.sync_visible_state(snapshot("https://www.wdxproperties.com/"))

    assert progress.completed_steps == ("Go to wdxproperties.com",)
    assert progress.current_step == "click Condos for rent"


def test_confirmed_current_step_advances_exactly_once():
    progress = TaskProgress.from_goal(
        "Open Condos for rent and click Inquire in the first listing"
    )
    result = snapshot("https://wdxproperties.com/properties/rent", "Rental listings")

    progress.advance_current_step(result, action_count=3)

    assert progress.completed_steps == ("Open Condos for rent",)
    assert progress.current_step == "click Inquire in the first listing"
    assert progress.history_boundary == 3


def test_page_change_summary_records_url_and_control_state():
    before = BrowserSnapshot(
        "https://example.com/watch",
        "Video",
        "Video",
        (ObservedElement("e1", "button", "Dislike", "button", pressed=False),),
    )
    after = BrowserSnapshot(
        "https://example.com/watch?selected=dislike",
        "Video",
        "Video",
        (ObservedElement("e1", "button", "Dislike", "button", pressed=True),),
    )

    summary = summarize_page_change(before, after)

    assert "URL changed" in summary
    assert "pressed=False->True" in summary


def decision(probability: float) -> ModelDecision:
    return ModelDecision(
        proposed_action="click_e1",
        probabilities={"click_e1": 1.0},
        confidence=1.0,
        goal_probability=0.0,
        stuck_probability=0.0,
        inference_ms=1.0,
        input_tokens=10,
        step_completion_probability=probability,
    )


def record(kind: str, *, changed: bool = True) -> StepRecord:
    return StepRecord(
        step=1,
        url="https://example.com",
        proposed_action=kind,
        executed_action=kind,
        description=kind,
        confidence=1.0,
        goal_probability=0.0,
        stuck_probability=0.0,
        inference_ms=1.0,
        action_kind=kind,
        changed=changed,
    )


def test_previous_step_evidence_cannot_complete_the_next_step():
    progress = TaskProgress(("Open rentals", "Click Inquire"), index=1, history_boundary=1)

    disposition = step_completion_disposition(progress, decision(0.89), (record("click"),))

    assert disposition == "pending"


def test_exploration_change_cannot_unlock_step_confirmation():
    progress = TaskProgress(("Click Inquire",), history_boundary=0)

    disposition = step_completion_disposition(
        progress, decision(0.8), (record("scroll_down"),)
    )

    assert disposition == "pending"


def test_filling_search_does_not_complete_it_before_submission():
    progress = TaskProgress(("Search for Neon AI stream",), history_boundary=0)

    disposition = step_completion_disposition(
        progress, decision(0.99), (record("fill"),)
    )

    assert disposition == "pending"


def test_click_navigation_is_authoritative_for_an_explicit_click_step():
    progress = TaskProgress(("click on it",), history_boundary=0)
    click = record("click")
    click.change_summary = "URL changed: https://example.com/results -> https://example.com/watch"

    assert step_completion_disposition(progress, decision(0.1), (click,)) == "verified"


def test_navigation_does_not_claim_a_stateful_dislike_step_completed():
    progress = TaskProgress(("dislike his video",), history_boundary=0)
    click = record("click")
    click.change_summary = "URL changed: https://example.com/watch -> https://accounts.example.com"

    assert step_completion_disposition(progress, decision(0.1), (click,)) == "pending"


def test_semantic_change_with_mid_confidence_requests_focused_confirmation():
    progress = TaskProgress(("Click Inquire",), history_boundary=0)
    matching = record("click")
    matching.description = "Activate button Inquire"

    disposition = step_completion_disposition(progress, decision(0.7), (matching,))

    assert disposition == "confirm"


def test_unrelated_changed_control_cannot_complete_a_step_at_high_confidence():
    progress = TaskProgress(("Click Thu Jan 28",), history_boundary=0)
    wrong = record("click")
    wrong.description = "Activate button Sorted by top flights, Change sort order"
    wrong.change_summary = "Changed controls: Change sort order expanded=False->True"

    assert step_completion_disposition(progress, decision(0.99), (wrong,)) == "pending"


def test_calendar_navigation_cannot_complete_before_exact_date_selection():
    progress = TaskProgress(("change the date to September 28th",), history_boundary=0)
    navigation = record("click")
    navigation.description = (
        "Activate button Previous. Requested date 2026-09-28 is previous of the "
        "visible calendar range 2027-01-01 to 2027-02-28: YES."
    )

    assert step_completion_disposition(progress, decision(0.99), (navigation,)) == "pending"


def test_optional_login_does_not_block_a_direct_semantic_action():
    model = replace(decision(0.0), login_probability=0.8)
    submit = CandidateAction(
        "submit_search",
        ActionKind.PRESS_ENTER,
        "Submit search",
        "e1",
        goal_match=True,
    )

    assert not login_blocks_progress(model, (submit,))


def test_login_blocks_when_no_direct_semantic_action_remains():
    model = replace(decision(0.0), login_probability=0.8)
    scroll = CandidateAction(
        "scroll_down", ActionKind.SCROLL_DOWN, "Scroll down", goal_match=True
    )

    assert login_blocks_progress(model, (scroll,))


def test_login_page_does_not_block_a_confident_back_instruction():
    back = CandidateAction("back", ActionKind.BACK, "Return to the previous page")
    model = replace(
        decision(0.0),
        proposed_action="back",
        probabilities={"back": 0.9},
        confidence=0.9,
        login_probability=0.95,
    )

    assert not login_blocks_progress(model, (back,))


def test_login_page_does_not_block_a_confident_tab_escape():
    switch = CandidateAction(
        "switch_tab_previous",
        ActionKind.SWITCH_TAB,
        "Return to the previous browser tab",
        browser_target_id="previous",
        goal_match=True,
    )
    model = ModelDecision(
        proposed_action="switch_tab_previous",
        probabilities={"switch_tab_previous": 1.0},
        confidence=0.9,
        goal_probability=0.0,
        stuck_probability=0.0,
        inference_ms=1.0,
        input_tokens=10,
        login_probability=0.99,
    )

    assert not login_blocks_progress(model, (switch,))


def test_model_error_does_not_block_without_browser_alert_evidence():
    model = replace(decision(0.0), error_probability=0.9)
    page = snapshot("https://example.com")

    assert not error_blocks_progress(model, page, ())


def test_browser_alert_blocks_when_no_recovery_action_remains():
    model = replace(decision(0.0), error_probability=0.9)
    page = replace(snapshot("https://example.com"), alerts=("Request rejected",))
    scroll = CandidateAction(
        "scroll_down", ActionKind.SCROLL_DOWN, "Scroll down", goal_match=True
    )

    assert error_blocks_progress(model, page, (scroll,))


def test_browser_alert_does_not_block_a_direct_recovery_action():
    model = replace(decision(0.0), error_probability=0.9)
    page = replace(snapshot("https://example.com"), alerts=("Required field",))
    fill = CandidateAction(
        "fill_name", ActionKind.FILL, "Fill name", "e1", goal_match=True
    )

    assert not error_blocks_progress(model, page, (fill,))
