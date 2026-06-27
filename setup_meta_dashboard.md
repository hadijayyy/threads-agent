# 📋 SETUP GUIDE: Meta for Developers + Threads API

## Step 1: Buat Aplikasi di Meta for Developers

1. Buka [https://developers.facebook.com/](https://developers.facebook.com/)
2. Login dengan akun Facebook yang terhubung ke akun Threads
3. Klik **"My Apps"** → **"Create App"**
4. Pilih **"Business"** → Klik **"Next"**
5. **Display Name**: `Threads Auto Post` (atau nama lain yang kalian mau)
6. **Contact Email**: email@domain.com
7. Klik **"Create App"**

---

## Step 2: Tambahkan Produk Threads

1. Di dashboard aplikasi, cari **"Add a Product"** di sidebar
2. Find **"Threads"** → Klik **"Set Up"**
3. Produk Threads sekarang aktif

---

## Step 3: Setup OAuth Redirect URI

### ⚠️ KRUSIAL - Ini yang sering gagal

1. Di sidebar kiri: **"Settings"** → **"Basic"**
2. Scroll ke bawah ke bagian **"Threads App"**
3. Cari **"Valid OAuth Redirect URIs"**
4. Klik **"Add_uri"** dan tambahkan:
   ```
   https://localhost:8765/callback
   ```
5. Centang/aktifkan:
   - ✅ **Client OAuth Login**
   - ✅ **Web OAuth Login**
   - ✅ **Require App Secret**
6. Klik **"Save Changes"**

---

## Step 4: Dapatkan App ID dan App Secret

1. Di halaman **"Settings"** → **"Basic"**
2. Copy **App ID** (angka panjang)
3. Copy **App Secret** (klik "Show" untuk melihat)

---

## Step 5: Setup Permissions

Permissions yang dibutuhkan:
- `threads_basic` - Baca profil Threads
- `threads_content_publish` - Posting ke Threads
- `threads_manage_replies` - Kelola reply
- `threads_read_replies` - Baca reply
- `threads_manage_insights` - Analytics

---

## Step 6: Dapatkan User Access Token (Long-Lived)

### Cara 1: Graph API Explorer (Paling Gampang)

1. Buka: [https://developers.facebook.com/tools/explorer/](https://developers.facebook.com/tools/explorer/)
2. Pilih aplikasi yang baru dibuat di dropdown "Meta App"
3. Klik **"Generate Access Token"**
4. Login dengan akun Facebook/Threads
5. Grant permission yang dibutuhkan
6. Copy token yang dihasilkan

### Cara 2: OAuth Flow (Dari Aplikasi Kalian)

1. Buka URL ini di browser:
```
https://threads.net/oauth/authorize
  ?client_id=APP_ID_KALIAN
  &redirect_uri=https://localhost:8765/callback
  &scope=threads_basic,threads_content_publish,threads_manage_replies,threads_read_replies,threads_manage_insights
  &response_type=code
```

2. Login dan authorize
3. Copy redirect URL
4. Exchange code ke long-lived token

---

## Step 7: Exchange Short-Lived → Long-Lived Token

Long-lived token bertahan ~60 hari (auto-refresh setiap 7 hari).

```bash
curl -X GET "https://graph.threads.net/access_token?grant_type=th_exchange_token&client_secret=APP_SECRET&access_token=SHORT_LIVED_TOKEN"
```

Response:
```json
{
  "access_token": "LONG_LIVED_TOKEN...",
  "expires_in": 5184000
}
```

---

## Step 8: Verifikasi Token

```bash
curl -X GET "https://graph.threads.net/me?fields=id,username&access_token=LONG_LIVED_TOKEN"
```

Response:
```json
{
  "id": "17841400000000000",
  "username": "namakalian"
}
```

---

## Step 9: Setup Environment Variables

```bash
cp .env.example .env
nano .env
```

Isi dengan:
```
THREADS_APP_ID=APP_ID_KALIAN
THREADS_APP_SECRET=APP_SECRET_KALIAN
THREADS_ACCESS_TOKEN=LONG_LIVED_TOKEN_DARI_STEP_7
THREADS_USER_ID=USER_ID_DARI_STEP_8
TELEGRAM_BOT_TOKEN=TOKEN_BOT_TELEGRAM
TELEGRAM_CHAT_ID=CHAT_ID_TELEGRAM_KALIAN
```

---

## Step 10: Verifikasi Setup

```bash
cd ~/threads-agent
python3 verify_setup.py
```

---

## Troubleshooting

### Error: "URL Blocked: redirect URI not whitelisted"
➡️ Checklist:
- [ ] redirect URI `https://localhost:8765/callback` sudah ditambahkan
- [ ] Client OAuth Login diaktifkan
- [ ] Web OAuth Login diaktifkan
- [ ] Perubahan sudah di-save

### Error: "Insecure Login Blocked"
➡️ Pastikan redirect_uri pakai HTTPS:
```
&redirect_uri=https%3A%2F%2Flocalhost%3A8765%2Fcallback
```

### Error: "Invalid OAuth 2.0 Access Token"
➡️ Token expired atau belum di-exchange ke long-lived. Lihat Step 7.

---

## Quick Reference: Endpoints

| Endpoint | Method | Fungsi |
|----------|--------|--------|
| `/me` | GET | Get user info |
| `/{user_id}/threads` | POST | Create container |
| `/{user_id}/threads_publish` | POST | Publish container |
| `/access_token` | GET | Exchange/refresh token |
| `/{user_id}/threads_publishing_limit` | GET | Check rate limit |
