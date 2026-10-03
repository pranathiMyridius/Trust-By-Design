# Governance Decisions Needed: Risk Assessment Workbench

**Prepared:** 2026-10-03 · **For:** FCRM governance, Compliance, Legal, and the Risk Committee chair · **Status of every item: PENDING GOVERNANCE APPROVAL**

## Why this document exists

The Workbench is built and tested. Some rules in it were never specified by the requirements brief, so the project put in **provisional defaults** (decisions of 2026-10-02):
- each default is a configuration setting, not hard-coded policy;
- the screens that depend on one show "Pending Governance Approval";
- the API returns `policy_status: PROVISIONAL_PENDING_GOVERNANCE_APPROVAL`.

On 2026-10-03 the project received a set of **proposed answers**. They are included under each item as **Proposed answer**. They are a draft for approval: **nothing in the system has been changed to match them.** Where a proposal differs from today's behaviour, the item says what would change and roughly how much work it is.

**Each item has:**
- **Today:** what the system does now.
- **Proposed answer:** the draft answer, with its proposed decision text.
- **Change from today:**
  - none (the proposal matches the system);
  - **config** (a setting, no development);
  - **small**, **medium** or **large** code change.
- **Note:** an engineering comment, where useful.

**Urgency:**
- 🔴 **Before go-live**: the rule is enforced on every case, every day.
- 🟠 **Before disposal**: needed only before records may be deleted. Nothing is deleted today.
- 🟢 **Can follow**: low impact, so the current default is acceptable for now.

The proposals and notes are implementation recommendations. They are not legal or compliance advice.

### Numbering

The proposal numbered its later items differently. This pack keeps one set of numbers:

| Proposal | This pack | Topic |
|---|---|---|
| G-1 to G-9 | G-1 to G-9 | same topics |
| G-10 Retention period | G-13 | Retention periods |
| G-11 Retention exceptions and legal holds | G-14 | Approving retention changes and legal holds |
| G-12 Legal-hold triggers | **G-18** (new) | Legal-hold triggers |
| G-13 Purge authorization + G-14 Deletion process | G-16 | Disposal sign-off process |
| G-15 Risk-methodology configuration | **G-19** (new) | Methodology change control |
| G-16 Source-library approval | **G-20** (new) | Source library governance |
| G-17 Review intervals | G-17 | Reassessment intervals |
| none | G-10, G-11, G-12, G-15 | No proposal yet; the engineering recommendation stands |

---

## Summary

| # | Decision | Urgency | Proposed answer (short) | Change from today |
|---|---|---|---|---|
| G-1 | SoD exception approval | 🔴 | Head of FCRM/Compliance (standard); Committee Chair (high-risk, repeated, long) | Config, plus a small change |
| G-2 | Admins as committee members | 🔴 | No by default; only through a time-bound exception, with that case's administration blocked | Small |
| G-3 | Override review | 🔴 | An independent Senior Analyst, FCRM Manager, QA or Head of FCRM approves material overrides | Config, plus a small change |
| G-4 | Challenge-review sign-off | 🔴 | An independent reviewer; HIGH findings may proceed if the Committee accepts them; CRITICAL needs a Committee decision | Medium |
| G-5 | What blocks Committee | 🔴 | Add evidence, critical control gap, quorum and manager-approval blockers | Medium |
| G-6 | Governance visibility | 🔴 | **Least privilege**: no role sees everything by title | **Large** (reverses today) |
| G-7 | LOW risk and Committee | 🔴 | **No Committee** for LOW unless an escalation trigger applies | **Medium–large** (reverses the 2026-10-02 decision) |
| G-8 | Confidential originals | 🔴 | Need-to-know by assignment; Admin only through logged break-glass access | **Large** |
| G-9 | Stage 4 keyword lists | 🔴 | Controlled, versioned reference data in 10 groups, owned by FCRM; suggestions only | Config + medium |
| G-10 | Expired evidence deciders | 🟢 | (no proposal) | — |
| G-11 | Confidence scale | 🟢 | (no proposal) | — |
| G-12 | Intake changes that cancel confirmation | 🟢 | (no proposal) | — |
| G-13 | Retention periods | 🟠 | 7 years from the **latest** of closure, decision, expiry or remediation closure | Config + small |
| G-14 | Retention changes and legal holds: approvals | 🟠 | Joint Legal + Head of Compliance/FCRM; only Legal releases a hold | Small (needs a Legal designation) |
| G-15 | Retention limits and rules | 🟠 | (no proposal) | — |
| G-16 | Disposal process | 🟠 | Deletion request, holds check, dual approval, grace period, purge, deletion certificate | Large (it is the purge feature) |
| G-17 | Reassessment intervals | 🟢 | 6 / 12 / 24 / 36 months, due 30 days early; plus material-change triggers | None for the intervals; small for the triggers |
| G-18 | Legal-hold triggers | 🟠 | A defined list of trigger types | Small |
| G-19 | Methodology change control | 🟢 | A dedicated Methodology Administrator; maker-checker; Committee approves material changes | Medium |
| G-20 | Source library governance | 🟢 | An owner per source type; independent approval | Medium |

