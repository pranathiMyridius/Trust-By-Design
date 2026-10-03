# Testing & Evaluation

The Risk Assessment Workbench is a high-impact decision-support system, so
it is tested at two separate levels:

| Question | Suite | Needs an LLM? | Runs on every PR? |
|---|---|---|---|
| Does the application behave correctly? | **pytest** (unit, API, workflow) | No — provider faked | Yes |
| Do the user journeys work in a browser? | **Playwright** | No — provider faked | Yes |
| Are the AI's financial-crime assessments any good? | **DeepEval** eval suite | Yes (real model + judge) | No — on demand / weekly |
| What happened in a given run? | **Langfuse** tracing | — | Optional, off by default |

Deterministic business logic (scoring, bands, policy rules, workflow gates)
is asserted exactly. AI output is **never** checked with exact-string
assertions: it is scored against expected ranges, required risk factors
and rubrics.

---

## Quick start

From `backend/` (Windows paths shown; use `.venv/bin/python` elsewhere):

```bash
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m pip install -r requirements-dev.txt   # separate step, see note in the file
```

| What | Command (from) |
|---|---|
| Unit + API + workflow tests | `pytest` (backend/) |
| …with coverage | `pytest --cov=app --cov-report=term` (backend/) |
| AI evaluation, pytest style | `pytest tests/evals -v` (backend/) |
| AI evaluation, report + exit code | `python -m tests.evals.run` (backend/) |
| Subset / deterministic-only | `python -m tests.evals.run --cases RA-001,RA-014 --no-judge` |
| Consistency (repeat each case) | `python -m tests.evals.run --runs 3` |
| Compare models | `python -m tests.evals.run --models google/gemma-4-31b-it:free,openrouter/free` |
| Re-score a stored run (no model calls) | `python -m tests.evals.run --replay eval_results/latest/raw_outputs.json` |
| Accept a run as the regression baseline | `python -m tests.evals.run --save-baseline` |
| Browser E2E | `npm run test:e2e` (frontend/) — first time: `npx playwright install chromium` |
| Open the E2E HTML report | `npm run test:e2e:report` (frontend/) |

`pytest` on its own never touches the network, never needs `backend/.env`
and always runs against a throwaway SQLite file. The legacy acceptance
scripts (`backend/test_*.py`) are unchanged and still run with
`python test_xxx.py`; they are not collected by `pytest`.

---

## 1. pytest — deterministic application tests

```
backend/
├── pytest.ini                  testpaths = unit, api, workflow (evals are opt-in by path)
└── tests/
    ├── conftest.py             temp DB, test users, auth helper, autouse fake LLM
    ├── support/fake_llm.py     deterministic OpenRouter stand-in (also used by E2E)
    ├── unit/
    │   ├── test_scoring.py         factor score, band boundaries, weighted average,
    │   │                           unrated ≠ zero, mitigants, sanctions override, draft rules
    │   ├── test_risk_logic.py      rule engine, degraded-mode translation, error classification
    │   ├── test_llm_integration.py parsing, validation, quote verification (hallucination guard),
    │   │                           retry policy, typed errors, masking, rating suggestions
    │   ├── test_observability.py   Langfuse spans, no-secret/no-content guarantees, fail-safety
    │   └── test_eval_harness.py    the evaluator itself: dataset integrity, defect rules,
    │                               DeepEval wiring via a stub judge, gates, regression
    ├── api/test_assessments.py     create / get / list / update, validation (422), 401/403/404,
    │                               status & stage transitions and their gates, analyze (sync +
    │                               async), rating → deterministic score, AI outage via HTTP,
    │                               workflow crash → 500 with rollback
    └── workflow/test_langgraph.py  each LangGraph node, graph topology, every degraded path
                                    (timeout / 429 / 5xx / malformed / fallback off / rule engine
                                    failure / non-AI failure), versioning, no data loss on failed re-run
```

