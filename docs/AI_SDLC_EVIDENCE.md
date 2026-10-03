# AI-Assisted SDLC: Evidence

This document records how AI was used to **build** the Risk Assessment
Workbench, across six SDLC stages. It is separate from how AI is used
**inside** the product (see `backend/AI_ORCHESTRATION.md`).

Every claim below cites a Claude Code session ID and date, so it can be
checked against the original transcript. Nothing here is reconstructed
from memory.

## How to verify

Transcripts are stored by Claude Code on the development machine:

    ~/.claude/projects/C--Users-KPranathi-Documents-RiskAssessmentWorkbench/<session-id>.jsonl

Session IDs below are the first 8 characters of the file name. Subagent
transcripts sit under `<session-id>/subagents/`. The statistics were
computed directly from these files.

## At a glance

| Measure | Value |
|---|---|
| Development period | 2026-09-21 to 2026-09-28 |
| Claude Code sessions | 19 |
| Subagent transcripts (delegated analysis/design tasks) | 13 |
| Human prompts | 121 |
| Distinct files written or edited by AI | 229 |
| Browser-driven verification tool calls | 556+ |
| Clarifying questions AI asked the developer | 13 |
| Web research calls (papers, regulatory sources) | 16 |
| Output tokens | 4.87M |
| Input served from prompt cache | 98.9% |

Models used, by assistant turns:

| Model | Turns | Typical use |
|---|---|---|
| `claude-sonnet-5` | 5,298 | Most stage implementation |
| `claude-opus-5-5` | 994 | Stages 14, 17, 19; this review |
| `claude-opus-5` | 753 | Methodology research, risk taxonomy, review |
| `claude-haiku-4-5` | 281 | Small UI adjustments (`31f7e67f`) |

The mix shows models chosen per task, with a small model for layout
tweaks and larger ones for design and research. Model choice was made
by the developer per session; it was not an automated routing policy.

---

## 1. Requirements

**What AI did:** traceability gap analysis of each specification
against the existing code; research that expanded requirements; and,
at the end, recovery of the requirement set from session history.

| Evidence | Session | Date |
|---|---|---|
| Developer pasted the six *Scope and Principles*; AI checked each against the code and proposed changes ("is this already exist and working… make changes according to this") | `8a76f4fc` | 09-21 |
| Stage 1, 2 and 4 specs pasted; three subagents ran gap analyses of R1.x, R2.x and R4.x against the codebase | `1af20dc5` | 09-21 |
| 22,000-character specification for a hierarchical approval workflow, analysed and decomposed by a subagent | `8a0772bf` | 09-21 |
| **Requirement expansion:** AI reviewed a Springer paper on risk-prediction methodology and advised what applied; the developer then chose the missing-data strategy (median imputation) | `7bb42e0f` | 09-24 |
| **Requirement expansion:** AI mapped the OCC risk categories onto the model; the developer chose "roll-up only, keep the control library as is" | `97bc4e31` | 09-24 |
| **Requirement expansion:** developer-originated dashboard prioritisation by launch date and assessment filters | `b7d15b2e` | 09-23 |

**Specified stages, recovered from session history:**

| Stage | Title | Specified in | In code | Tests |
|---|---|---|---|---|
| 1 | Business Request Intake | `1af20dc5` | Yes | No |
| 2 | Document and Evidence Management | `1af20dc5` | Yes | No |
| 3 | Intake Validation and Structuring | `8a76f4fc` | Yes | No |
| 4 | Risk Factor Identification | `1af20dc5` | Yes | Partial (degraded mode) |
| 5 | *Never specified* (AI-inferred: Risk Methodology) | — | Untraced | No |
| 6 | Inherent Risk Assessment – Manual Calculation (No AI) | `31f47eb1` | Yes | No |
| 7 | Control Identification and Assessment | `46a662bb` | Yes | No |
| 8 | *Never specified* (AI-inferred: Residual Risk) | — | Untraced | No |
| 9 | Assessment Draft Generation | `31f47eb1` | Yes | No |
| 10 | FCRM Analyst Review | `b7d15b2e` | Yes | No |
| 11 | Conditional Challenge Review | `46a662bb` | Yes | No |
| 12 | Committee Decision | (in code as R12.x) | Yes | No |
| 13 | Conditions and Remediation Tracking | `b7d15b2e` | Yes | No |
| 14 | Workflow and Status Management | `798f1234` | Yes | Yes |
| 15 | *Never specified* (AI-inferred: Authentication, Roles and Access) | — | Untraced | No |
| 16 | Audit Trail and Explainability | `46a662bb` | Yes | No |
| 17 | Reporting and Monitoring | `798f1234` | Yes | Yes |
| AW | Approval Workflow (project requirement, formerly "R17") | `8a0772bf` | AW.1–AW.7 | AW.7 automated; AW.2–AW.5 in E2E plan |
| 18 | Reassessment and Change Management | `31f47eb1` | Yes | No |
| 19 | Non-Functional Requirements | `213c9e63` | Yes | Yes |

