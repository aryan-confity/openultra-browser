from laya_browser.models import StepRecord
from laya_browser.progress import repeats_action_cycle


def step(number, action):
    return StepRecord(
        step=number,
        url="https://example.com",
        proposed_action=action,
        executed_action=action,
        description=action,
        confidence=1.0,
        goal_probability=0.0,
        stuck_probability=0.0,
        inference_ms=1.0,
        changed=True,
    )


def test_repeated_two_action_oscillation_is_detected():
    history = [step(index + 1, action) for index, action in enumerate(["map", "back"] * 3)]

    assert repeats_action_cycle(history)


def test_nonrepeating_progress_is_not_stopped():
    history = [step(index + 1, action) for index, action in enumerate(["fill", "search", "site"])]

    assert not repeats_action_cycle(history)
