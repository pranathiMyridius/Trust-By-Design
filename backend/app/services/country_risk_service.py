"""
Loading and querying published country-risk designations.

The loader is snapshot-versioned rather than destructive: a new load
marks the previous rows for that source superseded and inserts a fresh
set, so an assessment completed under an earlier FATF statement can
still be explained against the list that was actually in force.
"""

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models.country_risk import CountryRisk
from app.models.reference_data_snapshot import ReferenceDataSnapshot
from app.reference_data.country_normalizer import (
    ISO_NAMES,
    resolve_countries,
)

REFERENCE_DATA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "reference_data",
)

FATF_SNAPSHOT = os.path.join(REFERENCE_DATA_DIR, "fatf_jurisdictions.json")
EU_SNAPSHOT = os.path.join(REFERENCE_DATA_DIR, "eu_high_risk_third_countries.json")

ALL_SNAPSHOTS = (FATF_SNAPSHOT, EU_SNAPSHOT)

# Every tier any loaded source can produce, with a rank used to order
# designations and pick an overall severity, and the phrase findings use
# to describe it. Kept here rather than in the challenge rules so those
# stay source-agnostic: adding a source means adding tiers here, not
# editing rule logic.
#
# Rank 3 is reserved for designations that reflect a standing public
# call for action or an equivalent finding of ongoing substantial risk;
# rank 2 for other serious designations; rank 1 for "committed to an
# action plan" style listings, which are a country-risk input rather
# than a call for enhanced due diligence in their own right.
TIER_META: dict[str, dict[str, Any]] = {
    # FATF
    "CALL_FOR_ACTION": {
        "rank": 3,
        "severity": "HIGH",
        "label": "subject to a call for action",
    },
    "INCREASED_MONITORING": {
        "rank": 1,
        "severity": "MEDIUM",
        "label": "under increased monitoring",
    },
    # EU, Delegated Regulation 2016/1675 Annex I-IV.
    "EU_ONGOING_SUBSTANTIAL_RISK": {
        "rank": 3,
        "severity": "HIGH",
        "label": "a high-risk third country presenting ongoing and substantial ML/TF risks",
    },
    "EU_TECHNICAL_ASSISTANCE": {
        "rank": 3,
        "severity": "HIGH",
        "label": "a high-risk third country seeking technical assistance on its FATF action plan",
    },
    "EU_FATF_MEMBERSHIP_SUSPENDED": {
        "rank": 2,
        "severity": "HIGH",
        "label": "a high-risk third country whose FATF membership is suspended",
    },
    "EU_COMMITMENT_ACTION_PLAN": {
        "rank": 1,
        "severity": "MEDIUM",
        "label": "a high-risk third country with a political commitment and FATF action plan",
    },
}

_UNKNOWN_TIER = {"rank": 1, "severity": "MEDIUM", "label": "designated"}


def tier_meta(tier: str) -> dict[str, Any]:
    return TIER_META.get(tier, _UNKNOWN_TIER)


def load_snapshot(db: Session, path: str = FATF_SNAPSHOT) -> dict[str, Any]:
    """
    Load a reference snapshot into country_risk, superseding whatever
    was current for that source. Idempotent in effect: re-loading the
    same snapshot produces the same current set.

    Returns a summary for the caller to log or print.
    """

    with open(path, encoding="utf-8") as handle:
        snapshot = json.load(handle)

    return load_snapshot_data(db, snapshot)


