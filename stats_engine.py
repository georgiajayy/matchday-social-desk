"""
stats_engine.py
Stage (a) INGEST + (b) INTERPRET of the Matchday Social Desk pipeline.

The engine reads match events one at a time (as if live), keeps a running picture
of the match, and flags "key moments" together with the facts that explain WHY
they matter. The AI agents never invent numbers: they only use what this engine
computes, which keeps every insight explainable and checkable.

Try it:   python stats_engine.py            (replays the match and prints key moments)
          python stats_engine.py --minute 60 (snapshot of the match at 60 minutes)
"""

import argparse
import json
from collections import defaultdict, deque
from pathlib import Path

DATA = Path(__file__).parent / "data"

WINDOW_S = 10 * 60          # rolling window for momentum (10 minutes)
SHORT_WINDOW_S = 5 * 60     # rolling window for pressure / chaos (5 minutes)
FINAL_THIRD_X = 70          # metres: pitch is 105m long, attacking left -> right
BIG_CHANCE_XG = 0.20
PASS_MILESTONES = (50, 75)
MILESTONE_MIN_ACCURACY = 85   # only flag milestones for players passing well


def pass_difficulty(e):
    """Rate a pass from 1 (simple) to 10 (very hard) using length, pressure,
    how far it moves the ball forward, and whether it reaches the final third."""
    length = e.get("length_m", 0)
    progress = (e.get("end_x") or e["x"]) - e["x"]
    score = 1.0
    score += 3.0 * min(length / 40, 1)
    score += 2.0 if e.get("under_pressure") else 0
    score += 2.0 * max(0, min(progress / 30, 1))
    score += 1.5 if (e.get("end_x") or 0) >= FINAL_THIRD_X else 0
    score += 0.5 if e.get("ball_speed_kmh", 0) > 80 else 0
    return round(min(score, 10), 1)


def new_team_stats():
    return {
        "passes": 0, "passes_complete": 0, "pass_difficulty_sum": 0.0,
        "hard_passes_complete": 0,   # difficulty >= 7
        "final_third_entries": 0,
        "shots": 0, "shots_on_target": 0, "goals": 0, "xg": 0.0,
        "fastest_shot_kmh": 0.0, "fastest_pass_kmh": 0.0,
        "tackles": 0, "interceptions": 0, "pressures": 0, "fouls": 0,
        "turnovers": 0,
    }


def new_player_stats():
    return {"team": None, "passes": 0, "passes_complete": 0, "pass_difficulty_sum": 0.0,
            "shots": 0, "goals": 0, "xg": 0.0, "tackles": 0, "interceptions": 0,
            "pressures": 0, "fastest_shot_kmh": 0.0}


