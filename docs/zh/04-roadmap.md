> [English](../en/04-roadmap.md) | **中文**

# RARK Roadmap

> 基于当前实现状态和生态调研（`03-ecosystem.md`）整理的改进计划。
> 按优先级排序，每项均标注驱动原因和最小实现方案。

---

# 已完成

| 功能                         | 说明                                                              |
|------------------------------|-------------------------------------------------------------------|
| RARKKernel（生命周期内核）   | 优先级调度、事件驱动、SQLite 持久化、崩溃恢复                    |
| SkillRunner（技能执行层）    | `@runner.skill()` 装饰器、asyncio.Task 管理、自动 complete/fail  |
| HTTP API 层（`server.py`）   | `create_app()` 工厂、6 个 REST 端点、FastAPI lifespan 集成       |
| 公开导出（`__init__.py`）    | `SkillRunner`, `Task`, `Event`, `EventType`, `LifecycleState`     |
| 即时可查询（`register()`）   | submit/interrupt 后无需等待 run_loop 即可通过 API 查询任务状态   |
| Skill Resume 语义（1.1）     | metadata 中断持久化 + checkpoint 约定已验证；2 个专项测试        |
| crash_policy 可配置          | "resume"（默认）/ "fail"（安全模式），应对物理一致性要求差异     |
| SQLite WAL 模式              | `PRAGMA journal_mode=WAL`，崩溃时日志可回放                      |
| DB 跨实例恢复测试            | 验证不同 runner 实例从同一 DB 恢复时 metadata 完整              |
| 架构文档完善                 | 时间边界、at-least-once 语义、crash_policy、优先级反转分析       |
| 完整测试覆盖                 | 33 个测试，覆盖内核/技能/HTTP 三层                                |

---

# Phase 1：修复设计债务（高优先级）

## ✅ 1.1 Skill Resume 语义（已完成）

**问题**

当前 `_cancel_running_skill()` 是硬取消。任务从 PAUSED 恢复后，`_launch_skill()` 重新启动技能协程，即**从头重跑**。

这意味着：

- "倒水倒了 80%"被中断后，恢复时从 0% 重新开始
- 如果 skill 已向执行器发送了指令，重跑会发出重复指令
- 无法传递"上次执行到哪里了"的上下文

**方案**

不修改内核，通过约定规范解决。

`task.metadata` 已存在，skill 可在执行前读取进度、执行后写入进度：

```python
@runner.skill("pour_water")
async def pour_water(task: Task) -> None:
    progress = task.metadata.get("progress", 0.0)

    if progress < 0.5:
        await move_to_cup()
        task.metadata["progress"] = 0.5
        # （此处若被中断，metadata 已更新）

    if progress < 1.0:
        await pour()
        task.metadata["progress"] = 1.0
```

**内核侧需要做的**：确保 `task.metadata` 在 PAUSED 时持久化（当前已通过 `_store.upsert(task)` 写库）。唯一缺失是 `_on_interrupt` 里在 `suspend()` 之后没有立即 `upsert`——需要检查确认。

**验收标准**

- skill 可以通过 `task.metadata` 读写进度
- PAUSED → ACTIVE 恢复后，metadata 值与中断前一致
- 添加对应测试

**工作量估计**：小（主要是约定文档 + 一个测试）

**完成状态**：基础设施已验证无 bug（`_on_interrupt` 正确 upsert，`upsert` 更新 metadata 列，`load_all` 正确反序列化）。新增两个测试：`test_metadata_persisted_on_pause`（DB 持久化验证）和 `test_resume_reads_metadata_checkpoint`（端到端 `stages_seen == [0, 1]`）。Checkpoint 约定已写入 `docs/02-architecture.md` Section 3.5。

---

# Phase 2：结构能力补全（中优先级）

## ✅ 2.1 BLOCKED 状态 + 任务依赖（已完成）

**问题**

真实机器人任务经常有依赖关系：

```
navigate_to_kitchen
  → 完成后才能执行 → grasp_cup
      → 完成后才能执行 → pour_water
```

当前 RARK 不支持这种有向依赖，只能外部手动按序提交。

**方案**

扩展 `Task` 和 `Scheduler`：

