from __future__ import annotations

from datetime import datetime, timezone
import json
import re
import ssl
from urllib import request

import certifi

from app.core.config import AppConfig
from app.ev_support.faq_store import LocalFAQRetriever
from app.ev_support.media_store import LocalMediaStore, StoredMedia
from app.ev_support.models import (
    EVSupportMessage,
    EVSupportRequest,
    EVSupportResponse,
    EVSupportSession,
    LinePushRequest,
    LineReplyRequest,
)
from app.ev_support.repository import InMemoryEVSupportRepository
from app.llm.providers import LLMClient


THAI_SYSTEM_PROMPT = """คุณคือผู้ช่วยบริการลูกค้าสำหรับสถานีชาร์จรถ EV ในประเทศไทย
เป้าหมายคือช่วยลูกค้าอย่างสุภาพ กระชับ และใช้งานได้จริง
ให้ตอบเป็นภาษาไทยเสมอ
ถ้ายังไม่มีข้อมูลเพียงพอ ให้ถามคำถามสั้น ๆ เพิ่มเติม
ถ้าเป็นเรื่องฉุกเฉินด้านความปลอดภัย ให้แนะนำให้หยุดใช้งานเครื่องชาร์จทันทีและติดต่อเจ้าหน้าที่
อย่าสร้างข้อมูลราคา โปรโมชั่น หรือนโยบายที่ไม่ได้อยู่ในข้อมูลอ้างอิง
"""

ENGLISH_SYSTEM_PROMPT = """You are a customer support assistant for an EV charging service in Thailand.
Be polite, concise, and operationally useful.
Reply in English unless the system is explicitly switched to Thai.
If details are missing, ask one or two short follow-up questions.
For safety incidents, instruct the customer to stop using the charger immediately and contact support staff.
Do not invent pricing, promotions, or policies that are not in the provided knowledge.
"""

CHINESE_SYSTEM_PROMPT = """你是泰國電動車充電服務的客服助理。
請以繁體中文回覆，語氣清楚、簡潔、實用。
如資料不足，先提出一至兩個簡短追問。
如涉及安全事故，請立即建議停止使用充電設備並聯絡現場或客服人員。
不要捏造價格、優惠或政策內容。
"""

FAQ_KNOWLEDGE = {
    "pricing": "ค่าบริการชาร์จขึ้นอยู่กับสถานีและช่วงเวลา โดยปกติจะแสดงในแอปก่อนเริ่มชาร์จ",
    "start_charge": "วิธีเริ่มชาร์จ: 1. จอดรถและดับเครื่อง 2. เปิดแอปหรือสแกน QR 3. เลือกหัวชาร์จ 4. เสียบหัวชาร์จให้แน่น 5. กดยืนยันเริ่มชาร์จ",
    "payment": "รองรับการชำระเงินผ่านบัตรที่ผูกไว้ในแอปหรือช่องทางที่สถานีกำหนด",
    "connector_issue": "หากเสียบหัวชาร์จแล้วไม่เริ่มทำงาน ให้ตรวจสอบว่าปิดประตูรถสนิท ปลดล็อกรถ และเสียบหัวชาร์จจนสุด",
    "station_offline": "หากสถานีขึ้นออฟไลน์ แนะนำให้ลองเปลี่ยนหัวชาร์จหรือเลือกสถานีใกล้เคียงในแอป",
    "refund": "กรณีถูกตัดเงินแต่ชาร์จไม่สำเร็จ ให้แจ้งหมายเลขสถานี เวลาเกิดเหตุ และ 4 หลักท้ายของรายการชำระเงินเพื่อให้เจ้าหน้าที่ตรวจสอบ",
    "emergency": "หากมีกลิ่นไหม้ ควัน หรืออุปกรณ์ร้อนผิดปกติ ให้หยุดใช้งานทันที ออกจากพื้นที่อย่างปลอดภัย และติดต่อเจ้าหน้าที่",
}

