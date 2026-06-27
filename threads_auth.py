#!/usr/bin/env python3
"""
Threads Auth — Unified OAuth 2.0 + Token Manager + API Client.

Same pattern as Buffer: user authorizes once, token auto-refreshes,
pipelines just call post() and it works.

Usage:
    # OAuth login (one-time)
    from threads_auth import ThreadsAuth
    auth = ThreadsAuth(account="ryanhadiii")
    auth.login()  # Opens browser, catches redirect, stores token

    # Auto-refresh + post (from pipeline)
    auth = ThreadsAuth(account="ryanhadiii")
    auth.post_text("Hello world!")
    auth.post_thread(slides, image_url="...")

Token storage: ~/.hermes/threads_accounts.json
    {
      "ryanhadiii": {
        "access_token": "...",
        "user_id": "178414...",
        "username": "ryanhadiii",
        "expires_at": 1721234567,
        "refreshed_at": 1718640000
      }
    }
"""

import json
import os
import re
import sys
import time
import http.server
import threading
from pathlib import Path
from urllib.parse import urlencode, urlparse, parse_qs
from dataclasses import dataclass, field
from typing import Optional

import httpx

# ─── Constants ───────────────────────────────────────────────────────────────

THREADS_API = "https://graph.threads.net/v1.0"
THREADS_OAUTH = "https://threads.net/oauth/authorize"
ACCOUNTS_FILE = Path.home() / ".hermes" / "threads_accounts.json"
LEGACY_TOKEN_FILE = Path.home() / ".hermes" / "threads_token.json"
LEGACY_MM_TOKEN_FILE = Path.home() / ".hermes" / "market_monday" / "threads_token.json"
APP_CONFIG_FILE = Path.home() / ".hermes" / "threads_app.json"

DEFAULT_SCOPES = [
    "threads_basic",
    "threads_content_publish",
    "threads_manage_replies",
    "threads_read_replies",
    "threads_manage_insights",
]

REFRESH_THRESHOLD_DAYS = 7
POST_DELAY_SECONDS = 5
MAX_POST_LENGTH = 500

# ─── Exceptions ──────────────────────────────────────────────────────────────

class ThreadsAuthError(Exception):
    """Base error for Threads auth operations."""
    pass

class TokenExpiredError(ThreadsAuthError):
    """Token has expired and cannot be refreshed."""
    pass

class OAuthTimeoutError(ThreadsAuthError):
    """OAuth callback server timed out."""
    pass


# ─── Token Manager ───────────────────────────────────────────────────────────

class TokenManager:
    """Load, save, refresh, and check Threads tokens. Multi-account."""

    def __init__(self, accounts_file: Path = ACCOUNTS_FILE):
        self.accounts_file = accounts_file
        self._accounts: dict = {}
        self._load_accounts()

    def _load_accounts(self):
        """Load accounts file, migrating from legacy locations if needed."""
        if self.accounts_file.exists():
            try:
                self._accounts = json.loads(self.accounts_file.read_text())
            except (json.JSONDecodeError, OSError):
                self._accounts = {}
        else:
            self._accounts = {}
            self._migrate_legacy()

    def _migrate_legacy(self):
        """Migrate tokens from legacy single-file locations."""
        for legacy_path, default_account in [
            (LEGACY_TOKEN_FILE, "default"),
            (LEGACY_MM_TOKEN_FILE, "ryanhadiii"),
        ]:
            if legacy_path.exists():
                try:
                    data = json.loads(legacy_path.read_text())
                    username = data.get("username", default_account)
                    self._accounts[username] = {
                        "access_token": data["access_token"],
                        "user_id": str(data.get("user_id", "")),
                        "username": username,
                        "expires_at": data.get("expires_at", 0),
                        "refreshed_at": data.get("refreshed_at", 0),
                    }
                    print(f"📦 Migrated token from {legacy_path} → account '{username}'")
                except (json.JSONDecodeError, KeyError, OSError) as e:
                    print(f"⚠️ Could not migrate {legacy_path}: {e}")
        if self._accounts:
            self._save_accounts()

    def _save_accounts(self):
        self.accounts_file.parent.mkdir(parents=True, exist_ok=True)
        self.accounts_file.write_text(json.dumps(self._accounts, indent=2))
        os.chmod(self.accounts_file, 0o600)

    def get(self, account: str) -> Optional[dict]:
        """Get raw token data for an account."""
        return self._accounts.get(account)

    def save(self, account: str, token: str, user_id: str, username: str,
             expires_in: int):
        """Save or update token for an account."""
        self._accounts[account] = {
            "access_token": token,
            "user_id": str(user_id),
            "username": username,
            "expires_at": int(time.time()) + expires_in,
            "refreshed_at": int(time.time()),
        }
        self._save_accounts()

    def is_expired(self, account: str) -> bool:
        data = self._accounts.get(account)
        if not data:
            return True
        return time.time() > data.get("expires_at", 0)

    def days_left(self, account: str) -> float:
        data = self._accounts.get(account)
        if not data:
            return 0
        return (data.get("expires_at", 0) - time.time()) / 86400

    def needs_refresh(self, account: str) -> bool:
        return self.days_left(account) < REFRESH_THRESHOLD_DAYS

    def list_accounts(self) -> list[str]:
        return list(self._accounts.keys())


