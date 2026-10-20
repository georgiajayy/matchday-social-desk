from stats_engine import replay
from agents import FactCheckerAgent, LLM
import pytest
import os

os.environ["MOCK_AI"] = "1"


@pytest.fixture
def fact_checker_source():
    state = replay(until_minute=10)
    moment = next(
        moment for moment in state.key_moments
        if moment["facts"].get("xg") == 0.459
    )
    return FactCheckerAgent(LLM()), moment, state.snapshot()


def test_approves_caption_with_rounded_match_number(fact_checker_source):
    checker, moment, snapshot = fact_checker_source
    caption = {
        "title": "A clinical finish",
        "body": "Darnell Hayes scores for North West Wonders with an xG of 0.46.",
    }

    result = checker.run(caption, moment, snapshot)

    assert result["approved"] is True
    assert result["problems"] == []


@pytest.mark.parametrize(
    "body, expected_problem",
    [
        ("The chance had an xG of 987654.",
         "These numbers are not in the match data"),
        ("NWW take the lead.", "Use full team names, not team codes"),
        ("The chaos_index rises.", "data field names with underscores"),
    ],
)
def test_rejects_unsupported_caption_content(fact_checker_source, body, expected_problem):
    checker, moment, snapshot = fact_checker_source

    result = checker.run(
        {"title": "Match update", "body": body}, moment, snapshot)

    assert result["approved"] is False
    assert any(expected_problem in problem for problem in result["problems"])
