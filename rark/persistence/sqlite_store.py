import json
from datetime import datetime, timezone
from typing import List, Optional

import aiosqlite

from ..core.task import Task
from ..core.transitions import LifecycleState

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS tasks (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    priority    INTEGER NOT NULL,
    state       TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    metadata    TEXT NOT NULL DEFAULT '{}',
    blocked_by  TEXT NOT NULL DEFAULT '[]'
)
"""


class SQLiteStore:
    def __init__(self, db_path: str = "rark.db"):
        self.db_path = db_path
        self._db: Optional[aiosqlite.Connection] = None

    async def open(self) -> None:
        self._db = await aiosqlite.connect(self.db_path)
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute(_CREATE_TABLE)
        await self._db.commit()

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    async def upsert(self, task: Task) -> None:
        await self._db.execute(
            """
            INSERT INTO tasks (id, name, priority, state, created_at, updated_at, metadata, blocked_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                state      = excluded.state,
                updated_at = excluded.updated_at,
                metadata   = excluded.metadata,
                blocked_by = excluded.blocked_by
            """,
            (
                task.id,
                task.name,
                task.priority,
                task.state.value,
                task.created_at.isoformat(),
                task.updated_at.isoformat(),
                json.dumps(task.metadata),
                json.dumps(sorted(task.blocked_by)),
            ),
        )
        await self._db.commit()

    async def load_all(self) -> List[Task]:
        async with self._db.execute(
            "SELECT id, name, priority, state, created_at, updated_at, metadata, blocked_by FROM tasks"
        ) as cursor:
            rows = await cursor.fetchall()

        tasks = []
        for row in rows:
            id_, name, priority, state, created_at, updated_at, metadata, blocked_by = (
                row
            )
            tasks.append(
                Task(
                    id=id_,
                    name=name,
                    priority=priority,
                    state=LifecycleState(state),
                    created_at=datetime.fromisoformat(created_at),
                    updated_at=datetime.fromisoformat(updated_at),
                    metadata=json.loads(metadata),
                    blocked_by=set(json.loads(blocked_by)),
                )
            )
        return tasks