def snapshot_checksum(snapshot: dict[str, Any]) -> str:
    canonical = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_snapshot_data(
    db: Session,
    snapshot: dict[str, Any],
    loaded_by: str | None = None,
) -> dict[str, Any]:
    """
    load_snapshot for an already-parsed snapshot. Records a
    ReferenceDataSnapshot identified by the content checksum. Loading
    content identical to the current snapshot for that source is a no-op,
    so a reviewer's attestation is not lost by a routine reload; any
    change produces a new, unattested snapshot.
    """

    source = snapshot["source"]
    as_of = snapshot["as_of"]
    verified = bool(snapshot.get("verified", False))
    note = snapshot.get("verification_note")
    checksum = snapshot_checksum(snapshot)

    now = datetime.now(timezone.utc)

    current_snapshot = (
        db.query(ReferenceDataSnapshot)
        .filter(ReferenceDataSnapshot.source == source, ReferenceDataSnapshot.is_current.is_(True))
        .first()
    )
    if current_snapshot is not None and current_snapshot.checksum == checksum:
        return {
            "source": source,
            "as_of": as_of,
            "verified": verified,
            "snapshot_id": current_snapshot.id,
            "attested": current_snapshot.attested,
            "loaded": 0,
            "superseded": 0,
            "unchanged": True,
            "codes_unknown_to_normalizer": [],
        }

    if current_snapshot is not None:
        current_snapshot.is_current = False
        current_snapshot.superseded_at = now

    record = ReferenceDataSnapshot(
        source=source,
        as_of=as_of,
        source_url=snapshot.get("source_url"),
        statement=snapshot.get("statement"),
        checksum=checksum,
        entry_count=len(snapshot["jurisdictions"]),
        source_claims_verified=verified,
        source_verification_note=note,
        loaded_by=loaded_by,
        loaded_at=now,
        is_current=True,
    )
    db.add(record)
    db.flush()

    previous = (
        db.query(CountryRisk)
        .filter(CountryRisk.source == source, CountryRisk.is_current.is_(True))
        .all()
    )

    for row in previous:
        row.is_current = False
        row.superseded_at = now

    unknown_codes = []

    for entry in snapshot["jurisdictions"]:
        iso_code = entry["iso_code"].upper()

        # A code the normalizer doesn't know would load fine but could
        # never be matched from intake text, so surface it rather than
        # letting it sit in the table doing nothing.
        if iso_code not in ISO_NAMES:
            unknown_codes.append(iso_code)

        db.add(
            CountryRisk(
                iso_code=iso_code,
                name=entry["name"],
                tier=entry["tier"],
                source=source,
                as_of=as_of,
                verified=verified,
                notes=note,
                snapshot_id=record.id,
            )
        )

    db.commit()

    return {
        "source": source,
        "as_of": as_of,
        "verified": verified,
        "snapshot_id": record.id,
        "attested": False,
        "unchanged": False,
        "loaded": len(snapshot["jurisdictions"]),
        "superseded": len(previous),
        "codes_unknown_to_normalizer": unknown_codes,
    }


def get_current_designations(db: Session) -> dict[str, list[dict[str, Any]]]:
    """
    {alpha-2: [designation, ...]} for every currently designated country,
    most severe designation first.

    A country can legitimately appear on more than one list with
    different meanings -- Myanmar is a FATF call-for-action jurisdiction
    and sits in Annex I of the EU regulation -- and the lists can
    disagree outright, since the EU list lags FATF (Algeria and Namibia
    remain EU-listed after FATF removed them). Collapsing that to a
    single "worst" tier would hide exactly the detail a reviewer needs,
    so every designation is kept.
    """

    rows = (
        db.query(CountryRisk)
        .filter(CountryRisk.is_current.is_(True))
        .all()
    )

    designations: dict[str, list[dict[str, Any]]] = {}

    for row in rows:
        meta = tier_meta(row.tier)
        designations.setdefault(row.iso_code, []).append(
            {
                "name": row.name,
                "tier": row.tier,
                "tier_label": meta["label"],
                "severity": meta["severity"],
                "rank": meta["rank"],
                "source": row.source,
                "as_of": row.as_of,
                "verified": row.verified,
            }
        )

    for entries in designations.values():
        entries.sort(key=lambda entry: (-entry["rank"], entry["source"]))

    return designations


