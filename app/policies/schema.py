from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field


class PolicyBranding(BaseModel):
    advisor_brand_name: str
    primary_color: str
    secondary_color: str
    presentation_theme: str


class PolicyAnalysisConfig(BaseModel):
    allowed_output_style: list[str] = Field(default_factory=list)
    required_sections: list[str] = Field(default_factory=list)
    banned_phrases: list[str] = Field(default_factory=list)
    preferred_terms: dict[str, str] = Field(default_factory=dict)


class BannedClaimRule(BaseModel):
    phrase: str
    severity: Literal["low", "medium", "high"]
    reason: str


class ApprovalRules(BaseModel):
    low: Literal["auto_approve", "approve_with_disclosure", "manual_review"] = "auto_approve"
    medium: Literal["auto_approve", "approve_with_disclosure", "manual_review"] = "approve_with_disclosure"
    high: Literal["auto_approve", "approve_with_disclosure", "manual_review"] = "manual_review"


class RiskThresholds(BaseModel):
    low_max: int = 29
    medium_max: int = 69
    high_max: int = 100


class PolicyComplianceConfig(BaseModel):
    risk_thresholds: RiskThresholds
    banned_claims: list[BannedClaimRule] = Field(default_factory=list)
    required_disclosures: list[str] = Field(default_factory=list)
    approval_rules: ApprovalRules = Field(default_factory=ApprovalRules)


class PolicyPresentationConfig(BaseModel):
    required_slides: list[str] = Field(default_factory=list)
    mandatory_footer: str
    icon_style: str = "standard"
    max_bullets_per_slide: int = 4
    max_chars_per_bullet: int = 54


class PolicyRetrievalFilters(BaseModel):
    jurisdiction: str
    language: str
    status: str = "active"


class PolicyRetrievalConfig(BaseModel):
    collections: list[str] = Field(default_factory=list)
    doc_priority: list[str] = Field(default_factory=list)
    filters: PolicyRetrievalFilters


class PolicyPromptConfig(BaseModel):
    analysis_prompt_id: str
    compliance_prompt_id: str
    presentation_prompt_id: str


class PolicyAuditConfig(BaseModel):
    retain_prompt_id: bool = True
    retain_sources: bool = True
    retain_policy_version: bool = True


class PolicyPack(BaseModel):
    company_id: str
    company_name: str
    jurisdiction: str
    language: str
    version: str
    effective_from: str | date
    status: Literal["active", "retired", "draft"] = "active"
    branding: PolicyBranding
    analysis: PolicyAnalysisConfig
    compliance: PolicyComplianceConfig
    presentation: PolicyPresentationConfig
    retrieval: PolicyRetrievalConfig
    prompts: PolicyPromptConfig
    audit: PolicyAuditConfig = Field(default_factory=PolicyAuditConfig)
