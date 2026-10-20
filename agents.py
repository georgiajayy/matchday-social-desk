"""
agents.py
Stages (c) EXPLAIN, (d) RENDER and (e) PERSONALISE of the Matchday Social Desk.

A small team of AI agents works on every key moment the stats engine flags:

    Analyst      -> explains WHY the moment matters (tactics, momentum, control vs chaos)
    Fan Voice    -> rewrites it for a specific fan (analyst, casual fan, social caption)
    Fact-checker -> checks every claim against the engine's numbers; sends it back if wrong
    Translator   -> renders the final text in the fan's language
    Storyteller  -> writes the full-time match recap

The Orchestrator runs them in order and outputs timed, machine-readable overlay cards.
Each moment only sees the match as it stood at that minute, so nothing "knows the future".

Set MOCK_AI=1 in .env to run without Azure (handy for testing and offline demos).
Run:  python agents.py                          (casual fan, English)
      python agents.py --mode analyst
      python agents.py --mode social --language Spanish --player "Ollie Khan"
"""

import argparse
import json
import os
import re
import threading
from dataclasses import dataclass, field

from dotenv import load_dotenv

from stats_engine import MatchState, load_match

load_dotenv()

# How each viewer type should be written for
FAN_MODES = {
    "analyst": "A data-hungry fan who wants depth. Use 2-3 precise numbers (xG, pass difficulty, "
               "momentum %, pressures) and explain the tactical mechanism. Max 40 words.",
    "casual": "A casual fan watching with friends. Plain everyday words, NO jargon (never say xG, "
              "expected goals, momentum index, chaos index or pass difficulty). At most one simple "
              "number such as the score or a minute. Warm and energetic. Max 30 words.",
    "social": "A social media caption for TikTok or Instagram. Punchy, max 20 words, at most one "
              "emoji and at most one hashtag. Written to stop the scroll. No jargon.",
}

# Rules every writing agent follows
HOUSE_STYLE = (
    "Style rules: write to UK broadcast standards (accurate, clear, no swearing). Always use full team names "
    "(never codes like NWW or SEE). Never show data field names (like chaos_index or "
    "momentum_last_10_min); describe them in words, e.g. 'a scrappy, end-to-end spell'. "
    "Only describe what has happened up to this minute; never mention the final result or "
    "anything later in the match."
)

# Pundit personalities: the same facts, a different character
PERSONALITIES = {            # one house style for every caption, recap and pundit answer
    "standard": ("Commentator", "Speak like a natural, relaxed British football commentator: clear, warm "
                                "and conversational, with a touch of humour. No forced slang."),
}

FALLBACK_TEMPLATES = {
    "goal": "{player} scores for {team}. It's {score}.",
    "big_save": "Big save from {player} to keep {team} in it.",
    "big_chance_missed": "Huge chance for {player}, but it doesn't go in.",
    "momentum_swing": "{team} are on top right now.",
    "pressing_surge": "{team} turn up the pressure.",
    "chaotic_spell": "A scrappy, end-to-end spell with possession changing hands.",
    "player_milestone": "{player} keeps the ball moving for {team}.",
}


@dataclass
class FanProfile:
    mode: str = "casual"
    language: str = "English"
    favourite_team: str | None = None      # team id, e.g. "NWW"
    favourite_player: str | None = None
    player_focus: bool = False             # only follow the favourite player
    personality: str = "standard"            # key of PERSONALITIES

    def describe(self, team_names):
        parts = [FAN_MODES[self.mode], PERSONALITIES.get(self.personality, PERSONALITIES["standard"])[1]]
        if self.favourite_team:
            parts.append(f"They support {team_names.get(self.favourite_team, self.favourite_team)}: "
                         "speak to a fan of that club, but stay fair and accurate.")
        if self.favourite_player:
            parts.append(f"Their favourite player is {self.favourite_player}. Mention him ONLY if he "
                         "was directly involved in this moment; otherwise do not mention him.")
        return " ".join(parts)


def tidy(text, max_chars=None):
    """Clean model output for on-screen use: one line, no stray whitespace."""
    text = re.sub(r"\s+", " ", str(text)).strip()
    if max_chars and len(text) > max_chars:
        text = text[:max_chars].rsplit(" ", 1)[0].rstrip(",;:-") + "…"
    return text


