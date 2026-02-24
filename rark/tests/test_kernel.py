import pytest

from rark.core.events import Event, EventType
from rark.core.kernel import RARKKernel
from rark.core.task import Task
from rark.core.transitions import LifecycleState


@pytest.fixture
def temp_db(tmp_path):
    return str(tmp_path / "test.db")


async def _drain(kernel: RARKKernel) -> None:
    """Process one event from the queue."""
    event = await kernel._queue.get()
    await kernel._dispatch(event)


async def test_pour_water_interrupted(temp_db):
    """
    倒水被打断场景：
      1. pour_water (priority=3) 提交并激活
      2. 高优先级中断 avoid_obstacle (priority=10) 到来
      3. pour_water 被暂停，avoid_obstacle 激活
      4. avoid_obstacle 完成后，pour_water 自动恢复
    """
    kernel = RARKKernel(db_path=temp_db)
    await kernel.start()

    pour_water = Task(name="pour_water", priority=3)

    # 提交 pour_water
    await kernel.emit(Event(type=EventType.TASK_SUBMIT, payload={"task": pour_water}))
    await _drain(kernel)

    # tick → pour_water 激活
    await kernel._tick()
    assert kernel._active_task is not None
    assert kernel._active_task.name == "pour_water"
    assert pour_water.state == LifecycleState.ACTIVE

    # 注入高优先级中断
    avoid_obstacle = Task(name="avoid_obstacle", priority=10)
    await kernel.emit(Event(type=EventType.INTERRUPT, payload={"task": avoid_obstacle}))
    await _drain(kernel)

    # pour_water 应当被暂停
    assert pour_water.state == LifecycleState.PAUSED
    assert kernel._active_task is None

    # tick → avoid_obstacle 激活
    await kernel._tick()
    assert kernel._active_task is not None
    assert kernel._active_task.name == "avoid_obstacle"
    assert avoid_obstacle.state == LifecycleState.ACTIVE

    # avoid_obstacle 完成
    await kernel.emit(Event(type=EventType.TASK_COMPLETE, task_id=avoid_obstacle.id))
    await _drain(kernel)
    assert avoid_obstacle.state == LifecycleState.COMPLETED
    assert kernel._active_task is None

    # tick → pour_water 恢复
    await kernel._tick()
    assert kernel._active_task is not None
    assert kernel._active_task.name == "pour_water"
    assert pour_water.state == LifecycleState.ACTIVE

    await kernel.stop()


async def test_priority_ordering(temp_db):
    """高优先级任务应先于低优先级任务被调度。"""
    kernel = RARKKernel(db_path=temp_db)
    await kernel.start()

    low = Task(name="low_priority", priority=1)
    high = Task(name="high_priority", priority=9)

    # 先提交低优先级
    await kernel.emit(Event(type=EventType.TASK_SUBMIT, payload={"task": low}))
    await _drain(kernel)

    # 再提交高优先级
    await kernel.emit(Event(type=EventType.TASK_SUBMIT, payload={"task": high}))
    await _drain(kernel)

    # tick → 应当调度 high
    await kernel._tick()
    assert kernel._active_task is not None
    assert kernel._active_task.name == "high_priority"

    await kernel.stop()


async def test_crash_recovery(temp_db):
    """ACTIVE 任务在 kernel 崩溃后应恢复为 PAUSED，并在下次启动时重新调度。"""
    # 第一次启动：激活任务后模拟崩溃（只关闭存储，不更新状态）
    kernel = RARKKernel(db_path=temp_db)
    await kernel.start()

    task = Task(name="fragile_task", priority=5)
    await kernel.emit(Event(type=EventType.TASK_SUBMIT, payload={"task": task}))
    await _drain(kernel)
    await kernel._tick()
    assert task.state == LifecycleState.ACTIVE

    # 模拟崩溃：直接关闭，不走正常完成流程
    await kernel._store.close()

    # 第二次启动：recovery 应把 ACTIVE 改为 PAUSED 并重新入队
    kernel2 = RARKKernel(db_path=temp_db)
    await kernel2.start()

    await kernel2._tick()
    assert kernel2._active_task is not None
    assert kernel2._active_task.name == "fragile_task"

    await kernel2.stop()


async def test_task_cancellation(temp_db):
    """任务可以从 PENDING 状态被取消。"""
    kernel = RARKKernel(db_path=temp_db)
    await kernel.start()

    task = Task(name="cancellable", priority=3)
    await kernel.emit(Event(type=EventType.TASK_SUBMIT, payload={"task": task}))
    await _drain(kernel)

    await kernel.emit(Event(type=EventType.TASK_CANCEL, task_id=task.id))
    await _drain(kernel)

    assert task.state == LifecycleState.CANCELLED
    assert kernel._active_task is None

    await kernel.stop()


# ── Phase 2.1: 任务依赖（BLOCKED）─────────────────────────────────────────