**Gaps found while comparing** (not decisions; see the end): an Admin listed as able to make the manager's decision (fixed 2026-10-03), methodology changes have no maker-checker step, sources can be approved by the person who created them, and one MANAGER role covers both business and FCRM managers.

---

## 🔴 Before go-live

### G-1 SoD exception approval (R15.2, R-GOV-01)

**Today:**
- **Standard tier:** approved by an FCRM Governance Owner, the Head of FCRM, or a Compliance Manager.
- **Committee tier:** approved by the Committee Chair. An exception goes to this tier if any of these is true:
  - its risk is HIGH or CRITICAL;
  - the case's band is HIGH or CRITICAL;
  - it lasts more than 30 days;
  - it is enterprise-wide;
  - the same person has had 2 or more other exceptions in the last 365 days.
- **Duration:** at most 90 days, with an expiry warning 7 days before.
- **Who can never approve:** the requester, the affected person, that person's direct manager, and anyone involved in the case.
- **Who may revoke:** an Admin, the FCRM Governance Owner, the Head of FCRM, or the Committee Chair.
- Every exception records the conflict, justification, compensating controls, scope, approver, start and expiry dates, and is audited and shown to the Committee.

**Proposed answer:**
- Standard exceptions are approved by the Head of FCRM/Compliance or an approved FCRM Governance delegate.
- High-risk, critical, repeated, long-duration or senior-role exceptions go to the Risk Committee Chair or an authorized delegate.
- An exception counts as high-risk when it lets one person do two or more of these on the same case:
  - create or submit the case;
  - give the manager approval;
  - do the FCRM assessment;
  - create an override;
  - approve that override;
  - sign off the challenge review;
  - vote at Committee;
  - administer users, workflow, permissions, evidence access or methodology.

> **G-1 — Segregation of Duties Exceptions:** The system shall prohibit conflicting role combinations by default. Standard, temporary SoD exceptions may be approved by the Head of FCRM/Compliance or an approved FCRM Governance delegate. High-risk, critical, repeated, or long-term exceptions require approval by the Risk Committee Chair or authorized delegate. Requestors, affected users, and direct managers must not approve their own exceptions. Every exception must state the conflict, justification, compensating controls, approved scope, approver, start date, expiry date, and audit reference.

**Change from today:**
- **Config:** the approver list and thresholds. The numbers "long-duration" (today more than 30 days) and "repeated" (today 2 in 365 days) still need to be confirmed.
- **Small:** today's tier rule uses the risk level and the case band, not "two or more conflicting activities". Adding that rule and a "senior role" test is a small change.

### G-2 Admins as committee members (R-GOV-02)

**Today:**
- An Admin can't vote by default.
- The only way is an approved, declared dual-role exception. One that is enterprise-wide or on a HIGH/CRITICAL case needs the Chair.
- While the exception is in force, the Admin is blocked from:
  - **all** user and system configuration;
  - administration of the covered case.

**Proposed answer:** the roles are mutually exclusive by default. A dual role needs a formally approved, time-bound exception. While acting as a committee member on a case, the user is blocked from administration that affects that case: users, permissions, routing, thresholds, reassignment, confidentiality labels, evidence access, methodology, committee membership, audit records, and editing the final decision after sign-off.

> **G-2 — Administrator and Committee Role Separation:** System Administrator and Committee Member roles are mutually exclusive by default. A dual role may be granted only through a formally approved, time-bound SoD exception. When acting as a Committee Member on an assessment, the user shall be blocked from carrying out administrative actions affecting that assessment. The dual-role exception, conflict declaration, and all related activity must be retained in the audit trail.

**Change from today:**
- **Small:** the proposal is narrower than today. Today all user and system administration is blocked; the proposal blocks only actions "affecting that assessment". Global settings, such as methodology and thresholds, do affect every case, so we suggest keeping those blocked globally.
- **Open:** the proposal doesn't say whether the Chair must approve every dual-role exception (today: only high-risk, enterprise-wide, long or repeated ones).

