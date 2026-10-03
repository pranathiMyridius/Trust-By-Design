# Local Test Guide and Test Cases

How to run and test the Risk Assessment Workbench on your machine, the test
cases to execute, and what the automated Playwright suite already covers.

Last Playwright run: **2026-10-03, 31 of 31 passed** (Chromium, fake AI provider, throwaway database, ~5 min).

---

## 1. Run it locally

> **Warning: protect the primary database.** `backend/.env` can point at the
> primary Neon database, and starting the app (or running alembic) migrates
> whatever `DATABASE_URL` points at. For testing, always override it with a
> local SQLite file as shown below.

### Option A: seeded test backend (fastest, no API key)

Uses a fresh temporary SQLite database, pre-created users for every role and a
**fake AI provider**, so results are repeatable.

```powershell
# Terminal 1: backend  (port 8000 must be free)
cd backend
.venv\Scripts\python tests\e2e_server.py

# Terminal 2: frontend
cd frontend
npm install        # first time only
npm run dev        # http://localhost:5173
```

Sign in with the accounts in `backend/tests/support/e2e_users.json`
(test-only; the file lists email and password for each):

| Persona | Role (extra designations) |
|---|---|
| `owner@e2e.test` | Business user (submits requests) |
| `analyst@e2e.test` | FCRM analyst |
| `reviewer@e2e.test` | FCRM analyst (senior, QA, challenge reviewer) |
| `manager@e2e.test` | Manager |
| `head@e2e.test` | Manager (Head of FCRM, governance owner) |
| `committee@e2e.test` | Committee member |
| `chair@e2e.test` | Committee member (chair) |
| `admin@example.com` | Admin |
| `dualadmin@e2e.test` | Admin (used for separation-of-duties tests) |

Tip: put `SIMULATE_AI_OUTAGE` in an intake description to force the AI-outage (rules-only) path.

### Option B: your own dev backend (real AI)

```powershell
cd backend
copy .env.example .env      # set JWT_SECRET, FILE_ENCRYPTION_KEY, ADMIN_BOOTSTRAP_PASSWORD, AI key
$env:DATABASE_URL = "sqlite:///./local_test.db"     # NOT the Neon URL
.venv\Scripts\python -m uvicorn app.main:app --reload --port 8000
# frontend as above; API docs: http://localhost:8000/docs
```

Log in as the bootstrap admin (`ADMIN_BOOTSTRAP_EMAIL` / `ADMIN_BOOTSTRAP_PASSWORD`),
open **Users**, create one user per role (see `docs/E2E_TEST_PLAN.md` §0.2).

### Automated checks

| Suite | Command | Needs |
|---|---|---|
| Backend unit/API/workflow | `cd backend; .venv\Scripts\pytest` | nothing (fake AI, temp DB) |
| Browser end-to-end | `cd frontend; npm run test:e2e` | ports 8000 and 5173 free; first time `npx playwright install chromium` |
| HTML report | `npm run test:e2e:report` | |
| Watch it run | `npm run test:e2e:ui`, or `$env:E2E_SLOWMO=800` for slow-motion | |
| AI quality (real model) | `cd backend; python -m tests.evals.run` | AI key; on demand |

Playwright starts its own backend and refuses to run if something is already
on port 8000. Stop your dev backend first. Pytest was not re-run when this guide was written.

---

## 2. Quick smoke test (about 30 minutes, manual)

1. `owner` creates a request (New Assessment, fill every field, submit). Status is Submitted, shown on the Dashboard.
2. On the Intake step the owner clicks **Confirm extracted information**, then **Complete Intake & Start Evidence Collection**.
3. `analyst` opens it, **Run Risk Identification & Continue**, and rates every applicable factor.
4. Analyst advances through Inherent Risk, Controls, Residual Risk, Human Review.
5. `reviewer` completes the independent challenge review; `owner` clicks **Submit to Manager**.
6. `manager` signs off the challenge review in **Approvals** and approves.
7. `committee` votes, then records the decision (**Approve with Conditions** needs a condition).
8. Action items appear from the conditions; evidence is attached and closure approved.
9. **Audit History** shows every step; the audit export and explanation reflect it.

Full per-stage manual plan with API paths: `docs/E2E_TEST_PLAN.md`.

---

## 3. Test cases covered by Playwright (31)

Status column: ✅ passed in the 2026-10-03 run. IDs are `PW-nn` in run order.

### Intake: `create-assessment.spec.ts`

