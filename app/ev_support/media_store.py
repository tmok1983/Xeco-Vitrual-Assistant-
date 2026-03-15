from __future__ import annotations

import mimetypes
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class StoredMedia:
    message_type: str
    content_type: str
    file_path: str
    file_size: int


class LocalMediaStore:
    def __init__(self, root_dir: str) -> None:
        self.root_dir = Path(root_dir)
        self.root_dir.mkdir(parents=True, exist_ok=True)

    def store(self, session_id: str, message_id: str, message_type: str, content_type: str, payload: bytes) -> StoredMedia:
        ext = self._extension_for(content_type, message_type)
        target_dir = self.root_dir / session_id.replace(":", "_")
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / f"{message_id}{ext}"
        target_path.write_bytes(payload)
        return StoredMedia(
            message_type=message_type,
            content_type=content_type,
            file_path=str(target_path),
            file_size=len(payload),
        )

    def _extension_for(self, content_type: str, message_type: str) -> str:
        guessed = mimetypes.guess_extension((content_type or "").split(";")[0].strip()) if content_type else None
        if guessed:
            return guessed
        return ".jpg" if message_type == "image" else ".m4a" if message_type == "audio" else ".bin"
