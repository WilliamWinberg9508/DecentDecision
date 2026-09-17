"""Passwords and sessions.

Passwords are hashed with scrypt from the standard library. scrypt is a real
password KDF -- deliberately slow and memory-hard, so a stolen database is
expensive to attack offline -- and it needs no third-party dependency, which
keeps the deploy story to "docker compose up".

Every password gets its own 16-byte random salt, stored alongside the hash.
The stored string carries its own parameters:

    scrypt$16384$8$1$<salt hex>$<hash hex>

so raising the cost later does not invalidate existing passwords: old hashes
keep verifying with the parameters they were written with, and get rewritten
at the next successful login.

If you ever want argon2id instead, add `argon2-cffi` and write a second
verify branch keyed on the prefix. The format above is designed for that.
"""

import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone

# ~16 MB and roughly 50-100 ms per hash on a desktop CPU. High enough to make
# bulk offline cracking painful, low enough that a login is not noticeable.
N, R, P = 16384, 8, 1
MAXMEM = 64 * 1024 * 1024
SALT_BYTES = 16
SESSION_DAYS = 30


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(SALT_BYTES)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=N, r=R, p=P,
                        maxmem=MAXMEM, dklen=32)
    return f"scrypt${N}${R}${P}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str | None) -> bool:
    """Constant-time check. Returns False for a missing or malformed hash
    rather than raising, so a user row with no password behaves like a wrong
    password instead of a 500."""
    if not stored:
        return False
    try:
        scheme, n, r, p, salt_hex, hash_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        dk = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex),
                            n=int(n), r=int(r), p=int(p),
                            maxmem=MAXMEM, dklen=len(hash_hex) // 2)
    except (ValueError, TypeError):
        return False
    # compare_digest, not ==, so a wrong password cannot be found byte by byte
    # from how long the comparison took.
    return hmac.compare_digest(dk.hex(), hash_hex)


def needs_rehash(stored: str | None) -> bool:
    """True when a hash was written with weaker parameters than we now use."""
    if not stored:
        return False
    try:
        scheme, n, r, p, _, _ = stored.split("$")
    except ValueError:
        return True
    return scheme != "scrypt" or (int(n), int(r), int(p)) != (N, R, P)


# --- sessions ----------------------------------------------------------------

COOKIE = "dd_session"
# Set COOKIE_SECURE=true once the site is served over HTTPS. It must stay false
# on plain http://localhost, because a Secure cookie is simply never sent there
# and every login would appear to succeed and then do nothing.
SECURE = os.environ.get("COOKIE_SECURE", "false").lower() == "true"


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    """Sessions are stored hashed for the same reason passwords are: read
    access to the table should not be enough to impersonate anyone."""
    return hashlib.sha256(token.encode()).hexdigest()


def expiry() -> datetime:
    return datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)


# --- CSRF --------------------------------------------------------------------
# Double-submit: the same random value goes in a cookie and in a hidden form
# field, and the server checks they match. A site on another origin can make
# your browser send the cookie, but it cannot read it, so it cannot put the
# matching value in the form. This works for anonymous forms (login, register)
# as well as logged-in ones, which a session-bound token would not.

CSRF_COOKIE = "dd_csrf"
CSRF_FIELD = "csrf"


def new_csrf() -> str:
    return secrets.token_urlsafe(24)


def csrf_ok(cookie_value: str | None, form_value: str | None) -> bool:
    if not cookie_value or not form_value:
        return False
    return hmac.compare_digest(cookie_value, form_value)


def set_csrf_cookie(response, value: str) -> None:
    response.set_cookie(
        CSRF_COOKIE, value,
        max_age=SESSION_DAYS * 24 * 3600,
        httponly=True,      # the server writes it into the form; JS never needs it
        samesite="lax",
        secure=SECURE,
        path="/",
    )


def set_cookie(response, token: str) -> None:
    response.set_cookie(
        COOKIE, token,
        max_age=SESSION_DAYS * 24 * 3600,
        httponly=True,      # JavaScript cannot read it, so an XSS cannot steal it
        samesite="lax",     # not sent on cross-site POSTs: basic CSRF cover
        secure=SECURE,
        path="/",
    )


def clear_cookie(response) -> None:
    response.delete_cookie(COOKIE, path="/")
