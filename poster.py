#!/usr/bin/env python3
"""
Threads Poster — 2-step posting engine (learned from pressbox-pattern).

Step 1: POST /{user_id}/threads        → create container → returns creation_id
Step 2: POST /{user_id}/threads_publish → publish container → returns post_id

Key lessons from pressbox-pipeline/threads_poster.py:
  1. Container polling: wait for FINISHED status before publishing
  2. Chain posting: each slide replies to PREVIOUS slide (not fan-out)
  3. 3s delay between posts
  4. requests.Session for persistent connections
"""

import os
import re
import sys
import time
import json
import requests
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# Load .env
def _load_env():
    env_file = Path(__file__).parent / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

_load_env()

GRAPH_API_BASE = "https://graph.threads.net/v1.0"
MAX_POST_LENGTH = 500
CONTAINER_POLL_INTERVAL = 2
CONTAINER_POLL_MAX = 10
INTER_POST_DELAY = 3


# ─── Exceptions ───────────────────────────────────────────────────────────────

class PosterError(Exception):
    pass


# ─── Result ───────────────────────────────────────────────────────────────────

@dataclass
class ThreadPostResult:
    text: str
    post_id: str
    image_url: Optional[str] = None


@dataclass
class ThreadResult:
    root_id: str = ""
    permalink: str = ""
    slides: list = None
    success: bool = True
    error: str = ""

    def __post_init__(self):
        if self.slides is None:
            self.slides = []

    @property
    def post_ids(self) -> list[str]:
        return [s.post_id for s in self.slides if s.post_id]


# ─── Poster ───────────────────────────────────────────────────────────────────

