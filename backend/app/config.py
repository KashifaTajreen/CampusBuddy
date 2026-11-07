from functools import lru_cache
from pathlib import Path
from typing import Literal
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")
    database_url: str = "sqlite:///./data/campus.db"
    app_env: str = "development"
    frontend_origin: str = "http://localhost:3000"
    supabase_url: str = ""
    supabase_publishable_key: str = ""
    google_admin_email: str = ""
    google_admin_user_id: str = ""
    google_test_student_enabled: bool = False
    google_test_student_email: str = ""
    google_test_student_user_id: str = ""
    google_test_student_tenant_id: str = ""
    supabase_service_role_key: str = ""
    supabase_storage_bucket: str = "campus-sources"
    storage_backend: str = "local"
    embedding_model_path: str = ""
    embedding_provider: Literal["auto", "lexical", "onnx", "sentence_transformers"] = "auto"
    semantic_min_score: float = Field(default=0.78, ge=0, le=1)
    ocr_enabled: bool = True
    ocr_tessdata_path: str = str(ROOT / ".scratch" / "tessdata")
    ocr_languages: str = "eng"
    speech_enabled: bool = True
    speech_model_path: str = str(ROOT / ".scratch" / "whisper-tiny")
    redis_enabled: bool = False
    redis_url: str = ""
    cache_ttl_seconds: int = Field(default=30, ge=1, le=120)
    ingestion_queue_limit: int = Field(default=100, ge=1, le=1000)
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    llm_enabled: bool = False
    llm_provider: Literal["none", "gemini", "openrouter", "ollama"] = "none"
    llm_data_policy: Literal["disabled", "shared_only", "private_local", "approved_private"] = "disabled"
    llm_policy_reference: str = ""
    llm_timeout_seconds: float = Field(default=8, ge=0.1, le=12)
    openrouter_api_key: str = ""
    openrouter_model: str = ""
    ollama_base_url: str = ""
    ollama_model: str = ""
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:8000/v1/integrations/google/callback"
    token_encryption_key: str = ""
    campus_access_token: str = ""
    campus_tenant_id: str = ""
    mcp_allowed_hosts: list[str] = ["127.0.0.1:*", "localhost:*", "testserver"]
    mcp_allowed_origins: list[str] = ["http://127.0.0.1:*", "http://localhost:*"]

    def validate_production(self):
        if self.google_test_student_enabled and self.app_env not in ('development','test'):
            raise ValueError('The temporary Google test-student exception is development/test only. Disable it before deployment.')
        if self.app_env == "production":
            if not self.database_url.startswith("postgresql"):
                raise ValueError("Production requires PostgreSQL.")
            if not self.supabase_url.startswith("https://"):
                raise ValueError("Production requires HTTPS Supabase Auth.")
            if self.storage_backend != "supabase" or not self.supabase_service_role_key:
                raise ValueError("Production requires configured private Supabase Storage.")
            if not self.frontend_origin.startswith("https://"):
                raise ValueError("Production requires an HTTPS frontend origin.")


@lru_cache
def settings():
    return Settings()