**How the LLM is faked.** Every AI call in the app goes through
`app/ai/metering.py::metered_post`. The autouse `fake_llm` fixture replaces
the `requests.post` it calls, so the real analyzers, evidence verifier,
rule engine, scoring and persistence all run; only the network hop is
fake. Tests inject failures with `fake_llm.transport = FakeLLM.timeout`
(also `rate_limited`, `server_error`, `malformed`, `connection_error`).

**Result:** 205 passed.
Coverage of the AI/workflow path: `langgraph/graph.py` 100%,
`langgraph/nodes.py` 94%, `ai/risk_factor_analyzer.py` 93%,
`ai/likelihood_impact_analyzer.py` 94%, `risk_engine/degraded.py` 100%,
`ai/metering.py` 87%. Overall app coverage is 56%, mostly because
`api/assessments.py` (5,000 lines, 41%) has many endpoints outside this scope
(documents, drafts, challenge, controls).

---

## 2. DeepEval — AI quality evaluation

### What is evaluated

Exactly what the product produces, minus the database:

1. `identify_risk_factors` — the real prompt, parsing and quote verification;
2. `estimate_likelihood_impact` for every applicable factor — the AI rating
   suggestion an analyst sees after **Suggest Ratings with AI**;
3. the app's own `calculate_inherent_risk` (default methodology) turning those
   suggestions into a **score and band**, including policy rules such as
   `SANCTIONS_EXPOSURE_001` (a verified sanctions indicator forces CRITICAL).

So "the AI said HIGH" means "an analyst accepting every AI suggestion gets
HIGH from the official methodology".

### Dataset — `backend/tests/evals/dataset.json` (v1.0.0, 21 cases)

| Mix | Cases |
|---|---|
| LOW | RA-001 UI-only change, RA-002 e-statements, RA-003 round-up savings, RA-004 document portal |
| MEDIUM | RA-005 EU SME debit card, RA-006 BNPL + remote onboarding (fraud), RA-007 outsourced KYC, RA-008 P2P limit ×10 (APP fraud / mules) |
| HIGH | RA-009 cash-out remittances, RA-010 crypto on/off-ramp, RA-011 nested correspondent banking, RA-012 offshore structures, RA-013 cash-loaded anonymous prepaid, RA-016 cash-intensive MSB acquiring |
| CRITICAL (sanctions) | RA-014 trade finance, buyer in a sanctioned jurisdiction; RA-015 owner with confirmed screening hit |
| Incomplete / ambiguous | RA-017 near-empty request, RA-018 BaaS with undisclosed partner base |
| Edge cases | RA-019 **negation trap** (cash explicitly disabled), RA-020 **contradictory** scope, RA-021 **prompt injection** inside evidence |

Each case records the intake input, expected level plus *acceptable* levels, a
score **range**, categories that must / must not be flagged, indicators
that must / must not appear, key factors for the judge, and whether the
model should say information is missing.

