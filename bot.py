import re
import time
import asyncio
import logging
from dataclasses import dataclass
from typing import Dict, Deque, Tuple
from collections import defaultdict, deque

import httpx
from telegram import (
    Update,
    ChatPermissions,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.constants import ChatMemberStatus
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

from config import Config
import storage

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("protect-bot")

BOT_START_TS = time.time()

# ---- Regex link ----
URL_RE = re.compile(r"(https?://|www\.)\S+", re.IGNORECASE)
TME_RE = re.compile(r"(t\.me/|telegram\.me/|telegram\.dog/)\S+", re.IGNORECASE)

# ---- In-memory state ----
# flood[chat_id][user_id] -> deque[timestamps]
flood: Dict[int, Dict[int, Deque[float]]] = defaultdict(lambda: defaultdict(deque))

# captcha_pending[(chat_id, user_id)] = (deadline_ts, verify_token)
captcha_pending: Dict[Tuple[int, int], Tuple[float, str]] = {}

@dataclass
class DynamicLimits:
    captcha_timeout: int
    flood_max: int

def get_limits(chat_id: int) -> DynamicLimits:
    if storage.is_raid(chat_id):
        return DynamicLimits(
            captcha_timeout=Config.RAID_CAPTCHA_TIMEOUT_SEC,
            flood_max=Config.RAID_FLOOD_MAX_MSG,
        )
    return DynamicLimits(
        captcha_timeout=Config.CAPTCHA_TIMEOUT_SEC,
        flood_max=Config.FLOOD_MAX_MSG,
    )

def fmt_uptime(seconds: int) -> str:
    seconds = max(0, int(seconds))
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    if d > 0:
        return f"{d}d {h}h {m}m {s}s"
    if h > 0:
        return f"{h}h {m}m {s}s"
    if m > 0:
        return f"{m}m {s}s"
    return f"{s}s"

def mention_html(user_id: int, name: str) -> str:
    safe = (name or "user").replace("<", "").replace(">", "")
    return f'<a href="tg://user?id={user_id}">{safe}</a>'

async def is_admin(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    try:
        cm = await context.bot.get_chat_member(update.effective_chat.id, user_id)
        return cm.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER)
    except Exception:
        return False

async def restrict_user(chat_id: int, user_id: int, can_send: bool):
    perms = ChatPermissions(
        can_send_messages=can_send,
        can_send_audios=can_send,
        can_send_documents=can_send,
        can_send_photos=can_send,
        can_send_videos=can_send,
        can_send_video_notes=can_send,
        can_send_voice_notes=can_send,
        can_send_polls=can_send,
        can_send_other_messages=can_send,
        can_add_web_page_previews=can_send,
    )
    return perms

async def mute_for(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int, seconds: int, reason: str = ""):
    until = int(time.time()) + seconds
    perms = await restrict_user(chat_id, user_id, can_send=False)
    await context.bot.restrict_chat_member(chat_id, user_id, permissions=perms, until_date=until)
    if reason:
        try:
            await context.bot.send_message(chat_id, f"User {user_id} dimute {seconds}s. {reason}")
        except Exception:
            pass

async def unmute(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int):
    perms = await restrict_user(chat_id, user_id, can_send=True)
    await context.bot.restrict_chat_member(chat_id, user_id, permissions=perms)

async def kick(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int, reason: str = ""):
    await context.bot.ban_chat_member(chat_id, user_id)
    await context.bot.unban_chat_member(chat_id, user_id)  # soft-kick
    if reason:
        try:
            await context.bot.send_message(chat_id, f"User {user_id} dikick. {reason}")
        except Exception:
            pass

# ----------------- Inline Modules UI -----------------
MODULE_TEXT = {
    "protection": (
        "<b>🛡 Protection Module</b>\n\n"
        "• Anti flood spam\n"
        "• Anti link (URL / t.me)\n"
        "• Badword filter\n"
        "• Auto mute / kick\n\n"
        "Toggle: /modules → ⚙️ Settings"
    ),
    "captcha": (
        "<b>👋 Captcha Module</b>\n\n"
        "• Verifikasi member baru (button)\n"
        "• Timeout = auto kick\n"
        "• Skip untuk whitelist\n\n"
        "Toggle: /modules → ⚙️ Settings"
    ),
    "warn": (
        "<b>⚠️ Warn Module</b>\n\n"
        "Commands:\n"
        "• /warn (reply user)\n"
        "• /unwarn\n"
        "• /warns (reply user)\n"
        "• Auto kick jika limit\n"
    ),
    "whitelist": (
        "<b>🧑‍💼 Whitelist Module</b>\n\n"
        "Commands:\n"
        "• /allow (reply user)\n"
        "• /unallow\n"
        "• /allowlist\n\n"
        "Whitelist = kebal semua proteksi."
    ),
    "tagall": (
        "<b>📢 TagAll Module</b>\n\n"
        "Commands:\n"
        "• /tagall [text]\n"
        "• /tagadmin [text]\n\n"
        "TagAll = tracked members (yang pernah aktif)."
    ),
    "raid": (
        "<b>🚨 Raid Mode</b>\n\n"
        "Commands:\n"
        "• /raid on\n"
        "• /raid off\n\n"
        "Saat ON: captcha lebih ketat, flood lebih sensitif."
    ),
    "ping": (
        "<b>🏓 Ping</b>\n\n"
        "• /ping = latency + uptime\n"
        "• Button Ping = quick status\n"
    ),
    "chatbot": (
        "<b>🤖 Chatbot AI</b>\n\n"
        "• /ai <prompt>\n"
        "• Pakai API Siputzx\n\n"
        "Toggle: /modules → ⚙️ Settings"
    ),
    "settings": (
        "<b>⚙️ Settings</b>\n\n"
        "Klik tombol untuk ON/OFF fitur:\n"
        "• CAPTCHA\n"
        "• PROTECTION\n"
        "• CHATBOT AI\n"
    ),
}

def modules_main_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🛡 Protection", callback_data="mod:protection"),
            InlineKeyboardButton("👋 Captcha", callback_data="mod:captcha"),
        ],
        [
            InlineKeyboardButton("⚠️ Warn", callback_data="mod:warn"),
            InlineKeyboardButton("🧑‍💼 Whitelist", callback_data="mod:whitelist"),
        ],
        [
            InlineKeyboardButton("📢 TagAll", callback_data="mod:tagall"),
            InlineKeyboardButton("🚨 Raid", callback_data="mod:raid"),
        ],
        [
            InlineKeyboardButton("🤖 Chatbot AI", callback_data="mod:chatbot"),
            InlineKeyboardButton("🏓 Ping", callback_data="mod:ping"),
        ],
        [
            InlineKeyboardButton("⚙️ Settings", callback_data="mod:settings"),
        ],
    ])

