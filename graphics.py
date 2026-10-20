"""
graphics.py
Club badges, player graphics and shareable social cards for the Matchday Social Desk.
Everything is drawn in code (no images downloaded), so the visuals are original and
work offline. All clubs and players are fictional.
"""

import base64
import io
import os
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

RED = "#E8202A"
WHITE = "#E9E9E9"
BLACK = "#0A0A0A"
PANEL = "#161616"
MUTED = "#8C8C8C"

ASSETS = Path(__file__).parent / "assets"
# optional headshots: assets/players/ollie_khan.png (or .jpg)
PHOTOS = ASSETS / "players"

TEAM_STYLE = {
    "NWW": {"name": "North West Wonders", "short": "WONDERS", "primary": "#3D85F0", "on_primary": "#FFFFFF"},
    "SEE": {"name": "South East Eagles", "short": "EAGLES", "primary": "#E8559A", "on_primary": "#FFFFFF"},
}


# ------------------------------------------------------------------ helpers
def slug(name):
    return "".join(c if c.isalnum() else "_" for c in str(name).lower()).strip("_")


@lru_cache(maxsize=None)
def photo_path(name):
    """Headshot file for a player, if one has been added to assets/players."""
    for ext in (".png", ".jpg", ".jpeg", ".webp"):
        path = PHOTOS / f"{slug(name)}{ext}"
        if path.exists():
            return path
    return None


@lru_cache(maxsize=None)
def _data_uri(path):
    mime = {".svg": "image/svg+xml", ".png": "image/png", ".webp": "image/webp"}.get(
        Path(path).suffix.lower(), "image/jpeg")
    return f"data:{mime};base64," + base64.b64encode(Path(path).read_bytes()).decode()


# ------------------------------------------------------------------ for the web app
def badge_svg(team_id, size=40):
    """Club crest (assets/crest_<team>.svg, drawn by assets/make_crests.py)."""
    name = TEAM_STYLE.get(team_id, {}).get("name", team_id)
    return (f'<img src="{_data_uri(ASSETS / f"crest_{team_id}.svg")}" width="{size}" height="{size}" '
            f'alt="{name} crest" style="object-fit:contain;display:block">')


def player_svg(team_id, number, size=56, name=None):
    """Player headshot if there is one, otherwise a broadcast-style kit silhouette with the number."""
    style = TEAM_STYLE.get(team_id, TEAM_STYLE["NWW"])
    kit, ink = style["primary"], style["on_primary"]
    photo = photo_path(name) if name else None
    if photo:
        return (f'<img src="{_data_uri(photo)}" width="{size}" height="{size}" alt="{name}" '
                f'style="border-radius:50%;object-fit:cover;border:3px solid {kit};display:block;'
                f'background:{PANEL}">')
    return f"""<svg width="{size}" height="{size}" viewBox="0 0 100 100" role="img"
      aria-label="Player graphic, shirt number {number}">
      <circle cx="50" cy="50" r="49" fill="{PANEL}" stroke="#2A2A2A" stroke-width="2"/>
      <clipPath id="c{team_id}{number}"><circle cx="50" cy="50" r="48"/></clipPath>
      <g clip-path="url(#c{team_id}{number})">
        <circle cx="50" cy="36" r="16" fill="#3A3A3A"/>
        <path d="M14 100 C14 70 30 58 50 58 C70 58 86 70 86 100 Z" fill="{kit}"/>
        <text x="50" y="90" text-anchor="middle" font-family="Barlow Condensed, Impact, sans-serif"
              font-weight="800" font-size="26" fill="{ink}">{number}</text>
      </g>
    </svg>"""