**Gaps:**
- **Stages 5, 8 and 15 were never specified.** Their requirements were
  never given to the AI, so nothing in the code is tagged R5, R8 or R15.
  Later sessions filled the gaps with AI-inferred titles, and not
  consistently: `31f7e67f` called Stage 5 "Control Assessment", while
  `213c9e63` and `b459d10a` called it "Risk Methodology". The E2E plan
  uses Risk Methodology (5), Residual Risk (8) and Authentication, Roles
  and Access (15). Working code exists for all three (the
  `/api/risk-methodologies` API, residual scoring in the Stage 7 control
  engine, and `app/auth/`), but whether it meets the brief's actual
  requirements is unverified. Confirm the titles and requirements against
  the original brief, then tag the code.
- **ID collision (resolved 09-28):** the approval-workflow spec in
  `8a0772bf` was labelled "R17", which collided with the brief's Stage 17
  (Reporting and Monitoring). It is now **AW.1–AW.7**, defined in
  `backend/app/api/approvals.py`. Every remaining R17 reference is
  reporting. AW.7 (approval delegation) was the one unimplemented
  sub-requirement; it was built on 09-28 (`2d8e7fb6`) with its own
  acceptance suite, `backend/test_aw7_delegation.py`.

## 2. Design

**What AI did:** proposed designs for each stage (often through a
design subagent before any code was written) and researched the
methodology. The developer made the key architectural calls.

| Evidence | Session | Date |
|---|---|---|
| **The core AI/deterministic split, decided by the developer:** "assign the severity or score by AI but for calculation like average let's do by system". This became `calculate_scores` in the LangGraph pipeline (deterministic) after `identify_risks` (AI) | `798f1234` | 09-23 |
| Stage 6 explicitly specified as *No AI*: manual inherent-risk calculation | `31f47eb1` | 09-23 |
| Design subagents ran before implementation of Stage 7 ("I'm designing Stage 7…"), Stage 11 and Stage 16 | `46a662bb` | 09-23 |
| The developer checked the design approach: "are you creating new nodes or based on the requirements are you making changes where they need?" | `46a662bb` | 09-23 |
| Storage design: SQLite vs Postgres, Docker vs managed Neon, pgvector for document embeddings across multiple documents | `b143bb11` | 09-22 |
| The developer asked whether AI needs a training dataset; the design stayed prompt-based with no model training | `798f1234` | 09-23 |
| Country-risk reference data and AI-suggested ratings, from the methodology research | `7bb42e0f` | 09-24 |

## 3. Implementation

**What AI did:** wrote and edited 229 files across the FastAPI backend,
React frontend, migrations and scripts, one specification stage at a
time.

| Stages | Session | Date |
|---|---|---|
| Scope principles, Stages 1–3 | `8a76f4fc` | 09-21 |
| Stages 1, 2, 4 | `1af20dc5` | 09-21 |
| Approval workflow; admin Users tab | `8a0772bf` | 09-21 |
| Stages 6, 9, 18 | `31f47eb1` | 09-23 |
| Stages 7, 11, 16 | `46a662bb` | 09-23 |
| Stages 10, 13; dashboard filters | `b7d15b2e` | 09-23 |
| Stages 14, 17 | `798f1234` | 09-23 |
| Stage 19 (security, encryption at rest, accessibility, processing jobs) | `213c9e63` | 09-23 |
| AI likelihood/impact scoring in the risk calculator | `31f7e67f` | 09-24 |
| OCC category roll-up, AI metrics | `97bc4e31` | 09-24 |

**Gap:** the git history does not show this work. There are three
commits, all dated 2026-09-18, and everything above is uncommitted. The
developer deliberately held commits until each change was tested (see
section 7), but committing per stage would give reviewers a verifiable
trail inside the repo itself.

## 4. Testing

**What AI did:** drove the running app in a browser to check each
feature, wrote test plans and acceptance tests, and in the final review
found and fixed failing tests.

| Evidence | Session | Date |
|---|---|---|
| 556+ browser-automation calls across sessions: clicking through each feature in the running app and reading console errors | many | 09-21 → 09-24 |
| Manual test cases written for the approval workflow (no Postman available) | `8a0772bf` | 09-22 |
| `docs/E2E_TEST_PLAN.md` and `docs/E2E_Test_Guide.docx`: end-to-end steps per requirement and role (business owner → manager → FCRM → committee) | `b459d10a` | 09-24 |
| Acceptance suites `test_stage14_workflow.py`, `test_stage17_reporting.py`, `test_stage19_nfr.py`, `test_degraded_mode.py` | several | 09-23 → 09-24 |
| **Review session:** three failing tests diagnosed. One stale test was corrected, one scoring function was fixed, and one test exposed a real account-takeover hole, which was closed (section 6) | `2d8e7fb6` | 09-28 |

