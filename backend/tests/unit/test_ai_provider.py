"""
app/ai/provider.py: which provider, key, URL and models the AI modules use,
and metering sending OpenRouter-only fields to OpenRouter only. No network.
"""

import importlib

import pytest

import app.ai.metering as metering
import app.ai.provider as provider
from tests.support.fake_llm import chat


def _reload(monkeypatch, **env):
    for name in ("LLM_PROVIDER", "OPENAI_API_KEY", "OPENAI_MODEL", "OPENAI_EMBEDDING_MODEL",
                 "OPENROUTER_API_KEY", "OPENROUTER_MODEL", "OPENROUTER_EMBEDDING_MODEL"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    # load_dotenv never overrides a set variable; blank ones keep .env out.
    for name in ("LLM_PROVIDER", "OPENAI_API_KEY", "OPENROUTER_API_KEY", "OPENAI_MODEL", "OPENROUTER_MODEL"):
        monkeypatch.setenv(name, env.get(name, ""))
    return importlib.reload(provider)


@pytest.fixture(autouse=True)
def _restore():
    yield
    importlib.reload(provider)


def test_openai_when_its_key_is_set(monkeypatch):
    p = _reload(monkeypatch, OPENAI_API_KEY="sk-test-openai")
    assert p.PROVIDER == "openai" and p.API_KEY == "sk-test-openai"
    assert p.CHAT_COMPLETIONS_URL == "https://api.openai.com/v1/chat/completions"
    assert p.EMBEDDINGS_URL == "https://api.openai.com/v1/embeddings"
    assert p.MODEL == "gpt-4.1-mini" and p.EMBEDDING_MODEL == "text-embedding-3-small"
    assert p.KEY_NAME == "OPENAI_API_KEY"


def test_openrouter_by_default_and_when_chosen(monkeypatch):
    p = _reload(monkeypatch, OPENROUTER_API_KEY="or-test")
    assert p.PROVIDER == "openrouter" and p.API_KEY == "or-test"
    assert p.CHAT_COMPLETIONS_URL == "https://openrouter.ai/api/v1/chat/completions"
    assert p.MODEL == "openrouter/free"
    p = _reload(monkeypatch, LLM_PROVIDER="openrouter", OPENAI_API_KEY="sk-ignored", OPENROUTER_API_KEY="or-test")
    assert p.PROVIDER == "openrouter" and p.API_KEY == "or-test"


def test_model_overrides(monkeypatch):
    p = _reload(monkeypatch, LLM_PROVIDER="openai", OPENAI_API_KEY="sk-x", OPENAI_MODEL="gpt-4.1")
    assert p.MODEL == "gpt-4.1"


@pytest.mark.parametrize(
    "url, expected_usage_field, provider_name",
    [
        ("https://openrouter.ai/api/v1/chat/completions", True, "openrouter"),
        ("https://api.openai.com/v1/chat/completions", False, "openai"),
    ],
)
def test_openrouter_only_fields_never_reach_openai(fake_llm, url, expected_usage_field, provider_name):
    sent = {}

    def capture(request_url, payload):
        sent.update(payload or {})
        return chat("{}")

    fake_llm.transport = capture
    metering.metered_post("TEST", url, json={"model": "m", "messages": [{"role": "user", "content": "hi"}]})
    assert ("usage" in sent) is expected_usage_field
    assert provider.provider_for_url(url) == provider_name


def test_the_test_suite_is_pinned_offline():
    # tests/conftest.py: the deterministic suite never uses backend/.env's provider.
    assert provider.PROVIDER == "openrouter"
