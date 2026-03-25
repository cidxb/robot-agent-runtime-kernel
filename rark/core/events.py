from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional


class EventType(str, Enum):
    TASK_SUBMIT = "task_submit"
    TASK_COMPLETE = "task_complete"
    TASK_FAIL = "task_fail"
    TASK_CANCEL = "task_cancel"
    TASK_RETRY = "task_retry"
    TASK_PAUSE = "task_pause"
    TASK_RESUME = "task_resume"
    INTERRUPT = "interrupt"


@dataclass
class Event:
    type: EventType
    task_id: Optional[str] = None
    payload: Dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
