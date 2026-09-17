"""Login rate limiting.

Two limits with two different jobs: a hard cap per source address, which is
about not letting anyone spend the site's CPU on scrypt, and a widening pause
per account, which is about slowing distributed guessing without handing
anyone a way to lock the owner out.
"""

import pytest

from app import main as dd
from helpers import PASSWORD, login, register

pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest.fixture(autouse=True)
def small_limits(monkeypatch):
    monkeypatch.setattr(dd, "LOGIN_FAILS_PER_IP", 4)
    monkeypatch.setattr(dd, "LOGIN_FAILS_PER_ACCOUNT", 2)


async def backdate(seconds):
    """Move every recorded failure into the past, instead of sleeping."""
    await dd.q("UPDATE login_throttle SET at = at - %s::interval",
               (f"{seconds} seconds",))


async def test_a_source_address_is_capped(client, browser, monkeypatch):
    monkeypatch.setattr(dd, "LOGIN_FAILS_PER_ACCOUNT", 999)   # isolate the ip cap
    await register(client, "Tengil")
    guesser = await browser("198.51.100.70")

    for _ in range(4):
        assert (await login(guesser, "Tengil", "wrong-password")).status_code == 401

    blocked = await login(guesser, "Tengil", "wrong-password")
    assert blocked.status_code == 429
    assert "failed sign-ins from this connection" in blocked.text


async def test_the_cap_applies_before_the_password_is_checked(client, browser,
                                                              monkeypatch):
    """A limiter that runs after the hashing has already happened protects
    nothing -- the expense it is supposed to prevent has been paid."""
    monkeypatch.setattr(dd, "LOGIN_FAILS_PER_ACCOUNT", 999)
    await register(client, "Tengil")
    guesser = await browser("198.51.100.71")
    for _ in range(4):
        await login(guesser, "Tengil", "wrong-password")

    hashed = []
    real = dd.auth.verify_password
    monkeypatch.setattr(dd.auth, "verify_password",
                        lambda *a: hashed.append(1) or real(*a))

    assert (await login(guesser, "Tengil", PASSWORD)).status_code == 429
    assert hashed == [], "the password was hashed for a request already blocked"


async def test_guessing_one_account_gets_slower(client, browser):
    """Two failures, then each further attempt has to wait -- doubling, so a
    long run costs the attacker real time."""
    await register(client, "Tengil")
    guesser = await browser("198.51.100.72")

    for _ in range(2):
        assert (await login(guesser, "Tengil", "wrong")).status_code == 401

    paused = await login(guesser, "Tengil", "wrong")
    assert paused.status_code == 429
    assert "Try again in" in paused.text

    await backdate(5)                       # the first pause is one second
    assert (await login(guesser, "Tengil", "wrong")).status_code == 401


async def test_the_owner_can_never_be_locked_out(client, browser):
    """The reason this is a pause and not a lock. Anyone who knows a username
    could otherwise keep that account -- including the only admin -- out of
    the site indefinitely, which is worse than the problem it solves."""
    await register(client, "Tengil")
    attacker = await browser("198.51.100.73")
    for _ in range(12):
        await login(attacker, "Tengil", "wrong")

    owner = await browser("198.51.100.74")
    at_once = await login(owner, "Tengil")
    assert at_once.status_code == 429            # they do have to wait

    # ...but never for more than the cap, however long the attack has run.
    await backdate(dd.LOGIN_MAX_PAUSE)
    assert (await login(owner, "Tengil")).status_code == 303


async def test_a_correct_password_counts_against_nobody(client, browser, sql):
    await register(client, "Tengil")
    c = await browser("198.51.100.75")
    assert (await login(c, "Tengil")).status_code == 303
    assert (await sql("SELECT count(*) AS n FROM login_throttle",
                      one=True))["n"] == 0


async def test_failures_expire_on_their_own(client, browser, sql):
    await register(client, "Tengil")
    c = await browser("198.51.100.76")
    for _ in range(4):
        await login(c, "Tengil", "wrong")

    await backdate(20 * 60)                      # past the 15 minute window
    assert (await login(c, "Tengil")).status_code == 303
    # And the swept rows are gone, so this table cannot grow without bound.
    assert (await sql("SELECT count(*) AS n FROM login_throttle",
                      one=True))["n"] == 0


async def test_neither_the_username_nor_the_address_is_stored_readable(
        client, browser, sql):
    """This table is a rate limiter, not a record of who tried to log in from
    where -- which it would become if either side were stored in the clear."""
    await register(client, "Tengil")
    c = await browser("203.0.113.88")
    await login(c, "Tengil", "wrong")

    rows = await sql("SELECT subject FROM login_throttle")
    assert len(rows) == 2                        # one for the address, one for the account
    for r in rows:
        assert len(r["subject"]) == 64
        assert "203.0.113.88" not in r["subject"]
        assert "tengil" not in r["subject"].lower()


async def test_one_account_being_guessed_does_not_block_another(client, browser):
    await register(client, "Tengil")
    anna = await browser("198.51.100.77")
    await register(anna, "anna", ip="198.51.100.77")

    guesser = await browser("198.51.100.78")
    for _ in range(3):
        await login(guesser, "Tengil", "wrong")

    elsewhere = await browser("198.51.100.79")
    assert (await login(elsewhere, "anna")).status_code == 303
