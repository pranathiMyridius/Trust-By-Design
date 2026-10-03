# ADR-003 — Neon PostgreSQL as system of record

**Status:** Accepted (retain; harden) · **Date:** 2026-09-30

**Context.** The application moved from SQLite to Postgres with pgvector and was deployed to Neon (session history in `docs/AI_SDLC_EVIDENCE.md`). `database.py` still falls back to `sqlite:///./risk.db` when `DATABASE_URL` is unset and configures no pool options; migrations run at import.

**Problem.** Confirm the database, and make connection handling safe for Neon's serverless behaviour (idle suspend, pooled endpoint).

**Decision.** Neon PostgreSQL 16 + pgvector is the only runtime database. Remove the SQLite runtime fallback (SQLite allowed only when `APP_ENV=test`). Use the Neon **pooled** endpoint for the app and the **direct** endpoint for Alembic. Configure `pool_pre_ping`, `pool_recycle=300`, bounded pool, `connect_timeout`, `statement_timeout`, `sslmode=require`. One Neon branch per environment; ephemeral branches for CI.

**Alternatives considered.** SQLite (not concurrent, no pgvector, no PITR — rejected); self-hosted Postgres in Docker (ops burden; no branching); Azure/AWS managed Postgres (viable for production if enterprise policy requires; same SQL, change is a connection string); separate vector DB (pgvector suffices for similar-case retrieval).

**Why selected.** Already in use; branching gives cheap dev/staging/CI isolation; PITR; autoscaling; standard Postgres keeps portability.

**Benefits.** Reliability, environment isolation, relational integrity + JSON flexibility, vector search in the same DB. **Trade-offs.** Cold-start latency on suspended branches; pooler (transaction mode) forbids session state; external SaaS dependency.

**Consequences.** New env vars (`APP_ENV`, `DATABASE_URL_DIRECT`, pool settings); migration becomes a release step; runtime role without DDL rights.
