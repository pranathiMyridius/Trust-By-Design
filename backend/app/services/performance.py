"""
Stage 19 (Performance): service targets and in-process response timing.

Every API request is timed (see app/middleware.py). Requests are grouped
by route template and classified so they can be compared to an agreed
target:

    PAGE      GET requests that back a page       PAGE_LOAD_TARGET_MS (2000)
    RISK_CALC risk calculation after input        RISK_CALC_TARGET_MS (3000)
    SUBMIT    everything else that changes data   SUBMIT_TARGET_MS    (5000)

Long-running work (document extraction, risk analysis via
/analyze-async) runs as a background job and is not on the request path,
so it doesn't count against these targets. The synchronous /analyze is
excluded for the same reason (it calls an external LLM).

GET /api/system/performance reports p50/p95/max and target breaches per
route from a rolling window of the most recent samples. Samples are kept
in memory only -- this is a live operational view, not a metrics store.
"""

from __future__ import annotations

import os
import re
import threading
from collections import defaultdict, deque

PAGE_LOAD_TARGET_MS = float(os.getenv("PAGE_LOAD_TARGET_MS", "2000"))
RISK_CALC_TARGET_MS = float(os.getenv("RISK_CALC_TARGET_MS", "3000"))
SUBMIT_TARGET_MS = float(os.getenv("SUBMIT_TARGET_MS", "5000"))

WINDOW = int(os.getenv("PERFORMANCE_SAMPLE_WINDOW", "500"))

_RISK_CALC_PATTERNS = (
    re.compile(r"/risk-factors/\{[^}]+\}/rating$"),
    re.compile(r"/risk-factors/\{[^}]+\}/exclude$"),
    re.compile(r"/inherent-risk/override$"),
    re.compile(r"/manual-score(/draft)?$"),
    re.compile(r"/audit/calculator$"),
    re.compile(r"/controls/.*assessment"),
    re.compile(r"/overrides$"),
)

_EXCLUDED = (
    re.compile(r"/analyze$"),
    re.compile(r"/draft/generate$"),
    re.compile(r"/create-(from|with)-document$"),
    re.compile(r"/analyze-document$"),
)

_lock = threading.Lock()
_samples: dict[tuple[str, str], deque] = defaultdict(lambda: deque(maxlen=WINDOW))


def classify(method: str, route: str) -> str | None:
    if any(p.search(route) for p in _EXCLUDED):
        return None
    if method != "GET" and any(p.search(route) for p in _RISK_CALC_PATTERNS):
        return "RISK_CALC"
    if method == "GET":
        return "PAGE"
    return "SUBMIT"


def target_for(category: str | None) -> float | None:
    return {
        "PAGE": PAGE_LOAD_TARGET_MS,
        "RISK_CALC": RISK_CALC_TARGET_MS,
        "SUBMIT": SUBMIT_TARGET_MS,
    }.get(category or "")


def record(method: str, route: str, duration_ms: float, status: int) -> None:
    with _lock:
        _samples[(method, route)].append((duration_ms, status))


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round(pct / 100 * (len(ordered) - 1)))))
    return round(ordered[index], 1)


def summary() -> dict:
    with _lock:
        snapshot = {key: list(values) for key, values in _samples.items()}

    routes = []
    for (method, route), samples in snapshot.items():
        category = classify(method, route)
        target = target_for(category)
        durations = [d for d, _ in samples]
        breaches = sum(1 for d in durations if target is not None and d > target)
        p95 = _percentile(durations, 95)
        routes.append(
            {
                "method": method,
                "route": route,
                "category": category or "BACKGROUND_OR_EXTERNAL",
                "target_ms": target,
                "samples": len(durations),
                "p50_ms": _percentile(durations, 50),
                "p95_ms": p95,
                "max_ms": round(max(durations), 1) if durations else 0.0,
                "breaches": breaches,
                "within_target": target is None or p95 <= target,
                "errors": sum(1 for _, status in samples if status >= 500),
            }
        )

    routes.sort(key=lambda r: (r["within_target"], -r["p95_ms"]))

    return {
        "targets_ms": {
            "PAGE": PAGE_LOAD_TARGET_MS,
            "RISK_CALC": RISK_CALC_TARGET_MS,
            "SUBMIT": SUBMIT_TARGET_MS,
        },
        "window": WINDOW,
        "routes": routes,
        "routes_outside_target": sum(1 for r in routes if not r["within_target"]),
    }
