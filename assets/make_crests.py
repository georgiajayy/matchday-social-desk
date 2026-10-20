"""
make_crests.py
Draws the two (fictional) club crests as SVG files in this folder.
Run:  python assets/make_crests.py
"""

import math
from pathlib import Path

HERE = Path(__file__).parent
FONT = "'Barlow Condensed','Arial Narrow',Arial,sans-serif"


def star(cx, cy, r_out, r_in, points=5, rot=-90):
    pts = []
    for i in range(points * 2):
        r = r_out if i % 2 == 0 else r_in
        a = math.radians(rot + i * 180 / points)
        pts.append(f"{cx + r * math.cos(a):.2f},{cy + r * math.sin(a):.2f}")
    return " ".join(pts)


def wonders():
    """North West Wonders: black heater shield with black and blue vertical stripes and blue trim, a compass rose whose longest point aims north-west, and a ribbon banner."""
    black, blue, blue_dark, blue_light = "#0B0B0D", "#1F6FE0", "#123F86", "#5AA2FF"
    TRIM, TRIM_DARK = blue, blue_dark
    shield = "M20 18 H200 V118 C200 178 160 214 110 236 C60 214 20 178 20 118 Z"
    inner = "M31 29 H189 V118 C189 170 154 202 110 222 C66 202 31 170 31 118 Z"
    # compass rose: 8 points, north-west point longest
    rose = []
    cx, cy = 110, 128
    for i in range(8):
        ang = -90 + i * 45
        nw = (ang % 360) == 225
        length = 66 if nw else (46 if i % 2 == 0 else 30)
        width = 10 if i % 2 == 0 else 7
        a = math.radians(ang)
        tip = (cx + length * math.cos(a), cy + length * math.sin(a))
        left = (cx + width * math.cos(a - math.pi / 2), cy + width * math.sin(a - math.pi / 2))
        right = (cx + width * math.cos(a + math.pi / 2), cy + width * math.sin(a + math.pi / 2))
        fill_l, fill_r = (blue_light, blue) if nw else ("#FFFFFF", "#AEB8C8")
        rose.append(f'<polygon points="{cx},{cy} {left[0]:.1f},{left[1]:.1f} {tip[0]:.1f},{tip[1]:.1f}" fill="{fill_l}"/>')
        rose.append(f'<polygon points="{cx},{cy} {right[0]:.1f},{right[1]:.1f} {tip[0]:.1f},{tip[1]:.1f}" fill="{fill_r}"/>')
    stars = "".join(f'<polygon points="{star(x, 58, 9, 3.8)}" fill="#FFFFFF"/>' for x in (70, 110, 150))
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 220 300" role="img" aria-label="North West Wonders crest">
  <defs>
    <clipPath id="nww-in"><path d="{inner}"/></clipPath>
    <linearGradient id="nww-sheen" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#ffffff" stop-opacity=".16"/><stop offset=".5" stop-color="#ffffff" stop-opacity="0"/>
    </linearGradient>
  </defs>
  <path d="{shield}" fill="{TRIM}"/>
  <path d="{shield}" fill="none" stroke="{TRIM_DARK}" stroke-width="2"/>
  <path d="{inner}" fill="{black}"/>
  <g clip-path="url(#nww-in)">
    {''.join(f'<rect x="{x}" y="18" width="11" height="230" fill="{blue_dark}"/>' for x in range(31, 200, 22))}
    <circle cx="{cx}" cy="{cy}" r="40" fill="{black}"/>
    <circle cx="{cx}" cy="{cy}" r="38" fill="none" stroke="{TRIM}" stroke-width="2"/>
    <circle cx="{cx}" cy="{cy}" r="31" fill="none" stroke="#ffffff" stroke-opacity=".35" stroke-width="1"/>
    {''.join(rose)}
    <circle cx="{cx}" cy="{cy}" r="5" fill="{TRIM}" stroke="{black}" stroke-width="2"/>
    <path d="{inner}" fill="url(#nww-sheen)"/>
  </g>
  <path d="{inner}" fill="none" stroke="#ffffff" stroke-width="1.5" stroke-opacity=".8"/>
  <!-- ribbon banner -->
  <path d="M6 236 L28 226 L28 262 L6 272 L16 254 Z" fill="{TRIM_DARK}"/>
  <path d="M214 236 L192 226 L192 262 L214 272 L204 254 Z" fill="{TRIM_DARK}"/>
  <path d="M24 222 Q110 206 196 222 L196 260 Q110 244 24 260 Z" fill="{black}" stroke="{blue}" stroke-width="3"/>
  <path id="nww-arc" d="M36 248 Q110 232 184 248" fill="none"/>
  <text font-family="{FONT}" font-weight="800" font-size="21" letter-spacing="1.5" fill="#ffffff">
    <textPath href="#nww-arc" startOffset="50%" text-anchor="middle" textLength="142" lengthAdjust="spacingAndGlyphs">NORTH WEST WONDERS</textPath>
  </text>