def assess_countries(db: Session, values: list[str]) -> dict[str, Any]:
    """
    The single entry point callers should use: free-text country
    references in, designations out.

    `unresolved` is always returned and is never empty-by-omission --
    text that isn't a recognised country is reported so an analyst can
    correct it, rather than being silently read as "no exposure".
    """

    resolved, unresolved = resolve_countries(values)
    designations = get_current_designations(db)

    matches = []
    for iso_code, matched_text in resolved.items():
        entries = designations.get(iso_code)
        if not entries:
            continue

        worst = entries[0]

        matches.append(
            {
                "iso_code": iso_code,
                "matched_text": matched_text,
                # The published name from the most severe designation;
                # sources spell things differently and this keeps one
                # canonical label per match.
                "name": worst["name"],
                "tier": worst["tier"],
                "severity": worst["severity"],
                "rank": worst["rank"],
                "sources": sorted({entry["source"] for entry in entries}),
                "designations": entries,
            }
        )

    matches.sort(key=lambda m: (-m["rank"], m["iso_code"]))

    return {
        "resolved": resolved,
        "unresolved": unresolved,
        "matches": matches,
        "highest_tier": matches[0]["tier"] if matches else None,
    }


def scoring_jurisdiction_context(db: Session, countries_text: str | None) -> dict[str, Any]:
    """
    What the scoring rules may rely on for an assessment's countries:
    designations from *attested* current snapshots only.

    Returns:
      matches           [{iso_code, name, tier, source, as_of, snapshot_id}]
                        -- one entry per (country, designation), from
                        attested snapshots
      snapshots_used    references to the attested snapshots consulted
      snapshots_skipped current snapshots NOT used because nobody has
                        attested them, with any countries they would have
                        matched, so a reviewer sees what was held back
      unresolved        country text that matched no known country
    """

    values = [part.strip() for part in re.split(r"[,;/\n]+", countries_text or "") if part.strip()]
    resolved, unresolved = resolve_countries(values)

    snapshots = (
        db.query(ReferenceDataSnapshot)
        .filter(ReferenceDataSnapshot.is_current.is_(True))
        .order_by(ReferenceDataSnapshot.source)
        .all()
    )
    by_id = {snapshot.id: snapshot for snapshot in snapshots}

    rows = (
        db.query(CountryRisk)
        .filter(
            CountryRisk.is_current.is_(True),
            CountryRisk.iso_code.in_(list(resolved) or [""]),
        )
        .all()
    )

    matches: list[dict[str, Any]] = []
    held_back: dict[int | None, list[str]] = {}

    for row in rows:
        snapshot = by_id.get(row.snapshot_id)
        entry = {
            "iso_code": row.iso_code,
            "name": row.name,
            "tier": row.tier,
            "source": row.source,
            "as_of": row.as_of,
            "snapshot_id": row.snapshot_id,
        }
        if snapshot is not None and snapshot.attested:
            matches.append(entry)
        else:
            held_back.setdefault(row.snapshot_id, []).append(f"{row.iso_code}:{row.tier}")

    matches.sort(key=lambda m: (-tier_meta(m["tier"])["rank"], m["iso_code"], m["source"]))

    skipped = []
    for snapshot in snapshots:
        if not snapshot.attested:
            skipped.append({**snapshot.as_reference(), "would_match": held_back.pop(snapshot.id, [])})
    if held_back.get(None):
        skipped.append(
            {
                "snapshot_id": None,
                "source": "legacy rows (no snapshot record)",
                "attested": False,
                "would_match": held_back[None],
            }
        )

    return {
        "matches": matches,
        "snapshots_used": [s.as_reference() for s in snapshots if s.attested],
        "snapshots_skipped": skipped,
        "resolved": resolved,
        "unresolved": unresolved,
    }


def get_high_risk_iso_codes(db: Session) -> list[str]:
    """
    Alpha-2 codes for every currently designated country -- what the
    challenge engine's `high_risk_jurisdictions` config is populated
    from, so that rule stops depending on a hand-maintained list.
    """

    return sorted(get_current_designations(db))
