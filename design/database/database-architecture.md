# Database Architecture — Neon PostgreSQL

## 1. Decision

**Neon-hosted PostgreSQL 16 with the `pgvector` extension remains the system of record** (ADR-003). SQLite is used **only** by the unit-test suite (`APP_ENV=test`) and is never a runtime fallback.

## 2. Current implementation (inspected)

| Aspect | Today (`app/database.py`, `alembic/`) | Assessment |
|---|---|---|
| URL | `DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./risk.db")` | 🔧 Silent SQLite fallback (D-06). A `risk.db` file exists in the repo. |
| Driver | `psycopg2-binary` 2.9 | ✅ Fine for sync SQLAlchemy |
| Engine | `create_engine(DATABASE_URL)` — default QueuePool (5 + 10), no `pool_pre_ping`, no `pool_recycle` | 🔧 Neon suspends idle compute and closes idle connections → stale-connection errors after idle periods |
| Extension | `CREATE EXTENSION IF NOT EXISTS vector` executed **at import time** (Postgres URLs only) | 🔧 Import requires a live DB and an owner-privileged role |
| Sessions | `SessionLocal(autocommit=False, autoflush=False)`, `get_db()` per request | ✅ |
| Schema mgmt | Alembic 0001–0008; `run_migrations()` at import of `main.py`; advisory lock `7482019` | 🟡 Works; should be a release step |
| TLS | Only checked by `/api/system/security-posture` | 🔧 Enforce `sslmode=require` |
| Types | JSON in `Text`; naive `DateTime`; enums as `String` | 🟡 Portable; weaker DB-side validation |
| Relationships | FK columns only; no ORM `relationship()` | ✅ Acceptable (explicit queries) |
| Protection | ORM hooks block deletes on 12 tables and updates on `audit_events` | ✅/🔧 Extend list (D-13) |

## 3. Target connection management

```python
# app/database.py (target shape — Stage 3 implements)
settings = get_settings()                          # app/config.py, validates APP_ENV
url = settings.database_url                        # Neon *pooled* host, ...?sslmode=require
if url.startswith("sqlite") and settings.app_env != "test":
    raise RuntimeError("DATABASE_URL must point to PostgreSQL (Neon) outside tests.")

engine = create_engine(
    url,
    pool_size=settings.db_pool_size,               # default 5
    max_overflow=settings.db_max_overflow,         # default 5
    pool_pre_ping=True,                            # survive Neon idle suspend
    pool_recycle=settings.db_pool_recycle_seconds, # default 300
    pool_timeout=30,
    connect_args={"connect_timeout": 10,
                  "application_name": f"raw-{settings.app_env}",
                  "options": "-c statement_timeout=30000"},
)
```

- **Pooled vs direct:** runtime uses Neon's PgBouncer endpoint (`…-pooler.<region>.aws.neon.tech`, transaction pooling). Alembic and `CREATE EXTENSION` use the **direct** endpoint (`DATABASE_URL_DIRECT`) because advisory locks and DDL need a session-level connection. With transaction pooling, avoid session state (`SET`, temp tables, server-side cursors) in app code — none is used today.
- **Pool sizing:** per instance `pool_size + max_overflow ≤ 10`. Neon pooler supports far more client connections than direct Postgres, so horizontal scale is not constrained by the DB in MVP.
- **Cold start:** Neon compute resumes in ~sub-second to a few seconds; `connect_timeout=10` and `pool_pre_ping` absorb it. Keep autosuspend on for dev branches, off (or longer) for production.
- **Statement timeout** 30 s protects against runaway report queries; the LLM call is never inside a DB transaction.

## 4. Transactions

| Rule | Why |
|---|---|
| One transaction per use case, committed in the service layer | Fixes partial state in `advance-stage` (D-08) |
| Never hold a transaction open across an LLM call | LLM calls take 5–120 s; the worker commits the "running" state, calls the model, then opens a new transaction to persist |
| `SELECT … FOR UPDATE` on the assessment row inside `workflow.transition()` | Prevents concurrent approvals/transitions |
| Engines are pure; only services flush/commit | Reproducibility + testability |
| GET handlers are read-only | Enables safe retries, caching and a read replica later (D-07) |

## 5. JSON vs relational — decision

| Data | Storage | Rationale |
|---|---|---|
| Core entities, statuses, scores, bands, FKs, actor ids, timestamps | **Relational columns** | Filtered, joined, constrained, reported on |
| Per-factor evidence records, indicators, rejected indicators, missing information | JSON (today `Text`; production `JSONB`) | Variable-length lists read with the factor; rarely queried across assessments |
| Calculation snapshots (inputs, weights, bands, rules, grid) | JSON | Immutable snapshots — the point is to freeze exactly what was used |
| Frozen decision record | JSON + checksum | Must be byte-stable for SHA-256 verification — keep as `Text` forever (JSONB reorders keys) |
| AI raw output / input snapshot on `analysis_runs` | `Text` (masked) | Stored verbatim for audit; never queried by content |
| Methodology config (weights, bands, rules, grid) | JSON | Versioned as a unit and fingerprinted |

**MVP:** keep `Text` JSON (works; no migration risk).
**Production:** convert *queried* JSON columns to `JSONB` using `sa.JSON().with_variant(JSONB, "postgresql")`, with GIN indexes on `risk_factors.indicators` and `risk_factors.crime_typologies` (portfolio reporting: "all assessments with SANCTIONS_EXPOSURE"). Snapshot/record columns stay `Text`.

## 6. Data protection in the database

- Append-only (no UPDATE/DELETE via ORM): `audit_events`, `workflow_transitions`, `decision_records`, `prompt_versions`, `ai_usage_logs`.
- No DELETE (versioned via `is_current`): existing 12 protected tables **plus** `residual_risk_calculations`, `challenge_findings`, `controls`, `control_assessments`, `analysis_runs`, `assessment_drafts`, `reference_data_snapshots`, `risk_methodologies`, `country_risk`.
- Production: enforce the same at DB level — the runtime role `app_rw` gets `INSERT, SELECT` only on append-only tables, plus a trigger that rejects `UPDATE/DELETE` on `audit_events` and `decision_records`.
- Soft delete only (`assessment_retention.is_deleted`), subject to legal hold and retention policy (2,555 days default).

## 7. Backup and recovery

- MVP: Neon point-in-time restore (history retention per plan) **replaces** the app's own backup loop for the database; keep `services/backup.py` only for uploaded files until object storage exists.
- Restore drill: create a Neon branch at a timestamp → point staging at it → verify decision-record checksums (`GET /decision-record` → `intact: true`).

## 8. Environments

See `architecture/deployment-architecture.md` §3 (one Neon branch per environment; separate roles and secrets; CI uses ephemeral branches).
