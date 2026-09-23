"""Fixes from the pre-launch security review."""

import pytest

from app import main as dd
from helpers import approved, csrf, post_issue

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_one_mailbox_cannot_register_under_display_names(client, browser, sql):
    await approved(client, "Tengil")
    c = await browser("198.51.100.201")
    for n, addr in enumerate(["x <me@example.test>", "a@example.test, b@example.test"]):
        r = await c.post("/register", data={
            "username": f"sneaky{n}", "email": addr, "password": "a-long-enough-pw",
            "csrf": await csrf(c, "/register")})
        assert r.status_code == 409 and "valid email" in r.text
    assert (await sql("SELECT count(*) AS n FROM users", one=True))["n"] == 1


async def test_issue_titles_and_bodies_have_a_size_limit(client, sql):
    await approved(client, "Tengil")
    r = await post_issue(client, "Should this be " + "very " * 60 + "long?")
    assert r.status_code == 422
    r = await post_issue(client, "Should the body be huge?", body="x" * 9000)
    assert r.status_code == 422
    assert (await sql("SELECT count(*) AS n FROM issues", one=True))["n"] == 0


async def test_posting_issues_is_rate_limited(client, monkeypatch):
    await approved(client, "Tengil")
    monkeypatch.setattr(dd, "ISSUES_PER_DAY", 2)
    assert (await post_issue(client, "Should one be fine?")).status_code == 303
    assert (await post_issue(client, "Should two be fine?")).status_code == 303
    assert (await post_issue(client, "Should three be refused?")).status_code == 429


async def test_the_api_map_is_not_published(client):
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert (await client.get(path)).status_code == 404


def test_ipv6_is_limited_per_connection_not_per_address():
    a = dd._ip_bucket("2001:db8:1:2:aaaa::1")
    b = dd._ip_bucket("2001:db8:1:2:bbbb::9")
    assert a == b == "2001:db8:1:2::/64"
    assert dd._ip_bucket("203.0.113.9") == "203.0.113.9"
    assert dd._ip_bucket("::ffff:203.0.113.9") == "203.0.113.9"


def test_control_and_bidi_characters_are_stripped():
    assert dd.clean("a\x9b[2Jb‮c") == "a[2Jbc"


async def test_the_donation_address_shows_only_when_set_and_valid(client, monkeypatch):
    env = dd.templates.env.globals
    monkeypatch.setitem(env, "btc_address", "")
    assert 'class="donate"' not in (await client.get("/")).text
    monkeypatch.setitem(env, "btc_address", "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4")
    page = (await client.get("/")).text
    assert 'href="bitcoin:bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4"' in page
