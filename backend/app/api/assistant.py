"""
POST /api/assistant/chat -- the read-only assistant (see app/assistant).

Safe to call by every signed-in role, including the read-only ones: it
changes nothing (the tools are read-only and scoped to the caller). The
read-only-role write block in app/auth/access.py therefore lets this one
POST through (READ_ONLY_POST_ALLOWLIST).
"""

from __future__ import annotations

import time
from collections import defaultdict, deque

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.assistant.orchestrator import AssistantUnavailable, run_chat
from app.auth.dependencies import get_current_user
from app.database import get_db
from app.models.user import User
from app.services.audit_service import AuditAction, actor_name, log_audit_event

router = APIRouter(prefix="/api/assistant", tags=["Assistant"])

# A cost and abuse guard: each model call costs money, so one person can
# only ask so often. Per process; fine for one web service.
RATE_LIMIT_REQUESTS = 20
RATE_LIMIT_WINDOW_SECONDS = 300
_recent: dict[int, deque[float]] = defaultdict(deque)


def _check_rate_limit(user_id: int) -> None:
    now = time.monotonic()
    window = _recent[user_id]
    while window and now - window[0] > RATE_LIMIT_WINDOW_SECONDS:
        window.popleft()
    if len(window) >= RATE_LIMIT_REQUESTS:
        raise HTTPException(
            status_code=429,
            detail="You're asking quite quickly. Please wait a few minutes and try again.",
        )
    window.append(now)


class ChatMessage(BaseModel):
    # Only what a person can legitimately say. System and tool messages are
    # built on the server, never accepted from the browser.
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(min_length=1, max_length=2000)


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=30)
    # The assessment the user has open, if any ("summarise this one").
    assessment_id: int | None = Field(default=None, ge=1)


class ChatReference(BaseModel):
    id: int
    reference_id: str | None = None
    title: str | None = None


class ChatResponse(BaseModel):
    reply: str
    tools_used: list[str]
    references: list[ChatReference]


@router.post("/chat", response_model=ChatResponse)
def chat(
    request: ChatRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if request.messages[-1].role != "user":
        raise HTTPException(status_code=422, detail="The last message must be from the user.")

    _check_rate_limit(current_user.id)

    try:
        result = run_chat(
            db,
            current_user,
            [message.model_dump() for message in request.messages],
            request.assessment_id,
        )
    except AssistantUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    # Who looked at what: one audit event per assessment whose detail the
    # assistant read for this person. The question text is not recorded.
    for assessment_id in result["assessment_ids_read"]:
        log_audit_event(
            db=db,
            assessment_id=assessment_id,
            action=AuditAction.ASSISTANT_QUERY,
            actor=actor_name(current_user),
            actor_id=current_user.id,
            details="The assistant read this assessment's details for the user.",
        )
    if result["assessment_ids_read"]:
        db.commit()

    return ChatResponse(
        reply=result["reply"],
        tools_used=result["tools_used"],
        references=result["references"],
    )
