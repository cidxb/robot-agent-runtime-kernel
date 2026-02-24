import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Set

from .transitions import LifecycleState, apply_transition


@dataclass
class Task:
    name: str
    priority: int  # higher value = more urgent
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    state: LifecycleState = field(default=LifecycleState.PENDING)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Dict[str, Any] = field(default_factory=dict)
    blocked_by: Set[str] = field(
        default_factory=set
    )  # task IDs that must complete first

    def transition(self, target: LifecycleState) -> None:
        self.state = apply_transition(self.state, target)
        self.updated_at = datetime.now(timezone.utc)
