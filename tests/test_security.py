"""The properties that are easy to lose in a later refactor without noticing.

Each of these was a deliberate decision. A test is the only thing that makes a
deliberate decision survive contact with the next change.
"""

import pathlib
import re

import pytest

from app import auth
from app import main as dd
from helpers import approved, csrf, issue_id, post_issue, register, user_id, verify

pytestmark = pytest.mark.asyncio(loop_scope="session")

ROOT = pathlib.Path(__file__).resolve().parents[1]
TEMPLATES = sorted((ROOT / "app" / "templates").glob("*.html"))


async def test_every_response_carries_the_security_headers(client):
    r = await client.get("/")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["referrer-policy"] == "no-referrer"
    csp = r.headers["content-security-policy"]
    assert "default-src 'none'" in csp and "frame-ancestors 'none'" in csp
    # The important line: no script source at all. This site ships no
    # JavaScript, so an injected <script> has nowhere to run from.
    assert "script-src" not in csp


async def test_hsts_appears_only_when_the_site_is_served_over_https(
        client, monkeypatch):
    """On plain http a browser that sees HSTS once refuses http for a year --
    which on localhost means bricking your own development site."""
    assert "strict-transport-security" not in (await client.get("/")).headers
    monkeypatch.setattr(auth, "SECURE", True)
    assert "strict-transport-security" in (await client.get("/")).headers


async def test_user_content_is_escaped_on_the_way_out(client, sql):
    await approved(client, "Tengil")
    nasty = 'Should <script>alert(1)</script> "run"?'
    await post_issue(client, nasty, body="<img src=x onerror=alert(2)>")
    iid = await issue_id(nasty)

    page = (await client.get(f"/i/{iid}")).text
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page   # visible, inert
    assert "<img" not in page, "an attribute payload survived as a real tag"
    assert "&lt;img src=x onerror=alert(2)&gt;" in page


POSTS = [
    ("/register", {"username": "x", "email": "x@example.test",
                   "password": "a-long-enough-pw"}),
    ("/login", {"username": "x", "password": "a-long-enough-pw"}),
    ("/forgot", {"email": "x@example.test"}),
    ("/reset/sometoken", {"password": "a-long-enough-pw",
                          "password2": "a-long-enough-pw"}),
    ("/new", {"title": "Should this post be refused?", "body": "b"}),
    ("/logout", {}),
    ("/account/token", {}),
    ("/admin/approve", {"user_id": "1", "agent_name": "a"}),
    ("/admin/reject", {"user_id": "1"}),
    ("/admin/prompt", {"body": "be nice"}),
    ("/admin/admin-token", {}),
    ("/i/1/remove", {}),
    ("/i/1/restore", {}),
    ("/i/1/purge", {"confirm": "purge"}),
    ("/vote/1/remove", {}),
]


@pytest.mark.parametrize("path,payload", POSTS, ids=[p for p, _ in POSTS])
async def test_no_form_route_accepts_a_post_without_csrf(client, path, payload):
    """A missing hidden field is silent: the form still works when you click
    it yourself. This is the check that catches the one someone forgot."""
    await approved(client, "Tengil")             # an admin, so nothing 404s early
    await post_issue(client)

    r = await client.post(path, data=payload)
    assert r.status_code == 403, f"{path} accepted a post with no CSRF token"


async def test_a_csrf_value_from_another_browser_does_not_work(client, browser):
    """Double-submit: the cookie and the field have to be the same value, and
    a site on another origin can send your cookie but cannot read it."""
    await approved(client, "Tengil")
    attacker = await browser("198.51.100.50")
    stolen = await csrf(attacker, "/login")

    r = await client.post("/logout", data={"csrf": stolen})
    assert r.status_code == 403


async def test_the_admin_page_is_invisible_to_everyone_else(client, browser):
    await approved(client, "Tengil")
    anna = await browser("198.51.100.51")
    await register(anna, "anna", ip="198.51.100.51")
    await verify(anna, await user_id("anna"))

    stranger = await browser("198.51.100.52")
    assert (await anna.get("/admin")).status_code == 404      # approved, not admin
    assert (await stranger.get("/admin")).status_code == 404  # logged out
    assert (await client.get("/admin")).status_code == 200    # the admin


async def test_the_api_admin_token_stops_working_once_it_is_rotated(
        client, admin_token, sql):
    """A leaked .env from last month should not be a way in."""
    await approved(client, "Tengil")
    head = {"Authorization": f"Bearer {admin_token}"}
    assert (await client.get("/admin/applications", headers=head)).status_code == 200

    await client.post("/admin/admin-token",
                      data={"csrf": await csrf(client, "/admin")})
    assert (await client.get("/admin/applications", headers=head)).status_code == 401
    # Only the hash is stored, so the database does not hold a working key.
    stored = await sql("SELECT value FROM site_config WHERE key = 'admin_token_hash'",
                       one=True)
    assert stored and stored["value"] != admin_token


async def test_secrets_are_never_stored_in_the_clear(client, sql):
    """Password, session, agent token, admin token, verification link: every
    one of them is a hash in the database, not the thing itself."""
    _, agent_token = await approved(client, "Tengil")

    user = await sql("SELECT * FROM users", one=True)
    assert user["password_hash"].startswith("scrypt$")
    assert "a-long-enough-pw" not in user["password_hash"]

    cookie = client.cookies.get(auth.COOKIE)
    session = await sql("SELECT token_hash FROM sessions", one=True)
    assert session["token_hash"] == auth.token_hash(cookie) != cookie

    agent = await sql("SELECT * FROM agents", one=True)
    assert agent["token_hash"] == dd._hash(agent_token)
    assert "token" not in agent, "a readable token column is back on agents"


async def test_the_session_cookie_is_httponly_and_samesite(client, browser,
                                                           monkeypatch):
    """HttpOnly is what makes the no-JavaScript argument pay off: even if a
    script ran, it could not read the session. Secure has to follow the
    deployment, not be hardcoded either way."""
    c = await browser("198.51.100.61")
    csrf_cookie = next(v for v in (await c.get("/register")
                                   ).headers.get_list("set-cookie")
                       if v.startswith(auth.CSRF_COOKIE))
    session_cookie = next(v for v in (await register(c, "solo",
                                                     ip="198.51.100.61")
                                      ).headers.get_list("set-cookie")
                          if v.startswith(auth.COOKIE))

    for cookie in (csrf_cookie, session_cookie):
        assert "httponly" in cookie.lower()
        assert "samesite=lax" in cookie.lower()
        # COOKIE_SECURE is false in this environment, and must be true in
        # production -- the flag follows the deployment rather than the code.
        assert "secure" not in cookie.lower()


async def test_the_abuse_contact_is_on_every_page(client):
    """You cannot act on notice if there is nowhere to send notice."""
    for path in ("/", "/login", "/register"):
        assert "abuse@example.test" in (await client.get(path)).text


async def test_the_ip_used_for_throttling_is_never_stored_raw(client, browser, sql):
    c = await browser("203.0.113.77")
    await register(c, "someone", ip="203.0.113.77")

    rows = await sql("SELECT ip_hash FROM signup_throttle")
    assert rows and all("203.0.113.77" not in r["ip_hash"] for r in rows)
    assert all(len(r["ip_hash"]) == 64 for r in rows)


async def test_health_check_answers_without_a_session(browser):
    c = await browser("198.51.100.60")
    r = await c.get("/healthz")
    assert r.status_code == 200 and r.json() == {"ok": True}
