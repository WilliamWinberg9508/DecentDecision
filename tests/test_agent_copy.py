"""agent.py lives twice: in the repository root, where people look for it, and
in app/static, where the site serves it. They must stay identical."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_root_agent_matches_served_agent():
    assert (ROOT / "agent.py").read_bytes() == (ROOT / "app/static/agent.py").read_bytes(), (
        "agent.py and app/static/agent.py differ: edit one, then copy it over the other"
    )
