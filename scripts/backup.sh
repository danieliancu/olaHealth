#!/bin/sh
# Daily backup: PostgreSQL (custom format) + private media (contributed photos).
# Runs inside the `backup` service (postgres:17-alpine). Keeps BACKUP_KEEP_DAYS days.
set -eu

DEST=/backups
KEEP_DAYS="${BACKUP_KEEP_DAYS:-14}"
TS=$(date -u +%Y%m%dT%H%M%SZ)
export PGPASSWORD="$POSTGRES_PASSWORD"

mkdir -p "$DEST"
pg_dump -Fc -h "${POSTGRES_HOST:-postgres}" -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  -f "$DEST/db-$TS.dump.partial"
mv "$DEST/db-$TS.dump.partial" "$DEST/db-$TS.dump"

tar -czf "$DEST/media-$TS.tar.gz.partial" -C /data private
mv "$DEST/media-$TS.tar.gz.partial" "$DEST/media-$TS.tar.gz"

find "$DEST" -name 'db-*.dump' -mtime +"$KEEP_DAYS" -delete
find "$DEST" -name 'media-*.tar.gz' -mtime +"$KEEP_DAYS" -delete
find "$DEST" -name '*.partial' -mmin +120 -delete

echo "backup ok: db-$TS.dump media-$TS.tar.gz"
