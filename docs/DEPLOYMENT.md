# Deployment

The workbench deploys as **one web service + one Postgres database**.
The root [`Dockerfile`](../Dockerfile) builds the React frontend and has
FastAPI serve it (`FRONTEND_DIST_DIR`), so the browser calls `/api` on
the same URL — no CORS configuration is needed.

## Render (free, from GitHub)

1. Push this repository to GitHub.
2. Sign in at https://render.com with GitHub.
3. **New > Blueprint**, pick the repository. [`render.yaml`](../render.yaml)
   creates the web service and the database.
4. Fill in the values it asks for:
   - `ADMIN_BOOTSTRAP_EMAIL` / `ADMIN_BOOTSTRAP_PASSWORD` — the first
     admin account (only used while the database has no users).
   - `FILE_ENCRYPTION_KEY` —
     `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
   - `OPENAI_API_KEY` — for AI analysis, with `LLM_PROVIDER=openai` and
     `OPENAI_MODEL=gpt-4.1-mini` (set in `render.yaml`). The app falls back
     to rules-only analysis without it. `OPENROUTER_API_KEY` is used only
     with `LLM_PROVIDER=openrouter`.
5. Wait for the first deploy, open `https://<service>.onrender.com`, sign
   in as the admin and create users on the **Users** page.

Every push to `main` redeploys.

### Free plan limits

- The service sleeps after ~15 minutes idle; the first visit after that
  takes ~1 minute to wake.
- No persistent disk: **uploaded evidence files are lost on each
  redeploy/restart** (database data is kept). For real use, choose a paid
  plan and add a disk mounted at `/app/backend/uploaded_files`.
- The free Postgres database expires after 30 days.

## Any other Docker host

```bash
docker build -t risk-workbench .
docker run -p 8000:8000 --env-file backend/.env risk-workbench
```

Set the same variables as in [`backend/.env.example`](../backend/.env.example).
Use a Postgres `DATABASE_URL` (with `?sslmode=require` for a managed
database) and mount persistent storage at `/app/backend/uploaded_files`.

## Database migrations (guarded)

Importing the application never touches the database. The schema is
prepared on server start-up (and explicitly by tests and scripts) by
`prepare_database()` → `app/migrations.py`:

| Database | What happens on start-up |
|---|---|
| Local (SQLite, Postgres on `localhost`) | Migrated to head automatically |
| Remote, already at head | Starts normally; nothing is migrated |
| Remote, behind the code, not authorized | **Refuses to start**, naming the revision gap and how to authorize |
| Remote, `MIGRATION_AUTHORIZED_HOST=<its host>` | Migrated to head |
| Remote host listed in `PROTECTED_DATABASE_HOSTS` | Also needs `MIGRATION_ALLOW_PROTECTED_HOST=true` |

`alembic/env.py` applies the same check, so a bare `alembic upgrade` can't
bypass it.

Run migrations deliberately from `backend/`:

```bash
python -m app.migrations status
python -m app.migrations upgrade --confirm-host <host>
```

`status` is read-only. Add `--allow-protected-host` only for a host in
`PROTECTED_DATABASE_HOSTS` and only with approval. Validate every new
migration on an **isolated staging branch** (for Neon, a branch of the
primary) before the primary. Put the primary's host in
`PROTECTED_DATABASE_HOSTS`.

### Staging validation before production

1. Create an isolated staging branch of the primary (Neon: **Branches → New branch** from the primary). Branch from the primary's *current* state to test exactly what production will run. If the primary has already been migrated, branch from a timestamp before that to test the original upgrade path.
2. Put its connection string in `STAGING_DATABASE_URL` (in `backend/.env` or the environment). Never use `DATABASE_URL` for this.
3. Run from `backend/`:

   ```bash
   python scripts/validate_staging_migration.py --rollback-check
   ```

   The script refuses the primary's host and any host in `PROTECTED_DATABASE_HOSTS`. It records the revision, row counts and a vote fingerprint, migrates with authorization for the staging host only, verifies integrity, checks app start-up, and round-trips the rollback. The report is written to `backend/eval_results/staging_migration_report.json`. A `--local-rehearsal` run writes `staging_migration_rehearsal_report.json` instead, so it never overwrites the evidence of a real staging run.
4. A production migration is a separate, approved change. Confirm PITR/backup readiness, then run `python -m app.migrations upgrade --confirm-host <primary host> --allow-protected-host` with approval.

### Release notes: migration 0018 and the P3 governance policy

- **Migration `0018_sod_governance`** (additive):
  - designations on users and `rated_by_id` on factors
  - override materiality and approval columns
  - challenge review stage and escalation columns
  - new tables `sod_exceptions` and `sod_exception_events`
- **Validation:** 15/15 on an isolated Neon staging branch, including rollback and re-upgrade. The primary has **not** been migrated to 0018; that needs a separate, approved production change.
- **Rollback:** `alembic downgrade 0017_governance_records` is lossless while there are no SoD exceptions and no independent challenge reviews. It refuses otherwise rather than discard governance records. Take a Neon point-in-time-restore checkpoint before any production migration.
- **Governance policy:** the rules are **pending governance approval**. They are provisional defaults in `backend/app/governance/policy.py`, overridable key by key from the JSON file named in `GOVERNANCE_POLICY_FILE`. `GET /api/governance/policy` shows the effective rules and their status.
- **After deploying P3:**
  - **Designations:** an Admin assigns governance designations on the Users page (Head of FCRM, FCRM Governance Owner, Committee Chair, Senior Analyst / QA / Challenge Reviewer). Until then, nobody can approve SoD exceptions or material overrides, and no independent challenge reviews can be completed. Committee submission is then blocked by design.
  - **Retired switch:** `COMMITTEE_ADMIN_AUTHORITY` no longer grants Admins committee authority; remove it from environments.

