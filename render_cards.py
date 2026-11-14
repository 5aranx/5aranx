#!/usr/bin/env python3
"""Render the profile sheets into cards/ as standalone SVGs for use in <img> tags.

  python render_cards.py              animated cards
  STATIC=1 python render_cards.py     final frame, no animation (for screenshots)
  SAMPLE=1 python render_cards.py     placeholder data, only for previewing the look

Theme: near-black warm paper, hairline rules, letter-spaced mono labels and a
serif display face, accent in maroon. Layout follows the usual section order
(about, selected projects, technical focus, how I work, activity, priorities).

Everything is <img>-safe: one inline <style> block, CSS keyframes, fonts subset
with pyftsubset and embedded as base64 WOFF. Every animation is plain CSS, so a
single prefers-reduced-motion rule switches all of it off; the base style IS the
final frame.
Data: profile.json (hand-written) + contribution_data.json (refreshed daily).
"""
from __future__ import annotations
import base64, datetime as dt, html, json, os, random, shutil, subprocess, sys, tempfile
from collections import defaultdict
from pathlib import Path

from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parent
STATIC = os.environ.get("STATIC") == "1"
SAMPLE = os.environ.get("SAMPLE") == "1"
OUT = ROOT / "cards"

# ---- design tokens: edit here ----------------------------------------------
T = dict(
    paper="#0b0608",          # near-black with a maroon cast
    cell0="#170f12",          # empty heatmap cell
    ink="#e9dedb",            # warm off-white
    dim="#b9a6a3",
    muted="#97817e",
    faint="#7b6663",
    acc="#b8324e",            # maroon accent
    accd="#6f1a2e",           # deep maroon fill
    line="rgba(233,222,219,.24)",
    soft="rgba(233,222,219,.11)",
)
FACES = {  # css class -> (file in fonts/, family, weight)
    "disp":  ("Fraunces-400.woff", "Fraunces", 400),
    "sans":  ("SpaceGrotesk-400.woff", "Space Grotesk", 400),
    "sansb": ("SpaceGrotesk-600.woff", "Space Grotesk", 600),
    "mono":  ("SpaceMono-400.woff", "Space Mono", 400),
}
W = 880          # full-width card
PAD = 36
CW = W - 2 * PAD
HALF = 428       # card width inside a two-column table cell
HPAD = 26


def mix(a, b, t):
    pa, pb = [int(a[i:i + 2], 16) for i in (1, 3, 5)], [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(pa, pb))


esc = lambda s: html.escape(str(s), quote=True)

# ---- font metrics (exact text widths, no guessing) --------------------------
_m = {}


def tw(face, s, size, ls=0.0):
    if face not in _m:
        f = TTFont(ROOT / "fonts" / FACES[face][0])
        _m[face] = (f.getBestCmap(), f["hmtx"], f["head"].unitsPerEm)
    cmap, hmtx, upem = _m[face]
    total = 0
    for ch in s:
        g = cmap.get(ord(ch))
        if g is None:
            sys.exit(f"glyph U+{ord(ch):04X} {ch!r} is not in {face}; change the text or the font")
        total += hmtx[g][0]
    return total * size / upem + ls * len(s)


def wrap(face, s, size, maxw):
    lines, cur = [], ""
    for word in s.split():
        t = f"{cur} {word}".strip()
        if tw(face, t, size) <= maxw:
            cur = t
        else:
            lines.append(cur)
            cur = word
    return lines + [cur]


def subset_b64(face, chars):
    src = ROOT / "fonts" / FACES[face][0]
    with tempfile.TemporaryDirectory() as td:
        dst = Path(td) / "s.woff"
        tool = ["pyftsubset"] if shutil.which("pyftsubset") else [sys.executable, "-m", "fontTools.subset"]
        uni = ",".join(f"U+{ord(c):04X}" for c in sorted(set(chars) | {" "}))
        subprocess.run(tool + [str(src), f"--unicodes={uni}", "--flavor=woff", "--no-hinting",
                               "--layout-features=kern,liga", f"--output-file={dst}"],
                       check=True, capture_output=True)
        return base64.b64encode(dst.read_bytes()).decode()


