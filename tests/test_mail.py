"""Sending mail must never slow the site down."""

import asyncio
import time

import pytest

from app import main as dd
from helpers import csrf

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_a_slow_mail_relay_does_not_hold_up_signup(browser, monkeypatch):
    sent = []
    def slow_send(to, subject, body):
        time.sleep(2)                      # a relay that takes its time
        sent.append(to)
    monkeypatch.setattr(dd, "SMTP_HOST", "smtp.example.test")
    monkeypatch.setattr(dd, "send_mail", slow_send)
    c = await browser("198.51.100.77")
    started = time.monotonic()
    r = await c.post("/register", data={
        "username": "patient", "email": "patient@example.test",
        "password": "a-long-enough-pw", "csrf": await csrf(c, "/register")})
    assert r.status_code == 303 and time.monotonic() - started < 1.5
    for _ in range(40):                     # ...and the mail still goes out
        if sent:
            break
        await asyncio.sleep(0.1)
    assert sent == ["patient@example.test"]


def _capture_mail(monkeypatch):
    sent = []
    monkeypatch.setattr(dd, "SMTP_HOST", "smtp.example.test")
    monkeypatch.setattr(dd, "_RESEND_PAUSE", 0)
    monkeypatch.setattr(dd, "send_mail", lambda to, subject, body: sent.append(to))
    return sent


async def _until(condition):
    for _ in range(50):
        if condition():
            return
        await asyncio.sleep(0.05)


async def test_a_member_can_ask_for_the_confirmation_email_again(
        client, browser, sql, monkeypatch):
    from helpers import register, user_id, verify
    await register(client, "Tengil")
    anna = await browser("198.51.100.20")
    await register(anna, "anna")
    sent = _capture_mail(monkeypatch)
    token = await csrf(anna, "/account")

    # Straight after signing up the first email has only just gone: wait.
    r = await anna.post("/account/resend-verification", data={"csrf": token})
    assert r.status_code == 429 and not sent

    await sql("UPDATE users SET verify_sent_at = now() - interval '5 minutes' "
              "WHERE username = 'anna'")
    r = await anna.post("/account/resend-verification", data={"csrf": token})
    assert r.status_code == 200
    await _until(lambda: sent)
    assert sent == ["anna@example.test"]

    # ...and not again until the cooldown has passed.
    r = await anna.post("/account/resend-verification", data={"csrf": token})
    assert r.status_code == 429
    await asyncio.sleep(0.2)
    assert sent == ["anna@example.test"]

    # Once confirmed there is nothing to resend, and no button.
    await verify(anna, await user_id("anna"))
    r = await anna.post("/account/resend-verification", data={"csrf": token})
    assert r.status_code == 303
    assert "resend-verification" not in (await anna.get("/account")).text


async def test_the_resend_button_needs_a_login_and_a_mail_server(
        client, browser, sql, monkeypatch):
    from helpers import register
    await register(client, "Tengil")
    anna = await browser("198.51.100.21")
    await register(anna, "anna")
    token = await csrf(anna, "/account")
    # No mail server: say so, and send nothing.
    r = await anna.post("/account/resend-verification", data={"csrf": token})
    assert r.status_code == 200 and "cannot send email" in r.text
    # Logged out: sent to the login page.
    anon = await browser("198.51.100.22")
    r = await anon.post("/account/resend-verification",
                        data={"csrf": await csrf(anon, "/register")})
    assert r.status_code == 303 and r.headers["location"] == "/login"


async def test_an_admin_can_resend_to_everyone_who_has_not_confirmed(
        client, browser, sql, monkeypatch):
    from helpers import register, user_id, verify
    await register(client, "Tengil")
    anna = await browser("198.51.100.23")
    bob = await browser("198.51.100.24")
    await register(anna, "anna")
    await register(bob, "bob")
    await verify(bob, await user_id("bob"))          # bob has confirmed
    await sql("UPDATE users SET verify_sent_at = NULL")
    sent = _capture_mail(monkeypatch)

    # Only an admin can do it.
    r = await bob.post("/admin/resend-all-verifications",
                       data={"csrf": await csrf(bob, "/account")})
    assert r.status_code == 404 and not sent

    token = await csrf(client, "/admin")
    r = await client.post("/admin/resend-all-verifications", data={"csrf": token})
    assert r.status_code == 200 and "Queued 2 confirmation emails" in r.text
    await _until(lambda: len(sent) == 2)
    assert sorted(sent) == ["anna@example.test", "tengil@example.test"]

    # A second click an instant later mails nobody twice.
    r = await client.post("/admin/resend-all-verifications", data={"csrf": token})
    assert "Queued 0 confirmation emails" in r.text
    await asyncio.sleep(0.2)
    assert len(sent) == 2
