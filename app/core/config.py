from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _load_dotenv() -> None:
    env_file = Path(__file__).resolve().parents[2] / ".env"
    if not env_file.exists():
        return

    for line in env_file.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


@dataclass(frozen=True)
class AppConfig:
    data_backend: str = "memory"
    database_url: str | None = None
    llm_provider: str = "mock"
    openai_api_key: str | None = None
    openai_model: str = "gpt-4.1-mini"
    openai_transcribe_model: str = "whisper-1"
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-2.0-flash"
    api_auth_key: str | None = None
    n8n_webhook_url: str = "https://hkbt.app.n8n.cloud/webhook/insurance-intake-codex-v3"
    google_slides_enabled: bool = False
    google_service_account_file: str | None = None
    google_slides_folder_id: str | None = None
    google_oauth_enabled: bool = False
    google_oauth_client_secret_file: str | None = None
    google_oauth_token_file: str | None = None
    google_oauth_redirect_uri: str = "http://127.0.0.1:8000/api/google/callback"
    line_channel_secret: str | None = None
    line_channel_access_token: str | None = None
    line_reply_api_url: str = "https://api.line.me/v2/bot/message/reply"
    line_push_api_url: str = "https://api.line.me/v2/bot/message/push"
    line_content_api_base_url: str = "https://api-data.line.me/v2/bot/message"
    line_support_group_id: str | None = None
    ev_n8n_webhook_url: str | None = None
    ev_bot_display_name: str = "EV Charging Support"
    ev_default_language: str = "en-US"
    ev_enable_thai_after_setup: bool = False
    ev_faq_path: str = str(Path(__file__).resolve().parents[2] / "data" / "ev_faq.json")
    ev_chat_log_db_path: str = str(Path(__file__).resolve().parents[2] / "data" / "chat_logs.db")
    ev_session_db_path: str = str(Path(__file__).resolve().parents[2] / "data" / "ev_support_sessions.db")
    ev_media_storage_dir: str = str(Path(__file__).resolve().parents[2] / "data" / "line_media")

    @staticmethod
    def from_env() -> "AppConfig":
        _load_dotenv()
        default_chat_log_db_path = str(Path(__file__).resolve().parents[2] / "data" / "chat_logs.db")
        chat_log_db_path = os.getenv("EV_CHAT_LOG_DB_PATH", default_chat_log_db_path)
        session_db_path = os.getenv(
            "EV_SESSION_DB_PATH",
            str(Path(chat_log_db_path).with_name("ev_support_sessions.db")),
        )
        return AppConfig(
            data_backend=os.getenv("DATA_BACKEND", "memory").lower(),
            database_url=os.getenv("DATABASE_URL"),
            llm_provider=os.getenv("LLM_PROVIDER", "mock").lower(),
            openai_api_key=os.getenv("OPENAI_API_KEY"),
            openai_model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
            openai_transcribe_model=os.getenv("OPENAI_TRANSCRIBE_MODEL", "whisper-1"),
            gemini_api_key=os.getenv("GEMINI_API_KEY"),
            gemini_model=os.getenv("GEMINI_MODEL", "gemini-2.0-flash"),
            api_auth_key=os.getenv("API_AUTH_KEY"),
            n8n_webhook_url=os.getenv(
                "N8N_WEBHOOK_URL",
                "https://hkbt.app.n8n.cloud/webhook/insurance-intake-codex-v3",
            ),
            google_slides_enabled=os.getenv("GOOGLE_SLIDES_ENABLED", "false").lower() == "true",
            google_service_account_file=os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE"),
            google_slides_folder_id=os.getenv("GOOGLE_SLIDES_FOLDER_ID"),
            google_oauth_enabled=os.getenv("GOOGLE_OAUTH_ENABLED", "false").lower() == "true",
            google_oauth_client_secret_file=os.getenv("GOOGLE_OAUTH_CLIENT_SECRET_FILE"),
            google_oauth_token_file=os.getenv(
                "GOOGLE_OAUTH_TOKEN_FILE",
                str(Path(__file__).resolve().parents[2] / "credentials" / "google-oauth-token.json"),
            ),
            google_oauth_redirect_uri=os.getenv(
                "GOOGLE_OAUTH_REDIRECT_URI",
                "http://127.0.0.1:8000/api/google/callback",
            ),
            line_channel_secret=os.getenv("LINE_CHANNEL_SECRET"),
            line_channel_access_token=os.getenv("LINE_CHANNEL_ACCESS_TOKEN"),
            line_reply_api_url=os.getenv(
                "LINE_REPLY_API_URL",
                "https://api.line.me/v2/bot/message/reply",
            ),
            line_push_api_url=os.getenv(
                "LINE_PUSH_API_URL",
                "https://api.line.me/v2/bot/message/push",
            ),
            line_content_api_base_url=os.getenv(
                "LINE_CONTENT_API_BASE_URL",
                "https://api-data.line.me/v2/bot/message",
            ),
            line_support_group_id=os.getenv("LINE_SUPPORT_GROUP_ID"),
            ev_n8n_webhook_url=os.getenv("EV_N8N_WEBHOOK_URL"),
            ev_bot_display_name=os.getenv("EV_BOT_DISPLAY_NAME", "EV Charging Support"),
            ev_default_language=os.getenv("EV_DEFAULT_LANGUAGE", "en-US"),
            ev_enable_thai_after_setup=os.getenv("EV_ENABLE_THAI_AFTER_SETUP", "false").lower() == "true",
            ev_faq_path=os.getenv(
                "EV_FAQ_PATH",
                str(Path(__file__).resolve().parents[2] / "data" / "ev_faq.json"),
            ),
            ev_chat_log_db_path=chat_log_db_path,
            ev_session_db_path=session_db_path,
            ev_media_storage_dir=os.getenv(
                "EV_MEDIA_STORAGE_DIR",
                str(Path(__file__).resolve().parents[2] / "data" / "line_media"),
            ),
        )
