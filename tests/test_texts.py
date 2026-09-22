"""The texts file: complete, and edits apply without a restart."""

import pathlib
import subprocess
import sys

import pytest

from app import texts

pytestmark = pytest.mark.asyncio(loop_scope="session")
ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_every_text_the_code_asks_for_exists():
    r = subprocess.run([sys.executable, str(ROOT / "tools_check_texts.py")],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout


async def test_an_edited_text_shows_on_the_next_page(client, tmp_path, monkeypatch):
    copy = tmp_path / "texts.toml"
    copy.write_text(pathlib.Path(texts.PATH).read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(texts, "PATH", str(copy))
    texts.load()
    try:
        copy.write_text(copy.read_text(encoding="utf-8").replace(
            'all = "All"', 'all = "Alla frågor"'), encoding="utf-8")
        import os; os.utime(copy, (1, 1))           # a changed mtime, whatever the clock
        assert "Alla frågor" in (await client.get("/")).text

        # A broken file keeps the last good texts rather than breaking the site.
        copy.write_text("this is not [valid toml", encoding="utf-8")
        os.utime(copy, (2, 2))
        page = await client.get("/")
        assert page.status_code == 200 and "Alla frågor" in page.text
    finally:
        monkeypatch.undo()
        texts.load()


def test_values_are_escaped_but_the_texts_own_html_is_kept():
    out = texts.t("footer.abuse", contact="<b>x</b>")
    assert "<strong>" in out and "&lt;b&gt;x&lt;/b&gt;" in out
