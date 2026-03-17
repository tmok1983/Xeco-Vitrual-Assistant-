from __future__ import annotations

import base64
import hashlib
import hmac
from html import escape
import json
import ssl
from functools import lru_cache
from urllib import request
from urllib.error import URLError
from pathlib import Path

import certifi
from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from app.core.config import AppConfig
from app.core.bootstrap import build_chat_log_repository, build_ev_support_service, build_service
from app.core.models import IntakeRequest, ReviewDecision
from app.ev_support.log_repository import ChatLogRecord
from app.ev_support.models import EVSupportRequest, LinePushRequest, LineWebhookPayload
from app.modules.google_slides import build_google_oauth_url, get_google_oauth_status, handle_google_oauth_callback
from app.modules.proposal_pdf import build_proposal_pdf

router = APIRouter(prefix="/api", tags=["insurance-assistant"])
config = AppConfig.from_env()


@lru_cache(maxsize=1)
def get_service():
    return build_service()


@lru_cache(maxsize=1)
def get_ev_support_service():
    return build_ev_support_service()


@lru_cache(maxsize=1)
def get_chat_log_repository():
    return build_chat_log_repository()


def require_api_key(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    key: str | None = Query(default=None),
) -> None:
    if not config.api_auth_key:
        return
    candidate = x_api_key or key
    if candidate != config.api_auth_key:
        raise HTTPException(status_code=401, detail="Invalid API key")


def _post_to_n8n_webhook(payload: dict) -> tuple[int, dict]:
    ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    req = request.Request(
        config.n8n_webhook_url,
        method="POST",
        headers={"Content-Type": "application/json"},
        data=json.dumps(payload).encode("utf-8"),
    )
    with request.urlopen(req, timeout=120, context=ssl_ctx) as resp:  # nosec B310
        raw = resp.read().decode("utf-8")
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            body = {"raw": raw}
        return resp.status, body


def _post_to_ev_n8n_webhook(payload: dict) -> tuple[int, dict]:
    if not config.ev_n8n_webhook_url:
        raise ValueError("EV_N8N_WEBHOOK_URL is not configured")

    ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    req = request.Request(
        config.ev_n8n_webhook_url,
        method="POST",
        headers={"Content-Type": "application/json"},
        data=json.dumps(payload).encode("utf-8"),
    )
    with request.urlopen(req, timeout=120, context=ssl_ctx) as resp:  # nosec B310
        raw = resp.read().decode("utf-8") or "{}"
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            body = {"raw": raw}
        return resp.status, body


def _verify_line_signature(raw_body: bytes, signature: str | None) -> None:
    if not config.line_channel_secret:
        return
    if not signature:
        raise HTTPException(status_code=401, detail="Missing LINE signature")

    digest = hmac.new(
        config.line_channel_secret.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).digest()
    expected = base64.b64encode(digest).decode("utf-8")
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=401, detail="Invalid LINE signature")


def _format_case_output(case) -> dict:
    analysis = case.analysis_result
    risk = analysis.risk_gap if analysis else None
    presentation = case.presentation_result
    compliance = case.compliance_report
    return {
        "case_id": case.case_id,
        "status": case.status.value,
        "review_status": case.review_status.value,
        "module_a_report": {
            "summary": analysis.summary if analysis else None,
            "technical_summary": analysis.technical_summary if analysis else None,
            "advisor_narrative": analysis.advisor_narrative if analysis else None,
            "recommendations": analysis.recommendations if analysis else [],
            "risk_score": risk.score if risk else None,
            "risk_ranking": risk.ranking if risk else [],
            "risk_findings": risk.findings if risk else [],
        },
        "compliance_report": {
            "risk_level": compliance.risk_level.value if compliance else None,
            "approval_recommendation": compliance.approval_recommendation if compliance else None,
            "required_disclosures": compliance.required_disclosures if compliance else [],
            "issues": [issue.model_dump(mode="json") for issue in compliance.issues] if compliance else [],
            "safe_summary": compliance.safe_summary if compliance else None,
        },
        "presentation": {
            "page_count": len(presentation.pages) if presentation and presentation.pages else 0,
            "pages": presentation.pages if presentation else [],
            "google_presentation_id": presentation.google_presentation_id if presentation else None,
            "google_presentation_url": presentation.google_presentation_url if presentation else None,
        },
        "raw_case": case.model_dump(mode="json"),
    }


def _run_local_workbench(req: IntakeRequest) -> tuple[int, dict]:
    service = get_service()
    result = service.intake_and_orchestrate(req)
    if "error" in result:
        err = result["error"]
        return 502, {
            "dead_letter": True,
            "status": "failed",
            "stage": "local_backend",
            "error": err.model_dump(mode="json"),
        }

    case = result["case"]
    compliance = case.compliance_report
    risk_level = compliance.risk_level.value if compliance else "low"
    approved = risk_level != "high"
    reason = (
        f"Manual review required (high risk): {'; '.join(f'{i.claim}:{i.reason}' for i in compliance.issues) or 'high risk flagged'}"
        if not approved
        else (
            f"Approved with disclosures: {' | '.join(compliance.required_disclosures)}"
            if risk_level == "medium"
            else "Auto-approved: low compliance risk"
        )
    )
    decision = ReviewDecision(
        approved=approved,
        reviewer="local-compliance-bot",
        reason=reason,
    )
    reviewed_case = service.review_case(case.case_id, decision)
    payload = _format_case_output(reviewed_case)
    payload["execution_mode"] = "local_fallback"
    payload["notification"] = result["notification"]
    return 200, payload


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ev-support/health")
def ev_support_health() -> dict[str, str]:
    return {"status": "ok", "service": "ev-support"}


@router.post("/ev-support/respond")
def ev_support_respond(req: EVSupportRequest) -> dict:
    service = get_ev_support_service()
    detected_language = service.detect_language(req.message_text, req.language)
    repo = get_chat_log_repository()
    repo.log_message(
        ChatLogRecord(
            session_id=req.session_id,
            user_id=req.user_id,
            channel=req.channel,
            direction="inbound",
            message_type="text",
            language=detected_language,
            detected_intent=None,
            message_text=req.message_text,
            analysis_text=None,
            knowledge_hits=[],
            status="received",
        )
    )
    response = service.generate_reply(req)
    repo.log_message(
        ChatLogRecord(
            session_id=response.session_id,
            user_id=response.user_id,
            channel=req.channel,
            direction="outbound",
            message_type="text",
            language=response.detected_language,
            detected_intent=response.detected_intent,
            message_text=response.reply_text,
            analysis_text=None,
            knowledge_hits=response.knowledge_hits,
            status="generated",
        )
    )
    return response.model_dump(mode="json")


@router.post("/ev-support/debug/respond", dependencies=[Depends(require_api_key)])
def ev_support_debug_respond(req: EVSupportRequest) -> dict:
    service = get_ev_support_service()
    detected_language = service.detect_language(req.message_text, req.language)
    response = service.generate_reply(req)
    return {
        "request": req.model_dump(mode="json"),
        "detected_language_before_reply": detected_language,
        "response": response.model_dump(mode="json"),
    }


@router.get("/ev-support/debug/faq", dependencies=[Depends(require_api_key)])
def ev_support_debug_faq() -> dict:
    service = get_ev_support_service()
    faq_path = Path(service.faq_retriever.faq_path)
    return {
        "faq_path": str(faq_path),
        "exists": faq_path.exists(),
        "entry_count": len(service.faq_retriever.entries),
    }


@router.get("/ev-support/debug/runtime", dependencies=[Depends(require_api_key)])
def ev_support_debug_runtime() -> dict:
    service = get_ev_support_service()
    media_dir = Path(service.media_store.root_dir)
    return {
        "llm_provider": config.llm_provider,
        "openai_api_key_present": bool(config.openai_api_key),
        "openai_model": config.openai_model,
        "openai_transcribe_model": config.openai_transcribe_model,
        "ev_media_storage_dir": str(media_dir),
        "ev_media_storage_exists": media_dir.exists(),
    }


@router.get("/ev-support/logs", dependencies=[Depends(require_api_key)])
def ev_support_logs(limit: int = Query(default=100, ge=1, le=500), session_id: str | None = None) -> dict:
    rows = get_chat_log_repository().list_messages(limit=limit, session_id=session_id)
    return {"items": rows, "count": len(rows)}


