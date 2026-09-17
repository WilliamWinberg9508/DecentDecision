"""Registration, verification, login, sessions, the agent token."""

import pytest

from app import auth
from app import main as dd
from helpers import (PASSWORD, TOKEN_RE, approved, csrf, login, register,
                     user_id, verify)

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_the_first_account_on_an_empty_site_is_the_admin(client, sql):
    """Deterministic and visible, rather than a magic token in a log file."""
    r = await register(client, "Tengil")
    assert r.status_code == 303 and r.headers["location"] == "/account"

    row = await sql("SELECT * FROM users WHERE username = 'Tengil'", one=True)
    assert row["is_admin"] and row["status"] == "approved"
    # And their agent exists straight away, since there is nobody to approve them.
    assert await sql("SELECT 1 FROM agents WHERE user_id = %s",
                     (row["id"],), one=True)


async def test_everyone_after_the_first_starts_pending(client, browser, sql):
    await register(client, "Tengil")
    anna = await browser("198.51.100.5")
    await register(anna, "anna")

    row = await sql("SELECT status, is_admin FROM users WHERE username = 'anna'",
                    one=True)
    assert row["status"] == "pending" and not row["is_admin"]
    assert not await sql("SELECT 1 FROM agents a JOIN users u ON u.id = a.user_id "
                         "WHERE u.username = 'anna'", one=True)


async def test_verifying_the_email_approves_and_hands_over_one_token(
        client, browser, sql):
    await register(client, "Tengil")
    anna = await browser("198.51.100.6")
    await register(anna, "anna")

    response, token = await verify(anna, await user_id("anna"))
    assert response.status_code == 200 and token

    row = await sql("SELECT status, email_verified FROM users "
                    "WHERE username = 'anna'", one=True)
    assert row["status"] == "approved" and row["email_verified"]
    # Stored hashed, never in the clear.
    stored = await sql("SELECT a.token_hash FROM agents a JOIN users u "
                       "ON u.id = a.user_id WHERE u.username = 'anna'", one=True)
    assert stored["token_hash"] != token
    assert stored["token_hash"] == dd._hash(token)


async def test_a_used_or_invented_verification_link_does_nothing(client, browser):
    await register(client, "Tengil")
    anna = await browser("198.51.100.7")
    await register(anna, "anna")
    uid = await user_id("anna")

    link = await dd.issue_verification(uid)
    path = link.replace(dd.SITE_URL, "")
    assert (await anna.get(path)).status_code == 200

    # The stored hash is cleared on use, so replaying the link must not mint a
    # second agent -- UNIQUE(agents.user_id) is the backstop, this is the door.
    replay = await anna.get(path)
    assert not TOKEN_RE.search(replay.text), "a replayed link handed out a token"
    assert (await dd.q("SELECT count(*) AS n FROM agents", one=True))["n"] == 2

    invented = await anna.get("/verify/not-a-real-token")
    assert invented.status_code == 200 and not TOKEN_RE.search(invented.text)


async def test_admin_approval_still_works_with_auto_approve_off(
        client, browser, sql, monkeypatch):
    """The stricter posture is one environment variable away, and has to keep
    working -- it is where you go if the room fills with accounts."""
    monkeypatch.setattr(dd, "AUTO_APPROVE", False)
    await register(client, "Tengil")
    anna = await browser("198.51.100.8")
    await register(anna, "anna")
    uid = await user_id("anna")

    await verify(anna, uid)
    assert (await sql("SELECT status FROM users WHERE id = %s", (uid,),
                      one=True))["status"] == "pending"

    r = await client.post("/admin/approve", data={
        "user_id": uid, "agent_name": "anna-agent",
        "csrf": await csrf(client, "/admin")})
    assert r.status_code == 303
    assert (await sql("SELECT status FROM users WHERE id = %s", (uid,),
                      one=True))["status"] == "approved"


@pytest.mark.parametrize("username,email,password,because", [
    ("ab", None, PASSWORD, "username too short"),
    ("has space", None, PASSWORD, "username has a space"),
    ("ok_name", None, "short", "password under ten characters"),
    ("ok_name", "throwaway@mailinator.com", PASSWORD, "disposable address"),
])
async def test_registration_refuses_bad_input(client, browser, sql,
                                              username, email, password, because):
    await register(client, "Tengil")             # so we are not the first account
    c = await browser("198.51.100.9")
    r = await register(c, username, email=email, password=password)
    assert r.status_code == 409, because
    assert (await sql("SELECT count(*) AS n FROM users", one=True))["n"] == 1


async def test_two_spellings_of_one_gmail_address_cannot_both_register(
        client, browser, sql):
    """The test that matters most for vote integrity: one mailbox, one agent."""
    await register(client, "Tengil", email="bo.smith@gmail.com")
    second = await browser("198.51.100.10")
    r = await register(second, "bosmith2", email="b.o.s.m.i.t.h+vote@googlemail.com")

    assert r.status_code == 409
    assert "already an account" in r.text
    assert (await sql("SELECT count(*) AS n FROM users", one=True))["n"] == 1


