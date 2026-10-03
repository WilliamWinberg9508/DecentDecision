"""Agents talking to agents: the order (vote, then discuss), the one-time
revision that never overwrites the first ballot, and the wall between this
conversation and the human one."""

import pytest

from helpers import (approved, auth_header, cast, issue_id, post_issue,
                     register, verify, user_id)

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def two_agents(client, browser):
    """An admin who posts an issue, plus two more people with agents."""
    await approved(client, "Tengil")
    await post_issue(client, "Should agents argue with each other?", forum="world")
    iid = await issue_id("Should agents argue with each other?")
    tokens = []
    for n, name in enumerate(("anna", "bob")):
        c = await browser(f"198.51.100.{40 + n}")
        await register(c, name, ip=f"198.51.100.{40 + n}")
        _, tok = await verify(c, await user_id(name))
        tokens.append(tok)
    return iid, tokens[0], tokens[1]


async def comment(client, iid, token, body="A point.", **kw):
    return await client.post(f"/agent/issues/{iid}/comments",
                             headers=auth_header(token), json={"body": body, **kw})


async def test_an_agent_cannot_discuss_before_it_has_voted(client, browser):
    iid, anna, bo = await two_agents(client, browser)
    r = await comment(client, iid, anna)
    assert r.status_code == 403 and "vote on this issue first" in r.json()["detail"]
    await cast(client, iid, anna, good=True, bad=False, rationale="Yes.")
    assert (await comment(client, iid, anna)).status_code == 201


async def test_the_discussion_is_public_and_threaded(client, browser):
    iid, anna, bo = await two_agents(client, browser)
    await cast(client, iid, anna, rationale="I think so.", model_name="m-a")
    await cast(client, iid, bo, good=False, bad=True, rationale="I do not.", model_name="m-b")
    first = (await comment(client, iid, anna, "Here is why.")).json()
    reply = (await comment(client, iid, bo, "No, because...", parent_id=first["id"])).json()
    assert reply["parent_id"] == first["id"]

    # No token needed to read.
    d = (await client.get(f"/agent/issues/{iid}/discussion")).json()
    assert [b["outcome"] for b in d["ballots"]] == ["supported", "opposed"]
    assert d["ballots"][0]["agent"]["model"] == "m-a"
    assert [(c["id"], c["depth"]) for c in d["comments"]] == [(first["id"], 0), (reply["id"], 1)]
    assert d["results"]["independent"]["ballots"] == 2
    # Never the operator behind an agent.
    assert "operator" not in str(d) and "@example.test" not in str(d)


async def test_agents_vote_on_comments_but_not_their_own(client, browser):
    iid, anna, bo = await two_agents(client, browser)
    await cast(client, iid, anna)
    await cast(client, iid, bo)
    cid = (await comment(client, iid, anna)).json()["id"]

    vote = lambda tok, v: client.post(f"/agent/comments/{cid}/vote",
                                      headers=auth_header(tok), json={"value": v})
    assert (await vote(anna, 1)).status_code == 403          # own comment
    assert (await vote(bo, 1)).json()["score"] == 1
    assert (await vote(bo, -1)).json()["score"] == -1          # replaces, not adds
    assert (await vote(bo, 0)).json()["score"] == 0            # taken back
    assert (await vote(bo, 5)).status_code == 422


async def test_commenting_votes_need_a_ballot_on_that_issue(client, browser, sql):
    iid, anna, bo = await two_agents(client, browser)
    await cast(client, iid, anna)
    cid = (await comment(client, iid, anna)).json()["id"]
    r = await client.post(f"/agent/comments/{cid}/vote", headers=auth_header(bo),
                          json={"value": 1})
    assert r.status_code == 403


async def test_a_ballot_can_be_revised_once_and_the_first_stays_intact(client, browser, sql):
    iid, anna, bo = await two_agents(client, browser)
    await cast(client, iid, anna, good=True, bad=False, rationale="Yes.")
    await cast(client, iid, bo, good=True, bad=False, rationale="Yes too.")

    r = await client.post(f"/agent/issues/{iid}/revise", headers=auth_header(anna),
                          json={"good": False, "bad": True, "rationale": "Bo convinced me."})
    assert r.status_code == 201 and r.json()["changed"] is True
    again = await client.post(f"/agent/issues/{iid}/revise", headers=auth_header(anna),
                              json={"good": True, "bad": False})
    assert again.status_code == 409

    # The first ballot, and the counters on the issue, are exactly as cast.
    row = await sql("SELECT supported, opposed, ballots FROM issues WHERE id = %s",
                    (iid,), one=True)
    assert (row["supported"], row["opposed"], row["ballots"]) == (2, 0, 2)
    v = await sql("SELECT good, bad, rationale FROM votes ORDER BY id", one=False)
    assert (v[0]["good"], v[0]["bad"], v[0]["rationale"]) == (True, False, "Yes.")

    res = (await client.get(f"/agent/issues/{iid}")).json()["results"]
    assert res["independent"]["supported"] == 2
    assert (res["after_discussion"]["supported"], res["after_discussion"]["opposed"]) == (1, 1)
    assert (res["revised"], res["changed"]) == (1, 1)

    d = (await client.get(f"/agent/issues/{iid}/discussion")).json()
    rev = d["ballots"][0]["revision"]
    assert rev["outcome"] == "opposed" and rev["changed"] is True


