#!/usr/bin/env python3
"""
Threads Notifier — Telegram alerts on publish success / failure.
"""

import os
import sys
import httpx
from typing import Optional

TELEGRAM_API = "https://api.telegram.org/bot"


def _get_creds() -> tuple[Optional[str], Optional[str]]:
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    return bot_token, chat_id


def send(text: str, silent: bool = False) -> bool:
    """Send a Telegram message. Returns True on success."""
    bot_token, chat_id = _get_creds()
    if not bot_token or not chat_id:
        return False

    url = f"{TELEGRAM_API}{bot_token}/sendMessage"
    try:
        r = httpx.post(url, data={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_notification": silent,
        }, timeout=10)
        return r.status_code == 200
    except Exception as e:
        print(f"[Telegram] Send failed: {e}", file=sys.stderr)
        return False


def send_success(post_id: int, content_preview: str,
                thread_id: str, permalink: str) -> None:
    emoji = "✅"
    text = f"""{emoji} <b>Posted to Threads!</b>

Post #{post_id}
{content_preview}

🔗 {permalink}
"""
    send(text)


def send_failure(post_id: int, content_preview: str,
                 error: str, retry_count: int) -> None:
    max_retries = 3
    text = f"""❌ <b>Post Failed</b>

Post #{post_id}
{content_preview}

⚠️ Error: {error}
🔁 Retry: {retry_count}/{max_retries}
"""
    send(text)


def send_status(summary: str) -> None:
    """Send a periodic status digest."""
    send(f"📊 <b>Threads Agent Status</b>\n\n{summary}", silent=True)
