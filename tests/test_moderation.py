"""Taking things down: remove, restore, purge, and the audit trail.

Under BBS-lagen the obligation is not to prevent bad posts; it is to be
reachable and to act on notice. These tests are that obligation, written down.
"""

import pytest

from helpers import (approved, cast, csrf, issue_id, post_issue, register,
                     user_id, verify)

pytestmark = pytest.mark.asyncio(loop_scope="session")

TITLE = "Should this question be removable?"


async def scene(client, browser):
    """Admin, author, stranger, one question with one ballot on it."""
    await approved(client, "Tengil")
    anna = await browser("198.51.100.40")
    await register(anna, "anna", ip="198.51.100.40")
    _, token = await verify(anna, await user_id("anna"))

    await post_issue(anna, TITLE)
    iid = await issue_id(TITLE)
    await cast(client, iid, token, good=True, bad=True, model_name="qwen3:4b")

    stranger = await browser("198.51.100.41")
    return iid, anna, stranger, token


async def test_a_stranger_cannot_remove_anything(client, browser, sql):
    iid, _, stranger, _ = await scene(client, browser)

    # A valid CSRF pair of their own -- so what is being tested is the
    # authorisation, not the form token. (The issue page shows them no
    # moderation form at all, which is the first line of defence.)
    r = await stranger.post(f"/i/{iid}/remove",
                            data={"reason": "mine now",
                                  "csrf": await csrf(stranger, "/login")})
    assert r.status_code == 404, "404 rather than 403: do not advertise the control"
    assert (await sql("SELECT removed_at FROM issues WHERE id = %s", (iid,),
                      one=True))["removed_at"] is None


async def test_the_author_can_remove_their_own_question(client, browser, sql):
    iid, anna, _, _ = await scene(client, browser)

    r = await anna.post(f"/i/{iid}/remove",
                        data={"reason": "posted by mistake",
                              "csrf": await csrf(anna, f"/i/{iid}")})
    assert r.status_code == 303

    row = await sql("SELECT * FROM issues WHERE id = %s", (iid,), one=True)
    assert row["removed_at"] is not None
    assert row["removed_reason"] == "posted by mistake"
    # Reversible: the ballots already cast are kept.
    assert row["ballots"] == 1


async def test_a_removed_question_leaves_every_listing(client, browser):
    iid, anna, stranger, token = await scene(client, browser)
    await anna.post(f"/i/{iid}/remove", data={"csrf": await csrf(anna, f"/i/{iid}")})

    assert TITLE not in (await stranger.get("/?status=all")).text
    assert not any(i["id"] == iid
                   for i in (await stranger.get("/issues/open")).json())
    queue = await client.get("/agent/issues",
                             headers={"Authorization": f"Bearer {token}"})
    assert not any(i["id"] == iid for i in queue.json())


async def test_the_link_keeps_working_and_says_so(client, browser):
    """A dead link makes a reader think they mistyped it. A tombstone says
    plainly that something was here and was taken down."""
    iid, anna, stranger, _ = await scene(client, browser)
    await anna.post(f"/i/{iid}/remove",
                    data={"reason": "doxxing", "csrf": await csrf(anna, f"/i/{iid}")})

    page = (await stranger.get(f"/i/{iid}")).text
    assert "has been taken down" in page
    assert TITLE not in page, "the removed title leaked to a stranger"
    # Including the <title> tag: the tab, the history and link previews read it.
    assert "<title>Removed" in page

    # The author still sees their own, so they know what was removed.
    assert TITLE in (await anna.get(f"/i/{iid}")).text


async def test_the_json_api_does_not_serve_a_removed_question_either(
        client, browser):
    """A tombstone on the web page is worth nothing if /issues/{id}/results
    still returns the title to anyone who asks. Taken down means taken down on
    every route that reads it."""
    iid, anna, stranger, _ = await scene(client, browser)
    await anna.post(f"/i/{iid}/remove",
                    data={"reason": "doxxing", "csrf": await csrf(anna, f"/i/{iid}")})

    results = await stranger.get(f"/issues/{iid}/results")
    assert results.status_code == 404, "the results API served a removed question"
    assert TITLE not in results.text

    ballots = await stranger.get(f"/issues/{iid}/votes")
    assert ballots.status_code == 404 or ballots.json() == []


async def test_a_removed_question_stops_accepting_ballots(client, browser, sql):
    """Leaving the vote route open means an agent that fetched the queue a
    minute before the removal can still add to a tally nobody can see."""
    iid, anna, _, token = await scene(client, browser)
    await anna.post(f"/i/{iid}/remove", data={"csrf": await csrf(anna, f"/i/{iid}")})

    # A second agent, so this is not refused merely as a duplicate ballot.
    bosse = await browser("198.51.100.42")
    await register(bosse, "bosse", ip="198.51.100.42")
    _, bosse_token = await verify(bosse, await user_id("bosse"))

    r = await cast(client, iid, bosse_token)
    assert r.status_code == 404
    assert (await sql("SELECT ballots FROM issues WHERE id = %s", (iid,),
                      one=True))["ballots"] == 1


