"""
Stage 19: ASGI middleware for the cross-cutting non-functional
requirements. Pure ASGI (not BaseHTTPMiddleware) so they don't buffer
streamed responses and can see the matched route template.

  * SecurityHeadersMiddleware -- protection in transit: HSTS (when served
    over HTTPS or FORCE_HTTPS is set), no MIME sniffing, no framing, no
    referrer leakage, and `Cache-Control: no-store` on API responses so
    assessment data isn't left in shared/browser caches.
  * RequestTimingMiddleware -- times every /api request against the
    service targets in app/services/performance.py and returns the
    duration in `Server-Timing` / `X-Response-Time-Ms`.
  * AdminAuditMiddleware -- every state-changing request made by an ADMIN
    is written to the audit log (method, route, outcome) -- request bodies
    are never logged, so passwords etc. can't leak into the audit trail.
"""

from __future__ import annotations

import logging
import os
import re
import time

import anyio

from app.services import performance

logger = logging.getLogger("app.performance")

MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _route_template(scope) -> str:
    route = scope.get("route")
    path = getattr(route, "path", None)
    if path:
        return path
    return re.sub(r"/\d+(?=/|$)", "/{id}", scope.get("path", ""))


def _force_https() -> bool:
    return os.getenv("FORCE_HTTPS", "false").strip().lower() in {"1", "true", "yes"}


class SecurityHeadersMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        is_api = scope.get("path", "").startswith("/api")
        https = scope.get("scheme") == "https" or _force_https()

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                existing = {name.lower() for name, _ in headers}

                def add(name: str, value: str) -> None:
                    if name.encode() not in existing:
                        headers.append((name.encode(), value.encode()))

                add("x-content-type-options", "nosniff")
                add("x-frame-options", "DENY")
                add("referrer-policy", "no-referrer")
                add("permissions-policy", "camera=(), microphone=(), geolocation=()")
                if https:
                    add("strict-transport-security", "max-age=31536000; includeSubDomains")
                if is_api:
                    add("cache-control", "no-store")
                    add("pragma", "no-cache")
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_wrapper)


class RequestTimingMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope.get("path", "").startswith("/api"):
            await self.app(scope, receive, send)
            return

        started = time.perf_counter()
        status_holder = {"status": 500}

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                elapsed = (time.perf_counter() - started) * 1000
                headers = list(message.get("headers", []))
                headers.append((b"server-timing", f"app;dur={elapsed:.1f}".encode()))
                headers.append((b"x-response-time-ms", f"{elapsed:.1f}".encode()))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            elapsed = (time.perf_counter() - started) * 1000
            method = scope.get("method", "GET")
            route = _route_template(scope)
            performance.record(method, route, elapsed, status_holder["status"])
            target = performance.target_for(performance.classify(method, route))
            if target is not None and elapsed > target:
                logger.warning(
                    "Slow request: %s %s took %.0f ms (target %.0f ms)",
                    method,
                    route,
                    elapsed,
                    target,
                )


def _log_admin_action(token: str, method: str, route: str, path_params: dict, status: int) -> None:
    from app.auth.security import decode_access_token
    from app.database import SessionLocal
    from app.models.user import User, UserRole
    from app.services.audit_service import AuditAction, log_audit_event

    user_id = decode_access_token(token)
    if user_id is None:
        return

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == int(user_id)).first()
        if user is None or user.role != UserRole.ADMIN.value:
            return

        assessment_id = path_params.get("assessment_id")
        try:
            assessment_id = int(assessment_id) if assessment_id is not None else None
        except (TypeError, ValueError):
            assessment_id = None

        outcome = "succeeded" if status < 400 else f"was rejected (HTTP {status})"
        log_audit_event(
            db=db,
            assessment_id=assessment_id,
            action=AuditAction.ADMIN_ACTION,
            actor=user.full_name or user.email,
            actor_id=user.id,
            details=f"Administrative request {method} {route} {outcome}.",
        )
        db.commit()
    except Exception as exc:  # noqa: BLE001 -- logging must never break a request
        db.rollback()
        logging.getLogger(__name__).warning("Could not record admin action: %s", exc)
    finally:
        db.close()


class AdminAuditMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        method = scope.get("method", "")
        path = scope.get("path", "")
        if (
            scope["type"] != "http"
            or method not in MUTATING_METHODS
            or not path.startswith("/api")
            or path.startswith("/api/auth/")
        ):
            await self.app(scope, receive, send)
            return

        status_holder = {"status": 500}

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            auth = dict(scope.get("headers", [])).get(b"authorization", b"").decode()
            if auth.lower().startswith("bearer "):
                await anyio.to_thread.run_sync(
                    _log_admin_action,
                    auth[7:],
                    method,
                    _route_template(scope),
                    dict(scope.get("path_params") or {}),
                    status_holder["status"],
                )
