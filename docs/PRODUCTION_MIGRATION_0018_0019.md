# Production migration runbook: 0017 → 0018 → 0019

**Status: prepared 2026-10-02, NOT executed. Needs explicit approval, and the governance acceptance in §6, before any step that touches the primary.**

Target: Neon project `Trust BY Design` (`summer-flower-19034040`), branch `production` (`br-winter-art-b3xohxo2`), endpoint `ep-falling-water-b35syv2c-pooler.c-4.ap-southeast-1.aws.neon.tech`, database `neondb`. Recovery branch `production-pre-0019` (`br-snowy-math-b3pt5x8g`).

## 1. Review of the migrations

| | 0018_sod_governance | 0019_retention_lifecycle |
|---|---|---|
| Depends on | `0017_governance_records`, the recorded production revision | `0018_sod_governance` |
| Schema | Adds `users.governance_designations`, `risk_factors.rated_by_id`, 8 nullable `assessment_overrides` columns, and `challenge_review_signoffs.stage` (NOT NULL, default `SIGNOFF`) and `.committee_escalation` (NOT NULL, default false). New tables `sod_exceptions` and `sod_exception_events`. | New tables `retention_policy_versions` and `legal_hold_events`; 2 partial unique indexes; 2 nullable `assessment_retention` columns (plain integers, no FK) |
| Data changes | Existing sign-offs become `stage = SIGNOFF` (column default). Existing overrides are not classified. Nothing else changes. | Inserts ASSESSMENT **v1 ACTIVE** (the legacy `retention_policies` days, or 2,555 if there is no row; "not compliance-approved"). Inserts one SET event per assessment on hold. Changes no existing row |
| Idempotent | Yes (checks before every create) | Yes (checks before every create; backfills only if empty) |
| Downgrade | Refuses while SoD exceptions or independent challenge reviews exist. **Otherwise it drops its columns, so designations assigned after deployment and override materiality/approval data would be silently lost.** | Refuses once any proposed or decided version, any hold event, or any soft delete with a recorded version exists |

**Execution:**
- `alembic/env.py` runs all pending revisions in **one transaction** under a Postgres advisory lock. 0018 and 0019 commit together, or not at all.
- Every `ADD COLUMN` is either nullable or uses a constant default, which is a metadata-only change on Postgres 18. Locks are short on small tables: about 10 assessments, 8 users, 236 audit events, 1 override, 0 sign-offs, per the last read-only check on 2026-10-02.

**Evidence so far:**
- 0017 → 0018: staging 15/15, rollback included (P3).
- 0018 → 0019: staging 22/22, rollback included (P5).
- Both ran on a copy of production taken at **11:07 UTC**. Production was active again from 14:14 to 14:19 and from 16:49 to 17:01 UTC.
- The single-step 0017 → 0019 path has now been rehearsed on a copy of current production (step 0): 22/22, all 43 tables' counts unchanged, and the rollback limits confirmed.

**Code compatibility:**
- The code that needs 0018/0019 (P1–P5) is **not committed**.
- No deployment pipeline exists: the CI workflows only run tests and evals.
- After the migration, the current working tree matches the schema, and the start-up guard refuses to run current code against an older schema.
- Older code still works against the new schema: every change is additive and every new NOT NULL column has a default.

**Further validation (2026-10-02):** the recovery point was re-verified as identical to production (all 43 tables). A Postgres functional rehearsal passed 14/14, including the legal-hold and edited-policy backfills, and the P1–P5 API suites passed on Postgres (101 passed in 1018.74s (0:16:58)). Staging's schema is identical to a freshly migrated production copy. See the tracker section "Neon database validation completion".

## 2. Recovery branch: is it sufficient?

**Yes, for a full database restore,** with these conditions:
- **Coverage:** it is a complete copy-on-write copy of `production` (schema, data, roles) at **2026-10-02 17:01:16 UTC**, LSN 0/2BFA4D0. It has no expiry and no compute.
- **Currency:** production has been suspended since 17:01:31, with no operations since, so the branch currently equals production. **Re-check immediately before migrating** (step 1). If production has been active since then, create a fresh branch first.
- **Point-in-time restore is limited:** the project keeps only **6 hours** of history (`history_retention_seconds` 21600). Point-in-time restore of `production` is therefore short-lived. The recovery branch is the durable restore point; keep it until sign-off.
- **What it does not cover:**
  - uploaded evidence files, which live on the application's file storage, not in Neon (the migrations don't touch them);
  - environment variables and secrets;
  - data written to production **after** the migration. A restore discards it; use `preserve_under_name` to keep it for reconciliation.

## 3. Pre-conditions (all required)

1. Explicit approval of this production change in chat, recorded in the tracker.
2. Governance acceptance in §6.
3. Write freeze: no app server, script or person using the primary for the duration (about 15 minutes). The shell has **no** `DATABASE_URL` or `MIGRATION_*` overrides, so `backend/.env` is the only source.
4. Optional but recommended: **step 0 passed**.

