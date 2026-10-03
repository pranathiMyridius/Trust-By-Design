# Google Stitch Prompt — Financial Crime Risk Assessment Workbench

> **How to use this file**
> 1. Copy everything inside **Part 1 — Master Prompt** (between the two `=====` markers) into Google Stitch as one prompt. It is self-contained: Stitch does not need repository access.
> 2. If Stitch truncates or simplifies, use **Part 2 — Follow-up Prompts** one at a time, in order, in the same Stitch project. Each one references the design system and sample data defined in the master prompt.
> 3. **Part 3 — Codebase Analysis Notes** is for the team only (do not paste). It records what was verified in the repository, what is partial, and what is planned, so reviewers can check the prompt against the code.
>
> Generated on 3 October 2026 from the `RiskAssessmentWorkbench` repository (frontend `src/`, backend `app/`, `design/`, `docs/`). No application files were changed.

---

## Part 1 — Master Prompt

```
=====
ROLE AND GOAL
You are designing a complete, connected, high-fidelity web-application prototype for an internal
enterprise product called "Risk Assessment Workbench" (full name: Financial Crime Risk Assessment
Workbench). It is used by a financial-services firm's First Line business teams and its Financial
Crime Risk Management (FCRM) function to assess the financial-crime risk (money laundering,
terrorist financing, sanctions, fraud) of new products, services, customer segments, geographies,
third parties, technology changes and channel/limit changes BEFORE they launch.

This prompt describes an application that ALREADY EXISTS (React + FastAPI). Design a better UX for
the existing workflow. Do NOT invent capabilities, roles, statuses, risk dimensions, fields or
metrics beyond those listed here. Anything marked [PLANNED] does not exist yet: show it only where
stated, with a small dashed "Planned" tag.

Design principle in one sentence, to be visible in the product: "AI proposes, rules calculate,
people decide."

-------------------------------------------------------------------------------------------------
A. PRODUCT VISION
-------------------------------------------------------------------------------------------------
Purpose: turn a business change request into a governed, auditable financial-crime risk decision.

Core workflow (the real pipeline, in order):
 1. Intake – a Business User raises a request (or saves a draft); can upload a document and have
    the request fields auto-extracted by AI.
 2. Evidence Collection – documents are uploaded and text-extracted; AI builds a structured
    "Business Profile" that the business owner must confirm; contradictions are flagged.
 3. Risk Identification – an AI analysis (LangGraph pipeline) evaluates 10 fixed risk categories,
    cites verified quotes from the evidence, and SUGGESTS a likelihood × impact rating per category.
 4. Inherent Risk Assessment – an FCRM Analyst rates every applicable category (accept or override
    the AI suggestion). The score is calculated deterministically and frozen.
 5. Control Assessment – AI proposes controls per risk; analysts record design adequacy and
    operating effectiveness; control gaps are detected.
 6. Residual Risk – a deterministic grid (inherent band × control rating) gives residual risk; an
    analyst confirms it; an AI-generated decision-ready draft is produced for human editing.
 7. Human Review (Analyst Review) – the analyst finalises the draft, comments, and may request
    information; the business owner then submits to their line Manager.
 8. Challenge Review (Manager) – the assigned Manager approves to committee, returns for
    amendment, or rejects. Open high-severity challenge findings block approval.
 9. Committee Review – committee members cast votes (Approve / Dissent / Abstain); one binding
    decision is recorded: Approved, Approved with Conditions, Deferred, or Rejected.
10. Decision & Audit – the decision record is frozen (checksummed), conditions become tracked
    action items, a reassessment date is set, and everything is in the audit trail.
At any active stage an analyst, manager or committee member can "Request information"; the case
pauses in INFORMATION_REQUESTED and resumes at the same stage when the owner responds.

Business goals: consistent, explainable risk ratings; no AI output ever treated as a decision;
separation of duties; full audit trail; visible SLAs; fewer back-and-forth cycles.

-------------------------------------------------------------------------------------------------
B. EXISTING ARCHITECTURE (for context — design must fit it)
-------------------------------------------------------------------------------------------------
- Frontend: React 19 + TypeScript + Vite, plain CSS, no component library, no router (screens are
  switched by state today; the new design should assume real URLs per screen and per tab).
- Backend: Python FastAPI, ~200 REST operations under /api (assessments, workflow, approvals,
  controls, challenge-review, action-items, reassessment, reports, audit, users, delegations,
  sod-exceptions, sources, risk-methodologies, system, processing-jobs). JWT bearer login.
- Database: PostgreSQL on Neon (SQLAlchemy + Alembic). Key tables: assessments, risk_factors,
  assessment_intelligence, assessment_documents, controls, control_assessments, control_gaps,
  inherent/residual_risk_calculations, assessment_drafts, assessment_comments, challenge findings,
  committee_votes, committee_conditions, decision_records, action_items, workflow_transitions,
  audit_events, approval_delegations, sod_exceptions, approved_sources, reassessment_triggers,
  processing_jobs, risk_methodologies, users.
- AI: LangGraph graph load_assessment → gather_intelligence → identify_risks → calculate_scores →
  persist_results, run as a background job with named progress steps and a percentage. LLM is
  an OpenAI-compatible provider (configurable). The LLM never produces the score: it identifies
  applicable categories, indicators, rationale, misuse scenarios, evidence quotes and a SUGGESTED
  likelihood/impact. Quotes are verified deterministically against the source text. If the AI
  fails, the system falls back to rules-only (provisional) or "unavailable" and says so.

-------------------------------------------------------------------------------------------------
C. DESIGN SYSTEM
-------------------------------------------------------------------------------------------------
Language: modern enterprise financial-services; calm, minimal, information-dense, trustworthy.
Light theme by default. Navy sidebar, neutral backgrounds, restrained blue accent. No gradients,
no decorative illustrations, no oversized cards, no chatbot UI, no emoji, no gauges/speedometers,
no fake confidence percentages.

Colour tokens
- Primary / sidebar: Deep navy #17365D
- Accent / primary buttons / links / active tab: Blue #356AC3 (weak tint #E8EFFA)
- App background #F5F7FA; Surface #FFFFFF; Subtle surface #F9FAFC; Hover #F0F3F8
- Text primary #202B3C; Text secondary #687386; Borders #E0E5EC (strong #C9D1DC)
- Success #23834B; Warning #D99A23; Neutral #718096
- Risk bands (always colour + shape icon + text label, never colour alone — this matches the
  existing RiskLevelBadge):
    LOW      ○  blue-grey  #2F6E8F on #E6F1F6
    MEDIUM   ◆  amber-olive #8A6A00 on #FBF4D6
    HIGH     ▲  orange #D97706 (text #B85F05) on #FCEEDC
    CRITICAL ‼  red #C43D3D on #FBE9E9
    UNRATED  ?  dashed outline, grey text — used for any factor/assessment not yet rated
    PROVISIONAL  amber diagonal-stripe badge with text "Provisional" + tooltip reason
- AI vs human: AI-generated content uses a muted violet tag "AI suggestion" (#5B4BB7 on #EFEDFA)
  with a sparkle icon; human-validated content uses a navy tag "Analyst rated" / "Human decision"
  (#17365D on #E6ECF4) with a person-check icon. These two tags must appear consistently
  everywhere AI and human values sit side by side.

Typography: Inter (fallback system-ui). Page title 20/28 semibold; section title 16/24 semibold;
card title 14/20 semibold; body 14/20; table 13/18; labels 12.5/16 medium; metadata 12/16
secondary colour; uppercase micro-labels 11px +0.06em tracking. Monospace (JetBrains Mono) only
for reference IDs like RAW-2026-00042, checksums and rule codes. Tabular numerals for all numbers.

Spacing & shape: 4px base grid (4/8/12/16/24/32). Radius 6px controls, 8px panels. 1px borders;
shadows only on menus, modals and drawers. Dense tables (40px rows). Max content width 1440px.

Navigation: left sidebar 248px (collapsible to a 64px icon rail with tooltips; off-canvas drawer
below 900px). Top bar 56px: breadcrumbs, global search, alert pills, help, user avatar + name +
role, profile menu with Sign out. A small amber "Prototype · sample data" chip in the top bar.

Components to define once and reuse: sidebar, top bar, breadcrumbs, page header (title, subtitle,
primary action), filter bar (search + selects + chips + clear), data table (sortable headers,
row hover, row click, sticky header, pagination "1–25 of 132"), risk badge, workflow-status badge,
SLA indicator (dot + "On track / At risk / Overdue / No SLA"), priority badge (LOW/MEDIUM/HIGH/
URGENT), AI/Human tags, lifecycle stepper, form stepper, tabs, drawer (document preview), modal,
confirmation dialog with required reason field, toast (success/error/info), callouts (info,
warning, critical, AI), empty state, skeleton loading, progress list with named steps,
timeline, definition list, key–value header strip, file drop zone, chip/tag input, segmented
control, likelihood × impact selector, 5×5 risk matrix, horizontal bar chart, "disabled button
with reasons" pattern (a disabled primary action always lists what it is waiting for).

Status badges — use EXACTLY these lifecycle statuses and labels (workflow_status):
DRAFT "Draft" (grey) · SUBMITTED "Submitted" (blue) · INTAKE_VALIDATION "Intake Validation" (blue)
· INFORMATION_REQUESTED "Information Requested" (amber) · EVIDENCE_REVIEW "Evidence Review" (blue)
· RISK_ASSESSMENT_IN_PROGRESS "Risk Assessment in Progress" (blue) · ANALYST_REVIEW "Analyst
Review" (blue) · CHALLENGE_REVIEW "Challenge Review" (violet-navy) · READY_FOR_COMMITTEE "Ready for
Committee" (navy) · COMMITTEE_REVIEW "Committee Review" (navy) · AMENDMENT_REQUIRED "Amendment
Required" (amber) · DEFERRED "Deferred" (amber) · APPROVED "Approved" (green) ·
APPROVED_WITH_CONDITIONS "Approved with Conditions" (green with condition icon) · REJECTED
"Rejected" (red) · CLOSED "Closed" (grey).

Responsive: primary target 1440px desktop; works at 1280 laptop; at 768 tablet the sidebar
becomes a drawer, two-column layouts stack, tables scroll horizontally inside their panel; at
375 mobile show read-and-act views (work queue, assessment overview, decision panels) with
tables collapsing to stacked cards.

Accessibility: WCAG 2.2 AA contrast; visible 2px blue focus ring; full keyboard operation of
tabs, menus, dialogs (focus trapped, Esc closes); "Skip to main content" link; every input has a
visible label; errors in text next to the field and summarised at the top of the form; tables
use proper headers; status never conveyed by colour alone; live regions for toasts and progress.

-------------------------------------------------------------------------------------------------
D. USER PERSONAS (the real roles in the system)
-------------------------------------------------------------------------------------------------
Sample people (fictional) to use consistently:
1. Priya Raman — BUSINESS_USER ("Business User"). Owner of requests. Reports to Daniel.
   Can: create/save draft/submit requests; edit while Draft, Submitted or Amendment Required;
   withdraw before review starts; upload evidence; confirm the AI-extracted business profile;
   answer information requests; submit to manager after Analyst Review; resubmit after a return.
   Sees only assessments she owns (plus any scope rules). Cannot run AI analysis or rate risks.
2. Arjun Mehta — FCRM_ANALYST ("FCRM Analyst"). Can: run AI analysis; advance pipeline stages;
   rate/override/exclude risk factors (with reason); add manual factors; map and assess
   controls; confirm residual risk; edit/accept the AI draft; comment; request information;
   assign/claim tasks; set target dates; close decided cases. Governance designations may add
   SENIOR_ANALYST, QA_REVIEWER or CHALLENGE_REVIEWER.
3. Daniel Okafor — MANAGER ("Manager"). Has all analyst pipeline rights, plus the Challenge
   Review decision on cases where he is the assigned manager: Approve (to committee), Return
   (comment required), Reject (comment required). Cannot decide on his own submission. Can
   delegate his approval authority for a period.
4. Helen Fischer — COMMITTEE_MEMBER ("Committee Member", designation COMMITTEE_CHAIR). Can: open
   committee review, cast/re-cast a vote (APPROVE / DISSENT / ABSTAIN; re-cast needs a reason;
   history is kept), record the binding decision (approve / approve with conditions / defer /
   reject — written rationale always required; conditions need description, owner, due date,
   priority), request information, open a controlled amendment on a decided case, close.
5. Sam Torres — ADMIN ("Administrator"). Manages users (role, manager, active, access scope by
   legal entity / business unit / country, governance designations), risk methodology versions,
   challenge-trigger settings, source library, retention policies, system health and backups.
   Cannot take the Manager decision. Cannot act as committee member unless an approved
   dual-role SoD exception exists.
6. Grace Lin — AUDITOR ("Auditor", read-only). Sees everything incl. audit export; changes nothing.
7. Executive (read-only) — sees assessments and reports; changes nothing.
8. Control Owner — sees assessments in scope; evidences closure of action items assigned to them.
9. Policy Admin — maintains the Approved Source Library (draft → approve → retire).
Read-only roles see a persistent banner: "Read-only access (Auditor): you can view assessments,
history and reports, but not change them." All action buttons are hidden for them.

-------------------------------------------------------------------------------------------------
E. SAMPLE DATA (use the same values on every screen)
-------------------------------------------------------------------------------------------------
Reference IDs follow the format RAW-YYYY-NNNNN. Label all data "Sample data".

Flagship assessment (drive every journey with this one):
- reference_id RAW-2026-00042 · title "Launch PayBridge SME Wallet payouts to UAE and Kenya"
- change_type NEW_PRODUCT ("New Product") · product_or_service_name "PayBridge SME Wallet"
- legal_entity "Northbridge Payments Ltd" · business_unit "SME Banking"
- business_owner "Priya Raman, Head of SME Products" · owner Priya Raman · manager Daniel Okafor
- customer_segment "SME importers and exporters, remotely onboarded"
- countries_jurisdictions "United Kingdom, United Arab Emirates, Kenya"
- delivery_channels "Mobile app, Web portal, Partner API"
- transaction_types "Cross-border payouts, wallet top-ups by bank transfer"
- expected_transaction_volume "12,000 payments/month" · expected_transaction_value "£4.5M/month"
- third_party_vendor_usage "Local payout partner in Kenya; KYC data provider"
- technology_process_changes "New mobile onboarding flow with remote ID verification"
- expected_launch_date 2026-11-16 · shell_company_indicator No
- priority HIGH (intake triage) · assigned_team "FCRM – Payments" · target_date 2026-11-02
- Starts the demo at status RISK_IDENTIFICATION → workflow_status "Risk Assessment in
  Progress", SLA "At risk", AI analysis mode "AI-assisted", 3 of 8 applicable factors rated
  (so the overall result is PROVISIONAL).

Risk factors for RAW-2026-00042 (AI suggestion L×I → score, and evidence status):
| Category (label)                         | Applicable | AI suggests | Score | Band     | Evidence status          |
| PRODUCT_SERVICE_RISK (Product or Service) | Yes | L3 × I4 | 48 | MEDIUM   | EVIDENCE_FOUND |
| CUSTOMER_SEGMENT_RISK (Customer/Segment)  | Yes | L3 × I3 → analyst overrode to L3 × I4 | 48 | MEDIUM | EVIDENCE_FOUND |
| GEOGRAPHIC_RISK (Geographic)              | Yes | L4 × I4 | 64 | HIGH     | EVIDENCE_FOUND |
| DELIVERY_CHANNEL_RISK (Delivery Channel)  | Yes | L4 × I3 | 48 | MEDIUM   | EVIDENCE_FOUND |
| TRANSACTION_ACTIVITY_RISK (Transaction)   | Yes | L4 × I4 | 64 | HIGH     | EVIDENCE_FOUND |
| TECHNOLOGY_DEVELOPMENT_RISK (Technology)  | Yes | L2 × I3 | 24 | LOW      | EVIDENCE_FOUND |
| THIRD_PARTY_VENDOR_RISK (Third Party)     | Yes | L3 × I3 | 36 | LOW      | INSUFFICIENT_EVIDENCE (missing: vendor due-diligence report) |
| OWNERSHIP_ENTITY_COMPLEXITY_RISK          | No  | —       | —  | Not applicable (rationale shown) | NOT_APPLICABLE |
| FINANCIAL_CRIME_TYPOLOGY_RISK (Typology)  | Yes | L4 × I5 | 80 | CRITICAL | EVIDENCE_FOUND |
| CONTROL_ENVIRONMENT_RISK (Control Env.)   | Yes | L2 × I3 | 24 | LOW — mitigant, excluded from the inherent average | EVIDENCE_FOUND |
Initially rated by analyst: Geographic (confirmed), Transaction (confirmed), Customer (override).
Others show "Unrated" with the AI suggestion beside them.
When all are rated as suggested: inherent score = average of the 8 applicable non-mitigant
factors = (48+48+64+48+64+24+36+80)/8 = 51.5 → MEDIUM. Show the note: "2 draft escalation rules
would raise this to HIGH if approved (MIN_BAND_KEY_FACTOR_001, GEO_HIGH_RISK_THIRD_COUNTRY_001) —
not applied because their status is Draft." Overall control rating PARTIAL → residual MEDIUM
(grid). Methodology required approvals for MEDIUM: Analyst, Reviewer; the workflow still routes
every case to committee.
Indicators used: CROSS_BORDER_CAPABILITY, REMOTE_ONBOARDING, TRANSACTION_VELOCITY,
UNKNOWN_PARTY_PAYMENTS, MULTIPLE_CURRENCIES, THIRD_PARTY_DEPENDENCIES.

Evidence documents for RAW-2026-00042:
1. PayBridge_Product_Specification_v2.pdf — PRODUCT_SPECIFICATION, Confidential, v2 (supersedes
   v1), owner Priya Raman, effective 2026-09-01, text extracted.
2. SME_Corridor_Business_Case.docx — BUSINESS_REQUIREMENT, Internal, v1.
3. Remote_Onboarding_Process_Map.pdf — PROCESS_MAP, Internal, v1.
4. Kenya_Payout_Partner_Contract_Summary.pdf — VENDOR_DOCUMENT, Restricted, v1, expiry
   2026-10-31 (shows "Expires in 28 days").
5. Transaction_Monitoring_Rules.xlsx — CONTROL_DESCRIPTION, Internal, v1.
Missing information requested by AI: "Vendor due-diligence report for the Kenya payout partner".

Other assessments for lists (owner / status / risk):
- RAW-2026-00031 "Merchant cash-advance product for UK retailers" – APPROVED_WITH_CONDITIONS –
  residual HIGH – 2 conditions (1 open). Use for the Final Decision screen.
- RAW-2026-00038 "Introduce crypto-on-ramp partner" – THIRD_PARTY_INTRODUCTION – COMMITTEE_REVIEW
  – CRITICAL – 2 of 3 votes cast. Use for Committee screen.
- RAW-2026-00040 "Raise daily card limit for premium segment" – TRANSACTION_LIMIT_OR_CHANNEL_CHANGE
  – CHALLENGE_REVIEW – HIGH – assigned manager Daniel. Use for Manager Approval.
- RAW-2026-00044 "Onboard SMEs in Nigeria via agent network" – NEW_GEOGRAPHY – INFORMATION_
  REQUESTED – HIGH – SLA Overdue.
- RAW-2026-00045 "Replace sanctions screening vendor" – TECHNOLOGY_CHANGE – EVIDENCE_REVIEW –
  Unrated.
- RAW-2026-00046 "Student current account" – NEW_PRODUCT – DRAFT – Unrated.
- RAW-2026-00035 "Periodic reassessment: FX forwards for corporates" – PERIODIC_REASSESSMENT –
  AMENDMENT_REQUIRED – MEDIUM.
- RAW-2026-00027 "Gift card resale channel" – REJECTED (Manager) – HIGH.
- RAW-2026-00019 "Payroll API for fintech partners" – CLOSED – LOW.

-------------------------------------------------------------------------------------------------
F. SCREEN INVENTORY AND SPECIFICATIONS
-------------------------------------------------------------------------------------------------
Sidebar (show items by role):
  WORK: Dashboard (all) · My Work Queue (all) · Assessments (all; tabs "My assessments" / "All
  assessments" — Business Users only get "My assessments") · New Assessment (not read-only roles)
  · Approvals (Manager, Committee Member).
  INSIGHT: Reports (Analyst, Manager, Committee, Admin, Auditor, Executive) · Audit History (all)
  · Risk Calculator (all).
  LIBRARY & GOVERNANCE: Source Library (all; manage = Policy Admin, Admin) · Delegations
  (Manager, Committee, Admin) · SoD Exceptions (all) · Retention & Legal Holds (only when
  permitted).
  ADMINISTRATION (Admin only): Users · Risk Governance · System Health.
Note: there is no global "AI Risk Analysis" or "Evidence Library" area — AI analysis and evidence
live inside each assessment. "Review Queue" = My Work Queue. "Committee Decisions" = Approvals
filtered to committee.

For EVERY screen design: default state, loading (skeleton rows), empty state (icon + one line +
primary action if allowed), error state (callout: what failed + "Try again"), and success toasts.

1) LOGIN
Layout: split screen — left navy panel with product name, one-line purpose and the 4-word
principle "AI proposes, people decide."; right white card. Fields: Email (validation "Enter a
valid email address"), Password with show/hide eye button, "Sign in" primary button (spinner while
signing in). Error: "Incorrect email or password." Footer: "Authorised use only. Activity is
logged for audit." Do NOT include SSO or "Forgot password" (not implemented; show a muted line
"Forgot your password? Contact your administrator."). Prototype-only: a "Demo personas" list
under the card to sign in as Priya, Arjun, Daniel, Helen, Sam or Grace.

2) DASHBOARD
Header: "Good morning, Arjun" · role chip · today's date · primary "New assessment" (hidden for
read-only). Alert pills from real data: "2 provisional AI results to review" (violet) and
"1 launching within 7 days" (amber).
KPI cards (each clickable → Assessments list pre-filtered): Total assessments · Draft ·
In progress · Pending review (Analyst Review, Challenge Review, Ready for Committee, Committee
Review) · Approved · Provisional (AI). Each with "+n in last 30 days".
Main column: "Action queue" table — Assessment ID, Entity / title, Risk level, Stage (workflow
status badge), SLA status, Assigned date; filter chips: All · Needs my action · Launching soon ·
Provisional AI · Overdue; risk filter; search; pagination; row click opens the assessment.
Side column: "Risk level distribution across active assessments" (horizontal stacked bar:
Critical, High, Medium, Low, Unrated — Unrated drawn as hatched grey, never as Low);
"Assessments by status" (compact bar list); "Recent activity" (last 6 audit events, AI vs human
icons). Empty state: "No assessments yet — start your first request."

3) ASSESSMENTS (My / All)
Filter bar: search (ID, title, product, entity), Status (multi), Risk level (multi incl.
Unrated), Change type, Business unit, Legal entity, Priority, SLA state, Created date range,
"Clear filters". Columns: Assessment ID (mono) · Title & product (two lines) · Change type ·
Business unit / legal entity · Countries · Risk level (residual, else inherent, else AI) ·
Score (one decimal; "—" if unrated, stripe if provisional) · Status · Current owner / team ·
Priority · SLA · Created · Last updated · ⋯ actions (Open, Open audit trail, Withdraw — owner &
Draft/Submitted only). Sortable headers, pagination 25/50/100, sticky header, row click opens
details. Empty-filter state: "No assessments match these filters" + Clear filters.

4) NEW ASSESSMENT (guided intake, 6 steps with a visible stepper; Save as draft on every step)
Banner option at top: "Start from a document" — drop a BRD/spec, AI extracts the fields; each
pre-filled field shows a small "Extracted from <file>, p.3" provenance tag with a confidence chip
(HIGH / MEDIUM / LOW / USER_PROVIDED — categorical, never a percentage); the user must review.
Duplicate check: after title + product are entered show "Possible duplicate: RAW-2026-00031
(similar product)" inline callout with "Open" and "Not a duplicate".
Step 1 Product information: Assessment title*, Business change type* (select: New Product, New
Service, New Customer Segment, New Country / Geography, Process Change, Technology Change,
Third-Party Introduction, Transaction-Limit or Channel Change, Periodic Reassessment), Product or
service name*, Legal entity*, Business unit (optional), Business owner*, Shell company indicator
(Yes / No / Not answered), Expected launch or implementation date*.
Step 2 Business context: Business description* (textarea), Technology or process changes*,
Customer segment*.
Step 3 Geography: Countries and jurisdictions involved* (chip input with country look-up; each
chip shows if it matched reference data, and "Could not identify" in amber when it did not — an
unidentified country later triggers a mandatory challenge).
Step 4 Channels & transactions: Delivery channels*, Transaction types*, Expected transaction
volume*, Expected transaction value*, Use of third parties or vendors* ("None" allowed).
Step 5 Financial-crime context & evidence: Evidence & known risk indicators (textarea: adverse
media, sanctions exposure, known impacts); file drop zone (PDF, DOCX, XLSX, TXT) with per-file
metadata: Document type (Business Requirement Document, Product Specification, Process Map,
Customer Information, Vendor Document, Control Description, Regulatory / Policy Reference,
Previous Assessment, Other), Confidentiality (Public, Internal, Confidential, Restricted), Owner,
Source, Effective date, Expiry date; upload progress and "Text extraction" status per file
(Queued / Running / Completed / Failed + Retry); remove file.
Note shown on this step: "You don't choose risk categories — the analysis evaluates all ten."
Step 6 Review & submit: grouped summary with "Edit" per section; a "Missing for submission" list
linking to fields (all 15 mandatory fields above); buttons "Save as draft" and "Submit request".
Confirmation modal: "Submit RAW-2026-00051 for review? You can still edit until review starts."
Success page: reference ID, intake priority (e.g. HIGH), routed team ("FCRM – Payments"),
target date, next step "Confirm your business profile", button "Open assessment".
Field-level validation on blur and on submit; required marker *; drafts skip validation.

5) ASSESSMENT DETAILS — shared frame for all assessment screens
Header strip: reference ID (mono) · title · workflow-status badge · risk badge (with
"Inherent"/"Residual"/"AI-assessed" qualifier and Provisional stripe when relevant) · priority ·
SLA chip with due date · Business unit · Legal entity · Created · Last updated · Owner · Current
task owner ("Waiting on: Arjun Mehta (FCRM – Payments) — Rate remaining risk factors").
Lifecycle stepper (9 tracked stages): Intake · Evidence Collection · Risk Identification
(includes Inherent Risk) · Control Assessment · Residual Risk · Human Review · Submitted to
Manager · Ready for Committee · Decision. Completed = green check, current = blue ring,
INFORMATION_REQUESTED shows a pause marker on the current step, AMENDMENT_REQUIRED shows a return
arrow back to Human Review.
Action bar (only actions permitted for this user at this status; disabled ones list their
missing gates in a popover): Run AI analysis · Advance to <next stage> · Request information ·
Submit to manager · Assign / claim · Set target date · Withdraw · Close · Export audit pack ·
View audit trail. Mandatory issues (from the server) appear as a red-bordered checklist:
"Before committee: 1 high-severity challenge finding open; residual risk not confirmed".
Tabs (each its own URL): Overview · Business Profile · Evidence · AI Risk Analysis · Risk
Ratings · Controls & Residual · Review & Decision · Comments · Audit History.

5a) OVERVIEW tab: executive summary (from the AI draft once generated, labelled AI + edited-by);
key facts definition list; risk summary card (inherent score/band, control rating, residual
band, required approvals); "Pending actions" list per role; "Information requested" callout
when paused (who asked, note, target, Respond button for the owner); decision history
(workflow transitions: from → to, action, actor, reason, time); open conditions/action items.

5b) BUSINESS PROFILE tab (Evidence Collection stage): AI-extracted structured profile — business
line, customer type, transaction origin/destination, frequency, payment methods, onboarding
approach, ownership/entity structure, channels, countries, customer segments, transaction
volume, average size, maximum limit, third-party vendors, data shared, technologies, regulatory
considerations, existing controls, additional risk factors. Each field: value, provenance
(document, page, quote), confidence chip, edit (after confirmation an edit needs a reason and
un-confirms). "Conflicts" panel: form value vs extracted value, or document vs document, with
the passages. Primary action for the owner: "Confirm business profile". Readiness checklist:
Assessment evidence provided · Business intelligence available · Profile confirmed by the
business owner · Source document text extracted. Intake history (versioned snapshots: who
changed what, old → new, reason).

6) EVIDENCE tab (Evidence Review)
Left: documents table — File name, Document type, Version (with "supersedes v1"), Uploaded by,
Upload date, Confidentiality, Effective / Expiry (expiring or expired highlighted; expired
documents need an "Acknowledge expiry" with reason before they can be used), Text extraction
status, "Cited by" (number of risk factors quoting it). Actions: Preview, Download original
(only owner, analysts, admins for Confidential/Restricted — others see "Masked view only"),
Upload new version. "Evidence gaps" panel: missing information requested by the analysis
(e.g., vendor due-diligence report) and expired documents. No completeness percentage.
Right drawer "Document preview": metadata, extracted-text preview with highlighted quotes that
risk factors cite (click a highlight → jumps to that factor), masked text for restricted
documents ("■■■■ masked — you are not entitled to the original"), comments on this evidence
(section EVIDENCE), "Compare with business profile" toggle showing profile fields next to the
supporting passages.

7) AI RISK ANALYSIS tab (Risk Identification) — see section H for the full specification.

8) RISK RATINGS tab (Human risk rating, Inherent Risk Assessment)
Header: methodology version in use; scales — Likelihood 1 Rare, 2 Unlikely, 3 Possible, 4 Likely,
5 Almost Certain; Impact 1 Negligible, 2 Minor, 3 Moderate, 4 Major, 5 Severe; formula "factor
score = likelihood × impact ÷ 25 × 100"; bands LOW 0–39, MEDIUM 40–59, HIGH 60–79, CRITICAL 80–100.
Progress: "5 of 8 applicable factors unrated — result is provisional".
Rating table, one row per category: Category · Applicable · AI suggestion (violet tag, L×I,
score, band, rationale link) · Analyst rating (navy tag: Likelihood select, Impact select,
live score + band) · Rating source (ANALYST_CONFIRMED "Confirmed AI suggestion" /
ANALYST_OVERRIDE "Overridden" / ANALYST_RATED "Rated without AI suggestion") · Rationale (required
when overriding) · Evidence status · Rated by / at · History (popover of prior ratings).
Row actions: "Accept AI suggestion", "Override", "Exclude factor" (reason required), "Edit
indicators". "+ Add manual risk factor". Significant change confirmation: when an override moves
a factor by a whole band or more, show a dialog with old vs new band and a required reason.
Summary panel (sticky right): weighted inherent score, band, escalation rules (approved rules
applied; draft rules listed as "not applied"), "Inherent risk override" (reason + approval),
"Advance to Control Assessment" disabled until all applicable factors are rated and any degraded
AI result has been acknowledged.

9) CONTROLS & RESIDUAL tab
Per risk factor: mapped controls from the control library (KYC / Customer Due Diligence,
Enhanced Due Diligence, Beneficial Ownership Verification, Sanctions Screening, Transaction
Monitoring, Fraud Monitoring, Transaction Limits, Geographic Restrictions, Customer-Risk
Monitoring, Vendor Due Diligence, Investigation Processes, Regulatory Reporting) — each with
owner, performing department, frequency, evidence source, design adequacy (ADEQUATE /
INADEQUATE / NOT_ASSESSED + rationale), operating effectiveness (EFFECTIVE / PARTIALLY_EFFECTIVE /
INEFFECTIVE / UNVERIFIED + rationale), AI-proposed vs analyst-added tag, version history,
Unmap / Remap. Control rating per factor (WEAK / PARTIAL / EFFECTIVE); overall = weakest.
Control gaps list → become action items. Residual grid shown as a 4×3 table (inherent LOW/
MEDIUM/HIGH/CRITICAL × control WEAK/PARTIAL/EFFECTIVE) with the applied cell highlighted.
Residual result card: calculated band, "Confirm residual risk" (analyst) or "Confirm with
override" (reason, stored beside the calculated value). Recommended conditions (accept/reject).

10) REVIEW & DECISION tab — contains the FCRM Review, Manager Approval, Committee and Final
Decision sections stacked in order; only the current one is expanded.
 10a FCRM / Analyst review (stage HUMAN_REVIEW, status "Analyst Review"): AI-generated
  decision-ready draft with sections Executive summary, Business change description, Applicable
  risk categories, Risk statements, Inherent risk, Evidence references, Mapped controls &
  effectiveness, Residual risk, Risk gaps, Assumptions, Missing information, Recommended
  conditions, Analyst recommendation (tag "Advisory — not a decision"), Required approvals,
  Uncertainty (low-confidence items, unsupported conclusions, conflicting evidence, unresolved
  questions). Draft metadata: generated at, generation method, version; "Edit", "Accept draft",
  version history. Analyst comments by section. Analyst actions: Request information, Comment.
  Owner action: "Submit to manager" (blocked while analyst review comments are unresolved).
  There is no analyst "approve/reject" in the workflow — do not add one.
 10b Challenge review (Manager; status "Challenge Review"): triggers panel listing which fired
  (Risk is high or critical · Evidence is missing · Contradictions exist · Confidence is low
  (provisional calculation) · Important risk factors are missing · Human and system ratings
  differ materially · Controls are weak or unsupported · Residual risk exceeds tolerance · New
  high-risk jurisdiction or technology · A stated country could not be identified); findings
  table (category, description, related section, severity, supporting evidence, recommended
  action, status, resolve/accept with note); independent challenge sign-off record. Decision
  panel (assigned manager only): Approve to committee / Return for amendment / Reject, comment
  (required for return and reject), confirmation dialog. "Approve" disabled while any HIGH
  finding is open. Show "Decided on behalf of <manager> under delegation" when applicable.
 10c Committee (statuses Ready for Committee, Committee Review, Deferred): committee pack
  summary (inherent, residual, key findings, manager decision + comment, open issues);
  votes table (member, vote, comment, time, version; superseded votes shown struck-through with
  recast reason; "cast by delegate" note); "Cast your vote" (Approve / Dissent / Abstain +
  comment; re-cast needs reason). Separate, visually distinct "Record committee decision"
  panel (binding): Approve · Approve with conditions · Defer · Reject; Decision rationale
  (required); conditions builder (description, owner, due date, priority LOW/MEDIUM/HIGH/
  CRITICAL; at least one for "with conditions"). Clearly label votes as "Recommendations of
  members" and the decision as "Binding committee decision".
 10d Final decision record (decided statuses): decision, rationale, decided by (and on behalf
  of), date, frozen decision record with checksum, inherent and residual results, factor
  ratings (AI vs human), manager decision, votes, conditions with status (OPEN / IN_PROGRESS /
  COMPLETED / CANCELLED) and completion evidence, next review date, "Open controlled amendment"
  (committee), "Close assessment" (when no open actions), "Download decision package".

11) COMMENTS tab: threaded comments with section tag (INTAKE, EVIDENCE, RISK_FACTORS, SCORES,
RISK_RATIONALE, CONTROL_MAPPINGS, RESIDUAL_RISK, CONDITIONS, MISSING_INFORMATION), visibility
(All / Manager and above / Committee only), resolve or accept as exception (reason).

12) AUDIT HISTORY tab and global AUDIT HISTORY page
Timeline grouped by day; each event: event type label, actor (person, or "System"/"AI
analysis" with violet icon), timestamp, description, previous → new values where tracked, reason
/ related comment, source (assessment section). Filters: date range, event type (multi), actor,
search; "Export audit pack" (CSV/JSON, Auditor/Admin/Analyst). Use real event types: Created,
Draft submitted, Analysis, AI analysis degraded, Analysis unavailable, Degraded result
acknowledged, Stage advanced, Status change, Information requested, Information provided,
Manual score override, Override applied/reviewed, Control identified/assessed/updated/remapped/
unmapped, Comment added/resolved, Submitted to manager, Manager approved/returned/rejected,
Committee review opened, Committee vote cast, Committee approved / approved with conditions /
deferred / rejected, Decision record frozen, Committee condition updated, Action item created/
escalated/closure requested/approved/rejected, Workflow assigned, Target date set, Workflow
escalated, Assessment amendment opened, Assessment closed, Assessment withdrawn, Delegation
created/revoked, Methodology changed, Access denied.

13) MY WORK QUEUE (= review queue for every role)
Tabs/sections: Tasks (items whose current step belongs to me or my role) · Escalations
(overdue items escalated to me) · Escalated action items · Reassessments due. Summary counts:
"3 overdue · 2 at risk". Columns: Assessment (ID + title) · Change type · Risk level · Current
stage · Owner / team · Next action · Priority · Due (status_due_at) · SLA · Reason (Task /
Escalation). Row actions: Open, Assign to me / Assign to… (pipeline and committee roles),
Set target date. Search, filter by stage/priority/SLA, sort by due date (default). Empty:
"Nothing waiting on you right now."

14) APPROVALS (Manager and Committee Member)
Manager view: "Awaiting your challenge review" table with risk, submitted by, submitted date,
pending since, open high findings count → opens tab 10b. Committee view: "Ready for committee"
and "In committee review" with votes cast (2 of 3), → opens tab 10c. Recently decided list.

15) REPORTS (role-gated)
Tabs: Operational · Risk · Governance · High-risk portfolio · AI evaluation (Analyst, Manager,
Admin only). Period selector (last 30 / 90 days / year / custom), CSV export per panel.
Operational: Assessments by status; by risk band (residual, else inherent, else AI-assessed);
by business unit; by legal entity; average time in each status (from workflow history); overdue
assessments; open conditions and actions; deferred and rejected; completed assessments.
Risk: main risk drivers (categories by number of HIGH/CRITICAL ratings); most frequent risk
indicators; risk by geography / product / customer segment / channel; risk by typology; control
gaps by type; residual risk above tolerance; open control gaps.
Governance: human overrides (original → final, reason); unresolved findings; exceptions;
approval conditions; reassessment due dates; policy and methodology changes.
AI evaluation: model usage and cost; usage by purpose and prompt version; per-assessment
evaluation; "How these are measured". Charts: simple horizontal bars and stacked bars only.

16) ADMINISTRATION (Admin)
 - Users: table (Name, Email, Role, Manager, Access scope, Designations, Active); Create user
   modal (email, name, role, manager, initial password); edit role, manager, scope (legal
   entities, business units, countries), designations (Senior Analyst, QA Reviewer, Challenge
   Reviewer, Head of FCRM, FCRM Governance Owner, Compliance Manager, Committee Chair — tagged
   "Provisional policy, pending governance approval"), deactivate.
 - Risk Governance: methodology versions (draft → active, clone); factor weights (10 × 0.10
   default); thresholds and bands; likelihood/impact scales; residual grid editor; escalation
   rules with status Approved / Draft / Disabled; challenge-trigger configuration (mandatory
   triggers cannot be switched off); every change requires a reason and is audited.
 - System Health: AI reliability (success / degraded / unavailable counts), performance,
   integrity checks, backups (create, verify), recovery status, security posture.
 - Also in the governance group: Source Library (approved policies/procedures/control standards/
   regulatory guidance/risk frameworks/previous assessments/vendor-control documentation; status
   DRAFT/APPROVED/RETIRED; search), Delegations (delegate, authority, assessment or all, from/to
   dates, revoke), SoD Exceptions (request → independent approval → declaration → expiry/revoke),
   Retention & Legal Holds (versioned policies, eligibility, legal holds).

17) RISK CALCULATOR (all users): 5×5 likelihood × impact matrix with band colours, and a
what-if "Manual scoring calculator" per assessment (factor scores, include/exclude, weights,
"Save draft", "Apply as official score" only for analysts with a reason).

[PLANNED] Notification centre (bell icon in the top bar). Today the product only shows derived
alert pills; render the bell with a "Planned" tag and an empty-state popover.
[PLANNED] Help centre (question-mark icon) — link-style popover marked Planned.
[PLANNED] SSO and self-service password reset — not shown on Login.

-------------------------------------------------------------------------------------------------
G. END-TO-END JOURNEYS (wire the prototype to play these)
-------------------------------------------------------------------------------------------------
Journey A — Business User (Priya): Login → Dashboard → New Assessment → "Start from a document"
(extracts fields) → steps 1–6 → Submit → Success (RAW-2026-00051, HIGH, FCRM – Payments) →
Assessment Overview (status Submitted) → Business Profile → resolve 1 conflict → Confirm profile
(status Evidence Review) → later: Information Requested callout on RAW-2026-00044 → Respond →
back to RAW-2026-00042 at Analyst Review → read AI analysis (read-only) → Submit to manager →
after manager returns (Amendment Required) edit and resubmit → view Final Decision (Approved
with Conditions) and her assigned condition.
Journey B — FCRM Analyst (Arjun): Login → My Work Queue → open RAW-2026-00042 → Evidence (preview
spec, see highlighted quote) → AI Risk Analysis (review factor cards, evidence vs inference) →
Risk Ratings (accept 4 suggestions, override Third-Party L3×I3 → L3×I4 with reason, see
significant-change dialog) → result becomes final 52.0 MEDIUM… (recalculates live) → Advance to
Control Assessment (progress steps) → assess controls → Advance → Residual: confirm MEDIUM →
Advance → AI draft generated → edit and accept draft → request information from owner on
vendor due diligence → after response, case shows ready for owner to submit.
Journey C — Manager (Daniel): Login → Dashboard → Approvals → RAW-2026-00040 → Review & Decision
(10b): triggers fired, 1 HIGH finding open → Approve disabled with reason → resolve finding with
note → Approve to committee (confirm) → toast "Sent to committee" → status Ready for Committee →
decision appears in history. Alternate: Return with comment → status Amendment Required.
Journey D — Committee Member (Helen): Login → Approvals (committee) → RAW-2026-00038 → committee
pack → cast vote Approve with comment → record binding decision "Approve with conditions" with 2
conditions (owner, due date, priority) and rationale → confirmation → Final decision record
(frozen, checksum) → status Approved with Conditions → conditions appear as action items.
Journey E — Administrator (Sam): Login → Users → edit Arjun's designations → Risk Governance →
open draft escalation rule MIN_BAND_KEY_FACTOR_001 → view (approval needs the governance owner)
→ System Health → run backup verification → Audit History filtered to Methodology changed.
Journey F — Auditor (Grace): Login → Assessments (all, read-only banner) → RAW-2026-00031 → Audit
History → Export audit pack.

-------------------------------------------------------------------------------------------------
H. AI RISK ANALYSIS UX (tab 7) — explainable, not a chatbot
-------------------------------------------------------------------------------------------------
Header: "AI Risk Analysis" · RAW-2026-00042 · Analysis mode badge: "AI-assisted" (assessment_mode
ai_assisted) / "Rules only — provisional" (rules_only, amber) / "Unavailable" (red) · AI status
(success, failed, timeout, rate limited, invalid response, not attempted) · Score source ("AI and
rules" / "Deterministic rules" / "Not available") · Last analysed timestamp · "Run AI analysis"
(Analyst/Manager/Admin) · "Suggest ratings" (refresh AI L×I suggestions).
States: (1) Not yet analysed — empty state with Run button. (2) Running — progress list with
named steps and %: Loading assessment → Investigative AI screening → Risk identification engine →
Preliminary risk score → Saving results to the workbench profile; page stays usable. (3) Failed —
"Analysis could not run. Previous results kept." + Retry. (4) Degraded — amber banner "AI
unavailable — rules-only, provisional. Not evaluated: <categories>." with "Acknowledge result"
(blocks stage advance until acknowledged; shows who acknowledged). (5) Complete.
Summary row (not a gauge): Overall — while any applicable factor is unrated show "Provisional ·
5 of 8 factors unrated" and NO band; when rated show weighted score 51.5 and MEDIUM badge with
"How this was calculated" link (formula, weights, included factors, rules applied/not applied).
Category coverage strip: 10 category chips — applicable (band or Unrated), not applicable (grey,
with rationale), not evaluated (red outline).
Factor cards (one per category, expandable): header with category label, applicable flag, AI
suggested rating (violet tag) and analyst rating (navy tag) side by side, evidence status badge
(Evidence found / Insufficient evidence / Conflicting evidence / Not verified / Not applicable),
indicator chips. Body has THREE fixed blocks:
  [Evidence — quote found in source] verbatim quotes with document name, version, page, and a
  "verified" check; rejected quotes collapsed under "Claims the system could not verify".
  [AI inference — not evidence] (violet panel) rationale, misuse scenario ("how this change could
  be misused"), AI suggestion rationale.
  [Information needed] missing_information items with "Request from owner" action.
Also per card: Stage-4 rule triggers (rule id, effect FORCED_APPLICABLE / ADDED / CONFIRMED,
signals that fired), rejected indicators with reasons, linked approved-source passages from the
Source Library, comments count, "Rate this factor" → Risk Ratings tab.
Findings table view (toggle): Category · Description/rationale · Indicators · AI suggestion ·
Human rating · Rating source · Inherent band · Control rating · Residual band · Evidence status ·
Reviewer comments.
Transparency panel: "AI suggestions never change the score; only an analyst's rating does." ·
analysis timestamp · methodology version · "Model details are recorded in the AI evaluation
report" (no provider/model names in user messages) · warnings (unevaluated categories, expired
documents used, unconfirmed profile) · OCC supervisory risk-category mapping (covered / not
covered). Explainability statements panel with four tabs: Facts · Assumptions · Recommendations ·
Decisions, each statement tagged with its source.
Never show confidence percentages, probability numbers, or "AI score" for a decision.

-------------------------------------------------------------------------------------------------
I. INTERACTIVE PROTOTYPE REQUIREMENTS
-------------------------------------------------------------------------------------------------
- Sidebar and breadcrumbs navigate between all screens; active item highlighted; collapse works.
- Persona switcher (profile menu → "Switch persona (prototype)") changes sidebar items, banner,
  and available actions instantly.
- KPI cards open filtered Assessments; all table rows open the assessment; tabs switch content.
- Forms validate (inline + summary), stepper moves forward/back, Save as draft shows toast
  "Draft saved".
- Global search shows a dropdown of matching assessments by ID/title/product.
- Simulated AI analysis: clicking Run shows the 5 named steps advancing to 100%, then results.
- Rating a factor recalculates score, band and provisional state live.
- Every state-changing action (submit, advance, request information, approve, return, reject,
  vote, decide, close, withdraw, override) opens a confirmation dialog stating what will happen;
  required reasons are enforced; on confirm show a toast, update the status badge, the lifecycle
  stepper, the work queue and the audit timeline consistently.
- Notifications: toasts only (bottom-right), plus top-bar alert pills.
- Keep RAW-2026-00042 identical everywhere (ID, title, score, statuses, history).
- Mark simulated results with a subtle "Simulated" tag in the prototype.

-------------------------------------------------------------------------------------------------
J. VISUAL QUALITY BAR
-------------------------------------------------------------------------------------------------
Enterprise-grade, clean, information-dense, consistent spacing and alignment, one accent colour,
restrained iconography (Lucide-style outline icons, 16–18px), readable tables, clear hierarchy,
compliance-professional tone in all copy (short, specific, active voice; buttons say exactly
what happens; errors say what went wrong and how to fix it). Suitable for stakeholder demos.

-------------------------------------------------------------------------------------------------
K. DELIVERABLES
-------------------------------------------------------------------------------------------------
Produce ONE connected high-fidelity prototype (not isolated screenshots) at 1440px, plus mobile
(375px) variants of Login, Dashboard, My Work Queue, Assessment Overview and the Manager and
Committee decision panels. Include a design-system page (colours, type, badges, tags, buttons,
inputs, table, stepper, dialogs, toasts, empty/loading/error states).
Priority order if you must reduce scope: (1) Assessment Details with AI Risk Analysis and Risk
Ratings tabs, (2) Review & Decision tab (manager + committee + final record), (3) Dashboard,
(4) My Work Queue, (5) New Assessment, (6) Evidence tab, (7) Assessments list, (8) Audit
History, (9) Login, (10) Reports, (11) Administration.
=====
```