### G-3 Override review (R-GOV-03)

**Today:**
- **Proposer:** an FCRM Analyst, including a Senior Analyst.
- **Reviewer:** a Senior Analyst, QA Reviewer or FCRM Manager.
- **Approver of material changes:** an FCRM Manager or the Head of FCRM. Critical changes: the Head of FCRM only.
- The proposer, reviewer, approver and case owner are all different people.
- The system decides what is "material" from the actual effect.

**Proposed answer:**
- **Proposers:** an FCRM Analyst, Senior Analyst or authorized FCRM Manager.
- **Independent reviewers and approvers:** a Senior Analyst not involved in the case, an FCRM Manager, a QA Reviewer, or the Head of FCRM for high-risk and critical overrides.
- **Never:** the Business User, the requester's manager, the override's author, an Admin, or anyone with a conflict.
- **Material changes:**
  - factor rating;
  - inherent score or band;
  - control design or effectiveness;
  - residual score or band;
  - removing a mandatory escalation;
  - evidence sufficiency;
  - removing or downgrading a challenge finding;
  - recommended conditions.

> **G-3 — Override Review:** Material overrides shall be independently reviewed and approved by a Senior FCRM Analyst, FCRM Manager, FCRM Quality Assurance Reviewer, or Head of FCRM who was not involved in creating the override. The system shall retain the original value, revised value, reason, evidence, proposer, reviewer, approval decision, and timestamps. The override creator may not approve their own override.

**Change from today:**
- **Config:** let FCRM Managers propose overrides.
- **Small:** add "evidence sufficiency", "removing a mandatory escalation" and "downgrading a challenge finding" as material effects. Most of the others are already material today.

**Note:** "the requester's manager" must be excluded. But today one MANAGER role covers both the business line manager and the FCRM manager (see *Gaps*). The system already excludes the case's assigned manager as a conflicted party in SoD and readiness checks. Whether it also excludes them from reviewing overrides needs to be confirmed when this is implemented.

### G-4 Challenge-review sign-off (R-GOV-03)

**Today:**
- **Review:** a Challenge Reviewer, Senior Analyst or QA Reviewer who didn't prepare the case.
- **Sign-off:** the FCRM Manager or Head of FCRM, who must not be the reviewer.
- **Blocking findings:** HIGH and CRITICAL findings block until resolved. MEDIUM findings block unless accepted with a reason. LOW findings never block.
- CRITICAL cases are flagged to the Committee.

**Proposed answer:** an independent reviewer: not the creator, submitter, original analyst, override author, or product owner.

| Finding | Signed off by | Can the Committee proceed? |
|---|---|---|
| Low | FCRM Analyst or Challenge Reviewer | Yes, if shown in the decision package |
| Medium | Independent Senior Analyst or FCRM Manager | Only if resolved or formally accepted |
| High | FCRM Manager or Head of FCRM | No, until resolved **or formally accepted by the Committee** |
| Critical | Head of FCRM **plus** Risk Committee | No, until the Committee makes a documented decision |

> **G-4 — Challenge Review Sign-Off:** Challenge reviews shall be performed and signed off by an independent authorized FCRM reviewer. High-severity findings must be resolved before Committee submission unless the Risk Committee formally accepts the exception. Critical findings require Head of FCRM review and explicit Committee decision.

**Change from today:** **medium.**
- Sign-off would depend on how severe each finding is.
- A new step lets the Committee accept a HIGH finding, which is stricter in one way and looser in another than today's "HIGH always blocks".
- The Committee would explicitly acknowledge CRITICAL findings.
- "The product owner" is not an independence check today; it would be added.

**Note:** the table says HIGH findings may go to the Committee if the Committee accepts them. The decision text says they must be resolved *before Committee submission* unless accepted. Please confirm whether acceptance happens before submission (for example by the Chair) or at the meeting.

### G-5 Committee readiness (R-GOV-04)

**Today, these block:**
- missing mandatory fields;
- no residual risk;
- unresolved comments;
- open HIGH or CRITICAL findings, or unaccepted MEDIUM findings;
- an incomplete challenge review or sign-off;
- overrides awaiting review or approval;
- a rejected override still in effect;
- votes by a conflicted person without an exception.

Manager approval is enforced by the workflow status, not by readiness. Expired evidence is enforced earlier, at risk identification.