async def test_a_taken_username_is_refused_case_insensitively(client, browser):
    await register(client, "Tengil")
    other = await browser("198.51.100.11")
    r = await register(other, "TENGIL", email="other@example.test")
    assert r.status_code == 409 and "taken" in r.text


async def test_signups_from_one_source_are_throttled(client, browser, monkeypatch):
    monkeypatch.setattr(dd, "SIGNUPS_PER_HOUR", 2)
    c = await browser("198.51.100.12")
    assert (await register(c, "one")).status_code == 303
    assert (await register(c, "two")).status_code == 303
    blocked = await register(c, "three")
    assert blocked.status_code == 429

    # Another source is unaffected -- the limit is per origin, not global.
    elsewhere = await browser("198.51.100.13")
    assert (await register(elsewhere, "four")).status_code == 303


async def test_login_says_the_same_thing_for_a_wrong_name_and_a_wrong_password(
        client, browser):
    """Anything that distinguishes the two is a free username oracle."""
    await register(client, "Tengil")
    c = await browser("198.51.100.14")

    wrong_password = await login(c, "Tengil", "not-the-password")
    no_such_user = await login(c, "nobody-here", PASSWORD)
    assert wrong_password.status_code == no_such_user.status_code == 401
    assert wrong_password.text.count("Wrong username or password.") == \
        no_such_user.text.count("Wrong username or password.")


async def test_logging_in_and_out_moves_the_session_row(client, browser, sql):
    await register(client, "Tengil")
    c = await browser("198.51.100.15")

    assert (await login(c, "Tengil")).status_code == 303
    assert auth.COOKIE in c.cookies
    assert (await c.get("/account")).status_code == 200
    assert (await sql("SELECT count(*) AS n FROM sessions", one=True))["n"] >= 1

    await c.post("/logout", data={"csrf": await csrf(c, "/account")})
    # The row is gone, not merely the cookie: a copied cookie is dead too.
    assert (await sql("SELECT count(*) AS n FROM sessions WHERE token_hash = %s",
                      (auth.token_hash(c.cookies.get(auth.COOKIE) or "x"),),
                      one=True))["n"] == 0
    assert (await c.get("/account")).status_code == 303


async def test_login_will_not_redirect_off_site(client, browser):
    """`next` comes from the query string, so it is attacker-controlled."""
    await register(client, "Tengil")
    for destination in ("//evil.example", "https://evil.example/x", "evil"):
        c = await browser("198.51.100.16")
        r = await login(c, "Tengil", next_=destination)
        assert r.headers["location"] == "/account"


async def test_a_stale_session_cookie_logs_you_out_rather_than_erroring(
        client, sql):
    await register(client, "Tengil")
    await sql("UPDATE sessions SET expires_at = now() - interval '1 day'")
    assert (await client.get("/account")).status_code == 303


async def test_an_expired_password_hash_is_upgraded_on_next_login(
        client, browser, sql, monkeypatch):
    await register(client, "Tengil")
    weak = auth.hash_password(PASSWORD).replace(f"scrypt${auth.N}$", "scrypt$1024$")
    # Rewrite it as if it had been created under cheaper parameters.
    import hashlib
    _, n, r_, p, salt, _ = weak.split("$")
    dk = hashlib.scrypt(PASSWORD.encode(), salt=bytes.fromhex(salt), n=1024,
                        r=int(r_), p=int(p), maxmem=auth.MAXMEM, dklen=32)
    weak = f"scrypt$1024${r_}${p}${salt}${dk.hex()}"
    await sql("UPDATE users SET password_hash = %s WHERE username = 'Tengil'",
              (weak,))

    c = await browser("198.51.100.17")
    assert (await login(c, "Tengil")).status_code == 303
    now = await sql("SELECT password_hash FROM users WHERE username = 'Tengil'",
                    one=True)
    assert not auth.needs_rehash(now["password_hash"])


async def test_the_token_is_shown_once_and_never_again(client, sql):
    """Deliberate: only the hash is kept, so the account page cannot show it.
    That is affordable precisely because generating another is one click."""
    _, token = await approved(client, "Tengil")
    page = (await client.get("/account")).text
    assert token not in page
    assert "stored only as a hash" in page


async def test_rotating_the_token_kills_the_old_one_immediately(client, sql):
    _, first = await approved(client, "Tengil")
    r = await client.post("/account/token",
                          data={"csrf": await csrf(client, "/account")})
    match = TOKEN_RE.search(r.text)
    assert match, "rotation did not show the new token"
    second = match.group(1)

    assert second != first
    stored = await sql("SELECT token_hash FROM agents", one=True)
    assert stored["token_hash"] == dd._hash(second)
    assert stored["token_hash"] != dd._hash(first)
