"""
Digest news pipeline
--------------------
Pulls today's headlines from publisher RSS feeds and social sources (Reddit,
Mastodon, YouTube, Bluesky), groups reports of the same event into a single
story, writes a short description and a multi-source summary for each one,
and produces the `news.json` digest that the local server and the phone app
both read.

Personal ranking happens on the device (static/model.js). This module only
computes the per-story features that model needs.

Build a digest from the command line with:
    python build_feed.py --out site
"""

import calendar
import gzip
import hashlib
import html
import math
import re
import threading
import time
import zlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import feedparser
from feedparser.datetimes import _parse_date as _feedparser_parse_date

import ranking
import summarizer

# ---------------------------------------------------------------------------
# 1. Sources. Every topic mixes publisher RSS (real paragraph descriptions),
#    Google News (breadth) and social feeds (what people are sharing). All of
#    them are free and need no API key.
# ---------------------------------------------------------------------------


def news(url: str, name: str = "") -> dict:
    """Publisher or aggregator RSS. `name` overrides the feed's own title."""
    return {"kind": "news", "url": url, "name": name}


def reddit(subreddit: str) -> dict:
    return {
        "kind": "reddit",
        "url": f"https://www.reddit.com/r/{subreddit}/top/.rss?t=day",
        "name": f"r/{subreddit}",
    }


def mastodon(tag: str) -> dict:
    return {"kind": "mastodon", "url": f"https://mastodon.social/tags/{tag}.rss", "name": f"#{tag}"}


def youtube(channel_id: str, name: str) -> dict:
    return {
        "kind": "youtube",
        "url": f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}",
        "name": name,
    }


def bluesky(handle: str, name: str) -> dict:
    return {"kind": "bluesky", "url": f"https://bsky.app/profile/{handle}/rss", "name": name}


def _google(query: str = "", section: str = "", region: str = "US") -> str:
    lang = "en-LK" if region == "LK" else "en-US"
    suffix = f"hl={lang}&gl={region}&ceid={region}:en"
    if section:
        return f"https://news.google.com/rss/headlines/section/topic/{section}?{suffix}"
    if query:
        return f"https://news.google.com/rss/search?q={query}&{suffix}"
    return f"https://news.google.com/rss?{suffix}"


FEEDS: dict[str, list[dict]] = {
    "Sri Lanka": [
        news("https://www.adaderana.lk/rss.php", "Ada Derana"),
        news("https://www.dailymirror.lk/rss/breaking_news/108", "Daily Mirror"),
        news("https://economynext.com/feed/", "EconomyNext"),
        news("https://srilankamirror.com/feed/", "Sri Lanka Mirror"),
        news(_google("site%3Aadaderana.lk+OR+site%3Adailymirror.lk+OR+site%3Anewsfirst.lk+Sri+Lanka", region="LK")),
        news(_google("site%3Acolombotelegraph.com+OR+site%3Aisland.lk+Sri+Lanka", region="LK")),
        news(_google(region="LK")),
        reddit("srilanka"),
        mastodon("srilanka"),
    ],
    "Mannar": [
        news(_google("Mannar+Sri+Lanka", region="LK")),
        news(_google("Mannar+district", region="LK")),
    ],
    "World": [
        news("https://feeds.bbci.co.uk/news/world/rss.xml", "BBC"),
        news("https://www.aljazeera.com/xml/rss/all.xml", "Al Jazeera"),
        news("https://www.theguardian.com/world/rss", "The Guardian"),
        news("https://www.npr.org/rss/rss.php?id=1001", "NPR"),
        news(_google(section="WORLD")),
        reddit("worldnews"),
        mastodon("worldnews"),
        youtube("UCNye-wNBqNL5ZzHSJj3l8Bg", "Al Jazeera English"),
        youtube("UCknLrEdhRCp1aegoMqRaCZg", "DW News"),
        bluesky("reuters.com", "Reuters"),
        bluesky("apnews.com", "AP"),
        bluesky("aljazeera.com", "Al Jazeera"),
        bluesky("theguardian.com", "The Guardian"),
    ],
    "Technology": [
        news("https://feeds.bbci.co.uk/news/technology/rss.xml", "BBC"),
        news("https://www.theguardian.com/uk/technology/rss", "The Guardian"),
        news("https://feeds.arstechnica.com/arstechnica/index", "Ars Technica"),
        news("https://www.theverge.com/rss/index.xml", "The Verge"),
        news(_google(section="TECHNOLOGY")),
        reddit("technology"),
        mastodon("technology"),
        bluesky("theverge.com", "The Verge"),
        bluesky("arstechnica.com", "Ars Technica"),
    ],
    "Business": [
        news("https://feeds.bbci.co.uk/news/business/rss.xml", "BBC"),
        news("https://www.theguardian.com/uk/business/rss", "The Guardian"),
        news("https://www.cnbc.com/id/100003114/device/rss/rss.html", "CNBC"),
        news("https://www.dailymirror.lk/rss/business/215", "Daily Mirror"),
        news("https://www.lankabusinessonline.com/feed/", "Lanka Business Online"),
        news(_google(section="BUSINESS")),
        reddit("business"),
        reddit("economics"),
        mastodon("business"),
        mastodon("economy"),
        youtube("UCvJJ_dzjViJCoLf5uKUTwoA", "CNBC"),
        bluesky("bloomberg.com", "Bloomberg"),
    ],
    "Sports": [
        news(_google("football+OR+soccer")),
        news("https://feeds.bbci.co.uk/sport/football/rss.xml", "BBC Sport"),
        news("https://www.theguardian.com/football/rss", "The Guardian"),
        news("https://feeds.bbci.co.uk/sport/cricket/rss.xml", "BBC Sport"),
        news("https://www.espncricinfo.com/rss/content/story/feeds/0.xml", "ESPNcricinfo"),
        news(_google(section="SPORTS")),
        reddit("soccer"),
        reddit("cricket"),
        mastodon("football"),
        mastodon("cricket"),
    ],
    "Science": [
        news("https://feeds.bbci.co.uk/news/science_and_environment/rss.xml", "BBC"),
        news("https://www.theguardian.com/science/rss", "The Guardian"),
        news("https://www.sciencedaily.com/rss/top/science.xml", "ScienceDaily"),
        news(_google(section="SCIENCE")),
        reddit("science"),
        mastodon("science"),
        bluesky("nature.com", "Nature"),
    ],
}

