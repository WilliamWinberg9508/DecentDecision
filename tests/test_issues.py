"""Posting questions, the public listing, sorting and pagination."""

import pytest

from app import main as dd
from helpers import approved, auth_header, csrf, post_issue, register

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_posting_a_question(client, sql):
    await approved(client, "Tengil")
    r = await post_issue(client, "Should the old town be closed to cars?",
                         "Air quality, and deliveries at night.")
    assert r.status_code == 303 and r.headers["location"].startswith("/i/")

    row = await sql("SELECT * FROM issues", one=True)
    assert row["title"] == "Should the old town be closed to cars?"
    assert row["ballots"] == 0 and row["removed_at"] is None
    assert row["closes_at"] > row["created_at"]


@pytest.mark.parametrize("title", [
    "Cars should be banned from the old town.",     # a statement, not a question
    "Ban cars",                                     # no question mark
    "Why?",                                         # a question, but says nothing
])
async def test_a_title_that_is_not_a_real_question_is_refused(client, sql, title):
    """The title is the proposition the agent votes on. A statement leaves it
    to guess what good and bad are being applied to."""
    await approved(client, "Tengil")
    r = await post_issue(client, title)
    assert r.status_code == 422
    assert (await sql("SELECT count(*) AS n FROM issues", one=True))["n"] == 0


async def test_a_pending_account_cannot_post(client, browser, sql):
    await approved(client, "Tengil")
    anna = await browser("198.51.100.20")
    await register(anna, "anna")                 # registered, not verified

    r = await post_issue(anna, "Should pending accounts be able to post?")
    assert r.status_code == 403
    assert (await sql("SELECT count(*) AS n FROM issues", one=True))["n"] == 0


async def test_a_logged_out_visitor_is_sent_to_log_in(browser):
    c = await browser("198.51.100.21")
    r = await c.get("/new")
    assert r.status_code == 303 and r.headers["location"] == "/login?next=/new"


async def test_the_api_route_needs_a_user_token_not_an_agent_token(client):
    """Agents vote; people post. The two tokens are not interchangeable."""
    _, agent_token = await approved(client, "Tengil")
    r = await client.post("/issues", headers=auth_header(agent_token),
                          json={"title": "Should an agent be able to post?",
                                "body": "It should not."})
    assert r.status_code == 401


async def test_open_issues_is_what_an_agent_polls(client, sql):
    await approved(client, "Tengil")
    await post_issue(client, "Should this one be open?")
    await post_issue(client, "Should this one be closed?")
    await sql("UPDATE issues SET closes_at = now() - interval '1 day' "
              "WHERE title = 'Should this one be closed?'")

    titles = [i["title"] for i in (await client.get("/issues/open")).json()]
    assert titles == ["Should this one be open?"]


async def test_the_listing_pages_at_twenty_five(client, sql):
    await approved(client, "Tengil")
    uid = (await sql("SELECT id FROM users LIMIT 1", one=True))["id"]
    await sql("""INSERT INTO issues (author_id, title, body, closes_at)
                 SELECT %s, 'Question number ' || g || '?', 'body',
                        now() + interval '7 days'
                   FROM generate_series(1, 60) g""", (uid,))

    # The rendered page, not just the query behind it.
    first = await client.get("/")
    assert first.text.count('class="issue-title"') == dd.PAGE_SIZE
    assert 'href="/?sort=new&window=all&status=open&page=3"' in first.text
    assert "Page 1 of 3" in first.text

    view = await dd.browse("new", "all", "open", 1)
    assert view["total"] == 60 and view["pages"] == 3 and len(view["rows"]) == 25

    last = await dd.browse("new", "all", "open", 3)
    assert len(last["rows"]) == 10
    # Out-of-range pages clamp rather than 404 or return nothing.
    assert (await dd.browse("new", "all", "open", 99))["page"] == 3
    assert (await dd.browse("new", "all", "open", -5))["page"] == 1


async def test_each_sort_puts_the_right_question_first(client, sql):
    await approved(client, "Tengil")
    uid = (await sql("SELECT id FROM users LIMIT 1", one=True))["id"]
    # Counters are written by the vote trigger in real life; set them directly
    # here so the ordering itself is what is under test.
    await sql("""INSERT INTO issues
                   (author_id, title, body, closes_at,
                    ballots, supported, contested, opposed, irrelevant,
                    created_at)
                 VALUES
                   (%(u)s, 'Loved?',   'b', now() + interval '7 days', 10, 9, 0, 1, 0,
                    now() - interval '3 hours'),
                   (%(u)s, 'Hated?',   'b', now() + interval '7 days', 10, 1, 0, 9, 0,
                    now() - interval '2 hours'),
                   (%(u)s, 'Split?',   'b', now() + interval '7 days', 12, 4, 4, 4, 0,
                    now() - interval '1 hour'),
                   (%(u)s, 'Ignored?', 'b', now() + interval '7 days',  1, 0, 0, 0, 1,
                    now())""",
              {"u": uid})

    async def top(sort):
        return (await dd.browse(sort, "all", "open", 1))["rows"][0]["title"]

    assert await top("positive") == "Loved?"
    assert await top("negative") == "Hated?"
    assert await top("controversial") == "Split?"
    assert await top("voted") == "Split?"          # most ballots
    assert await top("new") == "Ignored?"          # most recently posted


async def test_an_unknown_sort_falls_back_instead_of_reaching_the_database(client):
    """The sort key is spliced into the ORDER BY, so it can only ever be a
    key of SORTS. This is the test that says so out loud."""
    await approved(client, "Tengil")
    await post_issue(client)

    view = await dd.browse("created_at; DROP TABLE users --", "all", "open", 1)
    assert view["sort"] == "new"
    r = await client.get("/?sort=;DROP%20TABLE%20users;&window=x&status=y&page=0")
    assert r.status_code == 200
    assert await dd.q("SELECT 1 FROM users LIMIT 1", one=True)


async def test_the_time_window_only_narrows(client, sql):
    await approved(client, "Tengil")
    await post_issue(client, "Should this be old?")
    await post_issue(client, "Should this be new?")
    await sql("UPDATE issues SET created_at = now() - interval '40 days' "
              "WHERE title = 'Should this be old?'")

    assert (await dd.browse("voted", "all", "open", 1))["total"] == 2
    assert (await dd.browse("voted", "month", "open", 1))["total"] == 1
    assert (await dd.browse("voted", "day", "open", 1))["total"] == 1


async def test_open_and_closed_are_separate_views(client, sql):
    await approved(client, "Tengil")
    await post_issue(client, "Should this still be open?")
    await post_issue(client, "Should this have closed?")
    await sql("UPDATE issues SET closes_at = now() - interval '1 hour' "
              "WHERE title = 'Should this have closed?'")

    assert (await dd.browse("new", "all", "open", 1))["total"] == 1
    assert (await dd.browse("new", "all", "closed", 1))["total"] == 1
    assert (await dd.browse("new", "all", "all", 1))["total"] == 2
