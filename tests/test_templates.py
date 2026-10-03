"""Static checks on the templates themselves. No database, no HTTP."""

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
TEMPLATES = sorted((ROOT / "app" / "templates").glob("*.html"))


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda p: p.name)
def test_no_template_ships_javascript(template):
    """If this ever fails, the CSP above has to change, and the argument that
    XSS has nowhere to run goes with it."""
    text = template.read_text()
    # The one exception on the whole site: the essay page's copy button, a
    # script from our own static directory (see test_essay.py for its policy).
    text = text.replace('<script src="/static/copy.js" defer></script>', "")
    text = re.sub(r'<script type="application/ld\+json">.*?</script>', "", text, flags=re.S)
    assert "<script" not in text.lower()
    assert not re.search(r'\son(click|load|error|submit|focus)\s*=', text, re.I)


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda p: p.name)
def test_no_template_disables_escaping(template):
    """One `| safe` on a field a user controls is all it takes."""
    text = template.read_text()
    assert "|safe" not in text.replace(" ", "")
    assert "autoescape false" not in text


def test_the_discussion_window_is_closed_until_it_is_opened():
    """The dialog is CSS-only. If these rules go, the thread stops being a
    window and becomes a very long section of the issue page that everyone
    sees whether they asked for it or not."""
    css = (ROOT / "app" / "static" / "style.css").read_text()
    assert ".overlay { display: none; }" in css
    assert ".overlay:target," in css
    # The rule that lets a notification link open the window on one comment.
    assert ".overlay:has(:target)" in css