# ─── OAuth Server ────────────────────────────────────────────────────────────

class OAuthCallbackHandler(http.server.BaseHTTPRequestHandler):
    """HTTP handler that captures the OAuth redirect code."""

    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        code = params.get("code", [None])[0]

        if code:
            self.server.auth_code = code
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"""
            <html><body style="font-family:system-ui;text-align:center;padding:60px;">
            <h1>&#10004; Connected!</h1>
            <p>You can close this tab and return to the terminal.</p>
            </body></html>
            """)
        else:
            self.send_response(400)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            error = params.get("error_message", ["Unknown error"])[0]
            self.wfile.write(f"<html><body><h1>&#10060; Error</h1><p>{error}</p></body></html>".encode())

    def log_message(self, format, *args):
        pass  # Suppress request logs


class OAuthServer:
    """Local HTTP server to catch OAuth redirect callback."""

    def __init__(self, port: int = 8765):
        self.port = port
        self.server = None

    def start(self) -> http.server.HTTPServer:
        self.server = http.server.HTTPServer(("127.0.0.1", self.port), OAuthCallbackHandler)
        self.server.auth_code = None
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        return self.server

    def wait_for_code(self, timeout: int = 300) -> str:
        """Wait for the OAuth redirect with auth code."""
        start = time.time()
        while time.time() - start < timeout:
            if self.server and self.server.auth_code:
                code = self.server.auth_code
                self.server.shutdown()
                return code
            time.sleep(0.5)
        if self.server:
            self.server.shutdown()
        raise OAuthTimeoutError(f"No callback received within {timeout}s")

    def stop(self):
        if self.server:
            self.server.shutdown()


# ─── Threads API Client ─────────────────────────────────────────────────────

@dataclass
class SlideResult:
    slide_idx: int
    post_id: str
    success: bool = True
    error: str = ""


@dataclass
class ThreadResult:
    root_id: str = ""
    permalink: str = ""
    slides: list = field(default_factory=list)
    success: bool = True
    error: str = ""

    @property
    def post_ids(self) -> list[str]:
        return [s.post_id for s in self.slides if s.success]


