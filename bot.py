import re
import time
import asyncio
import logging
from dataclasses import dataclass
from typing import Dict, Deque, Tuple
from collections import defaultdict, deque
from urllib.parse import urlencode

import httpx
from telegram import Update, ChatPermissions, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatMemberStatus, ChatAction
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)

from config import Config
import storage

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
log = logging.getLogger("protect-bot")

BOT_START_TS = time.time()

URL_RE = re.compile(r"(https?://|www\.)\S+", re.IGNORECASE)
TME_RE = re.compile(r"(t\.me/|telegram\.me/|telegram\.dog/)\S+", re.IGNORECASE)

# Flood memory
flood: Dict[int, Dict[int, Deque[float]]] = defaultdict(lambda: defaultdict(deque))

# Captcha pending
captcha_pending: Dict[Tuple[int, int], Tuple[float, str]] = {}

# Chatbot cooldown (anti spam)
_ai_last: Dict[Tuple[int, int], float] = {}

@dataclass
class DynamicLimits:
    captcha_timeout: int
    flood_max: int

def get_limits(chat_id: int) -> DynamicLimits:
    if storage.is_raid(chat_id):
        return DynamicLimits(Config.RAID_CAPTCHA_TIMEOUT_SEC, Config.RAID_FLOOD_MAX_MSG)
    return DynamicLimits(Config.CAPTCHA_TIMEOUT_SEC, Config.FLOOD_MAX_MSG)

def fmt_uptime(seconds: int) -> str:
    seconds = max(0, int(seconds))
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    if d > 0: return f"{d}d {h}h {m}m {s}s"
    if h > 0: return f"{h}h {m}m {s}s"
    if m > 0: return f"{m}m {s}s"
    return f"{s}s"

def mention_html(user_id: int, name: str) -> str:
    safe = (name or "user").replace("<","").replace(">","")
    return f'<a href="tg://user?id={user_id}">{safe}</a>'

async def is_admin(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    try:
        cm = await context.bot.get_chat_member(update.effective_chat.id, user_id)
        return cm.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER)
    except Exception:
        return False

async def restrict_user(chat_id: int, user_id: int, can_send: bool):
    return ChatPermissions(
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

async def mute_for(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int, seconds: int, reason: str=""):
    until = int(time.time()) + seconds
    perms = await restrict_user(chat_id, user_id, False)
    await context.bot.restrict_chat_member(chat_id, user_id, permissions=perms, until_date=until)
    if reason:
        try:
            await context.bot.send_message(chat_id, f"User {user_id} dimute {seconds}s. {reason}")
        except Exception:
            pass

async def unmute(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int):
    perms = await restrict_user(chat_id, user_id, True)
    await context.bot.restrict_chat_member(chat_id, user_id, permissions=perms)

async def kick(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int, reason: str=""):
    await context.bot.ban_chat_member(chat_id, user_id)
    await context.bot.unban_chat_member(chat_id, user_id)
    if reason:
        try:
            await context.bot.send_message(chat_id, f"User {user_id} dikick. {reason}")
        except Exception:
            pass

# =========================
# Chatbot (userbot style)
# =========================
def limit_response(text: str, max_sentences: int=1, max_chars: int=15) -> str:
    if not text:
        return text
    text = text.strip()
    text = re.sub(r"^\s*#{1,6}\s+.*$", "", text, flags=re.MULTILINE).strip()
    text = re.sub(r"^\s*[-•]\s+", "", text, flags=re.MULTILINE).strip()
    parts = re.split(r"(?<=[.!?])\s+", text)
    short = " ".join(parts[:max_sentences]).strip() or text
    if len(short) > max_chars:
        short = short[:max_chars].rstrip()
    return short

def fallback_reply(_: str) -> str:
    return "wkwk"

async def call_siputzx(prompt: str, role: str) -> str | None:
    prompt = (prompt or "").strip()
    role = (role or Config.DEFAULT_ROLE).strip()
    if not prompt:
        return None

    async with httpx.AsyncClient(timeout=20) as client:
        # gpt3 primary
        try:
            params = {"prompt": role, "content": prompt}
            url = f"{Config.SIPUTZX_GPT3_URL}?{urlencode(params)}"
            r = await client.get(url)
            raw = r.text
            if r.status_code == 200:
                try:
                    js = r.json()
                except Exception:
                    return raw.strip() if raw.strip() else None

                if isinstance(js, dict):
                    val = js.get("data")
                    if isinstance(val, str) and val.strip():
                        return val.strip()
                    if isinstance(val, dict):
                        c = val.get("content")
                        if isinstance(c, str) and c.strip():
                            return c.strip()
                    for v in js.values():
                        if isinstance(v, str) and v.strip():
                            return v.strip()
            else:
                log.warning("Siputzx gpt3 non-200: %s %s", r.status_code, raw[:200])
        except Exception:
            log.exception("Error call_siputzx (gpt3)")

        # fallback
        try:
            url = f"{Config.SIPUTZX_GPT_URL}?{urlencode({'text': prompt})}"
            r = await client.get(url)
            raw = r.text
            if r.status_code != 200:
                log.warning("Siputzx fallback non-200: %s %s", r.status_code, raw[:200])
                return None
            js = r.json()
            if isinstance(js, dict):
                data_obj = js.get("data")
                if isinstance(data_obj, dict):
                    content = data_obj.get("content")
                    if isinstance(content, str) and content.strip():
                        return content.strip()
                for k in ("result", "answer", "message", "data"):
                    v = js.get(k)
                    if isinstance(v, str) and v.strip():
                        return v.strip()
            return None
        except Exception:
            log.exception("Error call_siputzx (fallback)")
            return None

# =========================
# UI
# =========================
def home_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📦 Modules", callback_data="ui:modules"),
         InlineKeyboardButton("⚙️ Settings", callback_data="ui:settings")],
        [InlineKeyboardButton("🏓 Ping", callback_data="ui:ping"),
         InlineKeyboardButton("🤖 Chatbot", callback_data="ui:chatbot")],
    ])

