import asyncio
import time
import unittest
from email.utils import formatdate
from unittest.mock import patch

import feedparser

import main
import news_pipeline as p
import summarizer

NOW = time.time()


def item(title, source, kind="news", hours_old=1.0, text="", link=None, article_url=None, platform=None):
    link = link or f"https://example.com/{source.replace(' ', '').lower()}/{abs(hash(title))}"
    result = {
        "title": title,
        "text": text,
        "link": link,
        "article_url": article_url if article_url is not None else (link if kind == "news" else ""),
        "source": source,
        "kind": kind,
        "published_ts": NOW - hours_old * 3600,
    }
    if platform or kind in p.SOCIAL_KINDS:
        result["platform"] = platform or kind
    result["title_tokens"] = set(p.content_tokens(title))
    result["lead_tokens"] = set(p.content_tokens(" ".join(text.split()[:30])))
    return result


def rss(entries):
    body = "".join(
        f"<item><title>{title}</title><link>{link}</link><description>{desc}</description>"
        f"<pubDate>{formatdate(ts, usegmt=True)}</pubDate></item>"
        for title, link, desc, ts in entries
    )
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>Test feed</title>{body}</channel></rss>'.encode()


FILLER = [
    item("Central bank holds interest rates steady", "CNBC"),
    item("Wildfire forces evacuations in California", "NPR"),
    item("New species of frog found in Amazon", "ScienceDaily"),
    item("Tennis star wins open final", "BBC Sport"),
    item("Elections due in Japan next spring", "The Guardian"),
]


class GroupingTests(unittest.TestCase):
    def test_reports_of_the_same_event_are_grouped(self):
        items = FILLER + [
            item("Former German spy chief arrested for espionage and treason", "BBC"),
            item("Former German spy chief arrested on suspicion of treason and espionage", "Al Jazeera"),
            item("Former German spy chief arrested on espionage charges", "NPR"),
        ]
        groups = p.group_items(items)
        spy = [g for g in groups if any("spy" in i["title"] for i in g["items"])]
        self.assertEqual(len(spy), 1)
        self.assertEqual({i["source"] for i in spy[0]["items"]}, {"BBC", "Al Jazeera", "NPR"})

    def test_unrelated_reports_stay_separate(self):
        groups = p.group_items(FILLER)
        self.assertEqual(len(groups), len(FILLER))

    def test_grouping_respects_48_hour_window(self):
        items = FILLER + [
            item("Parliament passes national budget", "Ada Derana", hours_old=1),
            item("Parliament passes national budget", "Daily Mirror", hours_old=60),
        ]
        groups = p.group_items(items)
        budget = [g for g in groups if "budget" in g["items"][0]["title"]]
        self.assertEqual(len(budget), 2)

    def test_social_post_joins_matching_news_story(self):
        items = FILLER + [
            item("Kenya confirms first Ebola case after patient dies in Nairobi", "BBC"),
            item("Kenya confirms first Ebola case after patient from DR Congo dies", "Mastodon @news", kind="mastodon"),
        ]
        groups = p.group_items(items)
        ebola = [g for g in groups if "Ebola" in g["items"][0]["title"]]
        self.assertEqual(len(ebola), 1)
        self.assertEqual({i["kind"] for i in ebola[0]["items"]}, {"news", "mastodon"})

    def test_social_post_sharing_the_article_link_joins_even_with_different_words(self):
        article = "https://www.bbc.co.uk/news/articles/abc"
        items = FILLER + [
            item("Ship sinks off Bulgaria after drone attack", "BBC", link=article),
            item("Wow, look at this", "r/worldnews", kind="reddit", article_url=article,
                 link="https://www.reddit.com/r/worldnews/comments/1"),
        ]
        groups = p.group_items(items)
        ship = [g for g in groups if g["items"][0]["link"] == article][0]
        self.assertEqual(len(ship["items"]), 2)


