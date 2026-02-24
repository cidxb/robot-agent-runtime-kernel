import asyncio
import logging
from typing import Callable, Dict, Optional

from .events import Event, EventType
from .scheduler import Scheduler
from .task import Task
from .transitions import LifecycleState
from ..persistence.sqlite_store import SQLiteStore

logger = logging.getLogger("rark")


class RARKKernel:
    def __init__(self, db_path: str = "rark.db", crash_policy: str = "resume"):
        """
        Parameters
        ----------
        db_path : str
            SQLite 数据库路径，":memory:" 用于测试。
        crash_policy : str
            崩溃恢复策略，控制重启时 ACTIVE 任务的处理方式：
            - "resume"（默认）：ACTIVE → PAUSED，依赖 skill 的 checkpoint 继续执行。
              适合幂等 skill 或已实现断点续传的 skill。
            - "fail"：ACTIVE → FAILED，不自动重试。适合物理状态一致性要求严格、
              skill 无法安全重跑的场景（需手动重提交任务）。
        """
        self._crash_policy = crash_policy
        self._scheduler = Scheduler()
        self._store = SQLiteStore(db_path)
        self._queue: asyncio.Queue[Event] = asyncio.Queue()
        self._active_task: Optional[Task] = None
        self._running = False
        self._handlers: Dict[EventType, Callable] = {
            EventType.TASK_SUBMIT: self._on_submit,
            EventType.TASK_COMPLETE: self._on_complete,
            EventType.TASK_FAIL: self._on_fail,
            EventType.TASK_CANCEL: self._on_cancel,
            EventType.TASK_RETRY: self._on_retry,
            EventType.INTERRUPT: self._on_interrupt,
        }

    async def start(self) -> None:
        await self._store.open()
        await self._recover()
        self._running = True

    async def stop(self) -> None:
        self._running = False
        await self._store.close()

    async def emit(self, event: Event) -> None:
        await self._queue.put(event)

    def get_task(self, task_id: str) -> Optional[Task]:
        return self._scheduler.get(task_id)

    def list_tasks(self) -> list:
        return list(self._scheduler._tasks.values())

    async def run_loop(self) -> None:
        """Main event loop: drain the queue, fall back to _tick on idle."""
        while self._running:
            try:
                event = await asyncio.wait_for(self._queue.get(), timeout=0.1)
                await self._dispatch(event)
                self._queue.task_done()
            except asyncio.TimeoutError:
                await self._tick()
            except Exception as e:
                logger.error("unhandled error in run_loop: %s", e, exc_info=True)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _dispatch(self, event: Event) -> None:
        handler = self._handlers.get(event.type)
        if handler:
            await handler(event)

    async def _tick(self) -> None:
        """Promote the next queued task when no task is currently active."""
        if self._active_task is not None:
            return
        task = self._scheduler.pick_next()
        if task is None:
            return
        task.transition(LifecycleState.ACTIVE)
        self._active_task = task
        await self._store.upsert(task)
        logger.info("started  → %s (priority=%d)", task.name, task.priority)

    async def _recover(self) -> None:
        """Restore PENDING/PAUSED tasks after a crash."""
        tasks = await self._store.load_all()
        for task in tasks:
            if task.state in (LifecycleState.PENDING, LifecycleState.PAUSED):
                self._scheduler.add(task)
            elif task.state == LifecycleState.ACTIVE:
                # Kernel crashed while this task was running.
                # Recovery strategy depends on crash_policy:
                #   "resume" → PAUSED: task is re-queued; skill re-runs from
                #              task.metadata checkpoint (at-least-once semantics).
                #   "fail"   → FAILED: task is not re-queued; physical state
                #              must be verified and task manually resubmitted.
                if self._crash_policy == "resume":
                    task.transition(LifecycleState.PAUSED)
                    await self._store.upsert(task)
                    self._scheduler.add(task)
                    logger.warning(
                        "recovered → %s (ACTIVE→PAUSED, will resume)", task.name
                    )
                else:
                    task.transition(LifecycleState.FAILED)
                    await self._store.upsert(task)
                    self._scheduler.register(task)  # queryable but not scheduled
                    logger.warning(
                        "recovered → %s (ACTIVE→FAILED, manual resubmit required)",
                        task.name,
                    )
        if tasks:
            logger.info("recovered %d task(s) from persistence", len(tasks))

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    async def _on_submit(self, event: Event) -> None:
        task: Task = event.payload["task"]
        self._scheduler.add(task)
        await self._store.upsert(task)
        logger.info("submitted → %s (priority=%d)", task.name, task.priority)

    async def _on_complete(self, event: Event) -> None:
        task = self._scheduler.get(event.task_id)
        if task is None:
            return
        task.transition(LifecycleState.COMPLETED)
        await self._store.upsert(task)
        logger.info("completed → %s", task.name)
        if self._active_task and self._active_task.id == event.task_id:
            self._active_task = None
        self._scheduler.release_dependents(event.task_id)

    async def _on_fail(self, event: Event) -> None:
        task = self._scheduler.get(event.task_id)
        if task is None:
            return
        task.transition(LifecycleState.FAILED)
        await self._store.upsert(task)
        error = event.payload.get("error", "unknown")
        logger.warning("failed    → %s: %s", task.name, error)
        if self._active_task and self._active_task.id == event.task_id:
            self._active_task = None

    async def _on_cancel(self, event: Event) -> None:
        task = self._scheduler.get(event.task_id)
        if task is None:
            return
        task.transition(LifecycleState.CANCELLED)
        await self._store.upsert(task)
        logger.info("cancelled → %s", task.name)
        if self._active_task and self._active_task.id == event.task_id:
            self._active_task = None

    async def _on_retry(self, event: Event) -> None:
        """Re-queue a failed task for another attempt (ACTIVE → PENDING).

        Retry budget is tracked via task.metadata["retry_count"] / ["max_retries"].
        An optional metadata["retry_delay"] (seconds, default 0) defers re-queuing.
        """
        task = self._scheduler.get(event.task_id)
        if task is None:
            return
        task.transition(LifecycleState.PENDING)
        await self._store.upsert(task)
        if self._active_task and self._active_task.id == event.task_id:
            self._active_task = None

        retry_count = task.metadata.get("retry_count", 0)
        max_retries = task.metadata.get("max_retries", 0)
        logger.info("retry     → %s (%d/%d)", task.name, retry_count, max_retries)

        delay = task.metadata.get("retry_delay", 0.0)
        if delay > 0:
            async def _delayed_requeue(t: Task = task) -> None:
                await asyncio.sleep(delay)
                self._scheduler.add(t)
            asyncio.create_task(_delayed_requeue())
        else:
            self._scheduler.add(task)

    async def _on_interrupt(self, event: Event) -> None:
        """Pause the active task and inject a high-priority interrupt task."""
        if self._active_task is not None:
            self._scheduler.suspend(self._active_task.id)
            await self._store.upsert(self._active_task)
            logger.info("paused    → %s", self._active_task.name)
            self._active_task = None

        interrupt_task: Task = event.payload["task"]
        self._scheduler.add(interrupt_task)
        await self._store.upsert(interrupt_task)
        logger.info(
            "interrupt → %s (priority=%d)", interrupt_task.name, interrupt_task.priority
        )
