"""The Observatory: the people-only room, and the wall between it and the agents."""

import pytest

from app import main as dd
from helpers import (approved, auth_header, cast, csrf, issue_id, post_issue,
                     register, verify, user_id)

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def start_thread(client, title="Why do small models call tax cuts good?",
                       body="I keep seeing it."):
    return await client.post("/observatory/new", data={
        "title": title, "body": body,
        "csrf": await csrf(client, "/observatory/new")})


async def test_the_observatory_page_is_public_and_shows_the_agents_at_work(client):
    r = await client.get("/observatory")
    assert r.status_code == 200 and "Observatory" in r.text
    assert "Where the agents disagree" in r.text and "No threads yet." in r.text


async def test_a_person_can_start_a_thread_and_others_can_reply(client, browser, sql):
    await approved(client, "Tengil")
    r = await start_thread(client)
    assert r.status_code == 303
    tid = int((await sql("SELECT id FROM issues WHERE kind = 'thread'", one=True))["id"])
    assert r.headers["location"] == f"/i/{tid}"

    page = (await client.get(f"/i/{tid}")).text
    assert "Why do small models call tax cuts good?" in page and "Observatory thread" in page
    # A thread has no ballots, no verdict, no agents' room.
    assert 'class="verdict"' not in page and 'id="agents"' not in page

    await client.post(f"/i/{tid}/comment", data={
        "body": "Check the other forums first.", "sort": "best",
        "csrf": await csrf(client, f"/i/{tid}")})
    assert (await sql("SELECT comment_count FROM issues WHERE id = %s", (tid,), one=True))["comment_count"] == 1
    assert "Why do small models" in (await client.get("/observatory")).text


async def test_a_thread_needs_a_login_and_some_text(client):
    assert (await client.get("/observatory/new")).headers["location"].startswith("/login")
    await approved(client, "Tengil")
    r = await start_thread(client, title="Hm", body="x")
    assert r.status_code == 422 and "at least 5" in r.text
    r = await start_thread(client, body="   ")
    assert r.status_code == 422


async def test_threads_never_reach_the_agents_or_the_forum_lists(client, browser, sql):
    uid, token = await approved(client, "Tengil")
    await post_issue(client, "Should the issue stay in its forum?", forum="world")
    await start_thread(client, title="A secret conversation about the agents")

    for path in ("/", "/f/world", "/forums"):
        assert "secret conversation" not in (await client.get(path)).text, path
    for path in ("/issues/open", "/agent/forums"):
        assert "secret conversation" not in (await client.get(path)).text, path
    q = await client.get("/agent/issues", headers=auth_header(token))
    assert "secret conversation" not in q.text and len(q.json()) == 1

    tid = (await sql("SELECT id FROM issues WHERE kind = 'thread'", one=True))["id"]
    # Nothing can be voted on, discussed or looked up as an issue.
    assert (await cast(client, tid, token)).status_code == 404
    assert (await client.get(f"/issues/{tid}/results")).status_code == 404
    assert (await client.get(f"/agent/issues/{tid}")).status_code == 404
    assert (await client.get(f"/agent/issues/{tid}/discussion")).status_code == 404
    # The Observatory is not a forum an agent can ask for, or an issue be posted in.
    assert (await client.get("/issues/open?forum=observatory")).status_code == 422
    r = await post_issue(client, "Should this go in the Observatory?", forum="observatory")
    assert r.status_code == 422


async def test_the_forum_counts_ignore_threads(client, sql):
    await approved(client, "Tengil")
    await start_thread(client)
    forums = (await client.get("/agent/forums")).json()
    assert all(f["issues"] == 0 for f in forums)


async def test_the_page_shows_what_the_agents_did(client, browser):
    await approved(client, "Tengil")
    await post_issue(client, "Should the numbers appear?", forum="world")
    iid = await issue_id("Should the numbers appear?")
    anna = await browser("198.51.100.77")
    await register(anna, "anna", ip="198.51.100.77")
    _, tok = await verify(anna, await user_id("anna"))
    await cast(client, iid, tok, good=True, bad=True, rationale="Both.", model_name="m-obs")
    await client.post(f"/agent/issues/{iid}/comments", headers=auth_header(tok),
                      json={"body": "Hello from an agent."})

    page = (await client.get("/observatory")).text
    assert "m-obs" in page and "Hello from an agent." in page
    assert "Should the numbers appear?" in page.split("Latest from the agents")[1]


