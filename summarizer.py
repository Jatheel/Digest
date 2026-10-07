"""
Story summaries
---------------
Every story gets two pieces of text built only from what its sources say:

* description  - one short paragraph (2-3 sentences) shown under the headline.
* full_summary - a brief (up to ~6 sentences) combining every source, shown
                 when the reader taps "Read more".

The default is extractive and free: sentences are scored by how central they
are across sources (facts several outlets agree on rank higher) and by how
close they are to the headline; near-duplicates are dropped. Nothing is
invented, because every sentence comes from a source.

Optionally, when ANTHROPIC_API_KEY is set (e.g. as a GitHub Actions secret),
stories reported by two or more outlets get an abstractive summary from
Claude, constrained to the facts in the source texts. Results are cached by
story id so a story is summarized once, not every 30 minutes.
"""

import json
import math
import os
import re
import time

DESCRIPTION_MAX_SENTENCES = 3
DESCRIPTION_MAX_CHARS = 460
FULL_SUMMARY_MAX_SENTENCES = 6
FULL_SUMMARY_MAX_CHARS = 1100
DUPLICATE_OVERLAP = 0.6

LLM_MODEL = os.environ.get("DIGEST_SUMMARY_MODEL", "claude-opus-5-5")
LLM_MAX_PER_RUN = int(os.environ.get("DIGEST_LLM_MAX_PER_RUN", "20"))
LLM_MIN_SOURCES = 2
LLM_CACHE_MAX_AGE_SECONDS = 4 * 24 * 3600

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])[\"'”’)\]]?\s+(?=[A-Z0-9\"'“‘(])")
_BOILERPLATE_RE = re.compile(
    r"click here|subscribe|newsletter|sign up|cookie|all rights reserved|read more|"
    r"follow us|©|download the|share this|advertisement|for the latest|watch:|listen:|"
    r"commission|affiliate|\(pictured|getty images|reuters/|photograph:|image caption|"
    r"this article|this story|our journalism|support us|terms of use|privacy policy",
    re.I,
)
_WORD_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = {
    "the", "a", "an", "and", "or", "for", "with", "from", "into", "this", "that",
    "these", "those", "about", "after", "before", "over", "under", "while",
    "their", "there", "where", "what", "when", "why", "how", "have", "has",
    "had", "will", "would", "should", "could", "can", "but", "not", "been",
    "being", "to", "of", "in", "on", "at", "by", "as", "is", "are", "was",
    "were", "be", "it", "its", "his", "her", "they", "them", "he", "she", "we",
    "you", "i", "said", "says", "also", "who", "which", "than", "more", "most",
}


def _words(text: str) -> list[str]:
    tokens = []
    for word in _WORD_RE.findall(text.lower()):
        if word in _STOPWORDS or len(word) < 2:
            continue
        if len(word) > 4 and word.endswith("s"):
            word = word[:-1]
        tokens.append(word)
    return tokens


def _overlap(a: str, b: str) -> float:
    a_set, b_set = set(_words(a)), set(_words(b))
    if not a_set or not b_set:
        return 0.0
    return len(a_set & b_set) / len(a_set | b_set)


def split_sentences(text: str) -> list[str]:
    sentences = []
    for part in _SENTENCE_SPLIT_RE.split((text or "").strip()):
        part = part.strip()
        if len(part) < 30 or len(part) > 450 or len(part.split()) < 5:
            continue
        if _BOILERPLATE_RE.search(part):
            continue
        sentences.append(part)
    # A trailing fragment cut off by the feed ("…") reads badly; keep it only
    # if it is all we have.
    if len(sentences) > 1 and sentences[-1].endswith("…"):
        sentences.pop()
    return sentences


def _vectorize(token_lists: list[list[str]]) -> list[dict[str, float]]:
    df: dict[str, int] = {}
    for tokens in token_lists:
        for token in set(tokens):
            df[token] = df.get(token, 0) + 1
    n = len(token_lists)
    vectors = []
    for tokens in token_lists:
        counts: dict[str, int] = {}
        for token in tokens:
            counts[token] = counts.get(token, 0) + 1
        vector = {t: c * math.log(1 + n / df[t]) for t, c in counts.items()}
        norm = math.sqrt(sum(v * v for v in vector.values())) or 1.0
        vectors.append({t: v / norm for t, v in vector.items()})
    return vectors


