"""Test harness.

The tests run the real application against a real Postgres. There are no
mocks and no fake database: half of what this app promises -- the unique
constraints that stop ballot stuffing, the trigger that keeps the tallies
honest, the cascade that takes ballots with a purged question -- lives in the
schema, and a mocked database would test none of it.

    TEST_DATABASE_URL=postgresql://... pytest

or, with nothing installed but Docker:

    docker compose -f docker-compose.test.yml run --rm tests

Every test starts from an empty database, so *the first account registered in
a test is that test's admin* -- see create_user(): the first row in an empty
users table is the site owner.
"""

import os
import pathlib
import re
import sys

import pytest
import pytest_asyncio

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DSN = os.environ.get("TEST_DATABASE_URL")
if not DSN:
    raise pytest.UsageError(
        "TEST_DATABASE_URL is not set. These tests need a throwaway Postgres "
        "-- they TRUNCATE every table between tests, so never point this at "
        "anything you care about. See tests/README.md."
    )

# Read at import time by app.main, so it has to be set before that import.
ADMIN_TOKEN = "test-admin-token"
os.environ.update(
    DATABASE_URL=DSN,
    ADMIN_TOKEN=ADMIN_TOKEN,
    AUTO_APPROVE_VERIFIED="true",
    SIGNUPS_PER_HOUR="5",
    ABUSE_CONTACT="abuse@example.test",
    SITE_URL="http://dd.test",
    COOKIE_SECURE="false",
    SMTP_HOST="",
    BLOCKED_EMAIL_DOMAINS="",
)

import httpx                                            # noqa: E402
from app import auth                                    # noqa: E402
from app import main as dd                               # noqa: E402

# Emptied between tests. agent_prompts is in the list because it references
# users, so a CASCADE would take it anyway -- better to say so and put the
# seeded prompt back deliberately than to have it vanish by side effect.
TABLES = ("users", "agents", "issues", "votes", "comments", "comment_votes",
          "notifications", "sessions", "audit_log", "site_config",
          "signup_throttle", "login_throttle", "password_resets",
          "agent_prompts")

_prompt = ""


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def _app():
    """Opens the pool and applies schema.sql exactly as production startup
    does -- which also means the schema is under test on every run."""
    global _prompt
    async with dd.app.router.lifespan_context(dd.app):
        row = await dd.q("SELECT body FROM agent_prompts ORDER BY version "
                         "LIMIT 1", one=True)
        assert row, "schema.sql did not seed the baseline agent prompt"
        _prompt = row["body"]
        yield dd.app


@pytest_asyncio.fixture(loop_scope="session", autouse=True)
async def clean(_app):
    await dd.q(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY CASCADE")
    # Forums are not truncated: the 25 country forums come from schema.sql and
    # are part of the site. Only the ones a test created go.
    await dd.q("DELETE FROM forums WHERE created_by <> 'seed'")
    await dd.q("INSERT INTO agent_prompts (body) VALUES (%s)", (_prompt,))
    yield


@pytest.fixture
def sql():
    """Straight to the database, for asserting on what the app actually
    wrote rather than on what it told us it wrote."""
    return dd.q


@pytest.fixture
def admin_token():
    return ADMIN_TOKEN


@pytest_asyncio.fixture(loop_scope="session")
async def client():
    """A browser. Redirects are not followed: a 303 to /account is itself the
    assertion in several places."""
    async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=dd.app),
            base_url="http://dd.test") as c:
        yield c


@pytest_asyncio.fixture(loop_scope="session")
async def browser(client):
    """Factory for additional independent browsers -- a second person, or a
    logged-out stranger, in the same test."""
    made = []

    async def new(ip: str = "203.0.113.9"):
        c = httpx.AsyncClient(transport=httpx.ASGITransport(app=dd.app),
                              base_url="http://dd.test",
                              headers={"cf-connecting-ip": ip})
        made.append(c)
        return c

    yield new
    for c in made:
        await c.aclose()