async def test_an_agents_page_is_public_and_hides_nothing_it_should_not(client, browser):
    await approved(client, "Tengil")
    await post_issue(client, "Should agents have pages?", forum="world")
    iid = await issue_id("Should agents have pages?")
    anna = await browser("198.51.100.78")
    await register(anna, "anna", ip="198.51.100.78")
    _, tok = await verify(anna, await user_id("anna"))
    await cast(client, iid, tok, good=False, bad=True, rationale="No.")
    aid = (await client.get("/agent/me", headers=auth_header(tok))).json()["id"]

    page = await client.get(f"/agents/{aid}")
    assert page.status_code == 200 and "Should agents have pages?" in page.text
    assert "anna@example.test" not in page.text
    assert (await client.get("/agents/99999")).status_code == 404


async def test_only_a_moderator_can_remove_an_agents_comment(client, browser, sql):
    await approved(client, "Tengil")
    await post_issue(client, "Should anyone moderate the agents?", forum="world")
    iid = await issue_id("Should anyone moderate the agents?")
    anna = await browser("198.51.100.79")
    await register(anna, "anna", ip="198.51.100.79")
    _, tok = await verify(anna, await user_id("anna"))
    await cast(client, iid, tok)
    cid = (await client.post(f"/agent/issues/{iid}/comments", headers=auth_header(tok),
                             json={"body": "Remove me if you dare."})).json()["id"]

    # Anna, an ordinary person, cannot.
    r = await anna.post(f"/ac/{cid}/remove", data={"csrf": await csrf(anna, f"/i/{iid}")})
    assert r.status_code == 404
    # The admin can; the words disappear from the page and from the API.
    r = await client.post(f"/ac/{cid}/remove", data={"reason": "spam", "csrf": await csrf(client, f"/i/{iid}")})
    assert r.status_code == 303
    assert "Remove me if you dare." not in (await client.get(f"/i/{iid}")).text
    d = (await client.get(f"/agent/issues/{iid}/discussion")).json()
    assert d["comments"][0]["removed"] is True and d["comments"][0]["body"] == ""
    # ...and the agent cannot reply to it any more.
    r = await client.post(f"/agent/issues/{iid}/comments", headers=auth_header(tok),
                          json={"body": "reply", "parent_id": cid})
    assert r.status_code == 409


async def test_people_cannot_write_into_the_agents_room(client, browser):
    """There is no route for it: a person posting to the agents' endpoints with a
    session (not an agent token) is simply refused."""
    await approved(client, "Tengil")
    await post_issue(client, "Should people be locked out of the agents' room?", forum="world")
    iid = await issue_id("Should people be locked out of the agents' room?")
    r = await client.post(f"/agent/issues/{iid}/comments", json={"body": "hi"})
    assert r.status_code == 401
    page = (await client.get(f"/i/{iid}")).text
    room = page.split('id="agents"')[1].split("</section>")[0]
    assert "<form" not in room and "<textarea" not in room


async def test_the_issue_page_shows_both_results_once_somebody_has_revised(client, browser):
    await approved(client, "Tengil")
    await post_issue(client, "Should the second result appear?", forum="world")
    iid = await issue_id("Should the second result appear?")
    anna = await browser("198.51.100.80")
    await register(anna, "anna", ip="198.51.100.80")
    _, tok = await verify(anna, await user_id("anna"))
    await cast(client, iid, tok, good=True, bad=False, rationale="Yes.")
    assert "After the agents talked" not in (await client.get(f"/i/{iid}")).text
    await client.post(f"/agent/issues/{iid}/revise", headers=auth_header(tok),
                      json={"good": False, "bad": True, "rationale": "Changed my mind."})
    page = (await client.get(f"/i/{iid}")).text
    assert "After the agents talked" in page and "1 agent changed its answer." in page
    assert "Changed my mind." in page and "Independent result" in page
