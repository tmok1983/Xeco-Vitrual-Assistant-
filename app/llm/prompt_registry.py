from __future__ import annotations

import json
from pathlib import Path


class PromptRegistry:
    def __init__(self, prompt_file: str | None = None) -> None:
        if prompt_file:
            self.prompt_file = Path(prompt_file)
        else:
            self.prompt_file = Path(__file__).resolve().parents[2] / "prompts" / "registry.json"
        self._cache: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        if not self.prompt_file.exists():
            raise FileNotFoundError(f"Prompt file not found: {self.prompt_file}")

        data = json.loads(self.prompt_file.read_text(encoding="utf-8"))
        self._cache = {item["id"]: item["template"] for item in data.get("prompts", [])}

    def get(self, prompt_id: str) -> str:
        if prompt_id not in self._cache:
            raise KeyError(f"Prompt ID not found: {prompt_id}")
        return self._cache[prompt_id]