**Gaps:** tests exist for Stages 14, 17 and 19 only. There are no
frontend tests, `pytest` is not in `requirements.txt`, and there is no
CI.

## 5. Deployment

**What AI did:** set up the database, migrated off local-only storage,
and automated schema migration.

| Evidence | Session | Date |
|---|---|---|
| Moved from SQLite to Postgres with pgvector; worked around no Docker access by using managed Neon | `b143bb11` | 09-22 |
| Deployed the schema to Neon | `798f1234` | 09-23 |
| After a production-style startup error, the developer chose to "run it automatically instead of end user to run this". AI introduced Alembic (`alembic/`, `app/migrations.py`) so migrations apply on startup | `6adfac05` | 09-24 |
| `.env.example` with documented configuration and security settings | `213c9e63` | 09-23 |
| Backup and restore tooling; `docs/BACKUP_AND_RECOVERY.md` | `213c9e63` | 09-23 |

**Gap:** there is no application Dockerfile; `docker-compose.yml` runs
Postgres only.

## 6. Maintenance

**What AI did:** diagnosed defects from pasted errors, and at the end
ran a full review of the codebase.

| Evidence | Session | Date |
|---|---|---|
| Missing-column error (`reference_id`) diagnosed and fixed in a two-minute session | `499187ec` | 09-21 |
| Server error on manager login, diagnosed from a pasted traceback | `6adfac05` | 09-24 |
| Notes lost on navigation; risk calculator resetting to defaults | `5dc3f97b`, `213c9e63` | 09-23 → 09-24 |
| "Why can't it estimate every factor?": investigation of partial AI ratings | `97bc4e31` | 09-24 |
| **Codebase review** (`2d8e7fb6`, 09-28): | | |
| - Dead code found by import analysis and removed: 25 files, including an unused 1,005-line analyzer and an unused component | `2d8e7fb6` | 09-28 |
| - `requirements.txt` was UTF-16, which blocked `pip install`; converted to UTF-8 | `2d8e7fb6` | 09-28 |
| - Contradictory provider docs (Gemini and Anthropic, neither in use) replaced with one accurate OpenRouter doc | `2d8e7fb6` | 09-28 |
| - **Security:** an unauthenticated password-reset endpoint allowed takeover of any account, including admin. The endpoint was removed, and a direct probe confirmed the attack now fails (404; original password still valid) | `2d8e7fb6` | 09-28 |
| - Requirement stages recovered from session history (section 1) | `2d8e7fb6` | 09-28 |

## 7. Human oversight of AI during development

The developer kept control over what the AI changed:

- **No autonomous commits.** The first instruction in the first two
  sessions was not to commit: "just make local changes, I will commit
  those changes once I test" (`8a76f4fc`, `1af20dc5`). Every change was
  held for human testing.
- **Clarifying questions:** the AI asked the developer 13 structured
  questions instead of guessing, for example choosing the security fix
  in `2d8e7fb6`.
- **Design decisions stayed with the developer:** the AI/deterministic
  split, Stage 6 as no-AI, median imputation, OCC roll-up only, and the
  automatic-migration approach.
- **Challenge of AI approach:** the developer asked mid-stream whether
  the AI was following the requirements or inventing structure
  (`46a662bb`).

## 8. Token efficiency during development

- **98.9% of input tokens were served from the prompt cache**
  (1.93B cache reads against 20.8M cache writes and 16K uncached
  tokens). Long sessions re-read the same codebase context cheaply
  instead of resending it.
- **Delegation:** 13 subagents did bounded analysis and design work in
  their own context, returning conclusions rather than filling the main
  session with file contents.
- **Model sizing:** Haiku was used for small UI changes, and larger
  models for design and research.

This covers the development process only. Token efficiency of the
product's own AI calls is a separate open gap: calls are metered, but
no call sets `max_tokens`, there is no context truncation, and all AI
modules share one model setting.

## 9. Known gaps in this evidence

| Gap | Impact | Fix |
|---|---|---|
| Stages 5, 8, 15 never specified | Related code exists but is untraced and unverified against the brief | Confirm against the brief, then tag or build |
| Work is not in git history | Reviewers cannot check work against commits | Commit per stage |
| Transcripts exist on one machine only | Evidence could be lost | Export key sessions into `docs/` |
| Tests cover 3 of 16 implemented stages | Weak testing-stage evidence | Add suites for the other stages |