**Proposed answer:** block both Committee submission and the final decision for:
- missing manager approval, FCRM review or required evidence;
- expired or rejected evidence (unless an accepted exception exists);
- material or critical overrides awaiting review;
- open HIGH or CRITICAL findings;
- an SoD conflict without an exception;
- an **unresolved critical control gap**;
- **no Committee quorum**.

Low findings and non-material corrections may stay open if they are visible to the Committee.

> **G-5 — Committee Decision Readiness:** The system shall block Committee submission and final sign-off when mandatory approvals, required evidence, independent FCRM review, material override review, required challenge review, SoD controls, or critical control-gap remediation are incomplete. Low-severity findings and non-material administrative corrections may remain open only where the Committee can view them and applicable policy permits progression.

**Change from today:** **medium.** New blockers:
- **Critical control gap:** today only caught if the challenge engine raises it as a finding.
- **Quorum:** the system has no quorum concept today, so a quorum rule (the number or share of members) must be defined.
- **Rejected evidence:** no "rejected" state exists today.
- **Manager approval and expired evidence** become explicit readiness checks.

**Note:** an Admin can't stand in for the manager's approval (*Gaps*, item 1, fixed 2026-10-03).

### G-6 Governance visibility

**Today:**
- **See every assessment:** Admin, FCRM Analyst, Auditor, Executive, and the governance role holders (Head of FCRM, Governance Owner, Compliance Manager, Committee Chair, QA Reviewer, Challenge Reviewer).
- **Managers:** see the cases they manage or own, and cases delegated to them.
- **Committee members:** see cases at the committee stage.
- **Business users:** see their own cases.
- Legal-entity and business-unit limits apply to everyone.

**Proposed answer:** **least privilege.** No role sees everything because of its title.

| Role | Sees |
|---|---|
| Business User | Own cases, or cases they are assigned to support |
| Manager | Direct reports' cases or their assigned business unit |
| FCRM Analyst | Assigned cases and authorized portfolio |
| FCRM Manager | Team portfolio and escalated cases |
| Challenge Reviewer | Cases assigned for challenge |
| Committee Member | Their Committee queue |
| Committee Chair | Committee queue plus escalated and exception cases |
| Auditor | Read-only, within approved audit scope |
| Legal | Cases needing legal review or under legal hold |
| System Administrator | Technical access only; business content only if separately authorized |
| FCRM Governance Owner | Full oversight only where formally assigned |

> **G-6 — Governance Case Visibility:** Access to assessments shall follow least-privilege and need-to-know principles. Governance, FCRM, Committee, Audit, Legal, and Administrative roles shall receive access based on approved scope, legal entity, business unit, assignment, confidentiality classification, and documented oversight responsibility. No governance role shall automatically receive unrestricted access solely because of title or system role.

**Change from today:** **large.** This reverses today's model for Admin, FCRM Analyst, Auditor, Executive and the governance roles. It needs things that don't exist yet:
- case **assignment** to individual analysts and challenge reviewers;
- **portfolios** and **audit scopes**;
- a **Legal** role;
- "technical-only" Admin access.

Every list, report and dashboard would need re-testing; the route-by-role access test already covers all 197 API operations and would catch regressions.

**Suggestion:** if approved, phase it in. Start with Admin (technical access only) and the governance roles. Then FCRM analyst assignment. Then audit scopes.

### G-7 LOW-risk cases and the Committee

**Today:** every risk level goes through the manager and the Committee. This was the provisional decision of 2026-10-02: no shortcut. A methodology setting (LOW requires "Analyst" only) exists, but it adds checks; it doesn't change the route.

**Proposed answer:** LOW risk goes Business User → Manager approval → FCRM validation → final record, **with no Committee**. The case goes to Committee only if an escalation trigger applies:
- a high-risk or prohibited country;
- sanctions exposure;
- a new third-party processor or critical vendor;
- cross-border payment capability;
- an unresolved control gap;
- an SoD exception;
- a material override;
- a Legal or Compliance referral;
- a Chair escalation;
- adverse media, fraud, bribery or corruption exposure;
- a material change to an approved product.

> **G-7 — Low-Risk Approval Path:** Low-risk assessments shall not require Risk Committee approval by default. They shall require Business Manager approval and independent FCRM validation. The system shall route a Low-risk case to the Committee when a mandatory escalation trigger applies, including sanctions exposure, high-risk geography, material third-party dependency, unresolved control gap, SoD exception, material override, or FCRM/Legal/Compliance escalation.

