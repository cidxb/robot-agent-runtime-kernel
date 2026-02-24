# RARK 当前实现架构

> 本文描述当前代码库的实际实现，是 `01-philosophy.md` 愿景的当前落地状态。

---

# 1. 整体结构

```
rark/
├── __init__.py          # 公开 API 导出
├── server.py            # FastAPI HTTP 层（create_app 工厂）
├── core/
│   ├── task.py          # Task 数据类
│   ├── transitions.py   # 状态转换规则
│   ├── events.py        # 事件类型定义
│   ├── scheduler.py     # 优先级调度器
│   ├── kernel.py        # RARKKernel（生命周期内核）
│   └── runner.py        # SkillRunner（技能执行层）
├── persistence/
│   └── sqlite_store.py  # SQLite 持久化
├── tests/
│   ├── test_task.py
│   ├── test_kernel.py
│   ├── test_runner.py
│   └── test_server.py   # HTTP 层端到端测试
└── examples/
    ├── interrupt_demo.py
    └── server_demo.py   # 完整可运行 HTTP 服务示例
```

---

# 2. 任务生命周期状态机

## 2.1 状态定义

| 状态        | 含义                                   |
|-------------|----------------------------------------|
| `PENDING`   | 已提交，在调度队列中等待               |
| `ACTIVE`    | 当前正在执行（同时只有一个）           |
| `PAUSED`    | 被中断挂起，等待恢复                   |
| `COMPLETED` | 成功完成（终态）                       |
| `FAILED`    | 执行失败（终态）                       |
| `CANCELLED` | 被主动取消（终态）                     |

## 2.2 合法转换矩阵

```
PENDING   → ACTIVE
PENDING   → CANCELLED
ACTIVE    → PAUSED
ACTIVE    → COMPLETED
ACTIVE    → FAILED
ACTIVE    → CANCELLED
PAUSED    → ACTIVE      （resume）
PAUSED    → CANCELLED
```

终态（COMPLETED / FAILED / CANCELLED）无法再转换。

## 2.3 与 Philosophy 的对比

`01-philosophy.md` 描述了更完整的生命周期愿景（含 CREATED / READY / WAITING / RECOVERING）。当前实现是简化版本，覆盖核心场景。未来扩展见 `04-roadmap.md`。

---

# 3. 核心组件

## 3.1 Task

```python
@dataclass
class Task:
    name: str
    priority: int          # 数值越大越紧急（interrupt 通常用 10，普通任务用 3）
    id: str                # UUID，自动生成
    state: LifecycleState  # 当前生命周期状态
    created_at: datetime
    updated_at: datetime
    metadata: Dict[str, Any]  # 任意附加数据，可跨重启传递
```

**关键设计**：`metadata` 字段可用于传递技能的执行进度上下文，让技能在 resume 时知道"我上次执行到哪里了"。

---

## 3.2 Scheduler

**数据结构**：max-heap（Python `heapq`，存 `-priority`）

```
register(task) → 仅加入 _tasks 字典，不入堆（用于 submit/interrupt 的即时可查询）
add(task)      → 入堆 + 加入 _tasks 字典
pick_next()    → 弹出最高优先级的 PENDING/PAUSED 任务
suspend(id)    → 将 ACTIVE 任务转为 PAUSED
get(id)        → 按 id 查找任务
```

**惰性删除**：heap 中可能存在已完成任务的旧条目，`pick_next()` 通过状态检查跳过。

**register() 的作用**：`SkillRunner.submit()` / `interrupt()` 在 emit 事件之前先调用 `register()`，使任务在 `run_loop()` 处理事件之前就已可通过 `get_task()` / `list_tasks()` 查询到。这也使得 `httpx.ASGITransport` 测试环境下无需等待 lifespan 启动即可查询任务。

---

## 3.3 事件系统

```
EventType:
  TASK_SUBMIT    → payload: {"task": Task}
  TASK_COMPLETE  → task_id
  TASK_FAIL      → task_id, payload: {"error": str}
  TASK_CANCEL    → task_id
  INTERRUPT      → payload: {"task": Task}  # 高优先级任务
```