# ------------------------------------------------------------------ model access
class LLM:
    """Thin wrapper around the Microsoft Foundry model (or a mock for offline runs)."""

    def __init__(self):
        self.mock = os.getenv("MOCK_AI") == "1"
        self.calls = 0
        self._lock = threading.Lock()      # agents may run in parallel during the warm-up
        if not self.mock:
            from openai import OpenAI
            endpoint = os.environ["AZURE_OPENAI_ENDPOINT"].rstrip("/")
            # max_retries: back off and retry automatically if Azure rate-limits us
            self.client = OpenAI(base_url=f"{endpoint}/openai/v1/",
                                 api_key=os.environ["AZURE_OPENAI_API_KEY"], max_retries=6, timeout=60)
            self.model = os.environ["AZURE_OPENAI_DEPLOYMENT"]

    def count(self):
        with self._lock:
            self.calls += 1

    def json(self, system, user, mock_reply):
        """Ask the model for a JSON object. Returns a dict."""
        self.count()
        if self.mock:
            return mock_reply
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": system + "\nReply with a single JSON object only."},
                      {"role": "user", "content": user}],
            response_format={"type": "json_object"},
            reasoning_effort="low",
        )
        return json.loads(response.choices[0].message.content)


# ------------------------------------------------------------------ the agents
class AnalystAgent:
    SYSTEM = (
        "You are a football analyst for a TV studio, working live. You receive one key moment and the "
        "match statistics AS THEY STAND AT THAT MINUTE. Explain WHY the moment matters, not just what "
        "happened: control vs chaos, shifts in pressing, momentum and game rhythm. Use ONLY numbers "
        "that appear in the data. " + HOUSE_STYLE +
        " Return JSON: {\"headline\": str (max 6 words), \"why_it_matters\": str (max 2 short "
        "sentences), \"theme\": one of [\"momentum\", \"control_vs_chaos\", \"pressing\", \"finishing\", "
        "\"goalkeeping\", \"individual_performance\"], \"key_stat\": {\"label\": str (plain words), "
        "\"value\": str}}"
    )

    def __init__(self, llm):
        self.llm = llm

    def run(self, moment, snapshot):
        user = json.dumps({"moment": moment, "match_stats_at_this_minute": snapshot})
        mock = {
            "headline": f"{moment['type'].replace('_', ' ').title()}",
            "why_it_matters": "Why it matters: " + ", ".join(
                w.replace("_", " ") for w in moment["why_signals"]) + ".",
            "theme": "momentum",
            "key_stat": {"label": next(iter(moment["facts"]), "minute").replace("_", " "),
                         "value": str(next(iter(moment["facts"].values()), moment["minute"]))},
        }
        return self.llm.json(self.SYSTEM, user, mock)


class FanVoiceAgent:
    SYSTEM = (
        "You turn a studio analyst's insight into an on-screen caption for one specific viewer. "
        "Keep every fact and number exactly as given; never add new statistics. Write in English. "
        + HOUSE_STYLE +
        " Return JSON: {\"title\": str (max 6 words), \"body\": str}"
    )

    def __init__(self, llm):
        self.llm = llm

    def run(self, insight, moment, profile, team_names, home_away, feedback=None):
        user = json.dumps({"viewer": profile.describe(team_names), "insight": insight,
                           "moment_facts": moment["facts"], "player": moment["player"],
                           "team": moment["team_name"], "minute": moment["display_minute"],
                           "score_home_away": moment["score"], "teams": home_away,
                           "fix_these_problems": feedback})
        mock = {"title": insight["headline"], "body": insight["why_it_matters"]}
        return self.llm.json(self.SYSTEM, user, mock)


class FactCheckerAgent:
    SYSTEM = (
        "You are a strict fact-checker for live sports graphics. Compare the caption with the source "
        "data. Reject it if any number, name, score or claim is not supported by the source data, or "
        "if it mentions anything that happens after this minute. Also reject it if it shows data field "
        "names (words with underscores) or team codes instead of team names. "
        "Return JSON: {\"approved\": bool, \"problems\": [str]}"
    )

    def __init__(self, llm):
        self.llm = llm

    @staticmethod
    def numbers_in(text):
        return set(re.findall(r"\d+(?:\.\d+)?", text))

    def allowed_numbers(self, moment, snapshot):
        """Every number in the source data, plus sensible roundings (0.459 -> 0.46, 0.5, 0)."""
        allowed = set()
        for n in self.numbers_in(json.dumps({"m": moment, "s": snapshot})):
            allowed.add(n)
            value = float(n)
            for digits in (0, 1, 2):
                r = round(value, digits)
                allowed.add(str(int(r)) if digits == 0 else str(r))
            if 0 < value < 1:                         # xG 0.459 may be shown as 46%
                allowed.add(str(round(value * 100)))
        return allowed

    def run(self, caption, moment, snapshot):
        caption_text = caption["title"] + " " + caption["body"]
        problems = []
        # 1) Fast rule-based checks before spending an AI call.
        unknown = sorted(n for n in self.numbers_in(caption_text)
                         if n not in self.allowed_numbers(moment, snapshot))
        if unknown:
            problems.append(f"These numbers are not in the match data: {', '.join(unknown)}")
        if re.search(r"\b[a-z]+_[a-z_]+\b", caption_text):
            problems.append("Do not show data field names with underscores; describe them in words.")
        if re.search(r"\b(NWW|SEE)\b", caption_text):
            problems.append("Use full team names, not team codes.")
        if problems:
            return {"approved": False, "problems": problems}
        # 2) Model check for names, claims and spoilers.
        user = json.dumps({"caption": caption,
                           "source": {"moment": moment, "match_stats_at_this_minute": snapshot}})
        return self.llm.json(self.SYSTEM, user, {"approved": True, "problems": []})