---

## Part 2 — Follow-up Prompts (use if Stitch shortens the master prompt)

Paste these one at a time in the same Stitch project after the master prompt.

1. **Design system page** — "Create the design-system page for Risk Assessment Workbench using the tokens in section C: colours, Inter type scale, risk badges (LOW ○, MEDIUM ◆, HIGH ▲, CRITICAL ‼, UNRATED dashed, PROVISIONAL striped), the 16 workflow-status badges, SLA indicator, priority badge, AI-suggestion (violet) and Human-decision (navy) tags, buttons, inputs, table, lifecycle stepper, form stepper, tabs, modal with required reason, toast, empty/loading/error states."
2. **App shell + Dashboard** — "Create the app shell (navy 248px sidebar with the role-based groups in section F, 56px top bar) and the Dashboard for Arjun Mehta (FCRM Analyst) with the six KPI cards, action queue table, risk distribution (Unrated hatched), status bars, recent activity, using the sample assessments in section E."
3. **Assessment Details – Overview + AI Risk Analysis** — "Design RAW-2026-00042 Assessment Details: header strip, 9-step lifecycle stepper at Risk Identification, action bar with a disabled 'Advance' listing its gates, and the AI Risk Analysis tab per section H, showing the three-block factor card for GEOGRAPHIC_RISK and TRANSACTION_ACTIVITY_RISK, the coverage strip and the provisional overall result."
4. **Risk Ratings** — "Design the Risk Ratings tab per section F-8 with all 10 categories, AI suggestion vs analyst rating columns, live score, the significant-change confirmation dialog, and the sticky summary panel."
5. **Controls & Residual** — "Design the Controls & Residual tab per F-9 with the residual grid highlighting MEDIUM × PARTIAL = MEDIUM."
6. **Review & Decision** — "Design the Review & Decision tab per F-10: (a) analyst draft with Advisory recommendation, (b) Manager challenge review for RAW-2026-00040 with Approve disabled by one HIGH finding, (c) committee votes and binding decision for RAW-2026-00038, (d) final decision record for RAW-2026-00031."
7. **New Assessment** — "Design the 6-step intake per F-4 including 'Start from a document' provenance tags, duplicate warning, the evidence upload step and the success page."
8. **Evidence + preview drawer** — "Design the Evidence tab and document preview drawer per F-6, including masked Restricted document and expiry acknowledgement."
9. **Work Queue, Approvals, Audit, Reports, Admin, Login** — "Design screens 1, 12–17 per section F."
10. **Wire it** — "Connect all screens for journeys A–F in section G with the interactions in section I, and produce 375px variants listed in section K."

