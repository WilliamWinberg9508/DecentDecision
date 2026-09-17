#!/bin/sh
# The scheduler. Docker Desktop has no cron, and Windows Task Scheduler only
# runs while someone is logged in, so the schedule lives in the stack itself:
# this wakes up every fifteen minutes and decides whether it is time.
#
# Full backup on one day a week, incremental on the others. An incremental
# stores only the blocks that changed since the last backup of any kind, so a
# week of them is cheap; the weekly full is what stops the chain getting long
# enough to be slow to restore.
set -eu

STANZA="${PGBACKREST_STANZA:-dd}"
FULL_DOW="${BACKUP_FULL_DOW:-0}"        # 0 = Sunday
HOUR="${BACKUP_HOUR:-3}"                # container clock, which is UTC
MARKER="/var/lib/pgbackrest/.last-backup"

# Compose cannot leave an environment variable out conditionally: with the
# off-machine repository switched off, PGBACKREST_REPO2_S3_KEY arrives defined
# and empty, and pgBackRest treats "defined but empty" as an error rather than
# as absent -- "environment variable 'repo2-s3-key' must have a value". So the
# empty ones are removed here, where it can be done properly.
for v in PGBACKREST_REPO2_S3_KEY PGBACKREST_REPO2_S3_KEY_SECRET; do
    eval "value=\${$v:-}"
    # An if, not `[ -z ] && unset`: under `set -e` a failed test as the last
    # command in the loop body would end the script -- which is exactly the
    # case where the keys ARE set, so it would only break once it mattered.
    if [ -z "$value" ]; then
        unset "$v"
    fi
done

run() {
    echo "--- $(date -Iseconds) $1 backup ---"
    if pgbackrest --stanza="$STANZA" --type="$1" backup; then
        date +%F > "$MARKER"
    else
        # Never exit: a failed backup must not take the container down and
        # stop every later one from being attempted.
        echo "!!! $(date -Iseconds) backup FAILED -- will try again next cycle"
    fi
}

echo "waiting for postgres..."
until pg_isready -h /var/run/postgresql -U "${POSTGRES_USER:-vote}" -q; do
    sleep 2
done

# Idempotent: harmless on every restart, and the only setup step there is.
pgbackrest --stanza="$STANZA" stanza-create 2>&1 | grep -v "already exists" || true
pgbackrest --stanza="$STANZA" check

# On a brand new stanza there is nothing to recover from until the first full
# backup exists, so take one now rather than waiting for Sunday.
if ! pgbackrest --stanza="$STANZA" info | grep -q "full backup"; then
    echo "no backup in the repository yet"
    run full
fi

while true; do
    today=$(date +%F)
    if [ "$(date +%H)" = "$(printf '%02d' "$HOUR")" ] \
       && [ "$(cat "$MARKER" 2>/dev/null || echo none)" != "$today" ]; then
        if [ "$(date +%w)" = "$FULL_DOW" ]; then
            run full
        else
            run incr
        fi
        # What the repository holds now, in the container log, so `docker
        # compose logs backup` answers "when was the last good backup?"
        pgbackrest --stanza="$STANZA" info
    fi
    sleep 900
done
