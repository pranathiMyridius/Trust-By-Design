# P5: Retention and data lifecycle (implementation prompt)

Prepared 2026-10-02 from a review of the working tree. Give this to the coding
agent **only after the preconditions in §0 are met**.

---

## Role and context

You are working on the Financial Crime Risk Assessment Workbench (FastAPI +
SQLAlchemy + Alembic backend in `backend/`, React/TypeScript/Vite frontend in
`frontend/`, Neon Postgres in production, SQLite/local Postgres for tests).
Phases P1 (security) and P2 (governance records) are complete. P3 (SoD
exceptions, governance designations) must be closed out first (§0). You are
implementing **P5: retention and data lifecycle** (brief R16.1, R16.3, R16.4).

Read first: `docs/REMAINING_REQUIREMENTS.md` (§2.3, §4 Q-3, §6), `docs/DEPLOYMENT.md`
(migration guard), `backend/app/governance/policy.py` (how provisional,
configurable rules are built), `backend/app/api/audit_trail.py`,
`backend/app/models/audit_trail.py`, `backend/app/schemas/audit_trail.py`,
`backend/app/auth/access.py`, `backend/app/services/data_protection.py`,
`backend/app/services/audit_service.py`, and the retention panel in
`frontend/src/components/AssessmentWorkflow.tsx` (search `AssessmentRetentionInfo`).

## Hard safety rules (non-negotiable)

1. **Never connect to, migrate or run DDL against the primary Neon database.**
   `backend/.env` `DATABASE_URL` is the primary. Never run `import app.main`,
   `alembic`, uvicorn or any script importing `app.database` without setting
   `DATABASE_URL` to a throwaway database in the same command. Use
   `python -m py_compile` for syntax checks; `pytest` is safe (its conftest
   forces a temp SQLite DB).
2. Remote validation only on the authorized staging branch through
   `scripts/validate_staging_migration.py`, and **only after the user approves
   that run in chat**. Never pass `--allow-protected-host`.
3. Do not weaken or bypass the migration guard (`app/migrations.py`,
   `alembic/env.py`).
4. Never print, log or commit credentials. Do not edit `backend/.env`.
5. **No physical deletion** of any row or file. Disposal stays a logged soft
   delete. Physical purge is out of scope (see §6).
6. Do not `git commit` or push. Leave changes in the working tree.
7. Retention periods are **not compliance-approved**. Never label any period or
   rule as approved. Everything new carries
   `policy_status = "PROVISIONAL_PENDING_GOVERNANCE_APPROVAL"`.

## 0. Preconditions (verify, don't assume)

- [ ] P3 is closed out: its tests exist and pass, the 11 P2 tests broken by P3's
      role changes have been updated (not weakened), and the tracker has a P3 report.
- [ ] `pytest` (tests/unit, tests/api, tests/workflow) is fully green. Record the count.
- [ ] `cd frontend && npx tsc -b` is clean.
- [ ] Playwright: only the 3 known baseline failures (§5 of the tracker), or fewer.
- [ ] `alembic heads` (with a temp `DATABASE_URL`) shows a **single head**. Your
      migration revises it (expected `0018_sod_governance`; if P3 renamed it, use
      the real head).
- [ ] No other agent session is editing the same files.

If any box fails, stop and report. Don't start P5 on a red baseline.

## 1. What exists today (gaps to close)

| Area | Today | Gap |
|---|---|---|
| Policy | One `retention_policies` row, `default_retention_days=2555`, edited **in place** by `PATCH /api/retention-policy` (Admin) | No versions, no reason, no previous value, no record types, no provisional status, logged as generic `STATUS_CHANGE` |
| Legal hold | `assessment_retention.legal_hold*` columns, overwritten on each change | No history; release needs no reason; generic `STATUS_CHANGE` |
| Soft delete | `POST /api/assessments/{id}/soft-delete`: requires final decision + elapsed period + no hold + reason | Doesn't record which policy version made it eligible; generic audit action |
| Eligibility | Computed per assessment on read | No portfolio view of what is eligible, held or close to eligibility |
| UI | Per-assessment retention panel in `AssessmentWorkflow.tsx` | No admin screen for policy, versions or holds |
| Read access | `GET /api/retention-policy` and `/assessments/{id}/retention` are open to any authenticated user | Acceptable (no sensitive content), but responses must add `policy_status` and must respect the P1 soft-delete read gate |

