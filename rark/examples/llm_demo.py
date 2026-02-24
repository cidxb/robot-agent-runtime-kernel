"""
LLM → RARK integration demo
============================

Shows how a language model can act as a *planner* while RARK handles
execution, interruption, and recovery.

The demo works in two modes:

    python -m rark.examples.llm_demo            # mock LLM (no API key needed)
    python -m rark.examples.llm_demo --claude   # real Claude via anthropic SDK

Architecture
------------

    ┌─────────────────────────────┐
    │  LLM planner                │  decides WHAT to do
    │  (Claude / mock)            │
    └──────────┬──────────────────┘
               │  HTTP POST /tasks
               ▼
    ┌─────────────────────────────┐
    │  RARK kernel                │  decides WHEN and handles faults
    │  (this process)             │
    └──────────┬──────────────────┘
               │  async def skill(task)
               ▼
    ┌─────────────────────────────┐
    │  Skills                     │  simulated hardware calls
    └─────────────────────────────┘

Usage
-----

    pip install -e ".[server]"          # FastAPI + uvicorn
    pip install anthropic               # only for --claude mode

    python -m rark.examples.llm_demo
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from typing import Any

import httpx
import uvicorn

from rark.core.runner import SkillRunner
from rark.core.task import Task
from rark.server import create_app

logging.basicConfig(level=logging.INFO, format="%(name)s  %(levelname)s  %(message)s")
logger = logging.getLogger("llm_demo")

BASE_URL = "http://127.0.0.1:8765"

# ---------------------------------------------------------------------------
# Skills — simulated robot actions
# ---------------------------------------------------------------------------

runner = SkillRunner(db_path=":memory:")


@runner.skill("navigate_to")
async def navigate_to(task: Task) -> None:
    target = task.metadata.get("target", "base")
    logger.info("[skill] navigate_to  target=%s", target)
    await asyncio.sleep(1.0)  # simulate motion
    logger.info("[skill] navigate_to  arrived at %s", target)


@runner.skill("grasp_object")
async def grasp_object(task: Task) -> None:
    obj = task.metadata.get("object", "unknown")
    logger.info("[skill] grasp_object  object=%s", obj)
    await asyncio.sleep(0.8)
    logger.info("[skill] grasp_object  grasped %s", obj)


@runner.skill("pour_water")
async def pour_water(task: Task) -> None:
    stage = task.metadata.get("stage", 0)
    logger.info("[skill] pour_water  resuming from stage=%d", stage)

    if stage < 1:
        task.metadata["stage"] = 1
        logger.info("[skill] pour_water  moving arm to cup")
        await asyncio.sleep(0.5)

    if stage < 2:
        task.metadata["stage"] = 2
        logger.info("[skill] pour_water  tilting and pouring")
        await asyncio.sleep(0.5)

    logger.info("[skill] pour_water  done")


@runner.skill("avoid_obstacle")
async def avoid_obstacle(task: Task) -> None:
    logger.info("[skill] avoid_obstacle  emergency stop + backing up")
    await asyncio.sleep(0.3)
    logger.info("[skill] avoid_obstacle  safe")


# ---------------------------------------------------------------------------
# LLM planners
# ---------------------------------------------------------------------------


class MockPlanner:
    """Returns a hard-coded task plan without calling any API."""

    async def plan(self, goal: str) -> list[dict[str, Any]]:
        logger.info("[planner] mock — generating plan for: %r", goal)
        await asyncio.sleep(0.05)  # simulate latency

        # Fixed plan: navigate to kitchen, grasp cup, pour water
        return [
            {"name": "navigate_to",  "priority": 5, "metadata": {"target": "kitchen"}},
            {"name": "grasp_object", "priority": 5, "metadata": {"object": "cup"}},
            {"name": "pour_water",   "priority": 5, "metadata": {}},
        ]


class ClaudePlanner:
    """Calls Claude to translate a free-text goal into a RARK task list."""

    SYSTEM = """\
You are a robot task planner. The robot has these skills:
  - navigate_to(target: str)
  - grasp_object(object: str)
  - pour_water()