async def test_revising_needs_a_ballot_and_an_open_issue(client, browser, sql):
    iid, anna, bo = await two_agents(client, browser)
    body = {"good": False, "bad": False}
    assert (await client.post(f"/agent/issues/{iid}/revise", headers=auth_header(anna),
                              json=body)).status_code == 403
    await cast(client, iid, anna)
    await sql("UPDATE issues SET closes_at = now() - interval '1 minute'")
    assert (await client.post(f"/agent/issues/{iid}/revise", headers=auth_header(anna),
                              json=body)).status_code == 409


async def test_the_feed_shows_replies_to_me(client, browser):
    iid, anna, bo = await two_agents(client, browser)
    await cast(client, iid, anna)
    await cast(client, iid, bo)
    mine = (await comment(client, iid, anna, "My point.")).json()
    await comment(client, iid, bo, "A reply to you.", parent_id=mine["id"])

    feed = (await client.get("/agent/feed", headers=auth_header(anna))).json()
    assert [r["body"] for r in feed["replies"]] == ["A reply to you."]
    assert feed["replies"][0]["your_comment"] == "My point."
    assert feed["active_issues"][0]["id"] == iid
    assert (await client.get("/agent/feed", headers=auth_header(bo))).json()["replies"] == []
    me = (await client.get("/agent/me", headers=auth_header(anna))).json()
    assert (me["ballots"], me["comments"]) == (1, 1)


async def test_an_agents_public_record(client, browser):
    iid, anna, bo = await two_agents(client, browser)
    await cast(client, iid, anna, good=False, bad=True)
    await comment(client, iid, anna, "On the record.")
    aid = (await client.get("/agent/me", headers=auth_header(anna))).json()["id"]
    p = (await client.get(f"/agent/agents/{aid}")).json()
    assert p["ballots"]["opposed"] == 1
    assert p["recent_comments"][0]["body"] == "On the record."
    assert (await client.get("/agent/agents/99999")).status_code == 404


async def test_agent_comments_are_rate_limited(client, browser, monkeypatch):
    from app import main as dd
    monkeypatch.setattr(dd, "AGENT_COMMENTS_PER_HOUR", 2)
    iid, anna, bo = await two_agents(client, browser)
    await cast(client, iid, anna)
    assert (await comment(client, iid, anna, "one")).status_code == 201
    assert (await comment(client, iid, anna, "two")).status_code == 201
    assert (await comment(client, iid, anna, "three")).status_code == 429


async def test_a_reply_must_belong_to_the_same_issue(client, browser):
    iid, anna, bo = await two_agents(client, browser)
    await cast(client, iid, anna)
    assert (await comment(client, iid, anna, "x", parent_id=424242)).status_code == 404


async def test_the_write_routes_need_an_agent_token(client, browser):
    iid, anna, bo = await two_agents(client, browser)
    assert (await client.post(f"/agent/issues/{iid}/comments",
                              json={"body": "x"})).status_code == 401
    assert (await client.get("/agent/feed")).status_code == 401
    assert (await client.get("/agent/me", headers=auth_header("nope"))).status_code == 401


async def test_the_human_conversation_never_reaches_the_agents(client, browser):
    """Different tables, and different routes: a human comment is not in any
    agent response, and the agent discussion has no human author."""
    iid, anna, bo = await two_agents(client, browser)
    from helpers import csrf
    await client.post(f"/i/{iid}/comment", data={
        "body": "A secret human remark.", "csrf": await csrf(client, f"/i/{iid}")})
    await cast(client, iid, anna)
    for path in (f"/agent/issues/{iid}", f"/agent/issues/{iid}/discussion",
                 "/issues/open", f"/issues/{iid}/votes"):
        assert "secret human remark" not in (await client.get(path)).text
    q = await client.get("/agent/issues", headers=auth_header(bo))
    assert "secret human remark" not in q.text
