"""
app.py - Matchday Social Desk (live studio interface)

Replays the synthetic match like a live broadcast feed. Before kick-off, the AI agent
team "warms up" by preparing every key moment in parallel, so the match then plays
smoothly: a broadcast score bug and captions, a spoken AI commentator, a momentum
timeline, a social clip pack for each big moment, and "Ask the pundit" at half-time
and full-time.

Run:  streamlit run app.py
"""

import hashlib
import html
import json
import os
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import streamlit as st

from agents import FAN_MODES, PERSONALITIES, FanProfile, Orchestrator, tidy
from graphics import badge_svg, player_svg, social_card_png
from pundit import PunditAgent
from stats_engine import MatchState, load_match
from voice import estimate_seconds, speak, voice_for

CACHE_FILE = Path(__file__).parent / "output" / "agent_cache.json"

# Broadcaster brand: black, white and red. Red only ever means "live" or "event".
INK, PANEL, LINE, WHITE, MUTED, RED = "#000000", "#141519", "#2A2B31", "#FFFFFF", "#8F939B", "#E10600"
# Club colours come from the crests (checked for colour-blind separation on dark backgrounds).
HOME_COLOUR, AWAY_COLOUR = "#3D85F0", "#E8559A"

LANGUAGES = ["English", "Spanish", "French", "Portuguese", "Arabic", "Hindi", "Japanese", "Yoruba"]
MODE_LABELS = {"casual": "Casual fan", "analyst": "Analyst", "social": "Social feed"}
WARM_UP_WORKERS = 4       # how many moments the agents prepare at the same time

st.set_page_config(page_title="Matchday Social Desk", page_icon="⚽", layout="wide")

