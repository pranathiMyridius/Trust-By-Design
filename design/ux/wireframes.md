# Wireframes (low-fidelity specification)

These are **textual wireframes** for Stage 3 and for the Figma file (see `ux-decisions.md` §5). Each screen lists the URL route, existing component it evolves from, and the required states. `[P]` = primary action, `[S]` = secondary.

Global shell: left nav (role-filtered), top bar (search, user menu, sign out), main area, toast region (`role="status"`).

---

## S1 Dashboard — `/`
**Evolves:** inline dashboard in `App.tsx`.
**Purpose:** see what needs attention; start new work.

```
┌ Dashboard ─────────────────────────────────────────── [+ New assessment] ┐
│ [Total 42] [Draft 6] [In progress 14] [Pending review 9] [Approved 11]   │
│ [Provisional 2]                                                           │
│ Risk: ■Critical 3  ■High 8  ■Medium 12  ■Low 9  □Unrated 10              │
├ Needs my action ──────────────────────┬ Launching in 7 days ─────────────┤
│ RAW-2026-00031 Rate factors  ⏱ At risk│ RAW-…19  CryptoBridge  HIGH  3d   │
│ RAW-2026-00027 Manager decision ⚠ Over│ RAW-…22  OmniBaaS      UNRATED 5d │
├ Recently updated ──────────────────────────────────────────────────────── ┤
│ Ref | Title | Stage | Inherent | Residual | Owner | Updated              │
└───────────────────────────────────────────────────────────────────────────┘
```
| Item | Spec |
|---|---|
| Hierarchy | Counts → my actions → launch risk → recent |
| Primary | New assessment (hidden for COMMITTEE_MEMBER) |
| Secondary | Click tile → Assessments list pre-filtered; search |
| Empty | "No assessments yet — create the first one" + [P] |
| Loading | Skeleton tiles (not "…") |
| Error | Inline alert + Retry; tiles show "—" |
| Permissions | Counts scoped to visibility; "Needs my action" per role |

---

## S2 Create assessment — `/assessments/new`
**Evolves:** `CreateAssessment.tsx`.

```
┌ New assessment ────────────────────────── ( Manual | Upload document ) ┐
│ Error summary (if any)                                                   │
│ ▸ 1 Product information   ● ● ○     ◂ progress per section              │
│ ▸ 2 Business context                                                     │
│ ▸ 3 Geography            [BR ✕][AE ✕][+ add country]                     │
│ ▸ 4 Customers                                                            │
│ ▸ 5 Transactions & channels                                              │
│ ▸ 6 Third parties                                                        │
│ ▸ 7 Financial-crime context + documents                                  │
│ ⚠ Possible duplicate: RAW-2026-00012 "GlobalSpeed Pay" [Not a duplicate]  │
│                                   [S Save draft]  [P Submit assessment] │
└──────────────────────────────────────────────────────────────────────────┘
```
| Item | Spec |
|---|---|
| Validation | Required-field rules in `user-journeys.md` J2; ISO date; countries resolved to ISO (unresolved shown as warning, not blocking) |
| Loading | Extraction: progress with filename + stage; submit button busy state |
| Error | 422 → per-field errors from `details.missing_fields`; 413 → file-level error |
| Permissions | `assessment.create` |

---

## S3 Assessment details / workspace — `/assessments/:id/:stage?`
**Evolves:** `AssessmentWorkflow.tsx` (split into stage modules).

```
┌ RAW-2026-00031 · Cross-border P2P wallet ······ Workflow: RISK ASSESSMENT ┐
│ Owner Priya · Analyst Arjun · SLA ⏱ 3d left · Mode: AI-assisted          │
│ Stage: Intake ✓ ─ Evidence ✓ ─ [Risk factors] ─ Controls ─ Residual ─      │
│        Review ─ Manager ─ Committee ─ Decision                            │
├──────────────────────────────────────────────┬────────────────────────────┤
│ <stage content>                              │ Risk summary (sticky)      │
│                                              │ Inherent  MEDIUM 52.0      │
│                                              │ Residual  —  (not yet)     │
│                                              │ Confidence LOW (2 unrated) │
│                                              │ Open findings 3 (1 HIGH)   │
│                                              │ [P stage action]           │
└──────────────────────────────────────────────┴────────────────────────────┘
```
| Item | Spec |
|---|---|
| Stage list | One list from `GET /api/workflow/config` (fixes D-16); URL segment per stage; server status decides the furthest reachable stage (localStorage no longer overrides) |
| Primary | Stage-specific (e.g. "Run risk identification", "Approve inherent risk & continue") — disabled with a reasons list from `GATE_NOT_MET` |
| Secondary | Comment, Request information, Audit history, Export |
| Empty/Loading/Error | Per stage; whole-page error with Retry if assessment fails to load |
| Permissions | Read = visibility; actions per permission matrix; read-only banner when locked |

---

## S4 Analysis progress — panel inside S3 (Evidence / Risk factors stage)
**Evolves:** `ProcessingStatus.tsx`.

