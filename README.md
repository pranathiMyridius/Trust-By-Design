# Trust-By-Design

Risk Assessment Workbench — a financial-crime risk assessment platform
(FastAPI + LangGraph backend, React/Vite frontend).

## Testing & evaluation

| Suite | Command |
|---|---|
| Unit, API and LangGraph workflow tests (no network) | `cd backend && pytest` |
| AI quality evaluation (DeepEval, real model) | `cd backend && python -m tests.evals.run` or `pytest tests/evals -v` |
| Browser end-to-end journeys (Playwright) | `cd frontend && npm run test:e2e` |

Optional Langfuse tracing is enabled by setting `LANGFUSE_PUBLIC_KEY` /
`LANGFUSE_SECRET_KEY` (see `backend/.env.example`).

Full guide — what each suite proves, the evaluation dataset, metrics,
thresholds, defect reports, CI and known limitations:
[docs/TESTING.md](docs/TESTING.md).