**Change from today:** **medium–large.**
- It **reverses the 2026-10-02 decision**, so it needs explicit sign-off as a change.
- It needs a new final-approval step by FCRM, with its own decision record.
- It needs routing rules. Most triggers can be detected automatically: the sanctions indicator, the Stage 4 cross-border and third-party rules, open control gaps, SoD exceptions and material overrides. Adverse media, referrals and Chair escalation need a manual "escalate" action.
- Decide whether a LOW case that is later re-rated MEDIUM or above must go back to the Committee. We recommend yes.

### G-8 Confidential original files

**Today:**
- Only the case owner, FCRM Analysts and **Admins** can open CONFIDENTIAL or RESTRICTED originals.
- Everyone else sees masked text and can't download.
- Every view, download and refusal is logged.

**Proposed answer:** need-to-know, at document level. Allowed, when assigned:
- the document owner;
- the assigned manager;
- the assigned FCRM Analyst;
- the assigned Challenge Reviewer;
- an authorized FCRM Manager;
- Committee members reviewing that case;
- Auditors within audit scope;
- Legal, when required.

Further rules:
- Admins only through **logged break-glass access** for technical support, with a review afterwards.
- People see a document's metadata first and must give a reason before seeing its content.
- Download is disabled by default for highly confidential material.
- Previews, prints and exports are logged.
- **SAR/STR and investigation material gets its own, more restricted classification.**
- Legal advice is restricted to Legal.

> **G-8 — Confidential Original Files:** Confidential original files shall be accessible only to users with a documented need to know and authorized role/assignment. Original-file access must be controlled at document level, logged, and limited by confidentiality classification. System Administrators have no default business-content access and may use break-glass access only for approved technical support or recovery, with additional logging and post-event review.

**Change from today:** **large.**
- Removing Admin's default access is small.
- These are new:
  - assignment-based access (see G-6);
  - a "reason required" step;
  - break-glass access with review;
  - a new SAR/STR classification;
  - a download-off setting for each classification.

**Note:** SAR/STR material carries tipping-off risk. The system treats it like any other CONFIDENTIAL or RESTRICTED document until the new classification exists. Until then, we suggest such material is not uploaded.

### G-9 Stage 4 keyword lists

**Today:**
- Three deterministic rules: cross-border payment, remote digital channel and third-party processor.
- Each uses a keyword list, versioned and marked `PROVISIONAL_PENDING_BUSINESS_VALIDATION`.
- A rule only makes the relevant risk factor appear, **unrated**, for an analyst to rate or exclude with a reason. It never sets an indicator or a rating.
- Matching does not understand negation ("no third party" still fires).

**Proposed answer:** controlled, versioned reference data in 10 groups:
- product;
- customer;
- geography;
- channel;
- transaction;
- technology;
- third party;
- typology;
- controls;
- escalation.

The lists **suggest** risk factors, evidence searches, questions and controls, and never set a rating or decision by themselves. The FCRM Methodology Owner owns them. SMEs review the content, and the Head of FCRM approves. They are reviewed quarterly. Every change records the version, rationale, effective date, approver and test results.

> **G-9 — Stage 4 Keyword Lists:** Stage 4 keyword lists shall be controlled, versioned reference data owned by the FCRM Methodology Owner. They may identify potential risk factors, evidence, controls, missing information, and escalation triggers, but they shall not independently determine final risk ratings or approval decisions. Changes require documented testing, approval, version control, and audit logging.

**Change from today:**
- **Config:** the ownership and the approval of the existing three lists. This is consistent with today: "never decides".
- **Medium:**
  - growing from 3 forcing rules to 10 suggestion groups is a new feature: suggestions for evidence, questions and controls;
  - an in-app approval workflow for list changes. Today a list is a JSON file swapped by configuration.

**Note:** please confirm the three existing rules stay **mandatory**: the factor *must* be rated or excluded. The new groups would only suggest. The brief requires the three Stage 4 considerations.

---

## 🟢 Can follow (no proposal yet)

### G-10 Expired evidence
**Today:**
- An expired document is used only after someone decides "use" or "exclude", with a reason of at least 10 characters.
- The owner, an FCRM Analyst, a Manager or an Admin may decide.

**Engineering recommendation:** remove Admin; this is a business judgement. **(small)**

### G-11 Confidence scale for extracted fields
**Today:** three levels.
- **HIGH:** an AI-cited quote was found word for word in a document.
- **MEDIUM:** the value appears in a document, but there is no verified quote.
- **LOW:** the value appears nowhere in the documents.

The AI's own confidence is never used.

**Engineering recommendation:** accept.

### G-12 Intake changes that cancel a confirmed profile
**Today:** changing a field in `material_intake_fields` (customer types, products, channels, geographies, volumes and similar) withdraws the confirmation.

