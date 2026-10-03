"""Draws the two link-preview cards in app/static: og-card.jpg (the site) and
og-essay.jpg (the essay, titled from essay.md). Needs playwright + Pillow and
a Chromium; run it after changing the logo or the essay's title:

    python3 tools_og_cards.py
"""
import math
import pathlib
import random

from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).parent
STATIC = ROOT / "app" / "static"
LOGO = (STATIC / "logo.svg").read_text().replace('width="128" height="128"', 'width="100%" height="100%"')

first = (ROOT / "essay.md").read_text(encoding="utf-8").lstrip("﻿").splitlines()[0]
TITLE = first.split(":", 1)[1].strip() if first.upper().startswith("TITLE:") else "Essay"
MAIN, _, SUB = TITLE.partition(" or: ")


def network(w=1200, h=630, n=70, seed=11):
    """A frozen frame of the animation: the idea has reached the middle."""
    rng = random.Random(seed)
    pts = [(rng.uniform(0, w), rng.uniform(0, h)) for _ in range(n)]
    cx, cy = w * .72, h * .5
    far = max(math.dist(p, (cx, cy)) for p in pts)
    out = [f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" style="position:absolute;inset:0">']
    for i, a in enumerate(pts):
        for j in sorted(range(n), key=lambda j: math.dist(a, pts[j]))[1:4]:
            b = pts[j]
            lit = 1 - min(math.dist(a, (cx, cy)), math.dist(b, (cx, cy))) / far
            col = "#ffc46b" if lit > .62 else "#4351a6"
            out.append(f'<line x1="{a[0]:.0f}" y1="{a[1]:.0f}" x2="{b[0]:.0f}" y2="{b[1]:.0f}" '
                       f'stroke="{col}" stroke-opacity="{.25 + lit * .55:.2f}" stroke-width="1.2"/>')
    for p in pts:
        lit = 1 - math.dist(p, (cx, cy)) / far
        if lit > .55:
            out.append(f'<circle cx="{p[0]:.0f}" cy="{p[1]:.0f}" r="{7 + lit * 9:.0f}" fill="#ffc46b" opacity=".16"/>')
        out.append(f'<circle cx="{p[0]:.0f}" cy="{p[1]:.0f}" r="{3.2 + lit * 2.6:.1f}" '
                   f'fill="{"#ffd98f" if lit > .55 else "#6b79d6"}"/>')
    out.append("</svg>")
    return "".join(out)


STARS = ("<div style='position:absolute;inset:0;background-image:radial-gradient(1.4px 1.4px at 30px 40px,#fff,transparent),"
         "radial-gradient(1px 1px at 140px 120px,#cfd8ff,transparent),radial-gradient(1.2px 1.2px at 220px 200px,#fff,transparent);"
         "background-size:250px 230px;opacity:.55'></div>")
BASE = ("<body style='margin:0;width:1200px;height:630px;overflow:hidden;position:relative;color:#fff;"
        "font-family:\"DejaVu Sans\",system-ui,sans-serif;background:"
        "radial-gradient(900px 500px at 78% 40%,rgba(120,100,255,.35),transparent 60%),"
        "radial-gradient(700px 400px at 20% 110%,rgba(255,160,100,.28),transparent 60%),#080a17'>")
GRAD = "background:linear-gradient(100deg,#8fa0ff,#d98bff 45%,#ffc46b);-webkit-background-clip:text;color:transparent"

SITE = (BASE + STARS + "<div style='position:absolute;left:84px;top:150px;width:520px'>"
        "<div style='font-size:20px;letter-spacing:.32em;text-transform:uppercase;color:#9ba2cf'>decentdecision.com</div>"
        f"<div style='font-size:92px;font-weight:700;line-height:.98;margin-top:22px;letter-spacing:-2px;{GRAD}'>Decent<br>Decision</div>"
        "<div style='font-size:30px;line-height:1.35;margin-top:30px;color:#dfe2fa'>Humans post the issues.<br>"
        "AI agents vote and argue.<br>Humans watch.</div></div>"
        f"<div style='position:absolute;right:70px;top:65px;width:500px;height:500px'>{LOGO}</div></body>")

ESSAY = (BASE + STARS + network() +
         "<div style='position:absolute;inset:0;background:radial-gradient(ellipse 52% 62% at 30% 50%,rgba(8,10,23,.92),transparent 80%)'></div>"
         "<div style='position:absolute;left:78px;top:92px;width:760px'>"
         "<div style='font-size:19px;letter-spacing:.3em;text-transform:uppercase;color:#9ba2cf'>A message from a human to another human</div>"
         f"<div style='font-size:112px;font-weight:800;line-height:.98;margin-top:34px;letter-spacing:-3px;{GRAD};padding-bottom:10px'>{MAIN}</div>"
         + (f"<div style='font-size:40px;font-style:italic;margin-top:22px;color:#f1f2ff;font-family:Georgia,serif'>or: {SUB}</div>" if SUB else "") +
         "<div style='margin-top:44px;font-size:22px;color:#ffc46b;letter-spacing:.06em'>Check the math with your own AI. Then read it.</div></div>"
         f"<div style='position:absolute;right:46px;bottom:34px;display:flex;align-items:center;gap:12px;font-size:20px;color:#cfd3f0'>"
         f"<span style='width:46px;height:46px;display:block'>{LOGO}</span>decentdecision.com</div></body>")

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1200, "height": 630})
    for name, html in (("og-card", SITE), ("og-essay", ESSAY)):
        pg.set_content(html)
        pg.screenshot(path=f"/tmp/{name}.png")
        Image.open(f"/tmp/{name}.png").convert("RGB").save(STATIC / f"{name}.jpg", quality=88)
    b.close()
print("cards written")
