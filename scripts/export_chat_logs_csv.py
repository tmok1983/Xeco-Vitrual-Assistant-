from __future__ import annotations

import csv
import re
import sqlite3
import sys
from pathlib import Path


def flatten(value: object) -> str:
    if value is None:
        return ""
    text = " ".join(str(value).splitlines())
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: export_chat_logs_csv.py <sqlite_db_path> <output_csv_path>")
        return 2

    db_path = Path(sys.argv[1])
    out_path = Path(sys.argv[2])
    if not db_path.exists():
        print(f"database not found: {db_path}")
        return 1

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            select id, session_id, user_id, channel, direction, message_type, language,
                   detected_intent, message_text, analysis_text, knowledge_hits, status,
                   media_path, error_detail, created_at
            from chat_message_logs
            order by id desc
            """
        ).fetchall()
    finally:
        conn.close()

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        header = [
            "id",
            "session_id",
            "user_id",
            "channel",
            "direction",
            "message_type",
            "language",
            "detected_intent",
            "message_text",
            "analysis_text",
            "knowledge_hits",
            "status",
            "media_path",
            "error_detail",
            "created_at",
        ]
        writer.writerow(header)
        for row in rows:
            writer.writerow([flatten(row[col]) for col in header])

    print(f"wrote {len(rows)} rows to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
