#!/usr/bin/env python3
"""
Threads Agent — Main scheduler engine.

Checks DB every POLL_INTERVAL_MINUTES for pending posts,
executes them via Poster, updates DB, sends Telegram alerts.

Usage:
    python3 agent.py run          # Daemon mode (continuous)
    python3 agent.py check         # Single poll cycle
    python3 agent.py enqueue       # Add a test post
    python3 agent.py stats         # Show DB stats
    python3 agent.py verify        # Test API connection
"""

import os
import sys
import time
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

# Setup path
AGENT_DIR = Path(__file__).parent
sys.path.insert(0, str(AGENT_DIR))

from database import Database
from poster import Poster
from notifier import send_success, send_failure, send_status

# ─── Logging ─────────────────────────────────────────────────────────────────

LOG_DIR = Path(os.getenv("LOG_DIR", str(AGENT_DIR / "logs")))
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "agent.log"),
        logging.StreamHandler(sys.stderr),
    ],
)
log = logging.getLogger("threads-agent")


# ─── Config ───────────────────────────────────────────────────────────────────

def _load_env():
    """Load .env file if it exists."""
    env_file = AGENT_DIR / ".env"
    if env_file.exists():
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())

_load_env()

DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL_MINUTES", "5")) * 60
MAX_RETRIES = 3


# ─── Agent ───────────────────────────────────────────────────────────────────

class ThreadsAgent:
    """
    Main agent that:
    1. Polls DB for due posts
    2. Posts via Threads API
    3. Updates DB with results
    4. Sends Telegram notifications
    """

    def __init__(self):
        self.db = Database()
        self._poster = None

    @property
    def poster(self) -> Poster:
        if self._poster is None:
            self._poster = Poster()
        return self._poster

    def verify(self) -> bool:
        """Verify API credentials."""
        return self.poster.verify_connection()

    def check(self) -> dict:
        """
        Single poll cycle. Returns summary dict.
        """
        results = {"processed": 0, "succeeded": 0, "failed": 0, "skipped": 0}

        pending = self.db.get_pending(limit=10)
        results["pending_found"] = len(pending)

        if not pending:
            log.info("No pending posts due.")
            return results

        log.info(f"Found {len(pending)} pending post(s)")

        for post in pending:
            results["processed"] += 1
            self._process_post(post, results)

        return results

    def _process_post(self, post: dict, results: dict):
        post_id = post["id"]
        content = post["content"]
        media_url = post.get("media_url")

        log.info(f"Processing post #{post_id}")

        if DRY_RUN:
            log.info(f"[DRY RUN] Would post: {content[:80]}...")
            self.db.mark_published(post_id, thread_id="DRY_RUN", root_post_id="DRY_RUN")
            results["succeeded"] += 1
            return

        # Parse slides
        slides = [s.strip() for s in content.split("===") if s.strip()]

        try:
            if len(slides) == 1:
                result = self.poster.post_text(slides[0], media_url=media_url)
            else:
                result = self.poster.post_thread(slides, image_url=media_url)

            if result.success:
                self.db.mark_published(
                    post_id,
                    thread_id=result.root_id,
                    root_post_id=result.root_id,
                )
                results["succeeded"] += 1
                log.info(f"✅ Post #{post_id} published: {result.root_id}")

                # Telegram notification
                preview = slides[0][:100].replace("\n", " ")
                send_success(post_id, preview, result.root_id, result.permalink)
            else:
                raise Exception(result.error or "Unknown error")

        except Exception as e:
            error_msg = str(e)[:500]
            self.db.mark_failed(post_id, error_msg)
            results["failed"] += 1
            log.error(f"❌ Post #{post_id} failed: {error_msg}")

            # Telegram notification
            preview = slides[0][:100].replace("\n", " ") if slides else "(empty)"
            retry_count = post.get("retry_count", 0) + 1
            send_failure(post_id, preview, error_msg, retry_count)

    def enqueue_test(self) -> int:
        """Add a test post scheduled for now."""
        now = datetime.now(timezone.utc).isoformat()
        content = (
            "🧪 Test Post dari Threads Agent\n"
            "===\n"
            "Slide 1: Kalau lu bisa baca ini, berarti posting berhasil!\n"
            "===\n"
            "Slide 2: Cron agent working perfectly ✅"
        )
        pid = self.db.add_post(content, now)
        log.info(f"Test post #{pid} enqueued (scheduled now)")
        return pid

    def stats(self) -> dict:
        return self.db.stats()

    def close(self):
        if self._poster:
            self._poster.close()

    # ─── Daemon mode ─────────────────────────────────────────────────────────

    def run(self):
        """Run continuously, polling every POLL_INTERVAL seconds."""
        log.info(f"🚀 Threads Agent started (poll every {POLL_INTERVAL // 60} min)")
        log.info(f"   Dry run: {DRY_RUN}")

        if not self.verify():
            log.error("❌ API verification failed. Check credentials in .env")
            sys.exit(1)

        while True:
            try:
                results = self.check()
                stats = self.stats()
                log.info(f"Stats: {stats}")
            except Exception as e:
                log.error(f"Poll cycle error: {e}")

            log.info(f"Sleeping {POLL_INTERVAL // 60} min until next poll...")
            time.sleep(POLL_INTERVAL)


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main():
    import argparse

    parser = argparse.ArgumentParser(description="Threads Auto-Post Agent")
    sub = parser.add_subparsers(dest="command", required=True)

    # run - daemon mode
    run_p = sub.add_parser("run", help="Run agent in daemon mode")

    # check - single poll
    check_p = sub.add_parser("check", help="Run single poll cycle")

    # enqueue - add test post
    enqueue_p = sub.add_parser("enqueue", help="Enqueue a test post")
    enqueue_p.add_argument("--content", default=None)
    enqueue_p.add_argument("--at", default=None)

    # stats
    sub.add_parser("stats", help="Show DB statistics")

    # verify
    sub.add_parser("verify", help="Verify API connection")

    args = parser.parse_args()
    agent = ThreadsAgent()

    try:
        if args.command == "run":
            agent.run()

        elif args.command == "check":
            results = agent.check()
            print(json.dumps(results, indent=2))

        elif args.command == "enqueue":
            if args.content:
                now = datetime.now(timezone.utc).isoformat()
                pid = agent.db.add_post(args.content, args.at or now)
            else:
                pid = agent.enqueue_test()
            print(f"✅ Post #{pid} enqueued")

        elif args.command == "stats":
            s = agent.stats()
            print(f"\n📊 Database Stats")
            print(f"   Total:     {s['total']}")
            print(f"   Pending:   {s['pending']}")
            print(f"   Published: {s['published']}")
            print(f"   Failed:    {s['failed']}")
            print(f"   Cancelled: {s['cancelled']}")

        elif args.command == "verify":
            if agent.verify():
                print("✅ API connection OK")
            else:
                print("❌ API connection failed")
                sys.exit(1)

    finally:
        agent.close()


if __name__ == "__main__":
    main()
