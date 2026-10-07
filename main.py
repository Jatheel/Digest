"""
Personal News Digest — local server
-----------------------------------
Serves the web app from static/ plus the same news.json digest that GitHub
Actions publishes for the phone app, rebuilt in the background every 20
minutes. Handy for development and for using Digest on the home network.

Run it with:
    uvicorn main:app --host 0.0.0.0 --port 8000 --reload
"""

import asyncio
import json
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

import news_pipeline

CACHE_TTL_SECONDS = 20 * 60  # rebuild the digest at most every 20 minutes
DIGEST_CACHE_PATH = Path(__file__).with_name(".digest_cache.json")
SUMMARY_CACHE_PATH = Path(__file__).with_name(".summary_cache.json")

_digest: dict | None = None
_build_lock = asyncio.Lock()


def _load_disk_cache() -> dict | None:
    """Reuse the last digest across restarts so the first page load is instant."""
    try:
        return json.loads(DIGEST_CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _build_sync() -> dict:
    try:
        summary_cache = json.loads(SUMMARY_CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        summary_cache = {}
    digest = news_pipeline.build_digest(summary_cache=summary_cache)
    DIGEST_CACHE_PATH.write_text(json.dumps(digest, ensure_ascii=False), encoding="utf-8")
    SUMMARY_CACHE_PATH.write_text(json.dumps(summary_cache, ensure_ascii=False), encoding="utf-8")
    return digest


def _is_stale(digest: dict | None) -> bool:
    return digest is None or time.time() - digest.get("generated_at", 0) > CACHE_TTL_SECONDS


async def get_digest(force: bool = False) -> dict:
    global _digest
    if _digest is None:
        _digest = _load_disk_cache()
    if not force and not _is_stale(_digest):
        return _digest
    async with _build_lock:
        # Another request may have rebuilt it while we waited for the lock.
        if force or _is_stale(_digest):
            _digest = await asyncio.to_thread(_build_sync)
    return _digest


async def _refresh_forever() -> None:
    while True:
        try:
            await get_digest()
        except Exception as exc:  # noqa: BLE001 - keep serving the last good digest
            print(f"[digest] background rebuild failed: {exc}")
        await asyncio.sleep(60)


@asynccontextmanager
async def lifespan(_: FastAPI):
    task = asyncio.create_task(_refresh_forever())
    yield
    task.cancel()


app = FastAPI(title="Personal News Digest", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.get("/news.json")
async def news_json(refresh: bool = Query(False, description="Force a rebuild, skipping the cache")):
    digest = await get_digest(force=refresh)
    return JSONResponse(digest, headers={"Cache-Control": "no-store"})


@app.get("/api/topics")
async def list_topics():
    return {"topics": list(news_pipeline.FEEDS.keys())}


@app.get("/api/news")
async def get_news(
    topic: str = Query(..., description="One of /api/topics"),
    refresh: bool = Query(False, description="Force a refetch, skipping the cache"),
):
    if topic not in news_pipeline.FEEDS:
        return {"error": f"Unknown topic '{topic}'. See /api/topics.", "articles": []}
    digest = await get_digest(force=refresh)
    return {"topic": topic, "articles": digest["stories"].get(topic, []), "cached_at": digest["generated_at"]}


@app.get("/api/news/{story_id}/details")
async def get_story_details(story_id: str):
    """Look a story up in the cached digest. Never touches the network."""
    for stories in (_digest or {}).get("stories", {}).values():
        for story in stories:
            if story.get("id") == story_id:
                return {
                    "id": story["id"],
                    "description": story.get("description", ""),
                    "full_summary": story.get("full_summary") or story.get("description", ""),
                    "best_link": story.get("best_link", ""),
                    "best_source": story.get("best_source", ""),
                    "sources": story.get("sources", []),
                    "social": story.get("social", []),
                }
    raise HTTPException(status_code=404, detail="Story is no longer in the digest")


@app.post("/api/v1/ingest/ide/event")
async def ignore_ide_events():
    return {"status": "ignored"}


@app.post("/api/v1/ingest/browser/event")
async def ignore_browser_events():
    return {"status": "ignored"}


# Serve the frontend (static/index.html, app.js, ...) at "/".
# This MUST be mounted last — routes above take priority over the catch-all.
app.mount("/", StaticFiles(directory=Path(__file__).with_name("static"), html=True), name="static")
