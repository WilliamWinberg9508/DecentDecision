#!/bin/sh
# pgbackrest, for running by hand:
#   docker compose exec backup sh /backup/pgbr.sh --stanza=dd info
#
# Calling pgbackrest directly with `docker compose exec` fails while the
# off-machine repository is switched off: the R2 key variables arrive defined
# but empty, and pgBackRest refuses them ("environment variable 'repo2-s3-key'
# must have a value"). backup-loop.sh removes them for its own runs; this does
# the same for yours.
for v in PGBACKREST_REPO2_S3_KEY PGBACKREST_REPO2_S3_KEY_SECRET; do
    eval "value=\${$v:-}"
    if [ -z "$value" ]; then
        unset "$v"
    fi
done
exec pgbackrest "$@"