# Stories in these topics must actually be about the place. Local outlets and
# place-tagged social feeds pass automatically; everything else needs a mention.
TOPIC_REQUIRED_TERMS: dict[str, list[str]] = {
    "Sri Lanka": ["sri lanka", "lanka", "colombo", "sri lankan"],
    "Mannar": ["mannar"],
}

TRUSTED_SOURCES: dict[str, float] = {
    "Newsfirst": 0.97,
    "Reuters": 0.97,
    "Ada Derana": 0.96,
    "AP": 0.96,
    "Associated Press": 0.96,
    "Daily Mirror": 0.94,
    "BBC": 0.92,
    "The Sunday Times": 0.92,
    "Bloomberg": 0.92,
    "Nature": 0.92,
    "The Island": 0.9,
    "Al Jazeera": 0.9,
    "The New York Times": 0.9,
    "EconomyNext": 0.9,
    "ESPNcricinfo": 0.9,
    "The Guardian": 0.89,
    "Ars Technica": 0.89,
    "Sri Lanka Mirror": 0.88,
    "NPR": 0.88,
    "DW": 0.88,
    "CNBC": 0.87,
    "CNN": 0.87,
    "The Verge": 0.86,
    "Lanka Business Online": 0.86,
    "ScienceDaily": 0.85,
}

LOCAL_SOURCE_TOKENS = [
    "daily mirror", "ada derana", "newsfirst", "sri lanka", "sunday times",
    "the island", "colombo", "lankapuvath", "hirunews", "economynext",
    "lanka business", "r/srilanka", "#srilanka", "onlanka",
]

SOURCE_ALIASES = {
    "news first": "Newsfirst",
    "newsfirst": "Newsfirst",
    "bbc news": "BBC",
    "bbc sport": "BBC Sport",
    "associated press": "AP",
    "ap news": "AP",
    "al jazeera english": "Al Jazeera",
    "the guardian": "The Guardian",
    "dailymirror": "Daily Mirror",
}

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36 Digest/1.0"
REDDIT_USER_AGENT = "Mozilla/5.0 (compatible; DigestNewsApp/1.0; +https://github.com/Jatheel/Digest)"

