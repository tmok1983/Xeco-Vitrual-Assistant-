from __future__ import annotations

from app.core.models import AnalysisResult, CaseData, RiskGapResult
from app.llm.prompt_registry import PromptRegistry
from app.llm.providers import LLMClient


class RiskGapEngine:
    @staticmethod
    def run(case: CaseData) -> RiskGapResult:
        cp = case.client_profile
        ip = case.insurance_profile

        score = 0
        findings: list[str] = []

        if cp.dependents > 0 and not ip.existing_life:
            score += 30
            findings.append("有受扶養責任，但目前保障資料中未見壽險配置。")

        if cp.age >= 35 and not ip.existing_ci:
            score += 25
            findings.append("以目前年齡階段判斷，重大疾病保障可能存在缺口。")

        if not ip.existing_medical:
            score += 20
            findings.append("目前未見醫療保障配置，醫療支出風險較高。")

        savings_capacity = cp.income_monthly - cp.expenses_monthly
        if savings_capacity < cp.budget_monthly:
            score += 15
            findings.append("目前申報的保險預算可能對每月現金流造成壓力。")

        if ip.current_premium < cp.income_monthly * 0.02:
            score += 10
            findings.append("目前保費占收入比例偏低，保障完整度可能不足。")

        ranking: list[str] = ["醫療保障", "壽險保障", "重大疾病保障", "意外保障"]

        if not ip.existing_life:
            ranking.insert(0, ranking.pop(ranking.index("壽險保障")))
        if not ip.existing_ci:
            ranking.insert(1, ranking.pop(ranking.index("重大疾病保障")))

        return RiskGapResult(score=min(score, 100), ranking=ranking, findings=findings)


class AnalysisWriter:
    @staticmethod
    def write(case: CaseData, risk: RiskGapResult, llm: LLMClient, prompts: PromptRegistry) -> AnalysisResult:
        cp = case.client_profile
        template = prompts.get("analysis_writer_v2")
        prompt = template.format(
            case_id=case.case_id,
            client_name=cp.name_or_code,
            age=cp.age,
            dependents=cp.dependents,
            risk_score=risk.score,
            findings="; ".join(risk.findings),
            ranking=", ".join(risk.ranking),
        )
        llm_text = llm.complete(prompt)

        technical_summary = (
            f"{cp.name_or_code} 的保障缺口評分為 {risk.score}/100。"
            f"目前優先補強方向為：{'、'.join(risk.ranking[:3])}。\n{llm_text[:700]}"
        )
        advisor_narrative = (
            f"根據目前資料，{cp.name_or_code} 的保障優先順序建議為 {'、'.join(risk.ranking[:3])}。"
            "建議以分階段方式調整，並在年度檢視時更新家庭與收入變動。"
        )

        recommendations = [
            f"優先補強 {risk.ranking[0]}，並以每月預算 {cp.budget_monthly:.0f} 作為規劃基準。",
            "以 6 至 12 個月分階段補強保障，避免一次性調整造成現金流壓力。",
            "建議每年依家庭責任、收入與保障缺口進行一次保單檢視。",
        ]

        return AnalysisResult(
            summary=technical_summary,
            technical_summary=technical_summary,
            advisor_narrative=advisor_narrative,
            recommendations=recommendations,
            risk_gap=risk,
            prompt_version="analysis_writer_v2",
        )


def run_module_a(case: CaseData, llm: LLMClient, prompts: PromptRegistry) -> AnalysisResult:
    risk = RiskGapEngine.run(case)
    return AnalysisWriter.write(case, risk, llm=llm, prompts=prompts)
