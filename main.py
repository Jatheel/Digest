"""
Personal News Digest — backend
--------------------------------
Pulls headlines from free, no-key-required RSS feeds, groups them into
topics, caches results for a while (so we don't hammer the feeds), and
serves them as JSON to the frontend. Also serves the frontend itself.

Run it with:
    uvicorn main:app --host 0.0.0.0 --port 8000 --reload
"""

import asyncio
import html
import json
import math
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import feedparser
from fastapi import FastAPI, Body, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# ---------------------------------------------------------------------------
# 1. Configure your topics here. Each topic is a label + a list of RSS feed
#    URLs to pull from. All of these are free and need no API key.
#
#    Google News RSS is the workhorse: you can localize it with hl (language),
#    gl (country) and ceid (edition) query params, or scope it to a topic.
#    Swap "LK"/"en-LK" for your own country/language if you ever want to.
# ---------------------------------------------------------------------------

TOPICS: dict[str, list[str]] = {
    "Sri Lanka": [
        "https://news.google.com/rss/search?q=site%3Aadaderana.lk+OR+site%3Adailymirror.lk+OR+site%3Anewsfirst.lk+Sri+Lanka&hl=en-LK&gl=LK&ceid=LK:en",
        "https://news.google.com/rss/search?q=site%3Acolombotelegraph.com+OR+site%3Aisland.lk+Sri+Lanka&hl=en-LK&gl=LK&ceid=LK:en",
        "https://news.google.com/rss?hl=en-LK&gl=LK&ceid=LK:en",
    ],
    "Mannar": [
        "https://news.google.com/rss/search?q=Mannar+Sri+Lanka&hl=en-LK&gl=LK&ceid=LK:en",
    ],
    "World": [
        "https://news.google.com/rss/headlines/section/topic/WORLD?hl=en-US&gl=US&ceid=US:en",
    ],
    "Technology": [
        "https://news.google.com/rss/headlines/section/topic/TECHNOLOGY?hl=en-US&gl=US&ceid=US:en",
    ],
    "Business": [
        "https://news.google.com/rss/headlines/section/topic/BUSINESS?hl=en-US&gl=US&ceid=US:en",
    ],
    "Sports": [
        # Prioritize Football / Soccer news
        "https://news.google.com/rss/search?q=football+OR+soccer&hl=en-US&gl=US&ceid=US:en",
        # General sports as a backup
        "https://news.google.com/rss/headlines/section/topic/SPORTS?hl=en-US&gl=US&ceid=US:en",
    ],
    "Science": [
        "https://news.google.com/rss/headlines/section/topic/SCIENCE?hl=en-US&gl=US&ceid=US:en",
    ],
}

CACHE_TTL_SECONDS = 20 * 60  # refetch a topic at most every 20 minutes
MAX_ARTICLES_PER_TOPIC = 25
SUMMARY_MAX_CHARS = 220
MAX_STALE_HOURS = 3 * 30 * 24

TRUSTED_SOURCES: dict[str, float] = {
    "News First": 0.97,
    "Ada Derana": 0.96,
    "Daily Mirror": 0.94,
    "The Sunday Times": 0.92,
    "The Island": 0.9,
    "Sri Lanka Mirror": 0.88,
    "BBC": 0.92,
    "Reuters": 0.97,
    "AP": 0.96,
    "Al Jazeera": 0.9,
    "The Guardian": 0.89,
    "CNN": 0.87,
    "NPR": 0.88,
    "The New York Times": 0.9,
}

PROFILE_PATH = Path(__file__).with_name("user_profile.json")

DEFAULT_USER_PROFILE = {
    "topic_weights": {
        "Sri Lanka": 1.6,
        "Mannar": 1.45,
        "World": 0.95,
        "Technology": 0.75,
        "Business": 0.8,
        "Sports": 0.9,
        "Science": 0.7,
    },
    "liked_keywords": ["sri lanka", "economy", "politics", "health"],
    "disliked_keywords": ["rumor", "speculation", "fake", "hoax"],
    "blocked_sources": [],
    "trusted_sources": sorted(TRUSTED_SOURCES.keys()),
    "strict_local_only": True,
    "seen_articles": set(),
}


