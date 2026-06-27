#!/usr/bin/env python3
"""
Threads Auto-Post Agent - Database Schema & Operations

SQLite database schema for storing scheduled posts.
Auto-created on first run.

Table: posts
------------
id            INTEGER PRIMARY KEY AUTOINCREMENT
content       TEXT    NOT NULL       # Post text (slides separated by ===)
media_url     TEXT                    # Optional image/video URL (root slide only)
scheduled_at  TEXT    NOT NULL       # ISO timestamp (UTC)
published_at  TEXT                    # ISO timestamp when actually posted
status        TEXT    DEFAULT 'pending'  # pending | published | failed | cancelled
thread_id     TEXT                    # Threads post ID after successful publish
root_post_id  TEXT                    # First post ID in thread (for carousel)
error_msg     TEXT                    # Last error message if failed
retry_count   INTEGER DEFAULT 0       # Number of retry attempts
created_at    TEXT    NOT NULL
updated_at    TEXT    NOT NULL

Indexes:
- idx_status_scheduled: WHERE status='pending' ORDER BY scheduled_at
- idx_retry: WHERE status='failed' AND retry_count < 3
"""

import sqlite3
import time
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ─── Schema ───────────────────────────────────────────────────────────────────

SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    content      TEXT    NOT NULL,
    media_url    TEXT,
    scheduled_at TEXT    NOT NULL,
    published_at TEXT,
    status       TEXT    NOT NULL DEFAULT 'pending',
    thread_id    TEXT,
    root_post_id TEXT,
    error_msg    TEXT,
    retry_count  INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT    NOT NULL,
    updated_at   TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_status_scheduled
    ON posts(status, scheduled_at);

CREATE INDEX IF NOT EXISTS idx_retry
    ON posts(status, retry_count);
"""


# ─── Database Connection ─────────────────────────────────────────────────────

class Database:
    def __init__(self, db_path: str = None):
        if db_path is None:
            db_path = os.getenv("DB_PATH", "/home/ubuntu/threads-agent/posts.db")
        self.db_path = Path(db_path)
        self._init_db()

    def _init_db(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(SCHEMA)
            conn.commit()

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    # ─── Insert ─────────────────────────────────────────────────────────────

    def add_post(self, content: str, scheduled_at: str,
                 media_url: str = None) -> int:
        """Insert a new scheduled post. Returns post ID."""
        now = self._now()
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute("""
                INSERT INTO posts
                    (content, media_url, scheduled_at, status, created_at, updated_at)
                VALUES (?, ?, ?, 'pending', ?, ?)
            """, [content, media_url, scheduled_at, now, now])
            conn.commit()
            return cur.lastrowid

    # ─── Query ──────────────────────────────────────────────────────────────

    def get_pending(self, limit: int = 10) -> list[dict]:
        """Get all pending posts where scheduled_at <= now. Ordered oldest first."""
        now = self._now()
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT * FROM posts
                WHERE status = 'pending'
                  AND scheduled_at <= ?
                ORDER BY scheduled_at ASC
                LIMIT ?
            """, [now, limit]).fetchall()
            return [dict(r) for r in rows]

    def get_by_id(self, post_id: int) -> Optional[dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM posts WHERE id = ?", [post_id]).fetchone()
            return dict(row) if row else None

    def get_recent(self, limit: int = 20) -> list[dict]:
        """Get most recent posts."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT * FROM posts
                ORDER BY created_at DESC
                LIMIT ?
            """, [limit]).fetchall()
            return [dict(r) for r in rows]

    # ─── Update ─────────────────────────────────────────────────────────────

    def mark_published(self, post_id: int,
                       thread_id: str = None,
                       root_post_id: str = None) -> None:
        now = self._now()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                UPDATE posts
                SET status = 'published',
                    published_at = ?,
                    thread_id = ?,
                    root_post_id = ?,
                    error_msg = NULL,
                    updated_at = ?
                WHERE id = ?
            """, [now, thread_id, root_post_id, now, post_id])
            conn.commit()

    def mark_failed(self, post_id: int, error: str) -> None:
        now = self._now()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                UPDATE posts
                SET status = CASE WHEN retry_count >= 2 THEN 'failed' ELSE 'pending' END,
                    error_msg = ?,
                    retry_count = retry_count + 1,
                    updated_at = ?
                WHERE id = ?
            """, [error, now, post_id])
            conn.commit()

    def mark_cancelled(self, post_id: int) -> None:
        now = self._now()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                UPDATE posts
                SET status = 'cancelled', updated_at = ?
                WHERE id = ?
            """, [now, post_id])
            conn.commit()

    # ─── Stats ──────────────────────────────────────────────────────────────

    def stats(self) -> dict:
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute("""
                SELECT
                    status,
                    COUNT(*) as count
                FROM posts
                GROUP BY status
            """)
            rows = dict(cur.fetchall())
            total = sum(rows.values())
            return {
                "total": total,
                "pending": rows.get("pending", 0),
                "published": rows.get("published", 0),
                "failed": rows.get("failed", 0),
                "cancelled": rows.get("cancelled", 0),
            }


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main():
    import argparse

    parser = argparse.ArgumentParser(description="Threads Agent Database")
    sub = parser.add_subparsers(dest="command")

    add_p = sub.add_parser("add", help="Add a scheduled post")
    add_p.add_argument("--content", "-c", required=True, help="Post content")
    add_p.add_argument("--at", required=True, help="ISO scheduled time, e.g. 2025-07-14T09:00:00Z")
    add_p.add_argument("--media", help="Optional media URL")

    list_p = sub.add_parser("list", help="List posts")
    list_p.add_argument("--status", default=None)
    list_p.add_argument("--limit", type=int, default=20)

    stats_p = sub.add_parser("stats", help="Show statistics")

    cancel_p = sub.add_parser("cancel", help="Cancel a post")
    cancel_p.add_argument("post_id", type=int)

    args = parser.parse_args()

    db = Database()

    if args.command == "add":
        pid = db.add_post(args.content, args.at, args.media)
        print(f"✅ Post #{pid} scheduled for {args.at}")

    elif args.command == "list":
        posts = db.get_recent(limit=args.limit)
        if args.status:
            posts = [p for p in posts if p["status"] == args.status]
        print(f"\n{'ID':>4}  {'Status':<12} {'Scheduled':<25}  {'Content Preview'}")
        print("-" * 80)
        for p in posts:
            preview = p["content"][:50].replace("\n", " ")
            print(f"{p['id']:>4}  {p['status']:<12} {p['scheduled_at']:<25}  {preview}")

    elif args.command == "stats":
        s = db.stats()
        print(f"Total posts: {s['total']}")
        print(f"  Pending:   {s['pending']}")
        print(f"  Published: {s['published']}")
        print(f"  Failed:    {s['failed']}")
        print(f"  Cancelled: {s['cancelled']}")

    elif args.command == "cancel":
        db.mark_cancelled(args.post_id)
        print(f"✅ Post #{args.post_id} cancelled")


if __name__ == "__main__":
    main()
