# Digest: Multi-Source News Features and Application Plan

## 1. Feature Description

Digest should collect the same news story from multiple reliable sources and present one useful story card instead of several repeated headlines.

For every story, the application should:

- Find related reports from different configured news sources.
- Group reports that describe the same event.
- Show one primary headline and a short preview.
- Provide a `Read more` button for an expanded full summary.
- Provide the best link for reading the original story online.
- Preserve links to other sources for comparison.
- Prefer trusted and relevant sources when selecting the primary link.
- Cache results so repeated requests do not overload external feeds.

The first version should use RSS data and local summary generation. An optional AI summarization provider can be added later without changing the frontend contract.

## 2. Current Application Structure

- `main.py`: FastAPI backend, RSS fetching, article cleanup, deduplication, ranking, caching, and API routes.
- `static/index.html`: Vanilla JavaScript frontend that loads topics, renders article cards, and handles user actions.
- `test_news_ranking.py`: Backend unit tests for ranking, deduplication, filtering, and freshness.
- `requirements.txt`: Python dependencies.

The existing deduplication logic currently keeps one version of a repeated story. It should be changed to keep the related source reports inside a grouped story instead of discarding them.

## 3. Target Story Data Model

Each item returned by `/api/news` should eventually have a stable structure similar to this:

```json
{
  "id": "story-unique-id",
  "title": "Parliament approves the new budget",
  "summary": "Short preview from the main RSS report.",
  "full_summary": "Combined summary of the reports.",
  "source": "Reuters",
  "best_link": "https://example.com/original-story",
  "published": "2026-09-30T12:00:00+00:00",
  "sources": [
    {
      "name": "Reuters",
      "url": "https://example.com/reuters-story",
      "summary": "Report from Reuters.",
      "published": "2026-09-30T12:00:00+00:00"
    },
    {
      "name": "BBC",
      "url": "https://example.com/bbc-story",
      "summary": "Report from BBC.",
      "published": "2026-09-30T12:30:00+00:00"
    }
  ]
}
```

`full_summary` should be generated during the normal topic fetch and stored with the grouped story. The details endpoint must only read the already-cached story, so `Read more` never performs a network request.

## 4. Backend Implementation Plan

### Phase 1: Group reports during the topic fetch

Fold grouping into the existing `_fetch_topic_sync` pipeline, after article cleanup and before ranking. Add a grouping function in `main.py`, for example:

```python
def group_related_articles(articles):
    ...
```

Use the existing title normalization and `_token_overlap` helpers. Exact normalized-title matches should group as before. For cross-outlet phrasing, use a separate starting threshold of about `0.35` to `0.40`, rather than reusing the near-duplicate threshold of `0.62`. Only compare reports within a 48-hour publication window to limit false positives. Keep the threshold easy to tune after inspecting real grouped output.

Each group should contain:

- A representative headline.
- All matching source reports.
- The newest publication time.
- A stable story ID.

### Phase 2: Rank each grouped story and choose its primary link

Add a function such as:

```python
def choose_best_source(sources, topic, profile):
    ...
```

Run `logistic_news_score` once for the representative article in each group. Rank candidate source links using:

- Existing `TRUSTED_SOURCES` scores.
- User blocked-source and trusted-source settings.
- Topic relevance.
- Title similarity.
- Publication freshness.
- Availability of a direct article URL.

Return both the selected URL and source name while preserving the complete `sources` list on the group:

```json
{
  "best_link": "https://example.com/story",
  "best_source": "Reuters"
}
```

### Phase 3: Generate the expanded summary during fetch

Run a local summary function while the topic cache is being built:

1. Combines descriptions from all matching RSS reports.
2. Splits each short RSS description into sentences.
3. Removes near-identical sentences using `_token_overlap` with a high threshold.
4. Concatenates the remaining sentences, capped at approximately 6 sentences.
5. Clearly avoids inventing details that are not present in the sources.

RSS descriptions are already short, so do not add an NLP or extractive-summarization dependency for v1.

