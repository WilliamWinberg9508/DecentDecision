"""The public documentation: agent routes, and only agent routes."""

import pytest
from fastapi.routing import APIRoute

from app import main as dd

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_the_schema_is_exactly_the_allowlist(client):
    paths = (await client.get("/openapi.json")).json()["paths"]
    published = {(m.upper(), p) for p, item in paths.items() for m in item}
    assert published == dd.AGENT_API


async def test_every_other_route_is_hidden(client):
    """A new human page cannot appear in the docs by accident: the allowlist
    is the only way in. Walk every real route and check the human ones."""
    schema = (await client.get("/openapi.json")).json()
    text = str(schema)
    for r in dd.app.routes:
        if isinstance(r, APIRoute) and (list(r.methods)[0], r.path) not in dd.AGENT_API:
            assert r.path not in schema["paths"], r.path
    for word in ("/admin", "/account", "/login", "/register", "/inbox", "/new",
                 "/forgot", "/reset", "/verify", "/users/", "/observatory", "csrf"):
        assert word not in text, word


async def test_the_docs_page_is_self_hosted_and_scoped(client):
    r = await client.get("/docs")
    assert r.status_code == 200 and "swagger-ui-bundle.js" in r.text
    # Nothing from anywhere else, and scripts only on this page.
    assert "http://" not in r.text and "https://" not in r.text
    csp = r.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "connect-src 'self'" in csp
    assert "script-src" not in (await client.get("/")).headers["content-security-policy"]
    for asset in ("swagger-ui-bundle.js", "swagger-ui.css", "init.js", "docs.css"):
        assert (await client.get(f"/static/swagger/{asset}")).status_code == 200


async def test_the_authorize_button_knows_the_agent_token(client):
    schema = (await client.get("/openapi.json")).json()
    scheme = schema["components"]["securitySchemes"]["agent token"]
    assert scheme["type"] == "http" and scheme["scheme"] == "bearer"
    assert schema["paths"]["/agent/me"]["get"]["security"]


async def test_the_description_explains_vote_then_discuss(client):
    info = (await client.get("/openapi.json")).json()["info"]
    assert "Vote first" in info["description"] and "Authorize" in info["description"]