def back_home_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Home", callback_data="ui:home")]])

def modules_main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🛡 Protection", callback_data="mod:protection"),
         InlineKeyboardButton("👋 Captcha", callback_data="mod:captcha")],
        [InlineKeyboardButton("⚠️ Warn", callback_data="mod:warn"),
         InlineKeyboardButton("🧑‍💼 Whitelist", callback_data="mod:whitelist")],
        [InlineKeyboardButton("📢 TagAll", callback_data="mod:tagall"),
         InlineKeyboardButton("🚨 Raid", callback_data="mod:raid")],
        [InlineKeyboardButton("🤖 Chatbot", callback_data="mod:chatbot"),
         InlineKeyboardButton("🏓 Ping", callback_data="mod:ping")],
        [InlineKeyboardButton("⚙️ Settings", callback_data="mod:settings"),
         InlineKeyboardButton("🏠 Home", callback_data="ui:home")],
    ])

def module_back_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Back", callback_data="mod:main")],
        [InlineKeyboardButton("🏠 Home", callback_data="ui:home")],
    ])

def settings_keyboard(chat_id: int):
    s = storage.get_settings(chat_id)
    def onoff(v: bool): return "✅ ON" if v else "❌ OFF"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"👋 CAPTCHA ({onoff(s['captcha'])})", callback_data="set:captcha")],
        [InlineKeyboardButton(f"🛡 PROTECTION ({onoff(s['protection'])})", callback_data="set:protection")],
        [InlineKeyboardButton(f"🤖 CHATBOT ({onoff(s['chatbot'])})", callback_data="set:chatbot")],
        [InlineKeyboardButton("⬅️ Back", callback_data="mod:main"),
         InlineKeyboardButton("🏠 Home", callback_data="ui:home")],
    ])

def settings_text(chat_id: int) -> str:
    s = storage.get_settings(chat_id)
    def onoff(v: bool): return "✅ ON" if v else "❌ OFF"
    return (
        "<b>⚙️ Settings</b>\n\n"
        f"• 👋 CAPTCHA: <b>{onoff(s['captcha'])}</b>\n"
        "  └ Kunci member baru sampai verifikasi.\n\n"
        f"• 🛡 PROTECTION: <b>{onoff(s['protection'])}</b>\n"
        "  └ Anti spam (flood), anti link, filter kata.\n\n"
        f"• 🤖 CHATBOT: <b>{onoff(s['chatbot'])}</b>\n"
        "  └ Bot nimbrung jawab singkat sesuai role.\n"
        "     /setrole &lt;teks&gt;  |  /role\n\n"
        "<i>Note:</i> Bot harus admin + izin Delete/Restrict/Ban."
    )