**Engineering recommendation:** confirm the list.

---

## 🟠 Before any record is disposed of

Nothing is deleted today; every record is kept. These decisions are needed before the deletion (purge) feature is built.

### G-13 Retention periods
**Today:**
- **Assessments:** 2,555 days (7 years) from the final decision date. This is provisional and **not compliance-approved**.
- **Documents, audit events and AI usage logs:** no period, so never eligible.

**Proposed answer:**
- 7 years after the **latest** of: case closure, final decision, assessment expiry, or remediation closure.
- The period covers assessments, evidence references, approvals, decisions, overrides, challenge findings, audit events and remediation records.
- Records are kept longer where law, regulation, an investigation, an audit, a dispute, litigation, a contract or a legal hold requires it.
- The proposal notes that AML/CFT guidance commonly expects at least five years after a relationship ends. 7 years is a safer default.

> **G-10 (proposal) — Record Retention:** Assessment records, evidence references, approvals, decisions, overrides, challenge findings, audit events, and remediation records shall be retained for seven years after the later of case closure, final decision, assessment expiry, or remediation closure. Records must be retained longer where law, regulation, investigation, audit, dispute, litigation, contractual obligation, or legal hold requires it.

**Change from today:**
- **Config:** the period, which is the same 2,555 days as today, and the periods for the other record types.
- **Small:** the start date becomes "the latest of four dates" instead of the final decision date only.

**Legal must confirm 7 years for your jurisdictions** before it is recorded as approved.

### G-14 Approving retention changes and legal holds
**Today:**
- **Retention periods:** an Admin proposes; an FCRM Governance Owner or Compliance Manager approves. The two must be different people.
- **Legal holds:** an Admin or Compliance Manager places and releases them, and a different person must release a hold from the one who placed it.

**Proposed answer:**
- Any change to retention (shortening, extending or varying it) needs **both** Legal **and** the Head of Compliance/FCRM.
- A legal hold overrides every schedule and stays until **Legal releases it in writing**.

> **G-11 (proposal) — Retention Exceptions and Legal Holds:** Legal and Head of Compliance/FCRM approval is required to shorten, extend, or otherwise vary retention for an assessment record. A legal hold supersedes all standard deletion or retention schedules and remains active until released in writing by Legal.

**Change from today:** **small.**
- A new **Legal** designation; none exists today.
- Two approvals instead of one.
- Release limited to Legal, with a written-release reference. The existing `matter_reference` field can hold it.

### G-15 Retention limits and rules (no proposal yet)
**Today:**
- A period must be between 365 and 36,500 days, and a justification must be at least 20 characters.
- Auditors can read the eligibility report.
- A changed period applies to all records, including old ones.
- Each assessment in a reassessment chain has its own clock.

**Engineering recommendation:**
- Confirm the limits and the Auditor access.
- Apply period changes only to records decided afterwards. **(code)**
- Keep a whole reassessment chain until its newest member's period ends. **(code)**

The proposal's "latest of four dates" rule (G-13) partly covers chains through "remediation closure", but not the original assessment.

### G-16 Disposal sign-off process
**Today:** there is no deletion. Records are only listed in a read-only eligibility report.

**Proposed answer:** no direct delete. **Dual authorization:** Records Management/Legal **and** the Head of Compliance/FCRM or Data Owner. The person who starts the deletion can't approve it. High-risk or confidential cases may also need Data Protection or InfoSec approval.

The process:
1. Eligibility is found.
2. The retention period is checked.
3. Checks for open actions, reassessments, audits, investigations and holds.
4. A deletion request is created.
5. Legal approves.
6. Compliance or the Data Owner approves.
7. A grace period passes.
8. The controlled purge runs.
9. A deletion certificate and an immutable audit event are kept.

No deletion while any of these is true:
- a hold is active;
- remediation is open;
- a reassessment is active;
- an audit is incomplete;
- an enquiry is open;
- an approval is missing.

**A restore from backup must not bring back purged records.**

> **G-13 (proposal) — Purge Authorization:** Permanent deletion of an assessment record requires dual authorization from Records Management/Legal and Head of Compliance/FCRM or the authorized Data Owner. The user initiating deletion may not be one of the approvers. High-risk or confidential cases may require additional Data Protection or Information Security approval according to policy.
>
> **G-14 (proposal) — Deletion and Purge Process:** The system shall not permit direct permanent deletion by ordinary users. It shall create a deletion request, validate retention eligibility and legal holds, require dual approval, apply a defined review/grace period, execute a controlled purge, and retain an immutable deletion certificate showing record scope, approvals, timestamps, legal-hold check, and purge result.

