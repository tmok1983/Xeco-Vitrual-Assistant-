from app.core.models import CaseInput, ClientProfile, InsuranceProfile
from app.shared.subflows import validate_input


def test_validate_input_ok() -> None:
    case_input = CaseInput(
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
    )

    validate_input(case_input)
