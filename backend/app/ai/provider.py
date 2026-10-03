"""
The AI provider every AI module calls: OpenAI or OpenRouter.

Both expose the same OpenAI-compatible chat-completions and embeddings
APIs, so only the base URL, the key and the default model names differ.

  LLM_PROVIDER      "openai" or "openrouter". Default: openai when
                    OPENAI_API_KEY is set, otherwise openrouter.
  OPENAI_API_KEY    the OpenAI key (backend/.env; never committed).
  OPENAI_MODEL      chat model on OpenAI (default gpt-4.1-mini). The app
                    sets a temperature and JSON mode on every call, so use
                    a non-reasoning chat model (gpt-4.1*, gpt-4o*).
  OPENAI_EMBEDDING_MODEL  default text-embedding-3-small (1536 dims, the
                    width of document_embeddings.embedding).
  OPENROUTER_API_KEY / OPENROUTER_MODEL / OPENROUTER_EMBEDDING_MODEL
                    the same for OpenRouter (unchanged).

Read once at import, like the modules that use it; tests replace the
modules' own OPENROUTER_* attributes, which still hold these values.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()

OPENAI = "openai"
OPENROUTER = "openrouter"

_BASE_URLS = {
    OPENAI: "https://api.openai.com/v1",
    OPENROUTER: "https://openrouter.ai/api/v1",
}
_DEFAULT_MODELS = {OPENAI: "gpt-4.1-mini", OPENROUTER: "openrouter/free"}
_DEFAULT_EMBEDDING_MODELS = {OPENAI: "text-embedding-3-small", OPENROUTER: "openai/text-embedding-3-small"}


def _provider() -> str:
    configured = (os.getenv("LLM_PROVIDER") or "").strip().lower()
    if configured in _BASE_URLS:
        return configured
    return OPENAI if os.getenv("OPENAI_API_KEY") else OPENROUTER


PROVIDER = _provider()
BASE_URL = _BASE_URLS[PROVIDER]
CHAT_COMPLETIONS_URL = f"{BASE_URL}/chat/completions"
EMBEDDINGS_URL = f"{BASE_URL}/embeddings"

if PROVIDER == OPENAI:
    API_KEY = os.getenv("OPENAI_API_KEY")
    MODEL = os.getenv("OPENAI_MODEL") or _DEFAULT_MODELS[OPENAI]
    EMBEDDING_MODEL = os.getenv("OPENAI_EMBEDDING_MODEL") or _DEFAULT_EMBEDDING_MODELS[OPENAI]
else:
    API_KEY = os.getenv("OPENROUTER_API_KEY")
    MODEL = os.getenv("OPENROUTER_MODEL") or _DEFAULT_MODELS[OPENROUTER]
    EMBEDDING_MODEL = os.getenv("OPENROUTER_EMBEDDING_MODEL") or _DEFAULT_EMBEDDING_MODELS[OPENROUTER]

KEY_NAME = "OPENAI_API_KEY" if PROVIDER == OPENAI else "OPENROUTER_API_KEY"
LABEL = "OpenAI" if PROVIDER == OPENAI else "OpenRouter"


def provider_for_url(url: str) -> str:
    if "openrouter.ai" in url:
        return OPENROUTER
    if "api.openai.com" in url:
        return OPENAI
    return "other"