class StoryTests(unittest.TestCase):
    def test_story_keeps_every_source_and_prefers_trusted_direct_links(self):
        group = {"items": [
            item("Parliament passes budget", "Lanka Times", text="The vote passed after a long debate on spending plans."),
            item("Parliament passes budget", "Ada Derana", text="Parliament approved the 2027 budget by a large majority on Tuesday evening."),
            item("Parliament passes budget", "Reuters", link="https://news.google.com/rss/articles/xyz"),
        ]}
        story = p.build_story(group, "Sri Lanka")
        self.assertEqual(story["best_source"], "Ada Derana")
        self.assertEqual({s["name"] for s in story["sources"]}, {"Lanka Times", "Ada Derana", "Reuters"})
        self.assertEqual(story["features"]["source_count"], 3)
        self.assertEqual(story["features"]["local_source"], 1)

    def test_story_ids_are_stable_as_new_reports_join(self):
        first = item("Port deal signed in Colombo", "Daily Mirror", hours_old=5)
        later = item("Colombo port deal signed with investors", "Ada Derana", hours_old=1)
        self.assertEqual(p.stable_story_id([first]), p.stable_story_id([first, later]))

    def test_outlet_listed_once_preferring_direct_link(self):
        direct = "https://www.bbc.co.uk/news/articles/1"
        group = {"items": [
            item("Ship sinks in Black Sea", "BBC", link="https://news.google.com/rss/articles/1"),
            item("Ship sinks in Black Sea", "BBC", link=direct),
        ]}
        story = p.build_story(group, "World")
        self.assertEqual([s["url"] for s in story["sources"]], [direct])

    def test_lone_social_post_without_link_is_dropped(self):
        group = {"items": [item("Just a random thought about the news today", "Mastodon @x", kind="mastodon")]}
        self.assertIsNone(p.build_story(group, "World"))

    def test_social_only_story_with_article_link(self):
        group = {"items": [item("Big new satellite launched today by agency", "Reuters", kind="bluesky",
                                article_url="https://example.org/satellite")]}
        story = p.build_story(group, "Science")
        self.assertEqual(story["features"]["is_social_only"], 1)
        self.assertEqual(story["best_link"], "https://example.org/satellite")
        self.assertEqual(story["social"][0]["platform"], "bluesky")


class FocusTests(unittest.TestCase):
    """Sports: football in full, other sports only when popular.
    Technology: AI & IT on top."""

    def sports_items(self):
        football = item("Arsenal beat Chelsea 2-1 in London derby", "BBC Sport")
        football["feed_focus"] = True
        nfl = item("Eagles quarterback ruled out of NFL game", "ESPN")
        tennis_popular = item("Verstappen wins Singapore Grand Prix", "BBC Sport")
        tennis_popular["feed_top"] = True
        cricket_quiet = item("County cricket club signs new bowler", "ESPNcricinfo")
        return FILLER + [football, nfl, tennis_popular, cricket_quiet]

    def test_football_detection_ignores_american_football(self):
        self.assertTrue(p._matches_football("England 3-0 Czech Republic: Harry Kane marks record"))
        self.assertTrue(p._matches_football("Transfer news: Liverpool eye striker"))
        self.assertFalse(p._matches_football("Eagles quarterback ruled out of NFL game"))
        self.assertFalse(p._matches_football("Sri Lanka beat India in cricket World Cup"))

    def test_ai_it_detection(self):
        self.assertTrue(p._matches_ai_it("OpenAI releases a new model"))
        self.assertTrue(p._matches_ai_it("Hackers exploit zero-day in Jira"))
        self.assertTrue(p._matches_ai_it("New AI-powered phone launched"))
        self.assertFalse(p._matches_ai_it("Kai Havertz scores twice"))
        self.assertFalse(p._matches_ai_it("Apple TV adds a new comedy series"))
        # Shopping posts and consumer stories that only mention software in passing are not AI & IT.
        self.assertFalse(p._is_ai_it("The best robot vacuum deals during October Prime Day", "", True))
        self.assertFalse(p._is_ai_it("Tesla cars can power your house", "A software update enables it.", False))
        self.assertTrue(p._is_ai_it("Tesla cars can power your house", "The feature uses an AI model.", False))

    def test_sports_keeps_all_football_but_only_popular_other_sports(self):
        with patch.object(p, "fetch_article_text", return_value=""):
            stories = p.build_topic("Sports", self.sports_items(), NOW)
        titles = {s["title"]: s for s in stories}
        self.assertIn("Arsenal beat Chelsea 2-1 in London derby", titles)
        self.assertEqual(titles["Arsenal beat Chelsea 2-1 in London derby"]["focus_label"], "Football")
        self.assertIn("Verstappen wins Singapore Grand Prix", titles)  # a curated top story
        self.assertEqual(titles["Verstappen wins Singapore Grand Prix"]["focus"], 0)
        self.assertNotIn("County cricket club signs new bowler", titles)
        self.assertNotIn("Eagles quarterback ruled out of NFL game", titles)

    def test_focus_stories_rank_first(self):
        with patch.object(p, "fetch_article_text", return_value=""):
            stories = p.build_topic("Sports", self.sports_items(), NOW)
        self.assertEqual(stories[0]["title"], "Arsenal beat Chelsea 2-1 in London derby")

    def test_technology_puts_ai_and_it_first_and_keeps_the_rest(self):
        items = [
            item("Apple TV adds a new comedy series", "The Verge", hours_old=0.5),
            item("Anthropic releases new Claude model for developers", "TechCrunch", hours_old=3),
        ]
        with patch.object(p, "fetch_article_text", return_value=""):
            stories = p.build_topic("Technology", items, NOW)
        self.assertEqual([s["title"] for s in stories], [
            "Anthropic releases new Claude model for developers",
            "Apple TV adds a new comedy series",
        ])
        self.assertEqual(stories[0]["focus_label"], "AI & IT")