@router.get("/ev-support/logs/view", response_class=HTMLResponse, dependencies=[Depends(require_api_key)])
def ev_support_logs_view(
    limit: int = Query(default=100, ge=1, le=500),
    session_id: str | None = None,
    key: str | None = Query(default=None),
) -> HTMLResponse:
    rows = get_chat_log_repository().list_messages(limit=limit, session_id=session_id)

    table_rows: list[str] = []
    for row in rows:
        message_text = escape((row.get("message_text") or "").replace("\n", " "))
        analysis_text = escape((row.get("analysis_text") or "").replace("\n", " "))
        table_rows.append(
            "<tr>"
            f"<td>{row.get('id', '')}</td>"
            f"<td>{escape(str(row.get('created_at', '')))}</td>"
            f"<td>{escape(str(row.get('session_id', '')))}</td>"
            f"<td>{escape(str(row.get('direction', '')))}</td>"
            f"<td>{escape(str(row.get('message_type', '')))}</td>"
            f"<td>{escape(str(row.get('language', '')))}</td>"
            f"<td>{escape(str(row.get('status', '')))}</td>"
            f"<td>{escape(str(row.get('detected_intent', '') or ''))}</td>"
            f"<td title=\"{message_text}\">{message_text[:180]}</td>"
            f"<td title=\"{analysis_text}\">{analysis_text[:180]}</td>"
            "</tr>"
        )

    rows_html = "".join(table_rows) or "<tr><td colspan='10'>No logs found.</td></tr>"
    session_value = escape(session_id or "")
    key_value = escape(key or "")
    html_doc = f"""
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>EV Support Logs</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 24px; color: #111; }}
    h1 {{ margin: 0 0 16px; }}
    form {{ display: flex; gap: 12px; align-items: end; flex-wrap: wrap; margin-bottom: 16px; }}
    label {{ display: flex; flex-direction: column; gap: 6px; font-size: 13px; }}
    input {{ padding: 8px; min-width: 220px; }}
    button {{ padding: 9px 14px; cursor: pointer; }}
    table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
    th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; vertical-align: top; }}
    th {{ background: #f5f5f5; position: sticky; top: 0; }}
    .meta {{ margin-bottom: 12px; color: #666; font-size: 12px; }}
  </style>
</head>
<body>
  <h1>EV Support Conversation Logs</h1>
  <div class="meta">Showing {len(rows)} rows</div>
  <form method="get" action="/api/ev-support/logs/view">
    <label>Limit
      <input type="number" name="limit" min="1" max="500" value="{limit}" />
    </label>
    <label>Session ID
      <input type="text" name="session_id" value="{session_value}" placeholder="line:Uxxxx" />
    </label>
    <label>API Key
      <input type="text" name="key" value="{key_value}" placeholder="required if API_AUTH_KEY is set" />
    </label>
    <button type="submit">Refresh</button>
  </form>
  <table>
    <thead>
      <tr>
        <th>ID</th>
        <th>Created At</th>
        <th>Session ID</th>
        <th>Direction</th>
        <th>Type</th>
        <th>Language</th>
        <th>Status</th>
        <th>Intent</th>
        <th>Message</th>
        <th>Analysis</th>
      </tr>
    </thead>
    <tbody>
      {rows_html}
    </tbody>
  </table>
</body>
</html>
"""
    return HTMLResponse(content=html_doc)


