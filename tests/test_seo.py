"""What search engines and link unfurlers see."""

import json
import re

import pytest

from helpers import approved, issue_id, post_issue

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_robots_txt_points_to_the_sitemap_and_keeps_private_pages_out(client):
    r = await client.get("/robots.txt")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    assert "Sitemap: http://dd.test/sitemap.xml" in r.text
    for path in ("/admin", "/account", "/inbox", "/login", "/agent", "/issues"):
        assert f"Disallow: {path}" in r.text
    assert "Disallow: /essay" not in r.text and "Disallow: /f/" not in r.text


async def test_private_and_machine_pages_say_noindex(client):
    for path in ("/login", "/register", "/account", "/inbox", "/admin", "/agent/forums",
                 "/issues/open", "/healthz"):
        r = await client.get(path)
        assert "noindex" in r.headers.get("x-robots-tag", ""), path
    for path in ("/", "/essay", "/how-to", "/forums", "/observatory"):
        assert "x-robots-tag" not in (await client.get(path)).headers, path


async def test_the_sitemap_lists_public_pages_and_real_issues_only(client):
    await approved(client, "Tengil")
    await post_issue(client, title="Should the sitemap list this?")
    await post_issue(client, title="Should this one be hidden?")
    gone = await issue_id("Should this one be hidden?")
    from app import main as dd
    await dd.q("UPDATE issues SET removed_at = now() WHERE id = %s", (gone,))
    shown = await issue_id("Should the sitemap list this?")
    r = await client.get("/sitemap.xml")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/xml")
    assert r.text.startswith('<?xml version="1.0"')
    for path in ("/", "/essay", "/how-to", "/forums", "/f/china", f"/i/{shown}"):
        assert f"<loc>http://dd.test{path}</loc>" in r.text, path
    assert f"/i/{gone}<" not in r.text
    assert "/admin" not in r.text and "/observatory/new" not in r.text
    assert re.search(r"<lastmod>\d{4}-\d\d-\d\d</lastmod>", r.text)


async def test_every_page_names_its_canonical_address(client):
    r = await client.get("/how-to?utm_source=x")
    assert '<link rel="canonical" href="http://dd.test/how-to">' in r.text


async def test_an_issue_shares_with_its_own_title_and_words(client):
    await approved(client, "Tengil")
    await post_issue(client, title="Should the link preview say this?",
                     body="A few words of context for the preview.")
    iid = await issue_id("Should the link preview say this?")
    page = (await client.get(f"/i/{iid}")).text
    assert 'property="og:title" content="Should the link preview say this?"' in page
    assert 'content="A few words of context for the preview."' in page
    assert 'property="og:type" content="article"' in page
    assert f'property="og:url" content="http://dd.test/i/{iid}"' in page


async def test_structured_data_is_valid_json_for_the_site_and_the_essay(client):
    for path, kind in (("/", "WebSite"), ("/essay", "Article")):
        html = (await client.get(path)).text
        blob = re.search(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)
        data = json.loads(blob.group(1))
        assert data["@type"] == kind and data["@context"] == "https://schema.org"
    assert "ld+json" not in (await client.get("/f/china")).text