def back_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Back", callback_data="mod:main")]
    ])

def settings_keyboard(chat_id: int):
    s = storage.get_settings(chat_id)

    def onoff(v: bool):
        return "✅ ON" if v else "❌ OFF"

    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"👋 CAPTCHA: {onoff(s['captcha'])}", callback_data="set:captcha")],
        [InlineKeyboardButton(f"🛡 PROTECTION: {onoff(s['protection'])}", callback_data="set:protection")],
        [InlineKeyboardButton(f"🤖 CHATBOT AI: {onoff(s['chatbot'])}", callback_data="set:chatbot")],
        [InlineKeyboardButton("⬅️ Back", callback_data="mod:main")],
    ])

async def modules_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    data = q.data
    chat_id = q.message.chat.id

    if data == "mod:main":
        return await q.edit_message_text(
            "<b>📦 Bot Modules</b>\n\nPilih modul untuk lihat fitur:",
            reply_markup=modules_main_keyboard(),
            parse_mode="HTML",
        )

    if data == "mod:settings":
        if not await is_admin(update, context, update.effective_user.id):
            return await q.answer("Admin only.", show_alert=True)
        return await q.edit_message_text(
            MODULE_TEXT["settings"],
            reply_markup=settings_keyboard(chat_id),
            parse_mode="HTML",
        )

    if data == "mod:ping":
        # quick status
        uptime = fmt_uptime(int(time.time() - BOT_START_TS))
        return await q.edit_message_text(
            f"🏓 <b>Pong</b>\n"
            f"• Uptime: <b>{uptime}</b>\n"
            f"• Status: <b>ON</b>",
            reply_markup=back_keyboard(),
            parse_mode="HTML",
        )

    if data.startswith("mod:"):
        key = data.split(":", 1)[1]
        text = MODULE_TEXT.get(key)
        if not text:
            return
        await q.edit_message_text(text, reply_markup=back_keyboard(), parse_mode="HTML")