class MatchState:
    """Running picture of the match, updated one event at a time."""

    def __init__(self, meta):
        self.meta = meta
        self.home = meta["home"]["id"]
        self.away = meta["away"]["id"]
        self.names = {self.home: meta["home"]["name"], self.away: meta["away"]["name"]}
        self.clock_s = 0.0
        self.minute = 0
        self.score = {self.home: 0, self.away: 0}
        self.team = {self.home: new_team_stats(), self.away: new_team_stats()}
        self.players = defaultdict(new_player_stats)
        self.recent = deque()          # (clock, team, kind, value) for rolling windows
        self.key_moments = []
        self._last_flag = {}           # debounce: (type, team) -> clock
        self._milestones_done = set()
        self._momentum_history = deque()
        self.period = 1
        self._shots = []
        self._last_shot = None

    # ------------------------------------------------------------------ ingest
    def ingest(self, e):
        """Update the match state with one event. Returns any new key moments."""
        self.clock_s = e["clock_s"]
        self.minute = e["minute"]
        self.period = e["period"]
        t, kind = e["team"], e["type"]
        before = len(self.key_moments)
        if t is None:
            return []
        ts = self.team[t]
        ps = self.players[e["player"]] if e.get("player") else None
        if ps is not None:
            ps["team"] = t

        if kind == "pass":
            diff = pass_difficulty(e)
            ok = e["outcome"] == "complete"
            ts["passes"] += 1
            ts["passes_complete"] += ok
            ts["pass_difficulty_sum"] += diff
            ts["hard_passes_complete"] += ok and diff >= 7
            ts["fastest_pass_kmh"] = max(ts["fastest_pass_kmh"], e.get("ball_speed_kmh", 0))
            ps["passes"] += 1
            ps["passes_complete"] += ok
            ps["pass_difficulty_sum"] += diff
            if ok and e["x"] < FINAL_THIRD_X <= e["end_x"]:
                ts["final_third_entries"] += 1
                self._push(t, "entry", 1)
            self._push(t, "pass", 1)
            self._push(t, "pass_ok", 1 if ok else 0)
            if not ok:
                ts["turnovers"] += 1
                self._push(t, "turnover", 1)
            self._check_milestone(e, ps)

        elif kind == "shot":
            self._shots.append(e)
            self._last_shot = e
            ts["shots"] += 1
            ts["xg"] += e["xg"]
            ts["fastest_shot_kmh"] = max(ts["fastest_shot_kmh"], e["shot_speed_kmh"])
            ps["shots"] += 1
            ps["xg"] += e["xg"]
            ps["fastest_shot_kmh"] = max(ps["fastest_shot_kmh"], e["shot_speed_kmh"])
            if e["outcome"] in ("goal", "saved"):
                ts["shots_on_target"] += 1
            self._push(t, "xg", e["xg"])
            self._on_shot(e)

        elif kind == "goal":
            ts["goals"] += 1
            ps["goals"] += 1
            self.score[t] += 1
            self._on_goal(e)

        elif kind in ("tackle", "interception"):
            ts[kind + "s"] += 1
            ps[kind + "s"] += 1
        elif kind == "pressure":
            ts["pressures"] += 1
            ps["pressures"] += 1
            self._push(t, "pressure", 1)
        elif kind == "foul_committed":
            ts["fouls"] += 1
            self._push(t, "foul", 1)
        elif kind == "save":
            self._on_save(e)

        self._trim()
        self._check_momentum()
        self._check_pressure_and_chaos()
        return self.key_moments[before:]

    # ---------------------------------------------------------- rolling windows
    def _push(self, team, kind, value):
        self.recent.append((self.clock_s, team, kind, value))

    def _trim(self):
        while self.recent and self.clock_s - self.recent[0][0] > WINDOW_S:
            self.recent.popleft()

    def _window_sum(self, team, kind, seconds):
        return sum(v for c, t, k, v in self.recent
                   if (team is None or t == team) and k == kind and self.clock_s - c <= seconds)

    def momentum(self):
        """Share of 'threat' (xG + final-third entries + completed passes) over the last
        10 minutes, as a percentage for each team. 50/50 = even game."""
        threat = {}
        for t in (self.home, self.away):
            threat[t] = (self._window_sum(t, "xg", WINDOW_S) * 10
                         + self._window_sum(t, "entry", WINDOW_S) * 1.0
                         + self._window_sum(t, "pass_ok", WINDOW_S) * 0.05)
        total = sum(threat.values())
        if total == 0:                      # nothing has happened yet: an even game
            return {t: 50 for t in threat}
        return {t: round(100 * v / total) for t, v in threat.items()}

    def chaos_index(self):
        """0 = calm, controlled football; 100 = scrappy, end-to-end chaos.
        Based on turnovers and fouls as a share of all passes in the last 5 minutes."""
        passes = self._window_sum(None, "pass", SHORT_WINDOW_S)
        messy = self._window_sum(None, "turnover", SHORT_WINDOW_S) + \
            2 * self._window_sum(None, "foul", SHORT_WINDOW_S)
        if passes < 10:
            return None
        return round(min(100, 100 * messy / passes / 0.45))

    # ---------------------------------------------------------- key moments
    def _flag(self, mtype, e, team, facts, why, cooldown_s=0, importance=2):
        key = (mtype, team)
        if cooldown_s and self.clock_s - self._last_flag.get(key, -1e9) < cooldown_s:
            return
        self._last_flag[key] = self.clock_s
        self.key_moments.append({
            "moment_id": len(self.key_moments) + 1,
            "type": mtype,
            "clock_s": self.clock_s,
            "minute": self.minute,
            "display_minute": self.display_minute(),
            "team": team,
            "team_name": self.names.get(team),
            "player": e.get("player") if e else None,
            "importance": importance,     # 1 = minor, 3 = headline
            "score": f"{self.score[self.home]}-{self.score[self.away]}",
            "facts": facts,               # numbers the agents are allowed to use
            "why_signals": why,           # machine-readable reasons it matters
        })

    def _on_shot(self, e):
        if e["outcome"] != "goal" and e["xg"] >= BIG_CHANCE_XG:
            self._flag("big_chance_missed", e, e["team"],
                       {"xg": e["xg"], "distance_m": e["distance_m"],
                        "shot_speed_kmh": e["shot_speed_kmh"], "outcome": e["outcome"]},
                       ["high_quality_chance_not_taken"], importance=2)

    def _on_save(self, e):
        shot = self._last_shot if self._last_shot and self._last_shot["outcome"] == "saved" else None
        if shot and shot["xg"] >= 0.15:
            self._flag("big_save", e, e["team"],
                       {"xg_faced": shot["xg"], "shot_speed_kmh": shot["shot_speed_kmh"],
                        "shooter": shot["player"]},
                       ["goalkeeper_prevented_likely_goal"], importance=2)

    def _on_goal(self, e):
        t = e["team"]
        opp = self.away if t == self.home else self.home
        shot = self._last_shot
        mom = self.momentum()
        why = []
        if self.score[t] == self.score[opp]:
            why.append("equaliser")
        elif self.score[t] - self.score[opp] == 1:
            why.append("takes_the_lead")
        else:
            why.append("extends_the_lead")
        if mom[opp] >= 60:
            why.append("against_the_run_of_play")
        if shot and shot["xg"] < 0.08:
            why.append("low_probability_finish")
        if shot and shot["shot_speed_kmh"] >= 100:
            why.append("powerful_strike")
        if self.minute >= 80:
            why.append("late_goal")
        self._flag("goal", e, t,
                   {"xg": shot["xg"] if shot else None,
                    "distance_m": shot["distance_m"] if shot else None,
                    "shot_speed_kmh": shot["shot_speed_kmh"] if shot else None,
                    "momentum_before_goal": mom,
                    "new_score": f"{self.score[self.home]}-{self.score[self.away]}"},
                   why, importance=3)

    def _check_momentum(self):
        if self.clock_s < 12 * 60:
            return
        mom = self.momentum()
        self._momentum_history.append((self.clock_s, mom))
        while self._momentum_history and self.clock_s - self._momentum_history[0][0] > WINDOW_S:
            self._momentum_history.popleft()
        then = self._momentum_history[0][1]
        for t in (self.home, self.away):
            if mom[t] >= 65 and then[t] <= 45:
                self._flag("momentum_swing", None, t,
                           {"momentum_now": mom[t], "momentum_10_min_ago": then[t],
                            "final_third_entries_last_10": self._window_sum(t, "entry", WINDOW_S),
                            "xg_last_10": round(self._window_sum(t, "xg", WINDOW_S), 2)},
                           ["team_has_taken_control"], cooldown_s=15 * 60, importance=2)

    def _check_pressure_and_chaos(self):
        if self.clock_s < 15 * 60:
            return
        for t in (self.home, self.away):
            recent = self._window_sum(t, "pressure", SHORT_WINDOW_S)
            avg_per_5 = self.team[t]["pressures"] / (self.clock_s / SHORT_WINDOW_S)
            if recent >= 12 and recent >= 1.6 * avg_per_5:
                self._flag("pressing_surge", None, t,
                           {"pressures_last_5_min": recent,
                            "match_average_per_5_min": round(avg_per_5, 1)},
                           ["tactical_pressure_change"], cooldown_s=15 * 60, importance=1)
        chaos = self.chaos_index()
        if chaos is not None and chaos >= 70:
            self._flag("chaotic_spell", None, None,
                       {"chaos_index": chaos,
                        "turnovers_last_5_min": self._window_sum(None, "turnover", SHORT_WINDOW_S)},
                       ["control_to_chaos"], cooldown_s=15 * 60, importance=1)

    def _check_milestone(self, e, ps):
        name = e["player"]
        for m in PASS_MILESTONES:
            if ps["passes"] == m and (name, m) not in self._milestones_done:
                self._milestones_done.add((name, m))
                acc = round(100 * ps["passes_complete"] / ps["passes"])
                if acc < MILESTONE_MIN_ACCURACY:
                    continue
                self._flag("player_milestone", e, e["team"],
                           {"milestone": f"{m} passes", "pass_accuracy_pct": acc,
                            "avg_pass_difficulty": round(ps["pass_difficulty_sum"] / ps["passes"], 1)},
                           ["player_driving_the_game"], importance=1)

    def display_minute(self):
        """Broadcast-style minute, e.g. 45+2' in first-half stoppage time."""
        if self.period == 1 and self.minute >= 45:
            return f"45+{self.minute - 44}'"
        if self.period == 2 and self.minute >= 90:
            return f"90+{self.minute - 89}'"
        return f"{self.minute + 1}'"

    # ----------------------------------------------------------- snapshot
    def snapshot(self):
        """Everything the agents and the on-screen graphics need, as plain data."""
        teams = {}
        for t, s in self.team.items():
            teams[t] = {
                "name": self.names[t],
                "goals": self.score[t],
                "pass_accuracy_pct": round(100 * s["passes_complete"] / s["passes"]) if s["passes"] else None,
                "passes": s["passes"],
                "avg_pass_difficulty": round(s["pass_difficulty_sum"] / s["passes"], 1) if s["passes"] else None,
                "hard_passes_complete": s["hard_passes_complete"],
                "final_third_entries": s["final_third_entries"],
                "shots": s["shots"], "shots_on_target": s["shots_on_target"],
                "xg": round(s["xg"], 2),
                "fastest_shot_kmh": s["fastest_shot_kmh"],
                "tackles": s["tackles"], "interceptions": s["interceptions"],
                "pressures": s["pressures"], "fouls": s["fouls"],
            }
        total_passes = sum(self.team[t]["passes"] for t in self.team) or 1
        for t in teams:
            teams[t]["possession_pct"] = round(100 * self.team[t]["passes"] / total_passes)
        top_players = sorted(
            ({"name": n, **{k: (round(v, 2) if isinstance(v, float) else v) for k, v in p.items()}}
             for n, p in self.players.items()),
            key=lambda p: (p["goals"], p["xg"], p["passes_complete"]), reverse=True)[:6]
        return {
            "minute": self.minute,
            "display_minute": self.display_minute(),
            "home_team": self.names[self.home],
            "away_team": self.names[self.away],
            "score": {"home": self.score[self.home], "away": self.score[self.away]},
            "teams": teams,
            "momentum_last_10_min": self.momentum(),
            "chaos_index": self.chaos_index(),
            "top_players": top_players,
            "key_moments_so_far": len(self.key_moments),
        }

    def player_report(self, name):
        p = self.players.get(name)
        if not p:
            return None
        out = {k: (round(v, 2) if isinstance(v, float) else v) for k, v in p.items()}
        out["pass_accuracy_pct"] = round(100 * p["passes_complete"] / p["passes"]) if p["passes"] else None
        out["avg_pass_difficulty"] = round(p["pass_difficulty_sum"] / p["passes"], 1) if p["passes"] else None
        out.pop("pass_difficulty_sum", None)
        return out


def load_match():
    meta = json.loads((DATA / "match_meta.json").read_text(encoding="utf-8"))
    events = [json.loads(line) for line in (DATA / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    return meta, events


def replay(until_minute=None):
    """Feed events into a fresh MatchState in match order, like a live feed."""
    meta, events = load_match()
    state = MatchState(meta)
    for e in events:
        if until_minute is not None and e["minute"] > until_minute:
            break
        state.ingest(e)
    return state


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--minute", type=int, default=None, help="stop the replay at this minute")
    args = parser.parse_args()
    state = replay(args.minute)

    print(f"\nKEY MOMENTS (up to minute {state.minute})")
    for m in state.key_moments:
        who = m["player"] or m["team_name"] or "Both teams"
        print(f"  {m['display_minute']:>6}  [{m['type']}]  {who}  {m['why_signals']}  {m['facts']}")

    print(f"\nSNAPSHOT AT {state.display_minute()}")
    print(json.dumps(state.snapshot(), indent=2))


if __name__ == "__main__":
    main()
