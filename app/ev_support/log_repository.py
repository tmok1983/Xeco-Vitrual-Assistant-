from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ChatLogRecord:
    session_id: str
    user_id: str
    channel: str
    direction: str
    message_type: str
    language: str
    detected_intent: str | None
    message_text: str
    knowledge_hits: list[str]
    status: str
    analysis_text: str | None = None
    media_path: str | None = None
    error_detail: str | None = None


class SQLiteChatLogRepository:
    def __init__(self, db_path: str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        ddl = """
        create table if not exists chat_message_logs (
          id integer primary key autoincrement,
          session_id text not null,
          user_id text not null,
          channel text not null,
          direction text not null,
          message_type text not null default 'text',
          language text not null,
          detected_intent text,
          message_text text not null,
          analysis_text text,
          knowledge_hits text not null,
          status text not null,
          media_path text,
          error_detail text,
          created_at timestamptz not null default current_timestamp
        );

        create index if not exists idx_chat_logs_session_id
          on chat_message_logs (session_id, created_at desc);
        """
        with self._connect() as conn:
            conn.executescript(ddl)
            columns = {row["name"] for row in conn.execute("pragma table_info(chat_message_logs)").fetchall()}
            if "message_type" not in columns:
                conn.execute("alter table chat_message_logs add column message_type text not null default 'text'")
            if "media_path" not in columns:
                conn.execute("alter table chat_message_logs add column media_path text")
            if "analysis_text" not in columns:
                conn.execute("alter table chat_message_logs add column analysis_text text")
            conn.commit()

    def log_message(self, record: ChatLogRecord) -> None:
        sql = """
        insert into chat_message_logs (
          session_id, user_id, channel, direction, message_type, language,
          detected_intent, message_text, analysis_text, knowledge_hits, status, media_path, error_detail
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        with self._connect() as conn:
            conn.execute(
                sql,
                (
                    record.session_id,
                    record.user_id,
                    record.channel,
                    record.direction,
                    record.message_type,
                    record.language,
                    record.detected_intent,
                    record.message_text,
                    record.analysis_text,
                    json.dumps(record.knowledge_hits, ensure_ascii=False),
                    record.status,
                    record.media_path,
                    record.error_detail,
                ),
            )
            conn.commit()

    def list_messages(self, limit: int = 100, session_id: str | None = None) -> list[dict]:
        params: list[object] = []
        where = ""
        if session_id:
            where = "where session_id = ?"
            params.append(session_id)

        sql = f"""
        select id, session_id, user_id, channel, direction, message_type, language,
               detected_intent, message_text, analysis_text, knowledge_hits, status,
               media_path, error_detail, created_at
        from chat_message_logs
        {where}
        order by created_at desc, id desc
        limit ?
        """
        params.append(limit)

        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()

        result: list[dict] = []
        for row in rows:
            payload = dict(row)
            payload["knowledge_hits"] = json.loads(payload["knowledge_hits"])
            result.append(payload)
        return result