def _coerce_profile(profile: dict | None = None) -> dict:
    base = {**DEFAULT_USER_PROFILE}
    if profile:
        for key, value in profile.items():
            base[key] = value
    base["liked_keywords"] = [str(v).lower() for v in base.get("liked_keywords", [])]
    base["disliked_keywords"] = [str(v).lower() for v in base.get("disliked_keywords", [])]
    base["blocked_sources"] = [str(v) for v in base.get("blocked_sources", [])]
    base["trusted_sources"] = [str(v) for v in base.get("trusted_sources", list(TRUSTED_SOURCES.keys()))]
    base["strict_local_only"] = bool(base.get("strict_local_only", True))
    base["seen_articles"] = set(str(v) for v in base.get("seen_articles", []))
    return base


def _save_profile(profile: dict) -> None:
    payload = {
        "topic_weights": profile.get("topic_weights", {}),
        "liked_keywords": profile.get("liked_keywords", []),
        "disliked_keywords": profile.get("disliked_keywords", []),
        "blocked_sources": profile.get("blocked_sources", []),
        "trusted_sources": profile.get("trusted_sources", list(TRUSTED_SOURCES.keys())),
        "strict_local_only": bool(profile.get("strict_local_only", True)),
        "seen_articles": sorted(profile.get("seen_articles", set())),
    }
    PROFILE_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _load_profile() -> dict:
    if PROFILE_PATH.exists():
        try:
            data = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
            return _coerce_profile(data)
        except Exception:
            pass
    profile = _coerce_profile()
    _save_profile(profile)
    return profile


USER_PROFILE = _load_profile()

# ---------------------------------------------------------------------------
# 2. Tiny in-memory cache. Good enough for a single personal user; if this
#    ever needs to survive restarts, swap this dict for a JSON file or SQLite.
# ---------------------------------------------------------------------------


@dataclass
class TopicCache:
    articles: list[dict] = field(default_factory=list)
    fetched_at: float = 0.0


_cache: dict[str, TopicCache] = {topic: TopicCache() for topic in TOPICS}
_cache_lock = asyncio.Lock()

_TAG_RE = re.compile(r"<[^>]+>")
_STOPWORDS = {
    "the", "a", "an", "and", "or", "for", "with", "from", "into", "this",
    "that", "these", "those", "about", "after", "before", "over", "under",
    "via", "while", "their", "there", "where", "what", "when", "why", "how",
    "have", "has", "had", "will", "would", "should", "could", "can", "but",
    "not", "more", "most", "been", "being", "new", "news", "story", "update"
}


def _clean_summary(raw: str) -> str:
    """RSS summaries often contain HTML and entities — strip both, then trim."""
    if not raw:
        return ""
    text = html.unescape(_TAG_RE.sub("", raw)).strip()
    text = re.sub(r"\s+", " ", text)
    if len(text) > SUMMARY_MAX_CHARS:
        text = text[:SUMMARY_MAX_CHARS].rsplit(" ", 1)[0] + "…"
    return text


def _normalize_text(value: str) -> str:
    if not value:
        return ""
    value = html.unescape(value)
    value = re.sub(r"[^a-zA-Z0-9\s]", " ", value.lower())
    value = re.sub(r"\s+", " ", value).strip()
    return value


def _article_key(article: dict) -> str:
    link = str(article.get("link") or "").strip()
    title = str(article.get("title") or "").strip()
    source = str(article.get("source") or "").strip()
    if link:
        return "url:" + link
    if title:
        return "title:" + _normalize_text(title)
    return "source:" + _normalize_text(source)


def _story_signature(article: dict) -> str:
    title = _normalize_text(article.get("title") or "")
    if title:
        return title
    link = str(article.get("link") or "").strip()
    return _normalize_text(link)


def _token_overlap(a: str, b: str) -> float:
    a_tokens = set(re.findall(r"\w+", a.lower()))
    b_tokens = set(re.findall(r"\w+", b.lower()))
    if not a_tokens and not b_tokens:
        return 0.0
    inter = a_tokens & b_tokens
    union = a_tokens | b_tokens
    return len(inter) / max(1, len(union))