MODULE_TEXT = {
    "protection": "<b>🛡 Protection</b>\n• Anti flood\n• Anti link\n• Badword filter\n• Auto mute/kick\n\nToggle di Settings.",
    "captcha": "<b>👋 Captcha</b>\nMember baru dimute sampai klik tombol.\nKalau timeout: auto kick.\n\nToggle di Settings.",
    "warn": "<b>⚠️ Warn</b>\n/warn (reply)\n/unwarn\n/warns (reply)\nAuto kick di limit warn.",
    "whitelist": "<b>🧑‍💼 Whitelist</b>\n/allow (reply)\n/unallow\n/allowlist\nWhitelist kebal proteksi.",
    "tagall": "<b>📢 TagAll</b>\n/tagall <teks>\n/tagadmin <teks>\nMention member yang pernah aktif.",
    "raid": "<b>🚨 Raid Mode</b>\n/raid on | /raid off\nSaat ON: captcha+flood lebih ketat.",
    "chatbot": "<b>🤖 Chatbot</b>\nToggle di Settings.\nSet role: /setrole <teks>\nCek role: /role\nJawab super singkat.",
    "ping": "<b>🏓 Ping</b>\n/ping + button Ping.",
}

# =========================
# Commands
# =========================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    chat_id = chat.id
    s = storage.get_settings(chat_id)
    def onoff(v: bool): return "✅ ON" if v else "❌ OFF"
    uptime = fmt_uptime(int(time.time() - BOT_START_TS))
    title = chat.title or "Private Chat"
    text = (
        "<b>🛡 Protection Dashboard</b>\n"
        f"• Chat: <b>{title}</b>\n"
        f"• Uptime: <b>{uptime}</b>\n\n"
        "<b>Status fitur:</b>\n"
        f"• 👋 CAPTCHA: <b>{onoff(s['captcha'])}</b> — kunci member baru sampai verifikasi\n"
        f"• 🛡 PROTECTION: <b>{onoff(s['protection'])}</b> — anti spam/link/badword\n"
        f"• 🤖 CHATBOT: <b>{onoff(s['chatbot'])}</b> — balas singkat pakai role\n\n"
        "Klik menu di bawah."
    )
    await update.message.reply_text(text, reply_markup=home_keyboard(), parse_mode="HTML")

async def cmd_ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t0 = time.time()
    msg = await update.message.reply_text("Pinging...")
    t1 = time.time()
    latency_ms = int((t1 - t0) * 1000)
    uptime = fmt_uptime(int(time.time() - BOT_START_TS))
    await msg.edit_text(f"🏓 <b>Pong</b>\n• {latency_ms} ms\n• Uptime: <b>{uptime}</b>", parse_mode="HTML")

async def cmd_raid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context, update.effective_user.id):
        return await update.message.reply_text("Admin only.")
    if len(context.args) != 1 or context.args[0].lower() not in ("on","off"):
        return await update.message.reply_text("Usage: /raid on|off")
    enabled = context.args[0].lower() == "on"
    storage.set_raid(update.effective_chat.id, enabled)
    await update.message.reply_text(f"Raid mode: {'ON' if enabled else 'OFF'}")

async def cmd_warn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context, update.effective_user.id):
        return await update.message.reply_text("Admin only.")
    if not update.message.reply_to_message:
        return await update.message.reply_text("Reply ke user yang mau di-warn.")
    target = update.message.reply_to_message.from_user
    chat_id = update.effective_chat.id
    newc = storage.inc_warn(chat_id, target.id, 1)
    if newc >= Config.WARN_LIMIT:
        storage.set_warn(chat_id, target.id, 0)
        await kick(context, chat_id, target.id, reason=f"WARN limit ({Config.WARN_LIMIT})")
        return
    await update.message.reply_text(f"Warn: {newc}/{Config.WARN_LIMIT}")

async def cmd_unwarn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context, update.effective_user.id):
        return await update.message.reply_text("Admin only.")
    if not update.message.reply_to_message:
        return await update.message.reply_text("Reply ke user yang mau dikurangin warn.")
    target = update.message.reply_to_message.from_user
    chat_id = update.effective_chat.id
    newc = storage.inc_warn(chat_id, target.id, -1)
    await update.message.reply_text(f"Warn: {newc}/{Config.WARN_LIMIT}")