```python
# Task 增加
blocked_by: Set[str] = field(default_factory=set)  # 依赖的 task_id 集合

# Scheduler.pick_next() 修改
# 跳过 blocked_by 非空且依赖未全部完成的任务

# _on_complete() 修改
# 完成时检查是否有其他任务在等待此 task_id，解除 BLOCKED
```

新增状态转换：`PENDING → BLOCKED → PENDING（依赖完成后自动解除）`

**注意**：BLOCKED 不是新的生命周期状态，而是调度器层面的"跳过"标记——不需要修改状态机，只需在 `pick_next()` 增加过滤条件。

**验收标准**

- `Task(name="grasp_cup", blocked_by={navigate_id})` 在 navigate 完成前不会被调度
- navigate 完成后 grasp_cup 自动进入调度队列
- 添加对应测试

**工作量估计**：中（~100 行代码 + 测试）

---

## ✅ 2.2 Skill 重试机制（已完成）

**问题**

当前 skill 抛出任何异常都直接进入 FAILED（终态），没有重试机会。对于瞬时故障（网络抖动、传感器噪声）这太激进了。

**方案**

在 `SkillRunner._run_skill()` 中增加 retry budget：

```python
# Task.metadata 约定
task.metadata.get("max_retries", 0)   # 允许重试次数
task.metadata.get("retry_count", 0)   # 已重试次数
task.metadata.get("retry_delay", 1.0) # 重试间隔（秒）
```

`_run_skill()` 捕获异常后，若 `retry_count < max_retries`，自增 retry_count 并重新 emit TASK_SUBMIT（而非 TASK_FAIL），让任务重新进入队列。

**验收标准**

- `metadata={"max_retries": 3}` 的任务在前 3 次失败后自动重试
- 第 4 次失败才 → FAILED
- 添加对应测试

**工作量估计**：小（~30 行代码 + 测试）

---

# Phase 3：可观测性（低优先级，但影响生产可用性）

## ✅ 3.1 结构化日志（已完成）

**问题**

当前全部使用 `print()`，无法：

- 按级别过滤
- 接入日志聚合系统
- 在生产环境关闭调试输出

**方案**

将 `kernel.py` 和 `runner.py` 中所有 `print(f"[RARK] ...")` 替换为 `logging.getLogger("rark")`：

```python
import logging
logger = logging.getLogger("rark")

# 替换示例
logger.info("submitted → %s (priority=%d)", task.name, task.priority)
logger.warning("failed    → %s: %s", task.name, error)
```

调用方可通过标准 `logging.basicConfig()` 控制输出。

**为 OpenTelemetry 预留接入点**（可选，不强制）：在 `_on_complete`、`_on_fail` 等 handler 里预留 hook 接口，后续可注入 span。

**验收标准**

- 所有 print 替换为 logging
- 测试中日志不干扰输出（pytest 默认抑制 logging）
- 不引入任何新依赖

**工作量估计**：小（纯机械替换）

---

# Phase 4：API 与安全补全（高优先级）

## 4.1 HTTP API 暴露 `blocked_by` 和 `retry`

**问题**

内核已支持任务依赖（`blocked_by`）和重试（`max_retries`、`retry_delay`），但这些字段未在 HTTP `POST /tasks` 请求体中暴露。用户必须使用 Python API 才能使用这些功能。

**方案**

扩展 `server.py` 中的 `SubmitRequest`：

```python
class SubmitRequest(BaseModel):
    name: str
    priority: int = 5
    metadata: dict = {}
    blocked_by: list[str] = []          # 依赖的 task ID
    max_retries: int = 0                # 注入到 metadata
    retry_delay: float = 1.0            # 注入到 metadata
```

将 `blocked_by` 映射到 `Task(blocked_by=set(...))`，将重试字段注入 `metadata`。

**验收标准**

- `POST /tasks` 接受 `blocked_by`、`max_retries`、`retry_delay`
- 不带这些字段的现有请求继续正常工作（向后兼容）
- 添加对应测试

**工作量**：小

---

## 4.2 WebSocket 事件流

**问题**

监控任务状态变化需要轮询 `GET /tasks`。对于实时机器人调试来说这不够——操作者需要在任务转换时获得即时反馈。

**方案**

添加 WebSocket 端点：

```python
@app.websocket("/ws/events")
async def event_stream(ws: WebSocket):
    await ws.accept()
    while True:
        event = await event_queue.get()
        await ws.send_json(event.to_dict())
```

