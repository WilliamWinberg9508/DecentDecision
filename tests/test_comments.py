"""Discussion: threading, scores, editing and removal.

Comments are between people and hang off the question, never off a ballot.
The two sets of numbers on an issue page — the quadrant tally and the comment
scores — have to stay separate, because the whole site depends on nobody
reading one as the other.
"""

import pytest

from app import main as dd
from helpers import approved, csrf, issue_id, post_issue, register, user_id, verify

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def scene(client, browser, ip="198.51.100.100"):
    """An admin who posted a question, and a second approved person."""
    await approved(client, "Tengil")
    await post_issue(client)
    iid = await issue_id()
    anna = await browser(ip)
    await register(anna, "anna", ip=ip)
    await verify(anna, await user_id("anna"))
    return iid, anna


async def say(c, iid, body, parent=None, sort="best"):
    data = {"body": body, "sort": sort, "csrf": await csrf(c, f"/i/{iid}")}
    if parent:
        data["parent_id"] = parent
    return await c.post(f"/i/{iid}/comment", data=data)


async def vote(c, cid, value, iid, sort="best"):
    return await c.post(f"/c/{cid}/vote", data={
        "value": value, "sort": sort, "csrf": await csrf(c, f"/i/{iid}")})


async def last_comment():
    return await dd.q("SELECT * FROM comments ORDER BY id DESC LIMIT 1", one=True)


async def test_a_comment_lands_on_the_question(client, browser, sql):
    iid, _ = await scene(client, browser)
    r = await say(client, iid, "The cost figure here looks optimistic.")
    assert r.status_code == 303 and r.headers["location"].endswith(f"#c1")

    row = await last_comment()
    assert row["issue_id"] == iid and row["parent_id"] is None
    assert row["body"] == "The cost figure here looks optimistic."
    assert row["depth"] == 0 and row["path"] == "0000000001"
    # And the count on the question moved, so the listing never counts rows.
    assert (await sql("SELECT comment_count FROM issues WHERE id = %s", (iid,),
                      one=True))["comment_count"] == 1


async def test_replies_nest_and_read_in_order(client, browser):
    iid, anna = await scene(client, browser)
    await say(client, iid, "first")
    first = (await last_comment())["id"]
    await say(anna, iid, "a reply", parent=first)
    reply = (await last_comment())["id"]
    await say(client, iid, "a reply to the reply", parent=reply)
    deep = (await last_comment())["id"]
    await say(anna, iid, "a second top-level comment")
    second = (await last_comment())["id"]

    ordered = await dd.thread(iid, None, "old")
    assert [c["id"] for c in ordered] == [first, reply, deep, second]
    assert [c["depth"] for c in ordered] == [0, 1, 2, 0]


async def test_indentation_stops_but_nesting_does_not(client, browser):
    """A long argument keeps its true shape in the data; only the left margin
    gives up, because a phone runs out of screen before the thread runs out of
    meaning."""
    iid, _ = await scene(client, browser)
    await say(client, iid, "level 0")
    parent = (await last_comment())["id"]
    for level in range(1, 10):
        await say(client, iid, f"level {level}", parent=parent)
        parent = (await last_comment())["id"]

    ordered = await dd.thread(iid, None, "old")
    assert ordered[-1]["depth"] == 9
    assert ordered[-1]["indent"] == dd.MAX_INDENT


async def test_voting_moves_the_score_and_can_be_taken_back(client, browser, sql):
    iid, anna = await scene(client, browser)
    await say(client, iid, "worth arguing about")
    cid = (await last_comment())["id"]

    await vote(anna, cid, 1, iid)
    row = await sql("SELECT ups, downs, score FROM comments WHERE id = %s",
                    (cid,), one=True)
    assert (row["ups"], row["downs"], row["score"]) == (1, 0, 1)

    # The same arrow again is a misclick undone, not a second vote.
    await vote(anna, cid, 1, iid)
    row = await sql("SELECT ups, score FROM comments WHERE id = %s", (cid,),
                    one=True)
    assert (row["ups"], row["score"]) == (0, 0)
    assert (await sql("SELECT count(*) AS n FROM comment_votes", one=True))["n"] == 0