@router.post("/ev-support/line/webhook")
async def ev_support_line_webhook(
    request_: Request,
    x_line_signature: str | None = Header(default=None, alias="X-Line-Signature"),
) -> dict:
    raw_body = await request_.body()
    _verify_line_signature(raw_body, x_line_signature)
    payload = LineWebhookPayload.model_validate_json(raw_body)

    results: list[dict] = []
    repo = get_chat_log_repository()
    for event in payload.events:
        if event.type != "message" or not event.message or event.message.type not in {"text", "image", "audio"}:
            results.append({"status": "ignored", "reason": "unsupported_event"})
            continue
        raw_text = event.message.text or ""
        if event.source.type != "user" or not event.source.userId:
            ev_support_service = get_ev_support_service()
            if (
                event.source.type == "group"
                and event.source.groupId
                and event.source.groupId == config.line_support_group_id
                and event.message.type == "text"
            ):
                target_session_id = ev_support_service.parse_handoff_close_command(raw_text)
                if target_session_id:
                    session = ev_support_service.close_human_handoff(target_session_id)
                    if session:
                        group_reply = ev_support_service.handoff_closed_group_text(target_session_id)
                        customer_notice = ev_support_service.bot_resumed_customer_text(session.language)

                        if config.line_channel_access_token:
                            ev_support_service.send_line_push(
                                LinePushRequest(
                                    to=session.channel_user_id,
                                    messages=[{"type": "text", "text": customer_notice}],
                                )
                            )
                            repo.log_message(
                                ChatLogRecord(
                                    session_id=target_session_id,
                                    user_id=session.channel_user_id,
                                    channel="line",
                                    direction="outbound",
                                    message_type="text",
                                    language=session.language,
                                    detected_intent="human_handoff_closed",
                                    message_text=customer_notice,
                                    analysis_text=None,
                                    knowledge_hits=[],
                                    status="delivered",
                                )
                            )

                        if event.replyToken and config.line_channel_access_token:
                            status, body = ev_support_service.send_line_reply(
                                ev_support_service.build_line_reply_request(
                                    event.replyToken,
                                    type("Resp", (), {"reply_text": group_reply}),
                                )
                            )
                            repo.log_message(
                                ChatLogRecord(
                                    session_id=target_session_id,
                                    user_id=event.source.groupId,
                                    channel="line",
                                    direction="outbound",
                                    message_type="text",
                                    language="en-US",
                                    detected_intent="human_handoff_closed",
                                    message_text=group_reply,
                                    analysis_text=None,
                                    knowledge_hits=[],
                                    status="delivered" if status < 400 else "delivery_failed",
                                    error_detail=None if status < 400 else json.dumps(body, ensure_ascii=False),
                                )
                            )

                        results.append({"status": "human_handoff_closed", "session_id": target_session_id})
                        continue

                    help_text = (
                        f"No active session found for {target_session_id}.\n"
                        f"{ev_support_service.handoff_close_help_text()}"
                    )
                    if event.replyToken and config.line_channel_access_token:
                        ev_support_service.send_line_reply(
                            ev_support_service.build_line_reply_request(
                                event.replyToken,
                                type("Resp", (), {"reply_text": help_text}),
                            )
                        )
                    results.append({"status": "handoff_close_not_found", "session_id": target_session_id})
                    continue

            source_identifier = event.source.groupId or event.source.roomId or "unknown-source"
            source_session_prefix = "line-group" if event.source.groupId else "line-room"
            repo.log_message(
                ChatLogRecord(
                    session_id=f"{source_session_prefix}:{source_identifier}",
                    user_id=source_identifier,
                    channel="line",
                    direction="inbound",
                    message_type=event.message.type,
                    language=config.ev_default_language,
                    detected_intent="group_event_capture",
                    message_text=raw_text or f"[{event.message.type} attachment]",
                    analysis_text=(
                        f"source_type={event.source.type}; "
                        f"group_id={event.source.groupId or ''}; "
                        f"room_id={event.source.roomId or ''}"
                    ),
                    knowledge_hits=[],
                    status="received_non_user_source",
                )
            )
            results.append(
                {
                    "status": "ignored",
                    "reason": "non_user_source",
                    "source_type": event.source.type,
                    "group_id": event.source.groupId,
                    "room_id": event.source.roomId,
                }
            )
            continue

        session_id = f"line:{event.source.userId}"
        ev_support_service = get_ev_support_service()
        detected_language = ev_support_service.detect_language(raw_text, config.ev_default_language)
        session = ev_support_service._load_or_create_session(session_id, event.source.userId, "line", detected_language)
        repo.log_message(
            ChatLogRecord(
                session_id=session_id,
                user_id=event.source.userId,
                channel="line",
                direction="inbound",
                message_type=event.message.type,
                language=detected_language,
                detected_intent=None,
                message_text=raw_text or f"[{event.message.type} attachment]",
                analysis_text=None,
                knowledge_hits=[],
                status="received",
            )
        )

        if event.message.type == "text" and ev_support_service.is_human_handoff_requested(raw_text):
            session = ev_support_service.activate_human_handoff(
                session_id=session_id,
                user_id=event.source.userId,
                channel="line",
                language=detected_language,
                reason="customer_requested_human",
            )
            customer_reply = ev_support_service.handoff_reply_text(detected_language)
            support_alert = ev_support_service.support_group_alert_text(
                session_id=session_id,
                user_id=event.source.userId,
                language=detected_language,
                customer_text=raw_text,
                reason="customer_requested_human",
                media_summary=session.last_media_summary,
            )

            if config.line_support_group_id:
                try:
                    ev_support_service.send_line_push(
                        LinePushRequest(
                            to=config.line_support_group_id,
                            messages=[{"type": "text", "text": support_alert}],
                        )
                    )
                except Exception as exc:
                    repo.log_message(
                        ChatLogRecord(
                            session_id=session_id,
                            user_id=event.source.userId,
                            channel="line",
                            direction="outbound",
                            message_type="text",
                            language=detected_language,
                            detected_intent="human_handoff",
                            message_text=support_alert,
                            analysis_text=None,
                            knowledge_hits=[],
                            status="support_group_notify_failed",
                            error_detail=str(exc),
                        )
                    )
                    results.append({"status": "support_group_notify_failed", "error": str(exc)})

            if event.replyToken and config.line_channel_access_token:
                status, body = ev_support_service.send_line_reply(
                    ev_support_service.build_line_reply_request(
                        event.replyToken,
                        type(
                            "Resp",
                            (),
                            {
                                "reply_text": customer_reply,
                            },
                        ),
                    )
                )
                repo.log_message(
                    ChatLogRecord(
                        session_id=session_id,
                        user_id=event.source.userId,
                        channel="line",
                        direction="outbound",
                        message_type="text",
                        language=detected_language,
                        detected_intent="human_handoff",
                        message_text=customer_reply,
                        analysis_text=None,
                        knowledge_hits=[],
                        status="delivered" if status < 400 else "delivery_failed",
                        error_detail=None if status < 400 else json.dumps(body, ensure_ascii=False),
                    )
                )
                results.append({"status": "human_handoff_started", "reply_text": customer_reply})
            else:
                results.append({"status": "human_handoff_started", "reply_text": customer_reply})
            continue

        if session.human_handoff_active and event.message.type == "text":
            if ev_support_service.is_customer_handoff_close_request(raw_text):
                closed_session = ev_support_service.close_human_handoff(session_id)
                resumed_reply = ev_support_service.bot_resumed_customer_text(detected_language)

                if event.replyToken and config.line_channel_access_token:
                    status, body = ev_support_service.send_line_reply(
                        ev_support_service.build_line_reply_request(
                            event.replyToken,
                            type("Resp", (), {"reply_text": resumed_reply}),
                        )
                    )
                    repo.log_message(
                        ChatLogRecord(
                            session_id=session_id,
                            user_id=event.source.userId,
                            channel="line",
                            direction="outbound",
                            message_type="text",
                            language=detected_language,
                            detected_intent="human_handoff_closed",
                            message_text=resumed_reply,
                            analysis_text=None,
                            knowledge_hits=[],
                            status="delivered" if status < 400 else "delivery_failed",
                            error_detail=None if status < 400 else json.dumps(body, ensure_ascii=False),
                        )
                    )
                else:
                    repo.log_message(
                        ChatLogRecord(
                            session_id=session_id,
                            user_id=event.source.userId,
                            channel="line",
                            direction="outbound",
                            message_type="text",
                            language=detected_language,
                            detected_intent="human_handoff_closed",
                            message_text=resumed_reply,
                            analysis_text=None,
                            knowledge_hits=[],
                            status="generated_locally",
                        )
                    )

                results.append(
                    {
                        "status": "human_handoff_closed",
                        "session_id": closed_session.session_id if closed_session else session_id,
                        "reply_text": resumed_reply,
                    }
                )
                continue

            if config.line_support_group_id:
                support_alert = ev_support_service.support_group_alert_text(
                    session_id=session_id,
                    user_id=event.source.userId,
                    language=detected_language,
                    customer_text=raw_text,
                    reason="handoff_followup",
                    media_summary=session.last_media_summary,
                )
                try:
                    ev_support_service.send_line_push(
                        LinePushRequest(
                            to=config.line_support_group_id,
                            messages=[{"type": "text", "text": support_alert}],
                        )
                    )
                except Exception as exc:
                    repo.log_message(
                        ChatLogRecord(
                            session_id=session_id,
                            user_id=event.source.userId,
                            channel="line",
                            direction="outbound",
                            message_type="text",
                            language=detected_language,
                            detected_intent="human_handoff_followup",
                            message_text=support_alert,
                            analysis_text=None,
                            knowledge_hits=[],
                            status="support_group_notify_failed",
                            error_detail=str(exc),
                        )
                    )
                    results.append({"status": "support_group_notify_failed", "error": str(exc)})

            followup_reply = ev_support_service.handoff_followup_text(detected_language)
            if event.replyToken and config.line_channel_access_token:
                status, body = ev_support_service.send_line_reply(
                    ev_support_service.build_line_reply_request(
                        event.replyToken,
                        type("Resp", (), {"reply_text": followup_reply}),
                    )
                )
                repo.log_message(
                    ChatLogRecord(
                        session_id=session_id,
                        user_id=event.source.userId,
                        channel="line",
                        direction="outbound",
                        message_type="text",
                        language=detected_language,
                        detected_intent="human_handoff_followup",
                        message_text=followup_reply,
                        analysis_text=None,
                        knowledge_hits=[],
                        status="delivered" if status < 400 else "delivery_failed",
                        error_detail=None if status < 400 else json.dumps(body, ensure_ascii=False),
                    )
                )
            results.append({"status": "human_handoff_active"})
            continue

        if event.message.type in {"image", "audio"}:
            try:
                stored = ev_support_service.fetch_and_store_line_media(
                    session_id=session_id,
                    message_id=event.message.id,
                    message_type=event.message.type,
                )
            except Exception as exc:
                repo.log_message(
                    ChatLogRecord(
                        session_id=session_id,
                        user_id=event.source.userId,
                        channel="line",
                        direction="outbound",
                        message_type=event.message.type,
                        language=detected_language,
                        detected_intent="media_received",
                        message_text="",
                        analysis_text=None,
                        knowledge_hits=[],
                        status="media_download_failed",
                        error_detail=str(exc),
                    )
                )
                raise

            analysis_text = ev_support_service.analyze_media(
                session_id=session_id,
                user_id=event.source.userId,
                channel="line",
                language=detected_language,
                message_type=event.message.type,
                file_path=stored.file_path,
            )
            media_language = ev_support_service.detect_language(analysis_text, detected_language)

            if ev_support_service.is_human_handoff_requested(analysis_text):
                session = ev_support_service.activate_human_handoff(
                    session_id=session_id,
                    user_id=event.source.userId,
                    channel="line",
                    language=media_language,
                    reason="customer_requested_human_via_media",
                )
                customer_reply = ev_support_service.handoff_reply_text(media_language)
                support_alert = ev_support_service.support_group_alert_text(
                    session_id=session_id,
                    user_id=event.source.userId,
                    language=media_language,
                    customer_text=raw_text,
                    reason="customer_requested_human_via_media",
                    media_summary=analysis_text,
                )

                if config.line_support_group_id:
                    try:
                        ev_support_service.send_line_push(
                            LinePushRequest(
                                to=config.line_support_group_id,
                                messages=[{"type": "text", "text": support_alert}],
                            )
                        )
                    except Exception as exc:
                        repo.log_message(
                            ChatLogRecord(
                                session_id=session_id,
                                user_id=event.source.userId,
                                channel="line",
                                direction="outbound",
                                message_type=event.message.type,
                                language=media_language,
                                detected_intent="human_handoff",
                                message_text=support_alert,
                                analysis_text=analysis_text,
                                knowledge_hits=[],
                                status="support_group_notify_failed",
                                media_path=stored.file_path,
                                error_detail=str(exc),
                            )
                        )
                        results.append({"status": "support_group_notify_failed", "error": str(exc)})

                if event.replyToken and config.line_channel_access_token:
                    line_payload = ev_support_service.build_line_reply_request(
                        event.replyToken,
                        type("Resp", (), {"reply_text": customer_reply}),
                    )
                    status, body = ev_support_service.send_line_reply(line_payload)
                    repo.log_message(
                        ChatLogRecord(
                            session_id=session_id,
                            user_id=event.source.userId,
                            channel="line",
                            direction="outbound",
                            message_type=event.message.type,
                            language=media_language,
                            detected_intent="human_handoff",
                            message_text=customer_reply,
                            analysis_text=analysis_text,
                            knowledge_hits=[],
                            status="delivered" if status < 400 else "delivery_failed",
                            media_path=stored.file_path,
                            error_detail=None if status < 400 else json.dumps(body, ensure_ascii=False),
                        )
                    )
                else:
                    repo.log_message(
                        ChatLogRecord(
                            session_id=session_id,
                            user_id=event.source.userId,
                            channel="line",
                            direction="outbound",
                            message_type=event.message.type,
                            language=media_language,
                            detected_intent="human_handoff",
                            message_text=customer_reply,
                            analysis_text=analysis_text,
                            knowledge_hits=[],
                            status="generated_locally",
                            media_path=stored.file_path,
                        )
                    )
                results.append(
                    {
                        "status": "human_handoff_started",
                        "reply_text": customer_reply,
                        "analysis_text": analysis_text,
                        "media_path": stored.file_path,
                        "session_id": session.session_id,
                    }
                )
                continue

            response = ev_support_service.build_media_response(
                session_id=session_id,
                user_id=event.source.userId,
                channel="line",
                language=media_language,
                message_type=event.message.type,
                analysis=analysis_text,
            )

            if event.replyToken and config.line_channel_access_token:
                line_payload = ev_support_service.build_line_reply_request(event.replyToken, response)
                status, body = ev_support_service.send_line_reply(line_payload)
                repo.log_message(
                    ChatLogRecord(
                        session_id=session_id,
                        user_id=event.source.userId,
                        channel="line",
                        direction="outbound",
                        message_type=event.message.type,
                        language=response.detected_language,
                        detected_intent=response.detected_intent,
                        message_text=response.reply_text,
                        analysis_text=analysis_text,
                        knowledge_hits=[],
                        status="delivered" if status < 400 else "delivery_failed",
                        media_path=stored.file_path,
                        error_detail=None if status < 400 else json.dumps(body, ensure_ascii=False),
                    )
                )
                results.append(
                    {
                        "status": "media_received",
                        "http_status": status,
                        "reply_text": response.reply_text,
                        "analysis_text": analysis_text,
                        "media_path": stored.file_path,
                        "line_response": body,
                    }
                )
            else:
                repo.log_message(
                    ChatLogRecord(
                        session_id=session_id,
                        user_id=event.source.userId,
                        channel="line",
                        direction="outbound",
                        message_type=event.message.type,
                        language=response.detected_language,
                        detected_intent=response.detected_intent,
                        message_text=response.reply_text,
                        analysis_text=analysis_text,
                        knowledge_hits=[],
                        status="generated_locally",
                        media_path=stored.file_path,
                    )
                )
                results.append(
                    {
                        "status": "media_received",
                        "reply_text": response.reply_text,
                        "analysis_text": analysis_text,
                        "media_path": stored.file_path,
                    }
                )
            continue

        req = EVSupportRequest(
            session_id=session_id,
            user_id=event.source.userId,
            message_text=raw_text,
            channel="line",
            language=detected_language,
        )

        if config.ev_n8n_webhook_url:
            status, body = _post_to_ev_n8n_webhook(
                {
                    "session_id": session_id,
                    "user_id": event.source.userId,
                    "reply_token": event.replyToken,
                    "message_text": event.message.text,
                    "language": detected_language,
                    "source": "line",
                    "event_timestamp": event.timestamp,
                }
            )
            repo.log_message(
                ChatLogRecord(
                    session_id=session_id,
                    user_id=event.source.userId,
                    channel="line",
                    direction="outbound",
                    message_type="text",
                    language=req.language,
                    detected_intent=None,
                    message_text=json.dumps(body, ensure_ascii=False),
                    analysis_text=None,
                    knowledge_hits=[],
                    status="forwarded_to_n8n",
                )
            )
            results.append({"status": "forwarded_to_n8n", "http_status": status, "body": body})
            continue

        response = ev_support_service.generate_reply(req)
        if event.replyToken and config.line_channel_access_token:
            line_payload = ev_support_service.build_line_reply_request(event.replyToken, response)
            try:
                status, body = ev_support_service.send_line_reply(line_payload)
                repo.log_message(
                    ChatLogRecord(
                        session_id=session_id,
                        user_id=event.source.userId,
                        channel="line",
                        direction="outbound",
                        message_type="text",
                        language=response.detected_language,
                        detected_intent=response.detected_intent,
                        message_text=response.reply_text,
                        analysis_text=None,
                        knowledge_hits=response.knowledge_hits,
                        status="delivered" if status < 400 else "delivery_failed",
                        error_detail=None if status < 400 else json.dumps(body, ensure_ascii=False),
                    )
                )
                results.append(
                    {
                        "status": "replied_via_line",
                        "http_status": status,
                        "reply_text": response.reply_text,
                        "line_response": body,
                    }
                )
            except Exception as exc:
                repo.log_message(
                    ChatLogRecord(
                        session_id=session_id,
                        user_id=event.source.userId,
                        channel="line",
                        direction="outbound",
                        message_type="text",
                        language=response.detected_language,
                        detected_intent=response.detected_intent,
                        message_text=response.reply_text,
                        analysis_text=None,
                        knowledge_hits=response.knowledge_hits,
                        status="delivery_failed",
                        error_detail=str(exc),
                    )
                )
                raise
        else:
            repo.log_message(
                ChatLogRecord(
                    session_id=session_id,
                    user_id=event.source.userId,
                    channel="line",
                    direction="outbound",
                    message_type="text",
                    language=response.detected_language,
                    detected_intent=response.detected_intent,
                    message_text=response.reply_text,
                    analysis_text=None,
                    knowledge_hits=response.knowledge_hits,
                    status="generated_locally",
                )
            )
            results.append(
                {
                    "status": "generated_locally",
                    "reply_text": response.reply_text,
                    "detected_intent": response.detected_intent,
                }
            )

    return {"ok": True, "results": results}


