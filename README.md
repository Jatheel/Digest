# Digest

> Your daily news by category, from the web and social media, summarized and
> ranked by a model that learns what you care about.

Digest is an Android app (and web app) that shows the newest headlines for
each category: Sri Lanka, Mannar, World, Technology, Business, Sports and
Science. Under every headline you get:

- **a short description**: one paragraph saying what happened;
- **Read more**: a brief summary that combines every outlet that reported the
  story, then a **Read full article** button for the best source, the other
  outlets' links and the social media posts discussing it;
- **👍 Interested / Moderate / 👎 Not for me** buttons that teach the ranking
  model what you like.

## Download the app

Get the latest APK from **[Releases](https://github.com/Jatheel/Digest/releases/latest)**.
Open the download on your Android phone and allow "Install unknown apps" for
your browser when asked.

The same app also runs in any browser at
**https://jatheel.github.io/Digest/**, and you can add it to your home screen.

## How it works

```
GitHub Actions (every 30 min)                       Your phone
┌──────────────────────────────────────┐            ┌─────────────────────────────┐
│ fetch feeds: publishers, Google News,│            │ download news.json          │
│   Reddit, Mastodon, YouTube, Bluesky │  news.json │ rank with logistic          │
│ group reports of the same event      │ ─────────► │   regression (on device)    │
│ write description + combined summary │  (GitHub   │ learn from your 👍 / 👎,     │
│ compute ranking features            │   Pages)   │   opens and "Read more"     │
└──────────────────────────────────────┘            └─────────────────────────────┘
```

- **Sources** (`news_pipeline.py` → `FEEDS`): publisher RSS (Ada Derana, Daily
  Mirror, EconomyNext, BBC, Al Jazeera, The Guardian, NPR, Ars Technica, The
  Verge, CNBC, ESPNcricinfo, ScienceDaily, ...), Google News, and social
  sources: Reddit subreddits, Mastodon hashtags, YouTube news channels and
  news accounts on Bluesky. No API keys needed. X/Twitter, Facebook and
  Instagram are not included because they have no free API.
- **Grouping**: reports with similar headlines (words weighted by rarity, so
  names and places count most) within 48 hours become one story. Social posts
  join the story they match or link to.
- **Summaries** (`summarizer.py`): sentences are taken from the source reports
  and picked for how many outlets agree on them, with duplicates removed.
  Nothing is invented. Optionally, set an `ANTHROPIC_API_KEY` repository
  secret to have Claude write summaries for stories covered by two or more
  outlets, using only facts from those reports.
- **Ranking** (`static/model.js`, mirrored in `ranking.py`): a logistic
  regression over freshness, number of outlets, social buzz, source trust,
  local relevance, topic, source and headline keywords. It starts from
  sensible default weights (`static/model_defaults.json`) and retrains on your
  reactions on the phone. Your reactions never leave the device. Switch
  between **For you** (ranked) and **Latest** (by time) at the top.

## Run it locally

```powershell
cd C:\path\to\news-app
python -m venv venv
venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Or just double-click `run.bat`. Then open http://localhost:8000. The first
start takes a minute or two while it collects the news; after that the digest
is rebuilt in the background every 20 minutes.

To build the static site that GitHub Pages serves:

```powershell
python build_feed.py --out site
```

## Tests

```powershell
python -m unittest test_news_ranking test_pipeline   # pipeline, summaries, model
npm test                                              # on-device model (Node 22+)
```

`test_news_ranking` also checks that the phone's JavaScript model and the
Python model give identical scores.

## Publishing (one-time setup)

1. **Turn on GitHub Pages**: repository *Settings → Pages → Source: GitHub
   Actions*. The *Update news* workflow then publishes fresh news every 30
   minutes (and on every push to `main`).
2. **Release the app**: push a tag, for example
   `git tag v1.0.0 && git push origin v1.0.0`. The *Android release* workflow
   builds the APK and attaches it to a GitHub Release.

### Signing the Android app (recommended)

Without a signing key the workflow builds a debug-signed APK, and each new
version has to be uninstalled before the next one installs. To sign releases
with a fixed key, create one once (needs a JDK):

```powershell
keytool -genkeypair -v -keystore digest-release.jks -alias digest -keyalg RSA -keysize 2048 -validity 10000
[Convert]::ToBase64String([IO.File]::ReadAllBytes("digest-release.jks")) | Set-Clipboard
```

Then add these repository secrets (*Settings → Secrets and variables →
Actions*): `ANDROID_KEYSTORE_B64` (the clipboard contents), `KEYSTORE_PASSWORD`,
`KEY_ALIAS` (`digest`) and `KEY_PASSWORD`. Keep the `.jks` file safe and out
of git.

### Optional: AI summaries

Add an `ANTHROPIC_API_KEY` repository secret. Each run summarizes up to 20 new
multi-source stories (set `DIGEST_LLM_MAX_PER_RUN` to change that). Summaries
are cached, so a story is summarized once. The model defaults to
`claude-opus-5-5`; set `DIGEST_SUMMARY_MODEL` in the workflow to use a
cheaper one.

## Customize

- Add or change sources and categories in `FEEDS` in `news_pipeline.py`, and
  give a new category a colour in `ACCENTS` in `static/app.js`.
- Tune default ranking weights (for example, how much you like each topic) in
  `static/model_defaults.json`.
- Change where the app downloads news from in `static/config.js`.

## Notes

- Reddit rate-limits automated readers heavily, so Reddit posts appear only
  when it lets requests through. Every other source works without that limit.
- Digest links to and credits the original publishers; read the full article
  at the source.

## License

This project is shared for personal and educational use.
