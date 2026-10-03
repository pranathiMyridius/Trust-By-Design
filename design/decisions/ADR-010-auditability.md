# ADR-010 — Auditability by design

**Status:** Accepted (retain; extend) · **Date:** 2026-09-30

**Context.** `audit_events` is append-only via ORM hooks, `workflow_transitions` records every status change with reason, 12 tables block deletes, decision records carry SHA-256 checksums, admin actions are logged by middleware. Gaps: actor missing or client-supplied in many events; any user can read all audit events; decision records and several calculation tables are not protected; no request correlation.

**Problem.** Make every change attributable, tamper-evident and retrievable for audit.

**Decision.** (1) Actor id from JWT on every event; (2) `request_id`, entity reference and before/after JSON on audit events; (3) extend protected/append-only tables (decision records, residual calcs, challenge findings, runs, prompts); (4) no GET writes, except explicitly auditing exports; (5) audit read-all restricted to ADMIN (AUDITOR in production); (6) production: DB-level triggers, least-privilege roles and a per-assessment hash chain; (7) retention + legal hold as today.

**Alternatives considered.** External audit service / SIEM only (MVP overkill; keep as production sink); event sourcing (large redesign).

**Why selected.** Builds on what exists; low cost; satisfies "who changed what, when, why, from what".

**Benefits.** Reconstructable history; tamper evidence. **Trade-offs.** More rows; before/after payloads must be masked.

**Consequences.** Audit export (`/audit-export`) becomes the auditor's primary artefact; integrity check verifies decision-record checksums and (production) hash chains.
