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
  "full_summary": null,
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

`full_summary` may remain `null` until the user clicks `Read more`. This keeps the initial topic request fast.

## 4. Backend Implementation Plan

### Phase 1: Preserve all matching sources

Add a grouping function in `main.py`, for example:

```python
def group_related_articles(articles):
    ...
```

Use the existing title normalization and token-overlap helpers. Reports should be grouped when their normalized titles match or their similarity exceeds a chosen threshold. Add a publication time limit, such as 48 hours, to avoid grouping unrelated older stories.

Each group should contain:

- A representative headline.
- All matching source reports.
- The newest publication time.
- A stable story ID.

### Phase 2: Search configured feeds

Create a source configuration containing RSS feeds that can be searched or checked for related stories:

```python
SOURCE_FEEDS = {
    "Ada Derana": "...",
    "Daily Mirror": "...",
    "News First": "...",
    "BBC": "...",
    "Reuters": "...",
    "AP": "...",
    "Al Jazeera": "..."
}
```

Use source RSS feeds and Google News RSS rather than scraping article pages initially. RSS is simpler, more reliable, and less likely to be blocked.

Do not perform a large external search for every card during the initial page load. Search on demand when the user requests the expanded details, then cache the result.

### Phase 3: Choose the best article link

Add a function such as:

```python
def choose_best_source(sources, topic, profile):
    ...
```

Rank candidate sources using:

- Existing `TRUSTED_SOURCES` scores.
- User blocked-source and trusted-source settings.
- Topic relevance.
- Title similarity.
- Publication freshness.
- Availability of a direct article URL.

Return both the selected URL and source name:

```json
{
  "best_link": "https://example.com/story",
  "best_source": "Reuters"
}
```

### Phase 4: Generate the expanded summary

Start with a local summary function that:

1. Combines descriptions from all matching RSS reports.
2. Removes repeated sentences.
3. Selects the most informative sentences.
4. Produces approximately 3 to 6 sentences.
5. Clearly avoids inventing details that are not present in the sources.

Example function:

```python
def generate_full_summary(title, sources):
    ...
```

Later, an AI provider can be added behind this function. The API response should remain the same regardless of which summary implementation is used.

### Phase 5: Add an article details endpoint

Add an endpoint similar to:

```text
GET /api/news/{story_id}/details
```

The endpoint should:

- Locate or search related reports.
- Generate the full summary.
- Select the best reading link.
- Return all source links.
- Cache the result for a short period.

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

Use two levels of caching:

1. Existing topic cache for the initial RSS headline list.
2. Story-details cache for grouped sources and expanded summaries.

Recommended starting values:

- Topic cache: keep the existing 20-minute duration.
- Story details cache: 30 to 60 minutes.
- Limit the number of external feeds checked for one story.
- Add timeouts so one unavailable source does not block the whole response.
- Return the original RSS article if the additional source search fails.

## 7. Testing Plan

Add backend tests in `test_news_ranking.py` for:

- Exact duplicate reports being grouped.
- Similar reports from different sources being grouped.
- Unrelated reports remaining separate.
- All source links being preserved.
- Best-source selection respecting trust scores and blocked sources.
- Duplicate sentences being removed from the full summary.
- The details endpoint returning the expected JSON structure.
- A failed source feed not breaking the entire story response.

Perform a manual frontend check for:

- `Read more` opening only the selected card.
- Loading and error states.
- Multiple source links opening in a new tab.
- The best link opening the selected primary source.
- Mobile layout and long headlines.

## 8. Recommended Delivery Order

1. Add stable story IDs.
2. Replace destructive deduplication with grouped stories.
3. Preserve all matching source reports.
4. Add best-source selection.
5. Add local full-summary generation.
6. Add the story details API endpoint.
7. Add the frontend `Read more` interaction.
8. Add story-details caching.
9. Add backend tests and manual browser checks.
10. Add an optional AI summary provider if local summaries are not sufficient.

## 9. Important Constraints

- RSS descriptions may be incomplete, so the full summary should be labelled as a combined summary rather than a replacement for the original reporting.
- The application should link to and credit the original publishers instead of copying full article text.
- External feeds can fail or rate-limit requests; every feed request needs a timeout and graceful fallback.
- API keys should be stored in environment variables if a paid search or AI service is added.
- The frontend should never trust raw HTML from RSS feeds. Return cleaned text from the backend and render it as text.
