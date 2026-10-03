"""
Stage 19: restore a backup made by backup_database.py / the admin API.

Stop the application first. Run from backend/:

    python restore_backup.py backup-20260923T101500000000Z --confirm

The backup is verified (checksums + SQLite integrity check) before
anything is touched, and a safety backup of the current state is taken
first, so a restore can itself be undone. Postgres restores are done with
pg_restore -- see docs/BACKUP_AND_RECOVERY.md.
"""

import argparse
import json
import time

from app.services import backup


def main() -> None:
    parser = argparse.ArgumentParser(description="Restore a Risk Assessment Workbench backup")
    parser.add_argument("name", help="backup folder name, e.g. backup-20260923T101500000000Z")
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="required: acknowledges the live database will be replaced",
    )
    args = parser.parse_args()

    if not args.confirm:
        raise SystemExit(
            "Refusing to restore without --confirm. This replaces the live database "
            "(a safety backup of the current state is taken first)."
        )

    started = time.perf_counter()
    result = backup.restore_backup(args.name)
    result["duration_seconds"] = round(time.perf_counter() - started, 2)
    result["recovery_time_objective_hours"] = backup.RTO_HOURS
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
