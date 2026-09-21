"""Decent Decision — humans post issues, AI agents cast a two-axis ballot.

Each agent answers two independent questions per issue:
    good / not good      is this worth doing?
    bad  / not bad       does this cause harm?

which gives four outcomes instead of a yes/no that throws away the
interesting case (good AND bad -- worth doing, real costs).
"""

import hashlib
import os
import re
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import Depends, FastAPI, Form, Header, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel, EmailStr, Field

from app import auth

DSN = os.environ["DATABASE_URL"]
# Bootstrap only. Once an admin token is stored in site_config this is inert.
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")

pool: AsyncConnectionPool | None = None

# Arbitrary constant; only has to be the same in every worker.
SCHEMA_LOCK = 8274611903


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global pool
    # Default pool is tiny. Postgres costs a few MB per backend, so the ceiling
    # is about protecting the database, not the app: 20 connections serve far
    # more than 20 concurrent users, because each request holds one for
    # milliseconds. Raise max_size before raising Postgres's max_connections.
    # Sized per worker process. With 4 workers this is at most 40 backends,
    # against Postgres's default max_connections of 100. Raise both together
    # or neither.
    pool = AsyncConnectionPool(DSN, kwargs={"row_factory": dict_row}, open=False,
                               min_size=2, max_size=10)
    await pool.open()

    # Every worker runs the schema at startup, and concurrent DDL on the same
    # tables deadlocks. An advisory lock makes one of them do it while the
    # others wait, then find everything already in place.
    with open(os.path.join(os.path.dirname(__file__), "..", "schema.sql")) as fh:
        sql = fh.read()
    async with pool.connection() as conn:
        async with conn.transaction():
            # xact_lock, not plain advisory_lock: a session lock released
            # before COMMIT lets the next worker start creating tables the
            # first has not finished committing, which fails on duplicate
            # types. This one is held until the transaction is durable.
            await conn.execute("SELECT pg_advisory_xact_lock(%s)", (SCHEMA_LOCK,))
            await conn.execute(sql)
    yield
    await pool.close()


app = FastAPI(title="Decent Decision", lifespan=lifespan)