async def cmd_warns(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.reply_to_message:
        return await update.message.reply_text("Reply ke user buat cek warn.")
    target = update.message.reply_to_message.from_user
    c = storage.get_warn(update.effective_chat.id, target.id)
    await update.message.reply_text(f"Warn: {c}/{Config.WARN_LIMIT}")

async def cmd_allow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context, update.effective_user.id):
        return await update.message.reply_text("Admin only.")
    if not update.message.reply_to_message:
        return await update.message.reply_text("Reply ke user yang mau di-allow.")
    target = update.message.reply_to_message.from_user
    storage.add_whitelist(update.effective_chat.id, target.id)
    await update.message.reply_text("Ok. kebal.")

async def cmd_unallow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context, update.effective_user.id):
        return await update.message.reply_text("Admin only.")
    if not update.message.reply_to_message:
        return await update.message.reply_text("Reply ke user yang mau dicabut.")
    target = update.message.reply_to_message.from_user
    storage.remove_whitelist(update.effective_chat.id, target.id)
    await update.message.reply_text("Ok.")

async def cmd_allowlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context, update.effective_user.id):
        return await update.message.reply_text("Admin only.")
    rows = storage.list_whitelist(update.effective_chat.id)
    if not rows:
        return await update.message.reply_text("Kosong.")
    await update.message.reply_text("Whitelist:\n" + "\n".join([f"- {uid}" for (uid,) in rows]))

async def cmd_tagall(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context, update.effective_user.id):
        return await update.message.reply_text("Admin only.")
    chat_id = update.effective_chat.id
    now = int(time.time())
    last = storage.get_tagall_last(chat_id)
    if now - last < Config.TAGALL_COOLDOWN_SEC:
        return await update.message.reply_text("Cooldown.")
    rows = storage.get_members(chat_id, limit=Config.TAGALL_MAX_MEMBERS)
    if not rows:
        return await update.message.reply_text("Belum ada member ke-track.")
    header = (" ".join(context.args).strip() or "TagAll:").replace("<","").replace(">","")
    mentions = []
    for (mid, first_name, username) in rows:
        name = first_name or (f"@{username}" if username else "user")
        mentions.append(mention_html(int(mid), name))
    storage.set_tagall_last(chat_id, now)
    chunk_size = max(5, min(40, Config.TAGALL_CHUNK_SIZE))
    for i in range(0, len(mentions), chunk_size):
        part = mentions[i:i+chunk_size]
        await update.message.reply_text(f"<b>{header}</b>\n" + " ".join(part), parse_mode="HTML")
        await asyncio.sleep(1.0)

async def cmd_tagadmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context, update.effective_user.id):
        return await update.message.reply_text("Admin only.")
    chat_id = update.effective_chat.id
    admins = await context.bot.get_chat_administrators(chat_id)
    header = (" ".join(context.args).strip() or "Tag Admin:").replace("<","").replace(">","")
    mentions = [mention_html(a.user.id, a.user.first_name or "admin") for a in admins]
    await update.message.reply_text(f"<b>{header}</b>\n" + " ".join(mentions), parse_mode="HTML")

async def cmd_setrole(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context, update.effective_user.id):
        return await update.message.reply_text("Admin only.")
    role = " ".join(context.args).strip()
    if not role:
        return await update.message.reply_text("Pakai: /setrole <teks>")
    storage.set_role(update.effective_chat.id, role)
    await update.message.reply_text("Ok.")

async def cmd_role(update: Update, context: ContextTypes.DEFAULT_TYPE):
    role = storage.get_role(update.effective_chat.id, Config.DEFAULT_ROLE)
    await update.message.reply_text(f"Role:\n\n{role}")

