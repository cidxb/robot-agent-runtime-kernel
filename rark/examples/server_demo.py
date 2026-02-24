"""
RARK HTTP API Demo

三个预设技能（mock，实际部署时替换为真实硬件调用）：
  navigate_to    priority=5   ~5s   metadata: {"target": "kitchen"}
  pour_water     priority=5   ~8s
  avoid_obstacle priority=10  ~1s   通常作为中断使用

启动：
  python -m rark.examples.server_demo

API 文档：
  http://localhost:8000/docs

示例调用：
  # 1. 提交 pour_water 任务
  curl -s -X POST http://localhost:8000/tasks \\
       -H "Content-Type: application/json" \\
       -d '{"name": "pour_water", "priority": 5}' | python3 -m json.tool

  # 2. 技能执行中，触发中断
  curl -s -X POST http://localhost:8000/interrupt \\
       -H "Content-Type: application/json" \\
       -d '{"name": "avoid_obstacle", "priority": 10}' | python3 -m json.tool

  # 3. 查看当前状态
  curl -s http://localhost:8000/health | python3 -m json.tool

  # 4. 查看所有任务
  curl -s http://localhost:8000/tasks | python3 -m json.tool

  # 5. 导航（带参数）
  curl -s -X POST http://localhost:8000/tasks \\
       -H "Content-Type: application/json" \\
       -d '{"name": "navigate_to", "priority": 5, "metadata": {"target": "kitchen"}}' \\
       | python3 -m json.tool

  # 6. 取消任务（替换 <task_id>）
  curl -s -X DELETE http://localhost:8000/tasks/<task_id> | python3 -m json.tool
"""

import asyncio

import uvicorn

from rark import SkillRunner, Task
from rark.server import create_app

runner = SkillRunner(db_path=":memory:")


# ── Skill 注册 ────────────────────────────────────────────────────────────
# 这里是你唯一需要替换的地方：
# Mock 版 → await asyncio.sleep(n)
# 真实版 → await ros_action_client.execute(...) 或其他底层控制接口


@runner.skill("navigate_to")
async def navigate_to(task: Task) -> None:
    target = task.metadata.get("target", "unknown")
    print(f"  [skill] navigate_to  → '{target}' (5s)")
    await asyncio.sleep(5)
    print(f"  [skill] navigate_to  ✓ arrived at '{target}'")


@runner.skill("pour_water")
async def pour_water(task: Task) -> None:
    print(f"  [skill] pour_water   → starting (8s)")
    for i in range(1, 5):
        await asyncio.sleep(2)
        print(f"  [skill] pour_water   … {i * 25}%")
    print(f"  [skill] pour_water   ✓ done")


@runner.skill("avoid_obstacle")
async def avoid_obstacle(task: Task) -> None:
    print(f"  [skill] avoid_obstacle → evading (1s)")
    await asyncio.sleep(1)
    print(f"  [skill] avoid_obstacle ✓ clear")


# ── App ───────────────────────────────────────────────────────────────────

app = create_app(runner)

if __name__ == "__main__":
    banner = "═" * 56
    print(f"\n{banner}")
    print("  RARK HTTP API")
    print("  Endpoints : http://localhost:8000")
    print("  Swagger   : http://localhost:8000/docs")
    print(f"{banner}\n")
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="warning")