```
┌ Risk identification — running (Run 5b1e…) ───────────────────── 62% ┐
│ ✓ Validating input                                                    │
│ ✓ Loading context & reference data (FATF/EU snapshot 2026-06 attested)│
│ ● AI identifying risk factors  · model anthropic/…  · 00:41           │
│ ○ Verifying evidence quotes                                           │
│ ○ Aggregating factors · ○ Provisional scoring · ○ Saving results      │
│ You can leave this page — progress is saved.        [S Cancel wait]   │
└───────────────────────────────────────────────────────────────────────┘
```
States: QUEUED ("Waiting for a worker"), RUNNING (stepper), SUCCEEDED (summary counts + "View findings"), DEGRADED (amber: provisional, categories not evaluated, [Acknowledge]), UNAVAILABLE/FAILED (red, plain-language reason, [Retry], "Previous results kept"), timeout after 10 min of no progress ("Still running — check back later").

---

## S5 Risk results — stage "Risk factors" and summary tab
```
┌ Overall ────────────────────────────────────────────────────────────┐
│ Inherent MEDIUM 52.0   Residual —   Confidence LOW   Provisional: no │
│ Rules fired: none   Methodology v2 (sha256:9ac…)   Engine 1.0.0      │
├ Dimensions ─────────────────────────────────────────────────────────┤
│ Category        Appl  L  I  Score Weight Contrib Evidence   Typology │
│ Customer seg.   Yes   4  4  64   0.10  24.6%  ✓ found     AML FRAUD  │
│ Geographic      Yes   –  –  UNRATED      –    ⚠ insufficient SANCTIONS│
│ Ownership       No    ·  ·   ·     ·     ·    n/a                    │
└─────────────────────────────────────────────────────────────────────┘
```
Rules: UNRATED shown as a distinct badge (outline, "?" icon, text) — never LOW/0. Contributions sum to 100 % of rated. Empty: "Run risk identification to see results". Error: inline.

---

## S6 Risk dimension details — drawer from S5 row
Contents: category description; all factors of this category (current + superseded versions toggle); rating control (L, I pickers with labels from methodology scale; AI suggestion chip "AI suggests 4 × 3 — rationale"); reason field (required when differing); history (who/when/source). Primary: **Save rating**. Secondary: Exclude (reason), Add manual factor. Permissions: `factor.rate`; business users see read-only.

---

## S7 Finding details — factor card expanded / challenge finding
```
┌ GEOGRAPHIC_RISK · applicable · Evidence: INSUFFICIENT ─────────────────┐
│ EVIDENCE — quote found in source                                        │
│  “Transfers between Brazil and the UAE …”  FIELD:countries ✓           │
│ INDICATORS  ✓ CROSS_BORDER_CAPABILITY   ✗ SANCTIONS_EXPOSURE           │
│             “cited text does not mention sanctions”                     │
│ AI INFERENCE — not evidence  (model x · prompt risk_factor_id v2.0.0)   │
│  Rationale: …   Misuse scenario: …                                      │
│ INFORMATION NEEDED  • Corridor list  • Screening vendor   [Request info]│
└─────────────────────────────────────────────────────────────────────────┘
```
Challenge findings (same component style): category, severity, section, supporting evidence, recommended action; actions **Resolve** (note) / **Accept risk** (reason) per permission; HIGH/CRITICAL open findings show "Blocks manager approval".

---

## S8 Evidence view — `/assessments/:id/documents/:docId?q=<offset>`
Split view: left = document list (type, version, confidentiality, processing badge); right = extracted text with highlighted quote, page marker, checksum and "version 2 of 2 (current)". RESTRICTED docs: masked text for unauthorised roles with explanation. Empty: "No documents uploaded". Loading: processing job progress. Error: extraction failed + Retry.

---

## S9 Recommendations — stage "Review" (draft)
Sections from `assessment_drafts`: executive summary, risk statements, mapped controls & effectiveness, residual, gaps, assumptions, missing information, recommended conditions, required approvals, analyst recommendation. Each section: *Advisory* label, generated-by provenance (model/prompt or deterministic template), [Edit] (creates new version), [Accept draft] [P]. Diff view between versions.

---

## S10 Human review — stage "Review"
```
┌ Ready to submit? ──────────────────────────────────────────────┐
│ ✓ All 5 applicable factors rated                                │
│ ✓ Evidence reviewed (2 factors with information gaps accepted)  │
│ ✗ 1 HIGH challenge finding open  → [Go to finding]              │
│ ✓ Comments resolved   ✓ Draft accepted   ✓ Residual frozen      │
│                                   [P Submit to manager] (owner) │
└─────────────────────────────────────────────────────────────────┘
+ FCRM justification · Overrides ledger (AI value → human value, reason, who, when) · Comments · Information requests
```

---

## S11 Approval / finalisation — `/approvals` and `/assessments/:id/decision`
**Evolves:** `ApprovalsPage.tsx`.
Queue (manager/committee) → decision package (summary, inherent/residual, confidence, rules, open items, overrides, votes so far) → Manager: Approve / Return / Reject (+comment). Committee: Vote; Chair: Approve / Approve with conditions (conditions table) / Defer / Reject + rationale. After decision: decision record card (checksum, "intact ✓"), conditions → action items, next review date. Disabled buttons always list unmet gates.

---

## S12 Audit history — `/assessments/:id/audit` and `/audit` (ADMIN)
Timeline merging `workflow_transitions`, `audit_events`, `analysis_runs`, overrides, votes; filters by type/actor/date; each entry: time, actor (and "on behalf of"), action, before → after, reason, link to entity. Export audit package [P] (authorised roles). Empty: "No activity yet". Pagination (50).