st.markdown(f"""
<style>
  @import url('https://fonts.googleapis.com/css2?family=Archivo:wdth,wght@62..125,300..900&display=swap');
  :root {{ --ink:{INK}; --panel:{PANEL}; --line:{LINE}; --white:{WHITE}; --muted:{MUTED}; --red:{RED};
           --home:{HOME_COLOUR}; --away:{AWAY_COLOUR}; }}
  html, body, .stApp, .stMarkdown, button, input, textarea {{ font-family:'Archivo', sans-serif !important; }}
  .stApp {{ background:var(--ink); color:var(--white); }}
  [data-testid="stSidebar"] {{ background:var(--panel); border-right:1px solid var(--line); }}
  [data-testid="stSidebar"] h2 {{ font-stretch:112%; font-weight:800; letter-spacing:-.01em; }}
  .block-container {{ padding-top:1.2rem; max-width:1320px; }}
  .wide {{ font-stretch:125%; }}

  /* masthead */
  .masthead {{ display:flex; justify-content:space-between; align-items:flex-end; gap:24px;
      padding-bottom:14px; margin-bottom:16px; border-bottom:1px solid var(--line); }}
  .wordmark {{ display:flex; align-items:center; gap:12px; font-stretch:125%; font-weight:850;
      font-size:1.7rem; letter-spacing:-.02em; line-height:1; margin:0; }}
  .wordmark i {{ display:block; width:10px; height:30px; background:var(--red);
      clip-path:polygon(0 0,100% 0,60% 100%,0 100%); }}
  .fixture {{ text-align:right; line-height:1.3; }}
  .fixture b {{ font-weight:700; font-size:1rem; }}
  .fixture span {{ color:var(--muted); font-size:.85rem; display:block; }}

  /* the broadcast feed */
  .feed {{ position:relative; border:1px solid var(--line); background:#0B0D0B; overflow:hidden; }}
  .bug {{ position:absolute; top:3.5%; left:2.5%; display:flex; align-items:stretch; height:44px;
      box-shadow:0 6px 18px rgba(0,0,0,.5); }}
  .bug-team {{ display:flex; align-items:center; gap:8px; background:var(--white); color:var(--ink);
      padding:0 12px; font-stretch:118%; font-weight:800; font-size:1.02rem; letter-spacing:.02em; }}
  .bug-team .kit {{ width:4px; align-self:stretch; margin:8px 0; }}
  .bug-score {{ display:flex; align-items:center; background:var(--ink); color:var(--white);
      padding:0 14px; font-stretch:125%; font-weight:850; font-size:1.35rem;
      font-variant-numeric:tabular-nums; }}
  .bug-clock {{ display:flex; align-items:center; background:var(--red); color:var(--white);
      padding:0 14px 0 12px; font-weight:800; font-variant-numeric:tabular-nums;
      clip-path:polygon(0 0,100% 0,calc(100% - 10px) 100%,0 100%); padding-right:20px; }}
  .tag {{ position:absolute; top:3.5%; right:2.5%; background:rgba(0,0,0,.72); color:var(--white);
      padding:6px 12px; font-weight:700; font-size:.82rem; display:flex; gap:8px; align-items:center; }}
  .tag .dot {{ width:8px; height:8px; border-radius:50%; background:var(--red); }}

  .lower-third {{ position:absolute; left:2.5%; right:2.5%; bottom:4%; display:flex; gap:16px;
      align-items:center; background:rgba(0,0,0,.9); border-left:6px solid var(--team);
      padding:14px 20px 14px 16px; clip-path:polygon(0 0,calc(100% - 22px) 0,100% 22px,100% 100%,0 100%); }}
  .lt-new {{ animation:lt-in .45s cubic-bezier(.2,.8,.2,1) both; }}
  @keyframes lt-in {{ from {{ transform:translateY(18px); opacity:0; }} to {{ transform:none; opacity:1; }} }}
  @media (prefers-reduced-motion: reduce) {{ .lt-new {{ animation:none; }} }}
  .lt-text {{ min-width:0; }}
  .lt-meta {{ display:flex; gap:10px; align-items:center; color:var(--muted); font-size:.8rem; }}
  .lt-min {{ background:var(--red); color:var(--white); font-weight:800; padding:2px 8px;
      font-variant-numeric:tabular-nums; font-size:.85rem; }}
  .lt-title {{ font-stretch:112%; font-weight:850; font-size:1.55rem; line-height:1.08; margin:6px 0 4px;
      letter-spacing:-.01em; }}
  .lt-body {{ font-size:1rem; line-height:1.42; color:#E4E5E7; max-width:68ch; }}
  .chip {{ display:inline-block; margin-top:8px; border:1px solid #3A3B42; padding:2px 9px; font-size:.8rem;
      color:var(--white); }}
  .chip b {{ font-variant-numeric:tabular-nums; }}

  /* section headings: sentence case, weight not caps */
  .h {{ font-stretch:112%; font-weight:800; font-size:1.08rem; letter-spacing:-.01em; margin:2px 0 10px; }}
  .h small {{ color:var(--muted); font-weight:500; font-size:.82rem; font-stretch:100%; margin-left:8px; }}

  /* stats */
  .stats-head {{ display:flex; justify-content:space-between; align-items:center; margin-bottom:6px; }}
  .stats-head div {{ display:flex; align-items:center; gap:8px; font-stretch:118%; font-weight:800; }}
  .stat-row {{ margin:9px 0 13px; }}
  .stat-label {{ display:flex; justify-content:space-between; align-items:baseline; font-size:.86rem;
      color:#BFC2C8; }}
  .stat-label b {{ color:var(--white); font-stretch:118%; font-weight:800; font-size:1.02rem;
      font-variant-numeric:tabular-nums; }}
  .bar {{ display:flex; height:5px; gap:3px; margin-top:6px; }}
  .meter {{ height:5px; background:#24252B; margin-top:6px; }}
  .meter span {{ display:block; height:100%; background:var(--white); }}

  .pf-head {{ display:flex; gap:12px; align-items:center; margin:2px 0 10px; }}
  .pf-head b {{ font-stretch:112%; font-weight:800; font-size:1.05rem; }}
  .pf-head span {{ color:var(--muted); font-size:.85rem; }}
  .pf-row {{ display:flex; justify-content:space-between; font-size:.88rem; color:#BFC2C8; margin:5px 0; }}
  .pf-row b {{ color:var(--white); font-variant-numeric:tabular-nums; }}

  /* key-moment timeline */
  .moment {{ display:grid; grid-template-columns:52px 4px 1fr; gap:12px; padding:10px 0;
      border-bottom:1px solid var(--line); }}
  .moment .m {{ font-stretch:118%; font-weight:800; font-variant-numeric:tabular-nums; color:var(--white); }}
  .moment .k {{ background:var(--team); }}
  .moment b {{ display:block; font-weight:750; margin-bottom:2px; }}
  .moment p {{ margin:0; color:#C9CBD0; font-size:.92rem; line-height:1.4; }}
  .moment .v {{ color:var(--muted); font-size:.78rem; margin-top:4px; }}
  .empty {{ color:var(--muted); }}

  .panel {{ background:var(--panel); border:1px solid var(--line); padding:18px 22px; margin-top:18px; }}
  .panel h3 {{ font-stretch:118%; font-weight:850; letter-spacing:-.015em; margin:2px 0 10px; }}
  .pack-label {{ color:var(--muted); font-size:.8rem; margin-top:12px; }}
  .pack-label:first-child {{ margin-top:0; }}
  .pack-hook {{ font-stretch:118%; font-size:1.5rem; font-weight:850; line-height:1.1; }}

  /* Streamlit controls in the house style */
  .stButton button, .stDownloadButton button {{ border-radius:2px; font-weight:700; }}
  .stButton button[kind="primary"] {{ background:var(--red); border-color:var(--red); color:var(--white); }}
  .stButton button[kind="primary"]:hover {{ background:#B80500; border-color:#B80500; }}
  .stTabs [aria-selected="true"] {{ color:var(--white) !important; }}
  .stTabs [data-baseweb="tab-highlight"] {{ background:var(--red) !important; }}
  :focus-visible {{ outline:2px solid var(--white) !important; outline-offset:2px; }}
</style>
""", unsafe_allow_html=True)


