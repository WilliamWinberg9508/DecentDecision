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
    assert "<script" not in r.text


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
