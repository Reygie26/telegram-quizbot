####################################################################################################################################################################################################################################
# CODE BY PARTS - PART 1 (START OF CODE)
####################################################################################################################################################################################################################################
# TeleQuiz.py
# FULL STABLE VERSION – TIMER & SHUFFLE FIXED
# All Edit buttons now open real menus

import asyncio
import uuid
import sqlite3
import os
import secrets
import time
import datetime

from telegram.ext import ApplicationHandlerStop
from telegram import InputMediaPhoto
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardRemove,
)

from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

import random
from difflib import SequenceMatcher

from google import genai as google_genai
import base64

GEMINI_API_KEY = "AIzaSyCRQ6CvyI2iYJF3pD_7MsVGaXQAH5uSZf0"
GEMINI_CLIENT = google_genai.Client(api_key=GEMINI_API_KEY)

##### =============================================================================================
##### BOT TOKEN TO USE
##### =============================================================================================
##### BOT TOKEN TO USE FOR GITHUB    - BOT_TOKEN = os.environ.get("BOT_TOKEN")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("❌ BOT_TOKEN environment variable is missing")

##### =============================================================================================
##### OWNER USER ID TO USE
##### =============================================================================================
##### OWNER USER ID FOR GITHUB       - OWNER_USER_ID = int(os.getenv("OWNER_USER_ID"))
OWNER_USER_ID = int(os.getenv("OWNER_USER_ID"))

##### =============================================================================================
##### BOT USERNAME TO USE
##### =============================================================================================
##### BOT USERNAME FOR GITHUB        - BOT_USERNAME = "EucresiaBot"
BOT_USERNAME = "EucresiaBot"

##### =============================================================================================
##### DB_FILE TO USE
##### =============================================================================================
##### DB_FILE TO USE FOR GITHUB      - DB_FILE = "/var/data/quizbot.db"
DB_FILE = "/var/data/quizbot.db"

##### =============================================================================================
##### print("📂 Using database file at:", DB_FILE)
##### =============================================================================================
##### FOR TEST BOT                   - (empty)
##### FOR GITHUB                     - print("📂 Using database file at:", DB_FILE)
print("📂 Using database file at:", DB_FILE)

##### =============================================================================================
##### CONFIGURATION
##### =============================================================================================
QUESTIONS_PER_PAGE = 10
QUIZ_FOLDERS_PER_PAGE = 5
PLACEHOLDER_IMAGE_URL = "https://via.placeholder.com/1x1.png"
PLACEHOLDER_IMAGE_FILE_ID = "AgACAgUAAxkBAAId1GmNwdjStLkxKCsKAodhZXjm9Fc5AAKJDGsbhHpxVPfj2MXOcpF3AQADAgADeQADOgQ"
DB_LOCK = asyncio.Lock()
MAX_QUESTION_LENGTH = 400
MAX_OPTION_LENGTH = 200
MAX_EXPLANATION_LENGTH = 400
MAX_TITLE_LENGTH = 50
MAX_DESC_LENGTH = 100
MAX_FOLDER_NAME_LENGTH = 30
SUBSCRIPTION_DURATIONS = {
    "Lifetime":   0,
    "1 Year":     365 * 24 * 3600,
    "6 Months":   183 * 24 * 3600,
    "1 Month":    30  * 24 * 3600,
    "1 Week":     7   * 24 * 3600,
    "1 Day":      1   * 24 * 3600,
}

## =========================
## START OF CODE
## =========================

## =========================
## HELPERS
## =========================

# =========================
# OWNER RESTORE
# =========================
def load_owner_from_db():
    global OWNER_USER_ID
    _conn, _cur = get_db()
    _cur.execute("SELECT owner_id FROM quizzes LIMIT 1")
    row = _cur.fetchone()
    _conn.close()
    if row:
        OWNER_USER_ID = row[0]

def ensure_default_folder():
    _conn, _cur = get_db()
    _cur.execute(
        "INSERT OR IGNORE INTO folders (owner_id, name) VALUES (?, 'Default')",
        (OWNER_USER_ID,)
    )
    _conn.commit()
    _conn.close()

def ensure_indexes():
    _conn, _cur = get_db()
    _cur.execute("CREATE INDEX IF NOT EXISTS idx_ql_quiz_id ON quiz_question_links(quiz_id)")
    _cur.execute("CREATE INDEX IF NOT EXISTS idx_ql_question_id ON quiz_question_links(question_id)")
    _cur.execute("CREATE INDEX IF NOT EXISTS idx_ql_quiz_question ON quiz_question_links(quiz_id, question_id)")
    _cur.execute("CREATE INDEX IF NOT EXISTS idx_qb_folder_id ON question_bank(folder_id)")
    _cur.execute("CREATE INDEX IF NOT EXISTS idx_quizzes_owner_folder ON quizzes(owner_id, folder)")
    _cur.execute("CREATE INDEX IF NOT EXISTS idx_leaderboard_quiz_chat ON leaderboard(quiz_id, chat_id)")
    _conn.commit()
    _conn.close()

def restore_group_lb_messages():
    _conn, _cur = get_db()
    _cur.execute("""
        SELECT leaderboard_key, quiz_id, token, chat_id, message_id, page, inline_message_id, show_score
        FROM group_lb_messages
    """)
    rows = _cur.fetchall()
    _conn.close()

    restored = 0
    for leaderboard_key, quiz_id, token, chat_id, message_id, page, inline_message_id, show_score in rows:
        # Rebuild the key from quiz_id + token to guarantee format consistency
        rebuilt_key = make_leaderboard_key(quiz_id, token)

        GROUP_LB_MESSAGES[rebuilt_key] = {
            "quiz_id":           quiz_id,
            "token":             token,
            "chat_id":           chat_id,
            "message_id":        message_id,
            "page":              page,
            "inline_message_id": inline_message_id,
            "show_score":        1 if show_score is None else show_score,
        }
        restored += 1

    print(f"✅ Restored {restored} leaderboard message(s) from DB.")

def fix_leaderboard_key_format():
    """One-time fix: ensures all group_lb_messages keys use quiz_id:token format."""
    _conn, _cur = get_db()
    _cur.execute("SELECT leaderboard_key, quiz_id, token FROM group_lb_messages")
    rows = _cur.fetchall()
    fixed = 0
    for key, quiz_id, token in rows:
        expected = make_leaderboard_key(quiz_id, token)
        if key != expected:
            _cur.execute(
                "UPDATE group_lb_messages SET leaderboard_key=? WHERE leaderboard_key=?",
                (expected, key)
            )
            fixed += 1
    if fixed:
        _conn.commit()
        print(f"🔧 Fixed {fixed} leaderboard key(s).")
    else:
        print("✅ All leaderboard keys are correct.")
    _conn.close()



# =========================
# NAME FORMATTER IN QUIZ LEADERBOARD
# =========================
def format_user_name(user):
    """
    Format name as: Lastname, Firstname
    Example: Cayleen Astrid Gorgonio -> Gorgonio, Cayleen
    """
    if not user:
        return "Unknown"

    first = (user.first_name or "").strip()
    last = (user.last_name or "").strip()

    # Take only the FIRST word of first name
    first_word = first.split()[0] if first else ""

    if last and first_word:
        return f"{last}, {first_word}"
    elif first_word:
        return first_word
    elif last:
        return last
    else:
        return "Unknown"

import re

def natural_sort_key(s):
    """
    Splits a string into text and integer chunks so that
    'Quiz 2' sorts before 'Quiz 10'.
    """
    return [
        int(chunk) if chunk.isdigit() else chunk.lower()
        for chunk in re.split(r'(\d+)', s)
    ]

# =========================
# GEMINI IMAGE SCANNER
# =========================
async def scan_image_with_gemini(file_bytes: bytes):
    import json

    prompt = """You are a quiz question extractor. Analyze this image and extract:
1. The full question text
2. All answer options (there should be exactly 4, labeled A/B/C/D or 1/2/3/4 or similar)

Respond ONLY with a valid JSON object in this exact format, no explanation, no markdown:
{
  "question": "the full question text here",
  "options": ["option A text", "option B text", "option C text", "option D text"]
}

Rules:
- If no clear question is found, set "question" to ""
- If options are missing or fewer than 4, fill missing ones with ""
- Strip any leading labels like "A.", "1.", "(a)" from option text
- Preserve the original wording exactly, including punctuation
- Do NOT include the correct answer indicator"""

    try:
        loop = asyncio.get_event_loop()

        def _call_gemini():
            response = GEMINI_CLIENT.models.generate_content(
                model="gemini-1.5-flash-latest",
                contents=[
                    google_genai.types.Part.from_bytes(
                        data=file_bytes,
                        mime_type="image/jpeg",
                    ),
                    prompt,
                ]
            )
            return response.text

        raw = await loop.run_in_executor(None, _call_gemini)

        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        raw = raw.strip()

        data = json.loads(raw)

        question = (data.get("question") or "").strip()
        options  = [str(o).strip() for o in (data.get("options") or [])]

        while len(options) < 4:
            options.append("")

        options = options[:4]

        valid_options = [o for o in options if o]

        return question, valid_options

    except Exception as e:
        print(f"⚠️ Gemini scan error: {e}")
        return "", []

def get_active_user_id(context) -> int:
    """
    Returns the logged-in user's ID from session.
    Falls back to OWNER_USER_ID if not set (safety net).
    """
    return context.user_data.get("active_user_id", OWNER_USER_ID)

def verify_quiz_owner(quiz_id: str, context) -> bool:
    _conn, _cur = get_db()
    _cur.execute(
        "SELECT 1 FROM quizzes WHERE quiz_id=? AND owner_id=?",
        (quiz_id, get_active_user_id(context))
    )
    row = _cur.fetchone()
    _conn.close()
    return row is not None

def verify_question_owner(question_id: int, context) -> bool:
    _conn, _cur = get_db()
    _cur.execute(
        """
        SELECT 1 FROM question_bank qb
        JOIN question_bank_folders f ON f.id = qb.folder_id
        WHERE qb.id=? AND f.owner_id=?
        """,
        (question_id, get_active_user_id(context))
    )
    row = _cur.fetchone()
    _conn.close()
    return row is not None

def is_authorized(user_id: int) -> bool:
    if user_id == OWNER_USER_ID:
        return True
    now = int(time.time())
    _conn, _cur = get_db()
    _cur.execute("""
        SELECT subscription_type, expires_at, is_active
        FROM subscribers
        WHERE user_id = ?
    """, (user_id,))
    row = _cur.fetchone()
    _conn.close()
    if not row:
        return False
    subscription_type, expires_at, is_active = row
    if not is_active:
        return False
    if subscription_type == "Lifetime":
        return True
    return expires_at > now

# =========================
# LEADERBOARD KEY HELPER
# =========================
def make_leaderboard_key(quiz_id: str, token: str) -> str:
    """
    Unique identifier for ONE posted quiz instance.
    Format: <quiz_id>:<token>
    """
    return f"{quiz_id}:{token}"

# =========================
# GLOBAL "QUIZ IS ACTIVE" CHECKER
# =========================
def is_quiz_active(context):
    return "play" in context.user_data

# =========================
# GROUP QUIZ STATE (IN-MEMORY)
# =========================
GROUP_QUIZZES = {}      # inline_message_id -> quiz_id

GROUP_LEADERBOARDS = {}  # quiz_id -> {
                         #   user_id: {
                         #       "name": str,
                         #       "score": int,
                         #       "answered": int
                         #   }
                         # }

GROUP_LB_MESSAGES = {}   # quiz_id -> {
                         #   "chat_id": int,
                         #   "message_id": int,
                         #   "page": int
                         # }

USER_RATE_LIMIT = {}
RATE_LIMIT_SECONDS = 1
ONE_YEAR_SECONDS = 365 * 24 * 3600  # 1-year token validity

# =========================
# DATABASE
# =========================
def get_db():
    """
    Returns a fresh (conn, cur) pair for each call.
    SQLite allows multiple connections to the same file.
    Using per-operation connections eliminates shared-cursor
    data corruption in async code.
    """
    connection = sqlite3.connect(DB_FILE, check_same_thread=False)
    connection.execute("PRAGMA journal_mode=WAL")
    cursor = connection.cursor()
    return connection, cursor

# =========================
# DATABASE SCHEMA SETUP
# =========================
def _setup_schema():
    _conn, _cur = get_db()

    _cur.execute("""
CREATE TABLE IF NOT EXISTS leaderboard (
    quiz_id TEXT,
    chat_id INTEGER,
    user_id INTEGER,
    username TEXT,
    score INTEGER,
    PRIMARY KEY (quiz_id, chat_id, user_id)
)
""")
    _conn.commit()

    _cur.execute("""
CREATE TABLE IF NOT EXISTS question_bank (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    folder_id INTEGER,
    question TEXT,
    image_file_id TEXT,
    options TEXT,
    correct INTEGER,
    explanation TEXT
)
""")

    _cur.execute("""
CREATE TABLE IF NOT EXISTS quiz_question_links (
    quiz_id TEXT,
    question_id INTEGER,
    position INTEGER,
    PRIMARY KEY (quiz_id, question_id)
)
""")
    _conn.commit()

    _cur.execute("""
CREATE TABLE IF NOT EXISTS quizzes (
    quiz_id TEXT PRIMARY KEY,
    owner_id INTEGER,
    title TEXT,
    description TEXT,
    folder TEXT DEFAULT 'Default',
    shuffle_q INTEGER,
    shuffle_a INTEGER,
    timer INTEGER
)
""")

    _cur.execute("""
CREATE TABLE IF NOT EXISTS folders (
    owner_id INTEGER,
    name TEXT,
    UNIQUE(owner_id, name)
)
""")
    _conn.commit()

    _cur.execute("""
CREATE TABLE IF NOT EXISTS question_bank_folders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id INTEGER,
    name TEXT,
    UNIQUE(owner_id, name)
)
""")

    _cur.execute("""
CREATE TABLE IF NOT EXISTS quiz_post_tokens (
    token TEXT PRIMARY KEY,
    quiz_id TEXT,
    owner_id INTEGER,
    created_at INTEGER
)
""")
    _conn.commit()

    _cur.execute("""
CREATE TABLE IF NOT EXISTS group_leaderboard (
    leaderboard_key TEXT,
    user_id INTEGER,
    name TEXT,
    score INTEGER,
    PRIMARY KEY (leaderboard_key, user_id)
)
""")
    _conn.commit()

    _cur.execute("""
CREATE TABLE IF NOT EXISTS group_lb_messages (
    leaderboard_key  TEXT PRIMARY KEY,
    quiz_id          TEXT,
    token            TEXT,
    chat_id          INTEGER,
    message_id       INTEGER,
    page             INTEGER DEFAULT 0,
    inline_message_id TEXT
)
""")
    _conn.commit()

    # Safe migration — ignored if column already exists
    try:
        _cur.execute("ALTER TABLE group_lb_messages ADD COLUMN inline_message_id TEXT")
        _conn.commit()
    except Exception:
        pass

    # Safe migration for show_score column
    try:
        _cur.execute("ALTER TABLE group_lb_messages ADD COLUMN show_score INTEGER DEFAULT 1")
        _conn.commit()
    except Exception:
        pass

    _cur.execute("""
CREATE TABLE IF NOT EXISTS subscribers (
    user_id INTEGER PRIMARY KEY,
    name TEXT,
    subscription_type TEXT,
    expires_at INTEGER,
    is_active INTEGER DEFAULT 1,
    subscribed_at INTEGER DEFAULT 0
)
""")
    _conn.commit()

    # Safe column migrations — ignored if columns already exist
    for sql in [
        "ALTER TABLE subscribers ADD COLUMN subscribed_at INTEGER DEFAULT 0",
        "ALTER TABLE subscribers ADD COLUMN needs_notice INTEGER DEFAULT 0",
    ]:
        try:
            _cur.execute(sql)
            _conn.commit()
        except Exception:
            pass

    _conn.close()

_setup_schema()

# =========================
# UI
# =========================

# =========================
# START
# =========================

# 🟢 GROUP QUIZ POST DETECTION
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat = update.effective_chat
    chat_type = chat.type

    # 🔥 Track /start message itself
    if update.message:
        context.user_data.setdefault("chat_messages", []).append(update.message.message_id)

    # 🎮 PLAY MODE (deep link from group post)
    if context.args and context.args[0].startswith("PLAY_"):
        try:
            _, quiz_id, token = context.args[0].split("_", 2)
        except ValueError:
            msg = await update.message.reply_text("❌ Invalid quiz link.")
            context.user_data.setdefault("chat_messages", []).append(msg.message_id)
            return

        # 🔑 Build leaderboard key
        leaderboard_key = make_leaderboard_key(quiz_id, token)

        # 🔍 Verify token exists in DB and is within 1 year
        _conn_t, _cur_t = get_db()
        _cur_t.execute(
            "SELECT created_at FROM quiz_post_tokens WHERE token=? AND quiz_id=?",
            (token, quiz_id)
        )
        token_row = _cur_t.fetchone()
        _conn_t.close()

        now = int(time.time())

        if not token_row or (now - token_row[0]) > ONE_YEAR_SECONDS:
            msg = await update.message.reply_text("❌ This quiz link is no longer valid.")
            async def _delete_invalid():
                await asyncio.sleep(5)
                try:
                    await msg.delete()
                except:
                    pass
            asyncio.create_task(_delete_invalid())
            return

        # 🔍 Verify leaderboard exists in memory
        lb_info = GROUP_LB_MESSAGES.get(leaderboard_key)

        # ── Fallback: rebuild from DB — retry up to 5× with 1s delay
        # (handles race condition where send_quiz_to_group hasn't committed yet)
        if not lb_info:
            db_row = None
            for attempt in range(5):
                _conn_lb, _cur_lb = get_db()
                _cur_lb.execute(
                    """
                    SELECT quiz_id, token, chat_id, message_id, page
                    FROM group_lb_messages
                    WHERE leaderboard_key = ?
                    """,
                    (leaderboard_key,)
                )
                db_row = _cur_lb.fetchone()
                _conn_lb.close()
                if db_row:
                    break
                await asyncio.sleep(1)  # wait 1s and retry

            if db_row:
                r_quiz_id, r_token, r_chat_id, r_message_id, r_page = db_row
                lb_info = {
                    "quiz_id":    r_quiz_id,
                    "token":      r_token,
                    "chat_id":    r_chat_id,
                    "message_id": r_message_id,
                    "page":       r_page,
                }
                GROUP_LB_MESSAGES[leaderboard_key] = lb_info
            else:
                msg = await update.message.reply_text("❌ This quiz link is no longer valid.")
                async def _delete_invalid():
                    await asyncio.sleep(5)
                    try:
                        await msg.delete()
                    except:
                        pass
                asyncio.create_task(_delete_invalid())
                return

        # 🔒 Reset user state completely
        context.user_data.clear()
        context.user_data["chat_messages"] = []

        # 🔥 Track /start again after clear
        if update.message:
            context.user_data["chat_messages"].append(update.message.message_id)

        # =========================
        # BIND PLAYER → LEADERBOARD
        # =========================
        context.user_data["play_quiz_id"] = quiz_id
        context.user_data["play_token"] = token
        context.user_data["leaderboard_key"] = leaderboard_key
        context.user_data["group_chat_id"] = lb_info["chat_id"]

        # 🔍 Fetch quiz title from DB
        _conn, _cur = get_db()
        _cur.execute("SELECT title FROM quizzes WHERE quiz_id=?", (quiz_id,))
        row = _cur.fetchone()
        _conn.close()
        quiz_title = row[0] if row else "Quiz"

        msg = await update.message.reply_text(
            f"🎮 *Quiz Ready!*\n"
            f"📘 *{quiz_title}*\n\n"
            f"Press the button below to start.",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("▶️ Start Quiz", callback_data="PLAY_START"),
                    InlineKeyboardButton("❌ Cancel",     callback_data="CANCEL_PLAY_READY"),
                ]
            ]),
            parse_mode="Markdown"
        )

        context.user_data["chat_messages"].append(msg.message_id)
        return

    # ⚙️ QUIZ ADMIN MODE (creator tapped Quiz Admin button from a group post)
    if context.args and context.args[0].startswith("QA_"):
        # Only works in private chat
        if chat_type in ("group", "supergroup", "channel"):
            return

        # Format: QA_<quiz_id>_<token>
        # quiz_id is a UUID (contains hyphens), token is hex
        # Split at the LAST underscore to isolate the token
        try:
            payload = context.args[0][len("QA_"):]
            last_underscore = payload.rfind("_")
            if last_underscore == -1:
                raise ValueError("No underscore found")
            quiz_id = payload[:last_underscore]
            token   = payload[last_underscore + 1:]
        except (ValueError, IndexError):
            return

        # 🔒 Only the quiz CREATOR (owner) can access Quiz Admin
        _conn_o, _cur_o = get_db()
        _cur_o.execute("SELECT owner_id FROM quizzes WHERE quiz_id=?", (quiz_id,))
        owner_row = _cur_o.fetchone()
        _conn_o.close()

        if not owner_row or owner_row[0] != user_id:
            msg = await update.message.reply_text(
                "❌ Only the quiz creator can access Quiz Admin."
            )
            async def _delete_denial():
                await asyncio.sleep(5)
                try:
                    await msg.delete()
                except:
                    pass
                try:
                    await update.message.delete()
                except:
                    pass
            asyncio.create_task(_delete_denial())
            return

        # ✅ Authorized — build and send the Quiz Admin panel
        leaderboard_key = make_leaderboard_key(quiz_id, token)
        info = GROUP_LB_MESSAGES.get(leaderboard_key, {})
        show_score = info.get("show_score", 1)

        text = _build_qa_panel_text(quiz_id)
        keyboard = _build_qa_panel_keyboard(leaderboard_key, show_score)

        # 🔑 Set context so Timer/Shuffle edit handlers work if triggered from this panel
        context.user_data["active_quiz_id"] = quiz_id

        msg = await update.message.reply_text(
            text,
            reply_markup=keyboard,
            parse_mode="Markdown"
        )

        # 🔑 Store so show_quiz_action_menu_by_id can update this message after edits
        context.user_data["quiz_overview_msg_id"] = msg.message_id
        context.user_data.setdefault("chat_messages", []).append(msg.message_id)
        return

    # 📤 GROUP POST MODE (admin shared the startgroup link into a group)
    if context.args and context.args[0].startswith("POST_"):
        if chat_type not in ("group", "supergroup"):
            return

        # Format: POST_<quiz_id>_<token>
        # quiz_id is a UUID (contains hyphens, no underscores)
        # token is token_hex (only hex chars, no underscores)
        # So splitting on "_" gives exactly: ["POST", "<uuid-part1>", ..., "<token>"]
        # We join everything after "POST_" and split at the LAST underscore to isolate the token
        try:
            payload = context.args[0][len("POST_"):]   # strip the "POST_" prefix
            last_underscore = payload.rfind("_")
            if last_underscore == -1:
                raise ValueError("No underscore found")
            quiz_id = payload[:last_underscore]
            token   = payload[last_underscore + 1:]
        except (ValueError, IndexError):
            return

        # 🔒 Only authorized users can trigger a post
        if not is_authorized(user_id):
            return

        # 🔍 Verify token exists and belongs to this user
        _conn_t, _cur_t = get_db()
        _cur_t.execute(
            "SELECT owner_id FROM quiz_post_tokens WHERE token=? AND quiz_id=?",
            (token, quiz_id)
        )
        token_row = _cur_t.fetchone()
        _conn_t.close()

        if not token_row or token_row[0] != user_id:
            return

        # ✅ Post the quiz to this group
        await send_quiz_to_group(chat.id, quiz_id, context, token)

        # 🧹 Delete the /start trigger message
        try:
            await update.message.delete()
        except:
            pass
        return

    # ❌ Block /start inside groups & channels
    if chat_type in ("group", "supergroup", "channel"):
        return

    # 🔒 Private chat but NOT owner — check subscriber access
    if user_id != OWNER_USER_ID:
        if not is_authorized(user_id):
            msg = await update.message.reply_text(
                "👋 Hi!\n\nYou don't have Admin access to this Bot yet. To begin, open a Quiz posted in a group and start answering or avail Admin access.\n\nTo avail of Admin access, please contact the Bot creator on Telegram:\nReygie Marimon Gorgonio\nContact No. : 0928 180 2793\nTelegram      : @Eucresia\n\nTeleQuiz Bot Official links\nChannel : https://t.me/Bot_TeleQuiz\nGroup    : https://t.me/+pbioRS0BWN4wZjM9\n\nYou can also DM the Official Channel to avail Admin Access to the Bot"
            )
            context.user_data.setdefault("chat_messages", []).append(msg.message_id)
            return
        # ✅ Authorized subscriber — show admin panel
        context.user_data.clear()
        context.user_data["active_user_id"] = user_id
        context.user_data["chat_messages"] = []
        if update.message:
            context.user_data["chat_messages"].append(update.message.message_id)

        # 🔔 Check if this subscriber needs to see the first-access notice
        _conn_n, _cur_n = get_db()
        _cur_n.execute(
            "SELECT needs_notice FROM subscribers WHERE user_id=?",
            (user_id,)
        )
        notice_row = _cur_n.fetchone()
        _conn_n.close()
        needs_notice = notice_row and notice_row[0] == 1

        if needs_notice:
            notice_keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ I Agree", callback_data="SUB_AGREE_NOTICE")]
            ])
            msg = await update.message.reply_text(
                "🧠 **Welcome to TeleQuiz (Admin Panel)**\n\n"
                "⚠️ *Important Notice*\n\n"
                "All folders, quizzes, and questions you create are tied to your subscription. "
                "If your subscription becomes inactive and is not renewed within 1 year, "
                "all your data will be permanently and automatically deleted.\n\n"
                "Please tap I Agree to continue.",
                reply_markup=notice_keyboard,
                parse_mode="Markdown"
            )
            context.user_data["chat_messages"].append(msg.message_id)
            return

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("📂 Quiz Folder", callback_data="HOME_MY_QUIZZES"),
                InlineKeyboardButton("➕ Create a new Quiz", callback_data="HOME_CREATE"),
            ],
            [
                InlineKeyboardButton("🗄 Database", callback_data="HOME_DATABASE"),
                InlineKeyboardButton("❓ Create a Question", callback_data="HOME_CREATE_QUESTION"),
            ],
        ])

        msg = await update.message.reply_text(
            "🧠 Welcome to TeleQuiz (Admin Panel)\n\nPlease choose an option to start 👇:",
            reply_markup=keyboard,
            parse_mode="Markdown"
        )
        context.user_data["chat_messages"].append(msg.message_id)
        return

    # ✅ OWNER — show admin home
    # 🔒 HARD RESET: entering admin mode must clear play state
    context.user_data.clear()
    context.user_data["active_user_id"] = user_id
    context.user_data["chat_messages"] = []

    # 🔥 Track /start again after reset
    if update.message:
        context.user_data["chat_messages"].append(update.message.message_id)

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📂 Quiz Folder", callback_data="HOME_MY_QUIZZES"),
            InlineKeyboardButton("➕ Create a new Quiz", callback_data="HOME_CREATE"),
        ],
        [
            InlineKeyboardButton("🗄 Database", callback_data="HOME_DATABASE"),
            InlineKeyboardButton("❓ Create a Question", callback_data="HOME_CREATE_QUESTION"),
        ],
        [
            InlineKeyboardButton("👥 Manage Subscribers", callback_data="HOME_MANAGE_SUBSCRIBERS"),
        ]
    ])

    msg = await update.message.reply_text(
        "🧠 Welcome to TeleQuiz (Bot Creator Panel)\n\nPlease choose an option to start 👇:",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )

    # 🔥 Track admin panel message
    context.user_data["chat_messages"].append(msg.message_id)

# =========================
# CREATE QUIZ
# =========================
async def create_quiz(update_or_message, context: ContextTypes.DEFAULT_TYPE):
    global OWNER_USER_ID

    # ✅ CORRECT HANDLING FOR BUTTON OR MESSAGE
    if isinstance(update_or_message, Update):
        user_id = update_or_message.effective_user.id
        message = update_or_message.message
        original_message = update_or_message.message
    else:
        query = update_or_message
        user_id = query.from_user.id
        message = query.message
        original_message = query.message

    # 🔒 OWNER-ONLY CHECK
    if user_id != OWNER_USER_ID:
        await message.reply_text("❌ Only the bot owner can create quizzes.")
        return

    # ⚠️ DO NOT CLEAR EVERYTHING blindly (safer reset)
    context.user_data.clear()

    context.user_data["quiz_id"] = str(uuid.uuid4())
    context.user_data["state"] = "WAIT_TITLE"

    # 📝 Send prompt and STORE its ID
    prompt_msg = await message.reply_text("📝 Send quiz title:")

    context.user_data["create_quiz_prompt_id"] = prompt_msg.message_id

    # 🔑 Store original message for safe editing later
    context.user_data["quiz_overview_msg_id"] = original_message.message_id

