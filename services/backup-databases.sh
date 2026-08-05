#!/usr/bin/env bash
# Nightly database backup for the Chatwoot install and the AI agent services.
#
# Everything else on this box is code and lives in git. These files are not:
# they hold the conversations, and until this script existed the only copy of
# them was the running server.
set -euo pipefail

DEST=${BDSK_BACKUP_DIR:-/root/backups/behdashtik}
KEEP_DAYS=${BDSK_BACKUP_KEEP_DAYS:-14}
CHATWOOT=/root/tesnet-behdashtik/chatwoot
STAMP=$(date +%Y%m%d-%H%M%S)

mkdir -p "$DEST"

log() { echo "[$(date '+%F %T')] $*"; }

# The live Chatwoot database is chatwoot_dev — RAILS_ENV is production but the
# container was pointed at the dev database, so dumping `chatwoot` backs up an
# empty one.
log "pg_dump chatwoot_dev"
docker exec chatwoot-postgres-1 pg_dump -U postgres --clean --if-exists chatwoot_dev \
  | gzip > "$DEST/chatwoot_dev-$STAMP.sql.gz"

# A plain cp of a WAL-mode SQLite file can catch it mid-write; the backup API
# takes a consistent snapshot while the service keeps running.
backup_sqlite() {
  local src=$1 name=$2
  [ -s "$src" ] || { log "skip $name (missing or empty)"; return 0; }
  log "sqlite backup $name"
  python3 - "$src" "$DEST/$name-$STAMP.sqlite3" <<'PY'
import sqlite3, sys
source, dest = sys.argv[1], sys.argv[2]
src = sqlite3.connect(source)
dst = sqlite3.connect(dest)
with dst:
    src.backup(dst)
dst.close()
src.close()
PY
  gzip -f "$DEST/$name-$STAMP.sqlite3"
}

backup_sqlite /root/behdashtik-ai-agent/data/agent.db          site-agent
backup_sqlite "$CHATWOOT/services/basalam-agent/data/agent.db"  basalam-agent
backup_sqlite "$CHATWOOT/services/basalam-bridge/data/bridge.sqlite3" basalam-bridge

find "$DEST" -maxdepth 1 -type f -name '*.gz' -mtime +"$KEEP_DAYS" -delete

log "done — $(ls -1 "$DEST"/*-"$STAMP".* 2>/dev/null | wc -l) files, $(du -sh "$DEST" | cut -f1) total"
