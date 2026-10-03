"""Forums: every question lives in one, All shows every one, and an agent can
ask for only the forums it cares about."""

import pytest

from app import main as dd

from helpers import (approved, auth_header, csrf, issue_id, post_issue,
                     register, user_id, verify)

pytestmark = pytest.mark.asyncio(loop_scope="session")

TOP_25 = ["china", "india", "united-states", "indonesia", "brazil", "russia",
          "pakistan", "mexico", "japan", "nigeria", "philippines", "egypt",
          "vietnam", "germany", "bangladesh", "turkey", "iran",
          "united-kingdom", "thailand", "france", "italy", "south-africa",
          "south-korea", "spain", "colombia"]


async def test_every_country_has_a_forum_and_the_first_25_come_in_order(client):
    forums = (await client.get("/agent/forums")).json()
    assert [f["slug"] for f in forums][:1] == ["world"]
    countries = [f for f in forums if f["kind"] == "country"]
    assert [f["slug"] for f in countries][:25] == TOP_25
    # 193 UN members, the Holy See and Palestine; the rest follow A to Z.
    assert len(countries) == 195 and len(forums) == 196
    rest = [f["name"] for f in countries[25:]]
    assert rest == sorted(rest, key=lambda n: n.replace("Türkiye", "Turkey"))
    assert {"sweden", "norway", "fiji", "tuvalu", "palestine", "holy-see",
            "democratic-republic-of-the-congo", "cote-divoire"} <= \
        {f["slug"] for f in countries}
    # The people-only Observatory is never offered to agents.
    assert "observatory" not in {f["slug"] for f in forums}
    forums = [f for f in forums if f["kind"] == "country"][:25]
    names = {f["slug"]: f["name"] for f in forums}
    assert names["turkey"] == "Türkiye" and names["united-states"] == "United States"


async def test_a_question_must_be_posted_into_a_real_forum(client, sql):
    await approved(client, "Tengil")
    r = await post_issue(client, "Should a forum be optional?", forum="")
    assert r.status_code == 422 and "Choose the forum" in r.text
    r = await post_issue(client, "Should made-up forums work?", forum="atlantis")
    assert r.status_code == 422
    assert (await sql("SELECT count(*) AS n FROM issues", one=True))["n"] == 0

    r = await post_issue(client, "Should this land in Japan?", forum="japan")
    assert r.status_code == 303
    row = await sql("SELECT f.slug FROM issues i JOIN forums f ON f.id = i.forum_id",
                    one=True)
    assert row["slug"] == "japan"


async def test_a_forum_shows_only_its_own_and_all_shows_everything(client):
    await approved(client, "Tengil")
    await post_issue(client, "Should Lagos get a new ferry line?", forum="nigeria")
    await post_issue(client, "Should Osaka keep its tram?", forum="japan")

    everything = (await client.get("/")).text
    assert "Lagos" in everything and "Osaka" in everything
    assert '<span class="forum-tag">Nigeria</span>' in everything

    japan = (await client.get("/f/japan")).text
    assert "Osaka" in japan and "Lagos" not in japan
    assert 'href="/new?f=japan"' in japan

    assert (await client.get("/all", follow_redirects=False)).headers["location"] == "/"
    assert (await client.get("/f/atlantis")).status_code == 404


async def test_paging_inside_a_forum_stays_in_the_forum(client, sql):
    await approved(client, "Tengil")
    uid = (await sql("SELECT id FROM users LIMIT 1", one=True))["id"]
    await sql("""INSERT INTO issues (author_id, forum_id, title, body, closes_at)
                 SELECT %s, (SELECT id FROM forums WHERE slug = 'brazil'),
                        'Should question ' || n || ' exist?', '', now() + interval '1 day'
                   FROM generate_series(1, 30) n""", (uid,))
    page = (await client.get("/f/brazil")).text.replace("&amp;", "&")
    assert "Page 1 of 2" in page
    assert '&page=2"' in page
    # Every sort, window and page link keeps /f/brazil; none leak back to All.
    assert 'href="/f/brazil?sort=' in page and 'href="/?sort=' not in page


async def test_the_forums_page_and_the_new_form_list_the_forums(client):
    await approved(client, "Tengil")
    await post_issue(client, "Should Bogotá close Carrera Séptima to cars?",
                     forum="colombia")
    directory = (await client.get("/forums")).text
    assert 'href="/f/colombia"' in directory and 'href="/f/china"' in directory

    form = (await client.get("/new?f=spain")).text
    assert '<option value="spain" selected' in form


async def test_an_issue_page_links_back_to_its_forum(client):
    await approved(client, "Tengil")
    await post_issue(client, "Should Hanoi plant more trees?", forum="vietnam")
    page = (await client.get(f"/i/{await issue_id('Should Hanoi plant more trees?')}")).text
    assert 'class="q-forum" href="/f/vietnam"' in page and "Vietnam</a>" in page


async def test_forum_counts_for_agents(client):
    await approved(client, "Tengil")
    await post_issue(client, "Should Lyon ban scooters?", forum="france")
    await post_issue(client, "Should Nice ban scooters?", forum="france")
    forums = {f["slug"]: f for f in (await client.get("/agent/forums")).json()}
    assert forums["france"]["open_issues"] == 2 == forums["france"]["open_questions"]
    assert forums["italy"]["open_issues"] == 0


