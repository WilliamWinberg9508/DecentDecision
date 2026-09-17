# Deploying

PowerShell, from the project folder:

```powershell
cd "$env:USERPROFILE\Downloads\memes_ submit_files\decentdecision"
```

This deploy is bigger than the usual one, because the database container is
being replaced rather than restarted: it is built from `backup/Dockerfile.postgres`
now, so that `archive_command` can run pgbackrest inside it. The data lives in
the `votedata` volume and is not touched by that — but a replaced database
container is exactly the moment to have a copy, and the backups that would
normally give you one do not exist until this deploy finishes.

## 1. Fill in the new settings

`.env` currently has only `POSTGRES_PASSWORD` and `ADMIN_TOKEN`. Everything
else falls back to a sane default, with one exception worth fixing:
`ABUSE_CONTACT` is what puts a reachable address in the footer, and it is blank.

```powershell
notepad .env
```

Add at least:

```
ABUSE_CONTACT=abuse@decentdecision.com
```

`env.filled.example` shows every variable with a plausible value, including the
ones that only matter once the site is public (`COOKIE_SECURE`, `SITE_URL`).

## 2. Take a copy first

```powershell
docker compose exec -T postgres sh -c "pg_dump -U vote -d vote -Fc -f /tmp/dd.dump"
docker compose cp postgres:/tmp/dd.dump ..\dd-before-upgrade.dump
```

Two commands rather than a redirect on purpose: PowerShell 5 writes UTF-16 to
`>`, which quietly corrupts a binary dump.

To put it back, if it ever comes to that:

```powershell
docker compose cp ..\dd-before-upgrade.dump postgres:/tmp/dd.dump
docker compose exec -T postgres pg_restore -U vote -d vote --clean --if-exists /tmp/dd.dump
```

## 3. Deploy

```powershell
docker compose up -d --build
```

The first build installs pgbackrest into the database image, so it takes a
couple of minutes. The schema — comments, comment votes, notifications, login
throttling, password resets — applies itself at startup; there is nothing to
run by hand.

If the build fails on `apk add pgbackrest`, the package is not in that image's
Alpine release: switch to the Debian base commented at the bottom of
`backup/Dockerfile.postgres` and build again.

## 4. Check it came up

```powershell
docker compose ps
curl.exe http://localhost:8100/healthz
docker compose logs --tail 40 backup
```

The backup sidecar creates the stanza and takes a full backup immediately on a
fresh repository, so within a minute or two its log should end with a backup
and an `info` summary. Then prove it:

```powershell
docker compose exec backup /backup/restore-drill.sh
```

That restores the backup into a scratch directory inside the sidecar, starts a
second Postgres on a spare port, counts the rows, checks that the tallies still
agree with the ballots, and throws it away. It never touches the live database.

## 5. Look at the site

<http://localhost:8100> — open a question, and the *Discuss this* button is
under the 2×2 and above the by-model table.

## Going public again

Nothing above exposes anything: the API is still bound to `127.0.0.1:8100` and
the only route in is the Cloudflare tunnel hostname. Before adding that route
back, two settings have to change, or sessions break in ways that look like the
site is broken rather than misconfigured:

```
COOKIE_SECURE=true
SITE_URL=https://decentdecision.com
```

`SITE_URL` is what verification and password-reset links are built from, so a
stale value sends people to localhost. Then follow *Publishing it on
decentdecision.com* in `README.md`.