EASE = "cubic-bezier(.215,.61,.355,1)"
ANIM_CSS = (
    f"@keyframes rise{{from{{opacity:0;transform:translateY(7px)}}}}"
    f"@keyframes grow{{from{{transform:scaleY(0)}}}}"
    f"@keyframes wipe{{from{{transform:scaleX(0)}}}}"
    f"@keyframes fade{{from{{opacity:0}}}}"
    f".rise{{animation:rise .62s {EASE} both}}"
    f".grow{{animation:grow .7s {EASE} both;transform-box:fill-box;transform-origin:50% 100%}}"
    f".wipe{{animation:wipe .6s {EASE} both;transform-box:fill-box;transform-origin:0 50%}}"
    f".fade{{animation:fade .9s {EASE} both}}"
    "@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}"
)

GRAIN = ('<filter id="gr" x="0" y="0" width="100%" height="100%">'
         '<feTurbulence type="fractalNoise" baseFrequency=".85" numOctaves="2" stitchTiles="stitch"/>'
         '<feColorMatrix type="saturate" values="0"/></filter>')
GLOW = ('<radialGradient id="gl" cx="82%" cy="-12%" r="78%">'
        '<stop offset="0" stop-color="#8e2439" stop-opacity=".55"/>'
        '<stop offset="1" stop-color="#0b0608" stop-opacity="0"/></radialGradient>')

ARROW = ('<path d="M0 10 L10 0 M2.5 0 H10 V7.5" fill="none" stroke="{c}" stroke-width="1.4" '
         'stroke-linecap="round" stroke-linejoin="round"/>')
STAR = '<path d="M5 .6 L6.5 3.7 L9.9 4.2 L7.4 6.6 L8 10 L5 8.4 L2 10 L2.6 6.6 L.1 4.2 L3.5 3.7 Z" fill="{c}"/>'
DOT = '<circle cx="{cx:g}" cy="{cy:g}" r="2.2" fill="{c}"/>'


class Card:
    def __init__(self, name, w, h, label):
        self.name, self.w, self.h, self.label = name, w, h, label
        self.parts, self.used = [], defaultdict(set)

    def add(self, s):
        self.parts.append(s)

    def open(self, delay=0, cls="rise"):
        if STATIC:
            self.add("<g>")
        else:
            st = f' style="animation-delay:{delay}ms"' if delay else ""
            self.add(f'<g class="{cls}"{st}>')

    def close(self):
        self.add("</g>")

    def text(self, x, y, s, face, size, color, anchor="start", ls=0, opacity=None):
        self.used[face].update(s)
        a = f' text-anchor="{anchor}"' if anchor != "start" else ""
        l = f' letter-spacing="{ls}"' if ls else ""
        o = f' opacity="{opacity}"' if opacity else ""
        self.add(f'<text x="{x:g}" y="{y:g}" class="{face}" font-size="{size:g}" fill="{T.get(color, color)}"{a}{l}{o}>{esc(s)}</text>')

    def rect(self, x, y, w, h, color, rx=0, cls=None, delay=0, opacity=None):
        extra = ""
        if cls and not STATIC:
            extra = f' class="{cls}"' + (f' style="animation-delay:{delay}ms"' if delay else "")
        if opacity:
            extra += f' opacity="{opacity}"'
        self.add(f'<rect x="{x:g}" y="{y:g}" width="{w:g}" height="{h:g}" rx="{rx}" fill="{T.get(color, color)}"{extra}/>')

    def line(self, x1, y1, x2, y2, color="line", width=1):
        self.add(f'<line x1="{x1:g}" y1="{y1:g}" x2="{x2:g}" y2="{y2:g}" stroke="{T.get(color, color)}" stroke-width="{width:g}"/>')

    def head(self, num, title, right=None, y=46):
        """Section header: accented index, tracked mono label, hairline rule."""
        self.text(PAD, y, num, "mono", 10, "acc", ls=1.4)
        self.text(PAD + 30, y, title.upper(), "mono", 10, "muted", ls=1.4)
        if right:
            self.text(self.w - PAD, y, right.upper(), "mono", 9.5, "faint", "end", ls=1.2)
        self.line(PAD, y + 12, self.w - PAD, y + 12)

    def chrome(self):
        return (f'<rect x=".5" y=".5" width="{self.w - 1}" height="{self.h - 1}" rx="2" fill="{T["paper"]}"/>')

    def svg(self):
        css = []
        for face, chars in sorted(self.used.items()):
            _, fam, wt = FACES[face]
            css.append(f"@font-face{{font-family:'{fam}';font-weight:{wt};font-style:normal;"
                       f"src:url(\"data:font/woff;base64,{subset_b64(face, chars)}\") format('woff')}}")
            css.append(f".{face}{{font-family:'{fam}',Georgia,serif;font-weight:{wt}}}")
        if not STATIC:
            css.append(ANIM_CSS)
        inner = (self.chrome() + "".join(self.parts) +
                 f'<rect width="{self.w}" height="{self.h}" rx="2" filter="url(#gr)" opacity=".05" pointer-events="none"/>'
                 f'<rect x=".5" y=".5" width="{self.w - 1}" height="{self.h - 1}" rx="2" fill="none" stroke="{T["line"]}"/>')
        return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w}" height="{self.h}" '
                f'viewBox="0 0 {self.w} {self.h}" role="img" aria-label="{esc(self.label)}">'
                f'<title>{esc(self.label)}</title><defs>{GRAIN}{GLOW}</defs>'
                f'<style>{"".join(css)}</style>{inner}</svg>')