# =========================
# TEXT HANDLER
# =========================
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # 🔒 Ignore channel posts forwarded to groups
    if update.effective_user is None:
        return
    if update.effective_chat and update.effective_chat.type in ("channel",):
        return
    if context.user_data.get("add_q_state"):
        context.user_data.setdefault("question_flow_msgs", []).append(update.message.message_id)

    # ================= EDIT QUESTION IMAGE (QUESTION BANK) =================
    if context.user_data.get("edit_q_field") == "IMAGE":
        photo = update.message.photo[-1]
        file_id = photo.file_id

        qid = context.user_data.get("active_question_id")
        if not qid:
            await update.message.reply_text("❌ No question selected.")
            return

        # 🔑 UPDATE QUESTION BANK (SOURCE OF TRUTH)
        async with DB_LOCK:
            _conn_img, _cur_img = get_db()
            _cur_img.execute(
                "UPDATE question_bank SET image_file_id=? WHERE id=?",
                (file_id, qid)
            )
            _conn_img.commit()
            _conn_img.close()

        context.user_data.pop("edit_q_field", None)

        # ✅ Confirmation message
        confirm_msg = await update.message.reply_text("✅ Image updated.")

        await asyncio.sleep(2)

        chat_id = update.effective_chat.id

        # 🧹 Delete confirmation
        try:
            await confirm_msg.delete()
        except:
            pass

        # 🧹 Delete user image
        try:
            await update.message.delete()
        except:
            pass

        # 🧹 Delete image edit menu
        menu_id = context.user_data.pop("edit_image_menu_msg_id", None)
        if menu_id:
            try:
                await context.bot.delete_message(chat_id, menu_id)
            except:
                pass

        # 🧹 Delete image prompt
        prompt_id = context.user_data.pop("edit_image_prompt_msg_id", None)
        if prompt_id:
            try:
                await context.bot.delete_message(chat_id, prompt_id)
            except:
                pass

        # 🔄 Rebuild preview safely (handles media type correctly)
        await rebuild_question_preview(chat_id, context)

        return

    # ================= NEW QUESTION IMAGE STEP =================
    q_state = context.user_data.get("add_q_state")

    if q_state not in ("NEW_Q_IMAGE", "NEW_Q_PHOTO_WAIT"):
        return

    # ================= OCR SCAN PHOTO (GEMINI) =================
    if q_state == "NEW_Q_PHOTO_WAIT":
        photo   = update.message.photo[-1]
        file_id = photo.file_id

        context.user_data.setdefault("question_flow_msgs", []).append(
            update.message.message_id
        )

        processing_msg = await update.message.reply_text("🔍 Scanning image with Gemini AI, please wait...")
        context.user_data["question_flow_msgs"].append(processing_msg.message_id)

        try:
            tg_file    = await context.bot.get_file(file_id)
            file_bytes = await tg_file.download_as_bytearray()
            question, options = await scan_image_with_gemini(bytes(file_bytes))

        except Exception as e:
            print("⚠️ Gemini scan failed:", e)
            await processing_msg.edit_text(
                "❌ Failed to scan the image. Please try again with a clearer photo."
            )
            context.user_data["add_q_state"] = "NEW_Q_PHOTO_WAIT"
            return

        try:
            await processing_msg.delete()
            context.user_data["question_flow_msgs"].remove(processing_msg.message_id)
        except:
            pass

        if not question and not options:
            msg = await update.message.reply_text(
                "❌ No question text was detected in the image.\n\n"
                "Please send a clearer photo and try again.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("❌ Cancel", callback_data="CANCEL_CREATE_QUESTION")]
                ])
            )
            context.user_data["question_flow_msgs"].append(msg.message_id)
            context.user_data["add_q_state"] = "NEW_Q_PHOTO_WAIT"
            return

        context.user_data["ocr_question"] = question
        context.user_data["ocr_options"]  = options
        context.user_data["add_q_state"]  = "NEW_Q_PHOTO_CONFIRM"

        labels      = ["A", "B", "C", "D"]
        result_text = "🔍 *Scanned Result (Gemini AI):*\n\n"

        if question:
            result_text += f"📝 *Question:*\n{escape_md(question)}\n\n"
        else:
            result_text += "⚠️ _No question text detected._\n\n"

        if options:
            result_text += "🔤 *Options:*\n"
            for i, opt in enumerate(options):
                label = labels[i] if i < len(labels) else str(i + 1)
                result_text += f"{label}. {escape_md(opt)}\n"
        else:
            result_text += "⚠️ _No options detected._\n"

        buttons = []
        if question and len(options) == 4 and all(options):
            buttons.append([
                InlineKeyboardButton("✅ Use This",  callback_data="OCR_ACCEPT"),
                InlineKeyboardButton("🔄 Retake",    callback_data="OCR_RETAKE"),
            ])
        else:
            missing = []
            if not question:
                missing.append("question text")
            missing_opts = [labels[i] for i in range(len(options)) if not options[i]]
            if missing_opts:
                missing.append(f"option(s) {', '.join(missing_opts)}")
            if not options:
                missing.append("all options")

            result_text += (
                f"\n\n⚠️ _Could not fully detect: {', '.join(missing)}. "
                "Retake with a clearer photo or cancel._"
            )
            if question and len([o for o in options if o]) >= 2:
                buttons.append([
                    InlineKeyboardButton("✅ Use This",  callback_data="OCR_ACCEPT"),
                    InlineKeyboardButton("🔄 Retake",    callback_data="OCR_RETAKE"),
                ])
            else:
                buttons.append([
                    InlineKeyboardButton("🔄 Retake", callback_data="OCR_RETAKE"),
                ])

        buttons.append([
            InlineKeyboardButton("❌ Cancel", callback_data="CANCEL_CREATE_QUESTION")
        ])

        msg = await update.message.reply_text(
            result_text,
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode="Markdown"
        )
        context.user_data["question_flow_msgs"].append(msg.message_id)
        return

    photo = update.message.photo[-1]
    file_id = photo.file_id

    context.user_data["new_question"]["image"] = file_id
    context.user_data["add_q_state"] = "NEW_Q_OPTION_1"

    msg = await update.message.reply_text("➡️ Send option 1:")
    context.user_data.setdefault("question_flow_msgs", []).append(msg.message_id)

    return

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # 🔒 Ignore channel posts forwarded to groups
    if update.effective_user is None:
        return
    if update.effective_chat and update.effective_chat.type in ("channel",):
        return
    # Track user message
    context.user_data.setdefault("chat_messages", []).append(update.message.message_id)

    # ================= DATABASE TEXT FLOW (HARD ISOLATION) =================
    state = context.user_data.get("state")
    text = update.message.text.strip()

    # 🔑 Track user messages during question creation
    if context.user_data.get("add_q_state"):
        context.user_data.setdefault("question_flow_msgs", []).append(update.message.message_id)

    if state == "DB_ADD_FOLDER":
        chat_id = update.effective_chat.id
        user_msg_id = update.message.message_id
        folder = text.strip()

        # ❌ Empty name
        if not folder:
            await update.message.reply_text("❌ Folder name cannot be empty.")
            return

        normalized = folder.strip()

        if len(normalized) > MAX_FOLDER_NAME_LENGTH:
            err = await update.message.reply_text(
                f"❌ Folder name is too long ({len(normalized)} characters).\n"
                f"Maximum allowed: {MAX_FOLDER_NAME_LENGTH} characters.\n\nPlease shorten and send again."
            )
            await asyncio.sleep(4)
            await asyncio.gather(
                context.bot.delete_message(chat_id, err.message_id),
                context.bot.delete_message(chat_id, update.message.message_id),
                return_exceptions=True
            )
            return

        # ❌ Default is reserved
        if normalized.lower() == "default":
            await update.message.reply_text("❌ 'Default Folder' already exists.")
            return

        # ❌ Check duplicate (case-insensitive)
        _conn_chk, _cur_chk = get_db()
        _cur_chk.execute(
            """
            SELECT 1
            FROM question_bank_folders
            WHERE owner_id=?
              AND LOWER(name) = LOWER(?)
            """,
            (get_active_user_id(context), normalized)
        )
        already = _cur_chk.fetchone()
        _conn_chk.close()
        if already:
            await update.message.reply_text("❌ Folder already exists.")
            return

        # ✅ Create folder
        try:
            async with DB_LOCK:
                _conn, _cur = get_db()
                _cur.execute(
                    "INSERT INTO question_bank_folders (owner_id, name) VALUES (?, ?)",
                    (get_active_user_id(context), normalized)
                )
                _conn.commit()
                _conn.close()
        except Exception as e:
            print("⚠️ DB folder create failed:", e)
            await update.message.reply_text("❌ Failed to create folder.")
            return

        # 🔑 EXIT DB MODE IMMEDIATELY
        context.user_data.pop("state", None)

        # ✅ Confirmation message
        confirm_msg = await update.message.reply_text(
            f"📁 Database folder **{normalized}** created.",
            parse_mode="Markdown"
        )

        await asyncio.sleep(2)

        # 🧹 Bulk declutter: prompt + user message + confirmation
        prompt_id = context.user_data.pop("db_add_folder_prompt_id", None)
        delete_tasks = []
        if prompt_id:
            delete_tasks.append(context.bot.delete_message(chat_id, prompt_id))
        delete_tasks.append(context.bot.delete_message(chat_id, user_msg_id))
        delete_tasks.append(context.bot.delete_message(chat_id, confirm_msg.message_id))
        await asyncio.gather(*delete_tasks, return_exceptions=True)

        # 🔄 Menu replacement: edit the original database menu message
        db_menu_msg = context.user_data.get("db_menu_message_object")
        if db_menu_msg:
            await show_database_menu(db_menu_msg, context)
        return

    # ================= DB RENAME FOLDER =================
    if state == "DB_RENAME_FOLDER":
        chat_id = update.effective_chat.id
        user_msg_id = update.message.message_id

        old_name = context.user_data.get("db_rename_folder_name")
        new_name = text.strip()

        # ❌ Empty name
        if not new_name:
            await update.message.reply_text("❌ Folder name cannot be empty.")
            return

        if len(new_name) > MAX_FOLDER_NAME_LENGTH:
            err = await update.message.reply_text(
                f"❌ Folder name is too long ({len(new_name)} characters).\n"
                f"Maximum allowed: {MAX_FOLDER_NAME_LENGTH} characters.\n\nPlease shorten and send again."
            )
            await asyncio.sleep(4)
            await asyncio.gather(
                context.bot.delete_message(chat_id, err.message_id),
                context.bot.delete_message(chat_id, update.message.message_id),
                return_exceptions=True
            )
            return

        # ❌ Default is reserved
        if new_name.lower() == "default":
            await update.message.reply_text("❌ You cannot rename a folder to Default.")
            return

        # ❌ Check duplicate (case-insensitive)
        _conn_chk, _cur_chk = get_db()
        _cur_chk.execute(
            """
            SELECT 1
            FROM question_bank_folders
            WHERE owner_id=?
              AND LOWER(name) = LOWER(?)
            """,
            (get_active_user_id(context), new_name)
        )
        already = _cur_chk.fetchone()
        _conn_chk.close()
        if already:
            await update.message.reply_text("❌ A folder with this name already exists.")
            return

        # ✅ Rename folder
        try:
            async with DB_LOCK:
                _conn, _cur = get_db()
                _cur.execute(
                    "UPDATE question_bank_folders SET name=? WHERE owner_id=? AND name=?",
                    (new_name, get_active_user_id(context), old_name)
                )
                _conn.commit()
                _conn.close()
        except Exception as e:
            print("⚠️ DB folder rename failed:", e)
            await flash_message(context.bot, chat_id, "❌ Rename failed.")
            return

        # 🧹 Delete prompt message
        prompt_id = context.user_data.pop("db_rename_prompt_id", None)
        if prompt_id:
            try:
                await context.bot.delete_message(chat_id, prompt_id)
            except:
                pass

        # 🧹 Delete user's typed message
        try:
            await context.bot.delete_message(chat_id, user_msg_id)
        except:
            pass

        # 🧹 Clear state
        context.user_data.pop("state", None)
        context.user_data.pop("db_rename_folder_name", None)

        # ✅ Update db_folder_name so the refreshed view shows the new name
        context.user_data["db_folder_name"] = new_name

        # 🔔 Confirmation
        confirm_msg = await context.bot.send_message(
            chat_id,
            f"✅ Folder renamed to **{new_name}**.",
            parse_mode="Markdown"
        )

        await asyncio.sleep(2)

        try:
            await confirm_msg.delete()
        except:
            pass

        # 🔁 Refresh the folder question list with new name
        await show_db_questions_from_message(
            context.user_data.get("db_rename_menu_message"),
            context
        )
        return

    # ================= DB SEARCH =================
    if state == "DB_SEARCH":
        chat_id = update.effective_chat.id
        user_msg_id = update.message.message_id
        keyword = text.strip()

        if not keyword:
            await update.message.reply_text("❌ Please enter a keyword.")
            return

        # 🧹 Delete prompt
        prompt_id = context.user_data.pop("db_search_prompt_id", None)
        if prompt_id:
            try:
                await context.bot.delete_message(chat_id, prompt_id)
            except:
                pass

        # 🧹 Delete user's typed message
        try:
            await context.bot.delete_message(chat_id, user_msg_id)
        except:
            pass

        context.user_data.pop("state", None)
        context.user_data["db_search_keyword"] = keyword
        context.user_data["db_search_page"] = 0

        # 🔄 Replace the database menu message with search results
        menu_msg = context.user_data.get("db_search_menu_message")
        if menu_msg:
            await show_db_search_results(menu_msg, context)
        return

    # ================= ADD QUESTION FLOW =================
    q_state = context.user_data.get("add_q_state")

    # ================= EDIT QUESTION EXPLANATION =================
    if context.user_data.get("edit_q_field") == "EXPLANATION":
        qid = context.user_data.get("active_question_id")
        if not qid:
            return

        new_text = update.message.text.strip()

        if len(new_text) > MAX_EXPLANATION_LENGTH:
            err = await update.message.reply_text(
                f"❌ Explanation is too long ({len(new_text)} characters).\n"
                f"Maximum allowed: {MAX_EXPLANATION_LENGTH} characters.\n\nPlease shorten and send again."
            )
            await asyncio.sleep(4)
            await asyncio.gather(
                context.bot.delete_message(update.effective_chat.id, err.message_id),
                context.bot.delete_message(update.effective_chat.id, update.message.message_id),
                return_exceptions=True
            )
            return

        chat_id = update.effective_chat.id

        # Update DB
        async with DB_LOCK:
            _conn_e, _cur_e = get_db()
            _cur_e.execute(
                "UPDATE question_bank SET explanation=? WHERE id=?",
                (new_text, qid)
            )
            _conn_e.commit()
            _conn_e.close()

        # Delete prompt
        prompt_id = context.user_data.pop("edit_expl_prompt_id", None)
        if prompt_id:
            try:
                await context.bot.delete_message(chat_id, prompt_id)
            except:
                pass

        # Exit edit mode
        context.user_data.pop("edit_q_field", None)

        # Confirmation
        confirm = await update.message.reply_text("✅ Explanation added.")

        await asyncio.sleep(2)

        try:
            await confirm.delete()
            await update.message.delete()
        except:
            pass

        # Rebuild preview
        await rebuild_question_preview(chat_id, context)

        return

    # ================= EDIT QUESTION TEXT =================
    edit_field = context.user_data.get("edit_q_field")

    if edit_field == "TEXT":
        qid = context.user_data.get("active_question_id")
        if not qid:
            await update.message.reply_text("❌ No question selected.")
            return

        if len(text) > MAX_QUESTION_LENGTH:
            err = await update.message.reply_text(
                f"❌ Question is too long ({len(text)} characters).\n"
                f"Maximum allowed: {MAX_QUESTION_LENGTH} characters.\n\nPlease shorten and send again."
            )
            await asyncio.sleep(4)
            await asyncio.gather(
                context.bot.delete_message(update.effective_chat.id, err.message_id),
                context.bot.delete_message(update.effective_chat.id, update.message.message_id),
                return_exceptions=True
            )
            return

        # 🔑 Update DB
        async with DB_LOCK:
            _conn_q, _cur_q = get_db()
            _cur_q.execute(
                "UPDATE question_bank SET question=? WHERE id=?",
                (text, qid)
            )
            _conn_q.commit()
            _conn_q.close()

        context.user_data.pop("edit_q_field", None)

        confirm_msg = await update.message.reply_text("✅ Question text updated.")

        await asyncio.sleep(2)

        chat_id = update.effective_chat.id

        try:
            await confirm_msg.delete()
        except:
            pass

        try:
            await update.message.delete()
        except:
            pass

        prompt_id = context.user_data.pop("edit_text_prompt_id", None)
        if prompt_id:
            try:
                await context.bot.delete_message(chat_id, prompt_id)
            except:
                pass

        chat_id = update.effective_chat.id
        await rebuild_question_preview(chat_id, context)

        return

    # 📝 Question text (NEW QUESTION — WITH DUPLICATE CHECK)
    if q_state == "NEW_Q_TEXT":

        context.user_data["last_user_question_msg_id"] = update.message.message_id

        new_text = text.strip()

        # ── Length validation ──────────────────────────────
        if len(new_text) > MAX_QUESTION_LENGTH:
            err = await update.message.reply_text(
                f"❌ Question is too long ({len(new_text)} characters).\n"
                f"Maximum allowed: {MAX_QUESTION_LENGTH} characters.\n\n"
                f"Please shorten your question and send it again."
            )
            await asyncio.sleep(4)
            await asyncio.gather(
                context.bot.delete_message(update.effective_chat.id, err.message_id),
                context.bot.delete_message(update.effective_chat.id, update.message.message_id),
                return_exceptions=True
            )
            return
        # ──────────────────────────────────────────────────
        similar_matches = []

        _conn_dup, _cur_dup = get_db()
        _cur_dup.execute("SELECT id, question FROM question_bank")
        existing_questions = _cur_dup.fetchall()
        _conn_dup.close()

        for qid, existing_text in existing_questions:
            similarity = SequenceMatcher(
                None,
                new_text.lower(),
                existing_text.lower()
            ).ratio()

            if similarity >= 0.80:
                similar_matches.append((similarity, existing_text))

        similar_matches.sort(reverse=True, key=lambda x: x[0])

        if similar_matches:
            top_matches = similar_matches[:5]

            warning_text = "⚠️ *Similar question(s) found:*\n\n"

            for i, (_, q_text) in enumerate(top_matches, 1):
                # Fetch options and correct answer for this question
                _conn_qr, _cur_qr = get_db()
                _cur_qr.execute(
                    "SELECT options, correct FROM question_bank WHERE question=? LIMIT 1",
                    (q_text,)
                )
                q_row = _cur_qr.fetchone()
                _conn_qr.close()
                if q_row:
                    opts = q_row[0].split("||")
                    correct_idx = q_row[1]
                    correct_text = opts[correct_idx] if 0 <= correct_idx < len(opts) else "—"
                    warning_text += f"{i}. {escape_md(q_text[:80])}\n    ✅ _{escape_md(correct_text)}_\n\n"
                else:
                    warning_text += f"{i}. {q_text[:80]}\n\n"

            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ Create Anyway", callback_data="DUP_CREATE_ANYWAY"),
                    InlineKeyboardButton("✏️ Change Question", callback_data="DUP_EDIT"),
                ],
                [
                    InlineKeyboardButton("❌ Cancel Question Creation", callback_data="DUP_CANCEL")
                ]
            ])

            context.user_data["pending_duplicate_text"] = new_text
            context.user_data["add_q_state"] = "CONFIRM_DUPLICATE_Q"

            msg = await update.message.reply_text(
                warning_text,
                reply_markup=keyboard,
                parse_mode="Markdown"
            )

            context.user_data.setdefault("question_flow_msgs", []).append(msg.message_id)
            return

        context.user_data["new_question"]["text"] = new_text
        context.user_data["add_q_state"] = "NEW_Q_IMAGE"

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("⏭ Skip image", callback_data="SKIP_Q_IMAGE")]
        ])

        msg = await update.message.reply_text(
            "🖼 Send image for this question:",
            reply_markup=keyboard
        )

        context.user_data.setdefault("question_flow_msgs", []).append(msg.message_id)
        return

    # ================= OPTIONS FLOW (NEW QUESTION — DO NOT CHANGE) =================

    if q_state == "NEW_Q_OPTION_1":
        if len(text) > MAX_OPTION_LENGTH:
            err = await update.message.reply_text(
                f"❌ Option 1 is too long ({len(text)} characters).\n"
                f"Maximum: {MAX_OPTION_LENGTH} characters. Please send it again."
            )
            await asyncio.sleep(4)
            await asyncio.gather(
                context.bot.delete_message(update.effective_chat.id, err.message_id),
                context.bot.delete_message(update.effective_chat.id, update.message.message_id),
                return_exceptions=True
            )
            return
        context.user_data["new_question"]["options"].append(text)
        context.user_data["add_q_state"] = "NEW_Q_OPTION_2"
        msg = await update.message.reply_text("➡️ Send option 2:")
        context.user_data.setdefault("question_flow_msgs", []).append(msg.message_id)
        return

    if q_state == "NEW_Q_OPTION_2":
        if len(text) > MAX_OPTION_LENGTH:
            err = await update.message.reply_text(
                f"❌ Option 2 is too long ({len(text)} characters).\n"
                f"Maximum: {MAX_OPTION_LENGTH} characters. Please send it again."
            )
            await asyncio.sleep(4)
            await asyncio.gather(
                context.bot.delete_message(update.effective_chat.id, err.message_id),
                context.bot.delete_message(update.effective_chat.id, update.message.message_id),
                return_exceptions=True
            )
            return
        context.user_data["new_question"]["options"].append(text)
        context.user_data["add_q_state"] = "NEW_Q_OPTION_3"
        msg = await update.message.reply_text("➡️ Send option 3:")
        context.user_data.setdefault("question_flow_msgs", []).append(msg.message_id)
        return

    if q_state == "NEW_Q_OPTION_3":
        if len(text) > MAX_OPTION_LENGTH:
            err = await update.message.reply_text(
                f"❌ Option 3 is too long ({len(text)} characters).\n"
                f"Maximum: {MAX_OPTION_LENGTH} characters. Please send it again."
            )
            await asyncio.sleep(4)
            await asyncio.gather(
                context.bot.delete_message(update.effective_chat.id, err.message_id),
                context.bot.delete_message(update.effective_chat.id, update.message.message_id),
                return_exceptions=True
            )
            return
        context.user_data["new_question"]["options"].append(text)
        context.user_data["add_q_state"] = "NEW_Q_OPTION_4"
        msg = await update.message.reply_text("➡️ Send option 4:")
        context.user_data.setdefault("question_flow_msgs", []).append(msg.message_id)
        return

    if q_state == "NEW_Q_OPTION_4":
        if len(text) > MAX_OPTION_LENGTH:
            err = await update.message.reply_text(
                f"❌ Option 4 is too long ({len(text)} characters).\n"
                f"Maximum: {MAX_OPTION_LENGTH} characters. Please send it again."
            )
            await asyncio.sleep(4)
            await asyncio.gather(
                context.bot.delete_message(update.effective_chat.id, err.message_id),
                context.bot.delete_message(update.effective_chat.id, update.message.message_id),
                return_exceptions=True
            )
            return
        context.user_data["new_question"]["options"].append(text)
        context.user_data["add_q_state"] = "NEW_Q_CORRECT"
        # ... rest of this block stays exactly as-is

        opts = context.user_data["new_question"]["options"]

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"1️⃣ {opts[0]}", callback_data="CORRECT_0")],
            [InlineKeyboardButton(f"2️⃣ {opts[1]}", callback_data="CORRECT_1")],
            [InlineKeyboardButton(f"3️⃣ {opts[2]}", callback_data="CORRECT_2")],
            [InlineKeyboardButton(f"4️⃣ {opts[3]}", callback_data="CORRECT_3")],
        ])

        msg = await update.message.reply_text(
            "✅ Choose the correct answer:",
            reply_markup=keyboard
        )

        context.user_data.setdefault("question_flow_msgs", []).append(msg.message_id)
        return

    # ================= EDIT QUESTION OPTIONS =================
    if context.user_data.get("edit_q_field") == "OPTIONS":

        new_text = update.message.text.strip()
        context.user_data["edit_options"].append(new_text)

        flow_msgs = context.user_data.get("edit_options_flow_msgs", [])
        flow_msgs.append(update.message.message_id)

        count = len(context.user_data["edit_options"])

        if count < 4:
            next_msg = await update.message.reply_text(
                f"➡️ Send NEW option {count + 1}:"
            )
            flow_msgs.append(next_msg.message_id)
            return

        context.user_data["edit_q_field"] = "OPTIONS_CORRECT"

        new_opts = context.user_data["edit_options"]

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"1️⃣ {new_opts[0]}", callback_data="EDIT_OPT_CORRECT_0")],
            [InlineKeyboardButton(f"2️⃣ {new_opts[1]}", callback_data="EDIT_OPT_CORRECT_1")],
            [InlineKeyboardButton(f"3️⃣ {new_opts[2]}", callback_data="EDIT_OPT_CORRECT_2")],
            [InlineKeyboardButton(f"4️⃣ {new_opts[3]}", callback_data="EDIT_OPT_CORRECT_3")],
        ])

        msg = await update.message.reply_text(
            "✅ Choose the NEW correct answer:",
            reply_markup=keyboard
        )

        flow_msgs.append(msg.message_id)
        return

    # ================= EXPLANATION (NEW QUESTION — DO NOT CHANGE) =================
    if q_state == "NEW_Q_EXPLANATION":
        if len(text) > MAX_EXPLANATION_LENGTH:
            err = await update.message.reply_text(
                f"❌ Explanation is too long ({len(text)} characters).\n"
                f"Maximum allowed: {MAX_EXPLANATION_LENGTH} characters.\n\nPlease shorten and send again."
            )
            await asyncio.sleep(4)
            await asyncio.gather(
                context.bot.delete_message(update.effective_chat.id, err.message_id),
                context.bot.delete_message(update.effective_chat.id, update.message.message_id),
                return_exceptions=True
            )
            return
        context.user_data["new_question"]["explanation"] = text
        await save_new_question(update.message, context)
        return

    # ================= MOVE + CREATE FOLDER =================
    if state == "MOVE_ADD_FOLDER":
        chat_id = update.effective_chat.id
        user_msg_id = update.message.message_id
        folder = text.strip()

        if folder == "Default":
            await update.message.reply_text("❌ 'Default' folder already exists.")
            return

        _conn_chk, _cur_chk = get_db()
        _cur_chk.execute(
            "SELECT 1 FROM folders WHERE owner_id=? AND name=?",
            (get_active_user_id(context), folder)
        )
        already = _cur_chk.fetchone()
        _conn_chk.close()
        if already:
            await update.message.reply_text("❌ Folder already exists.")
            return

        quiz_id = context.user_data.get("active_quiz_id")
        if not quiz_id:
            return

        async with DB_LOCK:
            _conn_mf, _cur_mf = get_db()
            _cur_mf.execute(
                "INSERT INTO folders (owner_id, name) VALUES (?, ?)",
                (get_active_user_id(context), folder)
            )
            _cur_mf.execute(
                "UPDATE quizzes SET folder=? WHERE quiz_id=? AND owner_id=?",
                (folder, quiz_id, get_active_user_id(context))
            )
            _conn_mf.commit()
            _conn_mf.close()

        context.user_data["state"] = None

        # ✅ Confirmation message
        confirm_msg = await update.message.reply_text(
            f"✅ Folder '{folder}' created and quiz moved."
        )

        await asyncio.sleep(2)

        # 🧹 Bulk declutter: prompt + user message + confirmation
        prompt_id = context.user_data.pop("move_create_folder_prompt_id", None)
        delete_tasks = []
        if prompt_id:
            delete_tasks.append(context.bot.delete_message(chat_id, prompt_id))
        delete_tasks.append(context.bot.delete_message(chat_id, user_msg_id))
        delete_tasks.append(context.bot.delete_message(chat_id, confirm_msg.message_id))
        await asyncio.gather(*delete_tasks, return_exceptions=True)

        # 🔄 Return to quiz action menu
        overview_id = context.user_data.get("quiz_overview_msg_id")
        if overview_id:
            await show_quiz_action_menu_by_id(
                chat_id=chat_id,
                message_id=overview_id,
                context=context
            )
        return

    # ================= ADD FOLDER =================
    if state == "ADD_FOLDER":
        chat_id = update.effective_chat.id
        user_msg_id = update.message.message_id
        folder_name = text.strip()

        if len(folder_name) > MAX_FOLDER_NAME_LENGTH:
            err = await update.message.reply_text(
                f"❌ Folder name is too long ({len(folder_name)} characters).\n"
                f"Maximum allowed: {MAX_FOLDER_NAME_LENGTH} characters.\n\nPlease shorten and send again."
            )
            await asyncio.sleep(4)
            await asyncio.gather(
                context.bot.delete_message(chat_id, err.message_id),
                context.bot.delete_message(chat_id, user_msg_id),
                return_exceptions=True
            )
            return

        if folder_name == "Default":
            await update.message.reply_text("❌ You cannot create a folder named Default.")
            return

        _conn_chk, _cur_chk = get_db()
        _cur_chk.execute(
            "SELECT 1 FROM folders WHERE owner_id=? AND LOWER(name)=LOWER(?)",
            (get_active_user_id(context), folder_name)
        )
        already = _cur_chk.fetchone()
        _conn_chk.close()
        if already:
            err = await update.message.reply_text(
                f"❌ A folder named *{folder_name}* already exists.\n\nPlease send a different name:",
                parse_mode="Markdown"
            )
            await asyncio.sleep(3)
            await asyncio.gather(
                context.bot.delete_message(chat_id, err.message_id),
                context.bot.delete_message(chat_id, user_msg_id),
                return_exceptions=True
            )
            return

        try:
            async with DB_LOCK:
                _conn, _cur = get_db()
                _cur.execute(
                    "INSERT INTO folders (owner_id, name) VALUES (?, ?)",
                    (get_active_user_id(context), folder_name)
                )
                _conn.commit()
                _conn.close()
        except Exception as e:
            print("⚠️ Failed to create folder:", e)
            await flash_message(context.bot, chat_id, "❌ Folder creation failed.")
            return

        prompt_id = context.user_data.pop("add_folder_prompt_id", None)
        if prompt_id:
            try:
                await context.bot.delete_message(chat_id, prompt_id)
            except:
                pass

        try:
            await context.bot.delete_message(chat_id, user_msg_id)
        except:
            pass

        confirm_msg = await context.bot.send_message(
            chat_id,
            f'✅ Folder "{folder_name}" created.'
        )

        context.user_data["state"] = None

        await asyncio.sleep(2)

        try:
            await confirm_msg.delete()
        except:
            pass

        await show_quiz_folders(
            context.user_data.get("folder_screen_message_object"),
            context
        )
        return

    # ================= RENAME FOLDER =================
    if state == "RENAME_FOLDER":
        chat_id = update.effective_chat.id
        user_msg_id = update.message.message_id

        old = context.user_data["rename_folder"]
        new = text.strip()

        if len(new) > MAX_FOLDER_NAME_LENGTH:
            err = await update.message.reply_text(
                f"❌ Folder name is too long ({len(new)} characters).\n"
                f"Maximum allowed: {MAX_FOLDER_NAME_LENGTH} characters.\n\nPlease shorten and send again."
            )
            await asyncio.sleep(4)
            await asyncio.gather(
                context.bot.delete_message(chat_id, err.message_id),
                context.bot.delete_message(chat_id, user_msg_id),
                return_exceptions=True
            )
            return

        if new == "Default":
            await update.message.reply_text("❌ You cannot rename a folder to Default.")
            return

        _conn_chk, _cur_chk = get_db()
        _cur_chk.execute(
            "SELECT 1 FROM folders WHERE owner_id=? AND LOWER(name)=LOWER(?)",
            (get_active_user_id(context), new)
        )
        already = _cur_chk.fetchone()
        _conn_chk.close()
        if already:
            err = await update.message.reply_text(
                f"❌ A folder named *{new}* already exists.\n\nPlease send a different name:",
                parse_mode="Markdown"
            )
            await asyncio.sleep(3)
            await asyncio.gather(
                context.bot.delete_message(chat_id, err.message_id),
                context.bot.delete_message(chat_id, user_msg_id),
                return_exceptions=True
            )
            return

        try:
            async with DB_LOCK:
                _conn, _cur = get_db()
                _cur.execute(
                    "UPDATE folders SET name=? WHERE owner_id=? AND name=?",
                    (new, get_active_user_id(context), old)
                )
                _cur.execute(
                    "UPDATE quizzes SET folder=? WHERE owner_id=? AND folder=?",
                    (new, get_active_user_id(context), old)
                )
                _conn.commit()
                _conn.close()

        except Exception as e:
            print("⚠️ Rename failed:", e)
            await flash_message(context.bot, chat_id, "❌ Rename failed.")
            return

        prompt_id = context.user_data.pop("rename_prompt_msg_id", None)
        if prompt_id:
            try:
                await context.bot.delete_message(chat_id, prompt_id)
            except:
                pass

        try:
            await context.bot.delete_message(chat_id, user_msg_id)
        except:
            pass

        context.user_data["state"] = None
        context.user_data.pop("rename_folder", None)

        await show_quiz_folders(
            context.user_data.get("folder_screen_message_object"),
            context
        )

        return

    # ================= CREATE QUIZ =================
    if state == "CREATE_QUIZ":
        chat_id = update.effective_chat.id
        user_msg_id = update.message.message_id
        title = text.strip()

        if len(title) > MAX_TITLE_LENGTH:
            err = await update.message.reply_text(
                f"❌ Title is too long ({len(title)} characters).\n"
                f"Maximum allowed: {MAX_TITLE_LENGTH} characters.\n\nPlease shorten and send again."
            )
            await asyncio.sleep(4)
            await asyncio.gather(
                context.bot.delete_message(chat_id, err.message_id),
                context.bot.delete_message(chat_id, user_msg_id),
                return_exceptions=True
            )
            return

        if not title:
            err = await update.message.reply_text("❌ Quiz title cannot be empty. Please send a title:")
            await asyncio.sleep(3)
            await asyncio.gather(
                context.bot.delete_message(chat_id, err.message_id),
                context.bot.delete_message(chat_id, user_msg_id),
                return_exceptions=True
            )
            return

        # 🔒 Duplicate title check (case-insensitive)
        _conn_chk, _cur_chk = get_db()
        _cur_chk.execute(
            "SELECT 1 FROM quizzes WHERE owner_id=? AND LOWER(title)=LOWER(?)",
            (get_active_user_id(context), title)
        )
        already = _cur_chk.fetchone()
        _conn_chk.close()
        if already:
            err = await update.message.reply_text(
                f"❌ A quiz named *{title}* already exists.\n\nPlease send a different title:",
                parse_mode="Markdown"
            )
            await asyncio.sleep(3)
            await asyncio.gather(
                context.bot.delete_message(chat_id, err.message_id),
                context.bot.delete_message(chat_id, user_msg_id),
                return_exceptions=True
            )
            return

        try:
            async with DB_LOCK:
                _conn, _cur = get_db()
                _cur.execute(
                    "INSERT INTO quizzes VALUES (?, ?, ?, NULL, 'Default', 1, 1, 15)",
                    (
                        context.user_data["quiz_id"],
                        get_active_user_id(context),
                        title,
                    )
                )
                _conn.commit()
                _conn.close()
        except Exception as e:
            print("⚠️ Quiz creation failed:", e)
            await flash_message(context.bot, chat_id, "❌ Failed to create quiz.")
            return

        context.user_data["active_quiz_id"] = context.user_data["quiz_id"]
        context.user_data["state"] = None

        prompt_id = context.user_data.pop("create_quiz_prompt_id", None)
        if prompt_id:
            try:
                await context.bot.delete_message(chat_id, prompt_id)
            except:
                pass

        try:
            await context.bot.delete_message(chat_id, user_msg_id)
        except:
            pass

        confirm_msg = await context.bot.send_message(chat_id, "✅ Quiz created.")

        await asyncio.sleep(2)

        try:
            await confirm_msg.delete()
        except:
            pass

        overview_id = context.user_data.get("quiz_overview_msg_id")
        if overview_id:
            await show_quiz_action_menu_by_id(
                chat_id=chat_id,
                message_id=overview_id,
                context=context
            )

        return

    # ================= EDIT TITLE =================
    if state == "EDIT_TITLE":
        quiz_id = context.user_data.get("active_quiz_id")
        if not quiz_id:
            return

        chat_id = update.effective_chat.id
        user_msg_id = update.message.message_id
        new_title = text.strip()

        if len(new_title) > MAX_TITLE_LENGTH:
            err = await update.message.reply_text(
                f"❌ Title is too long ({len(new_title)} characters).\n"
                f"Maximum allowed: {MAX_TITLE_LENGTH} characters.\n\nPlease shorten and send again."
            )
            await asyncio.sleep(4)
            await asyncio.gather(
                context.bot.delete_message(chat_id, err.message_id),
                context.bot.delete_message(chat_id, user_msg_id),
                return_exceptions=True
            )
            return

        if not new_title:
            err = await update.message.reply_text("❌ Title cannot be empty. Please send a title:")
            await asyncio.sleep(3)
            await asyncio.gather(
                context.bot.delete_message(chat_id, err.message_id),
                context.bot.delete_message(chat_id, user_msg_id),
                return_exceptions=True
            )
            return

        # 🔒 Duplicate title check (exclude current quiz)
        _conn_chk, _cur_chk = get_db()
        _cur_chk.execute(
            "SELECT 1 FROM quizzes WHERE owner_id=? AND LOWER(title)=LOWER(?) AND quiz_id!=?",
            (get_active_user_id(context), new_title, quiz_id)
        )
        already = _cur_chk.fetchone()
        _conn_chk.close()
        if already:
            err = await update.message.reply_text(
                f"❌ A quiz named *{new_title}* already exists.\n\nPlease send a different title:",
                parse_mode="Markdown"
            )
            await asyncio.sleep(3)
            await asyncio.gather(
                context.bot.delete_message(chat_id, err.message_id),
                context.bot.delete_message(chat_id, user_msg_id),
                return_exceptions=True
            )
            return

        async with DB_LOCK:
            _conn_w, _cur_w = get_db()
            _cur_w.execute(
                "UPDATE quizzes SET title=? WHERE quiz_id=?",
                (new_title, quiz_id)
            )
            _conn_w.commit()
            _conn_w.close()

        context.user_data["state"] = None

        confirm_msg = await update.message.reply_text("✅ Title updated.")

        prompt_id = context.user_data.pop("edit_title_prompt_id", None)
        if prompt_id:
            try:
                await context.bot.delete_message(chat_id, prompt_id)
            except:
                pass

        try:
            await context.bot.delete_message(chat_id, user_msg_id)
        except:
            pass

        try:
            await confirm_msg.delete()
        except:
            pass

        asyncio.create_task(
            refresh_all_group_posts_for_quiz(quiz_id, context)
        )

        overview_id = context.user_data.get("quiz_overview_msg_id")
        if overview_id:
            await show_quiz_action_menu_by_id(
                chat_id=chat_id,
                message_id=overview_id,
                context=context
            )

        return

    # ================= EDIT DESCRIPTION =================
    if state == "EDIT_DESC":
        quiz_id = context.user_data.get("active_quiz_id")
        if not quiz_id:
            return

        user_msg_id = update.message.message_id

        if text.upper() != "CLEAR" and len(text) > MAX_DESC_LENGTH:
            err = await update.message.reply_text(
                f"❌ Description is too long ({len(text)} characters).\n"
                f"Maximum allowed: {MAX_DESC_LENGTH} characters.\n\nPlease shorten and send again."
            )
            await asyncio.sleep(4)
            await asyncio.gather(
                context.bot.delete_message(update.effective_chat.id, err.message_id),
                context.bot.delete_message(update.effective_chat.id, user_msg_id),
                return_exceptions=True
            )
            return

        async with DB_LOCK:
            _conn_d, _cur_d = get_db()
            if text.upper() == "CLEAR":
                _cur_d.execute(
                    "UPDATE quizzes SET description=NULL WHERE quiz_id=?",
                    (quiz_id,)
                )
            else:
                _cur_d.execute(
                    "UPDATE quizzes SET description=? WHERE quiz_id=?",
                    (text, quiz_id)
                )
            _conn_d.commit()
            _conn_d.close()

        context.user_data["state"] = None

        confirm_msg = await update.message.reply_text("✅ Description updated.")

        prompt_id = context.user_data.pop("edit_desc_prompt_id", None)
        if prompt_id:
            try:
                await context.bot.delete_message(
                    chat_id=update.effective_chat.id,
                    message_id=prompt_id
                )
            except:
                pass

        try:
            await context.bot.delete_message(
                chat_id=update.effective_chat.id,
                message_id=user_msg_id
            )
        except:
            pass

        try:
            await confirm_msg.delete()
        except:
            pass

        # 🔄 SYNC: Refresh all active group posts for this quiz
        asyncio.create_task(
            refresh_all_group_posts_for_quiz(quiz_id, context)
        )

        overview_id = context.user_data.get("quiz_overview_msg_id")
        if overview_id:
            await show_quiz_action_menu_by_id(
                chat_id=update.effective_chat.id,
                message_id=overview_id,
                context=context
            )

        return

    # SUB_WAIT_USER_ID
    if state == "SUB_WAIT_USER_ID":
        chat_id = update.effective_chat.id
        try:
            new_user_id = int(text.strip())
        except ValueError:
            err = await update.message.reply_text("❌ Invalid User ID. Please send numbers only.")
            await asyncio.sleep(2)
            await asyncio.gather(
                context.bot.delete_message(chat_id, err.message_id),
                context.bot.delete_message(chat_id, update.message.message_id),
                return_exceptions=True
            )
            return

        # 🔍 Validate: check if this Telegram User ID actually exists
        try:
            await context.bot.get_chat(new_user_id)
        except Exception:
            # 🧹 Delete the old prompt message
            old_prompt_id = context.user_data.pop("sub_prompt_id", None)
            if old_prompt_id:
                try:
                    await context.bot.delete_message(chat_id, old_prompt_id)
                except:
                    pass

            # 🧹 Delete the user's typed invalid ID message
            try:
                await context.bot.delete_message(chat_id, update.message.message_id)
            except:
                pass

            # Send new error prompt
            err = await context.bot.send_message(
                chat_id,
                f"❌ User ID `{new_user_id}` not found on Telegram.\n\nPlease send a valid User ID:",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("❌ Cancel", callback_data="SUB_CANCEL")]
                ])
            )
            context.user_data["sub_prompt_id"] = err.message_id
            return

        context.user_data["sub_new_user_id"] = new_user_id
        context.user_data["state"] = "SUB_WAIT_NAME"

        # Cleanup
        prompt_id = context.user_data.pop("sub_prompt_id", None)
        if prompt_id:
            try: await context.bot.delete_message(chat_id, prompt_id)
            except: pass
        try: await context.bot.delete_message(chat_id, update.message.message_id)
        except: pass

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ Cancel", callback_data="SUB_CANCEL")]
        ])
        msg = await context.bot.send_message(
            chat_id,
            f"✅ User ID `{new_user_id}` accepted.\n\n📝 Now send the *Subscriber Name*:",
            reply_markup=keyboard,
            parse_mode="Markdown"
        )
        context.user_data["sub_prompt_id"] = msg.message_id
        return

    # SUB_WAIT_NAME
    if state == "SUB_WAIT_NAME":
        chat_id = update.effective_chat.id
        context.user_data["sub_new_name"] = text.strip()
        context.user_data["state"] = "SUB_WAIT_DURATION"

        prompt_id = context.user_data.pop("sub_prompt_id", None)
        if prompt_id:
            try: await context.bot.delete_message(chat_id, prompt_id)
            except: pass
        try: await context.bot.delete_message(chat_id, update.message.message_id)
        except: pass

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📅 1 Day",    callback_data="SUB_DURATION|1 Day"),
             InlineKeyboardButton("📅 1 Week",   callback_data="SUB_DURATION|1 Week"),
             InlineKeyboardButton("📅 1 Month",  callback_data="SUB_DURATION|1 Month")],
            [InlineKeyboardButton("📅 6 Months", callback_data="SUB_DURATION|6 Months"),
             InlineKeyboardButton("📅 1 Year",   callback_data="SUB_DURATION|1 Year"),
             InlineKeyboardButton("♾ Lifetime",  callback_data="SUB_DURATION|Lifetime")],
            [InlineKeyboardButton("❌ Cancel",   callback_data="SUB_CANCEL")],
        ])
        msg = await context.bot.send_message(
            chat_id,
            f"👤 Name: *{text.strip()}*\n\n⏳ Select subscription duration:",
            reply_markup=keyboard,
            parse_mode="Markdown"
        )
        context.user_data["sub_prompt_id"] = msg.message_id
        return

# =========================
# DELETE QUESTION
# =========================
async def delete_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    qid = context.user_data.get("active_question_id")
    if not qid:
        await flash_message(context.bot, query.message.chat_id, "❌ No question selected.")
        return

    # Save delete request
    context.user_data["confirm_delete"] = ("QUESTION", qid)

    # 🔑 IMPORTANT: mark this as a DATABASE delete
    context.user_data["delete_scope"] = "DATABASE"

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Yes, delete", callback_data="CONFIRM_DELETE"),
            InlineKeyboardButton("❌ Cancel", callback_data="CANCEL_DELETE")
        ]
    ])

    await query.message.reply_text(
        "❗ Are you sure you want to delete this question?",
        reply_markup=keyboard
    )

async def delete_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    quiz_id = context.user_data.get("active_quiz_id")
    if not quiz_id:
        await flash_message(context.bot, query.message.chat_id, "❌ No quiz selected.")
        return
    if not verify_quiz_owner(quiz_id, context):
        await flash_message(context.bot, query.message.chat_id, "❌ Access denied.")
        return

    context.user_data["confirm_delete"] = ("QUIZ", quiz_id)

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Yes, delete", callback_data="CONFIRM_DELETE"),
            InlineKeyboardButton("❌ Cancel", callback_data="CANCEL_DELETE")
        ]
    ])

    await query.message.reply_text(
        "❗ Are you sure you want to delete this quiz?",
        reply_markup=keyboard
    )

