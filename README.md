# Digest

> Your personal news digest, tuned for Sri Lanka and ranked by relevance.

Digest is a lightweight news app that pulls free RSS feeds, ranks the stories,
filters out old or repeated items, and presents a clean mobile-friendly reading
experience. It is designed for personal use, with features like:

- topic-based browsing (Sri Lanka, World, Business, Sports, Science, etc.)
- prioritization of local and trusted sources
- spam/duplicate suppression
- stale article removal (older than 3 months)
- per-article feedback buttons: Interested, Moderate, and Not interested
- a simple installable web app shell that works well on mobile

## Features

- Sri Lanka-first ranking: local sources are promoted when relevant
- Story deduplication: duplicate titles and repeated sources are filtered
- Freshness control: old headlines are removed automatically
- User feedback loop: stories marked as not relevant are hidden from future views
- Local-first profile settings: preferences are stored in a local JSON profile
- Clean PWA-like frontend: served from the same FastAPI app as the backend

## Tech stack

- Python 3
- FastAPI
- Uvicorn
- feedparser
- HTML + CSS + vanilla JavaScript

## Run locally

Create and activate a virtual environment before starting the app. This avoids
common missing-dependency issues, especially with `feedparser`.

### Windows (PowerShell)

```powershell
cd C:\path\to\news-app
python -m venv venv
venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

### macOS / Linux

```bash
cd /path/to/news-app
python3 -m venv venv
source venv/bin/activate
python -m pip install -r requirements.txt
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Then open:

- http://localhost:8000

> If you see `ModuleNotFoundError: No module named 'feedparser'`, make sure the
> project virtual environment is active before running the app.

## Open it on your phone

1. Find your laptop's local IP address (`ipconfig` on Windows or `ifconfig` on Mac/Linux).
2. Make sure your phone is on the same Wi-Fi network.
3. Open `http://<your-laptop-ip>:8000` on the phone.
4. Use "Add to Home Screen" in the browser to install it like an app.

## Customize and extend

The app is configured from the `TOPICS` dictionary in `main.py`. You can add or change RSS feeds there.

```python
TOPICS = {
    "Sri Lanka": ["https://news.google.com/rss?hl=en-LK&gl=LK&ceid=LK:en"],
    "World": ["https://feeds.bbci.co.uk/news/world/rss.xml"],
}
```

You can also update the trust sources and the ranking logic in the backend if you want stronger personalization.

## Notes

- The news data is refreshed on a cache cycle and should feel fast for personal use.
- The app intentionally avoids paid APIs and prefers free RSS sources.
- It is best suited for personal, local experimentation rather than large-scale publishing.

## License

This project is shared for personal and educational use.
