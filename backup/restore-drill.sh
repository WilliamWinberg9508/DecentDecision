#!/bin/sh
# A backup you have never restored is not a backup. Run this:
#
#   docker compose exec backup /backup/restore-drill.sh
#
# It restores the most recent backup into a scratch directory inside the
# sidecar, starts a second Postgres on a spare port, counts what came back,
# and throws it away. It never touches the live database, the live data
# directory or the repository -- it only reads.
#
# Expect it to take seconds on a small site. Run it after any change to the
# backup setup, and then once in a while for no reason, which is the point.
set -eu

STANZA="${PGBACKREST_STANZA:-dd}"
DRILL=/tmp/drill

PORT=5433
SET_ARG=""

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

# Optionally drill a specific backup: restore-drill.sh 20260917-030001F
[ $# -ge 1 ] && SET_ARG="--set=$1"

rm -rf "$DRILL"
mkdir -p "$DRILL"
chmod 700 "$DRILL"

echo "=== what is in the repository ==="
pgbackrest --stanza="$STANZA" info

echo
echo "=== restoring into $DRILL ==="
# --type=immediate stops as soon as the backup is consistent rather than
# replaying every later WAL segment: this is a check that the backup restores,
# not a recovery.
pgbackrest --stanza="$STANZA" --pg1-path="$DRILL" $SET_ARG \
           --type=immediate --target-action=promote restore

echo
echo "=== starting it on port $PORT ==="
pg_ctl -D "$DRILL" -l "$DRILL/postgres.log" -w -t 120 \
       -o "-p $PORT -k /tmp -c listen_addresses= -c archive_mode=off" start

echo
echo "=== what came back ==="
psql -p "$PORT" -h /tmp -U "${POSTGRES_USER:-vote}" -d "${POSTGRES_DB:-vote}" \
     -v ON_ERROR_STOP=1 <<'SQL'
SELECT 'users'   AS table, count(*) FROM users
UNION ALL SELECT 'issues',  count(*) FROM issues
UNION ALL SELECT 'votes',   count(*) FROM votes
UNION ALL SELECT 'agents',  count(*) FROM agents;

-- The tallies on issues are maintained by a trigger, so a restore that
-- brought back rows but not the counters would still be a broken site.
SELECT count(*) AS issues_whose_counters_disagree
  FROM issues i
 WHERE i.ballots <> (SELECT count(*) FROM votes v WHERE v.issue_id = i.id);

SELECT max(created_at) AS newest_row_restored FROM issues;
SQL

echo
echo "=== cleaning up ==="
pg_ctl -D "$DRILL" -m immediate stop
rm -rf "$DRILL"
echo "drill finished: the backup restores and the restored database is consistent."
