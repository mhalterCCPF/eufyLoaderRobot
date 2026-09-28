"""Analytical cycle/home duration model, independent of controller.py's runtime state.

Mirrors CycleRunner.run_cycle / CycleRunner.home_cycle's move sequence purely to
predict wall-clock duration from the same speed/distance/pause constants, so
headless.py can compare a measured run against an expectation without requiring
any changes to controller.py.
"""

from __future__ import annotations

from typing import Dict

from controller import (
    LS1,
    LS2,
    O1,
    O2,
    O3,
    OFFSET_HOME,
    PM_D1,
    PM_D2,
    PM_D3,
    PRINT_PLACEHOLDER_SEC,
    PS1,
    PS2,
    RM_D1,
    RM_D2,
    RM_D3,
    RS1,
    RS2,
    T1_SEC,
    lmp,
)

AxisState = Dict[str, float]

# Gate (servo) motion has no known speed constant; treated as instantaneous.
GATE_SECONDS = 0.0


def new_axis_state() -> AxisState:
    """Return axis positions matching the state right after a successful home."""
    return {"PM": 0.0, "LM": 0.0, "RM": 0.0}


def _move_seconds(axis_state: AxisState, axis: str, target_mm: float, speed_mm_per_second: float) -> float:
    seconds = abs(target_mm - axis_state[axis]) / speed_mm_per_second
    axis_state[axis] = target_mm
    return seconds


def expected_cycle_seconds(shelf_position: int, offsets: list, axis_state: AxisState) -> float:
    """Predict one run_cycle(shelf_position)'s duration, mutating axis_state to match."""
    shelf_lift_position = lmp(shelf_position, offsets)
    total = 0.0
    total += _move_seconds(axis_state, "LM", shelf_lift_position + O1, LS1)
    total += _move_seconds(axis_state, "PM", PM_D1, PS1)
    total += _move_seconds(axis_state, "LM", shelf_lift_position, LS1)
    total += _move_seconds(axis_state, "PM", PM_D2, PS1)
    total += _move_seconds(axis_state, "PM", PM_D3, PS2)
    total += T1_SEC
    total += _move_seconds(axis_state, "PM", PM_D2, PS2)
    total += _move_seconds(axis_state, "PM", OFFSET_HOME, PS1)
    total += PRINT_PLACEHOLDER_SEC
    total += _move_seconds(axis_state, "RM", RM_D1, RS1)
    total += GATE_SECONDS
    total += _move_seconds(axis_state, "LM", shelf_lift_position - O2, LS2)
    total += _move_seconds(axis_state, "RM", RM_D2, RS1)
    total += GATE_SECONDS
    total += _move_seconds(axis_state, "LM", shelf_lift_position - O3, LS1)
    total += _move_seconds(axis_state, "RM", RM_D3, RS1)
    return total


def expected_home_seconds(axis_state: AxisState) -> float:
    """Predict home_cycle()'s duration, then reset axis_state to all zero."""
    total = 0.0
    for axis, approach_speed, seek_speed in (("PM", PS1, PS2), ("LM", LS1, LS2), ("RM", RS1, RS2)):
        total += _move_seconds(axis_state, axis, OFFSET_HOME, approach_speed)
        total += abs(0.0 - axis_state[axis]) / seek_speed
        axis_state[axis] = 0.0
    return total
