import asyncio

import pytest

from rark.core.events import Event, EventType
from rark.core.runner import SkillRunner
from rark.core.task import Task
from rark.core.transitions import LifecycleState


@pytest.fixture
def temp_db(tmp_path):
    return str(tmp_path / "test.db")


async def _drain(runner: SkillRunner) -> None:
    """Process one event from the queue."""
    event = await runner._queue.get()
    await runner._dispatch(event)


async def test_skill_auto_complete(temp_db):
    """skill 正常返回 → task COMPLETED"""
    runner = SkillRunner(db_path=temp_db)
    await runner.start()

    task = Task(name="greeter", priority=5)

    async def greeter(t: Task) -> None:
        pass  # instant success

    runner.register("greeter", greeter)

    await runner.submit(task)
    await _drain(runner)  # consume TASK_SUBMIT
    await runner._tick()  # promote to ACTIVE + launch skill
    await asyncio.sleep(0)  # let asyncio.Task run
    await _drain(runner)  # consume TASK_COMPLETE

    assert task.state == LifecycleState.COMPLETED

    await runner.stop()


async def test_skill_auto_fail_on_exception(temp_db):
    """skill 抛异常 → task FAILED"""
    runner = SkillRunner(db_path=temp_db)
    await runner.start()

    task = Task(name="broken", priority=5)

    async def broken(t: Task) -> None:
        raise RuntimeError("oops")

    runner.register("broken", broken)

    await runner.submit(task)
    await _drain(runner)
    await runner._tick()
    await asyncio.sleep(0)
    await _drain(runner)  # consume TASK_FAIL

    assert task.state == LifecycleState.FAILED

    await runner.stop()


async def test_unknown_skill_auto_fail(temp_db):
    """未注册 skill → task FAILED"""
    runner = SkillRunner(db_path=temp_db)
    await runner.start()

    task = Task(name="ghost", priority=5)
    # intentionally NOT registering any skill

    await runner.submit(task)
    await _drain(runner)
    await runner._tick()  # promote + _launch_skill emits TASK_FAIL immediately
    await _drain(runner)  # consume TASK_FAIL

    assert task.state == LifecycleState.FAILED

    await runner.stop()


async def test_interrupt_cancels_skill(temp_db):
    """中断时 asyncio.Task 被 cancel，CancelledError 传入 skill"""
    runner = SkillRunner(db_path=temp_db)
    await runner.start()

    task = Task(name="long_pour", priority=5)
    cancelled = False

    async def long_pour(t: Task) -> None:
        nonlocal cancelled
        try:
            await asyncio.sleep(100)
        except asyncio.CancelledError:
            cancelled = True
            raise

    runner.register("long_pour", long_pour)

    await runner.submit(task)
    await _drain(runner)
    await runner._tick()  # promote to ACTIVE + launch skill
    await asyncio.sleep(0)  # let skill start (now blocked on sleep(100))

    # fire interrupt
    interrupt_task = Task(name="urgent", priority=10)
    await runner.interrupt(interrupt_task)
    await _drain(runner)  # consume INTERRUPT → cancel skill + pause task

    assert task.state == LifecycleState.PAUSED
    assert cancelled is True
    assert runner._running_skill_task is None

    await runner.stop()


async def test_cancel_active_skill(temp_db):
    """TASK_CANCEL 事件取消正在运行的 skill"""
    runner = SkillRunner(db_path=temp_db)
    await runner.start()

    task = Task(name="slow_mover", priority=5)

    async def slow_mover(t: Task) -> None:
        await asyncio.sleep(100)

    runner.register("slow_mover", slow_mover)

    await runner.submit(task)
    await _drain(runner)
    await runner._tick()  # promote to ACTIVE + launch skill
    await asyncio.sleep(0)  # let skill start

    await runner.emit(Event(type=EventType.TASK_CANCEL, task_id=task.id))
    await _drain(runner)  # consume TASK_CANCEL → cancel skill

    assert task.state == LifecycleState.CANCELLED
    assert runner._running_skill_task is None

    await runner.stop()


