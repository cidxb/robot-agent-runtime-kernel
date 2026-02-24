> [English](../en/01-philosophy.md) | **中文**

# RARK：Robot Agent Runtime Kernel

## —— 面向真实人机交互与机器人落地的时间一致性内核

---

# 1. 背景与问题定义（Why）

## 1.1 当前具身智能的主流方向

当前行业主流聚焦于：

- VLA（Vision-Language-Action）
- 长程规划
- 端到端策略模型
- LLM 作为高层决策器

这些方向主要解决：

> 如何生成复杂行为序列。

但在真实机器人落地项目中，真正困难的问题并不是行为生成，而是：

- 任务会被打断
- 行为会失败
- 人类会修改目标
- 系统会崩溃
- 电量会耗尽
- 任务跨小时/跨天存在

当前模型默认一个隐含前提：

> 执行环境是稳定且连续的。

但现实世界不是。

---

## 1.2 机器人落地的真实挑战

真实机器人系统具有以下特征：

### ✅ 强人机交互

- 用户随时打断
- 用户临时修改任务
- 用户插入优先级更高的需求

### ✅ 长时间运行

- 任务跨小时甚至跨天
- 机器人持续在线运行

### ✅ 系统级不稳定性

- 断电
- 网络中断
- 传感器异常
- 执行失败

### ✅ 多任务并发

- 主任务
- 语音交互
- 电量管理
- 安全监控
- 后台维护

这些问题并不是模型能力问题，而是：

> 时间一致性与任务生存问题。

---

# 2. 现有架构的结构性问题

## 2.1 传统 FSM（有限状态机）

**优点：** 可控、可预测、工程成熟

**缺点：**

- 逻辑爆炸
- 无生命周期管理
- 无任务对象化
- 无持久化
- 无系统级中断机制
- 难支持复杂人机交互

FSM 本质是：

> 行为流程描述工具。

它不是系统运行机制。

---

## 2.2 纯 VLA / 长程规划架构

**优点：** 泛化能力强、语义理解强、组合能力强

**缺点：**

- 无任务生命周期管理
- 无任务持久化
- 无系统调度能力
- 无崩溃恢复能力
- 任务不可长期存在

模型可以聪明，但模型不能当操作系统。

---

# 3. 核心洞察

机器人落地真正缺的不是：

> 更强的模型。

而是：

> 一个可以承载智能的时间结构。

这就是 RARK 要解决的问题。

---

# 4. RARK 的核心定义

## 4.1 定义

RARK（Robot Agent Runtime Kernel）是：

> 一个管理"任务在时间中持续存在"的执行内核。

**它负责：**

- 任务生命周期管理
- 多任务调度
- 中断与恢复
- 系统级状态一致性
- 任务持久化
- 崩溃恢复

**它不负责：**

- 语义决策
- 视觉感知
- 控制算法
- 行为生成

---

# 5. RARK 在系统中的位置

## 5.1 三层结构

```
┌────────────────────────────────────┐
│          Cognitive Layer           │
│  (VLA / LLM / Planner / Policy)    │
│                                    │
│  • 语义理解                        │
│  • 长程规划                        │
│  • 行为生成                        │
└────────────────────────────────────┘
                    ↓
┌────────────────────────────────────┐
│        Task Runtime Layer          │
│          (RARK Kernel)             │
│                                    │
│  • 任务对象管理                    │
│  • 生命周期管理                    │
│  • 中断与恢复                      │
│  • 多任务调度                      │
│  • 持久化                          │
│  • 系统级一致性                    │
└────────────────────────────────────┘
                    ↓
┌────────────────────────────────────┐
│         Control / ROS Layer        │
│                                    │
│  • ROS2 Nodes                      │
│  • Action / Topic                  │
│  • Motion Control                  │
│  • Perception Nodes                │
└────────────────────────────────────┘
```

## 5.2 关键职责分离

| 层级      | 负责               |
|-----------|--------------------|
| Cognitive | 决策"做什么"       |
| RARK      | 管理"任务如何存在" |
| ROS       | 执行"怎么做"       |

---

# 6. LLM 在 RARK 架构中的真实角色

## 6.1 是否必须有 LLM 作为大脑？

不是必须。RARK 本身可以运行在规则系统上。

但在现代具身智能系统中：

> LLM 强烈建议作为"语义策略层"。

关键点在于：

**LLM 负责 Policy（策略）**
**RARK 负责 Mechanism（机制）**

---

## 6.2 Policy vs Mechanism 分离原则

在成熟系统设计中：

- **Policy** = 决定"应该做什么"
- **Mechanism** = 决定"现在能不能做"

**LLM 可以参与：**

- 任务优先级建议
- 紧急程度判断
- 用户意图解析
- 高层任务分解

