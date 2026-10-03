"""
Live, per-step progress for one stage move (PATCH /advance-stage run as a
STAGE_ADVANCE background job).

Each stage move has a fixed plan of steps (STAGE_STEPS, keyed by the
stage being left). advance_assessment_stage calls `begin(step)` as it
reaches each one, and the risk-assessment graph's nodes report themselves
the same way (see graph._traced). Both are no-ops outside `track` -- e.g.
for the synchronous PATCH /advance-stage request.

The log lives in memory while the job runs: the move shares one
SQLAlchemy session whose work must not be committed half-way (a failed
move is rolled back as a whole), so progress cannot be written to the job
row mid-run. GET /api/processing-jobs/{id} reads the live log; when the
job finishes the handler stores the final log on the row
(ProcessingJob.stage_log). With several API worker processes, a poll that
lands on a different worker sees only the row's coarse status until the
job finishes.
"""

from __future__ import annotations

import contextvars
import json
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator

# The risk-assessment graph's nodes, in execution order.
PIPELINE_NODES: tuple[tuple[str, str], ...] = (
    ("load_assessment", "Loading assessment"),
    ("gather_intelligence", "Investigative AI screening"),
    ("identify_risks", "Risk identification engine"),
    ("calculate_scores", "Preliminary risk score"),
    ("persist_results", "Saving results to the workbench profile"),
)

RECORD_TRANSITION = ("record_transition", "Recording the stage move")

# Steps per stage move, keyed by the stage being LEFT.
STAGE_STEPS: dict[str, tuple[tuple[str, str], ...]] = {
    "INTAKE": (
        ("validate_intake", "Validating intake completeness"),
        RECORD_TRANSITION,
    ),
    "EVIDENCE_COLLECTION": (
        ("confirm_profile", "Checking the confirmed business profile"),
        *PIPELINE_NODES,
        RECORD_TRANSITION,
    ),
    "RISK_IDENTIFICATION": (
        ("check_analysis", "Checking the analysis is final"),
        ("calculate_inherent", "Calculating and freezing inherent risk"),
        RECORD_TRANSITION,
    ),
    "INHERENT_RISK_ASSESSMENT": (
        ("identify_controls", "Identifying applicable controls (AI)"),
        ("evaluate_controls", "Evaluating control effectiveness"),
        RECORD_TRANSITION,
    ),
    "CONTROL_ASSESSMENT": (
        ("check_challenge", "Checking the control assessment outcome"),
        ("calculate_residual", "Calculating and freezing residual risk"),
        RECORD_TRANSITION,
    ),
    "RESIDUAL_RISK": (
        ("generate_draft", "Generating the decision-ready draft (AI)"),
        RECORD_TRANSITION,
    ),
    "REMEDIATION": (RECORD_TRANSITION,),
}

QUEUED, RUNNING, COMPLETED, FAILED = "queued", "running", "completed", "failed"
# Finished, but a best-effort part of it didn't work (e.g. AI control
# suggestions were unavailable); the move itself still went ahead.
WARNING = "warning"
# Not reached in a move that otherwise succeeded (e.g. control evaluation
# when AI control suggestions failed before it).
SKIPPED = "skipped"

_FINISHED = {COMPLETED, FAILED, WARNING, SKIPPED}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class StageLog:
    """Status and timestamps per step of one stage move. Thread-safe: the
    job's worker thread writes while request threads read."""

    def __init__(self, from_status: str, entries: dict[str, dict] | None = None):
        self._lock = threading.Lock()
        self.from_status = from_status
        self.plan = STAGE_STEPS.get(from_status, (RECORD_TRANSITION,))
        self._keys = {key for key, _ in self.plan}
        self._entries: dict[str, dict] = entries or {}

    # -- writing ------------------------------------------------------------

    def _set(self, key: str, status: str) -> None:
        entry = self._entries.setdefault(key, {"started_at": None, "finished_at": None})
        entry["status"] = status
        if status == RUNNING:
            entry["started_at"] = _now()
        elif status in _FINISHED:
            entry["finished_at"] = _now()
            entry["started_at"] = entry["started_at"] or entry["finished_at"]

    def _running(self) -> str | None:
        for key, _ in self.plan:
            if self._entries.get(key, {}).get("status") == RUNNING:
                return key
        return None

    def begin(self, key: str) -> None:
        """Start `key`; the step before it (if still running) is done."""
        if key not in self._keys:
            return
        with self._lock:
            current = self._running()
            if current and current != key:
                self._set(current, COMPLETED)
            self._set(key, RUNNING)

    def record(self, key: str, status: str) -> None:
        if status == RUNNING:
            self.begin(key)
            return
        if key not in self._keys:
            return
        with self._lock:
            self._set(key, status)

    def warn_running(self) -> None:
        with self._lock:
            current = self._running()
            if current:
                self._set(current, WARNING)

    def finish(self, succeeded: bool) -> None:
        with self._lock:
            current = self._running()
            if current:
                self._set(current, COMPLETED if succeeded else FAILED)
            if succeeded:
                for key, _ in self.plan:
                    if key not in self._entries:
                        self._set(key, SKIPPED)

    # -- reading ------------------------------------------------------------

    def snapshot(self) -> list[dict]:
        """Every step of the plan, in order; steps not reached are queued."""
        with self._lock:
            return [
                {
                    "key": key,
                    "label": label,
                    "status": self._entries.get(key, {}).get("status", QUEUED),
                    "started_at": self._entries.get(key, {}).get("started_at"),
                    "finished_at": self._entries.get(key, {}).get("finished_at"),
                }
                for key, label in self.plan
            ]

    def done_count(self) -> int:
        return sum(1 for step in self.snapshot() if step["status"] in _FINISHED)

    def current_label(self) -> str | None:
        for step in self.snapshot():
            if step["status"] == RUNNING:
                return step["label"]
        return None

    def to_json(self) -> str:
        with self._lock:
            return json.dumps({"from_status": self.from_status, "entries": self._entries})

    @classmethod
    def from_json(cls, raw: str | None) -> "StageLog":
        try:
            data = json.loads(raw) if raw else {}
        except ValueError:
            data = {}
        if not isinstance(data, dict):
            data = {}
        if "from_status" not in data:
            # Logs written before stage moves were generalised held only
            # the risk-identification graph nodes.
            return cls("EVIDENCE_COLLECTION", data or None)
        entries = data.get("entries")
        return cls(str(data["from_status"]), entries if isinstance(entries, dict) else None)


_listener: contextvars.ContextVar[StageLog | None] = contextvars.ContextVar(
    "stage_progress_listener", default=None
)
_live: dict[int, StageLog] = {}
_live_lock = threading.Lock()


@contextmanager
def track(job_id: int, from_status: str) -> Iterator[StageLog]:
    """Collect step progress for the stage move started inside this block."""

    log = StageLog(from_status)
    with _live_lock:
        _live[job_id] = log
    token = _listener.set(log)
    try:
        yield log
    finally:
        _listener.reset(token)
        with _live_lock:
            _live.pop(job_id, None)


def begin(step: str) -> None:
    log = _listener.get()
    if log is not None:
        log.begin(step)


def notify(step: str, status: str) -> None:
    """Graph nodes report running / completed / failed."""
    log = _listener.get()
    if log is not None:
        log.record(step, status)


def warn_running() -> None:
    log = _listener.get()
    if log is not None:
        log.warn_running()


def live_log(job_id: int) -> StageLog | None:
    with _live_lock:
        return _live.get(job_id)
