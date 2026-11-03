from functools import lru_cache
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")
    database_url: str = "sqlite:///./data/campus.db"
    app_env: str = "development"
    frontend_origin: str = "http://localhost:3000"
    supabase_url: str = ""
    supabase_publishable_key: str = ""
    supabase_service_role_key: str = ""
    supabase_storage_bucket: str = "campus-sources"
    storage_backend: str = "local"
    embedding_model_path: str = ""
    # auto | none | ollama | gemini. "auto" uses Gemini only when a key is set; without an LLM
    # the assistant stays deterministic and extractive.
    llm_provider: str = "auto"
    rate_limit_per_minute: int = 120
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen3.5:9b"
    ollama_embedding_model: str = ""
    ollama_num_ctx: int = 8192
    llm_timeout_seconds: float = 90
    # regex | hybrid. Hybrid sends longer or unmatched questions to the tool-calling agent.
    routing_mode: str = "regex"
    reranker_model_path: str = ""
    retrieval_min_similarity: float = 0.45
    # pymupdf | docling
    document_parser: str = "pymupdf"
    speech_model: str = "data/models/faster-whisper-small"
    speech_language: str = "en"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:8000/v1/integrations/google/callback"
    token_encryption_key: str = ""
    campus_access_token: str = ""
    campus_tenant_id: str = ""
    mcp_allowed_hosts: list[str] = ["127.0.0.1:*", "localhost:*", "testserver"]
    mcp_allowed_origins: list[str] = ["http://127.0.0.1:*", "http://localhost:*"]

    def validate_production(self):
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