MAX_ENTRIES_PER_FEED = 25
MAX_STORIES_PER_TOPIC = 40
MAX_AGE_HOURS = 72
# Low-volume local topics would be empty with a 3-day window.
TOPIC_MAX_AGE_HOURS = {"Mannar": 14 * 24}
FEED_TIMEOUT_SECONDS = 12
ARTICLE_TIMEOUT_SECONDS = 6
FETCH_WORKERS = 16
REDDIT_MIN_GAP_SECONDS = 2.5
# IDF-weighted similarity of headline (+ lead) words; tuned on real feeds,
# where the same event reported by two outlets typically scores 0.3-0.9.
GROUP_SIMILARITY_THRESHOLD = 0.27
SOCIAL_SIMILARITY_THRESHOLD = 0.30
GROUP_MAX_HOURS = 48
SOURCE_TEXT_MAX_CHARS = 1500
THIN_TEXT_CHARS = 160
MAX_ENRICH_PER_STORY = 3
GENERIC_GOOGLE_NEWS_DESCRIPTION = "comprehensive up-to-date news coverage, aggregated from sources all over the world by google news"

SOCIAL_KINDS = {"reddit", "mastodon", "youtube", "bluesky"}
SOCIAL_HOSTS = ("reddit.com", "redd.it", "mastodon.social", "bsky.app", "youtube.com", "youtu.be")

_TAG_RE = re.compile(r"<[^>]+>")
_URL_RE = re.compile(r"https?://[^\s<>\"']+")
_STOPWORDS = {
    "the", "a", "an", "and", "or", "for", "with", "from", "into", "this",
    "that", "these", "those", "about", "after", "before", "over", "under",
    "via", "while", "their", "there", "where", "what", "when", "why", "how",
    "have", "has", "had", "will", "would", "should", "could", "can", "but",
    "not", "more", "most", "been", "being", "new", "news", "story", "update",
    "to", "of", "in", "on", "at", "by", "as", "is", "are", "was", "were", "be",
    "it", "its", "his", "her", "they", "them", "he", "she", "we", "you", "i",
    "says", "said", "say", "amid", "up", "out", "off", "than", "also", "who",
    "which", "all", "just", "now", "live", "latest", "s", "vs",
}
_DISCUSSION_THREAD_RE = re.compile(r"\b(daily|weekly|monthly) (discussion|thread)|megathread|^\s*\[?meta\]?\b", re.I)

# ---------------------------------------------------------------------------
# 2. Text helpers.
# ---------------------------------------------------------------------------


_BLOCK_END_RE = re.compile(r"</(p|div|li|h[1-6]|blockquote|figcaption|tr)>|<br\s*/?>", re.I)
_TERMINAL_PUNCTUATION = ('.', '!', '?', '"', "'", '”', '’', ')', '…', ':')


def clean_text(raw: str, max_chars: int = SOURCE_TEXT_MAX_CHARS, paragraphs: bool = False) -> str:
    """Feeds often contain HTML and entities. Strip both, then trim on a word.

    With paragraphs=True, block boundaries become sentence boundaries, so a
    standfirst and the first paragraph don't run together into one sentence.
    """
    if not raw:
        return ""
    if paragraphs:
        blocks = [html.unescape(_TAG_RE.sub(" ", b)) for b in _BLOCK_END_RE.split(raw)[::2]]
        blocks = [re.sub(r"\s+", " ", b).strip() for b in blocks]
        text = " ".join(b if b.endswith(_TERMINAL_PUNCTUATION) else b + "." for b in blocks if b)
    else:
        text = html.unescape(_TAG_RE.sub(" ", raw))
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_chars:
        text = text[:max_chars].rsplit(" ", 1)[0] + "…"
    return text


def normalize_text(value: str) -> str:
    if not value:
        return ""
    value = html.unescape(value)
    value = re.sub(r"[^a-zA-Z0-9\s]", " ", value.lower())
    return re.sub(r"\s+", " ", value).strip()


def content_tokens(text: str) -> list[str]:
    return [w for w in normalize_text(text).split() if len(w) > 1 and w not in _STOPWORDS]


def token_overlap(a: str, b: str) -> float:
    """Jaccard overlap of the word sets of two strings."""
    a_tokens = set(re.findall(r"\w+", a.lower()))
    b_tokens = set(re.findall(r"\w+", b.lower()))
    if not a_tokens or not b_tokens:
        return 0.0
    return len(a_tokens & b_tokens) / len(a_tokens | b_tokens)


def canonical_source(source: str) -> str:
    text = re.sub(r"\s+", " ", str(source or "")).strip()
    return SOURCE_ALIASES.get(text.lower(), text)


def source_trust(source: str) -> float:
    name = canonical_source(source)
    if name in TRUSTED_SOURCES:
        return TRUSTED_SOURCES[name]
    lowered = name.lower()
    for known, trust in TRUSTED_SOURCES.items():
        if re.search(r"\b" + re.escape(known.lower()) + r"\b", lowered):
            return trust
    return 0.6


def is_local_source(source: str) -> bool:
    lowered = str(source or "").lower()
    return any(token in lowered for token in LOCAL_SOURCE_TOKENS)


