import asyncio
import unittest
from unittest.mock import patch

from main import (
    TopicCache,
    USER_PROFILE,
    _cache,
    deduplicate_articles,
    filter_seen_articles,
    filter_stale_articles,
    generate_full_summary,
    get_story_details,
    logistic_news_score,
    record_feedback,
)


class NewsRankingTests(unittest.TestCase):
    def test_interested_feedback_does_not_block_source(self):
        original_profile = {key: value.copy() if isinstance(value, (list, set)) else value for key, value in USER_PROFILE.items()}
        try:
            USER_PROFILE["blocked_sources"] = []
            USER_PROFILE["liked_keywords"] = []
            with patch("main._save_profile"):
                asyncio.run(record_feedback({
                    "label": "interested",
                    "title": "Budget update",
                    "source": "Test Source",
                    "link": "https://example.com/interested",
                }))
            self.assertEqual(USER_PROFILE["blocked_sources"], [])
        finally:
            USER_PROFILE.clear()
            USER_PROFILE.update(original_profile)

    def test_newsfirst_is_trusted_and_allowed_in_strict_local_mode(self):
        article = {
            "title": "President addresses parliament on new budget",
            "source": "Newsfirst",
            "summary": "",
            "published": "2026-09-30T12:00:00+00:00",
        }
        profile = {
            "trusted_sources": ["Newsfirst"],
            "blocked_sources": [],
            "strict_local_only": True,
        }
        score = logistic_news_score(article, "Sri Lanka", profile)
        self.assertGreater(score, 0.9)

    def test_logistic_score_prioritizes_trusted_local_news_and_recent_items(self):
        article = {
            "title": "Sri Lanka economy expands in first quarter",
            "summary": "Growth improves amid strong export demand.",
            "source": "Daily Mirror",
            "published": "2026-09-30T12:00:00+00:00",
            "link": "https://example.com/local-growth",
        }
        profile = {
            "topic_weights": {"Sri Lanka": 1.7},
            "liked_keywords": ["economy", "growth"],
            "disliked_keywords": ["crime"],
            "blocked_sources": [],
        }
        score = logistic_news_score(article, "Sri Lanka", profile)
        self.assertGreater(score, 0.7)

    def test_deduplicate_articles_keeps_the_best_version(self):
        articles = [
            {
                "title": "Sri Lanka parliament passes budget",
                "source": "Ada Derana",
                "published": "2026-09-30T09:00:00+00:00",
                "link": "https://example.com/story-1",
            },
            {
                "title": "Sri Lanka parliament passes budget",
                "source": "News First",
                "published": "2026-09-30T10:00:00+00:00",
                "link": "https://example.com/story-2",
            },
        ]
        result = deduplicate_articles(articles)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["source"], "News First")

    def test_deduplicate_articles_preserves_related_sources(self):
        articles = [
            {
                "title": "Parliament approves national budget",
                "source": "Ada Derana",
                "published": "2026-09-30T09:00:00+00:00",
                "summary": "The vote passed after debate.",
                "link": "https://example.com/story-1",
            },
            {
                "title": "Parliament passes national budget",
                "source": "BBC",
                "published": "2026-09-30T10:00:00+00:00",
                "summary": "Lawmakers approved the budget.",
                "link": "https://example.com/story-2",
            },
        ]
        result = deduplicate_articles(articles)
        self.assertEqual(len(result), 1)
        self.assertEqual(len(result[0]["sources"]), 2)
        self.assertEqual({source["name"] for source in result[0]["sources"]}, {"Ada Derana", "BBC"})

    def test_full_summary_removes_near_duplicate_sentences(self):
        sources = [
            {"summary": "The bill passed parliament. The vote was unanimous."},
            {"summary": "The bill passed Parliament. A new committee will review it."},
        ]
        result = generate_full_summary("Budget bill", sources)
        self.assertEqual(result.count("bill passed"), 1)
        self.assertIn("The vote was unanimous.", result)
        self.assertIn("A new committee will review it.", result)

    def test_details_endpoint_reads_only_cached_story(self):
        original_cache = dict(_cache)
        try:
            article = {
                "id": "story-test",
                "summary": "Preview",
                "full_summary": "Cached full summary.",
                "best_link": "https://example.com/best",
                "best_source": "BBC",
                "sources": [{"name": "BBC", "url": "https://example.com/best"}],
            }
            _cache.clear()
            _cache["Test"] = TopicCache(articles=[article], fetched_at=1.0)
            result = asyncio.run(get_story_details("story-test"))
            self.assertEqual(result["full_summary"], "Cached full summary.")
            self.assertEqual(result["best_source"], "BBC")
        finally:
            _cache.clear()
            _cache.update(original_cache)

    def test_filter_seen_articles_hides_user_rejected_items(self):
        articles = [
            {"title": "New port deal signed", "source": "News First", "link": "https://example.com/port"},
            {"title": "Second story", "source": "Daily Mirror", "link": "https://example.com/second"},
        ]
        seen = {"https://example.com/port"}
        result = filter_seen_articles(articles, seen)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["link"], "https://example.com/second")

    def test_filter_stale_articles_removes_old_news(self):
        articles = [
            {"title": "Very old story", "source": "Ada Derana", "published": "Wed, 14 Sep 2011 07:48:40 GMT", "link": "https://example.com/old"},
            {"title": "Fresh story", "source": "Daily Mirror", "published": "Tue, 29 Sep 2026 09:47:00 GMT", "link": "https://example.com/fresh"},
        ]
        result = filter_stale_articles(articles)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["link"], "https://example.com/fresh")

    def test_filter_stale_articles_respects_three_month_limit(self):
        old_date = "Tue, 01 Jan 2025 09:00:00 GMT"
        recent_date = "Tue, 29 Sep 2026 09:47:00 GMT"
        articles = [
            {"title": "Old but trusted", "source": "Ada Derana", "published": old_date, "link": "https://example.com/old-trusted"},
            {"title": "Recent story", "source": "Daily Mirror", "published": recent_date, "link": "https://example.com/recent"},
        ]
        result = filter_stale_articles(articles)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["link"], "https://example.com/recent")


if __name__ == "__main__":
    unittest.main()
