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
    assert "<script" not in text.lower()
    assert not re.search(r'\son(click|load|error|submit|focus)\s*=', text, re.I)


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda p: p.name)
def test_no_template_disables_escaping(template):
    """One `| safe` on a field a user controls is all it takes."""
    text = template.read_text()
    assert "|safe" not in text.replace(" ", "")
    assert "autoescape false" not in text
