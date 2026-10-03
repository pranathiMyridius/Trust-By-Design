# End-to-End Test Plan: Risk Assessment Workbench

This plan tests every requirement from intake (Stage 1) through the non-functional requirements (Stage 19), in the order an assessment moves through the product. Each test gives the **steps** and the **expected result**. API paths are shown so a tester can check the same behaviour through Swagger (`http://localhost:8000/docs`) when the UI hides a detail.

---

## 0. Setup (do this once)

### 0.1 Environment
1. Copy `backend/.env.example` to `backend/.env`. Set `JWT_SECRET`, `FILE_ENCRYPTION_KEY`, `ADMIN_BOOTSTRAP_PASSWORD` and `OPENROUTER_API_KEY` (the AI steps need this key).
2. Use a **fresh database** so results can be repeated. Either point `DATABASE_URL` at a new SQLite file, or run `docker compose up -d` for Postgres.
3. Run the migrations from `backend/`, oldest first: `migrate_*.py`, ending with `migrate_stage19_nfr.py`.
4. Start the backend from `backend/`: `uvicorn app.main:app --reload --port 8000`.
5. Start the frontend: `npm --prefix frontend run dev`, then open `http://localhost:5173`.
6. Run the automated checks as a baseline. They use a throwaway database:
   - `python test_risk_engine.py`
   - `python test_stage14_workflow.py`
   - `python test_stage17_reporting.py`
   - `python test_stage19_nfr.py`

### 0.2 Test users
Sign in as the bootstrap admin, open **Users**, and create one user per role:

| Persona | Role | Used for |
|---|---|---|
| `bu1` | BUSINESS_USER (reports to `mgr1`) | Submits requests |
| `bu2` | BUSINESS_USER | Visibility and access checks |
| `analyst1` | FCRM_ANALYST | Review, ratings, controls, overrides |
| `mgr1` | MANAGER | Manager approval, challenge triggers |
| `mgr2` | MANAGER | Separation-of-duties checks |
| `cm1`, `cm2` | COMMITTEE_MEMBER | Committee votes and decision |
| `admin` | ADMIN | Configuration, system health |

### 0.3 Test data
Use the sample intake documents in `backend/uploaded_files/`, for example `GlobalSpeed_Pay_Intake_Document.docx`, `SwiftSend_P2P_Intake_Document.docx`, `CryptoBridge_DigitalAssets_Intake_Document.docx` and `AgentPay_AI_Fully_Populated_Intake_Document.docx`. You also need:
- a document containing a **fake card number, IBAN, e-mail and phone number** (for the masking tests);
- a **corrupt or empty** file (for the failure and retry tests);
- a file **larger than `MAX_UPLOAD_MB`**.

---

## Golden path (smoke test, about 30 minutes)

Run this first. If it passes, move on to the detailed tests.

1. `bu1` creates a request from `GlobalSpeed_Pay_Intake_Document.docx` and submits it. → Status is **INTAKE**, and it appears in the analyst's work queue.
2. `analyst1` works through Evidence → Risk Identification → Inherent Risk → Controls → Residual Risk → Human Review, rating and confirming each stage.
3. `analyst1` generates and accepts the assessment draft. `bu1` (the owner, who must have `mgr1` set as their manager) clicks **Submit to Manager**.
4. `mgr1` approves.
5. `cm1` and `cm2` vote, then `cm1` records **Approve with Conditions**.
6. Action items are created from the conditions. `bu1` attaches evidence and requests closure, and the approver closes them.
7. The analyst closes the assessment (status **CLOSED**). The audit export, the explanation and the reports all reflect it.

---

## Stage 1: Assessment Request Intake