class Poster:
    """
    Thread-safe Threads API poster using pressbox pattern.
    """

    def __init__(self, access_token: str = None, user_id: str = None):
        self.access_token = access_token or os.getenv("THREADS_ACCESS_TOKEN")
        self.user_id = user_id or os.getenv("THREADS_USER_ID")
        self.session = requests.Session()

    def close(self):
        self.session.close()

    # ─── Low-level API ────────────────────────────────────────────────────────

    def _create_container(self, text: str,
                          reply_to_id: str = None,
                          image_url: str = None) -> str:
        """Step 1: create container. Returns creation_id."""
        url = f"{GRAPH_API_BASE}/{self.user_id}/threads"
        params = {
            "text": text,
            "access_token": self.access_token,
        }
        if image_url:
            params["media_type"] = "IMAGE"
            params["image_url"] = image_url
        else:
            params["media_type"] = "TEXT"

        if reply_to_id:
            params["reply_to_id"] = reply_to_id

        resp = self.session.post(url, data=params, timeout=30)
        data = self._parse(resp)
        creation_id = data.get("id")
        if not creation_id:
            raise PosterError(f"No creation_id returned: {data}")
        return creation_id

    def _get_container_status(self, creation_id: str) -> str:
        """Check container processing status."""
        url = f"{GRAPH_API_BASE}/{creation_id}"
        params = {
            "fields": "status,error_message",
            "access_token": self.access_token,
        }
        resp = self.session.get(url, params=params, timeout=30)
        data = self._parse(resp)
        return data.get("status", "UNKNOWN")

    def _wait_for_container_ready(self, creation_id: str, has_media: bool) -> None:
        """Poll until container is FINISHED. Text-only = 1s sleep. Image = poll."""
        if not has_media:
            time.sleep(1)
            return
        for attempt in range(CONTAINER_POLL_MAX):
            status = self._get_container_status(creation_id)
            if status == "FINISHED":
                return
            if status == "ERROR":
                raise PosterError(f"Container {creation_id} failed processing")
            print(f"[Poster] Container {creation_id} status={status}, waiting ({attempt+1}/{CONTAINER_POLL_MAX})", file=sys.stderr)
            time.sleep(CONTAINER_POLL_INTERVAL)
        raise PosterError(f"Container {creation_id} did not finish in time")

    def _publish_container(self, creation_id: str) -> str:
        """Step 2: publish container. Returns post_id."""
        url = f"{GRAPH_API_BASE}/{self.user_id}/threads_publish"
        params = {
            "creation_id": creation_id,
            "access_token": self.access_token,
        }
        resp = self.session.post(url, data=params, timeout=30)
        data = self._parse(resp)
        post_id = data.get("id")
        if not post_id:
            raise PosterError(f"No post_id returned: {data}")
        return post_id

    @staticmethod
    def _parse(resp: requests.Response) -> dict:
        try:
            data = resp.json()
        except ValueError:
            raise PosterError(f"Non-JSON response: {resp.text}")
        if resp.status_code >= 400 or "error" in data:
            err = data.get("error", {})
            msg = err.get("message", str(data))
            raise PosterError(f"HTTP {resp.status_code}: {msg}")
        return data

    # ─── Public API ───────────────────────────────────────────────────────────

    def post_single(self, text: str, reply_to_id: str = None,
                    image_url: str = None) -> str:
        """Create + publish single post. Returns post_id."""
        creation_id = self._create_container(text, reply_to_id=reply_to_id,
                                             image_url=image_url)
        self._wait_for_container_ready(creation_id, has_media=bool(image_url))
        return self._publish_container(creation_id)

    def post_thread(self, slides: list[str],
                    image_url: str = None) -> ThreadResult:
        """
        Post slides as a chain thread.

        Slide 1 → root. Slide 2 → reply to slide 1. Slide 3 → reply to slide 2. etc.
        This produces correct display order: S1, S2, S3, S4, S5, S6.
        """
        filtered = [s.strip() for s in slides if s.strip()]
        if not filtered:
            return ThreadResult(success=False, error="No slides provided")

        result = ThreadResult()
        reply_to_id = None

        for i, text in enumerate(filtered):
            # Char cap safety
            if len(text) > MAX_POST_LENGTH:
                trimmed = text[:MAX_POST_LENGTH]
                last_break = max(trimmed.rfind(". "), trimmed.rfind("! "), trimmed.rfind("? "))
                if last_break > 50:
                    text = trimmed[:last_break + 1]
                else:
                    text = trimmed[:-1].rstrip() + "…"
                print(f"[Poster] ✂️ Slide {i+1} trimmed to {len(text)} chars", file=sys.stderr)

            img = image_url if i == 0 else None
            role = "root" if i == 0 else f"reply→{reply_to_id}"
            print(f"[Poster] Slide {i+1}/{len(filtered)}: {role}", file=sys.stderr)

            try:
                post_id = self.post_single(text, reply_to_id=reply_to_id, image_url=img)
                result.slides.append(ThreadPostResult(text=text, post_id=post_id, image_url=img))
                reply_to_id = post_id  # chain: next slide replies to THIS post

                if i == 0:
                    result.root_id = post_id
                    time.sleep(1)
                    result.permalink = self._get_permalink(post_id)

                # Inter-post delay (pressbox pattern: 3s)
                if i < len(filtered) - 1:
                    time.sleep(INTER_POST_DELAY)

            except PosterError as e:
                print(f"[Poster] ⚠️ Slide {i+1} failed: {e}", file=sys.stderr)
                # Retry once
                time.sleep(8)
                try:
                    post_id = self.post_single(text, reply_to_id=reply_to_id, image_url=img)
                    result.slides.append(ThreadPostResult(text=text, post_id=post_id))
                    reply_to_id = post_id
                    if i == 0:
                        result.root_id = post_id
                except Exception as retry_err:
                    print(f"[Poster] ❌ Retry also failed: {retry_err}", file=sys.stderr)
                    result.slides.append(ThreadPostResult(text=text, post_id=""))
                    if result.root_id == "":
                        result.success = False
                        result.error = "Root post failed"
                        return result

        return result

    def post_text(self, text: str, image_url: str = None) -> ThreadResult:
        """Post a single text update."""
        text = text[:MAX_POST_LENGTH] if len(text) > MAX_POST_LENGTH else text
        try:
            post_id = self.post_single(text, image_url=image_url)
            permalink = self._get_permalink(post_id)
            return ThreadResult(
                root_id=post_id, permalink=permalink,
                slides=[ThreadPostResult(text=text, post_id=post_id)])
        except PosterError as e:
            return ThreadResult(success=False, error=str(e))

    def _get_permalink(self, post_id: str = None) -> str:
        try:
            params = {
                "fields": "id,permalink,text",
                "limit": "5",
                "access_token": self.access_token,
            }
            resp = self.session.get(
                f"{GRAPH_API_BASE}/{self.user_id}/threads",
                params=params, timeout=15)
            data = self._parse(resp)
            posts = data.get("data", [])
            if post_id:
                for p in posts:
                    if p["id"] == post_id:
                        return p.get("permalink", "")
            return posts[0].get("permalink", "") if posts else ""
        except Exception:
            return ""

    def get_user_info(self) -> dict:
        params = {"fields": "id,username", "access_token": self.access_token}
        resp = self.session.get(f"{GRAPH_API_BASE}/me", params=params, timeout=15)
        return self._parse(resp)

    def verify_connection(self) -> bool:
        try:
            info = self.get_user_info()
            print(f"✅ Connected as: @{info.get('username')} ({info.get('id')})")
            return True
        except Exception as e:
            print(f"❌ Connection failed: {e}", file=sys.stderr)
            return False


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main():
    import argparse

    parser = argparse.ArgumentParser(description="Threads Poster")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("test", help="Test API connection")

    post_p = sub.add_parser("post", help="Post text or thread")
    post_p.add_argument("text", nargs="+", help="Text to post")
    post_p.add_argument("--image", help="Image URL (root slide)")
    post_p.add_argument("--thread", action="store_true",
                        help="Treat === as slide separator")

    args = parser.parse_args()
    poster = Poster()

    if args.command == "test":
        poster.verify_connection()

    elif args.command == "post":
        text = " ".join(args.text)
        if args.thread:
            slides = [s.strip() for s in re.split(r"(?:^|\n)===\s*\n", text)]
        else:
            slides = [text]
        result = poster.post_thread(slides, image_url=args.image)
        if result.success:
            print(f"✅ Thread posted!")
            print(f"   Root: {result.root_id}")
            print(f"   URL: {result.permalink}")
        else:
            print(f"❌ Failed: {result.error}")
            sys.exit(1)

    poster.close()


if __name__ == "__main__":
    main()