class ParserTests(unittest.TestCase):
    def test_reddit_link_post_resolves_to_external_article(self):
        entry = feedparser.FeedParserDict({
            "title": "Kenya confirms first Ebola case",
            "link": "https://www.reddit.com/r/worldnews/comments/abc/kenya/",
            "summary": '<table><tr><td> submitted by /u/x <a href="https://www.bbc.co.uk/news/1">[link]</a> '
                       '<a href="https://www.reddit.com/r/worldnews/comments/abc/kenya/">[comments]</a></td></tr></table>',
        })
        result = p._parse_reddit_entry(entry, p.reddit("worldnews"), "")
        self.assertEqual(result["article_url"], "https://www.bbc.co.uk/news/1")
        self.assertEqual(result["link"], "https://www.reddit.com/r/worldnews/comments/abc/kenya/")
        self.assertEqual(result["source"], "r/worldnews")

    def test_reddit_discussion_threads_are_skipped(self):
        entry = feedparser.FeedParserDict({"title": "Daily Discussion | October 6", "link": "x", "summary": ""})
        self.assertIsNone(p._parse_reddit_entry(entry, p.reddit("soccer"), ""))

    def test_mastodon_post_headline_and_link(self):
        entry = feedparser.FeedParserDict({
            "link": "https://mastodon.social/@onlanka/1",
            "summary": '<p>Trincomalee Prison official arrested over Rs. 1.23 Million bribe</p>'
                       '<p>Read more <a href="https://www.onlanka.com/?p=1">onlanka.com</a></p>',
        })
        result = p._parse_mastodon_entry(entry, p.mastodon("srilanka"), "")
        self.assertEqual(result["title"], "Trincomalee Prison official arrested over Rs. 1.23 Million bribe")
        self.assertEqual(result["article_url"], "https://www.onlanka.com/?p=1")
        self.assertEqual(result["source"], "@onlanka")

    def test_google_news_title_drops_publisher_suffix(self):
        entry = feedparser.FeedParserDict({
            "title": "Police fire teargas at protesters - Reuters",
            "link": "https://news.google.com/rss/articles/abc",
            "summary": "<ol><li>junk</li></ol>",
            "source": {"title": "Reuters"},
        })
        result = p._parse_news_entry(entry, p.news("https://news.google.com/rss"), "Google News")
        self.assertEqual(result["title"], "Police fire teargas at protesters")
        self.assertEqual(result["source"], "Reuters")
        self.assertEqual(result["text"], "")

    def test_paragraph_breaks_become_sentence_breaks(self):
        text = p.clean_text("<p>Health minister says 28 contacts identified</p><p>Kenya reported its first death.</p>", paragraphs=True)
        self.assertEqual(text, "Health minister says 28 contacts identified. Kenya reported its first death.")


