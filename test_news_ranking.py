import unittest

from main import deduplicate_articles, logistic_news_score, filter_seen_articles, filter_stale_articles


class NewsRankingTests(unittest.TestCase):
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