def deduplicate_articles(articles: Iterable[dict]) -> list[dict]:
    deduped: list[dict] = []
    for article in sorted(articles, key=lambda a: a.get("published", ""), reverse=True):
        item_key = _story_signature(article)
        match_index = None
        for idx, saved in enumerate(deduped):
            saved_key = _story_signature(saved)
            if item_key and item_key == saved_key:
                match_index = idx
                break
            if _token_overlap(item_key, saved_key) >= 0.62:
                match_index = idx
                break
        if match_index is None:
            deduped.append(article)
            continue
        existing = deduped[match_index]
        if article.get("published") and existing.get("published"):
            if article["published"] > existing["published"]:
                deduped[match_index] = article
        elif article.get("source", "").lower() in TRUSTED_SOURCES and existing.get("source", "").lower() not in TRUSTED_SOURCES:
            deduped[match_index] = article
    return deduped


def filter_seen_articles(articles: Iterable[dict], seen: set[str]) -> list[dict]:
    blocked = set()
    for key in seen:
        key_text = str(key).strip()
        if not key_text:
            continue
        blocked.add(key_text)
        blocked.add(f"url:{key_text}")
        blocked.add(f"title:{_normalize_text(key_text)}")

    filtered = []
    for article in articles:
        article_key = _article_key(article)
        article_link = str(article.get("link") or "").strip()
        article_title = _normalize_text(str(article.get("title") or ""))
        if article_key in blocked or article_link in blocked or article_title in blocked:
            continue
        filtered.append(article)
    return filtered


def _parse_article_time(published: str) -> float | None:
    if not published:
        return None
    for fmt in (
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S%z",
        "%a, %d %b %Y %H:%M:%S %Z",
        "%a, %d %b %Y %H:%M:%S GMT",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
    ):
        try:
            return time.mktime(time.strptime(published, fmt))
        except ValueError:
            continue
    return None


def filter_stale_articles(articles: Iterable[dict]) -> list[dict]:
    fresh = []
    for article in articles:
        published = str(article.get("published") or "")
        timestamp = _parse_article_time(published)
        if timestamp is None:
            fresh.append(article)
            continue
        age_hours = max(0.0, (time.time() - timestamp) / 3600)
        if age_hours <= MAX_STALE_HOURS:
            fresh.append(article)
    return fresh


def _extract_keywords(text: str) -> list[str]:
    words = re.findall(r"[a-zA-Z][a-zA-Z0-9]+", text.lower())
    words = [w for w in words if len(w) > 3 and w not in _STOPWORDS]
    return words[:8]


def logistic_news_score(article: dict, topic: str, profile: dict | None = None) -> float:
    profile = profile or {}
    source_name = str(article.get("source") or "Unknown")
    source_name_lower = source_name.lower()
    blocked_sources = {str(v).lower() for v in profile.get("blocked_sources", [])}
    if source_name_lower in blocked_sources:
        return 0.0

    source_trust = TRUSTED_SOURCES.get(source_name, 0.6)
    trusted_sources = {str(v).lower() for v in profile.get("trusted_sources", list(TRUSTED_SOURCES.keys()))}
    if profile.get("strict_local_only") and source_name_lower not in trusted_sources and topic == "Sri Lanka":
        source_trust *= 0.2
    local_source = any(
        token in source_name_lower
        for token in [
            "daily mirror",
            "ada derana",
            "news first",
            "sri lanka",
            "sunday times",
            "the island",
            "colombo",
            "lankapuvath",
            "hirunews",
        ]
    )

    topic_weights = profile.get("topic_weights", {})
    topic_weight = float(topic_weights.get(topic, 0.75))

    summary_text = f"{article.get('title', '')} {article.get('summary', '')}".lower()
    liked_keywords = [str(k).lower() for k in profile.get("liked_keywords", [])]
    disliked_keywords = [str(k).lower() for k in profile.get("disliked_keywords", [])]

    like_hits = sum(1 for kw in liked_keywords if kw in summary_text)
    dislike_hits = sum(1 for kw in disliked_keywords if kw in summary_text)

    published = str(article.get("published") or "")
    recency = 0.5
    timestamp = _parse_article_time(published)
    if timestamp is not None:
        age_hours = max(0.0, (time.time() - timestamp) / 3600)
        recency = max(0.05, min(1.0, 1.0 / (1.0 + age_hours / 12.0)))
    if timestamp is not None and (time.time() - timestamp) / 3600 > MAX_STALE_HOURS:
        return 0.0

    local_priority = 1.0 if topic == "Sri Lanka" else 0.45
    if "sri lanka" in summary_text or "colombo" in summary_text or "lanka" in summary_text:
        local_priority = 1.35

    z = (
        -1.0
        + (2.3 * source_trust)
        + (2.4 * local_priority if local_source else 0.0)
        + (1.2 * local_priority)
        + (1.5 * recency)
        + (0.85 * topic_weight)
        + (0.7 * like_hits)
        - (1.1 * dislike_hits)
    )
    return 1.0 / (1.0 + math.exp(-z))