### Release notes: migration 0019 and the P5 retention policy

- **Migration `0019_retention_lifecycle`** (additive, revises `0018_sod_governance`):
  - new append-only tables `retention_policy_versions` and `legal_hold_events`
  - `assessment_retention.retention_policy_version_id` and `legal_hold_set_by_id` (nullable)
  - partial unique indexes: at most one ACTIVE and one PROPOSED version per record type
  - backfill: ASSESSMENT **v1 ACTIVE** from the active `retention_policies` row (or the provisional 2,555-day default), reason "Migrated existing default; not compliance-approved"; one `SET` event per assessment currently on hold ("Backfilled from pre-P5 state: …"). `retention_policies` is kept and no longer edited.
- **Validation plan:** `python scripts/validate_staging_migration.py --status-only --expect-revision 0018_sod_governance`, then the same with `--rollback-check` instead of `--status-only`. `--expect-revision` stops before any change if the branch is not at 0018. The full plan, stop conditions and recovery are in `docs/REMAINING_REQUIREMENTS.md` (P5).
- **Staging result (2026-10-02):** 22/22 on the isolated Neon child branch (`ep-small-leaf-…`), 0018 → 0019 with rollback and re-upgrade, row counts identical in every pre-existing table. Recovery point: a read-only snapshot in `backend/backups/staging-pre-0019-20261002T165357Z/` and PITR target 2026-10-02 16:53:57 UTC. The primary is **not** migrated.
- **Production runbook:** `docs/PRODUCTION_MIGRATION_0018_0019.md` (review, commands, read-only pre/post checks via `scripts/verify_production_migration.py`, recovery, governance pre-conditions). Not executed.
- **Production recovery branch:** `production-pre-0019` (`br-snowy-math-b3pt5x8g`), branched from `production` at 2026-10-02T17:01:16Z with no compute. To recover after a production migration, restore `production` from this branch (or to that timestamp) in the Neon console.
- **Local rehearsal:** a local SQLite rehearsal of `validate_staging_migration.py --local-rehearsal --rollback-check` passed 17/17 (0018 → 0019 with an edited policy and a held assessment, rollback, re-upgrade). It is not staging evidence; the staging result is above.
- **Rollback:** `alembic downgrade 0018_sod_governance` is lossless while only the backfill exists. It **refuses** once any version has been proposed or decided, any hold event recorded, or any soft delete has recorded its policy version, rather than lose retention history. Take a point-in-time-restore checkpoint before any production migration.
- **Configuration** (`GOVERNANCE_POLICY_FILE`, key `retention`; replaces the whole section): `record_types`, `proposers`, `approvers`, `require_independent_approval` (D-1), `legal_hold_setters`, `legal_hold_releasers`, `release_requires_different_user` (D-2), `min_retention_days` / `max_retention_days` (D-3, 365 / 36,500), `min_reason_length` (20), `soft_deleters`, `eligibility_report_readers` (D-4). All values are **pending governance approval**.
- **After deploying P5:** approvals need a Manager holding the FCRM Governance Owner or Compliance Manager designation (assigned by an Admin on the Users page). An Admin can propose but never approve. Until such a designation exists, a proposal stays pending and the current period stays in force. Clients that used `PATCH /api/retention-policy` must now send `change_reason`, and get a pending proposal, not an immediate change.
- **No purge:** nothing in P5 deletes rows or files, and there is no scheduled disposal. Eligibility only identifies records for a controlled review.

## Encryption requirements (P7)

- **Set `APP_ENV=production` for any real deployment.** Start-up is then refused unless all of these are configured:
  - `JWT_SECRET` of at least 32 characters
  - a valid `FILE_ENCRYPTION_KEY`
  - `FORCE_HTTPS=true`, behind a TLS-terminating proxy
  - a `DATABASE_URL` that requires TLS

  `GET /api/system/security-posture` (Admin) shows each item and `production_ready`.
- **Database TLS:** a remote Postgres URL needs `?sslmode=require` (or `verify-full`). The app won't start otherwise. Neon connection strings include `sslmode=require`.
- **Evidence:** `python scripts/verify_encryption.py --target staging` (or `--target database`). It is read-only and records the live TLS protocol and cipher, file and backup encryption counts and key validity in `eval_results/encryption_verification_<target>.json`. It never prints secrets.
- **Existing plaintext uploads:**
  1. Run `python scripts/encrypt_existing_files.py` (dry run) to count them.
  2. Back up `FILE_ENCRYPTION_KEY` separately from the data, then run with `--apply`. Without the key the files can't be decrypted.
- **Backups** are encrypted with `FILE_ENCRYPTION_KEY` (database copy and uploads; the manifest is metadata), and **key rotation** uses `FILE_ENCRYPTION_PREVIOUS_KEYS` plus `scripts/rotate_file_encryption_key.py`. See `docs/BACKUP_AND_RECOVERY.md`.
- **Not covered by the application:** database storage encryption (provider-managed). Copies of backups kept elsewhere stay encrypted, but the key must be stored separately from them.

## Hosting the frontend separately

Build with the API's public URL and allow the frontend's origin on the API:

```bash
VITE_API_BASE_URL=https://api.example.com npm run build   # in frontend/
CORS_ALLOWED_ORIGINS=https://app.example.com              # backend env
```
