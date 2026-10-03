"""
DeepEval judge model backed by OpenRouter -- the provider this project
already uses -- so no second AI vendor account is needed.

    EVAL_JUDGE_MODEL   model id for the judge (default: OPENROUTER_MODEL).
                       Prefer a stronger model than the one under test: a
                       model grading its own output is a biased judge.

Judge calls go straight to OpenRouter (not through app/ai/metering.py),
so they are not counted as application AI usage.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any

import requests
from deepeval.models import DeepEvalBaseLLM

# The application's configured provider (OpenAI or OpenRouter, app/ai/provider.py).
from app.ai import provider  # noqa: E402

OPENROUTER_URL = provider.CHAT_COMPLETIONS_URL
_RETRYABLE = {408, 429, 500, 502, 503, 504}


def judge_model_name() -> str:
    return os.getenv("EVAL_JUDGE_MODEL") or provider.MODEL


def _extract_json(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        text = text.rsplit("```", 1)[0]
    start, end = text.find("{"), text.rfind("}")
    return text[start : end + 1] if start != -1 and end > start else text


class OpenRouterJudge(DeepEvalBaseLLM):
    def __init__(self, model: str | None = None, timeout: float | None = None, max_attempts: int = 4):
        self.model_id = model or judge_model_name()
        self.timeout = timeout or float(os.getenv("EVAL_JUDGE_TIMEOUT_SECONDS") or 120)
        self.max_attempts = max_attempts
        super().__init__(self.model_id)

    def load_model(self) -> str:
        return self.model_id

    def get_model_name(self) -> str:
        return f"{provider.PROVIDER}:{self.model_id}"

    def _post(self, payload: dict[str, Any]) -> str:
        key = os.getenv("OPENROUTER_API_KEY")
        if not key:
            raise RuntimeError("OPENROUTER_API_KEY is required for the evaluation judge.")
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

        last_error: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                response = requests.post(OPENROUTER_URL, headers=headers, json=payload, timeout=self.timeout)
                if response.status_code == 200:
                    content = response.json()["choices"][0]["message"].get("content") or ""
                    if content.strip():
                        return content
                    last_error = RuntimeError("judge returned an empty reply")
                elif response.status_code in _RETRYABLE:
                    last_error = RuntimeError(f"judge HTTP {response.status_code}")
                else:
                    raise RuntimeError(f"judge HTTP {response.status_code}: {response.text[:200]}")
            except (requests.RequestException, KeyError, IndexError, ValueError) as exc:
                last_error = exc
            time.sleep(min(30, 3 * 2 ** (attempt - 1)))
        raise RuntimeError(f"Judge model failed after {self.max_attempts} attempts: {last_error}")

    def generate(self, prompt: str, schema: Any = None) -> Any:
        payload: dict[str, Any] = {
            "model": self.model_id,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
        }
        if schema is None:
            return self._post(payload)
        payload["response_format"] = {"type": "json_object"}
        # DeepEval accepts a schema instance or JSON text. Weaker judge
        # models sometimes wrap or truncate their JSON, so ask again
        # before handing DeepEval something it cannot parse.
        content = ""
        for _ in range(3):
            content = self._post(payload)
            try:
                return schema.model_validate(json.loads(_extract_json(content)))
            except Exception:  # noqa: BLE001
                continue
        return _extract_json(content)

    async def a_generate(self, prompt: str, schema: Any = None) -> Any:
        return await asyncio.to_thread(self.generate, prompt, schema)
