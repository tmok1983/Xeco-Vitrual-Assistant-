from __future__ import annotations

from app.core.config import AppConfig
from app.core.models import CaseData, PresentationResult
from app.modules.google_slides import create_google_slides_presentation
from app.llm.prompt_registry import PromptRegistry
from app.llm.providers import LLMClient


def _money(value: float) -> str:
    return f"{value:,.0f}"


def _join_lines(lines: list[str]) -> str:
    return "\n".join(line for line in lines if line)


def _trim_text(text: str, limit: int) -> str:
    clean = " ".join(text.split())
    return clean if len(clean) <= limit else clean[: limit - 1].rstrip() + "…"


def _trim_points(points: list[str], max_points: int = 4, max_chars: int = 54) -> list[str]:
    return [_trim_text(point, max_chars) for point in points[:max_points]]


def _visual_for(section: str, title: str) -> str:
    key = f"{section} {title}"
    if "封面" in key or "提案" in key:
        return "📘"
    if "客戶" in key or "摘要" in key:
        return "👤"
    if "保障盤點" in key:
        return "🛡️"
    if "風險" in key:
        return "📊"
    if "責任" in key:
        return "🏠"
    if "策略" in key:
        return "🧭"
    if "預算" in key:
        return "💰"
    if "顧問" in key:
        return "🤝"
    if "行動" in key:
        return "✅"
    if "會談" in key or "常見問題" in key:
        return "💬"
    if "合規" in key:
        return "⚖️"
    return "📘"


def _slide(section: str, title: str, key_message: str, supporting_points: list[str], advisor_recommendation: str) -> dict[str, str]:
    icon = _visual_for(section, title)
    trimmed_key_message = _trim_text(key_message, 56)
    trimmed_points = _trim_points(supporting_points)
    trimmed_recommendation = _trim_text(advisor_recommendation, 90)
    return {
        "section": section,
        "title": title,
        "key_message": trimmed_key_message,
        "supporting_points": "\n".join(f"- {item}" for item in trimmed_points),
        "advisor_recommendation": trimmed_recommendation,
        "visual_icon": icon,
        "content": _join_lines(
            [
                f"重點訊息：{trimmed_key_message}",
                "",
                "分析重點：",
                *[f"- {item}" for item in trimmed_points],
                "",
                f"顧問建議：{trimmed_recommendation}",
            ]
        ),
    }


