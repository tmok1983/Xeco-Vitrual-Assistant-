from __future__ import annotations

from pathlib import Path

import yaml

from app.policies.schema import PolicyPack


class PolicyPackLoader:
    def __init__(self, base_dir: str | None = None) -> None:
        self.base_dir = Path(base_dir) if base_dir else Path(__file__).resolve().parents[2] / "policy_packs"

    def load(self, filename: str) -> PolicyPack:
        path = self.base_dir / filename
        if not path.exists():
            raise FileNotFoundError(f"Policy pack not found: {path}")
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return PolicyPack.model_validate(data)

    def list_available(self) -> list[str]:
        if not self.base_dir.exists():
            return []
        return sorted(p.name for p in self.base_dir.glob("*.yaml"))
