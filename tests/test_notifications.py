"""Replies, mentions and the inbox.

In-site only. Nothing is emailed, which is what stops being mentioned from
being a way to fill up somebody's mailbox.
"""

import pytest

from app import main as dd
from helpers import approved, csrf, issue_id, post_issue, register, user_id, verify

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def cast(client, browser):
    """Three people: the admin, anna and bosse, and one question."""
    await approved(client, "Tengil")
    await post_issue(client)
    people = {}
    for name, ip in (("anna", "198.51.100.110"), ("bosse", "198.51.100.111")):
        c = await browser(ip)
        await register(c, name, ip=ip)
        await verify(c, await user_id(name))
        people[name] = c
    return await issue_id(), people


async def say(c, iid, body, parent=None):
    data = {"body": body, "sort": "best", "csrf": await csrf(c, f"/i/{iid}")}
    if parent:
        data["parent_id"] = parent
    return await c.post(f"/i/{iid}/comment", data=data)


async def last_comment():
    return await dd.q("SELECT * FROM comments ORDER BY id DESC LIMIT 1", one=True)


async def inbox_of(username):
    return await dd.q(
        """SELECT n.kind, n.read_at, a.username AS actor
             FROM notifications n
             JOIN users u ON u.id = n.user_id
        LEFT JOIN users a ON a.id = n.actor_id
            WHERE u.username = %s ORDER BY n.id""", (username,))


async def test_a_reply_notifies_the_person_replied_to(client, browser):
    iid, people = await cast(client, browser)
    await say(client, iid, "the original point")
    parent = (await last_comment())["id"]

    await say(people["anna"], iid, "I disagree", parent=parent)

    got = await inbox_of("Tengil")
    assert [(n["kind"], n["actor"]) for n in got] == [("reply", "anna")]
    assert await inbox_of("anna") == []


async def test_replying_to_yourself_notifies_nobody(client, browser):
    """Otherwise every thread you keep going fills your own inbox."""
    iid, _ = await cast(client, browser)
    await say(client, iid, "thinking out loud")
    first = (await last_comment())["id"]
    await say(client, iid, "and another thing", parent=first)

    assert await inbox_of("Tengil") == []


async def test_writing_someones_name_notifies_them(client, browser):
    iid, people = await cast(client, browser)
    await say(people["anna"], iid, "I think @bosse ran this one on a 4b model")

    got = await inbox_of("bosse")
    assert [(n["kind"], n["actor"]) for n in got] == [("mention", "anna")]


async def test_a_name_that_is_not_an_account_notifies_nobody(client, browser, sql):
    iid, people = await cast(client, browser)
    await say(people["anna"], iid, "@nobody-here and @bosse")

    assert (await sql("SELECT count(*) AS n FROM notifications",
                      one=True))["n"] == 1


async def test_mentioning_yourself_does_nothing(client, browser, sql):
    iid, people = await cast(client, browser)
    await say(people["anna"], iid, "as @anna said earlier")
    assert (await sql("SELECT count(*) AS n FROM notifications",
                      one=True))["n"] == 0


async def test_a_reply_that_also_names_you_is_one_notification(client, browser):
    """Enforced by the unique constraint rather than by a check, so two
    concurrent paths to the same row cannot both create one."""
    iid, people = await cast(client, browser)
    await say(client, iid, "the original point")
    parent = (await last_comment())["id"]

    await say(people["anna"], iid, "@Tengil that is not what the model said",
              parent=parent)

    got = await inbox_of("Tengil")
    assert len(got) == 1 and got[0]["kind"] == "reply"


async def test_several_names_in_one_comment_each_get_one(client, browser, sql):
    iid, people = await cast(client, browser)
    await say(people["anna"], iid, "@bosse and @Tengil, both of you ran 14b?")

    assert len(await inbox_of("bosse")) == 1
    assert len(await inbox_of("Tengil")) == 1
    assert (await sql("SELECT count(*) AS n FROM notifications",
                      one=True))["n"] == 2


async def test_the_name_match_is_not_case_sensitive(client, browser):
    iid, people = await cast(client, browser)
    await say(people["anna"], iid, "@TENGIL what do you think")
    assert len(await inbox_of("Tengil")) == 1


async def test_a_name_added_by_an_edit_still_notifies(client, browser):
    iid, people = await cast(client, browser)
    await say(people["anna"], iid, "somebody ran this on a small model")
    cid = (await last_comment())["id"]

    await people["anna"].post(f"/c/{cid}/edit", data={
        "body": "@bosse ran this on a small model", "sort": "best",
        "csrf": await csrf(people["anna"], f"/i/{iid}")})

    assert len(await inbox_of("bosse")) == 1


async def test_the_unread_count_shows_in_the_nav_and_clears(client, browser):
    iid, people = await cast(client, browser)
    await say(client, iid, "the original point")
    parent = (await last_comment())["id"]
    await say(people["anna"], iid, "one", parent=parent)
    await say(people["bosse"], iid, "two", parent=parent)

    page = (await client.get("/")).text
    assert '<span class="badge">2</span>' in page

    inbox = (await client.get("/inbox")).text
    assert "anna replied to you" in inbox and "bosse replied to you" in inbox
    # Reading the page does not clear it; saying so does.
    assert '<span class="badge">2</span>' in (await client.get("/")).text

    await client.post("/inbox/read",
                      data={"csrf": await csrf(client, "/inbox")})
    assert '<span class="badge">' not in (await client.get("/")).text


async def test_a_notification_links_to_the_comment(client, browser):
    iid, people = await cast(client, browser)
    await say(client, iid, "the original point")
    parent = (await last_comment())["id"]
    await say(people["anna"], iid, "a reply", parent=parent)
    reply = (await last_comment())["id"]

    assert f'href="/i/{iid}#c{reply}"' in (await client.get("/inbox")).text


async def test_the_inbox_does_not_quote_a_comment_that_was_removed(
        client, browser):
    """The notification stays — you were replied to — but the text it was
    carrying is not served after a moderator has taken it down."""
    iid, people = await cast(client, browser)
    await say(client, iid, "the original point")
    parent = (await last_comment())["id"]
    await say(people["anna"], iid, "something unpleasant", parent=parent)
    bad = (await last_comment())["id"]

    await client.post(f"/c/{bad}/remove", data={
        "reason": "abuse", "sort": "best", "csrf": await csrf(client, f"/i/{iid}")})

    inbox = (await client.get("/inbox")).text
    assert "something unpleasant" not in inbox
    assert "has since been removed" in inbox


async def test_notifications_die_with_the_comment(client, browser, sql):
    iid, people = await cast(client, browser)
    await say(client, iid, "the original point")
    parent = (await last_comment())["id"]
    await say(people["anna"], iid, "a reply", parent=parent)
    reply = (await last_comment())["id"]
    assert (await sql("SELECT count(*) AS n FROM notifications", one=True))["n"] == 1

    await client.post(f"/c/{reply}/purge", data={
        "sort": "best", "csrf": await csrf(client, f"/i/{iid}")})
    assert (await sql("SELECT count(*) AS n FROM notifications", one=True))["n"] == 0


async def test_the_inbox_is_yours_alone(client, browser):
    iid, people = await cast(client, browser)
    await say(client, iid, "the original point")
    parent = (await last_comment())["id"]
    await say(people["anna"], iid, "a reply", parent=parent)

    assert "replied to you" not in (await people["bosse"].get("/inbox")).text
    stranger = await browser("198.51.100.112")
    r = await stranger.get("/inbox")
    assert r.status_code == 303 and r.headers["location"] == "/login?next=/inbox"
