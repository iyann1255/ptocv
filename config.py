import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

    # Captcha / New member
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

    # Word filter
    BAD_WORDS = [w.strip().lower() for w in os.getenv("BAD_WORDS", "scam,judol").split(",") if w.strip()]

    # Admin bypass
    ADMIN_BYPASS = os.getenv("ADMIN_BYPASS", "1") == "1"

    # Raid mode
    RAID_CAPTCHA_TIMEOUT_SEC = int(os.getenv("RAID_CAPTCHA_TIMEOUT_SEC", "45"))
    RAID_FLOOD_MAX_MSG = int(os.getenv("RAID_FLOOD_MAX_MSG", "4"))

    # TagAll
    TAGALL_COOLDOWN_SEC = int(os.getenv("TAGALL_COOLDOWN_SEC", "90"))
    TAGALL_CHUNK_SIZE = int(os.getenv("TAGALL_CHUNK_SIZE", "25"))
    TAGALL_MAX_MEMBERS = int(os.getenv("TAGALL_MAX_MEMBERS", "250"))

    # =========================
    # CHATBOT (USERBOT STYLE)
    # =========================
    FAST_TIMEOUT = float(os.getenv("FAST_TIMEOUT", "4"))

    DEFAULT_ROLE = os.getenv(
        "DEFAULT_ROLE",
        "Kamu adalah seorang yang lagi ngebalesin chat di dalam group chat. "
        "Balasanmu harus menggunakan bahasa gaul sehari-hari ala anak gen z, sangat singkat, dan responsif. "
        "Balasan tidak perlu selalu formal atau informatif, fokus saja pada reaksi emosional atau komentar cepat.\n"
        "Jangan pernah menawarkan bantuan dan bertanya.\n"
        "Jangan ngaku bot atau AI.\n"
        "Jawaban singkat Maksimal 10 - 15 karakter.\n"
        "kalo ada yang nanya/ada bacaan ubot, langsung ketik ini, ubot gacor di @asepvoid.\n"
    )

    SIPUTZX_GPT3_URL = os.getenv("SIPUTZX_GPT3_URL", "https://api.siputzx.my.id/api/ai/gpt3").strip()
    SIPUTZX_GPT_URL = os.getenv("SIPUTZX_GPT_URL", "https://apis-liart.vercel.app/api/gpt").strip()

    MAX_SENTENCES = int(os.getenv("MAX_SENTENCES", "1"))
    MAX_CHARS = int(os.getenv("MAX_CHARS", "15"))

    # kalau True: hapus mention @botusername dari teks prompt (bukan hapus message)
    STRIP_BOT_MENTION = os.getenv("STRIP_BOT_MENTION", "1") == "1"
