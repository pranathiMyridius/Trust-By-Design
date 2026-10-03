# MVP vs Production

Production items are **not** prerequisites for the hackathon MVP.

| Component | Hackathon MVP (Stage 3) | Production evolution |
|---|---|---|
| Frontend | React/Vite/TS + react-router; env-based API URL; split workspace; UNRATED/provisional states | Component library, TanStack Query, i18n, Figma design system |
| Backend | FastAPI monolith; permission matrix; error envelope; actor-from-JWT; lifespan startup | Split worker process; import-linter boundaries; OpenAPI-generated TS client |
| Database | Neon Postgres + pgvector; pooled/direct URLs; pool pre-ping; no SQLite fallback; migrations 0009–0014 | Neon Scale plan, PITR ≥ 7–30 d, IP allow-list, protected branch, read replica for reports; JSONB + timestamptz; CHECK constraints; DB-level append-only triggers |
| Environments | Neon branches: dev, staging, main; CI ephemeral branches | Separate Neon projects per env if policy requires; data anonymisation pipeline for staging |
| AI gateway | OpenRouter, pinned model, allow-list, masking, metering | Configurable enterprise gateway; zero-retention provider routing; per-task model config |
| Workflow | LangGraph 7-node graph, 1 LLM node; async jobs; per-node progress | PostgresSaver checkpointing; dedicated worker; circuit breaker |
| Prompts | Code registry + `prompt_versions` table; eval gate | Prompt diff tooling; A/B shadow runs |
| Scoring | Existing engine + engine version, confidence, replay test, weight/band validation | Nightly integrity re-verification; methodology impact simulation before activation |
| Evidence | Exact-quote verification; origin + claim-type labels | Normalised `factor_evidence` table; advisory entailment check; external sources with provenance |
| Human review | Existing flow + override rules + real reviewer identity | QA sampling workflow; override analytics |
| Approvals | Existing manager → committee, delegation, decision record 1.1 | `approval_decisions` history table; e-signature integration |
| Auth | JWT HS256, bcrypt, 5 roles, login throttling, mandatory secret | OIDC SSO (Entra ID), MFA, AUDITOR role, httpOnly cookies |
| Audit | Append-only `audit_events` with actor_id, request_id, before/after; ADMIN-only read-all | Hash chain, SIEM export, DB role grants, AUDITOR portal |
| Files | Fernet-encrypted on a persistent volume | Object storage + SSE + signed URLs; malware scanning |
| Background jobs | In-process pool (1 instance) | `SKIP LOCKED` queue in Postgres, N workers |
| Observability | App logs, `Server-Timing`, Langfuse optional, `/api/system/*` | OpenTelemetry → central logs/metrics; alerts (AI error rate, job backlog, SLA) |
| Evaluation | 21-case DeepEval set, pinned model baseline, gate before prompt/model change | Continuous evals, larger labelled set incl. bribery/fraud typologies, human QA feedback loop |
| Deployment | 1 backend container + static frontend + Neon; migrations as release step | ≥2 instances behind LB, WAF/CDN, IaC, blue-green |
| Security | CSP, CORS allow-list, upload limits, masking, secrets in platform store | Key Vault with rotation, pen test, rate limiting at gateway, DLP |

## Stage 3 build list (priority order)

**P0 — correctness & governance (must have for demo)**
1. `app/config.py` + `database.py` hardening (DR-08, D-06).
2. Permission matrix + fixes D-01, D-02, D-05; actor-from-JWT (D-03, D-12).
3. Fix D-04 controls endpoints; single-commit `advance-stage` (D-08).
4. Migrations 0009–0011 (`analysis_runs`, `prompt_versions`, `idempotency_keys`, actor ids, engine version, confidence).
5. Prompt registry + run recording in LangGraph and the other AI tasks (DR-02).
6. LangGraph 7-node graph with async-only analysis and `stage_key` progress.
7. UI: UNRATED/provisional states, fresh scores after rating, degraded banner, gate reasons (D-15, DR-05, DR-12).
8. Pin model; re-run eval; baseline.

**P1 — completeness**
9. Override rules (floors, downward four-eyes) (DR-07).
10. `GET /scores`, `GET /assessments/summary`, `/analysis-runs*`, `/health/ready`.
11. Error envelope + no-GET-writes (DR-10).
12. react-router + split `AssessmentWorkflow.tsx`; single API client.
13. Typology tags (DR-11); remove OCC panel.
14. Retire legacy `/status`, `/challenge`, `create-from-document`.

**P2 — nice to have in MVP**
15. Idempotency-Key support; optimistic `lock_version`.
16. Backend Dockerfile + CI deploy; migrations 0012–0014.
17. Login throttling + CSP.
