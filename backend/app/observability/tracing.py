"""
Optional Langfuse tracing for the risk-assessment workflow and every
outbound AI call.

Observability must never be a dependency of the analysis itself, so:

  * It is off unless LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY are both
    set (and LANGFUSE_ENABLED is not "false"). With it off, or with the
    langfuse package missing, every helper here is a cheap no-op.
  * Any failure inside Langfuse -- client construction, a span, an
    update, a flush -- is swallowed and logged once at debug level. An
    exception raised by the *traced code* always propagates unchanged.
  * No prompt or completion text is sent by default. Spans carry ids,
    model, timings, token usage, status codes and sizes. Setting
    LANGFUSE_CAPTURE_CONTENT=true additionally sends the prompt and
    reply, *after* the same masking applied before content reaches the
    AI provider (app/services/data_masking.py). API keys and headers are
    never passed to a span.

Configuration (environment):
    LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY   required to enable
    LANGFUSE_HOST (or LANGFUSE_BASE_URL)       default: Langfuse cloud
    LANGFUSE_ENABLED=false                     force off
    LANGFUSE_CAPTURE_CONTENT=true              include masked prompt/reply
    LANGFUSE_ENVIRONMENT                       e.g. "local", "eval", "ci"
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any

logger = logging.getLogger(__name__)

_TRUTHY = {"1", "true", "yes", "on"}

_client: Any = None
_client_failed = False
_lock = threading.Lock()


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in _TRUTHY


def is_configured() -> bool:
    return bool(
        _env_flag("LANGFUSE_ENABLED", True)
        and (os.getenv("LANGFUSE_PUBLIC_KEY") or "").strip()
        and (os.getenv("LANGFUSE_SECRET_KEY") or "").strip()
    )


def capture_content() -> bool:
    return _env_flag("LANGFUSE_CAPTURE_CONTENT", False)


def _get_client() -> Any:
    global _client, _client_failed

    if _client is not None or _client_failed or not is_configured():
        return _client

    with _lock:
        if _client is not None or _client_failed:
            return _client
        try:
            from langfuse import Langfuse

            host = os.getenv("LANGFUSE_HOST") or os.getenv("LANGFUSE_BASE_URL") or None
            _client = Langfuse(
                public_key=os.getenv("LANGFUSE_PUBLIC_KEY"),
                secret_key=os.getenv("LANGFUSE_SECRET_KEY"),
                host=host,
                environment=os.getenv("LANGFUSE_ENVIRONMENT") or None,
            )
        except Exception as exc:  # noqa: BLE001 -- tracing never breaks the app
            _client_failed = True
            logger.warning("Langfuse tracing disabled: client could not start (%s).", type(exc).__name__)
    return _client


def warm_up_in_background() -> None:
    """
    Importing the SDK and building the client takes several seconds the
    first time. Do it off the request path at startup so the first
    analysis after a restart doesn't pay for it. No-op when unconfigured.
    """

    if is_configured():
        threading.Thread(target=_get_client, name="langfuse-warm-up", daemon=True).start()


def safe_error(exc: BaseException) -> str:
    """
    A short, loggable description of an exception. AI provider errors are
    reduced to their stable error code, because their messages can echo
    back provider responses and therefore prompt content.
    """

    code = getattr(exc, "error_code", None)
    if code:
        return f"{type(exc).__name__} ({code})"

    from app.services.data_masking import mask_sensitive_text

    message = mask_sensitive_text(str(exc)) or ""
    return f"{type(exc).__name__}: {message[:300]}"


class Observation:
    """
    Context manager around one Langfuse observation (span/generation).
    With tracing off it does nothing; `update()` and `trace_id` are
    always safe to call.
    """

    def __init__(self, name: str, as_type: str = "span", **attributes: Any):
        self._name = name
        self._as_type = as_type
        self._attributes = {key: value for key, value in attributes.items() if value is not None}
        self._cm: Any = None
        self._obs: Any = None

    def __enter__(self) -> "Observation":
        client = _get_client()
        if client is None:
            return self
        try:
            self._cm = client.start_as_current_observation(
                name=self._name, as_type=self._as_type, **self._attributes
            )
            self._obs = self._cm.__enter__()
        except Exception as exc:  # noqa: BLE001
            logger.debug("Langfuse span %s could not start: %s", self._name, exc)
            self._cm = self._obs = None
        return self

    @property
    def active(self) -> bool:
        return self._obs is not None

    @property
    def trace_id(self) -> str | None:
        return getattr(self._obs, "trace_id", None) if self._obs is not None else None

    def update(self, **fields: Any) -> None:
        if self._obs is None:
            return
        try:
            self._obs.update(**{key: value for key, value in fields.items() if value is not None})
        except Exception as exc:  # noqa: BLE001
            logger.debug("Langfuse span %s update failed: %s", self._name, exc)

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc is not None:
            self.update(level="ERROR", status_message=safe_error(exc))
        if self._cm is not None:
            try:
                self._cm.__exit__(exc_type, exc, tb)
            except Exception as close_exc:  # noqa: BLE001
                logger.debug("Langfuse span %s could not close: %s", self._name, close_exc)
        # Never suppress the traced code's own exception.
        return False


def observe(name: str, as_type: str = "span", **attributes: Any) -> Observation:
    return Observation(name, as_type, **attributes)


def trace_url(trace_id: str | None) -> str | None:
    client = _get_client()
    if client is None or not trace_id:
        return None
    try:
        return client.get_trace_url(trace_id=trace_id)
    except Exception:  # noqa: BLE001
        return None


def score_trace(trace_id: str | None, name: str, value: float, comment: str | None = None) -> None:
    """Attach an evaluation score to a trace (used by tests/evals)."""

    client = _get_client()
    if client is None or not trace_id:
        return
    try:
        client.create_score(name=name, value=float(value), trace_id=trace_id, comment=comment)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Langfuse score %s could not be recorded: %s", name, exc)


def flush() -> None:
    """Send buffered spans now (short-lived processes: evals, scripts)."""

    client = _get_client()
    if client is None:
        return
    try:
        client.flush()
    except Exception as exc:  # noqa: BLE001
        logger.debug("Langfuse flush failed: %s", exc)


def reset_for_tests() -> None:
    """Forget the cached client so a test can change the configuration."""

    global _client, _client_failed
    with _lock:
        _client = None
        _client_failed = False
