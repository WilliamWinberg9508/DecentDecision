"""Reasoning has no length rule, and other models' reasoning can be read."""

import pytest

from app import main as dd
from helpers import (approved, auth_header, cast, issue_id, post_issue,
                     register, user_id, verify)

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def two_agents(client, browser):
    await approved(client, "Tengil")
    await post_issue(client)
    tokens = []
    for n, name in enumerate(("anna", "bob")):
        c = await browser(f"198.51.100.{40 + n}")
        await register(c, name, ip=f"198.51.100.{40 + n}")
        _, token = await verify(c, await user_id(name))
        tokens.append(token)
    return await issue_id(), tokens


async def test_reasoning_can_be_long(client, browser, sql):
    iid, (token, _) = await two_agents(client, browser)
    essay = "Considered at length. " * 400                # ~8,800 characters
    r = await cast(client, iid, token, rationale=essay, model_name="qwen3:4b")
    assert r.status_code == 201
    row = await sql("SELECT rationale FROM votes WHERE issue_id = %s", (iid,), one=True)
    assert len(row["rationale"]) == len(essay.strip())


async def test_only_the_ceiling_is_refused(client, browser):
    iid, (token, other) = await two_agents(client, browser)
    r = await cast(client, iid, token, rationale="x" * (dd.RATIONALE_CEILING + 1))
    assert r.status_code == 422
    r = await cast(client, iid, other, rationale="x" * dd.RATIONALE_CEILING)
    assert r.status_code == 201


async def test_the_published_protocol_no_longer_asks_for_one_sentence(client):
    p = (await client.get("/agent/prompt")).json()
    assert "maxLength" not in p["response_schema"]["properties"]["rationale"]
    assert "300" not in p["format"] and "sentence" not in p["system"].split("\n\n")[-1]
    assert "500" not in p["vote"]["fields"]["rationale"]
    assert p["reasoning"]["path"] == "/agent/reasoning"


async def test_agents_can_read_what_other_models_wrote(client, browser):
    iid, (anna, bob) = await two_agents(client, browser)
    await cast(client, iid, anna, good=True, bad=False,
               rationale="Clear benefit.", model_name="qwen3:4b")
    await cast(client, iid, bob, good=True, bad=True,
               rationale="Worth it, but costly.", model_name="llama3.2:3b")

    r = await client.get(f"/agent/reasoning?issue={iid}")
    assert r.status_code == 200
    (issue,) = r.json()["issues"]
    assert issue["id"] == iid and issue["open"] is True
    got = {b["model"]: b for b in issue["ballots"]}
    assert got["qwen3:4b"]["reasoning"] == "Clear benefit."
    assert got["qwen3:4b"]["outcome"] == "supported"
    assert got["llama3.2:3b"]["outcome"] == "contested"
    # Which models, never which people.
    text = r.text
    assert "anna" not in text and "bob" not in text and "example.test" not in text


async def test_reasoning_can_be_filtered_by_model_and_limited(client, browser):
    iid, (anna, bob) = await two_agents(client, browser)
    await cast(client, iid, anna, rationale="One.", model_name="qwen3:4b")
    await cast(client, iid, bob, rationale="Two.", model_name="llama3.2:3b")

    r = await client.get(f"/agent/reasoning?issue={iid}&model=QWEN")
    assert [b["model"] for b in r.json()["issues"][0]["ballots"]] == ["qwen3:4b"]
    r = await client.get(f"/agent/reasoning?issue={iid}&limit=1")
    assert len(r.json()["issues"][0]["ballots"]) == 1
    # A wildcard typed into the filter is text, not a pattern.
    r = await client.get(f"/agent/reasoning?issue={iid}&model=%25")
    assert r.json()["issues"][0]["ballots"] == []


async def test_ballots_without_reasoning_and_removed_issues_stay_out(
        client, browser, sql):
    iid, (anna, bob) = await two_agents(client, browser)
    await cast(client, iid, anna, rationale="", model_name="qwen3:4b")
    await cast(client, iid, bob, rationale="Said something.", model_name="x")
    r = await client.get(f"/agent/reasoning?issue={iid}")
    assert [b["reasoning"] for b in r.json()["issues"][0]["ballots"]] == ["Said something."]

    await sql("UPDATE issues SET removed_at = now() WHERE id = %s", (iid,))
    assert (await client.get(f"/agent/reasoning?issue={iid}")).json() == {"issues": []}


async def test_the_issue_list_has_to_be_sensible(client):
    for bad in ("", "abc", "1,,x", ",".join(str(i) for i in range(1, 22))):
        r = await client.get("/agent/reasoning", params={"issue": bad})
        assert r.status_code == 422, bad
    assert (await client.get("/agent/reasoning")).status_code == 422
    assert (await client.get("/agent/reasoning?issue=999999")).json() == {"issues": []}
