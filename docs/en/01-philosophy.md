> **English** | [中文](../zh/01-philosophy.md)

# RARK: Robot Agent Runtime Kernel

## A Temporal Consistency Kernel for Real-World Human-Robot Interaction

---

# 1. Background and Problem Definition

## 1.1 The Current Focus of Embodied AI

The industry is largely converging on:

- VLA (Vision-Language-Action) models
- Long-horizon planning
- End-to-end policy models
- LLMs as high-level decision makers

These directions primarily address:

> How to generate complex action sequences.

But in real robot deployments, the genuinely hard problems are not about action generation. They are:

- Tasks get interrupted
- Actions fail
- Humans change goals mid-execution
- Systems crash
- Power runs out
- Tasks span hours or days

Current models share an unstated assumption:

> The execution environment is stable and continuous.

The real world is not.

---

## 1.2 The Real Challenges of Robot Deployment

Real robot systems exhibit the following characteristics:

### Strong Human-Robot Interaction

- Users interrupt at any moment
- Users modify tasks in progress
- Users inject higher-priority demands

### Long-Running Operation

- Tasks span hours or even days
- Robots run continuously online

### System-Level Instability

- Power cuts
- Network failures
- Sensor anomalies
- Execution failures

### Multi-Task Concurrency

- Main task
- Voice interaction
- Battery management
- Safety monitoring
- Background maintenance

These are not model capability problems. They are:

> Temporal consistency and task survival problems.

---

# 2. Structural Problems with Existing Architectures

## 2.1 Traditional FSM (Finite State Machines)

**Pros:** Controllable, predictable, engineering-mature

**Cons:**

- Logic explosion as complexity grows
- No lifecycle management
- No task objectification
- No persistence
- No system-level interrupt mechanism
- Poor support for complex human-robot interaction

An FSM is fundamentally:

> A tool for describing behavioral flow.

It is not a system execution mechanism.

---

## 2.2 Pure VLA / Long-Horizon Planning Architectures

**Pros:** Strong generalization, strong semantic understanding, strong compositional ability

**Cons:**

- No task lifecycle management
- No task persistence
- No system scheduling capability
- No crash recovery capability
- Tasks cannot survive long-term

A model can be intelligent, but a model cannot be an operating system.

---

# 3. The Core Insight

What robot deployment actually lacks is not:

> A more capable model.

It is:

> A temporal structure capable of carrying intelligence.

This is the problem RARK is designed to solve.

---

# 4. What RARK Is

## 4.1 Definition

RARK (Robot Agent Runtime Kernel) is:

> An execution kernel that manages "how tasks persist through time."

**It is responsible for:**

- Task lifecycle management
- Multi-task scheduling
- Interruption and resumption
- System-level state consistency
- Task persistence
- Crash recovery

**It is NOT responsible for:**

- Semantic decision-making
- Visual perception
- Control algorithms
- Behavior generation

---

# 5. Where RARK Sits in the System

## 5.1 Three-Layer Architecture

```
┌────────────────────────────────────┐
│          Cognitive Layer           │
│  (VLA / LLM / Planner / Policy)    │
│                                    │
│  • Semantic understanding          │
│  • Long-horizon planning           │
│  • Behavior generation             │
└────────────────────────────────────┘
                    ↓
┌────────────────────────────────────┐
│        Task Runtime Layer          │
│          (RARK Kernel)             │
│                                    │
│  • Task object management          │
│  • Lifecycle management            │
│  • Interruption & resumption       │
│  • Multi-task scheduling           │
│  • Persistence                     │
│  • System-level consistency        │
└────────────────────────────────────┘
                    ↓
┌────────────────────────────────────┐
│         Control / ROS Layer        │
│                                    │
│  • ROS 2 Nodes                     │
│  • Action / Topic                  │
│  • Motion Control                  │
│  • Perception Nodes                │
└────────────────────────────────────┘
```

## 5.2 Separation of Responsibilities

| Layer     | Responsible for              |
|-----------|------------------------------|
| Cognitive | Deciding *what* to do        |
| RARK      | Managing *how tasks exist*   |
| ROS       | Executing *how to do it*     |

---

# 6. The Real Role of LLMs in the RARK Architecture

## 6.1 Is an LLM Required as the "Brain"?

Not necessarily. RARK can run on top of a rule-based system.

But in modern embodied AI systems:

> An LLM is strongly recommended as the semantic policy layer.

The key point:

**LLM handles Policy**
**RARK handles Mechanism**

---