| ID | Steps | Expected |
|---|---|---|
| **R1.1** Capture request fields | As `bu1`, open **New Assessment**. Fill in every field: product/service name, business owner, legal entity, business unit, change type, description, launch date, and so on. Save it, then reopen it. | Every field keeps its value. `GET /api/assessments/{id}` returns them all. |
| **R1.1** Dynamic fields | Change the **change type** several times. | The conditional fields for each type appear and disappear. |
| **R1.1** Prefill from a document | Upload `SwiftSend_P2P_Intake_Document.docx` in the "create from document" flow (`POST /api/assessments/analyze-document`). | The form is prefilled from the document, and you can edit it before saving. |
| **R1.2** Change types | Open the change-type dropdown. | Nine canonical change types are listed. Each one saves and reloads correctly. |
| **R1.3** Save as draft | Fill in only the product name, then click **Save draft**. Log out, log back in, and reopen the request. | It is saved with `is_draft=true` and the missing fields blank. You can finish and save it later. |
| **R1.4** Mandatory-field validation | Try to **Submit** a draft that is missing required fields. | Submission is blocked (a 422 from the API). Each missing field shows an error next to it, an error summary appears, and focus moves to the first invalid field. |
| **R1.4** Valid submit | Complete every required field, then submit. | `is_draft=false`, and the status moves to INTAKE or Submitted. |
| **R1.5** Submitter stamp | Look at the submitted request's details. | `submitted_by` is `bu1` and `submitted_at` is set. Both are null while the request is a draft. If the document names a submitter, that name is shown separately. |
| Duplicates | Create a second request with the same product name. | `GET /api/assessments/check-duplicates` warns about it, and the UI shows the warning. |

## Stage 2: Document and Evidence Management

| ID | Steps | Expected |
|---|---|---|
| **R2.1** Document categories | On an assessment, upload evidence under each category in the dropdown. | Every category is accepted and listed under **Documents**. |
| **R2.2** Classification metadata | When uploading, set the document type, confidentiality (PUBLIC, INTERNAL, CONFIDENTIAL, RESTRICTED), owner and date. | The metadata is saved and shown. `GET /{id}/documents` returns it. |
| **R2.3** Human confirmation of extracted data | Open **Intelligence** (the extracted profile), edit a field, then click **Confirm**. | The field is marked confirmed with who confirmed it and when. The AI's original value is kept alongside the corrected one. |
| **R2.4** Provenance | Look at the extracted fields. | Each field shows the document it was extracted from. |
| **R2.5** Evidence gaps | On a new assessment that has few documents, open **Evidence gaps** (`GET /{id}/evidence-gaps`). Then upload a missing item. | A checklist of missing evidence is shown. After the upload, that item is no longer flagged as missing. |
| **R2.6** Versioning | Upload a new version of an existing document using "replace". | The new document is v2 and links to v1. v1 is kept and can still be downloaded. |
| Download | Download a document (`GET /api/assessments/documents/{id}/file`). | The original file is returned, decrypted if it was encrypted. |

## Stage 3: Intake Validation and Structuring

| ID | Steps | Expected |
|---|---|---|
| **R3.1** Structured profile | After a document is analysed, open **Intelligence**. | Structured business and product profile fields are populated. |
| **R3.2** Contradictions | Upload a document that contradicts the intake form, for example a different launch date or different geographies. Run `GET /{id}/consistency-check`. | Each contradiction is listed with both values and its source. |
| **R3.3** Validated profile state | Confirm the profile. | The profile shows as validated. An assessment without a profile shows "not yet validated" instead of an error. |
| **R3.4** Correcting a validated field | Edit a field that has already been confirmed, first **without** a reason and then with one. | Without a reason, the change is rejected. With a reason, it is accepted, and who, when, the old value, the new value and the reason are all audited. |
| Similar assessments | Open **Similar assessments**. | Earlier assessments of related products are listed. |

## Stage 4: Risk Factor Identification

