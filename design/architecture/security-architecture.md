# Security Architecture

*(Added to the prescribed package structure.)*

## 1. Authentication

| | MVP (existing, hardened) | Production |
|---|---|---|
| Mechanism | Email + password → JWT HS256 (`python-jose`), `sub = user.id`, 480 min expiry | OIDC SSO (e.g. Microsoft Entra ID); group → role mapping |
| Password storage | bcrypt (passlib) | IdP |
| Token transport | `Authorization: Bearer`; stored in `localStorage` | httpOnly, Secure, SameSite=Strict session cookie + CSRF token |
| Per-request check | User loaded from DB each request (role changes/deactivation immediate) | same |
| Hardening (Stage 3) | `JWT_SECRET` mandatory outside dev; login throttling (10/min/IP) and lockout after 10 failures/15 min; audit `LOGIN_SUCCEEDED/FAILED`; shorter expiry (60 min) + silent re-login prompt; global 401 → logout in UI | MFA via IdP; refresh tokens |

## 2. Authorisation

Three layers, all server-side:

1. **Authenticated** — global dependency on every router except login.
2. **Visibility** — `_scope_assessments_for_user`: ADMIN & FCRM_ANALYST all; MANAGER own/managed/delegated; COMMITTEE_MEMBER committee-visible statuses + own; BUSINESS_USER own. Extended to body/query assessment ids.
3. **Permission** — single matrix `app/auth/permissions.py` (`api/api-design.md` §2) + separation-of-duties checks + `decision_lock` for final statuses.

Frontend role checks are **UX only** (hide/disable), never security.

## 3. Secrets

| Secret | Where | Never |
|---|---|---|
| `DATABASE_URL`, `DATABASE_URL_DIRECT` (Neon credentials) | PaaS secret store / Key Vault | In repo, logs, frontend, error messages |
| `OPENROUTER_API_KEY` | same | In frontend bundle (all AI calls are server-side) |
| `JWT_SECRET`, `FILE_ENCRYPTION_KEY` | same | Random per-process fallback outside dev |
| `LANGFUSE_*` | same | |

`backend/.env` exists in the working tree — confirm it is git-ignored and rotate any key that was ever committed (Q-07). Separate Neon roles: `app_rw` (runtime, DML only) and `migrator` (DDL). Rotate Neon passwords and API keys per environment.

## 4. Sensitive data handling

| Data | Control |
|---|---|
| Intake text / documents (may contain customer PII, commercial secrets) | TLS in transit (`sslmode=require` to Neon; HTTPS to clients); Fernet encryption of uploaded files at rest; Neon storage encryption at rest |
| Data sent to LLM | `mask_ai_payload` (cards, IBAN, email, phone, national id) — on by default; only masked text stored in `analysis_runs.input_snapshot` |
| CONFIDENTIAL / RESTRICTED documents | Text masked for users other than owner/analyst/admin; RESTRICTED original file served only to those |
| Traces | `LANGFUSE_CAPTURE_CONTENT=false` default; span metadata limited to status codes and counts |
| Logs | No request bodies; no prompts; `request_id` correlation |
| Error responses | Generic messages; no stack traces or raw exception text |

## 5. API security

- CORS allow-list from `CORS_ALLOWED_ORIGINS` (existing).
- Security headers (existing) + **Content-Security-Policy** (`default-src 'self'; connect-src 'self' <api>; frame-ancestors 'none'`).
- Upload limits (`MAX_UPLOAD_MB`), extension + MIME allow-list, zip-bomb guard on expansion.
- Pydantic validation on every body; `extra="forbid"` on write schemas after the deprecated actor fields are removed.
- Rate limiting: login (MVP, in-app); gateway-level for all routes (production).

## 6. Audit trail

| Store | What | Integrity |
|---|---|---|
| `audit_events` | Every business action: action, actor_id, previous/new status, details, entity, before/after, request_id, run id | Append-only (ORM hook, MVP); DB trigger + role grants + per-assessment SHA-256 hash chain (production) |
| `workflow_transitions` | Every status change with reason and actor (incl. "X (delegate for Y)") | Append-only |
| `ai_usage_logs`, `analysis_runs` | Every AI call and run | Append-only / single terminal update |
| `decision_records` | Frozen decision snapshot | SHA-256 checksum, `verify_record` |
| Admin middleware | Every state-changing admin request (method, route, outcome) | Append-only |

Reading all audit events is ADMIN-only (fixes D-01); per-assessment audit follows visibility.