class TranslatorAgent:
    SYSTEM = (
        "Translate this on-screen football caption naturally for fans who speak the target language. "
        "Translate EVERY word, including stat labels; keep player names, team names and numbers "
        "unchanged and keep the same tone and length. Return JSON: {\"title\": str, \"body\": str}"
    )

    def __init__(self, llm):
        self.llm = llm

    def run(self, caption, language):
        if language.lower() == "english":
            return caption
        user = json.dumps({"target_language": language, "caption": caption})
        return self.llm.json(self.SYSTEM, user, {"title": f"[{language}] {caption['title']}",
                                                 "body": f"[{language}] {caption['body']}"})


class StorytellerAgent:
    SYSTEM = (
        "You write the full-time match recap for a streaming app. Use the key moments in order and the "
        "final statistics. Tell the story of the match (how control and momentum shifted), not a list. "
        "Use ONLY numbers from the data, and get home and away right. " + HOUSE_STYLE.split(
            " Only describe")[0] +
        " Write for this viewer: {viewer} Write in {language}. "
        "Return JSON: {\"headline\": str, \"recap\": str (100-140 words), \"player_of_the_match\": str, "
        "\"player_of_the_match_reason\": str (one sentence)}"
    )

    def __init__(self, llm):
        self.llm = llm

    def run(self, moments, insights, snapshot, profile, team_names):
        system = self.SYSTEM.replace("{viewer}", profile.describe(team_names)) \
                            .replace("{language}", profile.language)
        timeline = [{"minute": m["display_minute"], "type": m["type"], "player": m["player"],
                     "team": m["team_name"], "insight": i} for m, i in zip(moments, insights)]
        user = json.dumps({"home_team": snapshot["home_team"], "away_team": snapshot["away_team"],
                           "timeline": timeline, "final_stats": snapshot})
        top = snapshot["top_players"][0]["name"] if snapshot["top_players"] else "n/a"
        mock = {"headline": "Full time", "recap": f"{len(moments)} key moments analysed.",
                "player_of_the_match": top, "player_of_the_match_reason": "Most influential on the data."}
        return self.llm.json(system, user, mock)


class SocialPackAgent:
    """Turns a big moment into a ready-to-post social package for a club's content team."""
    SYSTEM = (
        "You are a social media creative at a football club's content team. From one verified "
        "on-screen caption, create a ready-to-post package. Keep every fact and number exactly as "
        "given; never add new statistics. Always use full team names. Match this voice: {voice} "
        "Return JSON: {\"hook\": str (first 3 seconds of a video, max 8 words), "
        "\"caption\": str (max 30 words, max 1 emoji), \"on_screen_text\": str (max 6 words), "
        "\"format_idea\": str (one sentence: a platform-native video or meme format to use), "
        "\"hashtags\": [max 3 strings], \"best_platform\": one of [\"TikTok\", \"Instagram\", "
        "\"X\", \"YouTube Shorts\"]}"
    )

    def __init__(self, llm):
        self.llm = llm

    def run(self, card, moment, profile):
        voice = PERSONALITIES.get(profile.personality, PERSONALITIES["standard"])[1]
        user = json.dumps({"caption": {"title": card["title"], "body": card["body"]},
                           "moment": {"type": moment["type"], "minute": moment["display_minute"],
                                      "player": moment["player"], "team": moment["team_name"],
                                      "score_home_away": moment["score"]}})
        mock = {"hook": card["title"], "caption": card["body"], "on_screen_text": card["title"],
                "format_idea": "Quick-cut replay with on-screen text.", "hashtags": ["#Matchday"],
                "best_platform": "TikTok"}
        return self.llm.json(self.SYSTEM.replace("{voice}", voice), user, mock)


