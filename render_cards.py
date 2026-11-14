#!/usr/bin/env python3
"""Render the profile cards into cards/ as standalone SVGs for use in <img> tags.

  python render_cards.py              animated cards
  STATIC=1 python render_cards.py     final frame, no animation (for screenshots)
  SAMPLE=1 python render_cards.py     placeholder data, only for previewing the look

Everything is <img>-safe: one inline <style> block, CSS keyframes, fonts subset with
pyftsubset and embedded as base64 WOFF. Every animation is plain CSS, so a single
prefers-reduced-motion rule switches all of it off; the base style IS the final frame.
Data: profile.json (hand-written) + contribution_data.json (refreshed daily by the Action).
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
T = dict(bg="#0f1114", line="#242a32", hi="#e8eaee", mid="#a1a9b6", dim="#667080",
         acc="#4fd1b5", accd="#2c6e62", cell0="#1a1f26")
FACES = {  # css class -> (file in fonts/, family, weight)
    "sans":  ("PlexSans-400.woff", "IBM Plex Sans", 400),
    "sansb": ("PlexSans-600.woff", "IBM Plex Sans", 600),
    "mono":  ("PlexMono-400.woff", "IBM Plex Mono", 400),
    "monom": ("PlexMono-500.woff", "IBM Plex Mono", 500),
}
W = 860          # full-width card
PAD = 40


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


def fit(face, s, size, maxw, what):
    if tw(face, s, size) > maxw:
        sys.exit(f"{what} too wide ({tw(face, s, size):.0f}px > {maxw}px): {s!r}")


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


ANIM_CSS = (
    "@keyframes rise{from{opacity:0;transform:translateY(8px)}}"
    "@keyframes grow{from{transform:scaleY(0)}}"
    "@keyframes wipe{from{transform:scaleX(0)}}"
    "@keyframes fade{from{opacity:0}}"
    ".rise{animation:rise .6s cubic-bezier(.2,.7,.2,1) both}"
    ".grow{animation:grow .7s cubic-bezier(.2,.7,.2,1) both;transform-box:fill-box;transform-origin:50% 100%}"
    ".wipe{animation:wipe .6s cubic-bezier(.2,.7,.2,1) both;transform-box:fill-box;transform-origin:0 50%}"
    ".fade{animation:fade .45s ease-out both}"
    "@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}"
)


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

    def text(self, x, y, s, face, size, color, anchor="start", ls=0):
        self.used[face].update(s)
        a = f' text-anchor="{anchor}"' if anchor != "start" else ""
        l = f' letter-spacing="{ls}"' if ls else ""
        self.add(f'<text x="{x:g}" y="{y:g}" class="{face}" font-size="{size:g}" fill="{T.get(color, color)}"{a}{l}>{esc(s)}</text>')

    def rect(self, x, y, w, h, color, rx=0, cls=None, delay=0):
        extra = ""
        if cls and not STATIC:
            extra = f' class="{cls}"' + (f' style="animation-delay:{delay}ms"' if delay else "")
        self.add(f'<rect x="{x:g}" y="{y:g}" width="{w:g}" height="{h:g}" rx="{rx}" fill="{T.get(color, color)}"{extra}/>')

    def svg(self):
        css = []
        for face, chars in sorted(self.used.items()):
            _, fam, wt = FACES[face]
            css.append(f"@font-face{{font-family:'{fam}';font-weight:{wt};font-style:normal;"
                       f"src:url(\"data:font/woff;base64,{subset_b64(face, chars)}\") format('woff')}}")
            css.append(f".{face}{{font-family:'{fam}',system-ui,sans-serif;font-weight:{wt}}}")
        if not STATIC:
            css.append(ANIM_CSS)
        return (f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
                f'width="{self.w}" height="{self.h}" viewBox="0 0 {self.w} {self.h}" role="img" aria-label="{esc(self.label)}">'
                f'<title>{esc(self.label)}</title><style>{"".join(css)}</style>'
                f'<rect x=".5" y=".5" width="{self.w - 1}" height="{self.h - 1}" rx="6" fill="{T["bg"]}" stroke="{T["line"]}"/>'
                + "".join(self.parts) + "</svg>")


ARROW = ('<path d="M0 10 L10 0 M2.5 0 H10 V7.5" fill="none" stroke="{c}" stroke-width="1.6" '
         'stroke-linecap="round" stroke-linejoin="round"/>')
STAR = '<path d="M5 .6 L6.5 3.7 L9.9 4.2 L7.4 6.6 L8 10 L5 8.4 L2 10 L2.6 6.6 L.1 4.2 L3.5 3.7 Z" fill="{c}"/>'


# ---- cards -----------------------------------------------------------------
def hero(p):
    port = p.get("portrait")
    textw = W - 2 * PAD - (172 if port else 0)
    lines = wrap("sans", p["tagline"], 19, textw)
    last = 184 + (len(lines) - 1) * 27
    meta_y = last + 42
    H = meta_y + 34
    c = Card("hero", W, H, f"{p['name']}. {p['tagline']}")
    c.open(0)
    c.text(PAD, 58, "@" + p["handle"], "mono", 13, "dim", ls=0.6)
    c.text(PAD, 124, p["name"], "sansb", 58, "hi", ls=-1)
    c.close()
    c.rect(PAD, 146, 56, 3, "acc", cls="wipe", delay=260)
    c.open(320)
    for i, l in enumerate(lines):
        c.text(PAD, 184 + i * 27, l, "sans", 19, "mid")
    c.close()
    c.open(440)
    c.text(PAD, meta_y, "  ·  ".join(p["meta"]), "mono", 12, "dim", ls=0.4)
    c.close()
    if port:
        raw = (ROOT / port).read_bytes()
        mime = "image/png" if port.lower().endswith("png") else "image/jpeg"
        x, y, s = W - PAD - 132, (H - 132) // 2, 132
        c.open(200, "fade")
        c.add(f'<clipPath id="pc"><rect x="{x}" y="{y}" width="{s}" height="{s}" rx="4"/></clipPath>'
              f'<image href="data:{mime};base64,{base64.b64encode(raw).decode()}" x="{x}" y="{y}" width="{s}" height="{s}" '
              f'preserveAspectRatio="xMidYMid slice" clip-path="url(#pc)"/>'
              f'<rect x="{x}" y="{y}" width="{s}" height="{s}" rx="4" fill="none" stroke="{T["acc"]}" stroke-width="1.5"/>')
        c.close()
    return c


def tile(t, i):
    w, h = 280, 104
    c = Card(f"tile-{i}", w, h, f"{t['title']}: {t['sub']}")
    for what, face, size, s in (("label", "mono", 10.5, t["label"].upper()), ("title", "sansb", 17, t["title"]), ("sub", "sans", 12.5, t["sub"])):
        fit(face, s, size, w - 76 if what == "label" else w - 40, f"tile {what}")
    c.open(220 + i * 90)
    c.rect(20, 0, 18, 2, "acc")
    c.text(20, 32, t["label"].upper(), "mono", 10.5, "dim", ls=1.6)
    c.text(20, 62, t["title"], "sansb", 17, "hi")
    c.text(20, 85, t["sub"], "sans", 12.5, "mid")
    c.add(f'<g transform="translate({w - 34} 20)">{ARROW.format(c=T["acc"])}</g>')
    c.close()
    return c


def plural(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}"


def chart(d):
    H = 336
    c = Card("chart", W, H, "Contributions per month over the last 12 months, with current streak, active days and busiest day")
    c.open(520)
    c.text(PAD, 46, "CONTRIBUTIONS  ·  LAST 12 MONTHS", "mono", 11, "dim", ls=1.4)
    c.text(W - PAD, 46, f"{d['total']:,} in total", "sans", 13, "mid", "end")
    b = d.get("busiest")
    if b:
        bd = dt.date.fromisoformat(b["date"])
        big3, cap3 = str(b["count"]), f"BUSIEST DAY  ·  {bd.day} {bd.strftime('%b').upper()} {bd.year}"
    else:
        big3, cap3 = "–", "BUSIEST DAY"
    for i, (big, cap) in enumerate([(plural(d["current_streak"], "day"), "CURRENT STREAK"),
                                    (str(d["active_days"]), "ACTIVE DAYS  ·  LAST 12 MO"), (big3, cap3)]):
        x = PAD + i * 260
        c.text(x, 104, big, "sansb", 30, "hi")
        c.text(x, 126, cap, "mono", 10.5, "dim", ls=1.2)
    c.close()
    months = d["months"]
    mx = max((m["count"] for m in months), default=0) or 1
    base, maxh, slot = 288, 96, (W - 2 * PAD) / 12
    c.open(520)
    c.add(f'<line x1="{PAD}" y1="{base + .5}" x2="{W - PAD}" y2="{base + .5}" stroke="{T["line"]}"/>')
    c.close()
    for i, m in enumerate(months):
        cx = PAD + slot * i + slot / 2
        h = max(2, round(m["count"] / mx * maxh)) if m["count"] else 2
        col = T["acc"] if i == len(months) - 1 else (T["accd"] if m["count"] else T["line"])
        c.rect(cx - 17, base - h, 34, h, col, rx=2, cls="grow", delay=600 + i * 45)
        c.open(700 + i * 45, "fade")
        c.text(cx, base - h - 9, str(m["count"]), "mono", 10.5, "hi" if m["count"] == mx else "dim", "middle")
        c.text(cx, base + 22, m["label"], "mono", 10.5, "mid" if i == len(months) - 1 else "dim", "middle")
        c.close()
    return c


def repo(r, i, api, base_delay):
    lang = api.get("language") or r.get("language")
    lcol = api.get("color") or r.get("language_color") or T["mid"]
    stars = api.get("stars") or 0
    desc = wrap("sans", r["description"], 14, W - 2 * PAD)
    tag_y = 78 + (len(desc) - 1) * 22 + 38
    H = tag_y + 32
    c = Card(f"repo-{i}", W, H, f"{r['name']}: {r['description']}")
    c.open(base_delay + i * 180)
    c.rect(0, 28, 3, 26, "acc")
    c.text(PAD, 48, r["name"], "sansb", 18, "hi")
    x = W - PAD
    if r.get("url"):
        c.add(f'<g transform="translate({x - 10} 34)">{ARROW.format(c=T["acc"])}</g>')
        x -= 32
    if r.get("private"):
        c.text(x, 47, "PRIVATE", "mono", 10.5, "dim", "end", ls=1.4)
        x -= tw("mono", "PRIVATE", 10.5, 1.4) + 22
    if stars:
        c.text(x, 47, str(stars), "mono", 12, "mid", "end")
        sx = x - tw("mono", str(stars), 12) - 15
        c.add(f'<g transform="translate({sx:g} 38)">{STAR.format(c=T["mid"])}</g>')
        x = sx - 22
    if lang:
        c.text(x, 47, lang, "sans", 12.5, "mid", "end")
        c.add(f'<circle cx="{x - tw("sans", lang, 12.5) - 11:g}" cy="42.5" r="4" fill="{lcol}"/>')
    for k, l in enumerate(desc):
        c.text(PAD, 78 + k * 22, l, "sans", 14, "mid")
    c.text(PAD, tag_y, "  ·  ".join(r["tags"]), "mono", 11.5, "dim", ls=0.4)
    c.close()
    return c


def graph(d, delay):
    gx, gy, pitch, cell = PAD + 34, 90, 14, 11
    grid = d["grid"]
    H = gy + 7 * pitch + 58
    c = Card("graph", W, H, f"Contribution graph: {d['active_days']} active days in the last year")
    c.open(delay)
    c.text(PAD, 46, "CONTRIBUTION GRAPH", "mono", 11, "dim", ls=1.4)
    c.text(W - PAD, 46, f"{d['active_days']} active days", "sans", 13, "mid", "end")
    last_m, last_i = None, -9
    for wi, wk in enumerate(grid):
        s = dt.date.fromisoformat(wk["s"])
        if s.month != last_m and wi - last_i >= 3:
            c.text(gx + wi * pitch, 78, s.strftime("%b"), "mono", 10.5, "dim")
            last_m, last_i = s.month, wi
    for row, lab in ((1, "Mon"), (3, "Wed"), (5, "Fri")):
        c.text(PAD, gy + row * pitch + 9.5, lab, "mono", 10, "dim")
    c.close()
    nz = sorted(v for wk in grid for v in wk["c"] if v)
    th = [nz[min(len(nz) - 1, int(len(nz) * q))] for q in (.25, .5, .75)] if nz else []
    shades = [T["cell0"]] + [mix(T["cell0"], T["acc"], t) for t in (.3, .55, .78, 1.0)]
    for wi, wk in enumerate(grid):
        c.open(delay + 80 + wi * 14, "fade")
        for row, v in enumerate(wk["c"]):
            if v is None:
                continue
            lvl = 0 if not v else 1 + sum(v > t for t in th)
            c.add(f'<rect x="{gx + wi * pitch}" y="{gy + row * pitch}" width="{cell}" height="{cell}" rx="2" fill="{shades[lvl]}"/>')
        c.close()
    ly = gy + 7 * pitch + 26
    c.open(delay + 80 + len(grid) * 14, "fade")
    mw = tw("sans", "More", 11)
    c.text(W - PAD, ly + 9, "More", "sans", 11, "dim", "end")
    sx = W - PAD - mw - 8 - (5 * pitch - 3)
    for k, col in enumerate(shades):
        c.rect(sx + k * pitch, ly, cell, cell, col, rx=2)
    c.text(sx - 8, ly + 9, "Less", "sans", 11, "dim", "end")
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
    out["repos"] = {r["slug"]: {"stars": 1 if i == 1 else 0, "language": ["C++", "C"][i], "color": ["#f34b7d", "#555555"][i]}
                    for i, r in enumerate(x for x in profile["repos"] if x.get("slug"))}
    return out


def main():
    profile = json.loads((ROOT / "profile.json").read_text())
    if SAMPLE:
        data = synth(profile)
    elif (ROOT / "contribution_data.json").exists():
        data = json.loads((ROOT / "contribution_data.json").read_text())
    else:
        sys.exit("contribution_data.json is missing: run fetch_contributions.py first (or SAMPLE=1 to preview the layout)")
    cards = [hero(profile)] + [tile(t, i) for i, t in enumerate(profile["tiles"][:3])] + [chart(data)]
    rbase = 900
    cards += [repo(r, i, data.get("repos", {}).get(r.get("slug") or "", {}), rbase) for i, r in enumerate(profile["repos"])]
    cards.append(graph(data, rbase + len(profile["repos"]) * 180 + 120))
    OUT.mkdir(exist_ok=True)
    for old in OUT.glob("*.svg"):
        old.unlink()
    for c in cards:
        (OUT / f"{c.name}.svg").write_text(c.svg(), encoding="utf-8")
        print(f"  cards/{c.name}.svg  {(OUT / f'{c.name}.svg').stat().st_size / 1024:.1f} KB")
    print(f"{len(cards)} cards [{'STATIC' if STATIC else 'animated'}{', SAMPLE data' if SAMPLE else ''}]")


if __name__ == "__main__":
    main()