**Provenance.** Every fact the model needs is in the scenario text.
Country-risk and sanctions statuses are written as *the bank's own provided
policy* ("which the bank's sanctions policy treats as comprehensively
sanctioned"), not asserted as real-world designations. Each case has a
`provenance` block separating business rules implemented by this app,
evaluator assumptions and missing information. (The app's bundled FATF file
is itself marked `verified: false`.)

### Metrics

| Metric | How | Case threshold | Suite gate |
|---|---|---|---|
| Risk classification | band ∈ acceptable levels (deterministic) | — | ≥ 75% |
| Score deviation | distance from the expected range, points | ±5 pts tolerance | avg ≤ 10 |
| Risk-category recall | must-flag categories found applicable | 0.75 | reported |
| Indicator recall | expected verified indicators present | 0.5 | reported |
| Evidence verification | share of quotes found verbatim in the input | reported | reported |
| **Reasoning quality** | DeepEval G-Eval rubric | 0.6 | ≥ 0.7 |
| **Completeness** | G-Eval vs. expected key factors | 0.6 | ≥ 0.7 |
| **Faithfulness** (hallucination) | G-Eval; 1.0 = nothing invented | 0.7 | ≥ 0.8 |
| **Relevance** | G-Eval; includes prompt-injection resistance | 0.6 | reported |
| Consistency | Jaccard of applicable categories + band stability across `--runs` | 0.7 | reported |
| Pass rate | cases with no CRITICAL/HIGH/MEDIUM defect | — | ≥ 75% |
| Critical / High defects | count | — | 0 / ≤ 3 |

Thresholds live in `dataset.json` next to the cases, so they are
version-controlled together. Rubrics (fixed evaluation steps) live in
`tests/evals/metrics.py`. A run **fails** when any gate is missed, any case
errors, or — if `tests/evals/baseline.json` exists — any metric drops more
than `regression_tolerance` (0.05) below the baseline.

The judge is an OpenRouter model (`EVAL_JUDGE_MODEL`, default
`OPENROUTER_MODEL`). Use a stronger model than the one under test; a model
grading itself is biased.

### Defects

Every failed check becomes a typed defect with a severity. Under-rating
is treated as worse than over-rating, because in financial crime a missed
risk is costlier than an extra review.

| Category | Raised when | Severity |
|---|---|---|
| RISK_CLASSIFICATION_ERROR | under-classified by ≥2 / 1 band | CRITICAL / HIGH |
| | over-classified by ≥2 / 1 band | HIGH / MEDIUM |
| | flags a category the scenario gives no basis for | LOW |
| MISSING_RISK_FACTOR | required category missing | HIGH |
| | expected indicator missing (SANCTIONS_EXPOSURE: CRITICAL) | MEDIUM |
| HALLUCINATION | asserts a ruled-out indicator (e.g. cash when disabled) | HIGH |
| | quotes rejected by the verifier (<80% / ≥80% verified) | MEDIUM / LOW |
| | faithfulness below threshold (−0.2 or worse: HIGH) | MEDIUM / HIGH |
| INSUFFICIENT_REASONING | reasoning/relevance below threshold; missing info not stated | MEDIUM / HIGH |
| INCOMPLETE_OUTPUT | completeness below threshold; model omitted categories | MEDIUM / HIGH |
| SCORING_ERROR | score outside range (beyond / within tolerance) | MEDIUM / LOW |
| FORMAT_ERROR | missing rationale | LOW |
| WORKFLOW_ERROR | provider failed (case ERROR); rating suggestion fell back to 3×3 | HIGH / LOW |
| API_ERROR, UI_ERROR | reserved for the pytest / Playwright suites | — |

Each run writes to `backend/eval_results/<timestamp>/` (and `latest/`):
`summary.txt`, `summary.json`, `results.json` (one record per case),
`defects.json` (flat defect list), `raw_outputs.json` (model output, replayable).

### Real example output

From a full run on 2026-09-28 against the configured `OPENROUTER_MODEL=openrouter/free`:

```text
RISK ASSESSMENT EVALUATION
==========================
Dataset version:          1.0.0
Model under test:         openrouter/free
Judge model:              openrouter:openrouter/free

Total cases:              21
Passed:                   0
Failed:                   3
Errored:                  18

Risk classification:      67%
Reasoning quality:        73%
Completeness:             100%
Faithfulness:             3%
Relevance:                97%
Risk-category recall:     100%
Indicator recall:         100%
Evidence quotes verified: 94%

Critical defects:         0
High defects:             22
Medium defects:           2
Low defects:              3

Gate failed: pass_rate = 0.0 (threshold 0.75)
Gate failed: risk_classification_accuracy = 0.667 (threshold 0.75)
Gate failed: faithfulness = 0.033 (threshold 0.8)
Gate failed: high_defects = 22 (threshold 3)
Gate failed: errored_cases = 18 (threshold 0)
Regression status:        FAIL
```

The 18 errors were 9 rate limits, 6 timeouts (120 s) and 3 unparseable
replies. `openrouter/free` is a **router**: the metering log shows each call
answered by a different free model (a code model, a 2.6B model, even a
content-safety classifier). The eval shows this configuration is not fit
for a high-impact decision system. The faithfulness figure was computed
before a rubric fix (see Limitations), so re-run it before quoting it.

Real defect record (RA-005, EU SME debit card):

```json
{
  "test_case_id": "RA-005",
  "status": "FAIL",
  "risk_level_expected": "MEDIUM",
  "acceptable_risk_levels": ["MEDIUM", "HIGH"],
  "risk_level_actual": "CRITICAL",
  "score_expected_range": [35, 70],
  "score_actual": 64.0,
  "reasoning_score": 0.7,
  "completeness_score": 1.0,
  "evidence_verification_rate": 0.875,
  "policy_rules_fired": ["SANCTIONS_EXPOSURE_001"],
  "defects": [
    {
      "test_case_id": "RA-005",
      "category": "HALLUCINATION",
      "severity": "HIGH",
      "expected": "No SANCTIONS_EXPOSURE (the scenario rules it out)",
      "actual": "SANCTIONS_EXPOSURE asserted with a quote",
      "relevant_output": "FINANCIAL_CRIME_TYPOLOGY_RISK: \"card usable internationally\" -- International usability of the card exposes the bank to sanctions risks if transactions involve restricted countries or entities.",
      "trace_id": null
    }
  ]
}
```

`trace_id` is filled in when Langfuse is configured.

---

## 3. Playwright — end-to-end journeys

`frontend/playwright.config.ts` starts two servers:

* `backend/tests/e2e_server.py`: the real FastAPI app on `127.0.0.1:8000`
  (the address `src/api` hard-codes), with a **fresh temporary database**,
  test users from `backend/tests/support/e2e_users.json`, and the same
  deterministic fake AI provider as pytest (`E2E_REAL_LLM=1` uses the real
  one). Any intake text containing `SIMULATE_AI_OUTAGE` gets a provider
  timeout, so the degraded path can be driven from the browser;
* the Vite dev server on port 5173.

The backend is never reused: if your own dev backend is on port 8000, the
run stops rather than writing test data into your database. Stop it first.

| Spec | Journeys |
|---|---|
| `create-assessment.spec.ts` | submit a full intake → appears on the dashboard; missing fields flagged, focus moved, nothing created; draft saved with title only |
| `analyze-assessment.spec.ts` | analyst runs risk identification → factors, APPLIES/NOT APPLICABLE, rationale, misuse, verified evidence → rates factors → official score 64 / HIGH in the calculation panel **and** on the dashboard; analyze from the dashboard; AI outage → labelled rules-only factors → acknowledge → progress; unrated factors shown as UNRATED; sidebar updates after rating; calculator never self-logs |
| `status-workflow.spec.ts` | Draft → Submitted → Evidence Collection → (gate: unconfirmed profile refused) → confirm → Risk Identification; rating gate before Inherent Risk; business user cannot rate |

**Result:** 12 passed (stable across repeated runs), including regression
tests for the UI fixes in §8.

---

## 4. Langfuse — observability

`app/observability/tracing.py`, wired into three places without changing behaviour:

```
risk_assessment_workflow          (chain; langgraph/service.py)
├── load_assessment               (span per LangGraph node; langgraph/graph.py)
├── gather_intelligence
├── identify_risks
│   └── risk_factor_identification   (generation; ai/metering.py)
├── calculate_scores
└── persist_results
likelihood_impact_suggestion      (generation, per suggest-ratings call)
risk_assessment_eval_case         (chain, per eval case + eval.* scores)
```

Captured: trace id (returned in the workflow result and logged), assessment
id, node outputs (status, mode, AI status, error code, factor counts,
applicable categories, score, band), model requested and served, parameters,
latency, token usage, cost, HTTP status, errors (level ERROR), final
summary, and in evals the judge scores.

**Safe by default.** No prompt or reply text is sent unless
`LANGFUSE_CAPTURE_CONTENT=true`, and even then it is the payload after
`AI_MASK_SENSITIVE_DATA` masking. Headers and API keys are never passed to a
span. Provider errors are reduced to their error code (their messages can
echo prompt content). Unit tests assert all of this.

**Optional.** With the keys unset — or the package missing, or the Langfuse
server down — every helper is a no-op and every exception inside Langfuse is
swallowed. Tested with the real SDK against an unreachable host: the
workflow completes normally. The SDK's one-time start-up cost (~5 s import +
~4 s client init) is paid in a background thread at app start-up, not on the
first request.