def _cosine(a: dict[str, float], b: dict[str, float]) -> float:
    if len(a) > len(b):
        a, b = b, a
    return sum(v * b.get(t, 0.0) for t, v in a.items())


def extractive_summary(title: str, texts: list[dict], max_sentences: int, max_chars: int) -> str:
    """Pick the most central, non-redundant sentences across all sources."""
    candidates = []  # (source_index, position, sentence)
    for source_index, entry in enumerate(texts):
        for position, sentence in enumerate(split_sentences(entry.get("text", ""))):
            if _overlap(sentence, title) >= 0.8:
                continue  # just restates the headline
            candidates.append((source_index, position, sentence))
    if not candidates:
        return ""

    vectors = _vectorize([_words(s) for _, _, s in candidates] + [_words(title)])
    title_vector = vectors.pop()
    multi_source = len({c[0] for c in candidates}) > 1
    scores = []
    for i, (source_index, position, _) in enumerate(candidates):
        others = [
            _cosine(vectors[i], vectors[j])
            for j, other in enumerate(candidates)
            if j != i and (not multi_source or other[0] != source_index)
        ]
        centrality = sum(others) / len(others) if others else 0.0
        position_bonus = 0.25 if position == 0 else 0.12 if position == 1 else 0.0
        source_bonus = 0.1 if source_index == 0 else 0.0  # the best source leads
        scores.append(centrality + 0.6 * _cosine(vectors[i], title_vector) + position_bonus + source_bonus)

    chosen: list[int] = []
    used_chars = 0
    remaining = set(range(len(candidates)))
    while remaining and len(chosen) < max_sentences:
        def mmr(i: int) -> float:
            redundancy = max((_cosine(vectors[i], vectors[j]) for j in chosen), default=0.0)
            return scores[i] - 0.7 * redundancy

        best = max(remaining, key=mmr)
        remaining.discard(best)
        sentence = candidates[best][2]
        if any(_overlap(sentence, candidates[j][2]) >= DUPLICATE_OVERLAP for j in chosen):
            continue
        if chosen and used_chars + len(sentence) > max_chars:
            continue
        chosen.append(best)
        used_chars += len(sentence) + 1

    chosen.sort(key=lambda i: (candidates[i][0], candidates[i][1]))
    return " ".join(candidates[i][2] for i in chosen)


def lead_paragraph(title: str, text: str) -> str:
    """The opening sentences of a single report (news leads are written to
    summarize the story), skipping any that only restate the headline."""
    picked = []
    used = 0
    for sentence in split_sentences(text):
        if _overlap(sentence, title) >= 0.8:
            continue
        if picked and used + len(sentence) > DESCRIPTION_MAX_CHARS:
            break
        picked.append(sentence)
        used += len(sentence) + 1
        if len(picked) >= DESCRIPTION_MAX_SENTENCES:
            break
    return " ".join(picked)


def _fallback_text(texts: list[dict]) -> str:
    """When no full sentence survives the filters, use the longest raw text."""
    best = max((entry.get("text", "") for entry in texts), key=len, default="")
    if len(best) <= DESCRIPTION_MAX_CHARS:
        return best
    return best[:DESCRIPTION_MAX_CHARS].rsplit(" ", 1)[0] + "…"


def summarize(title: str, texts: list[dict]) -> tuple[str, str]:
    """Return (description, full_summary). `texts` is best source first."""
    texts = [t for t in texts if (t.get("text") or "").strip()]
    if not texts:
        return "", ""
    description = lead_paragraph(title, texts[0]["text"])
    if len(description) < 140:
        description = extractive_summary(title, texts, DESCRIPTION_MAX_SENTENCES, DESCRIPTION_MAX_CHARS) or description
    if not description:
        description = _fallback_text(texts)
    full_summary = extractive_summary(title, texts, FULL_SUMMARY_MAX_SENTENCES, FULL_SUMMARY_MAX_CHARS)
    if len(full_summary) < len(description):
        full_summary = description
    return description, full_summary


