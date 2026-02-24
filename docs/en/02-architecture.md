> **English** | [中文](../zh/02-architecture.md)

# RARK Architecture

> This document describes the current codebase implementation — the realized form of the vision in `01-philosophy.md`.

---

# 1. Project Layout

```
rark/
├── __init__.py          # Public API exports
├── server.py            # FastAPI HTTP layer (create_app factory)
├── core/
│   ├── task.py          # Task dataclass
│   ├── transitions.py   # State transition rules
│   ├── events.py        # Event type definitions
│   ├── scheduler.py     # Priority scheduler
│   ├── kernel.py        # RARKKernel (lifecycle kernel)
│   └── runner.py        # SkillRunner (skill execution layer)
├── persistence/
│   └── sqlite_store.py  # SQLite persistence
├── tests/
│   ├── test_task.py
│   ├── test_kernel.py
│   ├── test_runner.py
│   └── test_server.py   # HTTP layer end-to-end tests
└── examples/
    ├── interrupt_demo.py
    ├── server_demo.py    # Full runnable HTTP server demo
    └── llm_demo.py       # LLM planner → RARK integration demo
```

---

# 2. Task Lifecycle State Machine

## 2.1 State Definitions

| State       | Meaning                                          |
|-------------|--------------------------------------------------|
| `PENDING`   | Submitted, waiting in the scheduler queue        |
| `ACTIVE`    | Currently executing (only one at a time)         |
| `PAUSED`    | Suspended by an interrupt, waiting to resume     |
| `COMPLETED` | Finished successfully (terminal)                 |
| `FAILED`    | Execution failed (terminal)                      |
| `CANCELLED` | Explicitly cancelled (terminal)                  |

## 2.2 Legal Transition Matrix

```
PENDING   → ACTIVE
PENDING   → CANCELLED
ACTIVE    → PENDING     (retry: re-queue for another attempt)
ACTIVE    → PAUSED
ACTIVE    → COMPLETED
ACTIVE    → FAILED
ACTIVE    → CANCELLED
PAUSED    → ACTIVE      (resume)
PAUSED    → CANCELLED
```

Terminal states (COMPLETED / FAILED / CANCELLED) cannot be transitioned out of.

## 2.3 Relationship to Philosophy

`01-philosophy.md` describes a fuller lifecycle vision (with CREATED / READY / WAITING / RECOVERING). The current implementation is a focused subset covering the core scenarios. Future extensions are in `04-roadmap.md`.

---

# 3. Core Components

## 3.1 Task

```python
@dataclass
class Task:
    name: str
    priority: int          # higher = more urgent (interrupts typically 10, normal tasks 3–5)
    id: str                # UUID, auto-generated
    state: LifecycleState  # current lifecycle state
    created_at: datetime
    updated_at: datetime
    metadata: Dict[str, Any]  # arbitrary data, survives restarts
    blocked_by: Set[str]      # set of task IDs that must complete before this task runs
```

**Key design**: the `metadata` field passes skill execution progress so a skill can pick up from where it left off after resumption. `blocked_by` enables declarative task dependency graphs.

---

## 3.2 Scheduler

**Data structure**: max-heap (Python `heapq`, storing `-priority`)

```
register(task) → add to _tasks dict only, not to heap (enables immediate query after submit/interrupt)
add(task)      → push to heap + add to _tasks dict
pick_next()    → pop highest-priority PENDING/PAUSED task; skip blocked tasks
suspend(id)    → transition ACTIVE task to PAUSED
get(id)        → look up task by ID
release_dependents(id) → remove completed task's ID from all other tasks' blocked_by sets
```

**Lazy deletion**: the heap may contain stale entries for completed tasks; `pick_next()` skips them by checking state.

**Why `register()` exists**: `SkillRunner.submit()` and `interrupt()` call `register()` before emitting an event, making the task immediately queryable via `get_task()` / `list_tasks()` without waiting for `run_loop()` to process the event. This also makes `httpx.ASGITransport` tests work without a running lifespan.

---

## 3.3 Event System

