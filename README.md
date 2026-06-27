# 🧵 Threads Auto-Post Agent

Sistem otomatisasi posting Threads seperti Buffer.com. Sekali setup, agent akan cek database secara periodik dan post konten secara otomatis sesuai jadwal.

---

## 📁 Struktur File

```
threads-agent/
├── .env                      # Credential storage (git-ignored)
├── .env.example              # Template
├── setup_meta_dashboard.md   # Step-by-step Meta Developer setup guide
├── database.py               # SQLite operations
├── poster.py                 # Threads API posting engine
├── notifier.py               # Telegram notifications
├── agent.py                  # Main scheduler engine
├── logs/                     # Log files
└── posts.db                  # SQLite database (auto-created)
```

---

## ⚡ Quick Start

### 1. Setup Meta App (Wajib)

Ikuti panduan lengkap di `setup_meta_dashboard.md`:

1. Buat app di [Meta Developer](https://developers.facebook.com/)
2. Tambahkan produk **Threads**
3. Setup OAuth redirect URI → `https://localhost:8765/callback`
4. Enable permissions: `threads_content_publish`, `threads_basic`, dll.
5. Dapatkan **App ID** dan **App Secret**

### 2. Setup Credentials

```bash
cd ~/threads-agent
cp .env.example .env
nano .env
```

Isi `.env`:
```env
THREADS_APP_ID=1538280827781766
THREADS_APP_SECRET=your_app_secret_here
THREADS_ACCESS_TOKEN=your_long_lived_token_here
THREADS_USER_ID=your_user_id_here

TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id

DB_PATH=/home/ubuntu/threads-agent/posts.db
POLL_INTERVAL_MINUTES=5
```

### 3. Dapatkan Long-Lived Token

**Cara termudah** — pakai Graph API Explorer:

1. Buka: https://developers.facebook.com/tools/explorer/
2. Pilih app (dropdown "Meta App")
3. Klik **"Generate Access Token"**
4. Login + grant permissions
5. Copy token yang keluar

**Upgrade ke Long-Lived (60 days):**

```bash
curl -X GET "https://graph.threads.net/access_token?\
  grant_type=th_exchange_token&\
  client_secret=APP_SECRET&\
  access_token=SHORT_LIVED_TOKEN"
```

Response → copy `access_token` ke `.env`.

### 4. Verifikasi Setup

```bash
cd ~/threads-agent
python3 agent.py verify
```

Output harus: `✅ Connected as: @username (user_id)`

### 5. Enqueue Test Post

```bash
# Test post (langsung jalan)
python3 agent.py enqueue

# Cek stats
python3 agent.py stats
```

### 6. Run Agent

```bash
# Single poll (untuk test)
python3 agent.py check

# Daemon mode (untuk production)
python3 agent.py run
```

---

## 📝 Enqueue Posts

### Dari CLI

```bash
# Post biasa (skema sekarang)
python3 agent.py enqueue \
  --content "Market Update: $AAPL naik 5% pasca earnings 🎉" \
  --at "2025-07-14T09:00:00Z"

# Thread (pisah pakai ===)
python3 agent.py enqueue \
  --content "Slide 1 content===Slide 2 content===Slide 3 content" \
  --at "2025-07-14T09:00:00Z"

# Dengan gambar
python3 agent.py enqueue \
  --content "Check this out!" \
  --at "2025-07-14T09:00:00Z" \
  --media "https://example.com/image.jpg"
```

### Dari Python

```python
from database import Database
from datetime import datetime, timezone

db = Database()

# Post sederhana
pid = db.add_post(
    content="Market Monday! $TSLA breakout 🔥",
    scheduled_at=datetime.now(timezone.utc).isoformat()  # langsung jalan
)

# Post terjadwal
pid = db.add_post(
    content="Weekend recap\n===\nSlide 1\n===\nSlide 2",
    scheduled_at="2025-07-21T09:00:00Z"
)
```

---

## 🔄 Scheduling dengan Cron

### Otomatis check setiap 5 menit

```bash
crontab -e
```

Tambahkan:
```cron
*/5 * * * * cd /home/ubuntu/threads-agent && python3 agent.py check >> logs/cron.log 2>&1
```

### Market Monday Schedule

```cron
# Every Monday at 9 AM WIB
0 2 * * 1 cd /home/ubuntu/threads-agent && python3 agent.py check >> logs/cron.log 2>&1
```

---

## 📊 Database Operations

```bash
# Lihat semua post
python3 database.py list

# Filter by status
python3 database.py list --status pending
python3 database.py list --status published

# Statistik
python3 database.py stats

# Cancel post
python3 database.py cancel 42
```

---

## 🔔 Telegram Notifications

Bot akan kirim notifikasi ke Telegram saat:
- ✅ Post berhasil dipublish
- ❌ Post gagal (beserta error message)
- 📊 Status digest (bila di-enable)

**Setup:**
1. Buka [@BotFather](https://t.me/BotFather) di Telegram
2. Ketik `/newbot` → follow instructions
3. Copy Bot Token ke `.env`
4. Buka [@userinfobot](https://t.me/userinfobot) → get your Chat ID
5. Paste Chat ID ke `.env`

---

## 🧪 Testing

```bash
# Test API connection
python3 poster.py test

# Check publishing quota
python3 poster.py quota

# Dry run (gak benar-benar post)
DRY_RUN=true python3 agent.py check

# Post single text
python3 poster.py post "Hello from CLI!"

# Post thread via CLI
python3 poster.py post "Slide 1===Slide 2===Slide 3" --thread
```

---

## 🛠️ Troubleshooting

### "URL Blocked: redirect URI not whitelisted"
→ Checklist:
- [ ] `https://localhost:8765/callback` ada di Valid OAuth Redirect URIs
- [ ] Client OAuth Login = ON
- [ ] Web OAuth Login = ON
- [ ] Save Changes ditekan

### "Insecure Login Blocked"
→ Redirect URI harus HTTPS: `https://localhost:8765/callback`

### "Invalid OAuth 2.0 Access Token"
→ Token expired. Re-generate via Graph API Explorer.

### Post gagal terus
→ Cek error message dari Telegram notification.
→ Verifikasi token: `python3 agent.py verify`

---

## 🔄 Workflow Lengkap

```
1. Setup Meta App        → setup_meta_dashboard.md
2. Dapatkan Token        → Graph API Explorer
3. Setup .env           → Masukkan credentials
4. Verify              → python3 agent.py verify
5. Enqueue test post   → python3 agent.py enqueue
6. Test posting        → python3 agent.py check
7. Setup cron          → */5 * * * * ...
8. Done! 🎉           → Posts akan jalan otomatis
```

---

## 📡 API Reference

| Method | Endpoint | Fungsi |
|--------|----------|--------|
| GET | `/me` | User info |
| POST | `/{user_id}/threads` | Create container |
| POST | `/{user_id}/threads_publish` | Publish container |
| GET | `/access_token` | Exchange/refresh token |
| GET | `/{user_id}/threads_publishing_limit` | Rate limit quota |

---

## 🛡️ Security Notes

- `.env` contains secrets — **NEVER** commit to git
- Token files: `chmod 600 ~/.hermes/threads_accounts.json`
- Use environment variables in production
- Long-lived tokens auto-refresh setiap 7 hari

---

Buat oleh: Threads Agent Builder | Powered by Hermes Agent