---

## Part 3 — Codebase Analysis Notes (team reference, do not paste)

### 3.1 Architecture discovered

| Layer | Verified in code |
|---|---|
| Frontend | React 19, TypeScript, Vite 8, plain CSS (`App.css` ~88 KB). No router, no UI kit, no chart library. Page switching via `currentPage` state in `App.tsx`; icon-only 68px rail sidebar with tooltips; header search filters the dashboard. Main screens: `DashboardPage`, `AssessmentsPage`, `CreateAssessment`, `AssessmentWorkflow` (one ~290 KB component holding every stage), `WorkQueuePage`, `ApprovalsPage`, `ReportsPage`, `AuditHistoryPage`, `RiskCalculatorPage`/`ManualScoringCalculator`, `SourceLibraryPage`, `DelegationsPage`, `SodExceptionsPage`, `RetentionAdminPage`, `UsersAdminPage`, `GovernancePage`, `SystemHealthPage`, `LoginPage`. |
| Backend | FastAPI; 20 routers under `/api`; one access gate (`auth/access.py`) on every router: signed-in, read-only roles blocked on writes, per-assessment visibility and scope, soft-deleted assessments hidden, same-case admin actions refused under dual-role SoD exceptions. |
| Workflow | `services/workflow.py` is the only place status changes: pipeline `status` (INTAKE … AUDIT, plus SUBMITTED_TO_MANAGER, RETURNED_BY_MANAGER, MANAGER_REJECTED, READY_FOR_COMMITTEE, COMMITTEE_REVIEW, REMEDIATION, INFORMATION_REQUESTED, CLOSED) and derived business `workflow_status` (16 values). Transition table with role tokens OWNER / ASSIGNED_MANAGER; SLA days per status adjusted by priority; escalations; mandatory-issue gate before committee (`governance/readiness.py`). |
| Database | PostgreSQL (Neon) via SQLAlchemy/Alembic (migrations to 0022); SQLite for tests. Tables listed in the prompt §B. |
| AI | LangGraph 5-node graph; background processing jobs with named step progress; OpenAI-compatible provider (default `gpt-4.1-mini`, embeddings `text-embedding-3-small`; OpenRouter alternative). AI modules: document extraction, risk-factor analyzer, likelihood/impact suggestion, control identifier/design assessment, assessment-draft generator, embeddings (similar assessments / duplicate check / source search). Deterministic quote verification; degraded mode (`ai_assisted` / `rules_only` / `unavailable`) with acknowledgement gate. Scores are never LLM output. |