```
EventType:
  TASK_SUBMIT    → payload: {"task": Task}
  TASK_COMPLETE  → task_id
  TASK_FAIL      → task_id, payload: {"error": str}
  TASK_CANCEL    → task_id
  TASK_RETRY     → task_id  (re-queue for retry attempt)
  INTERRUPT      → payload: {"task": Task}  (high-priority task injection)
```

**Event queue**: `asyncio.Queue`, non-blocking enqueue, consumed with `asyncio.wait_for` at 0.1 s timeout.

---

## 3.4 RARKKernel

Pure lifecycle kernel — contains no skill execution logic.

```
Main loop:
  run_loop()
    ├─ event available → _dispatch() → corresponding handler
    └─ idle (0.1 s timeout) → _tick() → promote next queued task

Public query methods:
  get_task(task_id)  → look up task by ID (returns Task or None)
  list_tasks()       → return list of all known tasks

Event handlers:
  _on_submit()    → scheduler.add() + persist
  _on_complete()  → COMPLETED + persist + release _active_task + release_dependents()
  _on_fail()      → FAILED + persist + release _active_task
  _on_cancel()    → CANCELLED + persist + release _active_task
  _on_retry()     → PENDING + persist + optional delayed re-queue
  _on_interrupt() → suspend active task + add interrupt task

Crash recovery:
  _recover()
    ├─ PENDING/PAUSED → re-add to scheduler queue
    └─ ACTIVE         → demote to PAUSED (treated as interrupted)
```

---

## 3.5 SkillRunner (inherits RARKKernel)

The skill execution layer — users register `async def` functions; the kernel drives them automatically.

```python
@runner.skill("pour_water")
async def pour_water(task: Task) -> None:
    ...  # normal return → TASK_COMPLETE; exception → TASK_FAIL
```

**Execution flow**:

```
_tick() detects a newly ACTIVE task
  → _launch_skill(task)
      ├─ not registered → immediately emit TASK_FAIL
      └─ registered → asyncio.create_task(_run_skill(task, fn))
                       ├─ fn() returns normally → emit TASK_COMPLETE
                       ├─ fn() raises CancelledError → re-raise (do NOT emit TASK_FAIL)
                       └─ fn() raises other exception:
                            ├─ retry budget remaining → emit TASK_RETRY (retry_count++)
                            └─ budget exhausted → emit TASK_FAIL

_on_interrupt() override
  → _cancel_running_skill()   # cancel asyncio.Task + await cleanup
  → super()._on_interrupt()   # standard PAUSED flow

_on_cancel() override
  → if cancelling the ACTIVE task → _cancel_running_skill()
  → super()._on_cancel()
```

**Key design**: `_tick()` uses `is not` (object identity) to detect a newly promoted task, preventing the same task from being launched twice.

### Skill Resume: The Checkpoint Pattern

`task.metadata` is the state-passing channel between a skill and the kernel. The kernel automatically persists metadata on interrupt; on resume the same object is passed back, allowing the skill to read its last checkpoint:

```python
@runner.skill("pour_water")
async def pour_water(task: Task) -> None:
    stage = task.metadata.get("stage", 0)

    if stage < 1:
        task.metadata["stage"] = 1   # sync write — before await — safe from cancel
        await move_to_position()     # ← may be cancelled here

    if stage < 2:
        task.metadata["stage"] = 2
        await pour()

    # all done — normal return → TASK_COMPLETE
```

**Why this is reliable**:
- `task.metadata["stage"] = 1` is a synchronous dict write completed before `await`; a cancel at the `await` point does not lose the checkpoint
- On interrupt, `_on_interrupt` calls `_store.upsert(task)` to write metadata to SQLite
- On in-process resume the same Python object is used; on crash recovery it is loaded from SQLite

### Retry Mechanism

Skills that encounter transient failures can be automatically retried:

```python
Task(
    name="read_sensor",
    priority=5,
    metadata={"max_retries": 3, "retry_delay": 1.0},
)
```

On exception: if `retry_count < max_retries`, increment `retry_count`, emit `TASK_RETRY` → task returns to PENDING. After `max_retries` exhausted, emit `TASK_FAIL`.

---

## 3.6 Persistence (SQLiteStore)

- `upsert(task)` writes the DB after every state transition
- `load_all()` loads all tasks at startup for crash recovery
- `:memory:` supported for testing
- **WAL mode** (`PRAGMA journal_mode=WAL`) enabled — journal can be replayed on crash, reducing data corruption risk

