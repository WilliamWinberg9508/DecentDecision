"""Password hashing, session tokens, CSRF. No database, no HTTP."""

import pytest

from app import auth
from app.main import canonical_email, clean, quadrant


def test_same_password_hashes_differently_every_time():
    """Per-user salt. Identical hashes for identical passwords would let a
    stolen table be cracked once and read for everyone."""
    a, b = auth.hash_password("correct horse"), auth.hash_password("correct horse")
    assert a != b
    assert auth.verify_password("correct horse", a)
    assert auth.verify_password("correct horse", b)


def test_wrong_password_is_rejected():
    stored = auth.hash_password("correct horse")
    assert not auth.verify_password("correct hors", stored)
    assert not auth.verify_password("", stored)


def test_the_hash_carries_its_own_parameters():
    scheme, n, r, p, salt, digest = auth.hash_password("x").split("$")
    assert (scheme, int(n), int(r), int(p)) == ("scrypt", auth.N, auth.R, auth.P)
    assert len(bytes.fromhex(salt)) == auth.SALT_BYTES
    assert len(bytes.fromhex(digest)) == 32


@pytest.mark.parametrize("stored", [None, "", "not-a-hash", "scrypt$broken",
                                    "bcrypt$16384$8$1$aa$bb", "scrypt$a$b$c$d$e"])
def test_a_malformed_hash_is_a_wrong_password_not_a_crash(stored):
    """A user row with a damaged hash must behave like a bad password. If it
    raised, that row would return 500 and announce itself as interesting."""
    assert auth.verify_password("anything", stored) is False


def test_rehash_is_requested_only_for_weaker_parameters():
    assert not auth.needs_rehash(auth.hash_password("x"))
    assert auth.needs_rehash("scrypt$1024$8$1$aa$bb")     # cheaper than current
    assert auth.needs_rehash("md5$whatever")
    assert not auth.needs_rehash(None)                    # nothing to upgrade


def test_session_tokens_are_long_random_and_stored_hashed():
    a, b = auth.new_session_token(), auth.new_session_token()
    assert a != b and len(a) >= 32
    assert auth.token_hash(a) != a
    assert auth.token_hash(a) == auth.token_hash(a)


def test_csrf_needs_both_halves_to_match():
    token = auth.new_csrf()
    assert auth.csrf_ok(token, token)
    assert not auth.csrf_ok(token, token + "x")
    assert not auth.csrf_ok(None, token)      # no cookie
    assert not auth.csrf_ok(token, None)      # no form field
    assert not auth.csrf_ok("", "")           # both empty must not pass


@pytest.mark.parametrize("written,folded", [
    ("me@gmail.com", "me@gmail.com"),
    ("m.e@gmail.com", "me@gmail.com"),
    ("me+vote7@gmail.com", "me@gmail.com"),
    ("M.E+x@GoogleMail.com", "me@gmail.com"),   # the other spelling of the same inbox
    ("me+tag@fastmail.com", "me@fastmail.com"),  # plus-tags fold everywhere
    ("m.e@fastmail.com", "m.e@fastmail.com"),   # dots do NOT, outside Gmail
])
def test_one_mailbox_folds_to_one_key(written, folded):
    """This is the whole anti-stuffing argument: one verified mailbox is one
    agent, so every spelling of a mailbox has to collapse to one row."""
    assert canonical_email(written) == folded


def test_control_characters_are_stripped():
    """An agent's rationale ends up in terminals and logs; escape sequences
    do not belong in it."""
    assert clean("hi\x1b[31mred\x00") == "hi[31mred"
    assert clean("  keeps\ninner newlines  ") == "keeps\ninner newlines"


@pytest.mark.parametrize("good,bad,name", [
    (True, False, "supported"), (True, True, "contested"),
    (False, True, "opposed"), (False, False, "irrelevant"),
])
def test_the_two_booleans_map_to_four_quadrants(good, bad, name):
    assert quadrant(good, bad) == name