### 3.2 Implemented capabilities (verified)

- Email/password JWT login; 9 roles; access scope by legal entity / business unit / country; governance designations (provisional policy).
- Intake with drafts, 15 mandatory fields, document-based auto-extraction, duplicate check, triage priority (LOW/MEDIUM/HIGH/URGENT) and routing to a team/queue, reference IDs.
- Evidence: document upload with type/confidentiality/owner/source/effective/expiry/versioning, background text extraction, masking of confidential text, secure download rules, expiry acknowledgement, evidence gaps.
- Business profile extraction with per-field provenance and categorical confidence, conflicts, confirmation, intake history snapshots.
- 10-category AI risk identification with indicators, verified evidence, missing information, Stage-4 deterministic rule triggers, misuse scenarios, AI-suggested L×I kept separate from analyst ratings, exclusion with reason, manual factors.
- Deterministic inherent scoring (configurable methodology, weights, bands, escalation rules with approved/draft/disabled), inherent override ledger.
- Controls library, AI control identification, design/effectiveness assessments with versions, unmap/remap, control gaps, residual grid, residual confirmation, recommended conditions.
- AI decision-ready draft (advisory) with uncertainty section, edit/accept/versions.
- Comments with section and visibility, resolve/accept exception; information request/response loop.
- Challenge review: triggers, findings, resolve/accept, independent sign-off; HIGH findings block manager approval.
- Manager decision (approve/return/reject, comment required for return/reject, no self-approval, delegation).
- Committee: append-only votes with recast reason, binding decision with rationale and structured conditions, defer, amendment, frozen decision record with checksum, decision package.
- Action items (sources: control gap, committee condition, missing evidence, policy exception, vendor remediation, monitoring enhancement) with closure approval and escalation.
- Work queue (tasks, escalations, escalated action items, reassessments due), SLA states, assign/claim, target date.
- Reassessment triggers, compare, supersede; retention policies and legal holds; SoD exceptions; delegations.
- Reports: operational, risk, governance, high-risk portfolio, AI evaluation (with CSV export); audit trail, explainability statements (facts / assumptions / recommendations / decisions), audit export.
- Admin: users, methodology versions, challenge-trigger config, source library, system health/backups.