@router.get("/google/status")
def google_status() -> dict:
    return get_google_oauth_status(config)


@router.get("/google/start")
def google_start() -> RedirectResponse:
    auth_url = build_google_oauth_url(config)
    if not auth_url:
        raise HTTPException(status_code=400, detail="Google OAuth is not configured")
    return RedirectResponse(auth_url)


@router.get("/google/callback", response_class=HTMLResponse)
def google_callback(code: str, state: str | None = None) -> HTMLResponse:
    try:
        handle_google_oauth_callback(config, code=code, state=state)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return HTMLResponse(
        """
<!doctype html>
<html>
<head><meta charset="utf-8"><title>Google 已連接</title></head>
<body style="font-family: Arial, sans-serif; max-width: 560px; margin: 40px auto; color: #111827;">
  <h2>Google Slides 已連接</h2>
  <p>你可以關閉這個視窗，回到工作台繼續生成簡報。</p>
  <script>
    setTimeout(() => window.close(), 1200);
  </script>
</body>
</html>
"""
    )


@router.post("/cases/intake", dependencies=[Depends(require_api_key)])
def intake(req: IntakeRequest) -> dict:
    service = get_service()
    result = service.intake_and_orchestrate(req)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"].model_dump(mode="json"))
    return {
        "case_id": result["case"].case_id,
        "status": result["case"].status,
        "review_status": result["case"].review_status,
        "notification": result["notification"],
        "case": result["case"],
    }


@router.get("/cases", dependencies=[Depends(require_api_key)])
def list_cases() -> list[dict]:
    service = get_service()
    return [c.model_dump() for c in service.repo.list_all()]