# ---- cards -----------------------------------------------------------------
def cover(p):
    H = 300
    c = Card("cover", W, H, f"{p['name']}, @{p['handle']}. {p['role']}")
    c.add(f'<rect x=".5" y=".5" width="{W - 1}" height="{H - 1}" rx="2" fill="url(#gl)"/>')
    c.text(W - PAD, 250, "S", "disp", 190, "acc", "end", opacity=".1")
    c.open(0)
    c.text(PAD, 48, p["kicker"].upper(), "mono", 10, "faint", ls=1.5)
    c.text(PAD, 138, p["name"], "disp", 64, "ink")
    c.close()
    c.rect(PAD, 158, 60, 2, "acc", cls="wipe", delay=260)
    c.open(320)
    for i, l in enumerate(wrap("sans", p["role"], 19, 560)):
        c.text(PAD, 198 + i * 26, l, "sans", 19, "dim")
    c.close()
    c.open(440)
    facts, x = p["facts"], PAD
    for i, f in enumerate(facts):
        if i:
            c.text(x, 266, "·", "mono", 10, "acc")
            x += tw("mono", "·", 10) + 12
        c.text(x, 266, f.upper(), "mono", 10, "muted", ls=1.1)
        x += tw("mono", f.upper(), 10, 1.1) + 12
    c.close()
    return c


def link_cell(l, i):
    w, h = 256, 104
    c = Card(f"link-{i}", w, h, f"{l['label']}: {l['title']}")
    c.open(220 + i * 90)
    c.text(HPAD - 6, 32, l["label"].upper(), "mono", 9.5, "acc", ls=1.5)
    c.text(HPAD - 6, 62, l["title"], "sansb", 16, "ink")
    c.text(HPAD - 6, 84, l["sub"], "sans", 12, "muted")
    c.add(f'<g transform="translate({w - 30} 22)">{ARROW.format(c=T["acc"])}</g>')
    c.close()
    return c


def about(p):
    paras = [wrap("disp", s, 18, CW) for s in p["about"]]
    pillars = [(k["label"].upper(), wrap("sans", k["body"], 14, CW - 116)) for k in p["pillars"]]
    y = 96
    for lines in paras:
        y += len(lines) * 30 + 12
    y += 6
    for _, lines in pillars:
        y += max(30, len(lines) * 21)
        y += 12
    H = round(y) + 6
    c = Card("about", W, H, "About Saransh Singh.")
    c.open(0)
    c.head("01", "About", "Who I am")
    c.close()
    c.open(120)
    y = 96
    for lines in paras:
        for l in lines:
            c.text(PAD, y, l, "disp", 18, "dim")
            y += 30
        y += 12
    y += 6
    for label, lines in pillars:
        c.text(PAD, y, label, "mono", 10, "acc", ls=1.4)
        for k, l in enumerate(lines):
            c.text(PAD + 116, y + k * 21, l, "sans", 14, "dim")
        y += max(30, len(lines) * 21) + 12
    c.close()
    return c


