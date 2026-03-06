# Contributing to RARK

RARK is an early-stage project with a lot of interesting ground to cover. Every contribution — bug reports, docs fixes, new features, and architectural feedback — is genuinely useful.

---

## Ground rules

- **Tests first.** Every non-trivial change needs a test. See `rark/tests/` for patterns.
- **No new `print()`**. Use `logging.getLogger("rark")` throughout.
- **Stay focused.** RARK is intentionally narrow. See [what we're not building](docs/04-roadmap.md#刻意不做的事) before proposing large scope expansions.
- **No breaking changes to the public API** without a discussion issue first.

---

## Development setup

```bash
git clone https://github.com/cidxb/robot-agent-runtime-kernel.git
cd rark

python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,server]"

pytest rark/tests/ -v    # should be green before you start
```

---

## Project layout

```
rark/
├── core/
│   ├── task.py           Task dataclass + state machine
│   ├── transitions.py    Legal state transition table
│   ├── events.py         Event enum
│   ├── scheduler.py      Priority heap + dependency resolution
│   ├── kernel.py         Event loop, crash recovery, all handlers
│   └── runner.py         asyncio skill execution + retry
├── persistence/
│   └── sqlite_store.py   SQLite WAL store
├── server.py             FastAPI HTTP layer
├── tests/                38 tests across four modules
└── examples/             Runnable demos
```

Read [`docs/en/02-architecture.md`](docs/en/02-architecture.md) (or [`docs/zh/02-architecture.md`](docs/zh/02-architecture.md) for Chinese) before touching the kernel — it documents time-scale boundaries, at-least-once semantics, and crash recovery invariants that are easy to break accidentally.

---

## Making a change

```bash
git checkout -b feat/your-feature-name
# write code + tests
pytest rark/tests/ -v
git commit -m "feat(core): describe what and why"
git push origin feat/your-feature-name
# open a PR
```

Commit message format: `type(scope): short description`

| Type | When |
|---|---|
| `feat` | New capability |
| `fix` | Bug fix |
| `test` | Tests only |
| `docs` | Documentation |
| `refactor` | No behaviour change |
| `chore` | Tooling, CI, dependencies |

---

## Good first issues (Phase 4)

These are well-scoped, self-contained, and have clear acceptance criteria. See [Roadmap Phase 4](docs/en/04-roadmap.md#phase-4-api--safety-completion-high-priority) for full specs.

- **`blocked_by` + `retry` in HTTP API** (4.1) — expose `blocked_by`, `max_retries`, `retry_delay` in `POST /tasks`
- **WebSocket event stream** (4.2) — add a `GET /ws/events` endpoint that pushes task state changes in real-time
- **Time-bounded tasks** (4.3) — `deadline` field that auto-fails overdue tasks

---

## Bigger projects (Phase 5–6)

These need a design discussion (open an issue first). See [Roadmap Phase 5–6](docs/en/04-roadmap.md#phase-5-execution-isolation-medium-priority) for full specs.

- **Subprocess skill isolation** (5.1) — run skills in child processes; crash doesn't take down the kernel
- **Resource Domains** (5.2) — per-subsystem scheduling for multi-actuator robots
- **Task groups** (6.1) — submit a set of tasks as a unit; cancel all if any fails
- **ROS 2 skill adapter** (6.2) — reusable adapter wrapping ROS 2 action clients
- **OpenTelemetry spans** (6.3) — inject trace context at state transition hooks

---

## PR checklist

Before opening a pull request:

- [ ] `pytest rark/tests/ -v` passes
- [ ] New tests cover every changed behaviour
- [ ] `CHANGELOG.md` updated under `[Unreleased]`
- [ ] Docs updated if public API changed

---

## Questions?

Open a [discussion](https://github.com/cidxb/robot-agent-runtime-kernel.git/discussions) rather than an issue for open-ended questions.
