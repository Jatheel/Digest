"""
Build the static site that GitHub Pages serves and the phone app reads:

    python build_feed.py --out site

Writes site/news.json (the digest), site/summary_cache.json (Claude summaries,
if enabled) and a copy of the web app from static/.
"""

import argparse
import json
import shutil
import sys
from collections import Counter
from pathlib import Path
from urllib.error import URLError

import news_pipeline

ROOT = Path(__file__).parent


def load_summary_cache(out_dir: Path, url: str) -> dict:
    local = out_dir / "summary_cache.json"
    try:
        if local.exists():
            return json.loads(local.read_text(encoding="utf-8"))
        if url:
            return json.loads(news_pipeline.http_get(url, 15).decode("utf-8"))
    except (OSError, URLError, ValueError) as exc:
        print(f"[digest] no previous summary cache ({exc}); starting fresh")
    return {}


def print_stats(digest: dict) -> None:
    print(f"Built in {digest['build_seconds']}s")
    kinds = Counter()
    for feed in digest["feeds"]:
        kinds[feed["kind"]] += feed["items"]
    print("Items fetched by kind:", dict(kinds))
    failed = [f["url"] for f in digest["feeds"] if f["items"] == 0]
    if failed:
        print(f"Feeds with no items ({len(failed)}):")
        for url in failed:
            print("   ", url)
    for topic, stories in digest["stories"].items():
        multi = sum(1 for s in stories if s["features"]["source_count"] > 1)
        social = sum(1 for s in stories if s["social"])
        described = sum(1 for s in stories if s["description"])
        print(f"{topic:12s} {len(stories):3d} stories | {multi:3d} multi-source | {social:3d} with social | {described:3d} with description")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="site", help="output directory")
    parser.add_argument("--summary-cache-url", default="", help="URL of the previously published summary_cache.json")
    parser.add_argument("--topics", nargs="*", help="only build these topics (for quick local checks)")
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    cache = load_summary_cache(out_dir, args.summary_cache_url)

    digest = news_pipeline.build_digest(args.topics, summary_cache=cache)

    for item in (ROOT / "static").iterdir():
        target = out_dir / item.name
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True)
        else:
            shutil.copy2(item, target)
    (out_dir / ".nojekyll").write_text("", encoding="utf-8")
    (out_dir / "news.json").write_text(json.dumps(digest, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    (out_dir / "summary_cache.json").write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")

    print_stats(digest)
    total = sum(len(s) for s in digest["stories"].values())
    if total == 0:
        print("No stories were built; refusing to publish an empty digest.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
