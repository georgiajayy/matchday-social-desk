"""
pundit.py
"Ask the pundit": a tool-using agent that answers fans' questions about the match.

The model can't see the data directly. It has to decide which tools to call
(team stats, player stats, key moments, at any minute) and answer only from what the
tools return. That keeps answers grounded and explainable.

Test:  python pundit.py "Why did the Eagles stop creating chances?"
"""

import json
import sys

from agents import LLM, PERSONALITIES
from stats_engine import replay

TOOLS = [
    {"type": "function", "function": {
        "name": "get_match_stats",
        "description": "Team statistics, score, momentum and chaos index at a given minute "
                       "(default: the latest minute available).",
        "parameters": {"type": "object", "properties": {
            "minute": {"type": "integer", "description": "Match minute, 0-95"}}}}},
    {"type": "function", "function": {
        "name": "get_player_stats",
        "description": "One player's statistics at a given minute (default: latest).",
        "parameters": {"type": "object", "properties": {
            "player_name": {"type": "string"},
            "minute": {"type": "integer"}}, "required": ["player_name"]}}},
    {"type": "function", "function": {
        "name": "get_key_moments",
        "description": "Key moments (goals, saves, momentum swings, pressing surges, chaotic spells) "
                       "with the facts and reasons the stats engine recorded, up to a minute.",
        "parameters": {"type": "object", "properties": {
            "up_to_minute": {"type": "integer"}}}}},
    {"type": "function", "function": {
        "name": "list_players",
        "description": "Squad lists for both teams.",
        "parameters": {"type": "object", "properties": {}}}},
]


class PunditAgent:
    SYSTEM = (
        "You are the studio pundit for a live football broadcast. Fans ask you questions about the "
        "match. Use the tools to look up the facts before answering: never guess numbers. Answer in "
        "2-4 sentences, explain WHY (tactics, momentum, control vs chaos), use full team names and "
        "plain words for data (no field names with underscores). The match has only been played up to "
        "minute {limit}: never talk about anything after that. If the data can't answer the question, "
        "say so honestly. Voice: {voice} Answer in {language}."
    )

    def __init__(self, limit_minute=95, language="English", personality="standard", llm=None):
        self.limit = limit_minute
        self.language = language
        self.personality = personality
        self.llm = llm or LLM()
        self.tool_log = []

    # ---- tools (the only way the model can see the match) ----
    def _minute(self, m):
        return self.limit if m is None else max(0, min(int(m), self.limit))

    def get_match_stats(self, minute=None):
        snap = replay(self._minute(minute)).snapshot()
        snap.pop("top_players", None)
        return snap

    def get_player_stats(self, player_name, minute=None):
        state = replay(self._minute(minute))
        match = next((n for n in state.players if n.lower() == str(player_name).lower()), None) or \
            next((n for n in state.players if str(player_name).lower() in n.lower()), None)
        if not match:
            return {"error": f"No player called {player_name}. Use list_players."}
        return {"name": match, **state.player_report(match)}

    def get_key_moments(self, up_to_minute=None):
        state = replay(self._minute(up_to_minute))
        return [{k: m[k] for k in ("display_minute", "type", "team_name", "player", "facts", "why_signals")}
                for m in state.key_moments]

    def list_players(self):
        state = replay(0)
        meta = state.meta
        return {meta[s]["name"]: [f"{p['number']} {p['name']} ({p['position']})" for p in meta[s]["players"]]
                for s in ("home", "away")}

    def _call_tool(self, name, args):
        fn = getattr(self, name, None)
        if name not in {t["function"]["name"] for t in TOOLS} or fn is None:
            return {"error": "unknown tool"}
        try:
            return fn(**args)
        except Exception as e:      # keep the conversation alive if a tool fails
            return {"error": str(e)}

    # ---- the agent loop ----
    def ask(self, question, history=None):
        if self.llm.mock:
            self.tool_log.append(("get_match_stats", {}))
            s = self.get_match_stats()
            return (f"(Offline mode) At {s['display_minute']} it's {s['home_team']} "
                    f"{s['score']['home']}-{s['score']['away']} {s['away_team']}.")
        voice = PERSONALITIES.get(self.personality, PERSONALITIES["standard"])[1]
        system = self.SYSTEM.format(limit=self.limit, voice=voice, language=self.language)
        messages = [{"role": "system", "content": system}] + (history or []) + \
                   [{"role": "user", "content": question}]
        for _ in range(5):                       # up to 5 rounds of tool use
            self.llm.count()
            resp = self.llm.client.chat.completions.create(
                model=self.llm.model, messages=messages, tools=TOOLS, reasoning_effort="low")
            msg = resp.choices[0].message
            if not msg.tool_calls:
                return msg.content
            messages.append({"role": "assistant", "content": msg.content or "",
                             "tool_calls": [tc.model_dump() for tc in msg.tool_calls]})
            for tc in msg.tool_calls:
                args = json.loads(tc.function.arguments or "{}")
                self.tool_log.append((tc.function.name, args))
                result = self._call_tool(tc.function.name, args)
                messages.append({"role": "tool", "tool_call_id": tc.id,
                                 "content": json.dumps(result, ensure_ascii=False)})
        return "Sorry, I couldn't pin that down from the data."


if __name__ == "__main__":
    q = " ".join(sys.argv[1:]) or "Why did the Eagles stop creating chances?"
    pundit = PunditAgent()
    print(pundit.ask(q))
    print("\nTools used:", pundit.tool_log)