def run_module_b(case: CaseData, llm: LLMClient, prompts: PromptRegistry, config: AppConfig) -> PresentationResult:
    if not case.analysis_result:
        raise ValueError("Module B requires analysis_result from Module A")

    cp = case.client_profile
    ip = case.insurance_profile
    analysis = case.analysis_result
    risk = case.analysis_result.risk_gap
    compliance = case.compliance_report
    disclosures = compliance.required_disclosures if compliance else []
    risk_level = compliance.risk_level.value if compliance else "low"
    disclosure_prompt = prompts.get("presentation_compliance_slide_v1").format(
        risk_level=risk_level, disclosures="; ".join(disclosures)
    )
    disclosure_text = llm.complete(disclosure_prompt)[:500]
    narrative_prompt = prompts.get("presentation_storyline_v1").format(
        client_name=cp.name_or_code,
        age=cp.age,
        occupation=cp.occupation,
        marital_status=cp.marital_status,
        dependents=cp.dependents,
        income_monthly=_money(cp.income_monthly),
        expenses_monthly=_money(cp.expenses_monthly),
        budget_monthly=_money(cp.budget_monthly),
        current_premium=_money(ip.current_premium),
        risk_score=risk.score,
        risk_ranking=", ".join(risk.ranking[:4]),
        findings="; ".join(risk.findings) or "目前沒有重大保障缺口",
        technical_summary=analysis.technical_summary,
        advisor_narrative=analysis.advisor_narrative,
        recommendations="; ".join(analysis.recommendations),
    )
    storyline = llm.complete(narrative_prompt)[:1000]
    savings_capacity = max(cp.income_monthly - cp.expenses_monthly, 0)
    protection_gaps = [
        "醫療保障" if not ip.existing_medical else None,
        "重大疾病保障" if not ip.existing_ci else None,
        "壽險保障" if not ip.existing_life else None,
        "意外保障" if not ip.existing_accident else None,
    ]
    missing_covers = [item for item in protection_gaps if item]
    top_recommendation = risk.ranking[0] if risk.ranking else "醫療"
    secondary_recommendation = risk.ranking[1] if len(risk.ranking) > 1 else top_recommendation

    pages = [
        _slide(
            "提案封面",
            "客戶保障規劃提案",
            f"本次提案主軸為優先補強 {top_recommendation}，逐步建立完整的家庭保障防線。",
            [
                f"客戶：{cp.name_or_code}",
                "提案內容涵蓋現況盤點、風險缺口、優先順序與執行建議",
                f"顧問摘要：{analysis.advisor_narrative[:120]}",
            ],
            "先建立共同理解，再進入具體保障排序與預算配置。",
        ),
        _slide(
            "客戶摘要",
            "01 | 客戶現況總覽",
            f"{cp.name_or_code} 目前具備規劃空間，但需在家庭責任與現金流間取得平衡。",
            [
                f"年齡 / 職業：{cp.age} 歲 / {cp.occupation}",
                f"婚姻 / 受扶養人：{cp.marital_status} / {cp.dependents} 人",
                f"每月收入 / 支出：{_money(cp.income_monthly)} / {_money(cp.expenses_monthly)}",
                f"每月可運用餘額：約 {_money(savings_capacity)}",
                f"保險預算 / 現行保費：{_money(cp.budget_monthly)} / {_money(ip.current_premium)}",
            ],
            "建議先確認家庭責任與每月可承受保費區間，作為後續方案基準。",
        ),
        _slide(
            "保障盤點",
            "02 | 現有保障盤點",
            f"現有保障尚未完整覆蓋 {top_recommendation} 與家庭責任需求。",
            [
                f"已配置：{'、'.join([name for name, enabled in [('醫療', ip.existing_medical), ('重疾', ip.existing_ci), ('壽險', ip.existing_life), ('意外', ip.existing_accident)] if enabled]) or '尚未明確配置'}",
                f"待補強：{'、'.join(missing_covers) or '目前未見明顯缺口'}",
                f"現行保費占收入比：約 {(ip.current_premium / cp.income_monthly * 100) if cp.income_monthly else 0:.1f}%",
            ],
            "先處理直接影響家庭責任承接的保障項目，再擴充其他保障範圍。",
        ),
        _slide(
            "風險分析",
            "03 | 風險評估重點",
            f"目前風險分數為 {risk.score} 分，優先順序以 {' > '.join(risk.ranking[:3])} 為主。",
            ([f"風險發現：{finding}" for finding in risk.findings] if risk.findings else ["目前未偵測到明顯高風險缺口"]) + [f"分析摘要：{analysis.technical_summary[:180]}"],
            "本頁先建立風險共識，讓客戶理解為何後續建議是這樣排序。",
        ),
        _slide(
            "責任缺口",
            "04 | 家庭責任與缺口說明",
            f"若主要收入中斷，家庭支出與責任承接將是本案最需優先處理的風險。",
            [
                f"目前家庭責任：{cp.marital_status}，受扶養人 {cp.dependents} 人",
                f"每月固定支出約 {_money(cp.expenses_monthly)}",
                f"優先缺口：{top_recommendation}、{secondary_recommendation}",
            ],
            "以家庭責任承接、醫療支出與收入中斷風險三個面向重新校準保障。",
        ),
        _slide(
            "策略建議",
            "05 | 建議保障策略",
            f"本案建議採取『核心保障優先、次要保障分階段補強』的策略。",
            [
                f"第一優先：{top_recommendation}",
                f"第二優先：{secondary_recommendation}",
                f"第三優先：{risk.ranking[2] if len(risk.ranking) > 2 else secondary_recommendation}",
                f"先以每月 {_money(cp.budget_monthly)} 預算建立核心保護",
            ],
            "避免一次性堆高保費，改採分階段補強，讓保障與現金流同步穩定。",
        ),
        _slide(
            "預算規劃",
            "06 | 預算與執行節奏",
            f"本案可在現有預算框架下，規劃分階段的保障調整方案。",
            [
                f"可規劃預算：每月 {_money(cp.budget_monthly)}",
                f"現行保費：每月 {_money(ip.current_premium)}",
                f"可調整空間：約 {_money(max(cp.budget_monthly - ip.current_premium, 0))}",
                "第 1 階段：補齊最高優先缺口",
                "第 2 階段：補強重大醫療或收入保障",
                "第 3 階段：優化保障細節與長期檢視頻率",
            ],
            "先確認客戶可接受的月繳區間，再把方案拆成可執行節奏。",
        ),
        _slide(
            "顧問視角",
            "07 | 顧問建議摘要",
            analysis.advisor_narrative[:120],
            [
                analysis.advisor_narrative[:240],
                f"本次主軸建議：優先處理 {top_recommendation} 與 {secondary_recommendation}",
            ],
            "溝通時避免一次塞入太多商品細節，先讓客戶理解風險與順序。",
        ),
        _slide(
            "行動方案",
            "08 | 建議行動方案",
            "建議將本案推進到具體方案比較與核保資料準備階段。",
            analysis.recommendations,
            "每一項建議都需要回到商品條款、預算與核保條件做最終確認。",
        ),
        _slide(
            "會談引導",
            "09 | 談話重點與常見問題",
            "本頁作為顧問會談引導，幫助客戶理解優先順序與執行原因。",
            [
                f"為什麼 {top_recommendation} 是目前最優先",
                "如何在不影響現金流前提下逐步補強",
                "保障排序與家庭責任之間的關聯",
                f"簡報說明素材：{storyline[:220]}",
            ],
            "把焦點放在需求與風險，而不是先談單一商品。",
        ),
        _slide(
            "合規聲明",
            "10 | 合規重點與聲明",
            f"本提案屬保障規劃建議，實際承保與理賠仍以條款及核保結果為準。",
            [f"風險等級：{risk_level}", *disclosures, disclosure_text[:220]],
            "對外說明時務必避免保證型語句，並保留假設前提與限制條件。",
        ),
    ]

    presentation_id, presentation_url = create_google_slides_presentation(
        title=f"{cp.name_or_code} 客戶保障規劃建議",
        pages=pages,
        config=config,
    )

    return PresentationResult(
        pages=pages,
        google_presentation_id=presentation_id,
        google_presentation_url=presentation_url,
    )
