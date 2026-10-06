# Source Library

Governed regulatory and internal-policy sources (RBI/FATF/FFIEC/FCA/EBA/OFAC guidance, sanctions
resources, internal policies, procedures, control libraries, risk methodologies) with versions, a
review workflow, an audit trail, PDF processing, vector search, and verified citations in AI risk
analysis.

## How it works

```
DRAFT --submit--> IN_REVIEW --approve--> APPROVED --(successor approved)--> SUPERSEDED
                      |  \--reject--> REJECTED --(edit)--> DRAFT
                      \--withdraw--> DRAFT              any open/approved --retire--> RETIRED
```

* A **source record** (`SRC-RBI-KYC-001`) owns **versions**; each version is an official link and/or an
  uploaded PDF. At most one version per record is APPROVED (enforced by a partial unique index).
* A new version **never replaces** the approved one automatically; the approved version is superseded only
  at the moment a reviewer approves its successor. Only one open version (draft / in review / rejected) at a time.
* Roles (provisional, configurable in `app/governance/policy.py` -> `source_library`, overridable through
  `GOVERNANCE_POLICY_FILE`):
  * **Maintainers** (Policy Admin, Admin): create sources and versions, upload, edit drafts, submit, retire.
  * **Reviewers** (designations Compliance Manager, FCRM Governance Owner, Head of FCRM): approve or reject.
    A plain Admin cannot approve.
  * Auditors can read drafts, history and the audit timeline; they change nothing.
  * **Separation of duties** (hard rule, no exception route): anyone who created, edited, uploaded or submitted
    a version cannot approve or reject it. Decisions need a comment (>= 10 characters). Refused attempts are audited.
* Everything is append-only audited: `source_approvals` (submissions/decisions) and `source_audit_logs`
  (every change, download, refused upload, refused action), plus the application-wide audit trail.
* Changing category/jurisdiction/topics of a source with an approved version needs a recorded reason.

## Documents

PDF only, <= `SOURCE_MAX_UPLOAD_MB` (25), `%PDF-` header required. Every upload is checked for type/size, de-duplicated by SHA-256,
malware-scanned, stored privately under a random name (`SOURCE_LIBRARY_STORAGE_DIR`, default `uploaded_files/source_library`,
encrypted at rest when `FILE_ENCRYPTION_KEY` is set), and served only through the authenticated API (`nosniff`, `no-store`, audited).
Text is extracted per page (pypdf); chunks (~220 words, 40 overlap) keep page and section (heading detection is heuristic) and never
span two sections. Failures (encrypted, scanned/no text, corrupt) are recorded on the version and block submission.

Malware scanning: built-in tripwire (EICAR, PDF `/JavaScript` `/JS` `/Launch` `/EmbeddedFile` `/RichMedia`) **plus ClamAV** when
`CLAMAV_HOST` (`CLAMAV_PORT` 3310) is set. `MALWARE_SCAN_REQUIRED` (default true when `APP_ENV=production`) refuses uploads that
could not be scanned by ClamAV. The built-in layer is not an antivirus engine - **deploy ClamAV in production**.

## Embeddings and retrieval

* Embeddings use the existing provider (`app/ai/embeddings.py`) and are stored in `source_embeddings` (pgvector HNSW on PostgreSQL).
  They are on by default on PostgreSQL only (`SOURCE_EMBEDDINGS_ENABLED` overrides); a failure leaves keyword search working
  (`embedding_status` UNAVAILABLE/PARTIAL).
* Retrieval (`app/services/source_retrieval.py`) returns only passages whose version is APPROVED, record ACTIVE, already in effect,
  and whose jurisdiction applies (global/unspecified, the home jurisdiction `source_library.home_jurisdiction` = India, or one the assessment
  names; EU member states also match "European Union"). Endpoints: `GET /api/source-library/approved`, `GET /api/source-library/passages`.

## LangGraph / AI integration

`identify_risks` retrieves applicable approved passages and gives them to the model as a **REFERENCE LIBRARY** (`LIB:n`).
They are deliberately **not** citable evidence: a regulation mentioning sanctions must not tag a sanctions indicator. The model may add
`source_citations`; each is checked by exact string match against the passage it names. Verified citations are stored on the risk factor
(`risk_factors.source_citations`) with source ID, title, version, page/section, quote and hash; invented, unknown or unsupported ones are
kept as *rejected*. Citations never change indicators, evidence status, ratings or scores, and a factor whose explanation names a source
without a verified quote is flagged. Analysts still rate every factor; humans still decide. The analysis audit event lists the sources supplied.
Governed sources reach the older keyword "knowledge context" only through this filtered path (`exclude_governed`).

## Setup

```bash
cd backend
pip install -r requirements.txt
python -m app.migrations status                      # migration 0027_source_library
python -m app.migrations upgrade --confirm-host <host>   # remote DBs need authorisation (see docs/DEPLOYMENT.md)
python -m app.services.source_catalog seed --dry-run     # starter catalogue: 31 official/internal sources as DRAFTS
python -m app.services.source_catalog seed
```

Migration 0027 is additive. It also gives every row of the older `approved_sources` table a record and version (status kept; help articles
excluded; the older rows are not modified). Downgrade refuses while documents or approval history exist.
The starter catalogue creates **drafts only**; links other than the RBI KYC Direction are best-known official landing pages and
**must be confirmed by the source owner** before approval. Reviewers approve each source (the UI can also load it: empty library -> "Load the starter catalogue").

Environment: `SOURCE_MAX_UPLOAD_MB`, `SOURCE_MAX_PDF_PAGES`, `SOURCE_LIBRARY_STORAGE_DIR`, `SOURCE_EMBEDDINGS_ENABLED`, `CLAMAV_HOST`,
`CLAMAV_PORT`, `CLAMAV_TIMEOUT_SECONDS`, `MALWARE_SCAN_REQUIRED`, `SOURCE_PDF_ALLOW_ACTIVE_CONTENT`, `FILE_ENCRYPTION_KEY`.

## Testing

```bash
cd backend
python -m pytest tests/unit/test_source_documents.py tests/unit/test_source_library_migration.py \
    tests/api/test_source_library_governance.py tests/api/test_source_retrieval.py \
    tests/api/test_source_catalog.py tests/workflow/test_library_citations.py
cd ../frontend && npm run test:e2e -- source-library.spec.ts   # Playwright (starts backend + Vite)
```

## Deployment checklist

1. Back up the database; run the migration (authorised host). 2. Assign reviewer designations to the FCRM Compliance Owner / Legal-Compliance
users (Users admin) - nobody can approve until at least two reviewers exist, because of separation of duties. 3. Deploy ClamAV and set
`CLAMAV_HOST`; set `FILE_ENCRYPTION_KEY`; put `SOURCE_LIBRARY_STORAGE_DIR` on private, backed-up storage. 4. Confirm pgvector. 5. Seed the catalogue,
confirm links, capture/upload controlled copies, submit, and have reviewers approve.

## Manual compliance steps (not automated)

Approval of every source; confirming official URLs and version/effective dates; capturing PDFs of web-only sources; setting review dates;
sanctions list matches are alerts for human disposition, never decisions; the governance policy values above are provisional until approved.
