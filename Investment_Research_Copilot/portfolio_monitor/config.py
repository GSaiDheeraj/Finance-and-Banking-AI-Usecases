"""
Configuration management for Investment Research Copilot.

Everything is read from environment variables (see .env.example). No secrets
are hard-coded.

Chat LLM resolution (mirrors Credit_Risk_Assesment_Agent/credit_risk/config.py):
1. OpenAI-compatible gateway (ChatOpenAI with LLM_ENDPOINT) — tried first, when set.
2. Gemini via plain `requests` (see `gemini_requests.py`) — used when the OpenAI
   chat model is not configured / unreachable and GEMINI_API_KEY is set.
The LLM is used for language work only (news classification, briefing prose,
construction reasoning, Q&A) — never for any decision-bearing number.

Embedding resolution (for future semantic paths):
1. OpenAI-compatible gateway (OpenAIEmbeddings) — tried first, when reachable.
2. Local HuggingFace sentence-transformers (no API key needed).

The module stays import-safe when ``langchain_openai`` is missing: ``get_llm``
raises only when actually called, so the fully deterministic offline paths run
with no LLM and no network.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

try:
    import dotenv

    dotenv.load_dotenv()
except ImportError:
    _log = logging.getLogger(__name__)
    _log.debug("python-dotenv not installed; skipping .env loading.")
except OSError as exc:
    _log = logging.getLogger(__name__)
    _log.warning("Could not load .env file: %s", exc)

_log = logging.getLogger(__name__)

# Optional imports - these are only used when the respective providers are configured
try:
    import langchain_openai
except ImportError:
    langchain_openai = None

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

_DEFAULT_LLM_ENDPOINT = ""
_DEFAULT_LLM_MODEL = "anthropic.claude-4-5-haiku"
_PLACEHOLDER_ENDPOINT_FRAGMENT = "your-gateway.example.com"
_PLACEHOLDER_KEYS = {"", "replace-me", "your-api-key-here", "your-gemini-api-key-here"}

# Gemini chat fallback (override via .env)
DEFAULT_GEMINI_MODEL_NAME = "gemini-3.5-flash-lite"
# Local HuggingFace embedding fallback (no API key needed)
DEFAULT_HF_EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
DEFAULT_EMBED_MODEL_NAME = "openai.text-embedding-3-large"


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


@dataclass
class Config:
    """Application configuration."""

    llm_endpoint: str = ""
    llm_api_key: str = ""
    llm_model_name: str = _DEFAULT_LLM_MODEL
    llm_temperature: float = 0.0
    llm_max_tokens: int = 4096

    gemini_api_key: str = ""
    gemini_model_name: str = DEFAULT_GEMINI_MODEL_NAME

    embed_model_name: str = DEFAULT_EMBED_MODEL_NAME
    embed_api_key: str = ""
    hf_embed_model: str = DEFAULT_HF_EMBED_MODEL
    embed_dim: int = 1536

    pg_host: str = "localhost"
    pg_port: int = 5433
    pg_database: str = "investment_research_db"
    pg_user: str = "postgres"
    pg_password: str = ""

    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_password: str | None = None
    redis_db: int = 0

    log_level: str = "INFO"
    max_upload_size_mb: int = 100
    data_storage_dir: str = "./data/uploads"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    use_celery: bool = False

    @classmethod
    def from_env(cls) -> Config:
        """Create configuration from environment variables."""
        return cls(
            llm_endpoint=os.getenv("LLM_ENDPOINT", _DEFAULT_LLM_ENDPOINT),
            llm_api_key=os.getenv("LLM_API_KEY", ""),
            llm_model_name=os.getenv("LLM_MODEL_NAME", _DEFAULT_LLM_MODEL),
            llm_temperature=_parse_float("LLM_TEMPERATURE", 0.0),
            llm_max_tokens=_parse_int("LLM_MAX_TOKENS", 4096),
            gemini_api_key=os.getenv("GEMINI_API_KEY", ""),
            gemini_model_name=_gemini_model_name(),
            embed_model_name=os.getenv("EMBED_MODEL_NAME", DEFAULT_EMBED_MODEL_NAME),
            embed_api_key=(
                os.getenv("EMBED_API_KEY", "")
                or os.getenv("EMBED_LLM_API_KEY", "")
                or os.getenv("LLM_API_KEY", "")
            ),
            hf_embed_model=os.getenv("HF_EMBED_MODEL", DEFAULT_HF_EMBED_MODEL),
            embed_dim=_parse_int("EMBED_DIM", 1536),
            pg_host=os.getenv("PGHOST", "localhost"),
            pg_port=_parse_int("PGPORT", 5432),
            pg_database=os.getenv("PGDATABASE", "investment_research_db"),
            pg_user=os.getenv("PGUSER", "investment_research_user"),
            pg_password=os.getenv("PGPASSWORD", ""),
            redis_host=os.getenv("REDISHOST", "localhost"),
            redis_port=_parse_int("REDISPORT", 6379),
            redis_password=os.getenv("REDIS_PASSWORD"),
            redis_db=_parse_int("REDIS_DB", 0),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            max_upload_size_mb=_parse_int("MAX_UPLOAD_SIZE_MB", 100),
            data_storage_dir=os.getenv("DATA_STORAGE_DIR", "./data/uploads"),
            api_host=os.getenv("PM_API_HOST", "0.0.0.0"),
            api_port=_parse_int("PM_API_PORT", 8000),
            use_celery=os.getenv("USE_CELERY", "").lower() in ("1", "true", "yes"),
        )


_cached_config: Config | None = None
_llm = None
_embedder = None
_embed_dim: int | None = None


def get_config() -> Config:
    """Return the process-wide configuration, loading it once from the environment."""
    global _cached_config
    if _cached_config is None:
        _cached_config = Config.from_env()
    return _cached_config


def reset_config() -> None:
    """Clear cached config/LLM/embedder singletons (intended for tests)."""
    global _cached_config, _llm, _embedder, _embed_dim
    _cached_config = None
    _llm = None
    _embedder = None
    _embed_dim = None


def _openai_gateway_configured() -> bool:
    """True when an OpenAI-compatible gateway endpoint looks usable."""
    config = get_config()
    return bool(config.llm_endpoint) and (_PLACEHOLDER_ENDPOINT_FRAGMENT not in config.llm_endpoint)


def _gemini_configured() -> bool:
    """True when a GEMINI_API_KEY is set (not a placeholder)."""
    config = get_config()
    return config.gemini_api_key.strip() not in _PLACEHOLDER_KEYS


def llm_available() -> bool:
    """True if a chat LLM can plausibly be built (gateway or Gemini)."""
    if _openai_gateway_configured():
        if langchain_openai is not None:
            config = get_config()
            if config.llm_api_key.strip() not in _PLACEHOLDER_KEYS:
                return True
            # Gateway endpoint set but key missing: ChatOpenAI can still be
            # built with a dummy key for local gateways — treat as available
            # only if a probe is too expensive here. Fall through to Gemini.
    return _gemini_configured()


def _try_openai_llm(
    model_name: str | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
):
    """Try ChatOpenAI pointed at the configured gateway; None if not usable."""
    if not _openai_gateway_configured():
        return None
    if ChatOpenAI is None:
        return None
    config = get_config()
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
    if not _gemini_configured():
        return None
    if GeminiChat is None or list_gemini_models is None:
        _log.warning("Gemini backend unavailable.")
        return None
    config = get_config()
    try:
        list_gemini_models(config.gemini_api_key.strip())
        llm = GeminiChat(
            model=model_name or _gemini_model_name() or config.gemini_model_name,
            api_key=config.gemini_api_key.strip(),
            temperature=config.llm_temperature if temperature is None else temperature,
            max_tokens=max_tokens or config.llm_max_tokens,
        )
        _log.info("Successfully initialized Gemini (requests)")
        return llm
    except Exception as exc:  # noqa: BLE001
        _log.warning("Gemini initialization failed: %s.", exc)
        return None


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
        raise ValueError(
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
    """Return a chat LLM client (gateway first, then Gemini).

    Imported lazily so the deterministic, offline paths never require
    langchain_openai. Optional overrides create a fresh instance; the default
    call is cached for the process lifetime.
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
    """Return an embedding client (gateway first, then local HF). Cached."""
    global _embedder
    if _embedder is None:
        _embedder = _try_openai_embeddings() or _get_hf_embeddings()
    return _embedder


def get_embed_dim() -> int:
    """Return the embedding dimension for the active embedder (probed once)."""
    global _embed_dim
    if _embed_dim is None:
        _embed_dim = len(get_embedder().embed_query("dimension-check"))
        _log.info("Detected embedding dimension: %s", _embed_dim)
    return _embed_dim


def _parse_float(env_name: str, default: float) -> float:
    raw = os.getenv(env_name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"Environment variable {env_name}={raw!r} is not a float.") from exc


def _parse_int(env_name: str, default: int) -> int:
    raw = os.getenv(env_name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"Environment variable {env_name}={raw!r} is not an integer.") from exc