**LLM 不应该直接控制：**

- 生命周期状态转移
- 系统调度器
- 崩溃恢复逻辑
- 安全约束机制（电量、物理安全）

---

## 6.3 调度是否需要 LLM？

✅ LLM 可以参与优先级计算
❌ LLM 不应掌控调度机制

**场景示例：**

- 烧水中
- 门铃响
- 电量 3%

LLM 可以判断："开门优先级高于咖啡。"

但 RARK 内核必须保证：

```
if battery < 5%:
    强制进入充电流程
```

物理安全约束不可被 LLM 覆盖。

---

## 6.4 正确架构关系

```
LLM  → 提供 priority_score
RARK → 应用安全规则与调度策略
ROS  → 执行物理行为
```

核心原则：

> LLM 决定"应该做什么"
> RARK 决定"现在能不能做"

---

## 6.5 为什么不能让 LLM 直接管理调度？

- LLM 不保证实时性
- LLM 不保证确定性
- LLM 不具备物理安全约束意识
- LLM 不适合做强一致性控制
- LLM 不适合管理崩溃恢复

它适合做策略生成器，而不是操作系统。

---

# 7. RARK 的核心抽象

## 7.1 任务是一等公民

```python
class Task:
    id
    name
    lifecycle_state   # 系统级：PENDING/ACTIVE/PAUSED/COMPLETED/FAILED/CANCELLED
    business_state    # 业务级：GoToKitchen/GraspCup/PourWater（未来扩展）
    context           # 执行上下文（可跨重启传递）
    parent_task       # 子任务树
    priority          # 数值，越大越紧急
    created_at
    updated_at
    metadata          # 任意附加数据
```

关键思想：

- 任务是持久化对象
- 任务可以暂停
- 任务可以恢复
- 任务可以被取消
- 任务可以跨重启存在

---

## 7.2 生命周期（系统级状态）

生命周期与业务状态分离。

### 生命周期（当前实现）

```
PENDING → ACTIVE → COMPLETED
                 → FAILED
                 → CANCELLED
        → PAUSED → ACTIVE（resume）
```

### 业务状态（未来扩展）

```
GoToKitchen → GraspCup → PourWater
```

生命周期解决系统级问题。业务状态解决行为逻辑问题。

---

## 7.3 事件驱动内核

核心机制：

```
Event → Dispatch → Transition → Persist
```

事件来源：

- 用户指令（TASK_SUBMIT、INTERRUPT、TASK_CANCEL）
- Skill 执行结果（TASK_COMPLETE、TASK_FAIL）
- 定时器（idle tick 触发调度）
- 系统异常（崩溃后恢复）

---

## 7.4 持久化是强制能力

最小方案：

- SQLite（当前实现）
- 任务快照（每次状态转换后写库）
- 支持崩溃恢复、断电恢复、任务追踪

---

# 8. RARK vs 传统状态机

## 8.1 本质区别

状态机回答：

> 下一步进入哪个状态？

RARK 回答：

> 这个任务如何在真实时间中长期存在？

## 8.2 对比表

| 维度         | 传统 FSM | RARK         |
|--------------|----------|--------------|
| 核心单位     | 状态     | 任务         |
| 时间         | 隐式     | 显式         |
| 生命周期     | 无       | 一等公民     |
| 持久化       | 无       | 强制         |
| 中断         | 手写     | 系统级       |
| 崩溃恢复     | 无       | 原生支持     |
| 多任务调度   | 困难     | 原生设计     |
| 系统一致性   | 无       | 核心能力     |

## 8.3 类比

FSM 像函数。RARK 像操作系统的进程管理器。

---

# 9. 真实人机交互场景分析

## 场景：家庭服务机器人的一天

机器人在家中执行任务。

**上午 9:00**，用户说：

> "帮我准备一杯咖啡。"

执行过程中发生：

1. 机器人去厨房
2. 正在烧水
3. 门铃响了
4. 用户说："先去开门"
5. 机器人去开门
6. 开门后回到厨房
7. 烧水过程中突然断电
8. 10 分钟后恢复供电
9. 机器人重新启动
10. 应该继续完成咖啡任务

这个场景解释了 RARK 的六个核心能力。

---

## 9.1 任务对象化

**没有任务对象化时：**

```
current_state = "boiling_water"
```

断电后，这个状态消失，没人知道发生过什么。

**有任务对象化时：**

```python
Task {
    id: 42,
    type: "MakeCoffee",
    business_state: "BoilingWater",
    lifecycle: "RUNNING",
    context: {...}
}
```

任务是一个实体，不是变量，不是函数调用——是数据库里的对象。

> 任务对象化 = 任务成为系统一级实体。

