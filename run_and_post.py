#!/usr/bin/env python3
"""
Market Monday Pipeline + Auto-Post Wrapper.

Runs the content pipeline, then immediately posts the generated
content to Threads via the agent poster.

Reads from staging.json (has image_url + source URL), not just latest.md.

Usage:
    python3 run_and_post.py          # Full pipeline → post
    python3 run_and_post.py --dry    # Pipeline only (no post)
    python3 run_and_post.py --test   # Post test message only
"""

import os
import sys
import json
import re
import subprocess
import time
from pathlib import Path
from datetime import datetime, timezone

AGENT_DIR = Path(__file__).parent
MM_DIR = Path.home() / "market-monday"
MM_PIPELINE = MM_DIR / "scripts" / "market-monday-pipeline.py"
STAGING_FILE = Path.home() / ".hermes" / "market_monday" / "staging.json"

# Load env
for line in open(AGENT_DIR / ".env"):
    line = line.strip()
    if "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1)
        os.environ[k.strip()] = v.strip()


def run_pipeline(dry=False):
    """Run Market Monday pipeline to generate content."""
    print(f"[{timestamp()}] 🔄 Running content pipeline...")

    if dry:
        print("[DRY RUN] Skipping pipeline")
        return {"slides": ["DRY RUN slide 1", "DRY RUN slide 2"]}

    result = subprocess.run(
        [sys.executable, str(MM_PIPELINE), "--dry-run"],
        capture_output=True, text=True, cwd=str(MM_DIR)
    )

    if result.returncode != 0:
        print(f"[ERROR] Pipeline failed:\n{result.stderr[:500]}")
        return None

    # Read from staging.json (has image_url + source URL)
    if STAGING_FILE.exists():
        data = json.loads(STAGING_FILE.read_text())
        slides_raw = data.get("slides", [])
        image_url = data.get("image_url")
        source_url = data.get("url")
        title = data.get("title", "")

        if slides_raw:
            # Flatten slides: hook + content per slide
            slides = []
            for s in slides_raw:
                hook = s.get("hook", "").strip()
                content = s.get("content", "").strip()
                if hook and content:
                    slides.append(f"{hook}\n\n{content}")
                elif hook:
                    slides.append(hook)
                elif content:
                    slides.append(content)

            # Add source URL to last slide
            if source_url and slides:
                last = slides[-1]
                if source_url not in last:
                    slides[-1] = f"{last}\n\n🔗 {source_url}"

            print(f"[{timestamp()}] ✅ Pipeline generated {len(slides)} slides")
            print(f"[{timestamp()}] 📷 Image: {image_url or 'none'}")
            print(f"[{timestamp()}] 🔗 Source: {source_url or 'none'}")
            return {
                "slides": slides,
                "image_url": image_url,
                "source_url": source_url,
                "title": title,
            }

    # Fallback: try latest.md
    latest_candidates = sorted(
        (MM_DIR / "candidates").glob("*.md"),
        key=lambda p: p.stat().st_mtime, reverse=True)
    if latest_candidates:
        content = latest_candidates[0].read_text()
        slides = parse_slides(content)
        print(f"[{timestamp()}] ✅ Pipeline read from {latest_candidates[0].name} ({len(slides)} slides)")
        return {"slides": slides, "image_url": None, "source_url": None}

    print(f"[ERROR] No content generated")
    return None


def parse_slides(text):
    """Split text into slides by === separator."""
    slides = [s.strip() for s in re.split(r"(?:^|\n)===\s*\n", text) if s.strip()]
    return slides


def post_to_threads(slides, image_url=None):
    """Post slides to Threads via agent poster."""
    sys.path.insert(0, str(AGENT_DIR))
    from poster import Poster

    print(f"[{timestamp()}] 📡 Posting to Threads...")

    poster = Poster()
    result = poster.post_thread(slides, image_url=image_url)
    poster.close()

    if result.success:
        print(f"[{timestamp()}] ✅ Posted {len(result.slides)} slides")
        print(f"[{timestamp()}] 🔗 {result.permalink}")
        return result
    else:
        print(f"[{timestamp()}] ❌ Failed: {result.error}")
        return None


def timestamp():
    return datetime.now(timezone.utc).strftime("%H:%M:%S UTC")


def main():
    dry = "--dry" in sys.argv
    test = "--test" in sys.argv

    if test:
        # Quick test post
        print(f"[{timestamp()}] 🧪 Test post")
        sys.path.insert(0, str(AGENT_DIR))
        from poster import Poster
        poster = Poster()
        r = poster.post_text(f"Test post {datetime.now().strftime('%H:%M')} 🧪")
        poster.close()
        if r.success:
            print(f"✅ Posted: {r.permalink}")
        else:
            print(f"❌ Failed: {r.error}")
        return

    # Full pipeline → post
    print(f"\n{'='*50}")
    print(f"[{timestamp()}] 🚀 MARKET MONDAY PIPELINE + POST")
    print(f"{'='*50}\n")

    result = run_pipeline(dry=dry)
    if not result:
        sys.exit(1)

    slides = result["slides"]
    image_url = result.get("image_url")

    print(f"[{timestamp()}] 📝 Content: {len(slides)} slides")
    for i, s in enumerate(slides, 1):
        preview = s[:80].replace("\n", " ")
        print(f"  Slide {i}: {preview}...")

    if dry:
        print(f"\n[{timestamp()}] DRY RUN - skipping post")
        return

    post_result = post_to_threads(slides, image_url=image_url)
    if post_result:
        print(f"\n[{timestamp()}] 🎉 DONE! Posted to Threads")
        print(f"   {post_result.permalink}")
    else:
        print(f"\n[{timestamp()}] ❌ FAILED to post")
        sys.exit(1)


if __name__ == "__main__":
    main()
