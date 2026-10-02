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