# ------------------------------------------------------------------ PNG (for social posts)
@lru_cache(maxsize=None)
def _font(size, bold=True, display=False):
    candidates = []
    if display:
        candidates += ["C:/Windows/Fonts/impact.ttf",
                       "/System/Library/Fonts/Supplemental/Impact.ttf"]
    candidates += (["C:/Windows/Fonts/arialbd.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
                    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"] if bold else
                   ["C:/Windows/Fonts/arial.ttf", "/System/Library/Fonts/Supplemental/Arial.ttf",
                    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"])
    for path in candidates:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size=size)


def _wrap(draw, text, font, max_width):
    words, lines, line = text.split(), [], ""
    for w in words:
        test = f"{line} {w}".strip()
        if draw.textlength(test, font=font) <= max_width:
            line = test
        else:
            if line:
                lines.append(line)
            line = w
    if line:
        lines.append(line)
    return lines


def _badge_png(img, team_id, x, y, s):
    """Paste the club crest (assets/crest_<team>.png) at (x, y), size s."""
    crest = Image.open(ASSETS / f"crest_{team_id}.png").convert("RGBA")
    crest.thumbnail((s, s), Image.LANCZOS)
    img.paste(crest, (int(x + (s - crest.width) / 2),
              int(y + (s - crest.height) / 2)), crest)


def _player_png(img, team_id, number, x, y, s, name=None):
    style = TEAM_STYLE.get(team_id, TEAM_STYLE["NWW"])
    photo = photo_path(name) if name else None
    if photo:
        face = Image.open(photo).convert("RGB")
        side = min(face.size)
        face = face.crop(((face.width - side) // 2, 0, (face.width +
                         side) // 2, side)).resize((s, s), Image.LANCZOS)
        mask = Image.new("L", (s, s), 0)
        ImageDraw.Draw(mask).ellipse([0, 0, s - 1, s - 1], fill=255)
        ring = Image.new("RGBA", (s + 8, s + 8), (0, 0, 0, 0))
        ImageDraw.Draw(ring).ellipse(
            [0, 0, s + 7, s + 7], fill=style["primary"])
        img.paste(ring, (x - 4, y - 4), ring)
        img.paste(face, (x, y), mask)
        return
    layer = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    k = s / 100
    d.ellipse([0, 0, s - 1, s - 1], fill=PANEL, outline="#2A2A2A", width=2)
    d.ellipse([34 * k, 20 * k, 66 * k, 52 * k], fill="#3A3A3A")
    d.chord([14 * k, 58 * k, 86 * k, 142 * k], 180, 360, fill=style["primary"])
    d.rectangle([14 * k, 100 * k, 86 * k, 100 * k], fill=style["primary"])
    d.text((50 * k, 82 * k), str(number), font=_font(int(26 * k), display=True),
           fill=style["on_primary"], anchor="mm")
    mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, s - 1, s - 1], fill=255)
    img.paste(layer, (x, y), mask)


def social_card_png(card, score, home_id="NWW", away_id="SEE", player_number=None):
    """1080x1350 post (Instagram portrait) in the Social Desk style. Returns PNG bytes."""
    W, H = 1080, 1350
    img = Image.new("RGB", (W, H), BLACK)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 18], fill=RED)

    d.text((70, 70), "MATCHDAY", font=_font(40, display=True), fill="#FFFFFF")
    x = 70 + d.textlength("MATCHDAY ", font=_font(40, display=True))
    d.text((x, 70), "SOCIAL", font=_font(40, display=True), fill=RED)
    x += d.textlength("SOCIAL ", font=_font(40, display=True))
    d.text((x, 70), "DESK", font=_font(40, display=True), fill="#FFFFFF")

    # Scoreline with crests
    d.rounded_rectangle([70, 170, W - 70, 400], radius=10,
                        fill="#111111", outline="#222222", width=2)
    _badge_png(img, home_id, 95, 182, 205)
    _badge_png(img, away_id, W - 300, 182, 205)
    d.rectangle([W / 2 - 150, 225, W / 2 + 150, 345], fill="#FFFFFF")
    d.text((W / 2, 285), f"{score[0]} - {score[1]}",
           font=_font(96, display=True), fill=BLACK, anchor="mm")
    d.text((W / 2, 375), f"{TEAM_STYLE[home_id]['short']}  v  {TEAM_STYLE[away_id]['short']}",
           font=_font(28), fill=MUTED, anchor="mm")

    # Minute pill
    minute = str(card.get("display_minute", ""))
    mw = d.textlength(minute, font=_font(40, display=True)) + 40
    d.rectangle([70, 460, 70 + mw, 520], fill=RED)
    d.text((90, 490), minute, font=_font(
        40, display=True), fill="#FFFFFF", anchor="lm")

    # Headline + body
    y = 560
    for line in _wrap(d, str(card.get("title", "")).upper(), _font(92, display=True), W - 140)[:3]:
        d.text((70, y), line, font=_font(92, display=True), fill="#FFFFFF")
        y += 102
    y += 20
    for line in _wrap(d, str(card.get("body", "")), _font(40, bold=False), W - 140)[:5]:
        d.text((70, y), line, font=_font(40, bold=False), fill="#E6E6E6")
        y += 54

    # Player strip
    if card.get("player"):
        team = card.get("team") or home_id
        d.line([70, H - 230, W - 70, H - 230], fill="#222222", width=2)
        _player_png(img, team, player_number or "",
                    70, H - 200, 120, card["player"])
        d.text((215, H - 165), str(card["player"]).upper(),
               font=_font(48, display=True), fill="#FFFFFF")
        d.text((215, H - 110), TEAM_STYLE.get(team, {}).get("name", ""),
               font=_font(30, bold=False), fill=MUTED)

    d.rectangle([0, H - 12, W, H], fill=RED)
    d.text((W - 70, H - 40), "Synthetic match · fictional clubs", font=_font(22, bold=False),
           fill="#555555", anchor="rs")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


if __name__ == "__main__":
    demo = {"display_minute": "73'", "title": "Khan doubles the lead",
            "body": "Ollie Khan finishes from close range after six trips into the final third in ten "
                    "minutes. The Wonders are firmly back on top.",
            "player": "Ollie Khan", "team": "NWW"}
    os.makedirs("output", exist_ok=True)
    with open("output/social_card_demo.png", "wb") as f:
        f.write(social_card_png(demo, (2, 0), player_number=11))
    print("Saved output/social_card_demo.png")
