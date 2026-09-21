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