# ------------------------------------------------------------------ cache (fast, cheap demo re-runs)
def load_cache():
    try:
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_cache(cache):
    CACHE_FILE.parent.mkdir(exist_ok=True)
    CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")


def cache_key(profile, item):
    raw = json.dumps([profile.__dict__, item], sort_keys=True)
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


# ------------------------------------------------------------------ data
META, EVENTS = load_match()
HOME_ID, AWAY_ID = META["home"]["id"], META["away"]["id"]
TEAM_NAMES = {HOME_ID: META["home"]["name"], AWAY_ID: META["away"]["name"]}
SHIRTS = {p["name"]: (META[side]["id"], p["number"]) for side in ("home", "away")
          for p in META[side]["players"]}
HALF_TIME_INDEX = next(i for i, e in enumerate(EVENTS) if e["type"] == "half_time") + 1
_seen = {"card": None}    # which caption is on screen, so it only animates in once


def team_colour(team_id):
    return HOME_COLOUR if team_id == HOME_ID else AWAY_COLOUR if team_id == AWAY_ID else MUTED


# ------------------------------------------------------------------ drawing helpers
def esc(text):
    return html.escape(str(text or ""))


def pitch_svg(trail=None, colour=WHITE):
    """Top-down pitch with the ball's recent path fading out behind it."""
    stripes = "".join(f'<rect x="{i * 10.5}" y="0" width="10.5" height="68" fill="{c}"/>'
                      for i, c in enumerate(["#0E120F", "#111612"] * 5))
    line = 'stroke="rgba(255,255,255,.32)" stroke-width=".28" fill="none"'
    marks = ""
    if trail:
        pts = list(trail)
        for i, (bx, by) in enumerate(pts[:-1]):
            marks += (f'<circle cx="{bx}" cy="{by}" r=".55" fill="#fff" '
                      f'opacity="{0.08 + 0.5 * i / max(len(pts) - 1, 1):.2f}"/>')
        if len(pts) > 1:
            path = " ".join(f"{x},{y}" for x, y in pts)
            marks = (f'<polyline points="{path}" fill="none" stroke="{colour}" stroke-opacity=".45" '
                     f'stroke-width=".35" stroke-linejoin="round"/>') + marks
        bx, by = pts[-1]
        marks += (f'<circle cx="{bx}" cy="{by}" r="2.4" fill="{colour}" opacity=".28"/>'
                  f'<circle cx="{bx}" cy="{by}" r="1.05" fill="#fff"/>')
    return f"""
    <svg viewBox="0 0 105 68" width="100%" style="display:block" role="img"
         aria-label="Pitch showing the ball's recent path">
      {stripes}
      <rect x="1" y="1" width="103" height="66" {line}/>
      <line x1="52.5" y1="1" x2="52.5" y2="67" {line}/>
      <circle cx="52.5" cy="34" r="9.15" {line}/><circle cx="52.5" cy="34" r=".4" fill="rgba(255,255,255,.4)"/>
      <rect x="1" y="13.85" width="16.5" height="40.3" {line}/>
      <rect x="87.5" y="13.85" width="16.5" height="40.3" {line}/>
      <rect x="1" y="24.85" width="5.5" height="18.3" {line}/>
      <rect x="98.5" y="24.85" width="5.5" height="18.3" {line}/>
      <path d="M17.5 26.7 A9.15 9.15 0 0 1 17.5 41.3" {line}/><path d="M87.5 26.7 A9.15 9.15 0 0 0 87.5 41.3" {line}/>
      {marks}
    </svg>"""


def score_bug(score, clock):
    def team(tid, flip=False):
        kit = f'<span class="kit" style="background:{team_colour(tid)}"></span>'
        crest = badge_svg(tid, 30)
        parts = [crest, esc(tid), kit] if not flip else [kit, esc(tid), crest]
        return f'<div class="bug-team">{"".join(parts)}</div>'
    return (f'<div class="bug" aria-label="Score">{team(HOME_ID)}'
            f'<div class="bug-score">{score[0]}&ensp;{score[1]}</div>{team(AWAY_ID, True)}'
            f'<div class="bug-clock">{esc(clock)}</div></div>')


def status_tag(phase):
    if phase == "live":
        return '<div class="tag"><span class="dot"></span>Live</div>'
    label = {"half": "Half-time", "full": "Full-time"}.get(phase, "Pre-match")
    return f'<div class="tag">{label}</div>'