def workhead(projects, data):
    stars = sum((data.get("repos", {}).get(r.get("slug") or "", {}) or {}).get("stars") or 0
                for r in projects)
    right = f"{len(projects)} projects" + (f"  ·  {plural(stars, 'star')}" if stars else "")
    c = Card("work", W, 56, "Selected projects.")
    c.open(0)
    c.head("02", "Selected projects", right, y=30)
    c.close()
    return c


def project_layout(r):
    """Same flow as project(), so a row's two cards can share one height."""
    w = HALF
    desc = wrap("sans", r["description"], 13.5, w - 2 * HPAD)
    bullets = [wrap("sans", b, 13, w - 2 * HPAD - 16) for b in r["bullets"]]
    y = 126 + 20 * len(desc)
    y = max(y + 12, 196)
    for lines in bullets:
        y += 18 * len(lines) + 4
    return desc, bullets, y


def project(r, i, api, delay, h):
    w = HALF
    lang = api.get("language") or r.get("language")
    lcol = api.get("color") or r.get("language_color") or T["muted"]
    stars = api.get("stars") or 0
    desc, bullets, _ = project_layout(r)
    name = r["name"][:34]
    c = Card(f"project-{i}", w, h, f"{r['name']}: {r['description']}")
    c.open(delay)
    c.text(HPAD, 30, f"{i + 1:02d}", "mono", 9.5, "acc", ls=1.4)
    c.text(HPAD + 26, 30, r.get("status") or ("PRIVATE" if r.get("private") else "PUBLIC"),
           "mono", 9.5, "faint", ls=1.4)
    if r.get("url"):
        c.add(f'<g transform="translate({w - HPAD - 10} 20)">{ARROW.format(c=T["acc"])}</g>')
    c.text(HPAD, 66, name, "sansb", 18, "ink")
    meta, x = [], HPAD
    if lang:
        c.add(DOT.format(cx=x + 3, cy=82.5, c=lcol))
        c.text(x + 11, 86, lang, "mono", 9.5, "muted")
        x += 11 + tw("mono", lang, 9.5) + 14
    if stars:
        c.add(f'<g transform="translate({x:g} 77) scale(.9)">{STAR.format(c=T["muted"])}</g>')
        c.text(x + 13, 86, str(stars), "mono", 9.5, "muted")
    c.line(HPAD, 100, w - HPAD, 100, "soft")
    y = 126
    for l in desc:
        c.text(HPAD, y, l, "sans", 13.5, "dim")
        y += 20
    y = max(y + 12, 196)
    for lines in bullets:
        for k, l in enumerate(lines):
            if k == 0:
                c.add(DOT.format(cx=HPAD + 3, cy=y - 4.5, c=T["acc"]))
            c.text(HPAD + 16, y, l, "sans", 13, "dim")
            y += 18
        y += 4
    if y > h - 40:
        sys.exit(f"project-{i} overflows by {y - (h - 40):.0f}px; the row height is wrong")
    c.text(HPAD, h - 24, "  ·  ".join(r["tags"]), "mono", 9.5, "faint", ls=1.1)
    c.close()
    return c


def focus(p):
    rows = [(a.upper(), wrap("sans", b, 14, CW - 196)) for a, b in p["focus"]]
    y = 96
    for _, lines in rows:
        y += max(36, len(lines) * 21 + 16)
    H = round(y) + 6
    c = Card("focus", W, H, "Technical focus: areas and the tools used in each.")
    c.open(0)
    c.head("03", "Technical focus", "Not a ranking")
    c.close()
    y = 96
    for label, lines in rows:
        rh = max(36, len(lines) * 21 + 16)
        c.open(120)
        c.line(PAD, y - 20, W - PAD, y - 20, "soft")
        c.text(PAD, y + 2, label, "mono", 10, "acc", ls=1.3)
        for k, l in enumerate(lines):
            c.text(PAD + 196, y + 2 + k * 21, l, "sans", 14, "dim")
        c.close()
        y += rh
    return c


def how(p):
    items = [(i["title"], wrap("sans", i["body"], 13.5, CW - 40)) for i in p["how"]]
    y = 96
    for _, lines in items:
        y += 26 + len(lines) * 20 + 12
    H = round(y) + 8
    c = Card("how", W, H, "How I work.")
    c.open(0)
    c.head("04", "How I work", f"{len(items)} rules")
    c.close()
    y = 96
    for i, (title, lines) in enumerate(items):
        c.open(120 + i * 60)
        c.text(PAD, y, f"{i + 1:02d}", "mono", 10, "acc", ls=1.2)
        c.text(PAD + 40, y, title, "sansb", 15, "ink")
        for k, l in enumerate(lines):
            c.text(PAD + 40, y + 24 + k * 20, l, "sans", 13.5, "dim")
        c.close()
        y += 26 + len(lines) * 20 + 12
    return c