| ID | Steps | Expected |
|---|---|---|
| **R4.1** All 10 categories | Run the analysis (`POST /{id}/analyze`), then open **Risk Factors**. | All 10 risk categories are present, each marked applicable or not applicable. None is left out silently. |
| **R4.2** Indicators | Open a factor. | Indicator codes from the list of 12 are attached, and you can edit them. |
| **R4.3** Rationale | Look at each factor, including the not-applicable ones. | Each factor has a rationale explaining why it applies or doesn't. |
| **R4.4** Misuse scenario | Look at an applicable factor. | It has a free-text description of how the change could be misused. |
| **R4.5** Manual factor | As `analyst1`, add a factor the AI didn't identify. Then run the analysis again. | The new factor has source MANUAL and **survives** the re-analysis. |
| **R4.5** Exclude with a reason | Exclude a factor, first with no reason and then with one. | Without a reason, the exclusion is rejected. With a reason, the factor is excluded and the exclusion is audited. |

## Stage 5: Risk Methodology (configurable)

| Steps | Expected |
|---|---|
| As `admin`, list the methodologies (`GET /api/risk-methodologies`). | The active, versioned methodology is shown. |
| Create a new version with different category weights and thresholds, then activate it. | A new version is created, and the old version is kept. |
| Recalculate the inherent risk on a new assessment. | The new weights and thresholds are used, with no code change needed. |
| Try to change a methodology as `analyst1` or `bu1`. | Access is denied (403). |

## Stage 6: Inherent Risk Assessment

| ID | Steps | Expected |
|---|---|---|
| **R6.1** Configurable method | Covered by the Stage 5 tests. | |
| **R6.2 / R6.3** Human rating | Set likelihood and impact on each applicable factor (`PATCH …/risk-factors/{rf}/rating`). | The factor is rated by a person. AI-sourced factors start unrated, and the AI score is never used as the final rating. |
| **R6.4** Weighting | Rate two factors in categories with different weights. | The overall score is the weighted combination, and the numbers match the formula. |
| **R6.5** Transparency | Open **Inherent Risk** (`GET /{id}/inherent-risk`). | The inputs, weights, method, thresholds, final score and band are all shown. |
| **R6.6** Mandatory escalation | Give one factor a CRITICAL rating while the others are low. | The overall band is forced up to at least the configured minimum, and a flag shows that the escalation rule fired. A low average can't hide the critical factor. |
| **R6.7** Provisional result | Leave one applicable factor unrated. | The result is marked **provisional**. |
| **R6.7** Override | As `analyst1`, override the calculated result, first without a reason and then with one. | Without a reason, the override is rejected. With a reason, the override is stored next to the calculated value and audited. |
| Performance | Rate a factor and check the `X-Response-Time-Ms` header. | The time is under `RISK_CALC_TARGET_MS` (3000 ms). |

## Stage 7: Control Assessment

| ID | Steps | Expected |
|---|---|---|
| **R7.1 / R7.2** Map a control | Map a control from the library to an identified risk (`POST /{id}/controls`). | The control is linked to that risk. |
| **R7.3** Metadata | Edit the control's owner, frequency and type. | The changes are saved. |
| **R7.4** Design adequacy | Record an assessment of DESIGN_ADEQUATE, then another of DESIGN_INADEQUATE. | Each is saved, and the history is kept. |
| **R7.5** Operating effectiveness | Record EFFECTIVE, then PARTIALLY_EFFECTIVE, then INEFFECTIVE. | The residual-risk reduction changes to match each result. |
| **R7.6** No evidence | Mark a control EFFECTIVE without attaching any evidence. | It is forced to **UNVERIFIED** and gives no reduction. |
| **R7.6** Gaps | Leave one risk with no control. On another control, mark "does not cover the full risk" and "depends on unavailable data". | `GET /{id}/control-summary` lists all three gaps. |
| **R7.7** Control conditions | Add a required enhancement to a control, then complete it. | It is tracked from open to complete, and it appears under **Control conditions**. |

## Stage 8: Residual Risk

| Steps | Expected |
|---|---|
| With effective controls in place, open the residual-risk result. | Residual score = inherent score minus control reduction. The band uses the residual thresholds (defaults: MEDIUM 30, HIGH 60, CRITICAL 80, unless the methodology overrides them). |
| Change a control to INEFFECTIVE or UNVERIFIED. | The residual score goes back up. |
| Override the inherent result (Stage 6), then look at the residual result. | The residual score reflects the override. |
| Open the risk-results history (`GET /{id}/risk-results`). | Each recalculation is kept as its own version. |