async def settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    chat_id = q.message.chat.id
    if not await is_admin(update, context, update.effective_user.id):
        return await q.answer("Admin only.", show_alert=True)

    key = q.data.split(":", 1)[1]  # captcha / protection / chatbot
    storage.toggle_setting(chat_id, key)

    await q.edit_message_text(
        "<b>⚙️ Settings</b>\n\nKlik tombol untuk ON/OFF fitur:",
        reply_markup=settings_keyboard(chat_id),
        parse_mode="HTML",
    )

# ----------------- Commands -----------------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Protection bot aktif.\n"
        "Cek fitur: /modules\n"
        "Ping: /ping\n"
        "AI: /ai <prompt> (kalau ON)\n"
    )

async def cmd_modules(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "<b>📦 Bot Modules</b>\n\nPilih modul untuk lihat fitur:",
        reply_markup=modules_main_keyboard(),
        parse_mode="HTML",
    )

async def cmd_ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t0 = time.time()
    msg = await update.message.reply_text("Pinging...")
    t1 = time.time()

    latency_ms = int((t1 - t0) * 1000)
    uptime = fmt_uptime(int(time.time() - BOT_START_TS))

    await msg.edit_text(
        f"🏓 <b>Pong</b>\n"
        f"• Latency: <b>{latency_ms} ms</b>\n"
        f"• Uptime: <b>{uptime}</b>",
        parse_mode="HTML",
    )

async def cmd_raid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context, update.effective_user.id):
        return await update.message.reply_text("Admin only.")
    if len(context.args) != 1 or context.args[0].lower() not in ("on", "off"):
        return await update.message.reply_text("Usage: /raid on|off")
    enabled = context.args[0].lower() == "on"
    storage.set_raid(update.effective_chat.id, enabled)
    await update.message.reply_text(f"Raid mode: {'ON' if enabled else 'OFF'}")

async def cmd_warn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context, update.effective_user.id):
        return await update.message.reply_text("Admin only.")
    if not update.message.reply_to_message:
        return await update.message.reply_text("Reply ke pesan user yang mau di-warn.")
    target = update.message.reply_to_message.from_user
    chat_id = update.effective_chat.id

    newc = storage.inc_warn(chat_id, target.id, 1)
    if newc >= Config.WARN_LIMIT:
        storage.set_warn(chat_id, target.id, 0)
        await kick(context, chat_id, target.id, reason=f"WARN limit ({Config.WARN_LIMIT})")
        return
    await update.message.reply_text(f"Warn {target.id}: {newc}/{Config.WARN_LIMIT}")

async def cmd_unwarn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context, update.effective_user.id):
        return await update.message.reply_text("Admin only.")
    if not update.message.reply_to_message:
        return await update.message.reply_text("Reply ke pesan user yang mau dikurangin warn.")
    target = update.message.reply_to_message.from_user
    chat_id = update.effective_chat.id
    newc = storage.inc_warn(chat_id, target.id, -1)
    await update.message.reply_text(f"Warn {target.id}: {newc}/{Config.WARN_LIMIT}")

