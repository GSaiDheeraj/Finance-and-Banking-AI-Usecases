"""Runtime settings for the filing reader.

Values come from the process environment (see `.env.example`); nothing secret
is written here. Chat works in two tiers — an OpenAI-shaped gateway when one
is configured, otherwise Gemini over plain HTTPS — and embeddings prefer the
same gateway before dropping to a local sentence model that needs no key.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

import dotenv

dotenv.load_dotenv()
dotenv.load_dotenv(".env")

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

_logger = logging.getLogger(__name__)

DEFAULT_GEMINI_MODEL_NAME = "gemini-3.5-flash-lite"
DEFAULT_HF_EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

_SAMPLE_GATEWAY_MARKER = "your-gateway.example.com"
_SAMPLE_SECRETS = {"", "replace-me", "your-api-key-here", "your-gemini-api-key-here"}


def _looks_like_sample(candidate: str) -> bool:
    return (
        (not candidate)
        or (candidate.strip() in _SAMPLE_SECRETS)
        or (_SAMPLE_GATEWAY_MARKER in candidate)
    )


def _pick_gemini_model(explicit: str | None = None) -> str:
    """Choose the Gemini model id without ever sending a gateway id there.

    An explicit argument wins, then `GEMINI_MODEL_NAME`, then `LLM_MODEL_NAME`
    only when it already names a Gemini model; otherwise the bundled default.
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
    """Every tunable the process reads, with local-first defaults."""

    llm_endpoint: str = ""
    llm_api_key: str = ""
    llm_model_name: str = "anthropic.claude-4-5-haiku"
    llm_temperature: float = 0.0
    llm_max_tokens: int = 4096
    # Cheap organising passes vs. careful reading passes. Both follow the main
    # model unless overridden, so single-model setups change nothing.
    small_llm_model_name: str = "anthropic.claude-4-5-haiku"
    large_llm_model_name: str = "anthropic.claude-sonnet-4-6"

    # Second-tier chat, reached only when the gateway is absent or failing.
    gemini_api_key: str = ""
    gemini_model_name: str = DEFAULT_GEMINI_MODEL_NAME

    embed_model_name: str = "openai.text-embedding-3-large"
    embed_api_key: str = ""
    hf_embed_model: str = DEFAULT_HF_EMBED_MODEL
    embed_dim: int = 1536

    pg_host: str = "localhost"
    pg_port: int = 5433
    pg_database: str = "findoc_db"
    pg_user: str = "findoc_user"
    pg_password: str = "7194"

    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_password: str | None = None
    redis_db: int = 0

    log_level: str = "INFO"
    max_upload_size_mb: int = 100
    document_storage_dir: str = "./data/Financial document extraction"

    # Optional archive bucket. Empty means disk-only; see storage.py.
    s3_bucket: str | None = None
    s3_region: str | None = None

    @classmethod
    def from_env(cls) -> Config:
        """Snapshot the current environment into one settings object."""
        fallback_model = os.getenv("LLM_MODEL_NAME", "anthropic.claude-4-5-haiku")
        return cls(
            llm_endpoint=os.getenv("LLM_ENDPOINT", ""),
            llm_api_key=os.getenv("LLM_API_KEY", ""),
            llm_model_name=fallback_model,
            llm_temperature=float(os.getenv("LLM_TEMPERATURE", "0.0")),
            llm_max_tokens=int(os.getenv("LLM_MAX_TOKENS", "4096")),
            small_llm_model_name=os.getenv("SMALL_LLM_MODEL_NAME", fallback_model),
            large_llm_model_name=os.getenv("LARGE_LLM_MODEL_NAME", fallback_model),
            gemini_api_key=os.getenv("GEMINI_API_KEY", ""),
            gemini_model_name=_pick_gemini_model(),
            embed_model_name=os.getenv("EMBED_MODEL_NAME", "openai.text-embedding-3-large"),
            embed_api_key=(
                os.getenv("EMBED_API_KEY", "")
                or os.getenv("EMBED_LLM_API_KEY", "")
                or os.getenv("LLM_API_KEY", "")
            ),
            hf_embed_model=os.getenv("HF_EMBED_MODEL", DEFAULT_HF_EMBED_MODEL),
            embed_dim=int(os.getenv("EMBED_DIM", "1536")),
            pg_host=os.getenv("PGHOST", "localhost"),
            pg_port=int(os.getenv("PGPORT", "5433")),
            pg_database=os.getenv("PGDATABASE", "findoc_db"),
            pg_user=os.getenv("PGUSER", "findoc_user"),
            pg_password=os.getenv("PGPASSWORD", "7194"),
            redis_host=os.getenv("REDISHOST", "localhost"),
            redis_port=int(os.getenv("REDISPORT", "6379")),
            redis_password=os.getenv("REDIS_PASSWORD"),
            redis_db=int(os.getenv("REDIS_DB", "0")),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            max_upload_size_mb=int(os.getenv("MAX_UPLOAD_SIZE_MB", "100")),
            document_storage_dir=os.getenv(
                "DOCUMENT_STORAGE_DIR", "./data/Financial document extraction"
            ),
            s3_bucket=os.getenv("S3_BUCKET"),
            s3_region=os.getenv("S3_REGION"),
        )


