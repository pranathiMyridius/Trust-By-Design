# AI Governance

Goal: an auditor can take any decision and reconstruct **what data went in, which model and prompt produced which proposal, which deterministic rules and methodology turned it into a score, and which humans changed or approved what.**

## 1. What is tracked — current vs target

| Item | Current | Target (MVP) |
|---|---|---|
| Model requested / served | `ai_usage_logs.requested_model`, `response_model` per call | + on `analysis_runs`; pinned model; allow-list |
| Model parameters | Hard-coded per module (temp 0.1–0.5) | `analysis_runs.model_params` |
| Prompt version | ❌ none | `prompt_versions` registry; `prompt_version_id` on run; `prompt_key/version` on usage log |
| Workflow version | ❌ none | `WORKFLOW_VERSION` + `graph_hash` on run |
| Scoring algorithm version | Methodology version + fingerprint | + `scoring_engine_version` |
| Assessment run id | ❌ (only `processing_jobs`) | `analysis_runs.run_uuid`, FK from factors, calcs, drafts, usage logs, audit |
| Timestamp | usage log `created_at` | run `started_at` / `finished_at` / `duration_ms` |
| Input data version | ❌ | `input_sha256` + masked `input_snapshot`; document version + checksum per evidence quote |
| Output | Parsed factors only | `raw_output` (masked) + parsed `output_summary` |
| Reference data | Snapshot ids/checksums in calc `reference_data` | + `reference_snapshot_ids` on run |
| Human changes | `assessment_overrides`, audit events (actor sometimes from body) | Actor id from JWT always; before/after JSON |
| Evaluation | `ai_evaluation_records` (AI vs human per assessment); DeepEval offline | + linked to `analysis_run_id` |
| Tracing | Langfuse spans (optional) | `trace_id` on run |

## 2. Prompt registry

```
app/ai/prompts/
  registry.py            # PromptSpec(key, version, template, output_schema_version, temperature)
  risk_factor_identification.py   v2.0.0
  document_extraction.py          v1.0.0
  likelihood_impact_suggestion.py v1.0.0
  control_identification.py       v1.0.0
  control_design_assessment.py    v1.0.0
  draft_narrative.py              v1.0.0
```

- Templates move verbatim out of the analyzer modules (first registration = current text, so behaviour is unchanged).
- At startup `sync_prompt_registry(db)` upserts `(key, version, sha256, text)`; a changed hash under an existing version **aborts startup**.
- Every call passes `prompt_key`/`version` to `metered_post`, which records them on `ai_usage_logs`.

## 3. Change control for prompts and models

| Change | Required before merge/deploy |
|---|---|
| Prompt text | Version bump (SemVer: minor for wording, major for output schema); DeepEval run on the full 21-case dataset with the pinned model; gates in `tests/evals` (pass rate ≥ 0.75, classification accuracy ≥ 0.75, faithfulness ≥ 0.80, 0 errored cases, ≤ 3 high defects); results committed as baseline |
| Model | Same eval gate; add to `OPENROUTER_ALLOWED_MODELS`; record in release notes |
| Methodology | ADMIN clone → edit → activate with reason (existing); regression on fixture assessments showing band changes |
| Scoring engine code | Unit tests + replay test on frozen fixtures; bump `SCORING_ENGINE_VERSION` if outputs could change |
| Workflow graph | Bump `WORKFLOW_VERSION`; graph tests |

Latest eval (`eval_results/latest`, `full-run-1`) **fails** the gates with `openrouter/free` (18/21 errored; faithfulness 3 %). Pinning a capable model and re-baselining is a Stage 3 prerequisite for demoing AI quality.

## 4. Reproducing the reasoning path of an assessment

1. `GET /decision-record` → frozen record (format 1.1) lists `analysis_runs[]` (uuid, type, model, prompt version, workflow version), methodology fingerprint, engine version, reference snapshots, every factor with evidence, every override and decision. Checksum verifies it has not been altered.
2. `GET /analysis-runs/{uuid}` (with `audit.export`) → `input_snapshot` (what the model saw, masked) + `raw_output` (what it said).
3. **Deterministic replay:** `raw_output` → `verify_evidence` → `tag_and_aggregate` → with stored analyst ratings → `calculate_inherent_risk` (snapshotted config, recorded engine version) → must equal the stored calculation. Differences are an integrity failure.
4. **LLM replay** (not bit-exact): re-sending `input_snapshot` with the same prompt version and model shows whether the model is still stable; used in evals, not for audit proof.
5. Human path: `workflow_transitions` + `audit_events` (actor ids, reasons, before/after) + `assessment_overrides` + `committee_votes`.

## 5. Responsible-use controls

| Control | Implementation |
|---|---|
| Human accountability | AI never sets status or decision; approvals are named users; SoD rules |
| Transparency | AI-produced content labelled with model/prompt; advisory notice |
| Data minimisation | PII masking before provider call; `LANGFUSE_CAPTURE_CONTENT=false` by default |
| Provider data use | Choose OpenRouter models/providers with no-training / zero-retention policies (OpenRouter provider routing settings) — confirm with procurement (Q-06) |
| Monitoring | `/api/system/ai-reliability` (error/timeout/rate-limit rates), `/api/reports/ai-evaluation` (AI vs human agreement, groundedness) |
| Fallback | Degraded mode; never silent low risk |
| Retention | `input_snapshot`/`raw_output` follow the assessment retention policy (default 7 years) and legal hold |