async def show_quiz_folders(message, context):
    page = context.user_data.get("quiz_folder_page", 0)
    PER_PAGE = 5
    active_uid = get_active_user_id(context)

    _conn, _cur = get_db()
    _cur.execute("SELECT name FROM folders WHERE owner_id=?", (active_uid,))
    rows = [row[0] for row in _cur.fetchall()]
    _conn.close()

    default_folder = "Default"
    other_folders = sorted([f for f in rows if f != default_folder])

    total = len(other_folders)
    pages = (total - 1) // PER_PAGE + 1 if total else 1
    page = max(0, min(page, pages - 1))

    start = page * PER_PAGE
    end = start + PER_PAGE
    page_items = other_folders[start:end]

    keyboard = []

    _conn2, _cur2 = get_db()
    _cur2.execute(
        "SELECT COUNT(*) FROM quizzes WHERE owner_id=? AND folder=?",
        (active_uid, default_folder)
    )
    default_count = _cur2.fetchone()[0]
    _conn2.close()

    keyboard.append([
        InlineKeyboardButton(
            f"📁 Default Folder ({default_count})",
            callback_data=f"OPEN_FOLDER|{default_folder}"
        )
    ])

    for folder in page_items:
        _conn3, _cur3 = get_db()
        _cur3.execute(
            "SELECT COUNT(*) FROM quizzes WHERE owner_id=? AND folder=?",
            (active_uid, folder)
        )
        count = _cur3.fetchone()[0]
        _conn3.close()

        keyboard.append([
            InlineKeyboardButton(
                f"📁 {folder} ({count})",
                callback_data=f"OPEN_FOLDER|{folder}"
            )
        ])

    if pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("◀ Prev", callback_data="QUIZ_FOLDER_PREV"))
        nav.append(InlineKeyboardButton(f"{page + 1}/{pages}", callback_data="QUIZ_FOLDER_NOP"))
        if page < pages - 1:
            nav.append(InlineKeyboardButton("Next ▶", callback_data="QUIZ_FOLDER_NEXT"))
        keyboard.append(nav)

    keyboard.append([
        InlineKeyboardButton("➕ Add Folder", callback_data="ADD_FOLDER"),
        InlineKeyboardButton("🏠 Home", callback_data="GO_HOME")
    ])

    await message.edit_text(
        "📂 Quiz Folder",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def show_database_menu(message, context):
    active_uid = get_active_user_id(context)

    _conn, _cur = get_db()
    _cur.execute(
        """
        SELECT id, name FROM question_bank_folders
        WHERE owner_id=? AND name != 'Default'
        ORDER BY name COLLATE NOCASE
        """,
        (active_uid,)
    )
    folders = _cur.fetchall()
    _conn.close()

    PER_PAGE = 5
    page = context.user_data.get("db_page", 0)
    total = len(folders)
    pages = (total - 1) // PER_PAGE + 1 if total else 1
    page = max(0, min(page, pages - 1))

    start = page * PER_PAGE
    end = start + PER_PAGE
    page_items = folders[start:end]

    keyboard = []

    _conn2, _cur2 = get_db()
    _cur2.execute(
        """
        SELECT COUNT(*) FROM question_bank qb
        JOIN question_bank_folders f ON f.id = qb.folder_id
        WHERE f.owner_id=? AND f.name='Default'
        """,
        (active_uid,)
    )
    default_count = _cur2.fetchone()[0]
    _conn2.close()

    keyboard.append([InlineKeyboardButton("🔍 Search Questions", callback_data="DB_SEARCH_START")])
    keyboard.append([
        InlineKeyboardButton(
            f"📁 Default Folder ({default_count})",
            callback_data="DB_OPEN|Default"
        )
    ])

    for folder_id, folder_name in page_items:
        _conn3, _cur3 = get_db()
        _cur3.execute("SELECT COUNT(*) FROM question_bank WHERE folder_id=?", (folder_id,))
        count = _cur3.fetchone()[0]
        _conn3.close()

        keyboard.append([
            InlineKeyboardButton(
                f"📁 {folder_name} ({count})",
                callback_data=f"DB_OPEN|{folder_name}"
            )
        ])

    if pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("◀ Prev", callback_data="DB_PREV"))
        nav.append(InlineKeyboardButton(f"{page + 1}/{pages}", callback_data="DB_NOP"))
        if page < pages - 1:
            nav.append(InlineKeyboardButton("Next ▶", callback_data="DB_NEXT"))
        keyboard.append(nav)

    keyboard.append([
        InlineKeyboardButton("➕ Add Folder", callback_data="DB_ADD"),
        InlineKeyboardButton("🏠 Home", callback_data="GO_HOME"),
    ])

    await message.edit_text(
        "🗄 Database:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

async def show_db_questions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    active_uid = get_active_user_id(context)
    query = update.callback_query
    await query.answer()

    if query.data.startswith("DB_OPEN|"):
        folder_name = query.data.split("|", 1)[1]
        context.user_data["db_folder_name"] = folder_name
        context.user_data["db_q_page"] = 0
    else:
        folder_name = context.user_data.get("db_folder_name")

    if not folder_name:
        await query.answer("❌ Folder context lost.", show_alert=True)
        return

    context.user_data["preview_mode"] = "DATABASE"
    PER_PAGE = 10
    page = context.user_data.get("db_q_page", 0)

    _conn, _cur = get_db()
    _cur.execute(
        "SELECT id FROM question_bank_folders WHERE owner_id=? AND name=?",
        (active_uid, folder_name)
    )
    row = _cur.fetchone()
    _conn.close()

    if not row:
        await flash_message(context.bot, query.message.chat_id, "❌ Folder not found.")
        return
    folder_id = row[0]

    _conn2, _cur2 = get_db()
    _cur2.execute(
        "SELECT id, question FROM question_bank WHERE folder_id=? ORDER BY question COLLATE NOCASE",
        (folder_id,)
    )
    rows = _cur2.fetchall()
    _conn2.close()

    keyboard = []

    if not rows:
        if folder_name != "Default":
            keyboard.append([
                InlineKeyboardButton("✏️ Rename", callback_data=f"DB_RENAME_FOLDER|{folder_name}"),
                InlineKeyboardButton("📥 Move Questions In", callback_data=f"DB_MOVE_IN|{folder_name}")
            ])
            keyboard.append([
                InlineKeyboardButton("🗑 Delete Folder", callback_data=f"DB_DELETE_FOLDER|{folder_name}"),
                InlineKeyboardButton("⬅️ Back", callback_data="HOME_DATABASE")
            ])
        else:
            keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="HOME_DATABASE")])
        await query.message.edit_text(
            f"📁 **{folder_name}**\n\n_No questions in this folder yet._",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
        return

    total = len(rows)
    pages = (total - 1) // PER_PAGE + 1
    page = max(0, min(page, pages - 1))
    context.user_data["db_q_page"] = page
    start = page * PER_PAGE
    end = start + PER_PAGE

    for qid, text in rows[start:end]:
        keyboard.append([InlineKeyboardButton(text[:50], callback_data=f"Q_{qid}")])

    if pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("◀ Prev", callback_data="DB_Q_PREV"))
        nav.append(InlineKeyboardButton(f"{page + 1}/{pages}", callback_data="DB_Q_NOP"))
        if page < pages - 1:
            nav.append(InlineKeyboardButton("Next ▶", callback_data="DB_Q_NEXT"))
        keyboard.append(nav)

    if folder_name != "Default":
        keyboard.append([
            InlineKeyboardButton("✏️ Rename", callback_data=f"DB_RENAME_FOLDER|{folder_name}"),
            InlineKeyboardButton("📥 Move Questions In", callback_data=f"DB_MOVE_IN|{folder_name}")
        ])
        keyboard.append([
            InlineKeyboardButton("🗑 Delete Folder", callback_data=f"DB_DELETE_FOLDER|{folder_name}"),
            InlineKeyboardButton("⬅️ Back", callback_data="HOME_DATABASE")
        ])
    else:
        keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="HOME_DATABASE")])

    await query.message.edit_text(
        f"📁 **{folder_name}**\n\nSelect a question:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

async def qb_pick_folder_menu(message, context):
    context.user_data["qb_selected"] = set()
    context.user_data.setdefault("qb_folder_page", 0)
    page = context.user_data["qb_folder_page"]
    PER_PAGE = 5

    _conn, _cur = get_db()
    _cur.execute(
        "SELECT name FROM question_bank_folders WHERE owner_id=?",
        (get_active_user_id(context),)
    )
    rows = [row[0] for row in _cur.fetchall()]
    _conn.close()

    default_folder = "Default"
    other_folders = sorted([f for f in rows if f != default_folder], key=str.lower)
    folders = [default_folder] + other_folders if default_folder in rows else other_folders

    total = len(folders)
    pages = (total - 1) // PER_PAGE + 1 if total else 1
    page = max(0, min(page, pages - 1))

    start = page * PER_PAGE
    end = start + PER_PAGE
    page_items = folders[start:end]

    keyboard = []

    for folder in page_items:
        _conn2, _cur2 = get_db()
        _cur2.execute(
            "SELECT id FROM question_bank_folders WHERE owner_id=? AND name=?",
            (get_active_user_id(context), folder)
        )
        row = _cur2.fetchone()
        _conn2.close()
        if not row:
            continue
        folder_id = row[0]

        _conn3, _cur3 = get_db()
        _cur3.execute("SELECT COUNT(*) FROM question_bank WHERE folder_id=?", (folder_id,))
        count = _cur3.fetchone()[0]
        _conn3.close()

        keyboard.append([
            InlineKeyboardButton(f"📁 {folder} ({count})", callback_data=f"QB_OPEN_FOLDER|{folder}")
        ])

    if pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("◀ Prev", callback_data="QB_FOLDER_PREV"))
        nav.append(InlineKeyboardButton(f"{page + 1}/{pages}", callback_data="QB_FOLDER_NOP"))
        if page < pages - 1:
            nav.append(InlineKeyboardButton("Next ▶", callback_data="QB_FOLDER_NEXT"))
        keyboard.append(nav)

    keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="EDIT_QUESTIONS")])

    await message.edit_text(
        "📚 **Question Bank**\n\nSelect a folder:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

async def db_search_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    context.user_data["state"] = "DB_SEARCH"
    context.user_data["db_search_menu_message"] = query.message

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Cancel", callback_data="DB_SEARCH_CANCEL")]
    ])

    msg = await query.message.reply_text(
        "🔍 Search Questions\n\n📝 Send keyword(s) to search:",
        reply_markup=keyboard
    )

    context.user_data["db_search_prompt_id"] = msg.message_id

async def db_search_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    chat_id = query.message.chat_id

    # 🧹 Delete prompt
    prompt_id = context.user_data.pop("db_search_prompt_id", None)
    if prompt_id:
        try:
            await context.bot.delete_message(chat_id, prompt_id)
        except:
            pass

    context.user_data.pop("state", None)
    context.user_data.pop("db_search_menu_message", None)

async def show_db_search_results(message, context):
    keyword = context.user_data.get("db_search_keyword", "")
    page = context.user_data.get("db_search_page", 0)
    PER_PAGE = 10

    _conn, _cur = get_db()
    _cur.execute(
        """
        SELECT qb.id, qb.question, qb.options, qb.correct
        FROM question_bank qb
        JOIN question_bank_folders f ON f.id = qb.folder_id
        WHERE f.owner_id = ? AND LOWER(qb.question) LIKE LOWER(?)
        ORDER BY qb.question COLLATE NOCASE
        """,
        (get_active_user_id(context), f"%{keyword}%")
    )
    rows = _cur.fetchall()
    _conn.close()

    keyboard = []

    if not rows:
        keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="HOME_DATABASE")])
        await message.edit_text(
            f"🔍 Search: *{keyword}*\n\n_No questions found._",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
        return

    total = len(rows)
    pages = (total - 1) // PER_PAGE + 1
    page = max(0, min(page, pages - 1))
    context.user_data["db_search_page"] = page

    start = page * PER_PAGE
    end = start + PER_PAGE

    for qid, q_text, options_raw, correct in rows[start:end]:
        opts = options_raw.split("||")
        correct_text = opts[correct] if 0 <= correct < len(opts) else "—"
        label = f"{q_text[:38]}… ✅ {correct_text[:20]}"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"DB_SEARCH_Q|{qid}")])

    if pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("◀ Prev", callback_data="DB_SEARCH_PREV"))
        nav.append(InlineKeyboardButton(f"{page + 1}/{pages}", callback_data="DB_SEARCH_NOP"))
        if page < pages - 1:
            nav.append(InlineKeyboardButton("Next ▶", callback_data="DB_SEARCH_NEXT"))
        keyboard.append(nav)

    keyboard.append([
        InlineKeyboardButton("🔍 New Search", callback_data="DB_SEARCH_START"),
        InlineKeyboardButton("⬅️ Back", callback_data="HOME_DATABASE")
    ])

    await message.edit_text(
        f"🔍 *{keyword}* — {total} result(s):",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

async def db_search_preview_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    chat_id = query.message.chat_id
    qid = int(query.data.split("|", 1)[1])
    context.user_data["active_question_id"] = qid
    context.user_data["preview_mode"] = "DATABASE"
    context.user_data["preview_return"] = "DB_SEARCH"

    _conn, _cur = get_db()
    _cur.execute(
        """
        SELECT question, image_file_id, options, correct, explanation
        FROM question_bank WHERE id=?
        """,
        (qid,)
    )
    row = _cur.fetchone()
    _conn.close()

    if not row:
        await query.answer("❌ Question not found.", show_alert=True)
        return

    question, image, options, correct, explanation = row
    options = options.split("||")

    text = f"📝 **{escape_md(question)}**\n\n"
    for i, opt in enumerate(options):
        marker = "✅" if i == correct else "◻️"
        text += f"{marker} {escape_md(opt)}\n"
    if explanation:
        text += f"\n🧾 _{escape_md(explanation)}_"

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✏️ Edit",   callback_data="EDIT_Q"),
            InlineKeyboardButton("⚙️ Manage", callback_data="MANAGE_Q"),
        ],
        [
            InlineKeyboardButton("🗑 Delete",  callback_data="DELETE_Q_FROM_DB"),
            InlineKeyboardButton("↩️ Return",  callback_data="RETURN_TO_QUESTIONS"),
        ]
    ])

    old_list_id = query.message.message_id

    if image:
        try:
            await context.bot.delete_message(chat_id, old_list_id)
        except:
            pass
        msg = await context.bot.send_photo(
            chat_id=chat_id,
            photo=image,
            caption=text,
            reply_markup=keyboard,
            parse_mode="Markdown"
        )
    else:
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=old_list_id,
                text=text,
                reply_markup=keyboard,
                parse_mode="Markdown"
            )
            msg = query.message
        except:
            try:
                await context.bot.delete_message(chat_id, old_list_id)
            except:
                pass
            msg = await context.bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=keyboard,
                parse_mode="Markdown"
            )

    context.user_data["question_preview_msg_id"] = msg.message_id
    context.user_data["db_search_list_deleted"] = True

# =========================
# MY QUIZZES
# =========================

async def show_quizzes_in_folder(message, context, folder):
    active_uid = get_active_user_id(context)
    context.user_data["folder_screen_message_object"] = message
    context.user_data["last_folder_screen_msg_id"] = message.message_id

    _conn, _cur = get_db()
    _cur.execute(
        "SELECT quiz_id, title FROM quizzes WHERE owner_id=? AND folder=?",
        (active_uid, folder)
    )
    rows = _cur.fetchall()
    _conn.close()

    rows = sorted(rows, key=lambda r: natural_sort_key(r[1]))

    page_key = f"folder_page_{folder}"
    page = context.user_data.get(page_key, 0)

    PER_PAGE = 5
    total = len(rows)
    pages = (total - 1) // PER_PAGE + 1 if total else 1
    page = max(0, min(page, pages - 1))

    start = page * PER_PAGE
    end = start + PER_PAGE

    keyboard = []

    for qid, title in rows[start:end]:
        keyboard.append([InlineKeyboardButton(f"📘 {title}", callback_data=f"QUIZ_{qid}")])

    if pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("◀ Prev", callback_data=f"FOLDER_PREV|{folder}"))
        nav.append(InlineKeyboardButton(f"{page + 1}/{pages}", callback_data="FOLDER_NOP"))
        if page < pages - 1:
            nav.append(InlineKeyboardButton("Next ▶", callback_data=f"FOLDER_NEXT|{folder}"))
        keyboard.append(nav)

    if folder != "Default":
        keyboard.append([
            InlineKeyboardButton("✏️ Rename", callback_data=f"RENAME_FOLDER|{folder}"),
            InlineKeyboardButton("🗑 Delete", callback_data=f"DELETE_FOLDER|{folder}"),
            InlineKeyboardButton("⬅️ Back", callback_data="BACK_TO_FOLDERS")
        ])
    else:
        keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="BACK_TO_FOLDERS")])

    title_label = "All Quizzes" if folder == "Default" else folder
    await message.edit_text(
        f"📁 {title_label}",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def open_folder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    folder = query.data.split("|", 1)[1] or "Default"

    context.user_data["current_folder"] = folder
    await show_quizzes_in_folder(query.message, context, folder)

async def rename_folder_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    folder = query.data.split("|", 1)[1]

    context.user_data["rename_folder"] = folder
    context.user_data["state"] = "RENAME_FOLDER"

    # Send prompt and store its message ID
    msg = await query.message.reply_text(
        f"✏️ Send new name for folder:\n\n📁 {folder}"
    )

    context.user_data["rename_prompt_msg_id"] = msg.message_id

async def add_folder_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    context.user_data["state"] = "ADD_FOLDER"

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Cancel", callback_data="CANCEL_ADD_FOLDER")]
    ])

    msg = await query.message.reply_text(
        "📁 Send new folder name:",
        reply_markup=keyboard
    )

    # 🔑 Store prompt ID for deletion
    context.user_data["add_folder_prompt_id"] = msg.message_id

    # 🔑 Store original folder screen message (important for refresh later)
    context.user_data["folder_screen_message_object"] = query.message

async def my_quizzes(update_or_message, context: ContextTypes.DEFAULT_TYPE):
    # Accept Message or CallbackQuery
    if hasattr(update_or_message, "message"):
        message = update_or_message.message
    else:
        message = update_or_message

    await show_quiz_folders(message, context)

async def delete_folder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    folder = query.data.split("|", 1)[1]

    if folder == "Default":
        await flash_message(context.bot, query.message.chat_id, "❌ Default folder cannot be deleted.")
        return

    # Save delete request
    context.user_data["confirm_delete"] = ("FOLDER", folder)

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Yes, delete", callback_data="CONFIRM_DELETE"),
            InlineKeyboardButton("❌ Cancel", callback_data="CANCEL_DELETE"),
        ]
    ])

    await query.message.reply_text(
        f"❗ Are you sure you want to delete the folder **{folder}**?\n\n"
        "All quizzes inside will be moved to **Default Folder**.",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )

# =========================
# QUIZ ACTION MENU
# =========================
async def quiz_action_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    quiz_id = query.data.replace("QUIZ_", "")
    context.user_data["active_quiz_id"] = quiz_id

    # 🔁 Reset question pagination when entering a quiz
    context.user_data["reset_q_page"] = True

    # 🔑 SAVE THE FOLDER THIS QUIZ BELONGS TO
    _conn_f, _cur_f = get_db()
    _cur_f.execute(
        "SELECT folder FROM quizzes WHERE quiz_id=? AND owner_id=?",
        (quiz_id, get_active_user_id(context))
    )
    row = _cur_f.fetchone()
    _conn_f.close()
    if row:
        context.user_data["last_quiz_folder"] = row[0]

    await show_quiz_action_menu(query.message, context)

async def move_quiz_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    quiz_id = context.user_data.get("active_quiz_id")
    if not quiz_id:
        return

    # 🔄 Reset pagination when opening
    context.user_data["move_quiz_folder_page"] = 0

    await show_move_quiz_folders(query.message, context)

async def show_move_quiz_folders(message, context):
    PER_PAGE = 5
    page = context.user_data.get("move_quiz_folder_page", 0)

    _conn, _cur = get_db()
    _cur.execute("""
        SELECT name FROM folders
        WHERE owner_id=?
        ORDER BY
            CASE WHEN name='Default' THEN 0 ELSE 1 END,
            name COLLATE NOCASE
    """, (get_active_user_id(context),))
    folders = [row[0] for row in _cur.fetchall()]
    _conn.close()

    total = len(folders)
    pages = (total - 1) // PER_PAGE + 1 if total else 1
    page = max(0, min(page, pages - 1))
    context.user_data["move_quiz_folder_page"] = page

    start = page * PER_PAGE
    end = start + PER_PAGE
    page_items = folders[start:end]

    keyboard = []

    for folder_name in page_items:
        _conn2, _cur2 = get_db()
        _cur2.execute("""
            SELECT COUNT(*) FROM quizzes
            WHERE owner_id=? AND folder=?
        """, (get_active_user_id(context), folder_name))
        count = _cur2.fetchone()[0]
        _conn2.close()

        keyboard.append([
            InlineKeyboardButton(
                f"📁 {folder_name} ({count})",
                callback_data=f"MOVE_QUIZ_TO|{folder_name}"
            )
        ])

    if pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("◀ Prev", callback_data="MOVE_FOLDER_PREV"))
        nav.append(InlineKeyboardButton(f"{page+1}/{pages}", callback_data="MOVE_NOP"))
        if page < pages - 1:
            nav.append(InlineKeyboardButton("Next ▶", callback_data="MOVE_FOLDER_NEXT"))
        keyboard.append(nav)

    keyboard.append([
        InlineKeyboardButton("➕ Create new Folder", callback_data="MOVE_CREATE_FOLDER")
    ])
    keyboard.append([
        InlineKeyboardButton("⬅️ Back", callback_data="BACK_TO_ACTION")
    ])

    await safe_edit_message(
        message,
        "📂 Move quiz to folder",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def move_create_folder_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # Remember we are creating a folder for moving a quiz
    context.user_data["state"] = "MOVE_ADD_FOLDER"

    msg = await query.message.reply_text(
        "➕ Send the new folder name for this quiz:"
    )

    # 🔑 Store prompt ID for cleanup
    context.user_data["move_create_folder_prompt_id"] = msg.message_id

async def database_add_folder_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # ✅ Enter Database folder creation mode (do NOT hard clear — preserve menu reference)
    context.user_data["state"] = "DB_ADD_FOLDER"

    # 🔑 Store the database menu message for later replacement
    context.user_data["db_menu_message_object"] = query.message

    msg = await query.message.reply_text(
        "➕ Send the name of the new Database folder:"
    )

    # 🔑 Store prompt ID for cleanup
    context.user_data["db_add_folder_prompt_id"] = msg.message_id

async def move_quiz_to_folder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    folder = query.data.split("|", 1)[1]
    quiz_id = context.user_data.get("active_quiz_id")
    if not quiz_id:
        return

    async with DB_LOCK:
        _conn_mv, _cur_mv = get_db()
        _cur_mv.execute(
            "UPDATE quizzes SET folder=? WHERE quiz_id=? AND owner_id=?",
            (folder, quiz_id, get_active_user_id(context))
        )
        _conn_mv.commit()
        _conn_mv.close()

    await flash_message(context.bot, query.message.chat_id,
        f"✅ Quiz moved to 📁 {folder}"
    )

    await show_quiz_action_menu(query.message, context)

async def show_quiz_action_menu(message, context):
    quiz_id = context.user_data.get("active_quiz_id")
    if not quiz_id:
        return

    _conn, _cur = get_db()
    _cur.execute(
        "SELECT title, description, timer, shuffle_q, shuffle_a FROM quizzes WHERE quiz_id=?",
        (quiz_id,)
    )
    row = _cur.fetchone()
    _conn.close()

    if not row:
        return
    title, desc, timer, sq, sa = row

    _conn2, _cur2 = get_db()
    _cur2.execute("SELECT COUNT(*) FROM quiz_question_links WHERE quiz_id=?", (quiz_id,))
    total_questions = _cur2.fetchone()[0]
    _conn2.close()

    text = f"📘 **{escape_md(title)}**"
    if desc:
        text += f"\n📝 _{escape_md(desc)}_"
    text += "\n\n"
    text += f"📊 Questions: {total_questions}    ⏱ Timer: {timer}s"
    text += (
        f"\n🔀 Questions: {'ON' if sq else 'OFF'}"
        f"   🔀 Options: {'ON' if sa else 'OFF'}"
    )

    keyboard = [
        [
            InlineKeyboardButton("▶️ Start this Quiz", callback_data="START_THIS"),
            InlineKeyboardButton("📤 Post this Quiz", callback_data="POST_QUIZ"),
        ],
        [
            InlineKeyboardButton("✏️ Edit this Quiz", callback_data="EDIT_THIS"),
            InlineKeyboardButton("📁 Move this Quiz", callback_data="MOVE_QUIZ"),
        ],
        [
            InlineKeyboardButton("🗑 Delete this Quiz", callback_data="DELETE_QUIZ"),
            InlineKeyboardButton("⬅️ Back", callback_data="BACK_TO_QUIZZES"),
        ],
    ]

    await message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    context.user_data["quiz_overview_msg_id"] = message.message_id

# =========================
# EDIT CORRECT ANSWER FLOW
# =========================
async def edit_correct_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    context.user_data["edit_q_state"] = "EDIT_OPTION_1"
    context.user_data["edit_options"] = []

    await query.message.reply_text("Send NEW option 1:")

# =========================
# EDIT MENU
# =========================
async def edit_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    context.user_data["reset_q_page"] = True

    quiz_id = context.user_data.get("active_quiz_id")
    if not quiz_id:
        return
    if not verify_quiz_owner(quiz_id, context):
        await query.answer("❌ Access denied.", show_alert=True)
        return

    _conn, _cur = get_db()
    _cur.execute(
        "SELECT title, description, timer, shuffle_q, shuffle_a FROM quizzes WHERE quiz_id=?",
        (quiz_id,)
    )
    row = _cur.fetchone()
    _conn.close()

    if not row:
        return
    title, desc, timer, sq, sa = row

    _conn2, _cur2 = get_db()
    _cur2.execute("SELECT COUNT(*) FROM quiz_question_links WHERE quiz_id=?", (quiz_id,))
    total_questions = _cur2.fetchone()[0]
    _conn2.close()

    text = (
        f"📘 **{escape_md(title)}**"
        + (f"\n📝 _{escape_md(desc)}_" if desc else "")
        + "\n\n"
        + f"📊 Questions: {total_questions}    ⏱ Timer: {timer}s"
        + f"\n🔀 Questions: {'ON' if sq else 'OFF'}"
        + f"   🔀 Options: {'ON' if sa else 'OFF'}"
    )

    keyboard = [
        [
            InlineKeyboardButton("📝 Edit Title",       callback_data="EDIT_TITLE"),
            InlineKeyboardButton("🧾 Edit Description", callback_data="EDIT_DESC"),
        ],
        [
            InlineKeyboardButton("⏱ Timer Settings",   callback_data="EDIT_TIMER"),
            InlineKeyboardButton("🔀 Shuffle Settings", callback_data="EDIT_SHUFFLE"),
        ],
        [
            InlineKeyboardButton("❓ Show Questions",   callback_data="EDIT_QUESTIONS"),
            InlineKeyboardButton("⬅️ Back",            callback_data="BACK_TO_ACTION"),
        ],
    ]

    await query.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

# =========================
# EDIT ENTRY POINTS
# =========================
async def edit_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    context.user_data["state"] = "EDIT_TITLE"

    msg = await query.message.reply_text(
        "📝 Send new title:",
        reply_markup=InlineKeyboardMarkup([
            cancel_edit_button()
        ])
    )

    # 🔑 Remember prompt message for cleanup
    context.user_data["edit_title_prompt_id"] = msg.message_id

async def edit_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    context.user_data["state"] = "EDIT_DESC"

    msg = await query.message.reply_text(
        "🧾 Send new Quiz description:",
        reply_markup=InlineKeyboardMarkup([
            cancel_edit_button()
        ])
    )

    # 🔑 Remember prompt message for cleanup
    context.user_data["edit_desc_prompt_id"] = msg.message_id

# =========================
# ⏱ TIMER MENU (REAL)
# =========================
async def edit_timer_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    keyboard = [
        [
            InlineKeyboardButton("10 seconds", callback_data="SET_TIMER_10"),
            InlineKeyboardButton("1 minute", callback_data="SET_TIMER_60"),
            InlineKeyboardButton("15 minutes", callback_data="SET_TIMER_900"),
        ],
        [
            InlineKeyboardButton("15 seconds", callback_data="SET_TIMER_15"),
            InlineKeyboardButton("3 minutes", callback_data="SET_TIMER_180"),
            InlineKeyboardButton("30 minutes", callback_data="SET_TIMER_1800"),
        ],
        [
            InlineKeyboardButton("30 seconds", callback_data="SET_TIMER_30"),
            InlineKeyboardButton("5 minutes", callback_data="SET_TIMER_300"),
            InlineKeyboardButton("60 minutes", callback_data="SET_TIMER_3600"),
        ],
        [
            InlineKeyboardButton("❌ Cancel", callback_data="CANCEL_TIMER_MENU")
        ],
    ]

    msg = await query.message.reply_text(
        "⏱ Choose timer:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

    # 🔑 Remember prompt message for cleanup
    context.user_data["edit_timer_prompt_id"] = msg.message_id

async def set_timer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    seconds = int(query.data.replace("SET_TIMER_", ""))
    quiz_id = context.user_data.get("active_quiz_id")
    if not quiz_id:
        return

    try:
        async with DB_LOCK:
            _conn, _cur = get_db()
            _cur.execute(
                "UPDATE quizzes SET timer=? WHERE quiz_id=?",
                (seconds, quiz_id)
            )
            _conn.commit()
            _conn.close()
    except Exception as e:
        print("⚠️ Failed to update timer:", e)
        await query.answer("❌ Failed to update timer.", show_alert=True)
        return

    confirm_msg = await query.message.reply_text(
        f"✅ Timer set to {seconds}s."
    )

    prompt_id = context.user_data.pop("edit_timer_prompt_id", None)

    if prompt_id:
        try:
            await context.bot.delete_message(
                chat_id=query.message.chat_id,
                message_id=prompt_id
            )
        except:
            pass

    try:
        await confirm_msg.delete()
    except:
        pass

    # 🔄 SYNC: Refresh all active group posts for this quiz
    asyncio.create_task(
        refresh_all_group_posts_for_quiz(quiz_id, context)
    )

    overview_id = context.user_data.get("quiz_overview_msg_id")

    if overview_id:
        await show_quiz_action_menu_by_id(
            chat_id=query.message.chat_id,
            message_id=overview_id,
            context=context
        )

####################################################################################################################################################################################################################################
# CODE BY PARTS - PART 2
####################################################################################################################################################################################################################################
# =========================
# 🔀 SHUFFLE MENU (REAL)
# =========================
async def edit_shuffle_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    quiz_id = context.user_data.get("active_quiz_id")
    if not quiz_id:
        return
    _conn, _cur = get_db()
    _cur.execute(
        "SELECT shuffle_q, shuffle_a FROM quizzes WHERE quiz_id=?",
        (quiz_id,)
    )
    sq, sa = _cur.fetchone()
    _conn.close()

    keyboard = [
        [
            InlineKeyboardButton(
                f"Shuffle Questions: {'ON' if sq else 'OFF'}",
                callback_data="TOGGLE_Q"
            ),
            InlineKeyboardButton(
                f"Shuffle Options: {'ON' if sa else 'OFF'}",
                callback_data="TOGGLE_A"
            ),
        ],
        [
            InlineKeyboardButton("❌ Cancel", callback_data="CANCEL_SHUFFLE_MENU")
        ],
    ]

    msg = await query.message.reply_text(
        "🔀 Shuffle Settings:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

    # 🔑 Remember shuffle menu message for cleanup
    context.user_data["shuffle_menu_msg_id"] = msg.message_id

async def toggle_shuffle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    quiz_id = context.user_data.get("active_quiz_id")
    if not quiz_id:
        return

    try:
        async with DB_LOCK:
            _conn, _cur = get_db()
            if query.data == "TOGGLE_Q":
                _cur.execute(
                    "UPDATE quizzes SET shuffle_q = 1 - shuffle_q WHERE quiz_id=?",
                    (quiz_id,)
                )
            else:
                _cur.execute(
                    "UPDATE quizzes SET shuffle_a = 1 - shuffle_a WHERE quiz_id=?",
                    (quiz_id,)
                )
            _conn.commit()
            _conn.close()

    except Exception as e:
        print("⚠️ Failed to toggle shuffle:", e)
        await query.answer("❌ Failed to update setting.", show_alert=True)
        return

    msg_id = context.user_data.pop("shuffle_menu_msg_id", None)
    if msg_id:
        try:
            await context.bot.delete_message(
                chat_id=query.message.chat_id,
                message_id=msg_id
            )
        except:
            pass

    # 🔄 SYNC: Refresh all active group posts for this quiz
    asyncio.create_task(
        refresh_all_group_posts_for_quiz(quiz_id, context)
    )

    overview_id = context.user_data.get("quiz_overview_msg_id")

    if overview_id:
        await show_quiz_action_menu_by_id(
            chat_id=query.message.chat_id,
            message_id=overview_id,
            context=context
        )

# =========================
# NAVIGATION
# =========================
async def go_home(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    context.user_data.pop("preview_mode", None)

    user_id = query.from_user.id

    rows = [
        [
            InlineKeyboardButton("📂 Quiz Folder", callback_data="HOME_MY_QUIZZES"),
            InlineKeyboardButton("➕ Create a new Quiz", callback_data="HOME_CREATE"),
        ],
        [
            InlineKeyboardButton("🗄 Database", callback_data="HOME_DATABASE"),
            InlineKeyboardButton("❓ Create a Question", callback_data="HOME_CREATE_QUESTION"),
        ],
    ]

    # 👑 Only the owner sees Manage Subscribers
    if user_id == OWNER_USER_ID:
        rows.append([
            InlineKeyboardButton("👥 Manage Subscribers", callback_data="HOME_MANAGE_SUBSCRIBERS"),
        ])

    await query.message.edit_text(
        "🧠 Welcome to TeleQuiz (Admin Panel)\n\nPlease choose an option to start 👇:",
        reply_markup=InlineKeyboardMarkup(rows)
    )

def home_button():
    return [InlineKeyboardButton("🏠 Home", callback_data="GO_HOME")]

def cancel_edit_button():
    return [
        InlineKeyboardButton("❌ Cancel", callback_data="BACK_TO_EDIT_MENU")
    ]

async def home_create_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # 🔑 Generate quiz_id FIRST
    context.user_data["quiz_id"] = str(uuid.uuid4())

    # 🔑 Then set state
    context.user_data["state"] = "CREATE_QUIZ"

    # 📝 Send prompt and store its ID
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Cancel", callback_data="CANCEL_CREATE_QUIZ")]
    ])

    prompt_msg = await query.message.reply_text(
        "📝 Send quiz title:",
        reply_markup=keyboard
    )

    context.user_data["create_quiz_prompt_id"] = prompt_msg.message_id

    # 🔑 Store the menu message so we can edit it later
    context.user_data["quiz_overview_msg_id"] = query.message.message_id

async def back_to_edit_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # 🧹 Delete Edit Title prompt (if exists)
    title_prompt_id = context.user_data.pop("edit_title_prompt_id", None)
    if title_prompt_id:
        try:
            await context.bot.delete_message(
                chat_id=query.message.chat_id,
                message_id=title_prompt_id
            )
        except:
            pass

    # 🧹 Delete Edit Description prompt (if exists)
    desc_prompt_id = context.user_data.pop("edit_desc_prompt_id", None)
    if desc_prompt_id:
        try:
            await context.bot.delete_message(
                chat_id=query.message.chat_id,
                message_id=desc_prompt_id
            )
        except:
            pass

    # 🧹 Delete Edit Timer prompt (if exists)
    timer_prompt_id = context.user_data.pop("edit_timer_prompt_id", None)
    if timer_prompt_id:
        try:
            await context.bot.delete_message(
                chat_id=query.message.chat_id,
                message_id=timer_prompt_id
            )
        except:
            pass

    # 🔒 Exit edit mode silently
    context.user_data["state"] = None

    # 🚫 DO NOT send any new menu
    # Previous quiz overview remains visible

async def home_my_quizzes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # Reuse existing logic
    context.user_data["quiz_folder_page"] = 0
    await my_quizzes(query.message, context)

async def home_database(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    context.user_data.pop("preview_mode", None)

    # Reset database pagination
    context.user_data["db_page"] = 0

    await show_database_menu(query.message, context)

async def home_create_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✏️ Create Manually", callback_data="HOME_CREATE_MANUALLY"),
            InlineKeyboardButton("📷 Send Photo",      callback_data="HOME_CREATE_PHOTO"),
        ],
        [InlineKeyboardButton("❌ Cancel", callback_data="GO_HOME")],
    ])

    await query.message.edit_text(
        "❓ Create a Question\n\nChoose how to create your question:",
        reply_markup=keyboard
    )

async def home_create_manually(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # 🔒 Clear any quiz-specific state
    context.user_data.pop("active_quiz_id", None)
    context.user_data.pop("active_question_id", None)
    context.user_data.pop("edit_q_field", None)
    context.user_data.pop("add_q_state", None)
    context.user_data.pop("new_question", None)

    # 🧹 Clear any leftover duplicate tracking (safety)
    context.user_data.pop("create_q_prompt_msg_id", None)
    context.user_data.pop("last_user_question_msg_id", None)
    context.user_data.pop("pending_duplicate_text", None)

    # 🔑 Initialize Question Flow Tracker
    context.user_data["question_flow_msgs"] = []

    # ✅ Start pure Question Bank creation flow
    context.user_data["add_q_state"] = "NEW_Q_TEXT"
    context.user_data["new_question"] = {"options": []}

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Cancel", callback_data="CANCEL_CREATE_QUESTION")]
    ])

    await query.message.edit_text(
        "❓ Create a Question\n\n📝 Send question text:",
        reply_markup=keyboard
    )

    # 🔑 Track the prompt message (now the edited menu message)
    context.user_data["question_flow_msgs"].append(query.message.message_id)

    # 🔑 Track specifically for duplicate cancel cleanup
    context.user_data["create_q_prompt_msg_id"] = query.message.message_id

async def home_create_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # 🔑 Set state — waiting for the user to send a photo
    context.user_data["add_q_state"]       = "NEW_Q_PHOTO_WAIT"
    context.user_data["new_question"]       = {"options": []}
    context.user_data["question_flow_msgs"] = []

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Cancel", callback_data="CANCEL_CREATE_QUESTION")]
    ])

    await query.message.edit_text(
        "📷 *Send Photo*\n\n"
        "Send a clear photo of your question.\n"
        "Make sure the text and answer options are fully visible.",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )

    # 🔑 Track this message for cleanup
    context.user_data["create_q_prompt_msg_id"] = query.message.message_id
    context.user_data["question_flow_msgs"].append(query.message.message_id)

async def back_to_folders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    await show_quiz_folders(query.message, context)

async def back_to_quizzes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    folder = context.user_data.get("last_quiz_folder")

    if not folder:
        # fallback safety
        await show_quiz_folders(query.message, context)
        return

    await show_quizzes_in_folder(query.message, context, folder)

async def back_to_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # 🔁 Reset question pagination
    context.user_data["reset_q_page"] = True

    await show_quiz_action_menu(query.message, context)

async def placeholder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await flash_message(context.bot, query.message.chat_id, "🚧 Coming next.")


# =========================
# SHOW QUESTIONS (STEP 7.1)
# =========================
async def show_questions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    context.user_data["preview_mode"] = "QUIZ"
    context.user_data.setdefault("selected_questions", set())

    quiz_id = context.user_data.get("active_quiz_id")
    if not quiz_id:
        return

    if context.user_data.get("reset_q_page", True):
        context.user_data["q_page"] = 0
        context.user_data["reset_q_page"] = False

    page = context.user_data.get("q_page", 0)

    _conn, _cur = get_db()
    _cur.execute(
        """
        SELECT qb.id, qb.question
        FROM quiz_question_links ql
        JOIN question_bank qb ON qb.id = ql.question_id
        WHERE ql.quiz_id=?
        ORDER BY ql.position, qb.question COLLATE NOCASE
        """,
        (quiz_id,)
    )
    rows = _cur.fetchall()
    _conn.close()

    total = len(rows)
    start = page * QUESTIONS_PER_PAGE
    end = start + QUESTIONS_PER_PAGE

    keyboard = []
    keyboard.append([InlineKeyboardButton("➕ Add from Question Bank", callback_data="QB_PICK_FOLDER")])

    for i, (qid, q) in enumerate(rows[start:end], start=start + 1):
        keyboard.append([
            InlineKeyboardButton(f"{i}. {q[:40]}", callback_data=f"Q_{qid}")
        ])

    pages = (total - 1) // QUESTIONS_PER_PAGE + 1 if total else 1
    if pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("◀️ Prev", callback_data="QPAGE_PREV"))
        nav.append(InlineKeyboardButton(f"{page + 1}/{pages}", callback_data="QPAGE_NOP"))
        if page < pages - 1:
            nav.append(InlineKeyboardButton("Next ▶️", callback_data="QPAGE_NEXT"))
        keyboard.append(nav)

    keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="EDIT_THIS")])

    await query.message.edit_text(
        "❓ Questions in this quiz:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# =========================
# ADD NEW QUESTION (STEP 7.3)
# =========================
async def add_new_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    context.user_data["add_q_state"] = "NEW_Q_TEXT"
    context.user_data["new_question"] = {}
    context.user_data["new_question"]["options"] = []

    await query.message.reply_text("📝 Send question text:")

# =========================
# MESSAGE-SAFE RETURN TO QUESTIONS
# =========================
async def show_questions_from_message(message, context):
    quiz_id = context.user_data.get("active_quiz_id")
    if not quiz_id:
        return

    page = context.user_data.get("q_page", 0)
    context.user_data["preview_mode"] = "QUIZ"

    _conn, _cur = get_db()
    _cur.execute(
        """
        SELECT qb.id, qb.question
        FROM quiz_question_links ql
        JOIN question_bank qb ON qb.id = ql.question_id
        WHERE ql.quiz_id=?
        ORDER BY ql.position, qb.question COLLATE NOCASE
        """,
        (quiz_id,)
    )
    rows = _cur.fetchall()
    _conn.close()

    total = len(rows)
    start = page * QUESTIONS_PER_PAGE
    end = start + QUESTIONS_PER_PAGE

    keyboard = []
    keyboard.append([
        InlineKeyboardButton("➕ Add from Question Bank", callback_data="QB_PICK_FOLDER")
    ])

    for i, (qid, q) in enumerate(rows[start:end], start=start + 1):
        keyboard.append([
            InlineKeyboardButton(f"{i}. {q[:40]}", callback_data=f"Q_{qid}")
        ])

    pages = (total - 1) // QUESTIONS_PER_PAGE + 1 if total else 1
    if pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("◀️ Prev", callback_data="QPAGE_PREV"))
        nav.append(InlineKeyboardButton(f"{page + 1}/{pages}", callback_data="QPAGE_NOP"))
        if page < pages - 1:
            nav.append(InlineKeyboardButton("Next ▶️", callback_data="QPAGE_NEXT"))
        keyboard.append(nav)

    keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="EDIT_THIS")])

    await message.edit_text(
        "❓ Questions in this quiz:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def questions_prev(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    context.user_data["q_page"] = max(
        0,
        context.user_data.get("q_page", 0) - 1
    )

    await show_questions(update, context)

