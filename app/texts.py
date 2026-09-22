"""Every word the site shows, read from texts.toml.

    t("issue.discuss")                     -> the text, as safe HTML
    t("issue.of_total", n=3, total=5)      -> "{n} of {total}" filled in
    tn("common.ballots", 5)                -> picks ballots_one / ballots_other
    msg("errors.not_found")                -> plain text, for API errors and email

The texts are the owner's own words, so they may carry simple HTML (<strong>,
<a href>, <br>). Values filled into them are escaped, so a username with a <
in it stays text. The file is re-read when it changes, so an edit shows up on
the next page load -- no rebuild. A file that no longer parses keeps the old
texts in place and says why in the log, rather than taking the site down.
"""

import logging
import os
import tomllib

from markupsafe import Markup

PATH = os.environ.get("TEXTS_FILE") or os.path.join(
    os.path.dirname(__file__), "..", "texts.toml")
log = logging.getLogger("texts")

TEXTS: dict[str, str] = {}
TABLES: dict[str, list[dict]] = {}      # [[section.name]] blocks: rows of a table
_mtime = 0.0


def _flatten(d: dict, prefix: str = "", tables: dict | None = None) -> dict[str, str]:
    out = {}
    for k, v in d.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(_flatten(v, key + ".", tables))
        elif isinstance(v, list) and tables is not None:
            tables[key] = [{c: str(x).strip() for c, x in row.items()} for row in v]
        else:
            out[key] = str(v).strip()
    return out


def load(strict: bool = True) -> None:
    global TEXTS, _mtime
    try:
        mtime = os.stat(PATH).st_mtime
        with open(PATH, "rb") as fh:
            tables: dict = {}
            TEXTS = _flatten(tomllib.load(fh), "", tables)
            TABLES.clear()
            TABLES.update(tables)
        _mtime = mtime
    except Exception as exc:
        if strict:
            raise
        log.error("texts.toml not reloaded, keeping the previous texts: %s", exc)
        _mtime = os.stat(PATH).st_mtime       # do not retry until it changes again


def maybe_reload() -> None:
    try:
        if os.stat(PATH).st_mtime != _mtime:
            load(strict=False)
    except OSError:
        pass


def _raw(key: str) -> str:
    try:
        return TEXTS[key]
    except KeyError:
        raise KeyError(f"no text {key!r} in texts.toml") from None


def t(key: str, **values) -> Markup:
    text = Markup(_raw(key))
    return text.format(**values) if values else text


def tn(key: str, count: int, **values) -> Markup:
    """The singular or plural form: key_one when count is 1, else key_other."""
    return t(f"{key}_{'one' if count == 1 else 'other'}", count=count, **values)


def rows(key: str) -> list[dict]:
    """The rows of a table written as [[section.key]] blocks, each cell as
    safe HTML, for tables the owner should be able to edit (e.g. the model
    recommendations on /how-to)."""
    if key not in TABLES:
        raise KeyError(f"no table {key!r} in texts.toml")
    return [{c: Markup(v) for c, v in row.items()} for row in TABLES[key]]


def msg(key: str, **values) -> str:
    """Plain text: for JSON error bodies and email, where HTML means nothing."""
    text = _raw(key)
    return text.format(**values) if values else text


load()