# Kept for older callers: one combined summary from {"summary": ...} records.
def generate_full_summary(title: str, sources) -> str:
    texts = [{"source": s.get("name", ""), "text": s.get("summary", "")} for s in sources]
    return extractive_summary(title, texts, FULL_SUMMARY_MAX_SENTENCES, FULL_SUMMARY_MAX_CHARS) or str(title or "")


# ---------------------------------------------------------------------------
# Optional: Claude summaries for multi-source stories.
# ---------------------------------------------------------------------------

LLM_SYSTEM_PROMPT = (
    "You write short, neutral news briefs for a mobile news reader. You are given "
    "a headline and excerpts from several outlets that reported the same story. "
    "Use only facts stated in those excerpts: do not add background knowledge, "
    "speculation, opinion or quotes that are not in the text. If the outlets "
    "disagree on a fact, say so briefly. Write plain prose with no markdown, no "
    "headings and no source attribution lists."
)

LLM_SCHEMA = {
    "type": "object",
    "properties": {
        "description": {
            "type": "string",
            "description": "One paragraph of 2-3 sentences (max ~70 words) saying what happened.",
        },
        "summary": {
            "type": "string",
            "description": "A brief of 4-6 sentences (max ~140 words) combining the key facts from all outlets.",
        },
    },
    "required": ["description", "summary"],
    "additionalProperties": False,
}


def _llm_prompt(story: dict) -> str:
    parts = [f"Headline: {story['title']}", ""]
    for entry in story.get("_texts", [])[:6]:
        parts.append(f"Outlet: {entry['source']}\n{entry['text'][:1500]}\n")
    return "\n".join(parts)


def _llm_client():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        import anthropic
    except ImportError:
        print("[digest] ANTHROPIC_API_KEY is set but the anthropic package is not installed")
        return None
    return anthropic.Anthropic(max_retries=2, timeout=90.0)


def _summarize_with_claude(client, story: dict) -> dict | None:
    import anthropic

    try:
        response = client.beta.messages.create(
            model=LLM_MODEL,
            max_tokens=4000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            system=LLM_SYSTEM_PROMPT,
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": LLM_SCHEMA}},
            messages=[{"role": "user", "content": _llm_prompt(story)}],
        )
    except anthropic.RateLimitError:
        print("[digest] Claude rate limit hit; using extractive summaries for the rest of this run")
        raise
    except (anthropic.APIStatusError, anthropic.APIConnectionError) as exc:
        print(f"[digest] Claude summary failed for {story['id']}: {exc}")
        return None
    if response.stop_reason in ("refusal", "max_tokens"):
        return None
    text = next((b.text for b in response.content if b.type == "text"), "")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    description, summary = data.get("description", "").strip(), data.get("summary", "").strip()
    if not description or not summary:
        return None
    return {"description": description, "summary": summary}


def apply_llm_summaries(stories: list[dict], cache: dict) -> None:
    """Fill multi-source stories from the cache, calling Claude for new ones.

    `cache` maps story id -> {"n": source count, "ts": time, "description",
    "summary"}. It is updated in place; entries older than 4 days are pruned.
    """
    now = time.time()
    for key in [k for k, v in cache.items() if now - v.get("ts", 0) > LLM_CACHE_MAX_AGE_SECONDS]:
        del cache[key]

    client = _llm_client()
    calls = 0
    # The highest-ranked (first) stories in each topic get summarized first.
    for story in stories:
        source_count = story["features"]["source_count"]
        if source_count < LLM_MIN_SOURCES:
            continue
        cached = cache.get(story["id"])
        stale = cached is None or source_count >= cached.get("n", 0) + 2
        if stale and client is not None and calls < LLM_MAX_PER_RUN:
            calls += 1
            try:
                result = _summarize_with_claude(client, story)
            except Exception as exc:  # noqa: BLE001 - rate limited or SDK mismatch: stop calling this run
                print(f"[digest] stopping Claude summaries for this run: {exc}")
                client = None
                result = None
            if result:
                cached = {"n": source_count, "ts": now, **result}
                cache[story["id"]] = cached
        if cached:
            story["description"] = cached["description"]
            story["full_summary"] = cached["summary"]
            story["summary_by"] = "ai"
    if calls:
        print(f"[digest] Claude summaries requested: {calls}")
