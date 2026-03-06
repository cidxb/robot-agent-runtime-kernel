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

# Phase 4: API & Safety Completion (High Priority)

## 4.1 `blocked_by` and `retry` in HTTP API

**Problem**

The kernel already supports task dependencies (`blocked_by`) and retry (`max_retries`, `retry_delay`), but these fields are not exposed in the HTTP `POST /tasks` request body. Users must use the Python API to access these features.

**Solution**

Extend `SubmitRequest` in `server.py`:

```python
class SubmitRequest(BaseModel):
    name: str
    priority: int = 5
    metadata: dict = {}
    blocked_by: list[str] = []          # task IDs this task depends on
    max_retries: int = 0                # injected into metadata
    retry_delay: float = 1.0            # injected into metadata
```

Map `blocked_by` to `Task(blocked_by=set(...))` and inject retry fields into `metadata`.

**Acceptance criteria**

- `POST /tasks` accepts `blocked_by`, `max_retries`, `retry_delay`
- Existing requests without these fields continue to work (backward compatible)
- Tests added

**Effort**: Small

---

## 4.2 WebSocket Event Stream

**Problem**

Monitoring task state changes requires polling `GET /tasks`. For real-time robot debugging this is insufficient — operators need immediate feedback when a task transitions.

**Solution**

Add a WebSocket endpoint:

```python
@app.websocket("/ws/events")
async def event_stream(ws: WebSocket):
    await ws.accept()
    while True:
        event = await event_queue.get()
        await ws.send_json(event.to_dict())
```

Kernel emits events to a broadcast channel; WebSocket clients subscribe.

**Acceptance criteria**

- `ws://localhost:8000/ws/events` pushes JSON on every state transition
- Multiple clients can connect simultaneously
- Disconnected clients don't block the kernel
- Tests added

**Effort**: Medium

---

## 4.3 Time-Bounded Tasks (Deadline)

**Problem**

A stuck task (sensor hang, network timeout) blocks the entire scheduler indefinitely. In physical robots this is dangerous — a task that should take 5 seconds but runs for 60 means something is wrong.

**Solution**

Add optional `deadline` field to Task:

```python
@dataclass
class Task:
    ...
    deadline: Optional[float] = None  # seconds from ACTIVE start
```

Scheduler checks elapsed time on each tick; if exceeded, emit `TASK_FAIL` with `error="deadline_exceeded"`.

**Acceptance criteria**

- `Task(name="read_sensor", deadline=5.0)` auto-fails after 5 seconds in ACTIVE
- Tasks without deadline behave as before
- Deadline survives PAUSED state (clock pauses while PAUSED, resumes on ACTIVE)
- Tests added

**Effort**: Medium

---

# Phase 5: Execution Isolation (Medium Priority)

> Inspired by NanoClaw's container isolation architecture — each agent runs in its own Linux container with filesystem isolation, preventing a single agent crash from affecting others.

## 5.1 Subprocess Skill Isolation

**Problem**

Currently all skills execute in the kernel's asyncio event loop. A misbehaving skill (segfault in a C extension, infinite loop, memory leak) can crash the entire kernel process.

**Solution**

Add an optional `IsolatedRunner` that executes skills in child processes:

```python
runner = SkillRunner(db_path="robot.db", isolation="subprocess")

# Skills still registered the same way
@runner.skill("navigate_to")
async def navigate_to(task: Task) -> None:
    ...
```

Implementation approach:
- Skill function serialized and sent to child process via `multiprocessing`
- Child process runs its own asyncio loop
- Parent monitors child: if it dies, task transitions to FAILED or RETRY
- IPC via pipe (not network) for reliability in embedded environments
- `task.metadata` synced back to parent on checkpoint writes

**Key design constraint**: This is opt-in. Default remains in-process execution. The kernel API does not change.

**Acceptance criteria**

- `isolation="subprocess"` runs skills in child processes
- Child crash → task FAILED (not kernel crash)
- Metadata checkpoints work across process boundary
- In-process mode (`isolation=None`) unchanged
- Tests added

**Effort**: Large

---

## 5.2 Resource Domains

**Problem**

A real robot has multiple independent subsystems: a mobile base, a manipulator arm, and sensors. The current single-active-task model means the arm must wait for navigation to finish — even though they use completely independent hardware.

> Inspired by NanoClaw's per-group queue model — each group has its own message queue and concurrency control, with a global concurrency limit across groups.

**Solution**

Introduce `ResourceDomain` — each domain has its own single-active-task constraint, but domains run in parallel:

```python
runner = SkillRunner(db_path="robot.db")

@runner.skill("navigate_to", domain="base")
async def navigate_to(task: Task) -> None: ...

@runner.skill("grasp_cup", domain="arm")
async def grasp_cup(task: Task) -> None: ...

# These can run concurrently — different domains
await runner.submit(Task(name="navigate_to", priority=5))
await runner.submit(Task(name="grasp_cup", priority=5))
```