# ------------------------------------------------------------------ orchestration
@dataclass
class Orchestrator:
    profile: FanProfile
    llm: LLM = field(default_factory=LLM)
    max_fix_attempts: int = 2

    def __post_init__(self):
        self.analyst = AnalystAgent(self.llm)
        self.voice = FanVoiceAgent(self.llm)
        self.checker = FactCheckerAgent(self.llm)
        self.translator = TranslatorAgent(self.llm)
        self.storyteller = StorytellerAgent(self.llm)
        self.social = SocialPackAgent(self.llm)
        self.log = []   # what each agent did, for transparency in the UI

    def is_relevant(self, moment):
        """Personalisation: decide which moments this viewer sees."""
        p = self.profile
        involved = (p.favourite_player and moment["player"] == p.favourite_player) or \
                   (p.favourite_team and moment["team"] == p.favourite_team and moment["importance"] >= 2)
        if p.player_focus and p.favourite_player:
            return moment["player"] == p.favourite_player or moment["importance"] == 3
        if p.mode in ("casual", "social"):
            return moment["importance"] >= 2 or bool(involved)
        return True

    @staticmethod
    def fallback_caption(moment):
        """Safe, plain caption built only from the data, used if the AI can't pass the fact-check."""
        template = FALLBACK_TEMPLATES.get(moment["type"], "Key moment for {team}.")
        home, away = moment["score"].split("-")
        body = template.format(player=moment["player"] or "", team=moment["team_name"] or "",
                               score=f"{home}-{away}")
        return {"title": moment["type"].replace("_", " ").capitalize(), "body": body}

    def process_moment(self, moment, snapshot, team_names):
        """Run one key moment through the agent team and return an overlay card."""
        home_away = {"home": snapshot["home_team"], "away": snapshot["away_team"]}
        insight = self.analyst.run(moment, snapshot)
        self.log.append(("Analyst", moment["moment_id"], insight["headline"]))

        feedback, caption, verdict = None, None, None
        for _ in range(self.max_fix_attempts + 1):
            caption = self.voice.run(insight, moment, self.profile, team_names, home_away, feedback)
            caption = {"title": tidy(caption["title"], 48), "body": tidy(caption["body"], 280)}
            verdict = self.checker.run(caption, moment, snapshot)
            self.log.append(("Fact-checker", moment["moment_id"],
                             "approved" if verdict["approved"] else f"rejected: {verdict['problems']}"))
            if verdict["approved"]:
                break
            feedback = verdict["problems"]
        if not verdict["approved"]:
            caption = self.fallback_caption(moment)   # never show an unverified claim on screen

        final = self.translator.run(caption, self.profile.language)
        return {
            "card_id": f"{moment['moment_id']:03d}",
            "show_at_clock_s": moment["clock_s"],
            "duration_s": 8 if moment["importance"] == 3 else 6,
            "display_minute": moment["display_minute"],
            "moment_type": moment["type"],
            "team": moment["team"],
            "player": moment["player"],
            "theme": insight.get("theme"),
            "title": tidy(final["title"], 60),
            "body": tidy(final["body"], 320),
            "stat_chip": insight.get("key_stat"),
            "verified": verdict["approved"],
            "language": self.profile.language,
        }

    def run_match(self, until_minute=None, on_card=None):
        """Replay the match like a live feed. Each moment is analysed with the stats as they were
        at that minute. `on_card` is called with every new card (used by the live UI)."""
        meta, events = load_match()
        state = MatchState(meta)
        cards, moments, insights = [], [], []
        for e in events:
            if until_minute is not None and e["minute"] > until_minute:
                break
            for m in state.ingest(e):
                if not self.is_relevant(m):
                    continue
                card = self.process_moment(m, state.snapshot(), state.names)
                cards.append(card)
                moments.append(m)
                insights.append({"headline": card["title"], "why": card["body"]})
                if on_card:
                    on_card(card)
        recap = self.storyteller.run(moments, insights, state.snapshot(), self.profile, state.names)
        return {"profile": self.profile.__dict__, "overlay_cards": cards, "recap": recap,
                "final_stats": state.snapshot(), "agent_log": self.log,
                "model_calls": self.llm.calls}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=FAN_MODES, default="casual")
    parser.add_argument("--language", default="English")
    parser.add_argument("--team", default=None, help="favourite team id, e.g. NWW or SEE")
    parser.add_argument("--player", default=None, help="favourite player name")
    parser.add_argument("--player-focus", action="store_true")
    parser.add_argument("--minute", type=int, default=None)
    args = parser.parse_args()

    profile = FanProfile(args.mode, args.language, args.team, args.player, args.player_focus)
    result = Orchestrator(profile).run_match(args.minute)

    for c in result["overlay_cards"]:
        tick = "verified" if c["verified"] else "fallback"
        print(f"\n{c['display_minute']:>6}  {c['title']}  ({tick})\n        {c['body']}")
    r = result["recap"]
    print(f"\n=== {r['headline']} ===\n{r['recap']}\n"
          f"Player of the match: {r['player_of_the_match']} - {r['player_of_the_match_reason']}")
    print(f"\n({result['model_calls']} AI calls)")

    os.makedirs("output", exist_ok=True)
    with open("output/overlay_cards.json", "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print("Saved machine-readable overlay cards to output/overlay_cards.json")


if __name__ == "__main__":
    main()