def _host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def _is_google_news(url: str) -> bool:
    return _host(url).endswith("news.google.com")


def _is_social_url(url: str) -> bool:
    host = _host(url)
    return any(host == h or host.endswith("." + h) for h in SOCIAL_HOSTS)


def _shorten(text: str, max_chars: int) -> str:
    text = text.strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rsplit(" ", 1)[0].rstrip(",;:-") + "…"


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_time(published: str) -> float | None:
    """Best-effort parse of a feed date string into a UTC timestamp."""
    if not published:
        return None
    parsed = _feedparser_parse_date(published)  # handles RFC 822, ISO 8601 and friends
    if parsed:
        return float(calendar.timegm(parsed))
    return None


# ---------------------------------------------------------------------------
# 3. HTTP. Feeds are fetched with timeouts; one bad feed never sinks a topic.
# ---------------------------------------------------------------------------

_reddit_lock = threading.Lock()
_reddit_last_request = 0.0


def http_get(url: str, timeout: float, max_bytes: int = 2_000_000, user_agent: str = USER_AGENT) -> bytes:
    request = Request(url, headers={"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"})
    with urlopen(request, timeout=timeout) as response:
        body = response.read(max_bytes)
        encoding = response.headers.get("Content-Encoding", "").lower()
    if encoding == "gzip":
        body = gzip.decompress(body)
    elif encoding == "deflate":
        body = zlib.decompress(body)
    return body


def _fetch_reddit(url: str) -> bytes:
    """Reddit rate-limits aggressively, so requests go out one at a time."""
    global _reddit_last_request
    with _reddit_lock:
        for attempt, candidate in enumerate([url, url, url.replace("www.reddit.com", "old.reddit.com")]):
            wait = REDDIT_MIN_GAP_SECONDS - (time.time() - _reddit_last_request)
            if wait > 0:
                time.sleep(wait)
            _reddit_last_request = time.time()
            try:
                return http_get(candidate, FEED_TIMEOUT_SECONDS, user_agent=REDDIT_USER_AGENT)
            except HTTPError as exc:
                if exc.code != 429 or attempt == 2:
                    raise
                time.sleep(5)
    raise RuntimeError("unreachable")


def fetch_feed(feed: dict) -> list[dict]:
    """Fetch and parse one configured feed into normalized items."""
    try:
        if feed["kind"] == "reddit":
            body = _fetch_reddit(feed["url"])
        else:
            body = http_get(feed["url"], FEED_TIMEOUT_SECONDS)
        parsed = feedparser.parse(body)
    except Exception as exc:  # noqa: BLE001 - one bad feed shouldn't sink the topic
        print(f"[digest] failed to fetch {feed['url']}: {exc}")
        return []

    parser = _ENTRY_PARSERS[feed["kind"]]
    feed_title = clean_text(parsed.feed.get("title", "") if parsed.feed else "", 120)
    items = []
    now = time.time()
    for entry in parsed.entries[:MAX_ENTRIES_PER_FEED]:
        try:
            item = parser(entry, feed, feed_title)
        except Exception as exc:  # noqa: BLE001 - skip malformed entries
            print(f"[digest] skipped entry in {feed['url']}: {exc}")
            continue
        if not item or not item.get("title"):
            continue
        ts = _entry_time(entry)
        # Missing or future dates (some feeds publish local time without a
        # zone) are treated as "just published".
        item["published_ts"] = min(ts, now) if ts else now
        item["kind"] = feed["kind"]
        item["title_tokens"] = set(content_tokens(item["title"]))
        item["lead_tokens"] = set(content_tokens(" ".join((item.get("text") or "").split()[:30])))
        items.append(item)
    return items


def _entry_time(entry) -> float | None:
    for key in ("published_parsed", "updated_parsed"):
        value = entry.get(key)
        if value:
            return float(calendar.timegm(value))
    return parse_time(entry.get("published", "") or entry.get("updated", ""))


# ---------------------------------------------------------------------------
# 4. Entry parsers, one per source kind.
# ---------------------------------------------------------------------------


def _parse_news_entry(entry, feed: dict, feed_title: str) -> dict | None:
    link = entry.get("link", "")
    source_info = entry.get("source")
    if isinstance(source_info, dict) and source_info.get("title"):
        source = canonical_source(source_info["title"])
    else:
        source = canonical_source(feed.get("name") or feed_title)
    title = clean_text(entry.get("title", ""), 300)
    if _is_google_news(link):
        # Google News titles end with " - Publisher" and their summaries are
        # just a list of links, so keep neither.
        title = re.sub(r"\s+[-–|]\s+" + re.escape(source) + r"\s*$", "", title)
        text = ""
    else:
        text = clean_text(entry.get("summary", entry.get("description", "")), paragraphs=True)
    if normalize_text(text) == normalize_text(title):
        text = ""
    return {"title": title, "text": text, "link": link, "article_url": link, "source": source}


