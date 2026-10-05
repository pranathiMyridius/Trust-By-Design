"""
Stage 17 (R17.4): metering for every outbound AI/LLM request.

`metered_post()` is a drop-in replacement for `requests.post()` at each
OpenRouter call site. It returns the same Response (and re-raises the
same RequestException), and on the side records an AIUsageLog row with
purpose, model, tokens, cost, duration and outcome. Metering must never
break the AI call itself, so every failure inside it is swallowed.

Which assessment a call belongs to is carried by a context variable
(`ai_assessment_context`), set by the callers that know it -- the
LangGraph risk workflow, draft generation, embedding indexing -- so the
analyzers themselves don't need an extra parameter.
"""

from __future__ import annotations

import hashlib
import importlib
import inspect
import json
import logging
import os
import time
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache
from typing import Any, Iterator

import requests

from app.observability import tracing
from app.services.data_masking import mask_ai_payload

logger = logging.getLogger(__name__)

_current_assessment_id: ContextVar[int | None] = ContextVar("ai_assessment_id", default=None)

# R16.1 "model and prompt versions": the function that builds each
# purpose's prompt. The prompt text is a template in that function's
# source, so a fingerprint of the source is the prompt's version -- it
# changes exactly when someone edits the prompt, and the same version
# always means the same instructions, whatever data went into them.
PROMPT_SOURCES = {
    "RISK_FACTOR_IDENTIFICATION": "app.ai.risk_factor_analyzer:_build_prompt",
    "DRAFT_NARRATIVE": "app.ai.assessment_draft_generator:_build_prompt",
    "CONTROL_IDENTIFICATION": "app.ai.control_identifier:identify_applicable_controls",
    "CONTROL_DESIGN_ASSESSMENT": "app.ai.control_identifier:assess_control_design",
    "CONTROL_EVIDENCE_CHECK": "app.ai.evidence_checker:check_control_evidence",
    "LIKELIHOOD_IMPACT_SUGGESTION": "app.ai.likelihood_impact_analyzer:estimate_likelihood_impact",
    "DOCUMENT_EXTRACTION": "app.document_analysis.ai_extractor:_build_extraction_prompt",
    "ASSISTANT_CHAT": "app.assistant.orchestrator:build_system_prompt",
}


@lru_cache(maxsize=None)
def prompt_version(purpose: str) -> str | None:
    """e.g. "_build_prompt@3f9a1c2b7d4e"; None for calls without a prompt
    (embeddings) or if the source can't be read."""

    target = PROMPT_SOURCES.get(purpose)
    if not target:
        return None
    try:
        module_name, function_name = target.split(":")
        function = getattr(importlib.import_module(module_name), function_name)
        source = inspect.getsource(function)
    except Exception:  # noqa: BLE001 -- metering must never break the call
        logger.warning("Could not fingerprint the %s prompt", purpose, exc_info=True)
        return None
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()[:12]
    return f"{function_name}@{digest}"


@contextmanager
def ai_assessment_context(assessment_id: int | None) -> Iterator[None]:
    token = _current_assessment_id.set(assessment_id)
    try:
        yield
    finally:
        _current_assessment_id.reset(token)


def _pricing() -> dict[str, dict[str, float]]:
    """
    Optional fallback price table for models whose provider doesn't
    report cost: AI_MODEL_PRICING='{"model-id": {"prompt": 0.5,
    "completion": 1.5}}' in USD per 1M tokens.
    """

    raw = os.getenv("AI_MODEL_PRICING")
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except ValueError:
        logger.warning("AI_MODEL_PRICING is not valid JSON; ignoring it.")
        return {}


def _cost(model: str | None, usage: dict[str, Any]) -> tuple[float | None, str]:
    reported = usage.get("cost")
    if isinstance(reported, (int, float)):
        return float(reported), "REPORTED"

    price = _pricing().get(model or "")
    prompt = usage.get("prompt_tokens")
    completion = usage.get("completion_tokens")
    if price and (prompt is not None or completion is not None):
        cost = (
            (prompt or 0) * float(price.get("prompt", 0))
            + (completion or 0) * float(price.get("completion", 0))
        ) / 1_000_000
        return cost, "ESTIMATED"
    return None, "UNKNOWN"


