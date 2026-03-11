from app.core.models import CaseInput, ClientProfile, InsuranceProfile, IntakeRequest
from app.core.config import AppConfig
from app.core.service import InsuranceOrchestrationService
from app.data.repository import InMemoryCaseRepository
from app.llm.prompt_registry import PromptRegistry
from app.llm.providers import MockLLMClient


def test_intake_runs_modules() -> None:
    service = InsuranceOrchestrationService(
        repo=InMemoryCaseRepository(),
        llm=MockLLMClient(),
        prompts=PromptRegistry(),
        config=AppConfig(),
    )
    req = IntakeRequest(
        input=CaseInput(
            client_profile=ClientProfile(
                name_or_code="client a",
                age=40,
                gender="Male",
                marital_status="Married",
                dependents=2,
                occupation="Manager",
                income_monthly=50000,
                expenses_monthly=25000,
                budget_monthly=3000,
            ),
            insurance_profile=InsuranceProfile(
                existing_medical=True,
                existing_ci=False,
                existing_life=False,
                existing_accident=True,
                current_premium=1200,
            ),
        ),
        generate_presentation=True,
        generate_content=True,
    )

    result = service.intake_and_orchestrate(req)
    assert "error" not in result
    case = result["case"]
    assert case.analysis_result is not None
    assert case.presentation_result is not None
    assert case.content_result is not None