async def cmd_warns(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.reply_to_message:
        return await update.message.reply_text("Reply ke pesan user buat cek warn.")
    target = update.message.reply_to_message.from_user
    chat_id = update.effective_chat.id
    c = storage.get_warn(chat_id, target.id)
    await update.message.reply_text(f"Warn {target.id}: {c}/{Config.WARN_LIMIT}")

async def cmd_mute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context, update.effective_user.id):
        return await update.message.reply_text("Admin only.")
    if not update.message.reply_to_message:
        return await update.message.reply_text("Reply ke user. Usage: /mute 300")
    sec = int(context.args[0]) if context.args else 300
    target = update.message.reply_to_message.from_user
    await mute_for(context, update.effective_chat.id, target.id, sec, reason="Manual mute")
    await update.message.reply_text(f"Muted {target.id} for {sec}s")

async def cmd_unmute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context, update.effective_user.id):
        return await update.message.reply_text("Admin only.")
    if not update.message.reply_to_message:
        return await update.message.reply_text("Reply ke user buat unmute.")
    target = update.message.reply_to_message.from_user
    await unmute(context, update.effective_chat.id, target.id)
    await update.message.reply_text(f"Unmuted {target.id}")

# ---- Whitelist commands ----
async def cmd_allow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context, update.effective_user.id):
        return await update.message.reply_text("Admin only.")
    if not update.message.reply_to_message:
        return await update.message.reply_text("Reply ke user yang mau di-allow.")
    target = update.message.reply_to_message.from_user
    storage.add_whitelist(update.effective_chat.id, target.id)
    await update.message.reply_text(f"User {target.id} masuk whitelist. Kebal semua proteksi.")

async def cmd_unallow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context, update.effective_user.id):
        return await update.message.reply_text("Admin only.")
    if not update.message.reply_to_message:
        return await update.message.reply_text("Reply ke user yang mau dicabut whitelist.")
    target = update.message.reply_to_message.from_user
    storage.remove_whitelist(update.effective_chat.id, target.id)
    await update.message.reply_text(f"User {target.id} keluar dari whitelist.")

async def cmd_allowlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context, update.effective_user.id):
        return await update.message.reply_text("Admin only.")
    rows = storage.list_whitelist(update.effective_chat.id)
    if not rows:
        return await update.message.reply_text("Whitelist kosong.")
    text = "Whitelist users:\n" + "\n".join([f"- {uid}" for (uid,) in rows])
    await update.message.reply_text(text)

# ---- TagAll ----
async def cmd_tagall(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    uid = update.effective_user.id

    if not await is_admin(update, context, uid):
        return await update.message.reply_text("Admin only.")

    now = int(time.time())
    last = storage.get_tagall_last(chat_id)
    if now - last < Config.TAGALL_COOLDOWN_SEC:
        sisa = Config.TAGALL_COOLDOWN_SEC - (now - last)
        return await update.message.reply_text(f"Cooldown tagall. Coba lagi {sisa}s.")

    rows = storage.get_members(chat_id, limit=Config.TAGALL_MAX_MEMBERS)
    if not rows:
        return await update.message.reply_text("Belum ada member yang ke-track. Suruh pada chat dulu.")

    custom = " ".join(context.args).strip()
    header = custom if custom else "TagAll:"

    mentions = []
    for (mid, first_name, username) in rows:
        name = first_name or (f"@{username}" if username else "user")
        mentions.append(mention_html(int(mid), name))

    chunk_size = max(5, min(40, Config.TAGALL_CHUNK_SIZE))
    parts = [mentions[i:i + chunk_size] for i in range(0, len(mentions), chunk_size)]

    storage.set_tagall_last(chat_id, now)

    for part in parts:
        text = f"<b>{header}</b>\n" + " ".join(part)

        # prevent too long
        if len(text) > 3900:
            while len(text) > 3900 and part:
                part.pop()
                text = f"<b>{header}</b>\n" + " ".join(part)

        await update.message.reply_text(text, parse_mode="HTML", disable_web_page_preview=True)
        await asyncio.sleep(1.0)

async def cmd_tagadmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context, update.effective_user.id):
        return await update.message.reply_text("Admin only.")
    chat_id = update.effective_chat.id
    admins = await context.bot.get_chat_administrators(chat_id)
    header = " ".join(context.args).strip() or "Tag Admin:"

    mentions = []
    for a in admins:
        u = a.user
        mentions.append(mention_html(u.id, u.first_name or "admin"))

    await update.message.reply_text(
        f"<b>{header}</b>\n" + " ".join(mentions),
        parse_mode="HTML",
    )

