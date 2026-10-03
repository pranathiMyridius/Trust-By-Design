# Stage 19: Non-Functional Requirements

How each requirement is met, and where the code or check for it lives. The automated checks are in `backend/test_stage19_nfr.py`. Run them with `python test_stage19_nfr.py` from `backend/`.

## Security

| Requirement | Implementation |
|---|---|
| Sensitive data protected **in transit** | `SecurityHeadersMiddleware` (`app/middleware.py`) sends HSTS when running over HTTPS, `nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, and `Cache-Control: no-store` on every API response. `FORCE_HTTPS=true` redirects plain HTTP to HTTPS. For Postgres, add `?sslmode=require` to `DATABASE_URL`. TLS itself is terminated by a reverse proxy or by uvicorn `--ssl-*`. **P7:** a remote Postgres connection must require TLS (`sslmode` require / verify-ca / verify-full). `app/database.py` refuses to start otherwise; `DATABASE_ALLOW_INSECURE_TRANSPORT=true` is a deliberate non-production override and is refused with `APP_ENV=production`. Verified on the staging branch with `scripts/verify_encryption.py`: TLSv1.3, `TLS_AES_256_GCM_SHA384`, 256-bit. |
| Sensitive data protected **at rest** | Uploaded evidence files are encrypted with Fernet (AES-128-CBC with HMAC) when `FILE_ENCRYPTION_KEY` is set (`app/file_processing/storage.py`). Files are decrypted transparently when downloaded. Files stored before encryption was switched on can still be read. Database-level encryption belongs to the platform, for example encrypted volumes or managed Postgres. **P7:** with `APP_ENV=production` the app refuses to start without a valid `FILE_ENCRYPTION_KEY`, a `JWT_SECRET` of at least 32 characters, `FORCE_HTTPS=true` and database TLS (`app/security_settings.py`). Uploads written before the key was set are encrypted by `scripts/encrypt_existing_files.py` (dry run by default; each file is verified before it replaces the original). **Database encryption at rest is provider-managed (Neon) and can't be verified from the application.** It is reported as such by `GET /api/system/security-posture`; confirm it from the provider's current security and compliance documentation. Local backups (`BACKUP_DIR`) are **not** encrypted (open item). |
| Segregation of duties (P3, rules pending governance approval) | SoD exceptions need independent, tiered approval and a conflict-of-interest declaration before they authorize anything; expiry and revocation are checked at every use. Admin and committee entitlements are mutually exclusive except through an approved dual-role exception, during which same-case administration is refused by the global access gate. Override proposal, review and approval, and challenge review and sign-off, are performed by different people. Committee readiness is calculated by the server and enforced at submission and at the final decision. Every step, use and refusal is audited (`app/governance/`). |
| Record retention and legal holds (P5, rules pending governance approval) | The retention period is versioned per record type (`retention_policy_versions`, append-only, never edited in place). A change is proposed with a justification and takes effect only when a different user holding the FCRM Governance Owner or Compliance Manager designation approves it; an Admin can't approve. Legal holds keep an append-only history (`legal_hold_events`), need a reason to place and release, and are released by a different user from the one who set them. One server-side rule (`app/governance/retention.py`) decides eligibility for the per-assessment view, the read-only eligibility report and soft delete; missing or invalid data is never eligible and a hold always overrides. Nothing is physically deleted; soft delete records the policy version. Dedicated audit actions (`RETENTION_POLICY_*`, `LEGAL_HOLD_*`, `ASSESSMENT_SOFT_DELETED`, `RETENTION_REPORT_VIEWED`); refusals are `ACCESS_DENIED`. |
| Least privilege | Every `/api` router, except login, requires a signed-in user (`app/auth/access.py`, wired in `app/main.py`). Before this change about 40 endpoints were open. Any `{assessment_id}` or `{document_id}` in a URL is checked against the caller's visibility rules for that assessment. Role checks on individual endpoints still apply on top. |
| Administrative actions logged | `AdminAuditMiddleware` writes an `ADMIN_ACTION` audit event for every state-changing request made by an admin: method, route and outcome, never the request body. User create and update events (`USER_CREATED` and `USER_UPDATED`) also record the fields that changed. Backups are logged as `BACKUP_*`. |
| No secrets in code | The hard-coded JWT secret and the default admin password have been removed. Secrets come from the environment (`backend/.env.example`). If `JWT_SECRET` is unset, the app uses a random secret that lasts only as long as the process. The bootstrap admin password comes from `ADMIN_BOOTSTRAP_PASSWORD`; if that is unset, a random one-time password is printed to the server console. CORS origins are configured through `CORS_ALLOWED_ORIGINS`. |
| Data masking | `app/services/data_masking.py` masks card numbers (Luhn-checked), IBANs, e-mail addresses, phone numbers and national IDs. Two places use it. (1) Every AI request is masked before it leaves the system (`AI_MASK_SENSITIVE_DATA`, on by default). (2) The text of CONFIDENTIAL and RESTRICTED documents, and evidence quotes taken from them, are masked for anyone other than the assessment owner, FCRM analysts and admins, and the original file of either is only served to those people. Every view, download and refused attempt is audit-logged (`DOCUMENT_VIEWED`, `DOCUMENT_DOWNLOADED`, `ACCESS_DENIED`). |

## Performance

| Requirement | Implementation |
|---|---|
| Pages load within an agreed target | Every API call is timed (`RequestTimingMiddleware`) and compared with `PAGE_LOAD_TARGET_MS` (default 2000 ms). Responses include `Server-Timing` and `X-Response-Time-Ms` headers. Slow requests are logged. The System Health page shows p50 and p95 per route and which routes miss their target. |
| Risk calculations complete promptly | Risk-factor rating, exclusion, overrides and manual scores are measured against `RISK_CALC_TARGET_MS` (default 3000 ms). |
| Progress status for large documents | Text extraction runs as a background `ProcessingJob` that reports progress (0–100) and the current step. The UI shows a progress bar through `ProcessingStatus.tsx`. |
| Long-running work doesn't block the UI | Uploads return as soon as the file is stored. Extraction and indexing then run on a worker pool (`PROCESSING_MAX_WORKERS`). `POST /api/assessments/{id}/analyze-async` does the same for risk analysis. Uploads are capped at `MAX_UPLOAD_MB`. |

## Reliability

| Requirement | Implementation |
|---|---|
| No data lost when processing fails | The file and the document or assessment row are committed before processing starts. In `create-with-document`, one unreadable file no longer rolls back the whole assessment. |
| Failed processing is retryable | `POST /api/processing-jobs/{id}/retry`, up to `PROCESSING_MAX_ATTEMPTS` attempts. Jobs cut off by a server restart are marked as failed and can be retried. |
| Errors shown clearly | Each job has a plain-language `error_message`, plus an `error_detail` for support that the UI hides behind a toggle. Failures are also audit-logged (`PROCESSING_FAILED`). |
| Partial results marked incomplete | A job that finishes with missing output, such as no readable text or failed indexing, is marked `PARTIAL` with `is_incomplete` set and its reasons listed. It is never shown as complete. |

## Availability and recovery

See [BACKUP_AND_RECOVERY.md](BACKUP_AND_RECOVERY.md). In short: backups are scheduled and can also be made on demand, every backup is checksummed and verified, the default RPO is 24 h and the default RTO is 4 h, restore is done from the CLI and takes a safety backup first, hard deletes are blocked, the audit log and control revisions are append-only, committee votes, challenge-review sign-offs and the override ledger can only be superseded or reviewed once (never rewritten), and SQLite runs in WAL mode. Importing the application never touches the database, and a remote database is migrated only when authorized for its exact host (see DEPLOYMENT.md).

## Accessibility and usability

The accessibility pass covers:

- **Keyboard:** a skip link, visible `:focus-visible` outlines, keyboard-operable rows and tabs, and dialogs that can be closed with Escape.
- **Understandable messages:** plain-language errors from `friendlyError`, with `role="alert"` or `role="status"` for live messages.
- **Risk levels not shown by colour alone:** `RiskLevelBadge` adds a text label and a shape to each level.
- **Form validation:** errors are shown next to each field, with an error summary and focus moved to the first invalid field.
- **Long assessments:** a sticky section navigator and a back-to-top button, and the reader's place is restored for each assessment.

## Scalability

| Requirement | Implementation |
|---|---|
| Multiple business units and legal entities | New `business_unit` field alongside `legal_entity`, both indexed. List filters are available (`legal_entity`, `business_unit`, `status`, `search`), and `GET /api/assessments/org-units` returns the options. Run `python migrate_stage19_nfr.py`. |
| Growing document and assessment volume | Server-side paging (`limit`/`offset`, with the total in `X-Total-Count`), indexes on frequently filtered columns, background processing, and upload size limits. |
| Configurable methodologies | Already met by Stage 5: risk methodologies are versioned records managed through `/api/risk-methodologies` without code changes. |

## Explainability

| Requirement | Implementation |
|---|---|
| Distinguish facts, assumptions, recommendations and decisions | `GET /api/assessments/{id}/explain/statements` (`app/services/explainability_statements.py`) labels every item with one of the four kinds. AI-identified risks that nobody has rated yet are labelled *assumptions*, and only named people make *decisions*. The UI shows this in `ExplainabilityPanel.tsx`. |
| Automated output is reviewable and traceable | Each item records whether a person or the system produced it, the author or model version, the time, whether it has been reviewed, and a reference to the database record it came from. |
| No automated recommendation looks like a final decision | Recommendations are always labelled `ADVISORY` and shown with a notice that they are not decisions (`app/services/advisory.py`). Wording that sounds like a verdict, such as "Approved." or "Decision: reject", is rewritten as "Suggested outcome: …". The AI prompt asks for suggestions only. |