## Stage 9: Assessment Draft Generation

| ID | Steps | Expected |
|---|---|---|
| **R9.1** Auto-generated draft | Advance the assessment to HUMAN_REVIEW. | A structured draft is generated automatically, with every section filled in. `POST /{id}/draft/generate` can regenerate it. |
| **R9.2** Uncertainty | Look at the draft. | Anything the AI couldn't resolve is listed as an open question or uncertainty, not presented as fact. |
| **R9.3** Version history | Edit the draft, then regenerate it. | `GET /{id}/draft/versions` shows the original AI content, your edit and the regenerated version. |
| **R9.4** Acceptance | As `analyst1`, click **Accept**. | Who accepted the draft and when are recorded, along with the model or provenance of the generation. |

## Stage 10: FCRM Analyst Review

| ID | Steps | Expected |
|---|---|---|
| **R10.1** Section comments | Add comments linked to different sections, such as RISK_FACTORS and CONTROL_MAPPINGS. | Each comment appears on its section. |
| **R10.2 / R10.3** Overrides need a reason | Change an AI-generated value, such as a category or rating (`POST /{id}/overrides`), first without a reason. | Without a reason, the change is rejected. With a reason, the override is logged. |
| **R10.4** Comparison | Open the **Overrides** list. | Each override shows the AI value, the analyst's value, the reason, and who made it and when. |
| **R10.5** Request more information | As `analyst1`, click **Request information** and choose `bu1`. Then, as `bu1`, provide the information. | The assessment returns to the business owner, who is notified in their work queue. After `bu1` responds, it comes back to the analyst, and both steps are audited. |
| **R10.6** Comment linked to an entity | Link a comment to a specific risk factor or control. | The comment opens against that item. |

## Stage 11: Challenge Review

| ID | Steps | Expected |
|---|---|---|
| **R11.1** Configure triggers | As `mgr1` or `admin`, edit the challenge triggers (`PATCH /api/challenge-triggers`). Try the same as `analyst1`. | The manager's change is saved. The analyst gets a 403. |
| **R11.2** Overlooked categories | Mark a category that the evidence clearly supports as not applicable. | A finding is raised: "possibly overlooked category". |
| **R11.3** Unsupported conclusions | Give a factor a low rating with no evidence behind it. | A finding is raised: "conclusion without adequate evidence". |
| **R11.4** Contradictions | Keep a contradiction from Stage 3 unresolved. | A finding is raised: "contradiction". |
| **R11.5** Findings recorded | Open the challenge review (`GET …/challenge-review`). | Every finding is listed with its severity and status. |
| **R11.6** Blocking | Leave a HIGH or CRITICAL finding OPEN and try to submit to committee. | Submission is **blocked**. |
| **R11.6** Resolve or accept | Resolve one finding. Accept another, first without a justification and then with one. | Without a justification, acceptance is rejected. With one, it is accepted and recorded with who and when. The block is lifted. |

## Stage 12: Risk Committee Decision

| ID | Steps | Expected |
|---|---|---|
| Manager step | As `bu1` (the owner), click **Submit to Manager**. As `mgr1`, approve. | The status moves to READY_FOR_COMMITTEE. |
| **R12.2** Decision package | As `cm1`, open **Approvals** and then the decision package. | It contains the profile, risks, controls, inherent and residual scores, findings, the draft and the overrides. |
| **R12.6** Votes | `cm1` votes approve and `cm2` votes reject, each with a comment. | Each vote is stored separately and is distinct from the final decision. |
| **R12.1 / R12.3** Decision | Record the decision, first with no rationale and then with one. | Without a rationale, the decision is rejected. With one, it is saved. Test each outcome on a separate assessment: Approve, Approve with Conditions, Defer, and Reject. |
| **R12.4** Conditions | Choose **Approve with Conditions** with no conditions, then add one with an owner and a due date. | With no conditions, the decision is rejected. The condition is saved as its own tracked row. |
| **R12.4** Read-only after decision | Try to edit the assessment after the decision. | It is read-only. Changes have to go through `POST /{id}/amend`. |
| **R12.5** Committee only | Try `committee-decision` as `analyst1` or `mgr1`. | Access is denied (403). |
| Remediation loop | After a Reject decision, a committee member sends the assessment to remediation. | The status becomes REMEDIATION, and the owner or an analyst restarts it at INTAKE. |
| Reject | Record **Reject**. | The status becomes REJECTED, which is final. |