</svg>"""


def eagles():
    """South East Eagles: white roundel with pink lettering and rings, and a white spread eagle
    on a pink field."""
    pink, pink_dark, pink_pale, white = "#E5468C", "#A8275F", "#FFD3E4", "#FFFFFF"
    cx, cy = 150, 150
    feathers = []
    for side in (-1, 1):
        for i in range(7):
            deg = -38 + i * 13                       # fan from up-and-out to down-and-out
            ang = math.radians(deg if side == 1 else 180 - deg)
            length = 66 - abs(i - 2) * 5
            sx, sy = cx + side * 12, cy - 6 + i * 2.2
            tx, ty = sx + length * math.cos(ang), sy + length * math.sin(ang)
            nx, ny = -math.sin(ang) * 6.5, math.cos(ang) * 6.5
            colour = white if i % 2 == 0 else "#FCE6EF"
            feathers.append(
                f'<path d="M{sx:.1f},{sy:.1f} L{sx + nx:.1f},{sy + ny:.1f} '
                f'Q{(sx + tx) / 2 + nx * 1.3:.1f},{(sy + ty) / 2 + ny * 1.3:.1f} {tx:.1f},{ty:.1f} '
                f'Q{(sx + tx) / 2 - nx * .6:.1f},{(sy + ty) / 2 - ny * .6:.1f} {sx - nx:.1f},{sy - ny:.1f} Z" '
                f'fill="{colour}" stroke="{pink_dark}" stroke-width="1.2"/>')
    feathers.reverse()                               # draw lower feathers first
    tail = "".join(
        f'<path d="M{cx},{cy + 22} L{cx + dx - 6},{cy + 62} L{cx + dx + 6},{cy + 62} Z" fill="{white}" '
        f'stroke="{pink_dark}" stroke-width="1.2"/>' for dx in (-14, 0, 14))
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 300 300" role="img" aria-label="South East Eagles crest">
  <defs>
    <path id="see-top" d="M48 150 A102 102 0 0 1 252 150"/>
    <path id="see-bottom" d="M40 150 A110 110 0 0 0 260 150"/>
  </defs>
  <circle cx="150" cy="150" r="146" fill="{pink}"/>
  <circle cx="150" cy="150" r="140" fill="{white}"/>
  <circle cx="150" cy="150" r="134" fill="none" stroke="{pink}" stroke-width="2"/>
  <text font-family="{FONT}" font-weight="800" font-size="30" letter-spacing="4" fill="{pink}">
    <textPath href="#see-top" startOffset="50%" text-anchor="middle" textLength="250" lengthAdjust="spacingAndGlyphs">SOUTH EAST EAGLES</textPath>
  </text>
  <text font-family="{FONT}" font-weight="700" font-size="20" letter-spacing="5" fill="{pink_dark}">
    <textPath href="#see-bottom" startOffset="50%" text-anchor="middle" textLength="190" lengthAdjust="spacingAndGlyphs">FOOTBALL CLUB</textPath>
  </text>
  <circle cx="150" cy="150" r="88" fill="{pink}" stroke="{pink_dark}" stroke-width="5"/>
  <circle cx="150" cy="150" r="80" fill="none" stroke="{white}" stroke-opacity=".7" stroke-width="1.5"/>
  {tail}
  {''.join(feathers)}
  <ellipse cx="{cx}" cy="{cy + 6}" rx="15" ry="26" fill="{white}" stroke="{pink_dark}" stroke-width="1.5"/>
  <path d="M{cx - 12},{cy - 14} C{cx - 16},{cy - 34} {cx - 4},{cy - 46} {cx + 8},{cy - 42}
           C{cx + 16},{cy - 40} {cx + 20},{cy - 34} {cx + 26},{cy - 30}
           C{cx + 22},{cy - 26} {cx + 18},{cy - 25} {cx + 14},{cy - 26}
           C{cx + 12},{cy - 18} {cx + 8},{cy - 12} {cx + 10},{cy - 6} Z"
        fill="{white}" stroke="{pink_dark}" stroke-width="1.5"/>
  <path d="M{cx + 14},{cy - 36} C{cx + 22},{cy - 36} {cx + 30},{cy - 31} {cx + 28},{cy - 24}
           C{cx + 26},{cy - 27} {cx + 22},{cy - 28} {cx + 15},{cy - 28} Z" fill="{pink_pale}" stroke="{pink_dark}" stroke-width="1"/>
  <circle cx="{cx + 6}" cy="{cy - 34}" r="2.6" fill="{pink_dark}"/>
  <path d="M{cx - 10},{cy + 30} l-6,8 M{cx - 10},{cy + 30} l0,9 M{cx + 10},{cy + 30} l6,8 M{cx + 10},{cy + 30} l0,9"
        stroke="{pink_pale}" stroke-width="3" stroke-linecap="round"/>
  <polygon points="{star(39, 150, 9, 3.8)}" fill="{pink}"/>
  <polygon points="{star(261, 150, 9, 3.8)}" fill="{pink}"/>
</svg>"""


if __name__ == "__main__":
    (HERE / "crest_NWW.svg").write_text(wonders(), encoding="utf-8")
    (HERE / "crest_SEE.svg").write_text(eagles(), encoding="utf-8")
    print("Saved assets/crest_NWW.svg and assets/crest_SEE.svg")
