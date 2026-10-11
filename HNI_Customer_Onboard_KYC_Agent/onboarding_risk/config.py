"""
Configuration management for HNI Customer Onboard KYC Agent.

Everything is read from environment variables (see .env.example). No secrets are
hard-coded.

Chat LLM resolution (mirrors Credit_Risk_Assesment_Agent/credit_risk/config.py):
1. OpenAI-compatible gateway (ChatOpenAI with LLM_ENDPOINT) — tried first, when set.
2. Gemini via plain `requests` (see `gemini_requests.py`) — used when the OpenAI
   chat model is not configured / unreachable and GEMINI_API_KEY is set.

Embedding resolution:
1. OpenAI-compatible gateway (OpenAIEmbeddings) — tried first, when reachable.
2. Local HuggingFace sentence-transformers (no API key needed).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

import dotenv

dotenv.load_dotenv()

# Optional imports - these are only used when the respective providers are configured
try:
    from langchain_openai import ChatOpenAI, OpenAIEmbeddings
except ImportError:
    ChatOpenAI = None
    OpenAIEmbeddings = None

try:
    from langchain_huggingface import HuggingFaceEmbeddings
except ImportError:
    try:
        from langchain_community.embeddings import HuggingFaceEmbeddings  # type: ignore[no-redef]
    except ImportError:
        HuggingFaceEmbeddings = None

try:
    from .gemini_requests import GeminiChat, list_gemini_models
except ImportError:
    GeminiChat = None
    list_gemini_models = None

_log = logging.getLogger(__name__)

# --- Gemini chat fallback (override via .env) ---------------------------------
DEFAULT_GEMINI_MODEL_NAME = "gemini-3.5-flash-lite"
# Local HuggingFace embedding fallback (no API key needed)
DEFAULT_HF_EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

_PLACEHOLDER_ENDPOINT_FRAGMENT = "your-gateway.example.com"
_PLACEHOLDER_KEYS = {"", "replace-me", "your-api-key-here", "your-gemini-api-key-here"}


def _is_placeholder(value: str) -> bool:
    return (
        (not value)
        or (value.strip() in _PLACEHOLDER_KEYS)
        or (_PLACEHOLDER_ENDPOINT_FRAGMENT in value)
    )


def _gemini_model_name(explicit: str | None = None) -> str:
    """Resolve which Gemini model id to use.

    Explicit arg wins, then GEMINI_MODEL_NAME, then LLM_MODEL_NAME when it
    already looks like a Gemini id, else the bundled default.
    """
    if explicit:
        return explicit
    override = os.getenv("GEMINI_MODEL_NAME", "").strip()
    if override:
        return override
    configured = os.getenv("LLM_MODEL_NAME", "").strip()
    if configured.startswith("gemini"):
        return configured
    return DEFAULT_GEMINI_MODEL_NAME


def _get_float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        _log.warning("Invalid float for %s; using default %s.", name, default)
        return default


def _get_int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        _log.warning("Invalid integer for %s; using default %s.", name, default)
        return default


@dataclass
class Config:
    """Application configuration."""

    # LLM Configuration
    llm_endpoint: str = ""
    llm_api_key: str = ""
    llm_model_name: str = "anthropic.claude-4-5-haiku"
    llm_temperature: float = 0.0
    llm_max_tokens: int = 4096

    # Gemini fallback (used only when the OpenAI gateway is not configured)
    gemini_api_key: str = ""
    gemini_model_name: str = DEFAULT_GEMINI_MODEL_NAME

    # Embedding Configuration
    embed_model_name: str = "openai.text-embedding-3-large"
    embed_api_key: str = ""
    hf_embed_model: str = DEFAULT_HF_EMBED_MODEL
    embed_dim: int = 1536

    # Database Configuration
    pg_host: str = "localhost"
    pg_port: int = 5432
    pg_database: str = "fraud_detection_db"
    pg_user: str = "postgres"
    pg_password: str = ""

    # Redis Configuration
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_password: str | None = None
    redis_db: int = 0

    # Application Configuration
    log_level: str = "INFO"
    max_upload_size_mb: int = 100
    document_storage_dir: str = "./data/uploads"

    @classmethod
    def from_env(cls) -> Config:
        """Create configuration from environment variables."""
        return cls(
            llm_endpoint=os.getenv("LLM_ENDPOINT", ""),
            llm_api_key=os.getenv("LLM_API_KEY", ""),
            llm_model_name=os.getenv("LLM_MODEL_NAME", "anthropic.claude-4-5-haiku"),
            llm_temperature=_get_float_env("LLM_TEMPERATURE", 0.0),
            llm_max_tokens=_get_int_env("LLM_MAX_TOKENS", 4096),
            gemini_api_key=os.getenv("GEMINI_API_KEY", ""),
            gemini_model_name=_gemini_model_name(),
            embed_model_name=os.getenv("EMBED_MODEL_NAME", "openai.text-embedding-3-large"),
            embed_api_key=(
                os.getenv("EMBED_API_KEY", "")
                or os.getenv("EMBED_LLM_API_KEY", "")
                or os.getenv("LLM_API_KEY", "")
            ),
            hf_embed_model=os.getenv("HF_EMBED_MODEL", DEFAULT_HF_EMBED_MODEL),
            embed_dim=_get_int_env("EMBED_DIM", 1536),
            pg_host=os.getenv("PGHOST", "localhost"),
            pg_port=_get_int_env("PGPORT", 5432),
            pg_database=os.getenv("PGDATABASE", "fraud_detection_db"),
            pg_user=os.getenv("PGUSER", "fraud_detection_user"),
            pg_password=os.getenv("PGPASSWORD", ""),
            redis_host=os.getenv("REDISHOST", "localhost"),
            redis_port=_get_int_env("REDISPORT", 6379),
            redis_password=os.getenv("REDIS_PASSWORD"),
            redis_db=_get_int_env("REDIS_DB", 0),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            max_upload_size_mb=_get_int_env("MAX_UPLOAD_SIZE_MB", 100),
            document_storage_dir=os.getenv("DOCUMENT_STORAGE_DIR", "./data/uploads"),
        )


# Global configuration instance
_config: Config | None = None


def get_config() -> Config:
    """Get the global configuration instance."""
    global _config
    if _config is None:
        _config = Config.from_env()
    return _config


def reset_config() -> None:
    """Clear cached config/LLM/embedder singletons (intended for tests)."""
    global _config, _llm, _embedder, _embed_dim
    _config = None
    _llm = None
    _embedder = None
    _embed_dim = None


def _try_openai_llm(
    model_name: str | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
):
    """Try ChatOpenAI pointed at the configured gateway; None if not configured."""
    config = get_config()
    if _is_placeholder(config.llm_endpoint):
        return None
    if ChatOpenAI is None:
        return None
    try:
        llm = ChatOpenAI(
            model=model_name or config.llm_model_name,
            api_key=config.llm_api_key or "dummy-key",
            base_url=config.llm_endpoint,
            temperature=config.llm_temperature if temperature is None else temperature,
            max_tokens=max_tokens or config.llm_max_tokens,
        )
        llm.invoke("test")
        _log.info("Successfully initialized ChatOpenAI (gateway)")
        return llm
    except Exception as exc:  # noqa: BLE001
        _log.warning("ChatOpenAI (gateway) failed: %s. Falling back to Gemini.", exc)
        return None


def _get_gemini_llm(
    model_name: str | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
):
    """Create Gemini chat client via plain `requests`; None if not configured."""
    if GeminiChat is None or list_gemini_models is None:
        _log.warning("Gemini backend unavailable.")
        return None
    config = get_config()
    api_key = config.gemini_api_key.strip()
    if api_key in _PLACEHOLDER_KEYS:
        _log.warning("GEMINI_API_KEY not configured. Skipping Gemini.")
        return None
    try:
        list_gemini_models(api_key)
        llm = GeminiChat(
            model=model_name or _gemini_model_name() or config.gemini_model_name,
            api_key=api_key,
            temperature=config.llm_temperature if temperature is None else temperature,
            max_tokens=max_tokens or config.llm_max_tokens,
        )
        _log.info("Successfully initialized Gemini (requests)")
        return llm
    except Exception as exc:  # noqa: BLE001
        _log.warning("Gemini initialization failed: %s.", exc)
        return None


# Module-level singletons (initialized on first use)
_llm = None
_embedder = None
_embed_dim: int | None = None


def _resolve_llm(
    model_name: str | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
):
    """Try providers in order: OpenAI gateway -> Gemini."""
    llm = _try_openai_llm(model_name, max_tokens, temperature) or _get_gemini_llm(
        model_name, max_tokens, temperature
    )
    if llm is None:
        raise RuntimeError(
            "No LLM provider available. Configure one of:\n"
            "  - LLM_ENDPOINT + LLM_API_KEY for OpenAI-compatible gateway\n"
            "  - GEMINI_API_KEY for Gemini"
        )
    return llm


def get_llm(
    model_name: str | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
):
    """Return a chat LLM client.

    Tries OpenAI-compatible gateway first, then Gemini. Optional overrides
    create a fresh instance; the default call is cached for the process lifetime.
    Existing zero-arg call sites keep working unchanged.
    """
    global _llm
    if model_name is not None or max_tokens is not None or temperature is not None:
        return _resolve_llm(model_name, max_tokens, temperature)
    if _llm is None:
        _llm = _resolve_llm()
    return _llm


def _try_openai_embeddings():
    """Try OpenAI embeddings; None if unreachable."""
    config = get_config()
    if OpenAIEmbeddings is None:
        return None
    try:
        embedder = OpenAIEmbeddings(
            model=config.embed_model_name,
            api_key=config.embed_api_key or "dummy-key",
            base_url=config.llm_endpoint or None,
        )
        embedder.embed_query("test")
        _log.info("Successfully initialized OpenAI embeddings")
        return embedder
    except Exception as exc:  # noqa: BLE001
        _log.warning("OpenAI embeddings failed: %s. Falling back to HuggingFace.", exc)
        return None


def _get_hf_embeddings():
    """Create HuggingFace sentence-transformers embeddings (local, no API needed)."""
    if HuggingFaceEmbeddings is None:
        return None
    config = get_config()
    return HuggingFaceEmbeddings(
        model_name=config.hf_embed_model,
        model_kwargs={"device": "cpu"},
        encode_kwargs={"normalize_embeddings": True},
    )


def get_embedder():
    """Return an embedding client.

    Tries OpenAI-compatible gateway first, falls back to local HuggingFace
    sentence-transformers. Cached for the process lifetime.
    """
    global _embedder
    if _embedder is None:
        _embedder = _try_openai_embeddings() or _get_hf_embeddings()
    return _embedder


def get_embed_dim() -> int:
    """Return the embedding dimension for the active embedder.

    Detects dynamically via a test embedding so the HF fallback (384-dim)
    works without changing EMBED_DIM by hand. Cached for the process lifetime.
    """
    global _embed_dim
    if _embed_dim is None:
        _embed_dim = len(get_embedder().embed_query("dimension-check"))
        _log.info("Detected embedding dimension: %s", _embed_dim)
    return _embed_dim
