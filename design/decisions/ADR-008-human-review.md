# ADR-008 — Human-in-the-loop approval as the decision authority

**Status:** Accepted (retain; close gaps) · **Date:** 2026-09-30

**Context.** The lifecycle already requires analyst rating, FCRM review, manager decision and committee decision, with delegation, SoD, votes, conditions and a frozen decision record. Gaps: actor names from request bodies, hard-coded "FCRM Reviewer", override guards absent, some endpoints without role checks.

**Problem.** Ensure humans remain accountable for every risk decision and no AI result is silently accepted or overwritten.

**Decision.** Only named, authenticated users can rate, override, resolve findings, approve or reject. Every override stores previous value, new value, source of previous value, reason, comment, user id and timestamp on a new version; originals are retained. Downward band overrides require manager acceptance (auto-created HIGH finding); overrides below non-mitigable floors are blocked. Human review lives in the application state machine, not in the AI workflow.

**Alternatives considered.** Auto-approve LOW-risk results (not acceptable for FC governance without an approved policy — open question Q-03); single-tier approval (weaker SoD).

**Why selected.** Regulatory accountability; already the product's design.

**Benefits.** Clear accountability, four-eyes on risk reduction. **Trade-offs.** More steps for low-risk changes.

**Consequences.** Permission matrix, actor-from-JWT, and override rules are P0 for Stage 3.