**事件队列**：`asyncio.Queue`，非阻塞入队，带 0.1s timeout 的 `asyncio.wait_for` 消费。

---

## 3.4 RARKKernel

纯生命周期内核，不含技能执行逻辑。

```
核心循环：
  run_loop()
    ├─ 有事件 → _dispatch() → 对应 handler
    └─ idle（0.1s timeout）→ _tick() → 晋升下一个任务

公开查询方法：
  get_task(task_id)  → 按 id 查找任务（返回 Task 或 None）
  list_tasks()       → 返回全部已知任务列表

事件 handlers：
  _on_submit()    → scheduler.add() + persist
  _on_complete()  → COMPLETED + persist + release _active_task
  _on_fail()      → FAILED + persist + release _active_task
  _on_cancel()    → CANCELLED + persist + release _active_task
  _on_interrupt() → suspend 当前任务 + add 中断任务

崩溃恢复：
  _recover()
    ├─ PENDING/PAUSED → 重新入队
    └─ ACTIVE         → 降为 PAUSED（视为被中断）
```

---

## 3.5 SkillRunner（继承 RARKKernel）

技能执行层，让用户注册 `async def` 后内核自动驱动。

```python
@runner.skill("pour_water")
async def pour_water(task: Task) -> None:
    ...  # 技能逻辑，正常返回 → TASK_COMPLETE，抛异常 → TASK_FAIL
```

**执行流程**：

```
_tick() 检测到新 ACTIVE 任务
  → _launch_skill(task)
      ├─ 未注册 → 立即 emit TASK_FAIL
      └─ 已注册 → asyncio.create_task(_run_skill(task, fn))
                   ├─ fn() 正常返回 → emit TASK_COMPLETE
                   ├─ fn() 抛 CancelledError → 重抛（不发 TASK_FAIL）
                   └─ fn() 抛其他异常 → emit TASK_FAIL

_on_interrupt() override
  → _cancel_running_skill()   # cancel asyncio.Task + await 等待清理
  → super()._on_interrupt()   # 标准 PAUSED 流程

_on_cancel() override
  → 若取消的是当前 ACTIVE 任务 → _cancel_running_skill()
  → super()._on_cancel()
```

**关键设计**：`_tick()` 用 `is not`（对象身份）判断是否有新任务晋升，避免重复启动同一任务。

### Skill Resume 约定（Checkpoint Pattern）

`task.metadata` 是 skill 与内核之间的状态传递通道。中断后内核自动持久化 metadata，resume 时传入同一对象，skill 可读取上次断点：

```python
@runner.skill("pour_water")
async def pour_water(task: Task) -> None:
    stage = task.metadata.get("stage", 0)

    if stage < 1:
        task.metadata["stage"] = 1   # 同步写入，不经过 await，cancel 不会丢失
        await move_to_position()     # ← 可能在此处被 cancel

    if stage < 2:
        task.metadata["stage"] = 2
        await pour()

    # 全部完成，正常返回 → TASK_COMPLETE
```

**为什么可靠**：
- `task.metadata["stage"] = 1` 是同步 dict 写入，在 `await` 之前完成，cancel 发生在 `await` 处时断点已记录
- 中断时 `_on_interrupt` 调用 `_store.upsert(task)` 将 metadata 写入 SQLite
- Resume 时传入的是同一 Python 对象（内存中），崩溃恢复时从 SQLite 加载

---

## 3.6 持久化（SQLiteStore）

- 每次状态转换后调用 `upsert(task)` 写库
- `load_all()` 在启动时加载全部任务用于崩溃恢复
- 支持 `:memory:` 用于测试

---

## 3.7 公开导出（`__init__.py`）

```python
from rark import SkillRunner, Task, Event, EventType, LifecycleState
```

顶层包导出供调用方使用，无需了解内部模块路径。

---

# 4. HTTP Server 层（`server.py`）

## 4.1 设计原则

HTTP 层是薄包装，不含任何业务逻辑：

