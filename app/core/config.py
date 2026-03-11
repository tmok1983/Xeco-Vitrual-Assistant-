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

    @staticmethod
    def from_env() -> "AppConfig":
        _load_dotenv()
        return AppConfig(
            data_backend=os.getenv("DATA_BACKEND", "memory").lower(),
            database_url=os.getenv("DATABASE_URL"),
            llm_provider=os.getenv("LLM_PROVIDER", "mock").lower(),
            openai_api_key=os.getenv("OPENAI_API_KEY"),
            openai_model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
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
        )