async def questions_next(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    context.user_data["q_page"] = context.user_data.get("q_page", 0) + 1

    await show_questions(update, context)

async def skip_question_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # Only valid during image step
    if context.user_data.get("add_q_state") != "NEW_Q_IMAGE":
        return

    # ✅ NO early bulk delete here — mass declutter happens at the end in save_new_question

    # Proceed to Option 1
    context.user_data["new_question"]["image"] = None
    context.user_data["add_q_state"] = "NEW_Q_OPTION_1"

    msg = await query.message.reply_text("➡️ Send option 1:")

    # Track message for final mass declutter
    context.user_data.setdefault("question_flow_msgs", []).append(msg.message_id)

async def skip_question_explanation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # Only valid during explanation step
    if context.user_data.get("add_q_state") != "NEW_Q_EXPLANATION":
        return

    context.user_data["state"] = None
    context.user_data["new_question"]["explanation"] = None
    await save_new_question(query.message, context)

async def choose_correct_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    message = query.message
    correct_index = int(query.data.replace("CORRECT_", ""))

    context.user_data["new_question"]["correct"] = correct_index

    # ✅ Show green check on selected answer immediately
    opts = context.user_data["new_question"]["options"]
    labels = ["1️⃣", "2️⃣", "3️⃣", "4️⃣"]
    updated_keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(
            f"{'✅ ' if i == correct_index else ''}{labels[i]} {opts[i]}",
            callback_data="LOCKED"
        )]
        for i in range(len(opts))
    ])

    try:
        await query.message.edit_reply_markup(reply_markup=updated_keyboard)
    except Exception:
        pass

    context.user_data["add_q_state"] = "NEW_Q_EXPLANATION"
    context.user_data["state"] = None

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("⏭ Skip explanation", callback_data="SKIP_Q_EXPLANATION")]
    ])

    msg = await message.reply_text(
        "📝 Send explanation:",
        reply_markup=keyboard
    )
    context.user_data.setdefault("question_flow_msgs", []).append(msg.message_id)