def _first_external_link(raw_html: str) -> str:
    for href in re.findall(r'href="([^"]+)"', raw_html or ""):
        href = html.unescape(href)
        if href.startswith("http") and not _is_social_url(href) and "/tags/" not in href:
            return href
    return ""


def _parse_reddit_entry(entry, feed: dict, feed_title: str) -> dict | None:
    title = clean_text(entry.get("title", ""), 300)
    if _DISCUSSION_THREAD_RE.search(title):
        return None
    raw = entry.get("summary", "")
    match = re.search(r'<a href="([^"]+)">\[link\]</a>', raw)
    external = html.unescape(match.group(1)) if match else ""
    if external and _is_social_url(external):
        external = ""
    body = re.search(r'<div class="md">(.*?)</div>', raw, re.S)
    text = clean_text(body.group(1), paragraphs=True) if body else ""
    return {
        "title": title,
        "text": text,
        "link": entry.get("link", ""),
        "article_url": external,
        "source": feed["name"],
        "platform": "reddit",
    }


def _parse_mastodon_entry(entry, feed: dict, feed_title: str) -> dict | None:
    raw = entry.get("summary", "")
    paragraphs = [clean_text(p, 400) for p in re.findall(r"<p>(.*?)</p>", raw, re.S)]
    paragraphs = [p for p in paragraphs if p and not re.match(r"^(read more|more|source)\b", p, re.I)]
    if not paragraphs:
        return None
    title = _URL_RE.sub("", paragraphs[0])
    title = re.sub(r"(#\w+\s*)+$", "", title).strip(" -–:|")
    if len(title) < 25:
        return None
    link = entry.get("link", "")
    handle = re.search(r"/(@[^/]+)/", link)
    return {
        "title": _shorten(title, 220),
        "text": clean_text(" ".join(_URL_RE.sub("", p) for p in paragraphs[1:])),
        "link": link,
        "article_url": _first_external_link(raw),
        "source": handle.group(1) if handle else "Mastodon",
        "platform": "mastodon",
    }


def _parse_youtube_entry(entry, feed: dict, feed_title: str) -> dict | None:
    title = clean_text(entry.get("title", ""), 300)
    if re.search(r"#shorts\b|\blive\b\s*[:|]", title, re.I) or "🔴" in title:
        return None
    title = re.sub(r"(\s*#\w+)+\s*$", "", title).strip(" |-")
    description = entry.get("summary", "") or ""
    keep = []
    for line in description.splitlines():
        if re.search(r"subscribe|https?://|follow us|#\w+|download the", line, re.I):
            break
        keep.append(line)
    return {
        "title": title,
        "text": clean_text(" ".join(keep)),
        "link": entry.get("link", ""),
        "article_url": "",
        "source": feed["name"],
        "platform": "youtube",
    }


def _parse_bluesky_entry(entry, feed: dict, feed_title: str) -> dict | None:
    raw = html.unescape(entry.get("summary", "") or "")
    urls = _URL_RE.findall(raw)
    text = _URL_RE.sub("", raw)
    text = re.sub(r"\b[\w-]+\.[a-z]{2,4}/\S+", "", text)  # shortened links like reut.rs/abc
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) < 30:
        return None
    return {
        "title": _shorten(text.split("\n")[0], 220),
        "text": "",
        "link": entry.get("link", ""),
        "article_url": urls[0] if urls else "",
        "source": feed["name"],
        "platform": "bluesky",
    }


_ENTRY_PARSERS = {
    "news": _parse_news_entry,
    "reddit": _parse_reddit_entry,
    "mastodon": _parse_mastodon_entry,
    "youtube": _parse_youtube_entry,
    "bluesky": _parse_bluesky_entry,
}

# ---------------------------------------------------------------------------
# 5. Article enrichment: when a feed only gives us a headline, read the
#    article page's meta description and opening paragraphs.
# ---------------------------------------------------------------------------