class SummaryTests(unittest.TestCase):
    TEXTS = [
        {"source": "BBC", "text": "The bill passed parliament on Tuesday after a long debate. The vote was unanimous among members present."},
        {"source": "Reuters", "text": "The bill passed Parliament on Tuesday after a long debate. A new committee will review its spending plans next month."},
    ]

    def test_full_summary_removes_near_duplicate_sentences(self):
        _, full = summarizer.summarize("Budget bill approved", self.TEXTS)
        self.assertEqual(full.lower().count("bill passed parliament"), 1)
        self.assertIn("The vote was unanimous among members present.", full)
        self.assertIn("A new committee will review its spending plans next month.", full)

    def test_every_summary_sentence_comes_from_a_source(self):
        description, full = summarizer.summarize("Budget bill approved", self.TEXTS)
        source_text = " ".join(t["text"] for t in self.TEXTS)
        for sentence in summarizer.split_sentences(description) + summarizer.split_sentences(full):
            self.assertIn(sentence, source_text)

    def test_description_skips_sentences_that_repeat_the_headline(self):
        texts = [{"source": "BBC", "text": "Parliament passes national budget. Lawmakers voted 120 to 80 in favour of the plan on Tuesday."}]
        description, _ = summarizer.summarize("Parliament passes national budget", texts)
        self.assertEqual(description, "Lawmakers voted 120 to 80 in favour of the plan on Tuesday.")

    def test_no_text_gives_empty_summary(self):
        self.assertEqual(summarizer.summarize("Headline", []), ("", ""))

    def test_llm_summaries_use_cache_without_api_key(self):
        story = {"id": "story-1", "title": "T", "features": {"source_count": 3}, "description": "d", "full_summary": "f"}
        cache = {"story-1": {"n": 3, "ts": time.time(), "description": "AI d", "summary": "AI f"}}
        with patch.dict("os.environ", {}, clear=True):
            summarizer.apply_llm_summaries([story], cache)
        self.assertEqual(story["description"], "AI d")
        self.assertEqual(story["full_summary"], "AI f")
        self.assertEqual(story["summary_by"], "ai")


class DigestTests(unittest.TestCase):
    def fake_get(self, url, timeout, max_bytes=0, user_agent=""):
        if url == "https://good.example/rss":
            return rss([
                ("Parliament passes Sri Lanka budget", "https://good.example/1",
                 "Sri Lanka's parliament approved the 2027 budget by a large majority on Tuesday evening.", NOW - 3600),
                ("Very old Sri Lanka story", "https://good.example/2", "This happened in Sri Lanka a long time ago, before this week.", NOW - 10 * 86400),
            ])
        raise OSError("feed is down")

    def test_build_digest_tolerates_failed_feeds_and_drops_stale_items(self):
        feeds = {"Sri Lanka": [p.news("https://good.example/rss", "Ada Derana"), p.news("https://down.example/rss", "X")]}
        with patch.dict(p.FEEDS, feeds, clear=True), \
                patch.object(p, "http_get", side_effect=self.fake_get), \
                patch.object(p, "fetch_article_text", return_value=""):
            digest = p.build_digest()
        self.assertEqual(digest["topics"], ["Sri Lanka"])
        stories = digest["stories"]["Sri Lanka"]
        self.assertEqual([s["title"] for s in stories], ["Parliament passes Sri Lanka budget"])
        s = stories[0]
        for key in ("id", "topic", "title", "description", "full_summary", "published", "published_ts",
                    "best_link", "best_source", "sources", "social", "features", "keywords"):
            self.assertIn(key, s)
        self.assertNotIn("_texts", s)
        self.assertEqual(s["best_link"], "https://good.example/1")
        self.assertIn("2027 budget", s["description"])
        self.assertEqual({f["items"] for f in digest["feeds"]}, {2, 0})


class ServerTests(unittest.TestCase):
    def test_details_endpoint_reads_only_the_cached_digest(self):
        digest = {"generated_at": time.time(), "stories": {"World": [{
            "id": "story-test", "description": "Preview", "full_summary": "Cached full summary.",
            "best_link": "https://example.com/best", "best_source": "BBC",
            "sources": [{"name": "BBC", "url": "https://example.com/best"}], "social": [],
        }]}}
        with patch.object(main, "_digest", digest), patch.object(p, "http_get", side_effect=AssertionError("network")):
            result = asyncio.run(main.get_story_details("story-test"))
        self.assertEqual(result["full_summary"], "Cached full summary.")
        self.assertEqual(result["best_source"], "BBC")


if __name__ == "__main__":
    unittest.main()
