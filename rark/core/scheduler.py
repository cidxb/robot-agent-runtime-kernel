import heapq
from typing import Dict, List, Optional, Tuple

from .task import Task
from .transitions import LifecycleState


class Scheduler:
    def __init__(self):
        # max-heap via negated priority; entries: (-priority, task_id)
        self._heap: List[Tuple[int, str]] = []
        self._tasks: Dict[str, Task] = {}

    def register(self, task: Task) -> None:
        """Track a task without adding it to the scheduling heap.

        Used by SkillRunner.submit() / interrupt() so the task is immediately
        visible via list_tasks() / get_task(), before the TASK_SUBMIT event is
        processed by run_loop().
        """
        self._tasks[task.id] = task

    def add(self, task: Task) -> None:
        self._tasks[task.id] = task
        heapq.heappush(self._heap, (-task.priority, task.id))

    def pick_next(self) -> Optional[Task]:
        """Pop and return the highest-priority PENDING or PAUSED task.

        Tasks with unresolved dependencies (blocked_by non-empty) are skipped
        and put back on the heap to be re-evaluated later.
        """
        skipped: List[Tuple[int, str]] = []
        result: Optional[Task] = None

        while self._heap:
            entry = heapq.heappop(self._heap)
            _, task_id = entry
            task = self._tasks.get(task_id)
            if task is None or task.state not in (
                LifecycleState.PENDING,
                LifecycleState.PAUSED,
            ):
                continue
            if task.blocked_by:
                skipped.append(entry)  # still has unresolved deps; defer
                continue
            result = task
            break

        for entry in skipped:
            heapq.heappush(self._heap, entry)

        return result

    def release_dependents(self, completed_id: str) -> None:
        """Remove completed_id from blocked_by of all waiting tasks."""
        for task in self._tasks.values():
            task.blocked_by.discard(completed_id)

    def suspend(self, task_id: str) -> None:
        """Transition task to PAUSED and re-queue it."""
        task = self._tasks[task_id]
        task.transition(LifecycleState.PAUSED)
        heapq.heappush(self._heap, (-task.priority, task.id))

    def get(self, task_id: str) -> Optional[Task]:
        return self._tasks.get(task_id)

    def remove(self, task_id: str) -> None:
        """Remove from tracking; stale heap entries are discarded by pick_next."""
        self._tasks.pop(task_id, None)