```env
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_HOST=https://cloud.langfuse.com
LANGFUSE_CAPTURE_CONTENT=false   # optional
LANGFUSE_ENVIRONMENT=local       # optional
```

---

## 5. promptfoo — not added

promptfoo's main value here would be prompt/model comparison and prompt
regression. The DeepEval harness already does that against the same dataset
and the same production code path (`--models a,b` prints a side-by-side
table; `baseline.json` catches regressions). A second, Node-based eval
framework would duplicate the dataset and wrap the Python analyzer in a
custom provider for little extra value. Worth revisiting if non-developers
need to A/B prompt text in a web UI.

---

## 6. CI/CD

The repository had no CI, so two GitHub Actions workflows were added:

* **`.github/workflows/tests.yml`** — every PR and push to `main`: pytest with
  coverage and JUnit output, frontend type-check + build, lint (reported, not
  blocking yet: `src/` has 18 pre-existing lint errors), then Playwright with
  the fake provider (HTML report and traces uploaded as artifacts). No
  secrets needed.
* **`.github/workflows/ai-evals.yml`** — manual (`workflow_dispatch`, with
  case / model / judge / runs inputs) and weekly. Needs the
  `OPENROUTER_API_KEY` secret (Langfuse secrets optional). Fails on a quality
  gate or regression; the summary is posted to the job page and the full
  report is uploaded.

