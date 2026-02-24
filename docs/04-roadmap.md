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

# 优先级总结

| 编号 | 改进点           | 优先级   | 工作量 |
|------|------------------|----------|--------|
| 1.1  | Skill resume 语义 | ✅ 已完成 | 小    |
| 2.1  | 任务依赖（BLOCKED）| ✅ 已完成 | 中   |
| 2.2  | Skill 重试        | ✅ 已完成 | 小   |
| 3.1  | 结构化日志        | ✅ 已完成 | 小   |

---

# 刻意不做的事

以下是经过生态调研后**主动决定不做**的方向，避免过度工程化：

| 不做                     | 原因                                              |
|--------------------------|---------------------------------------------------|
| LLM 推理集成             | RARK 定位为 LLM-agnostic 调度内核，层次不该混     |
| 分布式多节点运行时       | 机器人嵌入式单节点场景，无需此复杂度              |
| 多任务并行执行           | 单活跃任务是有意约束，简化调度和硬件资源管理      |
| MCP 工具协议             | 属于 SkillRunner 之上的上层集成，不是内核责任     |
| 动态优先级调整           | 优先级语义应该在提交时确定，运行时修改引入歧义    |
