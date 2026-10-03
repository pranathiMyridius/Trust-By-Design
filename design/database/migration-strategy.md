# Migration Strategy

## 1. Current state

- Alembic with a linear chain `0001_baseline` → `0008_versioned_governance`.
- `0001_baseline` runs `metadata.create_all()` with the *current* models, so fresh databases get everything from 0001 and 0002–0008 are no-ops; upgraded databases differ slightly (FKs added as plain ints in 0006, missing indexes from 0008, server defaults only on migrated columns).
- Migrations run **at import time** of `app/main.py` (`run_migrations()`), guarded by `pg_advisory_xact_lock(7482019)`.
- Revision ids are ≤ 32 chars (`alembic_version.version_num` is `varchar(32)`).

## 2. Target rules

1. **Alembic remains the only schema tool.** No `create_all` outside `0001` and the test fixture.
2. **Migrations are a release step**, not an import side effect: `alembic upgrade head` executed by CI/CD (or `python -m app.migrate`) using `DATABASE_URL_DIRECT` (Neon direct endpoint; the pooler cannot hold the advisory lock / DDL session). `RUN_MIGRATIONS_ON_STARTUP=true` is allowed only when `APP_ENV=dev`.
3. **Expand → migrate → contract.** Additive changes ship first (nullable columns, new tables); code writes both; backfill; then constraints/NOT NULL; destructive changes last and never in the same release.
4. **Every migration is idempotent and reversible where practical** (existing pattern: inspector checks before add). Downgrades are provided for new tables/columns; data backfills are forward-only and documented.
5. **Test on a Neon branch first.** CI creates `ci/<run-id>` from `staging`, runs `alembic upgrade head`, runs the API test suite against it, deletes the branch.
6. **No autogenerate without review.** `alembic revision --autogenerate` output is edited by hand; `compare_type` for `VECTOR` is already customised in `env.py`.

## 3. Planned migrations for Stage 3

| Rev | Name | Type | Contents |
|---|---|---|---|
| 0009 | `analysis_runs_prompts` | Expand | Create `prompt_versions`, `analysis_runs`, `idempotency_keys`; add `analysis_run_id` to `risk_factors`, `ai_usage_logs`, `processing_jobs`, `inherent_risk_calculations`, `assessment_drafts`, `ai_evaluation_records`, `assessment_overrides`, `audit_events`; add `assessments.latest_analysis_run_id` |
| 0010 | `actor_ids` | Expand | Add `*_by_id` FK columns (`submitted_by_id`, `rated_by_id`, `excluded_by_id`, `added_by_id`, `reviewed_by_id`, `resolved_by_id`, `accepted_by_id`, `assessed_by_id`, `confirmed_by_id`); `audit_events.request_id`, `entity_type`, `entity_id`, `before_json`, `after_json` |
| 0011 | `scoring_confidence` | Expand | `inherent_risk_calculations.scoring_engine_version` (server_default `'1.0.0'`), `rating_coverage`, `evidence_coverage`, `confidence_level`, `inputs_sha256`; `residual_risk_calculations.scoring_engine_version`; `assessments.confidence_level`, `lock_version` |
| 0012 | `typologies` | Expand | `risk_factors.crime_typologies` (Text, nullable) |
| 0013 | `constraints_indexes` | Contract-safe | Partial unique index one active analysis job; `(assessment_id, is_current)` on `risk_factors`; `(assessment_id, created_at)` on `audit_events`; `(workflow_status, updated_at)` on `assessments`; missing FKs from 0006 (`manager_delegation_id`, `committee_delegation_id`, `*_on_behalf_of_id`, `committee_votes.delegate_id/delegation_id`) using `NOT VALID` then `VALIDATE CONSTRAINT` |
| 0014 | `backfill_actor_ids` | Data (forward-only) | Best-effort map existing name strings → `users.id` by email/full_name; unresolved remain NULL and are listed in a report table/CSV |

Production-phase migrations (later): JSONB conversion of queried columns (with `USING col::jsonb`), `timestamptz` conversion (`USING col AT TIME ZONE 'UTC'`), DB-level append-only triggers and role grants, `CHECK` constraints for enums, `factor_evidence` / `approval_decisions` tables, audit hash chain columns.

## 4. Data backfill policy

- Historical rows keep their original string actors; new `*_by_id` columns are nullable for history, required (application-enforced) for new writes.
- `scoring_engine_version` defaults to `'1.0.0'` for historical calculations (the formula has not changed since 0007).
- No historical AI run records exist; `analysis_run_id` is NULL for pre-0009 factors, and the decision record for such assessments states `analysis_run: "pre-versioning"`.

## 5. Schema versioning & compatibility

- The deployed schema revision is exposed at `GET /api/system/integrity` (existing ADMIN endpoint) and checked by `/health/ready`.
- The frozen decision record carries `record_format`. Stage 3 bumps it to `"1.1"` (adds `analysis_runs[]`, `prompt_versions[]`, `scoring_engine_version`, `confidence`). `verify_record` must accept both `1.0` and `1.1`.

## 6. Rollback

- Application rollback is always possible while only *expand* migrations are deployed (new columns are nullable and unused by the old code).
- Database rollback uses Neon point-in-time restore to a new branch, then repoint the app — never a destructive `downgrade` on production.