def lower_third(card):
    if not card:
        return ""
    new = card["card_id"] != _seen["card"]
    _seen["card"] = card["card_id"]
    check = "Fact-checked against the match data" if card["verified"] else "Safe caption from the match data"
    chip = ""
    if card.get("stat_chip") and card["stat_chip"].get("value"):
        chip = (f'<span class="chip">{esc(card["stat_chip"].get("label"))} '
                f'<b>{esc(card["stat_chip"].get("value"))}</b></span>')
    avatar = ""
    if card.get("player") in SHIRTS:
        team, number = SHIRTS[card["player"]]
        avatar = player_svg(team, number, 72, card["player"])
    return f"""
    <div class="lower-third{' lt-new' if new else ''}" style="--team:{team_colour(card['team'])}" dir="auto">
      {avatar}<div class="lt-text">
      <div class="lt-meta"><span class="lt-min">{esc(card["display_minute"])}</span><span>{check}</span></div>
      <div class="lt-title">{esc(card["title"])}</div>
      <div class="lt-body">{esc(card["body"])}</div>
      {chip}</div>
    </div>"""


MOMENT_GLYPH = {"goal": "goal", "big_save": "save", "big_chance_missed": "chance"}


def timeline_svg(history, cards):
    """The match on one line: who was on top each minute, with every key moment pinned to it.
    Bars above the line = home on top, below = away (share of attacking threat, last 10 minutes)."""
    w, h, mid, top = 1000, 190, 104, 30
    step = w / 96
    x = lambda minute: 6 + minute * (w - 12) / 96
    bars = []
    for minute, share in history:
        if minute < 3:
            continue
        diff = share - 50
        bh = abs(diff) / 50 * 58
        if bh < 1:
            continue
        colour = HOME_COLOUR if diff > 0 else AWAY_COLOUR
        y = mid - bh if diff > 0 else mid + 2
        leader = TEAM_NAMES[HOME_ID] if diff > 0 else TEAM_NAMES[AWAY_ID]
        tip = f"{minute + 1}': {leader} on top, {max(share, 100 - share)}% of the threat"
        bars.append(f'<rect x="{x(minute):.1f}" y="{y:.1f}" width="{step - 3:.1f}" height="{bh:.1f}" '
                    f'rx="2" fill="{colour}"><title>{esc(tip)}</title></rect>')
    pins = []
    for c in cards:
        minute = c["show_at_clock_s"] / 60
        px = x(minute)
        tip = f"{c['display_minute']} {c['title']}"
        if c["moment_type"] == "goal":
            scorer = (c.get("player") or "").split(" ")[-1]
            pins.append(f'<g><title>{esc(tip)}</title>'
                        f'<line x1="{px:.1f}" y1="{top + 12}" x2="{px:.1f}" y2="{mid}" stroke="#fff" stroke-width="1.2"/>'
                        f'<circle cx="{px:.1f}" cy="{top + 6}" r="6" fill="#fff" stroke="{team_colour(c["team"])}" stroke-width="3"/>'
                        f'<text x="{px + 11:.1f}" y="{top + 10}" fill="#fff" font-size="13" font-weight="700">'
                        f'{esc(scorer)} {esc(c["display_minute"])}</text></g>')
        else:
            pins.append(f'<g><title>{esc(tip)}</title>'
                        f'<line x1="{px:.1f}" y1="{mid - 8}" x2="{px:.1f}" y2="{mid + 8}" stroke="{RED}" stroke-width="3"/></g>')
    ticks = "".join(f'<text x="{x(m):.1f}" y="{h - 4}" fill="{MUTED}" font-size="12" text-anchor="middle">{m}\'</text>'
                    for m in (0, 15, 30, 45, 60, 75, 90))
    return f"""
    <svg viewBox="0 0 {w} {h}" width="100%" role="img"
         aria-label="Match timeline: which team was on top each minute, with key moments marked">
      <rect x="{x(0):.1f}" y="{mid - 62}" width="{x(45) - x(0):.1f}" height="124" fill="#0A0B0D"/>
      <line x1="0" y1="{mid}" x2="{w}" y2="{mid}" stroke="#3A3B42"/>
      <line x1="{x(45):.1f}" y1="{top}" x2="{x(45):.1f}" y2="{h - 20}" stroke="#3A3B42" stroke-dasharray="3 4"/>
      <text x="{w - 6}" y="{mid - 50}" fill="{HOME_COLOUR}" font-size="12" font-weight="700" text-anchor="end">{esc(TEAM_NAMES[HOME_ID])} on top</text>
      <text x="{w - 6}" y="{mid + 60}" fill="{AWAY_COLOUR}" font-size="12" font-weight="700" text-anchor="end">{esc(TEAM_NAMES[AWAY_ID])} on top</text>
      {''.join(bars)}{''.join(pins)}{ticks}
    </svg>"""