Given a goal, respond with a JSON array of tasks in execution order.
Each task: {"name": "<skill>", "priority": <1-10>, "metadata": {<kwargs>}}
Respond with ONLY the JSON array, no explanation."""

    def __init__(self) -> None:
        try:
            import anthropic  # type: ignore
        except ImportError:
            raise SystemExit(
                "anthropic package required for --claude mode.\n"
                "Install it with:  pip install anthropic"
            )
        self._client = anthropic.AsyncAnthropic()

    async def plan(self, goal: str) -> list[dict[str, Any]]:
        import anthropic  # type: ignore

        logger.info("[planner] claude — asking model for plan: %r", goal)
        message = await self._client.messages.create(
            model="claude-opus-4-6",
            max_tokens=512,
            system=self.SYSTEM,
            messages=[{"role": "user", "content": goal}],
        )
        raw = message.content[0].text.strip()
        logger.info("[planner] claude — raw response: %s", raw)
        return json.loads(raw)


# ---------------------------------------------------------------------------
# Demo orchestration
# ---------------------------------------------------------------------------


async def submit_plan(
    client: httpx.AsyncClient, tasks: list[dict[str, Any]]
) -> list[str]:
    """Submit an ordered plan, chaining blocked_by dependencies automatically."""
    ids: list[str] = []
    for i, spec in enumerate(tasks):
        payload: dict[str, Any] = {**spec}
        if ids:
            payload["blocked_by"] = [ids[-1]]  # each task waits for the previous
        resp = await client.post(f"{BASE_URL}/tasks", json=payload)
        resp.raise_for_status()
        task_id = resp.json()["id"]
        ids.append(task_id)
        logger.info("[http]    submitted %s → id=%s", spec["name"], task_id)
    return ids


async def wait_for_completion(
    client: httpx.AsyncClient, task_ids: list[str], timeout: float = 30.0
) -> None:
    terminal = {"COMPLETED", "FAILED", "CANCELLED"}
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        states = []
        for tid in task_ids:
            r = await client.get(f"{BASE_URL}/tasks/{tid}")
            r.raise_for_status()
            states.append(r.json()["state"])
        logger.info("[http]    states: %s", dict(zip([t[:8] for t in task_ids], states)))
        if all(s in terminal for s in states):
            return
        await asyncio.sleep(0.5)
    raise TimeoutError("tasks did not finish within timeout")


async def run_demo(use_claude: bool) -> None:
    planner = ClaudePlanner() if use_claude else MockPlanner()

    # Start the RARK HTTP server in the background
    app = create_app(runner)
    config = uvicorn.Config(app, host="127.0.0.1", port=8765, log_level="warning")
    server = uvicorn.Server(config)
    server_task = asyncio.create_task(server.serve())
    await asyncio.sleep(0.3)  # wait for server to bind

    async with httpx.AsyncClient() as client:
        # ── Phase 1: LLM plans the goal ──────────────────────────────────────
        goal = "Make tea: navigate to kitchen, grab the cup, and pour water into it"
        tasks = await planner.plan(goal)
        logger.info("[demo]    plan has %d tasks", len(tasks))

        task_ids = await submit_plan(client, tasks)

        # ── Phase 2: simulate an obstacle mid-execution ───────────────────────
        logger.info("[demo]    waiting 1.2s then injecting obstacle interrupt…")
        await asyncio.sleep(1.2)

        resp = await client.post(
            f"{BASE_URL}/interrupt",
            json={"name": "avoid_obstacle", "priority": 10, "metadata": {}},
        )
        resp.raise_for_status()
        logger.info("[demo]    interrupt submitted → id=%s", resp.json()["id"])

        # ── Phase 3: wait for everything to finish ────────────────────────────
        all_ids = task_ids + [resp.json()["id"]]
        await wait_for_completion(client, all_ids)

        # ── Summary ──────────────────────────────────────────────────────────
        print("\n── Final task states ──")
        for tid in all_ids:
            r = await client.get(f"{BASE_URL}/tasks/{tid}")
            t = r.json()
            print(f"  {t['name']:<20} {t['state']}")

    server.should_exit = True
    await server_task


def main() -> None:
    parser = argparse.ArgumentParser(description="LLM → RARK integration demo")
    parser.add_argument(
        "--claude",
        action="store_true",
        help="Use real Claude API instead of mock planner (requires ANTHROPIC_API_KEY)",
    )
    args = parser.parse_args()
    asyncio.run(run_demo(use_claude=args.claude))


if __name__ == "__main__":
    main()