# ---- Chatbot AI (Siputzx) ----
async def cmd_ai(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    s = storage.get_settings(chat_id)
    if not s["chatbot"]:
        return await update.message.reply_text("Chatbot AI lagi OFF. Admin nyalain di /modules → ⚙️ Settings.")

    prompt = " ".join(context.args).strip()
    if not prompt:
        return await update.message.reply_text("Usage: /ai <pertanyaan>")

    prompt = prompt[:Config.AI_MAX_CHARS]

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(Config.SIPUTZX_GPT_URL, params={"text": prompt})
            r.raise_for_status()
            data = r.json()

        ans = (data.get("data") or {}).get("content") or data.get("message") or "No response."
        ans = str(ans).strip()
        if not ans:
            ans = "AI-nya lagi diem. Coba ulang."

        if len(ans) > 3500:
            ans = ans[:3500] + "..."

        await update.message.reply_text(ans)
    except Exception as e:
        await update.message.reply_text(f"AI error: {e}")

# ----------------- Captcha / New members -----------------
async def on_new_members(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    # track joiners (for tagall)
    for u in update.message.new_chat_members:
        try:
            storage.upsert_member(chat_id, u.id, u.first_name, u.username)
        except Exception:
            pass

    settings = storage.get_settings(chat_id)
    if not settings["captcha"]:
        return  # captcha OFF => do nothing

    limits = get_limits(chat_id)

    for u in update.message.new_chat_members:
        # whitelist immunity
        if storage.is_whitelisted(chat_id, u.id):
            continue

        # mute first
        perms = await restrict_user(chat_id, u.id, can_send=False)
        await context.bot.restrict_chat_member(chat_id, u.id, permissions=perms)

        token = f"{chat_id}:{u.id}:{int(time.time())}"
        deadline = time.time() + limits.captcha_timeout
        captcha_pending[(chat_id, u.id)] = (deadline, token)

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("Saya manusia (klik)", callback_data=f"verify|{token}")]
        ])

        text = Config.CAPTCHA_TEXT.format(sec=limits.captcha_timeout)
        await update.message.reply_text(
            f"Halo {u.mention_html()}.\n{text}",
            reply_markup=kb,
            parse_mode="HTML",
        )

        context.job_queue.run_once(
            captcha_timeout_job,
            when=limits.captcha_timeout + 1,
            data={"chat_id": chat_id, "user_id": u.id, "token": token},
            name=f"captcha:{chat_id}:{u.id}",
        )

