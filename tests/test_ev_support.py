from __future__ import annotations

import base64
import hashlib
import hmac
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.api import routes
from app.ev_support.faq_store import LocalFAQRetriever
from app.ev_support.models import EVSupportRequest


client = TestClient(app)


def _configure_test_settings(**overrides: object) -> None:
    routes.config = routes.config.__class__(**{**routes.config.__dict__, **overrides})
    routes.get_chat_log_repository.cache_clear()
    routes.get_ev_support_service.cache_clear()


def test_ev_support_respond_returns_thai_reply() -> None:
    with TemporaryDirectory() as tmpdir:
        _configure_test_settings(
            ev_default_language="en-US",
            ev_enable_thai_after_setup=False,
            ev_chat_log_db_path=str(Path(tmpdir) / "chat_logs.db"),
        )
        response = client.post(
            "/api/ev-support/respond",
            json=EVSupportRequest(
                session_id="line:U123",
                user_id="U123",
                message_text="เสียบหัวชาร์จแล้ว แต่ยังไม่เริ่มชาร์จ",
                channel="line",
                language="th-TH",
            ).model_dump(mode="json"),
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["detected_intent"] == "start_charge"
        assert "ชาร์จ" in payload["reply_text"]
        assert payload["knowledge_hits"]

        logs = client.get("/api/ev-support/logs", params={"key": routes.config.api_auth_key or ""})
        assert logs.status_code == 200
        assert logs.json()["count"] >= 2


def test_ev_support_debug_respond_requires_api_key_and_returns_chinese_faq() -> None:
    _configure_test_settings(
        api_auth_key="test-key",
        ev_default_language="en-US",
        ev_enable_thai_after_setup=False,
    )

    forbidden = client.post(
        "/api/ev-support/debug/respond",
        json=EVSupportRequest(
            session_id="line:Udebug",
            user_id="Udebug",
            message_text="已扣款但未成功充電",
            channel="line",
            language="zh-HK",
        ).model_dump(mode="json"),
    )
    assert forbidden.status_code == 401

    allowed = client.post(
        "/api/ev-support/debug/respond?key=test-key",
        json=EVSupportRequest(
            session_id="line:Udebug",
            user_id="Udebug",
            message_text="已扣款但未成功充電",
            channel="line",
            language="zh-HK",
        ).model_dump(mode="json"),
    )

    assert allowed.status_code == 200
    payload = allowed.json()
    assert payload["detected_language_before_reply"] == "zh-HK"
    assert payload["response"]["detected_intent"] == "refund"
    assert payload["response"]["knowledge_hits"] == ["faq-021-refund-request-zh"]


def test_ev_support_debug_faq_reports_loaded_corpus() -> None:
    _configure_test_settings(
        api_auth_key="test-key",
        ev_faq_path="/Users/thomasmok/Documents/Playground/data/xeco_faq_corpus.json",
    )

    forbidden = client.get("/api/ev-support/debug/faq")
    assert forbidden.status_code == 401

    allowed = client.get("/api/ev-support/debug/faq?key=test-key")
    assert allowed.status_code == 200
    payload = allowed.json()
    assert payload["exists"] is True
    assert payload["entry_count"] > 0
    assert payload["faq_path"].endswith("xeco_faq_corpus.json")


def test_ev_support_debug_runtime_reports_llm_and_media_config() -> None:
    _configure_test_settings(
        api_auth_key="test-key",
        llm_provider="openai",
        openai_api_key="sk-test",
        openai_model="gpt-4.1-mini",
        openai_transcribe_model="gpt-4o-mini-transcribe",
        line_support_group_id="Ctestsupportgroup",
        ev_media_storage_dir="/Users/thomasmok/Documents/Playground/data/line_media",
    )

    forbidden = client.get("/api/ev-support/debug/runtime")
    assert forbidden.status_code == 401

    allowed = client.get("/api/ev-support/debug/runtime?key=test-key")
    assert allowed.status_code == 200
    payload = allowed.json()
    assert payload["llm_provider"] == "openai"
    assert payload["openai_api_key_present"] is True
    assert payload["openai_model"] == "gpt-4.1-mini"
    assert payload["openai_transcribe_model"] == "gpt-4o-mini-transcribe"
    assert payload["line_support_group_id"] == "Ctestsupportgroup"
    assert payload["ev_media_storage_dir"].endswith("line_media")


def test_ev_support_respond_returns_chinese_for_chinese_input() -> None:
    _configure_test_settings(
        ev_default_language="en-US",
        ev_enable_thai_after_setup=False,
    )
    response = client.post(
        "/api/ev-support/respond",
        json=EVSupportRequest(
            session_id="line:Uzh",
            user_id="Uzh",
            message_text="掃 Code 但充唔到電",
            channel="line",
            language="en-US",
        ).model_dump(mode="json"),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["knowledge_hits"]
    assert any("\u4e00" <= ch <= "\u9fff" for ch in payload["reply_text"])


def test_ev_support_respond_prefers_chinese_for_mixed_input() -> None:
    _configure_test_settings(
        ev_default_language="en-US",
        ev_enable_thai_after_setup=False,
    )
    response = client.post(
        "/api/ev-support/respond",
        json=EVSupportRequest(
            session_id="line:Umix",
            user_id="Umix",
            message_text="掃 Code but charging did not start",
            channel="line",
            language="en-US",
        ).model_dump(mode="json"),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["knowledge_hits"]
    assert any("\u4e00" <= ch <= "\u9fff" for ch in payload["reply_text"])


def test_ev_support_respond_grounds_cantonese_location_query_to_chinese_faq() -> None:
    _configure_test_settings(
        ev_default_language="en-US",
        ev_enable_thai_after_setup=False,
    )
    response = client.post(
        "/api/ev-support/respond",
        json=EVSupportRequest(
            session_id="line:Uzhloc",
            user_id="Uzhloc",
            message_text="就嚟可以找到充電地方",
            channel="line",
            language="zh-HK",
        ).model_dump(mode="json"),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["detected_intent"] == "charging_locations"
    assert payload["knowledge_hits"][0] == "faq-018-charging-locations-zh"
    assert "官方網站" in payload["reply_text"]


def test_ev_support_respond_grounds_chinese_refund_query_to_refund_faq() -> None:
    _configure_test_settings(
        ev_default_language="en-US",
        ev_enable_thai_after_setup=False,
    )
    response = client.post(
        "/api/ev-support/respond",
        json=EVSupportRequest(
            session_id="line:Uzhrefund",
            user_id="Uzhrefund",
            message_text="已扣款但未成功充電",
            channel="line",
            language="zh-HK",
        ).model_dump(mode="json"),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["detected_intent"] == "refund"
    assert payload["knowledge_hits"] == ["faq-021-refund-request-zh"]
    assert "退款" in payload["reply_text"]


def test_ev_support_respond_grounds_chinese_password_issue_to_password_faq() -> None:
    _configure_test_settings(
        ev_default_language="en-US",
        ev_enable_thai_after_setup=False,
    )
    response = client.post(
        "/api/ev-support/respond",
        json=EVSupportRequest(
            session_id="line:Uzhpwd",
            user_id="Uzhpwd",
            message_text="密碼錯誤點算？",
            channel="line",
            language="zh-HK",
        ).model_dump(mode="json"),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["detected_intent"] == "password_reset"
    assert payload["knowledge_hits"][0] == "faq-002-password-reset-zh"
    assert "重設密碼" in payload["reply_text"]


def test_line_webhook_generates_local_reply_without_token() -> None:
    with TemporaryDirectory() as tmpdir:
        _configure_test_settings(
            line_channel_secret=None,
            line_channel_access_token=None,
            line_support_group_id=None,
            ev_n8n_webhook_url=None,
            ev_default_language="en-US",
            ev_enable_thai_after_setup=False,
            ev_chat_log_db_path=str(Path(tmpdir) / "chat_logs.db"),
        )

        response = client.post(
            "/api/ev-support/line/webhook",
            json={
                "destination": "dest",
                "events": [
                    {
                        "type": "message",
                        "replyToken": "reply-token",
                        "timestamp": 1710000000000,
                        "source": {"type": "user", "userId": "U999"},
                        "message": {"id": "mid-1", "type": "text", "text": "ขอคืนเงิน ชาร์จไม่ติด"},
                    }
                ],
            },
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["ok"] is True
        assert payload["results"][0]["status"] == "generated_locally"
        assert payload["results"][0]["detected_intent"] == "refund"
        assert "คืนเงิน" in payload["results"][0]["reply_text"]


def test_line_webhook_validates_signature_when_secret_is_set() -> None:
    secret = "test-secret"
    _configure_test_settings(
        line_channel_secret=secret,
        line_channel_access_token=None,
        ev_n8n_webhook_url=None,
        ev_default_language="en-US",
        ev_enable_thai_after_setup=False,
    )
    body = (
        '{"destination":"dest","events":[{"type":"message","replyToken":"reply-token",'
        '"timestamp":1710000000000,"source":{"type":"user","userId":"Usig"},'
        '"message":{"id":"mid-2","type":"text","text":"สถานีออฟไลน์"}}]}'
    ).encode("utf-8")
    signature = base64.b64encode(hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()).decode("utf-8")

    ok_response = client.post(
        "/api/ev-support/line/webhook",
        content=body,
        headers={"X-Line-Signature": signature, "Content-Type": "application/json"},
    )
    assert ok_response.status_code == 200

    bad_response = client.post(
        "/api/ev-support/line/webhook",
        content=body,
        headers={"X-Line-Signature": "bad-signature", "Content-Type": "application/json"},
    )
    assert bad_response.status_code == 401


def test_thai_faq_retrieval_matches_station_location_variants() -> None:
    retriever = LocalFAQRetriever("data/xeco_faq_corpus.json")

    hits = retriever.retrieve("สถานีชาร์จอยู่ที่ไหน", language="th-TH", top_k=3)

    assert hits
    assert hits[0].entry.doc_id == "faq-018-charging-locations-th"


def test_thai_faq_retrieval_prefers_refund_entry_for_refund_question() -> None:
    retriever = LocalFAQRetriever("data/xeco_faq_corpus.json")

    hits = retriever.retrieve("ขอคืนเงิน ชาร์จไม่ติด", language="th-TH", top_k=3)

    assert hits
    assert hits[0].entry.doc_id == "faq-021-refund-request-th"


def test_ev_support_respond_grounds_station_lookup_to_faq() -> None:
    _configure_test_settings(
        ev_default_language="en-US",
        ev_enable_thai_after_setup=False,
    )
    response = client.post(
        "/api/ev-support/respond",
        json=EVSupportRequest(
            session_id="line:Ustation",
            user_id="Ustation",
            message_text="Where is the nearest charging station?",
            channel="line",
            language="en-US",
        ).model_dump(mode="json"),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["knowledge_hits"]
    assert payload["knowledge_hits"][0] == "faq-018-charging-locations-en"
    assert "official website" in payload["reply_text"].lower()


def test_ev_support_respond_does_not_use_unrelated_faq_for_station_offline() -> None:
    _configure_test_settings(
        ev_default_language="en-US",
        ev_enable_thai_after_setup=False,
    )
    response = client.post(
        "/api/ev-support/respond",
        json=EVSupportRequest(
            session_id="line:Uoffline",
            user_id="Uoffline",
            message_text="สถานีออฟไลน์",
            channel="line",
            language="th-TH",
        ).model_dump(mode="json"),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["detected_intent"] == "station_offline"
    assert payload["knowledge_hits"] == ["station_offline"]
    assert "ออฟไลน์" in payload["reply_text"]


def test_line_webhook_forwards_detected_language_to_n8n() -> None:
    _configure_test_settings(
        line_channel_secret=None,
        line_channel_access_token=None,
        ev_n8n_webhook_url="https://example.invalid/webhook/ev-line-support",
        ev_default_language="en-US",
        ev_enable_thai_after_setup=False,
    )

    with patch("app.api.routes._post_to_ev_n8n_webhook", return_value=(200, {"ok": True})) as post_mock:
        response = client.post(
            "/api/ev-support/line/webhook",
            json={
                "destination": "dest",
                "events": [
                    {
                        "type": "message",
                        "replyToken": "reply-token",
                        "timestamp": 1710000000000,
                        "source": {"type": "user", "userId": "Uthai"},
                        "message": {"id": "mid-3", "type": "text", "text": "สถานีออฟไลน์"},
                    }
                ],
            },
        )

    assert response.status_code == 200
    forwarded_payload = post_mock.call_args.args[0]
    assert forwarded_payload["language"] == "th-TH"


def test_line_webhook_allows_customer_to_resume_bot_from_direct_chat() -> None:
    with TemporaryDirectory() as tmpdir:
        _configure_test_settings(
            line_channel_secret=None,
            line_channel_access_token=None,
            ev_n8n_webhook_url=None,
            ev_default_language="en-US",
            ev_enable_thai_after_setup=False,
            ev_chat_log_db_path=str(Path(tmpdir) / "chat_logs.db"),
        )

        handoff_response = client.post(
            "/api/ev-support/line/webhook",
            json={
                "destination": "dest",
                "events": [
                    {
                        "type": "message",
                        "replyToken": "reply-token",
                        "timestamp": 1710000000000,
                        "source": {"type": "user", "userId": "Uresume"},
                        "message": {"id": "mid-4", "type": "text", "text": "客服"},
                    }
                ],
            },
        )
        assert handoff_response.status_code == 200
        assert handoff_response.json()["results"][0]["status"] == "human_handoff_started"

        resume_response = client.post(
            "/api/ev-support/line/webhook",
            json={
                "destination": "dest",
                "events": [
                    {
                        "type": "message",
                        "replyToken": "reply-token",
                        "timestamp": 1710000001000,
                        "source": {"type": "user", "userId": "Uresume"},
                        "message": {"id": "mid-5", "type": "text", "text": "resume bot"},
                    }
                ],
            },
        )

        assert resume_response.status_code == 200
        resume_payload = resume_response.json()
        assert resume_payload["results"][0]["status"] == "human_handoff_closed"
        assert "bot" in resume_payload["results"][0]["reply_text"].lower()

        normal_response = client.post(
            "/api/ev-support/line/webhook",
            json={
                "destination": "dest",
                "events": [
                    {
                        "type": "message",
                        "replyToken": "reply-token",
                        "timestamp": 1710000002000,
                        "source": {"type": "user", "userId": "Uresume"},
                        "message": {"id": "mid-6", "type": "text", "text": "Where is the nearest charging station?"},
                    }
                ],
            },
        )

        assert normal_response.status_code == 200
        normal_payload = normal_response.json()
        assert normal_payload["results"][0]["status"] == "generated_locally"
        assert normal_payload["results"][0]["detected_intent"] == "charging_locations"


def test_line_webhook_handoff_state_persists_across_service_rebuild() -> None:
    with TemporaryDirectory() as tmpdir:
        _configure_test_settings(
            line_channel_secret=None,
            line_channel_access_token=None,
            line_support_group_id=None,
            ev_n8n_webhook_url=None,
            ev_default_language="en-US",
            ev_enable_thai_after_setup=False,
            ev_chat_log_db_path=str(Path(tmpdir) / "chat_logs.db"),
            ev_session_db_path=str(Path(tmpdir) / "ev_support_sessions.db"),
        )

        handoff_response = client.post(
            "/api/ev-support/line/webhook",
            json={
                "destination": "dest",
                "events": [
                    {
                        "type": "message",
                        "replyToken": "reply-token",
                        "timestamp": 1710000003000,
                        "source": {"type": "user", "userId": "Upersist"},
                        "message": {"id": "mid-9", "type": "text", "text": "客服"},
                    }
                ],
            },
        )
        assert handoff_response.status_code == 200
        assert handoff_response.json()["results"][0]["status"] == "human_handoff_started"

        routes.get_ev_support_service.cache_clear()

        followup_response = client.post(
            "/api/ev-support/line/webhook",
            json={
                "destination": "dest",
                "events": [
                    {
                        "type": "message",
                        "replyToken": "reply-token",
                        "timestamp": 1710000004000,
                        "source": {"type": "user", "userId": "Upersist"},
                        "message": {"id": "mid-10", "type": "text", "text": "ยังไม่มีคนตอบ"},
                    }
                ],
            },
        )

        assert followup_response.status_code == 200
        assert followup_response.json()["results"][0]["status"] == "human_handoff_active"


def test_line_webhook_starts_handoff_for_cantonese_cs_text() -> None:
    _configure_test_settings(
        line_channel_secret=None,
        line_channel_access_token=None,
        line_support_group_id=None,
        ev_n8n_webhook_url=None,
        ev_default_language="en-US",
        ev_enable_thai_after_setup=False,
    )

    response = client.post(
        "/api/ev-support/line/webhook",
        json={
            "destination": "dest",
            "events": [
                {
                    "type": "message",
                    "replyToken": "reply-token",
                    "timestamp": 1710000010000,
                    "source": {"type": "user", "userId": "UcsText"},
                    "message": {"id": "mid-7", "type": "text", "text": "唔該轉去CS"},
                }
            ],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["results"][0]["status"] == "human_handoff_started"


def test_line_webhook_starts_handoff_for_audio_transcript_request() -> None:
    _configure_test_settings(
        line_channel_secret=None,
        line_channel_access_token=None,
        line_support_group_id=None,
        ev_n8n_webhook_url=None,
        ev_default_language="en-US",
        ev_enable_thai_after_setup=False,
    )

    service = routes.get_ev_support_service()
    stored = SimpleNamespace(file_path="/tmp/test-audio.m4a")

    with (
        patch.object(service, "fetch_and_store_line_media", return_value=stored),
        patch.object(service, "analyze_media", return_value="我意思係客戶服務,完"),
    ):
        response = client.post(
            "/api/ev-support/line/webhook",
            json={
                "destination": "dest",
                "events": [
                    {
                        "type": "message",
                        "replyToken": "reply-token",
                        "timestamp": 1710000011000,
                        "source": {"type": "user", "userId": "UcsAudio"},
                        "message": {"id": "mid-8", "type": "audio", "duration": 1000},
                    }
                ],
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["results"][0]["status"] == "human_handoff_started"
    assert "客服" in payload["results"][0]["reply_text"] or "support" in payload["results"][0]["reply_text"].lower()


def test_support_group_can_reply_to_customer_session() -> None:
    _configure_test_settings(
        line_channel_secret=None,
        line_channel_access_token=None,
        line_support_group_id="Csupportgroup",
        ev_n8n_webhook_url=None,
        ev_default_language="en-US",
        ev_enable_thai_after_setup=False,
    )

    service = routes.get_ev_support_service()
    service.activate_human_handoff(
        session_id="line:Ureply",
        user_id="Ureply",
        channel="line",
        language="en-US",
        reason="customer_requested_human",
    )

    response = client.post(
        "/api/ev-support/line/webhook",
        json={
            "destination": "dest",
            "events": [
                {
                    "type": "message",
                    "replyToken": "reply-token",
                    "timestamp": 1710000012000,
                    "source": {"type": "group", "groupId": "Csupportgroup"},
                    "message": {"id": "mid-11", "type": "text", "text": "reply Ureply We are checking your case now."},
                }
            ],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["results"][0]["status"] == "support_group_reply_sent"
    assert payload["results"][0]["session_id"] == "line:Ureply"


def test_support_group_reply_requires_existing_session() -> None:
    _configure_test_settings(
        line_channel_secret=None,
        line_channel_access_token=None,
        line_support_group_id="Csupportgroup",
        ev_n8n_webhook_url=None,
        ev_default_language="en-US",
        ev_enable_thai_after_setup=False,
    )

    response = client.post(
        "/api/ev-support/line/webhook",
        json={
            "destination": "dest",
            "events": [
                {
                    "type": "message",
                    "replyToken": "reply-token",
                    "timestamp": 1710000013000,
                    "source": {"type": "group", "groupId": "Csupportgroup"},
                    "message": {"id": "mid-12", "type": "text", "text": "reply Umissing Please contact us later."},
                }
            ],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["results"][0]["status"] == "support_group_reply_not_found"
    assert payload["results"][0]["session_id"] == "line:Umissing"


def test_support_group_reply_help_text_mentions_short_syntax() -> None:
    service = routes.get_ev_support_service()
    help_text = service.support_group_reply_help_text()

    assert "reply <user_id> <message>" in help_text
    assert "reply U1234567890" in help_text
