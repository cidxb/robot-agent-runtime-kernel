> **English** | [中文](../zh/03-ecosystem.md)

# RARK Ecosystem Analysis: Positioning and Differentiation

> Research date: February 2026, based on live documentation from each framework.

---

# 1. Frameworks Surveyed

| Framework  | Description                               | Source                 |
|------------|-------------------------------------------|------------------------|
| OpenClaw   | Personal AI assistant (IM channel access) | docs.openclaw.ai       |
| AgentScope | LLM multi-agent orchestration (Alibaba)   | doc.agentscope.io      |
| AutoGen    | LLM multi-agent orchestration (Microsoft) | microsoft.github.io    |

---

# 2. OpenClaw

## 2.1 Positioning

OpenClaw (formerly Clawdbot / Moltbot) is a **personal AI assistant framework**, not a robot runtime. Its design goal is to connect an LLM agent to channels like WhatsApp, Telegram, and Slack and autonomously execute tasks on the user's machine.

## 2.2 Agent Loop

```
intake → context assembly → model inference → tool execution → reply → persist
```

Default timeout: 600 s. Explicit cancellation via `AbortSignal`.

## 2.3 Task States

```
pending → blocked → claimed → in-progress → completed / failed
```

Includes a `blocked` (waiting for dependency) state — something RARK did not have until the task-dependency feature was added.

## 2.4 Scheduling Mechanism

**Serialized execution lanes** — tasks within the same session queue serially. No priority preemption. No PAUSED/resume semantics.

"Interruption" is message-level steering — injecting a new message at the next tool-call checkpoint. **Not a task-level preemption.**

## 2.5 Skill System

Skills are reusable modules (mintlify skill, 1password skill, etc.) loaded from a workspace directory. No formal registration or scheduling mechanism documented.

---

# 3. AgentScope

## 3.1 Positioning

A production-grade multi-agent framework for LLM applications, with `ReActAgent` (reasoning + tool execution) as its core abstraction.

## 3.2 Interrupt Mechanism (Most Noteworthy)

AgentScope's interruption implements two layers of semantics:

1. **asyncio cancellation**: calling `agent.interrupt()` cancels the current reply asyncio.Task
2. **Partial result preservation**: cancelled tools that already `yield`-ed partial results have those results saved ("gracefully preserves all results yielded up to that point")
3. **Seamless resumption**: Distributed Interrupt Service supports context-preserving resumption

> **RARK's current gap**: `_cancel_running_skill()` is a hard cancel with no partial result preservation; after resumption the skill re-runs from the beginning.

## 3.3 Task Decomposition: PlanNotebook

For complex multi-step tasks, AgentScope provides `PlanNotebook`:

- Splits complex goals into ordered, trackable steps
- Supports creating, modifying, pausing, and resuming multiple concurrent plans
- Can resume from already-completed steps after an interruption

RARK tasks are currently atomic (not decomposable into sub-steps).

## 3.4 Concurrent Execution

```python
# AgentScope parallel tool calls
results = await asyncio.gather(*[execute_tool(tc) for tc in tool_calls])
```

RARK uses a single-active-task model (uniprocessor) and does not support concurrent execution — a deliberate constraint for embedded robots.

## 3.5 Observability

Full OpenTelemetry integration with distributed tracing. RARK currently uses structured logging (`logging.getLogger("rark")`).

---

# 4. AutoGen 0.4

## 4.1 Positioning

An event-driven multi-agent framework based on the Actor model, targeting LLM application orchestration.

## 4.2 Runtime Model

```
Core API:      Event-driven Actor runtime (cross-language: .NET / Python)
AgentChat API: High-level wrapper for common multi-agent patterns
               (two-agent dialogue, GroupChat, etc.)
```

The biggest change in 0.4: from synchronous `ConversableAgent` conversation patterns to **fully async Actor message passing**.

## 4.3 Scheduling and Preemption

**No priority scheduling or task-level preemption mechanism found in official documentation.** AutoGen's message routing is handled through a centralized component that aids observability but is not a priority-based preemptive scheduler.

---

# 5. Framework Comparison vs. RARK

| Dimension                     | OpenClaw        | AgentScope       | AutoGen 0.4      | RARK             |
|-------------------------------|-----------------|------------------|------------------|------------------|
| **Target domain**             | Personal AI assistant | LLM multi-agent | LLM multi-agent | Robot task runtime |
| **Priority preemption**       | ❌ serial lanes | ❌               | ❌               | ✅ max-heap      |
| **PAUSED + resume**           | ❌              | ❌ (coroutine preserved) | ❌          | ✅ first-class   |
| **Partial result on interrupt** | ❌            | ✅               | ❌               | ❌ (future work) |
| **Crash recovery**            | ❌              | checkpoint semantics | ❌           | ✅ SQLite WAL    |
| **Task dependencies (BLOCKED)**| ✅             | PlanNotebook     | ❌               | ✅               |
| **LLM integration**           | ✅ core         | ✅ core          | ✅ core          | ❌ intentionally excluded |
| **Multi-agent coordination**  | ✅              | ✅               | ✅               | ❌ (single kernel) |
| **Observability**             | basic           | OpenTelemetry    | basic            | structured logging |
| **Embedded / robot fit**      | ❌              | ❌               | ❌               | ✅ design goal   |

---

# 6. RARK Is Not Reinventing the Wheel

## 6.1 Different Problem Domain

All three frameworks above are **LLM-centric multi-agent orchestration tools** solving "how do multiple LLM agents collaborate on a complex task?"

RARK solves the **task survival problem for robot systems**: priority preemption, PAUSED resumption, continuing after a crash and reboot — concepts closer to an RTOS task scheduler than an LLM agent framework.

## 6.2 The Closest Horizontal Analogies

| Analogy               | Similarity                              |
|-----------------------|-----------------------------------------|
| FreeRTOS              | Priority task scheduling, suspend/resume |
| ROS 2 Action          | Preemptible goal, cancel/feedback       |
| Linux process scheduler | Multi-task preemption, persistent tasks |

RARK implements these concepts at the Python asyncio layer, tailored for robot agent scenarios.

## 6.3 The Unique Combination RARK Offers

**Numeric priority preemption + PAUSED state + asyncio.Task cancellation + SQLite crash recovery** — this combination does not exist in any of the three frameworks surveyed.

---

# 7. Useful Takeaways from the Survey

## 7.1 From AgentScope

**Preserve partial results on interrupt**: RARK currently hard-cancels skills. For operations with external side effects (instructions already sent to an actuator), this can be a problem. `task.metadata` serves as the channel for skills to communicate progress.

**PlanNotebook concept**: For complex multi-step robot tasks (navigate to kitchen → find cup → grasp → pour), sub-task tree support will eventually be needed.

## 7.2 From OpenClaw

**BLOCKED state**: Task dependency graph (task B waits for task A to complete before starting). Now implemented in RARK's scheduler.

## 7.3 Intentionally Excluded

| Feature               | Reason for exclusion                                          |
|-----------------------|---------------------------------------------------------------|
| LLM reasoning layer   | RARK is an LLM-agnostic scheduling kernel; layers must stay separate |
| Distributed Actors    | Single-node embedded robot scenario; distributed adds unnecessary complexity |
| MCP tool integration  | Belongs above SkillRunner; not a kernel responsibility        |
| Multi-agent coordination | Single-kernel model is an intentional constraint for embedded systems |