async def save_new_question(message, context):
    q = context.user_data["new_question"]
    options_text = "||".join(q["options"])
    active_uid = get_active_user_id(context)

    _conn, _cur = get_db()
    _cur.execute(
        "SELECT id FROM question_bank_folders WHERE owner_id=? AND name='Default'",
        (active_uid,)
    )
    folder_row = _cur.fetchone()
    _conn.close()

    if not folder_row:
        async with DB_LOCK:
            _conn2, _cur2 = get_db()
            _cur2.execute(
                "INSERT OR IGNORE INTO question_bank_folders (owner_id, name) VALUES (?, 'Default')",
                (active_uid,)
            )
            _conn2.commit()
            _conn2.close()

        _conn3, _cur3 = get_db()
        _cur3.execute(
            "SELECT id FROM question_bank_folders WHERE owner_id=? AND name='Default'",
            (active_uid,)
        )
        folder_row = _cur3.fetchone()
        _conn3.close()

    if not folder_row:
        await message.reply_text("❌ Failed to resolve question folder. Please contact the bot admin.")
        context.user_data.pop("add_q_state", None)
        context.user_data.pop("new_question", None)
        return

    folder_id = folder_row[0]

    async with DB_LOCK:
        _conn4, _cur4 = get_db()
        _cur4.execute(
            """
            INSERT INTO question_bank (folder_id, question, image_file_id, options, correct, explanation)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                folder_id,
                q["text"],
                q.get("image"),
                options_text,
                q["correct"],
                q.get("explanation")
            )
        )
        _conn4.commit()
        _conn4.close()

    context.user_data.pop("add_q_state", None)
    context.user_data.pop("new_question", None)

    confirm = await message.reply_text("✅ Question saved to Question Bank.")
    context.user_data.setdefault("question_flow_msgs", []).append(confirm.message_id)

    await asyncio.sleep(2)

    chat_id = message.chat_id
    delete_tasks = [
        context.bot.delete_message(chat_id, msg_id)
        for msg_id in context.user_data.get("question_flow_msgs", [])
    ]
    if delete_tasks:
        await asyncio.gather(*delete_tasks, return_exceptions=True)

    context.user_data.pop("question_flow_msgs", None)

    if context.user_data.get("active_quiz_id"):
        context.user_data["reset_q_page"] = True
        await show_questions_from_message(message, context)
        return

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Cancel", callback_data="CANCEL_CREATE_QUESTION")]
    ])

    context.user_data["add_q_state"] = "NEW_Q_TEXT"
    context.user_data["new_question"] = {"options": []}

    msg = await message.reply_text(
        "❓ Create a Question\n\n📝 Send question text:",
        reply_markup=keyboard
    )

    context.user_data["question_flow_msgs"] = [msg.message_id]
    context.user_data["create_q_prompt_msg_id"] = msg.message_id

async def preview_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    chat_id = query.message.chat_id

    old_preview_id = context.user_data.pop("question_preview_msg_id", None)
    if old_preview_id:
        try:
            await context.bot.delete_message(chat_id, old_preview_id)
        except Exception:
            pass

    old_list_id = query.message.message_id

    qid = int(query.data.replace("Q_", ""))
    if not verify_question_owner(qid, context):
        await query.answer("❌ Access denied.", show_alert=True)
        return
    context.user_data["active_question_id"] = qid

    _conn, _cur = get_db()
    _cur.execute(
        "SELECT question, image_file_id, options, correct, explanation FROM question_bank WHERE id=?",
        (qid,)
    )
    row = _cur.fetchone()
    _conn.close()

    if not row:
        await context.bot.send_message(chat_id, "❌ Question not found.")
        return

    question, image, options, correct, explanation = row
    options = options.split("||")

    text = f"📝 **{escape_md(question)}**\n\n"
    for i, opt in enumerate(options):
        marker = "✅" if i == correct else "◻️"
        text += f"{marker} {escape_md(opt)}\n"
    if explanation:
        text += f"\n🧾 _{escape_md(explanation)}_"

    preview_mode = context.user_data.get("preview_mode", "QUIZ")
    delete_callback = "DELETE_Q_FROM_DB" if preview_mode == "DATABASE" else "DELETE_Q_FROM_QUIZ"

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✏️ Edit",   callback_data="EDIT_Q"),
            InlineKeyboardButton("⚙️ Manage", callback_data="MANAGE_Q"),
        ],
        [
            InlineKeyboardButton("🗑 Delete",   callback_data=delete_callback),
            InlineKeyboardButton("↩️ Return",   callback_data="RETURN_TO_QUESTIONS"),
        ]
    ])

    if image:
        try:
            await context.bot.delete_message(chat_id, old_list_id)
        except:
            pass
        msg = await context.bot.send_photo(
            chat_id=chat_id,
            photo=image,
            caption=text,
            reply_markup=keyboard,
            parse_mode="Markdown"
        )
    else:
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=old_list_id,
                text=text,
                reply_markup=keyboard,
                parse_mode="Markdown"
            )
            msg = query.message
        except:
            try:
                await context.bot.delete_message(chat_id, old_list_id)
            except:
                pass
            msg = await context.bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=keyboard,
                parse_mode="Markdown"
            )

    context.user_data["question_preview_msg_id"] = msg.message_id

async def rebuild_question_preview(chat_id, context):
    qid = context.user_data.get("active_question_id")
    if not qid:
        return

    _conn, _cur = get_db()
    _cur.execute(
        "SELECT question, image_file_id, options, correct, explanation FROM question_bank WHERE id=?",
        (qid,)
    )
    row = _cur.fetchone()
    _conn.close()

    if not row:
        return

    question, image, options, correct, explanation = row
    options = options.split("||")

    text = f"📝 **{escape_md(question)}**\n\n"
    for i, opt in enumerate(options):
        marker = "✅" if i == correct else "◻️"
        text += f"{marker} {escape_md(opt)}\n"
    if explanation:
        text += f"\n🧾 _{escape_md(explanation)}_"

    preview_mode = context.user_data.get("preview_mode", "QUIZ")
    delete_callback = "DELETE_Q_FROM_DB" if preview_mode == "DATABASE" else "DELETE_Q_FROM_QUIZ"

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✏️ Edit",   callback_data="EDIT_Q"),
            InlineKeyboardButton("⚙️ Manage", callback_data="MANAGE_Q"),
        ],
        [
            InlineKeyboardButton("🗑 Delete",  callback_data=delete_callback),
            InlineKeyboardButton("↩️ Return",  callback_data="RETURN_TO_QUESTIONS"),
        ]
    ])

    existing_msg_id = context.user_data.get("question_preview_msg_id")

    if existing_msg_id:
        try:
            if image:
                await context.bot.edit_message_caption(
                    chat_id=chat_id,
                    message_id=existing_msg_id,
                    caption=text,
                    reply_markup=keyboard,
                    parse_mode="Markdown"
                )
            else:
                await context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=existing_msg_id,
                    text=text,
                    reply_markup=keyboard,
                    parse_mode="Markdown"
                )
            return
        except Exception:
            try:
                await context.bot.delete_message(chat_id, existing_msg_id)
            except Exception:
                pass
            context.user_data.pop("question_preview_msg_id", None)

    if image:
        msg = await context.bot.send_photo(
            chat_id=chat_id,
            photo=image,
            caption=text,
            reply_markup=keyboard,
            parse_mode="Markdown"
        )
    else:
        msg = await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=keyboard,
            parse_mode="Markdown"
        )

    context.user_data["question_preview_msg_id"] = msg.message_id

async def show_question_preview_by_id(chat_id, context):
    """
    Safely rebuilds the question preview without requiring callback_query.
    Used after editing image.
    """

    qid = context.user_data.get("active_question_id")
    if not qid:
        return

    _conn_p, _cur_p = get_db()
    _cur_p.execute(
        """
        SELECT question, image_file_id, options, correct, explanation
        FROM question_bank
        WHERE id=?
        """,
        (qid,)
    )
    row = _cur_p.fetchone()
    _conn_p.close()
    if not row:
        return

    question, image, options, correct, explanation = row
    options = options.split("||")

    text = f"📝 **{escape_md(question)}**\n\n"
    for i, opt in enumerate(options):
        marker = "✅" if i == correct else "◻️"
        text += f"{marker} {escape_md(opt)}\n"

    if explanation:
        text += f"\n🧾 _{escape_md(explanation)}_"

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✏️ Edit", callback_data="EDIT_Q"),
            InlineKeyboardButton("⚙️ Manage", callback_data="MANAGE_Q"),
        ],
        [
            InlineKeyboardButton("🗑 Delete", callback_data="DELETE_QUESTION"),
            InlineKeyboardButton("↩️ Return", callback_data="RETURN_TO_QUESTIONS"),
        ]
    ])

    if image:
        await context.bot.send_photo(
            chat_id=chat_id,
            photo=image,
            caption=text,
            reply_markup=keyboard,
            parse_mode="Markdown"
        )
    else:
        await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=keyboard,
            parse_mode="Markdown"
        )

async def edit_question_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
 
    qid = context.user_data.get("active_question_id")
    if not qid:
        await flash_message(context.bot, query.message.chat_id, "❌ No question selected.")
        return
 
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📝 Question Text", callback_data="EDIT_Q_TEXT"),
            InlineKeyboardButton("🖼 Edit Image",     callback_data="EDIT_Q_IMAGE"),
        ],
        [
            InlineKeyboardButton("🔁 Edit Choices",  callback_data="EDIT_Q_OPTIONS"),
            InlineKeyboardButton("✅ Edit Answer",    callback_data="EDIT_Q_CORRECT"),
        ],
        [
            InlineKeyboardButton("🧾 Explanation",   callback_data="EDIT_Q_EXPLANATION"),
            InlineKeyboardButton("↩️ Return",        callback_data="RETURN_TO_PREVIEW"),
        ],
    ])
 
    # Keep the same message — only swap the buttons
    try:
        await query.message.edit_reply_markup(reply_markup=keyboard)
    except Exception as e:
        print("⚠️ Failed to replace edit menu:", e)
 
    # Ensure the stored ID always points to this message
    context.user_data["question_preview_msg_id"] = query.message.message_id

async def back_to_question_options(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    chat_id = query.message.chat_id

    # 🧹 Delete the Edit menu message
    try:
        await query.message.delete()
    except:
        pass

    # 🔄 Rebuild Question Preview cleanly
    await rebuild_question_preview(chat_id, context)

async def edit_question_text_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # 🔒 Safety: ensure a Question Bank question is active
    qid = context.user_data.get("active_question_id")
    if not qid:
        await flash_message(context.bot, query.message.chat_id, "❌ No question selected.")
        return

    # 🔄 Clear other edit modes
    context.user_data.pop("add_q_state", None)
    context.user_data.pop("edit_options", None)

    # ✏️ Enter text edit mode (Question Bank)
    context.user_data["edit_q_field"] = "TEXT"

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Cancel", callback_data="CANCEL_EDIT_Q_TEXT")]
    ])

    msg = await query.message.reply_text(
        "📝 Send new question text:",
        reply_markup=keyboard
    )

    # 🔑 Store prompt message ID for cleanup
    context.user_data["edit_text_prompt_id"] = msg.message_id

async def edit_question_image_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # 🔒 Safety: ensure a Question Bank question is active
    qid = context.user_data.get("active_question_id")
    if not qid:
        await flash_message(context.bot, query.message.chat_id, "❌ No question selected.")
        return

    # 🔄 Clear other edit modes
    context.user_data.pop("add_q_state", None)
    context.user_data.pop("edit_options", None)

    # 🖼 Enter image edit mode
    context.user_data["edit_q_field"] = "IMAGE"

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🖼 Send new Image", callback_data="EDIT_Q_IMAGE_SEND")],
        [InlineKeyboardButton("🗑 Remove Image", callback_data="EDIT_Q_IMAGE_REMOVE")],
        [InlineKeyboardButton("❌ Cancel", callback_data="CANCEL_EDIT_Q_IMAGE")]
    ])

    msg = await query.message.reply_text(
        "🖼 Change or remove question image:",
        reply_markup=keyboard
    )

    # 🔑 Track this message for cleanup
    context.user_data["edit_image_menu_msg_id"] = msg.message_id

async def remove_question_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    qid = context.user_data.get("active_question_id")
    if not qid:
        await flash_message(context.bot, query.message.chat_id, "❌ No question selected.")
        return

    chat_id = query.message.chat_id

    # 🔐 SAFE WRITE SECTION
    try:
        async with DB_LOCK:
            _conn, _cur = get_db()
            _cur.execute(
                "UPDATE question_bank SET image_file_id=NULL WHERE id=?",
                (qid,)
            )
            _conn.commit()
            _conn.close()
    except Exception as e:
        print("⚠️ Failed to remove image:", e)
        await query.answer("❌ Failed to remove image.", show_alert=True)
        return

    # Exit image edit mode
    context.user_data.pop("edit_q_field", None)

    # 2️⃣ Delete image edit menu
    menu_id = context.user_data.pop("edit_image_menu_msg_id", None)
    if menu_id:
        try:
            await context.bot.delete_message(chat_id, menu_id)
        except:
            pass

    # 3️⃣ Rebuild preview safely
    await rebuild_question_preview(chat_id, context)

async def edit_question_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    context.user_data.pop("edit_q_field", None)

    await question_action_menu(update, context)

async def edit_question_image_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # Tell bot we are now waiting for an image
    context.user_data["edit_q_field"] = "IMAGE"

    msg = await query.message.reply_text(
        "🖼 Please send the new image now."
    )

    context.user_data["edit_image_prompt_msg_id"] = msg.message_id

async def edit_question_options_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # 🔒 Safety: ensure a Question Bank question is active
    qid = context.user_data.get("active_question_id")
    if not qid:
        await flash_message(context.bot, query.message.chat_id, "❌ No question selected.")
        return

    # 🔑 Load existing options from QUESTION BANK
    _conn, _cur = get_db()
    _cur.execute(
        "SELECT options FROM question_bank WHERE id=?",
        (qid,)
    )
    row = _cur.fetchone()
    _conn.close()
    if not row:
        await flash_message(context.bot, query.message.chat_id, "❌ Question not found.")
        return

    old_options = row[0].split("||")

    # 🔄 Clear other edit modes safely
    context.user_data.pop("add_q_state", None)
    context.user_data.pop("edit_options", None)
    context.user_data.pop("edit_options_flow_msgs", None)

    # 🔁 Enter options edit mode
    context.user_data["edit_q_field"] = "OPTIONS"
    context.user_data["edit_options"] = []

    # 🔥 NEW: Track ALL temporary messages in this flow
    context.user_data["edit_options_flow_msgs"] = []

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Cancel", callback_data="CANCEL_EDIT_Q_OPTIONS")]
    ])

    msg = await query.message.reply_text(
        "✏️ Editing options\n\n"
        f"Current options:\n"
        f"1️⃣ {escape_md(old_options[0])}\n"
        f"2️⃣ {escape_md(old_options[1])}\n"
        f"3️⃣ {escape_md(old_options[2])}\n"
        f"4️⃣ {escape_md(old_options[3])}\n\n"
        "➡️ Send NEW option 1:",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )

    # 🔑 Store this message for later full declutter
    context.user_data["edit_options_flow_msgs"].append(msg.message_id)

async def edit_question_correct_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    qid = context.user_data.get("active_question_id")
    if not qid:
        await flash_message(context.bot, query.message.chat_id, "❌ No question selected.")
        return

    _conn, _cur = get_db()
    _cur.execute("SELECT options, correct FROM question_bank WHERE id=?", (qid,))
    row = _cur.fetchone()
    _conn.close()

    if not row:
        await flash_message(context.bot, query.message.chat_id, "❌ Question not found.")
        return

    options_text, current_correct = row
    opts = options_text.split("||")
    labels = ["1️⃣", "2️⃣", "3️⃣", "4️⃣"]

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(
            f"{'✅ ' if i == current_correct else ''}{labels[i]} {opts[i]}",
            callback_data=f"EDIT_CORRECT_{i}"
        )]
        for i in range(len(opts))
    ])

    msg = await query.message.reply_text(
        "✅ Choose the NEW correct answer:\n_(current answer is highlighted)_",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )

    context.user_data.setdefault("question_flow_msgs", []).append(msg.message_id)

async def edit_question_correct_apply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    qid = context.user_data.get("active_question_id")
    if not qid:
        await flash_message(context.bot, query.message.chat_id, "❌ No question selected.")
        return

    correct_index = int(query.data.replace("EDIT_CORRECT_", ""))

    async with DB_LOCK:
        _conn, _cur = get_db()
        _cur.execute(
            "UPDATE question_bank SET correct=? WHERE id=?",
            (correct_index, qid)
        )
        _conn.commit()
        _conn.close()

    chat_id = query.message.chat_id

    _conn2, _cur2 = get_db()
    _cur2.execute("SELECT options FROM question_bank WHERE id=?", (qid,))
    opts_row = _cur2.fetchone()
    _conn2.close()

    if opts_row:
        opts = opts_row[0].split("||")
        labels = ["1️⃣", "2️⃣", "3️⃣", "4️⃣"]
        flash_keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(
                f"{'✅ ' if i == correct_index else ''}{labels[i]} {opts[i]}",
                callback_data="LOCKED"
            )]
            for i in range(len(opts))
        ])
        try:
            await query.message.edit_reply_markup(reply_markup=flash_keyboard)
        except Exception:
            pass
        await asyncio.sleep(1)

    try:
        await query.message.delete()
    except:
        pass

    confirm_msg = await context.bot.send_message(chat_id=chat_id, text="✅ Correct answer updated.")
    await asyncio.sleep(2)
    try:
        await confirm_msg.delete()
    except:
        pass

    await rebuild_question_preview(chat_id, context)

async def edit_question_explanation_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    qid = context.user_data.get("active_question_id")
    if not qid:
        await flash_message(context.bot, query.message.chat_id, "❌ No question selected.")
        return

    _conn, _cur = get_db()
    _cur.execute("SELECT explanation FROM question_bank WHERE id=?", (qid,))
    row = _cur.fetchone()
    _conn.close()

    current = row[0] if row and row[0] else "— none —"
    context.user_data["edit_q_field"] = "EXPLANATION"

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🗑 Remove Explanation", callback_data="EDIT_Q_EXPL_REMOVE"),
            InlineKeyboardButton("❌ Cancel",             callback_data="CANCEL_EDIT_Q_EXPL"),
        ]
    ])

    msg = await query.message.reply_text(
        f"🧾 Current explanation:\n\n{current}\n\n✏️ Send new explanation text:",
        reply_markup=keyboard
    )

    context.user_data["edit_expl_prompt_id"] = msg.message_id

async def edit_question_explanation_remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    qid = context.user_data.get("active_question_id")
    if not qid:
        return

    chat_id = query.message.chat_id

    # Remove explanation from DB
    async with DB_LOCK:
        _conn, _cur = get_db()
        _cur.execute(
            "UPDATE question_bank SET explanation=NULL WHERE id=?",
            (qid,)
        )
        _conn.commit()
        _conn.close()

    # Delete prompt message
    prompt_id = context.user_data.pop("edit_expl_prompt_id", None)
    if prompt_id:
        try:
            await context.bot.delete_message(chat_id, prompt_id)
        except:
            pass

    # Exit edit mode
    context.user_data.pop("edit_q_field", None)

    # Confirmation
    confirm = await context.bot.send_message(
        chat_id,
        "🗑 Explanation removed."
    )

    await asyncio.sleep(2)

    try:
        await confirm.delete()
    except:
        pass

    # Rebuild preview cleanly
    await rebuild_question_preview(chat_id, context)

async def play_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    try:
        await query.answer()
    except Exception:
        pass

    # 🧹 DELETE READY MESSAGE ONLY FOR PLAYER MODE
    # (Admin "Start this Quiz" must NOT delete Quiz Overview)
    if "play_quiz_id" in context.user_data and "active_quiz_id" not in context.user_data:
        try:
            await query.message.delete()
        except Exception:
            pass

    # 🔑 FIX: ADMIN START (from quiz menu)
    if "play_quiz_id" not in context.user_data:
        quiz_id = context.user_data.get("active_quiz_id")
        if not quiz_id:
            return
        context.user_data["play_quiz_id"] = quiz_id

    # ▶️ STEP 2: START QUIZ (UNCHANGED LOGIC)
    await start_play_quiz(update, context)

async def cancel_play_ready(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # 🧹 Delete the "Quiz Ready!" message (includes both buttons)
    try:
        await query.message.delete()
    except Exception:
        pass

    # 🧼 Clear any play-related state that was pre-set
    context.user_data.pop("play_quiz_id", None)
    context.user_data.pop("play_token", None)
    context.user_data.pop("leaderboard_key", None)
    context.user_data.pop("group_chat_id", None)

def build_group_message_link(chat_id: int, message_id: int) -> str:
    """
    Always generate a valid Telegram message link.
    Works for private supergroups (no username).
    """
    chat_id_str = str(chat_id)

    # Supergroup / private group
    if chat_id_str.startswith("-100"):
        internal_id = chat_id_str[4:]
        return f"https://t.me/c/{internal_id}/{message_id}"

    # ❗ Fallback: no valid public link possible
    # (Telegram does NOT support numeric IDs here)
    return None

async def play_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass

    play = context.user_data.get("play")
    if not play or play.get("locked"):
        return

    # 🔒 Lock immediately
    play["locked"] = True

    # 📌 Parse answer
    chosen_index = int(query.data.replace("PLAY_ANSWER_", ""))
    q = play["questions"][play["index"]]
    correct_index = q["correct"]

    # 🎨 Build feedback buttons instantly
    labels = ["A", "B", "C", "D"]
    buttons = [[
        InlineKeyboardButton(
            f"{labels[i]}"
            f"{' ✅' if i == correct_index else ' ❌' if i == chosen_index else ' ✖️'}",
            callback_data="LOCKED"
        )
        for i in range(len(q["options"]))
    ]]

    # ⚡ UPDATE BUTTONS INSTANTLY — before anything else
    try:
        await query.message.edit_reply_markup(
            reply_markup=InlineKeyboardMarkup(buttons)
        )
    except:
        pass

    # ✅ Score update
    if chosen_index == correct_index:
        play["score"] += 1

    # ⛔ CANCEL TIMER TASK
    task = play.get("timer_task")
    if task:
        task.cancel()
    play["timer_task"] = None

    # 🧹 DELETE ALL TIMER MESSAGES
    for timer_msg_id in play.get("timer_message_ids", []):
        try:
            await context.bot.delete_message(
                chat_id=query.from_user.id,
                message_id=timer_msg_id
            )
        except:
            pass
    play["timer_message_ids"] = []

    # =========================
    # 📖 SEND EXPLANATION AS TEXT (PERSISTENT)
    # =========================
    explanation = q.get("explanation")
    if explanation:
        try:
            safe_explanation = explanation
            expl_msg = await context.bot.send_message(
                chat_id=query.from_user.id,
                text=f"📖 *Explanation:*\n_{safe_explanation}_",
                parse_mode="Markdown"
            )
            # 🔥 CRITICAL:
            # Add explanation message to bulk cleanup list
            play.setdefault("question_message_ids", []).append(
                expl_msg.message_id
            )
        except:
            pass

    # ⏳ Wait longer if explanation was shown
    pause = 4 if explanation else 0
    await asyncio.sleep(pause)

    # 🔴 HARD ASYNC BOUNDARY
    await asyncio.sleep(0)

    # ✅ Always reset advancing flag before calling advance_quiz
    # This prevents the quiz from freezing if a previous timer left it stuck True
    play = context.user_data.get("play")
    if play:
        play["advancing"] = False

    # ▶️ Advance quiz safely
    await advance_quiz(query.from_user.id, context)

async def start_play_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    try:
        await query.answer()
    except Exception:
        pass

    quiz_id = context.user_data.get("play_quiz_id")
    if not quiz_id:
        await flash_message(context.bot, query.message.chat_id, "❌ Quiz not found.")
        return

    _conn, _cur = get_db()
    _cur.execute("SELECT shuffle_q, shuffle_a FROM quizzes WHERE quiz_id=?", (quiz_id,))
    row = _cur.fetchone()
    _conn.close()

    shuffle_q, shuffle_a = row if row else (0, 0)

    _conn2, _cur2 = get_db()
    _cur2.execute(
        """
        SELECT qb.id, qb.question, qb.image_file_id, qb.options, qb.correct, qb.explanation
        FROM quiz_question_links ql
        JOIN question_bank qb ON qb.id = ql.question_id
        WHERE ql.quiz_id=?
        ORDER BY ql.position ASC, qb.question COLLATE NOCASE
        """,
        (quiz_id,)
    )
    rows = _cur2.fetchall()
    _conn2.close()

    if not rows:
        await flash_message(context.bot, query.message.chat_id, "❌ This quiz has no questions.")
        return

    questions = []
    for qid, text, image, options, correct, explanation in rows:
        opts = options.split("||")
        if shuffle_a:
            indexed = list(enumerate(opts))
            random.shuffle(indexed)
            opts = [o for _, o in indexed]
            correct = [i for i, (old_i, _) in enumerate(indexed) if old_i == correct][0]
        questions.append({
            "id": qid,
            "text": text,
            "image": image,
            "options": opts,
            "correct": correct,
            "explanation": explanation
        })

    if shuffle_q:
        random.shuffle(questions)

    context.user_data["play"] = {
        "questions": questions,
        "index": 0,
        "score": 0,
        "quiz_id": quiz_id,
        "user_name": format_user_name(query.from_user),
        "locked": False,
        "timer_task": None,
        "timer_message_ids": [],
        "question_message_ids": [],
        "context_lock": asyncio.Lock(),
    }

    user_id = query.from_user.id
    await send_next_question(user_id, context)

async def send_next_question(user_id, context):
    play = context.user_data.get("play")
    if not play:
        return

    old_task = play.get("timer_task")
    if old_task:
        old_task.cancel()
        play["timer_task"] = None

    quiz_id = play["quiz_id"]

    _conn, _cur = get_db()
    _cur.execute("SELECT timer FROM quizzes WHERE quiz_id=?", (quiz_id,))
    row = _cur.fetchone()
    _conn.close()

    timer_seconds = row[0] if row else 15

    index = play["index"]
    total = len(play["questions"])
    q = play["questions"][index]

    labels = ["A", "B", "C", "D"]

    options_text = "\n\n".join(
        f"{labels[i]}. {opt.strip()}"
        for i, opt in enumerate(q["options"])
    )

    question_text = (
        f"[{index+1}/{total}] 🧠 {q['text']}\n\n"
        f"{options_text}"
    )

    keyboard = [[
        InlineKeyboardButton(labels[i], callback_data=f"PLAY_ANSWER_{i}")
        for i in range(len(q["options"]))
    ]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    if q.get("image"):
        try:
            msg = await context.bot.send_photo(
                chat_id=user_id,
                photo=q["image"],
                caption=question_text,
                reply_markup=reply_markup
            )
        except:
            msg = await context.bot.send_message(
                chat_id=user_id,
                text=question_text,
                reply_markup=reply_markup
            )
    else:
        msg = await context.bot.send_message(
            chat_id=user_id,
            text=question_text,
            reply_markup=reply_markup
        )

    play["current_question_message_id"] = msg.message_id
    play.setdefault("question_message_ids", [])
    play["question_message_ids"].append(msg.message_id)
    play["locked"] = False

    timer_msg = await context.bot.send_message(
        chat_id=user_id,
        text=f"⏱ Time left: {timer_seconds}s"
    )
    play.setdefault("timer_message_ids", [])
    play["timer_message_ids"].append(timer_msg.message_id)

    play["timer_task"] = asyncio.create_task(
        countdown_timer(user_id, context, timer_seconds, play)
    )

async def countdown_timer(user_id, context, seconds, play):
    try:
        # Always re-fetch live play reference
        play = context.user_data.get("play")
        if not play:
            return

        # Ensure timer message exists
        timer_ids = play.get("timer_message_ids")
        if not timer_ids:
            return

        timer_msg_id = timer_ids[-1]

        # =========================
        # ⏱ Countdown Loop
        # =========================
        for remaining in range(seconds - 1, -1, -1):

            await asyncio.sleep(1)

            play = context.user_data.get("play")
            if not play:
                return

            if play.get("locked") or play.get("ended"):
                return

            if "questions" not in play or "index" not in play:
                return

            try:
                if remaining > 0:
                    await context.bot.edit_message_text(
                        chat_id=user_id,
                        message_id=timer_msg_id,
                        text=f"⏱ Time left: {remaining}s"
                    )
                else:
                    await context.bot.edit_message_text(
                        chat_id=user_id,
                        message_id=timer_msg_id,
                        text="⏳ Time’s up!"
                    )
            except:
                pass

        # =========================
        # ⏰ TIME EXPIRED
        # =========================

        play = context.user_data.get("play")
        if not play:
            return

        if play.get("locked") or play.get("ended"):
            return

        if "questions" not in play or "index" not in play:
            return

        play["locked"] = True
        play["timer_task"] = None

        if play["index"] >= len(play["questions"]):
            return

        q = play["questions"][play["index"]]
        correct_index = q["correct"]
        labels = ["A", "B", "C", "D"]

        # ⏱ SKIPPED CASE — show correct/incorrect marks
        buttons = [[
            InlineKeyboardButton(
                f"{labels[i]}{' ✅' if i == correct_index else ' ❌'}",
                callback_data="LOCKED"
            )
            for i in range(len(q["options"]))
        ]]

        msg_id = play.get("current_question_message_id")
        if not msg_id:
            return

        try:
            await context.bot.edit_message_reply_markup(
                chat_id=user_id,
                message_id=msg_id,
                reply_markup=InlineKeyboardMarkup(buttons)
            )
        except:
            pass

        # =========================
        # 📖 SEND EXPLANATION AS TEXT (PERSISTENT)
        # =========================
        explanation = q.get("explanation")

        if explanation:
            try:
                safe_explanation = explanation
                expl_msg = await context.bot.send_message(
                    chat_id=user_id,
                    text=f"📖 *Explanation:*\n_{safe_explanation}_",
                    parse_mode="Markdown"
                )

                # 🔥 Add to bulk cleanup list
                play.setdefault("question_message_ids", []).append(
                    expl_msg.message_id
                )

            except:
                pass

        # ⏳ Wait longer if explanation was shown
        pause = 4 if explanation else 0
        await asyncio.sleep(pause)

        # =========================
        # ▶️ Advance Safely (guard against race with answer button)
        # =========================
        play = context.user_data.get("play")
        if not play or play.get("finished"):
            return

        # ✅ Do NOT hold context_lock while calling advance_quiz —
        # advance_quiz acquires the same lock and asyncio.Lock is NOT reentrant.
        # Instead: check advancing, set it, release lock, then call advance_quiz.
        acquired = False
        async with play["context_lock"]:
            if play.get("advancing") or play.get("finished"):
                return
            play["advancing"] = True
            acquired = True

        if not acquired:
            return

        if play["index"] >= len(play["questions"]) - 1:
            play["advancing"] = False
            await finish_quiz(user_id, context)
        else:
            play["advancing"] = False   # ← release BEFORE calling advance_quiz
            await advance_quiz(user_id, context)

    except asyncio.CancelledError:
        # Proper cancellation handling
        return
    except Exception as e:
        print("⚠️ Timer error:", e)
        return

####################################################################################################################################################################################################################################
# CODE BY PARTS - PART 3
####################################################################################################################################################################################################################################

async def show_leaderboard(chat_id, quiz_id, bot):
    _conn, _cur = get_db()
    _cur.execute("""
        SELECT username, score
        FROM leaderboard
        WHERE quiz_id=? AND chat_id=?
        ORDER BY score DESC
        LIMIT 10
    """, (quiz_id, chat_id))
    rows = _cur.fetchall()
    _conn.close()

    if not rows:
        text = "📊 **Quiz Leaderboard**\n\n_No participants yet._"
    else:
        text = "📊 **Quiz Leaderboard**\n\n"
        for i, (name, score) in enumerate(rows, start=1):
            text += f"{i}. {name} — {score}\n"

    await bot.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode="Markdown"
    )

def build_group_post_keyboard(quiz_id: str, token: str, leaderboard_key: str, pages: int = 0, page: int = 0) -> InlineKeyboardMarkup:
    buttons = []

    if pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("◀ Prev", callback_data=f"LB_PREV|{leaderboard_key}"))
        nav.append(InlineKeyboardButton(f"{page+1}/{pages}", callback_data="LB_NOP"))
        if page < pages - 1:
            nav.append(InlineKeyboardButton("Next ▶", callback_data=f"LB_NEXT|{leaderboard_key}"))
        buttons.append(nav)

    # Always-present action row
    # ⚙️ Quiz Admin is now a URL button — tapping opens the bot directly (same as Start Quiz)
    buttons.append([
        InlineKeyboardButton("⚙️ Quiz Admin", url=f"https://t.me/{BOT_USERNAME}?start=QA_{quiz_id}_{token}"),
        InlineKeyboardButton("▶️ Start Quiz", url=f"https://t.me/{BOT_USERNAME}?start=PLAY_{quiz_id}_{token}"),
    ])

    return InlineKeyboardMarkup(buttons)

async def send_quiz_to_group(chat_id, quiz_id, context, token):
    leaderboard_key = make_leaderboard_key(quiz_id, token)

    _conn, _cur = get_db()
    _cur.execute(
        "SELECT title, description, timer, shuffle_q, shuffle_a FROM quizzes WHERE quiz_id=?",
        (quiz_id,)
    )
    row = _cur.fetchone()
    _conn.close()

    if not row:
        return
    title, desc, timer, sq, sa = row

    _conn2, _cur2 = get_db()
    _cur2.execute("SELECT COUNT(*) FROM quiz_question_links WHERE quiz_id=?", (quiz_id,))
    total_questions = _cur2.fetchone()[0]
    _conn2.close()

    text = f"📘 *{escape_md(title)}*\n"
    if desc:
        text += f"📝 _{escape_md(desc)}_\n"
    text += "\n"
    text += f"🧠 *{total_questions} Questions* • ⏱ *{timer}s*\n"
    text += (
        f"🔀 Questions: {'ON' if sq else 'OFF'} • "
        f"Answers: {'ON' if sa else 'OFF'}\n\n"
    )
    text += "🏆 *Leaderboard*\n— No attempts yet —"

    keyboard = build_group_post_keyboard(quiz_id, token, leaderboard_key)

    msg = await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        reply_markup=keyboard,
        parse_mode="Markdown"
    )

    GROUP_LB_MESSAGES[leaderboard_key] = {
        "quiz_id":    quiz_id,
        "token":      token,
        "chat_id":    chat_id,
        "message_id": msg.message_id,
        "page":       0,
        "show_score": 1,
    }

    try:
        async with DB_LOCK:
            _conn3, _cur3 = get_db()
            _cur3.execute("""
                INSERT OR REPLACE INTO group_lb_messages
                (leaderboard_key, quiz_id, token, chat_id, message_id, page)
                VALUES (?, ?, ?, ?, ?, 0)
            """, (leaderboard_key, quiz_id, token, chat_id, msg.message_id))
            _conn3.commit()
            _conn3.close()
    except Exception as e:
        print("⚠️ Failed to persist lb message info:", e)

    GROUP_LEADERBOARDS[leaderboard_key] = {}

def build_group_quiz_text(leaderboard_key, page=0):
    try:
        quiz_id, _ = leaderboard_key.split(":", 1)
    except ValueError:
        return "❌ Invalid leaderboard.", 0

    _conn, _cur = get_db()
    _cur.execute(
        "SELECT title, description, timer, shuffle_q, shuffle_a FROM quizzes WHERE quiz_id=?",
        (quiz_id,)
    )
    row = _cur.fetchone()
    _conn.close()

    if not row:
        return "❌ Quiz not found.", 0
    title, desc, timer, sq, sa = row

    _conn2, _cur2 = get_db()
    _cur2.execute("SELECT COUNT(*) FROM quiz_question_links WHERE quiz_id=?", (quiz_id,))
    total_questions = _cur2.fetchone()[0]
    _conn2.close()

    text = f"📘 *{escape_md(title)}*\n"
    if desc:
        text += f"📝 _{escape_md(desc)}_\n"
    text += "\n"
    text += f"🧠 *{total_questions} Questions* • ⏱ *{timer}s*\n"
    text += (
        f"🔀 Questions: {'ON' if sq else 'OFF'} • "
        f"Answers: {'ON' if sa else 'OFF'}\n\n"
    )
    # Check if leaderboard is hidden by admin
    lb_info = GROUP_LB_MESSAGES.get(leaderboard_key, {})
    show_score = lb_info.get("show_score", 1)

    if not show_score:
        text += "🏆 *Leaderboard*\n🔒 _Score display is currently hidden by \n      the admin._\n"
        return text, 0

    text += "🏆 *Leaderboard*\n"

    if leaderboard_key in GROUP_LEADERBOARDS and GROUP_LEADERBOARDS[leaderboard_key]:
        leaderboard = [
            {"user_id": uid, "name": data["name"], "score": data["score"]}
            for uid, data in GROUP_LEADERBOARDS[leaderboard_key].items()
        ]
    else:
        _conn3, _cur3 = get_db()
        _cur3.execute(
            "SELECT user_id, name, score FROM group_leaderboard WHERE leaderboard_key=?",
            (leaderboard_key,)
        )
        rows = _cur3.fetchall()
        _conn3.close()

        leaderboard = []
        GROUP_LEADERBOARDS.setdefault(leaderboard_key, {})
        for user_id, name, score in rows:
            GROUP_LEADERBOARDS[leaderboard_key][user_id] = {"name": name, "score": score}
            leaderboard.append({"user_id": user_id, "name": name, "score": score})

    if not leaderboard:
        text += "_No attempts yet_\n"
        return text, 0

    leaderboard.sort(key=lambda x: x["score"], reverse=True)

    per_page = 5
    pages = (len(leaderboard) - 1) // per_page + 1
    page = max(0, min(page, pages - 1))

    start = page * per_page
    end = start + per_page

    medals = {1: "🥇", 2: "🥈", 3: "🥉"}

    for i, user in enumerate(leaderboard[start:end], start=start + 1):
        prefix = medals.get(i, f"{i}.")
        text += f"{prefix} {user['name']} — {user['score']}\n"

    return text, pages

async def update_group_leaderboard(leaderboard_key, context):
    """
    Updates the leaderboard message in the group for ONE quiz post instance.
    leaderboard_key format: quiz_id:token
    """
    info = GROUP_LB_MESSAGES.get(leaderboard_key)
    if not info:
        return

    chat_id = info["chat_id"]
    message_id = info["message_id"]
    page = info.get("page", 0)

    # 🔑 Split leaderboard key
    try:
        quiz_id, token = leaderboard_key.split(":", 1)
    except ValueError:
        print("⚠️ Invalid leaderboard key:", leaderboard_key)
        return

    # 🔨 Build updated leaderboard text
    text, pages = build_group_quiz_text(leaderboard_key, page)

    # 🔑 Build keyboard from single source of truth
    keyboard = build_group_post_keyboard(quiz_id, token, leaderboard_key, pages=pages, page=page)

    inline_message_id = info.get("inline_message_id")

    try:
        if inline_message_id:
            # Posted via inline query — edit using inline_message_id
            await context.bot.edit_message_text(
                inline_message_id=inline_message_id,
                text=text,
                reply_markup=keyboard,
                parse_mode="Markdown"
            )
        else:
            # Posted via /post command — edit using chat_id + message_id
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                reply_markup=keyboard,
                parse_mode="Markdown"
            )
    except Exception as e:
        error_text = str(e).lower()

        # 🚨 If group quiz message was deleted
        if "message to edit not found" in error_text:
            print("🧹 Group quiz message deleted. Cleaning leaderboard:", leaderboard_key)

            # 🔑 Remove memory references
            GROUP_LEADERBOARDS.pop(leaderboard_key, None)
            GROUP_LB_MESSAGES.pop(leaderboard_key, None)

            # 🔐 Remove database records safely
            try:
                async with DB_LOCK:
                    _conn, _cur = get_db()
                    _cur.execute("DELETE FROM group_leaderboard WHERE leaderboard_key=?", (leaderboard_key,))
                    quiz_id, token = leaderboard_key.split(":", 1)
                    _cur.execute("DELETE FROM quiz_post_tokens WHERE token=? AND quiz_id=?", (token, quiz_id))
                    _cur.execute("DELETE FROM group_lb_messages WHERE leaderboard_key=?", (leaderboard_key,))
                    _conn.commit()
                    _conn.close()
            except Exception as db_error:
                print("⚠️ Failed to clean DB leaderboard:", db_error)

        else:
            # Other harmless errors
            print("⚠️ Failed to edit leaderboard message:", e)

async def refresh_all_group_posts_for_quiz(quiz_id: str, context):
    _conn, _cur = get_db()
    _cur.execute(
        "SELECT leaderboard_key, chat_id, message_id, page, inline_message_id FROM group_lb_messages WHERE quiz_id=?",
        (quiz_id,)
    )
    posts = _cur.fetchall()
    _conn.close()

    if not posts:
        return

    for leaderboard_key, chat_id, message_id, page, inline_message_id in posts:
        text, pages = build_group_quiz_text(leaderboard_key, page)

        try:
            _, token = leaderboard_key.split(":", 1)
        except ValueError:
            continue

        keyboard = build_group_post_keyboard(quiz_id, token, leaderboard_key, pages=pages, page=page)

        try:
            if inline_message_id:
                await context.bot.edit_message_text(
                    inline_message_id=inline_message_id,
                    text=text,
                    reply_markup=keyboard,
                    parse_mode="Markdown"
                )
            else:
                await context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=text,
                    reply_markup=keyboard,
                    parse_mode="Markdown"
                )
        except Exception as e:
            error_text = str(e).lower()
            if "message is not modified" in error_text:
                pass
            elif "message to edit not found" in error_text:
                print(f"🧹 Cleaning deleted post: {leaderboard_key}")
                async with DB_LOCK:
                    _conn2, _cur2 = get_db()
                    _cur2.execute(
                        "DELETE FROM group_lb_messages WHERE leaderboard_key=?",
                        (leaderboard_key,)
                    )
                    _conn2.commit()
                    _conn2.close()
                GROUP_LB_MESSAGES.pop(leaderboard_key, None)
            else:
                print(f"⚠️ Failed to refresh group post {leaderboard_key}:", e)

async def post_quiz_to_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    quiz_id = context.user_data.get("active_quiz_id")

    if not quiz_id:
        await flash_message(context.bot, query.message.chat_id, "❌ No quiz selected.")
        return

    # 🔑 Generate a unique token
    token = secrets.token_hex(6)
    timestamp = int(time.time())

    # 💾 Save token to DB
    try:
        async with DB_LOCK:
            _conn, _cur = get_db()
            _cur.execute(
                """
                INSERT INTO quiz_post_tokens (token, quiz_id, owner_id, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (token, quiz_id, get_active_user_id(context), timestamp)
            )
            _conn.commit()
            _conn.close()
    except Exception as e:
        print("⚠️ Failed to save post token:", e)
        await flash_message(context.bot, query.message.chat_id, "❌ Failed to generate post link.")
        return

    # 📋 Build the /postquiz command the admin will send in the group
    post_command = f"/postquiz@{BOT_USERNAME} {quiz_id}_{token}"

    # 📖 Fetch quiz title
    _conn_q, _cur_q = get_db()
    _cur_q.execute("SELECT title FROM quizzes WHERE quiz_id=?", (quiz_id,))
    row_q = _cur_q.fetchone()
    _conn_q.close()
    quiz_title = row_q[0] if row_q else "Quiz"

    msg = await query.message.reply_text(
        f"📤 *Posting:* _{quiz_title}_\n\n"
        f"To post this quiz to your group:\n\n"
        f"1️⃣ Go to your group\n"
        f"2️⃣ Send this exact command there:\n\n"
        f"`{post_command}`\n\n"
        f"_(Tap the command above to copy it, then paste it in the group)_\n\n"
        f"⚠️ Make sure the bot is already an admin in the group.",
        parse_mode="Markdown"
    )

    # ⏳ Auto-delete after 10 seconds
    async def delete_later():
        await asyncio.sleep(10)
        try:
            await msg.delete()
        except Exception:
            pass
    asyncio.create_task(delete_later())

async def post_quiz_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    chat = update.effective_chat
    if chat.type not in ("group", "supergroup"):
        return

    user_id = update.effective_user.id
    args = context.args

    if not args:
        await update.message.reply_text("❌ Missing quiz post token.")
        return

    # Join all args in case Telegram or the client split the payload on spaces
    payload = "".join(args)

    if "_" not in payload:
        err_msg = await update.message.reply_text("❌ Invalid post command format.")
        await asyncio.sleep(2)
        try:
            await err_msg.delete()
        except:
            pass
        try:
            await update.message.delete()
        except:
            pass
        return
    quiz_id, token = payload.split("_", 1)

    if not is_authorized(user_id):
        warn_msg = await update.message.reply_text("❌ Only the Bot Admin can post quizzes.")
        async def delete_later():
            await asyncio.sleep(3)
            try:
                await warn_msg.delete()
            except:
                pass
            try:
                await update.message.delete()
            except:
                pass
        asyncio.create_task(delete_later())
        return

    # 🔄 Retry up to 5× with 1s delay — handles race condition where
    # the token DB write hasn't committed yet when this handler fires.
    row = None
    for attempt in range(5):
        _conn, _cur = get_db()
        _cur.execute(
            "SELECT token FROM quiz_post_tokens WHERE token=? AND quiz_id=?",
            (token, quiz_id)
        )
        row = _cur.fetchone()
        _conn.close()
        if row:
            break
        await asyncio.sleep(1)

    if not row:
        warn_msg = await update.message.reply_text("❌ This quiz post command is invalid or expired.")
        async def delete_later():
            await asyncio.sleep(3)
            try:
                await warn_msg.delete()
            except:
                pass
            try:
                await update.message.delete()
            except:
                pass
        asyncio.create_task(delete_later())
        return

    _conn2, _cur2 = get_db()
    _cur2.execute(
        "SELECT owner_id FROM quiz_post_tokens WHERE token=? AND quiz_id=?",
        (token, quiz_id)
    )
    owner_row = _cur2.fetchone()
    _conn2.close()

    if not owner_row or owner_row[0] != user_id:
        warn_msg = await update.message.reply_text("❌ You can only post quizzes that you created.")
        async def delete_later():
            await asyncio.sleep(3)
            try:
                await warn_msg.delete()
            except:
                pass
            try:
                await update.message.delete()
            except:
                pass
        asyncio.create_task(delete_later())
        return

    await send_quiz_to_group(chat.id, quiz_id, context, token)

    try:
        await update.message.delete()
    except:
        pass

async def leaderboard_page_nav(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    try:
        action, leaderboard_key = query.data.split("|", 1)
    except ValueError:
        return

    info = GROUP_LB_MESSAGES.get(leaderboard_key)
    if not info:
        return

    page = info.get("page", 0)

    if action == "LB_PREV":
        page -= 1
    elif action == "LB_NEXT":
        page += 1

    if page < 0:
        page = 0

    info["page"] = page
    GROUP_LB_MESSAGES[leaderboard_key] = info

    await update_group_leaderboard(leaderboard_key, context)

async def folder_prev(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    folder = query.data.split("|", 1)[1]
    key = f"folder_page_{folder}"
    context.user_data[key] = max(0, context.user_data.get(key, 0) - 1)

    await show_quizzes_in_folder(query.message, context, folder)

async def folder_next(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    folder = query.data.split("|", 1)[1]
    key = f"folder_page_{folder}"
    context.user_data[key] = context.user_data.get(key, 0) + 1

    await show_quizzes_in_folder(query.message, context, folder)

async def database_prev(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    context.user_data["db_page"] = max(
        0, context.user_data.get("db_page", 0) - 1
    )

    await show_database_menu(query.message, context)


async def database_next(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    context.user_data["db_page"] = context.user_data.get("db_page", 0) + 1

    await show_database_menu(query.message, context)

async def copy_question_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    qid = context.user_data.get("active_question_id")
    source_quiz_id = context.user_data.get("active_quiz_id")

    if not qid or not source_quiz_id:
        await flash_message(context.bot, query.message.chat_id, "❌ No question selected.")
        return

    context.user_data["state"] = "COPY_QUESTION"
    page = context.user_data.get("copy_q_page", 0)

    _conn, _cur = get_db()
    _cur.execute(
        "SELECT quiz_id, title FROM quizzes WHERE owner_id=? ORDER BY title",
        (get_active_user_id(context),)
    )
    quizzes = _cur.fetchall()
    _conn.close()

    available = []
    for quiz_id, title in quizzes:
        if quiz_id == source_quiz_id:
            continue
        _conn2, _cur2 = get_db()
        _cur2.execute(
            "SELECT 1 FROM quiz_question_links WHERE quiz_id=? AND question_id=?",
            (quiz_id, qid)
        )
        already = _cur2.fetchone()
        _conn2.close()
        if already:
            continue
        available.append((quiz_id, title))

    if not available:
        await flash_message(context.bot, query.message.chat_id, "ℹ️ This question is already linked to all quizzes.")
        return

    PER_PAGE = 5
    pages = (len(available) - 1) // PER_PAGE + 1
    page = max(0, min(page, pages - 1))

    start = page * PER_PAGE
    end = start + PER_PAGE

    keyboard = []

    for quiz_id, title in available[start:end]:
        keyboard.append([
            InlineKeyboardButton(f"📘 {title}", callback_data=f"COPY_TO|{quiz_id}")
        ])

    if pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("◀ Prev", callback_data="COPY_Q_PREV"))
        nav.append(InlineKeyboardButton(f"{page+1}/{pages}", callback_data="COPY_Q_NOP"))
        if page < pages - 1:
            nav.append(InlineKeyboardButton("Next ▶", callback_data="COPY_Q_NEXT"))
        keyboard.append(nav)

    keyboard.append([InlineKeyboardButton("⬅️ Cancel", callback_data="EDIT_QUESTIONS")])

    await query.message.edit_text(
        "📋 *Add question to another quiz*\n\nSelect target quiz:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

async def copy_question_apply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    target_quiz_id = query.data.split("|", 1)[1]
    qid = context.user_data.get("active_question_id")

    if not qid:
        await flash_message(context.bot, query.message.chat_id, "❌ No question selected.")
        return

    # 🔒 Prevent duplicate links
    _conn_chk, _cur_chk = get_db()
    _cur_chk.execute(
        "SELECT 1 FROM quiz_question_links WHERE quiz_id=? AND question_id=?",
        (target_quiz_id, qid)
    )
    already = _cur_chk.fetchone()
    _conn_chk.close()
    if already:
        await flash_message(context.bot, query.message.chat_id, "ℹ️ Question already exists in this quiz.")
        return

    # 🔑 Insert link ONLY (no duplication)
    async with DB_LOCK:
        _conn_ins, _cur_ins = get_db()
        _cur_ins.execute(
            """
            INSERT INTO quiz_question_links (quiz_id, question_id, position)
            VALUES (?, ?, (
                SELECT COALESCE(MAX(position), 0) + 1
                FROM quiz_question_links
                WHERE quiz_id=?
            ))
            """,
            (target_quiz_id, qid, target_quiz_id)
        )
        _conn_ins.commit()
        _conn_ins.close()

    context.user_data.pop("state", None)

    await query.answer("✅ Question added.")

    # Return to source quiz question list
    await show_questions_from_message(query.message, context)

async def copy_q_prev(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["copy_q_page"] = max(0, context.user_data.get("copy_q_page", 0) - 1)
    await copy_question_start(update, context)

async def copy_q_next(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["copy_q_page"] = context.user_data.get("copy_q_page", 0) + 1
    await copy_question_start(update, context)

async def confirm_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = context.user_data.pop("confirm_delete", None)
    if not data:
        await flash_message(context.bot, query.message.chat_id, "❌ Nothing to delete.")
        return

    dtype, value = data

    # ======================================================
    # 🗑 UNLINK QUESTION FROM QUIZ (SOFT DELETE)
    # ======================================================
    if dtype == "QUESTION_FROM_QUIZ":
        qid = value
        quiz_id = context.user_data.get("active_quiz_id")

        if not quiz_id:
            return

        try:
            async with DB_LOCK:
                _conn, _cur = get_db()
                _cur.execute(
                    """
                    DELETE FROM quiz_question_links
                    WHERE quiz_id=? AND question_id=?
                    """,
                    (quiz_id, qid)
                )
                _conn.commit()
                _conn.close()

        except Exception as e:
            print("⚠️ Failed to unlink question:", e)
            await flash_message(context.bot, query.message.chat_id, "❌ Failed to remove.")
            return

        # 🔹 Delete confirmation message
        try:
            await query.message.delete()
        except:
            pass

        # 🔹 Delete the question preview message
        preview_id = context.user_data.get("question_preview_msg_id")
        if preview_id:
            try:
                await context.bot.delete_message(
                    chat_id=query.message.chat_id,
                    message_id=preview_id
                )
            except:
                pass

        context.user_data.pop("active_question_id", None)
        context.user_data.pop("question_preview_msg_id", None)

        # Reset pagination
        context.user_data["reset_q_page"] = True

        # 🔹 Send new placeholder message
        new_msg = await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="Loading..."
        )

        # 🔹 Build question list on that message
        await show_questions_from_message(new_msg, context)

        return

    # ======================================================
    # 🗑 DELETE QUIZ
    # ======================================================
    if dtype == "QUIZ":
        quiz_id = value
        folder = context.user_data.get("last_quiz_folder", "Default")

        try:
            async with DB_LOCK:
                _conn, _cur = get_db()
                _cur.execute(
                    "DELETE FROM quiz_question_links WHERE quiz_id=?",
                    (quiz_id,)
                )
                _cur.execute(
                    "DELETE FROM quizzes WHERE quiz_id=?",
                    (quiz_id,)
                )
                _conn.commit()
                _conn.close()

        except Exception as e:
            print("⚠️ Failed to delete quiz:", e)
            await flash_message(context.bot, query.message.chat_id, "❌ Quiz delete failed.")
            return

        # 🔥 STEP 1: Delete confirmation dialog
        try:
            await query.message.delete()
        except:
            pass

        # 🔥 STEP 2: Delete the quiz preview message (important fix)
        preview_id = context.user_data.get("quiz_overview_msg_id")
        if preview_id:
            try:
                await context.bot.delete_message(
                    chat_id=query.message.chat_id,
                    message_id=preview_id
                )
            except:
                pass

        context.user_data.pop("quiz_overview_msg_id", None)
        context.user_data.pop("active_quiz_id", None)

        # 🔥 STEP 3: Send clean placeholder
        new_msg = await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="Loading..."
        )

        # 🔥 STEP 4: Replace with folder quiz list
        await show_quizzes_in_folder(new_msg, context, folder)

        return

    # ======================================================
    # 🗑 DELETE FOLDER
    # ======================================================
    elif dtype == "FOLDER":
        folder = value
        chat_id = query.message.chat_id

        try:
            async with DB_LOCK:
                _conn, _cur = get_db()

                _cur.execute(
                    "SELECT quiz_id FROM quizzes WHERE folder=?",
                    (folder,)
                )
                quiz_ids = [row[0] for row in _cur.fetchall()]

                for qid in quiz_ids:
                    _cur.execute(
                        "DELETE FROM quiz_question_links WHERE quiz_id=?",
                        (qid,)
                    )

                _cur.execute(
                    "DELETE FROM quizzes WHERE folder=?",
                    (folder,)
                )

                _cur.execute(
                    "DELETE FROM folders WHERE name=?",
                    (folder,)
                )

                _conn.commit()
                _conn.close()

        except Exception as e:
            print("⚠️ Failed to delete folder:", e)
            await flash_message(context.bot, chat_id, "❌ Folder delete failed.")
            return

        # 🧹 STEP 1: Delete confirmation dialog
        try:
            await query.message.delete()
        except:
            pass

        # 🔁 STEP 2: Edit the ORIGINAL folder screen message
        # The original message is the one BEFORE confirmation dialog.
        # We access it via context.user_data.
        folder_msg_id = context.user_data.get("last_folder_screen_msg_id")

        if folder_msg_id:
            try:
                await context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=folder_msg_id,
                    text="📂 Quiz Folders",
                    reply_markup=None
                )
            except:
                pass

        # 🔁 STEP 3: Properly redraw folder list on same message
        # IMPORTANT: Use a dummy Message object reference from context
        await show_quiz_folders(
            message=context.user_data.get("folder_screen_message_object"),
            context=context
        )

        return

    # ======================================================
    # ❗ QUESTION DELETE REMOVED
    # ======================================================
    elif dtype == "QUESTION":
        # Question deletion is now handled by:
        # - delete_question_from_quiz
        # - delete_question_from_database
        await query.answer("⚠️ Question delete handler not assigned.", show_alert=True)

async def cancel_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # Clear delete state
    context.user_data.pop("confirm_delete", None)

    # 🧹 Delete the confirmation dialog itself
    try:
        await query.message.delete()
    except:
        pass

    # 🔕 No messages, no redraw

async def end_quiz(user_id, context):
    play = context.user_data.get("play")
    if not play:
        return

    # 🔒 HARD GUARANTEE: END ONLY ONCE
    if play.get("ended"):
        return

    play["ended"] = True

    # Normalize index to END
    play["index"] = len(play["questions"])

    await finish_quiz(user_id, context)

async def stop_active_quiz(user_id, context):
    """
    Safely stops an ongoing quiz immediately
    and CLEANS all quiz-related messages instantly.
    """
    play = context.user_data.get("play")
    if not play:
        return

    # 🛑 Mark quiz as stopped
    play["locked"] = True
    play["finished"] = True
    play["ended"] = True

    # ⛔ STEP 1: CANCEL TIMER TASK SAFELY
    task = play.get("timer_task")
    current = asyncio.current_task()

    if task and task is not current:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    play["timer_task"] = None

    # =========================
    # ⚡ STEP 2: INSTANT BULK DELETE
    # =========================
    delete_tasks = []

    # Timer messages
    for timer_msg_id in play.get("timer_message_ids", []):
        delete_tasks.append(
            context.bot.delete_message(
                chat_id=user_id,
                message_id=timer_msg_id
            )
        )

    # Question messages
    for msg_id in play.get("question_message_ids", []):
        delete_tasks.append(
            context.bot.delete_message(
                chat_id=user_id,
                message_id=msg_id
            )
        )

    # Warning message (if exists)
    warning_id = play.get("warning_message_id")
    if warning_id:
        delete_tasks.append(
            context.bot.delete_message(
                chat_id=user_id,
                message_id=warning_id
            )
        )

    if delete_tasks:
        await asyncio.gather(*delete_tasks, return_exceptions=True)

    # =========================
    # 🧼 STEP 3: CLEAN MEMORY
    # =========================
    play.clear()
    context.user_data.pop("play", None)
    context.user_data.pop("play_quiz_id", None)

async def finish_quiz(user_id, context):
    play = context.user_data.get("play")
    if not play:
        return

    if play.get("finish_sent"):
        return
    play["finish_sent"] = True

    quiz_id = play["quiz_id"]
    score   = play["score"]
    total   = len(play["questions"])

    play["locked"] = True

    task = play.get("timer_task")
    current = asyncio.current_task()
    if task and task is not current:
        task.cancel()
    play["timer_task"] = None

    leaderboard_key = context.user_data.get("leaderboard_key")

    if leaderboard_key:
        lb_info = GROUP_LB_MESSAGES.get(leaderboard_key)

        if lb_info:
            GROUP_LEADERBOARDS.setdefault(leaderboard_key, {})

            if user_id not in GROUP_LEADERBOARDS[leaderboard_key]:

                GROUP_LEADERBOARDS[leaderboard_key][user_id] = {
                    "name":  play["user_name"],
                    "score": score,
                }

                try:
                    async with DB_LOCK:
                        _conn, _cur = get_db()
                        _cur.execute("""
                            INSERT OR IGNORE INTO group_leaderboard
                            (leaderboard_key, user_id, name, score)
                            VALUES (?, ?, ?, ?)
                        """, (leaderboard_key, user_id, play["user_name"], score))
                        _conn.commit()
                        _conn.close()
                except Exception as e:
                    print("⚠️ Failed to save leaderboard:", e)

                try:
                    await update_group_leaderboard(leaderboard_key, context)
                except Exception as e:
                    print("⚠️ Leaderboard update failed:", e)

    delete_tasks = []
    for msg_id in play.get("question_message_ids", []):
        delete_tasks.append(
            context.bot.delete_message(chat_id=user_id, message_id=msg_id)
        )
    for timer_msg_id in play.get("timer_message_ids", []):
        delete_tasks.append(
            context.bot.delete_message(chat_id=user_id, message_id=timer_msg_id)
        )
    if delete_tasks:
        await asyncio.gather(*delete_tasks, return_exceptions=True)

    _conn2, _cur2 = get_db()
    _cur2.execute(
        "SELECT title, timer FROM quizzes WHERE quiz_id=?",
        (quiz_id,)
    )
    row = _cur2.fetchone()
    _conn2.close()

    title, timer = row if row else ("Quiz", 0)

    buttons = [
        [
            InlineKeyboardButton("🔁 Start Again",  callback_data="PLAY_START"),
            InlineKeyboardButton("🗑 Delete",        callback_data="DELETE_FINISH_MSG"),
        ]
    ]

    message_text = (
        f"*{title}*\n"
        f"{total} Questions • ⏱ {timer}s\n\n"
        f"🏁 *Quiz Finished!* : *Your Score {score}/{total}*\n\n"
        "📊 The leaderboard in the group has been updated. "
        "Please return to the 👥 Group or Channel where this quiz was posted."
    )

    await context.bot.send_message(
        chat_id=user_id,
        text=message_text,
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="Markdown"
    )

    context.user_data.pop("play", None)

async def advance_quiz(user_id, context):
    play = context.user_data.get("play")
    if not play:
        return

    async with play["context_lock"]:
        # 🚫 Prevent double-advance (from both timer expiry AND answer button)
        if play.get("advancing") or play.get("finished"):
            return

        # 🔒 Mark as advancing to block any concurrent call
        play["advancing"] = True

        play["index"] += 1

        # 🏁 END OF QUIZ
        if play["index"] >= len(play["questions"]):
            play["finished"] = True
            play["advancing"] = False
            await finish_quiz(user_id, context)
            return

        # ▶️ NEXT QUESTION
        await send_next_question(user_id, context)

        # 🔓 Release advance lock after next question is fully sent
        play["advancing"] = False

async def qb_pick_folder_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # Safety check
    if "active_quiz_id" not in context.user_data:
        await flash_message(context.bot, query.message.chat_id, "❌ No active quiz.")
        return

    # 🔒 Initialize Question Bank selection state
    context.user_data["qb_selected"] = set()
    context.user_data["qb_q_page"] = 0
    context.user_data.pop("qb_folder_name", None)

    await qb_pick_folder_menu(query.message, context)

async def qb_folder_prev(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    context.user_data["qb_folder_page"] = max(
        0, context.user_data.get("qb_folder_page", 0) - 1
    )

    await qb_pick_folder_menu(query.message, context)

async def qb_folder_next(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    context.user_data["qb_folder_page"] = context.user_data.get("qb_folder_page", 0) + 1

    await qb_pick_folder_menu(query.message, context)

async def qb_question_prev(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # Move page backward
    context.user_data["qb_q_page"] = max(
        0,
        context.user_data.get("qb_q_page", 0) - 1
    )

    # Reload the same folder
    await qb_open_folder(update, context)

async def qb_question_next(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # Move page forward
    context.user_data["qb_q_page"] = context.user_data.get("qb_q_page", 0) + 1

    # Reload the same folder
    await qb_open_folder(update, context)

def ensure_default_qb_folder():
    _conn, _cur = get_db()
    _cur.execute(
        """
        INSERT OR IGNORE INTO question_bank_folders (owner_id, name)
        VALUES (?, 'Default')
        """,
        (OWNER_USER_ID,)
    )
    _conn.commit()
    _conn.close()

def ensure_all_subscriber_default_folders():
    """
    One-time repair: ensures every active subscriber has both
    Default quiz folder and Default question bank folder.
    Safe to run on every startup — INSERT OR IGNORE is a no-op if already exists.
    """
    _conn, _cur = get_db()
    _cur.execute("SELECT user_id FROM subscribers WHERE is_active = 1")
    subscriber_ids = [row[0] for row in _cur.fetchall()]

    for uid in subscriber_ids:
        _cur.execute(
            "INSERT OR IGNORE INTO folders (owner_id, name) VALUES (?, 'Default')",
            (uid,)
        )
        _cur.execute(
            "INSERT OR IGNORE INTO question_bank_folders (owner_id, name) VALUES (?, 'Default')",
            (uid,)
        )

    _conn.commit()
    _conn.close()

async def quiz_folder_prev(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    context.user_data["quiz_folder_page"] = max(
        0, context.user_data.get("quiz_folder_page", 0) - 1
    )

    await show_quiz_folders(query.message, context)


async def quiz_folder_next(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    context.user_data["quiz_folder_page"] = context.user_data.get("quiz_folder_page", 0) + 1

    await show_quiz_folders(query.message, context)

async def qb_move_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # Safety check
    qid = context.user_data.get("active_question_id")
    if not qid:
        await flash_message(context.bot, query.message.chat_id, "❌ No question selected.")
        return

    # Reset pagination
    context.user_data["qb_move_page"] = 0

    await show_qb_move_folders(query.message, context)

async def qb_move_prev(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    context.user_data["qb_move_page"] = max(
        0, context.user_data.get("qb_move_page", 0) - 1
    )

    await show_qb_move_folders(query.message, context)


async def qb_move_next(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    context.user_data["qb_move_page"] = context.user_data.get("qb_move_page", 0) + 1

    await show_qb_move_folders(query.message, context)

async def show_qb_move_folders(message, context):
    page = context.user_data.get("qb_move_page", 0)
    PER_PAGE = 5

    _conn, _cur = get_db()
    _cur.execute(
        """
        SELECT id, name FROM question_bank_folders
        WHERE owner_id=?
        ORDER BY name COLLATE NOCASE
        """,
        (get_active_user_id(context),)
    )
    folders = _cur.fetchall()
    _conn.close()

    total = len(folders)
    pages = (total - 1) // PER_PAGE + 1 if total else 1
    page = max(0, min(page, pages - 1))

    start = page * PER_PAGE
    end = start + PER_PAGE
    page_items = folders[start:end]

    keyboard = []

    for folder_id, name in page_items:
        keyboard.append([
            InlineKeyboardButton(
                f"📁 {name}",
                callback_data=f"QB_MOVE_TO|{folder_id}"
            )
        ])

    if pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("◀ Prev", callback_data="QB_MOVE_PREV"))
        nav.append(InlineKeyboardButton(f"{page + 1}/{pages}", callback_data="QB_MOVE_NOP"))
        if page < pages - 1:
            nav.append(InlineKeyboardButton("Next ▶", callback_data="QB_MOVE_NEXT"))
        keyboard.append(nav)

    keyboard.append([
        InlineKeyboardButton("⬅️ Cancel", callback_data="EDIT_QUESTIONS")
    ])

    await message.edit_text(
        "📂 Move Question\n\nSelect destination folder:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

####################################################################################################################################################################################################################################
# CODE BY PARTS - PART 4
####################################################################################################################################################################################################################################

async def qb_move_apply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    qid = context.user_data.get("active_question_id")
    if not qid:
        await flash_message(context.bot, query.message.chat_id, "❌ No question selected.")
        return

    folder_id = int(query.data.split("|", 1)[1])

    # 🔐 WRITE SECTION (LOCKED)
    try:
        async with DB_LOCK:
            _conn, _cur = get_db()
            _cur.execute(
                "UPDATE question_bank SET folder_id=? WHERE id=?",
                (folder_id, qid)
            )
            _conn.commit()
            _conn.close()

    except Exception as e:
        print("⚠️ Failed to move question:", e)
        await flash_message(context.bot, query.message.chat_id, "❌ Failed to move question.")
        return

    await flash_message(context.bot, query.message.chat_id, "✅ Question moved successfully.")

    # Return to Database view
    await show_database_menu(query.message, context)

async def qb_open_folder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data.startswith("QB_OPEN_FOLDER|"):
        folder_name = query.data.split("|", 1)[1]
        context.user_data["qb_folder_name"] = folder_name
        context.user_data["qb_q_page"] = 0
        context.user_data.setdefault("qb_selected", set())
    else:
        folder_name = context.user_data.get("qb_folder_name")

    if not folder_name:
        await flash_message(context.bot, query.message.chat_id, "❌ Folder context lost.")
        return

    _conn, _cur = get_db()
    _cur.execute(
        "SELECT id FROM question_bank_folders WHERE owner_id=? AND name=?",
        (get_active_user_id(context), folder_name)
    )
    row = _cur.fetchone()
    _conn.close()

    if not row:
        await flash_message(context.bot, query.message.chat_id, "❌ Folder not found.")
        return
    folder_id = row[0]

    _conn2, _cur2 = get_db()
    _cur2.execute("SELECT COUNT(*) FROM question_bank WHERE folder_id=?", (folder_id,))
    count = _cur2.fetchone()[0]
    _conn2.close()

    if count == 0:
        msg = await query.message.reply_text(
            f"📁 **{folder_name}**\n\n_No questions in this folder._",
            parse_mode="Markdown"
        )
        await asyncio.sleep(2)
        try:
            await msg.delete()
        except:
            pass
        return

    reply_markup = build_qb_question_keyboard(context)

    await query.message.edit_text(
        f"📁 **{folder_name}**\n\nSelect questions:",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )

async def ocr_accept(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User confirmed the OCR result — move to correct answer selection."""
    query = update.callback_query
    await query.answer()

    question = context.user_data.pop("ocr_question", "")
    options  = context.user_data.pop("ocr_options", [])

    # ✅ Store in new_question exactly like the manual flow
    context.user_data["new_question"] = {
        "text":    question,
        "options": options,
        "image":   None,
    }

    context.user_data["add_q_state"] = "NEW_Q_CORRECT"

    labels = ["1️⃣", "2️⃣", "3️⃣", "4️⃣"]
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"{labels[i]} {options[i]}", callback_data=f"CORRECT_{i}")]
        for i in range(len(options))
    ])

    msg = await query.message.reply_text(
        "✅ Choose the correct answer:",
        reply_markup=keyboard
    )
    context.user_data.setdefault("question_flow_msgs", []).append(msg.message_id)


async def ocr_retake(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User wants to send a different photo."""
    query = update.callback_query
    await query.answer()

    context.user_data.pop("ocr_question", None)
    context.user_data.pop("ocr_options",  None)
    context.user_data["add_q_state"] = "NEW_Q_PHOTO_WAIT"

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Cancel", callback_data="CANCEL_CREATE_QUESTION")]
    ])

    await query.message.edit_text(
        "📷 *Send Photo*\n\n"
        "Send a clear photo of your question.\n"
        "Make sure the text and answer options are fully visible.",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )

async def cancel_create_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    chat_id = query.message.chat_id

    # 🧹 Delete the prompt message (with Cancel button)
    try:
        await query.message.delete()
    except:
        pass

    # 🧹 Clear question creation state
    context.user_data.pop("add_q_state", None)
    context.user_data.pop("new_question", None)
    context.user_data.pop("question_flow_msgs", None)

    # 🚫 DO NOT redraw Home
    # 🚫 DO NOT send any new message

async def cancel_timer_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # 🧹 Delete ONLY the Timer Settings menu
    msg_id = context.user_data.pop("edit_timer_prompt_id", None)
    if msg_id:
        try:
            await context.bot.delete_message(
                chat_id=query.message.chat_id,
                message_id=msg_id
            )
        except:
            pass

    # 🔕 IMPORTANT:
    # Do NOT send any new message
    # Do NOT redraw Quiz Overview
    # Let the previous screen remain

async def cancel_shuffle_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # 🧹 Delete ONLY the Shuffle Settings menu
    msg_id = context.user_data.pop("shuffle_menu_msg_id", None)
    if msg_id:
        try:
            await context.bot.delete_message(
                chat_id=query.message.chat_id,
                message_id=msg_id
            )
        except:
            pass

    # 🔕 IMPORTANT:
    # Do NOT send any new message
    # Do NOT redraw Quiz Overview
    # Let the previous screen remain visible

async def cancel_edit_question_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
 
    chat_id = query.message.chat_id
 
    prompt_id = context.user_data.pop("edit_text_prompt_id", None)
    if prompt_id:
        try:
            await context.bot.delete_message(chat_id, prompt_id)
        except Exception:
            pass
 
    context.user_data.pop("edit_q_field", None)
 
    # Restore full preview buttons on the existing preview message
    await rebuild_question_preview(chat_id, context)

async def force_stop_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    play = context.user_data.get("play")

    if not play:
        return

    # =========================
    # 🧮 ANTI-ABUSE RULE
    # =========================
    answered_questions = play.get("index", 0)
    MIN_REQUIRED = 3
    leaderboard_key = context.user_data.get("leaderboard_key")

    if answered_questions >= MIN_REQUIRED and leaderboard_key:
        GROUP_LEADERBOARDS.setdefault(leaderboard_key, {})

        if user_id not in GROUP_LEADERBOARDS[leaderboard_key]:

            # 1️⃣ Save to MEMORY
            GROUP_LEADERBOARDS[leaderboard_key][user_id] = {
                "name": play["user_name"],
                "score": play["score"],
            }

            # 2️⃣ Save to DATABASE
            try:
                async with DB_LOCK:
                    _conn, _cur = get_db()
                    _cur.execute("""
                        INSERT OR IGNORE INTO group_leaderboard
                        (leaderboard_key, user_id, name, score)
                        VALUES (?, ?, ?, ?)
                    """, (
                        leaderboard_key,
                        user_id,
                        play["user_name"],
                        play["score"]
                    ))
                    _conn.commit()
                    _conn.close()
            except Exception as e:
                print("⚠️ Failed to save leaderboard on stop:", e)

            # 3️⃣ Update group message
            try:
                await update_group_leaderboard(leaderboard_key, context)
            except Exception as e:
                print("⚠️ Leaderboard update failed on stop:", e)

    # =========================
    # 🛑 LOCK QUIZ STATE
    # =========================
    play["locked"] = True
    play["finished"] = True
    play["ended"] = True

    # ⛔ Cancel timer safely
    task = play.get("timer_task")
    current = asyncio.current_task()
    if task and task is not current:
        task.cancel()
    play["timer_task"] = None

    # =========================
    # 🧹 INSTANT BULK DELETE
    # =========================
    delete_tasks = []

    # Question messages
    for msg_id in play.get("question_message_ids", []):
        delete_tasks.append(
            context.bot.delete_message(chat_id=user_id, message_id=msg_id)
        )

    # Timer messages
    for msg_id in play.get("timer_message_ids", []):
        delete_tasks.append(
            context.bot.delete_message(chat_id=user_id, message_id=msg_id)
        )

    # Warning message
    warning_id = play.get("warning_message_id")
    if warning_id:
        delete_tasks.append(
            context.bot.delete_message(chat_id=user_id, message_id=warning_id)
        )

    if delete_tasks:
        await asyncio.gather(*delete_tasks, return_exceptions=True)

    # =========================
    # 🧼 CLEAN MEMORY
    # =========================
    play.clear()
    context.user_data.pop("play", None)
    context.user_data.pop("play_quiz_id", None)

    # =========================
    # ✅ Confirmation (auto-clean)
    # =========================
    msg = await query.message.reply_text("🛑 Quiz stopped. You may continue.")
    await asyncio.sleep(1.5)
    try:
        await msg.delete()
    except:
        pass

async def global_quiz_guard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    # 🔐 Global Rate Limit
    if is_rate_limited(query.from_user.id):
        try:
            await query.answer()
        except Exception:
            pass
        raise ApplicationHandlerStop

    data = query.data or ""

    # 🔒 Only apply quiz guard in private chats
    # Group callbacks (leaderboard buttons) should never trigger this
    if update.effective_chat and update.effective_chat.type in ("group", "supergroup", "channel"):
        return

    play = context.user_data.get("play")

    # ✅ No quiz running → allow everything
    if not play:
        return

    # ✅ Always allow answer buttons
    if data.startswith("PLAY_ANSWER_"):
        return

    # ✅ Always allow stop / resume
    if data in ("FORCE_STOP_QUIZ", "RESUME_QUIZ"):
        return

    # ✅ If warning already shown → block silently
    if play.get("warning_message_id"):
        raise ApplicationHandlerStop

    # ⚠️ SHOW WARNING MESSAGE (ONCE)
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("▶️ Resume Quiz", callback_data="RESUME_QUIZ"),
            InlineKeyboardButton("🛑 Stop Quiz", callback_data="FORCE_STOP_QUIZ"),
        ]
    ])

    try:
        msg = await query.message.reply_text(
            "⚠️ A quiz is currently running.\n\n"
            "Please stop or resume the quiz to continue. ⚠️ Stopping the quiz after 3 Questions will update the Score Leaderboard",
            reply_markup=keyboard
        )
        # 🔒 Lock UI with warning message ID
        play["warning_message_id"] = msg.message_id
    except Exception:
        pass

    # 🚫 Stop all other handlers
    raise ApplicationHandlerStop

async def resume_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    play = context.user_data.get("play")
    if not play:
        return

    # 🧹 Remove warning message
    warning_id = play.get("warning_message_id")
    if warning_id:
        try:
            await context.bot.delete_message(
                chat_id=query.from_user.id,
                message_id=warning_id
            )
        except:
            pass

    # 🔓 Unlock UI
    play.pop("warning_message_id", None)

async def shuffle_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # 🧹 Delete shuffle menu only
    msg_id = context.user_data.pop("shuffle_menu_msg_id", None)
    if msg_id:
        try:
            await context.bot.delete_message(
                chat_id=query.message.chat_id,
                message_id=msg_id
            )
        except:
            pass

    # 🔕 DO NOT send any new message
    # Let the existing quiz overview stay visible

def build_qb_question_keyboard(context):
    folder_name = context.user_data.get("qb_folder_name")
    page = context.user_data.get("qb_q_page", 0)
    selected = context.user_data.setdefault("qb_selected", set())
    quiz_id = context.user_data.get("active_quiz_id")
    PER_PAGE = 10

    _conn, _cur = get_db()
    _cur.execute(
        "SELECT id FROM question_bank_folders WHERE owner_id=? AND name=?",
        (get_active_user_id(context), folder_name)
    )
    row = _cur.fetchone()
    _conn.close()

    if not row:
        return InlineKeyboardMarkup([])
    folder_id = row[0]

    _conn2, _cur2 = get_db()
    _cur2.execute(
        "SELECT id, question FROM question_bank WHERE folder_id=? ORDER BY question COLLATE NOCASE",
        (folder_id,)
    )
    questions = _cur2.fetchall()
    _conn2.close()

    _conn3, _cur3 = get_db()
    _cur3.execute(
        "SELECT question_id FROM quiz_question_links WHERE quiz_id=?",
        (quiz_id,)
    )
    linked_questions = {row[0] for row in _cur3.fetchall()}
    _conn3.close()

    total = len(questions)
    pages = (total - 1) // PER_PAGE + 1 if total else 1
    page = max(0, min(page, pages - 1))
    context.user_data["qb_q_page"] = page

    start = page * PER_PAGE
    end = start + PER_PAGE
    page_items = questions[start:end]

    keyboard = []

    keyboard.append([
        InlineKeyboardButton("📄 Add this Page", callback_data="QB_ADD_THIS_PAGE"),
    ])
    keyboard.append([
        InlineKeyboardButton("🎲 Add 10",  callback_data="QB_AUTO_ADD|10"),
        InlineKeyboardButton("🎲 Add 50",  callback_data="QB_AUTO_ADD|50"),
        InlineKeyboardButton("🎲 Add 100", callback_data="QB_AUTO_ADD|100"),
    ])

    for qid, text in page_items:
        already_added = qid in linked_questions
        if already_added:
            label    = f"✅ {text[:45]}"
            callback = f"QB_REMOVE_Q|{qid}"
        else:
            checked  = "☑" if qid in selected else "⬜"
            label    = f"{checked} {text[:45]}"
            callback = f"QB_SELECT_Q|{qid}"
        keyboard.append([InlineKeyboardButton(label, callback_data=callback)])

    if pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("◀ Prev", callback_data="QB_Q_PREV"))
        nav.append(InlineKeyboardButton(f"{page + 1}/{pages}", callback_data="QB_Q_NOP"))
        if page < pages - 1:
            nav.append(InlineKeyboardButton("Next ▶", callback_data="QB_Q_NEXT"))
        keyboard.append(nav)

    keyboard.append([
        InlineKeyboardButton("🧹 Clear Selection",              callback_data="QB_CLEAR_SELECTED"),
        InlineKeyboardButton(f"➕ Add Selected ({len(selected)})", callback_data="QB_ADD_SELECTED"),
    ])
    keyboard.append([
        InlineKeyboardButton("⬅️ Back", callback_data="QB_PICK_FOLDER")
    ])

    return InlineKeyboardMarkup(keyboard)

async def qb_toggle_select_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    qid = int(query.data.split("|")[1])

    selected = context.user_data.setdefault("qb_selected", set())

    if qid in selected:
        selected.remove(qid)
    else:
        selected.add(qid)

    # Rebuild keyboard only
    reply_markup = build_qb_question_keyboard(context)

    # Edit the SAME message instead of sending a new one
    await query.edit_message_reply_markup(reply_markup=reply_markup)

async def qb_add_selected_questions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    quiz_id  = context.user_data.get("active_quiz_id")
    selected = context.user_data.get("qb_selected", set())

    if not quiz_id or not selected:
        await flash_message(context.bot, query.message.chat_id, "❌ No questions selected.")
        return

    added = 0

    try:
        async with DB_LOCK:
            _conn, _cur = get_db()
            _cur.execute(
                "SELECT COALESCE(MAX(position), 0) FROM quiz_question_links WHERE quiz_id=?",
                (quiz_id,)
            )
            position = _cur.fetchone()[0]

            for qid in selected:
                _cur.execute(
                    "SELECT 1 FROM quiz_question_links WHERE quiz_id=? AND question_id=?",
                    (quiz_id, qid)
                )
                if _cur.fetchone():
                    continue
                position += 1
                _cur.execute(
                    "INSERT INTO quiz_question_links (quiz_id, question_id, position) VALUES (?, ?, ?)",
                    (quiz_id, qid, position)
                )
                added += 1

            _conn.commit()
            _conn.close()

    except Exception as e:
        print("⚠️ Failed to add selected questions:", e)
        await flash_message(context.bot, query.message.chat_id, "❌ Failed to add questions.")
        return

    context.user_data["qb_selected"] = set()
    context.user_data.pop("qb_q_page", None)

    await flash_message(
        context.bot, query.message.chat_id,
        f"✅ {added} question(s) added to the quiz."
    )

    asyncio.create_task(refresh_all_group_posts_for_quiz(quiz_id, context))

    context.user_data["reset_q_page"] = True
    await show_questions_from_message(query.message, context)

async def qb_add_this_page(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    folder_name = context.user_data.get("qb_folder_name")
    page        = context.user_data.get("qb_q_page", 0)
    selected    = context.user_data.setdefault("qb_selected", set())
    quiz_id     = context.user_data.get("active_quiz_id")
    PER_PAGE    = 10

    _conn, _cur = get_db()
    _cur.execute(
        "SELECT id FROM question_bank_folders WHERE owner_id=? AND name=?",
        (get_active_user_id(context), folder_name)
    )
    row = _cur.fetchone()
    _conn.close()

    if not row:
        return
    folder_id = row[0]

    _conn2, _cur2 = get_db()
    _cur2.execute(
        "SELECT id FROM question_bank WHERE folder_id=? ORDER BY question COLLATE NOCASE",
        (folder_id,)
    )
    all_questions = [row[0] for row in _cur2.fetchall()]
    _conn2.close()

    _conn3, _cur3 = get_db()
    _cur3.execute(
        "SELECT question_id FROM quiz_question_links WHERE quiz_id=?",
        (quiz_id,)
    )
    linked = {row[0] for row in _cur3.fetchall()}
    _conn3.close()

    total  = len(all_questions)
    pages  = (total - 1) // PER_PAGE + 1 if total else 1
    page   = max(0, min(page, pages - 1))
    start  = page * PER_PAGE
    end    = start + PER_PAGE

    for qid in all_questions[start:end]:
        if qid not in linked:
            selected.add(qid)

    reply_markup = build_qb_question_keyboard(context)
    await query.edit_message_reply_markup(reply_markup=reply_markup)

async def qb_remove_question_from_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    qid = int(query.data.split("|")[1])
    quiz_id = context.user_data.get("active_quiz_id")

    if not quiz_id:
        return

    try:
        async with DB_LOCK:
            _conn, _cur = get_db()
            _cur.execute(
                """
                DELETE FROM quiz_question_links
                WHERE quiz_id=? AND question_id=?
                """,
                (quiz_id, qid)
            )
            _conn.commit()
            _conn.close()
    except Exception as e:
        print("⚠️ Failed to remove question:", e)
        await query.answer("❌ Failed to remove question.", show_alert=True)
        return

    # 🔄 SYNC: Refresh all active group posts for this quiz
    asyncio.create_task(
        refresh_all_group_posts_for_quiz(quiz_id, context)
    )

    reply_markup = build_qb_question_keyboard(context)
    await query.edit_message_reply_markup(reply_markup=reply_markup)

async def qb_auto_add_questions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    limit       = int(query.data.split("|")[1])
    active_uid  = get_active_user_id(context)
    quiz_id     = context.user_data.get("active_quiz_id")
    folder_name = context.user_data.get("qb_folder_name")

    if not quiz_id or not folder_name:
        return

    _conn, _cur = get_db()
    _cur.execute(
        "SELECT id FROM question_bank_folders WHERE owner_id=? AND name=?",
        (active_uid, folder_name)
    )
    row = _cur.fetchone()
    _conn.close()

    if not row:
        return
    folder_id = row[0]

    _conn2, _cur2 = get_db()
    _cur2.execute(
        "SELECT id FROM question_bank WHERE folder_id=?",
        (folder_id,)
    )
    all_questions = {row[0] for row in _cur2.fetchall()}
    _conn2.close()

    _conn3, _cur3 = get_db()
    _cur3.execute(
        "SELECT question_id FROM quiz_question_links WHERE quiz_id=?",
        (quiz_id,)
    )
    already_linked = {row[0] for row in _cur3.fetchall()}
    _conn3.close()

    selected   = context.user_data.setdefault("qb_selected", set())
    candidates = list(all_questions - already_linked - selected)

    if not candidates:
        return

    random.shuffle(candidates)
    selected.update(candidates[:limit])

    reply_markup = build_qb_question_keyboard(context)
    await query.edit_message_reply_markup(reply_markup=reply_markup)

async def show_quiz_action_menu_by_id(chat_id, message_id, context):
    quiz_id = context.user_data.get("active_quiz_id")
    if not quiz_id:
        return
    if not verify_quiz_owner(quiz_id, context):
        return

    _conn, _cur = get_db()
    _cur.execute("""
        SELECT q.title, q.description, q.timer, q.shuffle_q, q.shuffle_a,
               COUNT(ql.question_id)
        FROM quizzes q
        LEFT JOIN quiz_question_links ql ON q.quiz_id = ql.quiz_id
        WHERE q.quiz_id=?
        GROUP BY q.quiz_id
    """, (quiz_id,))
    row = _cur.fetchone()
    _conn.close()

    if not row:
        return
    title, desc, timer, sq, sa, total_questions = row

    text = f"📘 **{escape_md(title)}**"
    if desc:
        text += f"\n📝 _{escape_md(desc)}_"
    text += "\n\n"
    text += f"📊 Questions: {total_questions}    ⏱ Timer: {timer}s"
    text += (
        f"\n🔀 Questions: {'ON' if sq else 'OFF'}"
        f"   🔀 Options: {'ON' if sa else 'OFF'}"
    )

    keyboard = [
        [
            InlineKeyboardButton("▶️ Start this Quiz", callback_data="START_THIS"),
            InlineKeyboardButton("📤 Post this Quiz",  callback_data="POST_QUIZ"),
        ],
        [
            InlineKeyboardButton("✏️ Edit this Quiz",  callback_data="EDIT_THIS"),
            InlineKeyboardButton("📁 Move this Quiz",  callback_data="MOVE_QUIZ"),
        ],
        [
            InlineKeyboardButton("🗑 Delete this Quiz", callback_data="DELETE_QUIZ"),
            InlineKeyboardButton("⬅️ Back",            callback_data="BACK_TO_QUIZZES"),
        ],
    ]

    await context.bot.edit_message_text(
        chat_id=chat_id,
        message_id=message_id,
        text=text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

async def delete_finish_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # 🧹 Delete the finished quiz message itself
    try:
        await query.message.delete()
    except:
        pass

    # 🔕 No messages, no redraw, no state changes

async def cancel_edit_question_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
 
    chat_id = query.message.chat_id
 
    menu_id = context.user_data.pop("edit_image_menu_msg_id", None)
    if menu_id:
        try:
            await context.bot.delete_message(chat_id, menu_id)
        except Exception:
            pass
 
    context.user_data.pop("edit_q_field", None)
 
    # Restore full preview buttons
    await rebuild_question_preview(chat_id, context)

async def apply_new_options_correct(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    qid = context.user_data.get("active_question_id")
    new_options = context.user_data.get("edit_options", [])

    if not qid or len(new_options) != 4:
        return

    correct_index = int(query.data.split("_")[-1])
    options_text = "||".join(new_options)

    # 🔐 SAFE WRITE SECTION
    try:
        async with DB_LOCK:
            _conn, _cur = get_db()
            _cur.execute(
                """
                UPDATE question_bank
                SET options=?, correct=?
                WHERE id=?
                """,
                (options_text, correct_index, qid)
            )
            _conn.commit()
            _conn.close()

    except Exception as e:
        print("⚠️ Failed to update options:", e)
        await query.answer("❌ Failed to update options.", show_alert=True)
        return

    chat_id = query.message.chat_id

    # Confirmation message
    msg = await query.message.reply_text("✅ Options updated.")
    context.user_data["edit_options_flow_msgs"].append(msg.message_id)

    await asyncio.sleep(1)

    # =========================
    # FULL DECLUTTER
    # =========================
    for mid in context.user_data.get("edit_options_flow_msgs", []):
        try:
            await context.bot.delete_message(chat_id, mid)
        except:
            pass

    # Clear edit state
    context.user_data.pop("edit_options_flow_msgs", None)
    context.user_data.pop("edit_options", None)
    context.user_data.pop("edit_q_field", None)

    # 🔁 Rebuild clean preview
    await rebuild_question_preview(chat_id, context)

async def cancel_edit_question_options(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
 
    chat_id = query.message.chat_id
 
    for mid in context.user_data.get("edit_options_flow_msgs", []):
        try:
            await context.bot.delete_message(chat_id, mid)
        except Exception:
            pass
 
    context.user_data.pop("edit_options_flow_msgs", None)
    context.user_data.pop("edit_options", None)
    context.user_data.pop("edit_q_field", None)
 
    # Restore full preview buttons
    await rebuild_question_preview(chat_id, context)

async def cancel_edit_question_explanation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
 
    chat_id = query.message.chat_id
 
    prompt_id = context.user_data.pop("edit_expl_prompt_id", None)
    if prompt_id:
        try:
            await context.bot.delete_message(chat_id, prompt_id)
        except Exception:
            pass
 
    context.user_data.pop("edit_q_field", None)
 
    # Restore full preview buttons
    await rebuild_question_preview(chat_id, context)

async def back_to_questions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    chat_id = query.message.chat_id

    # 🧹 Always delete the current preview message (photo or text)
    # and build the question list on a fresh message to avoid edit-type conflicts.
    preview_id = context.user_data.pop("question_preview_msg_id", None)

    # Delete the message that contains the Return button (the preview itself)
    try:
        await query.message.delete()
    except Exception:
        pass

    # Also delete any separately tracked preview if it differs
    if preview_id and preview_id != query.message.message_id:
        try:
            await context.bot.delete_message(chat_id, preview_id)
        except Exception:
            pass

    # Check which mode we are in (Database folder or Quiz question list)
    preview_mode = context.user_data.get("preview_mode", "QUIZ")

    if preview_mode == "DATABASE":
        # Return to database folder question list
        new_msg = await context.bot.send_message(chat_id=chat_id, text="⏳")
        await show_db_questions_from_message(new_msg, context)
    else:
        # Return to quiz question list
        new_msg = await context.bot.send_message(chat_id=chat_id, text="⏳")
        await show_questions_from_message(new_msg, context)

async def show_db_questions_from_message(message, context):
    active_uid = get_active_user_id(context)
    folder_name = context.user_data.get("db_folder_name")
    if not folder_name:
        return

    page = context.user_data.get("db_q_page", 0)
    PER_PAGE = 10

    _conn, _cur = get_db()
    _cur.execute(
        "SELECT id FROM question_bank_folders WHERE owner_id=? AND name=?",
        (active_uid, folder_name)
    )
    row = _cur.fetchone()
    _conn.close()

    if not row:
        return
    folder_id = row[0]

    _conn2, _cur2 = get_db()
    _cur2.execute(
        """
        SELECT id, question FROM question_bank
        WHERE folder_id=?
        ORDER BY question COLLATE NOCASE
        """,
        (folder_id,)
    )
    rows = _cur2.fetchall()
    _conn2.close()

    keyboard = []

    if not rows:
        if folder_name != "Default":
            keyboard.append([
                InlineKeyboardButton("✏️ Rename", callback_data=f"DB_RENAME_FOLDER|{folder_name}"),
                InlineKeyboardButton("📥 Move Questions In", callback_data=f"DB_MOVE_IN|{folder_name}")
            ])
            keyboard.append([
                InlineKeyboardButton("🗑 Delete Folder", callback_data=f"DB_DELETE_FOLDER|{folder_name}"),
                InlineKeyboardButton("⬅️ Back", callback_data="HOME_DATABASE")
            ])
        else:
            keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="HOME_DATABASE")])

        await message.edit_text(
            f"📁 **{folder_name}**\n\n_No questions in this folder yet._",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
        return

    total = len(rows)
    pages = (total - 1) // PER_PAGE + 1
    page = max(0, min(page, pages - 1))
    context.user_data["db_q_page"] = page

    start = page * PER_PAGE
    end = start + PER_PAGE

    for qid, text in rows[start:end]:
        keyboard.append([
            InlineKeyboardButton(text[:50], callback_data=f"Q_{qid}")
        ])

    if pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("◀ Prev", callback_data="DB_Q_PREV"))
        nav.append(InlineKeyboardButton(f"{page + 1}/{pages}", callback_data="DB_Q_NOP"))
        if page < pages - 1:
            nav.append(InlineKeyboardButton("Next ▶", callback_data="DB_Q_NEXT"))
        keyboard.append(nav)

    if folder_name != "Default":
        keyboard.append([
            InlineKeyboardButton("✏️ Rename", callback_data=f"DB_RENAME_FOLDER|{folder_name}"),
            InlineKeyboardButton("📥 Move Questions In", callback_data=f"DB_MOVE_IN|{folder_name}")
        ])
        keyboard.append([
            InlineKeyboardButton("🗑 Delete Folder", callback_data=f"DB_DELETE_FOLDER|{folder_name}"),
            InlineKeyboardButton("⬅️ Back", callback_data="HOME_DATABASE")
        ])
    else:
        keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="HOME_DATABASE")])

    try:
        await message.edit_text(
            f"📁 {folder_name}\n\nSelect a question:",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    except Exception:
        try:
            await message.edit_text(
                f"📁 {folder_name}\n\nSelect a question:",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
        except Exception:
            pass

async def move_q_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    context.user_data["move_mode"] = "MOVE"
    context.user_data["mc_folder_page"] = 0
    context.user_data["mc_quiz_page"] = 0

    await show_move_copy_folders(query.message, context)

async def copy_q_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    context.user_data["move_mode"] = "COPY"
    context.user_data["mc_folder_page"] = 0
    context.user_data["mc_quiz_page"] = 0

    await show_move_copy_folders(query.message, context)

async def show_move_copy_folders(message, context):
    PER_PAGE = 5
    page = context.user_data.get("mc_folder_page", 0)

    _conn, _cur = get_db()
    _cur.execute("""
        SELECT name FROM folders
        WHERE owner_id=?
        ORDER BY
            CASE WHEN name='Default' THEN 0 ELSE 1 END,
            name COLLATE NOCASE
    """, (get_active_user_id(context),))
    folders = [row[0] for row in _cur.fetchall()]
    _conn.close()

    total = len(folders)
    pages = (total - 1) // PER_PAGE + 1 if total else 1
    page = max(0, min(page, pages - 1))
    context.user_data["mc_folder_page"] = page

    start = page * PER_PAGE
    end = start + PER_PAGE
    page_items = folders[start:end]

    keyboard = []

    for folder_name in page_items:
        _conn2, _cur2 = get_db()
        _cur2.execute("""
            SELECT COUNT(*) FROM quizzes
            WHERE owner_id=? AND folder=?
        """, (get_active_user_id(context), folder_name))
        count = _cur2.fetchone()[0]
        _conn2.close()

        keyboard.append([
            InlineKeyboardButton(
                f"📁 {folder_name} ({count})",
                callback_data=f"MC_FOLDER|{folder_name}"
            )
        ])

    if pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("◀ Prev", callback_data="MC_FOLDER_PREV"))
        nav.append(InlineKeyboardButton(f"{page+1}/{pages}", callback_data="MC_NOP"))
        if page < pages - 1:
            nav.append(InlineKeyboardButton("Next ▶", callback_data="MC_FOLDER_NEXT"))
        keyboard.append(nav)

    keyboard.append([
        InlineKeyboardButton("⬅️ Back", callback_data="BACK_TO_QUESTIONS")
    ])

    await safe_edit_message(
        message,
        "📂 Select Folder:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def move_copy_open_folder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # 🔑 Get selected folder
    folder = query.data.split("|")[1]
    context.user_data["mc_folder"] = folder

    # 🔄 Reset quiz pagination when opening a new folder
    context.user_data["mc_quiz_page"] = 0

    # Show quiz list
    await show_move_copy_quizzes(query.message, context)

async def move_copy_apply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    target_quiz_id = query.data.split("|")[1]

    current_quiz_id = context.user_data.get("active_quiz_id")
    qid = context.user_data.get("active_question_id")
    mode = context.user_data.get("move_mode")

    if not qid or not target_quiz_id:
        return

    try:
        # 🔐 FULL WRITE SECTION (LOCK EVERYTHING DB-RELATED)
        async with DB_LOCK:
            _conn, _cur = get_db()

            # COPY or MOVE logic
            if mode == "MOVE":
                _cur.execute(
                    "DELETE FROM quiz_question_links WHERE quiz_id=? AND question_id=?",
                    (current_quiz_id, qid)
                )

            # Check if already exists in target
            _cur.execute(
                "SELECT 1 FROM quiz_question_links WHERE quiz_id=? AND question_id=?",
                (target_quiz_id, qid)
            )

            if not _cur.fetchone():

                # 🔢 Get next position safely
                _cur.execute(
                    """
                    SELECT COALESCE(MAX(position), 0) + 1
                    FROM quiz_question_links
                    WHERE quiz_id=?
                    """,
                    (target_quiz_id,)
                )
                next_position = _cur.fetchone()[0]

                _cur.execute(
                    """
                    INSERT INTO quiz_question_links (quiz_id, question_id, position)
                    VALUES (?, ?, ?)
                    """,
                    (target_quiz_id, qid, next_position)
                )

            _conn.commit()
            _conn.close()

    except Exception as e:
        print("⚠️ Failed to move/copy question:", e)
        await query.answer("❌ Operation failed.", show_alert=True)
        return

    await flash_message(context.bot, query.message.chat_id, "✅ Operation completed.")

    # 🧹 Delete folder/quiz list message
    try:
        await query.message.delete()
    except:
        pass

    context.user_data.pop("move_mode", None)
    context.user_data.pop("mc_folder_page", None)
    context.user_data.pop("mc_quiz_page", None)
    context.user_data.pop("mc_folder", None)

    context.user_data["reset_q_page"] = True

    await show_questions_from_message(query.message, context)

async def safe_edit_message(message, text, reply_markup=None, parse_mode=None):
    """
    Safely edits a message whether it is a text message
    or a media (photo) message.
    """

    try:
        # If message has photo → use caption
        if message.photo:
            await message.edit_caption(
                caption=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode
            )
        else:
            await message.edit_text(
                text=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode
            )
    except Exception as e:
        print("⚠️ Safe edit fallback:", e)

async def flash_message(bot, chat_id, text, delay=1):
    """
    Sends a temporary message and auto-deletes it after `delay` seconds.
    Keeps chat clean and consistent.
    """
    try:
        msg = await bot.send_message(chat_id=chat_id, text=text)
        await asyncio.sleep(delay)
        await msg.delete()
    except:
        pass

async def refresh_menu(message, text, reply_markup=None, parse_mode="Markdown"):
    """
    Edits existing message instead of sending new ones.
    Prevents flicker and loading messages.
    """
    try:
        if message.photo:
            await message.edit_caption(
                caption=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode
            )
        else:
            await message.edit_text(
                text=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode
            )
    except Exception as e:
        print("⚠️ Refresh failed:", e)

async def mc_folder_prev(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    context.user_data["mc_folder_page"] = max(
        0,
        context.user_data.get("mc_folder_page", 0) - 1
    )

    await show_move_copy_folders(query.message, context)


async def mc_folder_next(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    context.user_data["mc_folder_page"] = context.user_data.get("mc_folder_page", 0) + 1

    await show_move_copy_folders(query.message, context)

async def show_move_copy_quizzes(message, context):
    PER_PAGE = 5
    page   = context.user_data.get("mc_quiz_page", 0)
    folder = context.user_data.get("mc_folder")

    _conn, _cur = get_db()
    _cur.execute("""
        SELECT quiz_id, title FROM quizzes
        WHERE owner_id=? AND folder=?
    """, (get_active_user_id(context), folder))
    quizzes = _cur.fetchall()
    _conn.close()

    quizzes = sorted(quizzes, key=lambda r: natural_sort_key(r[1]))

    total  = len(quizzes)
    pages  = (total - 1) // PER_PAGE + 1 if total else 1
    page   = max(0, min(page, pages - 1))
    context.user_data["mc_quiz_page"] = page

    start      = page * PER_PAGE
    end        = start + PER_PAGE
    page_items = quizzes[start:end]

    keyboard = []
    for quiz_id, title in page_items:
        keyboard.append([
            InlineKeyboardButton(f"📘 {title}", callback_data=f"MC_APPLY|{quiz_id}")
        ])

    if pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("◀ Prev", callback_data="MC_QUIZ_PREV"))
        nav.append(InlineKeyboardButton(f"{page+1}/{pages}", callback_data="MC_NOP"))
        if page < pages - 1:
            nav.append(InlineKeyboardButton("Next ▶", callback_data="MC_QUIZ_NEXT"))
        keyboard.append(nav)

    keyboard.append([
        InlineKeyboardButton("⬅️ Back", callback_data="MOVE_Q_START")
    ])

    await safe_edit_message(
        message,
        f"📁 {folder}\n\nSelect Quiz:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def mc_quiz_prev(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    context.user_data["mc_quiz_page"] = max(
        0,
        context.user_data.get("mc_quiz_page", 0) - 1
    )

    await show_move_copy_quizzes(query.message, context)


async def mc_quiz_next(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    context.user_data["mc_quiz_page"] = context.user_data.get("mc_quiz_page", 0) + 1

    await show_move_copy_quizzes(query.message, context)

def is_rate_limited(user_id):
    now = time.time()
    last = USER_RATE_LIMIT.get(user_id, 0)

    if now - last < RATE_LIMIT_SECONDS:
        return True

    USER_RATE_LIMIT[user_id] = now

    # Prune entries older than 60 seconds to prevent unbounded growth
    cutoff = now - 60
    stale_keys = [uid for uid, t in USER_RATE_LIMIT.items() if t < cutoff]
    for uid in stale_keys:
        del USER_RATE_LIMIT[uid]

    return False

async def manage_question_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    await flash_message(context.bot, query.message.chat_id, 
        "⚙️ Manage options will be rebuilt soon."
    )

async def return_to_preview(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
 
    context.user_data["question_preview_msg_id"] = query.message.message_id
    await rebuild_question_preview(query.message.chat_id, context)

async def manage_question_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
 
    qid = context.user_data.get("active_question_id")
    if not qid:
        await flash_message(context.bot, query.message.chat_id, "❌ No question selected.")
        return
 
    # Ensure we can return to the preview from deep inside the manage flow
    context.user_data["question_preview_msg_id"] = query.message.message_id
 
    context.user_data["manage_folder_page"] = 0
    await show_manage_folders(query.message, context)

async def show_manage_folders(message, context):
    PER_PAGE = 5
    page = context.user_data.get("manage_folder_page", 0)

    _conn, _cur = get_db()
    _cur.execute("""
        SELECT name FROM folders
        WHERE owner_id=?
        ORDER BY
            CASE WHEN name='Default' THEN 0 ELSE 1 END,
            name COLLATE NOCASE
    """, (get_active_user_id(context),))
    folders = [row[0] for row in _cur.fetchall()]
    _conn.close()

    total = len(folders)
    pages = (total - 1) // PER_PAGE + 1 if total else 1
    page = max(0, min(page, pages - 1))
    context.user_data["manage_folder_page"] = page

    start = page * PER_PAGE
    end = start + PER_PAGE
    page_items = folders[start:end]

    keyboard = []

    for folder in page_items:
        _conn2, _cur2 = get_db()
        _cur2.execute("""
            SELECT COUNT(*) FROM quizzes
            WHERE owner_id=? AND folder=?
        """, (get_active_user_id(context), folder))
        count = _cur2.fetchone()[0]
        _conn2.close()

        keyboard.append([
            InlineKeyboardButton(
                f"📁 {folder} ({count})",
                callback_data=f"MANAGE_FOLDER|{folder}"
            )
        ])

    if pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("◀ Prev", callback_data="MANAGE_FOLDER_PREV"))
        nav.append(InlineKeyboardButton(f"{page+1}/{pages}", callback_data="MANAGE_NOP"))
        if page < pages - 1:
            nav.append(InlineKeyboardButton("Next ▶", callback_data="MANAGE_FOLDER_NEXT"))
        keyboard.append(nav)

    keyboard.append([
        InlineKeyboardButton("↩️ Return", callback_data="RETURN_TO_PREVIEW")
    ])

    await safe_edit_message(
        message,
        "📂 Select Folder:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def manage_folder_prev(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["manage_folder_page"] = max(
        0,
        context.user_data.get("manage_folder_page", 0) - 1
    )
    await show_manage_folders(query.message, context)


async def manage_folder_next(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["manage_folder_page"] += 1
    await show_manage_folders(query.message, context)

async def manage_open_folder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    folder = query.data.split("|")[1]
    context.user_data["manage_folder"] = folder
    context.user_data["manage_quiz_page"] = 0

    await show_manage_quizzes(query.message, context)

async def show_manage_quizzes(message, context):
    PER_PAGE = 5
    page   = context.user_data.get("manage_quiz_page", 0)
    folder = context.user_data.get("manage_folder")
    qid    = context.user_data.get("active_question_id")

    _conn, _cur = get_db()
    _cur.execute("""
        SELECT quiz_id, title FROM quizzes
        WHERE owner_id=? AND folder=?
    """, (get_active_user_id(context), folder))
    quizzes = _cur.fetchall()
    _conn.close()

    quizzes = sorted(quizzes, key=lambda r: natural_sort_key(r[1]))

    _conn2, _cur2 = get_db()
    _cur2.execute(
        "SELECT quiz_id FROM quiz_question_links WHERE question_id=?",
        (qid,)
    )
    linked = {row[0] for row in _cur2.fetchall()}
    _conn2.close()

    total  = len(quizzes)
    pages  = (total - 1) // PER_PAGE + 1 if total else 1
    page   = max(0, min(page, pages - 1))
    context.user_data["manage_quiz_page"] = page

    start      = page * PER_PAGE
    end        = start + PER_PAGE
    page_items = quizzes[start:end]

    keyboard = []
    for quiz_id, title in page_items:
        checked = "☑" if quiz_id in linked else "⬜"
        keyboard.append([
            InlineKeyboardButton(
                f"{checked} {title}",
                callback_data=f"MANAGE_TOGGLE|{quiz_id}"
            )
        ])

    if pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("◀ Prev", callback_data="MANAGE_QUIZ_PREV"))
        nav.append(InlineKeyboardButton(f"{page+1}/{pages}", callback_data="MANAGE_NOP"))
        if page < pages - 1:
            nav.append(InlineKeyboardButton("Next ▶", callback_data="MANAGE_QUIZ_NEXT"))
        keyboard.append(nav)

    keyboard.append([
        InlineKeyboardButton("⬅️ Back", callback_data="MANAGE_BACK_TO_FOLDERS")
    ])

    await safe_edit_message(
        message,
        f"📁 {folder}\n\nSelect quizzes:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def manage_toggle_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    quiz_id = query.data.split("|")[1]
    qid     = context.user_data.get("active_question_id")

    _conn, _cur = get_db()
    _cur.execute("""
        SELECT 1 FROM quiz_question_links
        WHERE quiz_id=? AND question_id=?
    """, (quiz_id, qid))
    already_linked = _cur.fetchone()
    _conn.close()

    if already_linked:
        async with DB_LOCK:
            _conn2, _cur2 = get_db()
            _cur2.execute("""
                DELETE FROM quiz_question_links
                WHERE quiz_id=? AND question_id=?
            """, (quiz_id, qid))
            _conn2.commit()
            _conn2.close()
    else:
        async with DB_LOCK:
            _conn3, _cur3 = get_db()
            _cur3.execute("""
                SELECT COALESCE(MAX(position), 0) + 1
                FROM quiz_question_links
                WHERE quiz_id=?
            """, (quiz_id,))
            position = _cur3.fetchone()[0]
            _cur3.execute("""
                INSERT INTO quiz_question_links (quiz_id, question_id, position)
                VALUES (?, ?, ?)
            """, (quiz_id, qid, position))
            _conn3.commit()
            _conn3.close()

    await show_manage_quizzes(query.message, context)

async def manage_quiz_prev(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    context.user_data["manage_quiz_page"] = max(
        0,
        context.user_data.get("manage_quiz_page", 0) - 1
    )

    await show_manage_quizzes(query.message, context)


async def manage_quiz_next(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    context.user_data["manage_quiz_page"] = context.user_data.get("manage_quiz_page", 0) + 1

    await show_manage_quizzes(query.message, context)

async def manage_back_to_folders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    await show_manage_folders(query.message, context)

async def qb_clear_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    quiz_id = context.user_data.get("active_quiz_id")
    if not quiz_id:
        return

    context.user_data["qb_selected"] = set()

    try:
        async with DB_LOCK:
            _conn, _cur = get_db()
            _cur.execute(
                "DELETE FROM quiz_question_links WHERE quiz_id=?",
                (quiz_id,)
            )
            _conn.commit()
            _conn.close()
    except Exception as e:
        print("⚠️ Failed to unlink questions:", e)
        await query.answer("❌ Failed to clear quiz.", show_alert=True)
        return

    # 🔄 SYNC: Refresh all active group posts for this quiz
    asyncio.create_task(
        refresh_all_group_posts_for_quiz(quiz_id, context)
    )

    reply_markup = build_qb_question_keyboard(context)

    try:
        await query.edit_message_reply_markup(reply_markup=reply_markup)
    except:
        pass

async def delete_question_from_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    quiz_id = context.user_data.get("active_quiz_id")
    qid = context.user_data.get("active_question_id")

    if not quiz_id or not qid:
        await flash_message(context.bot, query.message.chat_id, "❌ No question selected.")
        return

    # Store everything needed for deletion
    context.user_data["confirm_delete"] = ("QUESTION_FROM_QUIZ", qid)

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Confirm", callback_data="CONFIRM_DELETE"),
            InlineKeyboardButton("❌ Cancel", callback_data="CANCEL_DELETE"),
        ]
    ])

    await query.message.reply_text(
        "⚠️ Are you sure you want to remove this question from this quiz?\n\n"
        "This will NOT delete it from the Database.",
        reply_markup=keyboard
    )

async def delete_question_from_database(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    qid = context.user_data.get("active_question_id")
    if not qid:
        await flash_message(context.bot, query.message.chat_id, "❌ No question selected.")
        return

    # Show confirmation dialog — do NOT delete yet
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Yes, Delete", callback_data="DB_DELETE_Q_CONFIRM"),
            InlineKeyboardButton("❌ Cancel", callback_data="DB_DELETE_Q_CANCEL"),
        ]
    ])

    msg = await query.message.reply_text(
        "🗑 *Permanently delete this question?*\n\n"
        "⚠️ This will remove it from the Database and ALL quizzes it belongs to.",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )

    context.user_data["db_delete_q_confirm_msg_id"] = msg.message_id

async def db_delete_question_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    chat_id = query.message.chat_id

    confirm_msg_id = context.user_data.pop("db_delete_q_confirm_msg_id", None)
    if confirm_msg_id:
        try:
            await context.bot.delete_message(chat_id, confirm_msg_id)
        except:
            pass

    qid = context.user_data.get("active_question_id")
    if not qid:
        await flash_message(context.bot, chat_id, "❌ No question selected.")
        return

    try:
        async with DB_LOCK:
            _conn, _cur = get_db()
            _cur.execute("DELETE FROM quiz_question_links WHERE question_id=?", (qid,))
            _cur.execute("DELETE FROM question_bank WHERE id=?", (qid,))
            _conn.commit()
            _conn.close()
    except Exception as e:
        print("⚠️ Failed to permanently delete question:", e)
        await flash_message(context.bot, chat_id, "❌ Failed to delete.")
        return

    context.user_data.pop("active_question_id", None)
    context.user_data.pop("question_preview_msg_id", None)

    if context.user_data.get("preview_return") == "DB_SEARCH":
        context.user_data.pop("preview_return", None)
        await show_db_search_results(query.message, context)
        return

    folder_name = context.user_data.get("db_folder_name")
    if folder_name:
        await show_db_questions_from_message(query.message, context)
    else:
        await show_database_menu(query.message, context)

async def db_delete_question_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    chat_id = query.message.chat_id

    confirm_msg_id = context.user_data.pop("db_delete_q_confirm_msg_id", None)
    if confirm_msg_id:
        try:
            await context.bot.delete_message(chat_id, confirm_msg_id)
        except:
            pass
    # Leave question preview visible — no redraw needed

async def move_folder_prev(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    context.user_data["move_quiz_folder_page"] = max(
        0,
        context.user_data.get("move_quiz_folder_page", 0) - 1
    )

    await show_move_quiz_folders(query.message, context)


async def move_folder_next(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    context.user_data["move_quiz_folder_page"] = context.user_data.get(
        "move_quiz_folder_page", 0
    ) + 1

    await show_move_quiz_folders(query.message, context)

async def cancel_create_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    chat_id = query.message.chat_id

    # 🧹 Delete the prompt message (with inline button)
    prompt_id = context.user_data.pop("create_quiz_prompt_id", None)
    if prompt_id:
        try:
            await context.bot.delete_message(chat_id, prompt_id)
        except:
            pass

    # 🧹 Clear state safely
    context.user_data.pop("state", None)
    context.user_data.pop("quiz_id", None)

    # ✅ Send temporary cancel confirmation
    msg = await context.bot.send_message(
        chat_id,
        "❌ Quiz creation cancelled."
    )

    await asyncio.sleep(1.5)

    try:
        await msg.delete()
    except:
        pass

async def cancel_add_folder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    chat_id = query.message.chat_id

    # 🧹 Delete the prompt message (with Cancel button)
    prompt_id = context.user_data.pop("add_folder_prompt_id", None)
    if prompt_id:
        try:
            await context.bot.delete_message(chat_id, prompt_id)
        except:
            pass

    # 🧹 Clear state
    context.user_data.pop("state", None)

    # 🚫 Do NOT redraw anything
    # 🚫 Do NOT send Home

async def duplicate_create_anyway(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    chat_id = query.message.chat_id

    new_text = context.user_data.get("pending_duplicate_text")
    if not new_text:
        return

    # Save question text
    context.user_data["new_question"]["text"] = new_text
    context.user_data.pop("pending_duplicate_text", None)

    # Clean warning message
    try:
        await query.message.delete()
    except:
        pass

    context.user_data["add_q_state"] = "NEW_Q_IMAGE"

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("⏭ Skip image", callback_data="SKIP_Q_IMAGE")]
    ])

    msg = await context.bot.send_message(
        chat_id,
        "🖼 Send image for this question:",
        reply_markup=keyboard
    )

    context.user_data.setdefault("question_flow_msgs", []).append(msg.message_id)

async def duplicate_edit_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    chat_id = query.message.chat_id
    delete_tasks = []

    # 1️⃣ Delete duplicate warning message
    delete_tasks.append(
        context.bot.delete_message(chat_id, query.message.message_id)
    )

    # 2️⃣ Delete user's previous question text
    user_msg_id = context.user_data.get("last_user_question_msg_id")
    if user_msg_id:
        delete_tasks.append(
            context.bot.delete_message(chat_id, user_msg_id)
        )

    if delete_tasks:
        await asyncio.gather(*delete_tasks, return_exceptions=True)

    # Reset state to allow retyping
    context.user_data["add_q_state"] = "NEW_Q_TEXT"

async def duplicate_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    chat_id = query.message.chat_id
    delete_tasks = []

    # 1️⃣ Delete duplicate warning message
    delete_tasks.append(
        context.bot.delete_message(chat_id, query.message.message_id)
    )

    # 2️⃣ Delete user's question message
    user_msg_id = context.user_data.get("last_user_question_msg_id")
    if user_msg_id:
        delete_tasks.append(
            context.bot.delete_message(chat_id, user_msg_id)
        )

    # 3️⃣ Delete initial "Create Question" prompt
    prompt_id = context.user_data.get("create_q_prompt_msg_id")
    if prompt_id:
        delete_tasks.append(
            context.bot.delete_message(chat_id, prompt_id)
        )

    # 4️⃣ Delete any flow messages
    for mid in context.user_data.get("question_flow_msgs", []):
        delete_tasks.append(
            context.bot.delete_message(chat_id, mid)
        )

    if delete_tasks:
        await asyncio.gather(*delete_tasks, return_exceptions=True)

    # 🧹 Clear all related state
    context.user_data.pop("pending_duplicate_text", None)
    context.user_data.pop("new_question", None)
    context.user_data.pop("add_q_state", None)
    context.user_data.pop("question_flow_msgs", None)
    context.user_data.pop("last_user_question_msg_id", None)
    context.user_data.pop("create_q_prompt_msg_id", None)

# =========================
# DB MOVE QUESTIONS INTO FOLDER
# =========================

async def db_move_in_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Step 1: Opens the DB folder picker so the user chooses
    WHICH folder to pull questions FROM.
    """
    query = update.callback_query
    await query.answer()

    target_folder = query.data.split("|", 1)[1]
    context.user_data["db_move_target_folder"] = target_folder
    context.user_data["db_move_selected"] = set()
    context.user_data["db_move_folder_page"] = 0
    context.user_data.pop("db_move_source_folder", None)
    context.user_data.pop("db_move_page", None)

    await show_db_move_folder_list(query.message, context)

async def db_move_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    qid = int(query.data.split("|", 1)[1])
    selected = context.user_data.setdefault("db_move_selected", set())

    if qid in selected:
        selected.remove(qid)
    else:
        selected.add(qid)

    await show_db_move_question_list(query.message, context)


async def db_move_prev(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["db_move_page"] = max(0, context.user_data.get("db_move_page", 0) - 1)
    await show_db_move_question_list(query.message, context)


async def db_move_next(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["db_move_page"] = context.user_data.get("db_move_page", 0) + 1
    await show_db_move_question_list(query.message, context)

async def show_db_move_folder_list(message, context):
    target_folder = context.user_data.get("db_move_target_folder")
    page          = context.user_data.get("db_move_folder_page", 0)
    PER_PAGE      = 5

    _conn, _cur = get_db()
    _cur.execute(
        """
        SELECT id, name FROM question_bank_folders
        WHERE owner_id=?
        ORDER BY name COLLATE NOCASE
        """,
        (get_active_user_id(context),)
    )
    all_folders = _cur.fetchall()
    _conn.close()

    default_entry = [(fid, name) for fid, name in all_folders if name == "Default"]
    other_folders = [(fid, name) for fid, name in all_folders if name != "Default"]
    folders       = default_entry + other_folders

    total = len(folders)
    pages = (total - 1) // PER_PAGE + 1 if total else 1
    page  = max(0, min(page, pages - 1))
    context.user_data["db_move_folder_page"] = page

    start = page * PER_PAGE
    end   = start + PER_PAGE
    page_items = folders[start:end]

    keyboard = []

    for folder_id, folder_name in page_items:
        _conn2, _cur2 = get_db()
        _cur2.execute(
            "SELECT COUNT(*) FROM question_bank WHERE folder_id=?",
            (folder_id,)
        )
        count = _cur2.fetchone()[0]
        _conn2.close()

        keyboard.append([
            InlineKeyboardButton(
                f"📁 {folder_name} ({count})",
                callback_data=f"DB_MOVE_FROM|{folder_name}"
            )
        ])

    if pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("◀ Prev", callback_data="DB_MOVE_FOLDER_PREV"))
        nav.append(InlineKeyboardButton(f"{page+1}/{pages}", callback_data="DB_MOVE_FOLDER_NOP"))
        if page < pages - 1:
            nav.append(InlineKeyboardButton("Next ▶", callback_data="DB_MOVE_FOLDER_NEXT"))
        keyboard.append(nav)

    keyboard.append([
        InlineKeyboardButton("⬅️ Cancel", callback_data=f"DB_OPEN|{target_folder}")
    ])

    await message.edit_text(
        f"📥 Move Questions Into 📁 **{target_folder}**\n\nSelect source folder:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

async def db_move_folder_prev(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["db_move_folder_page"] = max(
        0, context.user_data.get("db_move_folder_page", 0) - 1
    )
    await show_db_move_folder_list(query.message, context)


async def db_move_folder_next(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["db_move_folder_page"] = (
        context.user_data.get("db_move_folder_page", 0) + 1
    )
    await show_db_move_folder_list(query.message, context)

async def db_move_from_folder_open(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    source_folder_name = query.data.split("|", 1)[1]
    context.user_data["db_move_source_folder"] = source_folder_name
    context.user_data["db_move_page"] = 0
    context.user_data["db_move_selected"] = set()

    await show_db_move_question_list(query.message, context)

async def db_move_add_this_page(update: Update, context: ContextTypes.DEFAULT_TYPE):
    active_uid = get_active_user_id(context)
    query = update.callback_query
    await query.answer()

    source_folder = context.user_data.get("db_move_source_folder")
    target_folder = context.user_data.get("db_move_target_folder")
    page          = context.user_data.get("db_move_page", 0)
    PER_PAGE      = 10
    selected      = context.user_data.setdefault("db_move_selected", set())

    _conn, _cur = get_db()
    _cur.execute(
        "SELECT id FROM question_bank_folders WHERE owner_id=? AND name=?",
        (active_uid, source_folder)
    )
    row = _cur.fetchone()
    _conn.close()

    if not row:
        return
    source_folder_id = row[0]

    _conn2, _cur2 = get_db()
    _cur2.execute(
        "SELECT id FROM question_bank_folders WHERE owner_id=? AND name=?",
        (active_uid, target_folder)
    )
    row2 = _cur2.fetchone()
    _conn2.close()

    if not row2:
        return
    target_folder_id = row2[0]

    _conn3, _cur3 = get_db()
    _cur3.execute(
        """
        SELECT id, question FROM question_bank
        WHERE folder_id=?
        ORDER BY question COLLATE NOCASE
        """,
        (source_folder_id,)
    )
    all_questions = _cur3.fetchall()
    _conn3.close()

    _conn4, _cur4 = get_db()
    _cur4.execute(
        "SELECT id FROM question_bank WHERE folder_id=?",
        (target_folder_id,)
    )
    already_in_target = {row[0] for row in _cur4.fetchall()}
    _conn4.close()

    available = [(qid, text) for qid, text in all_questions if qid not in already_in_target]

    total  = len(available)
    pages  = (total - 1) // PER_PAGE + 1 if total else 1
    page   = max(0, min(page, pages - 1))
    start  = page * PER_PAGE
    end    = start + PER_PAGE

    for qid, text in available[start:end]:
        selected.add(qid)

    await show_db_move_question_list(query.message, context)

async def db_move_auto_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    active_uid = get_active_user_id(context)
    query = update.callback_query
    await query.answer()

    n             = int(query.data.split("|", 1)[1])
    source_folder = context.user_data.get("db_move_source_folder")
    target_folder = context.user_data.get("db_move_target_folder")
    selected      = context.user_data.setdefault("db_move_selected", set())

    _conn, _cur = get_db()
    _cur.execute(
        "SELECT id FROM question_bank_folders WHERE owner_id=? AND name=?",
        (active_uid, source_folder)
    )
    row = _cur.fetchone()
    _conn.close()

    if not row:
        return
    source_folder_id = row[0]

    _conn2, _cur2 = get_db()
    _cur2.execute(
        "SELECT id FROM question_bank_folders WHERE owner_id=? AND name=?",
        (active_uid, target_folder)
    )
    row2 = _cur2.fetchone()
    _conn2.close()

    if not row2:
        return
    target_folder_id = row2[0]

    _conn3, _cur3 = get_db()
    _cur3.execute(
        "SELECT id FROM question_bank WHERE folder_id=?",
        (source_folder_id,)
    )
    all_questions = {row[0] for row in _cur3.fetchall()}
    _conn3.close()

    _conn4, _cur4 = get_db()
    _cur4.execute(
        "SELECT id FROM question_bank WHERE folder_id=?",
        (target_folder_id,)
    )
    already_in_target = {row[0] for row in _cur4.fetchall()}
    _conn4.close()

    candidates = list(all_questions - already_in_target - selected)
    random.shuffle(candidates)
    selected.update(candidates[:n])

    await show_db_move_question_list(query.message, context)

async def show_db_move_question_list(message, context):
    active_uid    = get_active_user_id(context)
    target_folder = context.user_data.get("db_move_target_folder")
    source_folder = context.user_data.get("db_move_source_folder")
    selected      = context.user_data.setdefault("db_move_selected", set())
    page          = context.user_data.get("db_move_page", 0)
    PER_PAGE      = 10

    _conn, _cur = get_db()
    _cur.execute(
        "SELECT id FROM question_bank_folders WHERE owner_id=? AND name=?",
        (active_uid, source_folder)
    )
    row = _cur.fetchone()
    _conn.close()

    if not row:
        await message.edit_text("❌ Source folder not found.")
        return
    source_folder_id = row[0]

    _conn2, _cur2 = get_db()
    _cur2.execute(
        "SELECT id FROM question_bank_folders WHERE owner_id=? AND name=?",
        (active_uid, target_folder)
    )
    row2 = _cur2.fetchone()
    _conn2.close()

    if not row2:
        await message.edit_text("❌ Target folder not found.")
        return
    target_folder_id = row2[0]

    _conn3, _cur3 = get_db()
    _cur3.execute(
        """
        SELECT id, question FROM question_bank
        WHERE folder_id=?
        ORDER BY question COLLATE NOCASE
        """,
        (source_folder_id,)
    )
    all_questions = _cur3.fetchall()
    _conn3.close()

    _conn4, _cur4 = get_db()
    _cur4.execute(
        "SELECT id FROM question_bank WHERE folder_id=?",
        (target_folder_id,)
    )
    already_in_target = {row[0] for row in _cur4.fetchall()}
    _conn4.close()

    available = [(qid, text) for qid, text in all_questions if qid not in already_in_target]

    if not available:
        await message.edit_text(
            f"📁 **{source_folder}**\n\n✅ All questions here are already in **{target_folder}**.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Back", callback_data=f"DB_MOVE_IN|{target_folder}")]
            ]),
            parse_mode="Markdown"
        )
        return

    total = len(available)
    pages = (total - 1) // PER_PAGE + 1
    page  = max(0, min(page, pages - 1))
    context.user_data["db_move_page"] = page

    start = page * PER_PAGE
    end   = start + PER_PAGE
    page_items = available[start:end]

    keyboard = []

    keyboard.append([
        InlineKeyboardButton("📄 Add this Page", callback_data="DB_MOVE_ADD_PAGE"),
        InlineKeyboardButton("🎲 Add 10",        callback_data="DB_MOVE_AUTO_ADD|10"),
    ])
    keyboard.append([
        InlineKeyboardButton("🎲 Add 50",  callback_data="DB_MOVE_AUTO_ADD|50"),
        InlineKeyboardButton("🎲 Add 100", callback_data="DB_MOVE_AUTO_ADD|100"),
    ])

    for qid, text in page_items:
        checked = "☑" if qid in selected else "⬜"
        keyboard.append([
            InlineKeyboardButton(
                f"{checked} {text[:45]}",
                callback_data=f"DB_MOVE_TOGGLE|{qid}"
            )
        ])

    if pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("◀ Prev", callback_data="DB_MOVE_PREV"))
        nav.append(InlineKeyboardButton(f"{page+1}/{pages}", callback_data="DB_MOVE_NOP"))
        if page < pages - 1:
            nav.append(InlineKeyboardButton("Next ▶", callback_data="DB_MOVE_NEXT"))
        keyboard.append(nav)

    keyboard.append([
        InlineKeyboardButton(
            f"📦 Move Selected ({len(selected)})",
            callback_data="DB_MOVE_CONFIRM"
        ),
        InlineKeyboardButton("⬅️ Cancel", callback_data=f"DB_MOVE_IN|{target_folder}")
    ])

    await message.edit_text(
        f"📁 **{source_folder}** → 📁 **{target_folder}**\n\nSelect questions to move:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

async def db_move_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    active_uid    = get_active_user_id(context)
    target_folder = context.user_data.get("db_move_target_folder")
    selected      = context.user_data.get("db_move_selected", set())

    if not selected:
        await query.answer("⚠️ No questions selected.", show_alert=True)
        return

    _conn, _cur = get_db()
    _cur.execute(
        "SELECT id FROM question_bank_folders WHERE owner_id=? AND name=?",
        (active_uid, target_folder)
    )
    row = _cur.fetchone()
    _conn.close()

    if not row:
        await query.answer("❌ Target folder not found.", show_alert=True)
        return
    target_folder_id = row[0]

    try:
        async with DB_LOCK:
            _conn2, _cur2 = get_db()
            for qid in selected:
                _cur2.execute(
                    "UPDATE question_bank SET folder_id=? WHERE id=?",
                    (target_folder_id, qid)
                )
            _conn2.commit()
            _conn2.close()
    except Exception as e:
        print("⚠️ Failed to move questions:", e)
        await query.answer("❌ Move failed.", show_alert=True)
        return

    moved = len(selected)

    context.user_data.pop("db_move_selected", None)
    context.user_data.pop("db_move_page", None)
    context.user_data.pop("db_move_target_folder", None)
    context.user_data.pop("db_move_source_folder", None)
    context.user_data.pop("db_move_folder_page", None)

    await flash_message(
        context.bot,
        query.message.chat_id,
        f"✅ {moved} question(s) moved to 📁 {target_folder}"
    )

    context.user_data["db_folder_name"] = target_folder
    context.user_data["db_q_page"] = 0

    await show_db_questions_from_message(query.message, context)

async def db_delete_folder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    folder_name = query.data.split("|", 1)[1]

    if folder_name == "Default":
        await query.answer("❌ Cannot delete the Default folder.", show_alert=True)
        return

    _conn, _cur = get_db()
    _cur.execute(
        "SELECT id FROM question_bank_folders WHERE owner_id=? AND name=?",
        (get_active_user_id(context), folder_name)
    )
    row = _cur.fetchone()
    _conn.close()

    if not row:
        await query.answer("❌ Folder not found.", show_alert=True)
        return
    folder_id = row[0]

    _conn2, _cur2 = get_db()
    _cur2.execute(
        "SELECT id FROM question_bank_folders WHERE owner_id=? AND name='Default'",
        (get_active_user_id(context),)
    )
    default_row = _cur2.fetchone()
    _conn2.close()

    if not default_row:
        await query.answer("❌ Default folder not found.", show_alert=True)
        return
    default_folder_id = default_row[0]

    context.user_data["db_delete_folder_id"] = folder_id
    context.user_data["db_delete_folder_name"] = folder_name
    context.user_data["db_delete_default_folder_id"] = default_folder_id

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Yes, Delete", callback_data="DB_DELETE_FOLDER_CONFIRM"),
            InlineKeyboardButton("❌ Cancel",      callback_data=f"DB_OPEN|{folder_name}"),
        ]
    ])

    await query.message.edit_text(
        f"🗑 Delete folder **{folder_name}**?\n\n"
        f"All questions inside will be moved to **Default Folder**.",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )


async def db_delete_folder_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    folder_id = context.user_data.pop("db_delete_folder_id", None)
    folder_name = context.user_data.pop("db_delete_folder_name", None)
    default_folder_id = context.user_data.pop("db_delete_default_folder_id", None)

    if not folder_id or not default_folder_id:
        await query.answer("❌ Delete state lost. Please try again.", show_alert=True)
        return

    try:
        async with DB_LOCK:
            _conn, _cur = get_db()
            _cur.execute(
                "UPDATE question_bank SET folder_id=? WHERE folder_id=?",
                (default_folder_id, folder_id)
            )
            _cur.execute(
                "DELETE FROM question_bank_folders WHERE id=?",
                (folder_id,)
            )
            _conn.commit()
            _conn.close()

    except Exception as e:
        print("⚠️ Failed to delete DB folder:", e)
        await query.answer("❌ Delete failed.", show_alert=True)
        return

    await flash_message(
        context.bot,
        query.message.chat_id,
        f"✅ Folder '{folder_name}' deleted. Questions moved to Default Folder."
    )

    # Refresh Database menu
    await show_database_menu(query.message, context)

async def db_q_prev(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["db_q_page"] = max(0, context.user_data.get("db_q_page", 0) - 1)
    await show_db_questions(update, context)


async def db_q_next(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["db_q_page"] = context.user_data.get("db_q_page", 0) + 1
    await show_db_questions(update, context)

async def db_rename_folder_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    folder_name = query.data.split("|", 1)[1]

    context.user_data["state"] = "DB_RENAME_FOLDER"
    context.user_data["db_rename_folder_name"] = folder_name
    context.user_data["db_rename_menu_message"] = query.message

    # 🔑 Cancel goes to a clean handler — NOT DB_OPEN (which sends a new preview)
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Cancel", callback_data="CANCEL_DB_RENAME_FOLDER")]
    ])

    msg = await query.message.reply_text(
        f"✏️ Send new name for folder:\n\n📁 {folder_name}",
        reply_markup=keyboard
    )

    context.user_data["db_rename_prompt_id"] = msg.message_id

async def cancel_db_rename_folder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    chat_id = query.message.chat_id

    # 🧹 Delete ONLY the rename prompt message
    prompt_id = context.user_data.pop("db_rename_prompt_id", None)
    if prompt_id:
        try:
            await context.bot.delete_message(chat_id, prompt_id)
        except:
            pass

    # 🔒 Clear rename state
    context.user_data.pop("state", None)
    context.user_data.pop("db_rename_folder_name", None)
    context.user_data.pop("db_rename_menu_message", None)

    # 🔕 Do NOT send any new message — existing folder preview remains visible

async def db_search_prev(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["db_search_page"] = max(0, context.user_data.get("db_search_page", 0) - 1)
    await show_db_search_results(query.message, context)

async def db_search_next(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["db_search_page"] = context.user_data.get("db_search_page", 0) + 1
    await show_db_search_results(query.message, context)


# =========================
# QUIZ ADMIN PANEL (GROUP POST MANAGEMENT)
# =========================
def _build_qa_panel_text(quiz_id: str) -> str:
    """Builds the full quiz settings text for the Quiz Admin panel."""
    _conn, _cur = get_db()
    _cur.execute(
        "SELECT title, description, timer, shuffle_q, shuffle_a FROM quizzes WHERE quiz_id=?",
        (quiz_id,)
    )
    row = _cur.fetchone()
    _conn.close()

    if not row:
        return "⚙️ *Quiz Admin Panel*"

    title, desc, timer, sq, sa = row

    _conn2, _cur2 = get_db()
    _cur2.execute("SELECT COUNT(*) FROM quiz_question_links WHERE quiz_id=?", (quiz_id,))
    total_questions = _cur2.fetchone()[0]
    _conn2.close()

    text = "⚙️ *Quiz Admin Panel*\n\n"
    text += f"📘 *{escape_md(title)}*"
    if desc:
        text += f"\n📝 _{escape_md(desc)}_"
    text += f"\n\n📊 Questions: {total_questions}    ⏱ Timer: {timer}s"
    text += f"\n🔀 Questions: {'ON' if sq else 'OFF'}    🔀 Options: {'ON' if sa else 'OFF'}"
    return text

def _build_qa_panel_keyboard(leaderboard_key: str, show_score: int) -> InlineKeyboardMarkup:
    """Builds the Quiz Admin panel inline keyboard."""
    score_label = "👁 Show Score: ON" if show_score else "👁 Show Score: OFF"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 Leaderboard",     callback_data=f"QA_LB|{leaderboard_key}|0")],
        [InlineKeyboardButton(score_label,          callback_data=f"QA_TOGGLE|{leaderboard_key}")],
        [
            InlineKeyboardButton("🔄 Reset Score",  callback_data=f"QA_RESET|{leaderboard_key}"),
            InlineKeyboardButton("📄 Export Quiz",  callback_data=f"QA_EXPORT|{leaderboard_key}"),
        ],
        [InlineKeyboardButton("✖️ Close",           callback_data="QA_CLOSE")],
    ])

async def quiz_admin_open(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the ⚙️ Quiz Admin button tapped on a group quiz post."""
    query = update.callback_query
    user_id = query.from_user.id

    try:
        leaderboard_key = query.data.split("|", 1)[1]
    except (IndexError, ValueError):
        await query.answer()
        return

    quiz_id = leaderboard_key.split(":", 1)[0]

    # 🔒 Only the quiz CREATOR (owner) can access Quiz Admin
    _conn_o, _cur_o = get_db()
    _cur_o.execute("SELECT owner_id FROM quizzes WHERE quiz_id=?", (quiz_id,))
    owner_row = _cur_o.fetchone()
    _conn_o.close()

    if not owner_row or owner_row[0] != user_id:
        await query.answer("❌ Only the quiz creator can access Quiz Admin.", show_alert=True)
        return

    await query.answer()

    info = GROUP_LB_MESSAGES.get(leaderboard_key, {})
    show_score = info.get("show_score", 1)

    text = _build_qa_panel_text(quiz_id)
    keyboard = _build_qa_panel_keyboard(leaderboard_key, show_score)

    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=text,
            reply_markup=keyboard,
            parse_mode="Markdown"
        )
    except Exception as e:
        print("⚠️ Could not send Quiz Admin panel:", e)
        await query.answer("❌ Please start the bot in private first before using Quiz Admin.", show_alert=True)

async def qa_leaderboard_show(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows a paginated full leaderboard in the admin's private chat."""
    query = update.callback_query
    await query.answer()

    try:
        parts = query.data.split("|")
        leaderboard_key = parts[1]
        page = int(parts[2])
    except (ValueError, IndexError):
        return

    # Build leaderboard data from memory or DB
    if leaderboard_key in GROUP_LEADERBOARDS and GROUP_LEADERBOARDS[leaderboard_key]:
        leaderboard = [
            {"name": data["name"], "score": data["score"]}
            for data in GROUP_LEADERBOARDS[leaderboard_key].values()
        ]
    else:
        _conn, _cur = get_db()
        _cur.execute(
            "SELECT name, score FROM group_leaderboard WHERE leaderboard_key=? ORDER BY score DESC",
            (leaderboard_key,)
        )
        rows = _cur.fetchall()
        _conn.close()
        leaderboard = [{"name": name, "score": score} for name, score in rows]

    leaderboard.sort(key=lambda x: x["score"], reverse=True)

    per_page = 10
    total = len(leaderboard)
    pages = max(1, (total - 1) // per_page + 1) if total > 0 else 1
    page = max(0, min(page, pages - 1))
    start = page * per_page
    end = start + per_page

    if not leaderboard:
        text = "📋 *Leaderboard*\n\n_No participants yet._"
    else:
        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        text = f"📋 *Full Leaderboard* ({total} participants)\n\n"
        for i, user in enumerate(leaderboard[start:end], start=start + 1):
            prefix = medals.get(i, f"{i}.")
            text += f"{prefix} {escape_md(user['name'])} — {user['score']}\n"

    # Pagination navigation
    nav = []
    if pages > 1:
        if page > 0:
            nav.append(InlineKeyboardButton("◀ Prev", callback_data=f"QA_LB|{leaderboard_key}|{page - 1}"))
        nav.append(InlineKeyboardButton(f"{page + 1}/{pages}", callback_data="QA_LB_NOP"))
        if page < pages - 1:
            nav.append(InlineKeyboardButton("Next ▶", callback_data=f"QA_LB|{leaderboard_key}|{page + 1}"))

    buttons = []
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton("⬅️ Back to Admin Panel", callback_data=f"QA_BACK|{leaderboard_key}")])

    try:
        await query.message.edit_text(
            text,
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode="Markdown"
        )
    except Exception:
        pass


async def qa_toggle_score(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggles the Show Score ON/OFF on the group quiz post."""
    query = update.callback_query
    await query.answer()

    try:
        leaderboard_key = query.data.split("|", 1)[1]
    except (IndexError, ValueError):
        return

    # Toggle in memory
    info = GROUP_LB_MESSAGES.get(leaderboard_key, {})
    current = info.get("show_score", 1)
    new_val = 0 if current else 1
    info["show_score"] = new_val
    GROUP_LB_MESSAGES[leaderboard_key] = info

    # Toggle in DB
    try:
        async with DB_LOCK:
            _conn, _cur = get_db()
            _cur.execute(
                "UPDATE group_lb_messages SET show_score=? WHERE leaderboard_key=?",
                (new_val, leaderboard_key)
            )
            _conn.commit()
            _conn.close()
    except Exception as e:
        print("⚠️ Failed to toggle show_score:", e)

    # Refresh the group post immediately
    await update_group_leaderboard(leaderboard_key, context)

    # Refresh the admin panel with updated button label
    quiz_id = leaderboard_key.split(":", 1)[0]
    text = _build_qa_panel_text(quiz_id)
    keyboard = _build_qa_panel_keyboard(leaderboard_key, new_val)

    try:
        await query.message.edit_text(
            text,
            reply_markup=keyboard,
            parse_mode="Markdown"
        )
    except Exception:
        pass


async def qa_reset_score(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Resets all scores for a quiz post (triggered from the admin panel)."""
    query = update.callback_query

    try:
        leaderboard_key = query.data.split("|", 1)[1]
    except (IndexError, ValueError):
        await query.answer()
        return

    # 🧹 Clear memory
    GROUP_LEADERBOARDS.pop(leaderboard_key, None)

    # 🔐 Clear database
    try:
        async with DB_LOCK:
            _conn, _cur = get_db()
            _cur.execute(
                "DELETE FROM group_leaderboard WHERE leaderboard_key=?",
                (leaderboard_key,)
            )
            _conn.commit()
            _conn.close()
    except Exception as e:
        print("⚠️ QA Reset score DB error:", e)
        await query.answer("❌ Reset failed.", show_alert=True)
        return

    await query.answer("✅ All scores have been reset.", show_alert=True)

    # Refresh group post
    await update_group_leaderboard(leaderboard_key, context)

    # Refresh the admin panel
    quiz_id = leaderboard_key.split(":", 1)[0]
    text = _build_qa_panel_text(quiz_id)
    info = GROUP_LB_MESSAGES.get(leaderboard_key, {})
    show_score = info.get("show_score", 1)
    keyboard = _build_qa_panel_keyboard(leaderboard_key, show_score)

    try:
        await query.message.edit_text(
            text,
            reply_markup=keyboard,
            parse_mode="Markdown"
        )
    except Exception:
        pass


async def qa_export_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Exports quiz questions to a PDF file and sends it to the admin."""
    query = update.callback_query
    await query.answer("📄 Generating PDF, please wait...")

    try:
        leaderboard_key = query.data.split("|", 1)[1]
    except (IndexError, ValueError):
        return

    quiz_id = leaderboard_key.split(":", 1)[0]
    user_id = query.from_user.id

    # Fetch quiz info
    _conn, _cur = get_db()
    _cur.execute("SELECT title, description FROM quizzes WHERE quiz_id=?", (quiz_id,))
    row = _cur.fetchone()
    _conn.close()
    quiz_title = row[0] if row else "Quiz"
    quiz_desc  = row[1] if row and row[1] else ""

    # Fetch questions in order
    _conn2, _cur2 = get_db()
    _cur2.execute("""
        SELECT qb.question, qb.image_file_id, qb.options, qb.correct, qb.explanation
        FROM quiz_question_links ql
        JOIN question_bank qb ON qb.id = ql.question_id
        WHERE ql.quiz_id=?
        ORDER BY ql.position ASC
    """, (quiz_id,))
    questions = _cur2.fetchall()
    _conn2.close()

    if not questions:
        await context.bot.send_message(
            chat_id=user_id,
            text="❌ This quiz has no questions to export."
        )
        return

    try:
        from fpdf import FPDF
        import tempfile, os

        class QuizPDF(FPDF):
            pass

        pdf = QuizPDF()
        pdf.set_auto_page_break(auto=True, margin=15)
        pdf.add_page()

        # ── Title ───────────────────────────────────────────
        pdf.set_font("Helvetica", "B", 16)
        pdf.cell(0, 10, quiz_title[:80], ln=True, align="C")

        if quiz_desc:
            pdf.set_font("Helvetica", "I", 11)
            pdf.cell(0, 7, quiz_desc[:100], ln=True, align="C")

        pdf.ln(4)
        pdf.set_draw_color(120, 120, 120)
        pdf.line(10, pdf.get_y(), 200, pdf.get_y())
        pdf.ln(6)

        label_map = ["a", "b", "c", "d"]

        for i, (q_text, image_file_id, options_str, correct_idx, explanation) in enumerate(questions, start=1):
            opts = options_str.split("||")

            # ── Question number + text ──────────────────────
            pdf.set_font("Helvetica", "B", 11)
            header = f"{i}.) {q_text}"
            if image_file_id:
                header += "  [ Image ]"
            pdf.multi_cell(0, 7, header[:500])
            pdf.ln(1)

            # ── Options: a & c on same row, b & d on same row ──
            pdf.set_font("Helvetica", "", 10)
            col_w = 90
            left_opts  = []   # indices 0, 2  (a, c)
            right_opts = []   # indices 1, 3  (b, d)
            for j, opt in enumerate(opts):
                lbl = label_map[j] if j < len(label_map) else str(j + 1)
                entry = f"{lbl}.) {opt}"
                if j % 2 == 0:
                    left_opts.append(entry)
                else:
                    right_opts.append(entry)

            for k in range(max(len(left_opts), len(right_opts))):
                left  = left_opts[k]  if k < len(left_opts)  else ""
                right = right_opts[k] if k < len(right_opts) else ""
                y = pdf.get_y()
                pdf.set_xy(10, y)
                pdf.cell(col_w, 6, left[:60],  ln=False)
                pdf.set_xy(110, y)
                pdf.cell(col_w, 6, right[:60], ln=True)

            # ── Correct answer hint ─────────────────────────
            if 0 <= correct_idx < len(opts):
                clbl = label_map[correct_idx] if correct_idx < len(label_map) else str(correct_idx + 1)
                pdf.set_font("Helvetica", "I", 9)
                pdf.set_text_color(0, 130, 0)
                pdf.cell(0, 6, f"   Answer: {clbl}.) {opts[correct_idx][:80]}", ln=True)
                pdf.set_text_color(0, 0, 0)

            # ── Explanation ─────────────────────────────────
            if explanation:
                pdf.set_font("Helvetica", "I", 9)
                pdf.set_text_color(90, 90, 90)
                pdf.multi_cell(0, 5, f"   Explanation: {explanation[:300]}")
                pdf.set_text_color(0, 0, 0)

            pdf.ln(4)

        # ── Save and send ───────────────────────────────────
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            pdf_path = tmp.name

        pdf.output(pdf_path)

        safe_title = "".join(c for c in quiz_title if c.isalnum() or c in " _-")[:30].strip().replace(" ", "_")
        filename = f"{safe_title or 'quiz'}.pdf"

        with open(pdf_path, "rb") as f:
            await context.bot.send_document(
                chat_id=user_id,
                document=f,
                filename=filename,
                caption=f"📄 *{escape_md(quiz_title)}*\n_{len(questions)} Questions_",
                parse_mode="Markdown"
            )

        os.unlink(pdf_path)

    except ImportError:
        await context.bot.send_message(
            chat_id=user_id,
            text=(
                "❌ *PDF export requires the fpdf2 library.*\n\n"
                "Run this in your terminal:\n`pip install fpdf2`\n\n"
                "Then restart the bot and try again."
            ),
            parse_mode="Markdown"
        )
    except Exception as e:
        print("⚠️ PDF export error:", e)
        await context.bot.send_message(
            chat_id=user_id,
            text="❌ Failed to generate PDF. Please try again."
        )

async def qa_back_to_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Returns from the leaderboard view back to the Quiz Admin panel."""
    query = update.callback_query
    await query.answer()

    try:
        leaderboard_key = query.data.split("|", 1)[1]
    except (IndexError, ValueError):
        return

    quiz_id = leaderboard_key.split(":", 1)[0]
    text = _build_qa_panel_text(quiz_id)
    info = GROUP_LB_MESSAGES.get(leaderboard_key, {})
    show_score = info.get("show_score", 1)
    keyboard = _build_qa_panel_keyboard(leaderboard_key, show_score)

    try:
        await query.message.edit_text(
            text,
            reply_markup=keyboard,
            parse_mode="Markdown"
        )
    except Exception:
        pass

async def qa_close(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Closes (deletes) the Quiz Admin panel message."""
    query = update.callback_query
    await query.answer()
    try:
        await query.message.delete()
    except Exception:
        pass

async def reset_score(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    user_id = query.from_user.id

    # 🔒 Silent block for non-admins
    if user_id != OWNER_USER_ID:
        await query.answer("❌ Only the quiz admin can reset scores.", show_alert=True)
        return

    await query.answer()

    try:
        leaderboard_key = query.data.split("|", 1)[1]
    except (IndexError, ValueError):
        return

    # 🧹 Clear memory
    GROUP_LEADERBOARDS.pop(leaderboard_key, None)

    # 🔐 Clear database
    try:
        async with DB_LOCK:
            _conn, _cur = get_db()
            _cur.execute(
                "DELETE FROM group_leaderboard WHERE leaderboard_key=?",
                (leaderboard_key,)
            )
            _conn.commit()
            _conn.close()
    except Exception as e:
        print("⚠️ Reset score DB error:", e)
        return

    # 🔄 Refresh group post immediately
    await update_group_leaderboard(leaderboard_key, context)

async def refresh_all_group_posts(context):
    """Refreshes ALL active group posts across all quizzes."""
    _conn_r, _cur_r = get_db()
    _cur_r.execute("SELECT DISTINCT quiz_id FROM group_lb_messages")
    quiz_ids = [row[0] for row in _cur_r.fetchall()]
    _conn_r.close()
    for quiz_id in quiz_ids:
        await refresh_all_group_posts_for_quiz(quiz_id, context)

async def refresh_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    chat = update.effective_chat
    if chat.type not in ("group", "supergroup"):
        return
    if update.effective_user.id != OWNER_USER_ID:
        return
    try:
        await update.message.delete()
    except:
        pass
    await refresh_all_group_posts(context)

async def home_manage_subscribers(update, context):
    query = update.callback_query
    await query.answer()

    if query.from_user.id != OWNER_USER_ID:
        await flash_message(context.bot, query.message.chat_id,
            "❌ Only the Bot Creator can manage subscribers."
        )
        return

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add Subscriber",       callback_data="SUB_ADD")],
        [InlineKeyboardButton("✅ Active Subscriptions",  callback_data="SUB_LIST|active")],
        [InlineKeyboardButton("❌ Inactive Subscriptions",callback_data="SUB_LIST|inactive")],
        [InlineKeyboardButton("🏠 Home",                 callback_data="GO_HOME")],
    ])

    await query.message.edit_text(
        "👥 *Manage Subscribers*",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )

async def home_manage_subscribers_from_message(message, context):
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add Subscriber",        callback_data="SUB_ADD")],
        [InlineKeyboardButton("✅ Active Subscriptions",  callback_data="SUB_LIST|active")],
        [InlineKeyboardButton("❌ Inactive Subscriptions",callback_data="SUB_LIST|inactive")],
        [InlineKeyboardButton("🏠 Home",                  callback_data="GO_HOME")],
    ])

    try:
        await message.edit_text(
            "👥 *Manage Subscribers*",
            reply_markup=keyboard,
            parse_mode="Markdown"
        )
    except Exception:
        await context.bot.send_message(
            chat_id=message.chat_id,
            text="👥 *Manage Subscribers*",
            reply_markup=keyboard,
            parse_mode="Markdown"
        )

async def sub_add_start(update, context):
    query = update.callback_query
    await query.answer()

    context.user_data["state"] = "SUB_WAIT_USER_ID"

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Cancel", callback_data="SUB_CANCEL")]
    ])

    msg = await query.message.reply_text(
        "➕ *Add Subscriber*\n\n📋 Send the Telegram *User ID*:",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )
    context.user_data["sub_prompt_id"] = msg.message_id

async def sub_apply_duration(update, context):
    query = update.callback_query
    await query.answer()

    sub_type  = query.data.split("|", 1)[1]
    user_id   = context.user_data.get("sub_new_user_id")
    name      = context.user_data.get("sub_new_name")
    is_renew  = "sub_renew_id" in context.user_data

    if not user_id or not name:
        await flash_message(context.bot, query.message.chat_id, "❌ Subscriber data lost.")
        return

    now      = int(time.time())
    duration = SUBSCRIPTION_DURATIONS.get(sub_type, 0)

    # ── PROBLEM 2 FIX: When renewing, ADD duration to remaining time ──
    if is_renew and sub_type != "Lifetime":
        _conn_r, _cur_r = get_db()
        _cur_r.execute("SELECT expires_at, subscription_type FROM subscribers WHERE user_id=?", (user_id,))
        row = _cur_r.fetchone()
        _conn_r.close()
        if row:
            current_expires, current_type = row
            if current_type == "Lifetime":
                # Already lifetime — no change needed
                expires_at = 0
            else:
                # Add new duration on top of remaining time
                # If already expired, start counting from now
                base = max(current_expires, now)
                expires_at = base + duration
        else:
            expires_at = now + duration
    elif sub_type == "Lifetime":
        expires_at = 0
    else:
        expires_at = now + duration

    # ── PROBLEM 3 FIX: subscribed_at = now (latest action date) ──
    try:
        async with DB_LOCK:
            if is_renew:
                _conn, _cur = get_db()
                _cur.execute("""
                    UPDATE subscribers
                    SET subscription_type = ?,
                        expires_at        = ?,
                        is_active         = 1,
                        subscribed_at     = ?,
                        needs_notice      = 1
                    WHERE user_id = ?
                """, (sub_type, expires_at, now, user_id))
            else:
                _conn, _cur = get_db()
                _cur.execute("""
                    INSERT OR REPLACE INTO subscribers
                    (user_id, name, subscription_type, expires_at, is_active, subscribed_at, needs_notice)
                    VALUES (?, ?, ?, ?, 1, ?, 1)
                """, (user_id, name, sub_type, expires_at, now))
            _conn.commit()
            _conn.close()

        # ✅ Auto-create Default folders for new subscriber
        async with DB_LOCK:
            _conn2, _cur2 = get_db()
            _cur2.execute(
                "INSERT OR IGNORE INTO folders (owner_id, name) VALUES (?, 'Default')",
                (user_id,)
            )
            _cur2.execute(
                "INSERT OR IGNORE INTO question_bank_folders (owner_id, name) VALUES (?, 'Default')",
                (user_id,)
            )
            _conn2.commit()
            _conn2.close()

    except Exception as e:
        print("⚠️ Failed to save subscriber:", e)
        await flash_message(context.bot, query.message.chat_id, "❌ Operation failed.")
        return

    # ── Cleanup state ──────────────────────────────────────
    context.user_data.pop("sub_new_user_id", None)
    context.user_data.pop("sub_new_name", None)
    context.user_data.pop("sub_renew_id", None)
    context.user_data.pop("state", None)

    action_word = "renewed" if is_renew else "added"
    await flash_message(
        context.bot, query.message.chat_id,
        f"✅ *{name}* {action_word} with *{sub_type}* access.",
        delay=2
    )

    await home_manage_subscribers_from_message(query.message, context)

async def sub_list(update, context):
    """Router: dispatches to active or inactive list."""
    query = update.callback_query
    await query.answer()

    mode = query.data.split("|", 1)[1]
    context.user_data["sub_list_page"] = 0

    if mode == "active":
        await _show_sub_list(query.message, context, active=True, page=0)
    else:
        await _show_sub_list(query.message, context, active=False, page=0)

async def sub_list_prev(update, context):
    query = update.callback_query
    await query.answer()

    list_type = query.data.split("|", 1)[1]
    page = max(0, context.user_data.get("sub_list_page", 0) - 1)

    active = (list_type == "active")
    await _show_sub_list(query.message, context, active=active, page=page)


async def sub_list_next(update, context):
    query = update.callback_query
    await query.answer()

    list_type = query.data.split("|", 1)[1]
    page = context.user_data.get("sub_list_page", 0) + 1

    active = (list_type == "active")
    await _show_sub_list(query.message, context, active=active, page=page)

async def _show_sub_list(message, context, active: bool, page: int = 0):
    now = int(time.time())

    # Auto-expire overdue subscriptions
    async with DB_LOCK:
        _conn, _cur = get_db()
        _cur.execute("""
            UPDATE subscribers
            SET is_active = 0
            WHERE subscription_type != 'Lifetime'
              AND expires_at > 0
              AND expires_at <= ?
              AND is_active = 1
        """, (now,))
        _conn.commit()
        _conn.close()

    _conn2, _cur2 = get_db()
    if active:
        _cur2.execute("""
            SELECT user_id, name, subscription_type, expires_at
            FROM subscribers
            WHERE is_active = 1
              AND (subscription_type = 'Lifetime' OR expires_at > ?)
            ORDER BY
                CASE WHEN subscription_type = 'Lifetime' THEN 9999999999 ELSE expires_at END ASC
        """, (now,))
        header     = "✅ *Active Subscriptions*"
        empty_text = "✅ *Active Subscriptions*\n\n_No active subscribers yet._"
    else:
        _cur2.execute("""
            SELECT user_id, name, subscription_type, expires_at
            FROM subscribers
            WHERE is_active = 0
               OR (subscription_type != 'Lifetime' AND expires_at <= ?)
            ORDER BY expires_at ASC
        """, (now,))
        header     = "❌ *Inactive Subscriptions*"
        empty_text = "❌ *Inactive Subscriptions*\n\n_No inactive subscribers._"

    rows = _cur2.fetchall()
    _conn2.close()

    keyboard = []

    if not rows:
        keyboard.append([
            InlineKeyboardButton("⬅️ Back", callback_data="HOME_MANAGE_SUBSCRIBERS")
        ])
        await message.edit_text(
            empty_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
        return

    PER_PAGE = 10
    total = len(rows)
    pages = (total - 1) // PER_PAGE + 1
    page  = max(0, min(page, pages - 1))

    start = page * PER_PAGE
    end   = start + PER_PAGE

    for user_id, name, s_type, expires_at in rows[start:end]:
        if s_type == "Lifetime":
            badge = "Lifetime"
        elif expires_at and expires_at > now:
            days  = (expires_at - now) // 86400
            badge = f"{days}d left"
        else:
            badge = "Expired"

        keyboard.append([
            InlineKeyboardButton(
                f"{name}  •  {badge}",
                callback_data=f"SUB_VIEW|{user_id}"
            )
        ])

    list_type = "active" if active else "inactive"

    if pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("◀ Prev", callback_data=f"SUB_LIST_PREV|{list_type}"))
        nav.append(InlineKeyboardButton(f"{page + 1}/{pages}", callback_data="SUB_LIST_NOP"))
        if page < pages - 1:
            nav.append(InlineKeyboardButton("Next ▶", callback_data=f"SUB_LIST_NEXT|{list_type}"))
        keyboard.append(nav)

    keyboard.append([
        InlineKeyboardButton("⬅️ Back", callback_data="HOME_MANAGE_SUBSCRIBERS")
    ])

    context.user_data["sub_list_type"] = list_type
    context.user_data["sub_list_page"]  = page

    await message.edit_text(
        header,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

async def sub_overview(update, context):
    query = update.callback_query
    await query.answer()

    target_id = int(query.data.split("|", 1)[1])
    context.user_data["sub_view_id"] = target_id

    now = int(time.time())

    _conn, _cur = get_db()
    _cur.execute("""
        SELECT user_id, name, subscription_type, expires_at, is_active, subscribed_at
        FROM subscribers WHERE user_id=?
    """, (target_id,))
    row = _cur.fetchone()
    _conn.close()

    if not row:
        await query.answer("❌ Subscriber not found.", show_alert=True)
        return

    user_id, name, s_type, expires_at, is_active, subscribed_at = row

    if subscribed_at and subscribed_at > 0:
        sub_date = datetime.datetime.fromtimestamp(
            subscribed_at, datetime.timezone.utc
        ).strftime("%B %d, %Y")
        sub_date_label = "Last Renewed"
    else:
        sub_date = "—"
        sub_date_label = "Subscribed"

    if not is_active:
        remaining_text = "0 days (Revoked)"
    elif s_type == "Lifetime":
        remaining_text = "Lifetime (no expiry)"
    elif expires_at and expires_at > now:
        days = (expires_at - now) // 86400
        expiry_date = datetime.datetime.fromtimestamp(
            expires_at, datetime.timezone.utc
        ).strftime("%B %d, %Y")
        remaining_text = f"{days} day(s) (expires {expiry_date})"
    else:
        remaining_text = "Expired"

    text = (
        f"👤 *{name}*\n\n"
        f"🆔 User ID: `{user_id}`\n"
        f"📅 {sub_date_label}: {sub_date}\n"
        f"📦 Duration: {s_type}\n"
        f"⏳ Remaining: {remaining_text}"
    )

    list_type = context.user_data.get("sub_list_type", "active")
    back_cb = f"SUB_LIST|{list_type}"

    if list_type == "inactive":
        action_button = InlineKeyboardButton("🗑 Delete", callback_data=f"SUB_DELETE|{user_id}")
    else:
        action_button = InlineKeyboardButton("🚫 Revoke", callback_data=f"SUB_REVOKE|{user_id}")

    keyboard = InlineKeyboardMarkup([
        [
            action_button,
            InlineKeyboardButton("🔄 Renew", callback_data=f"SUB_RENEW|{user_id}"),
            InlineKeyboardButton("⬅️ Back",  callback_data=back_cb),
        ]
    ])

    await query.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")



async def sub_renew(update, context):
    """Opens the duration picker to renew an existing subscriber."""
    query = update.callback_query
    await query.answer()

    target_id = int(query.data.split("|", 1)[1])
    context.user_data["sub_renew_id"] = target_id

    # Pre-fill name so sub_apply_duration can use it
    _conn, _cur = get_db()
    _cur.execute("SELECT name FROM subscribers WHERE user_id=?", (target_id,))
    row = _cur.fetchone()
    _conn.close()
    name = row[0] if row else str(target_id)
    context.user_data["sub_new_name"] = name
    context.user_data["sub_new_user_id"] = target_id

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📅 1 Day",    callback_data="SUB_DURATION|1 Day"),
         InlineKeyboardButton("📅 1 Week",   callback_data="SUB_DURATION|1 Week"),
         InlineKeyboardButton("📅 1 Month",  callback_data="SUB_DURATION|1 Month")],
        [InlineKeyboardButton("📅 6 Months", callback_data="SUB_DURATION|6 Months"),
         InlineKeyboardButton("📅 1 Year",   callback_data="SUB_DURATION|1 Year"),
         InlineKeyboardButton("♾ Lifetime",  callback_data="SUB_DURATION|Lifetime")],
        [InlineKeyboardButton("❌ Cancel",   callback_data=f"SUB_VIEW|{target_id}")],
    ])

    await query.message.edit_text(
        f"🔄 *Renew subscription for {name}*\n\nSelect new duration:",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )

async def sub_revoke_confirm(update, context):
    query = update.callback_query
    await query.answer()

    target_id = int(query.data.split("|", 1)[1])

    _conn, _cur = get_db()
    _cur.execute("SELECT name FROM subscribers WHERE user_id=?", (target_id,))
    row = _cur.fetchone()
    _conn.close()
    name = row[0] if row else str(target_id)

    context.user_data["sub_revoke_id"] = target_id

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Yes, Revoke", callback_data="SUB_REVOKE_CONFIRM"),
            InlineKeyboardButton("❌ Cancel",      callback_data="SUB_REVOKE_CANCEL"),
        ]
    ])

    await query.message.reply_text(
        f"⚠️ Revoke access for *{name}*?\n\nThey will lose admin access immediately.",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )

async def sub_revoke_apply(update, context):
    query = update.callback_query
    await query.answer()

    target_id = context.user_data.pop("sub_revoke_id", None)
    if not target_id:
        return

    try:
        async with DB_LOCK:
            _conn, _cur = get_db()
            _cur.execute("""
                UPDATE subscribers
                SET is_active = 0, expires_at = 0
                WHERE user_id = ?
            """, (target_id,))
            _conn.commit()
            _conn.close()
    except Exception as e:
        print("⚠️ Revoke failed:", e)
        return

    try: await query.message.delete()
    except: pass

    await flash_message(context.bot, query.message.chat_id, "✅ Subscriber revoked.")

async def sub_revoke_cancel(update, context):
    query = update.callback_query
    await query.answer()
    try: await query.message.delete()
    except: pass

async def auto_expire_subscribers(context):
    now = int(time.time())
    ONE_YEAR = 365 * 24 * 3600

    async with DB_LOCK:
        _conn, _cur = get_db()

        # Step 1: Mark overdue active subscriptions as inactive
        _cur.execute("""
            UPDATE subscribers
            SET is_active = 0
            WHERE subscription_type != 'Lifetime'
              AND expires_at > 0
              AND expires_at <= ?
              AND is_active = 1
        """, (now,))
        _conn.commit()

        # Step 2: Find inactive subscribers who expired more than 1 year ago
        cutoff = now - ONE_YEAR
        _cur.execute("""
            SELECT user_id FROM subscribers
            WHERE is_active = 0
              AND expires_at > 0
              AND expires_at <= ?
        """, (cutoff,))
        stale_users = [row[0] for row in _cur.fetchall()]

        for uid in stale_users:
            print(f"🗑 Auto-purging data for inactive user {uid} (expired >1 year ago)")

            _cur.execute("""
                DELETE FROM quiz_question_links
                WHERE quiz_id IN (
                    SELECT quiz_id FROM quizzes WHERE owner_id=?
                )
            """, (uid,))

            _cur.execute("DELETE FROM quizzes WHERE owner_id=?", (uid,))

            _cur.execute("DELETE FROM folders WHERE owner_id=?", (uid,))

            _cur.execute("SELECT id FROM question_bank_folders WHERE owner_id=?", (uid,))
            qb_folder_ids = [row[0] for row in _cur.fetchall()]

            for fid in qb_folder_ids:
                _cur.execute("DELETE FROM question_bank WHERE folder_id=?", (fid,))

            _cur.execute("DELETE FROM question_bank_folders WHERE owner_id=?", (uid,))

            _cur.execute("DELETE FROM subscribers WHERE user_id=?", (uid,))

        _conn.commit()
        _conn.close()

async def subscriber_agree_notice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the subscriber tapping 'I Agree' on the first-access notice."""
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id

    # Mark notice as acknowledged in DB
    try:
        async with DB_LOCK:
            _conn, _cur = get_db()
            _cur.execute(
                "UPDATE subscribers SET needs_notice=0 WHERE user_id=?",
                (user_id,)
            )
            _conn.commit()
            _conn.close()
    except Exception as e:
        print("⚠️ Failed to clear notice flag:", e)

    # Delete the notice message
    try:
        await query.message.delete()
    except:
        pass

    # Now show the normal admin panel
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📂 Quiz Folder", callback_data="HOME_MY_QUIZZES"),
            InlineKeyboardButton("➕ Create a new Quiz", callback_data="HOME_CREATE"),
        ],
        [
            InlineKeyboardButton("🗄 Database", callback_data="HOME_DATABASE"),
            InlineKeyboardButton("❓ Create a Question", callback_data="HOME_CREATE_QUESTION"),
        ],
    ])

    msg = await context.bot.send_message(
        chat_id=query.message.chat_id,
        text="🧠 Welcome to TeleQuiz (Admin Panel)\n\nPlease choose an option to start 👇:",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )
    context.user_data.setdefault("chat_messages", []).append(msg.message_id)

async def sub_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    chat_id = query.message.chat_id

    # 🧹 Delete the prompt message
    prompt_id = context.user_data.pop("sub_prompt_id", None)
    if prompt_id:
        try:
            await context.bot.delete_message(chat_id, prompt_id)
        except Exception:
            pass

    # 🧹 Clear all subscriber flow state
    context.user_data.pop("state", None)
    context.user_data.pop("sub_new_user_id", None)
    context.user_data.pop("sub_new_name", None)

async def sub_delete(update, context):
    query = update.callback_query
    await query.answer()

    target_id = int(query.data.split("|", 1)[1])

    _conn, _cur = get_db()
    _cur.execute("SELECT name FROM subscribers WHERE user_id=?", (target_id,))
    row = _cur.fetchone()
    _conn.close()
    name = row[0] if row else str(target_id)

    try:
        async with DB_LOCK:
            _conn, _cur = get_db()
            _cur.execute("DELETE FROM subscribers WHERE user_id=?", (target_id,))
            _conn.commit()
            _conn.close()
    except Exception as e:
        print("⚠️ Failed to delete subscriber:", e)
        await flash_message(context.bot, query.message.chat_id, "❌ Delete failed.")
        return

    await flash_message(context.bot, query.message.chat_id, f"🗑 {name} removed from subscribers.")
    await home_manage_subscribers_from_message(query.message, context)

async def backup_db(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_USER_ID:
        return
    try:
        with open(DB_FILE, "rb") as f:
            await context.bot.send_document(
                chat_id=update.effective_user.id,
                document=f,
                filename="quizbot_backup.db",
                caption="📦 Database backup"
            )
    except Exception as e:
        await update.message.reply_text(f"❌ Backup failed: {e}")

async def cancel_post_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    chat_id = query.message.chat_id

    # Clean up the pending token from DB to avoid orphaned tokens
    quiz_id = context.user_data.pop("pending_post_quiz_id", None)
    token   = context.user_data.pop("pending_post_token", None)
    if quiz_id and token:
        try:
            async with DB_LOCK:
                _conn, _cur = get_db()
                _cur.execute(
                    "DELETE FROM quiz_post_tokens WHERE token=? AND quiz_id=?",
                    (token, quiz_id)
                )
                _conn.commit()
                _conn.close()
        except:
            pass

    context.user_data.pop("post_quiz_prompt_msg_id", None)

    try:
        await query.message.delete()
    except:
        pass

# =========================
# HANDLERS
# =========================
# load_owner_from_db()
ensure_default_folder()
ensure_default_qb_folder()
ensure_all_subscriber_default_folders()
ensure_indexes()
fix_leaderboard_key_format()
restore_group_lb_messages()

from telegram.ext import ApplicationBuilder

app = (
    ApplicationBuilder()
    .token(BOT_TOKEN)
    .connect_timeout(30)
    .read_timeout(30)
    .write_timeout(30)
    .pool_timeout(30)
    .build()
)

app.job_queue.run_repeating(auto_expire_subscribers, interval=3600, first=10)
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("backup", backup_db))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

# =========================
# Must Stay on Top of other CallbackQueryHandler
# =========================
app.add_handler(CallbackQueryHandler(global_quiz_guard), group=-1)
# =========================
app.add_handler(CommandHandler("refresh", refresh_command))
app.add_handler(CommandHandler("postquiz", post_quiz_command))
app.add_handler(CallbackQueryHandler(home_manage_subscribers, pattern="^HOME_MANAGE_SUBSCRIBERS$"))
app.add_handler(CallbackQueryHandler(sub_add_start,           pattern="^SUB_ADD$"))
app.add_handler(CallbackQueryHandler(sub_apply_duration,      pattern="^SUB_DURATION\\|"))
app.add_handler(CallbackQueryHandler(sub_list,                pattern="^SUB_LIST\\|"))
app.add_handler(CallbackQueryHandler(sub_list_prev, pattern="^SUB_LIST_PREV\\|"))
app.add_handler(CallbackQueryHandler(sub_list_next, pattern="^SUB_LIST_NEXT\\|"))
app.add_handler(CallbackQueryHandler(lambda u, c: u.callback_query.answer(), pattern="^SUB_LIST_NOP$"))
app.add_handler(CallbackQueryHandler(sub_overview,            pattern="^SUB_VIEW\\|"))
app.add_handler(CallbackQueryHandler(sub_renew,               pattern="^SUB_RENEW\\|"))
app.add_handler(CallbackQueryHandler(sub_revoke_confirm,      pattern="^SUB_REVOKE\\|"))
app.add_handler(CallbackQueryHandler(sub_revoke_apply,        pattern="^SUB_REVOKE_CONFIRM$"))
app.add_handler(CallbackQueryHandler(sub_revoke_cancel,       pattern="^SUB_REVOKE_CANCEL$"))
app.add_handler(CallbackQueryHandler(subscriber_agree_notice, pattern="^SUB_AGREE_NOTICE$"))
app.add_handler(CallbackQueryHandler(sub_cancel,              pattern="^SUB_CANCEL$"))
app.add_handler(CallbackQueryHandler(sub_delete, pattern="^SUB_DELETE\\|"))
app.add_handler(CallbackQueryHandler(db_rename_folder_start, pattern="^DB_RENAME_FOLDER\\|"))
app.add_handler(CallbackQueryHandler(cancel_db_rename_folder, pattern="^CANCEL_DB_RENAME_FOLDER$"))
app.add_handler(CallbackQueryHandler(db_delete_folder, pattern="^DB_DELETE_FOLDER\\|"))
app.add_handler(CallbackQueryHandler(db_delete_folder_confirm, pattern="^DB_DELETE_FOLDER_CONFIRM$"))
app.add_handler(CallbackQueryHandler(duplicate_create_anyway, pattern="^DUP_CREATE_ANYWAY$"))
app.add_handler(CallbackQueryHandler(duplicate_edit_question, pattern="^DUP_EDIT$"))
app.add_handler(CallbackQueryHandler(duplicate_cancel, pattern="^DUP_CANCEL$"))
app.add_handler(CallbackQueryHandler(cancel_add_folder, pattern="^CANCEL_ADD_FOLDER$"))
app.add_handler(CallbackQueryHandler(cancel_create_quiz, pattern="^CANCEL_CREATE_QUIZ$"))
app.add_handler(CallbackQueryHandler(move_folder_prev, pattern="^MOVE_FOLDER_PREV$"))
app.add_handler(CallbackQueryHandler(move_folder_next, pattern="^MOVE_FOLDER_NEXT$"))
app.add_handler(CallbackQueryHandler(qb_clear_selected, pattern="^QB_CLEAR_SELECTED$"))
app.add_handler(CallbackQueryHandler(manage_quiz_prev, pattern="^MANAGE_QUIZ_PREV$"))
app.add_handler(CallbackQueryHandler(manage_quiz_next, pattern="^MANAGE_QUIZ_NEXT$"))
app.add_handler(CallbackQueryHandler(manage_back_to_folders, pattern="^MANAGE_BACK_TO_FOLDERS$"))
app.add_handler(CallbackQueryHandler(manage_toggle_quiz, pattern="^MANAGE_TOGGLE\\|"))
app.add_handler(CallbackQueryHandler(manage_open_folder, pattern="^MANAGE_FOLDER\\|"))
app.add_handler(CallbackQueryHandler(manage_folder_prev, pattern="^MANAGE_FOLDER_PREV$"))
app.add_handler(CallbackQueryHandler(manage_folder_next, pattern="^MANAGE_FOLDER_NEXT$"))
app.add_handler(CallbackQueryHandler(manage_question_start, pattern="^MANAGE_Q$"))
app.add_handler(CallbackQueryHandler(return_to_preview, pattern="^RETURN_TO_PREVIEW$"))
app.add_handler(CallbackQueryHandler(manage_question_menu, pattern="^MANAGE_Q$"))
app.add_handler(CallbackQueryHandler(back_to_questions, pattern="^RETURN_TO_QUESTIONS$"))
app.add_handler(CallbackQueryHandler(mc_quiz_prev, pattern="^MC_QUIZ_PREV$"))
app.add_handler(CallbackQueryHandler(mc_quiz_next, pattern="^MC_QUIZ_NEXT$"))
app.add_handler(CallbackQueryHandler(mc_folder_prev, pattern="^MC_FOLDER_PREV$"))
app.add_handler(CallbackQueryHandler(mc_folder_next, pattern="^MC_FOLDER_NEXT$"))
app.add_handler(CallbackQueryHandler(cancel_edit_question_explanation, pattern="^CANCEL_EDIT_Q_EXPL$"))
app.add_handler(CallbackQueryHandler(cancel_edit_question_options, pattern="^CANCEL_EDIT_Q_OPTIONS$"))
app.add_handler(CallbackQueryHandler(apply_new_options_correct, pattern="^EDIT_OPT_CORRECT_"))
app.add_handler(CallbackQueryHandler(cancel_edit_question_text, pattern="^CANCEL_EDIT_Q_TEXT$"))
app.add_handler(CallbackQueryHandler(delete_finish_message, pattern="^DELETE_FINISH_MSG$"))
app.add_handler(CallbackQueryHandler(qb_auto_add_questions, pattern=r"^QB_AUTO_ADD\|"))
app.add_handler(CallbackQueryHandler(qb_remove_question_from_quiz, pattern=r"^QB_REMOVE_Q\|"))
app.add_handler(CallbackQueryHandler(qb_add_selected_questions, pattern="^QB_ADD_SELECTED$"))
app.add_handler(CallbackQueryHandler(qb_add_this_page, pattern="^QB_ADD_THIS_PAGE$"))
app.add_handler(CallbackQueryHandler(qb_toggle_select_question, pattern="^QB_SELECT_Q\\|"))
app.add_handler(CallbackQueryHandler(qb_question_prev, pattern="^QB_Q_PREV$"))
app.add_handler(CallbackQueryHandler(qb_question_next, pattern="^QB_Q_NEXT$"))
app.add_handler(CallbackQueryHandler(cancel_shuffle_menu, pattern="^CANCEL_SHUFFLE_MENU$"))
app.add_handler(CallbackQueryHandler(cancel_timer_menu, pattern="^CANCEL_TIMER_MENU$"))
app.add_handler(CallbackQueryHandler(cancel_edit_question_image, pattern="^CANCEL_EDIT_Q_IMAGE$"))
app.add_handler(CallbackQueryHandler(shuffle_back, pattern="^SHUFFLE_BACK$"))
app.add_handler(CallbackQueryHandler(resume_quiz, pattern="^RESUME_QUIZ$"))
app.add_handler(CallbackQueryHandler(force_stop_quiz, pattern="^FORCE_STOP_QUIZ$"))
app.add_handler(CallbackQueryHandler(post_quiz_to_group, pattern="^POST_QUIZ$"))
app.add_handler(CallbackQueryHandler(quiz_admin_open,      pattern=r"^GQ_ADMIN\|"))
app.add_handler(CallbackQueryHandler(qa_leaderboard_show,  pattern=r"^QA_LB\|"))
app.add_handler(CallbackQueryHandler(qa_toggle_score,      pattern=r"^QA_TOGGLE\|"))
app.add_handler(CallbackQueryHandler(qa_reset_score,       pattern=r"^QA_RESET\|"))
app.add_handler(CallbackQueryHandler(qa_export_quiz,       pattern=r"^QA_EXPORT\|"))
app.add_handler(CallbackQueryHandler(qa_back_to_panel,     pattern=r"^QA_BACK\|"))
app.add_handler(CallbackQueryHandler(qa_close,             pattern=r"^QA_CLOSE$"))
app.add_handler(CallbackQueryHandler(lambda u, c: u.callback_query.answer(), pattern=r"^QA_LB_NOP$"))
app.add_handler(CallbackQueryHandler(reset_score, pattern=r"^RESET_SCORE\|"))
app.add_handler(CallbackQueryHandler(cancel_create_question, pattern="^CANCEL_CREATE_QUESTION$"))
app.add_handler(CallbackQueryHandler(qb_move_question, pattern="^QB_MOVE$"))
app.add_handler(CallbackQueryHandler(qb_move_apply, pattern="^QB_MOVE_TO\\|"))
app.add_handler(CallbackQueryHandler(qb_move_prev, pattern="^QB_MOVE_PREV$"))
app.add_handler(CallbackQueryHandler(qb_move_next, pattern="^QB_MOVE_NEXT$"))
app.add_handler(CallbackQueryHandler(quiz_folder_prev, pattern="^QUIZ_FOLDER_PREV$"))
app.add_handler(CallbackQueryHandler(quiz_folder_next, pattern="^QUIZ_FOLDER_NEXT$"))
app.add_handler(CallbackQueryHandler(qb_open_folder, pattern="^QB_OPEN_FOLDER\\|"))
app.add_handler(CallbackQueryHandler(show_db_questions, pattern="^DB_OPEN\\|"))
app.add_handler(CallbackQueryHandler(db_q_prev, pattern="^DB_Q_PREV$"))
app.add_handler(CallbackQueryHandler(db_q_next, pattern="^DB_Q_NEXT$"))
app.add_handler(CallbackQueryHandler(lambda u, c: u.callback_query.answer(), pattern="^DB_Q_NOP$"))
# =========================
# DB MOVE IN handlers (order matters)
# =========================
app.add_handler(CallbackQueryHandler(db_move_folder_prev, pattern="^DB_MOVE_FOLDER_PREV$"))
app.add_handler(CallbackQueryHandler(db_move_folder_next, pattern="^DB_MOVE_FOLDER_NEXT$"))
app.add_handler(CallbackQueryHandler(lambda u, c: u.callback_query.answer(), pattern="^DB_MOVE_FOLDER_NOP$"))
app.add_handler(CallbackQueryHandler(db_move_from_folder_open, pattern="^DB_MOVE_FROM\\|"))
app.add_handler(CallbackQueryHandler(db_move_add_this_page, pattern="^DB_MOVE_ADD_PAGE$"))
app.add_handler(CallbackQueryHandler(db_move_auto_add, pattern="^DB_MOVE_AUTO_ADD\\|"))
app.add_handler(CallbackQueryHandler(db_move_in_start, pattern="^DB_MOVE_IN\\|"))
app.add_handler(CallbackQueryHandler(db_move_toggle, pattern="^DB_MOVE_TOGGLE\\|"))
app.add_handler(CallbackQueryHandler(db_move_confirm, pattern="^DB_MOVE_CONFIRM$"))
app.add_handler(CallbackQueryHandler(db_move_prev, pattern="^DB_MOVE_PREV$"))
app.add_handler(CallbackQueryHandler(db_move_next, pattern="^DB_MOVE_NEXT$"))
app.add_handler(CallbackQueryHandler(lambda u, c: u.callback_query.answer(), pattern="^DB_MOVE_NOP$"))
# =========================
app.add_handler(CallbackQueryHandler(db_search_start, pattern="^DB_SEARCH_START$"))
app.add_handler(CallbackQueryHandler(db_search_cancel, pattern="^DB_SEARCH_CANCEL$"))
app.add_handler(CallbackQueryHandler(db_search_preview_question, pattern="^DB_SEARCH_Q\\|"))
app.add_handler(CallbackQueryHandler(db_search_prev, pattern="^DB_SEARCH_PREV$"))
app.add_handler(CallbackQueryHandler(db_search_next, pattern="^DB_SEARCH_NEXT$"))
app.add_handler(CallbackQueryHandler(lambda u, c: u.callback_query.answer(), pattern="^DB_SEARCH_NOP$"))
app.add_handler(CallbackQueryHandler(qb_pick_folder_start, pattern="^QB_PICK_FOLDER$"))
app.add_handler(CallbackQueryHandler(qb_folder_prev, pattern="^QB_FOLDER_PREV$"))
app.add_handler(CallbackQueryHandler(qb_folder_next, pattern="^QB_FOLDER_NEXT$"))
app.add_handler(CallbackQueryHandler(home_create_manually, pattern="^HOME_CREATE_MANUALLY$"))
app.add_handler(CallbackQueryHandler(home_create_photo,    pattern="^HOME_CREATE_PHOTO$"))
app.add_handler(CallbackQueryHandler(ocr_accept,           pattern="^OCR_ACCEPT$"))
app.add_handler(CallbackQueryHandler(ocr_retake,           pattern="^OCR_RETAKE$"))
app.add_handler(CallbackQueryHandler(home_create_question, pattern="^HOME_CREATE_QUESTION$"))
app.add_handler(CallbackQueryHandler(database_add_folder_start, pattern="^DB_ADD$"))
app.add_handler(CallbackQueryHandler(confirm_delete, pattern="^CONFIRM_DELETE$"))
app.add_handler(CallbackQueryHandler(cancel_delete, pattern="^CANCEL_DELETE$"))
app.add_handler(CallbackQueryHandler(copy_question_apply, pattern="^COPY_TO\\|"))
app.add_handler(CallbackQueryHandler(folder_prev, pattern="^FOLDER_PREV\\|"))
app.add_handler(CallbackQueryHandler(folder_next, pattern="^FOLDER_NEXT\\|"))
app.add_handler(CallbackQueryHandler(home_database, pattern="^HOME_DATABASE$"))
app.add_handler(CallbackQueryHandler(database_prev, pattern="^DB_PREV$"))
app.add_handler(CallbackQueryHandler(database_next, pattern="^DB_NEXT$"))
app.add_handler(CallbackQueryHandler(cancel_play_ready, pattern="^CANCEL_PLAY_READY$"))
app.add_handler(CallbackQueryHandler(play_start, pattern="^START_THIS$"))
app.add_handler(CallbackQueryHandler(leaderboard_page_nav, pattern="^LB_PREV\\|"))
app.add_handler(CallbackQueryHandler(leaderboard_page_nav, pattern="^LB_NEXT\\|"))
app.add_handler(CallbackQueryHandler(lambda u, c: u.callback_query.answer(), pattern="^LOCKED$"))
app.add_handler(CallbackQueryHandler(play_start, pattern="^PLAY_START$"))
app.add_handler(CallbackQueryHandler(play_answer, pattern="^PLAY_ANSWER_"))
app.add_handler(CallbackQueryHandler(edit_question_explanation_start, pattern="^EDIT_Q_EXPLANATION$"))
app.add_handler(CallbackQueryHandler(edit_question_explanation_remove, pattern="^EDIT_Q_EXPL_REMOVE$"))
app.add_handler(CallbackQueryHandler(edit_question_correct_start, pattern="^EDIT_Q_CORRECT$"))
app.add_handler(CallbackQueryHandler(edit_question_correct_apply, pattern="^EDIT_CORRECT_"))
app.add_handler(CallbackQueryHandler(edit_question_options_start, pattern="^EDIT_Q_OPTIONS$"))
app.add_handler(CallbackQueryHandler(edit_question_image_send, pattern="^EDIT_Q_IMAGE_SEND$"))
app.add_handler(CallbackQueryHandler(edit_question_image_start, pattern="^EDIT_Q_IMAGE$"))
app.add_handler(CallbackQueryHandler(remove_question_image, pattern="^EDIT_Q_IMAGE_REMOVE$"))
app.add_handler(CallbackQueryHandler(edit_question_back, pattern="^EDIT_Q_BACK$"))
app.add_handler(CallbackQueryHandler(edit_question_text_start, pattern="^EDIT_Q_TEXT$"))
app.add_handler(CallbackQueryHandler(delete_question_from_quiz, pattern="^DELETE_Q_FROM_QUIZ$"))
app.add_handler(CallbackQueryHandler(db_delete_question_confirm, pattern="^DB_DELETE_Q_CONFIRM$"))
app.add_handler(CallbackQueryHandler(db_delete_question_cancel,  pattern="^DB_DELETE_Q_CANCEL$"))
app.add_handler(CallbackQueryHandler(delete_question_from_database, pattern="^DELETE_Q_FROM_DB$"))
app.add_handler(CallbackQueryHandler(edit_question_menu, pattern="^EDIT_Q$"))
app.add_handler(CallbackQueryHandler(back_to_question_options, pattern="^BACK_TO_Q_OPTIONS$"))
app.add_handler(CallbackQueryHandler(preview_question, pattern="^PREVIEW_Q$"))
app.add_handler(CallbackQueryHandler(skip_question_explanation, pattern="^SKIP_Q_EXPLANATION$"))
app.add_handler(CallbackQueryHandler(choose_correct_answer, pattern="^CORRECT_"))
app.add_handler(CallbackQueryHandler(skip_question_image, pattern="^SKIP_Q_IMAGE$"))
app.add_handler(CallbackQueryHandler(back_to_edit_menu, pattern="^BACK_TO_EDIT_MENU$"))
app.add_handler(CallbackQueryHandler(back_to_quizzes, pattern="^BACK_TO_QUIZZES$"))
app.add_handler(CallbackQueryHandler(delete_folder, pattern="^DELETE_FOLDER\\|"))
app.add_handler(CallbackQueryHandler(delete_quiz, pattern="^DELETE_QUIZ$"))
app.add_handler(CallbackQueryHandler(go_home, pattern="^GO_HOME$"))
app.add_handler(CallbackQueryHandler(home_create_quiz, pattern="^HOME_CREATE$"))
app.add_handler(CallbackQueryHandler(home_my_quizzes, pattern="^HOME_MY_QUIZZES$"))
app.add_handler(CallbackQueryHandler(move_create_folder_start, pattern="^MOVE_CREATE_FOLDER$"))
app.add_handler(CallbackQueryHandler(move_quiz_menu, pattern="^MOVE_QUIZ$"))
app.add_handler(CallbackQueryHandler(move_quiz_to_folder, pattern="^MOVE_QUIZ_TO\\|"))
app.add_handler(CallbackQueryHandler(add_folder_start, pattern="^ADD_FOLDER$"))
app.add_handler(CallbackQueryHandler(rename_folder_start, pattern="^RENAME_FOLDER\\|"))
app.add_handler(CallbackQueryHandler(open_folder, pattern="^OPEN_FOLDER\\|"))
app.add_handler(CallbackQueryHandler(back_to_folders, pattern="^BACK_TO_FOLDERS$"))
app.add_handler(CallbackQueryHandler(questions_prev, pattern="^QPAGE_PREV$"))
app.add_handler(CallbackQueryHandler(questions_next, pattern="^QPAGE_NEXT$"))
app.add_handler(CallbackQueryHandler(quiz_action_menu, pattern="^QUIZ_"))
app.add_handler(CallbackQueryHandler(preview_question, pattern=r"^Q_\d+$"))
app.add_handler(CallbackQueryHandler(edit_menu, pattern="^EDIT_THIS$"))
app.add_handler(CallbackQueryHandler(edit_title, pattern="^EDIT_TITLE$"))
app.add_handler(CallbackQueryHandler(edit_desc, pattern="^EDIT_DESC$"))
app.add_handler(CallbackQueryHandler(edit_timer_menu, pattern="^EDIT_TIMER$"))
app.add_handler(CallbackQueryHandler(set_timer, pattern="^SET_TIMER_"))
app.add_handler(CallbackQueryHandler(edit_shuffle_menu, pattern="^EDIT_SHUFFLE$"))
app.add_handler(CallbackQueryHandler(toggle_shuffle, pattern="^TOGGLE_"))
app.add_handler(CallbackQueryHandler(show_questions, pattern="^EDIT_QUESTIONS$"))
app.add_handler(CallbackQueryHandler(back_to_action, pattern="^BACK_TO_ACTION$"))
app.add_handler(CallbackQueryHandler(edit_correct_answer, pattern="^EDIT_CORRECT$"))

async def global_error_handler(update, context):
    print("❌ Unhandled error:", context.error)

app.add_error_handler(global_error_handler)

print("✅ TeleQuiz is running...")
app.run_polling(
    poll_interval=1.0,
    timeout=30,
    drop_pending_updates=True,
    close_loop=False
)
####################################################################################################################################################################################################################################
# CODE BY PARTS - END OF CODE
####################################################################################################################################################################################################################################
