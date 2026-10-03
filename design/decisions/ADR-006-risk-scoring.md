# ADR-006 — Deterministic, versioned risk scoring

**Status:** Accepted (retain; formalise) · **Date:** 2026-09-30

**Context.** The code already prevents the LLM from scoring: the prompt forbids numbers, AI factors start unrated, and `risk_engine/scoring.py` computes L×I, weighted average, bands, escalation floors and residual grid from an active, fingerprinted methodology. Gaps: unused `thresholds`, weight-0 edge case, hard-coded bands in two endpoints, no confidence measure, no engine version.

**Problem.** Guarantee that every score and band of record is reproducible and explainable.

**Decision.** Scores and bands are computed **only** by pure Python from (analyst ratings, verified indicators, attested reference data, methodology snapshot, engine version). The LLM may suggest L×I but suggestions never enter the calculation. Add `SCORING_ENGINE_VERSION`, deterministic `confidence_level`, inputs hash, and a replay check. All band logic uses the methodology's `risk_bands` (remove hard-coded thresholds).

**Alternatives considered.** LLM-produced scores (non-reproducible, unexplainable); ML model trained on past decisions (no labelled dataset; opaque); pure rules without analyst ratings (cannot capture judgement).

**Why selected.** Regulatory expectation of explainable, consistent methodology; already implemented.

**Benefits.** Same inputs → same output; full "why" breakdown; methodology change control. **Trade-offs.** Analysts must rate every applicable factor (effort); rules need governance.

**Consequences.** Replay tests in CI; methodology activation validates bands/weights; UI must show UNRATED rather than implied low scores.