async def test_blocked_task_waits_for_dependency(temp_db):
    """blocked_by 非空时任务不被调度，依赖完成后自动解锁。"""
    kernel = RARKKernel(db_path=temp_db)
    await kernel.start()

    task_a = Task(name="navigate", priority=5)
    task_b = Task(name="grasp", priority=5, blocked_by={task_a.id})

    await kernel.emit(Event(type=EventType.TASK_SUBMIT, payload={"task": task_a}))
    await _drain(kernel)
    await kernel.emit(Event(type=EventType.TASK_SUBMIT, payload={"task": task_b}))
    await _drain(kernel)

    # task_b 被阻塞：tick 只能调度 task_a
    await kernel._tick()
    assert kernel._active_task.name == "navigate"

    # task_a 完成 → release_dependents 解锁 task_b
    await kernel.emit(Event(type=EventType.TASK_COMPLETE, task_id=task_a.id))
    await _drain(kernel)
    assert task_a.state == LifecycleState.COMPLETED
    assert len(task_b.blocked_by) == 0  # 已解锁

    # 下一次 tick：task_b 进入调度
    await kernel._tick()
    assert kernel._active_task.name == "grasp"

    await kernel.stop()


async def test_high_priority_blocked_yields_to_lower_priority_ready(temp_db):
    """高优先级任务被阻塞时，低优先级但就绪的任务优先运行。"""
    kernel = RARKKernel(db_path=temp_db)
    await kernel.start()

    task_gate = Task(name="gate", priority=3)
    task_high = Task(name="high_blocked", priority=9, blocked_by={task_gate.id})
    task_low = Task(name="low_ready", priority=1)

    for t in (task_gate, task_high, task_low):
        await kernel.emit(Event(type=EventType.TASK_SUBMIT, payload={"task": t}))
        await _drain(kernel)

    # high_blocked 优先级最高，但被阻塞；gate 和 low_ready 都就绪
    # 应该先调度 gate（priority=3 > low_ready=1）
    await kernel._tick()
    assert kernel._active_task.name == "gate"

    await kernel.stop()


async def test_chained_dependencies(temp_db):
    """链式依赖：A → B → C，依次解锁并按序执行。"""
    kernel = RARKKernel(db_path=temp_db)
    await kernel.start()

    task_a = Task(name="step_a", priority=5)
    task_b = Task(name="step_b", priority=5, blocked_by={task_a.id})
    task_c = Task(name="step_c", priority=5, blocked_by={task_b.id})

    for t in (task_a, task_b, task_c):
        await kernel.emit(Event(type=EventType.TASK_SUBMIT, payload={"task": t}))
        await _drain(kernel)

    # 只有 A 就绪
    await kernel._tick()
    assert kernel._active_task.name == "step_a"

    await kernel.emit(Event(type=EventType.TASK_COMPLETE, task_id=task_a.id))
    await _drain(kernel)
    assert len(task_b.blocked_by) == 0  # B 解锁
    assert len(task_c.blocked_by) == 1  # C 仍等 B

    # B 就绪，C 仍阻塞
    await kernel._tick()
    assert kernel._active_task.name == "step_b"

    await kernel.emit(Event(type=EventType.TASK_COMPLETE, task_id=task_b.id))
    await _drain(kernel)
    assert len(task_c.blocked_by) == 0  # C 解锁

    await kernel._tick()
    assert kernel._active_task.name == "step_c"

    await kernel.stop()


# ── DB 崩溃恢复测试 ────────────────────────────────────────────────────────


async def test_crash_recovery_pending_survives(temp_db):
    """PENDING 任务在崩溃后从 DB 正确恢复，优先级和 metadata 完整。"""
    # ── 崩溃前：提交任务后模拟崩溃 ──
    k1 = RARKKernel(db_path=temp_db)
    await k1.start()

    task = Task(name="pending_job", priority=7, metadata={"target": "shelf"})
    await k1.emit(Event(type=EventType.TASK_SUBMIT, payload={"task": task}))
    await _drain(k1)
    assert task.state == LifecycleState.PENDING

    await k1._store.close()  # 模拟崩溃（不走 stop() 的正常流程）

    # ── 重启：_recover() 应把 PENDING 任务重新入队 ──
    k2 = RARKKernel(db_path=temp_db)
    await k2.start()

    recovered = k2.list_tasks()
    assert len(recovered) == 1
    r = recovered[0]
    assert r.name == "pending_job"
    assert r.priority == 7
    assert r.metadata["target"] == "shelf"
    assert r.state == LifecycleState.PENDING

    # 确认可以正常调度
    await k2._tick()
    assert k2._active_task is not None
    assert k2._active_task.name == "pending_job"

    await k2.stop()


async def test_crash_recovery_policy_fail(temp_db):
    """crash_policy='fail'：重启时 ACTIVE 任务转为 FAILED，不重新入队。"""
    # ── 崩溃前：任务进入 ACTIVE ──
    k1 = RARKKernel(db_path=temp_db)
    await k1.start()

    task = Task(name="risky_job", priority=5)
    await k1.emit(Event(type=EventType.TASK_SUBMIT, payload={"task": task}))
    await _drain(k1)
    await k1._tick()
    assert task.state == LifecycleState.ACTIVE

    await k1._store.close()  # 模拟崩溃

    # ── 重启：crash_policy="fail" → ACTIVE → FAILED ──
    k2 = RARKKernel(db_path=temp_db, crash_policy="fail")
    await k2.start()

    recovered = k2.list_tasks()
    assert len(recovered) == 1
    assert recovered[0].state == LifecycleState.FAILED

    # 确认没有可调度任务
    await k2._tick()
    assert k2._active_task is None

    await k2.stop()