---

## 7. Environment variables

| Variable | Used by | Notes |
|---|---|---|
| `OPENROUTER_API_KEY` | evals, app | Required for live evals only |
| `OPENROUTER_MODEL` | evals, app | Model under test |
| `EVAL_JUDGE_MODEL` | evals | Judge (default: `OPENROUTER_MODEL`) |
| `EVAL_CASES`, `EVAL_RUNS`, `EVAL_NO_JUDGE`, `EVAL_REPLAY` | `pytest tests/evals` | Same as the CLI flags |
| `EVAL_MAX_ATTEMPTS`, `EVAL_BACKOFF_SECONDS` | evals | Retry policy for rate-limited models |
| `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST` | app, evals | Enable tracing |
| `LANGFUSE_ENABLED`, `LANGFUSE_CAPTURE_CONTENT`, `LANGFUSE_ENVIRONMENT` | app, evals | Optional |
| `E2E_PYTHON`, `E2E_REAL_LLM`, `E2E_FRONTEND_PORT` | Playwright | Optional |

All are documented in `backend/.env.example`.

---

## 8. Findings from the new tests — and their fixes

Real issues the suites surfaced. All but #2 (a configuration choice) have
been fixed; each fix is guarded by a regression test.

| # | Finding | Fix | Guarded by |
|---|---|---|---|
| 1 | **Speculative sanctions tag escalated to CRITICAL** (eval RA-005/RA-011): the verifier only checked a quote *exists*, so "card usable internationally" could carry `SANCTIONS_EXPOSURE` and fire `SANCTIONS_EXPOSURE_001`. | `app/risk_engine/evidence.py`: for indicators that decide an outcome on their own (`INDICATOR_TOPIC_CUES`, currently sanctions), the sentence the quote comes from must mention sanctions, embargoes, designated/restricted parties or a screening hit. Otherwise the indicator is refused **and** added to the factor's missing information for a human to confirm. Prompt tightened to match. | `test_llm_integration.py` (off-topic refused, on-topic kept, prompt rule); legacy `test_phase1_evidence_scoring.py` |
| 2 | **`openrouter/free` is unfit for this use**: random model per call, timeouts, rate limits, invalid JSON. | Configuration: pin a specific model in `OPENROUTER_MODEL`. | the eval suite |
| 3 | **Hallucinated category name took the wrong path** (`ValueError` → "unavailable", no fallback). | `risk_factor_analyzer.py`: unknown categories are dropped and logged (the displaced category is filled in as unresolved); a reply with no valid category is `AIInvalidResponseError`, so the rules-only fallback runs. | `test_llm_integration.py` (2 tests, formerly a strict xfail) |
| 4 | **Unrated factors displayed as "Inherent Risk LOW / 0"** in the Active Risk Indicator Map and Risk Dimensions panels. | Rows whose factor is unrated show a neutral **UNRATED** badge and "—" (never the green LOW style). | E2E "unrated factors are not presented as low risk" |
| 5 | **Overall Inherent Risk sidebar went stale** after rating. | Rating, adding or excluding a factor re-fetches the assessment. | E2E "the overall inherent risk summary updates after rating" |
| 6 | **No UI for degraded-mode acknowledgement**: a rules-only result was stuck in the browser. | Risk Identification step reads `assessment_mode` and shows a **Provisional analysis** banner with the safe `degraded_reason` and an **Acknowledge provisional result** action (pipeline roles only); the dead rationale-text detection was replaced. | E2E "an AI outage yields a labelled rules-only result a reviewer can acknowledge and progress" |
| 7 | **Stale screen state**: the profile's Confirm button only appeared after re-opening; a re-opened assessment showed its old stage; lists stale until reload. | The screen adopts newer copies of the assessment it is showing; a refused advance reloads the evidence/profile data; closing an assessment (any route) reloads the lists; a finished rating save no longer closes a form opened meanwhile on another factor. | E2E status journey (no reloads), dashboard score check |
| 8 | **Misleading messages**: "no usable risk results" for unrated factors; raw API paths shown to business users; "Only a Manager or Admin" where FCRM Analysts are allowed. | The rating check now runs first with an actionable message; user-facing messages name the screen and button instead of API paths; role wording corrected in all 12 places. | `test_assessments.py`, E2E rating gate |
| 9 | **Manual Scoring Calculator logged itself** ("weighted total 0.00 (LOW)") after risk identification. | Auto-save and audit logging only follow a real user edit. | E2E "the scoring calculator never writes to Audit History on its own" |
| 10 | **`requirements.txt` missed `requests` and `truststore`.** | Added. | CI install |