## 4. Steps (from `backend/`)

**Step 0 is done (2026-10-02): passed 17/17, including the validator's 22/22, on the temporary branch `rehearsal-0019`, which was then deleted. See `backend/eval_results/rehearsal_0019_report.json` and the P5 section of `docs/REMAINING_REQUIREMENTS.md`.** As originally planned, step 0 is recommended. It is a dress rehearsal on a fresh branch of *current* production, so the combined 0017 → 0019 path runs on today's data:
- Create branch `rehearsal-0019` from `production`, with a compute endpoint, via the Neon API.
- Run `scripts/validate_staging_migration.py --expect-revision 0017_governance_records --rollback-check`, with that branch's connection string supplied to the process through the environment and never printed or stored.
- Delete `rehearsal-0019` afterwards.

Production:

```bash
# 1. Freeze check (Neon API, read-only): no start_compute on ep-falling-water-b35syv2c since 17:01:31Z.
#    If there was one, create a fresh recovery branch first (e.g. production-pre-0019-b).

# 2. Read-only status. Expect current ['0017_governance_records'], head ['0019_retention_lifecycle'].
python -m app.migrations status

# 3. Read-only pre-snapshot (revision and every table's row count); stops if not at 0017.
python scripts/verify_production_migration.py pre --confirm-host ep-falling-water-b35syv2c-pooler.c-4.ap-southeast-1.aws.neon.tech --expect-revision 0017_governance_records

# 4. THE MIGRATION (0018 + 0019 in one transaction).
python -m app.migrations upgrade --confirm-host ep-falling-water-b35syv2c-pooler.c-4.ap-southeast-1.aws.neon.tech --allow-protected-host

# 5. Read-only post-checks: at head; every pre-existing table's count unchanged; only the 4 expected
#    tables added; 0018 rows unlabelled; v1 backfilled; indexes, FKs, orphans; eligibility probe.
python scripts/verify_production_migration.py post --confirm-host ep-falling-water-b35syv2c-pooler.c-4.ap-southeast-1.aws.neon.tech

# 6. Application smoke test: start-up must be a no-op on the schema, and /health healthy.
python -c "from fastapi.testclient import TestClient; from app.main import app, prepare_database; prepare_database(); print(TestClient(app).get('/health').json())"
```

Evidence: `backend/eval_results/production_migration_pre.json`, `production_migration_post.json`, and the console output, recorded in `docs/REMAINING_REQUIREMENTS.md`.

## 5. Stop conditions and recovery

**Stop and change nothing if:**
- the status is not 0017;
- the pre-snapshot refuses;
- production was active after the recovery branch was taken and no fresh branch exists;
- any refusal from the migration guard.

**On failure:**

| Situation | Action |
|---|---|
| Step 4 fails | The single transaction rolls back automatically. Re-run step 2: it must still show 0017. Investigate before retrying. |
| A step 5 or 6 check fails, no new data written yet | Either `alembic downgrade 0017_governance_records` with `MIGRATION_AUTHORIZED_HOST=<host>` and `MIGRATION_ALLOW_PROTECTED_HOST=true` for that command only (lossless at this point), or restore from the branch (next row). |
| A problem found after the system has been used | **Don't downgrade** (0018 would drop designations; 0019 refuses once history exists). Either fix forward, or restore `production` from `production-pre-0019`: Neon console **Branches → production → Restore**, source `production-pre-0019`, preserving the current state under a new name. API: `POST /projects/summer-flower-19034040/branches/br-winter-art-b3xohxo2/restore` with `source_branch_id = br-snowy-math-b3pt5x8g` and `preserve_under_name`. The endpoint and connection string stay the same. Writes made after the migration are only in the preserved branch. |

## 6. Governance approvals needed before deployment

There is no approval record for any of these in the repository.

- **Change approval** for a production schema change (your change-management process).
- **P3 rules (R-GOV-01 to R-GOV-04), pending governance approval:** deploying 0018 puts the provisional SoD, override, challenge-review and readiness rules into force on real cases. Either governance approves them, or the business explicitly accepts running production on provisional rules.
- **Retention (Q-3; D-1 to D-4), pending governance approval:** the 2,555-day period, independent approval, independent release, the 365–36,500-day bounds and Auditor report access. The same choice applies: approve, or explicitly accept as provisional.
- **Who holds which governance designation.** After 0018 nobody holds one. Until an Admin assigns them, SoD exceptions, material overrides and independent challenge reviews can't be approved, so **committee submissions are blocked**, and retention changes can't be approved. Agree the named holders before go-live.
- **Releasing the code:** the P1–P5 code is uncommitted. Deploying it is a separate step that needs review and a commit.
