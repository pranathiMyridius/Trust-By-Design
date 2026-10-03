"""
Load the country-risk reference snapshots into the database.

    python load_country_risk.py                      # load every snapshot
    python load_country_risk.py path/to/one.json     # load just one

Re-run after each FATF plenary (roughly three times a year) and after
each amending EU Delegated Regulation, once the snapshot under
app/reference_data/ has been updated from the primary publication. Each
source is superseded independently, and superseded rows are kept rather
than deleted, so assessments completed under an earlier list remain
explainable.
"""

import sys

from app.database import SessionLocal
from app.services.country_risk_service import (
    ALL_SNAPSHOTS,
    get_current_designations,
    load_snapshot,
)


def main() -> int:
    paths = sys.argv[1:] or list(ALL_SNAPSHOTS)

    db = SessionLocal()
    try:
        for path in paths:
            result = load_snapshot(db, path)

            print(
                f"Loaded {result['loaded']} jurisdiction(s) from "
                f"{result['source']} (as of {result['as_of']}); superseded "
                f"{result['superseded']} previously current row(s)"
            )

            if not result["verified"]:
                print(
                    "  WARNING: marked verified=false -- not checked against the "
                    "primary publication.\n           Verify before relying on it "
                    "for a real assessment."
                )

            if result["codes_unknown_to_normalizer"]:
                print(
                    "  WARNING: ISO codes unknown to country_normalizer.py, so "
                    "they can never be\n           matched from intake text. Add "
                    "them to ISO_NAMES: "
                    + ", ".join(result["codes_unknown_to_normalizer"])
                )

        designations = get_current_designations(db)

        by_tier: dict[str, int] = {}
        for entries in designations.values():
            for entry in entries:
                by_tier[entry["tier"]] = by_tier.get(entry["tier"], 0) + 1

        print(f"\n{len(designations)} distinct countries designated:")
        for tier, count in sorted(by_tier.items()):
            print(f"  {tier}: {count}")

        multi = {
            code: sorted({entry["source"] for entry in entries})
            for code, entries in designations.items()
            if len({entry["source"] for entry in entries}) > 1
        }
        if multi:
            print(f"\nListed by more than one source ({len(multi)}):")
            for code, sources in sorted(multi.items()):
                print(f"  {code}: {', '.join(sources)}")

        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
