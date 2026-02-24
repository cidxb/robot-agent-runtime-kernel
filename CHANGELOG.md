# Changelog

All notable changes to RARK are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [0.1.0] — 2026-02-25

Initial release of the Robot Agent Runtime Kernel.

### Added

**Core kernel**
- `Task` dataclass with full lifecycle state machine (PENDING → ACTIVE → PAUSED/COMPLETED/FAILED/CANCELLED)
- `RARKKernel` event-driven scheduler with asyncio event queue and 100 ms idle tick
- Priority preemption: highest-priority task always wins; interrupt drops the active task to PAUSED immediately
- Suspend & resume: interrupted tasks re-enter the queue with metadata intact
- Configurable crash recovery via `crash_policy` parameter:
  - `"resume"` (default): ACTIVE tasks transition to PAUSED on restart, resuming from their last checkpoint
  - `"fail"`: ACTIVE tasks transition to FAILED on restart, requiring explicit resubmit

**SkillRunner**
- `@runner.skill(name)` decorator for registering `async def` skill functions
- Automatic TASK_COMPLETE / TASK_FAIL emission on skill return / exception
- Retry mechanism via `metadata["max_retries"]` / `metadata["retry_delay"]`
- Checkpoint-based resume: skills read/write `task.metadata` to survive interruption

**Scheduler**
- Priority heap with O(log n) pick_next
- Task dependency graph via `blocked_by: Set[str]` field
- `release_dependents()` automatically unblocks waiting tasks on completion

**Persistence**
- SQLite store with WAL journal mode for crash resilience
- Full task state + metadata + blocked_by persisted at every transition
- Cross-instance crash recovery tested

**HTTP API** (requires `.[server]` extra)
- FastAPI application via `create_app(runner)` factory
- Endpoints: `GET /health`, `GET /tasks`, `POST /tasks`, `GET /tasks/{id}`, `DELETE /tasks/{id}`, `POST /interrupt`
- Interactive docs at `/docs`

**Examples**
- `rark/examples/interrupt_demo.py` — CLI demonstration of preemption and resume flow
- `rark/examples/server_demo.py` — runnable HTTP server with mock skills
- `rark/examples/llm_demo.py` — LLM planning → RARK execution integration pattern

**Tests**
- 38 tests across four modules (kernel, scheduler, runner, HTTP)
- Coverage: state machine, preemption, retry, dependencies, crash recovery, HTTP endpoints

**Docs**
- `docs/01-why.md` — motivation and problem space
- `docs/02-architecture.md` — internal design, time-scale boundary, at-least-once semantics, crash_policy
- `docs/03-ecosystem.md` — comparison with ROS 2 actions, BehaviorTree.CPP, Temporal, Prefect
- `docs/04-roadmap.md` — completed phases and intentional non-goals
