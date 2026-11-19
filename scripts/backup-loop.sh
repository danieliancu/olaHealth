#!/bin/sh
# Backup service entrypoint: one backup at BACKUP_HOUR (UTC) every day.
set -eu
HOUR="${BACKUP_HOUR:-3}"
while true; do
  now=$(date -u +%s)
  next=$(date -u -d "@$(( now - now % 86400 + HOUR * 3600 ))" +%s 2>/dev/null || echo $(( now - now % 86400 + HOUR * 3600 )))
  [ "$next" -le "$now" ] && next=$(( next + 86400 ))
  sleep $(( next - now ))
  /scripts/backup.sh || echo "backup FAILED" >&2
done