def plural(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}"


def chart(d):
    H = 340
    c = Card("chart", W, H, f"Contributions per month over the last 12 months: {d['total']} in total.")
    c.open(0)
    c.head("05", "GitHub activity", f"{d['total']:,} contributions")
    c.close()
    b = d.get("busiest")
    if b:
        bd = dt.date.fromisoformat(b["date"])
        big3, cap3 = str(b["count"]), f"Busiest day  ·  {bd.day} {bd.strftime('%b')} {bd.year}"
    else:
        big3, cap3 = "–", "Busiest day"
    c.open(120)
    for i, (big, cap) in enumerate([(plural(d["current_streak"], "day"), "Current streak"),
                                    (str(d["active_days"]), "Active days  ·  12 mo"), (big3, cap3)]):
        x = PAD + i * 260
        c.text(x, 108, big, "disp", 30, "ink")
        c.text(x, 130, cap.upper(), "mono", 9.5, "faint", ls=1.2)
    c.close()
    months = d["months"]
    mx = max((m["count"] for m in months), default=0) or 1
    base, maxh, slot = 292, 96, CW / 12
    c.open(200)
    c.line(PAD, base + .5, W - PAD, base + .5)
    c.close()
    for i, m in enumerate(months):
        cx = PAD + slot * i + slot / 2
        h = max(2, round(m["count"] / mx * maxh)) if m["count"] else 2
        col = T["acc"] if i == len(months) - 1 else (T["accd"] if m["count"] else T["soft"])
        c.rect(cx - 17, base - h, 34, h, col, rx=1, cls="grow", delay=260 + i * 45)
        c.open(360 + i * 45, "fade")
        c.text(cx, base - h - 8, str(m["count"]), "mono", 9.5, "ink" if m["count"] == mx else "faint", "middle")
        c.text(cx, base + 22, m["label"].upper(), "mono", 9.5, "muted" if i == len(months) - 1 else "faint", "middle", ls=1)
        c.close()
    return c


def graph(d, delay):
    gx, gy, pitch, cell = PAD + 30, 94, 14, 11
    grid = d["grid"]
    H = gy + 7 * pitch + 46
    c = Card("graph", W, H, f"Contribution graph: {d['active_days']} active days in the last year.")
    c.open(0)
    c.head("05", "GitHub activity", f"{d['active_days']} active days")
    c.close()
    last_m, last_i = None, -9
    for wi, wk in enumerate(grid):
        s = dt.date.fromisoformat(wk["s"])
        if s.month != last_m and wi - last_i >= 3:
            c.text(gx + wi * pitch, 84, s.strftime("%b").upper(), "mono", 9, "faint", ls=1)
            last_m, last_i = s.month, wi
    for row, lab in ((1, "Mon"), (3, "Wed"), (5, "Fri")):
        c.text(PAD, gy + row * pitch + 9, lab.upper(), "mono", 9, "faint", ls=1)
    nz = sorted(v for wk in grid for v in wk["c"] if v)
    th = [nz[min(len(nz) - 1, int(len(nz) * q))] for q in (.25, .5, .75)] if nz else []
    shades = [T["cell0"]] + [mix(T["cell0"], T["acc"], t) for t in (.34, .58, .8, 1.0)]
    for wi, wk in enumerate(grid):
        c.open(delay + 80 + wi * 14, "fade")
        for row, v in enumerate(wk["c"]):
            if v is None:
                continue
            lvl = 0 if not v else 1 + sum(v > t for t in th)
            c.add(f'<rect x="{gx + wi * pitch}" y="{gy + row * pitch}" width="{cell}" height="{cell}" rx="1.5" fill="{shades[lvl]}"/>')
        c.close()
    ly = gy + 7 * pitch + 18
    c.open(delay + 80 + len(grid) * 14, "fade")
    mw = tw("mono", "More", 9, 1)
    c.text(W - PAD, ly + 9, "MORE", "mono", 9, "faint", "end", ls=1)
    sx = W - PAD - mw - 10 - (5 * pitch - 3)
    for k, col in enumerate(shades):
        c.rect(sx + k * pitch, ly, cell, cell, col, rx=1.5)
    c.text(sx - 10, ly + 9, "LESS", "mono", 9, "faint", "end", ls=1)
    c.close()
    return c