FAQ_KNOWLEDGE_EN = {
    "pricing": "Charging fees depend on the station and time slot. The price should be shown in the app before charging starts.",
    "start_charge": "To start charging: 1. Park safely and turn off the vehicle. 2. Open the app or scan the QR code. 3. Select the charger. 4. Plug in firmly. 5. Confirm start charging.",
    "payment": "Payment is supported through the card or payment method linked in the app, or other options defined by the station.",
    "connector_issue": "If the connector is plugged in but charging does not start, check that the car is ready to charge, the connector is fully inserted, and the vehicle is not locked in a way that blocks charging.",
    "station_offline": "If the station shows offline, try another connector or select a nearby station in the app.",
    "refund": "If you were charged but charging did not start, please share the station ID, incident time, and the last 4 digits of the payment reference so support can investigate.",
    "emergency": "If you notice smoke, a burning smell, or abnormal heat, stop using the charger immediately, move to a safe area, and contact station staff.",
}

FAQ_KNOWLEDGE_ZH = {
    "pricing": "充電費用會因充電站及時段而有所不同，實際價格請以 App 顯示為準。",
    "start_charge": "開始充電：1. 停車並熄火 2. 開啟 App 或掃描 QR Code 3. 選擇充電器 4. 插穩充電槍 5. 按「開始充電」。",
    "payment": "付款方式以 App 已綁定的付款工具或充電站支援方式為準。",
    "connector_issue": "如已插入充電槍但未開始充電，請確認車輛可進入充電狀態、充電槍已插穩，以及車輛設定沒有阻止充電。",
    "station_offline": "如充電站顯示離線，建議改用其他槍口或在 App 內查看附近可用站點。",
    "refund": "如已扣款但未成功充電，請提供站點編號、發生時間及付款記錄末 4 位，方便客服跟進。",
    "emergency": "如有冒煙、燒焦味或設備異常發熱，請立即停止使用，離開設備附近並聯絡現場或客服人員。",
}

SCREENSHOT_GUIDANCE_EN = {
    "charging_error": (
        "The screenshot shows a charging error and the charger reports that the car detected a problem. "
        "Please stop the session, unplug the connector safely, wait about 10 seconds, restart the vehicle, "
        "then reconnect and try again. If the same error appears again, try another charger and send the station ID and vehicle model to support."
    ),
}

SCREENSHOT_GUIDANCE_ZH = {
    "charging_error": (
        "截圖顯示充電錯誤，且畫面提示車輛偵測到問題。請先停止充電、在安全情況下拔槍、等待約 10 秒，"
        "重新啟動車輛後再插槍重試。若再次出現相同錯誤，請改用其他充電器，並提供站點編號及車型給客服跟進。"
    ),
}

SCREENSHOT_GUIDANCE_TH = {
    "charging_error": (
        "จากภาพ หน้าจอแสดงข้อผิดพลาดในการชาร์จ และระบบระบุว่ารถตรวจพบปัญหา "
        "กรุณาหยุดการชาร์จ ถอดหัวชาร์จอย่างปลอดภัย รอประมาณ 10 วินาที แล้วสตาร์ทรถใหม่ก่อนลองเสียบและเริ่มชาร์จอีกครั้ง "
        "หากยังขึ้นข้อความเดิม ให้ลองเปลี่ยนหัวชาร์จหรือเปลี่ยนตู้ และส่งหมายเลขสถานีพร้อมรุ่นรถให้ทีมงานตรวจสอบต่อ"
    ),
}

THAI_RE = re.compile(r"[\u0E00-\u0E7F]")
CHINESE_RE = re.compile(r"[\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF]")
LATIN_RE = re.compile(r"[A-Za-z]")
HUMAN_HANDOFF_PATTERNS = [
    "human",
    "agent",
    "staff",
    "operator",
    "customer support",
    "talk to human",
    "need human",
    "customer service",
    "cs team",
    "真人",
    "人工",
    "客服",
    "搵客服",
    "轉人工",
    "เจ้าหน้าที่",
    "คุยกับคน",
    "คนจริง",
    "ติดต่อเจ้าหน้าที่",
]
HANDOFF_CLOSE_PATTERNS = [
    "close handoff",
    "resume bot",
    "resume chatbot",
    "handoff done",
    "結束人工",
    "恢復機器人",
    "ปิดการส่งต่อ",
    "กลับสู่บอท",
]

