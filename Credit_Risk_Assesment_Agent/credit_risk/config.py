"""LLM and embedding client configuration.

Everything is read from environment variables (see .env.example). No secrets are
hard-coded. The chat LLM tries OpenAI-compatible gateway first (ChatOpenAI), then
falls back to Gemini via plain `requests` (see `gemini_requests.py`); embeddings try
OpenAI-compatible gateway first, then fall back to local HuggingFace
sentence-transformers.
"""

import os
from collections.abc import Callable
from typing import TypeVar

import dotenv
from langchain_core.embeddings import Embeddings
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_openai import ChatOpenAI, OpenAIEmbeddings

try:
    from langchain_huggingface import HuggingFaceEmbeddings
except ImportError:
    from langchain_community.embeddings import HuggingFaceEmbeddings

from .gemini_requests import GeminiChat, list_gemini_models
from .logconf import get_logger

dotenv.load_dotenv()

_log = get_logger()

# --- Gemini chat model configuration (override via .env) --------------------
DEFAULT_GEMINI_MODEL_NAME = "gemini-3.5-flash-lite"
DEFAULT_EMBED_MODEL_NAME = "openai.text-embedding-3-small"
DEFAULT_LLM_TEMPERATURE = 0.0
DEFAULT_LLM_MAX_TOKENS = 4096

# Local HuggingFace embedding model (used as fallback)
DEFAULT_HF_EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

LLM_MODEL_NAME = os.getenv("LLM_MODEL_NAME", DEFAULT_GEMINI_MODEL_NAME)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
LLM_ENDPOINT = os.getenv("LLM_ENDPOINT", "")

EMBED_API_KEY = os.getenv("EMBED_API_KEY", "") or os.getenv("LLM_API_KEY", "")
EMBED_MODEL_NAME = os.getenv("EMBED_MODEL_NAME", DEFAULT_EMBED_MODEL_NAME)
HF_EMBED_MODEL = os.getenv("HF_EMBED_MODEL", DEFAULT_HF_EMBED_MODEL)


T = TypeVar("T")


def _parse_env(var_name: str, default: T, cast: Callable[[str], T]) -> T:
    """Parse an env var with `cast`, falling back to default on missing/malformed values."""
    raw_value = os.getenv(var_name)
    if raw_value is None or not raw_value.strip():
        return default
    try:
        return cast(raw_value)
    except ValueError:
        return default


# Deterministic by default — credit decisions must be reproducible.
LLM_TEMPERATURE = _parse_env("LLM_TEMPERATURE", DEFAULT_LLM_TEMPERATURE, float)
LLM_MAX_TOKENS = _parse_env("LLM_MAX_TOKENS", DEFAULT_LLM_MAX_TOKENS, int)


def _try_openai_llm(
    model_name: str | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
) -> BaseChatModel | None:
    """Try to create ChatOpenAI client pointed at the configured gateway; return None if it fails."""
    if not LLM_ENDPOINT:
        return None
    try:
        llm = ChatOpenAI(
            model=model_name or LLM_MODEL_NAME,
            api_key=EMBED_API_KEY
            or "dummy-key",  # Use EMBED_API_KEY as fallback for LLM gateway
            base_url=LLM_ENDPOINT,
            temperature=LLM_TEMPERATURE if temperature is None else temperature,
            max_tokens=max_tokens or LLM_MAX_TOKENS,
        )
        # Test the connection with a small call
        llm.invoke("test")
        _log.info("Successfully initialized ChatOpenAI (gateway)")
        return llm
    except Exception as e:  # noqa: BLE001
        _log.warning(f"ChatOpenAI (gateway) failed: {e}. Falling back to Gemini.")
        return None