内核向广播通道发送事件；WebSocket 客户端订阅。

**验收标准**

- `ws://localhost:8000/ws/events` 在每次状态转换时推送 JSON
- 多个客户端可以同时连接
- 断开连接的客户端不会阻塞内核
- 添加对应测试

**工作量**：中

---

## 4.3 时限任务（Deadline）

**问题**

卡住的任务（传感器挂起、网络超时）会无限期阻塞调度器。在物理机器人中这很危险——一个应该花 5 秒但跑了 60 秒的任务意味着出了问题。

**方案**

为 Task 添加可选的 `deadline` 字段：

```python
@dataclass
class Task:
    ...
    deadline: Optional[float] = None  # 从 ACTIVE 开始计算的秒数
```

调度器在每次 tick 时检查已用时间；如超出限制，发出 `TASK_FAIL`，`error="deadline_exceeded"`。

**验收标准**

- `Task(name="read_sensor", deadline=5.0)` 在 ACTIVE 状态超过 5 秒后自动失败
- 没有 deadline 的任务行为不变
- Deadline 在 PAUSED 状态暂停计时（PAUSED 时时钟暂停，ACTIVE 时恢复）
- 添加对应测试

**工作量**：中

---

# Phase 5：执行隔离（中优先级）

> 灵感来自 NanoClaw 的容器隔离架构——每个 agent 在独立的 Linux 容器中运行，拥有独立的文件系统隔离，防止单个 agent 崩溃影响其他 agent。

## 5.1 子进程 Skill 隔离

**问题**

当前所有 skill 在内核的 asyncio 事件循环中执行。一个异常的 skill（C 扩展 segfault、死循环、内存泄漏）可能导致整个内核进程崩溃。

**方案**

添加可选的 `IsolatedRunner`，在子进程中执行 skill：

```python
runner = SkillRunner(db_path="robot.db", isolation="subprocess")

# Skill 注册方式不变
@runner.skill("navigate_to")
async def navigate_to(task: Task) -> None:
    ...
```

实现方式：
- Skill 函数序列化后通过 `multiprocessing` 发送给子进程
- 子进程运行自己的 asyncio 循环
- 父进程监控子进程：子进程崩溃时，任务转为 FAILED 或 RETRY
- IPC 通过管道（非网络），在嵌入式环境中更可靠
- `task.metadata` 在 checkpoint 写入时同步回父进程

**关键设计约束**：这是 opt-in 的。默认仍为进程内执行，内核 API 不变。

**验收标准**

- `isolation="subprocess"` 在子进程中运行 skill
- 子进程崩溃 → 任务 FAILED（内核不崩溃）
- Metadata checkpoint 跨进程边界正常工作
- 进程内模式（`isolation=None`）保持不变
- 添加对应测试

**工作量**：大

---

## 5.2 资源域（Resource Domains）

**问题**

真实机器人有多个独立子系统：移动底盘、机械臂、传感器。当前的单活跃任务模型意味着机械臂必须等导航完成——即使它们使用完全独立的硬件。

> 灵感来自 NanoClaw 的 per-group 队列模型——每个 group 有自己的消息队列和并发控制，同时有跨 group 的全局并发上限。

**方案**

引入 `ResourceDomain`——每个域有自己的单活跃任务约束，但域之间并行运行：

```python
runner = SkillRunner(db_path="robot.db")

@runner.skill("navigate_to", domain="base")
async def navigate_to(task: Task) -> None: ...

@runner.skill("grasp_cup", domain="arm")
async def grasp_cup(task: Task) -> None: ...

# 这两个可以并发执行——不同的域
await runner.submit(Task(name="navigate_to", priority=5))
await runner.submit(Task(name="grasp_cup", priority=5))
```

实现方式：
- 每个域有自己的调度器（优先级堆）
- 内核管理多个活跃任务（每个域一个）
- 中断只在同一域内抢占
- 跨域依赖通过现有的 `blocked_by` 实现
- 可选的全局并发上限（如最多 3 个域同时活跃）

**关键设计约束**：这不违反"单活跃任务"原则——而是将其细化为"**每个硬件资源**单活跃任务"。未显式指定域的 skill 默认归入 `"default"` 域，完全向后兼容。

**验收标准**

- 不同域的 skill 可以并发运行
- 同一域内的 skill 遵循单活跃任务调度
- 中断只影响被中断的域
- `blocked_by` 跨域工作
- 默认域保持现有单活跃行为
- 添加对应测试

