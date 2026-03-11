from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class WorkflowStatus(str, Enum):
    intake_received = "intake_received"
    validated = "validated"
    normalized = "normalized"
    analysis_done = "analysis_done"
    presentation_done = "presentation_done"
    content_done = "content_done"
    pending_review = "pending_review"
    approved = "approved"
    rejected = "rejected"
    error = "error"


class ReviewStatus(str, Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


class ComplianceRiskLevel(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"


class ComplianceIssueSeverity(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"


class ClientProfile(BaseModel):
    name_or_code: str
    age: int = Field(ge=0, le=120)
    gender: Literal["Male", "Female", "Other"]
    marital_status: Literal["Single", "Married", "Divorced", "Widowed"]
    dependents: int = Field(ge=0, le=20)
    occupation: str
    income_monthly: float = Field(ge=0)
    expenses_monthly: float = Field(ge=0)
    budget_monthly: float = Field(ge=0)


class InsuranceProfile(BaseModel):
    existing_medical: bool
    existing_ci: bool
    existing_life: bool
    existing_accident: bool
    current_premium: float = Field(ge=0)


class CaseInput(BaseModel):
    client_profile: ClientProfile
    insurance_profile: InsuranceProfile
    source: str = "web_form"


class CasePolicyContext(BaseModel):
    company_id: str = "default_company"
    company_name: str = "Default Insurance"
    jurisdiction: str = "HK"
    language: str = "zh-HK"
    policy_version: str = "default-v1"
    product_scope: list[str] = Field(default_factory=list)
    channel: str = "advisor_meeting"


class RetrievalSourceRef(BaseModel):
    doc_id: str
    title: str
    doc_type: str
    section: str | None = None
    version: str | None = None
    effective_date: str | None = None
    excerpt: str | None = None


class ModelAudit(BaseModel):
    provider: str
    model: str
    prompt_id: str
    policy_version: str
    retrieval_used: bool = False
    source_refs: list[RetrievalSourceRef] = Field(default_factory=list)


class RiskGapResult(BaseModel):
    score: int = Field(ge=0, le=100)
    ranking: list[str]
    findings: list[str]


class AnalysisResult(BaseModel):
    summary: str
    technical_summary: str
    advisor_narrative: str
    recommendations: list[str]
    risk_gap: RiskGapResult
    prompt_version: str = "analysis_writer_v2"
    audit: ModelAudit | None = None


class PresentationResult(BaseModel):
    pages: list[dict[str, str]]
    google_presentation_id: str | None = None
    google_presentation_url: str | None = None
    policy_version: str | None = None
    template_version: str | None = None


class ContentResult(BaseModel):
    instagram_caption: str
    carousel_copy: list[str]
    xiaohongshu_version: str
    short_video_script: str


class ComplianceIssue(BaseModel):
    claim: str
    reason: str
    severity: ComplianceIssueSeverity
    suggested_fix: str


class ComplianceReport(BaseModel):
    risk_level: ComplianceRiskLevel
    issues: list[ComplianceIssue] = Field(default_factory=list)
    required_disclosures: list[str] = Field(default_factory=list)
    approval_recommendation: Literal["auto_approve", "approve_with_disclosure", "manual_review"]
    safe_summary: str
    advisor_narrative: str
    source_refs: list[RetrievalSourceRef] = Field(default_factory=list)
    policy_version: str | None = None


class CaseData(BaseModel):
    case_id: str
    created_at: datetime
    updated_at: datetime
    status: WorkflowStatus
    review_status: ReviewStatus
    client_profile: ClientProfile
    insurance_profile: InsuranceProfile
    source: str
    policy_context: CasePolicyContext = Field(default_factory=CasePolicyContext)
    analysis_result: AnalysisResult | None = None
    presentation_result: PresentationResult | None = None
    content_result: ContentResult | None = None
    compliance_report: ComplianceReport | None = None
    compliance_notes: list[str] = Field(default_factory=list)
    events: list[str] = Field(default_factory=list)


class IntakeRequest(BaseModel):
    input: CaseInput
    generate_presentation: bool = True
    generate_content: bool = True
    policy_context: CasePolicyContext = Field(default_factory=CasePolicyContext)


class ReviewDecision(BaseModel):
    approved: bool
    reviewer: str
    reason: str | None = None


class ErrorEvent(BaseModel):
    case_id: str
    message: str
    detail: str | None = None
    at: datetime
