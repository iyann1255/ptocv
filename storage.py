import sqlite3
import time
from typing import List, Tuple

DB_PATH = "protection.db"

def _conn():
    return sqlite3.connect(DB_PATH)

def init_db():
    with _conn() as c:
        c.execute("""
        CREATE TABLE IF NOT EXISTS warns (
            chat_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            count INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (chat_id, user_id)
        );
        """)

        c.execute("""
        CREATE TABLE IF NOT EXISTS raid (
            chat_id INTEGER PRIMARY KEY,
            enabled INTEGER NOT NULL DEFAULT 0
        );
        """)

        c.execute("""
        CREATE TABLE IF NOT EXISTS whitelist (
            chat_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            PRIMARY KEY (chat_id, user_id)
        );
        """)

        c.execute("""
        CREATE TABLE IF NOT EXISTS members (
            chat_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            first_name TEXT,
            username TEXT,
            last_seen INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (chat_id, user_id)
        );
        """)

        c.execute("""
        CREATE TABLE IF NOT EXISTS tagall_cooldown (
            chat_id INTEGER PRIMARY KEY,
            last_ts INTEGER NOT NULL DEFAULT 0
        );
        """)

        # settings per chat (captcha/protection/chatbot + role)
        c.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            chat_id INTEGER PRIMARY KEY,
            captcha_enabled INTEGER NOT NULL DEFAULT 1,
            protection_enabled INTEGER NOT NULL DEFAULT 1,
            chatbot_enabled INTEGER NOT NULL DEFAULT 0,
            chatbot_role TEXT
        );
        """)

        c.commit()

# ---- Warn ----
def get_warn(chat_id: int, user_id: int) -> int:
    with _conn() as c:
        row = c.execute("SELECT count FROM warns WHERE chat_id=? AND user_id=?", (chat_id, user_id)).fetchone()
        return int(row[0]) if row else 0

def set_warn(chat_id: int, user_id: int, count: int):
    with _conn() as c:
        c.execute("""
        INSERT INTO warns(chat_id, user_id, count) VALUES(?,?,?)
        ON CONFLICT(chat_id, user_id) DO UPDATE SET count=excluded.count
        """, (chat_id, user_id, int(count)))
        c.commit()

def inc_warn(chat_id: int, user_id: int, delta: int = 1) -> int:
    current = get_warn(chat_id, user_id)
    newv = max(0, current + delta)
    set_warn(chat_id, user_id, newv)
    return newv

# ---- Raid ----
def set_raid(chat_id: int, enabled: bool):
    with _conn() as c:
        c.execute("""
        INSERT INTO raid(chat_id, enabled) VALUES(?,?)
        ON CONFLICT(chat_id) DO UPDATE SET enabled=excluded.enabled
        """, (chat_id, 1 if enabled else 0))
        c.commit()

def is_raid(chat_id: int) -> bool:
    with _conn() as c:
        row = c.execute("SELECT enabled FROM raid WHERE chat_id=?", (chat_id,)).fetchone()
        return bool(row[0]) if row else False

# ---- Whitelist ----
def is_whitelisted(chat_id: int, user_id: int) -> bool:
    with _conn() as c:
        row = c.execute("SELECT 1 FROM whitelist WHERE chat_id=? AND user_id=?", (chat_id, user_id)).fetchone()
        return bool(row)

def add_whitelist(chat_id: int, user_id: int):
    with _conn() as c:
        c.execute("INSERT OR IGNORE INTO whitelist(chat_id, user_id) VALUES(?,?)", (chat_id, user_id))
        c.commit()

def remove_whitelist(chat_id: int, user_id: int):
    with _conn() as c:
        c.execute("DELETE FROM whitelist WHERE chat_id=? AND user_id=?", (chat_id, user_id))
        c.commit()

def list_whitelist(chat_id: int) -> List[Tuple[int]]:
    with _conn() as c:
        return c.execute("SELECT user_id FROM whitelist WHERE chat_id=?", (chat_id,)).fetchall()

# ---- Members tracking ----
def upsert_member(chat_id: int, user_id: int, first_name: str | None, username: str | None):
    now = int(time.time())
    with _conn() as c:
        c.execute("""
        INSERT INTO members(chat_id, user_id, first_name, username, last_seen)
        VALUES(?,?,?,?,?)
        ON CONFLICT(chat_id, user_id) DO UPDATE SET
            first_name=excluded.first_name,
            username=excluded.username,
            last_seen=excluded.last_seen
        """, (chat_id, user_id, first_name or "", username or "", now))
        c.commit()

def get_members(chat_id: int, limit: int = 5000):
    with _conn() as c:
        return c.execute("""
        SELECT user_id, first_name, username
        FROM members
        WHERE chat_id=?
        ORDER BY last_seen DESC
        LIMIT ?
        """, (chat_id, int(limit))).fetchall()

def set_tagall_last(chat_id: int, ts: int):
    with _conn() as c:
        c.execute("""
        INSERT INTO tagall_cooldown(chat_id, last_ts)
        VALUES(?,?)
        ON CONFLICT(chat_id) DO UPDATE SET last_ts=excluded.last_ts
        """, (chat_id, int(ts)))
        c.commit()

def get_tagall_last(chat_id: int) -> int:
    with _conn() as c:
        row = c.execute("SELECT last_ts FROM tagall_cooldown WHERE chat_id=?", (chat_id,)).fetchone()
        return int(row[0]) if row else 0

# ---- Settings ----
def _ensure_settings_row(c, chat_id: int):
    c.execute("""
        INSERT INTO settings(chat_id, captcha_enabled, protection_enabled, chatbot_enabled, chatbot_role)
        VALUES(?, 1, 1, 0, NULL)
        ON CONFLICT(chat_id) DO NOTHING
    """, (chat_id,))

def get_settings(chat_id: int) -> dict:
    with _conn() as c:
        row = c.execute("""
            SELECT captcha_enabled, protection_enabled, chatbot_enabled, chatbot_role
            FROM settings WHERE chat_id=?
        """, (chat_id,)).fetchone()

        if not row:
            return {"captcha": True, "protection": True, "chatbot": False, "role": None}

        return {
            "captcha": bool(row[0]),
            "protection": bool(row[1]),
            "chatbot": bool(row[2]),
            "role": row[3],
        }

def set_setting(chat_id: int, key: str, value: bool):
    cols = {"captcha": "captcha_enabled", "protection": "protection_enabled", "chatbot": "chatbot_enabled"}
    if key not in cols:
        return
    col = cols[key]
    with _conn() as c:
        _ensure_settings_row(c, chat_id)
        c.execute(f"UPDATE settings SET {col}=? WHERE chat_id=?", (1 if value else 0, chat_id))
        c.commit()

def toggle_setting(chat_id: int, key: str) -> dict:
    s = get_settings(chat_id)
    newv = not bool(s[key])
    set_setting(chat_id, key, newv)
    return get_settings(chat_id)

def get_role(chat_id: int, default_role: str) -> str:
    s = get_settings(chat_id)
    role = (s.get("role") or "").strip()
    return role if role else (default_role or "")

def set_role(chat_id: int, role: str):
    role = (role or "").strip()
    with _conn() as c:
        _ensure_settings_row(c, chat_id)
        c.execute("UPDATE settings SET chatbot_role=? WHERE chat_id=?", (role[:3000], chat_id))
        c.commit()
