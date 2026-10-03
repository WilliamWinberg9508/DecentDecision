"""Issues posed by agents, kept apart from the ones people post; and people
seeing, editing and re-opening their own."""

import pytest

from app import main as dd
from helpers import (approved, auth_header, cast, csrf, issue_id, login, post_issue,
                     register, verify, user_id)

pytestmark = pytest.mark.asyncio(loop_scope="session")

PW = "a-long-enough-pw"


async def world(client, browser):
    """Tengil (admin) posts three issues; anna and bob each get an agent."""
    await approved(client, "Tengil")
    for n in range(3):
        await post_issue(client, f"Should issue number {n} be settled?", forum="world")
    people = {}
    for n, name in enumerate(("anna", "bob")):
        c = await browser(f"198.51.100.{60 + n}")
        await register(c, name, ip=f"198.51.100.{60 + n}")
        uid = await user_id(name)
        _, tok = await verify(c, uid)
        people[name] = (c, tok, uid)
    return people


async def vote_on_all(client, token):
    for r in await dd.q("SELECT id FROM issues WHERE kind = 'issue' ORDER BY id"):
        await cast(client, r["id"], token, good=True, bad=False, rationale="Yes.")


def pose(client, token, **kw):
    data = {"title": "Should every city plant a million trees?", "forum": "world",
            "body": "Trees cool cities, hold soil and shelter animals."} | kw
    return client.post("/agent/issues", headers=auth_header(token), json=data)


async def test_an_agent_must_vote_a_few_times_before_it_poses(client, browser):
    p = await world(client, browser)
    _, anna, _ = p["anna"]
    r = await pose(client, anna)
    assert r.status_code == 403 and "ballots" in r.json()["detail"]
    await vote_on_all(client, anna)
    r = await pose(client, anna)
    assert r.status_code == 201
    body = r.json()
    assert body["posed_by"] == "agents" and body["forum"] == "world"
    row = await dd.q("SELECT origin, agent_id, author_id FROM issues WHERE id = %s",
                     (body["id"],), one=True)
    assert row["origin"] == "agent" and row["agent_id"] and row["author_id"] == p["anna"][2]


async def test_posing_is_checked_like_a_human_post(client, browser):
    p = await world(client, browser)
    _, anna, _ = p["anna"]
    await vote_on_all(client, anna)
    assert (await pose(client, anna, title="This is a statement, not a question")).status_code == 422
    assert (await pose(client, anna, forum="nowhere-land")).status_code == 422
    assert (await pose(client, anna, forum="observatory")).status_code == 422
    assert (await pose(client, anna, body="", url="")).status_code == 422
    assert (await pose(client, anna, url="javascript:alert(1)")).status_code == 422
    assert (await client.post("/agent/issues", json={"title": "Should we?", "forum": "world"})).status_code == 401
    assert (await pose(client, anna)).status_code == 201
    # The same question is not asked twice -- by anyone.
    assert (await pose(client, anna)).status_code == 409
    assert (await pose(client, p["bob"][1])).status_code == 403          # bob has not voted yet
    await vote_on_all(client, p["bob"][1])
    assert (await pose(client, p["bob"][1])).status_code == 409
    assert (await pose(client, p["bob"][1], title="Should issue number 0 be settled?")).status_code == 409


async def test_agents_are_limited_to_a_few_issues_a_day(client, browser, monkeypatch):
    p = await world(client, browser)
    _, anna, _ = p["anna"]
    await vote_on_all(client, anna)
    monkeypatch.setattr(dd, "AGENT_ISSUES_PER_DAY", 2)
    assert (await pose(client, anna, title="Should the first one be asked?")).status_code == 201
    assert (await pose(client, anna, title="Should the second one be asked?")).status_code == 201
    r = await pose(client, anna, title="Should the third one be asked?")
    assert r.status_code == 429 and "2 issues" in r.json()["detail"]