**Change from today:** **large.** This is the purge feature itself. Decisions still needed:
- **Grace period length.** Not given in the proposal.
- **Restores.** A backup taken before a purge still contains the purged records. "Must not reintroduce" needs a purge ledger that is re-applied after any restore. We recommend building that ledger into the purge feature.
- **Backups and off-site copies.** Backups expire on their own cycle (today 14 are kept); off-site copies need a documented expiry.
- **What the certificate contains.** We recommend record identifiers, scope, approvals, timestamps, the hold check and the result, and **no record contents**.

### G-18 Legal-hold triggers (new, from the proposal)
**Today:** a hold has a required free-text reason and an optional matter reference. It has no trigger type.

**Proposed answer:** a hold may be placed for:
- litigation or a credible threat of it;
- an internal or external investigation;
- a regulatory enquiry or examination;
- an audit preservation request;
- a SAR/STR-related review;
- a suspected financial-crime investigation;
- a customer complaint or dispute;
- a Legal instruction;
- a Compliance or FCRM instruction;
- a preservation request from law enforcement.

Legal, Compliance and FCRM may place holds. A held record can't be deleted, anonymised or purged.

> **G-12 (proposal) — Legal Hold Triggers:** The system shall permit Legal, Compliance, and FCRM to apply a legal hold to an assessment or related evidence when litigation, investigation, regulatory enquiry, audit, suspicious activity, dispute, law-enforcement request, or other preservation obligation exists. A held record cannot be modified through deletion, anonymization, or purge processes until the hold is formally released.

**Change from today:** **small.**
- A required trigger-type field (a migration).
- FCRM and Legal added to the people who may place holds. Today only Admin and Compliance Manager can.
- "Related evidence" means holds at document level as well as assessment level. Today holds are per assessment, and the documents follow their assessment.

---

## 🟢 Can follow

### G-17 Reassessment intervals
**Today:**
- **Intervals,** by risk band: CRITICAL 6 months, HIGH 12, MEDIUM 24, LOW 36 (12 if there is no band). A review comes due 30 days early.
- **Triggers:** product, geography, customer segment, vendor, volume, channel and technology changes are detected automatically. Regulatory changes and control failures are flagged manually.
- **Other details:**
  - amending an approved reassessment leaves the original "superseded";
  - a reassessment can be started on a closed case that was never approved.

**Proposed answer:** the same intervals and the same 30-day rule, **plus** immediate reassessment on any material change.

> **G-17 — Assessment Review Cycle:** Critical assessments shall be reviewed every 6 months, High assessments every 12 months, Medium assessments every 24 months, and Low assessments every 36 months. The system shall create the review task 30 days before the scheduled review date. A material business, risk, control, vendor, technology, regulatory, or geographic change shall trigger reassessment regardless of the scheduled review cycle.

**Change from today:**
- **None** for the intervals: the proposal matches the system.
- **Small:** add four trigger types: material audit finding; significant fraud, AML or suspicious-activity event; adverse media or regulatory issue; and a Committee condition requiring reassessment.

**Still open:** the two details above. The engineering recommendation is to allow reassessment only of approved cases.

### G-19 Methodology change control (new, from the proposal)
**Today:**
- Only an Admin can create, edit and activate a methodology version.
- Versions are kept, and a version is locked once it has been used.
- **Activating a version counts as approving it**, so the Admin who edits a version can also activate it.

**Proposed answer:** a dedicated **FCRM Methodology Administrator** role, separate from System Administrator.
- **Standard changes** (wording, evidence fields, labels, non-material keywords) are approved by the Methodology Owner.
- **Material changes** (weights, thresholds, bands, escalation rules, the residual calculation, routing, review intervals) need documented testing and approval by the Head of FCRM, Compliance, and the Risk Committee or a delegate.

> **G-15 (proposal) — Risk Methodology Configuration:** Risk methodology configuration shall be controlled by a dedicated FCRM Methodology Administrator role. Material changes to scoring, weights, thresholds, risk bands, escalation rules, routing, or review intervals require documented testing, version control, Head of FCRM and Compliance approval, and Risk Committee approval or delegated governance approval.

**Change from today:** **medium.**
- A new role.
- A maker-checker approval step before a version can be activated.
- A classification of changes as standard or material.
- Review intervals become methodology settings. Today they are fixed in the code.