# =========================
# Callbacks
# =========================
async def ui_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    chat_id = q.message.chat.id

    if q.data == "ui:home":
        uptime = fmt_uptime(int(time.time() - BOT_START_TS))
        title = q.message.chat.title or "Private Chat"
        s = storage.get_settings(chat_id)
        def onoff(v: bool): return "✅ ON" if v else "❌ OFF"
        text = (
            "<b>🛡 Protection Dashboard</b>\n"
            f"• Chat: <b>{title}</b>\n"
            f"• Uptime: <b>{uptime}</b>\n\n"
            "<b>Status fitur:</b>\n"
            f"• 👋 CAPTCHA: <b>{onoff(s['captcha'])}</b>\n"
            f"• 🛡 PROTECTION: <b>{onoff(s['protection'])}</b>\n"
            f"• 🤖 CHATBOT: <b>{onoff(s['chatbot'])}</b>\n"
        )
        return await q.edit_message_text(text, reply_markup=home_keyboard(), parse_mode="HTML")

    if q.data == "ui:modules":
        return await q.edit_message_text(
            "<b>📦 Modules</b>\nKlik modul buat detail:",
            reply_markup=modules_main_keyboard(),
            parse_mode="HTML",
        )

    if q.data == "ui:settings":
        if not await is_admin(update, context, update.effective_user.id):
            return await q.answer("Admin only.", show_alert=True)
        return await q.edit_message_text(settings_text(chat_id), reply_markup=settings_keyboard(chat_id), parse_mode="HTML")

    if q.data == "ui:ping":
        uptime = fmt_uptime(int(time.time() - BOT_START_TS))
        return await q.edit_message_text(f"🏓 <b>ON</b>\n• Uptime: <b>{uptime}</b>", reply_markup=back_home_keyboard(), parse_mode="HTML")

    if q.data == "ui:chatbot":
        s = storage.get_settings(chat_id)
        state = "✅ ON" if s["chatbot"] else "❌ OFF"
        role_preview = storage.get_role(chat_id, Config.DEFAULT_ROLE)
        if len(role_preview) > 140:
            role_preview = role_preview[:140] + "..."
        return await q.edit_message_text(
            "<b>🤖 Chatbot (Userbot Style)</b>\n\n"
            f"• Status: <b>{state}</b>\n\n"
            "Set role: /setrole &lt;teks&gt;\n"
            "Cek role: /role\n\n"
            f"<b>Role sekarang:</b>\n<code>{role_preview.replace('<','').replace('>','')}</code>",
            reply_markup=back_home_keyboard(),
            parse_mode="HTML",
        )

async def modules_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if q.data == "mod:main":
        return await q.edit_message_text(
            "<b>📦 Modules</b>\nKlik modul buat detail:",
            reply_markup=modules_main_keyboard(),
            parse_mode="HTML",
        )

    if q.data == "mod:settings":
        if not await is_admin(update, context, update.effective_user.id):
            return await q.answer("Admin only.", show_alert=True)
        chat_id = q.message.chat.id
        return await q.edit_message_text(settings_text(chat_id), reply_markup=settings_keyboard(chat_id), parse_mode="HTML")

    if q.data.startswith("mod:"):
        key = q.data.split(":", 1)[1]
        text = MODULE_TEXT.get(key, "N/A")
        return await q.edit_message_text(text, reply_markup=module_back_keyboard(), parse_mode="HTML")

async def settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    chat_id = q.message.chat.id

    if not await is_admin(update, context, update.effective_user.id):
        return await q.answer("Admin only.", show_alert=True)

    key = q.data.split(":", 1)[1]
    storage.toggle_setting(chat_id, key)

    await q.answer("Updated ✅")
    return await q.edit_message_text(settings_text(chat_id), reply_markup=settings_keyboard(chat_id), parse_mode="HTML")

# Captcha verify
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
        return await q.answer("Captcha udah lewat.", show_alert=True)

    deadline, saved_token = captcha_pending[key]
    if saved_token != token:
        return await q.answer("Token mismatch.", show_alert=True)

    captcha_pending.pop(key, None)
    await unmute(context, chat_id, user_id)
    try:
        await q.edit_message_text("Ok.")
    except Exception:
        pass

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