def stat_bar(label, home_val, away_val, fmt="{}"):
    total = (home_val or 0) + (away_val or 0)
    hp = 50 if not total else max(3, min(97, 100 * (home_val or 0) / total))
    return f"""
    <div class="stat-row">
      <div class="stat-label"><b>{fmt.format(home_val)}</b><span>{esc(label)}</span><b>{fmt.format(away_val)}</b></div>
      <div class="bar"><span style="width:{hp}%;background:{HOME_COLOUR}"></span>
        <span style="width:{100 - hp}%;background:{AWAY_COLOUR}"></span></div>
    </div>"""


def stats_panel(snap, mode):
    h, a = snap["teams"][HOME_ID], snap["teams"][AWAY_ID]
    rows = [("Possession", h["possession_pct"], a["possession_pct"], "{}%"),
            ("Shots", h["shots"], a["shots"], "{}"),
            ("On target", h["shots_on_target"], a["shots_on_target"], "{}")]
    if mode == "analyst":
        rows += [("Expected goals (xG)", h["xg"], a["xg"], "{:.2f}"),
                 ("Pass accuracy", h["pass_accuracy_pct"] or 0, a["pass_accuracy_pct"] or 0, "{}%"),
                 ("Final-third entries", h["final_third_entries"], a["final_third_entries"], "{}"),
                 ("Hard passes completed", h["hard_passes_complete"], a["hard_passes_complete"], "{}"),
                 ("Pressures", h["pressures"], a["pressures"], "{}")]
    head = (f'<div class="stats-head"><div>{badge_svg(HOME_ID, 24)}<span style="color:{HOME_COLOUR}">'
            f'{HOME_ID}</span></div><div><span style="color:{AWAY_COLOUR}">{AWAY_ID}</span>'
            f'{badge_svg(AWAY_ID, 24)}</div></div>')
    out = '<div class="h">Match stats</div>' + head + "".join(stat_bar(*r) for r in rows)
    if mode == "analyst" and snap.get("chaos_index") is not None:
        c = snap["chaos_index"]
        feel = "calm and controlled" if c < 35 else "scrappy" if c < 65 else "end-to-end chaos"
        out += (f'<div class="stat-row"><div class="stat-label"><span>Last 5 minutes: {feel}</span>'
                f'<b>{c}</b></div><div class="meter"><span style="width:{max(c, 2)}%"></span></div></div>')
    return out


def moments_html(cards):
    rows = []
    for c in reversed(cards):
        check = "Fact-checked" if c["verified"] else "Safe caption"
        rows.append(f"""<div class="moment" style="--team:{team_colour(c['team'])}" dir="auto">
            <div class="m">{esc(c['display_minute'])}</div><div class="k"></div>
            <div><b>{esc(c['title'])}</b><p>{esc(c['body'])}</p><div class="v">{check}</div></div></div>""")
    return "".join(rows) or '<p class="empty">Key moments appear here as they happen.</p>'


def player_card(state, name):
    team, number = SHIRTS.get(name, (HOME_ID, ""))
    head = (f'<div class="h">Player focus</div><div class="pf-head">{player_svg(team, number, 56, name)}'
            f'<div><b>{esc(name)}</b><br><span>No. {number}, {esc(TEAM_NAMES.get(team, ""))}</span></div></div>')
    p = state.player_report(name)
    if not p:
        return head + '<p class="empty">Waiting for his first touch.</p>'
    items = [("Passes completed", f"{p['passes_complete']} of {p['passes']}"),
             ("Pass accuracy", f"{p['pass_accuracy_pct']}%" if p["pass_accuracy_pct"] is not None else "–"),
             ("Shots", p["shots"]), ("Goals", p["goals"]),
             ("Tackles and interceptions", p["tackles"] + p["interceptions"]),
             ("Pressures", p["pressures"])]
    return head + "".join(f'<div class="pf-row"><span>{k}</span><b>{v}</b></div>' for k, v in items)


# ------------------------------------------------------------------ sidebar: the viewer
with st.sidebar:
    st.header("Your matchday")
    mode = st.radio("Viewing mode", list(FAN_MODES), format_func=MODE_LABELS.get,
                    index=list(FAN_MODES).index("casual"))
    personality = "standard"
    language = st.selectbox("Language", LANGUAGES)
    team_choice = st.selectbox("Favourite club", ["None"] + [TEAM_NAMES[HOME_ID], TEAM_NAMES[AWAY_ID]])
    player = st.selectbox("Favourite player", ["None"] + sorted(SHIRTS))
    focus = st.toggle("Player focus mode", value=False, disabled=player == "None",
                      help="Follow one player all match. You still see goals.")
    st.divider()
    voice_on = st.toggle("Voice commentary", value=False,
                         disabled=voice_for(language, personality) is None,
                         help="An AI commentator reads each caption aloud.")
    social_on = st.toggle("Social clip pack", value=True,
                          help="Ready-to-post content for every big moment.")
    pause_ht = st.toggle("Pause at half-time", value=True,
                         help="Stop at the break so you can ask the pundit questions.")
    speed = st.slider("Replay speed (match minutes per second)", 1, 10, 4)
    use_cache = st.toggle("Reuse saved commentary", value=True,
                          help="Instant, free re-runs for the same viewer settings.")
    kick_off = st.button("Kick off", type="primary", use_container_width=True)
    st.caption("Synthetic data only: all clubs, players and events are fictional.")
    if os.getenv("MOCK_AI") == "1":
        st.warning("Offline mode: AI agents are mocked.")

