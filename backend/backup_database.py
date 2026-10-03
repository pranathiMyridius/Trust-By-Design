"""
Stage 19: create or verify a backup from the command line (e.g. from a
scheduled task / cron, independent of the running app). Run from backend/:

    python backup_database.py                 # create a backup
    python backup_database.py --list          # list backups
    python backup_database.py --verify NAME   # verify checksums/integrity

See docs/BACKUP_AND_RECOVERY.md.
"""

import argparse
import json

from app.services import backup


def main() -> None:
    parser = argparse.ArgumentParser(description="Risk Assessment Workbench backups")
    parser.add_argument("--list", action="store_true", help="list existing backups")
    parser.add_argument("--verify", metavar="NAME", help="verify an existing backup")
    parser.add_argument("--label", default="cli", help="label stored in the manifest")
    args = parser.parse_args()

    if args.list:
        print(json.dumps(backup.list_backups(), indent=2))
    elif args.verify:
        result = backup.verify_backup(args.verify)
        print(json.dumps(result, indent=2))
        raise SystemExit(0 if result["valid"] else 1)
    else:
        print(json.dumps(backup.create_backup(label=args.label, created_by="CLI"), indent=2))


if __name__ == "__main__":
    main()
