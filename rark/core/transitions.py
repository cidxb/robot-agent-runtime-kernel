from enum import Enum
from typing import Dict, Set


class LifecycleState(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


# Valid next states for each state
VALID_TRANSITIONS: Dict[LifecycleState, Set[LifecycleState]] = {
    LifecycleState.PENDING: {
        LifecycleState.ACTIVE,
        LifecycleState.CANCELLED,
    },
    LifecycleState.ACTIVE: {
        LifecycleState.PENDING,  # retry: re-queue for another attempt
        LifecycleState.PAUSED,
        LifecycleState.COMPLETED,
        LifecycleState.FAILED,
        LifecycleState.CANCELLED,
    },
    LifecycleState.PAUSED: {
        LifecycleState.ACTIVE,
        LifecycleState.CANCELLED,
    },
    LifecycleState.COMPLETED: set(),
    LifecycleState.FAILED: set(),
    LifecycleState.CANCELLED: set(),
}


def apply_transition(current: LifecycleState, target: LifecycleState) -> LifecycleState:
    if target not in VALID_TRANSITIONS[current]:
        raise ValueError(f"Invalid transition: {current} -> {target}")
    return target
