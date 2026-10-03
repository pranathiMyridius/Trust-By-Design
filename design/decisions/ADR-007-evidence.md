# ADR-007 — Evidence-first AI findings

**Status:** Accepted (retain; extend labels) · **Date:** 2026-09-30

**Context.** `risk_engine/evidence.py` verifies every model quote by exact normalised substring match against intake fields and uploaded documents, drops unverified quotes, rejects indicators without a verified on-topic quote, and sets a per-factor `evidence_status`.

**Problem.** Stop the AI from inventing evidence, and make the difference between evidence, inference and missing information unmistakable.

**Decision.** A factor's indicators and any rule escalation depend only on **verified quotes** or **attested reference data**. Every AI output is classified as *verified evidence* (with origin USER_PROVIDED / DOCUMENT / SYSTEM_DERIVED), *AI inference* (rationale, misuse scenario, suggestions), or *missing information*. UI wording: "quote found in source", never "fact verified". No external retrieval in MVP.

**Alternatives considered.** Trust model citations (hallucination risk); fuzzy matching (accepts paraphrase, weakens proof); retrieval-augmented external sources (provenance and licensing questions; not needed for MVP).

**Why selected.** Deterministic, cheap, auditable; eval shows 94 % of quotes verify.

**Benefits.** Hallucinated evidence cannot drive a score. **Trade-offs.** Genuine but paraphrased evidence is rejected (analyst can add manually); quote verification proves presence, not truth.

**Consequences.** Topic cues must be maintained per high-impact indicator; new indicators need cues before approval.
