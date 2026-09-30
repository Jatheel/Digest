# Digest — your own news app

A tiny app that pulls headlines from free RSS feeds, groups them into topics
(Sri Lanka, World, Technology, Business, Sports, Science), and shows them in
a phone-friendly page you can install like a real app.

**How it works, in one paragraph:** a small Python server (`main.py`) fetches
a handful of RSS feeds every ~20 minutes, cleans up the HTML in their
summaries, and caches the result. It exposes that as a JSON API
(`/api/news?topic=...`) and also serves the frontend (`static/index.html`) —
a single page that calls that API and renders the cards you see. Because
frontend and backend are served from the same app, there's no CORS to fight
with.

## 1. Run it

Create and activate a project virtual environment first. This avoids the
`ModuleNotFoundError: No module named 'feedparser'` problem that happens
when running the app with the system Python instead of the project's venv.

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

Open **http://localhost:8000** in your laptop's browser first — you should
see topic tabs and headlines. If a topic looks empty, check the terminal:
failed feeds are logged there but don't crash the app.

> If you see `ModuleNotFoundError: No module named 'feedparser'`, make sure
> the venv is active before running `uvicorn`.

## 2. Open it on your phone (same WiFi)

1. Find your laptop's local IP: `ifconfig | grep inet` (Mac/Linux) or
   `ipconfig` (Windows) — look for something like `192.168.1.23`.
2. Make sure your phone is on the **same WiFi network**.
3. On your phone's browser, go to `http://192.168.1.23:8000` (use your own IP).
4. **Add to Home Screen**:
   - Android (Chrome): menu (⋮) → "Add to Home screen".
   - iPhone (Safari): Share icon → "Add to Home Screen".

This only works while your laptop is on and running the server.

## 3. Make it work from anywhere (optional, still free)

If you want the app on your phone without your laptop needing to be on,
deploy it to a free host:

1. Push this folder to a GitHub repo.
2. Sign up at [render.com](https://render.com) (free tier).
3. New → Web Service → connect your repo.
4. Build command: `pip install -r requirements.txt`
   Start command: `uvicorn main:app --host 0.0.0.0 --port $PORT`
5. Once deployed, open the `https://your-app.onrender.com` URL on your phone
   and "Add to Home Screen" the same way as above.

(Render's free tier sleeps after inactivity, so the first load after a while
can take ~30 seconds — fine for a personal weekend project.)

## 4. Customize your topics

Open `main.py` and edit the `TOPICS` dictionary near the top — it's just a
label mapped to a list of RSS URLs:

```python
TOPICS = {
    "Sri Lanka": ["https://news.google.com/rss?hl=en-LK&gl=LK&ceid=LK:en"],
    "World": ["http://feeds.bbci.co.uk/news/world/rss.xml", ...],
    ...
}
```

To add a topic, add a new key. To find more feeds:
- **Google News** covers almost anything without an API key:
  `https://news.google.com/rss/search?q=YOUR+TOPIC&hl=en-US&gl=US&ceid=US:en`
  (swap `gl`/`hl`/`ceid` for another country/language, e.g. `LK`/`en-LK`).
- Most news sites still publish RSS at a predictable URL, usually
  `sitename.com/feed` or `sitename.com/rss.xml`.

## Why no Reddit or X/Twitter?

Both used to have free, no-login ways to pull public posts. As of 2026,
Reddit shut down its unauthenticated `.json` endpoints and X's API is
paid-only, so pulling from them for free now needs a registered developer
app (and, for X, a paid tier). RSS-based news sources stay genuinely free
and don't need any of that, which is why this app sticks to them — it keeps
the whole thing a true weekend build. If you want Reddit later, the free
route is registering an OAuth app at reddit.com/prefs/apps and using PRAW.

## Notes / limits (intentional, for a personal weekend project)

- Single in-memory cache, refreshed every 20 minutes per topic — restarting
  the server clears it, which is fine for personal use.
- No login/accounts — it's just for you.
- No AI summarization — it reuses each RSS feed's own description, cleaned
  up. That keeps it 100% free with no API keys. If you later want sharper
  AI-written summaries, that's a clean next step: call an LLM API from
  `_fetch_topic_sync` before caching.