---

## 9.2 生命周期分离

**如果没有生命周期分离，状态机可能是：**

```
BoilingWater → PourWater → Done
```

无法表达任务被打断、暂停、恢复、失败。

**有生命周期分离，任务有两层状态：**

- 业务状态：`BoilingWater`
- 生命周期状态：`RUNNING` / `PAUSED` / `FAILED` / `RECOVERING`

当门铃响：

```
BoilingWater + INTERRUPT
→ lifecycle = PAUSED
```

业务状态不变，生命周期状态独立变化。

> 生命周期是系统级状态，业务状态是行为级状态。这是层级分离。

---

## 9.3 调度系统

门铃响了，系统现在有两个任务：

- MakeCoffee（priority=5）
- OpenDoor（priority=8）

```
priority(OpenDoor) > priority(MakeCoffee)
```

调度器决定：挂起咖啡 → 执行开门 → 完成后恢复咖啡。

> 调度系统 = 决定哪个任务现在拥有执行权。这不是状态机职责。

---

## 9.4 持久化系统

烧水时突然断电。没有持久化，所有内存状态消失。

有持久化，数据库里仍然存在：

```python
Task 42: type=MakeCoffee, business_state=BoilingWater, lifecycle=RUNNING
```

> 持久化系统 = 时间连续性的保障。

---

## 9.5 崩溃恢复

系统重启，RARK 启动时：

```python
unfinished_tasks = load_unfinished()
```

发现 Task 42 未完成，于是：

```
lifecycle: ACTIVE → PAUSED（崩溃视为被中断）
→ ACTIVE（重新调度）
```

继续执行烧水。

> 崩溃恢复 = 把"时间中断"接上。这不是状态机功能。

---

## 9.6 多任务支持

现实系统中同时存在：咖啡任务、开门任务、电量监控任务、安全监控任务、语音交互任务。

没有多任务支持，所有逻辑耦合，系统变成 spaghetti code。

有多任务支持，调度系统统一管理，各任务独立存在。

---

## 9.7 六个能力总结

| 能力         | 在咖啡场景中意味着什么             |
|--------------|------------------------------------|
| 任务对象化   | "做咖啡"是一个可存储实体           |
| 生命周期分离 | 可以暂停而不丢失业务状态           |
| 调度系统     | 决定开门优先                       |
| 持久化系统   | 断电后任务仍然存在                 |
| 崩溃恢复     | 重启后继续烧水                     |
| 多任务支持   | 咖啡 + 开门 + 电量监控并存         |

---

## 9.8 这和状态机到底有什么不同？

如果只用状态机，必须把所有逻辑写进一个巨大 FSM：

```
BoilingWaterWhileDoorbellWhileBatteryLowWhileInterrupted...
```

复杂度指数增长。

RARK 把挂起、恢复、调度、持久化、崩溃恢复抽象为系统层能力。

状态机只描述：`BoilingWater → PourWater`

而任务的生存、调度、恢复由内核负责。

> 状态机管理"行为转移"。RARK 管理"任务在时间中的存在"。

---

# 10. 系统调度能力

RARK 支持：

- 优先级调度（max-heap，数值越高越优先）
- 可抢占（高优先级任务插入 → 当前任务 PAUSED）
- 单活跃任务模型（uniprocessor，适合嵌入式机器人）

调度机制由 RARK 内核控制。LLM 仅参与策略建议，不拥有调度权。

---

# 11. 崩溃恢复能力

断电重启后：

- 加载所有未完成任务（PENDING/PAUSED）
- ACTIVE 任务降为 PAUSED（崩溃时视为被中断）
- 重新调度执行

没有这一层，机器人无法长期运行。

---

# 12. 为什么这不是"开倒车"

误解来源：

> 看起来像状态机。

但区别在于层级。FSM 是行为逻辑。RARK 是任务执行平台。LLM 是策略层。

三层分离，是现代系统设计原则。它不是替代模型，而是承载模型。

---

# 13. RARK 的战略意义

RARK 解决：

- 真实人机交互问题
- 长时间运行问题
- 系统调度问题
- 任务生存问题
- 产品落地稳定性问题

它是：

> 具身智能走向产品化的关键层。

---

# 14. 一句话总结

RARK 是：

> 具身智能的时间操作系统内核。

LLM 是大脑。ROS 是身体。RARK 是时间与执行权的管理系统。

它回答的问题不是：

> "机器人下一步做什么？"

而是：

> "机器人如何在真实世界中长期、稳定、可控地运行？"

---

# Appendix：设计级伪代码

> 展示 RARK 的整体结构：任务对象、生命周期、调度、事件循环、持久化、LLM 参与、Skill 调用、恢复机制。