async def test_changing_your_mind_swaps_the_vote_rather_than_adding_one(
        client, browser, sql):
    iid, anna = await scene(client, browser)
    await say(client, iid, "contested")
    cid = (await last_comment())["id"]

    await vote(anna, cid, 1, iid)
    await vote(anna, cid, -1, iid)
    row = await sql("SELECT ups, downs, score FROM comments WHERE id = %s",
                    (cid,), one=True)
    assert (row["ups"], row["downs"], row["score"]) == (0, 1, -1)
    assert (await sql("SELECT count(*) AS n FROM comment_votes", one=True))["n"] == 1


async def test_one_person_one_vote_per_comment(client, browser, sql):
    """The same rule as ballots, enforced the same way — by the primary key."""
    import psycopg

    iid, anna = await scene(client, browser)
    await say(client, iid, "x")
    cid = (await last_comment())["id"]
    uid = await user_id("anna")

    await sql("INSERT INTO comment_votes (comment_id, user_id, value) "
              "VALUES (%s, %s, 1)", (cid, uid))
    with pytest.raises(psycopg.errors.UniqueViolation):
        await sql("INSERT INTO comment_votes (comment_id, user_id, value) "
                  "VALUES (%s, %s, 1)", (cid, uid))


async def test_best_ranks_by_score_and_new_by_time(client, browser, sql):
    iid, anna = await scene(client, browser)
    await say(client, iid, "early but liked")
    liked = (await last_comment())["id"]
    await say(client, iid, "later and ignored")
    ignored = (await last_comment())["id"]

    await vote(anna, liked, 1, iid)

    assert [c["id"] for c in await dd.thread(iid, None, "best")] == [liked, ignored]
    assert [c["id"] for c in await dd.thread(iid, None, "new")] == [ignored, liked]
    assert [c["id"] for c in await dd.thread(iid, None, "old")] == [liked, ignored]


async def test_a_downvoted_comment_sinks_below_its_siblings(client, browser):
    iid, anna = await scene(client, browser)
    await say(client, iid, "disliked")
    bad = (await last_comment())["id"]
    await say(client, iid, "neutral")
    neutral = (await last_comment())["id"]

    await vote(anna, bad, -1, iid)
    assert [c["id"] for c in await dd.thread(iid, None, "best")] == [neutral, bad]


async def test_a_pending_account_can_read_but_not_comment(client, browser, sql):
    iid, _ = await scene(client, browser)
    bosse = await browser("198.51.100.101")
    await register(bosse, "bosse", ip="198.51.100.101")     # not verified

    assert (await bosse.get(f"/i/{iid}")).status_code == 200
    assert (await say(bosse, iid, "hello")).status_code == 403
    assert (await sql("SELECT count(*) AS n FROM comments", one=True))["n"] == 0


async def test_a_logged_out_visitor_is_sent_to_log_in(client, browser):
    iid, _ = await scene(client, browser)
    stranger = await browser("198.51.100.102")
    r = await stranger.post(f"/i/{iid}/comment", data={
        "body": "hi", "csrf": await csrf(stranger, "/login")})
    assert r.status_code == 303 and r.headers["location"] == f"/login?next=/i/{iid}"


async def test_the_author_can_edit_and_it_says_so(client, browser, sql):
    iid, _ = await scene(client, browser)
    await say(client, iid, "frist")
    cid = (await last_comment())["id"]

    r = await client.post(f"/c/{cid}/edit", data={
        "body": "first", "sort": "best", "csrf": await csrf(client, f"/i/{iid}")})
    assert r.status_code == 303

    row = await sql("SELECT body, edited_at FROM comments WHERE id = %s",
                    (cid,), one=True)
    assert row["body"] == "first" and row["edited_at"] is not None
    assert "· edited" in (await client.get(f"/i/{iid}")).text


async def test_nobody_else_can_edit_your_words(client, browser, sql):
    """An admin may remove a comment. Rewriting one under someone else's name
    is not moderation."""
    iid, anna = await scene(client, browser)
    await say(anna, iid, "anna's opinion")
    cid = (await last_comment())["id"]

    r = await client.post(f"/c/{cid}/edit", data={          # client is the admin
        "body": "something anna never said", "sort": "best",
        "csrf": await csrf(client, f"/i/{iid}")})
    assert r.status_code == 404
    assert (await sql("SELECT body FROM comments WHERE id = %s", (cid,),
                      one=True))["body"] == "anna's opinion"