async def test_the_two_lists_are_kept_apart(client, browser):
    p = await world(client, browser)
    _, anna, _ = p["anna"]
    await vote_on_all(client, anna)
    await pose(client, anna, title="Should an agent be the one asking this?")
    people = (await client.get("/")).text
    assert "Should issue number 1 be settled?" in people
    assert "Should an agent be the one asking this?" not in people
    agents = (await client.get("/?by=agents")).text
    assert "Should an agent be the one asking this?" in agents
    assert "Should issue number 1 be settled?" not in agents
    assert 'class="origin-tag"' in agents and "posed by" in agents
    both = (await client.get("/?by=all")).text
    assert "Should an agent be the one asking this?" in both and "Should issue number 1" in both
    # The tabs say how many each side has, and the same goes inside a forum.
    assert ">3<" in people.replace(" ", "") and ">1<" in people.replace(" ", "")
    assert "Should an agent be the one asking this?" in (await client.get("/f/world?by=agents")).text
    assert "Should an agent be the one asking this?" not in (await client.get("/f/world")).text
    # An unknown value falls back to the people's list rather than failing.
    assert "Should issue number 1" in (await client.get("/?by=whatever")).text


async def test_agents_vote_on_both_kinds_but_never_on_their_own(client, browser):
    p = await world(client, browser)
    _, anna, _ = p["anna"]
    _, bob, _ = p["bob"]
    await vote_on_all(client, anna)
    r = await pose(client, anna, title="Should this one be voted on by others?")
    iid = r.json()["id"]
    # Bob's queue holds human issues and the agent's, labelled; Anna's holds neither of hers.
    queue = (await client.get("/agent/issues", headers=auth_header(bob))).json()
    kinds = {i["id"]: i["posed_by"] for i in queue}
    assert kinds[iid] == "agents" and "people" in kinds.values()
    assert iid not in [i["id"] for i in (await client.get("/agent/issues", headers=auth_header(anna))).json()]
    only_agents = (await client.get("/agent/issues?by=agents", headers=auth_header(bob))).json()
    assert [i["id"] for i in only_agents] == [iid]
    only_people = (await client.get("/agent/issues?by=people", headers=auth_header(bob))).json()
    assert iid not in [i["id"] for i in only_people] and len(only_people) == 3
    # Anna cannot vote on her own issue, Bob can, and the tally shows it.
    mine = await cast(client, iid, anna, good=True, bad=False)
    assert mine.status_code == 403 and "posed yourself" in mine.json()["detail"]
    assert (await cast(client, iid, bob, good=True, bad=True, rationale="Both.")).status_code == 201
    assert (await client.get(f"/issues/{iid}/results")).json()["tally"]["contested"] == 1
    # Public listings say who posed what.
    opened = {i["id"]: i["posed_by"] for i in (await client.get("/issues/open")).json()}
    assert opened[iid] == "agents"
    assert [i["id"] for i in (await client.get("/issues/open?by=agents")).json()] == [iid]
    detail = (await client.get(f"/agent/issues/{iid}")).json()
    assert detail["posed_by"] == "agents"
    # The feed and the profile know about it.
    feed = (await client.get("/agent/feed", headers=auth_header(anna))).json()
    assert [i["id"] for i in feed["your_issues"]] == [iid] and feed["your_issues"][0]["ballots"] == 1
    me = (await client.get("/agent/me", headers=auth_header(anna))).json()
    assert me["issues_posed"] == 1


async def test_people_see_an_agents_issue_and_who_posed_it(client, browser):
    p = await world(client, browser)
    _, anna, _ = p["anna"]
    await vote_on_all(client, anna)
    iid = (await pose(client, anna, title="Should the page say who posed this?")).json()["id"]
    page = (await client.get(f"/i/{iid}")).text
    agent_id = (await dd.q("SELECT agent_id FROM issues WHERE id = %s", (iid,), one=True))["agent_id"]
    assert "Posed by agent" in page and f'href="/agents/{agent_id}"' in page
    assert "Discuss with people" in page          # people can discuss it, apart from the agents
    assert f'href="/i/{iid}/edit"' not in page    # and nobody gets an edit link on an agent's issue
    agent_page = (await client.get(f"/agents/{agent_id}")).text
    assert "Should the page say who posed this?" in agent_page
    profile = (await client.get(f"/agent/agents/{agent_id}")).json()
    assert [i["id"] for i in profile["issues_posed"]] == [iid]


async def test_one_persons_human_limit_does_not_count_their_agents_issues(client, browser, monkeypatch):
    p = await world(client, browser)
    anna_client, anna, _ = p["anna"]
    await vote_on_all(client, anna)
    await pose(client, anna)
    monkeypatch.setattr(dd, "ISSUES_PER_DAY", 1)
    await login(anna_client, "anna")
    r = await post_issue(anna_client, "Should Anna post one of her own?", forum="world")
    assert r.status_code == 303


# --- your issues, and editing them ---------------------------------------------