## 2. Scope

### 2.1 Versioned retention policy (R16.4)

- New table `retention_policy_versions` (append-only):
  `id, record_type, version, retention_days, basis` (`FINAL_DECISION_DATE` for
  assessments), `effective_from, status` (`PROPOSED | ACTIVE | SUPERSEDED | REJECTED`),
  `policy_status` (always the provisional constant), `change_reason`,
  `proposed_by_id, proposed_at, decided_by_id, decided_at, decision_reason,
  supersedes_id`.
  Plus nullable `governance_approval_reference` and `governance_approved_at`,
  which the application **never** sets. They exist only so a later, real approval
  can be recorded by a separate, explicit process.
- `record_type` is a configured enum. Seed it with only `ASSESSMENT`. The structure
  must accept future types (`DOCUMENT`, `AUDIT_EVENT`, `AI_USAGE_LOG`), but **don't
  invent periods for them**. An unknown record type has "no policy" and is never eligible.
- The migration copies the active `retention_policies` row into
  `retention_policy_versions` as `ASSESSMENT` v1 `ACTIVE`, with
  `change_reason = "Migrated existing default; not compliance-approved"`. Keep
  `retention_policies` for backward compatibility. Make it a read-through of the
  active ASSESSMENT version, so the legacy `GET/PATCH /api/retention-policy` keeps
  working. Legacy PATCH now creates a proposal (§2.2) instead of editing in place.
- Validation: `retention_days` is an integer with a configured minimum and maximum
  (defaults 365 and 36500, provisional). The reason must be at least 20 characters
  after trimming.
- ORM protection in `services/data_protection.py`: versions are append-only. Only the
  status and decision fields may change, once, from PROPOSED. No deletes.

### 2.2 Who may change it: configurable, provisional

