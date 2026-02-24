import asyncio
from contextlib import asynccontextmanager
from typing import Any, Dict, List

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .core.events import Event, EventType
from .core.runner import SkillRunner
from .core.task import Task


# ── Request / Response models ──────────────────────────────────────────────


class SubmitRequest(BaseModel):
    name: str
    priority: int = 5
    metadata: Dict[str, Any] = {}


class InterruptRequest(BaseModel):
    name: str
    priority: int = 10
    metadata: Dict[str, Any] = {}


class TaskOut(BaseModel):
    id: str
    name: str
    state: str
    priority: int
    metadata: Dict[str, Any]


# ── App factory ────────────────────────────────────────────────────────────


def create_app(runner: SkillRunner) -> FastAPI:
    """
    Create a FastAPI application wrapping a SkillRunner.

    Usage::

        runner = SkillRunner(db_path="robot.db")

        @runner.skill("my_task")
        async def my_task(task: Task) -> None:
            ...

        app = create_app(runner)
        uvicorn.run(app, host="0.0.0.0", port=8000)
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await runner.start()
        loop_task = asyncio.create_task(runner.run_loop())
        yield
        await runner.stop()
        loop_task.cancel()
        try:
            await loop_task
        except (asyncio.CancelledError, Exception):
            pass

    app = FastAPI(
        title="RARK",
        description="Robot Agent Runtime Kernel — Task Lifecycle API",
        version="0.1.0",
        lifespan=lifespan,
    )

    def _out(task: Task) -> TaskOut:
        return TaskOut(
            id=task.id,
            name=task.name,
            state=task.state.value,
            priority=task.priority,
            metadata=task.metadata,
        )

    # ── Routes ────────────────────────────────────────────────────────────

    @app.get("/health", summary="Kernel health + active task")
    async def health():
        active = runner._active_task
        return {
            "status": "ok",
            "active_task": _out(active).model_dump() if active else None,
        }

    @app.get("/tasks", response_model=List[TaskOut], summary="List all tasks")
    async def list_tasks():
        return [_out(t) for t in runner.list_tasks()]

    @app.post(
        "/tasks", response_model=TaskOut, status_code=201, summary="Submit a task"
    )
    async def submit_task(req: SubmitRequest):
        task = Task(name=req.name, priority=req.priority, metadata=req.metadata)
        await runner.submit(task)
        return _out(task)

    @app.get("/tasks/{task_id}", response_model=TaskOut, summary="Get task by ID")
    async def get_task(task_id: str):
        task = runner.get_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found")
        return _out(task)

    @app.delete("/tasks/{task_id}", summary="Cancel a task")
    async def cancel_task(task_id: str):
        task = runner.get_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found")
        await runner.emit(Event(type=EventType.TASK_CANCEL, task_id=task_id))
        return {"cancelled": task_id}

    @app.post(
        "/interrupt",
        response_model=TaskOut,
        status_code=201,
        summary="Interrupt with high-priority task",
    )
    async def interrupt(req: InterruptRequest):
        task = Task(name=req.name, priority=req.priority, metadata=req.metadata)
        await runner.interrupt(task)
        return _out(task)

    return app
