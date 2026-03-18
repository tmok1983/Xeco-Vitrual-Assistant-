from __future__ import annotations

from app.core.config import AppConfig
from app.core.service import InsuranceOrchestrationService
from app.data.repository import InMemoryCaseRepository, PostgresCaseRepository
from app.ev_support.faq_store import LocalFAQRetriever
from app.ev_support.log_repository import SQLiteChatLogRepository
from app.ev_support.media_store import LocalMediaStore
from app.ev_support.repository import SQLiteEVSupportRepository
from app.ev_support.service import EVSupportService
from app.llm.prompt_registry import PromptRegistry
from app.llm.providers import GeminiClient, LLMClient, MockLLMClient, OpenAIClient


def build_repository(cfg: AppConfig):
    if cfg.data_backend in {"postgres", "supabase"}:
        if not cfg.database_url:
            raise ValueError("DATABASE_URL is required for DATA_BACKEND=postgres/supabase")
        return PostgresCaseRepository(cfg.database_url)
    return InMemoryCaseRepository()


def build_llm_client(cfg: AppConfig) -> LLMClient:
    if cfg.llm_provider == "openai" and cfg.openai_api_key:
        return OpenAIClient(api_key=cfg.openai_api_key, model=cfg.openai_model, transcribe_model=cfg.openai_transcribe_model)
    if cfg.llm_provider == "gemini" and cfg.gemini_api_key:
        return GeminiClient(api_key=cfg.gemini_api_key, model=cfg.gemini_model)
    return MockLLMClient()


def build_service() -> InsuranceOrchestrationService:
    cfg = AppConfig.from_env()
    repo = build_repository(cfg)
    llm = build_llm_client(cfg)
    prompts = PromptRegistry()
    return InsuranceOrchestrationService(repo=repo, llm=llm, prompts=prompts, config=cfg)


def build_ev_support_service() -> EVSupportService:
    cfg = AppConfig.from_env()
    llm = build_llm_client(cfg)
    return EVSupportService(
        faq_retriever=LocalFAQRetriever(cfg.ev_faq_path),
        media_store=LocalMediaStore(cfg.ev_media_storage_dir),
        repo=SQLiteEVSupportRepository(cfg.ev_session_db_path),
        llm=llm,
        config=cfg,
    )


def build_chat_log_repository() -> SQLiteChatLogRepository:
    cfg = AppConfig.from_env()
    return SQLiteChatLogRepository(cfg.ev_chat_log_db_path)