fav_team = next((t for t, n in TEAM_NAMES.items() if n == team_choice), None)
profile = FanProfile(mode=mode, language=language, favourite_team=fav_team,
                     favourite_player=None if player == "None" else player,
                     player_focus=focus and player != "None", personality=personality)
voice_on = voice_on and voice_for(language, personality) is not None

# ------------------------------------------------------------------ layout
st.markdown(f"""<div class="masthead">
  <p class="wordmark"><i></i>Matchday Social Desk</p>
  <div class="fixture"><b>{esc(TEAM_NAMES[HOME_ID])} v {esc(TEAM_NAMES[AWAY_ID])}</b>
    <span>{esc(META.get('venue', '').replace(' (fictional)', ''))}, synthetic match data</span></div>
</div>""", unsafe_allow_html=True)
left, right = st.columns([5, 3], gap="large")
with left:
    screen = st.empty()
    audio_box = st.empty()
    status = st.empty()
    st.markdown('<div class="h" style="margin-top:18px">The match so far<small>who was on top, minute by minute'
                '</small></div>', unsafe_allow_html=True)
    timeline_box = st.empty()
with right:
    stats_box = st.empty()
    player_box = st.empty()
    st.markdown('<div class="h" style="margin-top:14px">Key moments</div>', unsafe_allow_html=True)
    moments_box = st.empty()
after_box = st.container()


def render(state, cards, momentum, trail=None, ball_team=None, view_profile=None, phase="live"):
    view_profile = view_profile or profile
    snap = state.snapshot()
    score = (snap["score"]["home"], snap["score"]["away"])
    screen.markdown(f'<div class="feed">{pitch_svg(trail, team_colour(ball_team))}'
                    f'{score_bug(score, snap["display_minute"])}{status_tag(phase)}'
                    f'{lower_third(cards[-1] if cards else None)}</div>', unsafe_allow_html=True)
    timeline_box.markdown(timeline_svg(momentum, cards), unsafe_allow_html=True)
    stats_box.markdown(stats_panel(snap, view_profile.mode), unsafe_allow_html=True)
    if view_profile.favourite_player:
        player_box.markdown(player_card(state, view_profile.favourite_player), unsafe_allow_html=True)
    moments_box.markdown(moments_html(cards), unsafe_allow_html=True)


# ------------------------------------------------------------------ warm-up: agents prepare in parallel
def warm_up():
    """Find every moment this viewer will see, then run the agent team on all of them at once.
    Each moment is still analysed with the stats as they stood at that minute (no spoilers)."""
    orchestrator = Orchestrator(profile)
    cache = load_cache() if use_cache else {}
    state, jobs = MatchState(META), []
    for e in EVENTS:
        for m in state.ingest(e):
            if orchestrator.is_relevant(m):
                jobs.append((m, state.snapshot()))

    cards, packs = {}, {}
    progress = status.progress(0.0, text="The agents are preparing the match")
    done, total = 0, len(jobs) + 1

    def make_card(m, snap):
        key = cache_key(profile, ["moment", m["moment_id"], m["type"], m["clock_s"]])
        if key in cache:
            orchestrator.log.append(("Cache", m["moment_id"], "reused saved commentary"))
            card = cache[key]
        else:
            card = orchestrator.process_moment(m, snap, state.names)
            cache[key] = card
        pack = None
        if social_on and m["importance"] >= 2:
            pkey = cache_key(profile, ["social", m["moment_id"], card["title"]])
            if pkey in cache:
                pack = cache[pkey]
            else:
                pack = orchestrator.social.run(card, m, profile)
                pack = {k: (tidy(v) if isinstance(v, str) else v) for k, v in pack.items()}
                cache[pkey] = pack
                orchestrator.log.append(("Social Desk", m["moment_id"], pack.get("hook", "")))
        if voice_on:
            speak(f"{card['title']}. {card['body']}", profile.language, profile.personality)
        return m["moment_id"], card, pack

    with ThreadPoolExecutor(max_workers=WARM_UP_WORKERS) as pool:
        futures = [pool.submit(make_card, m, snap) for m, snap in jobs]
        for f in as_completed(futures):
            moment_id, card, pack = f.result()
            cards[moment_id] = card
            if pack:
                packs[moment_id] = pack
            done += 1
            progress.progress(done / total, text=f"The agents are preparing the match: {done - 1} of "
                                                 f"{len(jobs)} moments analysed, written and fact-checked")

    moments = [m for m, _ in jobs]
    insights = [{"headline": cards[m["moment_id"]]["title"], "why": cards[m["moment_id"]]["body"]}
                for m in moments]
    rkey = cache_key(profile, ["recap", len(moments)])
    if rkey in cache:
        recap = cache[rkey]
    else:
        progress.progress(0.97, text="The Storyteller is drafting the full-time recap")
        recap = orchestrator.storyteller.run(moments, insights, state.snapshot(), profile, state.names)
        cache[rkey] = recap
    save_cache(cache)
    status.empty()
    return {"cards": cards, "packs": packs, "recap": recap, "log": orchestrator.log,
            "calls": orchestrator.llm.calls}