def _fetch_topic_sync(topic: str) -> list[dict]:
    """Blocking feed fetch — runs in a worker thread, see fetch_topic()."""
    articles: list[dict] = []
    for feed_url in TOPICS[topic]:
        try:
            parsed = feedparser.parse(feed_url)
            source_name = parsed.feed.get("title", feed_url) if parsed.feed else feed_url
            for entry in parsed.entries[:MAX_ARTICLES_PER_TOPIC]:
                articles.append(
                    {
                        "title": entry.get("title", "(untitled)"),
                        "summary": _clean_summary(
                            entry.get("summary", entry.get("description", ""))
                        ),
                        "link": entry.get("link", ""),
                        "source": entry.get("source", {}).get("title", source_name)
                        if isinstance(entry.get("source"), dict)
                        else source_name,
                        "published": entry.get("published", ""),
                        # feedparser gives a parsed time struct when it can;
                        # fall back to 0 so unsortable entries sink to the end.
                        "_sort_key": time.mktime(entry.published_parsed)
                        if entry.get("published_parsed")
                        else 0,
                    }
                )
        except Exception as exc:  # noqa: BLE001 - one bad feed shouldn't sink the topic
            print(f"[news-app] failed to fetch {feed_url}: {exc}")

    articles.sort(key=lambda a: a["_sort_key"], reverse=True)
    for a in articles:
        del a["_sort_key"]

    deduped = deduplicate_articles(articles)
    deduped = filter_seen_articles(deduped, USER_PROFILE.get("seen_articles", set()))
    deduped = filter_stale_articles(deduped)

    blocked_sources = {str(v).lower() for v in USER_PROFILE.get("blocked_sources", [])}
    if USER_PROFILE.get("strict_local_only") and topic == "Sri Lanka":
        deduped = [
            article for article in deduped
            if (str(article.get("source") or "").lower() in {"ada derana", "daily mirror", "news first", "the island", "the sunday times", "sri lanka mirror"})
            or ("sri lanka" in (str(article.get("title") or "") + " " + str(article.get("summary") or "")).lower())
        ]
    deduped = [
        article for article in deduped if str(article.get("source") or "").lower() not in blocked_sources
    ]

    scored = []
    for article in deduped:
        article["_rank_score"] = logistic_news_score(article, topic, USER_PROFILE)
        published = str(article.get("published") or "")
        article["_published_ts"] = _parse_article_time(published) or 0
        scored.append(article)
    scored.sort(key=lambda a: (a["_published_ts"], a["_rank_score"]), reverse=True)
    for a in scored:
        del a["_rank_score"]
        del a["_published_ts"]
    return scored[:MAX_ARTICLES_PER_TOPIC]


async def fetch_topic(topic: str, force: bool = False) -> list[dict]:
    async with _cache_lock:
        cache = _cache[topic]
        is_stale = (time.time() - cache.fetched_at) > CACHE_TTL_SECONDS
        if not force and not is_stale and cache.articles:
            return cache.articles

    # Do the network-bound parsing off the event loop.
    articles = await asyncio.to_thread(_fetch_topic_sync, topic)

    async with _cache_lock:
        _cache[topic] = TopicCache(articles=articles, fetched_at=time.time())
        return _cache[topic].articles