Implementation approach:
- Each domain has its own scheduler (priority heap)
- Kernel manages multiple active tasks (one per domain)
- Interrupt only preempts within the same domain
- Cross-domain dependencies via existing `blocked_by`
- Optional global concurrency limit (e.g., max 3 domains active simultaneously)

**Key design constraint**: This does NOT violate the "single-active-task" principle — it refines it to "single-active-task **per hardware resource**". A domain without explicit assignment defaults to `"default"`, preserving backward compatibility.

**Acceptance criteria**

- Skills in different domains can run concurrently
- Skills in the same domain follow single-active-task scheduling
- Interrupt only affects the interrupted domain
- `blocked_by` works across domains
- Default domain preserves existing single-active behavior
- Tests added

**Effort**: Large

---

# Phase 6: Ecosystem Integration (Low Priority)

## 6.1 Task Groups (Atomic Batch)

**Problem**

Submitting a multi-step plan (navigate → grasp → pour) as individual tasks means partial failure leaves the system in an inconsistent state. If `grasp` fails, `pour` is still pending.

**Solution**

Add `TaskGroup` concept:

```python
group = TaskGroup(
    tasks=[nav, grasp, pour],
    policy="cancel_on_failure",  # or "continue_on_failure"
)
await runner.submit_group(group)
```

On any task failure within the group, all remaining tasks are cancelled.

**Acceptance criteria**

- `submit_group()` atomically submits a set of tasks
- `cancel_on_failure` policy cancels all pending/paused tasks in group when one fails
- Individual task lifecycle unchanged
- Tests added

**Effort**: Medium

---

## 6.2 ROS 2 Skill Adapter Template

**Problem**

ROS 2 is the dominant robot middleware. Wrapping a ROS 2 action client as a RARK skill requires boilerplate that every user will repeat.

**Solution**

Provide a reusable adapter in `rark/adapters/ros2.py`:

```python
from rark.adapters.ros2 import ros2_action_skill

@runner.skill("navigate")
@ros2_action_skill(
    action_type="nav2_msgs/action/NavigateToPose",
    server_name="/navigate_to_pose",
)
async def navigate(task: Task, goal_msg) -> None:
    goal_msg.pose = task.metadata["target_pose"]
```

The adapter handles: goal submission, feedback → metadata sync, preemption → action cancel, result → task completion.

**Acceptance criteria**

- Adapter wraps ROS 2 action client lifecycle
- Preemption triggers `cancel_goal()`
- Feedback synced to `task.metadata` for checkpoint
- Example in `examples/`
- Tests with mock action server

**Effort**: Medium (requires `rclpy` optional dependency)

---

## 6.3 OpenTelemetry Span Injection

**Problem**

Structured logging (Phase 3.1) provides text-based observability. For production deployments, distributed tracing with span context enables timeline visualization, latency analysis, and cross-service correlation.

**Solution**

Add optional OpenTelemetry hooks at state transition points:

```python
# opt-in via dependency
pip install rark[telemetry]

runner = SkillRunner(db_path="robot.db", telemetry=True)
```

Each task lifecycle gets a trace; each state transition creates a span. No performance impact when telemetry is disabled.

**Acceptance criteria**

- State transitions emit OpenTelemetry spans
- Disabled by default (zero overhead when off)
- `rark[telemetry]` optional dependency group
- Tests verify span creation with mock exporter

**Effort**: Medium

---

# Priority Summary

| No.  | Improvement              | Priority   | Effort | Status |
|------|--------------------------|------------|--------|--------|
| 1.1  | Skill resume semantics   | High       | Small  | Complete |
| 2.1  | Task dependencies        | Medium     | Medium | Complete |
| 2.2  | Skill retry              | Medium     | Small  | Complete |
| 3.1  | Structured logging       | Low        | Small  | Complete |
| 4.1  | HTTP API completeness    | High       | Small  | Planned |
| 4.2  | WebSocket event stream   | High       | Medium | Planned |
| 4.3  | Time-bounded tasks       | High       | Medium | Planned |
| 5.1  | Subprocess isolation     | Medium     | Large  | Planned |
| 5.2  | Resource Domains         | Medium     | Large  | Planned |
| 6.1  | Task groups              | Low        | Medium | Planned |
| 6.2  | ROS 2 adapter            | Low        | Medium | Planned |
| 6.3  | OpenTelemetry spans      | Low        | Medium | Planned |

---

# Intentional Non-Goals

The following directions were **actively decided against** after ecosystem research, to avoid over-engineering:

| Non-goal                      | Reason                                                              |
|-------------------------------|---------------------------------------------------------------------|
| LLM reasoning integration     | RARK is an LLM-agnostic scheduling kernel; layers must not mix      |
| Distributed multi-node runtime | Embedded single-node robot scenario; complexity not justified      |
| Unrestricted concurrent execution | Arbitrary multi-task parallelism without resource boundaries adds scheduling complexity for no clear benefit. Resource Domains (5.2) provide structured concurrency within explicit hardware boundaries instead |
| MCP tool protocol             | Belongs above SkillRunner; not a kernel responsibility              |
| Dynamic priority adjustment   | Priority semantics should be fixed at submission time; runtime modification introduces ambiguity |