# =========================
# Events
# =========================
async def on_new_members(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    for u in update.message.new_chat_members:
        try:
            storage.upsert_member(chat_id, u.id, u.first_name, u.username)
        except Exception:
            pass

    s = storage.get_settings(chat_id)
    if not s["captcha"]:
        return

    limits = get_limits(chat_id)

    for u in update.message.new_chat_members:
        if storage.is_whitelisted(chat_id, u.id):
            continue

        perms = await restrict_user(chat_id, u.id, False)
        await context.bot.restrict_chat_member(chat_id, u.id, permissions=perms)

        token = f"{chat_id}:{u.id}:{int(time.time())}"
        deadline = time.time() + limits.captcha_timeout
        captcha_pending[(chat_id, u.id)] = (deadline, token)

        kb = InlineKeyboardMarkup([[InlineKeyboardButton("Saya manusia", callback_data=f"verify|{token}")]])
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

# Chatbot handler (FIX: only TEXT, no filters.Caption)
async def chatbot_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_chat:
        return
    msg = update.message
    chat_id = update.effective_chat.id

    if msg.from_user and msg.from_user.is_bot:
        return

    cfg = storage.get_settings(chat_id)
    if not cfg["chatbot"]:
        return

    user_text = (msg.text or "").strip()
    if not user_text:
        return
    if user_text.startswith("/"):
        return

    key = (chat_id, msg.from_user.id if msg.from_user else 0)
    now = time.time()
    if now - _ai_last.get(key, 0) < 4:
        return
    _ai_last[key] = now

    if Config.STRIP_BOT_MENTION:
        bot_username = (context.bot.username or "").strip()
        if bot_username:
            user_text = re.sub(rf"@{re.escape(bot_username)}\b", "", user_text, flags=re.IGNORECASE).strip()

    role = storage.get_role(chat_id, Config.DEFAULT_ROLE)

    try:
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        try:
            answer = await asyncio.wait_for(call_siputzx(user_text, role), timeout=Config.FAST_TIMEOUT)
        except asyncio.TimeoutError:
            answer = None

        if not answer:
            answer = fallback_reply(user_text)

        answer = limit_response(answer, max_sentences=Config.MAX_SENTENCES, max_chars=Config.MAX_CHARS)
        await msg.reply_text(answer, disable_web_page_preview=True)
    except Exception as e:
        log.exception("chatbot error", exc_info=e)

# Protection guard
async def guard_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_chat or not update.effective_user:
        return

    chat_id = update.effective_chat.id
    user = update.effective_user
    text = (update.message.text or update.message.caption or "").strip()

    try:
        storage.upsert_member(chat_id, user.id, user.first_name, user.username)
    except Exception:
        pass

    if storage.is_whitelisted(chat_id, user.id):
        return

    if Config.ADMIN_BYPASS and await is_admin(update, context, user.id):
        return

    s = storage.get_settings(chat_id)
    if not s["protection"]:
        return

    limits = get_limits(chat_id)
    now = time.time()
    dq = flood[chat_id][user.id]
    dq.append(now)
    while dq and (now - dq[0]) > Config.FLOOD_WINDOW_SEC:
        dq.popleft()
    if len(dq) > limits.flood_max:
        dq.clear()
        await mute_for(context, chat_id, user.id, Config.FLOOD_MUTE_SEC, reason="Flood")
        return

    if text and Config.BLOCK_LINKS:
        is_url = bool(URL_RE.search(text))
        is_tme = bool(TME_RE.search(text)) if Config.BLOCK_TME else False
        if is_url or is_tme:
            try:
                await update.message.delete()
            except Exception:
                pass
            await mute_for(context, chat_id, user.id, 60, reason="Link")
            return

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
                await kick(context, chat_id, user.id, reason="Badwords")
                return
            await mute_for(context, chat_id, user.id, Config.WARN_MUTE_SEC, reason=f"Badwords warn {wcount}")
            return

async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    log.exception("Unhandled error", exc_info=context.error)

def main():
    if not Config.BOT_TOKEN:
        raise SystemExit("BOT_TOKEN kosong. Isi .env BOT_TOKEN dulu.")

    storage.init_db()

    app = Application.builder().token(Config.BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("ping", cmd_ping))
    app.add_handler(CommandHandler("raid", cmd_raid))

    app.add_handler(CommandHandler("warn", cmd_warn))
    app.add_handler(CommandHandler("unwarn", cmd_unwarn))
    app.add_handler(CommandHandler("warns", cmd_warns))

    app.add_handler(CommandHandler("allow", cmd_allow))
    app.add_handler(CommandHandler("unallow", cmd_unallow))
    app.add_handler(CommandHandler("allowlist", cmd_allowlist))

    app.add_handler(CommandHandler("tagall", cmd_tagall))
    app.add_handler(CommandHandler("tagadmin", cmd_tagadmin))

    app.add_handler(CommandHandler("setrole", cmd_setrole))
    app.add_handler(CommandHandler("role", cmd_role))

    app.add_handler(CallbackQueryHandler(on_verify_callback, pattern=r"^verify\|"))
    app.add_handler(CallbackQueryHandler(ui_callback, pattern=r"^ui:"))
    app.add_handler(CallbackQueryHandler(modules_callback, pattern=r"^mod:"))
    app.add_handler(CallbackQueryHandler(settings_callback, pattern=r"^set:"))

    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, on_new_members))

    # FIX: only TEXT, avoid filters.Caption bug
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chatbot_handler), group=0)
    app.add_handler(MessageHandler(filters.ALL & ~filters.StatusUpdate.ALL, guard_messages), group=1)

    app.add_error_handler(on_error)

    log.info("Bot running...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