@router.get("/cases/{case_id}", dependencies=[Depends(require_api_key)])
def get_case(case_id: str) -> dict:
    service = get_service()
    case = service.repo.get(case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    return case.model_dump()


@router.get("/cases/{case_id}/presentation", response_class=HTMLResponse, dependencies=[Depends(require_api_key)])
def get_case_presentation(case_id: str) -> HTMLResponse:
    service = get_service()
    case = service.repo.get(case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    if not case.presentation_result or not case.presentation_result.pages:
        raise HTTPException(status_code=404, detail="Presentation not found for this case")

    slides_html = []
    for i, page in enumerate(case.presentation_result.pages, start=1):
        title = page.get("title", f"Slide {i}")
        section = page.get("section", "")
        key_message = page.get("key_message", "")
        supporting_points = page.get("supporting_points", "")
        advisor_recommendation = page.get("advisor_recommendation", "")
        visual_icon = page.get("visual_icon", "📘")
        content = page.get("content", "")
        slides_html.append(
            f"""
            <section class="slide">
              <div class="slide-no">Slide {i}</div>
              <div class="slide-section">{section}</div>
              <h2>{title}</h2>
              <div class="slide-key">{key_message}</div>
              <div class="slide-grid">
                <div>
                  <h3>分析重點</h3>
                  <p>{supporting_points or content}</p>
                </div>
                <div>
                  <h3>顧問建議</h3>
                  <p>{advisor_recommendation or "請依客戶需求進一步調整建議節奏。"}</p>
                </div>
              </div>
              <div class="slide-footer">
                <div class="footer-meta">ET Consulting | Insurance Advisory Proposal</div>
                <div class="slide-visual">
                  <div class="visual-icon">{visual_icon}</div>
                </div>
              </div>
            </section>
            """
        )

    html = f"""
<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>簡報檢視 - {case.case_id}</title>
  <style>
    :root {{
      --font-cjk: "PingFang TC", "PingFang HK", "Hiragino Sans GB", "Microsoft JhengHei", "Noto Sans CJK TC", "Source Han Sans TC", sans-serif;
      --font-latin: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif;
    }}
    body {{
      margin: 0;
      font-family: var(--font-cjk), var(--font-latin);
      background: #f3f4f6;
      color: #111827;
      -webkit-font-smoothing: antialiased;
      text-rendering: optimizeLegibility;
    }}
    .wrap {{
      max-width: 980px;
      margin: 24px auto;
      padding: 0 16px 32px;
    }}
    .header {{
      margin-bottom: 16px;
      padding: 16px;
      background: white;
      border: 1px solid #e5e7eb;
      border-radius: 12px;
    }}
    .actions {{
      margin-top: 12px;
    }}
    .btn {{
      display: inline-block;
      border: 1px solid #d1d5db;
      background: #111827;
      color: #fff;
      border-radius: 8px;
      padding: 8px 12px;
      font-size: 14px;
      cursor: pointer;
      text-decoration: none;
      margin-right: 8px;
    }}
    .btn-secondary {{
      background: #fff;
      color: #111827;
    }}
    .header h1 {{
      margin: 0 0 8px;
      font-size: 22px;
      font-family: var(--font-cjk), var(--font-latin);
    }}
    .meta {{
      color: #4b5563;
      font-size: 14px;
    }}
    .slide {{
      background: white;
      border: 1px solid #e5e7eb;
      border-radius: 12px;
      padding: 24px;
      margin-top: 16px;
      min-height: 280px;
      box-shadow: 0 4px 16px rgba(17, 24, 39, 0.06);
    }}
    .slide-no {{
      color: #6b7280;
      font-size: 12px;
      margin-bottom: 10px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }}
    .slide-section {{
      display: inline-block;
      margin-bottom: 10px;
      padding: 4px 10px;
      border-radius: 999px;
      background: rgba(180, 83, 9, 0.12);
      color: #b45309;
      font-size: 12px;
      letter-spacing: 0.08em;
    }}
    .slide h2 {{
      margin: 0 0 12px;
      font-size: 28px;
      font-family: var(--font-cjk), var(--font-latin);
    }}
    .slide-key {{
      margin-bottom: 16px;
      font-size: 20px;
      font-weight: 700;
      color: #1f2937;
      line-height: 1.5;
      font-family: var(--font-cjk), var(--font-latin);
    }}
    .slide-grid {{
      display: grid;
      grid-template-columns: 1.1fr 0.9fr;
      gap: 18px;
    }}
    .slide-footer {{
      margin-top: 20px;
      display: flex;
      align-items: center;
      justify-content: space-between;
    }}
    .slide-visual {{
      width: 72px;
      min-height: 56px;
      display: flex;
      align-items: center;
      justify-content: center;
    }}
    .visual-icon {{
      font-size: 40px;
      line-height: 1;
      color: #b45309;
      font-family: "Apple Color Emoji", "Segoe UI Emoji", "Noto Color Emoji", sans-serif;
    }}
    .footer-meta {{
      font-size: 12px;
      color: #6b7280;
      letter-spacing: 0.04em;
    }}
    .slide h3 {{
      margin: 0 0 8px;
      font-size: 12px;
      letter-spacing: 0.1em;
      text-transform: uppercase;
      color: #6b7280;
    }}
    .slide p {{
      font-size: 20px;
      line-height: 1.5;
      white-space: pre-wrap;
      font-family: var(--font-cjk), var(--font-latin);
    }}
    @media print {{
      @page {{
        size: A4 landscape;
        margin: 10mm;
      }}
      body {{
        background: #fff;
        -webkit-print-color-adjust: exact;
        print-color-adjust: exact;
      }}
      .header {{
        border: none;
        padding: 0 0 8px;
      }}
      .actions {{
        display: none;
      }}
      .slide {{
        box-shadow: none;
        page-break-after: always;
        min-height: 0;
      }}
      .slide-grid {{
        grid-template-columns: 1fr;
      }}
      .slide:last-child {{
        page-break-after: auto;
      }}
    }}
  </style>
</head>
<body>
  <main class="wrap">
    <header class="header">
      <h1>客戶簡報檢視</h1>
      <div class="meta">
        案件編號: <strong>{case.case_id}</strong> |
        案件狀態: <strong>{case.status.value}</strong> |
        審核狀態: <strong>{case.review_status.value}</strong>
      </div>
      <div class="actions">
        <a class="btn" href="/api/cases/{case.case_id}/proposal.pdf?key={config.api_auth_key}" target="_blank" rel="noopener">下載 PDF</a>
        <button class="btn btn-secondary" onclick="window.print()">瀏覽器列印</button>
      </div>
    </header>
    {''.join(slides_html)}
  </main>
</body>
</html>
"""
    return HTMLResponse(content=html)


@router.get("/cases/{case_id}/proposal.pdf", dependencies=[Depends(require_api_key)])
def get_case_proposal_pdf(case_id: str) -> Response:
    service = get_service()
    case = service.repo.get(case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    if not case.presentation_result or not case.presentation_result.pages:
        raise HTTPException(status_code=404, detail="Presentation not found for this case")

    try:
        pdf_bytes = build_proposal_pdf(case)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    headers = {
        "Content-Disposition": f'inline; filename="proposal-{case.case_id}.pdf"',
    }
    return Response(content=pdf_bytes, media_type="application/pdf", headers=headers)


@router.post("/cases/{case_id}/review", dependencies=[Depends(require_api_key)])
def review_case(case_id: str, decision: ReviewDecision) -> dict:
    service = get_service()
    try:
        case = service.review_case(case_id, decision)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return {
        "case_id": case.case_id,
        "status": case.status,
        "review_status": case.review_status,
        "events": case.events,
    }


@router.get("/test-form", response_class=HTMLResponse)
def test_form() -> str:
    return """
<!doctype html>
<html>
<head><meta charset="utf-8"><title>Insurance Workflow Test Form v2</title></head>
<body style="font-family: Arial, sans-serif; max-width: 720px; margin: 24px auto;">
  <h2>Insurance Workflow Test Form v2</h2>
  <p>Submit directly to n8n webhook: <code>insurance-intake-codex-v3</code></p>
  <form id="f">
    <h3>Client Profile</h3>
    <label>Name / Client Code<br/><input name="name_or_code" placeholder="Name / Code" value="Client Test" required /></label><br/><br/>
    <label>Age<br/><input name="age" type="number" placeholder="Age" value="35" required /></label><br/><br/>
    <label>Gender<br/><select name="gender"><option>Male</option><option>Female</option><option>Other</option></select></label><br/><br/>
    <label>Marital Status<br/><select name="marital_status"><option>Single</option><option>Married</option><option>Divorced</option><option>Widowed</option></select></label><br/><br/>
    <label>Number of Dependents<br/><input name="dependents" type="number" value="1" required /></label><br/><br/>
    <label>Occupation<br/><input name="occupation" value="Manager" required /></label><br/><br/>
    <label>Monthly Salary / Income<br/><input name="income_monthly" type="number" value="50000" required /></label><br/><br/>
    <label>Monthly Expenses<br/><input name="expenses_monthly" type="number" value="25000" required /></label><br/><br/>
    <label>Monthly Insurance Budget<br/><input name="budget_monthly" type="number" value="3000" required /></label><br/><br/>

    <h3>Insurance Profile</h3>
    <label><input type="checkbox" name="existing_medical" checked /> Existing Medical</label><br/>
    <label><input type="checkbox" name="existing_ci" /> Existing CI</label><br/>
    <label><input type="checkbox" name="existing_life" /> Existing Life</label><br/>
    <label><input type="checkbox" name="existing_accident" checked /> Existing Accident</label><br/><br/>
    <label>Current Monthly Premium<br/><input name="current_premium" type="number" value="1200" required /></label><br/><br/>

    <button type="submit">Submit to n8n</button>
  </form>
  <pre id="out" style="background:#f6f8fa; padding:12px; margin-top:16px;"></pre>
  <script>
    const submitUrl = "/api/test-form/submit";
    document.getElementById("f").addEventListener("submit", async (e) => {
      e.preventDefault();
      const fd = new FormData(e.target);
      const payload = {
        input: {
          client_profile: {
            name_or_code: fd.get("name_or_code"),
            age: Number(fd.get("age")),
            gender: fd.get("gender"),
            marital_status: fd.get("marital_status"),
            dependents: Number(fd.get("dependents")),
            occupation: fd.get("occupation"),
            income_monthly: Number(fd.get("income_monthly")),
            expenses_monthly: Number(fd.get("expenses_monthly")),
            budget_monthly: Number(fd.get("budget_monthly"))
          },
          insurance_profile: {
            existing_medical: fd.get("existing_medical") === "on",
            existing_ci: fd.get("existing_ci") === "on",
            existing_life: fd.get("existing_life") === "on",
            existing_accident: fd.get("existing_accident") === "on",
            current_premium: Number(fd.get("current_premium"))
          },
          source: "web_form"
        },
        generate_presentation: true,
        generate_content: true
      };
      const res = await fetch(submitUrl, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(payload)
      });
      const text = await res.text();
      document.getElementById("out").textContent = `HTTP ${res.status}\\n${text}`;
    });
  </script>
</body>
</html>
"""


@router.post("/test-form/submit")
def test_form_submit(payload: dict = Body(...)) -> JSONResponse:
    try:
        status_code, body = _post_to_n8n_webhook(payload)
        return JSONResponse(status_code=status_code, content=body)
    except Exception as exc:
        return JSONResponse(status_code=502, content={"error": str(exc)})


@router.get("/workbench", response_class=HTMLResponse)
def workbench() -> str:
    return """
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>保險案件工作台</title>
  <style>
    :root {
      --bg: #f5efe4;
      --ink: #1f2937;
      --muted: #6b7280;
      --panel: rgba(255, 252, 247, 0.88);
      --line: rgba(80, 60, 32, 0.12);
      --accent: #b45309;
      --accent-2: #0f766e;
      --danger: #b91c1c;
      --shadow: 0 16px 40px rgba(64, 41, 10, 0.12);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, rgba(180, 83, 9, 0.12), transparent 32%),
        radial-gradient(circle at bottom right, rgba(15, 118, 110, 0.14), transparent 30%),
        linear-gradient(180deg, #fbf6ee 0%, #f1e6d3 100%);
      font-family: Georgia, "Times New Roman", serif;
    }
    .page {
      max-width: 1380px;
      margin: 0 auto;
      padding: 28px 18px 60px;
    }
    .hero {
      display: grid;
      grid-template-columns: 1.1fr 0.9fr;
      gap: 18px;
      margin-bottom: 18px;
    }
    .hero-card, .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 24px;
      box-shadow: var(--shadow);
      backdrop-filter: blur(10px);
    }
    .hero-copy {
      padding: 28px;
    }
    .eyebrow {
      display: inline-block;
      padding: 6px 10px;
      border-radius: 999px;
      background: rgba(180, 83, 9, 0.12);
      color: var(--accent);
      font-size: 12px;
      letter-spacing: 0.14em;
      text-transform: uppercase;
    }
    h1 {
      margin: 14px 0 10px;
      font-size: clamp(34px, 5vw, 62px);
      line-height: 0.95;
      font-weight: 700;
    }
    .hero-copy p {
      margin: 0;
      max-width: 52ch;
      color: #4b5563;
      font-size: 18px;
      line-height: 1.55;
    }
    .hero-stats {
      display: grid;
      grid-template-columns: repeat(2, 1fr);
      gap: 12px;
      padding: 18px;
    }
    .stat {
      padding: 18px;
      border-radius: 18px;
      background: rgba(255,255,255,0.7);
      border: 1px solid var(--line);
    }
    .stat-label {
      font-size: 12px;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      color: var(--muted);
    }
    .stat-value {
      margin-top: 10px;
      font-size: 28px;
      font-weight: 700;
    }
    .layout {
      display: grid;
      grid-template-columns: 420px 1fr;
      gap: 18px;
      align-items: start;
    }
    .panel {
      padding: 18px;
    }
    .panel h2, .panel h3 {
      margin: 0 0 14px;
      font-size: 22px;
    }
    .panel h3 {
      margin-top: 10px;
      font-size: 14px;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      color: var(--muted);
    }
    .grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 12px;
    }
    label {
      display: block;
      font-size: 13px;
      color: #374151;
      margin-bottom: 6px;
    }
    .voice-field {
      display: grid;
      grid-template-columns: 1fr 46px;
      gap: 8px;
      align-items: center;
    }
    input, select {
      width: 100%;
      border-radius: 14px;
      border: 1px solid rgba(107, 114, 128, 0.25);
      padding: 12px 14px;
      font-size: 15px;
      background: rgba(255,255,255,0.9);
      color: var(--ink);
    }
    .voice-btn {
      width: 46px;
      height: 46px;
      padding: 0;
      border-radius: 14px;
      border: 1px solid var(--line);
      background: rgba(255,255,255,0.88);
      color: var(--accent);
      font-size: 18px;
      line-height: 1;
    }
    .voice-btn.active {
      background: linear-gradient(135deg, #b45309, #ea580c);
      color: #fff;
    }
    .voice-note {
      margin: 12px 0 0;
      color: #6b7280;
      font-size: 13px;
      line-height: 1.5;
    }
    .checks {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 10px;
      margin-top: 10px;
    }
    .check {
      display: flex;
      gap: 10px;
      align-items: center;
      padding: 10px 12px;
      border-radius: 14px;
      border: 1px solid var(--line);
      background: rgba(255,255,255,0.7);
    }
    .check input {
      width: auto;
      margin: 0;
    }
    .actions {
      display: flex;
      gap: 10px;
      margin-top: 16px;
      flex-wrap: wrap;
    }
    button {
      border: 0;
      border-radius: 999px;
      padding: 12px 18px;
      font-size: 14px;
      font-weight: 700;
      cursor: pointer;
    }
    .primary {
      background: linear-gradient(135deg, #b45309, #ea580c);
      color: white;
    }
    .secondary {
      background: rgba(255,255,255,0.85);
      color: var(--ink);
      border: 1px solid var(--line);
    }
    .results {
      display: grid;
      gap: 18px;
    }
    .result-grid {
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 12px;
    }
    .result-card {
      padding: 16px;
      border-radius: 18px;
      border: 1px solid var(--line);
      background: rgba(255,255,255,0.78);
    }
    .result-card strong {
      display: block;
      font-size: 12px;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0.12em;
      margin-bottom: 8px;
    }
    .result-card span {
      font-size: 24px;
      font-weight: 700;
    }
    .split {
      display: grid;
      grid-template-columns: 1.15fr 0.85fr;
      gap: 18px;
    }
    .callout {
      padding: 16px;
      border-radius: 18px;
      border: 1px solid var(--line);
      background: rgba(255,255,255,0.76);
    }
    .callout p, .callout li {
      color: #374151;
      line-height: 1.6;
      margin: 0;
      white-space: pre-wrap;
    }
    .callout ul {
      margin: 0;
      padding-left: 18px;
    }
    .slides {
      display: grid;
      gap: 12px;
    }
    .slide {
      border-radius: 18px;
      padding: 18px;
      background: linear-gradient(180deg, rgba(255,255,255,0.95), rgba(250,245,236,0.95));
      border: 1px solid var(--line);
    }
    .slide-head {
      font-size: 11px;
      color: var(--muted);
      letter-spacing: 0.14em;
      text-transform: uppercase;
    }
    .slide h4 {
      margin: 8px 0 10px;
      font-size: 24px;
    }
    .slide .slide-section {
      display: inline-block;
      margin-top: 8px;
      padding: 4px 10px;
      border-radius: 999px;
      background: rgba(180, 83, 9, 0.12);
      color: #b45309;
      font-size: 11px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }
    .slide .slide-key {
      margin: 8px 0 12px;
      font-size: 18px;
      font-weight: 700;
      line-height: 1.5;
    }
    .slide .slide-grid {
      display: grid;
      grid-template-columns: 1.1fr 0.9fr;
      gap: 14px;
    }
    .slide .slide-footer {
      margin-top: 14px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
    }
    .slide .slide-visual {
      width: 68px;
      min-height: 52px;
      display: flex;
      align-items: center;
      justify-content: center;
    }
    .slide .visual-icon {
      font-size: 34px;
      line-height: 1;
      color: #b45309;
      font-family: "Apple Color Emoji", "Segoe UI Emoji", "Noto Color Emoji", sans-serif;
    }
    .slide .footer-meta {
      font-size: 11px;
      color: var(--muted);
      letter-spacing: 0.04em;
    }
    .slide .slide-block {
      padding: 12px;
      border-radius: 14px;
      background: rgba(255,255,255,0.75);
      border: 1px solid var(--line);
    }
    .slide .slide-block h5 {
      margin: 0 0 8px;
      font-size: 11px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: var(--muted);
    }
    .slide p {
      margin: 0;
      font-size: 17px;
      line-height: 1.55;
      white-space: pre-wrap;
    }
    .status {
      padding: 12px 14px;
      border-radius: 14px;
      background: rgba(255,255,255,0.82);
      border: 1px solid var(--line);
      min-height: 48px;
      color: #374151;
      white-space: pre-wrap;
    }
    .linkbar {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      margin-top: 12px;
    }
    .linkbar a {
      color: var(--accent-2);
      text-decoration: none;
      font-weight: 700;
    }
    .danger {
      color: var(--danger);
    }
    @media (max-width: 1080px) {
      .hero, .layout, .split, .result-grid {
        grid-template-columns: 1fr;
      }
    }
  </style>
</head>
<body>
  <main class="page">
    <section class="hero">
      <div class="hero-card hero-copy">
        <span class="eyebrow">保險顧問應用</span>
        <h1>保險案件工作台</h1>
        <p>輸入客戶資料後，直接查看分析結果、合規建議與簡報內容。</p>
      </div>
      <div class="hero-card hero-stats">
        <div class="stat"><div class="stat-label">Module A</div><div class="stat-value">分析</div></div>
        <div class="stat"><div class="stat-label">Module B</div><div class="stat-value">簡報</div></div>
      </div>
    </section>

    <section class="layout">
      <div class="panel">
        <h2>案件輸入</h2>
        <div class="status" id="statusBox">系統已就緒。提交案件後會生成 Module A 與 Module B 輸出。</div>
        <form id="workbenchForm">
          <h3>客戶資料</h3>
          <div class="grid">
            <div><label>客戶名稱 / 編號</label><div class="voice-field"><input name="name_or_code" value="Client Workbench" required /><button class="voice-btn" type="button" data-field="name_or_code" aria-label="語音輸入客戶名稱">🎙</button></div></div>
            <div><label>年齡</label><div class="voice-field"><input name="age" type="number" value="39" required /><button class="voice-btn" type="button" data-field="age" aria-label="語音輸入年齡">🎙</button></div></div>
            <div><label>性別</label><div class="voice-field"><select name="gender"><option>Male</option><option selected>Female</option><option>Other</option></select><button class="voice-btn" type="button" data-field="gender" aria-label="語音輸入性別">🎙</button></div></div>
            <div><label>婚姻狀況</label><div class="voice-field"><select name="marital_status"><option>Single</option><option selected>Married</option><option>Divorced</option><option>Widowed</option></select><button class="voice-btn" type="button" data-field="marital_status" aria-label="語音輸入婚姻狀況">🎙</button></div></div>
            <div><label>受扶養人數</label><div class="voice-field"><input name="dependents" type="number" value="1" required /><button class="voice-btn" type="button" data-field="dependents" aria-label="語音輸入受扶養人數">🎙</button></div></div>
            <div><label>職業</label><div class="voice-field"><input name="occupation" value="Product Manager" required /><button class="voice-btn" type="button" data-field="occupation" aria-label="語音輸入職業">🎙</button></div></div>
            <div><label>每月收入</label><div class="voice-field"><input name="income_monthly" type="number" value="82000" required /><button class="voice-btn" type="button" data-field="income_monthly" aria-label="語音輸入每月收入">🎙</button></div></div>
            <div><label>每月支出</label><div class="voice-field"><input name="expenses_monthly" type="number" value="36000" required /><button class="voice-btn" type="button" data-field="expenses_monthly" aria-label="語音輸入每月支出">🎙</button></div></div>
            <div><label>每月保險預算</label><div class="voice-field"><input name="budget_monthly" type="number" value="4500" required /><button class="voice-btn" type="button" data-field="budget_monthly" aria-label="語音輸入每月保險預算">🎙</button></div></div>
            <div><label>目前每月保費</label><div class="voice-field"><input name="current_premium" type="number" value="1400" required /><button class="voice-btn" type="button" data-field="current_premium" aria-label="語音輸入目前每月保費">🎙</button></div></div>
          </div>
          <p class="voice-note">點擊欄位右側麥克風即可語音填表。數字欄位會自動擷取數字；性別與婚姻狀況會自動對應選項。</p>

          <h3>現有保障</h3>
          <div class="checks">
            <label class="check"><input type="checkbox" name="existing_medical" checked /> 已有醫療保障</label>
            <label class="check"><input type="checkbox" name="existing_ci" /> 已有重大疾病保障</label>
            <label class="check"><input type="checkbox" name="existing_life" /> 已有壽險保障</label>
            <label class="check"><input type="checkbox" name="existing_accident" checked /> 已有意外保障</label>
          </div>

          <div class="actions">
            <button class="primary" type="submit">生成案件</button>
          </div>
          <p style="margin:12px 0 0; color:#6b7280; font-size:14px;">
            若系統已完成 Google Slides 設定，生成後會自動建立可分享的簡報連結。
          </p>
        </form>
      </div>

      <div class="results">
        <div class="panel">
          <h2>總覽</h2>
          <div class="result-grid">
            <div class="result-card"><strong>案件編號</strong><span id="caseId">-</span></div>
            <div class="result-card"><strong>案件狀態</strong><span id="caseStatus">-</span></div>
            <div class="result-card"><strong>審核狀態</strong><span id="reviewStatus">-</span></div>
            <div class="result-card"><strong>風險分數</strong><span id="riskScore">-</span></div>
            <div class="result-card"><strong>合規風險</strong><span id="complianceRisk">-</span></div>
            <div class="result-card"><strong>簡報頁數</strong><span id="slideCount">-</span></div>
            <div class="result-card"><strong>Google Slides</strong><span id="googleSlidesStatus">-</span></div>
          </div>
          <div class="linkbar" id="linkBar"></div>
          <div class="linkbar" id="googleAuthBar" style="margin-top:8px;"></div>
        </div>

        <div class="split">
          <div class="panel">
            <h2>Module A</h2>
            <div class="callout">
              <strong>技術摘要</strong>
              <p id="technicalSummary">尚未產生輸出。</p>
            </div>
            <div class="callout" style="margin-top:12px;">
              <strong>顧問說明</strong>
              <p id="advisorNarrative">尚未產生輸出。</p>
            </div>
            <div class="callout" style="margin-top:12px;">
              <strong>建議事項</strong>
              <ul id="recommendations"><li>尚未產生輸出。</li></ul>
            </div>
          </div>

          <div class="panel">
            <h2>合規結果</h2>
            <div class="callout">
              <strong>審核建議</strong>
              <p id="approvalRecommendation">尚未產生輸出。</p>
            </div>
            <div class="callout" style="margin-top:12px;">
              <strong>必要揭露</strong>
              <ul id="disclosures"><li>尚未產生輸出。</li></ul>
            </div>
            <div class="callout" style="margin-top:12px;">
              <strong>風險議題</strong>
              <ul id="issues"><li>尚未產生輸出。</li></ul>
            </div>
          </div>
        </div>

        <div class="panel">
          <h2>Module B</h2>
          <div class="slides" id="slides">
            <div class="slide">
              <div class="slide-head">簡報</div>
              <h4>等待生成</h4>
              <p>提交案件後，這裡會顯示 Module B 的簡報頁面內容。</p>
            </div>
          </div>
        </div>
      </div>
    </section>
  </main>

  <script>
    const form = document.getElementById("workbenchForm");
    const statusBox = document.getElementById("statusBox");
    const linkBar = document.getElementById("linkBar");
    const googleAuthBar = document.getElementById("googleAuthBar");
    const voiceButtons = Array.from(document.querySelectorAll(".voice-btn"));
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition || null;
    let recognition = null;
    let activeVoiceButton = null;

    async function loadGoogleStatus() {
      googleAuthBar.innerHTML = "";
      try {
        const response = await fetch("/api/google/status");
        const data = await response.json();
        const badge = document.createElement("span");
        badge.textContent = data.connected
          ? "Google Slides 已連接"
          : (data.configured ? "Google Slides 未連接" : "Google OAuth 尚未設定");
        googleAuthBar.appendChild(badge);

        if (data.configured && !data.connected) {
          const connect = document.createElement("a");
          connect.href = "/api/google/start";
          connect.target = "_blank";
          connect.textContent = "連接 Google 帳號";
          googleAuthBar.appendChild(connect);
        }
      } catch (error) {
        const badge = document.createElement("span");
        badge.textContent = "無法取得 Google 連線狀態";
        googleAuthBar.appendChild(badge);
      }
    }

    function setStatus(text, isError = false) {
      statusBox.textContent = text;
      statusBox.classList.toggle("danger", isError);
    }

    function normalizeVoiceValue(fieldName, transcript) {
      const raw = (transcript || "").trim();
      const compact = raw.replace(/\\s+/g, "");
      const digits = (raw.match(/\\d+/g) || []).join("");
      if (["age", "dependents", "income_monthly", "expenses_monthly", "budget_monthly", "current_premium"].includes(fieldName)) {
        return digits || "";
      }
      if (fieldName === "gender") {
        if (/female|女/i.test(raw)) return "Female";
        if (/male|男/i.test(raw)) return "Male";
        return "Other";
      }
      if (fieldName === "marital_status") {
        if (/married|已婚|結婚/i.test(raw)) return "Married";
        if (/single|單身|未婚/i.test(raw)) return "Single";
        if (/divorced|離婚/i.test(raw)) return "Divorced";
        if (/widowed|喪偶/i.test(raw)) return "Widowed";
        return "";
      }
      return compact || raw;
    }

    function stopVoiceInput() {
      if (recognition) {
        recognition.stop();
      }
      if (activeVoiceButton) {
        activeVoiceButton.classList.remove("active");
        activeVoiceButton = null;
      }
    }

    function startVoiceInput(button) {
      if (!SpeechRecognition) {
        setStatus("此瀏覽器不支援語音輸入。建議使用 Chrome 或 Edge。", true);
        return;
      }

      if (!recognition) {
        recognition = new SpeechRecognition();
        recognition.lang = "zh-HK";
        recognition.interimResults = false;
        recognition.maxAlternatives = 1;

        recognition.onresult = (event) => {
          if (!activeVoiceButton) return;
          const fieldName = activeVoiceButton.dataset.field;
          const target = form.elements[fieldName];
          const transcript = event.results[0][0].transcript;
          const value = normalizeVoiceValue(fieldName, transcript);
          if (!value) {
            setStatus(`未能辨識 ${fieldName} 的有效內容，請再試一次。`, true);
            return;
          }
          target.value = value;
          target.dispatchEvent(new Event("change", { bubbles: true }));
          setStatus(`已完成語音填寫：${transcript}`);
        };

        recognition.onerror = (event) => {
          const message = event.error === "not-allowed"
            ? "瀏覽器尚未取得麥克風權限。"
            : `語音輸入失敗：${event.error}`;
          setStatus(message, true);
          stopVoiceInput();
        };

        recognition.onend = () => {
          if (activeVoiceButton) {
            activeVoiceButton.classList.remove("active");
            activeVoiceButton = null;
          }
        };
      }

      if (activeVoiceButton === button) {
        stopVoiceInput();
        return;
      }

      if (activeVoiceButton) {
        activeVoiceButton.classList.remove("active");
      }

      activeVoiceButton = button;
      button.classList.add("active");
      setStatus("正在收音，請直接說出該欄位內容。");
      recognition.start();
    }

    function setList(id, items, formatter) {
      const el = document.getElementById(id);
      el.innerHTML = "";
      const values = items && items.length ? items : ["尚未產生輸出。"];
      values.forEach((item) => {
        const li = document.createElement("li");
        li.textContent = formatter ? formatter(item) : item;
        el.appendChild(li);
      });
    }

    voiceButtons.forEach((button) => {
      button.addEventListener("click", () => startVoiceInput(button));
      if (!SpeechRecognition) {
        button.disabled = true;
        button.title = "目前瀏覽器不支援語音輸入";
      }
    });

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      setStatus("系統正在生成報告與簡報...");
      linkBar.innerHTML = "";

      const fd = new FormData(form);
      const payload = {
        input: {
          client_profile: {
            name_or_code: fd.get("name_or_code"),
            age: Number(fd.get("age")),
            gender: fd.get("gender"),
            marital_status: fd.get("marital_status"),
            dependents: Number(fd.get("dependents")),
            occupation: fd.get("occupation"),
            income_monthly: Number(fd.get("income_monthly")),
            expenses_monthly: Number(fd.get("expenses_monthly")),
            budget_monthly: Number(fd.get("budget_monthly"))
          },
          insurance_profile: {
            existing_medical: fd.get("existing_medical") === "on",
            existing_ci: fd.get("existing_ci") === "on",
            existing_life: fd.get("existing_life") === "on",
            existing_accident: fd.get("existing_accident") === "on",
            current_premium: Number(fd.get("current_premium"))
          },
          source: "workbench"
        },
        generate_presentation: true,
        generate_content: true
      };

      try {
        const response = await fetch("/api/workbench/generate", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload)
        });
        const data = await response.json();
        if (!response.ok) {
          throw new Error(data.detail || data.error || "生成失敗");
        }

        if (data.dead_letter) {
          document.getElementById("caseId").textContent = "-";
          document.getElementById("caseStatus").textContent = data.status || "failed";
          document.getElementById("reviewStatus").textContent = "-";
          document.getElementById("riskScore").textContent = "-";
          document.getElementById("complianceRisk").textContent = "-";
          document.getElementById("slideCount").textContent = "-";
          document.getElementById("googleSlidesStatus").textContent = "-";
          document.getElementById("technicalSummary").textContent = "目前沒有分析輸出。";
          document.getElementById("advisorNarrative").textContent = "目前沒有顧問說明。";
          document.getElementById("approvalRecommendation").textContent = "流程失敗";
          setList("recommendations", []);
          setList("disclosures", []);
          setList("issues", [`${data.stage || "workflow"}: ${JSON.stringify(data.error || {})}`]);
          document.getElementById("slides").innerHTML = '<div class="slide"><div class="slide-head">簡報</div><h4>目前無法生成</h4><p>n8n workflow 已進入 dead letter。請先檢查 workflow 連到的後端網址是否仍然有效。</p></div>';
          setStatus(`n8n 執行失敗：${data.stage || "unknown stage"}`, true);
          return;
        }

        document.getElementById("caseId").textContent = data.case_id || "-";
        document.getElementById("caseStatus").textContent = data.status || "-";
        document.getElementById("reviewStatus").textContent = data.review_status || "-";
        document.getElementById("riskScore").textContent = data.module_a_report?.risk_score ?? "-";
        document.getElementById("complianceRisk").textContent = data.compliance_report?.risk_level || "-";
        document.getElementById("slideCount").textContent = data.presentation?.page_count ?? "-";
        const googleSlidesUrl = data.presentation?.google_presentation_url || data.raw_case?.presentation_result?.google_presentation_url || null;
        document.getElementById("googleSlidesStatus").textContent = googleSlidesUrl ? "已建立" : "未啟用";
        document.getElementById("technicalSummary").textContent = data.module_a_report?.technical_summary || "尚未產生輸出。";
        document.getElementById("advisorNarrative").textContent = data.module_a_report?.advisor_narrative || "尚未產生輸出。";
        document.getElementById("approvalRecommendation").textContent = data.compliance_report?.approval_recommendation || "尚未產生輸出。";
        setList("recommendations", data.module_a_report?.recommendations || []);
        setList("disclosures", data.compliance_report?.required_disclosures || []);
        setList("issues", data.compliance_report?.issues || [], (item) => `${item.severity}: ${item.claim} - ${item.reason}`);

        const slidesEl = document.getElementById("slides");
        slidesEl.innerHTML = "";
        (data.presentation?.pages || []).forEach((slide, index) => {
          const node = document.createElement("article");
          node.className = "slide";
          node.innerHTML = `
            <div class="slide-head">第 ${index + 1} 頁</div>
            <div class="slide-section">${slide.section || "簡報頁"}</div>
            <h4>${slide.title || `第 ${index + 1} 頁`}</h4>
            <div class="slide-key">${slide.key_message || ""}</div>
            <div class="slide-grid">
              <div class="slide-block">
                <h5>分析重點</h5>
                <p>${slide.supporting_points || slide.content || ""}</p>
              </div>
              <div class="slide-block">
                <h5>顧問建議</h5>
                <p>${slide.advisor_recommendation || ""}</p>
              </div>
            </div>
            <div class="slide-footer">
              <div class="footer-meta">ET Consulting | Insurance Advisory Proposal</div>
              <div class="slide-visual">
                <div class="visual-icon">${slide.visual_icon || "📘"}</div>
              </div>
            </div>`;
          slidesEl.appendChild(node);
        });
        if (!(data.presentation?.pages || []).length) {
          slidesEl.innerHTML = '<div class="slide"><div class="slide-head">簡報</div><h4>沒有輸出</h4><p>目前沒有返回任何簡報頁面。</p></div>';
        }

        if (data.case_id) {
          const report = document.createElement("a");
          report.href = `/api/cases/${data.case_id}?key=ins-agent-2026-key`;
          report.target = "_blank";
          report.textContent = "開啟案件報告 JSON";
          linkBar.appendChild(report);

          const presentation = document.createElement("a");
          presentation.href = `/api/cases/${data.case_id}/presentation?key=ins-agent-2026-key`;
          presentation.target = "_blank";
          presentation.textContent = "開啟簡報檢視";
          linkBar.appendChild(presentation);

          if (googleSlidesUrl) {
            const googleSlides = document.createElement("a");
            googleSlides.href = googleSlidesUrl;
            googleSlides.target = "_blank";
            googleSlides.textContent = "開啟 Google Slides";
            linkBar.appendChild(googleSlides);
          }
        }

        const modeText = data.execution_mode === "local_fallback" ? "本地 fallback" : "n8n";
        setStatus(`已完成。案件 ${data.case_id} 已生成。執行模式：${modeText}`);
        await loadGoogleStatus();
      } catch (error) {
        setStatus(error.message || "生成失敗", true);
      }
    });

    loadGoogleStatus();
  </script>
</body>
</html>
"""


@router.post("/workbench/generate")
def workbench_generate(req: IntakeRequest) -> JSONResponse:
    try:
        status_code, body = _post_to_n8n_webhook(req.model_dump(mode="json"))
        if status_code < 400 and not body.get("dead_letter"):
            return JSONResponse(status_code=status_code, content=body)
    except URLError:
        pass
    except Exception:
        pass

    status_code, body = _run_local_workbench(req)
    return JSONResponse(status_code=status_code, content=body)