| ID | Test case | Steps | Expected | Req | Status |
|---|---|---|---|---|---|
| PW-09 | Submit a full request | As owner: New Assessment, expand all, fill every field, **Continue to Profile Confirm** | Form closes; request opens; listed on Dashboard with change type and "Unrated" risk | R1.1, R1.4 | ✅ |
| PW-10 | Mandatory-field validation | Fill only the title, submit | Error beside the field, focus on first invalid field, nothing created (API search returns 0) | R1.4 | ✅ |
| PW-11 | Save draft | Fill only the title, **Save Draft** | Listed as Draft | R1.3 | ✅ |

### Workflow gates: `status-workflow.spec.ts`

| ID | Test case | Steps | Expected | Req | Status |
|---|---|---|---|---|---|
| PW-29 | Draft to risk identification | Submit request, complete intake, try risk identification unconfirmed, confirm profile, run again, reopen from list | Unconfirmed profile is refused; after confirm, stage is Risk Identification; reopen shows the true stage | R3.3, R14.1 | ✅ |
| PW-30 | Rating gate | Analyst confirms risk identification with unrated factors, then rates all and confirms | Refused with "must be rated"; after rating a progress dialog shows 8 stages and Inherent Risk begins | R6.2, R14.1 | ✅ |
| PW-31 | Business user cannot rate | Owner clicks Rate Factor, Save Rating | "can rate risk factors" error; ratings stay empty | R14.2 | ✅ |

### AI analysis and scoring: `analyze-assessment.spec.ts`

| ID | Test case | Steps | Expected | Req | Status |
|---|---|---|---|---|---|
| PW-03 | Risk identification and score | Analyst runs risk identification, views factors, rates them | Applies / Not applicable, rationale, misuse, verified evidence; nothing scored until rated; official score and band appear | R4.1-R4.4, R6.2-R6.5 | ✅ |
| PW-04 | Summary updates after rating | Rate a factor | Overall inherent risk sidebar refreshes without reload | R6.5 | ✅ |
| PW-05 | Analyze from dashboard | Click Analyze on dashboard row | Analysis starts, status moves on | R4 | ✅ |
| PW-06 | AI outage fallback | Intake containing `SIMULATE_AI_OUTAGE` | Labelled rules-only (provisional) result, acknowledge, progress | R4, NFR reliability | ✅ |
| PW-07 | Unrated is not low | View unrated factors | Shown as UNRATED, never green LOW / 0 | R6.7 | ✅ |
| PW-08 | Calculator never self-logs | Open scoring calculator after identification | No entry added to Audit History on its own | R16.1 | ✅ |

### Evidence and traceability: `evidence-traceability.spec.ts`

| ID | Test case | Steps | Expected | Req | Status |
|---|---|---|---|---|---|
| PW-12 | Expired document gate; provenance | Upload expired document, try risk identification, record decision with reason, view Intake provenance and history | Refused until decided; "Used as evidence by…"; provenance shows source file and field; history shows confirmation and v1 | R2.4, R2.6, R3.4 | ✅ |
| PW-13 | Rule-required factors, indicator edits | Analyst opens factors, edits indicators | Rule trigger shown as pending validation; save needs a reason; override ledger row written; evidence categories counted | R4.2, R5.4 | ✅ |

### Access control: `access-control.spec.ts`

| ID | Test case | Steps | Expected | Req | Status |
|---|---|---|---|---|---|
| PW-01 | Confidential document masked | Owner uploads confidential file with card number; manager views | Owner can open original; manager gets 403, masked text, disabled Download; card number never shown; denial audited | R15.3, R15.5, NFR masking | ✅ |
| PW-02 | Auditor read-only | Create auditor, sign in, attempt writes | Read-only banner, no New Assessment, create/advance/confirm all return 403 | R15.1 | ✅ |

### Governance records: `governance-records.spec.ts`

| ID | Test case | Steps | Expected | Req | Status |
|---|---|---|---|---|---|
| PW-14 | Challenge review sign-off | Manager approves before sign-off, then signs off without and with summary | Approve refused; summary required; after sign-off approval goes through | R11 | ✅ |
| PW-15 | Re-cast vote kept | Committee votes Approve, then Dissent | Reason required; v1 superseded, v2 current, both in history | R12.6 | ✅ |
| PW-16 | Override needs independent review | Analyst proposes override; manager confirms | Proposer cannot confirm; confirmed with note; calculated value unchanged | R6.7, R10.2-R10.4 | ✅ |
| PW-17 | Control edit versioned | Edit control owner, then unmap | Reason required; History v2 shows old to new; revisions EDIT then UNMAP | R7.2, R7.3 | ✅ |

### Segregation of duties: `sod-governance.spec.ts`

