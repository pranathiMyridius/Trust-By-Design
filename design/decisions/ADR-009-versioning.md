# ADR-009 — AI, prompt, workflow and scoring versioning

**Status:** Proposed (new) · **Date:** 2026-09-30

**Context.** Model names are logged per AI call and methodology is fingerprinted, but there is no prompt version, workflow version, engine version or run entity; prompts are inline f-strings.

**Problem.** An auditor cannot tell which prompt and workflow produced a given set of factors, nor replay the deterministic part.

**Decision.** Introduce `analysis_runs` (one row per AI task execution: run type, workflow version + graph hash, prompt version, requested/served model, params, methodology fingerprint, reference snapshots, input hash + masked snapshot, masked raw output, outcome, trace id, requester) and a `prompt_versions` registry seeded from code (startup fails if a template changes without a version bump). Add `SCORING_ENGINE_VERSION` to calculations and link factors, calculations, drafts, usage logs and audit events to the run. Decision record format 1.1 includes run ids and versions.

**Alternatives considered.** Rely on Langfuse only (optional, external, not part of the record); prompts in DB editable at runtime (bypasses code review); git SHA only (too coarse; not per prompt).

**Why selected.** Minimal new schema; code review remains the control point for prompts; enables replay.

**Benefits.** Full reasoning-path reconstruction; safe prompt/model evolution with eval gates. **Trade-offs.** Storage for input/output snapshots (masked, bounded by retention); discipline to bump versions.

**Consequences.** Migrations 0009–0011; eval baseline per prompt version.
