from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field


class RagDocumentMetadata(BaseModel):
    doc_id: str
    company_id: str
    jurisdiction: str
    language: str
    doc_type: Literal[
        "compliance_manual",
        "approved_script",
        "product_guide",
        "faq",
        "marketing_rule",
        "disclosure_rule",
    ]
    title: str
    source_path: str | None = None
    version: str
    status: Literal["active", "retired", "draft"]
    effective_from: str | date | None = None
    effective_to: str | date | None = None
    product_scope: list[str] = Field(default_factory=list)
    channel_scope: list[str] = Field(default_factory=list)
    section: str | None = None
    chunk_id: str
    chunk_index: int
    tags: list[str] = Field(default_factory=list)