class ThreadsClient:
    """Threads API client with auto-auth."""

    def __init__(self, auth: 'ThreadsAuth'):
        self._auth = auth
        self._http = httpx.Client(timeout=30)
        self._uid: Optional[str] = None
        self._token: Optional[str] = None

    def _ensure_auth(self):
        """Ensure we have a valid token, refreshing if needed."""
        token, uid = self._auth.get_token()
        self._token = token
        self._uid = uid

    def _post(self, path: str, data: dict, retries: int = 2) -> dict:
        """POST with retry on transient errors."""
        url = f"{THREADS_API}/{path}"
        data["access_token"] = self._token

        for attempt in range(retries + 1):
            try:
                r = self._http.post(url, data=data)
                if r.status_code >= 500:
                    if attempt < retries:
                        time.sleep(2 + attempt)
                        continue
                    raise ThreadsAuthError(f"HTTP {r.status_code}: {r.text[:200]}")
                result = r.json()
                if r.status_code == 200:
                    return result
                if "transient" in str(result).lower() and attempt < retries:
                    time.sleep(2 + attempt)
                    continue
                error_msg = result.get("error", {}).get("message", str(result))
                raise ThreadsAuthError(f"API error: {error_msg}")
            except httpx.TimeoutException:
                if attempt < retries:
                    time.sleep(2)
                    continue
                raise
        raise ThreadsAuthError(f"Failed after {retries + 1} attempts")

    def _get(self, path: str, params: dict = None, retries: int = 1) -> dict:
        """GET with retry."""
        params = params or {}
        url = f"{THREADS_API}/{path}"
        params["access_token"] = self._token

        for attempt in range(retries + 1):
            try:
                r = self._http.get(url, params=params, timeout=15)
                if r.status_code == 200:
                    return r.json()
                if r.status_code >= 500 and attempt < retries:
                    time.sleep(2)
                    continue
                raise ThreadsAuthError(f"GET {path} failed: {r.status_code}")
            except httpx.TimeoutException:
                if attempt < retries:
                    time.sleep(2)
                    continue
                raise
        raise ThreadsAuthError(f"GET failed after {retries + 1} attempts")

    def create_container(self, text: str, reply_to: str = None,
                         image_url: str = None) -> str:
        """Create a media container. Returns container ID."""
        self._ensure_auth()
        data = {"media_type": "TEXT", "text": text.strip()}
        if reply_to:
            data["reply_to_id"] = reply_to

        # Try IMAGE first if image_url provided (root slide only)
        if image_url and not reply_to:
            img_data = {
                "media_type": "IMAGE",
                "image_url": image_url,
                "text": text.strip(),
            }
            try:
                result = self._post(f"{self._uid}/threads", img_data)
                return result["id"]
            except ThreadsAuthError as e:
                print(f"   ⚠️ Image failed ({e}), fallback to TEXT", file=sys.stderr)

        result = self._post(f"{self._uid}/threads", data)
        return result["id"]

    def publish(self, container_id: str) -> str:
        """Publish a container. Returns published post ID."""
        self._ensure_auth()
        result = self._post(f"{self._uid}/threads_publish",
                            {"creation_id": container_id})
        return result.get("id", "")

    def get_permalink(self, post_id: str = None) -> str:
        """Get permalink for the latest or specific post."""
        self._ensure_auth()
        try:
            data = self._get(f"{self._uid}/threads",
                             {"fields": "id,permalink,text", "limit": "5"})
            posts = data.get("data", [])
            if post_id:
                for p in posts:
                    if p["id"] == post_id:
                        return p.get("permalink", "")
            return posts[0].get("permalink", "") if posts else ""
        except Exception:
            return ""

    def get_user_info(self) -> dict:
        """Get current user info (id, username)."""
        self._ensure_auth()
        return self._get("me", {"fields": "id,username"})

    def verify_posts(self, limit: int = 10) -> list[dict]:
        """Get recent posts for verification."""
        self._ensure_auth()
        data = self._get(f"{self._uid}/threads",
                         {"fields": "id,text,timestamp,permalink", "limit": str(limit)})
        return data.get("data", [])

    def delete_post(self, post_id: str) -> bool:
        """Delete a post."""
        self._ensure_auth()
        try:
            r = self._http.delete(f"{THREADS_API}/{post_id}",
                                  params={"access_token": self._token})
            return r.status_code == 200
        except Exception:
            return False

    def get_publishing_limit(self) -> dict:
        """Get current publishing quota usage."""
        self._ensure_auth()
        return self._get(f"{self._uid}/threads_publishing_limit",
                         {"fields": "quota_usage,config"})

    def post_thread(self, slides: list[str], image_url: str = None) -> ThreadResult:
        """
        Post slides as a thread (fan-out: all replies to root).

        Slide 1 = root, Slides 2-N = replies to root.
        Reverse posting for correct display order.
        """
        self._ensure_auth()
        filtered = [s for s in slides if s.strip()]
        if not filtered:
            return ThreadResult(success=False, error="No slides")

        result = ThreadResult()
        root_pid = None

        # Post root first, then slides N..2 in reverse
        slide_indices = [0] + list(range(len(filtered) - 1, 0, -1))

        for i, slide_idx in enumerate(slide_indices):
            text = filtered[slide_idx].strip()
            if not text:
                continue

            # Char cap safety
            if len(text) > MAX_POST_LENGTH:
                trimmed = text[:MAX_POST_LENGTH]
                last_period = max(trimmed.rfind(". "), trimmed.rfind("! "), trimmed.rfind("? "))
                text = trimmed[:last_period + 1] if last_period > 50 else trimmed[:-1].rstrip() + "…"
                print(f"   ✂️ Slide {slide_idx+1} trimmed to {len(text)} chars", file=sys.stderr)

            reply_to = root_pid if i > 0 else None
            img = image_url if slide_idx == 0 else None

            try:
                cid = self.create_container(text, reply_to=reply_to, image_url=img)
                pid = self.publish(cid)
                result.slides.append(SlideResult(slide_idx=slide_idx, post_id=pid))

                if i == 0:
                    root_pid = pid
                    result.root_id = pid
                    time.sleep(1)
                    result.permalink = self.get_permalink(pid)

                # Rate limit pacing
                if i < len(filtered) - 1:
                    time.sleep(POST_DELAY_SECONDS if i in (1, 3) else 3)

            except Exception as e:
                print(f"   ⚠️ Slide {slide_idx+1} failed: {e}", file=sys.stderr)
                # Retry once
                try:
                    time.sleep(5)
                    cid = self.create_container(text, reply_to=reply_to, image_url=img)
                    pid = self.publish(cid)
                    result.slides.append(SlideResult(slide_idx=slide_idx, post_id=pid))
                    if i == 0:
                        root_pid = pid
                        result.root_id = pid
                except Exception as retry_err:
                    print(f"   ❌ Slide {slide_idx+1} retry failed: {retry_err}", file=sys.stderr)
                    result.slides.append(SlideResult(
                        slide_idx=slide_idx, post_id="", success=False, error=str(retry_err)))
                    if root_pid is None:
                        result.success = False
                        result.error = "Root post failed"
                        return result

        return result

    def close(self):
        self._http.close()