async def test_your_issues_lists_yours_and_only_yours(client, browser):
    p = await world(client, browser)
    anna_client, anna, _ = p["anna"]
    await login(anna_client, "anna")
    await post_issue(anna_client, "Should Anna have posted this?", forum="india")
    await vote_on_all(client, anna)
    await pose(client, anna, title="Should Annas agent have posed this?")
    page = (await anna_client.get("/mine")).text
    assert "Should Anna have posted this?" in page and "Should Annas agent have posed this?" in page
    assert "Should issue number 0 be settled?" not in page            # Tengil's
    assert "Posed by your agent" in page and 'href="/i/' in page
    assert "noindex" in page
    mine_id = await issue_id("Should Anna have posted this?")
    assert f'href="/i/{mine_id}/edit"' in page
    # Logged out, it sends you to log in.
    stranger = await browser("203.0.113.77")
    r = await stranger.get("/mine")
    assert r.status_code == 303 and "/login" in r.headers["location"]


async def votes_on(iid):
    return (await dd.q("SELECT count(*) AS n FROM votes WHERE issue_id = %s", (iid,), one=True))["n"]


async def test_editing_an_issue_clears_the_votes_and_starts_them_again(client, browser):
    await approved(client, "Tengil")
    await post_issue(client, "Should the first wording be kept?", body="Old context.", forum="world")
    iid = await issue_id("Should the first wording be kept?")
    toks = []
    for n, name in enumerate(("anna", "bob")):
        c = await browser(f"198.51.100.{80 + n}")
        await register(c, name, ip=f"198.51.100.{80 + n}")
        _, tok = await verify(c, await user_id(name))
        toks.append(tok)
        assert (await cast(client, iid, tok, good=True, bad=(n == 1), rationale="r")).status_code == 201
    await client.post(f"/agent/issues/{iid}/comments", headers=auth_header(toks[0]),
                      json={"body": "An agent comment about the old wording."})
    await client.post(f"/agent/issues/{iid}/revise", headers=auth_header(toks[0]),
                      json={"good": False, "bad": True, "rationale": "Changed."})
    from test_comments import say
    await say(client, iid, "A person's comment, which should survive.")
    assert (await dd.q("SELECT ballots FROM issues WHERE id = %s", (iid,), one=True))["ballots"] == 2

    form = (await client.get(f"/i/{iid}/edit")).text
    assert "Should the first wording be kept?" in form and "Old context." in form
    assert "starts the voting over" in form.lower() and "2 ballots" in form
    r = await client.post(f"/i/{iid}/edit", data={
        "title": "Should the better wording be kept?", "body": "New context.", "url": "",
        "forum": "world", "days_open": 14, "csrf": await csrf(client, f"/i/{iid}/edit")})
    assert r.status_code == 303 and r.headers["location"] == f"/i/{iid}"

    row = await dd.q("SELECT * FROM issues WHERE id = %s", (iid,), one=True)
    assert row["title"] == "Should the better wording be kept?" and row["body"] == "New context."
    assert row["ballots"] == row["supported"] == row["contested"] == row["opposed"] == row["irrelevant"] == 0
    assert row["edit_count"] == 1 and row["edited_at"] is not None
    assert await votes_on(iid) == 0
    assert (await dd.q("SELECT count(*) AS n FROM vote_revisions", one=True))["n"] == 0
    assert (await dd.q("SELECT count(*) AS n FROM agent_comments WHERE issue_id = %s", (iid,), one=True))["n"] == 0
    assert (await dd.q("SELECT count(*) AS n FROM comments WHERE issue_id = %s", (iid,), one=True))["n"] == 1
    from datetime import datetime, timedelta, timezone
    assert row["closes_at"] > datetime.now(timezone.utc) + timedelta(days=13)
    # The old wording is kept, with how many ballots it cost.
    hist = await dd.q("SELECT * FROM issue_edits WHERE issue_id = %s", (iid,), one=True)
    assert hist["old_title"] == "Should the first wording be kept?" and hist["ballots_reset"] == 2
    assert (await dd.q("SELECT count(*) AS n FROM audit_log WHERE action = 'issue.edit'", one=True))["n"] == 1
    # The page says so, and the agents can vote on the new wording.
    page = (await client.get(f"/i/{iid}")).text
    assert "Edited" in page and "earlier votes were cleared" in page
    queue = (await client.get("/agent/issues", headers=auth_header(toks[0]))).json()
    assert iid in [i["id"] for i in queue]
    assert (await cast(client, iid, toks[0], good=True, bad=False)).status_code == 201
    assert (await dd.q("SELECT ballots FROM issues WHERE id = %s", (iid,), one=True))["ballots"] == 1


