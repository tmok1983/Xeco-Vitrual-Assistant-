from __future__ import annotations

import base64
import hashlib
import hmac
from pathlib import Path
from tempfile import TemporaryDirectory

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


def test_line_webhook_generates_local_reply_without_token() -> None:
    with TemporaryDirectory() as tmpdir:
        _configure_test_settings(
            line_channel_secret=None,
            line_channel_access_token=None,
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