## 6.2 The Policy vs. Mechanism Separation Principle

In mature system design:

- **Policy** = deciding *what should be done*
- **Mechanism** = deciding *whether it can be done right now*

**LLMs can participate in:**

- Task priority recommendations
- Urgency assessment
- User intent parsing
- High-level task decomposition

**LLMs should NOT directly control:**

- Lifecycle state transitions
- The system scheduler
- Crash recovery logic
- Safety constraint enforcement (battery, physical safety)

---

## 6.3 Does Scheduling Need an LLM?

✅ LLMs can participate in priority calculation
❌ LLMs should not own the scheduling mechanism

**Example scenario:**

- Making coffee
- Doorbell rings
- Battery at 3%

The LLM can judge: "Opening the door is higher priority than coffee."

But the RARK kernel must guarantee:

```python
if battery < 5%:
    force_enter_charging_routine()
```

Physical safety constraints must not be overridable by an LLM.

---

## 6.4 The Correct Architectural Relationship

```
LLM  → provides priority_score
RARK → applies safety rules and scheduling policy
ROS  → executes physical behavior
```

Core principle:

> LLM decides *what should be done*
> RARK decides *whether it can be done right now*

---

## 6.5 Why Can't the LLM Manage Scheduling Directly?

- LLMs do not guarantee real-time behavior
- LLMs do not guarantee determinism
- LLMs have no awareness of physical safety constraints
- LLMs are unsuited for strong-consistency control
- LLMs are unsuited for managing crash recovery

They are good at generating policies, not at being an operating system.

---

# 7. RARK's Core Abstractions

## 7.1 Tasks Are First-Class Citizens

```python
class Task:
    id
    name
    lifecycle_state   # system-level: PENDING/ACTIVE/PAUSED/COMPLETED/FAILED/CANCELLED
    business_state    # behavior-level: GoToKitchen/GraspCup/PourWater (future extension)
    context           # execution context (can survive restarts)
    parent_task       # subtask tree
    priority          # integer, higher = more urgent
    created_at
    updated_at
    metadata          # arbitrary additional data
```

Key ideas:

- A task is a persistent object
- A task can be paused
- A task can be resumed
- A task can be cancelled
- A task can survive a restart

---

## 7.2 Lifecycle (System-Level State)

Lifecycle and business state are separated.

### Lifecycle (current implementation)

```
PENDING → ACTIVE → COMPLETED
                 → FAILED
                 → CANCELLED
        → PAUSED → ACTIVE  (resume)
```

### Business state (future extension)

```
GoToKitchen → GraspCup → PourWater
```

Lifecycle solves system-level problems. Business state solves behavioral logic problems.

---

## 7.3 Event-Driven Kernel

Core mechanism:

```
Event → Dispatch → Transition → Persist
```

Event sources:

- User commands (TASK_SUBMIT, INTERRUPT, TASK_CANCEL)
- Skill execution results (TASK_COMPLETE, TASK_FAIL)
- Timer (idle tick triggers scheduling)
- System anomalies (post-crash recovery)

---

## 7.4 Persistence Is a Mandatory Capability

Minimum implementation:

- SQLite (current implementation)
- Task snapshots (written to DB after every state transition)
- Supports crash recovery, power-cut recovery, task tracking

---

# 8. RARK vs. Traditional State Machines

## 8.1 The Fundamental Difference

A state machine answers:

> What state do I enter next?

RARK answers:

> How does this task persist and survive in real time?

## 8.2 Comparison Table

| Dimension          | Traditional FSM | RARK              |
|--------------------|-----------------|-------------------|
| Core unit          | State           | Task              |
| Time               | Implicit        | Explicit          |
| Lifecycle          | None            | First-class       |
| Persistence        | None            | Mandatory         |
| Interruption       | Hand-coded      | System-level      |
| Crash recovery     | None            | Native support    |
| Multi-task         | Difficult       | Native design     |
| System consistency | None            | Core capability   |

## 8.3 Analogy

An FSM is like a function. RARK is like an OS process manager.

---

# 9. Real Human-Robot Interaction Scenario

## Scenario: A Household Service Robot's Day

**9:00 AM** — user says:

> "Make me a coffee."

During execution:

1. Robot navigates to kitchen
2. Starts boiling water
3. Doorbell rings
4. User says: "Answer the door first"
5. Robot opens the door
6. Returns to kitchen
7. Power cut during boiling
8. Power restored 10 minutes later
9. Robot reboots
10. Should continue the coffee task

This scenario demonstrates RARK's six core capabilities.

