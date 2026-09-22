#!/usr/bin/env python3
"""Give the five test agents fresh, random tokens.

    python make_test_tokens.py

Run it from the project folder, with the site's containers up. It makes a new
random token for each test account in vote_all.py, stores only the hash of it
in the database (like every other token on the site), and saves the tokens
themselves in test_tokens.json next to this script. That file is the only copy:
it is in .gitignore, so it never ends up on GitHub. vote_all.py reads it.

Run it again whenever you like -- the old tokens stop working at once.
--print-sql shows the SQL instead of running it (for piping into psql by hand).
"""

import hashlib
import json
import pathlib
import secrets
import subprocess
import sys

from vote_all import AGENTS, TOKENS_FILE

PSQL = ["docker", "compose", "exec", "-T", "postgres",
        "psql", "-U", "vote", "-d", "vote", "-v", "ON_ERROR_STOP=1", "-q"]


def main() -> None:
    tokens = {account: secrets.token_urlsafe(32) for account, _ in AGENTS}
    # All or nothing: if any account is missing, nothing changes.
    sql = ["BEGIN;"]
    for account, token in tokens.items():
        digest = hashlib.sha256(token.encode()).hexdigest()
        sql.append(f"""DO $$ BEGIN
  UPDATE agents SET token_hash = '{digest}'
   WHERE user_id = (SELECT id FROM users WHERE username = '{account}'
                                          AND email LIKE '%@dd.test');
  IF NOT FOUND THEN
    RAISE EXCEPTION 'No test account {account}. Run reset_and_seed.sql first.';
  END IF;
END $$;""")
    sql.append("COMMIT;")
    sql = "\n".join(sql) + "\n"

    if "--print-sql" in sys.argv:
        print(sql)
        TOKENS_FILE.write_text(json.dumps(tokens, indent=2), encoding="utf-8")
        return

    done = subprocess.run(PSQL, input=sql, text=True, capture_output=True,
                          cwd=pathlib.Path(__file__).parent)
    if done.returncode != 0:
        sys.exit(f"Nothing was changed.\n{done.stderr.strip()}")
    TOKENS_FILE.write_text(json.dumps(tokens, indent=2), encoding="utf-8")
    print(f"{len(tokens)} test agents have new tokens, saved in {TOKENS_FILE.name}.")
    print("The old ones no longer work. Vote with: python vote_all.py")


if __name__ == "__main__":
    main()
