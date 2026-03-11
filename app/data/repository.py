from __future__ import annotations

import json
from datetime import datetime, timezone
from threading import Lock
from typing import Protocol

from app.core.models import CaseData


class CaseRepository(Protocol):
    def save(self, case: CaseData) -> CaseData: ...

    def get(self, case_id: str) -> CaseData | None: ...

    def list_all(self) -> list[CaseData]: ...


class InMemoryCaseRepository:
    def __init__(self) -> None:
        self._store: dict[str, CaseData] = {}
        self._lock = Lock()

    def save(self, case: CaseData) -> CaseData:
        with self._lock:
            case.updated_at = datetime.now(timezone.utc)
            self._store[case.case_id] = case
        return case

    def get(self, case_id: str) -> CaseData | None:
        return self._store.get(case_id)

    def list_all(self) -> list[CaseData]:
        return list(self._store.values())


class PostgresCaseRepository:
    def __init__(self, dsn: str) -> None:
        try:
            import psycopg  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("psycopg is required for PostgresCaseRepository") from exc

        self._psycopg = psycopg
        self._dsn = dsn
        self._ensure_schema()

    def _connect(self):
        return self._psycopg.connect(self._dsn)

    def _ensure_schema(self) -> None:
        ddl = """
        create table if not exists insurance_cases (
          case_id text primary key,
          payload jsonb not null,
          created_at timestamptz not null default now(),
          updated_at timestamptz not null default now()
        );

        create index if not exists idx_insurance_cases_updated_at
          on insurance_cases (updated_at desc);
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(ddl)
            conn.commit()

    def save(self, case: CaseData) -> CaseData:
        case.updated_at = datetime.now(timezone.utc)
        payload = json.dumps(case.model_dump(mode="json"))
        sql = """
        insert into insurance_cases (case_id, payload, created_at, updated_at)
        values (%s, %s::jsonb, %s, %s)
        on conflict (case_id)
        do update set
          payload = excluded.payload,
          updated_at = excluded.updated_at;
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (case.case_id, payload, case.created_at, case.updated_at))
            conn.commit()
        return case

    def get(self, case_id: str) -> CaseData | None:
        sql = "select payload from insurance_cases where case_id = %s"
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (case_id,))
                row = cur.fetchone()

        if not row:
            return None
        return CaseData.model_validate(row[0])

    def list_all(self) -> list[CaseData]:
        sql = "select payload from insurance_cases order by updated_at desc"
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
                rows = cur.fetchall()

        return [CaseData.model_validate(r[0]) for r in rows]