_cached_settings: Config | None = None


def get_config() -> Config:
    """Return the process-wide settings, loading them exactly once."""
    global _cached_settings
    if _cached_settings is None:
        _cached_settings = Config.from_env()
    return _cached_settings


def reset_config() -> None:
    """Forget cached settings and clients (intended for tests)."""
    global _cached_settings, _llm, _embedder, _embed_dim
    _cached_settings = None
    _llm = None
    _embedder = None
    _embed_dim = None


def _build_gateway_chat(
    model_name: str | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
):
    """Point a gateway chat client at the configured endpoint, or None."""
    settings = get_config()
    if _looks_like_sample(settings.llm_endpoint):
        return None
    if ChatOpenAI is None:
        return None
    try:
        candidate = ChatOpenAI(
            model=model_name or settings.llm_model_name,
            api_key=settings.llm_api_key or "dummy-key",
            base_url=settings.llm_endpoint,
            temperature=settings.llm_temperature if temperature is None else temperature,
            max_tokens=max_tokens or settings.llm_max_tokens,
        )
        candidate.invoke("test")
        _logger.info("Successfully initialized ChatOpenAI (gateway)")
        return candidate
    except Exception as exc:  # noqa: BLE001
        _logger.warning("ChatOpenAI (gateway) failed: %s. Falling back to Gemini.", exc)
        return None


def _build_gemini_chat(
    model_name: str | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
):
    """Build the Gemini chat client, or None when its key is missing."""
    if GeminiChat is None or list_gemini_models is None:
        _logger.warning("Gemini backend unavailable.")
        return None
    settings = get_config()
    secret = settings.gemini_api_key.strip()
    if secret in _SAMPLE_SECRETS:
        _logger.warning("GEMINI_API_KEY not configured. Skipping Gemini.")
        return None
    try:
        list_gemini_models(secret)
        chat = GeminiChat(
            model=model_name or _pick_gemini_model() or settings.gemini_model_name,
            api_key=secret,
            temperature=settings.llm_temperature if temperature is None else temperature,
            max_tokens=max_tokens or settings.llm_max_tokens,
        )
        _logger.info("Successfully initialized Gemini (requests)")
        return chat
    except Exception as exc:  # noqa: BLE001
        _logger.warning("Gemini initialization failed: %s.", exc)
        return None


_llm = None
_embedder = None
_embed_dim: int | None = None


def _select_chat_model(
    model_name: str | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
):
    """First working chat tier wins: gateway, then Gemini."""
    chat = _build_gateway_chat(model_name, max_tokens, temperature) or _build_gemini_chat(
        model_name, max_tokens, temperature
    )
    if chat is None:
        raise RuntimeError(
            "No LLM provider available. Configure one of:\n"
            "  - LLM_ENDPOINT + LLM_API_KEY for OpenAI-compatible gateway\n"
            "  - GEMINI_API_KEY for Gemini"
        )
    return chat


def get_llm(
    model_name: str | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
):
    """Hand out a chat client.

    Plain calls share one cached client; any override builds a fresh one. The
    overrides let the table pipeline spend less on organising passes and more
    on the careful reading pass.
    """
    global _llm
    if model_name is not None or max_tokens is not None or temperature is not None:
        return _select_chat_model(model_name, max_tokens, temperature)
    if _llm is None:
        _llm = _select_chat_model()
    return _llm


def _build_gateway_embeddings():
    """Embed via the gateway, or None when it cannot be reached."""
    settings = get_config()
    if OpenAIEmbeddings is None:
        return None
    try:
        coder = OpenAIEmbeddings(
            model=settings.embed_model_name,
            api_key=settings.embed_api_key or "dummy-key",
            base_url=settings.llm_endpoint or None,
        )
        coder.embed_query("test")
        _logger.info("Successfully initialized OpenAI embeddings")
        return coder
    except Exception as exc:  # noqa: BLE001
        _logger.warning("OpenAI embeddings failed: %s. Falling back to HuggingFace.", exc)
        return None


def _build_local_embeddings():
    """Local sentence model needing no key and no network account."""
    if HuggingFaceEmbeddings is None:
        return None
    settings = get_config()
    return HuggingFaceEmbeddings(
        model_name=settings.hf_embed_model,
        model_kwargs={"device": "cpu"},
        encode_kwargs={"normalize_embeddings": True},
    )


def get_embedder():
    """Hand out the embedder, preferring the gateway, caching the winner."""
    global _embedder
    if _embedder is None:
        _embedder = _build_gateway_embeddings() or _build_local_embeddings()
    return _embedder


def get_embed_dim() -> int:
    """Measure the live embedder once so callers need no manual dimension."""
    global _embed_dim
    if _embed_dim is None:
        _embed_dim = len(get_embedder().embed_query("dimension-check"))
        _logger.info("Detected embedding dimension: %s", _embed_dim)
    return _embed_dim
