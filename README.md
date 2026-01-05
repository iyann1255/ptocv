# Telegram Protection Bot (Full)

## Features
- Captcha join (ON/OFF per chat)
- Protection (anti-flood, anti-link, badword) (ON/OFF per chat)
- Whitelist immunity
- Warn system
- Raid mode
- TagAll tracked members + TagAdmin
- Ping (latency + uptime)
- Modules menu via inline buttons
- Chatbot AI (Siputzx) (ON/OFF per chat) via /ai

## Setup
```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
Env (.env)

Create .env:

BOT_TOKEN=YOUR_TELEGRAM_BOT_TOKEN

CAPTCHA_TIMEOUT_SEC=90
FLOOD_WINDOW_SEC=6
FLOOD_MAX_MSG=6
FLOOD_MUTE_SEC=120

WARN_LIMIT=3
WARN_MUTE_SEC=300
BAD_WORDS=scam,judol

BLOCK_LINKS=1
BLOCK_TME=1
ADMIN_BYPASS=1

RAID_CAPTCHA_TIMEOUT_SEC=45
RAID_FLOOD_MAX_MSG=4

TAGALL_COOLDOWN_SEC=90
TAGALL_CHUNK_SIZE=25
TAGALL_MAX_MEMBERS=250

SIPUTZX_GPT_URL=https://apis-liart.vercel.app/api/gpt
AI_MAX_CHARS=600

Run
python bot.py

Permissions needed (make bot admin)

Delete messages

Restrict members

Ban users


---

## Cara pakai (singkat)
1) Jadikan bot admin di grup (wajib untuk delete/mute/kick).  
2) Ketik **/modules** → buka menu → **⚙️ Settings** → toggle:
- CAPTCHA ON/OFF
- PROTECTION ON/OFF
- CHATBOT AI ON/OFF

3) AI: **/ai halo** (kalau chatbot ON)

---

Kalau lu mau, next upgrade paling berguna:
- **Sub-toggle Protection** (anti-link ON tapi flood OFF, dll)
- **Auto AI mode** (nyaut kalau mention bot, tapi tetap aman)
- **Logging ke channel** (biar admin tau siapa di-mute/kick dan kenapa)
::contentReference[oaicite:0]{index=0}
