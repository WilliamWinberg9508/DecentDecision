"""Ballots: the constraints that make a tally mean something."""

import asyncio

import pytest

from app import main as dd
from helpers import (approved, auth_header, cast, issue_id, post_issue,
                     register, verify, user_id)

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def setup_one(client, browser, ip="198.51.100.30"):
    """An admin with a question, and a second person with an agent."""
    await approved(client, "Tengil")
    await post_issue(client)
    anna = await browser(ip)
    await register(anna, "anna", ip=ip)
    _, token = await verify(anna, await user_id("anna"))
    return await issue_id(), token


async def test_a_ballot_lands_and_the_row_counters_move(client, browser, sql):
    iid, token = await setup_one(client, browser)

    r = await cast(client, iid, token, good=True, bad=True,
                   rationale="Worth doing, real costs.", model_name="qwen3:4b")
    assert r.status_code == 201 and r.json()["good"] is True

    row = await sql("SELECT * FROM issues WHERE id = %s", (iid,), one=True)
    assert (row["ballots"], row["contested"]) == (1, 1)
    assert (row["supported"], row["opposed"], row["irrelevant"]) == (0, 0, 0)


@pytest.mark.parametrize("good,bad,column", [
    (True, False, "supported"), (True, True, "contested"),
    (False, True, "opposed"), (False, False, "irrelevant"),
])
async def test_each_ballot_lands_in_its_own_quadrant(client, browser, sql,
                                                     good, bad, column):
    iid, token = await setup_one(client, browser)
    await cast(client, iid, token, good=good, bad=bad)

    row = await sql("SELECT * FROM issues WHERE id = %s", (iid,), one=True)
    assert row[column] == 1 and row["ballots"] == 1
    assert sum(row[c] for c in ("supported", "contested", "opposed",
                                "irrelevant")) == 1


async def test_one_agent_gets_one_ballot_per_question(client, browser, sql):
    """The unique constraint decides, not a prior SELECT -- so two ballots
    arriving at the same instant cannot both slip through."""
    iid, token = await setup_one(client, browser)
    assert (await cast(client, iid, token, good=True)).status_code == 201

    again = await cast(client, iid, token, good=False, bad=True)
    assert again.status_code == 409
    row = await sql("SELECT ballots, supported, opposed FROM issues "
                    "WHERE id = %s", (iid,), one=True)
    assert (row["ballots"], row["supported"], row["opposed"]) == (1, 1, 0)


async def test_simultaneous_ballots_from_one_agent_still_leave_one(
        client, browser, sql):
    iid, token = await setup_one(client, browser)
    results = await asyncio.gather(*[cast(client, iid, token) for _ in range(8)])

    assert sorted(r.status_code for r in results) == [201] + [409] * 7
    assert (await sql("SELECT ballots FROM issues WHERE id = %s", (iid,),
                      one=True))["ballots"] == 1


async def test_voting_stops_when_the_question_closes(client, browser, sql):
    iid, token = await setup_one(client, browser)
    await sql("UPDATE issues SET closes_at = now() - interval '1 minute' "
              "WHERE id = %s", (iid,))

    r = await cast(client, iid, token)
    assert r.status_code == 409 and "closed" in r.json()["detail"]


async def test_an_unknown_token_cannot_vote(client, browser):
    iid, _ = await setup_one(client, browser)
    assert (await cast(client, iid, "not-a-real-token")).status_code == 401
    assert (await client.post(f"/issues/{iid}/vote",
                              json={"good": True, "bad": False})).status_code == 422


async def test_a_revoked_account_cannot_vote_with_an_old_token(
        client, browser, sql):
    """Suspension has to bite at the vote, not only at the login page."""
    iid, token = await setup_one(client, browser)
    await sql("UPDATE users SET status = 'rejected' WHERE username = 'anna'")
    assert (await cast(client, iid, token)).status_code == 401


