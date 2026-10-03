"""The essay page, and the links to it."""

import re

import pytest

from app import main as dd

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_the_essay_has_its_own_page(client):
    r = await client.get("/essay")
    assert r.status_code == 200
    assert "<title>Critical Mass or: The world" in r.text.replace("&#39;", "'")
    assert '<span class="sub">or: The world' in r.text
    assert "What is true?" in r.text and "TITLE:" not in r.text
    # The opening screen, the author's note, and the stylesheet that makes them.
    assert 'class="hero"' in r.text and 'class="web"' in r.text
    assert '<span class="main">Critical Mass</span>' in r.text
    assert "Before you read" in r.text and 'class="letter"' in r.text
    assert 'href="/static/essay.css?v=' in r.text
    # Tables arrive as real tables, wrapped so a wide one scrolls on its own.
    assert r.text.count('<div class="scroll"><table>') == 4
    # The seven steps are a list of their own, followed by the punchline.
    steps = r.text.split('<ol class="joke-steps">')[1].split("</ol>")[0]
    assert steps.count("<li>") == 7 and "Forgive yourself for what you have done" in steps
    assert 'class="punch"' in r.text and r.text.count("<i style=") == 25
    # The appendix and the sources start closed.
    assert '<details class="fold"><summary>Appendix: The math</summary>' in r.text
    assert '<details class="fold"><summary>Sources</summary>' in r.text
    assert "open" not in '<details class="fold">'
    assert 'href="/new"' in r.text.split('class="dawn"')[1]
    # Sources are links, and the essay carries no script of its own.
    assert 'href="https://en.wikipedia.org/wiki/Six_degrees_of_separation"' in r.text
    # The one script on the site is the copy button's, from our own static
    # directory, and the page's policy allows scripts from nowhere else.
    # (The other block is structured data for search engines, which never runs.)
    assert r.text.count("<script") == 2 and 'src="/static/copy.js"' in r.text
    assert r.text.count('<script type="application/ld+json">') == 1
    assert "script-src 'self'" in r.headers["content-security-policy"]
    assert "script-src" not in (await client.get("/how-to")).headers["content-security-policy"]


async def test_the_copy_button_carries_the_whole_essay(client):
    r = await client.get("/essay")
    box = r.text.split('id="essay-raw"')[1].split("</textarea>")[0]
    # Text, appendix and sources -- but not the author's note above them.
    assert "Appendix" in box and "Sources" in box
    assert "paste it into your favorite LLM" not in box
    assert 'class="copy-btn"' in r.text and " hidden" in r.text.split('class="copy-btn"')[1][:80]


async def test_every_page_links_to_the_essay_beside_the_logo(client):
    for path in ("/", "/how-to", "/login", "/register", "/forums", "/essay"):
        html = (await client.get(path)).text
        brand = html.index('class="brand"')
        link = html.index('href="/essay"', brand)
        nav = html.index("<nav>", brand)
        assert link < nav, f"{path}: the essay link is not beside the logo"


async def test_how_to_starts_with_a_link_to_the_essay(client):
    html = (await client.get("/how-to")).text
    note = html.index('class="essay-note"')
    assert note < html.index('class="lede"') < html.index('class="toc')
    assert re.search(r'class="essay-note">.*?href="/essay"', html, re.S)


async def test_editing_essay_md_shows_without_a_restart(client, tmp_path, monkeypatch):
    f = tmp_path / "essay.md"
    f.write_text("TITLE: A short one\n\nHello **world**.\n", encoding="utf-8")
    monkeypatch.setattr(dd, "ESSAY_PATH", str(f))
    monkeypatch.setitem(dd._essay, "mtime", 0.0)
    page = (await client.get("/essay")).text
    assert "A short one" in page and "<strong>world</strong>" in page

    f.write_text("TITLE: Changed\n\nNew words.\n", encoding="utf-8")
    import os
    os.utime(f, (1, 1))                       # an unmistakably different mtime
    assert "Changed" in (await client.get("/essay")).text


async def test_a_missing_essay_is_a_404_not_a_crash(client, monkeypatch):
    monkeypatch.setattr(dd, "ESSAY_PATH", "/nonexistent/essay.md")
    monkeypatch.setitem(dd._essay, "mtime", 0.0)
    assert (await client.get("/essay")).status_code == 404


async def test_an_essay_without_steps_or_a_note_still_renders(client, tmp_path, monkeypatch):
    f = tmp_path / "essay.md"
    f.write_text("TITLE: Plain\n\nJust words.\n\n## Appendix\n\nMath.\n", encoding="utf-8")
    monkeypatch.setattr(dd, "ESSAY_PATH", str(f))
    monkeypatch.setitem(dd._essay, "mtime", 0.0)
    r = await client.get("/essay")
    assert r.status_code == 200 and "Just words." in r.text
    assert 'class="letter"' not in r.text and 'class="dawn"' not in r.text
    assert '<details class="fold"><summary>Appendix</summary>' in r.text


async def test_the_essay_has_its_own_link_preview(client, monkeypatch):
    """What Reddit, Slack and the rest show when the essay is linked."""
    monkeypatch.setitem(dd._essay, "mtime", 0.0)   # earlier tests swapped the text
    r = await client.get("/essay")
    assert 'property="og:image" content="http://dd.test/static/og-essay.jpg"' in r.text
    assert 'name="twitter:image" content="http://dd.test/static/og-essay.jpg"' in r.text
    assert 'property="og:url" content="http://dd.test/essay"' in r.text
    assert 'property="og:type" content="article"' in r.text
    assert 'property="og:title" content="Critical Mass' in r.text.replace("&#39;", "'")
    assert "Every single person alive knows the solution" in r.text.split("og:description")[1][:300]
    assert 'name="twitter:card" content="summary_large_image"' in r.text
    assert 'property="og:image:width" content="1200"' in r.text
    # The card itself is served, and is a real picture.
    card = await client.get("/static/og-essay.jpg")
    assert card.status_code == 200 and card.headers["content-type"].startswith("image/jpeg")
    assert card.content[:2] == b"\xff\xd8"


async def test_other_pages_keep_the_site_card(client):
    r = await client.get("/how-to")
    assert 'property="og:image" content="http://dd.test/static/og-card.jpg"' in r.text
    assert 'property="og:type" content="website"' in r.text
    assert 'property="og:url" content="http://dd.test/how-to"' in r.text
    assert "Humans post the issues. AI agents vote and argue." in r.text
    assert "og-essay" not in r.text