- `create_app(runner: SkillRunner) -> FastAPI` — 工厂函数，接受已注册好技能的 SkillRunner
- lifespan 负责启动 `runner.start()` 和 `asyncio.create_task(runner.run_loop())`
- 路由层只做参数提取 → 调用 runner → 序列化响应

## 4.2 路由一览

| 方法     | 路径               | 说明                           |
|----------|--------------------|--------------------------------|
| `GET`    | `/health`          | 内核状态 + 当前活跃任务        |
| `GET`    | `/tasks`           | 所有已知任务列表               |
| `POST`   | `/tasks`           | 提交新任务（返回 201）         |
| `GET`    | `/tasks/{id}`      | 按 ID 查询任务（404 if missing）|
| `DELETE` | `/tasks/{id}`      | 取消任务（emit TASK_CANCEL）   |
| `POST`   | `/interrupt`       | 高优先级中断（emit INTERRUPT） |

## 4.3 请求/响应模型

```python
# 提交任务
POST /tasks
{"name": "pour_water", "priority": 5, "metadata": {"target": "kitchen"}}

# 响应
{"id": "...", "name": "pour_water", "state": "pending", "priority": 5, "metadata": {...}}

# 中断
POST /interrupt
{"name": "avoid_obstacle", "priority": 10}
```

## 4.4 lifespan 与 run_loop 关系

```
FastAPI lifespan start
  → runner.start()          # 打开 SQLite + 崩溃恢复
  → asyncio.create_task(runner.run_loop())  # 后台事件循环

HTTP 请求处理（在 lifespan 生存期内）
  → runner.submit(task)     # pre-register + emit TASK_SUBMIT
  → run_loop 异步消费事件，驱动技能执行

FastAPI lifespan shutdown
  → runner.stop()           # 关闭 SQLite
  → loop_task.cancel()      # 停止事件循环
```

## 4.5 测试注意事项

`httpx.ASGITransport` **不触发 FastAPI lifespan**，因此 `run_loop()` 在测试中不运行。

解决方案：`SkillRunner.submit()` 和 `interrupt()` 在 emit 事件前先调用 `scheduler.register(task)`，使任务立即可查询，无需等待事件被 `run_loop` 处理。这是架构设计，不是测试 workaround。

---

# 5. 关键时序图

## 5.1 普通任务完成

```
submit(task)
  → emit(TASK_SUBMIT)
  → [drain] _on_submit → scheduler.add()
  → [tick]  task → ACTIVE + _launch_skill()
  → [sleep(0)] skill 运行 → emit(TASK_COMPLETE)
  → [drain] _on_complete → task → COMPLETED
```

## 5.2 中断场景

```
task_A ACTIVE + skill 运行中
  → emit(INTERRUPT, task_B)
  → [drain] _on_interrupt
      → _cancel_running_skill()  # task_A 的 skill 被 cancel
      → scheduler.suspend(task_A)  # task_A → PAUSED
      → scheduler.add(task_B)
  → [tick]  task_B → ACTIVE + _launch_skill()
  → task_B skill 完成 → COMPLETED
  → [tick]  task_A → ACTIVE（resume）+ _launch_skill()  # 从头重跑 skill
  → task_A skill 完成 → COMPLETED
```

**注意**：resume 后技能从头重跑，进度上下文需通过 `task.metadata` 手动传递。

---

# 6. 时间尺度边界

RARK 工作在**任务层**，不是控制层。两个层次有截然不同的时间尺度：

```
控制层  ~1ms   硬实时   关节位置 / 力矩 / 传感器读取     ← ROS2 / firmware
任务层  ~1s    软实时   任务调度 / 状态转移 / skill 调用  ← RARK
```

RARK 的 `run_loop()` idle 间隔为 0.1s，这本身就表明它不在控制回路。RARK 的职责是：**在秒级时间尺度上决定现在执行哪个任务**，具体的电机指令由 skill 内部调用底层控制接口完成。

**RARK 不替代、也不干预实时控制层。**

---

# 7. 执行语义与崩溃安全性

## 7.1 At-Least-Once 语义

RARK 提供 **at-least-once** 执行语义。事件处理的关键路径：