| ID | Test case | Steps | Expected | Req | Status |
|---|---|---|---|---|---|
| PW-24 | SoD exception approval | Analyst requests exception; tries to approve; head approves | Validation messages; self and affected-manager approval refused; independent approval recorded | R15.2 | ✅ |
| PW-25 | Expired exception | Approve a 25-second exception, wait | Works while active; 403 after expiry; status EXPIRED (this test waits about 27 s) | R15.2 | ✅ |
| PW-26 | Same-case admin blocked | Dual-role admin votes, then tries admin actions | Assign and config changes refused with 403 | R15.2 | ✅ |
| PW-27 | Committee readiness | Propose material override; manager tries approve; reviewer completes review; head approves override; manager signs off | Blocking reasons listed; cleared only when all done | R11, R12 | ✅ |

### Reassessment: `reassessment.spec.ts` (runs in order)

| ID | Test case | Steps | Expected | Req | Status |
|---|---|---|---|---|---|
| PW-18 | Propose change | Owner proposes new country on approved assessment | Committee cannot propose; parent "Under reassessment, stays in force"; trigger recorded | R18.1 | ✅ |
| PW-19 | Reused fields and compare | Analyst opens the reassessment | Reused fields with source; changed field not reused; comparison shown | R18.2, R18.3 | ✅ |
| PW-20 | Supersede parent | Approve reassessment | Parent marked Superseded, record unchanged, no further proposals | R18.4 | ✅ |

### Retention and legal hold: `retention.spec.ts` (runs in order)

| ID | Test case | Steps | Expected | Req | Status |
|---|---|---|---|---|---|
| PW-21 | Versioned retention policy | Admin proposes 3000 days; tries self-approve; governance owner approves | Provisional banner; justification of 20+ characters; self-approval refused; v1 kept as Superseded | R16.4 | ✅ |
| PW-22 | Legal hold | Admin places hold; tries release; compliance manager releases | Hold blocks eligibility; placer cannot release; history shows both | R16.4 | ✅ |
| PW-23 | Auditor report scope | Auditor opens report | Read-only; entity scope respected; analyst and manager refused | R16.4, R15 | ✅ |

### Source library: `source-library.spec.ts`

| ID | Test case | Steps | Expected | Req | Status |
|---|---|---|---|---|---|
| PW-28 | Policy source upload | Admin uploads `.exe` (refused), then a policy `.txt`, saves draft, downloads, approves | Clear refusal; fields prefilled; download byte-identical; approved and searchable | R5.1 | ✅ |

---

## 4. Not covered by Playwright (test manually or via pytest)

Detailed steps for every item are in `docs/E2E_TEST_PLAN.md`. Pytest covers some
of the API behaviour (scoring, workflow, permissions sweep); `docs/TESTING.md` lists what.

| Area | Gap | Manual reference |
|---|---|---|
| Intake | Prefill from uploaded document, duplicate warning, all 9 change types, dynamic fields | Stage 1 |
| Documents | Upload categories and metadata, versioning/replace, evidence-gap checklist | Stage 2 |
| Intake validation | Contradiction detection, correcting a confirmed field with a reason (UI) | Stage 3 |
| Risk factors | Add manual factor, re-analysis keeps it, exclude with reason | R4.5 |
| Methodology | Admin versions, weights, thresholds | Stage 5 |
| Inherent risk | Critical-factor escalation, calculated-result override, performance timing | R6.6, R6.7 |
| Controls | Effectiveness levels, no-evidence forced UNVERIFIED, gap summary, control conditions | Stage 7 |
| Residual risk | Score changes with controls and versions | Stage 8 |
| Draft | Generate, uncertainty list, versions, accept | Stage 9 |
| Analyst review | Section comments, request more information from owner | Stage 10 |
| Challenge | Trigger configuration, overlooked category / unsupported conclusion findings | R11.1-R11.5 |
| Committee | Decision outcomes (Approve, Conditions, Defer, Reject), rationale and conditions required, read-only after decision, remediation loop | Stage 12 |
| Action items | Creation from conditions, escalation, closure with evidence | Stage 13 |
| Workflow | SLA/overdue, assignment, work queue, reason on every transition | R14.3-R14.5 |
| Authentication | Wrong password, no-token 401, menu per role, user admin audit events | Stage 15 |
| Audit | Full trail, explain, audit export, soft delete (only the legal-hold part is covered) | Stage 16 |
| Reporting | Operational, risk, governance and AI-evaluation reports | Stage 17 |
| Delegation | Manager and committee delegation (AW.7); pytest `test_aw7_delegation.py` exists | Approval Workflow |
| Non-functional | Security headers, file encryption, async upload progress, retry, backups and restore, keyboard accessibility, greyscale badges, pagination | Stage 19 |
| Browsers/devices | Only Chromium desktop is run | |

---

## 5. Recording results

For each test ID record Pass/Fail, build or commit, tester, date, assessment ID and,
for failures, a screenshot and the API response. Playwright keeps traces, screenshots
and video for failures in `frontend/test-results/` and an HTML report in `frontend/playwright-report/`.
