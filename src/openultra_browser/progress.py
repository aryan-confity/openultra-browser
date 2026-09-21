"""Deterministic progress checks shared by automatic and interactive runs."""

from __future__ import annotations

from collections.abc import Sequence

from .models import StepRecord


def repeats_action_cycle(
    history: Sequence[StepRecord],
    *,
    repetitions: int = 3,
    max_cycle_length: int = 3,
) -> bool:
    actions = [row.executed_action for row in history if row.executed_action]
    for length in range(1, min(max_cycle_length, len(actions) // repetitions) + 1):
        tail = actions[-length * repetitions :]
        block = tail[:length]
        if all(tail[index : index + length] == block for index in range(0, len(tail), length)):
            return True
    return False