async def test_removing_a_comment_keeps_the_replies_readable(client, browser, sql):
    iid, anna = await scene(client, browser)
    await say(client, iid, "the parent comment")
    parent = (await last_comment())["id"]
    await say(anna, iid, "an answer that still makes sense", parent=parent)
    child = (await last_comment())["id"]

    await client.post(f"/c/{parent}/remove", data={
        "reason": "off topic", "sort": "best",
        "csrf": await csrf(client, f"/i/{iid}")})

    stranger = await browser("198.51.100.103")
    page = (await stranger.get(f"/i/{iid}")).text
    assert "the parent comment" not in page
    assert "[removed" in page
    assert "an answer that still makes sense" in page       # the reply survives
    assert (await sql("SELECT count(*) AS n FROM comments", one=True))["n"] == 2


async def test_a_stranger_cannot_remove_a_comment(client, browser, sql):
    iid, _ = await scene(client, browser)
    await say(client, iid, "mine")
    cid = (await last_comment())["id"]

    stranger = await browser("198.51.100.104")
    r = await stranger.post(f"/c/{cid}/remove", data={
        "csrf": await csrf(stranger, "/login")})
    assert r.status_code == 404
    assert (await sql("SELECT removed_at FROM comments WHERE id = %s", (cid,),
                      one=True))["removed_at"] is None


async def test_you_cannot_reply_to_a_removed_comment(client, browser):
    iid, anna = await scene(client, browser)
    await say(client, iid, "will be removed")
    cid = (await last_comment())["id"]
    await client.post(f"/c/{cid}/remove", data={
        "sort": "best", "csrf": await csrf(client, f"/i/{iid}")})

    assert (await say(anna, iid, "hello?", parent=cid)).status_code == 409
    assert (await anna.get(f"/c/{cid}/reply")).status_code == 404


async def test_an_admin_purge_takes_the_subtree(client, browser, sql):
    """Deleting for real is admin-only and deliberately different from
    removing: it takes the replies with it, which is why it is for content
    that must not stay on disk rather than for tidying up."""
    iid, anna = await scene(client, browser)
    await say(client, iid, "root")
    root = (await last_comment())["id"]
    await say(anna, iid, "reply", parent=root)
    await say(client, iid, "an unrelated comment")

    refused = await anna.post(f"/c/{root}/purge", data={
        "sort": "best", "csrf": await csrf(anna, f"/i/{iid}")})
    assert refused.status_code == 404

    await client.post(f"/c/{root}/purge", data={
        "sort": "best", "csrf": await csrf(client, f"/i/{iid}")})
    left = await sql("SELECT body FROM comments")
    assert [r["body"] for r in left] == ["an unrelated comment"]
    # And the counter came back down with them.
    assert (await sql("SELECT comment_count FROM issues WHERE id = %s", (iid,),
                      one=True))["comment_count"] == 1


async def test_comments_die_with_a_purged_question(client, browser, sql):
    iid, anna = await scene(client, browser)
    await say(anna, iid, "something about this question")

    await client.post(f"/i/{iid}/purge", data={
        "confirm": "purge", "csrf": await csrf(client, f"/i/{iid}")})
    assert (await sql("SELECT count(*) AS n FROM comments", one=True))["n"] == 0


async def test_a_flood_of_comments_is_slowed_down(client, browser, monkeypatch):
    monkeypatch.setattr(dd, "COMMENTS_PER_5_MIN", 3)
    iid, _ = await scene(client, browser)
    for n in range(3):
        assert (await say(client, iid, f"comment {n}")).status_code == 303
    assert (await say(client, iid, "and another")).status_code == 429


async def test_comment_text_is_escaped_like_everything_else(client, browser):
    iid, _ = await scene(client, browser)
    await say(client, iid, '<script>alert(1)</script> and <img src=x onerror=1>')

    page = (await client.get(f"/i/{iid}")).text
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page