class _ArticleTextParser(HTMLParser):
    MAX_PARAGRAPHS = 6

    def __init__(self):
        super().__init__()
        self.description = ""
        self.paragraphs: list[str] = []
        self._current: list[str] | None = None
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript", "nav", "footer", "aside"}:
            self._skip_depth += 1
        elif tag == "meta" and not self.description:
            attributes = {key.lower(): value or "" for key, value in attrs}
            key = attributes.get("name", "").lower() or attributes.get("property", "").lower()
            if key in {"description", "og:description", "twitter:description"}:
                self.description = attributes.get("content", "")
        elif tag == "p" and self._skip_depth == 0 and len(self.paragraphs) < self.MAX_PARAGRAPHS:
            self._current = []

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript", "nav", "footer", "aside"}:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag == "p" and self._current is not None:
            paragraph = clean_text(" ".join(self._current), 600)
            # Photo captions and bylines rarely end like a sentence.
            if len(paragraph) >= 60 and paragraph.endswith(_TERMINAL_PUNCTUATION):
                self.paragraphs.append(paragraph)
            self._current = None

    def handle_data(self, data: str) -> None:
        if self._current is not None and self._skip_depth == 0:
            self._current.append(data)


def fetch_article_text(url: str) -> str:
    """Meta description plus the first paragraphs of an article page."""
    if not url or _is_google_news(url) or _is_social_url(url):
        return ""
    try:
        content = http_get(url, ARTICLE_TIMEOUT_SECONDS, max_bytes=600_000).decode("utf-8", errors="ignore")
    except (OSError, URLError, UnicodeError, ValueError, zlib.error):
        return ""
    parser = _ArticleTextParser()
    try:
        parser.feed(content)
    except Exception:  # noqa: BLE001 - malformed HTML; use what we parsed so far
        pass
    description = clean_text(parser.description, 600)
    if description and not description.endswith(_TERMINAL_PUNCTUATION):
        description += "."
    if normalize_text(description) == GENERIC_GOOGLE_NEWS_DESCRIPTION:
        description = ""
    parts = [description] if description else []
    for paragraph in parser.paragraphs:
        if not any(token_overlap(paragraph, saved) >= 0.6 for saved in parts):
            parts.append(paragraph)
    return clean_text(" ".join(parts))


def _is_thin(item: dict) -> bool:
    text = item.get("text") or ""
    return len(text) < THIN_TEXT_CHARS or normalize_text(text).startswith(normalize_text(item.get("title", "")))


# ---------------------------------------------------------------------------
# 6. Grouping: reports of the same event from different outlets (and the
#    social posts sharing them) become one story.
# ---------------------------------------------------------------------------


def _passes_topic_filter(item: dict, topic: str) -> bool:
    terms = TOPIC_REQUIRED_TERMS.get(topic)
    if not terms:
        return True
    feed_name = str(item.get("source") or "")
    if item["kind"] == "news" and is_local_source(feed_name) and topic == "Sri Lanka":
        return True
    if item["kind"] in SOCIAL_KINDS and item.get("feed_local"):
        return True
    haystack = f"{item.get('title', '')} {item.get('text', '')}".lower()
    return any(term in haystack for term in terms)


