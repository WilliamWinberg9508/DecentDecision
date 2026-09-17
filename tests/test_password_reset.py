"""Forgotten passwords.

A reset link is a password for as long as it is valid, so it gets the same
three properties as every other token here: short-lived, single use, and stored
only as a hash.
"""

import pytest

from app import auth
from app import main as dd
from helpers import PASSWORD, csrf, login, register, user_id

pytestmark = pytest.mark.asyncio(loop_scope="session")

NEW = "a-brand-new-password"


async def ask(c, email):
    return await c.post("/forgot", data={"email": email,
                                         "csrf": await csrf(c, "/forgot")})


async def link_for(username):
    user = await dd.q("SELECT id, email, username FROM users WHERE username = %s",
                      (username,), one=True)
    return (await dd.issue_password_reset(user)).replace(dd.SITE_URL, "")


async def submit(c, path, password=NEW, confirm=None):
    # The CSRF pair comes from a page that always has a form: a spent or
    # expired reset page shows no form at all, which is the behaviour under
    # test in a couple of these.
    return await c.post(path, data={
        "password": password, "password2": confirm or password,
        "csrf": await csrf(c, "/login")})


async def test_a_reset_changes_the_password(client, browser, sql):
    await register(client, "Tengil")
    c = await browser("198.51.100.80")

    before = (await sql("SELECT password_hash FROM users", one=True))["password_hash"]
    r = await submit(c, await link_for("Tengil"))
    assert r.status_code == 303 and r.headers["location"] == "/login?reset=1"

    after = (await sql("SELECT password_hash FROM users", one=True))["password_hash"]
    assert after != before
    assert auth.verify_password(NEW, after)
    assert not auth.verify_password(PASSWORD, after)

    fresh = await browser("198.51.100.81")
    assert (await login(fresh, "Tengil", NEW)).status_code == 303
    assert (await login(fresh, "Tengil", PASSWORD)).status_code == 401


async def test_the_form_says_the_same_thing_for_an_address_that_is_not_here(
        client, browser, sql):
    """Otherwise this form is a way to find out who has an account."""
    await register(client, "Tengil")
    c = await browser("198.51.100.82")

    known = await ask(c, "Tengil@example.test")
    unknown = await ask(c, "nobody@example.test")
    assert known.status_code == unknown.status_code == 200
    assert known.text == unknown.text

    # And only the real address actually minted anything.
    assert (await sql("SELECT count(*) AS n FROM password_resets",
                      one=True))["n"] == 1


async def test_the_link_works_exactly_once(client, browser):
    await register(client, "Tengil")
    c = await browser("198.51.100.83")
    path = await link_for("Tengil")

    assert (await c.get(path)).status_code == 200
    assert (await submit(c, path)).status_code == 303

    assert (await c.get(path)).status_code == 410
    assert (await submit(c, path, "yet-another-password")).status_code == 410


async def test_an_expired_link_is_refused(client, browser, sql):
    await register(client, "Tengil")
    c = await browser("198.51.100.84")
    path = await link_for("Tengil")
    await sql("UPDATE password_resets SET expires_at = now() - interval '1 minute'")

    assert (await c.get(path)).status_code == 410
    assert (await submit(c, path)).status_code == 410


async def test_using_one_link_kills_the_others(client, browser, sql):
    """If the reason for the reset was that somebody else had access, an older
    link still sitting in the mailbox is the same problem again."""
    await register(client, "Tengil")
    c = await browser("198.51.100.85")
    first = await link_for("Tengil")
    second = await link_for("Tengil")

    assert (await submit(c, second)).status_code == 303
    assert (await c.get(first)).status_code == 410
    assert (await sql("SELECT count(*) AS n FROM password_resets "
                      "WHERE used_at IS NULL", one=True))["n"] == 0


async def test_a_reset_signs_out_every_device(client, browser, sql):
    """The session is the thing an attacker actually holds. Changing the
    password while leaving their cookie working achieves nothing."""
    await register(client, "Tengil")               # this browser is logged in
    assert (await client.get("/account")).status_code == 200

    other = await browser("198.51.100.86")
    await submit(other, await link_for("Tengil"))

    assert (await sql("SELECT count(*) AS n FROM sessions", one=True))["n"] == 0
    assert (await client.get("/account")).status_code == 303


async def test_the_token_is_not_stored_in_the_database(client, sql):
    await register(client, "Tengil")
    path = await link_for("Tengil")
    token = path.rsplit("/", 1)[1]

    row = await sql("SELECT token_hash FROM password_resets", one=True)
    assert row["token_hash"] != token
    assert row["token_hash"] == dd._hash(token)


@pytest.mark.parametrize("password,confirm,because", [
    (NEW, "something-else", "the two do not match"),
    ("short", "short", "under ten characters"),
])
async def test_a_bad_new_password_is_refused_and_the_link_survives(
        client, browser, sql, password, confirm, because):
    await register(client, "Tengil")
    c = await browser("198.51.100.87")
    path = await link_for("Tengil")

    r = await submit(c, path, password, confirm)
    assert r.status_code == 422, because
    assert auth.verify_password(
        PASSWORD, (await sql("SELECT password_hash FROM users",
                             one=True))["password_hash"])
    # A typo must not burn the link, or every mistake means a new email.
    assert (await c.get(path)).status_code == 200


async def test_resetting_clears_the_guessing_penalty_on_that_account(
        client, browser, sql):
    """Someone who just proved control of the mailbox should not then be made
    to wait out a pause that the guessing run caused."""
    await register(client, "Tengil")
    guesser = await browser("198.51.100.88")
    for _ in range(10):
        await login(guesser, "Tengil", "wrong")
    assert (await sql("SELECT count(*) AS n FROM login_throttle",
                      one=True))["n"] > 0

    c = await browser("198.51.100.89")
    await submit(c, await link_for("Tengil"))

    owner = await browser("198.51.100.90")
    assert (await login(owner, "Tengil", NEW)).status_code == 303


async def test_requests_are_throttled_per_source(client, browser, sql,
                                                 monkeypatch):
    """Without this the form is a way to send someone five hundred emails."""
    await register(client, "Tengil")
    monkeypatch.setattr(dd, "RESETS_PER_IP", 2)
    c = await browser("198.51.100.91")

    assert (await ask(c, "Tengil@example.test")).status_code == 200
    assert (await ask(c, "Tengil@example.test")).status_code == 200
    assert (await ask(c, "Tengil@example.test")).status_code == 429
    assert (await sql("SELECT count(*) AS n FROM password_resets",
                      one=True))["n"] == 2


async def test_with_no_mail_server_the_link_goes_to_the_log_only(
        client, capsys, sql):
    """It is deliberately not shown on the admin page the way a verification
    link is: an admin who can read reset links can take over any account."""
    await register(client, "Tengil")
    await link_for("Tengil")

    printed = capsys.readouterr().out
    assert "[password reset] Tengil:" in printed
    assert "/reset/" in printed
    admin_page = (await client.get("/admin")).text
    assert "/reset/" not in admin_page