async def test_the_comment_score_is_not_mixed_into_the_ballot_tally(
        client, browser, sql):
    """The one confusion this feature could cause, asserted against."""
    iid, anna = await scene(client, browser)
    await say(client, iid, "a comment people like")
    cid = (await last_comment())["id"]
    await vote(anna, cid, 1, iid)

    row = await sql("SELECT ballots, supported, contested, opposed, irrelevant "
                    "FROM issues WHERE id = %s", (iid,), one=True)
    assert all(v == 0 for v in row.values())


# --- the discussion window ---------------------------------------------------
# It is a dialog built out of :target, so what can be asserted is the markup
# and the fragments. The CSS that hides it until it is opened is checked in
# test_templates.py, next to the other things the stylesheet is load-bearing for.

async def test_the_discuss_button_sits_between_the_tally_and_the_motivations(
        client, browser):
    """Where the button is *is* the feature: the discussion is about what the
    models said, so it belongs after the numbers and before the reasoning."""
    iid, _ = await scene(client, browser)
    page = (await client.get(f"/i/{iid}")).text

    quad = page.index('class="quad"')
    button = page.index('href="#discuss"')
    by_model = page.index("By model</h2>") if "By model</h2>" in page \
        else page.index('id="discuss"')

    assert quad < button < by_model


async def test_the_thread_is_inside_the_window(client, browser):
    """Not a section of the page that happens to be styled as one -- the
    comments are children of the overlay, which is what makes them invisible
    until it is opened."""
    iid, _ = await scene(client, browser)
    await say(client, iid, "inside the window")
    page = (await client.get(f"/i/{iid}")).text

    overlay = page.index('<div class="overlay" id="discuss">')
    comment = page.index("inside the window")
    closing = page.rindex("</div>")
    assert overlay < comment < closing


async def test_the_window_has_no_page_of_its_own(client, browser):
    """Only reachable by opening a question and pressing the button, so there
    is no separate address for it to drift out of sync at."""
    iid, _ = await scene(client, browser)
    for path in (f"/i/{iid}/discuss", "/discuss", f"/i/{iid}/comments"):
        assert (await client.get(path)).status_code == 404


async def test_posting_and_voting_come_back_with_the_window_open(
        client, browser):
    """Every action reloads the page, so each one has to bring the fragment
    back or the window would slam shut on every click."""
    iid, anna = await scene(client, browser)
    posted = await say(client, iid, "a comment")
    cid = (await last_comment())["id"]
    assert posted.headers["location"].endswith(f"#c{cid}")

    voted = await vote(anna, cid, 1, iid)
    assert voted.headers["location"].endswith(f"#c{cid}")

    removed = await client.post(f"/c/{cid}/remove", data={
        "sort": "best", "csrf": await csrf(client, f"/i/{iid}")})
    assert removed.headers["location"].endswith(f"#c{cid}")


async def test_the_sort_tabs_keep_the_window_open(client, browser):
    iid, _ = await scene(client, browser)
    await say(client, iid, "something to sort")
    page = (await client.get(f"/i/{iid}")).text

    assert f'/i/{iid}?comments=new#discuss' in page
    assert "#discussion" not in page, "a stale anchor from the old page layout"


async def test_replying_and_editing_happen_inside_the_window(client, browser):
    """The reply and edit boxes are in the overlay itself, so answering someone
    never leaves the discussion -- and posting lands back in it."""
    iid, _ = await scene(client, browser)
    await say(client, iid, "the first word")
    cid = (await dd.q("SELECT id FROM comments ORDER BY id DESC LIMIT 1", one=True))["id"]
    page = (await client.get(f"/i/{iid}")).text
    window = page[page.index('id="discuss"'):]

    assert f'name="parent_id" value="{cid}"' in window       # the reply box
    assert f'action="/c/{cid}/edit"' in window              # the edit box, own comment
    assert f'href="/c/{cid}/reply' not in page              # no second page

    r = await client.post(f"/i/{iid}/comment", data={
        "body": "a reply", "parent_id": cid, "csrf": await csrf(client, f"/i/{iid}")})
    assert r.status_code == 303 and "#c" in r.headers["location"]
    r = await client.post(f"/i/{iid}/comment", data={
        "body": "   ", "csrf": await csrf(client, f"/i/{iid}")})
    assert r.headers["location"].endswith("#discuss")        # still in the window
