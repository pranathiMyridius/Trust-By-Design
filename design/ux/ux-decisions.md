# UX Decisions

## 1. Principles

1. **Never show missing as safe.** Unrated, provisional, rules-only and unavailable each have a distinct, labelled visual state. None may render as LOW or 0.
2. **Separate evidence from inference.** Three fixed blocks on every finding: *Evidence (quote found in source)*, *AI inference — not evidence*, *Information needed*.
3. **Explain disabled actions.** A disabled primary button always lists the gates it is waiting on (from `GATE_NOT_MET.details.missing`).
4. **The server is the source of truth.** After every mutation the screen re-reads the affected resources; no locally computed scores.
5. **Progress, not spinners.** Long operations show named steps and percentages from `processing_jobs.stage_key`.
6. **Accessible by default** (existing pass retained): keyboard, focus management, colour + shape + text for risk levels, `role="alert"/"status"`.

## 2. Routing (react-router)

| Route | Screen |
|---|---|
| `/` | S1 Dashboard |
| `/assessments` | List (filters in query string) |
| `/assessments/new` | S2 |
| `/assessments/:id` → redirect to current stage | S3 |
| `/assessments/:id/intake · evidence · risk-factors · controls · residual · review · decision` | S3 stage modules (S4–S10 inside) |
| `/assessments/:id/documents/:docId` | S8 |
| `/assessments/:id/audit` | S12 |
| `/work-queue`, `/approvals`, `/delegations`, `/reports`, `/calculator` | existing pages |
| `/admin/users`, `/admin/governance`, `/admin/system`, `/audit` | admin pages |

Justification: deep links let a manager open exactly the finding a comment refers to, browser back works, and refresh no longer loses context (current state-based routing loses all three).

## 3. One stage model

The UI renders the stage tracker from `GET /api/workflow/config` (pipeline statuses in order + labels). Display stages: Intake · Evidence · Risk factors · Controls · Residual · Review · Manager · Committee · Decision. Mapping from pipeline status is defined once in `features/assessment/stages.ts`. The last-viewed stage in localStorage is a convenience only and never exceeds the server-permitted stage.

## 4. Visual states for risk values

| State | Badge | Text |
|---|---|---|
| CRITICAL / HIGH / MEDIUM / LOW | Filled, colour + icon shape (existing `RiskLevelBadge`) | Band name + score |
| UNRATED | Outline, "?" icon | "Unrated" |
| PROVISIONAL | Amber stripe on the badge | "Provisional" + reason tooltip |
| RULES-ONLY | Amber banner at top of stage | "AI unavailable — rules-only, provisional. Not evaluated: …" |
| UNAVAILABLE | Red banner | "Analysis could not run. Previous results kept." |
| OVERRIDDEN | Badge + pencil icon | "Overridden from HIGH by Meera — reason" |
| Confidence | Text chip HIGH/MEDIUM/LOW with "why" popover (coverage numbers) | |

## 5. Tool strategy

| Tool | Use for | Not for |
|---|---|---|
| **Mermaid** (`design/diagrams/*.mmd`) | All technical diagrams: architecture, sequence, ER, workflow, state machine, deployment. Version-controlled next to code; renders in GitHub/GitLab and IDEs | UX mockups |
| **Figma** | Wireframes and hi-fi mock-ups of S1–S12, component library (badges, factor card, evidence block, progress stepper), prototype of Journeys 3–5 for the demo | Architecture |
| **Excalidraw** | Whiteboard sessions and the 1-slide conceptual picture for the hackathon pitch ("AI proposes → rules decide → people approve") | Anything that must stay in sync with code |
| **draw.io** | Only if an enterprise architecture board requires a formal diagram (e.g. network zones) | Default diagrams |
| **Miro** | Only for a collaborative workshop (e.g. retro, stakeholder journey mapping) | Design artefacts of record |

The team does **not** need all five: Mermaid + Figma are the working set; Excalidraw for the pitch.

## 6. Decisions on known UX defects

| Defect (from E2E / inspection) | Decision |
|---|---|
| Unrated factors shown as LOW/0 (D-15) | UNRATED badge; API exposes `rated: bool` |
| Sidebar inherent score stale after rating | Re-fetch `/scores` after each rating; single `RiskSummary` component |
| No UI for degraded acknowledgement (partly fixed) | Banner + Acknowledge in Risk factors stage (analyst+) |
| Refused advance does not show new profile until reload | Re-fetch intelligence after any 409 on advance |
| Misleading gate messages | Structured `GATE_NOT_MET` items mapped to plain text in `utils/gates.ts` |
| Business user sees rating form then gets 403 | Hide/disable by permission; read-only rendering |
| Admin cannot see Approvals nav | Show for ADMIN |
| "Submitted by" defaults to "System" | Field removed |
| `window.alert` for document errors | Inline alert |
| Blank screen while auth loads | Full-page skeleton |
| Calculator logs itself on load | Save only on explicit user action |
| OCC profile (credit/liquidity/…) | Removed from UI (out of FC scope) |
