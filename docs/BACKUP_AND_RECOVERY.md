# Backup and Recovery (Stage 19)

## Recovery objectives

| Objective | Default | Setting | Meaning |
|---|---|---|---|
| Recovery point objective (RPO) | **24 hours** | `RECOVERY_POINT_OBJECTIVE_HOURS` | The most data that may be lost: the newest verified backup must never be older than this. |
| Recovery time objective (RTO) | **4 hours** | `RECOVERY_TIME_OBJECTIVE_HOURS` | The longest the service may be down while restoring. |

Agree these values with the business owner and set them in `backend/.env`. The admin **System Health** page (and `GET /api/system/recovery-status`) shows whether the latest backup is within the RPO.

## What a backup contains

Each backup is a folder under `BACKUP_DIR` (default `backend/backups/`), for example `backup-20260923T101500123456Z/`:

- `database.sqlite.enc`: a consistent online copy made with SQLite's backup API, safe while the app is running. On Postgres it is `database.dump.enc` from `pg_dump -Fc` instead, and `pg_dump` must be on the PATH.
- `uploaded_files/`: every stored evidence file, encrypted.
- `manifest.json`: the SHA-256 checksum of every file, row counts per table, whether the backup is encrypted, and who made the backup and when.

A backup is only given its final name once it has been written completely. If a backup fails partway through, it is discarded.

### Encryption

When `FILE_ENCRYPTION_KEY` is set (it must be in production), backups are encrypted with it, in the same format as uploaded files:

- The database copy is taken into memory and written **only encrypted**. No plaintext copy of the database is ever written to disk. Verifying and restoring also decrypt in memory.
- Uploads that are still plaintext are encrypted as they are copied into the backup. The live files are not changed.
- `manifest.json` holds only metadata (file names, checksums, row counts) and is not encrypted.
- Without the key, a backup is written as plaintext (`database.sqlite` / `database.dump`), marked `encrypted: false` on the System Health page, and a warning is logged.

**Restoring needs the key the backup was made with.** Keep `FILE_ENCRYPTION_KEY`, and any retired key that older backups or off-site copies were made with, in your secret store, separately from the backups. A backup that can't be decrypted fails verification with "can't be decrypted with the configured keys".

`pg_restore` can't read an encrypted dump directly. Decrypt it first (see *Postgres* below).

### Key rotation

1. Generate a new key: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.
2. Set `FILE_ENCRYPTION_KEY=<new key>` and `FILE_ENCRYPTION_PREVIOUS_KEYS=<old key>` (comma-separated for several), then restart. New files and backups now use the new key, and everything made with the old key can still be read.
3. From `backend/`, run `python scripts/rotate_file_encryption_key.py`. This dry run counts the files under the current key, under a previous key, unreadable, and not encrypted. It changes nothing.
4. Run it again with `--apply`. It re-encrypts uploads and backups (including logical snapshots' `*.enc` files) under the new key. Each file is checked before it atomically replaces the original, and backup manifests are updated so verification still passes.
5. When a dry run reports **0 under a previous key and 0 unreadable**, remove `FILE_ENCRYPTION_PREVIOUS_KEYS` and restart. Keep the old key in the secret store for as long as an off-site copy made before the rotation might need restoring.

The script never prints file names, contents or keys. It exits non-zero if any file can't be decrypted with the configured keys. Don't retire a key until those files are explained.

## Schedule and retention

- **Automatic:** the app takes a backup every `BACKUP_INTERVAL_HOURS` (default 24). Keep this value at or below the RPO. Set it to `0` to turn automatic backups off, for example when the database platform already takes backups.
- **Retention:** the newest `BACKUP_KEEP` backups are kept (default 14). This is **backup** retention: how many database backup files are kept for recovery. It is separate from **record** retention (P5), the versioned, governed period for which assessment records are kept before they may be put forward for a controlled disposal review (`retention_policy_versions`, Retention & Legal Holds page). A record that is soft-deleted under the record retention policy still exists in the database and in every backup; expiring backups that contain disposed records is an open item that needs an approved disposal process.
- **Off-site copy:** copy `BACKUP_DIR` to separate storage, such as object storage or another host. A backup kept on the same disk doesn't protect against losing that disk.
- **External scheduler:** `python backup_database.py` makes a backup without the app running, for example from cron or Windows Task Scheduler.

## Verifying backups

Run a verification at least weekly, or automate it:

```bash
python backup_database.py --verify backup-20260923T101500123456Z
```

This re-computes every checksum against the manifest. For SQLite it also runs `PRAGMA integrity_check` on the copy. The command exits non-zero if the backup is corrupt or incomplete. The same check is available on the System Health page and at `POST /api/system/backups/{name}/verify`. Both are audit-logged as `BACKUP_VERIFIED`.

## Restoring

Restoring replaces the live database, so it isn't available over the API.

### SQLite

1. Stop the backend.
2. From `backend/`, run:

   ```bash
   python restore_backup.py backup-20260923T101500123456Z --confirm
   ```

   The script:
   1. Verifies the backup and refuses to restore it if verification fails.
   2. Takes a **safety backup** of the current state, so the restore itself can be undone.
   3. Restores the database.
   4. Copies uploaded files back. This step only adds files: files that exist now but aren't in the backup are kept.
3. Start the backend again. Check the System Health page: the integrity check should pass and no documents should be missing files.

### Postgres

An encrypted dump must be decrypted first, to a location only you can read. Delete the decrypted file afterwards:

```bash
python -c "import sys; from app.file_processing.storage import decrypt_bytes; sys.stdout.buffer.write(decrypt_bytes(open(sys.argv[1], 'rb').read()))" backups/<name>/database.dump.enc > /secure/tmp/database.dump
pg_restore --clean --if-exists -d "$DATABASE_URL" /secure/tmp/database.dump
```

An unencrypted backup's `database.dump` is restored directly.

Then copy `backups/<name>/uploaded_files/` back into `backend/uploaded_files/`.

## Protection against accidental deletion and corruption

- **Soft delete only:** assessments are removed from view only through the controlled, logged soft-delete from Stage 16. The application blocks hard deletes (`session.delete()` or bulk `DELETE`) of assessments, documents, risk results, audit events, workflow transitions, committee votes and conditions, overrides and action items. See `app/services/data_protection.py`.
- **Append-only audit log:** existing audit events can't be modified.
- **Crash consistency:** SQLite runs in WAL mode with `synchronous=NORMAL`, so a crash in the middle of a write can't leave the database half-written.
- **Integrity check:** `GET /api/system/integrity` runs a database quick-check and lists any documents whose stored file is missing.
- **No data lost to failed processing:** uploaded files and assessment records are committed *before* any extraction or AI step runs. A failure leaves a retryable job, not a lost upload.

## Restore drill

Test a restore at least once per quarter:

1. Restore the latest backup into a scratch copy of the backend. Set `DATABASE_URL` and `BACKUP_DIR` to point at the scratch location.
2. Record how long the restore took.
3. Compare that time with the RTO.
