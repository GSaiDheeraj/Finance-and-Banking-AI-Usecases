"""
Configuration management for Fraud Detection Agent.

Everything is read from environment variables (see .env.example). No secrets are
hard-coded. The clients target an OpenAI-compatible gateway, so the underlying model
can be a Claude / OSS / Azure deployment without changing call sites.
"""
import os
from dataclasses import dataclass
from typing import Optional

import dotenv

from langchain_openai import ChatOpenAI, OpenAIEmbeddings

dotenv.load_dotenv()
dotenv.load_dotenv(".env")


@dataclass
class Config:
    """Application configuration."""
    
    # LLM Configuration
    llm_endpoint: str = ""
    llm_api_key: str = ""
    llm_model_name: str = "anthropic.claude-4-5-haiku"
    llm_temperature: float = 0.0
    llm_max_tokens: int = 4096
    
    # Embedding Configuration
    embed_model_name: str = "openai.text-embedding-3-large"
    embed_dim: int = 1536
    
    # Database Configuration
    pg_host: str = "localhost"
    pg_port: int = 5433
    pg_database: str = "fraud_detection_db"
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
    document_storage_dir: str = "./data/uploads"
    
    @classmethod
    def from_env(cls) -> "Config":
        """Create configuration from environment variables."""
        return cls(
            llm_endpoint=os.getenv("LLM_ENDPOINT", "https://llm-dev.fdscloud.io/"),
            llm_api_key=os.getenv("LLM_API_KEY", ""),
            llm_model_name=os.getenv("LLM_MODEL_NAME", "anthropic.claude-4-5-haiku"),
            llm_temperature=float(os.getenv("LLM_TEMPERATURE", "0.0")),
            llm_max_tokens=int(os.getenv("LLM_MAX_TOKENS", "4096")),
            embed_model_name=os.getenv("EMBED_MODEL_NAME", "openai.text-embedding-3-large"),
            embed_dim=int(os.getenv("EMBED_DIM", "1536")),
            pg_host=os.getenv("PGHOST", "localhost"),
            pg_port=int(os.getenv("PGPORT", "5432")),
            pg_database=os.getenv("PGDATABASE", "fraud_detection_db"),
            pg_user=os.getenv("PGUSER", "fraud_detection_user"),
            pg_password=os.getenv("PGPASSWORD", ""),
            redis_host=os.getenv("REDISHOST", "localhost"),
            redis_port=int(os.getenv("REDISPORT", "6379")),
            redis_password=os.getenv("REDIS_PASSWORD"),
            redis_db=int(os.getenv("REDIS_DB", "0")),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            max_upload_size_mb=int(os.getenv("MAX_UPLOAD_SIZE_MB", "100")),
            document_storage_dir=os.getenv("DOCUMENT_STORAGE_DIR", "./data/uploads"),
        )


# Global configuration instance
_config: Optional[Config] = None


def get_config() -> Config:
    """Get the global configuration instance."""
    global _config
    if _config is None:
        _config = Config.from_env()
    return _config


def get_llm() -> ChatOpenAI:
    """Return a chat LLM client pointed at the configured gateway."""
    config = get_config()
    return ChatOpenAI(
        model=config.llm_model_name,
        api_key=config.llm_api_key,
        base_url=config.llm_endpoint,
        temperature=config.llm_temperature,
        max_tokens=config.llm_max_tokens,
    )


def get_embedder() -> OpenAIEmbeddings:
    """Return an embedding client pointed at the configured gateway."""
    config = get_config()
    return OpenAIEmbeddings(
        model=config.embed_model_name,
        api_key=config.llm_api_key,  # Use same API key for embeddings
        base_url=config.llm_endpoint,
    )
