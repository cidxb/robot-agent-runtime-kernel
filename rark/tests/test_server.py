import asyncio

import httpx
import pytest
from fastapi import FastAPI

from rark import SkillRunner, Task
from rark.server import create_app


@pytest.fixture
def temp_db(tmp_path):
    return str(tmp_path / "test.db")


@pytest.fixture
def app(temp_db) -> FastAPI:
    runner = SkillRunner(db_path=temp_db)

    @runner.skill("instant")
    async def instant(task: Task) -> None:
        pass  # completes immediately

    @runner.skill("slow")
    async def slow(task: Task) -> None:
        await asyncio.sleep(100)  # blocks until cancelled

    return create_app(runner)


@pytest.fixture
async def client(app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


async def test_health(client):
    r = await client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"


async def test_submit_task(client):
    r = await client.post("/tasks", json={"name": "instant", "priority": 5})
    assert r.status_code == 201
    data = r.json()
    assert data["name"] == "instant"
    assert data["state"] in ("pending", "active", "completed")


async def test_submit_and_list(client):
    await client.post("/tasks", json={"name": "instant", "priority": 5})
    r = await client.get("/tasks")
    assert r.status_code == 200
    assert len(r.json()) >= 1


async def test_get_task(client):
    r = await client.post("/tasks", json={"name": "instant", "priority": 5})
    task_id = r.json()["id"]

    r2 = await client.get(f"/tasks/{task_id}")
    assert r2.status_code == 200
    assert r2.json()["id"] == task_id


async def test_get_task_not_found(client):
    r = await client.get("/tasks/nonexistent-id")
    assert r.status_code == 404


async def test_cancel_task(client):
    r = await client.post("/tasks", json={"name": "slow", "priority": 5})
    task_id = r.json()["id"]

    r2 = await client.delete(f"/tasks/{task_id}")
    assert r2.status_code == 200
    assert r2.json()["cancelled"] == task_id


async def test_cancel_not_found(client):
    r = await client.delete("/tasks/nonexistent-id")
    assert r.status_code == 404


async def test_interrupt(client):
    r = await client.post("/interrupt", json={"name": "avoid_obstacle", "priority": 10})
    # avoid_obstacle is not registered in fixture, but RARK accepts the interrupt event
    # and will emit TASK_FAIL via "no skill registered" path
    assert r.status_code == 201
    data = r.json()
    assert data["name"] == "avoid_obstacle"
    assert data["priority"] == 10


async def test_submit_with_metadata(client):
    r = await client.post(
        "/tasks",
        json={"name": "instant", "priority": 7, "metadata": {"target": "kitchen"}},
    )
    assert r.status_code == 201
    data = r.json()
    assert data["metadata"]["target"] == "kitchen"
    assert data["priority"] == 7
