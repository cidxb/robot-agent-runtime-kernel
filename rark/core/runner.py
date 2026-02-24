import asyncio
from typing import Callable, Coroutine, Dict, Optional

from .events import Event, EventType
from .kernel import RARKKernel
from .task import Task


class SkillRunner(RARKKernel):
    def __init__(self, db_path: str = "rark.db", crash_policy: str = "resume"):
        super().__init__(db_path, crash_policy)
        self._skills: Dict[str, Callable[[Task], Coroutine]] = {}
        self._running_skill_task: Optional[asyncio.Task] = None

    def skill(self, name: str):
        """Decorator to register a skill function."""

        def decorator(fn: Callable[[Task], Coroutine]):
            self.register(name, fn)
            return fn

        return decorator

    def register(self, name: str, fn: Callable[[Task], Coroutine]) -> None:
        self._skills[name] = fn

    async def submit(self, task: Task) -> None:
        self._scheduler.register(
            task
        )  # immediately queryable before run_loop processes event
        await self.emit(Event(type=EventType.TASK_SUBMIT, payload={"task": task}))

    async def interrupt(self, task: Task) -> None:
        self._scheduler.register(task)
        await self.emit(Event(type=EventType.INTERRUPT, payload={"task": task}))

    # ------------------------------------------------------------------
    # _tick override: detect new active task and launch its skill
    # ------------------------------------------------------------------

    async def _tick(self) -> None:
        prev_active = self._active_task
        await super()._tick()
        if self._active_task is not None and self._active_task is not prev_active:
            await self._launch_skill(self._active_task)

    # ------------------------------------------------------------------
    # Skill lifecycle
    # ------------------------------------------------------------------

    async def _launch_skill(self, task: Task) -> None:
        fn = self._skills.get(task.name)
        if fn is None:
            await self.emit(
                Event(
                    type=EventType.TASK_FAIL,
                    task_id=task.id,
                    payload={"error": f"No skill registered for '{task.name}'"},
                )
            )
            return
        skill_task = asyncio.create_task(self._run_skill(task, fn))
        self._running_skill_task = skill_task
        skill_task.add_done_callback(self._on_skill_done)

    async def _run_skill(self, task: Task, fn: Callable[[Task], Coroutine]) -> None:
        try:
            await fn(task)
            await self.emit(Event(type=EventType.TASK_COMPLETE, task_id=task.id))
        except asyncio.CancelledError:
            raise
        except Exception as e:
            retry_count = task.metadata.get("retry_count", 0)
            max_retries = task.metadata.get("max_retries", 0)
            if retry_count < max_retries:
                task.metadata["retry_count"] = retry_count + 1
                await self.emit(Event(type=EventType.TASK_RETRY, task_id=task.id))
            else:
                await self.emit(
                    Event(
                        type=EventType.TASK_FAIL,
                        task_id=task.id,
                        payload={"error": str(e)},
                    )
                )

    def _on_skill_done(self, fut: asyncio.Future) -> None:
        self._running_skill_task = None

    async def _cancel_running_skill(self) -> None:
        if self._running_skill_task and not self._running_skill_task.done():
            self._running_skill_task.cancel()
            try:
                await self._running_skill_task
            except (asyncio.CancelledError, Exception):
                pass
            self._running_skill_task = None

    # ------------------------------------------------------------------
    # Event handler overrides
    # ------------------------------------------------------------------

    async def _on_interrupt(self, event: Event) -> None:
        await self._cancel_running_skill()
        await super()._on_interrupt(event)

    async def _on_cancel(self, event: Event) -> None:
        if self._active_task and self._active_task.id == event.task_id:
            await self._cancel_running_skill()
        await super()._on_cancel(event)
