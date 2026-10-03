"""An issue is text, a link, or both."""

import pytest

from app import main as dd
from helpers import approved, auth_header, csrf, issue_id

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def post(client, **fields):
    data = {"title": "Should this link be judged?", "days_open": 7, "forum": "world",
            "csrf": await csrf(client, "/new")} | fields
    return await client.post("/new", data=data)


async def test_a_link_alone_is_enough(client, sql):
    await approved(client, "Tengil")
    r = await post(client, body="", url="https://example.com/some/article?id=3")
    assert r.status_code == 303
    row = await sql("SELECT body, url FROM issues", one=True)
    assert row["body"] == "" and row["url"] == "https://example.com/some/article?id=3"
    page = (await client.get(r.headers["location"])).text
    assert 'href="https://example.com/some/article?id=3"' in page
    assert 'rel="nofollow ugc noopener noreferrer"' in page and "example.com" in page


async def test_text_alone_still_works_and_neither_is_refused(client, sql):
    await approved(client, "Tengil")
    assert (await post(client, body="Just words.", url="")).status_code == 303
    r = await post(client, title="Should nothing be enough?", body="", url="")
    assert r.status_code == 422 and "text, a link, or both" in r.text


@pytest.mark.parametrize("bad", ["javascript:alert(1)", "ftp://example.com/x",
                                 "not a link", "https://", "http://exa mple.com",
                                 "data:text/html,<b>x</b>"])
async def test_only_web_addresses_are_accepted(client, bad):
    await approved(client, "Tengil")
    r = await post(client, body="x", url=bad)
    assert r.status_code == 422 and "does not look right" in r.text


async def test_the_agents_get_the_link_in_the_issue(client, sql):
    uid, token = await approved(client, "Tengil")
    await post(client, body="Context here.", url="https://example.com/a")
    issue = (await client.get("/agent/issues", headers=auth_header(token))).json()[0]
    assert issue["url"] == "https://example.com/a"
    assert issue["prompt"].endswith("Link: https://example.com/a\n</proposal>\n\nAnswer the question above.")
    assert issue["body"] == "Context here."
    listing = (await client.get("/issues/open")).json()[0]
    assert listing["url"] == "https://example.com/a"


async def test_the_api_takes_a_link_too(client, sql):
    uid, _ = await approved(client, "Tengil")
    await sql("UPDATE users SET token_hash = %s WHERE id = %s", (dd._hash("utok"), uid))
    h = auth_header("utok")
    r = await client.post("/issues", headers=h, json={
        "title": "Should the API accept a link?", "forum": "world",
        "url": "https://example.org/x"})
    assert r.status_code == 201 and r.json()["url"] == "https://example.org/x"
    r = await client.post("/issues", headers=h, json={
        "title": "Should the API accept nothing?", "forum": "world"})
    assert r.status_code == 422
    r = await client.post("/issues", headers=h, json={
        "title": "Should the API accept a bad link?", "forum": "world",
        "url": "javascript:alert(1)"})
    assert r.status_code == 422


async def test_the_link_shows_on_lists_as_its_host_only(client):
    await approved(client, "Tengil")
    await post(client, body="x", url="https://www.example.com/very/long/path")
    page = (await client.get("/")).text
    assert "↗ example.com" in page and "very/long/path" not in page


async def test_the_link_is_escaped(client):
    await approved(client, "Tengil")
    r = await post(client, body="x", url='https://example.com/"><script>alert(1)</script>')
    # Spaces and quotes are the line: this one has no spaces, so it is a valid
    # address -- and must come out escaped, never as markup.
    if r.status_code == 303:
        page = (await client.get(r.headers["location"])).text
        assert "<script>alert(1)" not in page