# ─── Main Auth Class ─────────────────────────────────────────────────────────

class ThreadsAuth:
    """
    Unified Threads authentication — same pattern as Buffer.

    auth = ThreadsAuth(account="ryanhadiii")
    auth.login()          # One-time OAuth (auto callback)
    auth.get_token()      # Auto-refresh, returns (token, user_id)
    client = auth.client()  # Get API client
    client.post_thread(slides)
    """

    def __init__(self, account: str = "default",
                 accounts_file: Path = ACCOUNTS_FILE,
                 callback_port: int = 8765):
        self.account = account
        self.tm = TokenManager(accounts_file)
        self.callback_port = callback_port
        self._client: Optional[ThreadsClient] = None

    def _load_app_config(self) -> tuple[str, str]:
        """Load app_id and app_secret from config file."""
        if not APP_CONFIG_FILE.exists():
            raise ThreadsAuthError(
                f"App config not found: {APP_CONFIG_FILE}\n"
                f"Create it with:\n"
                f'  {{"app_id": "YOUR_APP_ID", "app_secret": "YOUR_APP_SECRET"}}\n'
                f"Get from: https://developers.facebook.com/apps/"
            )
        data = json.loads(APP_CONFIG_FILE.read_text())
        if "app_id" not in data or "app_secret" not in data:
            raise ThreadsAuthError(f"Invalid config: {APP_CONFIG_FILE} (need app_id + app_secret)")
        return data["app_id"], data["app_secret"]

    def login(self, port: int = None) -> dict:
        """
        Full OAuth login flow with auto-callback server.

        1. Starts local HTTP server on port
        2. Prints authorization URL for user to open
        3. Catches redirect, extracts code
        4. Exchanges code → short-lived token → long-lived token
        5. Saves to accounts file

        Returns: token data dict
        """
        app_id, app_secret = self._load_app_config()
        port = port or self.callback_port
        redirect_uri = f"http://localhost:{port}/callback"

        # Build authorization URL
        auth_params = {
            "client_id": app_id,
            "redirect_uri": redirect_uri,
            "scope": ",".join(DEFAULT_SCOPES),
            "response_type": "code",
        }
        auth_url = f"{THREADS_OAUTH}?{urlencode(auth_params)}"

        # Start callback server
        server = OAuthServer(port)
        server.start()

        print(f"🔑 Threads OAuth Login — Account: {self.account}")
        print(f"{'=' * 50}")
        print()
        print(f"Open this URL in your browser:")
        print()
        print(f"  {auth_url}")
        print()
        print(f"Waiting for callback on localhost:{port}...")

        # Try to open browser
        try:
            import webbrowser
            webbrowser.open(auth_url)
            print("(Browser opened automatically)")
        except Exception:
            print(f"(Copy the URL above and open in your browser)")
        print()

        # Wait for callback
        try:
            code = server.wait_for_code(timeout=300)
        except OAuthTimeoutError:
            print()
            print("⏰ Timeout! No callback received.")
            print()
            print("Alternative: paste the redirect URL manually")
            raw = input("URL or code: ").strip()
            if "code=" in raw:
                code = raw.split("code=")[1].split("&")[0].split("#")[0]
            elif raw:
                code = raw
            else:
                raise

        print(f"✅ Got authorization code")

        # Exchange code → short-lived token
        print("⏳ Exchanging code for token...")
        http = httpx.Client(timeout=30)
        try:
            r = http.post(f"{THREADS_API}/oauth/access_token", data={
                "client_id": app_id,
                "client_secret": app_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            })
            if r.status_code != 200:
                raise ThreadsAuthError(f"Token exchange failed: {r.text}")
            short_token = r.json()["access_token"]
            print("✅ Short-lived token obtained")

            # Exchange → long-lived token
            print("⏳ Exchanging for long-lived token...")
            r = http.get(f"{THREADS_API}/access_token", params={
                "grant_type": "th_exchange_token",
                "client_secret": app_secret,
                "access_token": short_token,
            })
            if r.status_code == 200:
                data = r.json()
                long_token = data["access_token"]
                expires_in = data.get("expires_in", 60 * 86400)
                print(f"✅ Long-lived token ({expires_in // 86400} days)")
            else:
                # Fallback to short-lived
                long_token = short_token
                expires_in = 3600
                print("⚠️ Long-lived failed, using short-lived (1 hour)")

            # Get user info
            r = http.get(f"{THREADS_API}/me", params={
                "fields": "id,username",
                "access_token": long_token,
            })
            if r.status_code == 200:
                user_data = r.json()
                user_id = user_data.get("id", "")
                username = user_data.get("username", "")
                print(f"✅ Logged in as: @{username} (ID: {user_id})")
            else:
                user_id = ""
                username = self.account
                print(f"⚠️ Could not get user info: {r.text[:100]}")

        finally:
            http.close()

        # Save token
        self.tm.save(self.account, long_token, user_id, username, expires_in)

        print()
        print(f"{'=' * 50}")
        print(f"✅ Login complete! Token saved.")
        print(f"   Expires in ~{expires_in // 86400} days")
        print(f"   Account: {self.account}")

        return self.tm.get(self.account)

    def get_token(self, auto_refresh: bool = True) -> tuple[str, str]:
        """
        Get valid access token + user_id. Auto-refreshes if needed.

        Returns: (access_token, user_id)
        Raises: TokenExpiredError if token expired and refresh failed.
        """
        data = self.tm.get(self.account)
        if not data:
            raise TokenExpiredError(
                f"No token for account '{self.account}'. "
                f"Run: auth.login()"
            )

        # Check if refresh needed
        if auto_refresh and self.tm.needs_refresh(self.account):
            if self.tm.is_expired(self.account):
                # Try refresh one more time
                refreshed = self._refresh()
                if not refreshed:
                    raise TokenExpiredError(
                        f"Token for '{self.account}' expired. "
                        f"Run: auth.login()"
                    )
            else:
                self._refresh()

        # Re-read after potential refresh
        data = self.tm.get(self.account)
        return data["access_token"], data["user_id"]

    def _refresh(self) -> bool:
        """Refresh the long-lived token. Returns True on success."""
        try:
            _, app_secret = self._load_app_config()
        except ThreadsAuthError:
            return False

        data = self.tm.get(self.account)
        if not data:
            return False

        print(f"🔄 Refreshing token for '{self.account}'...", file=sys.stderr)
        http = httpx.Client(timeout=15)
        try:
            r = http.get(f"{THREADS_API}/refresh_access_token", params={
                "grant_type": "th_refresh_token",
                "access_token": data["access_token"],
            })
            if r.status_code == 200:
                result = r.json()
                new_token = result["access_token"]
                expires_in = result.get("expires_in", 60 * 86400)
                self.tm.save(self.account, new_token,
                             data["user_id"], data["username"], expires_in)
                print(f"✅ Token refreshed ({expires_in // 86400} days)", file=sys.stderr)
                return True
            else:
                print(f"❌ Refresh failed: {r.status_code} {r.text[:100]}", file=sys.stderr)
                return False
        except Exception as e:
            print(f"❌ Refresh error: {e}", file=sys.stderr)
            return False
        finally:
            http.close()

    def client(self) -> ThreadsClient:
        """Get a ThreadsClient bound to this auth."""
        if self._client is None:
            self._client = ThreadsClient(self)
        return self._client

    def status(self) -> dict:
        """Get account status info."""
        data = self.tm.get(self.account)
        if not data:
            return {"account": self.account, "status": "no_token"}
        return {
            "account": self.account,
            "username": data.get("username", "?"),
            "user_id": data.get("user_id", "?"),
            "days_left": round(self.tm.days_left(self.account), 1),
            "expired": self.tm.is_expired(self.account),
            "needs_refresh": self.tm.needs_refresh(self.account),
            "expires_at": data.get("expires_at", 0),
        }

    def close(self):
        if self._client:
            self._client.close()


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Threads Auth — OAuth + Token Manager")
    sub = parser.add_subparsers(dest="command")

    # login
    login_p = sub.add_parser("login", help="OAuth login flow")
    login_p.add_argument("--account", "-a", default="default", help="Account name")
    login_p.add_argument("--port", "-p", type=int, default=8765, help="Callback port")

    # status
    status_p = sub.add_parser("status", help="Check token status")
    status_p.add_argument("--account", "-a", default="default")

    # refresh
    refresh_p = sub.add_parser("refresh", help="Force refresh token")
    refresh_p.add_argument("--account", "-a", default="default")

    # list
    sub.add_parser("list", help="List all accounts")

    # post (quick test)
    post_p = sub.add_parser("post", help="Quick text post")
    post_p.add_argument("text", help="Text to post")
    post_p.add_argument("--account", "-a", default="default")

    args = parser.parse_args()

    if args.command == "login":
        auth = ThreadsAuth(account=args.account, callback_port=args.port)
        auth.login(port=args.port)

    elif args.command == "status":
        auth = ThreadsAuth(account=args.account)
        info = auth.status()
        if info["status"] == "no_token":
            print(f"❌ No token for '{args.account}'. Run: threads_auth.py login -a {args.account}")
        else:
            print(f"Account: {info['account']}")
            print(f"  Username:  @{info['username']}")
            print(f"  User ID:   {info['user_id']}")
            print(f"  Days left: {info['days_left']}")
            print(f"  Expired:   {info['expired']}")
            print(f"  Needs refresh: {info['needs_refresh']}")

    elif args.command == "refresh":
        auth = ThreadsAuth(account=args.account)
        success = auth._refresh()
        if success:
            info = auth.status()
            print(f"✅ Refreshed. Days left: {info['days_left']}")
        else:
            print("❌ Refresh failed")

    elif args.command == "list":
        tm = TokenManager()
        accounts = tm.list_accounts()
        if not accounts:
            print("No accounts configured. Run: threads_auth.py login")
        else:
            for acc in accounts:
                data = tm.get(acc)
                days = tm.days_left(acc)
                status = "✅" if days > 7 else "⚠️" if days > 0 else "❌"
                print(f"  {status} {acc} (@{data.get('username', '?')}) — {days:.1f} days left")

    elif args.command == "post":
        auth = ThreadsAuth(account=args.account)
        client = auth.client()
        try:
            cid = client.create_container(args.text)
            pid = client.publish(cid)
            print(f"✅ Posted: {pid}")
        except Exception as e:
            print(f"❌ Failed: {e}")
        finally:
            client.close()

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
