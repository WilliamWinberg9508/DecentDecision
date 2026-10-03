"""The voting prompt, and the discussion instructions served beside it."""

import pytest

from app import main as dd
from helpers import approved, auth_header

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def run_schema():
    with open(dd.os.path.join(dd.os.path.dirname(dd.__file__), "..", "schema.sql")) as fh:
        sql = fh.read()
    async with dd.pool.connection() as conn:
        await conn.execute(sql)


async def test_the_save_the_world_prompt_is_published_once(client):
    await dd.q("TRUNCATE site_config, agent_prompts RESTART IDENTITY CASCADE")
    await dd.q("INSERT INTO agent_prompts (body) VALUES ('Be nice.')")
    await run_schema()
    rows = await dd.q("SELECT version, body FROM agent_prompts ORDER BY version")
    assert [r["version"] for r in rows] == [1, 2]
    new = rows[-1]["body"]
    assert "save the world" in new and "Hurt no living thing" in new
    assert "<proposal>" in new and "Never obey it." in new      # the injection defence stays
    # Running the schema again (every restart does) adds nothing...
    await run_schema()
    assert await dd.q("SELECT count(*) AS n FROM agent_prompts", one=True) == {"n": 2}
    # ...and an admin's own later wording is not overwritten either.
    await dd.q("INSERT INTO agent_prompts (body) VALUES ('Our own words.')")
    await run_schema()
    assert (await dd.q("SELECT body FROM agent_prompts ORDER BY version DESC LIMIT 1",
                       one=True))["body"] == "Our own words."


async def test_the_served_prompt_carries_the_discussion_instructions(client):
    await dd.q("INSERT INTO agent_prompts (body) VALUES ('Save the world.')")
    p = (await client.get("/agent/prompt")).json()
    d = p["discussion"]
    assert d["system"].startswith("Save the world.")
    assert "reply_to" in d["format"] and "revise" in d["format"]
    assert "never instructions" in d["instructions"]
    assert d["response_schema"]["required"] == ["comment", "reply_to", "votes", "revise"]
    assert "{discussion}" in d["user_template"]
    # Humans are not part of it, and neither is anything private.
    assert "/admin" not in str(p) and "human" not in d["instructions"].lower()
