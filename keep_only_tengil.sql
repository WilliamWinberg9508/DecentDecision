-- One-off: remove every account except Tengil, and every question with them.
--
--   Get-Content keep_only_tengil.sql | docker compose exec -T postgres psql -U vote -d vote -v ON_ERROR_STOP=1
--
-- Then run reset_and_seed.sql to put 250 questions and the 5 test agents back.
--
-- This is deliberately a separate file from reset_and_seed.sql. That one is
-- meant to be re-run whenever you want a clean slate of test data; this one
-- deletes real accounts, and a destructive one-off should never be one habit
-- away from being run again on a site with other people on it.
--
-- It all happens in one transaction: if any step fails, nothing is deleted.

BEGIN;

-- Refuse unless there is exactly one Tengil. Matching nobody would delete
-- every account on the site; matching two would keep an impostor.
DO $guard$
DECLARE n int;
BEGIN
    SELECT count(*) INTO n FROM users WHERE lower(username) = 'tengil';
    IF n <> 1 THEN
        RAISE EXCEPTION 'Expected exactly one account named Tengil, found %. '
                        'Nothing has been deleted.', n;
    END IF;
END $guard$;

-- Questions and everything hanging off them. Named, not CASCADE, for the
-- reason given in reset_and_seed.sql.
TRUNCATE notifications, comment_votes, comments, votes, issues RESTART IDENTITY;

-- The published agent prompts are history -- ballots record which version
-- they used -- so they stay. Only the pointer to an author who is about to
-- stop existing is cleared.
UPDATE agent_prompts SET author_id = NULL
 WHERE author_id IS NOT NULL
   AND author_id <> (SELECT id FROM users WHERE lower(username) = 'tengil');

-- Everything else that points at users either cascades (agents, sessions,
-- password resets) or is set to NULL (the audit log, which keeps the actor's
-- name as text, so the record of who did what survives the account).
DELETE FROM users WHERE lower(username) <> 'tengil';

-- The throttles are keyed by salted hashes, not user ids, so nothing
-- references them -- but they hold nothing worth keeping either.
TRUNCATE signup_throttle, login_throttle;

COMMIT;

SELECT username, is_admin, status,
       (SELECT count(*) FROM agents a WHERE a.user_id = u.id) AS agents
  FROM users u;
SELECT (SELECT count(*) FROM users)  AS accounts_left,
       (SELECT count(*) FROM issues) AS questions,
       (SELECT count(*) FROM votes)  AS ballots;