def _get_gemini_llm(
    model_name: str | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
) -> BaseChatModel | None:
    """Create Gemini chat LLM client via plain `requests` (second fallback).

    Returns None if not configured. Validates the key with a lightweight
    `GET /models` call before returning the client.
    """
    if not GEMINI_API_KEY or GEMINI_API_KEY == "your-gemini-api-key-here":
        _log.warning("GEMINI_API_KEY not configured. Skipping Gemini.")
        return None
    try:
        list_gemini_models(GEMINI_API_KEY)
        llm = GeminiChat(
            model=model_name or LLM_MODEL_NAME,
            api_key=GEMINI_API_KEY,
            temperature=LLM_TEMPERATURE if temperature is None else temperature,
            max_tokens=max_tokens or LLM_MAX_TOKENS,
        )
        _log.info("Successfully initialized Gemini (requests)")
        return llm
    except Exception as e:  # noqa: BLE001
        _log.warning(f"Gemini initialization failed: {e}.")
        return None


# Module-level singleton for LLM (initialized on first use)
_llm: BaseChatModel | None = None


def _resolve_llm(
    model_name: str | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
) -> BaseChatModel:
    """Try all LLM providers in order: OpenAI gateway -> Gemini."""
    llm = _try_openai_llm(model_name, max_tokens, temperature) or _get_gemini_llm(
        model_name, max_tokens, temperature
    )
    if llm is None:
        raise RuntimeError(
            "No LLM provider available. Configure one of:\n"
            "  - LLM_ENDPOINT + EMBED_API_KEY for OpenAI-compatible gateway\n"
            "  - GEMINI_API_KEY for Gemini"
        )
    return llm


def get_llm(
    model_name: str | None = None,
    max_tokens: int | None = None,
) -> BaseChatModel:
    """Return a chat LLM client.

    Tries providers in order:
    1. OpenAI-compatible gateway (ChatOpenAI with LLM_ENDPOINT)
    2. Gemini (plain `requests`, see `gemini_requests.py`)

    Supports optional overrides for model_name and max_tokens.
    The result is cached for the process lifetime (when no overrides provided).
    """
    global _llm
    # If overrides provided, don't use cache; create fresh instance
    if model_name is not None or max_tokens is not None:
        return _resolve_llm(model_name, max_tokens)

    if _llm is None:
        _llm = _resolve_llm()
    return _llm


def get_product_llm(temperature: float = 0.7) -> BaseChatModel:
    """Return a chat LLM tuned for open-ended extraction demos.

    Uses non-zero temperature (default 0.7) since fluent prose extraction
    benefits from sampling. Falls back through the same provider chain.
    """
    return _resolve_llm(temperature=temperature)


def _try_openai_embeddings() -> Embeddings | None:
    """Try to create OpenAI embeddings client; return None if it fails."""
    try:
        embedder = OpenAIEmbeddings(
            model=EMBED_MODEL_NAME,
            api_key=EMBED_API_KEY
            or "dummy-key",  # API key required but can be dummy for local gateways
            base_url=LLM_ENDPOINT or None,
        )
        # Test the connection with a small embedding call
        embedder.embed_query("test")
        _log.info("Successfully initialized OpenAI embeddings")
        return embedder
    except Exception as e:  # noqa: BLE001
        _log.warning(f"OpenAI embeddings failed: {e}. Falling back to HuggingFace.")
        return None


def _get_hf_embeddings() -> Embeddings:
    """Create HuggingFace sentence-transformers embeddings (local, no API needed)."""
    return HuggingFaceEmbeddings(
        model_name=HF_EMBED_MODEL,
        model_kwargs={"device": "cpu"},
        encode_kwargs={"normalize_embeddings": True},
    )


# Module-level singleton for embedder (initialized on first use)
_embedder: Embeddings | None = None
_embed_dim: int | None = None


def get_embedder() -> Embeddings:
    """Return an embedding client.

    Tries OpenAI-compatible gateway first (for cloud/hosted embeddings).
    Falls back to local HuggingFace sentence-transformers if unavailable.
    The result is cached for the process lifetime.
    """
    global _embedder
    if _embedder is None:
        _embedder = _try_openai_embeddings() or _get_hf_embeddings()
    return _embedder


def get_embed_dim() -> int:
    """Return the embedding dimension for the active embedder.

    Dynamically detects the dimension by embedding a test string.
    Caches the result for the process lifetime.
    """
    global _embed_dim
    if _embed_dim is None:
        embedder = get_embedder()
        _embed_dim = len(embedder.embed_query("dimension-check"))
        _log.info(f"Detected embedding dimension: {_embed_dim}")
    return _embed_dim