Trade-off to know for #1: a genuine sanctions indicator whose quoted
sentence does not use sanctions language (for example "the buyer is in
country X" with no mention of sanctions) is now refused as an indicator,
so the override does not fire on its own. It is not lost: it appears in the
factor's missing information for the analyst, and countries named on the
assessment are still checked against the jurisdiction reference-data rules.

---

## 9. Known limitations

* **Free-tier quota.** OpenRouter's free tier allows 50 requests/day; one
  full run with the judge needs roughly 200+. Use credits or a paid model for
  full runs, or `--cases` / `--no-judge` / `--replay` within the quota.
* **Judge reliability.** G-Eval scores are only as good as the judge model;
  the example above used a random free model as judge. The faithfulness
  rubric was later scoped to ignore system-computed fields (score line, band
  definitions, rule names), which the first judge wrongly counted as
  invented claims — re-run before comparing faithfulness numbers.
* **No committed baseline yet.** Regression detection activates once a
  run is accepted with `--save-baseline` (use a pinned model and judge).
* **Rating-dependent score.** The eval score reflects AI *suggested* ratings;
  in production an analyst confirms or overrides each one.
* **Eval bypasses the database.** It calls the same functions the LangGraph
  node calls, but not the node itself; node wiring is covered by
  `tests/workflow`. Intelligence profiles and uploaded documents are not
  part of the eval inputs yet.
* **E2E uses the fake provider,** so it proves the journeys, not AI quality.
  It requires port 8000 to be free because the frontend hard-codes the API URL.
* **Scope of pytest coverage.** Documents, drafts, challenge, controls,
  approvals and reports endpoints are covered only by the legacy scripts.
* **Installing dev tooling downgrades `click`** 8.5.0 → 8.3.3 (DeepEval pins
  `<8.4`); install the two requirement files in separate steps.