def _record(**fields: Any) -> None:
    # Own short-lived session: the caller's transaction may later roll
    # back (e.g. a failed analysis), and the usage must still be counted.
    from app.database import SessionLocal
    from app.models.ai_metrics import AIUsageLog

    db = SessionLocal()
    try:
        db.add(AIUsageLog(**fields))
        db.commit()
    except Exception as exc:  # noqa: BLE001 -- metering never breaks the call
        db.rollback()
        logger.warning("Could not record AI usage: %s", exc)
    finally:
        db.close()


def _trace_generation(
    generation: "tracing.Observation",
    response: requests.Response,
    body: dict[str, Any],
    usage: dict[str, Any],
    response_model: str | None,
    cost: float | None,
    duration_ms: int,
    metadata: dict[str, Any],
) -> None:
    if not generation.active:
        return

    content = None
    try:
        content = body["choices"][0]["message"].get("content")
    except (KeyError, IndexError, TypeError, AttributeError):
        pass

    usage_details = {
        key: int(usage[source])
        for key, source in (
            ("input", "prompt_tokens"),
            ("output", "completion_tokens"),
            ("total", "total_tokens"),
        )
        if isinstance(usage.get(source), (int, float))
    }
    ok = response.status_code == 200
    generation.update(
        model=response_model,
        output=(
            content
            if tracing.capture_content()
            else {"http_status": response.status_code, "completion_chars": len(content or "")}
        ),
        usage_details=usage_details or None,
        cost_details={"total": float(cost)} if cost is not None else None,
        metadata={**metadata, "http_status": response.status_code, "duration_ms": duration_ms},
        level=None if ok else "ERROR",
        status_message=None if ok else f"HTTP {response.status_code}",
    )


def metered_post(
    purpose: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    json: dict[str, Any] | None = None,  # noqa: A002 -- mirrors requests.post
    timeout: float | None = None,
    **kwargs: Any,
) -> requests.Response:
    # Stage 19: mask card numbers, IBANs, e-mails etc. before any content
    # leaves for the external provider (AI_MASK_SENSITIVE_DATA).
    payload = mask_ai_payload(dict(json or {}))
    requested_model = payload.get("model")
    from app.ai.provider import OPENROUTER, provider_for_url

    provider_name = provider_for_url(url)
    if "chat/completions" in url and provider_name == OPENROUTER:
        # Ask OpenRouter to include token usage and cost in the response.
        # (OpenRouter-only: OpenAI rejects unknown request fields; it
        # reports token usage by default.)
        payload.setdefault("usage", {"include": True})

    base = {
        "assessment_id": _current_assessment_id.get(),
        "purpose": purpose,
        "provider": provider_name,
        "requested_model": requested_model,
        "prompt_version": prompt_version(purpose),
    }

    # Optional Langfuse generation (app/observability/tracing.py). The
    # payload is already masked here; headers (the API key) never go in.
    messages = payload.get("messages") if isinstance(payload.get("messages"), list) else []
    prompt_chars = sum(len(str(message.get("content") or "")) for message in messages if isinstance(message, dict))
    trace_metadata = {
        "purpose": purpose,
        "assessment_id": base["assessment_id"],
        "provider": base["provider"],
        "prompt_version": base["prompt_version"],
    }
    generation = tracing.observe(
        purpose.lower(),
        as_type="generation",
        model=requested_model,
        model_parameters={
            key: payload[key] for key in ("temperature", "max_tokens") if key in payload
        }
        or None,
        input=messages if tracing.capture_content() else {"message_count": len(messages), "prompt_chars": prompt_chars},
        metadata=trace_metadata,
    )

    with generation:
        started = time.perf_counter()
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=timeout, **kwargs)
        except requests.RequestException as exc:
            _record(
                **base,
                success=False,
                error=str(exc)[:2000],
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
            raise

        duration_ms = int((time.perf_counter() - started) * 1000)
        try:
            body = response.json() if response.content else {}
        except ValueError:
            body = {}
        body = body if isinstance(body, dict) else {}
        usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
        response_model = body.get("model") or requested_model
        cost, cost_source = _cost(response_model, usage)
        _trace_generation(generation, response, body, usage, response_model, cost, duration_ms, trace_metadata)

    _record(
        **base,
        response_model=response_model,
        success=response.status_code == 200,
        http_status=response.status_code,
        error=None if response.status_code == 200 else (response.text or "")[:2000],
        prompt_tokens=usage.get("prompt_tokens"),
        completion_tokens=usage.get("completion_tokens"),
        total_tokens=usage.get("total_tokens"),
        cost_usd=cost,
        cost_source=cost_source,
        duration_ms=duration_ms,
    )
    return response
