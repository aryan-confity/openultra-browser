"""Deterministic progress checks shared by automatic and interactive runs."""

from __future__ import annotations

from collections.abc import Sequence

from .models import StepRecord

MAX_EXPLORATION_SCROLLS = 6


def exhausted_scroll_exploration(history: Sequence[StepRecord]) -> bool:
    """Bound consecutive same-direction page exploration on infinite pages."""
    if len(history) < MAX_EXPLORATION_SCROLLS:
        return False
    tail = history[-MAX_EXPLORATION_SCROLLS:]
    direction = tail[0].action_kind
    return direction in {"scroll_up", "scroll_down"} and all(
        row.action_kind == direction and row.executed_action for row in tail
    )


def repeats_action_cycle(
    history: Sequence[StepRecord],
    *,
    repetitions: int = 3,
    max_cycle_length: int = 3,
) -> bool:
    actions = [
        (row.executed_action, row.change_summary or "") for row in history if row.executed_action
    ]
    for length in range(1, min(max_cycle_length, len(actions) // repetitions) + 1):
        tail = actions[-length * repetitions :]
        block = tail[:length]
        if all(tail[index : index + length] == block for index in range(0, len(tail), length)):
            return True
    return False