async def test_nobody_else_can_edit_your_issue(client, browser):
    p = await world(client, browser)
    anna_client, anna, _ = p["anna"]
    iid = await issue_id("Should issue number 0 be settled?")        # Tengil's
    await login(anna_client, "anna")
    assert (await anna_client.get(f"/i/{iid}/edit")).status_code == 404
    r = await anna_client.post(f"/i/{iid}/edit", data={
        "title": "Should anna rewrite this?", "body": "x", "forum": "world",
        "csrf": await csrf(anna_client, "/account")})
    assert r.status_code == 404
    assert (await dd.q("SELECT title FROM issues WHERE id = %s", (iid,), one=True))["title"] == "Should issue number 0 be settled?"
    stranger = await browser("203.0.113.78")
    assert (await stranger.get(f"/i/{iid}/edit")).status_code in (303, 404)
    # And an agent's issue is the agent's: not even the person who runs it edits it here.
    await vote_on_all(client, anna)
    aid = (await pose(client, anna)).json()["id"]
    assert (await anna_client.get(f"/i/{aid}/edit")).status_code == 404


async def test_an_edit_is_checked_like_a_new_issue_and_limited(client, browser, monkeypatch):
    await approved(client, "Tengil")
    await post_issue(client, "Should this be edited carefully?", forum="world")
    iid = await issue_id("Should this be edited carefully?")

    async def edit(**kw):
        data = {"title": "Should this be edited carefully now?", "body": "Context.",
                "url": "", "forum": "world", "days_open": 7,
                "csrf": await csrf(client, f"/i/{iid}/edit")} | kw
        return await client.post(f"/i/{iid}/edit", data=data)

    assert (await edit(title="Not a question at all")).status_code == 422
    assert (await edit(forum="observatory")).status_code == 422
    assert (await edit(body="", url="")).status_code == 422
    assert (await edit(url="ftp://example.com/x")).status_code == 422
    assert (await dd.q("SELECT edit_count FROM issues WHERE id = %s", (iid,), one=True))["edit_count"] == 0
    monkeypatch.setattr(dd, "ISSUE_EDITS_PER_DAY", 2)
    assert (await edit()).status_code == 303
    assert (await edit(title="Should this be edited carefully again?")).status_code == 303
    r = await edit(title="Should this be edited carefully a third time?")
    assert r.status_code == 429 and "2 times" in r.text


async def test_a_thread_can_be_edited_without_losing_anything(client):
    await approved(client, "Tengil")
    r = await client.post("/observatory/new", data={
        "title": "Why do the small models agree?", "body": "I noticed it.",
        "csrf": await csrf(client, "/observatory/new")})
    tid = int(r.headers["location"].rsplit("/", 1)[1])
    form = (await client.get(f"/i/{tid}/edit")).text
    assert "starts the voting over" not in form.lower()
    r = await client.post(f"/i/{tid}/edit", data={
        "title": "Why do the small models all agree?", "body": "I noticed it twice.",
        "csrf": await csrf(client, f"/i/{tid}/edit")})
    assert r.status_code == 303
    row = await dd.q("SELECT title, body, kind FROM issues WHERE id = %s", (tid,), one=True)
    assert row == {"title": "Why do the small models all agree?", "body": "I noticed it twice.", "kind": "thread"}


async def test_the_edit_pages_are_not_for_search_engines(client):
    await approved(client, "Tengil")
    await post_issue(client, "Should the edit page be hidden?", forum="world")
    iid = await issue_id("Should the edit page be hidden?")
    assert 'name="robots" content="noindex"' in (await client.get(f"/i/{iid}/edit")).text
    assert "<loc>http://dd.test/mine" not in (await client.get("/sitemap.xml")).text


async def test_the_prompt_tells_agents_how_to_pose(client):
    await dd.q("INSERT INTO agent_prompts (body) VALUES ('Save the world.')")
    p = (await client.get("/agent/prompt")).json()["pose"]
    assert p["path"] == "/agent/issues" and p["method"] == "POST"
    assert p["response_schema"]["required"] == ["title", "body", "forum", "days_open"]
    assert p["system"].startswith("Save the world.") and "question mark" in p["instructions"]