## Stage 13: Conditions and Remediation Tracking

| ID | Steps | Expected |
|---|---|---|
| **R13.1** Automatic action items | After an Approve with Conditions decision, open **Action items**. | Each condition has automatically become an action item. |
| **R13.2** Fields | Open an action item. | It has an owner, due date, status and source. |
| **R13.3** Escalation | Set a due date in the past, then run `POST /api/workflow/escalations/run` (or wait for the scheduled sweep). | The item is marked escalated, with the escalation level and who it was escalated to. |
| **R13.4** Closure needs evidence | Request closure with no evidence. | The request is rejected. |
| **R13.4** Closure approval | Attach evidence and request closure. Then have the approver reject it, and afterwards approve it. | When rejected, the item reopens with the reason. When approved, it is closed, and each step is audited. |
| **R13.5** Links | Open an action item. | It links to its risk, control or committee condition. |

## Stage 14: Workflow and Status Management

| ID | Steps | Expected |
|---|---|---|
| **R14.1** Permitted transitions | Try to skip a stage, for example going from INTAKE straight to COMMITTEE_DECISION. | The move is rejected. `GET /api/workflow/config` lists the allowed moves. |
| **R14.2** Role-based actions | Try each action as the wrong role, for example `bu1` advancing a stage or `bu2` editing `bu1`'s request. | Access is denied (403). Only the owner or a reviewer can edit. |
| **R14.3** Owner and next action | Open the **Workflow** panel. Assign the assessment to `analyst1`. | The current owner, team and next action are shown. The assessment appears in `analyst1`'s **Work Queue**. |
| **R14.4** SLA and due dates | Set a target date and let a stage go past its SLA, then run the escalations. | The assessment shows as overdue and escalated, and the work queue flags it. |
| **R14.5** Reason on every transition | Withdraw, submit, advance and close the assessment, each time without a comment and then with one. | Every transition needs a reason. The history shows every transition, with who made it and when. |

## Stage 15: Authentication, Roles and Access

| Steps | Expected |
|---|---|
| Log in with a wrong password, then with the right one. | The wrong password gives a clear error. The right one opens the app. `GET /api/auth/me` returns the user's role. |
| Call any `/api` endpoint with no token, for example `curl http://localhost:8000/api/assessments`. | 401 Unauthorized. |
| As `bu2`, open `bu1`'s assessment by URL or ID. | Access is denied, and the assessment doesn't appear in `bu2`'s list. |
| Check the navigation menu for each role. | Admin pages (Users and System Health) are visible only to `admin`. |
| As `admin`, create a user and deactivate one. | `USER_CREATED` and `USER_UPDATED` audit events are written, showing the changed fields. |

## Stage 16: Audit Trail and Explainability

| ID | Steps | Expected |
|---|---|---|
| **R16.1** Full decision path | Open **Audit History** for the assessment you finished in the golden path. | Every event is listed in order, from intake to closure. |
| **R16.1** No hard delete | Delete a completed assessment, then try to find it through the API. | The assessment is soft-deleted and hidden from the lists, but its rows still exist. The deletion itself is audited. |
| **R16.2** Rating explanation | `GET /api/assessments/{id}/explain`. | A plain explanation of how each rating was reached. |
| **R16.3** Export | `GET /api/assessments/{id}/audit-export`. | A complete package with every related record. |
| **R16.4** Legal hold | Place a legal hold, then try to delete the assessment. Lift the hold. | While the hold is on, deletion is blocked. Placing and lifting the hold are both audited. |
| Retention | As `admin`, view and change `/api/retention-policy`. | The change is saved and audited. Non-admins get a 403. |