### G-20 Source library governance (new, from the proposal)
**Today:**
- A Policy Admin or Admin creates, edits, approves and retires sources, and **the same person can create and approve a source**.
- Sources have an effective date, a review date and approval details, but **no owner**.
- A passed review date is flagged on the evidence, but the source's status doesn't change.

**Proposed answer:** a source can be used as evidence only after its **Source Owner** approves it:

| Source type | Approved by |
|---|---|
| Internal AML/FCRM policy | Head of FCRM/Compliance |
| Regulatory source | Compliance or Legal |
| Legal interpretation | Legal plus Compliance |
| Sanctions source | Sanctions Compliance Owner |
| Country-risk source | Country Risk or Methodology Owner |
| Typology source | Methodology Owner |
| Vendor evidence | Vendor Risk Owner plus an FCRM reviewer |
| Historical assessment | FCRM Manager or Records Owner |

Every source records its owner, authority, effective date, version, approval status, review date, jurisdiction, classification and history.

> **G-16 (proposal) — Source Library Governance:** Sources may be submitted by authorized users but may be used as formal assessment evidence only after approval by the relevant Source Owner. Regulatory interpretation requires Compliance and, where appropriate, Legal review. Every source shall have an owner, authority, effective date, version, approval status, review date, jurisdiction, classification, and audit history.

**Change from today:** **medium.**
- Owner, jurisdiction and classification fields (a migration).
- Approval routed by source type.
- The creator can't approve their own source.
- New Sanctions, Vendor Risk and Country Risk owner designations.

---

## Gaps found while comparing the proposals with the system

These are facts about today's system, not decisions. Most are fixed by the proposals above if they are approved. The first should be fixed either way.

1. ~~**An Admin can make the manager's decision.**~~ **Fixed 2026-10-03.** On closer inspection:
   - The manager-decision endpoint already refused Admins: it accepts only the assigned manager or a delegate.
   - But the shared workflow rule (`app/services/workflow.py`) still listed Admin for the three manager transitions. The UI was told an Admin was "allowed", and any other code path using that rule could have let an Admin decide. Refused attempts were also not logged.

   The fix:
   - Admin is removed from those transitions; an absent manager is covered by an audited delegation.
   - Every refused manager decision is now logged as `ACCESS_DENIED`.
   - Tests: `tests/api/test_manager_decision_authority.py`.
2. **One MANAGER role covers two jobs.** It is both the business line manager who approves the request and the FCRM manager who reviews overrides and signs off challenges. The proposals (G-3, G-4, G-6) treat these as different people. **Recommendation:** add an FCRM Manager designation, so the two can be told apart.
3. **Methodology changes have no maker-checker step** (G-19).
4. **A source can be approved by the person who created it** (G-20).
5. **There is no Legal role or designation**, but several proposals give Legal duties (G-6, G-8, G-14, G-16, G-18).

## Evidence still needed (not a decision)

- **Database encryption at rest:** Neon encrypts its storage. The application can't prove this. Someone with Neon account access should file Neon's current security and compliance report (for example SOC 2).
- **Production migration:** production is at version 0017. Moving it to the current version (0022) is a separate change with its own approval. Migrations 0020–0022 are validated on staging.

---

## Decision record

Complete one row per item. Engineering will then update the configuration or schedule the code change, remove the "Pending" marking and record the reference.

| # | Decision (accept proposal / keep today / change to …) | Decided by (name, role) | Date | Approval reference (minutes, ticket) |
|---|---|---|---|---|
| G-1 | | | | |
| G-2 | | | | |
| G-3 | | | | |
| G-4 | | | | |
| G-5 | | | | |
| G-6 | | | | |
| G-7 | | | | |
| G-8 | | | | |
| G-9 | | | | |
| G-10 | | | | |
| G-11 | | | | |
| G-12 | | | | |
| G-13 | | | | |
| G-14 | | | | |
| G-15 | | | | |
| G-16 | | | | |
| G-17 | | | | |
| G-18 | | | | |
| G-19 | | | | |
| G-20 | | | | |

### Full decision record (for items that need more than a row)

```markdown
Decision ID / Title:
Decision: [the approved decision]
Options considered: 1. … 2. … 3. …
Reason for decision: [governance, compliance, legal, operational and risk rationale]
Scope: [entities, business units, users, assessment types, jurisdictions]
Implementation impact: configuration / workflow / role and permission / data model / code / reporting and audit
Effective date:            Review date:
Approved by (name, role):  Approval date:
Approval reference / Committee minutes:
Recorded by:               Date recorded:
Supporting documents:
```
