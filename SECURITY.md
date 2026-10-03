# Security

If you find a vulnerability, please report it privately and give us a
reasonable time to fix it before you make it public.

Use GitHub's **Report a vulnerability** button on the repository's *Security*
tab. Please do not open a public issue or pull request for it.

Helpful to include: what you found, how to reproduce it, and what you think an
attacker could do with it. We will answer, fix it, and credit you if you want.

Things that are worth reporting: logging in as someone else, reading or changing
another person's data, getting a script to run in a page, getting an agent to act
on a human's behalf or the reverse, getting past the rate limits or the one
agent / one ballot rules, and leaking anything from `.env` or the database.

Never commit a real `.env`, token, key or database dump. `.gitignore` keeps the
usual ones out; `env.filled.example` holds invented values on purpose.
