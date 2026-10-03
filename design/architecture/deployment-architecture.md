# Deployment Architecture

## 1. Current state

- Local development only: `uvicorn` on `127.0.0.1:8000`, Vite dev server on `5173/5176`.
- `docker-compose.yml` starts **Postgres only** (`pgvector/pgvector:pg16`) for local use; the working database is **Neon** (per `docs/AI_SDLC_EVIDENCE.md` §5).
- No application Dockerfile; CI workflows are described in `docs/TESTING.md` §6.
- Files are stored on local disk (`backend/uploaded_files/`); background jobs run in an in-process thread pool.

## 2. Target — Hackathon MVP (single region, minimal moving parts)

| Component | Choice | Notes |
|---|---|---|
| Frontend | `vite build` → static files on any static host (e.g. Azure Static Web Apps, Netlify, Vercel, or served by the backend container) | `VITE_API_BASE_URL` at build time |
| Backend | **One** container (new `backend/Dockerfile`, `python:3.12-slim`, `uvicorn app.main:app --workers 1`) on a PaaS (e.g. Azure App Service / Render / Railway / Fly) | 1 instance: the in-process worker pool and escalation loop assume a single process |
| Worker | Same process (`PROCESSING_MAX_WORKERS=2`) | Acceptable for MVP |
| Database | **Neon** project, `main` branch = MVP environment; pooled connection string for app, direct for migrations | `sslmode=require` |
| Files | Persistent volume mounted at `/data/uploads`, Fernet-encrypted | Container disks are ephemeral — a volume is mandatory |
| LLM | OpenRouter, pinned paid model | Key in PaaS secret store |
| Tracing | Langfuse Cloud (optional) | `LANGFUSE_CAPTURE_CONTENT=false` |
| Secrets | PaaS environment secrets | Never in repo or frontend bundle |

Release procedure (MVP):

1. CI: `pytest` → frontend `tsc -b && vite build` → Playwright (fake LLM).
2. Build image; run `alembic upgrade head` as a **release step** with `DATABASE_URL_DIRECT` (not at import).
3. Deploy container with `RUN_MIGRATIONS_ON_STARTUP=false`.
4. Smoke test: `GET /health` (extend to check DB + model config) and one login.

## 3. Neon environment strategy

Neon branches are copy-on-write clones — cheap and ideal for environment separation.

| Environment | Neon branch | Created from | Data | Who |
|---|---|---|---|---|
| dev (per developer) | `dev/<name>` | `staging` (or `main` with anonymised data) | Synthetic | Developers |
| CI / preview | ephemeral `ci/<run-id>` | `staging` schema-only | Fixtures | CI; deleted after run |
| staging | `staging` | `main` | Anonymised copy | Testers, demo |
| production | `main` | — | Real | Users |

Each environment has its own `DATABASE_URL` / `DATABASE_URL_DIRECT`, its own Neon role with least privilege (`app_rw` for runtime; `migrator` owning the schema for Alembic), and its own `JWT_SECRET`, `FILE_ENCRYPTION_KEY` and OpenRouter key.

## 4. Production evolution

| Concern | MVP | Production |
|---|---|---|
| Backend instances | 1 | ≥2 behind a load balancer; `--workers` per CPU |
| Background jobs | In-process pool | Dedicated worker process consuming `processing_jobs` with `SELECT … FOR UPDATE SKIP LOCKED` (no Redis/Kafka needed) |
| Escalation / backup loops | `@app.on_event("startup")` loops | Scheduled jobs (platform cron) calling admin endpoints; Neon PITR replaces app-level DB backups |
| Files | Volume | Object storage (Azure Blob / S3) with server-side encryption + signed URLs |
| DB | Neon, autoscaling compute | Neon Scale plan: PITR ≥ 7–30 days, IP allow-list, protected `main` branch, read replica for reports |
| Auth | JWT HS256, local users | OIDC SSO (e.g. Entra ID), group→role mapping, httpOnly session cookie |
| Secrets | PaaS env | Key Vault / Secrets Manager with rotation |
| Edge | PaaS TLS | WAF + CDN; CSP; rate limiting at gateway |
| Observability | App logs + Langfuse | Centralised logs (OpenTelemetry), metrics, alerts on AI error rate, job backlog, SLA breaches |
| AI gateway | OpenRouter | Configurable gateway (OpenRouter or enterprise-hosted endpoint) behind the same `metered_post` interface |

Diagram: `diagrams/deployment.mmd`.

## 5. Health and readiness

`GET /health` (no auth) returns process liveness. Add `GET /health/ready` (no auth, no detail leakage) returning `200` only if: DB `SELECT 1` succeeds, Alembic head == DB revision, `OPENROUTER_MODEL` is set and allowed for `APP_ENV`. Detailed posture stays behind ADMIN at `/api/system/*`.
