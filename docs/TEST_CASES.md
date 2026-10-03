# Test Cases: Steps, Expected Behaviour, Acceptance Criteria

Companion to `docs/LOCAL_TEST_GUIDE.md` (how to run) and `docs/E2E_TEST_PLAN.md` (longer manual plan).

**Conventions**
- Personas are the seeded test users (`backend/tests/support/e2e_users.json`): owner, analyst, reviewer, manager, head, committee, chair, admin, dualadmin.
- **PW** = automated by Playwright (all 31 passed on 2026-10-03). **LIVE** = verified against the real AI provider. **MANUAL** = run by hand.
- A case is accepted only when **every** acceptance criterion holds.

## 0. Environment for these tests

| Mode | Command | Use for |
|---|---|---|
| Fake AI (repeatable) | `cd backend; .venv\Scripts\python tests\e2e_server.py` then `cd frontend; npm run dev` | PW cases, gates, governance |
| **Real AI (your API key)** | `$env:E2E_REAL_LLM="1"; .venv\Scripts\python tests\e2e_server.py` | LIVE cases. Uses `LLM_PROVIDER` and the key from `backend/.env` |

Both modes use a throwaway SQLite database, never the primary one. In real-AI
mode the Playwright suite is not meaningful (it asserts fixed scores from the fake);
test AI behaviour with the LIVE cases below.

---

## 1. Real-AI cases (LIVE)

### AI-01: Risk identification with the real provider
| | |
|---|---|
| **Preconditions** | Real-AI server running. Owner has created a submitted request (cash remittance Germany to Nigeria/Pakistan, remote onboarding, payout partner) and confirmed the profile; assessment is in Evidence Collection. |
| **Steps** | 1. Sign in as analyst. 2. Open the assessment. 3. Click **Run Risk Identification & Continue**. 4. Review the Risk Factor Categories. |
| **Expected behaviour** | Analysis completes; stage becomes Risk Identification. All 10 categories are listed, each Applies or Not applicable, with rationale and potential misuse. Evidence quotes are marked verified. No factor is scored until the analyst rates it. |
| **Acceptance criteria** | a) Status code 200 and `assessment_mode = ai_assisted`, `ai_status = success`, no degraded reason. b) 10 of 10 categories present. c) Every applicable factor has a rationale. d) Quotes marked verified exist in the intake text. e) Completes in under about 60 s. |
| **Result 2026-10-03** | ✅ LIVE: 200 in 17 s, `ai_assisted`/`success`; 10 categories (9 applicable, 1 not); applicable factors had 1-3 evidence quotes (Control Environment 0) and 0-3 indicators. |

### AI-02: AI outage falls back safely
| | |
|---|---|
| **Steps** | 1. Create a request whose description contains `SIMULATE_AI_OUTAGE` (fake-AI server only). 2. As analyst run risk identification. 3. Click **Acknowledge provisional result**. |
| **Expected behaviour** | A **Provisional analysis** banner appears with the reason; factors are rules-only; the analyst must acknowledge before progressing. |
| **Acceptance criteria** | Mode is rules-only, not `ai_assisted`; progress is blocked until acknowledged; nothing is presented as AI-verified. **PW-06** ✅ |

### AI-03: AI rating suggestions are advisory
| | |
|---|---|
| **Steps** | 1. After AI-01, click **Suggest Ratings with AI**. 2. Do not accept. 3. Check the Inherent Risk panel. 4. Rate a factor manually. |
| **Expected behaviour** | Suggestions appear as proposals; the score stays Not analysed / unrated until a person rates. |
| **Acceptance criteria** | Unrated factors show UNRATED, never LOW or 0; the final rating is the human value; the AI value is kept alongside. (MANUAL) |

### AI-04: Evidence quotes cannot be invented
| | |
|---|---|
| **Steps** | Run AI-01, then open each applicable factor's evidence. |
| **Acceptance criteria** | Each quote appears verbatim in the intake or uploaded documents; indicators that decide an outcome alone (sanctions) appear only when the sentence mentions sanctions; otherwise they are listed under missing information. (MANUAL; unit tests in `test_llm_integration.py`) |

### AI-05: Draft generation
| | |
|---|---|
| **Steps** | Advance an assessment to Human Review; open the draft; edit; regenerate; **Accept**. |
| **Acceptance criteria** | Every section filled; unresolved items listed as open questions; version history shows AI original, edit, regeneration; acceptance records who, when and the model. (MANUAL) |

---

## 2. Intake and workflow (PW)