Example function:

```python
def generate_full_summary(title, sources):
    ...
```

Later, an AI provider can be added behind this function. The API response should remain the same regardless of which summary implementation is used.

### Phase 4: Make the article details endpoint an in-memory lookup

Add an endpoint similar to:

```text
GET /api/news/{story_id}/details
```

The endpoint should:

- Locate the story in the existing topic cache by its stable story ID.
- Return the precomputed full summary, selected reading link, and all source links.
- Perform zero network calls and no additional feed search.
- Return a clear not-found response if the story is no longer in the topic cache.

Example response:

```json
{
  "id": "story-unique-id",
  "full_summary": "Combined summary of the reports...",
  "best_link": "https://example.com/story",
  "best_source": "Reuters",
  "sources": [
    {"name": "Reuters", "url": "https://example.com/story"},
    {"name": "BBC", "url": "https://example.com/other-story"}
  ]
}
```

## 5. Frontend Implementation Plan

Update `renderArticles()` in `static/index.html`.

Each article card should contain:

- Headline.
- Short RSS summary.
- Source and publication time.
- `Read more` button.
- Hidden details section.
- Full summary after loading.
- `Read full article` button using `best_link`.
- Optional links to the other reports.

Expected interaction:

```text
User clicks Read more
    -> Frontend requests /api/news/{story_id}/details
    -> Loading state appears inside the card
    -> Full summary is displayed
    -> Best article link is displayed
    -> Other source links are displayed
```

The article card should handle these states:

- Details loading.
- Details successfully loaded.
- Details request failed.
- Story has only one source.
- Story has no valid online link.

The `Read more` button should stop event propagation so it does not accidentally open the original article before the details are loaded.

## 6. Caching and Performance

Use the existing topic cache as the single cache for headlines, grouped sources, selected links, and full summaries.

Recommended starting values:

- Topic cache: keep the existing 20-minute duration.
- Build the complete pipeline once per topic-cache refresh: fetch, clean, group, rank, summarize, and cache.
- Keep existing feed timeouts and failure handling so an unavailable configured feed does not prevent other reports from being grouped.
- On a details request, return the cached story immediately without a timeout or external dependency.

## 7. Testing Plan

Add backend tests in `test_news_ranking.py` for:

- Exact duplicate reports being grouped.
- Similar reports from different sources being grouped.
- Unrelated reports remaining separate.
- All source links being preserved.
- Best-source selection respecting trust scores and blocked sources.
- Duplicate sentences being removed from the full summary.
- Grouping respecting the 48-hour publication window.
- The details endpoint returning the expected JSON structure from the topic cache.
- The details endpoint making no network calls.
- A failed source feed not breaking the topic fetch or grouped story response.

Perform a manual frontend check for:

- `Read more` opening only the selected card.
- Loading and error states.
- Multiple source links opening in a new tab.
- The best link opening the selected primary source.
- Mobile layout and long headlines.

## 8. Recommended Delivery Order

1. Add stable story IDs.
2. Replace destructive deduplication inside `_fetch_topic_sync` with grouped stories and preserved source reports.
3. Tune the separate cross-outlet grouping threshold around `0.35` to `0.40` with the 48-hour window.
4. Add best-source selection and rank each group once.
5. Add sentence-level local full-summary generation during fetch.
6. Make the story details API endpoint a topic-cache lookup.
7. Add the frontend `Read more` interaction.
8. Add backend tests and manual browser checks, including the no-network details path.
9. Add an optional AI summary provider if local summaries are not sufficient.

## 9. Important Constraints

- RSS descriptions may be incomplete, so the full summary should be labelled as a combined summary rather than a replacement for the original reporting.
- The application should link to and credit the original publishers instead of copying full article text.
- External feeds can fail or rate-limit requests; every feed request needs a timeout and graceful fallback.
- API keys should be stored in environment variables if a paid search or AI service is added.
- The frontend should never trust raw HTML from RSS feeds. Return cleaned text from the backend and render it as text.