def _weighted_jaccard(a: set[str], b: set[str], idf: dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    union = sum(idf.get(w, 1.0) for w in a | b)
    return sum(idf.get(w, 1.0) for w in a & b) / union if union else 0.0


def _similarity(item: dict, member: dict, idf: dict[str, float]) -> float:
    """Headline similarity, helped by the lead sentence when headlines differ."""
    title_score = _weighted_jaccard(item["title_tokens"], member["title_tokens"], idf)
    lead_score = _weighted_jaccard(
        item["title_tokens"] | item.get("lead_tokens", set()),
        member["title_tokens"] | member.get("lead_tokens", set()),
        idf,
    )
    return max(title_score, 0.5 * title_score + 0.5 * lead_score)


def group_items(items: Iterable[dict]) -> list[dict]:
    """Greedy clustering on headline similarity within a 48-hour window.

    Words are weighted by rarity (IDF over this batch), so names and places
    ("Kahandagama", "Black Sea") count for more than "arrested" or "says".
    News reports are placed first so that clusters are anchored on articles;
    social posts then join the cluster they match (or share a link with), or
    start their own social-only cluster.
    """
    items = list(items)
    df: dict[str, int] = {}
    for item in items:
        for word in item["title_tokens"] | item.get("lead_tokens", set()):
            df[word] = df.get(word, 0) + 1
    idf = {word: math.log(1 + len(items) / count) for word, count in df.items()}

    ordered = sorted(items, key=lambda i: (i["kind"] != "news", -i["published_ts"]))
    groups: list[dict] = []
    url_index: dict[str, dict] = {}
    for item in ordered:
        is_social = item["kind"] in SOCIAL_KINDS
        match = url_index.get(item.get("article_url") or "") if item.get("article_url") else None
        if match is None:
            threshold = SOCIAL_SIMILARITY_THRESHOLD if is_social else GROUP_SIMILARITY_THRESHOLD
            best_score = 0.0
            for group in groups:
                if abs(group["anchor_ts"] - item["published_ts"]) > GROUP_MAX_HOURS * 3600:
                    continue
                score = max(_similarity(item, member, idf) for member in group["anchors"])
                if score >= threshold and score > best_score:
                    match, best_score = group, score
        if match is None:
            match = {"items": [], "anchors": [], "anchor_ts": item["published_ts"]}
            groups.append(match)
        match["items"].append(item)
        if len(match["anchors"]) < 6:
            match["anchors"].append(item)
        for url in (item.get("article_url"), item.get("link")):
            if url and not _is_google_news(url):
                url_index.setdefault(url, match)
    return groups


def stable_story_id(items: list[dict]) -> str:
    """The oldest report anchors the id, so it stays put as new reports join."""
    oldest = min(items, key=lambda i: (i["published_ts"], i["title"]))
    signature = normalize_text(oldest["title"]) or oldest.get("link", "untitled")
    return "story-" + hashlib.sha1(signature.encode("utf-8")).hexdigest()[:16]


def choose_best_source(news_items: list[dict]) -> dict | None:
    """Prefer trusted publishers with a direct article link and real text."""
    if not news_items:
        return None

    def score(item: dict) -> float:
        value = source_trust(item["source"])
        if item.get("link") and not _is_google_news(item["link"]):
            value += 0.3
        if len(item.get("text") or "") >= THIN_TEXT_CHARS:
            value += 0.15
        return value

    return max(news_items, key=score)


def _story_keywords(title: str, description: str) -> list[str]:
    title_tokens = [t for t in content_tokens(title) if len(t) > 2]
    keywords = list(dict.fromkeys(title_tokens))
    keywords += [f"{a}_{b}" for a, b in zip(title_tokens, title_tokens[1:])]
    counts: dict[str, int] = {}
    for token in content_tokens(description):
        if len(token) > 3 and token not in title_tokens:
            counts[token] = counts.get(token, 0) + 1
    keywords += [t for t, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:5]]
    return list(dict.fromkeys(keywords))[:24]


def build_story(group: dict, topic: str) -> dict | None:
    news_items = [i for i in group["items"] if i["kind"] == "news"]
    social_items = [i for i in group["items"] if i["kind"] in SOCIAL_KINDS]
    best = choose_best_source(news_items)
    social_only = best is None
    if social_only:
        # Keep social-only stories only when they carry a real signal: a
        # linked article or several posts about the same thing.
        linked = [i for i in social_items if i.get("article_url")]
        if not linked and len(social_items) < 2:
            return None
        best = linked[0] if linked else social_items[0]

    title = best["title"]
    ordered_news = [best] + [i for i in news_items if i is not best] if not social_only else news_items
    texts = []
    for item in ordered_news + social_items:
        if item.get("text"):
            texts.append({"source": item["source"], "text": item["text"]})
    description, full_summary = summarizer.summarize(title, texts)

    newest = max(i["published_ts"] for i in group["items"])
    if social_only:
        best_link = best.get("article_url") or best["link"]
        best_source = _host(best_link).removeprefix("www.") if best.get("article_url") else best["source"]
    else:
        best_link, best_source = best["link"], best["source"]

    # One entry per outlet; when an outlet appears twice (its own feed and
    # Google News), keep the direct article link.
    by_outlet: dict[str, dict] = {}
    for item in ordered_news:
        current = by_outlet.get(item["source"])
        if current is None or (_is_google_news(current["link"]) and not _is_google_news(item["link"])):
            by_outlet[item["source"]] = item
    sources = []
    for item in ordered_news:
        if by_outlet.get(item["source"]) is not item:
            continue
        sources.append({
            "name": item["source"],
            "url": item["link"],
            "kind": "news",
            "published": _iso(item["published_ts"]),
        })
    social = [
        {"platform": i["platform"], "name": i["source"], "url": i["link"], "title": i["title"]}
        for i in social_items
    ]
    distinct_news_sources = {s["name"] for s in sources}
    haystack = f"{title} {description}".lower()
    story = {
        "id": stable_story_id(group["items"]),
        "topic": topic,
        "title": title,
        "description": description,
        "full_summary": full_summary,
        "published": _iso(newest),
        "published_ts": round(newest),
        "best_link": best_link,
        "best_source": best_source,
        "sources": sources,
        "social": social,
        "features": {
            "source_trust": round(max((source_trust(s) for s in distinct_news_sources), default=0.4), 3),
            "source_count": len(distinct_news_sources),
            "social_count": len(social),
            "local_source": int(any(is_local_source(s) for s in distinct_news_sources)),
            "local_mention": int(any(t in haystack for t in TOPIC_REQUIRED_TERMS["Sri Lanka"])),
            "is_social_only": int(social_only),
        },
        "keywords": _story_keywords(title, description),
        # Kept out of news.json; used for enrichment and LLM summaries.
        "_texts": texts,
        "_items": group["items"],
    }
    return story