## Stage 17: Reporting and Monitoring

| ID | Steps | Expected |
|---|---|---|
| **R17.1** Operational | Open **Reports → Operational**. | Volumes, counts per stage, ageing and SLA breaches match the data you created. A manager sees their direct reports' submissions. |
| **R17.2** Risk | Open **Risk** and **High-risk portfolio**. | The breakdown by band and category is correct, and the high and critical assessments are listed. |
| **R17.3** Governance | Open **Governance**. | Decisions, overrides, conditions and overdue actions are all counted. |
| **R17.4** AI evaluation | Open **AI evaluation**. | Processing time, model usage and cost are shown, along with how often analysts overrode the AI. Failed AI calls are marked as fallbacks. |

## Approval Workflow (AW)

A project requirement outside the brief's stage numbering (AW.1-AW.7),
originally labelled R17. Automated checks for AW.7: `backend/test_aw7_delegation.py`.

| ID | Steps | Expected |
|---|---|---|
| **AW.2** Separation of duties | Have `bu1` try to approve their own submission. Have `mgr1` approve an assessment, then try to vote on it as a committee member. | Both attempts are blocked. |
| **AW.3 / AW.4** Order | Try to record the committee decision before the manager has approved. | The decision is blocked. |
| **AW.5** Comment visibility | `analyst1` posts an internal comment. `bu1` views the assessment. | `bu1` can't see the internal comment. |
| **AW.7** Create a delegation | As `mgr1`, open **Delegations**. Delegate to `mgr2` for all approvals, from now for 7 days, with a reason. | The delegation is listed as **Active**. Only other active managers are offered as delegates. |
| **AW.7** Delegate approves | `bu1` submits to `mgr1`. As `mgr2`, open **Approvals**. | The item appears, marked "acting as delegate for mgr1". `mgr2` can approve, return or reject it. |
| **AW.7** Record names both | After `mgr2` decides, open **Audit History**. | The actor reads "mgr2 (delegate for mgr1, delegation #n)". |
| **AW.7** Scope | As `mgr1`, delegate one assessment only. Submit a second assessment to `mgr1`. | `mgr2` can act on the delegated one only; the other is not visible to them. |
| **AW.7** Expiry and revocation | As `mgr1`, revoke the delegation (or let it reach its end time). | `mgr2` loses access immediately and is told the delegation was revoked or expired. |
| **AW.7** Committee sign-off | As `cm1`, delegate committee sign-off to `mgr2`. As `mgr2`, vote and decide on a committee-ready assessment. | The vote sits in `cm1`'s seat, "cast by delegate mgr2". `mgr2` cannot sign off an assessment they approved as manager. |

## Stage 18: Reassessment and Change Management

| ID | Steps | Expected |
|---|---|---|
| **R18.1** Expiry and periodic triggers | On an approved assessment, set the review date or expiry into the past. Run `GET …/reassessment/check-triggers`. | An EXPIRY or PERIODIC_REVIEW trigger fires. |
| **R18.1** Change triggers | Propose a change, such as new geographies or a major product change (`POST …/reassessment/propose-change`). | The matching field-level triggers are detected. You can also flag a trigger by hand, and then acknowledge or dismiss it. |
| **R18.2** Comparison | After the reassessment is completed, open `GET …/reassessment/compare`. | The old and new risks, controls, scores and conditions are shown side by side. |
| **R18.3** Reuse prior information | Look at the new reassessment's intake. | The unchanged fields are carried forward and marked as reused. The changed fields are marked as needing review. |
| **R18.4** Review validity | Approve the reassessment. | A new review-validity period starts, and the parent and child assessments are linked. |

## Stage 19: Non-Functional Requirements

