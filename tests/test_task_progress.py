from openultra_browser.models import ActionKind, BrowserSnapshot, CandidateAction
from openultra_browser.task_progress import TaskProgress, split_task_steps


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


def test_form_fill_and_submit_remain_one_unfinished_step():
    steps = split_task_steps(
        "Go to wdxproperties.com and go to Sell, fill this form with my details "
        "and submit Aryan, 0637859636, and choose looking to rent"
    )

    assert steps == (
        "Go to wdxproperties.com",
        "go to Sell",
        "fill this form with my details and submit Aryan, 0637859636 and choose looking to rent",
    )


def test_visible_start_destination_advances_without_a_synthetic_action():
    progress = TaskProgress.from_goal(
        "Go to wdxproperties.com and click Condos for rent"
    )

    progress.sync_visible_state(snapshot("https://www.wdxproperties.com/"))

    assert progress.completed_steps == ("Go to wdxproperties.com",)
    assert progress.current_step == "click Condos for rent"


def test_only_changed_goal_matching_actions_advance_one_step():
    progress = TaskProgress.from_goal(
        "Click Condos for rent and open the first listing details"
    )
    matching = CandidateAction(
        "click_e1", ActionKind.CLICK, "Activate Condos for rent", "e1", goal_match=True
    )
    unrelated = CandidateAction(
        "click_e2", ActionKind.CLICK, "Open Login", "e2", goal_match=False
    )
    home = snapshot("https://wdxproperties.com/", "Home")
    rent = snapshot("https://wdxproperties.com/properties/rent", "Rent")

    progress.record_verified_action(unrelated, home, rent)
    assert progress.index == 0

    progress.record_verified_action(matching, home, rent)
    assert progress.index == 1
    assert progress.current_step == "open the first listing details"


def test_exploration_does_not_complete_a_semantic_task_step():
    progress = TaskProgress.from_goal("Open the first listing details")
    scroll = CandidateAction(
        "scroll_down",
        ActionKind.SCROLL_DOWN,
        "Scroll to reveal more content",
        goal_match=True,
    )
    before = snapshot("https://example.com/results", "Top")
    after = snapshot("https://example.com/results", "More results")

    progress.record_verified_action(scroll, before, after)

    assert progress.index == 0
    assert progress.current_step == "Open the first listing details"


def test_form_fill_does_not_complete_a_step_that_still_requires_submit():
    progress = TaskProgress.from_goal("Fill the form with Aryan and submit it")
    fill = CandidateAction(
        "fill_name",
        ActionKind.FILL,
        "Enter name",
        "e1",
        input_key="name",
        goal_match=True,
    )
    before = snapshot("https://example.com/form", "Empty")
    after = snapshot("https://example.com/form", "Name Aryan")

    progress.record_verified_action(fill, before, after)

    assert progress.index == 0


def test_form_submit_step_completes_only_from_a_submit_control():
    progress = TaskProgress.from_goal("Fill the form, choose Rent, and submit it")
    rent = CandidateAction(
        "click_rent", ActionKind.CLICK, "Activate button Rent", "e1", goal_match=True
    )
    send = CandidateAction(
        "click_send", ActionKind.CLICK, "Activate button Send", "e2", goal_match=True
    )
    before = snapshot("https://example.com/form", "Form")
    after = snapshot("https://example.com/form", "Changed")

    progress.record_verified_action(rent, before, after)
    assert progress.index == 0

    progress.record_verified_action(send, before, after, verified_change=True)
    assert progress.complete
