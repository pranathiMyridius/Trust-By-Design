# Production readiness checklist: database migration and release

**Prepared 2026-10-02. Status: NOT READY.** Production stays at `0017_governance_records`. The recovery branch `production-pre-0019` is preserved. No production migration or setting change may be made without a separate, explicit approval that names the exact operation and target.

Legend: ☐ open · ☑ done (evidence cited) · ⛔ blocker

## 0. Blocker found while preparing this checklist

⛔ **The code head is now `0021_reassessment_lifecycle` (P6, revising P4's `0020_evidence_traceability`), not 0019.** A release now means 0017 → 0021.
- A concurrent P4 session added `alembic/versions/0020_evidence_traceability.py` (revises 0019; chain 0017 → 0018 → 0019 → 0020; single head). It is still changing application files in the same working tree.
- `python -m app.migrations upgrade` always migrates to **head**, so the runbook's command would now also apply 0020. 0020 has not been validated on staging or on a production copy.
- The start-up guard refuses to run code against a schema that isn't at its head. So the database and the deployed code must be at the same revision; a P5-only release would need a P5-only code snapshot, and none exists (nothing is committed).
- Before any production approval, decide one of:
  - **(a) Release at 0020:** finish and verify P4, then repeat the staging run, the production-copy rehearsal and the Postgres suites for 0017 → 0020, and update the runbook and `verify_production_migration.py` (its "only these tables added" list knows only the 0018/0019 tables; 0021 adds columns only, P4's 0020 adds tables); or
  - **(b) Release at 0019:** isolate a P5-only code snapshot without P4 (needs a commit or branch, which you haven't authorized) and add a target-revision option to the migration CLI.

  Recommended: **(a)**.

## 1. Governance approvals and designation holders

There is no approval record in the repository for any item below.

| # | Item | Status |
|---|---|---|
| 1.1 | Change approval for a production schema change (your change-management process) | ☐ |
| 1.2 | P3 rules R-GOV-01 to R-GOV-04 (SoD exception tiers, Admin/committee exclusivity, override and challenge role matrix, readiness blocking): approved, **or** the business formally accepts running production on provisional rules | ☐ (provisional) |
| 1.3 | P5 retention: Q-3 periods (2,555 days provisional); D-1 independent approval; D-2 independent release; D-3 bounds 365–36,500 days and a 20-character justification; D-4 Auditor report access. Approved, or accepted as provisional | ☐ (provisional) |
| 1.4 | Whether a changed period applies to existing records (currently: the version in force applies to all) and who may see legal-hold reasons | ☐ |
| 1.5 | P4 (0020) governance items, as listed by the P4 session; P6 (0021): chain-level retention and the review intervals (existing defaults) | ☐ |
| 1.6 | **Named designation holders**, agreed before go-live | ☐ |
| 1.7 | **Staffing risk:** the production copy has 2 Admin, **1 Manager**, 2 FCRM Analyst, 2 Committee Member and 1 Business User accounts, and 0 designations. Every approver designation (Head of FCRM, FCRM Governance Owner, Compliance Manager) needs the **Manager** base role, so one person would be the only possible approver for retention changes, standard-tier SoD exceptions, material overrides and challenge sign-off. That is workable under the independence rules but a single point of failure. Decide whether more Manager accounts are needed | ☐ |

Designations each role can hold (provisional): Manager → Head of FCRM, FCRM Governance Owner, Compliance Manager, QA Reviewer, Challenge Reviewer. FCRM Analyst → Senior Analyst, QA Reviewer, Challenge Reviewer. Committee Member → Committee Chair.

**Day-one consequence if designations are not assigned:**
- No SoD exception, material override or independent challenge review can be approved, so **committee submissions are blocked**.
- Retention changes can't be approved; the provisional 2,555 days stays in force.
- Assign designations (Admin, Users page, with a reason; audited) immediately after deployment.

## 2. Application and database compatibility, and deployment order

| # | Item | Status |
|---|---|---|
| 2.1 | Every migration is additive; new NOT NULL columns have defaults; old code keeps working against the new schema | ☑ for 0018/0019 (review and rehearsal). ☐ for 0020 |
| 2.2 | The committed code (`2cbce9f`) has no Alembic and uses `create_all` at start-up, so it would tolerate the new schema (it only creates missing tables) | ☑ (inspected) |
| 2.3 | The current code refuses to start against a remote schema that isn't at its head, and never migrates a remote database without authorization | ☑ (guard tests; staging start-up) |
| 2.4 | The P1–P5 (+P4) code is **uncommitted**; there is no deployment pipeline (CI runs tests and evals only) | ☐ release step needs review and a commit (not authorized) |
| 2.5 | `backend/.env` points the local app at the **primary**. Until migration, the current code won't start against it (guard); after migration, older local code would still run | ☐ freeze local use during the window |

**Order:**
1. Freeze writes.
2. Read-only pre-checks.
3. Migrate the database to the head of the code being released.
4. Read-only post-checks.
5. Deploy or start that exact code.
6. Assign designations.
7. Smoke-test.

Never deploy the code first: the guard will refuse to start.

## 3. Recovery procedure and rollback alternatives

| # | Item | Status |
|---|---|---|
| 3.1 | `production-pre-0019` (`br-snowy-math-b3pt5x8g`) exists, is ready, has no expiry, was branched at 17:01:16Z (LSN 0/2BFA4D0), and equals production (all 43 tables) | ☑ `eval_results/neon_state_20261002.json` |
| 3.2 | Production must not have been written to since the branch was taken. On the day: check Neon operations and compare row counts. If it changed, create a fresh branch (e.g. `production-pre-release`) first | ☐ on the day |
| 3.3 | Point-in-time restore window is only **6 h** (`history_retention_seconds` 21600), so the branch is the durable restore point | ☑ noted. ☐ optional: lengthen (a setting change; needs approval) |
| 3.4 | Production branch protection is off | ☐ optional (needs approval) |

**Rollback alternatives, in order of preference:**

| Situation | Action | Data impact |
|---|---|---|
| Migration command fails | Nothing to do: one transaction rolls back automatically. Status must still show 0017 | none |
| Post-checks fail, before any use | `alembic downgrade 0017_governance_records` with `MIGRATION_AUTHORIZED_HOST=<primary host>` and `MIGRATION_ALLOW_PROTECTED_HOST=true` for that command only | none (proven on the production copy) |
| Problem after use | **Don't downgrade.** 0019 refuses once retention history exists. 0020's downgrade also refuses once its records exist (per the P4 migration). 0018 refuses once SoD exceptions or independent reviews exist, and otherwise **drops designations and override approvals silently**. Fix forward, or restore | — |
| Restore | Neon: restore `production` from `production-pre-0019` with `preserve_under_name` (console Branches → production → Restore, or API `POST …/branches/br-winter-art-b3xohxo2/restore`, source `br-snowy-math-b3pt5x8g`). The endpoint and connection string are unchanged | writes after the branch point are only in the preserved branch |

Not covered by any branch: uploaded evidence files (application storage), secrets and environment.

## 4. Verification steps

**Pre-migration (read-only), from `backend/`, with no `DATABASE_URL` or `MIGRATION_*` overrides in the shell:**
- ☐ Approval recorded, naming the exact command, target host and target revision.
- ☐ Write freeze in place; Neon shows no production activity since the latest recovery branch.
- ☐ `python -m app.migrations status`: current `0017_governance_records`, head = the agreed release revision.
- ☐ `python scripts/verify_production_migration.py pre --confirm-host <primary host> --expect-revision 0017_governance_records` (stops with exit 2 on any other revision).

**Migration (only with approval):**
- ☐ `python -m app.migrations upgrade --confirm-host <primary host> --allow-protected-host`

**Post-migration (read-only):**
- ☐ `python scripts/verify_production_migration.py post --confirm-host <primary host>`: at head; every pre-existing table's row count unchanged; only the expected tables added (**update this list for 0020 first**); 0018 rows unlabelled; v1 backfilled, provisional and not approved; partial unique indexes, FKs, no orphans; eligibility probe with 0 violations.
- ☐ App start-up is a no-op on the schema, and `/health` is healthy.
- ☐ Admin assigns designations; a smoke test of `GET /api/retention-policy` (v1, `policy_status` provisional) and the Retention page.
- ☐ Record the evidence (`eval_results/production_migration_pre.json` / `_post.json`) in the tracker. Keep `production-pre-0019` until sign-off.

## 5. Test results and known issues

**Last verified results.** These were taken before the P4 session's current edits, so they need a fresh baseline afterwards.

| Suite | Result |
|---|---|
| Backend pytest (SQLite), merged P4+P6+P7 tree | 476 passed, 0 failed (2026-10-03) |
| P1–P5 API suites on Postgres (temporary Neon branch) | 101 passed, 0 failed |
| Staging 0018 → 0019 | 22/22, rollback included |
| Production-copy rehearsal 0017 → 0019 | 17/17 steps, validator 22/22, rollback limits confirmed |
| Functional rehearsal on Postgres (legacy hold, edited policy, P2 sign-off) | 13/13 |
| Validator tests | 6/6 |
| `tsc -b` | clean |
| Playwright (merged tree, 2026-10-03) | **28 passed, 0 failed** (P4 corrected the 3 baseline tests' setup) |
| ESLint | pre-existing findings only (`AssessmentWorkflow.tsx` 10) |

**Known issues:**
- ⛔ §0: head is 0020, not validated.
- ☐ A fresh full regression is needed after P4 (pytest, legacy scripts, `tsc`, Playwright).
- ☐ Schema drift: 19 FK/index constraints are declared in the models but deliberately not created by the migrations (staging is identical to a migrated production copy). This is a possible hardening migration, not required for this release.
- ☐ The P3 staging JSON was overwritten earlier and is lost; its textual record remains in the tracker. Fresh staging evidence exists for 0019.
- ☐ `NEON_API_KEY` sits in `backend/.env`; moving it to a user-level variable is recommended.
- ☐ Not tested: load, the deployed container image, uploaded-file storage in a hosted environment.
- ☐ Out of scope by design: physical purge, scheduled disposal, backup expiry for disposed records.

## 6. Approval needed (separately, explicitly)

1. The release revision decision (§0 a or b).
2. The governance items in §1.
3. The exact production migration operation and target, after a fresh re-validation if 0020 is included.
4. Optional Neon setting changes (branch protection, history retention).
