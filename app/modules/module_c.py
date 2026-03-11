from __future__ import annotations

from app.core.models import CaseData, ContentResult
from app.llm.prompt_registry import PromptRegistry
from app.llm.providers import LLMClient


def run_module_c(case: CaseData, llm: LLMClient, prompts: PromptRegistry) -> ContentResult:
    if not case.analysis_result:
        raise ValueError("Module C requires analysis_result from Module A")

    priority = case.analysis_result.risk_gap.ranking[0]
    insights = "; ".join(case.analysis_result.risk_gap.findings[:2])

    template = prompts.get("social_content_v1")
    prompt = template.format(case_id=case.case_id, priority=priority, insights=insights)
    llm_text = llm.complete(prompt)

    instagram_caption = (
        f"你以為保險是成本，其實是風險管理。這週我處理一個案例，"
        f"優先缺口是 {priority}。{insights}。"
        "如果你也想知道自己的保障盲點，留言『檢視』。"
    )

    carousel_copy = [
        "第1頁: 你的保單真的夠嗎?",
        f"第2頁: 常見缺口第一名 - {priority}",
        "第3頁: 3分鐘自我檢視清單",
        "第4頁: 下一步如何補齊保障",
    ]

    xiaohongshu_version = (
        f"最近在做保單健檢，發現很多家庭在 {priority} 上都低估風險。"
        "先用預算分層，再逐步補齊，比一次買滿更穩。"
    )

    short_video_script = (
        "開場: 你知道自己保單最容易缺哪一塊嗎?\n"
        f"主體: 這次案例的第一優先是 {priority}，原因是現有保障與家庭責任不匹配。\n"
        "結尾: 想拿到檢視框架，私訊我『風險地圖』。\n"
        f"AI draft notes:\n{llm_text[:500]}"
    )

    return ContentResult(
        instagram_caption=instagram_caption,
        carousel_copy=carousel_copy,
        xiaohongshu_version=xiaohongshu_version,
        short_video_script=short_video_script,
    )