async def captcha_timeout_job(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data
    chat_id = data["chat_id"]
    user_id = data["user_id"]
    token = data["token"]

    key = (chat_id, user_id)
    if key not in captcha_pending:
        return
    deadline, saved_token = captcha_pending[key]
    if saved_token != token:
        return
    if time.time() >= deadline:
        captcha_pending.pop(key, None)
        await kick(context, chat_id, user_id, reason="Captcha timeout")

async def on_verify_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    try:
        _, token = q.data.split("|", 1)
    except Exception:
        return

    parts = token.split(":")
    if len(parts) < 3:
        return

    chat_id = int(parts[0])
    user_id = int(parts[1])

    if update.effective_user.id != user_id:
        return await q.answer("Bukan buat lu.", show_alert=True)

    key = (chat_id, user_id)
    if key not in captcha_pending:
        return await q.answer("Captcha udah gak berlaku.", show_alert=True)

    deadline, saved_token = captcha_pending[key]
    if saved_token != token:
        return await q.answer("Token mismatch.", show_alert=True)

    captcha_pending.pop(key, None)
    await unmute(context, chat_id, user_id)
    try:
        await q.edit_message_text("Verifikasi sukses. Silakan chat dengan damai.")
    except Exception:
        pass

# ----------------- Message guard -----------------
async def guard_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_chat or not update.effective_user:
        return

    chat_id = update.effective_chat.id
    user = update.effective_user
    text = (update.message.text or update.message.caption or "").strip()

    # Always track members for tagall
    try:
        storage.upsert_member(chat_id, user.id, user.first_name, user.username)
    except Exception:
        pass

    # Absolute whitelist immunity
    if storage.is_whitelisted(chat_id, user.id):
        return

    # Admin bypass (optional)
    if Config.ADMIN_BYPASS and await is_admin(update, context, user.id):
        return

    # Protection toggle
    settings = storage.get_settings(chat_id)
    if not settings["protection"]:
        return

    # Flood control
    limits = get_limits(chat_id)
    now = time.time()
    dq = flood[chat_id][user.id]
    dq.append(now)
    while dq and (now - dq[0]) > Config.FLOOD_WINDOW_SEC:
        dq.popleft()
    if len(dq) > limits.flood_max:
        dq.clear()
        await mute_for(context, chat_id, user.id, Config.FLOOD_MUTE_SEC, reason="Flood detected")
        return

    # Anti-link
    if text and Config.BLOCK_LINKS:
        is_url = bool(URL_RE.search(text))
        is_tme = bool(TME_RE.search(text)) if Config.BLOCK_TME else False
        if is_url or is_tme:
            try:
                await update.message.delete()
            except Exception:
                pass
            await mute_for(context, chat_id, user.id, 60, reason="Link blocked")
            return

    # Bad words
    if text and Config.BAD_WORDS:
        low = text.lower()
        if any(w in low for w in Config.BAD_WORDS):
            wcount = storage.inc_warn(chat_id, user.id, 1)
            try:
                await update.message.delete()
            except Exception:
                pass

            if wcount >= Config.WARN_LIMIT:
                storage.set_warn(chat_id, user.id, 0)
                await kick(context, chat_id, user.id, reason="Bad words + warn limit")
                return
            await mute_for(context, chat_id, user.id, Config.WARN_MUTE_SEC, reason=f"Bad words (warn {wcount})")
            return

def main():
    if not Config.BOT_TOKEN:
        raise SystemExit("BOT_TOKEN kosong. Set env BOT_TOKEN dulu.")

    storage.init_db()

    app = Application.builder().token(Config.BOT_TOKEN).build()

    # commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("modules", cmd_modules))
    app.add_handler(CommandHandler("ping", cmd_ping))

    app.add_handler(CommandHandler("raid", cmd_raid))

    app.add_handler(CommandHandler("warn", cmd_warn))
    app.add_handler(CommandHandler("unwarn", cmd_unwarn))
    app.add_handler(CommandHandler("warns", cmd_warns))

    app.add_handler(CommandHandler("mute", cmd_mute))
    app.add_handler(CommandHandler("unmute", cmd_unmute))

    app.add_handler(CommandHandler("allow", cmd_allow))
    app.add_handler(CommandHandler("unallow", cmd_unallow))
    app.add_handler(CommandHandler("allowlist", cmd_allowlist))

    app.add_handler(CommandHandler("tagall", cmd_tagall))
    app.add_handler(CommandHandler("tagadmin", cmd_tagadmin))

    app.add_handler(CommandHandler("ai", cmd_ai))

    # callbacks
    app.add_handler(CallbackQueryHandler(on_verify_callback, pattern=r"^verify\|"))
    app.add_handler(CallbackQueryHandler(modules_callback, pattern=r"^mod:"))
    app.add_handler(CallbackQueryHandler(settings_callback, pattern=r"^set:"))

    # new members (captcha)
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, on_new_members))

    # protection guard
    app.add_handler(MessageHandler(filters.ALL & ~filters.StatusUpdate.ALL, guard_messages))

    log.info("Bot running...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