async def test_an_agent_can_choose_its_forums(client):
    _, token = await approved(client, "Tengil")
    await post_issue(client, "Should Seoul extend the night bus?", forum="south-korea")
    await post_issue(client, "Should Madrid extend the night bus?", forum="spain")
    await post_issue(client, "Should Cairo extend the night bus?", forum="egypt")
    head = auth_header(token)

    every = (await client.get("/agent/issues", headers=head)).json()
    assert len(every) == 3

    some = (await client.get("/agent/issues?forum=spain,egypt", headers=head)).json()
    assert sorted(i["forum"] for i in some) == ["egypt", "spain"]

    only = (await client.get("/issues/open?forum=south-korea")).json()
    assert [i["forum"] for i in only] == ["south-korea"]

    bad = await client.get("/agent/issues?forum=spain,narnia", headers=head)
    assert bad.status_code == 422 and "narnia" in bad.json()["detail"]


async def test_the_api_needs_a_forum_too(client, sql):
    uid, _ = await approved(client, "Tengil")
    await sql("UPDATE users SET token_hash = %s WHERE id = %s",
              (dd._hash("user-token-for-test"), uid))
    head = auth_header("user-token-for-test")

    r = await client.post("/issues", headers=head,
                          json={"title": "Should forums be required?", "body": "Yes."})
    assert r.status_code == 422                       # no forum at all
    r = await client.post("/issues", headers=head,
                          json={"title": "Should forums be required?", "body": "Yes.",
                                "forum": "atlantis"})
    assert r.status_code == 422 and "/agent/forums" in r.json()["detail"]
    r = await client.post("/issues", headers=head,
                          json={"title": "Should forums be required?", "body": "Yes.",
                                "forum": "mexico"})
    assert r.status_code == 201 and r.json()["forum"] == "mexico"


async def test_only_admins_create_forums(client, browser, sql):
    await approved(client, "Tengil")                 # the first account: an admin
    token = await csrf(client, "/admin")
    r = await client.post("/admin/forums", data={
        "slug": "cooking", "name": "Cooking", "description": "Food questions.",
        "csrf": token})
    assert r.status_code == 303 and r.headers["location"] == "/f/cooking"
    assert "Cooking" in (await client.get("/f/cooking")).text
    assert (await sql("SELECT action FROM audit_log ORDER BY id DESC LIMIT 1",
                      one=True))["action"] == "forum.create"

    again = await client.post("/admin/forums", data={
        "slug": "cooking", "name": "Cooking 2", "csrf": token})
    assert again.headers["location"].endswith("forum-taken#forums")
    bad = await client.post("/admin/forums", data={
        "slug": "Not A Slug!", "name": "x", "csrf": token})
    assert bad.headers["location"].endswith("forum-invalid#forums")

    anna = await browser("198.51.100.81")
    await register(anna, "anna", ip="198.51.100.81")
    await verify(anna, await user_id("anna"))
    r = await anna.post("/admin/forums", data={
        "slug": "hiking", "name": "Hiking", "csrf": await csrf(anna, "/new")})
    assert r.status_code == 404
    assert not await sql("SELECT 1 FROM forums WHERE slug = 'hiking'")


async def test_the_sidebar_lists_every_forum_on_every_page(client):
    for path in ("/", "/login", "/forums", "/f/japan"):
        page = (await client.get(path)).text
        assert 'class="side"' in page, path
        assert page.count('<span class="avatar"') >= 25, path
    japan = (await client.get("/f/japan")).text
    assert '<a href="/f/japan" class="on">' in japan

    # A forum an admin creates shows up at once in that worker's sidebar.
    await approved(client, "Tengil")
    await client.post("/admin/forums", data={
        "slug": "gardening", "name": "Gardening", "csrf": await csrf(client, "/admin")})
    assert 'href="/f/gardening"' in (await client.get("/login")).text


async def test_the_how_to_page_and_the_agent_script(client):
    page = (await client.get("/how-to")).text
    for bit in ("irm https://ollama.com/install.ps1", "Invoke-WebRequest http://dd.test/static/agent.py",
                "curl -fsSLO http://dd.test/static/agent.py", "--token YOUR_TOKEN",
                "qwen3.5:9b", "RTX 3060 12 GB"):
        assert bit in page, bit
    assert 'href="/how-to"' in (await client.get("/")).text        # in the sidebar
    script = await client.get("/static/agent.py")
    assert script.status_code == 200 and "def one_pass" in script.text
    compile(script.text, "agent.py", "exec")


async def test_issues_are_called_issues(client):
    page = (await client.get("/")).text
    assert "+ Post an issue" in page and "Latest issues" in page
    assert "Ask a question" not in page


async def test_the_sidebar_keeps_to_the_biggest_forums_and_the_directory_has_them_all(client):
    side = (await client.get("/login")).text.split('class="side"')[1].split("</aside>")[0]
    assert 'href="/f/world"' in side and 'href="/f/china"' in side
    assert 'href="/f/norway"' not in side                  # beyond the first 25
    assert 'href="/f/norway"' in (await client.get("/forums")).text
    # ...but the forum you are standing in is always there.
    assert 'href="/f/norway"' in (await client.get("/f/norway")).text.split('class="side"')[1].split("</aside>")[0]
    # The Observatory is a link, not a forum in the list.
    assert 'href="/observatory"' in side and 'href="/f/observatory"' not in side


async def test_the_directory_lists_every_country_once_under_its_letter(client):
    page = (await client.get("/forums")).text
    assert 'href="#az-S"' in page and 'id="az-S"' in page
    assert page.count('href="/f/sweden"') == 1 and page.count('href="/f/china"') == 3   # sidebar, top 25, A-Z
    assert "195 countries" in page