**工作量**：大

---

# Phase 6：生态集成（低优先级）

## 6.1 任务组（Task Groups）

**问题**

将多步计划（导航 → 抓取 → 倒水）作为独立任务提交，意味着部分失败会让系统处于不一致状态。如果 `grasp` 失败，`pour` 仍在 pending。

**方案**

添加 `TaskGroup` 概念：

```python
group = TaskGroup(
    tasks=[nav, grasp, pour],
    policy="cancel_on_failure",  # 或 "continue_on_failure"
)
await runner.submit_group(group)
```

组内任何任务失败时，取消所有剩余任务。

**验收标准**

- `submit_group()` 原子提交一组任务
- `cancel_on_failure` 策略在一个任务失败时取消组内所有 pending/paused 任务
- 单个任务的生命周期不变
- 添加对应测试

**工作量**：中

---

## 6.2 ROS 2 Skill 适配器模板

**问题**

ROS 2 是主流的机器人中间件。将 ROS 2 action client 包装为 RARK skill 需要每个用户都会重复的样板代码。

**方案**

在 `rark/adapters/ros2.py` 中提供可复用的适配器：

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

适配器处理：目标提交、feedback → metadata 同步、抢占 → action 取消、结果 → 任务完成。

**验收标准**

- 适配器包装 ROS 2 action client 生命周期
- 抢占触发 `cancel_goal()`
- Feedback 同步到 `task.metadata` 用于 checkpoint
- `examples/` 中有示例
- 使用 mock action server 的测试

**工作量**：中（需要 `rclpy` 可选依赖）

---

## 6.3 OpenTelemetry Span 注入

**问题**

结构化日志（Phase 3.1）提供文本级可观测性。对于生产部署，分布式追踪的 span 上下文可以实现时间线可视化、延迟分析和跨服务关联。

**方案**

在状态转换点添加可选的 OpenTelemetry 钩子：

```python
# 通过可选依赖启用
pip install rark[telemetry]

runner = SkillRunner(db_path="robot.db", telemetry=True)
```

每个任务生命周期获得一个 trace；每次状态转换创建一个 span。未启用时零性能开销。

**验收标准**

- 状态转换发出 OpenTelemetry span
- 默认关闭（关闭时零开销）
- `rark[telemetry]` 可选依赖组
- 使用 mock exporter 验证 span 创建的测试

**工作量**：中

---

# 优先级总结

| 编号 | 改进点              | 优先级 | 工作量 | 状态   |
|------|---------------------|--------|--------|--------|
| 1.1  | Skill resume 语义   | 高     | 小     | 已完成 |
| 2.1  | 任务依赖（BLOCKED） | 中     | 中     | 已完成 |
| 2.2  | Skill 重试          | 中     | 小     | 已完成 |
| 3.1  | 结构化日志          | 低     | 小     | 已完成 |
| 4.1  | HTTP API 补全       | 高     | 小     | 计划中 |
| 4.2  | WebSocket 事件流    | 高     | 中     | 计划中 |
| 4.3  | 时限任务            | 高     | 中     | 计划中 |
| 5.1  | 子进程隔离          | 中     | 大     | 计划中 |
| 5.2  | 资源域              | 中     | 大     | 计划中 |
| 6.1  | 任务组              | 低     | 中     | 计划中 |
| 6.2  | ROS 2 适配器        | 低     | 中     | 计划中 |
| 6.3  | OpenTelemetry span  | 低     | 中     | 计划中 |

---

# 刻意不做的事

以下是经过生态调研后**主动决定不做**的方向，避免过度工程化：

| 不做                     | 原因                                              |
|--------------------------|---------------------------------------------------|
| LLM 推理集成             | RARK 定位为 LLM-agnostic 调度内核，层次不该混     |
| 分布式多节点运行时       | 机器人嵌入式单节点场景，无需此复杂度              |
| 无约束并行执行           | 无硬件资源边界的任意多任务并行增加调度复杂度，无明确收益。资源域（5.2）在显式硬件边界内提供结构化并发 |
| MCP 工具协议             | 属于 SkillRunner 之上的上层集成，不是内核责任     |
| 动态优先级调整           | 优先级语义应该在提交时确定，运行时修改引入歧义    |
