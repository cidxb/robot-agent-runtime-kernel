import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine, Dict, Optional, Set

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

    # Injected by SkillRunner before skill execution; not persisted.
    _checkpoint_fn: Optional[Callable[["Task"], Coroutine]] = field(
        default=None, repr=False, compare=False
    )

    def transition(self, target: LifecycleState) -> None:
        self.state = apply_transition(self.state, target)
        self.updated_at = datetime.now(timezone.utc)

    async def checkpoint(self) -> None:
        """Persist current metadata to storage mid-execution.

        Skills should call this after updating metadata["stage"] to ensure
        crash recovery can resume from the latest checkpoint.
        """
        if self._checkpoint_fn is not None:
            await self._checkpoint_fn(self)
