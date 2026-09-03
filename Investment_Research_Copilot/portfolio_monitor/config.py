"""
Configuration management for Investment Research Copilot.

Everything is read from environment variables (see .env.example). No secrets are
hard-coded. The client targets an OpenAI-compatible gateway, so the underlying model can be
a Claude / OSS / Azure deployment without changing call sites. The LLM is used for *language*
work only (news classification, briefing prose, construction reasoning, Q&A) — never for any
decision-bearing number.

The whole module is import-safe even when `langchain_openai` is not installed: `get_llm()`
raises only when actually called, so the fully deterministic, offline code paths
(allocation, drift, risk, scoring, the equal-weight constructor) run with no LLM and no
network.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

try:
    import dotenv
    dotenv.load_dotenv()
    dotenv.load_dotenv(".env")
except Exception:                                   # pragma: no cover - dotenv optional
    pass


@dataclass
class Config:
    """Application configuration."""
    
    # LLM Configuration
    llm_endpoint: str = ""
    llm_api_key: str = ""
    llm_model_name: str = "anthropic.claude-4-5-haiku"
    llm_temperature: float = 0.0
    llm_max_tokens: int = 4096
    
    # Database Configuration
    pg_host: str = "localhost"
    pg_port: int = 5433
    pg_database: str = "investment_research_db"
    pg_user: str = "postgres"
    pg_password: str = ""
    
    # Redis Configuration
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_password: Optional[str] = None
    redis_db: int = 0
    
    # Application Configuration
    log_level: str = "INFO"
    max_upload_size_mb: int = 100
    data_storage_dir: str = "./data/uploads"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    use_celery: bool = False
    
    @classmethod
    def from_env(cls) -> "Config":
        """Create configuration from environment variables."""
        return cls(
            llm_endpoint=os.getenv("LLM_ENDPOINT", "https://llm-dev.fdscloud.io/"),
            llm_api_key=os.getenv("LLM_API_KEY", ""),
            llm_model_name=os.getenv("LLM_MODEL_NAME", "anthropic.claude-4-5-haiku"),
            llm_temperature=float(os.getenv("LLM_TEMPERATURE", "0.0")),
            llm_max_tokens=int(os.getenv("LLM_MAX_TOKENS", "4096")),
            pg_host=os.getenv("PGHOST", "localhost"),
            pg_port=int(os.getenv("PGPORT", "5432")),
            pg_database=os.getenv("PGDATABASE", "investment_research_db"),
            pg_user=os.getenv("PGUSER", "investment_research_user"),
            pg_password=os.getenv("PGPASSWORD", ""),
            redis_host=os.getenv("REDISHOST", "localhost"),
            redis_port=int(os.getenv("REDISPORT", "6379")),
            redis_password=os.getenv("REDIS_PASSWORD"),
            redis_db=int(os.getenv("REDIS_DB", "0")),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            max_upload_size_mb=int(os.getenv("MAX_UPLOAD_SIZE_MB", "100")),
            data_storage_dir=os.getenv("DATA_STORAGE_DIR", "./data/uploads"),
            api_host=os.getenv("PM_API_HOST", "0.0.0.0"),
            api_port=int(os.getenv("PM_API_PORT", "8000")),
            use_celery=os.getenv("USE_CELERY", "").lower() in ("1", "true", "yes"),
        )


# Global configuration instance
_config: Optional[Config] = None


def get_config() -> Config:
    """Get the global configuration instance."""
    global _config
    if _config is None:
        _config = Config.from_env()
    return _config


def llm_available() -> bool:
    """True if an LLM client can plausibly be built (library importable + key set)."""
    try:
        import langchain_openai  # noqa: F401
    except Exception:
        return False
    config = get_config()
    return bool(config.llm_api_key) and "your-gateway.example.com" not in config.llm_endpoint


def get_llm():
    """Return a chat LLM client pointed at the configured gateway.

    Imported lazily so the deterministic, offline paths never require langchain_openai.
    """
    from langchain_openai import ChatOpenAI
    config = get_config()

    return ChatOpenAI(
        model=config.llm_model_name,
        api_key=config.llm_api_key,
        base_url=config.llm_endpoint,
        temperature=config.llm_temperature,
        max_tokens=config.llm_max_tokens,
    )