For the detail behind each check, see `docs/NON_FUNCTIONAL_REQUIREMENTS.md`. `python test_stage19_nfr.py` automates most of them.

**Security**
1. Check the response headers of any API call in DevTools (`nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy`, `Cache-Control: no-store`). With `FORCE_HTTPS=true`, HTTP is redirected and HSTS is sent.
2. Upload a file, then open the stored copy in `backend/uploaded_files/`. → It is encrypted (unreadable). Downloading it through the app returns the original.
3. Call about 5 random endpoints without a token. → 401. As `bu2`, request `bu1`'s assessment ID. → 403 or 404.
4. As `admin`, make any change. → An `ADMIN_ACTION` audit event is written, and it doesn't contain the request body.
5. Search the repository for secrets. There should be no hard-coded JWT secret or admin password. Start the app with no `ADMIN_BOOTSTRAP_PASSWORD`. → A one-time password is printed to the console.
6. Upload the document containing the fake card number, IBAN, e-mail and phone number, and mark it CONFIDENTIAL. View it as `bu2` or `cm1`. → The sensitive values are masked. The owner, analysts and admin see the full values. Also check that the AI request log contains only masked values.

**Performance**
7. Look at the **System Health** page. → p50 and p95 are shown per route, and routes slower than `PAGE_LOAD_TARGET_MS` are highlighted. Responses carry the `Server-Timing` and `X-Response-Time-Ms` headers.
8. Upload a large document. → The upload returns straight away and a progress bar climbs from 0 to 100% with the current step. The UI stays usable. Also try `analyze-async`.
9. Upload a file larger than `MAX_UPLOAD_MB`. → It is rejected with a clear message.

**Reliability**
10. Upload the corrupt file. → The document row is kept, and the job shows FAILED with a plain-language error. The technical detail is behind a toggle, and a `PROCESSING_FAILED` event is audited.
11. Click **Retry** (`POST /api/processing-jobs/{id}/retry`). → It retries up to `PROCESSING_MAX_ATTEMPTS` times.
12. Stop the backend while a job is running, then restart it. → The job is marked failed and can be retried.
13. Upload an image-only PDF with no readable text. → The job shows as **PARTIAL** or incomplete with the reasons listed, never as complete.
14. Run `create-with-document` with one good file and one bad file. → The assessment and the good file are kept.

**Availability and recovery**
15. As `admin`, open **System Health → Backups** and create a backup, then verify it. → A checksummed manifest is created and verification passes. The recovery status shows whether the latest backup is within the RPO.
16. Run `python restore_backup.py` on a copy of the environment. → A safety backup is taken first, the data is restored, and the integrity check passes.

**Accessibility and usability**
17. Use only the keyboard. Tab through the app, check the skip link and visible focus, move through rows and tabs, and close dialogs with Escape.
18. Check the risk badges. → Each shows a text label and a shape, not just a colour. Test in greyscale.
19. On a long assessment, check the sticky section navigator and back-to-top button. Leave the page and come back. → Your scroll position is restored.
20. Optional: run axe or Lighthouse accessibility checks on the main pages.

**Scalability**
21. Create assessments across several legal entities and business units. Filter by `legal_entity`, `business_unit`, `status` and `search`. `GET /api/assessments/org-units` should list the options.
22. Page through the results with `limit` and `offset`. → `X-Total-Count` is correct.

**Explainability**
23. `GET /api/assessments/{id}/explain/statements`. → Each item is labelled FACT, ASSUMPTION, RECOMMENDATION or DECISION. Unrated AI risks are ASSUMPTIONs, and only named people make DECISIONs. The UI shows this in the Explainability panel.
24. Check the AI recommendations. → They are always labelled **ADVISORY**, and any wording like "Approved." is rewritten as "Suggested outcome: …".

---

## Recording results

For each test ID, record **Pass/Fail**, the build or commit, the tester, the date, the assessment ID used, and, for failures, a screenshot and the API response. Rerun the golden path and the four automated scripts after every fix.
