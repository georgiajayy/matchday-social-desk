"""
generate_match.py
Creates a synthetic, football-realistic match as a stream of events.

Output (in the data/ folder):
  - match_meta.json   : teams, players, venue, kick-off info
  - events.jsonl      : one event per line, in match-clock order (ready to "stream")

All teams, players and venues are fictional. No real match data is used.
Run:  python generate_match.py            (default seed)
      python generate_match.py --seed 7   (a different match)
"""

import argparse
import json
import math
import random
from pathlib import Path

PITCH_LENGTH = 105  # metres
PITCH_WIDTH = 68

HOME = {
    "id": "NWW",
    "name": "North West Wonders",
    "colour": "#6A1B9A",
    "players": [
        (1, "Danny Okoro", "GK"), (2, "Jamal Bennett", "RB"), (4, "Callum Fitzgerald", "CB"),
        (5, "Kwame Asante-Ward", "CB"), (3, "Reece Patel", "LB"), (6, "Tyrone Mitchell", "DM"),
        (8, "Harvey Okonjo", "CM"), (10, "Kai Brennan", "AM"), (7, "Darnell Hayes", "RW"),
        (11, "Ollie Khan", "LW"), (9, "Jaden Forbes", "ST"),
    ],
}
AWAY = {
    "id": "SEE",
    "name": "South East Eagles",
    "colour": "#D4A017",
    "players": [
        (1, "Charlie Hudson", "GK"), (2, "Femi Lawal", "RB"), (5, "Connor Murphy", "CB"),
        (6, "Isaac Oduya", "CB"), (3, "Ryan Chopra", "LB"), (4, "Tunde Bakare", "DM"),
        (8, "Freddie Walsh", "CM"), (10, "Zain Hussain", "AM"), (7, "Lewis Achampong", "RW"),
        (11, "Jerome Clarke", "LW"), (9, "Kayden Bryan", "ST"),
    ],
}

# Rough pitch zones (x along the pitch, attacking left -> right) for each position
POSITION_X = {"GK": 5, "RB": 30, "CB": 22, "LB": 30, "DM": 40, "CM": 52,
              "AM": 66, "RW": 75, "LW": 75, "ST": 85}
POSITION_Y = {"GK": 34, "RB": 60, "CB": 34, "LB": 8, "DM": 34, "CM": 30,
              "AM": 34, "RW": 60, "LW": 8, "ST": 34}


def momentum(team_id, clock_s):
    """Scripted 'story' of the match: how strongly each team is playing over time.
    Home control the first half, away grow into it, then a late home push."""
    minute = clock_s / 60
    if minute < 35:
        home = 0.62
    elif minute < 55:
        home = 0.45
    elif minute < 75:
        home = 0.38
    else:
        home = 0.60
    return home if team_id == HOME["id"] else 1 - home


def pick_player(team, x_hint, rng, exclude=None):
    """Pick a plausible player for a pitch position (outfield players near the ball)."""
    weights = []
    for num, name, pos in team["players"]:
        if exclude and num == exclude:
            weights.append(0)
            continue
        if pos == "GK":
            weights.append(0.3 if x_hint < 20 else 0.02)
            continue
        dist = abs(POSITION_X[pos] - x_hint)
        weights.append(1 / (1 + dist / 10))
    return rng.choices(team["players"], weights=weights, k=1)[0]


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


