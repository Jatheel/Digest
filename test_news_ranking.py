import json
import shutil
import subprocess
import time
import unittest
from pathlib import Path

import ranking

ROOT = Path(__file__).parent
NOW = 1_790_000_000.0


def story(story_id, title, keywords, hours_old=2.0, topic="World", sources=1, trust=0.9, social=0, best_source="BBC"):
    return {
        "id": story_id,
        "topic": topic,
        "title": title,
        "published_ts": NOW - hours_old * 3600,
        "best_source": best_source,
        "keywords": keywords,
        "features": {
            "source_trust": trust,
            "source_count": sources,
            "social_count": social,
            "local_source": 0,
            "local_mention": 0,
            "is_social_only": 0,
        },
    }


BUDGET = story("budget", "Parliament passes budget", ["parliament", "budget", "parliament_budget"])
BUDGET_2 = story("budget2", "Budget debate continues in parliament", ["budget", "debate", "parliament", "budget_debate"], hours_old=5)
FOOTBALL = story("football", "Liverpool beat City at Anfield", ["liverpool", "city", "anfield"], hours_old=1, topic="Sports")
FOOTBALL_2 = story("football2", "Liverpool manager praises defence", ["liverpool", "manager", "defence"], hours_old=4, topic="Sports")


class RankingModelTests(unittest.TestCase):
    def setUp(self):
        self.defaults = ranking.load_defaults()

    def test_fnv1a_matches_reference_values(self):
        self.assertEqual(ranking.fnv1a(""), 0x811C9DC5)
        self.assertEqual(ranking.fnv1a("a"), 0xE40C292C)
        self.assertEqual(ranking.fnv1a("foobar"), 0xBF9CF968)

    def test_default_model_prefers_fresh_widely_reported_trusted_news(self):
        big = story("big", "Big story", ["big"], hours_old=1, sources=4, trust=0.95, social=3)
        small = story("small", "Small story", ["small"], hours_old=30, sources=1, trust=0.6)
        self.assertGreater(
            ranking.score_story(self.defaults, big, NOW),
            ranking.score_story(self.defaults, small, NOW),
        )

    def test_training_without_events_keeps_default_weights(self):
        model = ranking.train(self.defaults, [])
        self.assertEqual(model["dense"], self.defaults["dense"])
        self.assertEqual(model["sparse"], self.defaults["sparse"])

    def test_interested_raises_similar_stories(self):
        before = ranking.score_story(self.defaults, BUDGET_2, NOW)
        model = ranking.train(self.defaults, [ranking.make_event(BUDGET, "interested", NOW)])
        after = ranking.score_story(model, BUDGET_2, NOW)
        self.assertGreater(after, before + 0.03)

    def test_not_interested_lowers_similar_stories(self):
        before = ranking.score_story(self.defaults, FOOTBALL_2, NOW)
        model = ranking.train(self.defaults, [ranking.make_event(FOOTBALL, "not_interested", NOW)])
        after = ranking.score_story(model, FOOTBALL_2, NOW)
        self.assertLess(after, before - 0.03)

    def test_moderate_lands_between_interested_and_not_interested(self):
        scores = {}
        for reaction in ("interested", "moderate", "not_interested"):
            model = ranking.train(self.defaults, [ranking.make_event(BUDGET, reaction, NOW)])
            scores[reaction] = ranking.score_story(model, BUDGET_2, NOW)
        self.assertGreater(scores["interested"], scores["moderate"])
        self.assertGreater(scores["moderate"], scores["not_interested"])

    def test_for_you_order_differs_from_latest_after_feedback(self):
        events = [
            ranking.make_event(BUDGET, "interested", NOW),
            ranking.make_event(FOOTBALL, "not_interested", NOW),
        ]
        model = ranking.train(self.defaults, events)
        candidates = [BUDGET_2, FOOTBALL_2]
        latest = sorted(candidates, key=lambda s: -s["published_ts"])
        for_you = sorted(candidates, key=lambda s: -ranking.score_story(model, s, NOW))
        self.assertEqual([s["id"] for s in latest], ["football2", "budget2"])
        self.assertEqual([s["id"] for s in for_you], ["budget2", "football2"])

    def test_topic_preferences_carry_over_from_defaults(self):
        local = dict(BUDGET, topic="Sri Lanka", id="local")
        science = dict(BUDGET, topic="Science", id="science")
        self.assertGreater(
            ranking.score_story(self.defaults, local, NOW),
            ranking.score_story(self.defaults, science, NOW),
        )

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_javascript_model_matches_python(self):
        """static/model.js (phone) and ranking.py must score identically."""
        fixture = json.loads((ROOT / "tests" / "fixtures" / "model_fixture.json").read_text(encoding="utf-8"))
        events = [ranking.make_event(fixture["stories"][e["story"]], e["reaction"], fixture["now"]) for e in fixture["events"]]
        model = ranking.train(self.defaults, events)
        expected = [ranking.score_story(model, s, fixture["now"]) for s in fixture["stories"]]
        result = subprocess.run(
            ["node", str(ROOT / "tests" / "score_fixture.mjs")],
            capture_output=True, text=True, check=True, timeout=60,
        )
        actual = json.loads(result.stdout)
        self.assertEqual(len(actual), len(expected))
        for a, e in zip(actual, expected):
            self.assertAlmostEqual(a, e, places=9)


if __name__ == "__main__":
    unittest.main()
