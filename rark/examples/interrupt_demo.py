"""
RARK 演示：倒水被障碍物检测中断（SkillRunner 自动执行）

场景：
  1. 机器人执行"倒水"任务（priority=3），技能自动启动
  2. 传感器检测到障碍物，触发高优先级中断（priority=10）
  3. 倒水技能被取消、任务挂起（PAUSED），避障任务立即接管（ACTIVE）
  4. 避障技能自动完成后，倒水任务自动恢复（ACTIVE）并重新启动技能
"""

import asyncio

from rark.core.runner import SkillRunner
from rark.core.task import Task


def _sep(title: str = "") -> None:
    width = 52
    if title:
        pad = (width - len(title) - 2) // 2
        print(f"{'─' * pad} {title} {'─' * (width - pad - len(title) - 2)}")
    else:
        print("─" * width)


def _status(runner: SkillRunner, *tasks: Task) -> None:
    active = runner._active_task
    label = active.name if active else "None"
    print(f"  active task : {label}")
    for t in tasks:
        print(f"  {t.name:<20} state={t.state.value}")


async def _process(runner: SkillRunner) -> None:
    """Drain one event from the queue and dispatch it."""
    event = await runner._queue.get()
    await runner._dispatch(event)


async def main() -> None:
    _sep("RARK Interrupt Demo")

    runner = SkillRunner(db_path=":memory:")
    await runner.start()

    # ── Register skills ──────────────────────────────────────────────────
    @runner.skill("pour_water")
    async def pour_water_skill(task: Task) -> None:
        print("  [skill] pour_water: executing…")
        await asyncio.sleep(0)

    @runner.skill("avoid_obstacle")
    async def avoid_obstacle_skill(task: Task) -> None:
        print("  [skill] avoid_obstacle: executing…")
        await asyncio.sleep(0)

    pour_water = Task(name="pour_water", priority=3)
    avoid_obstacle = Task(name="avoid_obstacle", priority=10)

    # ── Step 1: submit pour_water ──────────────────────────────────────
    _sep("Step 1: submit pour_water")
    await runner.submit(pour_water)
    await _process(runner)  # consume TASK_SUBMIT
    await runner._tick()  # promote to ACTIVE + launch skill (not yet run)
    _status(runner, pour_water)

    # ── Step 2: interrupt fires before skill completes ─────────────────
    _sep("Step 2: obstacle detected → INTERRUPT")
    await runner.interrupt(avoid_obstacle)
    await _process(runner)  # consume INTERRUPT → cancel skill + pause pour_water
    _status(runner, pour_water, avoid_obstacle)

    # ── Step 3: avoid_obstacle activates and skill runs ───────────────
    _sep("Step 3: tick → avoid_obstacle activates")
    await runner._tick()  # promote to ACTIVE + launch avoid_obstacle skill
    await asyncio.sleep(0)  # let skill run one cycle
    await asyncio.sleep(0)  # let emit complete
    await _process(runner)  # consume TASK_COMPLETE
    _status(runner, pour_water, avoid_obstacle)

    # ── Step 4: pour_water resumes and skill runs ──────────────────────
    _sep("Step 4: tick → pour_water resumes")
    await runner._tick()  # promote to ACTIVE + launch pour_water skill
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    await _process(runner)  # consume TASK_COMPLETE
    _status(runner, pour_water, avoid_obstacle)

    await runner.stop()
    _sep("Done")


if __name__ == "__main__":
    asyncio.run(main())