# ------------------------------------------------------------------ the live replay
def ball_position(e):
    return (e["x"], e["y"]) if e["team"] == HOME_ID else (105 - e["x"], 68 - e["y"])


def play(match, start, stop):
    """Replay events[start:stop] like a live feed, showing each prepared card at its minute."""
    p = match["profile"]
    state = MatchState(META)
    for e in EVENTS[:start]:            # rebuild the match as it stood (e.g. at half-time)
        state.ingest(e)
    shown, momentum = match["shown"], match["momentum"]
    trail, ball_team = deque(maxlen=7), None
    last_minute, last_draw = state.minute, 0.0
    for e in EVENTS[start:stop]:
        new_moments = state.ingest(e)
        if e.get("x") is not None and e["team"]:
            ball_team = e["team"]
            trail.append(ball_position(e))
        for m in new_moments:
            card = match["cards"].get(m["moment_id"])
            if not card:
                continue
            shown.append(card)
            render(state, shown, momentum, trail, ball_team, p)
            hold = 2.2
            if match["voice"]:
                audio = speak(f"{card['title']}. {card['body']}", p.language, p.personality)
                if audio:
                    audio_box.audio(audio, format="audio/mp3", autoplay=True)
                    hold = max(hold, estimate_seconds(f"{card['title']} {card['body']}", p.personality))
            time.sleep(hold)
        if state.minute != last_minute:
            last_minute = state.minute
            momentum.append((state.minute, state.momentum()[HOME_ID]))
            wait = 1 / match["speed"] - (time.time() - last_draw)
            if wait > 0:
                time.sleep(wait)
            render(state, shown, momentum, trail, ball_team, p)
            last_draw = time.time()
    audio_box.empty()
    return state


def static_view(match):
    """Redraw the paused or finished match without replaying it (after any click)."""
    state = MatchState(META)
    for e in EVENTS[:match["position"]]:
        state.ingest(e)
    _seen["card"] = match["shown"][-1]["card_id"] if match["shown"] else None   # no re-animation
    render(state, match["shown"], match["momentum"], view_profile=match["profile"], phase=match["phase"])


# ------------------------------------------------------------------ after the whistle: social + pundit
def social_desk(match):
    packs = [(c, match["packs"][int(c["card_id"])]) for c in match["shown"]
             if int(c["card_id"]) in match["packs"]]
    if not packs:
        return
    st.markdown('<div class="h" style="margin-top:28px">Social Desk<small>every big moment, ready for the '
                "club's channels</small></div>", unsafe_allow_html=True)
    tabs = st.tabs([f"{c['display_minute']} {c['title'][:24]}" for c, _ in packs])
    for tab, (card, pack) in zip(tabs, packs):
        with tab:
            img_col, txt_col = st.columns([2, 3], gap="large")
            number = SHIRTS.get(card.get("player"), (None, None))[1]
            score = match["scores"].get(card["card_id"], (0, 0))
            png = social_card_png(card, score, HOME_ID, AWAY_ID, number)
            with img_col:
                st.image(png, width=320)
                st.download_button("Download post", png, file_name=f"moment_{card['card_id']}.png",
                                   mime="image/png", key=f"dl_{card['card_id']}", use_container_width=True)
            with txt_col:
                st.markdown(f"""<div class="panel" style="margin-top:0" dir="auto">
                  <div class="pack-label">Hook for the first three seconds</div><div class="pack-hook">{esc(pack.get('hook'))}</div>
                  <div class="pack-label">On-screen text</div><div>{esc(pack.get('on_screen_text'))}</div>
                  <div class="pack-label">Format</div><div>{esc(pack.get('format_idea'))}</div>
                  <div class="pack-label">Best on</div><div>{esc(pack.get('best_platform'))}</div>
                </div>""", unsafe_allow_html=True)
                st.caption("Caption, ready to copy")
                st.code(f"{pack.get('caption', '')} {' '.join(pack.get('hashtags') or [])}".strip(),
                        language=None, wrap_lines=True)


SUGGESTED = {
    "half": ["Who's been the best player so far?", "Why are the Wonders ahead?",
             "Which team is pressing harder?"],
    "full": ["Why did the Eagles stop creating chances?", "What changed in the second half?",
             "Who was the most accurate passer?"],
}