INTENT_TO_FAQ_INTENTS = {
    "start_charge": ["charging_not_starting", "charging_started"],
    "connector_issue": ["charging_cable_locked", "manual_release", "charging_not_starting"],
    "refund": ["refund_request"],
    "emergency": ["safety_requirements", "emergency_stop_limit"],
    "charging_locations": ["charging_locations"],
}


class EVSupportService:
    def __init__(
        self,
        faq_retriever: LocalFAQRetriever,
        media_store: LocalMediaStore,
        repo: InMemoryEVSupportRepository,
        llm: LLMClient,
        config: AppConfig,
    ) -> None:
        self.faq_retriever = faq_retriever
        self.media_store = media_store
        self.repo = repo
        self.llm = llm
        self.config = config

    def _detect_intent(self, text: str) -> str:
        lowered = text.lower()
        if any(token in text for token in ["ควัน", "ไหม้", "ไฟช็อต", "ร้อนผิดปกติ"]):
            return "emergency"
        if any(token in text for token in ["充電站", "哪裡", "在哪里", "喺邊", "邊度", "附近充電", "充電地方"]):
            return "charging_locations"
        if any(token in text for token in ["สถานีชาร์จ", "ใกล้ฉัน", "อยู่ที่ไหน"]):
            return "charging_locations"
        if any(phrase in lowered for phrase in ["charging station", "charging stations", "nearest station", "nearby station"]):
            return "charging_locations"
        if any(token in text for token in ["退款", "退錢", "已扣款", "扣款", "未成功充電", "扣咗錢", "收咗錢"]):
            return "refund"
        if any(phrase in lowered for phrase in ["charged but", "payment failed", "refund", "billing", "card charged"]):
            return "refund"
        if any(token in text for token in ["คืนเงิน", "refund", "ตัดเงิน", "ชำระเงิน", "payment"]):
            return "refund"
        if any(token in text for token in ["充唔到電", "充不到電", "無法充電", "掃 code", "掃code", "掃 qr", "掃qr"]):
            return "start_charge"
        if any(token in text for token in ["เริ่มชาร์จ", "start", "สแกน", "qr"]):
            return "start_charge"
        if any(token in text for token in ["ราคา", "ค่าบริการ", "โปร", "promotion", "ราคาเท่าไร"]):
            return "pricing"
        if any(token in text for token in ["หัวชาร์จ", "เสียบ", "ไม่เข้า", "ไม่เริ่ม", "charger"]):
            return "connector_issue"
        if any(token in text for token in ["ออฟไลน์", "offline", "สถานีเสีย", "ใช้ไม่ได้"]):
            return "station_offline"
        if "payment" in lowered:
            return "payment"
        return "general_support"

    def _resolve_language(self, message_text: str, requested_language: str | None) -> str:
        has_thai = bool(THAI_RE.search(message_text))
        has_chinese = bool(CHINESE_RE.search(message_text))
        has_latin = bool(LATIN_RE.search(message_text))

        if has_chinese:
            return "zh-HK"
        if has_thai:
            return "th-TH"
        if has_latin:
            return "en-US"
        if requested_language and requested_language.startswith("th"):
            return "th-TH"
        return self.config.ev_default_language or "en-US"

    def detect_language(self, message_text: str, requested_language: str | None = None) -> str:
        return self._resolve_language(message_text, requested_language)

    def _knowledge_base(self, language: str) -> dict[str, str]:
        if language.startswith("th"):
            return FAQ_KNOWLEDGE
        if language.startswith("zh"):
            return FAQ_KNOWLEDGE_ZH
        return FAQ_KNOWLEDGE_EN

    def _retrieve_faq_context(self, message_text: str, language: str) -> tuple[str, list[str], str | None, str | None, int]:
        hits = self.faq_retriever.retrieve(message_text, language=language, top_k=3)
        if not hits:
            return "", [], None, None, 0

        context_lines = []
        hit_ids: list[str] = []
        for hit in hits:
            context_lines.append(f"- Q: {hit.entry.question}\n  A: {hit.entry.answer}")
            hit_ids.append(hit.entry.doc_id)
        return "\n".join(context_lines), hit_ids, hits[0].entry.answer, hits[0].entry.intent, hits[0].score

    def _should_reply_from_faq(self, intent: str, top_faq_intent: str | None, top_faq_score: int) -> bool:
        if not top_faq_intent:
            return False
        if intent == "general_support":
            return top_faq_score >= 18
        expected_faq_intents = INTENT_TO_FAQ_INTENTS.get(intent)
        if not expected_faq_intents:
            return False
        return top_faq_intent in expected_faq_intents

    def _load_or_create_session(self, session_id: str, user_id: str, channel: str, language: str) -> EVSupportSession:
        session = self.repo.get(session_id)
        now = datetime.now(timezone.utc)
        if session:
            return session
        session = EVSupportSession(
            session_id=session_id,
            channel_user_id=user_id,
            source_channel=channel,
            language=language,
            created_at=now,
            updated_at=now,
        )
        self.repo.save(session)
        return session

    def is_human_handoff_requested(self, text: str) -> bool:
        lowered = text.lower()
        if re.search(r"(^|\W)cs(\W|$)", lowered):
            return True
        return any(pattern in lowered or pattern in text for pattern in HUMAN_HANDOFF_PATTERNS)

    def activate_human_handoff(self, session_id: str, user_id: str, channel: str, language: str, reason: str) -> EVSupportSession:
        session = self._load_or_create_session(session_id, user_id, channel, language)
        session.human_handoff_active = True
        session.handoff_requested_at = datetime.now(timezone.utc)
        session.handoff_reason = reason
        self.repo.save(session)
        return session

    def close_human_handoff(self, session_id: str) -> EVSupportSession | None:
        session = self.repo.get(session_id)
        if not session:
            return None
        session.human_handoff_active = False
        session.handoff_reason = None
        self.repo.save(session)
        return session

    def handoff_reply_text(self, language: str) -> str:
        if language.startswith("th"):
            return "รับคำขอเรียบร้อยแล้วครับ ทีมงานจะได้รับการแจ้งเตือนและติดต่อคุณผ่าน LINE โดยเร็วที่สุด"
        if language.startswith("zh"):
            return "已收到你的人工支援要求，客服團隊會收到通知並盡快透過 LINE 跟進你。"
        return "Your request for human support has been received. The support team has been notified and will follow up with you on LINE as soon as possible."

    def handoff_followup_text(self, language: str) -> str:
        if language.startswith("th"):
            return "ทีมงานกำลังตรวจสอบอยู่ครับ ข้อความล่าสุดของคุณถูกส่งต่อให้เจ้าหน้าที่แล้ว"
        if language.startswith("zh"):
            return "客服團隊正在跟進，你的最新訊息已轉交給相關同事。"
        return "The support team is reviewing your case. Your latest message has been forwarded to them."

    def bot_resumed_customer_text(self, language: str) -> str:
        if language.startswith("th"):
            return "ทีมงานได้ปิดการส่งต่อแล้ว ตอนนี้บอทกลับมาช่วยตอบคำถามต่อได้ตามปกติครับ"
        if language.startswith("zh"):
            return "客服團隊已結束人工跟進，現在機器人會恢復為你提供即時協助。"
        return "The human support handoff has been closed. The chatbot is now active again and can continue assisting you."

    def handoff_closed_group_text(self, session_id: str) -> str:
        return f"Handoff closed for {session_id}. The chatbot is active again for this customer."

    def handoff_close_help_text(self) -> str:
        return (
            "To reactivate the bot, send one of these commands in this support group:\n"
            "close handoff line:<session_id>\n"
            "resume bot line:<session_id>"
        )

    def parse_handoff_close_command(self, text: str) -> str | None:
        lowered = text.lower().strip()
        if not any(pattern in lowered or pattern in text for pattern in HANDOFF_CLOSE_PATTERNS):
            return None
        match = re.search(r"(line:[A-Za-z0-9]+)", text)
        if match:
            return match.group(1)
        return None

    def is_customer_handoff_close_request(self, text: str) -> bool:
        lowered = text.lower().strip()
        return any(pattern in lowered or pattern in text for pattern in HANDOFF_CLOSE_PATTERNS)

    def support_group_alert_text(
        self,
        session_id: str,
        user_id: str,
        language: str,
        customer_text: str,
        reason: str,
        media_summary: str | None = None,
    ) -> str:
        parts = [
            "XECO chatbot escalation",
            "",
            f"Customer message: {customer_text or '[attachment]'}",
            f"Language: {language}",
            f"Reason: {reason}",
            "",
            f"Session ID: {session_id}",
            f"User ID: {user_id}",
        ]
        if media_summary:
            parts.extend(["", f"Media summary: {media_summary[:500]}"])
        return "\n".join(parts)

    def _build_prompt(self, session: EVSupportSession, req: EVSupportRequest, intent: str, knowledge: str, language: str) -> str:
        history = "\n".join(f"{message.role}: {message.text}" for message in session.messages[-6:])
        if language.startswith("th"):
            system_prompt = THAI_SYSTEM_PROMPT
        elif language.startswith("zh"):
            system_prompt = CHINESE_SYSTEM_PROMPT
        else:
            system_prompt = ENGLISH_SYSTEM_PROMPT
        closing = (
            "ตอบลูกค้าเป็นภาษาไทยไม่เกิน 6 บรรทัด พร้อมขั้นตอนถัดไปที่ชัดเจน"
            if language.startswith("th")
            else "請用繁體中文回覆，6 行內清楚說明下一步。"
            if language.startswith("zh")
            else "Reply in English in no more than 6 lines with a clear next step."
        )
        return (
            f"{system_prompt}\n"
            f"ข้อมูลอ้างอิง:\n{knowledge}\n\n"
            f"intent={intent}\n"
            f"language={language}\n"
            f"session_id={req.session_id}\n"
            f"user_message={req.message_text}\n"
            f"context={req.context.model_dump_json(exclude_none=True)}\n"
            f"history:\n{history}\n\n"
            f"{closing}"
        )

    def _fallback_reply(self, intent: str, message_text: str, language: str, retrieved_answer: str | None = None) -> str:
        if retrieved_answer:
            return retrieved_answer
        if language.startswith("zh"):
            if intent == "emergency":
                return (
                    "請立即停止使用充電設備並先遠離裝置。\n"
                    "如有冒煙、燒焦味或異常發熱，請立即聯絡現場人員或客服。\n"
                    "你可以把站點編號和發生時間發給我，我會協助你整理後續處理。"
                )
            if intent in FAQ_KNOWLEDGE_ZH:
                return f"{FAQ_KNOWLEDGE_ZH[intent]}\n如方便，請再提供站點編號或截圖，我可以幫你確認下一步。"
            return (
                "我可以幫你查一下。\n"
                "請再提供一些資料，例如站點編號、遇到的情況，以及截圖（如有）。\n"
                "我會幫你整理下一步處理方式。"
            )
        if not language.startswith("th"):
            if intent == "emergency":
                return (
                    "Please stop using the charger immediately and move away from the equipment.\n"
                    "If there is smoke, a burning smell, or abnormal heat, contact station staff or emergency support right away.\n"
                    "Send the station ID and incident time and I will help route this to the team."
                )
            if intent in FAQ_KNOWLEDGE_EN:
                return f"{FAQ_KNOWLEDGE_EN[intent]}\nIf possible, send the station ID or a screenshot and I will help you check the next step."
            return (
                "I can help check this.\n"
                "Please share a few more details, such as the station ID, what happened, and a screenshot if available.\n"
                "I will summarize the next step for you."
            )
        if intent == "emergency":
            return (
                "ขอให้หยุดใช้งานตู้ชาร์จทันทีและออกห่างจากอุปกรณ์ก่อนนะครับ\n"
                "หากมีควัน กลิ่นไหม้ หรือความร้อนผิดปกติ กรุณาติดต่อเจ้าหน้าที่ฉุกเฉินของสถานีทันที\n"
                "ส่งหมายเลขสถานีและเวลาที่เกิดเหตุมาได้เลย เพื่อให้ทีมประสานงานต่อให้เร็วที่สุด"
            )
        if intent in FAQ_KNOWLEDGE:
            return f"{FAQ_KNOWLEDGE[intent]}\nหากสะดวก ส่งหมายเลขสถานีหรือภาพหน้าจอมาได้ ผมจะช่วยตรวจสอบต่อให้ครับ"
        return (
            "ยินดีช่วยตรวจสอบให้ครับ\n"
            "กรุณาส่งรายละเอียดเพิ่มอีกนิด เช่น หมายเลขสถานี ปัญหาที่พบ และภาพหน้าจอถ้ามี\n"
            "ผมจะสรุปขั้นตอนถัดไปให้เป็นภาษาไทยทันที"
        )

    def _guidance_from_media_analysis(self, analysis: str, language: str) -> str | None:
        lowered = analysis.lower()
        if "charging error" in lowered or "detected a problem" in lowered:
            if language.startswith("th"):
                return SCREENSHOT_GUIDANCE_TH["charging_error"]
            if language.startswith("zh"):
                return SCREENSHOT_GUIDANCE_ZH["charging_error"]
            return SCREENSHOT_GUIDANCE_EN["charging_error"]
        return None

    def generate_reply(self, req: EVSupportRequest) -> EVSupportResponse:
        session = self._load_or_create_session(req.session_id, req.user_id, req.channel, req.language)
        now = datetime.now(timezone.utc)

        language = self.detect_language(req.message_text, req.language)
        knowledge_map = self._knowledge_base(language)
        intent = self._detect_intent(req.message_text)
        retrieval_query = req.message_text
        if session.last_media_summary:
            retrieval_query = f"{req.message_text}\nMedia context: {session.last_media_summary}"

        faq_context, faq_hit_ids, top_faq_answer, top_faq_intent, top_faq_score = self._retrieve_faq_context(retrieval_query, language)
        if intent in INTENT_TO_FAQ_INTENTS:
            intent_hits = self.faq_retriever.retrieve_by_intents(
                retrieval_query,
                language=language,
                intents=INTENT_TO_FAQ_INTENTS[intent],
                top_k=3,
            )
            should_use_intent_hits = (
                not top_faq_answer
                or top_faq_intent not in INTENT_TO_FAQ_INTENTS[intent]
            )
            if intent_hits and should_use_intent_hits:
                faq_context = "\n".join(f"- Q: {hit.entry.question}\n  A: {hit.entry.answer}" for hit in intent_hits)
                faq_hit_ids = [hit.entry.doc_id for hit in intent_hits]
                top_faq_answer = intent_hits[0].entry.answer
                top_faq_intent = intent_hits[0].entry.intent
                top_faq_score = intent_hits[0].score
        knowledge = knowledge_map.get(
            intent,
            "ให้สอบถามข้อมูลเพิ่มเติมอย่างสุภาพและสรุปขั้นตอนถัดไป"
            if language.startswith("th")
            else "Ask for the missing details politely and summarize the next step.",
        )
        if faq_context:
            knowledge = f"{knowledge}\n\nRelevant FAQ context:\n{faq_context}"
        if session.last_media_summary:
            knowledge = f"{knowledge}\n\nRecent media summary:\n{session.last_media_summary}"
        session.messages.append(EVSupportMessage(role="user", text=req.message_text, at=now))
        trusted_faq = self._should_reply_from_faq(intent, top_faq_intent, top_faq_score)

        # FAQ-first mode: answer directly only when the FAQ hit matches the detected intent.
        if top_faq_answer and trusted_faq:
            reply_text = top_faq_answer
            session.messages.append(EVSupportMessage(role="assistant", text=reply_text, at=now))
            session.last_intent = intent
            session.last_station_id = req.context.station_id
            self.repo.save(session)
            return EVSupportResponse(
                session_id=req.session_id,
                user_id=req.user_id,
                detected_language=language,
                detected_intent=intent,
                reply_text=reply_text,
                escalate_to_human=False,
                knowledge_hits=faq_hit_ids,
            )

        raw_reply = ""
        if intent not in knowledge_map and self.config.llm_provider != "mock":
            prompt = self._build_prompt(session, req, intent, knowledge, language)
            raw_reply = self.llm.complete(prompt).strip()

        if not raw_reply or raw_reply.startswith("[MOCK_LLM]") or raw_reply.startswith("[LLM_FALLBACK:"):
            reply_text = self._fallback_reply(
                intent,
                req.message_text,
                language,
                retrieved_answer=top_faq_answer if trusted_faq else None,
            )
        else:
            reply_text = raw_reply

        session.messages.append(EVSupportMessage(role="assistant", text=reply_text, at=now))
        session.last_intent = intent
        session.last_station_id = req.context.station_id
        self.repo.save(session)

        return EVSupportResponse(
            session_id=req.session_id,
            user_id=req.user_id,
            detected_language=language,
            detected_intent=intent,
            reply_text=reply_text,
            escalate_to_human=intent == "emergency" or "เจ้าหน้าที่" in req.message_text,
            knowledge_hits=faq_hit_ids if trusted_faq else ([intent] if intent in knowledge_map else []),
        )

    def analyze_media(self, session_id: str, user_id: str, channel: str, language: str, message_type: str, file_path: str) -> str:
        session = self._load_or_create_session(session_id, user_id, channel, language)
        if message_type == "image":
            prompt = (
                "Read this EV charging screenshot and extract the exact visible error message or key UI text. "
                "Then summarize the likely issue in 2 short lines. Keep the output concise."
            )
            analysis = self.llm.analyze_image(file_path, prompt)
        else:
            analysis = self.llm.transcribe_audio(file_path)

        session.last_media_summary = analysis[:2000]
        session.last_media_type = message_type
        self.repo.save(session)
        return analysis

    def build_media_response(self, session_id: str, user_id: str, channel: str, language: str, message_type: str, analysis: str) -> EVSupportResponse:
        language = self.detect_language(analysis, language)
        if analysis.startswith("[LLM_FALLBACK:") or analysis.startswith("[MOCK_"):
            reply_text = self.build_media_acknowledgement(language, message_type)
            return EVSupportResponse(
                session_id=session_id,
                user_id=user_id,
                detected_language=language,
                detected_intent="media_received",
                reply_text=reply_text,
                knowledge_hits=[],
            )

        direct_guidance = self._guidance_from_media_analysis(analysis, language)
        if direct_guidance:
            return EVSupportResponse(
                session_id=session_id,
                user_id=user_id,
                detected_language=language,
                detected_intent="charger_error_from_media",
                reply_text=direct_guidance,
                knowledge_hits=[],
            )

        if language.startswith("th"):
            prompt_text = f"ผู้ใช้ส่ง{ 'ภาพหน้าจอ' if message_type == 'image' else 'คลิปเสียง' }มา ข้อมูลที่ตรวจพบคือ:\n{analysis}"
        elif language.startswith("zh"):
            prompt_text = f"使用者提供了{ '截圖' if message_type == 'image' else '語音訊息' }。可用內容如下：\n{analysis}"
        else:
            prompt_text = f"The user sent a { 'screenshot' if message_type == 'image' else 'voice note' }. Extracted content:\n{analysis}"

        return self.generate_reply(
            EVSupportRequest(
                session_id=session_id,
                user_id=user_id,
                message_text=prompt_text,
                channel=channel,
                language=language,
            )
        )

    def build_line_reply_request(self, reply_token: str, response: EVSupportResponse) -> LineReplyRequest:
        return LineReplyRequest(
            replyToken=reply_token,
            messages=[{"type": "text", "text": response.reply_text}],
        )

    def send_line_reply(self, payload: LineReplyRequest) -> tuple[int, dict]:
        if not self.config.line_channel_access_token:
            raise ValueError("LINE_CHANNEL_ACCESS_TOKEN is not configured")

        ssl_ctx = ssl.create_default_context(cafile=certifi.where())
        req = request.Request(
            self.config.line_reply_api_url,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.config.line_channel_access_token}",
            },
            data=json.dumps(payload.model_dump()).encode("utf-8"),
        )
        with request.urlopen(req, timeout=30, context=ssl_ctx) as resp:  # nosec B310
            raw = resp.read().decode("utf-8") or "{}"
            try:
                return resp.status, json.loads(raw)
            except json.JSONDecodeError:
                return resp.status, {"raw": raw}

    def send_line_push(self, payload: LinePushRequest) -> tuple[int, dict]:
        if not self.config.line_channel_access_token:
            raise ValueError("LINE_CHANNEL_ACCESS_TOKEN is not configured")

        ssl_ctx = ssl.create_default_context(cafile=certifi.where())
        req = request.Request(
            self.config.line_push_api_url,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.config.line_channel_access_token}",
            },
            data=json.dumps(payload.model_dump()).encode("utf-8"),
        )
        with request.urlopen(req, timeout=30, context=ssl_ctx) as resp:  # nosec B310
            raw = resp.read().decode("utf-8") or "{}"
            try:
                return resp.status, json.loads(raw)
            except json.JSONDecodeError:
                return resp.status, {"raw": raw}

    def fetch_and_store_line_media(self, session_id: str, message_id: str, message_type: str) -> StoredMedia:
        if not self.config.line_channel_access_token:
            raise ValueError("LINE_CHANNEL_ACCESS_TOKEN is not configured")

        ssl_ctx = ssl.create_default_context(cafile=certifi.where())
        req = request.Request(
            f"{self.config.line_content_api_base_url}/{message_id}/content",
            method="GET",
            headers={"Authorization": f"Bearer {self.config.line_channel_access_token}"},
        )
        with request.urlopen(req, timeout=30, context=ssl_ctx) as resp:  # nosec B310
            payload = resp.read()
            content_type = resp.headers.get("Content-Type", "application/octet-stream")

        return self.media_store.store(
            session_id=session_id,
            message_id=message_id,
            message_type=message_type,
            content_type=content_type,
            payload=payload,
        )

    def build_media_acknowledgement(self, language: str, message_type: str) -> str:
        if language.startswith("th"):
            noun = "ภาพหน้าจอ" if message_type == "image" else "คลิปเสียง"
            return (
                f"ได้รับ{noun}แล้วครับ\n"
                "ระบบบันทึกไฟล์ไว้เรียบร้อยแล้ว\n"
                "หากสะดวก กรุณาส่งข้อความอธิบายเพิ่มเติมเพื่อให้ทีมช่วยตรวจสอบได้เร็วขึ้น"
            )
        if language.startswith("zh"):
            noun = "截圖" if message_type == "image" else "語音訊息"
            return (
                f"已收到你的{noun}。\n"
                "系統已完成保存。\n"
                "如方便，請再補充一段文字說明問題，方便我們更快跟進。"
            )
        noun = "screenshot" if message_type == "image" else "audio clip"
        return (
            f"We received your {noun}.\n"
            "The file has been saved successfully.\n"
            "If possible, please send a short text description so we can help faster."
        )