# ── Phase 2.2: Skill 重试机制 ─────────────────────────────────────────────


async def test_skill_retry_succeeds_on_nth_attempt(temp_db):
    """前 N 次失败后成功：task 最终 COMPLETED，retry_count 正确记录。"""
    runner = SkillRunner(db_path=temp_db)
    await runner.start()

    task = Task(name="flaky", priority=5, metadata={"max_retries": 2})
    call_count = 0

    @runner.skill("flaky")
    async def flaky(t: Task) -> None:
        nonlocal call_count
        call_count += 1
        if call_count <= 2:
            raise RuntimeError("temporary failure")
        # 第 3 次调用直接返回（成功）

    await runner.submit(task)
    await _drain(runner)  # TASK_SUBMIT

    # 第 1 次：失败 → TASK_RETRY
    await runner._tick()
    await asyncio.sleep(0)
    await _drain(runner)  # TASK_RETRY → task PENDING，retry_count=1

    # 第 2 次：失败 → TASK_RETRY
    await runner._tick()
    await asyncio.sleep(0)
    await _drain(runner)  # TASK_RETRY → task PENDING，retry_count=2

    # 第 3 次：成功 → TASK_COMPLETE
    await runner._tick()
    await asyncio.sleep(0)
    await _drain(runner)  # TASK_COMPLETE

    assert task.state == LifecycleState.COMPLETED
    assert call_count == 3
    assert task.metadata["retry_count"] == 2

    await runner.stop()


async def test_skill_retry_exhausted_then_failed(temp_db):
    """重试次数耗尽后转 FAILED（不再重试）。"""
    runner = SkillRunner(db_path=temp_db)
    await runner.start()

    task = Task(name="always_fails", priority=5, metadata={"max_retries": 1})

    @runner.skill("always_fails")
    async def always_fails(t: Task) -> None:
        raise RuntimeError("unrecoverable")

    await runner.submit(task)
    await _drain(runner)  # TASK_SUBMIT

    # 第 1 次：失败 → TASK_RETRY（retry_count=1，还有配额）
    await runner._tick()
    await asyncio.sleep(0)
    await _drain(runner)  # TASK_RETRY

    # 第 2 次：失败 → TASK_FAIL（retry_count=1 == max_retries=1，配额耗尽）
    await runner._tick()
    await asyncio.sleep(0)
    await _drain(runner)  # TASK_FAIL

    assert task.state == LifecycleState.FAILED
    assert task.metadata["retry_count"] == 1

    await runner.stop()


# ── Phase 1.1: Skill Resume 语义 ──────────────────────────────────────────


async def test_crash_recovery_metadata_intact(temp_db):
    """跨 runner 实例的崩溃恢复：metadata 和 checkpoint 从 DB 完整加载。"""
    # ── runner1：提交任务，运行到 PAUSED（含 checkpoint metadata）──
    runner1 = SkillRunner(db_path=temp_db)
    await runner1.start()

    task = Task(name="worker", priority=5)

    @runner1.skill("worker")
    async def worker_r1(t: Task) -> None:
        t.metadata["checkpoint"] = "phase_1_started"
        await asyncio.sleep(100)

    await runner1.submit(task)
    await _drain(runner1)  # TASK_SUBMIT
    await runner1._tick()  # → ACTIVE + launch skill
    await asyncio.sleep(0)  # skill 设好 checkpoint，阻塞

    intr = Task(name="urgent", priority=10)
    await runner1.interrupt(intr)
    await _drain(runner1)  # INTERRUPT → PAUSED + upsert

    assert task.state == LifecycleState.PAUSED
    await runner1._store.close()  # 模拟崩溃（跳过 stop()）

    # ── runner2：从同一个 DB 重启，_recover() 加载 PAUSED 任务 ──
    runner2 = SkillRunner(db_path=temp_db)
    await runner2.start()

    recovered = runner2.list_tasks()
    # 可能有 urgent 任务也在 DB 里，只关注 worker
    workers = [t for t in recovered if t.name == "worker"]
    assert len(workers) == 1
    w = workers[0]
    assert w.state == LifecycleState.PAUSED
    assert w.metadata["checkpoint"] == "phase_1_started"  # 跨进程 metadata 完整

    await runner2.stop()


