"""
Typed AI-provider failures, so callers can tell an *operational* AI
outage (retry later, fall back to deterministic rules) apart from a
problem with the assessment itself (no fallback can compensate).

Everything here subclasses RuntimeError, which is what the analyzers
raised before this module existed -- callers that still catch
RuntimeError keep working unchanged.

The distinction matters because of how the caller uses it (see
app/risk_engine/degraded.py and app/langgraph/nodes.py::identify_risks):

  * AIProviderError and its subclasses  -> rules-only fallback is safe.
  * anything else (bad input data, broken methodology config, a failure
    inside the rule engine itself) -> the assessment is `unavailable`
    and gets no risk rating at all.
"""

from __future__ import annotations


class AIProviderError(RuntimeError):
    """
    An operational failure of the AI provider: the request was well
    formed and the input data was fine, the provider just did not give
    us a usable answer.

    `error_code` is the short, stable token recorded on the assessment
    and in the audit trail (technical_error_code). It is for logs and
    admins -- never shown verbatim to ordinary users, because provider
    messages can echo back prompt content.
    """

    error_code = "AI_PROVIDER_ERROR"
    # Which `ai_status` this failure maps to. See
    # app/risk_engine/degraded.py::AiStatus.
    ai_status = "failed"


class AITimeoutError(AIProviderError):
    error_code = "AI_TIMEOUT"
    ai_status = "timeout"


class AIRateLimitError(AIProviderError):
    error_code = "AI_RATE_LIMITED"
    ai_status = "rate_limited"


class AIInvalidResponseError(AIProviderError):
    """The provider answered, but not with anything we can parse/trust."""

    error_code = "AI_INVALID_RESPONSE"
    ai_status = "invalid_response"


class AINotConfiguredError(AIProviderError):
    """No API key. Operationally identical to an outage from here."""

    error_code = "AI_NOT_CONFIGURED"
    ai_status = "failed"
