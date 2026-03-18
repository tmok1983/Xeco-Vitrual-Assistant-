from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Protocol

from app.ev_support.models import EVSupportSession


class EVSupportSessionRepository(Protocol):
    def save(self, session: EVSupportSession) -> EVSupportSession: ...

    def get(self, session_id: str) -> EVSupportSession | None: ...


class InMemoryEVSupportRepository:
    def __init__(self) -> None:
        self._store: dict[str, EVSupportSession] = {}
        self._lock = Lock()

    def save(self, session: EVSupportSession) -> EVSupportSession:
        with self._lock:
            session.updated_at = datetime.now(timezone.utc)
            self._store[session.session_id] = session
        return session

    def get(self, session_id: str) -> EVSupportSession | None:
        return self._store.get(session_id)


@dataclass(frozen=True)
class SQLiteEVSupportRepository:
    db_path: str

    def __post_init__(self) -> None:
        path = Path(self.db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        ddl = """
        create table if not exists ev_support_sessions (
          session_id text primary key,
          payload text not null,
          updated_at timestamptz not null default current_timestamp
        );
        """
        with self._connect() as conn:
            conn.executescript(ddl)
            conn.commit()

    def save(self, session: EVSupportSession) -> EVSupportSession:
        session.updated_at = datetime.now(timezone.utc)
        sql = """
        insert into ev_support_sessions (session_id, payload, updated_at)
        values (?, ?, current_timestamp)
        on conflict(session_id) do update set
          payload = excluded.payload,
          updated_at = current_timestamp
        """
        with self._connect() as conn:
            conn.execute(
                sql,
                (
                    session.session_id,
                    session.model_dump_json(),
                ),
            )
            conn.commit()
        return session

    def get(self, session_id: str) -> EVSupportSession | None:
        sql = "select payload from ev_support_sessions where session_id = ?"
        with self._connect() as conn:
            row = conn.execute(sql, (session_id,)).fetchone()
        if not row:
            return None
        return EVSupportSession.model_validate_json(row["payload"])