class Match:
    def __init__(self, seed):
        self.rng = random.Random(seed)
        self.events = []
        self.clock = 0.0
        self.period = 1
        self.score = {HOME["id"]: 0, AWAY["id"]: 0}
        self.next_id = 1

    def team(self, team_id):
        return HOME if team_id == HOME["id"] else AWAY

    def other(self, team_id):
        return AWAY["id"] if team_id == HOME["id"] else HOME["id"]

    def add(self, team_id, player, etype, x, y, **extra):
        num, name, pos = player if player else (None, None, None)
        event = {
            "event_id": self.next_id,
            "match_id": "syn-nww-see-001",
            "period": self.period,
            "clock_s": round(self.clock, 1),
            "minute": int(self.clock // 60),
            "team": team_id,
            "player": name,
            "player_number": num,
            "position": pos,
            "type": etype,
            # coordinates are in metres, from the acting team's point of view (attacking left -> right)
            "x": round(x, 1) if x is not None else None,
            "y": round(y, 1) if y is not None else None,
            "score_home": self.score[HOME["id"]],
            "score_away": self.score[AWAY["id"]],
        }
        event.update(extra)
        self.events.append(event)
        self.next_id += 1
        return event

    def tick(self, lo, hi):
        self.clock += self.rng.uniform(lo, hi)

    def possession(self, team_id, x, y):
        """Play one possession until it ends in a shot, turnover or stoppage."""
        rng = self.rng
        team = self.team(team_id)
        strength = momentum(team_id, self.clock)
        carrier = pick_player(team, x, rng)
        self.add(team_id, carrier, "possession_start", x, y)

        for _ in range(rng.randint(2, 14)):
            # Opponent pressure is higher in the attacking third and when the opponent is on top
            pressure_chance = 0.25 + (x / PITCH_LENGTH) * 0.3 + (0.5 - strength) * 0.4
            under_pressure = rng.random() < pressure_chance
            if under_pressure:
                opp = self.other(team_id)
                presser = pick_player(self.team(opp), PITCH_LENGTH - x, rng)
                self.add(opp, presser, "pressure", PITCH_LENGTH - x, PITCH_WIDTH - y,
                         target_player=carrier[1])

            # Shoot?
            dist_to_goal = math.hypot(PITCH_LENGTH - x, 34 - y)
            shoot_p = (0.02 + strength * 0.045) * (1 + (x - 80) / 12)
            if x > 80 and rng.random() < shoot_p:
                self.shot(team_id, carrier, x, y, dist_to_goal, under_pressure)
                return

            # Pass
            forward = rng.gauss(8 + strength * 6, 9)
            lateral = rng.gauss(0, 12)
            end_x = clamp(x + forward, 2, PITCH_LENGTH - 2)
            end_y = clamp(y + lateral, 2, PITCH_WIDTH - 2)
            length = math.hypot(end_x - x, end_y - y)
            success_p = 1.0 - length / 160 - (0.08 if under_pressure else 0) \
                - (end_x / PITCH_LENGTH) * 0.10 + (strength - 0.5) * 0.25
            completed = rng.random() < clamp(success_p, 0.35, 0.97)
            receiver = pick_player(team, end_x, rng, exclude=carrier[0])
            ball_speed = clamp(rng.gauss(35 + length * 1.1, 6), 15, 110)
            self.tick(2, 6)
            self.add(team_id, carrier, "pass", x, y,
                     end_x=round(end_x, 1), end_y=round(end_y, 1),
                     length_m=round(length, 1),
                     ball_speed_kmh=round(ball_speed, 1),
                     under_pressure=under_pressure,
                     outcome="complete" if completed else "incomplete",
                     recipient=receiver[1] if completed else None)
            if not completed:
                opp = self.other(team_id)
                tx, ty = PITCH_LENGTH - end_x, PITCH_WIDTH - end_y
                winner = pick_player(self.team(opp), tx, rng)
                kind = "interception" if rng.random() < 0.55 else "tackle"
                self.tick(1, 3)
                self.add(opp, winner, kind, tx, ty, outcome="won")
                if kind == "tackle" and rng.random() < 0.18:
                    self.add(opp, winner, "foul_committed", tx, ty)
                    self.tick(15, 35)
                    self.possession(team_id, end_x, end_y)
                    return
                self.add(opp, winner, "possession_change", tx, ty, from_team=team_id)
                self.tick(1, 3)
                self.possession_queue = (opp, tx, ty)
                return
            carrier, x, y = receiver, end_x, end_y

        # Possession fizzles out (ball out of play)
        self.tick(5, 15)
        opp = self.other(team_id)
        self.add(team_id, carrier, "ball_out", x, y)
        self.possession_queue = (opp, PITCH_LENGTH - x, PITCH_WIDTH - y)

    def shot(self, team_id, shooter, x, y, dist, under_pressure):
        rng = self.rng
        # Simple expected-goals model: closer and more central = better chance
        angle = abs(math.atan2(7.32 * (PITCH_LENGTH - x),
                               (PITCH_LENGTH - x) ** 2 + (34 - y) ** 2 - (7.32 / 2) ** 2))
        centrality = clamp(angle / 0.6, 0.3, 1.0)
        xg = 0.8 * math.exp(-0.13 * dist) * centrality * (0.75 if under_pressure else 1.0)
        xg = clamp(xg, 0.01, 0.7)
        shot_speed = clamp(rng.gauss(95 - dist * 0.6, 12), 40, 135)
        self.tick(2, 5)
        r = rng.random()
        if r < xg:
            outcome = "goal"
        elif r < xg + 0.3:
            outcome = "saved"
        elif r < xg + 0.5:
            outcome = "blocked"
        else:
            outcome = "off_target"
        if outcome == "goal":
            self.score[team_id] += 1
        self.add(team_id, shooter, "shot", x, y, distance_m=round(dist, 1), xg=round(xg, 3),
                 shot_speed_kmh=round(shot_speed, 1), under_pressure=under_pressure,
                 outcome=outcome)
        opp = self.other(team_id)
        if outcome == "goal":
            self.add(team_id, shooter, "goal", x, y)
            self.tick(45, 75)
            self.possession_queue = (opp, 52.5, 34)
        elif outcome == "saved":
            keeper = self.team(opp)["players"][0]
            self.add(opp, keeper, "save", 3, 34)
            self.tick(4, 10)
            if rng.random() < 0.4:
                self.add(team_id, shooter, "corner", PITCH_LENGTH, 0)
                self.tick(15, 30)
                self.possession_queue = (team_id, 95, 30)
            else:
                self.possession_queue = (opp, 8, 34)
        else:
            self.tick(10, 25)
            self.possession_queue = (opp, 6, 34)

    def play_half(self, start_clock, end_clock, kicking_off):
        self.clock = start_clock
        kicker = pick_player(self.team(kicking_off), 52, self.rng)
        self.add(kicking_off, kicker, "kick_off", 52.5, 34)
        self.possession_queue = (kicking_off, 52.5, 34)
        added = self.rng.randint(1, 5) * 60
        while self.clock < end_clock + added:
            team_id, x, y = self.possession_queue
            self.possession(team_id, x, y)
        return added

    def run(self):
        added1 = self.play_half(0, 45 * 60, HOME["id"])
        self.add(None, None, "half_time", None, None, added_time_s=added1)
        self.period = 2
        added2 = self.play_half(45 * 60, 90 * 60, AWAY["id"])
        self.add(None, None, "full_time", None, None, added_time_s=added2)
        return self.events


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=2026)
    args = parser.parse_args()

    match = Match(args.seed)
    events = match.run()

    out = Path(__file__).parent / "data"
    out.mkdir(exist_ok=True)
    meta = {
        "match_id": "syn-nww-see-001",
        "competition": "Synthetic League (fictional)",
        "venue": "Wonders Park, North West London (fictional)",
        "seed": args.seed,
        "home": {k: v for k, v in HOME.items() if k != "players"} |
                {"players": [{"number": n, "name": nm, "position": p} for n, nm, p in HOME["players"]]},
        "away": {k: v for k, v in AWAY.items() if k != "players"} |
                {"players": [{"number": n, "name": nm, "position": p} for n, nm, p in AWAY["players"]]},
        "pitch": {"length_m": PITCH_LENGTH, "width_m": PITCH_WIDTH},
        "final_score": {"home": match.score[HOME["id"]], "away": match.score[AWAY["id"]]},
        "note": "All teams, players and events are synthetic. No real match data is used.",
    }
    (out / "match_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    with open(out / "events.jsonl", "w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")

    counts = {}
    for e in events:
        counts[e["type"]] = counts.get(e["type"], 0) + 1
    print(f"Generated {len(events)} events -> data/events.jsonl")
    print(f"Final score: {HOME['name']} {match.score[HOME['id']]} - "
          f"{match.score[AWAY['id']]} {AWAY['name']}")
    print("Event counts:", dict(sorted(counts.items(), key=lambda kv: -kv[1])))


if __name__ == "__main__":
    main()
