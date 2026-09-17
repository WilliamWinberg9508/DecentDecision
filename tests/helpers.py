"""Small helpers so each test reads as the story it is testing.

Everything here goes through the application's own HTTP surface -- registering
means posting the registration form, approving means walking the verification
link. Reaching into the database to fabricate state would let a test pass over
a broken route.
"""

import re

from app import main as dd

CSRF_RE = re.compile(r'name="csrf" value="([^"]+)"')
TOKEN_RE = re.compile(r'<pre class="token">([^<]+)</pre>')
PASSWORD = "a-long-enough-pw"


async def csrf(c, path: str = "/register") -> str:
    """Fetch a page to pick up the CSRF cookie and its matching field."""
    m = CSRF_RE.search((await c.get(path)).text)
    assert m, f"no csrf field on {path} — that form would be unprotected"
    return m.group(1)


async def register(c, username, email=None, password=PASSWORD, note="", ip=None):
    return await c.post(
        "/register",
        headers={"cf-connecting-ip": ip} if ip else {},
        data={"username": username, "email": email or f"{username}@example.test",
              "password": password, "note": note,
              "csrf": await csrf(c, "/register")})


async def login(c, username, password=PASSWORD, next_="/account"):
    return await c.post("/login", data={
        "username": username, "password": password, "next": next_,
        "csrf": await csrf(c, "/login")})


async def user_id(username) -> int:
    row = await dd.q("SELECT id FROM users WHERE username = %s",
                     (username,), one=True)
    return row["id"]


async def verify(c, uid):
    """Walk the real verification link. Returns (response, agent token) — the
    token being shown exactly once, there."""
    link = await dd.issue_verification(uid)
    r = await c.get(link.replace(dd.SITE_URL, ""))
    m = TOKEN_RE.search(r.text)
    return r, (m.group(1) if m else "")


async def rotate_token(c):
    r = await c.post("/account/token", data={"csrf": await csrf(c, "/account")})
    m = TOKEN_RE.search(r.text)
    assert m, "rotating the agent token showed nothing"
    return m.group(1)


async def approved(c, username, ip=None):
    """Register and verify, arriving at a usable agent token."""
    await register(c, username, ip=ip)
    uid = await user_id(username)
    _, token = await verify(c, uid)
    # The very first account on an empty site is approved and given its agent
    # at registration, so verification has nothing left to hand over. Generate
    # one the way its owner would.
    return uid, token or await rotate_token(c)


async def post_issue(c, title="Should the tests pass?", body="Context.", days=7):
    return await c.post("/new", data={
        "title": title, "body": body, "days_open": days,
        "csrf": await csrf(c, "/new")})


async def issue_id(title="Should the tests pass?") -> int:
    row = await dd.q("SELECT id FROM issues WHERE title = %s ORDER BY id DESC "
                     "LIMIT 1", (title,), one=True)
    return row["id"]


def auth_header(token):
    return {"Authorization": f"Bearer {token}"}


async def cast(c, iid, token, good=True, bad=False, **kw):
    return await c.post(f"/issues/{iid}/vote", headers=auth_header(token),
                        json={"good": good, "bad": bad, **kw})
