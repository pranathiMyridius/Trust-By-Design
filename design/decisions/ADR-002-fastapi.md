# ADR-002 — FastAPI modular monolith backend

**Status:** Accepted (retain existing) · **Date:** 2026-09-30

**Context.** Backend is FastAPI 0.141 + SQLAlchemy 2.0 + Pydantic 2 with 17 routers / 151 routes, services, and pure engines. `api/assessments.py` is 5,097 lines; four `require_pipeline_role` definitions share one name but use two different role sets.

**Problem.** Service topology (monolith vs microservices) and how to keep the codebase maintainable.

**Decision.** One FastAPI service (**modular monolith**) with strict layering: routers → services → engines/AI gateway → DB. Split `assessments.py` into sub-routers by capability, introduce a single permission matrix, a global error envelope, and move startup work to a `lifespan` handler. Background work stays in-process for MVP.

**Alternatives considered.** Microservices for AI, scoring, workflow (network boundaries without independent scaling needs; distributed transactions around approvals); Django (rewrite); serverless functions (long LLM calls, cold starts, in-process jobs).

**Why selected.** Existing, working, typed; one deployable is simplest to secure and audit; transactions span workflow + scoring + audit in one DB transaction.

**Benefits.** Simple deploy, atomic use cases, easy local dev. **Trade-offs.** One process hosts web + jobs in MVP (scale by splitting the worker process later, same codebase).

**Consequences.** Layering rules enforced in review; engines stay pure so they can later be extracted if ever needed.