async def test_metadata_persisted_on_pause(temp_db):
    """skill 写入 metadata 后被中断，PAUSED 状态的 metadata 应已写入 SQLite"""
    runner = SkillRunner(db_path=temp_db)
    await runner.start()

    task = Task(name="worker", priority=5)

    @runner.skill("worker")
    async def worker(t: Task) -> None:
        t.metadata["checkpoint"] = "phase_1_started"  # 同步写入，无 yield
        await asyncio.sleep(100)  # 在此处被 cancel

    await runner.submit(task)
    await _drain(runner)  # TASK_SUBMIT
    await runner._tick()  # → ACTIVE + launch skill
    await asyncio.sleep(0)  # skill 设好 checkpoint，阻塞在 sleep(100)

    intr = Task(name="urgent", priority=10)
    await runner.interrupt(intr)
    await _drain(runner)  # INTERRUPT: cancel skill + pause task + upsert

    # 内存中 metadata 已更新
    assert task.state == LifecycleState.PAUSED
    assert task.metadata["checkpoint"] == "phase_1_started"

    # SQLite 中 metadata 也已写入
    rows = await runner._store.load_all()
    paused = next(t for t in rows if t.name == "worker")
    assert paused.metadata["checkpoint"] == "phase_1_started"

    await runner.stop()


async def test_resume_reads_metadata_checkpoint(temp_db):
    """resume 后 skill 能从上次断点继续：stages_seen 应为 [0, 1]"""
    runner = SkillRunner(db_path=temp_db)
    await runner.start()

    task = Task(name="checkpointed", priority=5)
    stages_seen: list[int] = []

    @runner.skill("checkpointed")
    async def checkpointed(t: Task) -> None:
        stage = t.metadata.get("stage", 0)
        stages_seen.append(stage)
        if stage < 1:
            t.metadata["stage"] = 1  # 检查点：第一阶段已就绪
            await asyncio.sleep(100)  # 在此处被 cancel

        # stage >= 1 时直接完成（第二次调用走此分支）

    @runner.skill("urgent")
    async def urgent(t: Task) -> None:
        pass  # 立即完成

    # ── 第一次运行：提交 → ACTIVE → skill 设好 stage=1 → 阻塞 ──
    await runner.submit(task)
    await _drain(runner)  # TASK_SUBMIT
    await runner._tick()  # → ACTIVE + launch skill
    await asyncio.sleep(0)  # skill: stages_seen=[0], stage=1, 阻塞在 sleep(100)

    # 中断 → skill 被 cancel，task 转 PAUSED，metadata 保留
    intr = Task(name="urgent", priority=10)
    await runner.interrupt(intr)
    await _drain(runner)  # INTERRUPT event

    assert task.state == LifecycleState.PAUSED
    assert task.metadata["stage"] == 1

    # ── 完成中断任务 ──
    await runner._tick()  # intr → ACTIVE + launch urgent skill
    await asyncio.sleep(0)  # urgent: pass → emit TASK_COMPLETE
    await _drain(runner)  # TASK_COMPLETE for intr

    # ── resume 原任务 ──
    await runner._tick()  # checkpointed (PAUSED) → ACTIVE + re-launch skill
    await asyncio.sleep(
        0
    )  # skill: stages_seen=[0,1], stage >= 1 → 直接返回 → emit TASK_COMPLETE
    await _drain(runner)  # TASK_COMPLETE for task

    assert task.state == LifecycleState.COMPLETED
    assert stages_seen == [0, 1]

    await runner.stop()
