# User Journeys

Personas: **Priya** (business requester, BUSINESS_USER), **Arjun** (FCRM analyst), **Meera** (line manager), **Committee** (COMMITTEE_MEMBER), **Admin**. Screen numbers refer to `wireframes.md`.

---

## Journey 1 — Dashboard

`Login → Dashboard (S1) → filter / search → open assessment | Create assessment`

| Step | User sees | System |
|---|---|---|
| 1 | Role-aware summary tiles: **Total · Draft · In progress · Pending review · Approved · Provisional**; risk-level strip **Critical · High · Medium · Low · Unrated** | `GET /api/assessments/summary` (new, server-side counts; replaces client counting) |
| 2 | "Needs my action" list (top 5 from work queue, with SLA badge) | `GET /api/workflow/work-queue` |
| 3 | "Launching in 7 days" (not yet decided) and "Recently updated" | `GET /api/assessments?sort=launch_date…` |
| 4 | Search box + filters (status, risk level, legal entity, business unit, owner = me) → navigates to Assessments list with query in URL | `GET /api/assessments?…` |
| 5 | **+ New assessment** (primary) | → S2 |

No charts beyond the counts strip in MVP (the existing Reports page covers analytics).

---

## Journey 2 — Create assessment

`Dashboard → Create (S2): choose Manual or Upload → sections → Save draft | Submit`

Sections (the existing 17 fields, regrouped; `*` = required to submit; draft needs only title):

| Section | Fields |
|---|---|
| 1. Product information | Assessment title*, Business change type* (`CHANGE_TYPES`), Product or service name*, Business description* |
| 2. Business context | Business owner*, Legal entity*, Business unit, Expected launch date* (ISO; warning if in the past), Technology or process changes* |
| 3. Geography | Countries and jurisdictions involved* (**multi-select with ISO normalisation + free text "other"**) |
| 4. Customers | Customer segment* |
| 5. Transactions & channels | Delivery channels* (multi-select), Transaction types*, Expected transaction volume*, Expected transaction value* |
| 6. Third parties | Use of third parties or vendors* ("None" allowed) |
| 7. Financial-crime context | Evidence / known considerations (optional free text — e.g. sanctions exposure, cash, PEPs, agents); supporting documents (optional upload) |

Behaviours retained: per-field validation on blur, error summary with focus to first error, key-field highlighting per change type, debounced duplicate check with "not a duplicate — continue". "Submitted by" is removed (server uses the logged-in user).

Upload path: drop `.docx/.pdf/.xlsx/.csv/.txt/.zip` → "Extract information" → AI fills fields marked **"AI-extracted — check"** → user reviews every section → Save/Submit. On submit the extraction becomes the unconfirmed intelligence profile.

---

## Journey 3 — Run analysis

`Assessment (S3) → Evidence stage → Confirm profile → Run analysis → Progress (S4) → Findings (S5/S6) → Scores → (later) Recommendations`

| Step | Detail |
|---|---|
| 1 | Analyst opens assessment; stage tracker shows **Evidence**; banner: "Profile not confirmed" if applicable |
| 2 | Reviews/corrects intelligence profile → **Confirm profile** |
| 3 | **Run risk identification** (primary). If inputs incomplete → inline list of issues (`GATE_NOT_MET.details.missing`) with "Edit intake" / "Request information" |
| 4 | Progress panel (S4): 7-step stepper driven by `stage_key` — *Validating input → Loading context & reference data → AI identifying risk factors → Verifying evidence quotes → Aggregating factors → Provisional scoring → Saving results*, with % bar, elapsed time, and "You can leave this page; we'll keep working." |
| 5 | Outcome: **AI-assisted** → "10 categories assessed · 5 applicable · 14 quotes verified · 2 rejected · 3 information gaps". **Rules-only** → amber provisional banner + "Acknowledge" (analyst). **Unavailable** → red banner, Retry, nothing overwritten |
| 6 | Findings (S5): factor cards, all **UNRATED** until the analyst rates |
| 7 | Rating: AI suggestion shown next to L×I pickers; reason required if different; score and inherent band update live from the server response (fixes stale sidebar D-15) |
| 8 | Controls → Residual → Draft recommendation (S9) generated at Residual → Human Review |

---

## Journey 4 — Review results

`Risk results (S5) → Overall risk → Dimension (S6) → Finding / factor (S7) → Evidence (S8) → Recommendations (S9) → Reviewer comments`

- **Overall risk header:** Inherent band + score, Residual band, Confidence (HIGH/MEDIUM/LOW with "why"), provisional flag, rules fired chips (e.g. "SANCTIONS_EXPOSURE_001 → CRITICAL").
- **Dimensions table:** 10 categories — applicable?, L, I, score, weight, contribution %, evidence status, typology chips, rated by.
- **Factor detail:** three clearly separated blocks — *Evidence (quote found in source)*, *AI inference — not evidence*, *Information needed*; plus rating history.
- **Evidence view:** document text with the quote highlighted at `normalized_offset`, doc version, checksum, page.
- **Recommendations:** draft summary, recommended conditions, required approvals — all labelled "Advisory".
- **Comments:** per section with visibility (ALL / MANAGER_AND_ABOVE / COMMITTEE_ONLY), resolve / accept-with-exception.

---

## Journey 5 — Human approval

`Human review (S10) → inspect findings → validate evidence → accept/challenge → comment → submit → Manager approve/return/reject → Committee vote & decision (S11) → Finalize (decision record) → Audit history (S12)`

| Actor | Steps |
|---|---|
| Analyst | FCRM review checklist (S10): every applicable factor rated ✓, evidence status reviewed ✓, blocking challenge findings resolved/accepted ✓, comments resolved ✓, draft accepted ✓ → owner submits to manager |
| Manager (Meera) | Approvals queue → decision package → **Approve** (gates shown as checklist; disabled with reasons if unmet) / **Return** (reason) / **Reject** (reason) |
| Committee | Decision package → cast vote (Approve / Dissent / Abstain + comment) → chair records decision: Approve / Approve with conditions (structured conditions: description, owner, due date, priority) / Defer / Reject + rationale |
| System | Freezes decision record (checksum), creates action items for conditions, sets next review date |
| Anyone with visibility | Audit history (S12): timeline of transitions, overrides, AI runs, votes; export package (authorised roles) |
