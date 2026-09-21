# Health check for the whole stack. Writes verify-output.txt next to itself, so
# the result can be read without anyone having to copy a terminal window.
$ErrorActionPreference = "Continue"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here
$out = Join-Path $here "verify-output.txt"
"=== decentdecision verify, $(Get-Date -Format s) ===" | Set-Content $out

function Section($title, $block) {
    Add-Content $out ""
    Add-Content $out "--- $title ---"
    try   { & $block 2>&1 | Out-String -Width 200 | Add-Content $out }
    catch { Add-Content $out "FAILED: $_" }
}

Section "containers" { docker compose ps -a --format "{{.Service}}`t{{.Status}}" }
Section "api log, last 80"      { docker compose logs --no-color --tail 80 api }
Section "postgres log, last 40" { docker compose logs --no-color --tail 40 postgres }
Section "api health"  { curl.exe -s -m 10 http://localhost:8100/healthz }
Section "api http status on the main pages" {
    foreach ($p in "/", "/login", "/register", "/healthz") {
        $code = curl.exe -s -o NUL -w "%{http_code}" -m 10 "http://localhost:8100$p"
        "$code  $p"
    }
}
Section "settings the app actually got" {
    # One printenv per name: PowerShell 5 mangles the quotes a grep pattern
    # needs on its way to a native command. None of these are secrets.
    foreach ($k in "SITE_URL","COOKIE_SECURE","ABUSE_CONTACT","AUTO_APPROVE_VERIFIED",
                   "SMTP_HOST","LOGIN_FAILS_PER_IP","LOGIN_FAILS_PER_ACCOUNT","RESET_TTL_MINUTES") {
        "$k=" + (docker compose exec -T api printenv $k)
    }
}
Section "schema: the new tables are there" {
    docker compose exec -T postgres psql -U vote -d vote -c "\dt"
}
Section "row counts" {
    docker compose exec -T postgres psql -U vote -d vote -c "SELECT 'users' t, count(*) FROM users UNION ALL SELECT 'forums', count(*) FROM forums UNION ALL SELECT 'issues', count(*) FROM issues UNION ALL SELECT 'votes', count(*) FROM votes UNION ALL SELECT 'comments', count(*) FROM comments UNION ALL SELECT 'notifications', count(*) FROM notifications ORDER BY 1;"
}
Section "tallies agree with the ballots" {
    docker compose exec -T postgres psql -U vote -d vote -c "SELECT count(*) AS issues_whose_counters_disagree FROM issues i WHERE i.ballots <> (SELECT count(*) FROM votes v WHERE v.issue_id = i.id);"
}
Section "WAL archiving" {
    docker compose exec -T postgres psql -U vote -d vote -c "SELECT archived_count, last_archived_wal, failed_count, last_failed_wal FROM pg_stat_archiver;"
}
Section "pgbackrest check" { docker compose exec -T backup pgbackrest --stanza=dd check }
Section "pgbackrest info"  { docker compose exec -T backup pgbackrest --stanza=dd info }
Section "backup log, last 25" { docker compose logs --tail 25 backup }
Section "restore drill"    { docker compose exec -T backup /backup/restore-drill.sh }

Add-Content $out ""
Add-Content $out "=== done ==="
Write-Host "Written to $out"