def pundit_chat(match, limit, phase):
    p = match["profile"]
    st.markdown(f'<div class="h" style="margin-top:28px">Ask the pundit<small>the pundit '
                f'checks the stats before answering</small></div>', unsafe_allow_html=True)
    chat = st.session_state.setdefault(f"chat_{phase}", [])
    for role, text, tools in chat:
        with st.chat_message(role, avatar="🎙️" if role == "assistant" else None):
            st.markdown(esc(text))
            if tools:
                st.caption("Checked: " + ", ".join(tools))
    cols = st.columns(len(SUGGESTED[phase]))
    asked = None
    for col, q in zip(cols, SUGGESTED[phase]):
        if col.button(q, key=f"sugg_{phase}_{q}", use_container_width=True):
            asked = q
    typed = st.chat_input("Ask about the match", key=f"chatbox_{phase}")
    question = typed or asked
    if question:
        history = [{"role": role, "content": text} for role, text, _ in chat[-6:]]
        pundit = PunditAgent(limit_minute=limit, language=p.language, personality=p.personality)
        with st.spinner("The pundit is checking the numbers"):
            answer = pundit.ask(question, history)
        tools = [name.replace("get_", "").replace("_", " ") for name, _ in pundit.tool_log]
        chat.append(("user", question, []))
        chat.append(("assistant", answer or "…", tools))
        st.rerun()


def full_time_extras(match):
    r = match["recap"]
    with after_box:
        st.markdown(f"""<div class="panel" dir="auto"><div class="h">Full-time recap</div>
            <h3>{esc(r['headline'])}</h3><p style="max-width:75ch;line-height:1.55">{esc(r['recap'])}</p>
            <p><b>Player of the match: {esc(r['player_of_the_match'])}</b><br>
            <span style="color:{MUTED}">{esc(r['player_of_the_match_reason'])}</span></p></div>""",
                    unsafe_allow_html=True)
        result = {"profile": match["profile"].__dict__, "overlay_cards": match["shown"],
                  "social_packs": {str(k): v for k, v in match["packs"].items()},
                  "recap": r, "agent_log": match["log"], "model_calls": match["calls"]}
        c1, c2 = st.columns(2)
        with c1:
            st.download_button("Download overlay data (JSON)", json.dumps(result, ensure_ascii=False, indent=2),
                               file_name="overlay_cards.json", mime="application/json",
                               use_container_width=True)
        with c2:
            st.caption(f"{match['calls']} AI calls, {len(match['shown'])} captions, "
                       f"{sum(c['verified'] for c in match['shown'])} fact-checked")
        social_desk(match)
        pundit_chat(match, 95, "full")
        with st.expander("What the agents did"):
            for agent, moment_id, note in match["log"]:
                st.markdown(f"**{agent}**, moment {moment_id}: {esc(note)}")


def half_time_extras(match):
    with after_box:
        st.markdown('<div class="panel"><div class="h">Half-time</div><p style="margin:0">The players are '
                    'in the dressing room. Ask the pundit what has happened, then start the second half.'
                    '</p></div>', unsafe_allow_html=True)
        if st.button("Start the second half", type="primary", key="second_half"):
            st.session_state["resume"] = True
            st.rerun()
        pundit_chat(match, 45, "half")
        social_desk(match)


def record_scores(match):
    """Remember the score at each card, for the social graphics."""
    state = MatchState(META)
    for e in EVENTS:
        for m in state.ingest(e):
            if m["moment_id"] in match["cards"]:
                match["scores"][match["cards"][m["moment_id"]]["card_id"]] = \
                    (state.score[HOME_ID], state.score[AWAY_ID])


# ------------------------------------------------------------------ page flow
match = st.session_state.get("match")

if kick_off:
    for k in ("chat_half", "chat_full"):
        st.session_state.pop(k, None)
    render(MatchState(META), [], [], phase="pre")
    prepared = warm_up()
    match = {**prepared, "profile": profile, "voice": voice_on, "speed": speed, "shown": [],
             "momentum": [], "scores": {}, "position": 0, "phase": "live"}
    record_scores(match)
    st.session_state["match"] = match
    stop = HALF_TIME_INDEX if pause_ht else len(EVENTS)
    play(match, 0, stop)
    match["position"] = stop
    match["phase"] = "half" if pause_ht else "full"
    st.rerun()

elif match and st.session_state.pop("resume", False):
    match["phase"] = "live"
    static_view(match)
    play(match, match["position"], len(EVENTS))
    match["position"], match["phase"] = len(EVENTS), "full"
    st.rerun()

elif match:
    static_view(match)
    if match["phase"] == "half":
        half_time_extras(match)
    elif match["phase"] == "full":
        full_time_extras(match)
    if match["profile"] != profile:
        st.sidebar.info("Settings changed. Press Kick off to watch with the new settings.")

else:
    render(MatchState(META), [], [], phase="pre")
    status.info("Choose how you want to watch in the sidebar, then press Kick off.")