---

## 3.7 Public Exports (`__init__.py`)

```python
from rark import SkillRunner, Task, Event, EventType, LifecycleState
```

Top-level package exports for callers; no need to know internal module paths.

---

# 4. HTTP Server Layer (`server.py`)

## 4.1 Design Principle

The HTTP layer is a thin wrapper containing no business logic:

- `create_app(runner: SkillRunner) -> FastAPI` — factory function accepting a SkillRunner with skills already registered
- `lifespan` starts `runner.start()` and `asyncio.create_task(runner.run_loop())`
- Route handlers only extract parameters → call runner → serialize response

## 4.2 Route Summary

| Method   | Path           | Description                             |
|----------|----------------|-----------------------------------------|
| `GET`    | `/health`      | Kernel status + currently active task   |
| `GET`    | `/tasks`       | All known tasks                         |
| `POST`   | `/tasks`       | Submit a new task (returns 201)         |
| `GET`    | `/tasks/{id}`  | Look up task by ID (404 if missing)     |
| `DELETE` | `/tasks/{id}`  | Cancel a task (emits TASK_CANCEL)       |
| `POST`   | `/interrupt`   | High-priority interrupt (emits INTERRUPT) |

## 4.3 Request / Response Model

```python
# Submit a task
POST /tasks
{"name": "pour_water", "priority": 5, "metadata": {"target": "kitchen"},
 "blocked_by": ["<task-id>"]}  # optional dependency

# Response
{"id": "...", "name": "pour_water", "state": "pending", "priority": 5, "metadata": {...}}

# Interrupt
POST /interrupt
{"name": "avoid_obstacle", "priority": 10}
```

## 4.4 Lifespan and run_loop Relationship

```
FastAPI lifespan start
  → runner.start()                               # open SQLite + crash recovery
  → asyncio.create_task(runner.run_loop())       # background event loop

HTTP request handling (within lifespan)
  → runner.submit(task)    # pre-register + emit TASK_SUBMIT
  → run_loop consumes events asynchronously, drives skill execution

FastAPI lifespan shutdown
  → runner.stop()          # close SQLite
  → loop_task.cancel()     # stop event loop
```

## 4.5 Testing Note

`httpx.ASGITransport` **does not trigger FastAPI lifespan**, so `run_loop()` does not run in tests.

Solution: `SkillRunner.submit()` and `interrupt()` call `scheduler.register(task)` before emitting the event, making the task immediately queryable without waiting for `run_loop` to process it. This is an architectural decision, not a test workaround.

---

# 5. Key Sequence Diagrams

## 5.1 Normal Task Completion

```
submit(task)
  → emit(TASK_SUBMIT)
  → [drain] _on_submit → scheduler.add()
  → [tick]  task → ACTIVE + _launch_skill()
  → [sleep(0)] skill runs → emit(TASK_COMPLETE)
  → [drain] _on_complete → task → COMPLETED
```

## 5.2 Interrupt Scenario

```
task_A ACTIVE + skill running
  → emit(INTERRUPT, task_B)
  → [drain] _on_interrupt
      → _cancel_running_skill()  # task_A's skill is cancelled
      → scheduler.suspend(task_A)  # task_A → PAUSED
      → scheduler.add(task_B)
  → [tick]  task_B → ACTIVE + _launch_skill()
  → task_B skill completes → COMPLETED
  → [tick]  task_A → ACTIVE (resume) + _launch_skill()  # skill re-runs from checkpoint
  → task_A skill completes → COMPLETED
```

**Note**: on resume, the skill re-runs from its checkpoint stage rather than the beginning. Progress is conveyed via `task.metadata`.

---

# 6. Time-Scale Boundary

RARK operates at the **task layer**, not the control layer. The two layers operate at very different time scales:

```
Control layer  ~1 ms   hard real-time   joint position / torque / sensor reads  ← ROS 2 / firmware
Task layer     ~1 s    soft real-time   task scheduling / state transitions / skill calls  ← RARK
```