async def test_a_ballot_claiming_a_prompt_that_was_never_published_is_refused(
        client, browser):
    """Nothing can verify which weights ran on someone else's machine. What
    the site can check is that the claim is internally consistent."""
    iid, token = await setup_one(client, browser)
    assert (await cast(client, iid, token, prompt_version=999)).status_code == 422
    assert (await cast(client, iid, token, prompt_version=1)).status_code == 201


async def test_the_agents_work_queue_is_only_what_it_has_not_voted_on(
        client, browser):
    iid, token = await setup_one(client, browser)
    await post_issue(client, "Should there be a second question?")
    second = await issue_id("Should there be a second question?")

    queue = (await client.get("/agent/issues", headers=auth_header(token))).json()
    assert {i["id"] for i in queue} == {iid, second}

    await cast(client, iid, token)
    queue = (await client.get("/agent/issues", headers=auth_header(token))).json()
    assert [i["id"] for i in queue] == [second]


async def test_the_work_queue_needs_an_agent_token(client, browser):
    await setup_one(client, browser)
    assert (await client.get("/agent/issues")).status_code == 422
    assert (await client.get("/agent/issues",
                             headers=auth_header("nope"))).status_code == 401


async def test_the_results_page_and_the_row_counters_agree(client, browser, sql):
    """Two independent paths to the same number: the denormalised counters the
    pages read, and a live count over votes. If these ever disagree, the
    trigger is wrong and every page is quietly lying."""
    iid, token = await setup_one(client, browser)
    await cast(client, iid, token, good=True, bad=True)

    api = (await client.get(f"/issues/{iid}/results")).json()["tally"]
    row = await sql("SELECT * FROM issues WHERE id = %s", (iid,), one=True)
    for key in ("ballots", "supported", "contested", "opposed", "irrelevant"):
        assert api[key] == row[key], key


async def test_the_model_name_is_recorded_as_a_claim_not_a_fact(
        client, browser, sql):
    iid, token = await setup_one(client, browser)
    await cast(client, iid, token, model_name="llama3.2:1b")

    row = await sql("SELECT model_name, client_claim FROM votes", one=True)
    assert row["model_name"] == "llama3.2:1b"
    # The agent row learns the model it last reported, for the by-model table.
    agent = await sql("SELECT a.model_name FROM agents a "
                      "JOIN votes v ON v.agent_id = a.id", one=True)
    assert agent["model_name"] == "llama3.2:1b"


async def test_a_rationale_cannot_smuggle_terminal_escapes(client, browser, sql):
    iid, token = await setup_one(client, browser)
    await cast(client, iid, token, rationale="fine\x1b[2Jcleared\x00")

    assert (await sql("SELECT rationale FROM votes", one=True))["rationale"] \
        == "fine[2Jcleared"


async def test_deleting_a_ballot_decrements_the_counters(client, browser, sql):
    """The counters are only worth having if they come back down."""
    iid, token = await setup_one(client, browser)
    await cast(client, iid, token, good=True, bad=False)
    await sql("DELETE FROM votes WHERE issue_id = %s", (iid,))

    row = await sql("SELECT ballots, supported FROM issues WHERE id = %s",
                    (iid,), one=True)
    assert (row["ballots"], row["supported"]) == (0, 0)


async def test_one_person_cannot_hold_two_agents(client, sql):
    """The other half of the stuffing argument, and the one that lives in the
    schema: UNIQUE(agents.user_id). Without it, a second agent is a second
    vote on every question."""
    import psycopg

    uid, _ = await approved(client, "Tengil")
    with pytest.raises(psycopg.errors.UniqueViolation):
        await sql("INSERT INTO agents (user_id, name, token_hash) "
                  "VALUES (%s, 'second', 'abc')", (uid,))


async def test_the_published_prompt_is_public(client):
    """Anyone reading a ballot should be able to read what the agent was
    asked. Making it a login-only page would defeat that."""
    r = await client.get("/agent/prompt")
    assert r.status_code == 200
    body = r.json()
    assert body["version"] >= 1 and len(body["body"]) > 50
