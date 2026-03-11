from __future__ import annotations

from datetime import datetime, timezone

from app.core.config import AppConfig
from app.core.models import CaseData, IntakeRequest, ReviewDecision, ReviewStatus, WorkflowStatus
from app.data.repository import CaseRepository
from app.llm.prompt_registry import PromptRegistry
from app.llm.providers import LLMClient
from app.modules.module_a import run_module_a
from app.modules.module_b import run_module_b
from app.modules.module_c import run_module_c
from app.shared.subflows import (
    compliance_checker,
    global_error_handler,
    normalize_input,
    save_to_data_layer,
    send_notification,
    update_status,
    validate_input,
)


class InsuranceOrchestrationService:
    def __init__(self, repo: CaseRepository, llm: LLMClient, prompts: PromptRegistry, config: AppConfig) -> None:
        self.repo = repo
        self.llm = llm
        self.prompts = prompts
        self.config = config

    def _create_case_id(self) -> str:
        return f"CASE-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f')}"

    def intake_and_orchestrate(self, req: IntakeRequest) -> dict:
        case_id = self._create_case_id()
        case = CaseData(
            case_id=case_id,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            status=WorkflowStatus.intake_received,
            review_status=ReviewStatus.pending,
            client_profile=req.input.client_profile,
            insurance_profile=req.input.insurance_profile,
            source=req.input.source,
            policy_context=req.policy_context,
        )

        try:
            validate_input(req.input)
            update_status(case, WorkflowStatus.validated)
            save_to_data_layer(case, "validated")

            normalized = normalize_input(req.input)
            case.client_profile = normalized.client_profile
            case.insurance_profile = normalized.insurance_profile
            update_status(case, WorkflowStatus.normalized)
            save_to_data_layer(case, "normalized")

            case.analysis_result = run_module_a(case, llm=self.llm, prompts=self.prompts)
            update_status(case, WorkflowStatus.analysis_done)
            save_to_data_layer(case, "module_a_completed")
            case.compliance_report = compliance_checker(case, llm=self.llm, prompts=self.prompts)
            save_to_data_layer(case, "compliance_precheck_completed")

            if req.generate_presentation:
                case.presentation_result = run_module_b(case, llm=self.llm, prompts=self.prompts, config=self.config)
                update_status(case, WorkflowStatus.presentation_done)
                save_to_data_layer(case, "module_b_completed")

            if req.generate_content:
                case.content_result = run_module_c(case, llm=self.llm, prompts=self.prompts)
                update_status(case, WorkflowStatus.content_done)
                save_to_data_layer(case, "module_c_completed")

            case.compliance_report = compliance_checker(case, llm=self.llm, prompts=self.prompts)
            case.compliance_notes = [f"{i.severity.value}:{i.claim}" for i in case.compliance_report.issues]
            if case.analysis_result:
                case.analysis_result.summary = case.compliance_report.safe_summary[:1200]
                case.analysis_result.advisor_narrative = case.compliance_report.advisor_narrative[:1200]
            save_to_data_layer(case, "compliance_postcheck_completed")
            case.review_status = ReviewStatus.pending
            update_status(case, WorkflowStatus.pending_review)
            save_to_data_layer(case, "pending_review")

            self.repo.save(case)
            notice = send_notification(case, channel="slack")
            return {"case": case, "notification": notice}

        except Exception as exc:
            update_status(case, WorkflowStatus.error)
            save_to_data_layer(case, "error")
            self.repo.save(case)
            err = global_error_handler(case_id, exc)
            return {"case": case, "error": err}

    def review_case(self, case_id: str, decision: ReviewDecision) -> CaseData:
        case = self.repo.get(case_id)
        if not case:
            raise ValueError(f"Case not found: {case_id}")

        case.review_status = ReviewStatus.approved if decision.approved else ReviewStatus.rejected
        case.events.append(f"reviewed_by={decision.reviewer};approved={decision.approved}")
        if decision.reason:
            case.events.append(f"review_reason={decision.reason}")

        update_status(case, WorkflowStatus.approved if decision.approved else WorkflowStatus.rejected)
        self.repo.save(case)
        return case
