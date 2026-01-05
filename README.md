# Protection + Userbot-Style Chatbot (Siputzx)

## Features
- Dashboard /start (button)
- Toggle per chat via Settings button:
  - CAPTCHA ON/OFF
  - PROTECTION ON/OFF
  - CHATBOT ON/OFF
- Chatbot gaya userbot:
  - role per chat (SQLite)
  - /setrole <teks>
  - /role
  - output singkat (MAX_CHARS), 1 kalimat
  - Siputzx GPT3 (prompt+content) + fallback /api/gpt

## Setup
```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
.env
env
Salin kode
BOT_TOKEN=YOUR_TOKEN

# chatbot endpoints
SIPUTZX_GPT3_URL=https://api.siputzx.my.id/api/ai/gpt3
SIPUTZX_GPT_URL=https://apis-liart.vercel.app/api/gpt

DEFAULT_ROLE=...
FAST_TIMEOUT=4
MAX_SENTENCES=1
MAX_CHARS=15
STRIP_BOT_MENTION=1
Run
bash
Salin kode
python bot.py
perl
Salin kode

---

## Penting (biar sesuai yang kamu contohin)
Di contoh kamu ada “auto delete mention @mention”. Itu berbahaya kalau di grup (orang mention temennya malah kehapus).  
Makanya di versi ini aku **tidak delete message mention**. Aku cuma **hapus @botusername dari prompt** (biar jawaban bersih), lewat `STRIP_BOT_MENTION=1`.

Kalau kamu tetap ngotot mau delete mention, bilang—nanti aku bikin opsinya **delete hanya mention bot**, bukan semua mention.

Kalau kamu mau chatbotnya **default aktif (enabled=True)** kayak contoh Mongo kamu, tinggal ubah default `chatbot_enabled` dari 0 → 1 di table `settings` (storage.py).
::contentReference[oaicite:0]{index=0}