### TC-01: Submit a complete request (PW-09, R1.1, R1.4)
| | |
|---|---|
| **Preconditions** | Signed in as owner. |
| **Steps** | New Assessment, **Expand all**, fill every field, click **Continue to Profile Confirm**. |
| **Expected behaviour** | Form closes; the new assessment opens; Dashboard lists it with change type NEW_GEOGRAPHY and risk "Unrated". |
| **Acceptance criteria** | Request saved with all fields; status Submitted; visible on Dashboard; risk shows Unrated. |

### TC-02: Mandatory-field validation (PW-10, R1.4)
| **Steps** | Fill only the title; click **Continue to Profile Confirm**. |
|---|---|
| **Expected behaviour** | Error text beside each missing field; focus moves to the first invalid field; form stays open. |
| **Acceptance criteria** | Nothing is created (search returns 0); first invalid field focused; error is announced. |

### TC-03: Save as draft (PW-11, R1.3)
| **Steps** | Fill only the title; **Save Draft**; find it on the Dashboard. |
|---|---|
| **Acceptance criteria** | Row shows Draft; missing fields allowed; draft can be reopened and submitted later. |

### TC-04: Draft to Risk Identification with gates (PW-29, R3.3, R14.1)
| **Steps** | Open the draft; **Submit request**; **Complete Intake & Start Evidence Collection**; click **Run Risk Identification & Continue** before confirming the profile; then Intake step, **Confirm extracted information**; run again; go to Dashboard and reopen. |
|---|---|
| **Expected behaviour** | First attempt refused ("has not been confirmed"); after confirm it proceeds; reopening shows the true stage. |
| **Acceptance criteria** | Status sequence Draft, Submitted, Intake Validation, Risk Assessment in Progress; no stage skipped; no stale stage after reopening. |

### TC-05: Rating gate (PW-30, R6.2)
| **Steps** | Analyst clicks **Confirm Risk Identification & Continue** with unrated factors; rate all; confirm again. |
|---|---|
| **Acceptance criteria** | Refusal says "must be rated" (not "no usable results"); after rating, a progress dialog lists 8 stages; status becomes Inherent Risk Assessment. |

### TC-06: Business user cannot rate (PW-31, R14.2)
| **Steps** | As owner on an analysed assessment click **Rate Factor**, **Save Rating**. |
|---|---|
| **Acceptance criteria** | Error "can rate risk factors"; factor ratings remain empty in the API. |

### TC-07: Score and level after rating (PW-03, PW-04, R6.4-R6.5)
| **Steps** | Analyst rates all applicable factors; open the Inherent Risk panel and the sidebar. |
|---|---|
| **Expected behaviour** | Weighted score and band shown with inputs, weights and thresholds; sidebar and Dashboard update without reload. |
| **Acceptance criteria** | Score matches the methodology formula; band consistent with thresholds; sidebar refreshes on its own. |

### TC-08: Calculator never writes to the audit trail by itself (PW-08)
| **Acceptance criteria** | After risk identification, Audit History gains no entry until a person edits. |

---

## 3. Evidence and traceability (PW)

### TC-09: Expired document must be decided (PW-12, R2.6)
| **Steps** | Upload a document with a past expiry date; click **Run Risk Identification & Continue**; enter a reason; **Use as evidence**; then check Intake provenance and history. |
|---|---|
| **Acceptance criteria** | Refused with "Expired evidence must be acknowledged"; decision needs a reason and shows who; provenance lists source file and field; history shows profile confirmed and Request v1; afterwards identification proceeds. |

### TC-10: Rule-required factors and indicator edits (PW-13, R4.2, R5.4)
| **Steps** | As analyst open a delivery-channel factor; read the rule trigger; **Edit indicators**, tick one; try to save without reason; add reason; save. |
|---|---|
| **Acceptance criteria** | Rule trigger "Remote digital delivery channel" shown as pending business validation; Save disabled until a reason is given; change appears in the override ledger; evidence categories include Direct evidence and Extracted information. |

---

## 4. Access control and governance (PW)

### TC-11: Confidential document is masked (PW-01, R15.3, R15.5)
| **Steps** | Owner uploads a CONFIDENTIAL file containing a card number; manager (reviewing) opens Evidence Collection. |
|---|---|
| **Expected behaviour** | Manager sees the masked text and a disabled Download; card number never shown. |
| **Acceptance criteria** | Owner download 200; manager download 403; denial audited as ACCESS_DENIED. |

### TC-12: Auditor is read-only (PW-02, R15.1)
| **Acceptance criteria** | Read-only banner; no New Assessment button; create, advance and confirm return 403; assessment state unchanged. |

### TC-13: Challenge review sign-off (PW-14, R11)
| **Steps** | Manager opens Approvals; clicks Approve before sign-off; signs off with empty then filled summary; approves. |
|---|---|
| **Acceptance criteria** | Approve refused while unsigned ("cannot move to committee review"); summary required; after sign-off approval succeeds and queue empties. |