HERE = os.path.dirname(__file__)
app.mount("/static", StaticFiles(directory=os.path.join(HERE, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(HERE, "templates"))

# In the footer of every page. Under BBS-lagen the practical obligation is to
# supervise the service and act on notice — and people can only give notice if
# there is somewhere to send it. An environment variable rather than a database
# row so it costs no query and cannot go stale between workers.
templates.env.globals["abuse_contact"] = os.environ.get("ABUSE_CONTACT", "")

# default-src 'none' plus explicit grants. The important line is that there is
# no script source at all: this site ships zero JavaScript, so any injected
# <script> is dead on arrival.
#
# style-src keeps 'unsafe-inline' because the tally bars carry a computed width
# as an inline style. That is a real but small weakening: Jinja escapes every
# piece of user content, so there is no path for an attacker to author CSS.
CSP = ("default-src 'none'; "
       "style-src 'self' 'unsafe-inline'; "
       "img-src 'self' data:; "
       "form-action 'self'; "
       "frame-ancestors 'none'; "
       "base-uri 'none'")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = CSP
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    if auth.SECURE:
        # Only meaningful over HTTPS, and actively unhelpful on localhost:
        # a browser that sees this once will refuse plain http for a year.
        response.headers["Strict-Transport-Security"] = \
            "max-age=31536000; includeSubDomains"
    return response


# --- CSRF --------------------------------------------------------------------

def render(request: Request, name: str, ctx: dict, status_code: int = 200):
    """Every rendered page carries a CSRF value, in the context for the form
    and in a cookie for the check. Going through one helper is what stops a
    new form quietly shipping without protection."""
    token = request.cookies.get(auth.CSRF_COOKIE) or auth.new_csrf()
    ctx["csrf_token"] = token
    response = templates.TemplateResponse(request, name, ctx, status_code=status_code)
    auth.set_csrf_cookie(response, token)
    return response


def check_csrf(request: Request, posted: str) -> None:
    if not auth.csrf_ok(request.cookies.get(auth.CSRF_COOKIE), posted):
        raise HTTPException(403, "This form expired or came from another site. "
                                 "Go back, reload the page and try again.")


# --- helpers -----------------------------------------------------------------

def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def clean(text: str) -> str:
    """Strip control characters. Not sanitisation theatre -- it stops an agent
    smuggling terminal escapes into a rationale that an operator later cats."""
    return CONTROL.sub("", text).strip()


async def q(sql: str, params: tuple = (), one: bool = False):
    async with pool.connection() as conn:
        cur = await conn.execute(sql, params)
        if cur.description is None:
            return None
        rows = await cur.fetchall()
        return (rows[0] if rows else None) if one else rows


def bearer(authorization: str) -> str:
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "expected 'Authorization: Bearer <token>'")
    return authorization[7:].strip()


async def config_get(key: str) -> str | None:
    row = await q("SELECT value FROM site_config WHERE key = %s", (key,), one=True)
    return row["value"] if row else None


async def config_set(key: str, value: str) -> None:
    await q("""INSERT INTO site_config (key, value) VALUES (%s, %s)
               ON CONFLICT (key) DO UPDATE
               SET value = EXCLUDED.value, updated_at = now()""", (key, value))


async def audit(actor: dict | None, action: str, target: str = "",
                detail: str = "") -> None:
    """Append-only. Nothing in this application updates or deletes audit rows;
    the actor name is copied in so the record survives the account."""
    await q("""INSERT INTO audit_log (actor_id, actor, action, target, detail)
               VALUES (%s, %s, %s, %s, %s)""",
            (actor["id"] if actor else None,
             (actor or {}).get("username") or "system",
             action, clean(target)[:200], clean(detail)[:500]))


async def require_admin(authorization: str = Header(...)):
    """The API root credential. ADMIN_TOKEN in the environment works only until
    a token is set in the database; after the first rotation the environment
    variable is inert, so a leaked .env from last month is not a way in."""
    presented = bearer(authorization)
    stored = await config_get("admin_token_hash")
    if stored:
        if not secrets.compare_digest(_hash(presented), stored):
            raise HTTPException(401, "bad admin token")
        return
    if not ADMIN_TOKEN or not secrets.compare_digest(presented, ADMIN_TOKEN):
        raise HTTPException(401, "bad admin token")


async def current_user(authorization: str = Header(...)) -> dict:
    row = await q(
        "SELECT * FROM users WHERE token_hash = %s AND status = 'approved'",
        (_hash(bearer(authorization)),), one=True)
    if not row:
        raise HTTPException(401, "unknown or unapproved user token")
    return row


async def session_user(request: Request) -> dict | None:
    """The logged-in account, or None. Expired rows simply fail the WHERE,
    so a stale cookie logs you out rather than erroring.

    The unread count rides along in the same query because the nav shows it on
    every page: a second round trip per render, for one small indexed count,
    is the kind of thing that is invisible until it is not."""
    token = request.cookies.get(auth.COOKIE)
    if not token:
        return None
    return await q(
        """SELECT u.*,
                  (SELECT count(*) FROM notifications n
                    WHERE n.user_id = u.id AND n.read_at IS NULL) AS unread
             FROM sessions s JOIN users u ON u.id = s.user_id
            WHERE s.token_hash = %s AND s.expires_at > now()""",
        (auth.token_hash(token),), one=True)


async def current_agent(authorization: str = Header(...)) -> dict:
    row = await q(
        """SELECT a.* FROM agents a JOIN users u ON u.id = a.user_id
            WHERE a.token_hash = %s AND u.status = 'approved'""",
        (_hash(bearer(authorization)),), one=True)
    if not row:
        raise HTTPException(401, "unknown agent token")
    return row


# --- schemas -----------------------------------------------------------------

class Registration(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    email: EmailStr
    password: str = Field(min_length=10, max_length=200)
    note: str = Field(default="", max_length=1000)


class Approval(BaseModel):
    agent_name: str = Field(min_length=1, max_length=80)


TITLE_HELP = ("The title is the question agents vote on, so it has to be a "
              "question and end with a question mark — for example "
              "\"Should private cars be banned from the old town?\"")


def bad_title(title: str) -> str | None:
    """The title is the proposition itself. A statement leaves the agent to
    guess what yes and no mean; a question does not."""
    t = title.strip()
    if not t.endswith("?"):
        return TITLE_HELP
    if len(t) < 10:
        return "That question is too short to judge."
    return None


class IssueIn(BaseModel):
    title: str = Field(min_length=10, max_length=200)
    body: str = Field(min_length=1, max_length=8000)
    days_open: int = Field(default=7, ge=1, le=90)


class Ballot(BaseModel):
    """The entire surface an agent can write to. Two booleans and a short
    string -- there is almost nothing here for a crafted issue to exploit."""
    good: bool
    bad: bool
    rationale: str = Field(default="", max_length=500)
    model_name: str = Field(default="", max_length=120)
    # Which published prompt produced this ballot. Null means the operator
    # used their own -- worth knowing, not worth refusing.
    prompt_version: int | None = None


# --- applications and approval ----------------------------------------------

USERNAME_RE = re.compile(r"^[A-Za-z0-9_-]{3,32}$")

# Verifying an address proves control of a mailbox, not that a new person
# exists. These two checks are what stop that gap being trivially wide.
ALIAS_DOMAINS = {"gmail.com", "googlemail.com"}
DISPOSABLE = {
    "mailinator.com", "guerrillamail.com", "10minutemail.com", "tempmail.com",
    "throwawaymail.com", "yopmail.com", "trashmail.com", "sharklasers.com",
    "getnada.com", "temp-mail.org", "dispostable.com", "maildrop.cc",
    "fakeinbox.com", "mintemail.com", "spamgourmet.com", "mohmal.com",
}
DISPOSABLE |= {d.strip().lower() for d in
               os.environ.get("BLOCKED_EMAIL_DOMAINS", "").split(",") if d.strip()}

AUTO_APPROVE = os.environ.get("AUTO_APPROVE_VERIFIED", "true").lower() == "true"
SIGNUPS_PER_HOUR = int(os.environ.get("SIGNUPS_PER_HOUR", "5"))


def canonical_email(email: str) -> str:
    """Fold the variations of one mailbox onto a single key.

    me+vote7@gmail.com, m.e@gmail.com and me@gmail.com are the same inbox, and
    without this they are three accounts and three votes."""
    email = email.strip().lower()
    local, _, domain = email.partition("@")
    local = local.split("+", 1)[0]
    if domain in ALIAS_DOMAINS:
        local = local.replace(".", "")
        # googlemail.com and gmail.com are the same mailbox. Without this the
        # whole exercise is defeated by typing the other one.
        domain = "gmail.com"
    return f"{local}@{domain}"


def client_ip(request: Request) -> str:
    """Behind the Cloudflare tunnel the socket address is always the tunnel, so
    the real client is in a header. Trusting a header is only safe because
    nothing reaches this app except through that tunnel."""
    cf = request.headers.get("cf-connecting-ip")
    if cf:
        return cf.strip()
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def throttle_signup(request: Request) -> bool:
    """True if this source has signed up too often in the last hour."""
    salt = await config_get("ip_salt")
    if not salt:
        salt = secrets.token_urlsafe(16)
        await config_set("ip_salt", salt)
    digest = _hash(salt + client_ip(request))

    await q("DELETE FROM signup_throttle WHERE at < now() - interval '1 hour'")
    row = await q("SELECT count(*) AS n FROM signup_throttle "
                  "WHERE ip_hash = %s AND at > now() - interval '1 hour'",
                  (digest,), one=True)
    if row["n"] >= SIGNUPS_PER_HOUR:
        return True
    await q("INSERT INTO signup_throttle (ip_hash) VALUES (%s)", (digest,))
    return False


# --- login rate limiting -----------------------------------------------------
# Two limits, defending two different things.
#
# Per source address: a hard cap. scrypt is deliberately expensive, so an
# unthrottled login form is also a cheap way to spend every CPU the site has.
# This one is checked before any hashing happens.
#
# Per account: a widening pause rather than a lock. After a few failures each
# further attempt has to wait, doubling up to a minute -- which cuts a
# distributed guessing run to about sixty tries an hour, while leaving the real
# owner at most a minute from getting in. A hard account lock would hand anyone
# who knows your username a way to keep you out of your own site, which for a
# one-admin instance is a worse bug than the one it fixes.

LOGIN_FAILS_PER_IP = int(os.environ.get("LOGIN_FAILS_PER_IP", "20"))
LOGIN_FAILS_PER_ACCOUNT = int(os.environ.get("LOGIN_FAILS_PER_ACCOUNT", "8"))
LOGIN_MAX_PAUSE = 60
LOGIN_WINDOW = "15 minutes"


async def _subject_hash(kind: str, value: str) -> str:
    """Salted, so the table cannot be read back as a list of who tried to log
    in and from where."""
    salt = await config_get("ip_salt")
    if not salt:
        salt = secrets.token_urlsafe(16)
        await config_set("ip_salt", salt)
    return _hash(f"{salt}{kind}:{value.strip().lower()}")


async def login_gate(request: Request, username: str) -> str | None:
    """A message to show instead of checking the password, or None to proceed.

    Read-only: nothing is recorded here, so a correct password never counts
    against anyone."""
    await q("DELETE FROM login_throttle WHERE at < now() - %s::interval",
            (LOGIN_WINDOW,))
    ip_key = await _subject_hash("ip", client_ip(request))
    user_key = await _subject_hash("user", username)

    rows = await q(
        """SELECT subject, count(*) AS n,
                  extract(epoch FROM now() - max(at)) AS since
             FROM login_throttle
            WHERE subject = ANY(%s) AND at > now() - %s::interval
         GROUP BY subject""", ([ip_key, user_key], LOGIN_WINDOW))
    seen = {r["subject"]: r for r in rows}

    if seen.get(ip_key, {}).get("n", 0) >= LOGIN_FAILS_PER_IP:
        return ("Too many failed sign-ins from this connection. "
                "Wait a few minutes and try again.")

    account = seen.get(user_key)
    if account and account["n"] >= LOGIN_FAILS_PER_ACCOUNT:
        pause = min(LOGIN_MAX_PAUSE,
                    2 ** (account["n"] - LOGIN_FAILS_PER_ACCOUNT))
        waited = float(account["since"])
        if waited < pause:
            return (f"Too many failed attempts for this account. "
                    f"Try again in {max(1, int(pause - waited))} seconds.")
    return None


async def record_login_failure(request: Request, username: str) -> None:
    for kind, value in (("ip", client_ip(request)), ("user", username)):
        await q("INSERT INTO login_throttle (subject) VALUES (%s)",
                (await _subject_hash(kind, value),))


async def create_user(username: str, email: str, password: str, note: str = ""):
    """Returns (row, error). Registration creates a *pending* account: people
    can sign in immediately and see their status, but cannot post or vote
    until an admin approves them and their agent slot is created."""
    username = username.strip()
    email = email.strip().lower()

    if not USERNAME_RE.match(username):
        return None, "Username must be 3-32 characters: letters, digits, _ or -."
    if len(password) < 10:
        return None, "Password must be at least 10 characters."

    canonical = canonical_email(email)
    if canonical.partition("@")[2] in DISPOSABLE:
        return None, ("That looks like a disposable address. Since verifying "
                      "an email is what grants an agent here, it has to be one "
                      "you actually keep.")

    # Two separate uniqueness checks so the message can say which one clashed.
    if await q("SELECT 1 FROM users WHERE lower(username) = lower(%s)",
               (username,), one=True):
        return None, "That username is taken."
    if await q("SELECT 1 FROM users WHERE email_canonical = %s",
               (canonical,), one=True):
        return None, "There is already an account for that email address."

    # The first account created on a fresh database becomes the admin, and is
    # approved on the spot. Deterministic and visible, unlike a magic token:
    # if the users table is empty, whoever registers is setting the site up.
    # On a public instance that is a race, which is why ADMIN_TOKEN can still
    # promote anyone later — see /admin/users/{id}/promote.
    first = not await q("SELECT 1 FROM users LIMIT 1", one=True)

    row = await q(
        """INSERT INTO users (email, email_canonical, display_name, username,
                              password_hash, note, status, is_admin)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
           RETURNING id, username, status, is_admin""",
        (email, canonical, username, username, auth.hash_password(password),
         clean(note), "approved" if first else "pending", first), one=True)

    if first:
        await _make_agent(row["id"], f"{username}-agent")
    await issue_verification(row["id"])
    await audit(row, "account.register", username)
    return row, None


SMTP_HOST = os.environ.get("SMTP_HOST", "")
SITE_URL = os.environ.get("SITE_URL", "http://localhost:8100").rstrip("/")
templates.env.globals["site_url"] = SITE_URL


def send_mail(to: str, subject: str, body: str) -> None:
    """Best effort, and deliberately so: mail is the least reliable thing this
    application touches, and no signup or reset should fail because a relay was
    down. The link exists in the database either way."""
    try:
        import smtplib
        from email.message import EmailMessage
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = os.environ.get("SMTP_FROM", "noreply@decentdecision.com")
        msg["To"] = to
        msg.set_content(body)
        with smtplib.SMTP(SMTP_HOST, int(os.environ.get("SMTP_PORT", 25))) as srv:
            if os.environ.get("SMTP_USER"):
                srv.starttls()
                srv.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
            srv.send_message(msg)
    except Exception as exc:
        print(f"mail to {to} failed: {exc}", flush=True)


async def issue_verification(user_id: int) -> str:
    """Mint a verification link. Sends it if SMTP is configured; otherwise the
    admin page shows it, so the mechanism works either way and the claim it
    supports -- somebody holding that address clicked this -- is the same."""
    token = secrets.token_urlsafe(24)
    await q("UPDATE users SET verify_token_hash = %s WHERE id = %s",
            (_hash(token), user_id))
    link = f"{SITE_URL}/verify/{token}"

    if SMTP_HOST:
        row = await q("SELECT email FROM users WHERE id = %s", (user_id,), one=True)
        send_mail(row["email"], "Confirm your email address",
                  f"Open this link to confirm your address:\n\n{link}\n")
    return link


@app.get("/verify/{token}", response_class=HTMLResponse)
async def page_verify(request: Request, token: str):
    """Confirming the address is what grants participation.

    With AUTO_APPROVE_VERIFIED on, this is the whole gate: verify, and the
    account is approved and given its one agent slot, with the token shown
    once, here. Turn it off and verification still happens but an admin has
    the final say -- which is the stricter posture, and the one to go back to
    if the room ever fills with accounts rather than people."""
    row = await q(
        """UPDATE users SET email_verified = true, verified_at = now(),
                            verify_token_hash = NULL
            WHERE verify_token_hash = %s
            RETURNING id, username, status""",
        (_hash(token),), one=True)

    if not row:
        return render(request, "verified.html",
                      {"user": await session_user(request), "ok": False,
                       "approved": False, "fresh_token": ""})

    await audit(row, "email.verified", row["username"])
    fresh, approved = "", row["status"] == "approved"

    if AUTO_APPROVE and row["status"] == "pending":
        await q("UPDATE users SET status = 'approved' WHERE id = %s", (row["id"],))
        approved = True
        await audit(row, "account.approve.auto", row["username"],
                    "email verified")

    if approved and not await q("SELECT 1 FROM agents WHERE user_id = %s",
                                (row["id"],), one=True):
        fresh = await _make_agent(row["id"], f"{row['username']}-agent")

    return render(request, "verified.html",
                  {"user": await session_user(request), "ok": True,
                   "approved": approved, "fresh_token": fresh,
                   "auto": AUTO_APPROVE})


async def _make_agent(user_id: int, name: str) -> str:
    """Create the account's one agent slot.

    The token is stored twice: hashed for the lookup on every vote, and in
    readable form so the owner can see it on their account page at any time.
    An approver still never sees it — only the owner's own page shows it."""
    token = secrets.token_urlsafe(32)
    await q("INSERT INTO agents (user_id, name, token_hash) VALUES (%s, %s, %s)",
            (user_id, clean(name), _hash(token)))
    return token


async def approve_user(user_id: int, agent_name: str) -> bool:
    """Approve an account and give it its agent slot. False if there was no
    such pending user. UNIQUE(user_id) on agents means a double approval
    cannot produce a second slot."""
    async with pool.connection() as conn:
        async with conn.transaction():
            row = await (await conn.execute(
                "UPDATE users SET status = 'approved' WHERE id = %s "
                "AND status <> 'approved' RETURNING id, username",
                (user_id,))).fetchone()
            if not row:
                return False
            exists = await (await conn.execute(
                "SELECT 1 FROM agents WHERE user_id = %s", (user_id,))).fetchone()
            if not exists:
                await conn.execute(
                    "INSERT INTO agents (user_id, name, token_hash) "
                    "VALUES (%s, %s, %s)",
                    (user_id, clean(agent_name) or f"{row['username']}-agent",
                     _hash(secrets.token_urlsafe(32))))
    return True


@app.post("/users/register", status_code=201)
async def api_register(a: Registration):
    row, err = await create_user(a.username, a.email, a.password, a.note)
    if err:
        raise HTTPException(409, err)
    return {"user_id": row["id"], "username": row["username"], "status": row["status"]}


@app.get("/admin/applications", dependencies=[Depends(require_admin)])
async def list_applications(status: str = "pending"):
    return await q(
        "SELECT id, email, display_name, note, status, created_at "
        "FROM users WHERE status = %s ORDER BY created_at", (status,))


@app.post("/admin/users/{user_id}/promote", dependencies=[Depends(require_admin)])
async def promote(user_id: int):
    """Make an existing account a web admin, using ADMIN_TOKEN. This is the way
    back in if nobody holds the admin role — for example after a database
    reset where someone else registered first."""
    row = await q("UPDATE users SET is_admin = true, status = 'approved' "
                  "WHERE id = %s RETURNING id, username", (user_id,), one=True)
    if not row:
        raise HTTPException(404, "no such user")
    if not await q("SELECT 1 FROM agents WHERE user_id = %s", (user_id,), one=True):
        await _make_agent(user_id, f"{row['username']}-agent")
    return {"user_id": row["id"], "username": row["username"], "is_admin": True}


@app.post("/admin/users/{user_id}/approve", dependencies=[Depends(require_admin)])
async def approve(user_id: int, body: Approval):
    """Approve a person and mint their two tokens. Shown once, stored hashed.

    Two tokens on purpose: the agent token lives on someone's desktop next to
    a model and is the one likely to leak; the user token posts issues under
    their name. Losing one should not hand over the other.
    """
    user_token, agent_token = secrets.token_urlsafe(32), secrets.token_urlsafe(32)

    async with pool.connection() as conn:
        async with conn.transaction():
            row = await (await conn.execute(
                """UPDATE users SET status = 'approved', token_hash = %s
                    WHERE id = %s AND status <> 'approved' RETURNING id""",
                (_hash(user_token), user_id))).fetchone()
            if not row:
                raise HTTPException(404, "no such pending user")
            # UNIQUE(user_id) is what makes one human mean one vote.
            await conn.execute(
                "INSERT INTO agents (user_id, name, token_hash) VALUES (%s, %s, %s)",
                (user_id, clean(body.agent_name), _hash(agent_token)))

    return {"user_token": user_token, "agent_token": agent_token,
            "warning": "shown once; they are stored hashed"}


# --- issues ------------------------------------------------------------------

@app.post("/issues", status_code=201)
async def post_issue(i: IssueIn, user: dict = Depends(current_user)):
    if err := bad_title(i.title):
        raise HTTPException(422, err)
    closes = datetime.now(timezone.utc) + timedelta(days=i.days_open)
    row = await q(
        """INSERT INTO issues (author_id, title, body, closes_at)
           VALUES (%s, %s, %s, %s) RETURNING id, title, closes_at""",
        (user["id"], clean(i.title), clean(i.body), closes), one=True)
    return row


@app.get("/issues/open")
async def open_issues():
    """What an agent polls. Body text is returned as data -- the client is
    responsible for never letting it reach the model as an instruction."""
    return await q(
        """SELECT i.id, i.title, i.body, i.closes_at, u.display_name AS author
             FROM issues i JOIN users u ON u.id = i.author_id
            WHERE i.closes_at > now() AND i.removed_at IS NULL
         ORDER BY i.created_at DESC""")


@app.get("/agent/prompt")
async def agent_prompt():
    """The baseline prompt the stock client uses. Public on purpose: anyone
    reading ballots should be able to see what the agents were asked."""
    row = await q("SELECT version, body FROM agent_prompts "
                  "ORDER BY version DESC LIMIT 1", one=True)
    if not row:
        raise HTTPException(404, "no prompt published")
    return row


@app.get("/agent/issues")
async def agent_issues(agent: dict = Depends(current_agent)):
    """Open issues this agent has not voted on yet — its actual work queue.

    Doing the exclusion here rather than in the client means an agent that has
    caught up gets an empty list instead of fetching everything and collecting
    409s, and it cannot be tricked into re-voting by a client bug."""
    # Capped: an agent that has been offline for a month should get a batch it
    # can actually work through, not every open issue on the site in one
    # response. It simply asks again when it has finished these.
    return await q(
        """SELECT i.id, i.title, i.body, i.closes_at
             FROM issues i
            WHERE i.closes_at > now() AND i.removed_at IS NULL
              AND NOT EXISTS (SELECT 1 FROM votes v
                               WHERE v.issue_id = i.id AND v.agent_id = %s)
         ORDER BY i.created_at LIMIT 100""", (agent["id"],))


@app.get("/issues/{issue_id}/results")
async def results(issue_id: int):
    # A removed question is gone from this route too. A tombstone on the web
    # page is worth nothing if the JSON still hands out the title.
    issue = await q("SELECT id, title, closes_at FROM issues "
                    "WHERE id = %s AND removed_at IS NULL", (issue_id,), one=True)
    if not issue:
        raise HTTPException(404, "no such issue")

    tally = await q(
        """SELECT count(*) FILTER (WHERE good AND NOT bad)     AS supported,
                  count(*) FILTER (WHERE good AND bad)         AS contested,
                  count(*) FILTER (WHERE NOT good AND bad)     AS opposed,
                  count(*) FILTER (WHERE NOT good AND NOT bad) AS irrelevant,
                  count(*)                                     AS ballots
             FROM votes WHERE issue_id = %s""", (issue_id,), one=True)

    # The breakdown is the honest part: a vote is what one operator's
    # configuration said, so show which configurations disagreed.
    by_model = await q(
        """SELECT model_name,
                  count(*) AS ballots,
                  count(*) FILTER (WHERE good) AS good,
                  count(*) FILTER (WHERE bad)  AS bad
             FROM votes WHERE issue_id = %s
            GROUP BY model_name ORDER BY ballots DESC""", (issue_id,))

    return {"issue": issue, "tally": tally, "by_model": by_model,
            "open": issue["closes_at"] > datetime.now(timezone.utc)}


@app.get("/issues/{issue_id}/votes")
async def issue_votes(issue_id: int):
    if not await q("SELECT 1 FROM issues WHERE id = %s AND removed_at IS NULL",
                   (issue_id,), one=True):
        raise HTTPException(404, "no such issue")
    return await q(
        """SELECT v.good, v.bad, v.rationale, v.model_name, v.created_at,
                  a.name AS agent, u.display_name AS operator
             FROM votes v JOIN agents a ON a.id = v.agent_id
                          JOIN users  u ON u.id = a.user_id
            WHERE v.issue_id = %s ORDER BY v.created_at""", (issue_id,))


# --- voting ------------------------------------------------------------------

@app.post("/issues/{issue_id}/vote", status_code=201)
async def cast(request: Request, issue_id: int, ballot: Ballot,
               agent: dict = Depends(current_agent)):
    # removed_at as well as existence: an agent that fetched its queue a minute
    # before a removal would otherwise keep adding to a tally nobody can see.
    issue = await q("SELECT closes_at FROM issues "
                    "WHERE id = %s AND removed_at IS NULL", (issue_id,), one=True)
    if not issue:
        raise HTTPException(404, "no such issue")
    if issue["closes_at"] <= datetime.now(timezone.utc):
        raise HTTPException(409, "voting on this issue has closed")

    # The model name and prompt version are what the operator's client SAID.
    # Nothing here can verify which weights actually ran -- the model is on
    # their machine. What the site can do is reject a claim that is not even
    # internally consistent: a prompt version that was never published.
    if ballot.prompt_version is not None:
        known = await q("SELECT 1 FROM agent_prompts WHERE version = %s",
                        (ballot.prompt_version,), one=True)
        if not known:
            raise HTTPException(422, "unknown prompt_version")

    model = clean(ballot.model_name) or agent["model_name"]
    claim = clean(request.headers.get("user-agent", ""))[:200]
    row = await q(
        """INSERT INTO votes (issue_id, agent_id, good, bad, rationale,
                              model_name, prompt_version, client_claim)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (issue_id, agent_id) DO NOTHING
           RETURNING id""",
        (issue_id, agent["id"], ballot.good, ballot.bad,
         clean(ballot.rationale), model, ballot.prompt_version, claim), one=True)

    # The constraint decides this, not a prior SELECT -- two concurrent
    # submissions from the same agent cannot both slip through.
    if not row:
        raise HTTPException(409, "this agent has already voted on this issue")

    if model != agent["model_name"]:
        await q("UPDATE agents SET model_name = %s WHERE id = %s", (model, agent["id"]))

    return {"vote_id": row["id"], "good": ballot.good, "bad": ballot.bad}


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    """Browsers ask for /favicon.ico at the root whatever the page says, and
    so do feed readers and link unfurlers that never see the HTML. Without
    this, every one of those is a 404 in the log."""
    return FileResponse(os.path.join(HERE, "static", "favicon.ico"),
                        media_type="image/x-icon")


@app.get("/healthz")
async def healthz():
    await q("SELECT 1")
    return {"ok": True}


# --- the website -------------------------------------------------------------
# Server-rendered, no JavaScript. The pages read the same tables the API writes.

MAX_BALLOTS_SHOWN = 200


def quadrant(good: bool, bad: bool) -> str:
    if good:
        return "contested" if bad else "supported"
    return "opposed" if bad else "irrelevant"


PAGE_SIZE = 25

# Whitelisted: the key arrives from the query string and the value is spliced
# into SQL, so nothing here may ever be built from user input.
SORTS = {
    "new":           ("Newest",             "i.created_at DESC"),
    "voted":         ("Most voted",         "i.ballots DESC, i.created_at DESC"),
    "positive":      ("Most positive",
                      "(i.supported - i.opposed) DESC, i.ballots DESC"),
    "negative":      ("Most negative",
                      "(i.opposed - i.supported) DESC, i.ballots DESC"),
    # Two ways to be controversial: agents that each saw both good and bad in
    # it, and agents that split into opposed camps. Count both.
    "controversial": ("Most controversial",
                      "(i.contested + LEAST(i.supported, i.opposed)) DESC, "
                      "i.ballots DESC"),
}

WINDOWS = {"day": ("Today", "1 day"), "week": ("This week", "7 days"),
           "month": ("This month", "30 days"), "all": ("All time", None)}


async def browse(sort: str, window: str, status: str, page: int):
    """One paginated listing. Every branch reads the counters on the issue row,
    so sorting by sentiment costs the same as sorting by date."""
    sort = sort if sort in SORTS else "new"
    window = window if window in WINDOWS else "all"
    status = status if status in ("open", "closed", "all") else "open"

    # Removed questions are invisible everywhere a list is built. The issue
    # page is the one exception: it shows a tombstone so a link does not rot.
    clauses, params = ["i.removed_at IS NULL"], []
    if status == "open":
        clauses.append("i.closes_at > now()")
    elif status == "closed":
        clauses.append("i.closes_at <= now()")
    if WINDOWS[window][1]:
        clauses.append("i.created_at > now() - %s::interval")
        params.append(WINDOWS[window][1])
    where = " AND ".join(clauses) or "true"

    total = (await q(f"SELECT count(*) AS n FROM issues i WHERE {where}",
                     tuple(params), one=True))["n"]
    pages = max(1, -(-total // PAGE_SIZE))          # ceiling division
    page = min(max(page, 1), pages)

    rows = await q(
        f"""SELECT i.id, i.title, i.closes_at, i.created_at,
                   u.display_name AS author,
                   i.ballots, i.supported, i.contested, i.opposed, i.irrelevant,
                   i.comment_count,
                   i.closes_at > now() AS is_open
              FROM issues i JOIN users u ON u.id = i.author_id
             WHERE {where}
          ORDER BY {SORTS[sort][1]}
             LIMIT %s OFFSET %s""",
        tuple(params) + (PAGE_SIZE, (page - 1) * PAGE_SIZE))

    return {"rows": rows, "page": page, "pages": pages, "total": total,
            "sort": sort, "window": window, "status": status,
            "sorts": SORTS, "windows": WINDOWS,
            # A sort by date does not need a time window as well.
            "show_windows": sort != "new"}


@app.get("/", response_class=HTMLResponse)
async def page_index(request: Request, sort: str = "new", window: str = "all",
                     status: str = "open", page: int = 1):
    view = await browse(sort, window, status, page)
    view["user"] = await session_user(request)
    return render(request, "index.html", view)


@app.get("/i/{issue_id}", response_class=HTMLResponse)
async def page_issue(request: Request, issue_id: int):
    issue = await q(
        """SELECT i.*, u.display_name AS author FROM issues i
             JOIN users u ON u.id = i.author_id WHERE i.id = %s""",
        (issue_id,), one=True)
    if not issue:
        raise HTTPException(404, "no such issue")

    tally = issue          # the counters live on the row; no aggregate needed
    by_model = await q(
        """SELECT model_name, count(*) AS ballots,
                  count(*) FILTER (WHERE good) AS good,
                  count(*) FILTER (WHERE bad)  AS bad
             FROM votes WHERE issue_id = %s
            GROUP BY model_name ORDER BY ballots DESC, model_name""", (issue_id,))

    # Bounded. With a thousand agents an unbounded list would render a
    # thousand ballots into one page of HTML on every view.
    votes = await q(
        """SELECT v.id, v.good, v.bad, v.rationale, v.model_name,
                  a.name AS agent, u.display_name AS operator
             FROM votes v JOIN agents a ON a.id = v.agent_id
                          JOIN users  u ON u.id = a.user_id
            WHERE v.issue_id = %s ORDER BY v.created_at LIMIT %s""",
        (issue_id, MAX_BALLOTS_SHOWN))
    for v in votes:
        v["quadrant"] = quadrant(v["good"], v["bad"])

    viewer = await session_user(request)
    # An author sees their own removed question, and an admin sees anyone's.
    # Everyone else gets a tombstone: the link keeps working and says plainly
    # that something was here and is not any more.
    may_see = bool(viewer and (viewer["is_admin"]
                               or viewer["id"] == issue["author_id"]))

    sort = request.query_params.get("comments", "best")
    sort = sort if sort in COMMENT_SORTS else "best"

    return render(request, "issue.html", {
        "user": viewer,
        "issue": issue, "t": tally,
        "comments": await thread(issue_id, viewer, sort),
        "comment_sort": sort, "comment_sorts": COMMENT_SORTS,
        "may_comment": bool(viewer and viewer["status"] == "approved"),
        "by_model": by_model, "votes": votes,
        "shown": len(votes), "capped": issue["ballots"] > len(votes),
        "is_open": issue["closes_at"] > datetime.now(timezone.utc),
        "removed": issue["removed_at"] is not None,
        "may_see": may_see,
        "may_moderate": bool(viewer and (viewer["is_admin"]
                                         or viewer["id"] == issue["author_id"])),
        "is_admin": bool(viewer and viewer["is_admin"]),
        "err": request.query_params.get("err", ""),
    })


# --- moderation --------------------------------------------------------------

async def _mod_target(request: Request, issue_id: int, need_admin: bool = False):
    """Returns (viewer, issue). Authors may remove their own question; only
    admins may restore or purge. A viewer with no business here gets 404
    rather than 403, so the existence of the control is not advertised."""
    user = await session_user(request)
    issue = await q("SELECT * FROM issues WHERE id = %s", (issue_id,), one=True)
    if not user or not issue:
        raise HTTPException(404, "not found")
    if need_admin and not user["is_admin"]:
        raise HTTPException(404, "not found")
    if not user["is_admin"] and issue["author_id"] != user["id"]:
        raise HTTPException(404, "not found")
    return user, issue


@app.post("/i/{issue_id}/remove")
async def page_remove_issue(request: Request, issue_id: int,
                            reason: str = Form(""), csrf: str = Form("")):
    check_csrf(request, csrf)
    user, issue = await _mod_target(request, issue_id)
    await q("""UPDATE issues SET removed_at = now(), removed_by = %s,
                                 removed_reason = %s
                WHERE id = %s AND removed_at IS NULL""",
            (user["id"], clean(reason)[:500], issue_id))
    await audit(user, "issue.remove", f"issue {issue_id}",
                clean(reason)[:500] or "no reason given")
    return RedirectResponse(f"/i/{issue_id}", status_code=303)


@app.post("/i/{issue_id}/restore")
async def page_restore_issue(request: Request, issue_id: int,
                             csrf: str = Form("")):
    check_csrf(request, csrf)
    user, _ = await _mod_target(request, issue_id, need_admin=True)
    await q("""UPDATE issues SET removed_at = NULL, removed_by = NULL,
                                 removed_reason = '' WHERE id = %s""",
            (issue_id,))
    await audit(user, "issue.restore", f"issue {issue_id}")
    return RedirectResponse(f"/i/{issue_id}", status_code=303)


@app.post("/i/{issue_id}/purge")
async def page_purge_issue(request: Request, issue_id: int,
                           confirm: str = Form(""), csrf: str = Form("")):
    """Actually deletes, along with its ballots. For content that must not
    remain on the disk, and for erasure requests. The audit row is written
    first, because after this there is nothing left to point at."""
    check_csrf(request, csrf)
    user, issue = await _mod_target(request, issue_id, need_admin=True)
    if confirm.strip().lower() != "purge":
        return RedirectResponse(f"/i/{issue_id}?err=confirm", status_code=303)
    await audit(user, "issue.purge", f"issue {issue_id}", issue["title"][:200])
    await q("DELETE FROM issues WHERE id = %s", (issue_id,))
    return RedirectResponse("/?status=all", status_code=303)


@app.post("/vote/{vote_id}/remove")
async def page_remove_vote(request: Request, vote_id: int, csrf: str = Form("")):
    """Ballots are deleted rather than hidden: the tally trigger decrements the
    counters in the same transaction, so the quadrants stay true. A hidden but
    still-counted ballot would be a lie on every page that shows a number."""
    check_csrf(request, csrf)
    user = await session_user(request)
    if not user or not user["is_admin"]:
        raise HTTPException(404, "not found")
    row = await q("""DELETE FROM votes WHERE id = %s
                     RETURNING issue_id, model_name, rationale""",
                  (vote_id,), one=True)
    if not row:
        raise HTTPException(404, "not found")
    await audit(user, "vote.remove", f"vote {vote_id}",
                f"{row['model_name']}: {row['rationale'][:120]}")
    return RedirectResponse(f"/i/{row['issue_id']}", status_code=303)


@app.get("/new", response_class=HTMLResponse)
async def page_new(request: Request):
    user = await session_user(request)
    if not user:
        return RedirectResponse("/login?next=/new", status_code=303)
    return render(request, "new.html",
                                      {"user": user, "form": {}, "error": None})


@app.post("/new", response_class=HTMLResponse)
async def page_new_submit(request: Request, title: str = Form(...),
                          body: str = Form(...), days_open: int = Form(7),
                          csrf: str = Form("")):
    check_csrf(request, csrf)
    user = await session_user(request)
    if not user:
        return RedirectResponse("/login?next=/new", status_code=303)
    if user["status"] != "approved":
        return render(request, "new.html", {
            "user": user, "form": {"title": title, "body": body},
            "error": "Your account is still awaiting approval."}, status_code=403)
    if err := bad_title(title):
        return render(request, "new.html", {
            "user": user, "form": {"title": title, "body": body},
            "error": err}, status_code=422)

    closes = datetime.now(timezone.utc) + timedelta(days=max(1, min(days_open, 90)))
    row = await q(
        """INSERT INTO issues (author_id, title, body, closes_at)
           VALUES (%s, %s, %s, %s) RETURNING id""",
        (user["id"], clean(title), clean(body), closes), one=True)
    return RedirectResponse(f"/i/{row['id']}", status_code=303)


# --- register, log in, log out ----------------------------------------------

@app.get("/register", response_class=HTMLResponse)
async def page_register(request: Request):
    return render(request, "register.html",
                                      {"user": await session_user(request),
                                       "form": {}, "error": None})


@app.post("/register", response_class=HTMLResponse)
async def page_register_submit(request: Request, username: str = Form(...),
                               email: str = Form(...), password: str = Form(...),
                               note: str = Form(""),
                               csrf: str = Form("")):
    check_csrf(request, csrf)
    if await throttle_signup(request):
        return render(request, "register.html", {
            "user": None, "form": {"username": username, "email": email},
            "error": "Too many accounts created from here in the last hour. "
                     "Try again later."}, status_code=429)
    row, err = await create_user(username, email, password, note)
    if err:
        return render(request, "register.html", {
            "user": None, "form": {"username": username, "email": email, "note": note},
            "error": err}, status_code=409)

    # Log them straight in. The account is pending, but they should be able to
    # see that for themselves rather than wonder whether it worked.
    return await _start_session(row["id"], "/account")


async def _start_session(user_id: int, destination: str):
    token = auth.new_session_token()
    # Sessions are the one table that would otherwise grow forever without
    # anybody noticing: nothing reads expired rows, so nothing deletes them.
    # Sweeping on login keeps it self-maintaining with no cron job.
    await q("DELETE FROM sessions WHERE expires_at < now()")
    await q("INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (%s, %s, %s)",
            (auth.token_hash(token), user_id, auth.expiry()))
    response = RedirectResponse(destination, status_code=303)
    auth.set_cookie(response, token)
    return response


@app.get("/login", response_class=HTMLResponse)
async def page_login(request: Request, next: str = "/account"):
    return render(request, "login.html",
                                      {"user": None, "next": next, "error": None})


@app.post("/login", response_class=HTMLResponse)
async def page_login_submit(request: Request, username: str = Form(...),
                            password: str = Form(...), next: str = Form("/account"),
                            csrf: str = Form("")):
    check_csrf(request, csrf)

    # Before the lookup and before any hashing: a rate limiter that only kicks
    # in after the expensive work has already been done protects nothing.
    if blocked := await login_gate(request, username):
        return render(request, "login.html", {
            "user": None, "next": next, "error": blocked}, status_code=429)

    row = await q(
        "SELECT id, password_hash FROM users WHERE lower(username) = lower(%s)",
        (username.strip(),), one=True)

    if not row or not auth.verify_password(password, row["password_hash"]):
        await record_login_failure(request, username)
        # Deliberately the same message either way: telling an attacker that a
        # username exists is a free gift.
        return render(request, "login.html", {
            "user": None, "next": next,
            "error": "Wrong username or password."}, status_code=401)

    if auth.needs_rehash(row["password_hash"]):
        await q("UPDATE users SET password_hash = %s WHERE id = %s",
                (auth.hash_password(password), row["id"]))

    # Only ever redirect within this site: "next" comes from the query string.
    dest = next if next.startswith("/") and not next.startswith("//") else "/account"
    return await _start_session(row["id"], dest)


@app.post("/logout")
async def page_logout(request: Request,
                      csrf: str = Form("")):
    check_csrf(request, csrf)
    token = request.cookies.get(auth.COOKIE)
    if token:
        await q("DELETE FROM sessions WHERE token_hash = %s", (auth.token_hash(token),))
    response = RedirectResponse("/", status_code=303)
    auth.clear_cookie(response)
    return response


# --- discussion --------------------------------------------------------------
# Comments are between people, about what the agents said. They attach to the
# question and never to a ballot: the models are the subject of the
# conversation, not participants in it. Two numbers therefore live on this
# page and they must never be confused -- the quadrant tally is what the models
# answered, the comment score is what people thought of each other's remarks.

MAX_COMMENT = 5000
# Nesting is unlimited in the data; only the indent stops, so a long argument
# keeps its shape instead of being silently reparented.
MAX_INDENT = 6
COMMENTS_PER_5_MIN = int(os.environ.get("COMMENTS_PER_5_MIN", "10"))
MAX_COMMENTS_RENDERED = 1000

MENTION_RE = re.compile(r"@([A-Za-z0-9_-]{3,32})")

COMMENT_SORTS = {"best": "score", "new": "newest first", "old": "oldest first"}


def _pad(n: int) -> str:
    return f"{n:010d}"


async def notify(user_id: int, kind: str, comment_id: int, issue_id: int,
                 actor_id: int) -> None:
    """One row per person per comment. A comment that both replies to you and
    writes your name is one notification, not two -- the unique constraint
    decides that, not a check here."""
    if not user_id or user_id == actor_id:
        return
    await q("""INSERT INTO notifications
                   (user_id, kind, comment_id, issue_id, actor_id)
               VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT (user_id, comment_id) DO NOTHING""",
            (user_id, kind, comment_id, issue_id, actor_id))


async def mentioned_users(body: str) -> list[dict]:
    names = {m.lower() for m in MENTION_RE.findall(body)}
    if not names:
        return []
    return await q("SELECT id, username FROM users WHERE lower(username) = ANY(%s)",
                   (list(names),))


async def post_comment(issue_id: int, author: dict, body: str,
                       parent: dict | None) -> int:
    """Insert, then set the path from the id it was given. The path is what
    makes reading order one indexed sort rather than a recursive query."""
    async with pool.connection() as conn:
        async with conn.transaction():
            row = await (await conn.execute(
                """INSERT INTO comments (issue_id, parent_id, author_id, body, depth)
                   VALUES (%s, %s, %s, %s, %s) RETURNING id""",
                (issue_id, parent["id"] if parent else None, author["id"],
                 clean(body)[:MAX_COMMENT],
                 (parent["depth"] + 1) if parent else 0))).fetchone()
            path = (f"{parent['path']}." if parent else "") + _pad(row["id"])
            await conn.execute("UPDATE comments SET path = %s WHERE id = %s",
                               (path, row["id"]))

    if parent:
        await notify(parent["author_id"], "reply", row["id"], issue_id,
                     author["id"])
    for person in await mentioned_users(body):
        await notify(person["id"], "mention", row["id"], issue_id, author["id"])
    return row["id"]


async def comment_flood(user_id: int) -> bool:
    row = await q("""SELECT count(*) AS n FROM comments
                      WHERE author_id = %s AND created_at > now() - interval '5 minutes'""",
                  (user_id,), one=True)
    return row["n"] >= COMMENTS_PER_5_MIN


async def thread(issue_id: int, viewer: dict | None, sort: str) -> list[dict]:
    """The whole thread for one question, flattened into reading order.

    Sorting happens here rather than in SQL because 'best' means ordering each
    set of siblings, which a single ORDER BY over a materialised path cannot
    express. At a thousand comments this is microseconds; if a thread ever
    outgrows that, page the top-level comments and fetch their subtrees."""
    rows = await q(
        """SELECT c.id, c.parent_id, c.author_id, c.body, c.depth, c.created_at,
                  c.edited_at, c.ups, c.downs, c.score,
                  c.removed_at, c.removed_reason,
                  u.display_name AS author, u.username,
                  v.value AS my_vote
             FROM comments c
             JOIN users u ON u.id = c.author_id
        LEFT JOIN comment_votes v ON v.comment_id = c.id AND v.user_id = %s
            WHERE c.issue_id = %s
         ORDER BY c.path
            LIMIT %s""",
        (viewer["id"] if viewer else 0, issue_id, MAX_COMMENTS_RENDERED))

    children: dict[int | None, list] = {}
    for row in rows:
        row["removed"] = row["removed_at"] is not None
        # An author sees their own removed comment, an admin sees anyone's.
        row["may_see"] = bool(viewer and (viewer["is_admin"]
                                          or viewer["id"] == row["author_id"]))
        row["may_moderate"] = row["may_see"]
        row["indent"] = min(row["depth"], MAX_INDENT)
        children.setdefault(row["parent_id"], []).append(row)

    if sort == "new":
        key, reverse = (lambda r: r["created_at"]), True
    elif sort == "old":
        key, reverse = (lambda r: r["created_at"]), False
    else:
        # Ties by age, so an old comment is not leapfrogged by a new one on
        # the same score.
        key, reverse = (lambda r: (r["score"], -r["created_at"].timestamp())), True

    ordered: list[dict] = []

    def walk(parent_id):
        for row in sorted(children.get(parent_id, []), key=key, reverse=reverse):
            ordered.append(row)
            walk(row["id"])

    walk(None)
    return ordered


async def comment_target(request: Request, comment_id: int,
                         need_admin: bool = False):
    """(viewer, comment). 404 rather than 403 for someone with no business
    here, so the existence of the control is not advertised."""
    user = await session_user(request)
    row = await q("SELECT * FROM comments WHERE id = %s", (comment_id,), one=True)
    if not user or not row:
        raise HTTPException(404, "not found")
    if need_admin and not user["is_admin"]:
        raise HTTPException(404, "not found")
    if not user["is_admin"] and row["author_id"] != user["id"]:
        raise HTTPException(404, "not found")
    return user, row


def back_to(issue_id: int, comment_id: int | None = None, sort: str = "best"):
    anchor = f"#c{comment_id}" if comment_id else ""
    return RedirectResponse(f"/i/{issue_id}?comments={sort}{anchor}",
                            status_code=303)


@app.post("/i/{issue_id}/comment")
async def page_comment(request: Request, issue_id: int, body: str = Form(...),
                       parent_id: int = Form(0), sort: str = Form("best"),
                       csrf: str = Form("")):
    check_csrf(request, csrf)
    user = await session_user(request)
    if not user:
        return RedirectResponse(f"/login?next=/i/{issue_id}", status_code=303)
    if user["status"] != "approved":
        raise HTTPException(403, "Your account is still awaiting approval.")
    if not clean(body).strip():
        return back_to(issue_id, None, sort)
    if await comment_flood(user["id"]):
        raise HTTPException(429, "You are posting comments very fast. "
                                 "Give it a few minutes.")

    issue = await q("SELECT id FROM issues WHERE id = %s AND removed_at IS NULL",
                    (issue_id,), one=True)
    if not issue:
        raise HTTPException(404, "no such question")

    parent = None
    if parent_id:
        parent = await q("SELECT * FROM comments WHERE id = %s AND issue_id = %s",
                         (parent_id, issue_id), one=True)
        if not parent:
            raise HTTPException(404, "no such comment")
        if parent["removed_at"]:
            raise HTTPException(409, "that comment has been removed")

    new_id = await post_comment(issue_id, user, body, parent)
    return back_to(issue_id, new_id, sort)


@app.get("/c/{comment_id}/reply", response_class=HTMLResponse)
async def page_reply_form(request: Request, comment_id: int,
                          sort: str = "best"):
    """A page rather than a form under every comment: with no JavaScript, one
    textarea per comment would be a hundred textareas on a busy question."""
    user = await session_user(request)
    row = await q("""SELECT c.*, u.display_name AS author FROM comments c
                     JOIN users u ON u.id = c.author_id WHERE c.id = %s""",
                  (comment_id,), one=True)
    if not row or row["removed_at"]:
        raise HTTPException(404, "not found")
    if not user:
        return RedirectResponse(f"/login?next=/c/{comment_id}/reply",
                                status_code=303)
    issue = await q("SELECT id, title FROM issues WHERE id = %s",
                    (row["issue_id"],), one=True)
    return render(request, "comment_form.html",
                  {"user": user, "issue": issue, "parent": row,
                   "editing": None, "sort": sort, "error": None})


@app.get("/c/{comment_id}/edit", response_class=HTMLResponse)
async def page_edit_form(request: Request, comment_id: int, sort: str = "best"):
    user, row = await comment_target(request, comment_id)
    if row["author_id"] != user["id"]:
        # Admins may remove, never rewrite. Editing someone else's words under
        # their name is not moderation.
        raise HTTPException(404, "not found")
    issue = await q("SELECT id, title FROM issues WHERE id = %s",
                    (row["issue_id"],), one=True)
    return render(request, "comment_form.html",
                  {"user": user, "issue": issue, "parent": None,
                   "editing": row, "sort": sort, "error": None})


@app.post("/c/{comment_id}/edit")
async def page_edit(request: Request, comment_id: int, body: str = Form(...),
                    sort: str = Form("best"), csrf: str = Form("")):
    check_csrf(request, csrf)
    user, row = await comment_target(request, comment_id)
    if row["author_id"] != user["id"] or not clean(body).strip():
        raise HTTPException(404, "not found")
    await q("""UPDATE comments SET body = %s, edited_at = now() WHERE id = %s""",
            (clean(body)[:MAX_COMMENT], comment_id))
    # Names added by the edit still notify; the ones already there do not,
    # because the unique constraint already has a row for them.
    for person in await mentioned_users(body):
        await notify(person["id"], "mention", comment_id, row["issue_id"],
                     user["id"])
    return back_to(row["issue_id"], comment_id, sort)


@app.post("/c/{comment_id}/vote")
async def page_comment_vote(request: Request, comment_id: int,
                            value: int = Form(...), sort: str = Form("best"),
                            csrf: str = Form("")):
    """Clicking the same arrow again takes the vote back, which is what people
    expect and what stops a misclick being permanent."""
    check_csrf(request, csrf)
    user = await session_user(request)
    row = await q("SELECT issue_id, removed_at FROM comments WHERE id = %s",
                  (comment_id,), one=True)
    if not row:
        raise HTTPException(404, "not found")
    if not user:
        return RedirectResponse(f"/login?next=/i/{row['issue_id']}",
                                status_code=303)
    if user["status"] != "approved" or row["removed_at"]:
        raise HTTPException(403, "not allowed")
    if value not in (1, -1):
        raise HTTPException(422, "a vote is +1 or -1")

    changed = await q(
        """INSERT INTO comment_votes (comment_id, user_id, value)
           VALUES (%s, %s, %s)
           ON CONFLICT (comment_id, user_id) DO UPDATE
               SET value = EXCLUDED.value, at = now()
             WHERE comment_votes.value <> EXCLUDED.value
           RETURNING value""", (comment_id, user["id"], value), one=True)
    if not changed:
        # Nothing to update means the same arrow was already lit: take it back.
        await q("DELETE FROM comment_votes WHERE comment_id = %s AND user_id = %s",
                (comment_id, user["id"]))
    return back_to(row["issue_id"], comment_id, sort)


@app.post("/c/{comment_id}/remove")
async def page_comment_remove(request: Request, comment_id: int,
                              reason: str = Form(""), sort: str = Form("best"),
                              csrf: str = Form("")):
    """Soft, like a question: the row stays and the replies underneath it stay
    readable. Deleting it outright would take other people's answers with it."""
    check_csrf(request, csrf)
    user, row = await comment_target(request, comment_id)
    await q("""UPDATE comments SET removed_at = now(), removed_by = %s,
                                   removed_reason = %s
                WHERE id = %s AND removed_at IS NULL""",
            (user["id"], clean(reason)[:500], comment_id))
    await audit(user, "comment.remove", f"comment {comment_id}",
                clean(reason)[:500] or "no reason given")
    return back_to(row["issue_id"], comment_id, sort)


@app.post("/c/{comment_id}/restore")
async def page_comment_restore(request: Request, comment_id: int,
                               sort: str = Form("best"), csrf: str = Form("")):
    check_csrf(request, csrf)
    user, row = await comment_target(request, comment_id, need_admin=True)
    await q("""UPDATE comments SET removed_at = NULL, removed_by = NULL,
                                   removed_reason = '' WHERE id = %s""",
            (comment_id,))
    await audit(user, "comment.restore", f"comment {comment_id}")
    return back_to(row["issue_id"], comment_id, sort)


@app.post("/c/{comment_id}/purge")
async def page_comment_purge(request: Request, comment_id: int,
                             sort: str = Form("best"), csrf: str = Form("")):
    """Really deletes, and takes every reply under it. For content that must
    not stay on disk, and for erasure requests -- not for tidying up, which is
    what remove is for. The audit row goes in first, because afterwards there
    is nothing to point at."""
    check_csrf(request, csrf)
    user, row = await comment_target(request, comment_id, need_admin=True)
    await audit(user, "comment.purge", f"comment {comment_id}",
                row["body"][:200])
    await q("DELETE FROM comments WHERE id = %s", (comment_id,))
    return back_to(row["issue_id"], None, sort)


# --- the inbox ---------------------------------------------------------------

@app.get("/inbox", response_class=HTMLResponse)
async def page_inbox(request: Request):
    user = await session_user(request)
    if not user:
        return RedirectResponse("/login?next=/inbox", status_code=303)
    items = await q(
        """SELECT n.id, n.kind, n.created_at, n.read_at, n.comment_id,
                  n.issue_id, i.title, a.display_name AS actor,
                  left(c.body, 300) AS excerpt, c.removed_at
             FROM notifications n
             JOIN comments c ON c.id = n.comment_id
             JOIN issues   i ON i.id = n.issue_id
        LEFT JOIN users    a ON a.id = n.actor_id
            WHERE n.user_id = %s
         ORDER BY n.created_at DESC LIMIT 100""", (user["id"],))
    return render(request, "inbox.html", {"user": user, "items": items})


@app.post("/inbox/read")
async def page_inbox_read(request: Request, csrf: str = Form("")):
    check_csrf(request, csrf)
    user = await session_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    await q("UPDATE notifications SET read_at = now() "
            "WHERE user_id = %s AND read_at IS NULL", (user["id"],))
    return RedirectResponse("/inbox", status_code=303)


# --- forgotten passwords -----------------------------------------------------
# Short-lived, single use, and stored only as a hash: the same three properties
# as every other token here, for the same reason -- a reset link is a password
# for as long as it is valid, so the database must not contain a working one.

RESET_TTL_MINUTES = int(os.environ.get("RESET_TTL_MINUTES", "60"))
RESETS_PER_IP = 5


async def issue_password_reset(user: dict) -> str:
    token = secrets.token_urlsafe(32)
    await q("""INSERT INTO password_resets (token_hash, user_id, expires_at)
               VALUES (%s, %s, now() + %s::interval)""",
            (_hash(token), user["id"], f"{RESET_TTL_MINUTES} minutes"))
    link = f"{SITE_URL}/reset/{token}"

    if SMTP_HOST:
        send_mail(user["email"], "Reset your password",
                  f"Someone asked to reset the password for {user['username']}.\n\n"
                  f"{link}\n\nThe link works once and expires in "
                  f"{RESET_TTL_MINUTES} minutes. If this was not you, ignore "
                  f"this message -- nothing has changed.\n")
    else:
        # No mail server configured: the link goes to the server log and
        # nowhere else. Deliberately not shown on the admin page the way a
        # verification link is -- an admin who can read reset links is an admin
        # who can take over any account on the site.
        print(f"[password reset] {user['username']}: {link}", flush=True)
    return link


@app.get("/forgot", response_class=HTMLResponse)
async def page_forgot(request: Request):
    return render(request, "forgot.html", {"user": None, "sent": False,
                                           "error": None})


@app.post("/forgot", response_class=HTMLResponse)
async def page_forgot_submit(request: Request, email: str = Form(...),
                             csrf: str = Form("")):
    check_csrf(request, csrf)

    key = await _subject_hash("reset", client_ip(request))
    recent = await q("SELECT count(*) AS n FROM login_throttle "
                     "WHERE subject = %s AND at > now() - %s::interval",
                     (key, LOGIN_WINDOW), one=True)
    if recent["n"] >= RESETS_PER_IP:
        return render(request, "forgot.html", {
            "user": None, "sent": False,
            "error": "Too many reset requests from here. Try again later."},
            status_code=429)
    await q("INSERT INTO login_throttle (subject) VALUES (%s)", (key,))

    user = await q("SELECT id, email, username FROM users "
                   "WHERE email_canonical = %s", (canonical_email(email),),
                   one=True)
    if user:
        await issue_password_reset(user)
        await audit(user, "password.reset.request", user["username"])

    # The same page either way. "No account with that address" turns this form
    # into a way to find out who has an account here.
    return render(request, "forgot.html", {"user": None, "sent": True,
                                           "error": None})


async def valid_reset(token: str):
    return await q(
        """SELECT r.token_hash, u.id, u.username FROM password_resets r
             JOIN users u ON u.id = r.user_id
            WHERE r.token_hash = %s AND r.used_at IS NULL
              AND r.expires_at > now()""", (_hash(token),), one=True)


@app.get("/reset/{token}", response_class=HTMLResponse)
async def page_reset(request: Request, token: str):
    row = await valid_reset(token)
    return render(request, "reset.html",
                  {"user": None, "token": token, "ok": bool(row), "error": None},
                  status_code=200 if row else 410)


@app.post("/reset/{token}", response_class=HTMLResponse)
async def page_reset_submit(request: Request, token: str,
                            password: str = Form(...),
                            password2: str = Form(""),
                            csrf: str = Form("")):
    check_csrf(request, csrf)
    row = await valid_reset(token)
    if not row:
        return render(request, "reset.html",
                      {"user": None, "token": token, "ok": False, "error": None},
                      status_code=410)

    if password != password2:
        return render(request, "reset.html", {
            "user": None, "token": token, "ok": True,
            "error": "The two passwords do not match."}, status_code=422)
    if len(password) < 10:
        return render(request, "reset.html", {
            "user": None, "token": token, "ok": True,
            "error": "Password must be at least 10 characters."}, status_code=422)

    async with pool.connection() as conn:
        async with conn.transaction():
            await conn.execute("UPDATE users SET password_hash = %s WHERE id = %s",
                               (auth.hash_password(password), row["id"]))
            await conn.execute("UPDATE password_resets SET used_at = now() "
                               "WHERE token_hash = %s", (row["token_hash"],))
            # Any other outstanding link for this account dies with it, and so
            # does every live session: if the reason for the reset was that
            # somebody else had the password, leaving their session logged in
            # would make the reset pointless.
            await conn.execute("UPDATE password_resets SET used_at = now() "
                               "WHERE user_id = %s AND used_at IS NULL",
                               (row["id"],))
            await conn.execute("DELETE FROM sessions WHERE user_id = %s",
                               (row["id"],))
            # A password that worked is also evidence the account exists, so
            # clear the failure counters that a guessing run may have piled up.
            await conn.execute(
                "DELETE FROM login_throttle WHERE subject = %s",
                (await _subject_hash("user", row["username"]),))

    await audit(row, "password.reset.complete", row["username"])
    return RedirectResponse("/login?reset=1", status_code=303)


# --- admin -------------------------------------------------------------------

async def require_web_admin(request: Request):
    """Returns the admin account, or None. A non-admin gets 404 rather than
    403 from the caller, so the page's existence is not confirmed to someone
    who has no business there."""
    user = await session_user(request)
    return user if user and user["is_admin"] else None


async def _admin_page(request: Request, admin: dict, done: str | None = None,
                      fresh_admin_token: str = ""):
    pending = await q(
        "SELECT id, username, email, note, created_at, email_verified "
        "FROM users WHERE status = 'pending' ORDER BY created_at")
    members = await q(
        """SELECT u.id, u.username, u.email, u.status, u.is_admin,
                  u.email_verified, a.name AS agent, a.model_name,
                  (SELECT count(*) FROM votes v WHERE v.agent_id = a.id) AS ballots
             FROM users u LEFT JOIN agents a ON a.user_id = u.id
            WHERE u.status <> 'pending' ORDER BY u.id""")

    prompt = await q("SELECT version, body, created_at FROM agent_prompts "
                     "ORDER BY version DESC LIMIT 1", one=True)
    usage = await q(
        """SELECT coalesce(prompt_version::text, 'their own') AS v, count(*) AS n
             FROM votes GROUP BY 1 ORDER BY n DESC LIMIT 6""")
    log = await q("SELECT at, actor, action, target, detail FROM audit_log "
                  "ORDER BY at DESC LIMIT 40")

    return render(request, "admin.html", {
        "user": admin, "pending": pending, "members": members, "done": done,
        "prompt": prompt, "usage": usage, "log": log,
        "fresh_admin_token": fresh_admin_token,
        "env_token_live": not await config_get("admin_token_hash"),
        "smtp": bool(SMTP_HOST), "site_url": SITE_URL})


@app.get("/admin", response_class=HTMLResponse)
async def page_admin(request: Request, done: str | None = None):
    admin = await require_web_admin(request)
    if not admin:
        raise HTTPException(404, "not found")
    return await _admin_page(request, admin, done)


@app.post("/admin/verify-link", response_class=HTMLResponse)
async def page_verify_link(request: Request, user_id: int = Form(...),
                           csrf: str = Form("")):
    """Re-issue a verification link. With SMTP configured it is emailed; with
    no mail server it is shown here to be passed on by hand."""
    check_csrf(request, csrf)
    admin = await require_web_admin(request)
    if not admin:
        raise HTTPException(404, "not found")
    link = await issue_verification(user_id)
    await audit(admin, "email.verification.reissue", f"user {user_id}")
    return await _admin_page(request, admin, done="verify:" + link)


@app.post("/admin/approve")
async def page_admin_approve(request: Request, user_id: int = Form(...),
                             agent_name: str = Form(""),
                             csrf: str = Form("")):
    check_csrf(request, csrf)
    if not await require_web_admin(request):
        raise HTTPException(404, "not found")
    admin = await session_user(request)
    ok = await approve_user(user_id, agent_name)
    if ok:
        await audit(admin, "account.approve", f"user {user_id}", agent_name)
    return RedirectResponse(
        f"/admin?done={'approved' if ok else 'nothing-to-do'}", status_code=303)


@app.post("/admin/prompt")
async def page_admin_prompt(request: Request, body: str = Form(...),
                            csrf: str = Form("")):
    check_csrf(request, csrf)
    admin = await require_web_admin(request)
    if not admin:
        raise HTTPException(404, "not found")
    # Append-only. Old ballots keep pointing at the version that produced them,
    # so the record of what was asked stays true after the prompt changes.
    row = await q("INSERT INTO agent_prompts (body, author_id) VALUES (%s, %s) "
                  "RETURNING version", (body.strip(), admin["id"]), one=True)
    await audit(admin, "prompt.publish", f"version {row['version']}")
    return RedirectResponse("/admin?done=prompt", status_code=303)


@app.post("/admin/reject")
async def page_admin_reject(request: Request, user_id: int = Form(...),
                            csrf: str = Form("")):
    check_csrf(request, csrf)
    if not await require_web_admin(request):
        raise HTTPException(404, "not found")
    # Rejected, not deleted: the row is the record of having decided, and the
    # email stays taken so the same person cannot quietly reapply.
    await q("UPDATE users SET status = 'rejected' WHERE id = %s AND status = 'pending'",
            (user_id,))
    await audit(await session_user(request), "account.reject", f"user {user_id}")
    return RedirectResponse("/admin?done=rejected", status_code=303)


@app.post("/admin/admin-token", response_class=HTMLResponse)
async def page_rotate_admin_token(request: Request, csrf: str = Form("")):
    """Rotate the API root credential. Shown once; only a hash is kept. The
    first rotation also permanently retires the ADMIN_TOKEN from .env."""
    check_csrf(request, csrf)
    admin = await require_web_admin(request)
    if not admin:
        raise HTTPException(404, "not found")
    token = secrets.token_urlsafe(32)
    await config_set("admin_token_hash", _hash(token))
    await audit(admin, "admin_token.rotate")
    return await _admin_page(request, admin, fresh_admin_token=token)


# --- account -----------------------------------------------------------------

async def _account_page(request: Request, user: dict, fresh_token: str = ""):
    agent = await q("SELECT * FROM agents WHERE user_id = %s", (user["id"],), one=True)
    voted = await q("SELECT count(*) AS n FROM votes WHERE agent_id = %s",
                    (agent["id"],), one=True) if agent else {"n": 0}
    return render(request, "account.html", {
        "user": user, "agent": agent, "voted": voted["n"],
        "fresh_token": fresh_token})


@app.get("/account", response_class=HTMLResponse)
async def page_account(request: Request):
    user = await session_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    return await _account_page(request, user)


@app.post("/account/token", response_class=HTMLResponse)
async def page_rotate_token(request: Request,
                            csrf: str = Form("")):
    """Rotate the agent token. The old one stops working immediately, so any
    client still holding it starts getting 401s until it is updated."""
    check_csrf(request, csrf)
    user = await session_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    agent = await q("SELECT id FROM agents WHERE user_id = %s", (user["id"],), one=True)
    if not agent:
        return RedirectResponse("/account", status_code=303)

    token = secrets.token_urlsafe(32)
    # Only the hash is kept. The plaintext exists for exactly one page render
    # and is then unrecoverable -- which is affordable precisely because
    # generating another is one click away.
    await q("UPDATE agents SET token_hash = %s WHERE id = %s",
            (_hash(token), agent["id"]))
    await audit(user, "agent.token.rotate", f"agent {agent['id']}")
    return await _account_page(request, user, fresh_token=token)
