"""
LLM and embedding client configuration.

Everything is read from environment variables (see .env.example). No secrets are
hard-coded. The clients target an OpenAI-compatible gateway, so the underlying model
can be a Claude / OSS / Azure deployment without changing call sites.
"""
import os

import dotenv

from langchain_openai import ChatOpenAI, OpenAIEmbeddings

dotenv.load_dotenv()
dotenv.load_dotenv(".env")

# --- Endpoint / model configuration (override via .env) ---------------------
LLM_ENDPOINT = os.getenv("LLM_ENDPOINT", "")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_MODEL_NAME = os.getenv("LLM_MODEL_NAME", "anthropic.claude-4-5-haiku")

EMBED_API_KEY = os.getenv("LLM_API_KEY", LLM_API_KEY)
EMBED_MODEL_NAME = os.getenv("EMBED_MODEL_NAME", "openai.text-embedding-3-small")

# Deterministic by default — credit decisions must be reproducible.
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.0"))
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "4096"))


def get_llm() -> ChatOpenAI:
    """Return a chat LLM client pointed at the configured gateway."""
    return ChatOpenAI(
        model=LLM_MODEL_NAME,
        api_key=LLM_API_KEY,
        base_url=LLM_ENDPOINT,
        temperature=LLM_TEMPERATURE,
        max_tokens=LLM_MAX_TOKENS,
    )


def get_embedder() -> OpenAIEmbeddings:
    """Return an embedding client pointed at the configured gateway."""
    return OpenAIEmbeddings(
        model=EMBED_MODEL_NAME,
        api_key=EMBED_API_KEY,
        base_url=LLM_ENDPOINT,
    )