RARK's `run_loop()` idle interval is 0.1 s — this alone indicates it is not on the control loop. RARK's responsibility is: **deciding at the second scale which task executes right now.** Specific motor commands are issued by skills calling lower-level control interfaces.

**RARK does not replace or interfere with the real-time control layer.**

---

# 7. Execution Semantics and Crash Safety

## 7.1 At-Least-Once Semantics

RARK provides **at-least-once** execution semantics. The critical path for event handling is:

```
task.transition(NEW_STATE)   # ① memory updated first
await store.upsert(task)     # ② then persisted (SQLite ACID transaction)
```

If the process crashes after ① but before ② commits, the DB retains the old state and the task will be rescheduled on restart.

**Therefore, skills should be idempotent where possible**, or use `task.metadata` checkpoints to skip already-completed stages (see Section 3.5).

SQLite uses **WAL mode** (`PRAGMA journal_mode=WAL`), which reduces data corruption risk by allowing the journal to be replayed after a crash.

## 7.2 Crash Recovery Policy (crash_policy)

On restart, tasks still showing ACTIVE in the DB (state was not updated before the crash) are handled according to:

| policy | Behaviour | Suitable for |
|--------|-----------|--------------|
| `"resume"` (default) | ACTIVE → PAUSED, re-added to scheduler | Skills implementing metadata checkpoints; idempotent operations |
| `"fail"` | ACTIVE → FAILED, not rescheduled | Strict physical consistency requirements; skills that cannot safely re-run |

```python
# Default: resume (relies on skill checkpoint handling)
runner = SkillRunner(db_path="robot.db")

# Safety mode: fail (requires manual resubmit and physical state verification)
runner = SkillRunner(db_path="robot.db", crash_policy="fail")
```

## 7.3 Physical Consistency Is the Skill's Responsibility

Software can checkpoint-resume; the physical world cannot roll back. RARK provides the mechanism (metadata persistence, crash_policy), but cannot make physical judgements on behalf of the skill:

- Where did the robot's joints stop when power was cut?
- Is an object still in the gripper?
- Are sensor readings valid?

Skill authors should read `task.metadata["checkpoint"]` at the resume entry point, combine it with physical sensor data, and decide which stage to continue from — or whether a safe home position is needed first.

---

# 8. Priority Scheduling and Priority Inversion

## 8.1 Scheduling Algorithm

RARK uses **fixed priority + max-heap**:

```python
heapq.heappush(self._heap, (-task.priority, task.id))  # negate to simulate max-heap
```

- Scheduling time complexity: O(log n)
- Priority is immutable after submission (design decision: priority semantics should be fixed at submission time)
- Equal-priority tasks ordered by task_id lexicographically (UUID, approximately FIFO)

## 8.2 Why Classic Priority Inversion Doesn't Apply

Classic priority inversion requires: low-priority task holds a lock → high-priority task waits for the lock → medium-priority task preempts the low-priority task → high-priority task is indirectly blocked.

In RARK's single-active-task model:
- **No inter-task resource locks** (skills do not hold cross-task shared locks)
- High-priority interrupts **hard-cancel** the current skill via `asyncio.Task.cancel()`, rather than waiting for it to release a lock

RARK therefore does not experience classic priority inversion.

---

# 9. Test Coverage

| Test file         | Tests | Scenarios covered                                              |
|-------------------|-------|----------------------------------------------------------------|
| `test_task.py`    | 11    | State transition legality, illegal transition rejection, timestamp updates |
| `test_kernel.py`  | 14    | Interrupt/resume, priority ordering, crash recovery (resume/fail), cancellation, dependencies |
| `test_runner.py`  | 10    | Auto-complete/fail, interrupt cancellation, cross-instance DB recovery, metadata checkpoint, retry |
| `test_server.py`  | 9     | health, submit, list, get, get_404, cancel, cancel_404, interrupt, metadata |

---

# 10. Known Design Constraints

| Constraint                     | Impact                                                          |
|--------------------------------|-----------------------------------------------------------------|
| Single active task             | Only one task in ACTIVE at a time; suitable for single-body embedded robots |
| Skill re-runs from checkpoint  | Resumption does not restore coroutine state; skill manages its own progress via metadata |
| Priority immutable after submit | Task priority cannot be dynamically adjusted after enqueuing   |
