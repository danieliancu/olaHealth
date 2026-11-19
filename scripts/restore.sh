#!/bin/sh
# Restore a backup made by backup.sh.
#   docker compose stop web worker
#   docker compose run --rm backup /scripts/restore.sh db-20261119T030000Z.dump [media-20261119T030000Z.tar.gz]
#   docker compose start web worker
# The database is restored with --clean (existing objects are dropped first).
set -eu

DB_FILE="${1:?usage: restore.sh db-<ts>.dump [media-<ts>.tar.gz]}"
MEDIA_FILE="${2:-}"
export PGPASSWORD="$POSTGRES_PASSWORD"

pg_restore --clean --if-exists --no-owner -h "${POSTGRES_HOST:-postgres}" -U "$POSTGRES_USER" \
  -d "$POSTGRES_DB" "/backups/$DB_FILE"
echo "database restored from $DB_FILE"

if [ -n "$MEDIA_FILE" ]; then
  rm -rf /data/private/*
  tar -xzf "/backups/$MEDIA_FILE" -C /data
  echo "private media restored from $MEDIA_FILE"
fi