```python
# ===============================
# Task Definition
# ===============================

class Task:

    def __init__(self, id, task_type, context, priority=0):
        self.id = id
        self.type = task_type
        self.lifecycle = "CREATED"   # 系统级生命周期状态
        self.state = None            # 业务状态
        self.context = context
        self.priority = priority


# ===============================
# Storage Layer (Persistence)
# ===============================

class Storage:

    def __init__(self):
        self.db = {}

    def save(self, task):
        self.db[task.id] = task

    def load_unfinished(self):
        return [t for t in self.db.values()
                if t.lifecycle not in ["COMPLETED"]]


# ===============================
# Event Bus
# ===============================

class EventBus:

    def __init__(self):
        self.queue = []

    def publish(self, event):
        self.queue.append(event)

    def wait_event(self):
        while not self.queue:
            pass
        return self.queue.pop(0)


# ===============================
# RARK Kernel
# ===============================

class RARK:

    def __init__(self):
        self.storage = Storage()
        self.event_bus = EventBus()
        self.tasks = {}
        self.ready_queue = []

    # ---- Task Management ----

    def create_task(self, task_type, context, priority=0):
        task = Task(generate_id(), task_type, context, priority)
        self.tasks[task.id] = task
        self.storage.save(task)
        self.event_bus.publish(Event("TASK_CREATED", task.id))
        return task.id

    # ---- Lifecycle Logic ----

    def lifecycle_transition(self, task, event):

        if task.lifecycle == "CREATED" and event.type == "TASK_CREATED":
            task.lifecycle = "READY"
            self.ready_queue.append(task)

        elif task.lifecycle == "RUNNING":

            if event.type == "INTERRUPT":
                task.lifecycle = "SUSPENDED"

            elif event.type == "COMPLETE":
                task.lifecycle = "COMPLETED"

            elif event.type == "FAIL":
                task.lifecycle = "FAILED"

        elif task.lifecycle == "SUSPENDED" and event.type == "RESUME":
            task.lifecycle = "READY"
            self.ready_queue.append(task)

        elif task.lifecycle == "FAILED" and event.type == "RECOVER":
            task.lifecycle = "READY"
            self.ready_queue.append(task)

    # ---- Scheduler ----

    def schedule(self):
        if not self.ready_queue:
            return None
        self.ready_queue.sort(key=lambda t: t.priority, reverse=True)
        task = self.ready_queue.pop(0)
        task.lifecycle = "RUNNING"
        return task

    # ---- Execution ----

    def execute(self, task):
        # If no business state, ask LLM to plan
        if task.state is None:
            plan = LLM.plan(task.context)
            task.context["plan"] = plan
            task.state = plan.initial_state

        action = task.context["plan"].next_action(task.state)
        result = SkillLayer.execute(action)

        if result.success:
            task.state = result.next_state
            if result.finished:
                self.event_bus.publish(Event("COMPLETE", task.id))
        else:
            self.event_bus.publish(Event("FAIL", task.id))

    # ---- Recovery ----

    def recover(self):
        for task in self.storage.load_unfinished():
            task.lifecycle = "READY"
            self.ready_queue.append(task)

    # ---- Main Loop ----

    def run(self):
        self.recover()

        while True:
            event = self.event_bus.wait_event()
            task = self.tasks.get(event.task_id)

            if task:
                self.lifecycle_transition(task, event)
                self.storage.save(task)

            task_to_run = self.schedule()
            if task_to_run:
                self.execute(task_to_run)
                self.storage.save(task_to_run)


# ===============================
# External Components（分层）
# ===============================

class LLM:
    """策略层：解析意图、规划步骤、建议优先级。
    只参与策略生成，不拥有调度权。"""

    @staticmethod
    def plan(context):
        return Plan(context)


class SkillLayer:
    """执行层：对应 RARK 的 SkillRunner。
    每个 Skill 是一个可注册的 async 协程。"""

    @staticmethod
    def execute(action):
        return Result(success=True, next_state="DONE", finished=True)
```

**这段伪代码体现了：**

| 能力         | 体现位置                      |
|--------------|-------------------------------|
| 任务对象化   | `Task` 类，有 id/lifecycle/context |
| 生命周期分离 | `lifecycle_transition()`      |
| 调度系统     | `schedule()` + priority sort  |
| 持久化       | `Storage.save()` 每次转换后调用 |
| 崩溃恢复     | `recover()` 在 `run()` 最开始  |
| 事件驱动     | `EventBus` + 主循环            |
| LLM 参与     | `LLM.plan()` 只在规划阶段调用  |
| Skill 调用   | `SkillLayer.execute()`        |
| 中断扩展点   | `lifecycle_transition()` 中的 `INTERRUPT` 分支 |
