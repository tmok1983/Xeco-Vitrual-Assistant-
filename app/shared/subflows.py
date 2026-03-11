from __future__ import annotations

from datetime import datetime, timezone

from app.core.models import (
    CaseData,
    CaseInput,
    ComplianceIssue,
    ComplianceIssueSeverity,
    ComplianceReport,
    ComplianceRiskLevel,
    ErrorEvent,
    WorkflowStatus,
)
from app.llm.prompt_registry import PromptRegistry
from app.llm.providers import LLMClient


REQUIRED_FIELDS = [
    "name_or_code",
    "age",
    "gender",
    "marital_status",
    "dependents",
    "occupation",
    "income_monthly",
    "expenses_monthly",
    "budget_monthly",
]


def validate_input(case_input: CaseInput) -> None:
    missing = []
    client_data = case_input.client_profile.model_dump()
    for field in REQUIRED_FIELDS:
        value = client_data.get(field)
        if value is None or (isinstance(value, str) and not value.strip()):
            missing.append(field)

    if case_input.client_profile.expenses_monthly > case_input.client_profile.income_monthly:
        missing.append("expenses_monthly must not exceed income_monthly")

    if missing:
        raise ValueError(f"Input validation failed: {', '.join(missing)}")


def normalize_input(case_input: CaseInput) -> CaseInput:
    # Keep normalization predictable for downstream modules.
    normalized_name = case_input.client_profile.name_or_code.strip().title()
    case_input.client_profile.name_or_code = normalized_name
    return case_input


def save_to_data_layer(case: CaseData, event: str) -> CaseData:
    case.events.append(event)
    case.updated_at = datetime.now(timezone.utc)
    return case


DISALLOWED_CLAIMS = [
    ("guarantee", "Contains guarantee wording without qualification."),
    ("guaranteed", "Contains guaranteed wording without qualification."),
    ("100%", "Contains absolute certainty claim."),
    ("no risk", "Contains no-risk claim."),
]

REQUIRED_DISCLOSURES = [
    "本建議僅供保障規劃參考，非投資或法律意見。",
    "實際保障內容與理賠條件以保單條款及核保結果為準。",
]


def compliance_checker(case: CaseData, llm: LLMClient, prompts: PromptRegistry) -> ComplianceReport:
    issues: list[ComplianceIssue] = []
    source_texts: list[str] = []

    if case.analysis_result:
        source_texts.append(case.analysis_result.summary)
        source_texts.extend(case.analysis_result.recommendations)
    if case.content_result:
        source_texts.append(case.content_result.instagram_caption)
        source_texts.append(case.content_result.xiaohongshu_version)

    lowered = "\n".join(source_texts).lower()
    for keyword, reason in DISALLOWED_CLAIMS:
        if keyword in lowered:
            severity = ComplianceIssueSeverity.high if keyword in {"100%", "no risk"} else ComplianceIssueSeverity.medium
            issues.append(
                ComplianceIssue(
                    claim=keyword,
                    reason=reason,
                    severity=severity,
                    suggested_fix="Use conditional language and include product-term disclaimers.",
                )
            )

    if len(issues) >= 2 or any(i.severity == ComplianceIssueSeverity.high for i in issues):
        risk_level = ComplianceRiskLevel.high
        approval_recommendation = "manual_review"
    elif issues:
        risk_level = ComplianceRiskLevel.medium
        approval_recommendation = "approve_with_disclosure"
    else:
        risk_level = ComplianceRiskLevel.low
        approval_recommendation = "auto_approve"

    original = case.analysis_result.summary if case.analysis_result else ""
    prompt = prompts.get("compliance_rewriter_v1").format(text=original)
    safe_summary = llm.complete(prompt)
    advisor_narrative = safe_summary[:900]

    return ComplianceReport(
        risk_level=risk_level,
        issues=issues,
        required_disclosures=REQUIRED_DISCLOSURES,
        approval_recommendation=approval_recommendation,
        safe_summary=safe_summary[:1500],
        advisor_narrative=advisor_narrative,
    )


def send_notification(case: CaseData, channel: str = "email") -> str:
    return f"[{channel}] case={case.case_id} status={case.status.value} review={case.review_status.value}"


def global_error_handler(case_id: str, exc: Exception) -> ErrorEvent:
    return ErrorEvent(
        case_id=case_id,
        message=str(exc),
        detail=repr(exc),
        at=datetime.now(timezone.utc),
    )


def update_status(case: CaseData, status: WorkflowStatus) -> None:
    case.status = status
    case.updated_at = datetime.now(timezone.utc)