async def test_only_an_admin_restores(client, browser, sql):
    iid, anna, _, _ = await scene(client, browser)
    await anna.post(f"/i/{iid}/remove", data={"csrf": await csrf(anna, f"/i/{iid}")})

    refused = await anna.post(f"/i/{iid}/restore",
                              data={"csrf": await csrf(anna, f"/i/{iid}")})
    assert refused.status_code == 404
    assert (await sql("SELECT removed_at FROM issues WHERE id = %s", (iid,),
                      one=True))["removed_at"] is not None

    await client.post(f"/i/{iid}/restore", data={"csrf": await csrf(client, f"/i/{iid}")})
    row = await sql("SELECT removed_at, removed_reason FROM issues WHERE id = %s",
                    (iid,), one=True)
    assert row["removed_at"] is None and row["removed_reason"] == ""


async def test_purging_needs_the_word_and_takes_the_ballots_with_it(
        client, browser, sql):
    iid, _, _, _ = await scene(client, browser)

    wrong = await client.post(f"/i/{iid}/purge", data={
        "confirm": "yes", "csrf": await csrf(client, f"/i/{iid}")})
    assert wrong.status_code == 303 and "err=confirm" in wrong.headers["location"]
    assert (await sql("SELECT count(*) AS n FROM issues WHERE id = %s", (iid,),
                      one=True))["n"] == 1

    await client.post(f"/i/{iid}/purge", data={
        "confirm": "purge", "csrf": await csrf(client, f"/i/{iid}")})
    assert (await sql("SELECT count(*) AS n FROM issues WHERE id = %s", (iid,),
                      one=True))["n"] == 0
    assert (await sql("SELECT count(*) AS n FROM votes WHERE issue_id = %s",
                      (iid,), one=True))["n"] == 0


async def test_only_an_admin_purges(client, browser, sql):
    iid, anna, _, _ = await scene(client, browser)
    r = await anna.post(f"/i/{iid}/purge", data={
        "confirm": "purge", "csrf": await csrf(anna, f"/i/{iid}")})
    assert r.status_code == 404
    assert (await sql("SELECT count(*) AS n FROM issues", one=True))["n"] == 1


async def test_removing_a_ballot_corrects_the_quadrants(client, browser, sql):
    """Ballots are deleted rather than hidden, so the trigger decrements in the
    same transaction. A hidden-but-counted ballot would be a lie on every page
    that shows a number."""
    iid, _, _, _ = await scene(client, browser)
    vote = await sql("SELECT id FROM votes ORDER BY id DESC LIMIT 1", one=True)

    before = await sql("SELECT ballots, contested FROM issues WHERE id = %s",
                       (iid,), one=True)
    assert (before["ballots"], before["contested"]) == (1, 1)

    await client.post(f"/vote/{vote['id']}/remove",
                      data={"csrf": await csrf(client, f"/i/{iid}")})
    after = await sql("SELECT ballots, contested FROM issues WHERE id = %s",
                      (iid,), one=True)
    assert (after["ballots"], after["contested"]) == (0, 0)


async def test_a_non_admin_cannot_remove_a_ballot(client, browser, sql):
    iid, anna, _, _ = await scene(client, browser)
    vote = await sql("SELECT id FROM votes ORDER BY id DESC LIMIT 1", one=True)

    r = await anna.post(f"/vote/{vote['id']}/remove",
                        data={"csrf": await csrf(anna, f"/i/{iid}")})
    assert r.status_code == 404
    assert (await sql("SELECT count(*) AS n FROM votes", one=True))["n"] == 1


async def test_every_moderation_action_is_written_down(client, browser, sql):
    iid, anna, _, _ = await scene(client, browser)
    vote = await sql("SELECT id FROM votes ORDER BY id DESC LIMIT 1", one=True)

    await anna.post(f"/i/{iid}/remove", data={
        "reason": "posted by mistake", "csrf": await csrf(anna, f"/i/{iid}")})
    await client.post(f"/i/{iid}/restore", data={"csrf": await csrf(client, f"/i/{iid}")})
    await client.post(f"/vote/{vote['id']}/remove",
                      data={"csrf": await csrf(client, f"/i/{iid}")})
    await client.post(f"/i/{iid}/purge", data={
        "confirm": "purge", "csrf": await csrf(client, f"/i/{iid}")})

    log = await sql("SELECT actor, action, detail FROM audit_log "
                    "WHERE action LIKE 'issue%%' OR action LIKE 'vote%%' "
                    "ORDER BY id")
    assert [(e["actor"], e["action"]) for e in log] == [
        ("anna", "issue.remove"), ("Tengil", "issue.restore"),
        ("Tengil", "vote.remove"), ("Tengil", "issue.purge")]
    assert log[0]["detail"] == "posted by mistake"
    # The purge row carries the title, because afterwards there is nothing
    # left to point at.
    assert TITLE in log[-1]["detail"]
