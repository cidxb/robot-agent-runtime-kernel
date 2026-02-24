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

Read `docs/02-architecture.md` before touching the kernel — it documents time-scale boundaries, at-least-once semantics, and crash recovery invariants that are easy to break accidentally.

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

## Good first issues

These are well-scoped, self-contained, and have clear acceptance criteria:

- **WebSocket event stream** — add a `GET /ws/events` endpoint that pushes task state changes in real-time (FastAPI `WebSocket`)
- **`blocked_by` in HTTP API** — expose the `blocked_by` field in `POST /tasks` so dependency chains can be submitted over REST
- **ROS 2 skill adapter template** — write an `async def ros_action_skill(task)` that wraps a ROS 2 action client
- **LLM integration guide** — extend `examples/llm_demo.py` with a real Claude or OpenAI call and add to docs

---

## Bigger projects

These need a design discussion (open an issue first):

- **Time-bounded tasks** — `deadline` field that auto-cancels overdue tasks
- **Task groups** — submit a set of tasks as a unit; cancel all if any fails
- **OpenTelemetry spans** — inject trace context at state transition hooks
- **Persistent `blocked_by`** — dependency chains that survive a full restart

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