# ---------------------------------------------------------------------------
# 3. FastAPI app: a couple of JSON endpoints, plus the static frontend.
# ---------------------------------------------------------------------------

app = FastAPI(title="Personal News Digest")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.get("/api/profile")
async def get_profile():
    return {
        "topic_weights": USER_PROFILE.get("topic_weights", {}),
        "liked_keywords": USER_PROFILE.get("liked_keywords", []),
        "disliked_keywords": USER_PROFILE.get("disliked_keywords", []),
        "blocked_sources": USER_PROFILE.get("blocked_sources", []),
        "trusted_sources": USER_PROFILE.get("trusted_sources", list(TRUSTED_SOURCES.keys())),
        "strict_local_only": bool(USER_PROFILE.get("strict_local_only", True)),
        "seen_articles": sorted(USER_PROFILE.get("seen_articles", set())),
    }


@app.post("/api/profile/settings")
async def update_profile_settings(payload: dict = Body(...)):
    trusted_sources = payload.get("trusted_sources", USER_PROFILE.get("trusted_sources", list(TRUSTED_SOURCES.keys())))
    blocked_sources = payload.get("blocked_sources", USER_PROFILE.get("blocked_sources", []))
    strict_local_only = bool(payload.get("strict_local_only", USER_PROFILE.get("strict_local_only", True)))
    USER_PROFILE["trusted_sources"] = [str(v) for v in trusted_sources]
    USER_PROFILE["blocked_sources"] = [str(v) for v in blocked_sources]
    USER_PROFILE["strict_local_only"] = strict_local_only
    _save_profile(USER_PROFILE)
    return {"status": "saved", "strict_local_only": strict_local_only, "trusted_sources": USER_PROFILE["trusted_sources"]}


@app.post("/api/profile/feedback")
async def record_feedback(payload: dict = Body(...)):
    label = str(payload.get("label") or "").strip().lower()
    title = str(payload.get("title") or "").strip()
    source = str(payload.get("source") or "").strip()
    link = str(payload.get("link") or "").strip()
    if label not in {"interested", "moderate", "not_interested"}:
        return {"status": "ignored", "message": "Unknown feedback label"}

    profile = USER_PROFILE
    seen_key = _article_key({"link": link, "title": title, "source": source})
    profile["seen_articles"].add(seen_key)
    if link:
        profile["seen_articles"].add(link)
    if title:
        profile["seen_articles"].add(_normalize_text(title))

    if source:
        profile["blocked_sources"] = list(dict.fromkeys(profile.get("blocked_sources", []) + [source]))

    if title or link:
        keywords = _extract_keywords(f"{title} {source} {link}")
        for keyword in keywords:
            if label == "interested":
                if keyword not in profile["liked_keywords"]:
                    profile["liked_keywords"].append(keyword)
            elif label == "not_interested":
                if keyword not in profile["disliked_keywords"]:
                    profile["disliked_keywords"].append(keyword)
            elif label == "moderate":
                if keyword not in profile["disliked_keywords"] and keyword not in profile["liked_keywords"]:
                    profile["disliked_keywords"].append(keyword)

    _save_profile(profile)
    return {"status": "saved", "label": label, "key": seen_key}


@app.get("/api/topics")
async def list_topics():
    return {"topics": list(TOPICS.keys())}


@app.get("/api/news")
async def get_news(
    topic: str = Query(..., description="One of /api/topics"),
    refresh: bool = Query(False, description="Force a refetch, skipping the cache"),
):
    if topic not in TOPICS:
        return {"error": f"Unknown topic '{topic}'. See /api/topics.", "articles": []}
    articles = await fetch_topic(topic, force=refresh)
    return {"topic": topic, "articles": articles, "cached_at": _cache[topic].fetched_at}


# Serve the frontend (static/index.html, manifest.json, sw.js, ...) at "/".
# This MUST be mounted last — routes above take priority over the catch-all.


@app.post("/api/v1/ingest/ide/event")
async def ignore_ide_events():
    return {"status": "ignored"}


@app.post("/api/v1/ingest/browser/event")
async def ignore_browser_events():
    return {"status": "ignored"}


app.mount("/", StaticFiles(directory="static", html=True), name="static")