Add to `governance/policy.py` `DEFAULT_POLICY` (don't hard-code in routes):

```python
"retention": {
    "proposers":  {"roles": ["ADMIN"]},
    "approvers":  {"designations": ["FCRM_GOVERNANCE_OWNER", "COMPLIANCE_MANAGER"]},
    "require_independent_approval": True,   # DECISION D-1
    "legal_hold_setters":   {"roles": ["ADMIN"], "designations": ["COMPLIANCE_MANAGER"]},
    "legal_hold_releasers": {"roles": ["ADMIN"], "designations": ["COMPLIANCE_MANAGER"]},
    "release_requires_different_user": True, # DECISION D-2
    "min_retention_days": 365, "max_retention_days": 36500,
    "soft_deleters": {"roles": ["ADMIN"]},
    "eligibility_report_readers": {"roles": ["ADMIN", "AUDITOR"]},
}
```

- With `require_independent_approval`, a proposal becomes ACTIVE only when an approver
  who is **not the proposer** approves it, with a reason. The previous ACTIVE version is
  marked SUPERSEDED in the same transaction. Rejection needs a reason. There's no
  self-approval, and an Admin is not assumed to be an approver (Q-2/Q-6 precedent).
- With the flag off, the proposer's change activates immediately. It's still versioned,
  reasoned and audited.
- Only one PROPOSED version per record type at a time (409 otherwise).
- Every refusal goes through the existing `log_denied_attempt` (`ACCESS_DENIED`).
- Update the admin-route regex in `auth/access.py` for the new write routes.

### 2.3 Legal holds with history (R16.1, R16.4)

- New append-only table `legal_hold_events`:
  `id, assessment_id, action (SET | RELEASED), reason, actor_id, actor_name, created_at,
  matter_reference` (optional free text, max 100 characters, never a document body).
- `assessment_retention.legal_hold*` stays as the current state, updated in the same
  transaction as the event.
- Setting a hold that's already set, or releasing one that's not set, returns 409.
  A release needs a reason. With `release_requires_different_user`, the releaser can't
  be the user who set the current hold.
- A hold can be set on any assessment, in any status, including one not yet final.
  It still blocks soft deletion.
- A soft-deleted assessment can't get a new hold. It returns 409 and is logged.

### 2.4 Eligibility and soft delete

- A single service function `retention_status(db, assessment)` returns
  `{record_type, policy_version_id, retention_days, basis_date, eligible_at, eligible,
  legal_hold, reason_not_eligible}`. It's used by the per-assessment route, the report
  and soft delete, so there's one rule.
- The soft-delete route uses it and stores `retention_policy_version_id` on
  `assessment_retention` (new nullable column) at deletion time.
- New `GET /api/retention/eligibility?status=eligible|held|upcoming&within_days=N`
  (eligibility report readers only). It returns assessment id, reference, status,
  basis date, eligible_at, hold flag and policy version. **No risk content or document
  text.** It's paginated. Soft-deleted assessments appear only with
  `?include_deleted=true`.
- **No scheduler and no automatic deletion.** The report is read-only.

### 2.5 Audit (R16.3)

Add dedicated `AuditAction` values. Stop using `STATUS_CHANGE` for these:
`RETENTION_POLICY_PROPOSED`, `RETENTION_POLICY_APPROVED`, `RETENTION_POLICY_REJECTED`,
`RETENTION_POLICY_ACTIVATED`, `LEGAL_HOLD_SET`, `LEGAL_HOLD_RELEASED`,
`ASSESSMENT_SOFT_DELETED`, `RETENTION_REPORT_VIEWED`.

`details` carries the previous and new values (days, version ids, hold state), the
reason and the policy status. Never include document content. Existing audit rows
aren't relabelled.

### 2.6 Frontend

- New **Retention & legal holds** admin page (nav entry visible to proposers,
  approvers and report readers only; reuse the `GovernancePage.tsx` / `UsersAdminPage.tsx`
  patterns and `governanceHooks.ts`):
  - A persistent "Provisional: pending governance approval. Periods are not
    compliance-approved." banner.
  - Active policy per record type, version history (who, when, why, previous to new).
  - Propose form (days + reason), and pending proposal with Approve/Reject plus reason.
    Hide or disable the controls with an explanatory message for users who can't act.
  - Eligibility report table with filters.
- Per-assessment retention panel: show the policy version and status, hold history,
  Set/Release with reason, and the existing soft-delete action using the shared
  eligibility result.
- Backend errors (403/409) are shown as given. No client-side-only enforcement.
- `npx tsc -b` clean. No new ESLint findings in touched lines.

## 3. Migration `0019_retention_lifecycle`

- Revises the current single head. Additive and idempotent, in the same style as
  0017/0018 (inspect before adding).
- Creates `retention_policy_versions` and `legal_hold_events`, and adds
  `assessment_retention.retention_policy_version_id`.
- Backfills v1 from the active `retention_policies` row, or a default row if none
  exists. For each assessment currently on hold, it backfills one `legal_hold_events`
  SET row from the existing columns, marked `reason = "Backfilled from pre-P5 state: "
  + original reason`.
- Downgrade **refuses** if any version beyond the backfilled v1 or any non-backfilled
  hold event exists, rather than lose lifecycle history.
- Postgres and SQLite compatible.

## 4. Tests (all against isolated databases)

- `backend/tests/api/test_p5_retention.py`, at minimum:
  - The propose → independent approve flow. Self-approval is refused and logged.
    Admin without a designation can't approve. Reject needs a reason. Only one open proposal.
  - With the flag off, immediate activation (use `governance.policy.reload()` and a temp
    `GOVERNANCE_POLICY_FILE`).
  - Legacy `PATCH /api/retention-policy` creates a proposal and never edits in place.
  - Out-of-range days and short reasons give 422.
  - Hold set and release history, release reason, different-user rule, double set or
    release give 409, no hold on soft-deleted.
  - Soft delete records the policy version and is blocked by a hold and by
    not-yet-eligible. The P1 soft-delete read gate still holds.
  - Eligibility report: role access (Auditor yes, Manager/Analyst/Executive no), no
    content leakage, filters.
  - Every new audit action is written with previous and new values.
  - ORM: updating or deleting a version or hold event raises.
  - Every response carries `policy_status`.
- `backend/tests/unit/test_migration_0019.py`: upgrade from a real 0018-shape DB with an
  existing policy and a held assessment, backfill, idempotent re-run, downgrade refusal,
  and a clean downgrade when only backfill exists.
- Extend `scripts/validate_staging_migration.py` checks: the new tables exist, v1
  backfilled, governed-table row counts unchanged. (Don't run it against staging without
  approval.)
- Playwright `frontend/tests/e2e/retention.spec.ts`: admin proposes, governance-owner
  approves, banner visible, hold set and release with history, report visible to
  auditor, hidden from analyst. Use the E2E server and seeded users in
  `tests/support/e2e_users.json`. Add designations through the API, not the database.
- Full regression: pytest, legacy scripts (`test_stage19_nfr.py`,
  `test_stage14_workflow.py`, `test_aw7_delegation.py`, `test_stage17_reporting.py`, and
  the degraded/phase-1/phase-2 scripts), `tsc -b`, and Playwright. Report exact counts
  next to the precondition baseline.

## 5. Documentation

- `docs/REMAINING_REQUIREMENTS.md`: add a "P5 Retention" report in the same shape as
  P1 and P2 (what changed, permissions table, audit behaviour, migration, test results,
  unresolved items). Update the §7 checklist row. Q-3 stays open.
- `docs/DEPLOYMENT.md`: retention config keys and the 0019 staging-first rule.
- `docs/BACKUP_AND_RECOVERY.md`: one paragraph distinguishing **backup** retention
  (`BACKUP_KEEP`) from **record** retention.
- `docs/NON_FUNCTIONAL_REQUIREMENTS.md` and `design/requirements-traceability.md` R16 row.

## 6. Out of scope (record as open items, don't build)

- Physical purge of rows or uploaded files, crypto-shredding, and backup expiry for
  disposed records. These are irreversible and need approved periods and a disposal
  sign-off process. Note them for P7/P8.
- Retention periods for document, audit-event and AI-log record types (Q-3).
- Automatic or scheduled disposal.
- Reassessment-chain retention (P6 will link parent and child; until then, each
  assessment's clock runs from its own final decision).

## 7. Acceptance criteria

1. Changing the retention period never overwrites the previous value. History shows
   who, when, why, previous and new, and the version that is active.
2. With independent approval on, no single user can change the active period.
3. Legal holds have an append-only history with reasons for setting and releasing.
   A held assessment can't be soft-deleted.
4. Soft deletion records the policy version that made it eligible. Nothing is
   physically deleted.
5. Every lifecycle action writes a dedicated audit action. Every refusal writes
   `ACCESS_DENIED`.
6. Every retention API response and the UI show the provisional status. Nothing claims
   compliance approval.
7. Migration 0019 passes the local tests. The staging run is prepared but executed only
   on approval.
8. The full regression is green against the §0 baseline, and no existing control was
   weakened.

## 8. Decision points to confirm with the project before coding

- **D-1** Independent approval for retention changes (recommended default: on).
  Approvers: FCRM Governance Owner or Compliance Manager designation.
- **D-2** A legal hold is released by a different user from the one who set it
  (recommended: on).
- **D-3** Minimum and maximum bounds of 365 and 36,500 days (placeholders, provisional).
- **D-4** Auditor can read the eligibility report (recommended: yes; it's read-only, no content).

If unconfirmed, build them as the configurable defaults above and list them as open.

## 9. Report back

Changed files, the migration summary, the permissions table, the audit actions, test
counts against the baseline, anything skipped, and open questions. Don't mark P5
complete until all §7 criteria are verified.