---

## 9.1 Task Objectification

**Without task objectification:**

```
current_state = "boiling_water"
```

After a power cut, this state is gone — no record of what happened.

**With task objectification:**

```python
Task {
    id: 42,
    type: "MakeCoffee",
    business_state: "BoilingWater",
    lifecycle: "RUNNING",
    context: {...}
}
```

The task is an entity, not a variable or function call — it is an object in the database.

> Task objectification = tasks become first-class system entities.

---

## 9.2 Lifecycle Separation

**Without lifecycle separation, the FSM might be:**

```
BoilingWater → PourWater → Done
```

Unable to express: interrupted, paused, resumed, failed.

**With lifecycle separation, a task has two layers of state:**

- Business state: `BoilingWater`
- Lifecycle state: `RUNNING` / `PAUSED` / `FAILED` / `RECOVERING`

When the doorbell rings:

```
BoilingWater + INTERRUPT
→ lifecycle = PAUSED
```

Business state unchanged. Lifecycle state changes independently.

> Lifecycle = system-level state. Business state = behavioral-level state. This is layer separation.

---

## 9.3 Scheduling System

The doorbell has rung. The system now has two tasks:

- MakeCoffee (priority=5)
- OpenDoor (priority=8)

```
priority(OpenDoor) > priority(MakeCoffee)
```

The scheduler decides: suspend coffee → execute open-door → resume coffee when done.

> Scheduling = deciding which task owns execution rights right now. This is not the FSM's job.

---

## 9.4 Persistence System

Power cut during boiling. Without persistence, all in-memory state is lost.

With persistence, the database still contains:

```python
Task 42: type=MakeCoffee, business_state=BoilingWater, lifecycle=RUNNING
```

> Persistence = the guarantee of temporal continuity.

---

## 9.5 Crash Recovery

On restart, RARK loads:

```python
unfinished_tasks = load_unfinished()
```

Finds Task 42 unfinished, and:

```
lifecycle: ACTIVE → PAUSED  (crash treated as interrupt)
→ ACTIVE  (rescheduled)
```

Resumes boiling water.

> Crash recovery = reconnecting a break in time. Not a feature of FSMs.

---

## 9.6 Multi-Task Support

In a real system, multiple tasks coexist: coffee task, open-door task, battery monitoring, safety monitoring, voice interaction.

Without multi-task support, all logic is coupled and the system becomes spaghetti code.

With multi-task support, the scheduler manages everything uniformly, each task exists independently.

---

## 9.7 Summary: Six Capabilities

| Capability            | Meaning in the coffee scenario               |
|-----------------------|----------------------------------------------|
| Task objectification  | "Make coffee" is a storable entity           |
| Lifecycle separation  | Can pause without losing business state      |
| Scheduling system     | Decides door has higher priority             |
| Persistence system    | Task survives power cut                      |
| Crash recovery        | Resumes boiling after reboot                 |
| Multi-task support    | Coffee + door + battery monitoring coexist   |

---

# 10. Scheduling Capability

RARK supports:

- Priority scheduling (max-heap, higher number = higher priority)
- Preemption (high-priority interrupt → current task → PAUSED)
- Single-active-task model (uniprocessor, suitable for embedded robots)

Scheduling mechanism is controlled by the RARK kernel. LLMs only participate in policy recommendations; they do not own scheduling authority.

---

# 11. Crash Recovery Capability

After a power cut and reboot:

- Load all unfinished tasks (PENDING/PAUSED)
- ACTIVE tasks are demoted to PAUSED (crash treated as interrupt)
- Rescheduled for execution

Without this layer, robots cannot run long-term.

---

# 12. Why This Is Not "Going Backwards"

Common misconception:

> It looks like a state machine.

The difference is in the layer. FSMs describe behavioral logic. RARK is a task execution platform. LLMs are the policy layer.

Three-layer separation is modern system design. It does not replace the model — it carries the model.

---

# 13. RARK's Strategic Significance

RARK solves:

- Real human-robot interaction problems
- Long-running operation problems
- System scheduling problems
- Task survival problems
- Product deployment stability problems

It is:

> The critical layer for embodied intelligence to reach production.

---

# 14. One-Sentence Summary

RARK is:

> The temporal operating system kernel for embodied intelligence.

LLM is the brain. ROS is the body. RARK is the management system for time and execution authority.

The question it answers is not:

> "What should the robot do next?"

It is:

> "How does the robot run reliably, continuously, and under control in the real world over the long term?"