def priorities(p):
    rows = [wrap("sans", s, 14.5, CW - 56) for s in p["priorities"]]
    y = 96
    for lines in rows:
        y += len(lines) * 22 + 12
    H = round(y) + 8
    c = Card("priorities", W, H, "Priorities for 2026.")
    c.open(0)
    c.head("06", "2026 priorities", "Direction")
    c.close()
    y = 96
    for i, lines in enumerate(rows):
        c.open(120 + i * 60)
        c.text(PAD, y, f"{i + 1:02d}", "mono", 10, "acc", ls=1.2)
        for k, l in enumerate(lines):
            c.text(PAD + 56, y + k * 22, l, "sans", 14.5, "dim")
        c.close()
        y += len(lines) * 22 + 12
    return c


def closing(p):
    H = 96
    c = Card("closing", W, H, p["closing"])
    size = 20
    half = tw("disp", p["closing"], size) / 2
    mid = W / 2
    c.open(0)
    c.line(PAD, H / 2, mid - half - 24, H / 2, "soft")
    c.line(mid + half + 24, H / 2, W - PAD, H / 2, "soft")
    c.text(mid, H / 2 + 7, p["closing"], "disp", size, "ink", "middle")
    c.close()
    return c


# ---- data ------------------------------------------------------------------
def synth(profile):
    """Deterministic placeholder numbers, ONLY for previewing the layout (SAMPLE=1)."""
    from fetch_contributions import summarize
    rnd, today = random.Random(7), dt.date.today()
    start = today - dt.timedelta(days=364)
    start -= dt.timedelta(days=(start.weekday() + 1) % 7)
    rows, d = [], start
    while d <= today:
        c = []
        for i in range(7):
            day = d + dt.timedelta(days=i)
            c.append(None if day > today else (0 if rnd.random() > .42 else rnd.choice([1, 1, 2, 3, 4, 6, 9, 14, 22])))
        rows.append({"s": d.isoformat(), "c": c})
        d += dt.timedelta(days=7)
    out = summarize(rows, rows, today)
    out["repos"] = {r["slug"]: {"stars": 1 if i == 1 else 0, "language": ["C++", "C"][i % 2], "color": ["#f34b7d", "#555555"][i % 2]}
                    for i, r in enumerate(x for x in profile["projects"] if x.get("slug"))}
    return out


def main():
    profile = json.loads((ROOT / "profile.json").read_text())
    if SAMPLE:
        data = synth(profile)
    elif (ROOT / "contribution_data.json").exists():
        data = json.loads((ROOT / "contribution_data.json").read_text())
    else:
        sys.exit("contribution_data.json is missing: run fetch_contributions.py first (or SAMPLE=1 to preview the layout)")
    cards = [cover(profile)] + [link_cell(l, i) for i, l in enumerate(profile["links"][:3])]
    cards += [about(profile), workhead(profile["projects"], data)]
    projs = profile["projects"]
    repo_meta = lambda r: data.get("repos", {}).get(r.get("slug") or "", {})
    for start in range(0, len(projs), 2):
        pair = projs[start:start + 2]
        h = max(286, *(round(project_layout(r)[2]) + 44 for r in pair))
        cards += [project(r, start + k, repo_meta(r), 200 + (start + k) * 80, h)
                  for k, r in enumerate(pair)]
    cards += [focus(profile), how(profile), chart(data), graph(data, 600), priorities(profile), closing(profile)]
    OUT.mkdir(exist_ok=True)
    for old in OUT.glob("*.svg"):
        old.unlink()
    for c in cards:
        (OUT / f"{c.name}.svg").write_text(c.svg(), encoding="utf-8")
        print(f"  cards/{c.name}.svg  {(OUT / f'{c.name}.svg').stat().st_size / 1024:.1f} KB")
    print(f"{len(cards)} cards [{'STATIC' if STATIC else 'animated'}{', SAMPLE data' if SAMPLE else ''}]")


if __name__ == "__main__":
    main()