### 3.3 Partial or provisional (show as-is, don't over-promise)

- Governance designations, SoD tiers, retention periods, Stage-4 signal lists and two of the escalation rules are **provisional / draft** pending governance approval (`docs/GOVERNANCE_DECISIONS.md`).
- Legacy `fcrm-review` endpoint (justification + per-dimension human ratings) is superseded by factor ratings; the prompt models FCRM review as the Analyst Review stage.
- Manager, committee members and auditors see masked text for Confidential/Restricted originals (open question Q-7).
- Reassessment compare is structural (by category/control type), not a full diff.

### 3.4 Not implemented (marked Planned in the prompt or omitted)

- Notification centre / email notifications (only compiled remnants exist; no source).
- SSO and self-service password reset (no source; admins reset passwords).
- Help centre.
- URL routing / deep links (recommended by `design/ux/ux-decisions.md`, not built).
- Per-document "review status" and an evidence-completeness percentage (not modelled — the prompt uses a readiness checklist and evidence gaps instead).
- Any numeric AI confidence score.

### 3.5 UX improvements identified (no backend change needed)

1. Replace the icon-only rail with a labelled, grouped, collapsible sidebar; 14 flat icons are hard to learn.
2. Break the single long `AssessmentWorkflow` page into URL-addressable tabs so managers can deep-link to a finding and the back button works.
3. Always show "waiting on whom / next action" (already returned by `GET /workflow` as `owner.next_action`) in the header.
4. Disabled actions list their unmet gates (mandatory issues, unrated factors, unacknowledged degraded run, open HIGH findings).
5. Unrated and provisional values get their own visual state; never render as LOW/0.
6. Consistent AI-vs-human tagging everywhere AI suggestions and human values sit together.
7. Split "votes" (recommendations) from the "binding decision" visually in the committee view.
8. Evidence preview with highlighted cited quotes, linked back to factors.
9. Read-only roles: hide actions instead of showing disabled ones; keep the banner.
10. Separate the intake into a 6-step guided form with a missing-field summary (fields and validation unchanged).

### 3.6 Screens to prioritise for the hackathon demo

1. Assessment Details → AI Risk Analysis + Risk Ratings (the "AI proposes, people decide" story).
2. Review & Decision (Manager challenge → Committee vote/decision → frozen decision record).
3. Dashboard and My Work Queue (role-aware entry points with SLA).
4. New Assessment with document extraction.
5. Evidence tab with preview drawer; Audit History.
