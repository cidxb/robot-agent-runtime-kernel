> **English** | [中文](../zh/04-roadmap.md)

# RARK Roadmap

> Improvement plan compiled from the current implementation state and ecosystem research (`03-ecosystem.md`).
> Ordered by priority; each item notes the driving motivation and minimum viable implementation.

---

# Completed

| Feature                         | Description                                                               |
|---------------------------------|---------------------------------------------------------------------------|
| RARKKernel (lifecycle kernel)   | Priority scheduling, event-driven, SQLite persistence, crash recovery     |
| SkillRunner (skill execution)   | `@runner.skill()` decorator, asyncio.Task management, auto complete/fail  |
| HTTP API layer (`server.py`)    | `create_app()` factory, 6 REST endpoints, FastAPI lifespan integration    |
| Public exports (`__init__.py`)  | `SkillRunner`, `Task`, `Event`, `EventType`, `LifecycleState`             |
| Immediate queryability          | Tasks queryable via API after submit/interrupt without waiting for run_loop |
| Skill Resume semantics (1.1)    | metadata interrupt persistence + checkpoint pattern validated; 2 dedicated tests |
| Configurable crash_policy       | `"resume"` (default) / `"fail"` (safety mode) for varying physical consistency requirements |
| SQLite WAL mode                 | `PRAGMA journal_mode=WAL`; journal replayable on crash                    |
| Cross-instance DB recovery test | Validates metadata integrity when a different runner instance recovers from the same DB |
| Architecture documentation      | Time-scale boundary, at-least-once semantics, crash_policy, priority inversion analysis |
| Full test coverage              | 38 tests across kernel / skill / HTTP three layers                        |

---

# Phase 1: Fix Design Debt (High Priority)

## ✅ 1.1 Skill Resume Semantics (Complete)

**Problem**

`_cancel_running_skill()` was a hard cancel. When a task resumed from PAUSED, `_launch_skill()` restarted the skill coroutine from the beginning — **re-running from scratch**.

This meant:
- "Pouring water at 80%" when interrupted would restart from 0% on resume
- If the skill had already sent commands to an actuator, re-running would issue duplicate commands
- No way to pass "where we left off last time" context

**Solution**

Resolved through convention, without modifying the kernel.

`task.metadata` already exists; a skill reads its progress before executing and writes progress after:

```python
@runner.skill("pour_water")
async def pour_water(task: Task) -> None:
    progress = task.metadata.get("progress", 0.0)

    if progress < 0.5:
        await move_to_cup()
        task.metadata["progress"] = 0.5
        # (interrupted here? metadata already updated)

    if progress < 1.0:
        await pour()
        task.metadata["progress"] = 1.0
```

**Completion status**: Infrastructure verified bug-free (`_on_interrupt` correctly upserts; `upsert` updates the metadata column; `load_all` correctly deserializes). Two new tests: `test_metadata_persisted_on_pause` (DB persistence verification) and `test_resume_reads_metadata_checkpoint` (end-to-end `stages_seen == [0, 1]`). Checkpoint pattern documented in `docs/en/02-architecture.md` Section 3.5.

---

# Phase 2: Structural Capability Completion (Medium Priority)

## ✅ 2.1 BLOCKED State + Task Dependencies (Complete)

**Problem**

Real robot tasks often have dependencies:

```
navigate_to_kitchen
  → only after completion → grasp_cup
      → only after completion → pour_water
```

RARK did not support directed dependencies; tasks had to be submitted sequentially by the caller.

**Solution**

Extended `Task` and `Scheduler`:

```python
# Task addition
blocked_by: Set[str] = field(default_factory=set)  # set of dependency task IDs

# Scheduler.pick_next() change
# Skip tasks whose blocked_by set is non-empty and whose dependencies are not all complete

# _on_complete() change
# On completion, check if any other tasks were waiting on this task_id; unblock them
```

New state transition: tasks are skipped in scheduling when `blocked_by` is non-empty; on completion the kernel calls `release_dependents()` to remove the completed task ID from all other tasks' `blocked_by` sets.

---

## ✅ 2.2 Skill Retry Mechanism (Complete)

**Problem**

Any exception previously sent a task directly to FAILED (terminal) with no retry. Too aggressive for transient failures (network jitter, sensor noise).

**Solution**

Added retry budget to `SkillRunner._run_skill()`:

```python
# task.metadata convention
task.metadata.get("max_retries", 0)   # number of retries allowed
task.metadata.get("retry_count", 0)   # number of retries so far
task.metadata.get("retry_delay", 1.0) # retry interval (seconds)
```

On exception, if `retry_count < max_retries`, increment `retry_count` and emit `TASK_RETRY` (not `TASK_FAIL`), sending the task back to the queue.

---

# Phase 3: Observability (Low Priority, but Affects Production Readiness)

## ✅ 3.1 Structured Logging (Complete)

**Problem**

All output used `print()`, making it impossible to:
- Filter by log level
- Connect to a log aggregation system
- Suppress debug output in production

**Solution**

Replaced all `print(f"[RARK] ...")` calls in `kernel.py` and `runner.py` with `logging.getLogger("rark")`:

```python
import logging
logger = logging.getLogger("rark")

logger.info("submitted → %s (priority=%d)", task.name, task.priority)
logger.warning("failed    → %s: %s", task.name, error)
```

Callers control output via the standard `logging.basicConfig()`.

---

# Priority Summary

| No.  | Improvement              | Priority   | Effort |
|------|--------------------------|------------|--------|
| 1.1  | Skill resume semantics   | ✅ Complete | Small  |
| 2.1  | Task dependencies        | ✅ Complete | Medium |
| 2.2  | Skill retry              | ✅ Complete | Small  |
| 3.1  | Structured logging       | ✅ Complete | Small  |

---

# Intentional Non-Goals

The following directions were **actively decided against** after ecosystem research, to avoid over-engineering:

| Non-goal                      | Reason                                                              |
|-------------------------------|---------------------------------------------------------------------|
| LLM reasoning integration     | RARK is an LLM-agnostic scheduling kernel; layers must not mix      |
| Distributed multi-node runtime | Embedded single-node robot scenario; complexity not justified      |
| Concurrent multi-task execution | Single-active-task is an intentional constraint simplifying scheduling and hardware resource management |
| MCP tool protocol             | Belongs above SkillRunner; not a kernel responsibility              |
| Dynamic priority adjustment   | Priority semantics should be fixed at submission time; runtime modification introduces ambiguity |
