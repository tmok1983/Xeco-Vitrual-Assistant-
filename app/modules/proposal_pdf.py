from __future__ import annotations

from io import BytesIO
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Frame, Paragraph, SimpleDocTemplate

from app.core.models import CaseData

_FONT_NAME = "ProposalCJK"
_FONT_REGISTERED = False
_FONT_CANDIDATES = (
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/Supplemental/Songti.ttc",
)


def _register_cjk_font() -> str:
    global _FONT_REGISTERED
    if _FONT_REGISTERED:
        return _FONT_NAME

    for font_path in _FONT_CANDIDATES:
        if Path(font_path).exists():
            pdfmetrics.registerFont(TTFont(_FONT_NAME, font_path, subfontIndex=0))
            _FONT_REGISTERED = True
            return _FONT_NAME

    raise FileNotFoundError("No supported CJK system font found for PDF generation")


def _build_styles(font_name: str) -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "section": ParagraphStyle(
            "section",
            parent=base["Normal"],
            fontName=font_name,
            fontSize=9,
            leading=11,
            textColor=colors.HexColor("#B45309"),
            alignment=TA_LEFT,
        ),
        "title": ParagraphStyle(
            "title",
            parent=base["Heading1"],
            fontName=font_name,
            fontSize=24,
            leading=29,
            textColor=colors.HexColor("#4B5563"),
        ),
        "key": ParagraphStyle(
            "key",
            parent=base["Normal"],
            fontName=font_name,
            fontSize=16,
            leading=22,
            textColor=colors.HexColor("#1F2937"),
        ),
        "heading": ParagraphStyle(
            "heading",
            parent=base["Normal"],
            fontName=font_name,
            fontSize=10,
            leading=12,
            textColor=colors.HexColor("#6B7280"),
        ),
        "body": ParagraphStyle(
            "body",
            parent=base["Normal"],
            fontName=font_name,
            fontSize=12,
            leading=17,
            textColor=colors.HexColor("#4B5563"),
        ),
        "footer": ParagraphStyle(
            "footer",
            parent=base["Normal"],
            fontName=font_name,
            fontSize=9,
            leading=10,
            textColor=colors.HexColor("#6B7280"),
        ),
        "icon": ParagraphStyle(
            "icon",
            parent=base["Normal"],
            fontName=font_name,
            fontSize=28,
            leading=30,
            textColor=colors.HexColor("#B45309"),
            alignment=TA_CENTER,
        ),
    }


def _limit_lines(value: str | None, limit: int = 4) -> list[str]:
    if not value:
        return []
    lines = [line.strip(" -\u2022") for line in value.splitlines() if line.strip()]
    return lines[:limit]


def build_proposal_pdf(case: CaseData) -> bytes:
    if not case.presentation_result or not case.presentation_result.pages:
        raise ValueError("Presentation not found for this case")

    font_name = _register_cjk_font()
    styles = _build_styles(font_name)
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        leftMargin=0,
        rightMargin=0,
        topMargin=0,
        bottomMargin=0,
        title=f"簡報檢視 - {case.case_id}",
        author="ET Consulting",
    )
    page_width, page_height = landscape(A4)

    def draw_page(canvas, _doc) -> None:
        page_no = canvas.getPageNumber()
        slide = case.presentation_result.pages[page_no - 1]
        section = slide.get("section", "")
        title = slide.get("title", f"第 {page_no} 頁")
        key_message = slide.get("key_message", "")
        support_lines = _limit_lines(slide.get("supporting_points") or slide.get("content"), limit=4)
        advice_lines = _limit_lines(slide.get("advisor_recommendation"), limit=3)
        visual_icon = slide.get("visual_icon", "📘")

        support_html = "<br/>".join(f"- {line}" for line in support_lines) or "—"
        advice_html = "<br/>".join(f"- {line}" for line in advice_lines) or "—"

        canvas.saveState()
        canvas.setFillColor(colors.white)
        canvas.rect(0, 0, page_width, page_height, fill=1, stroke=0)
        canvas.setFillColor(colors.HexColor("#B45309"))
        canvas.rect(0, page_height - 9 * mm, page_width, 9 * mm, fill=1, stroke=0)
        canvas.setStrokeColor(colors.HexColor("#9CA3AF"))
        canvas.setLineWidth(0.6)
        canvas.rect(18 * mm, page_height - 42 * mm, page_width - 36 * mm, 24 * mm, fill=0, stroke=1)

        main_frame = Frame(22 * mm, 34 * mm, 112 * mm, page_height - 88 * mm, showBoundary=0)
        right_frame = Frame(144 * mm, 54 * mm, 48 * mm, 52 * mm, showBoundary=0)
        icon_frame = Frame(page_width - 42 * mm, 44 * mm, 18 * mm, 20 * mm, showBoundary=0)
        footer_left = Frame(18 * mm, 8 * mm, 100 * mm, 8 * mm, showBoundary=0)
        footer_right = Frame(page_width - 64 * mm, 8 * mm, 46 * mm, 8 * mm, showBoundary=0)

        main_story = [
            Paragraph(section or "&nbsp;", styles["section"]),
            Paragraph(f"{page_no:02d} | {title}", styles["title"]),
            Paragraph(key_message or "&nbsp;", styles["key"]),
            Paragraph("分析重點", styles["heading"]),
            Paragraph(support_html, styles["body"]),
        ]
        right_story = [
            Paragraph("顧問建議", styles["heading"]),
            Paragraph(advice_html, styles["body"]),
        ]
        icon_story = [Paragraph(visual_icon, styles["icon"])]
        footer_left_story = [Paragraph("ET Consulting | Insurance Advisory Proposal", styles["footer"])]
        footer_right_story = [Paragraph(f"{case.case_id} | 第 {page_no} 頁", styles["footer"])]

        main_frame.addFromList(main_story, canvas)
        right_frame.addFromList(right_story, canvas)
        icon_frame.addFromList(icon_story, canvas)
        footer_left.addFromList(footer_left_story, canvas)
        footer_right.addFromList(footer_right_story, canvas)
        canvas.restoreState()

    story = [Paragraph("&nbsp;", styles["body"]) for _ in case.presentation_result.pages]
    doc.build(story, onFirstPage=draw_page, onLaterPages=draw_page)
    return buffer.getvalue()
