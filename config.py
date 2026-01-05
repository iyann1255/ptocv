import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

    # Captcha / New member (default values, can be toggled per chat via DB settings)
    CAPTCHA_TIMEOUT_SEC = int(os.getenv("CAPTCHA_TIMEOUT_SEC", "90"))
    CAPTCHA_TEXT = os.getenv("CAPTCHA_TEXT", "Klik tombol untuk verifikasi dalam {sec} detik.")

    # Anti-flood
    FLOOD_WINDOW_SEC = int(os.getenv("FLOOD_WINDOW_SEC", "6"))
    FLOOD_MAX_MSG = int(os.getenv("FLOOD_MAX_MSG", "6"))
    FLOOD_MUTE_SEC = int(os.getenv("FLOOD_MUTE_SEC", "120"))

    # Warn system
    WARN_LIMIT = int(os.getenv("WARN_LIMIT", "3"))
    WARN_MUTE_SEC = int(os.getenv("WARN_MUTE_SEC", "300"))

    # Anti-link
    BLOCK_LINKS = os.getenv("BLOCK_LINKS", "1") == "1"
    BLOCK_TME = os.getenv("BLOCK_TME", "1") == "1"

    # Word filter (comma-separated)
    BAD_WORDS = [w.strip().lower() for w in os.getenv("BAD_WORDS", "scam,judol").split(",") if w.strip()]

    # Admin bypass
    ADMIN_BYPASS = os.getenv("ADMIN_BYPASS", "1") == "1"

    # Raid mode defaults
    RAID_CAPTCHA_TIMEOUT_SEC = int(os.getenv("RAID_CAPTCHA_TIMEOUT_SEC", "45"))
    RAID_FLOOD_MAX_MSG = int(os.getenv("RAID_FLOOD_MAX_MSG", "4"))

    # TagAll
    TAGALL_COOLDOWN_SEC = int(os.getenv("TAGALL_COOLDOWN_SEC", "90"))
    TAGALL_CHUNK_SIZE = int(os.getenv("TAGALL_CHUNK_SIZE", "25"))  # safe range 5..40
    TAGALL_MAX_MEMBERS = int(os.getenv("TAGALL_MAX_MEMBERS", "250"))

    # Chatbot AI (Siputzx)
    SIPUTZX_GPT_URL = os.getenv("SIPUTZX_GPT_URL", "https://apis-liart.vercel.app/api/gpt")
    AI_MAX_CHARS = int(os.getenv("AI_MAX_CHARS", "600"))