### TC-14: Committee votes are append-only (PW-15, R12.6)
| **Steps** | Committee member votes Approve; then Dissent with and without a reason. |
|---|---|
| **Acceptance criteria** | Re-cast needs a reason; history shows APPROVE v1 superseded and DISSENT v2 current with the reason; API history returns both. |

### TC-15: Override needs independent confirmation (PW-16, R6.7, R10.2)
| **Acceptance criteria** | Proposer cannot confirm own override; manager confirms with note; calculated values unchanged; ledger shows both. |

### TC-16: Control edit is versioned (PW-17, R7.2)
| **Acceptance criteria** | Edit needs a reason; History shows v2 with old to new value; unmap needs a reason; revisions are EDIT then UNMAP; nothing deleted. |

### TC-17: Separation-of-duties exception (PW-24 to PW-27)
| **Acceptance criteria** | Requester and affected person's manager cannot approve; independent designated approver can; exception expires at its end time and then authorizes nothing; dual-role admin cannot do same-case administration; committee readiness lists blocking reasons until material override review and challenge sign-off are done. |

---

## 5. Lifecycle (PW)

### TC-18: Reassessment (PW-18 to PW-20, R18)
| **Steps** | Owner proposes a change on an approved assessment; analyst opens the reassessment, views reused fields and **Compare with parent**; reassessment is approved. |
|---|---|
| **Acceptance criteria** | Committee member cannot propose; parent shows "Under reassessment, stays in force" with a trigger; reused fields show source and exclude the changed field; on approval parent shows Superseded, its own status stays APPROVED, and no further proposals are offered. |

### TC-19: Retention policy and legal hold (PW-21 to PW-23, R16.4)
| **Acceptance criteria** | Proposal needs a 20+ character justification; proposer cannot approve; independent governance owner approves and v1 is kept as Superseded; legal hold blocks eligibility, placer cannot release, a different authorized user can; auditor reads the report within scope only; analyst and manager are refused. |

### TC-20: Source library (PW-28, R5.1)
| **Acceptance criteria** | Unsupported file type refused with a clear message; text, title and reference prefilled; original downloads byte for byte; approved source becomes searchable. |

---

## 6. Manual cases not yet automated

| ID | Steps | Expected behaviour / acceptance criteria |
|---|---|---|
| M-01 Committee outcomes (R12) | Record Approve, Approve with Conditions, Defer, Reject on separate assessments, each first without rationale | Rationale required for all; Conditions needs at least one condition with owner and due date; Reject is final; decided assessment is read-only except via amend |
| M-02 Action items (R13) | After a conditional approval open Action items; request closure without then with evidence; set due date in the past and run escalations | One action item per condition with owner, due date, status; closure needs evidence; rejection reopens with reason; overdue item shows escalated |
| M-03 Critical escalation (R6.6) | Rate one factor CRITICAL, others low | Overall band forced to at least the configured minimum with an escalation flag |
| M-04 Controls (R7) | Mark a control EFFECTIVE with no evidence | Becomes UNVERIFIED with no residual reduction; gaps listed in control summary |
| M-05 Manual factor (R4.5) | Add a factor, re-run analysis; exclude another with and without reason | Manual factor survives; exclusion needs a reason and is audited |
| M-06 Delegation (AW.7) | Manager delegates to a second manager; submit an assessment; delegate approves; revoke | Delegate sees item "acting as delegate"; audit names both; access ends on revoke or expiry |
| M-07 Reports (R17) | Open Operational, Risk, Governance, AI evaluation | Figures match data created; failed AI calls shown as fallbacks |
| M-08 Upload failures (NFR) | Upload corrupt file, oversize file, image-only PDF | Clear message; document kept; job FAILED or PARTIAL with reasons; Retry works |
| M-09 Accessibility (NFR) | Keyboard only through the app; check badges in greyscale | Skip link and focus visible; Escape closes dialogs; badges carry text and shape |
| M-10 Security headers (NFR) | Inspect any API response | `nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy`, `Cache-Control: no-store`; requests without a token return 401 |

---

## 7. Results log

| ID | Result | Date | Tester | Build/commit | Evidence |
|---|---|---|---|---|---|
| TC-01 to TC-20, AI-02 (PW-01 to PW-31) | Pass | 2026-10-03 | automated | working tree | `frontend/playwright-report/` |
| AI-01 | Pass | 2026-10-03 | automated script | working tree | 200, ai_assisted/success, 17 s |
| AI-03 to AI-05, M-01 to M-10 | Not run | | | | |