```
task.transition(NEW_STATE)   # ① 内存先变
await store.upsert(task)     # ② 然后持久化（SQLite ACID 事务）
```

如果进程在 ① 之后、② commit 之前崩溃，重启后 DB 仍是旧状态，任务会被重新调度执行。

**因此 skill 应尽量实现幂等**，或通过 `task.metadata` checkpoint 跳过已完成的阶段（见 Section 3.5）。

SQLite 使用 **WAL 模式**（`PRAGMA journal_mode=WAL`），崩溃时日志可回放，减少 ② 阶段的数据损坏风险。

## 7.2 崩溃恢复策略（crash_policy）

重启时，DB 中仍为 ACTIVE 的任务（上次运行时未来得及转换状态）按以下策略处理：

| policy | 行为 | 适用场景 |
|--------|------|---------|
| `"resume"`（默认）| ACTIVE → PAUSED，重新入调度队列 | skill 实现了 metadata checkpoint；幂等操作 |
| `"fail"` | ACTIVE → FAILED，不重新调度 | 物理状态一致性要求严格；skill 不能安全重跑 |

```python
# 默认：resume（依赖 skill 的 checkpoint 处理）
runner = SkillRunner(db_path="robot.db")

# 安全模式：fail（需手动重提交，配合物理状态检验）
runner = SkillRunner(db_path="robot.db", crash_policy="fail")
```

## 7.3 物理一致性是 Skill 的责任

软件可以 checkpoint-resume，物理世界不能回滚。RARK 提供机制（metadata 持久化、crash_policy），但无法替 skill 做物理判断：

- 崩溃时机器人关节停在哪里？
- 手中是否有物体？
- 传感器读数是否有效？

Skill 作者应在 resume 入口读取 `task.metadata["checkpoint"]`，结合物理传感器数据，决定从哪个阶段继续或是否需要先执行安全回位。

---

# 8. 优先级调度与优先级反转

## 8.1 调度算法

RARK 使用**固定优先级 + 最大堆（max-heap）**：

```python
heapq.heappush(self._heap, (-task.priority, task.id))  # 取反模拟 max-heap
```

- 调度时间复杂度：O(log n)
- 入队后优先级不可变（设计决策：优先级语义应在提交时确定）
- 同优先级任务按 task_id 字典序（UUID，近似 FIFO）

## 8.2 优先级反转为什么不适用

经典优先级反转需要：低优先级任务持有锁 → 高优先级任务等锁 → 中优先级任务抢占低优先级任务 → 高优先级任务被间接阻塞。

RARK 的 single-active-task 模型中：
- **没有任务间的资源锁**（skill 不持有跨任务的共享锁）
- 高优先级中断通过 `INTERRUPT` 事件**硬取消**当前 skill（`asyncio.Task.cancel()`），而不是等待其释放锁

因此 RARK 不存在经典意义上的优先级反转问题。

---

# 9. 测试覆盖

| 测试文件          | 测试数 | 覆盖场景                                      |
|-------------------|--------|-----------------------------------------------|
| `test_task.py`    | 11     | 状态转换合法性、非法转换拒绝、时间戳更新      |
| `test_kernel.py`  | 7      | 中断/恢复流程、优先级排序、崩溃恢复（resume/fail）、取消 |
| `test_runner.py`  | 8      | 技能自动完成/失败、中断取消、DB 跨实例恢复、metadata checkpoint |
| `test_server.py`  | 9      | health、submit、list、get、get_404、cancel、cancel_404、interrupt、metadata |

---

# 10. 已知设计约束

| 约束                   | 影响                                           |
|------------------------|------------------------------------------------|
| 单活跃任务             | 同时只有一个任务在 ACTIVE，适合嵌入式单体机器人 |
| Skill 从头重跑         | resume 后不恢复协程状态，需 skill 自己处理进度 |
| 无任务依赖图           | 不支持"任务 B 等待任务 A 完成后才能执行"      |
| print() 日志           | 无结构化日志，无可观测性接入点                 |
| 优先级提交后不可变     | 任务入队后优先级不能动态调整                   |