# ---------------------------------------------------------------------------
# 7. The full pipeline.
# ---------------------------------------------------------------------------


def _enrich_items(items: list[dict]) -> None:
    """Fetch article text for thin items, in parallel."""
    targets = []
    for item in items:
        url = item.get("article_url") or ""
        if url and _is_thin(item) and not item.get("_enriched"):
            item["_enriched"] = True
            targets.append(item)
    if not targets:
        return
    with ThreadPoolExecutor(FETCH_WORKERS) as pool:
        for item, text in zip(targets, pool.map(lambda i: fetch_article_text(i["article_url"]), targets)):
            if len(text) > len(item.get("text") or ""):
                item["text"] = text


def _prerank(group: dict) -> tuple:
    news_sources = {i["source"] for i in group["items"] if i["kind"] == "news"}
    social = sum(1 for i in group["items"] if i["kind"] in SOCIAL_KINDS)
    newest = max(i["published_ts"] for i in group["items"])
    age_hours = max(0.0, (time.time() - newest) / 3600)
    return (len(news_sources) * 1.5 + math.log1p(social) - age_hours / 12, newest)


def build_topic(topic: str, items: list[dict], now: float) -> list[dict]:
    cutoff = now - TOPIC_MAX_AGE_HOURS.get(topic, MAX_AGE_HOURS) * 3600
    fresh = [i for i in items if i["published_ts"] >= cutoff and _passes_topic_filter(i, topic)]
    groups = group_items(fresh)
    groups.sort(key=_prerank, reverse=True)
    # Enrich a few more groups than we keep: social-only groups without text
    # may still drop out after enrichment.
    candidates = groups[: MAX_STORIES_PER_TOPIC + 15]
    to_enrich = []
    for group in candidates:
        thin = [i for i in group["items"] if _is_thin(i) and i.get("article_url")]
        to_enrich.extend(thin[:MAX_ENRICH_PER_STORY])
    _enrich_items(to_enrich)

    stories = []
    for group in candidates:
        story = build_story(group, topic)
        if story is None:
            continue
        if story["features"]["is_social_only"] and not story["description"] and story["features"]["social_count"] < 2:
            continue
        stories.append(story)
    model = ranking.load_defaults()
    stories.sort(key=lambda s: ranking.score_story(model, s, now), reverse=True)
    return stories[:MAX_STORIES_PER_TOPIC]


def build_digest(topics: Iterable[str] | None = None, summary_cache: dict | None = None) -> dict:
    """Fetch every feed, build each topic's stories and return the digest."""
    started = time.time()
    topic_names = list(topics or FEEDS.keys())
    jobs = [(topic, feed) for topic in topic_names for feed in FEEDS[topic]]
    with ThreadPoolExecutor(FETCH_WORKERS) as pool:
        results = list(pool.map(lambda job: fetch_feed(job[1]), jobs))

    by_topic: dict[str, list[dict]] = {topic: [] for topic in topic_names}
    feed_stats = []
    for (topic, feed), items in zip(jobs, results):
        local_feed = topic in TOPIC_REQUIRED_TERMS and feed["kind"] in SOCIAL_KINDS
        for item in items:
            item["feed_local"] = local_feed
        by_topic[topic].extend(items)
        feed_stats.append({"topic": topic, "kind": feed["kind"], "url": feed["url"], "items": len(items)})

    now = time.time()
    stories = {topic: build_topic(topic, by_topic[topic], now) for topic in topic_names}

    if summary_cache is not None:
        all_stories = [s for topic_stories in stories.values() for s in topic_stories]
        summarizer.apply_llm_summaries(all_stories, summary_cache)

    for topic_stories in stories.values():
        for story in topic_stories:
            story.pop("_texts", None)
            story.pop("_items", None)

    return {
        "version": 2,
        "generated_at": round(now),
        "build_seconds": round(time.time() - started, 1),
        "topics": topic_names,
        "stories": stories,
        "feeds": feed_stats,
    }
