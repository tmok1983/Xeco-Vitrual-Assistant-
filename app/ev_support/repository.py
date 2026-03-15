from __future__ import annotations

from datetime import datetime, timezone
from threading import Lock

from app.ev_support.models import EVSupportSession


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
