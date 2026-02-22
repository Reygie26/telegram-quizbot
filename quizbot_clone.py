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

from telegram.ext import InlineQueryHandler
from telegram import InlineQueryResultArticle, InputTextMessageContent



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

## =========================
## START OF CODE
## =========================

## =========================
## HELPERS
## =========================

async def send_tracked_message(update, context, text, **kwargs):
    msg = await update.effective_chat.send_message(text=text, **kwargs)

    # Store message ID
    context.user_data.setdefault("chat_messages", []).append(msg.message_id)

    return msg

def track_bot_message(context, message_id):
    context.user_data.setdefault("bot_messages", set()).add(message_id)

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

# =========================
# DATABASE
# =========================
conn = sqlite3.connect(DB_FILE, check_same_thread=False)
cur = conn.cursor()

cur.execute("""
CREATE TABLE IF NOT EXISTS leaderboard (
    quiz_id TEXT,
    chat_id INTEGER,
    user_id INTEGER,
    username TEXT,
    score INTEGER,
    PRIMARY KEY (quiz_id, chat_id, user_id)
)
""")
conn.commit()

cur.execute("""
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

cur.execute("""
CREATE TABLE IF NOT EXISTS quiz_question_links (
    quiz_id TEXT,
    question_id INTEGER,
    position INTEGER,
    PRIMARY KEY (quiz_id, question_id)
)
""")

conn.commit()

cur.execute("""
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

cur.execute("""
CREATE TABLE IF NOT EXISTS folders (
    owner_id INTEGER,
    name TEXT,
    UNIQUE(owner_id, name)
)
""")
conn.commit()

cur.execute("""
CREATE TABLE IF NOT EXISTS question_bank_folders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id INTEGER,
    name TEXT,
    UNIQUE(owner_id, name)
)
""")

cur.execute("""
CREATE TABLE IF NOT EXISTS quiz_post_tokens (
    token TEXT PRIMARY KEY,
    quiz_id TEXT,
    owner_id INTEGER,
    created_at INTEGER
)
""")
conn.commit()

cur.execute("""
CREATE TABLE IF NOT EXISTS group_leaderboard (
    leaderboard_key TEXT,
    user_id INTEGER,
    name TEXT,
    score INTEGER,
    PRIMARY KEY (leaderboard_key, user_id)
)
""")
conn.commit()

# ===== RUN ONCE: ADD FOLDER COLUMN IF MISSING =====
# =========================
# OWNER RESTORE
# =========================
def load_owner_from_db():
    global OWNER_USER_ID
    cur.execute("SELECT owner_id FROM quizzes LIMIT 1")
    row = cur.fetchone()
    if row:
        OWNER_USER_ID = row[0]

def ensure_default_folder():
    cur.execute(
        "INSERT OR IGNORE INTO folders (owner_id, name) VALUES (?, 'Default')",
        (OWNER_USER_ID,)
    )
    conn.commit()

def ensure_indexes():
    cur.execute("CREATE INDEX IF NOT EXISTS idx_ql_quiz_id ON quiz_question_links(quiz_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_ql_question_id ON quiz_question_links(question_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_ql_quiz_question ON quiz_question_links(quiz_id, question_id)")
    
    cur.execute("CREATE INDEX IF NOT EXISTS idx_qb_folder_id ON question_bank(folder_id)")
    
    cur.execute("CREATE INDEX IF NOT EXISTS idx_quizzes_owner_folder ON quizzes(owner_id, folder)")
    
    cur.execute("CREATE INDEX IF NOT EXISTS idx_leaderboard_quiz_chat ON leaderboard(quiz_id, chat_id)")
    
    conn.commit()

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

        # 🔍 Verify leaderboard exists
        lb_info = GROUP_LB_MESSAGES.get(leaderboard_key)
        if not lb_info:
            msg = await update.message.reply_text("❌ This quiz link is no longer valid.")
            context.user_data.setdefault("chat_messages", []).append(msg.message_id)
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

        msg = await update.message.reply_text(
            "🎮 Quiz ready!\n\nPress the button below to start.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("▶️ Start Quiz", callback_data="PLAY_START")]
            ])
        )

        context.user_data["chat_messages"].append(msg.message_id)
        return

    # ❌ Block /start inside groups & channels
    if chat_type in ("group", "supergroup", "channel"):
        return

    # 🔒 Private chat but NOT owner
    if user_id != OWNER_USER_ID:
        msg = await update.message.reply_text(
            "👋 Hi!\n\nPlease open a quiz from a group to start answering.\nYou don’t have access to the admin panel."
        )
        context.user_data.setdefault("chat_messages", []).append(msg.message_id)
        return

    # ✅ OWNER — show admin home
    # 🔒 HARD RESET: entering admin mode must clear play state
    context.user_data.clear()
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
        "🧠 **Welcome to TeleQuiz a Telegram Quiz Bot personally created by Engr. Reygie M. Gorgonio to provide review solutions (Admin Panel)**\n\nPlease choose an option:",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )

    # 🔥 Track admin panel message
    context.user_data["chat_messages"].append(msg.message_id)

    # (Optional — keep if you still use this elsewhere)
    track_bot_message(context, msg.message_id)

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
        cur.execute(
            "UPDATE question_bank SET image_file_id=? WHERE id=?",
            (file_id, qid)
        )
        conn.commit()

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

    if q_state != "NEW_Q_IMAGE":
        return

    photo = update.message.photo[-1]
    file_id = photo.file_id

    context.user_data["new_question"]["image"] = file_id
    context.user_data["add_q_state"] = "NEW_Q_OPTION_1"

    msg = await update.message.reply_text("➡️ Send option 1:")
    context.user_data.setdefault("question_flow_msgs", []).append(msg.message_id)

    return

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Track user message
    context.user_data.setdefault("chat_messages", []).append(update.message.message_id)

    # ================= DATABASE TEXT FLOW (HARD ISOLATION) =================
    state = context.user_data.get("state")
    text = update.message.text.strip()

    # 🔑 Track user messages during question creation
    if context.user_data.get("add_q_state"):
        context.user_data.setdefault("question_flow_msgs", []).append(update.message.message_id)

    if state == "DB_ADD_FOLDER":
        folder = text.strip()

        # ❌ Empty name
        if not folder:
            await update.message.reply_text("❌ Folder name cannot be empty.")
            return

        # Normalize name (capitalization only for display)
        normalized = folder.strip()

        # ❌ Default is reserved
        if normalized.lower() == "default":
            await update.message.reply_text("❌ 'Default Folder' already exists.")
            return

        # ❌ Check duplicate (case-insensitive)
        cur.execute(
            """
            SELECT 1
            FROM question_bank_folders
            WHERE owner_id=?
              AND LOWER(name) = LOWER(?)
            """,
            (OWNER_USER_ID, normalized)
        )
        if cur.fetchone():
            await update.message.reply_text("❌ Folder already exists.")
            return

        # ✅ Create folder
        cur.execute(
            "INSERT INTO question_bank_folders (owner_id, name) VALUES (?, ?)",
            (OWNER_USER_ID, normalized)
        )
        conn.commit()

        # 🔑 EXIT DB MODE IMMEDIATELY
        context.user_data.pop("state", None)

        await update.message.reply_text(
            f"📁 Database folder **{normalized}** created.",
            parse_mode="Markdown"
        )

        await show_database_menu(update.message, context)
        return

    # ================= ADD QUESTION FLOW =================
    q_state = context.user_data.get("add_q_state")

    # ================= EDIT QUESTION EXPLANATION =================
    if context.user_data.get("edit_q_field") == "EXPLANATION":
        qid = context.user_data.get("active_question_id")
        if not qid:
            return

        new_text = update.message.text.strip()
        chat_id = update.effective_chat.id

        # Update DB
        cur.execute(
            "UPDATE question_bank SET explanation=? WHERE id=?",
            (new_text, qid)
        )
        conn.commit()

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

        # 🔑 Update DB
        cur.execute(
            "UPDATE question_bank SET question=? WHERE id=?",
            (text, qid)
        )
        conn.commit()

        context.user_data.pop("edit_q_field", None)

        # =========================
        # STEP 1 — Confirmation
        # =========================
        confirm_msg = await update.message.reply_text("✅ Question text updated.")

        await asyncio.sleep(2)

        chat_id = update.effective_chat.id

        # =========================
        # STEP 2 — Cleanup
        # =========================

        # Delete confirmation
        try:
            await confirm_msg.delete()
        except:
            pass

        # Delete user's typed message
        try:
            await update.message.delete()
        except:
            pass

        # Delete "Send new question text" prompt
        prompt_id = context.user_data.pop("edit_text_prompt_id", None)
        if prompt_id:
            try:
                await context.bot.delete_message(chat_id, prompt_id)
            except:
                pass

        # =========================
        # STEP 3 — Replace preview safely
        # =========================
        chat_id = update.effective_chat.id
        await rebuild_question_preview(chat_id, context)

        return

    # 📝 Question text (NEW QUESTION — WITH DUPLICATE CHECK)
    if q_state == "NEW_Q_TEXT":

        context.user_data["last_user_question_msg_id"] = update.message.message_id

        from difflib import SequenceMatcher

        new_text = text.strip()
        similar_matches = []

        # 🔎 Load existing questions
        cur.execute("SELECT id, question FROM question_bank")
        existing_questions = cur.fetchall()

        for qid, existing_text in existing_questions:
            similarity = SequenceMatcher(
                None,
                new_text.lower(),
                existing_text.lower()
            ).ratio()

            if similarity >= 0.80:  # 🔥 Adjust threshold if needed
                similar_matches.append((similarity, existing_text))

        # Sort by highest similarity
        similar_matches.sort(reverse=True, key=lambda x: x[0])

        if similar_matches:
            top_matches = similar_matches[:5]

            warning_text = "⚠️ *Similar question(s) found:*\n\n"

            for i, (_, q_text) in enumerate(top_matches, 1):
                warning_text += f"{i}. {q_text[:80]}\n"

            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ Create Anyway", callback_data="DUP_CREATE_ANYWAY"),
                    InlineKeyboardButton("✏️ Edit Question", callback_data="DUP_EDIT"),
                ],
                [
                    InlineKeyboardButton("❌ Cancel", callback_data="DUP_CANCEL")
                ]
            ])

            # Store pending question text
            context.user_data["pending_duplicate_text"] = new_text
            context.user_data["add_q_state"] = "CONFIRM_DUPLICATE_Q"

            msg = await update.message.reply_text(
                warning_text,
                reply_markup=keyboard,
                parse_mode="Markdown"
            )

            context.user_data.setdefault("question_flow_msgs", []).append(msg.message_id)
            return

        # ✅ No duplicate → continue normally
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
        context.user_data["new_question"]["options"].append(text)
        context.user_data["add_q_state"] = "NEW_Q_OPTION_2"
        msg = await update.message.reply_text("➡️ Send option 2:")
        context.user_data.setdefault("question_flow_msgs", []).append(msg.message_id)
        return

    if q_state == "NEW_Q_OPTION_2":
        context.user_data["new_question"]["options"].append(text)
        context.user_data["add_q_state"] = "NEW_Q_OPTION_3"
        msg = await update.message.reply_text("➡️ Send option 3:")
        context.user_data.setdefault("question_flow_msgs", []).append(msg.message_id)
        return

    if q_state == "NEW_Q_OPTION_3":
        context.user_data["new_question"]["options"].append(text)
        context.user_data["add_q_state"] = "NEW_Q_OPTION_4"
        msg = await update.message.reply_text("➡️ Send option 4:")
        context.user_data.setdefault("question_flow_msgs", []).append(msg.message_id)
        return

    if q_state == "NEW_Q_OPTION_4":
        context.user_data["new_question"]["options"].append(text)
        context.user_data["add_q_state"] = "NEW_Q_CORRECT"

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

        # =========================
        # AFTER OPTION 4
        # =========================
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
        context.user_data["new_question"]["explanation"] = text
        await save_new_question(update.message, context)
        return

    # ================= MOVE + CREATE FOLDER =================
    if state == "MOVE_ADD_FOLDER":
        folder = text

        if folder == "Default":
            await update.message.reply_text("❌ 'Default' folder already exists.")
            return

        cur.execute(
            "SELECT 1 FROM folders WHERE owner_id=? AND name=?",
            (OWNER_USER_ID, folder)
        )
        if cur.fetchone():
            await update.message.reply_text("❌ Folder already exists.")
            return

        cur.execute(
            "INSERT INTO folders (owner_id, name) VALUES (?, ?)",
            (OWNER_USER_ID, folder)
        )

        quiz_id = context.user_data.get("active_quiz_id")
        if not quiz_id:
            return
        cur.execute(
            "UPDATE quizzes SET folder=? WHERE quiz_id=? AND owner_id=?",
            (folder, quiz_id, OWNER_USER_ID)
        )
        conn.commit()

        context.user_data["state"] = None
        await update.message.reply_text(f"✅ Folder '{folder}' created and quiz moved.")
        await show_quiz_action_menu(update.message, context)
        return

    # ================= ADD FOLDER =================
    if state == "ADD_FOLDER":
        chat_id = update.effective_chat.id
        user_msg_id = update.message.message_id
        folder_name = text.strip()

        if folder_name == "Default":
            await update.message.reply_text("❌ You cannot create a folder named Default.")
            return

        # Check duplicate
        cur.execute(
            "SELECT 1 FROM folders WHERE owner_id=? AND name=?",
            (OWNER_USER_ID, folder_name)
        )
        if cur.fetchone():
            await update.message.reply_text("❌ Folder already exists.")
            return

        try:
            async with DB_LOCK:
                cur.execute(
                    "INSERT INTO folders (owner_id, name) VALUES (?, ?)",
                    (OWNER_USER_ID, folder_name)
                )
                conn.commit()
        except Exception as e:
            print("⚠️ Failed to create folder:", e)
            await flash_message(context.bot, chat_id, "❌ Folder creation failed.")
            return

        # 🧹 DELETE prompt message
        prompt_id = context.user_data.pop("add_folder_prompt_id", None)
        if prompt_id:
            try:
                await context.bot.delete_message(chat_id, prompt_id)
            except:
                pass

        # 🧹 DELETE user message
        try:
            await context.bot.delete_message(chat_id, user_msg_id)
        except:
            pass

        # ✅ Send confirmation
        confirm_msg = await context.bot.send_message(
            chat_id,
            f'✅ Folder "{folder_name}" created.'
        )

        # 🧹 Clear state
        context.user_data["state"] = None

        # ⏳ Wait 2 seconds
        await asyncio.sleep(2)

        # 🧹 Delete confirmation
        try:
            await confirm_msg.delete()
        except:
            pass

        # 🔁 Refresh folder list CLEANLY (edit-based)
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

        if new == "Default":
            await update.message.reply_text("❌ You cannot rename a folder to Default.")
            return

        # Check if new name already exists
        cur.execute(
            "SELECT 1 FROM folders WHERE owner_id=? AND name=?",
            (OWNER_USER_ID, new)
        )
        if cur.fetchone():
            await update.message.reply_text("❌ A folder with this name already exists.")
            return

        try:
            async with DB_LOCK:
                # Rename folder in folders table
                cur.execute(
                    "UPDATE folders SET name=? WHERE owner_id=? AND name=?",
                    (new, OWNER_USER_ID, old)
                )

                # Rename folder in quizzes table
                cur.execute(
                    "UPDATE quizzes SET folder=? WHERE owner_id=? AND folder=?",
                    (new, OWNER_USER_ID, old)
                )

                conn.commit()
        except Exception as e:
            print("⚠️ Rename failed:", e)
            await flash_message(context.bot, chat_id, "❌ Rename failed.")
            return

        # 🧹 DELETE rename prompt
        prompt_id = context.user_data.pop("rename_prompt_msg_id", None)
        if prompt_id:
            try:
                await context.bot.delete_message(chat_id, prompt_id)
            except:
                pass

        # 🧹 DELETE user's typed message
        try:
            await context.bot.delete_message(chat_id, user_msg_id)
        except:
            pass

        # 🧹 Clear state
        context.user_data["state"] = None
        context.user_data.pop("rename_folder", None)

        # 🔁 Refresh folder list cleanly (edit-based)
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

        try:
            async with DB_LOCK:
                cur.execute(
                    "INSERT INTO quizzes VALUES (?, ?, ?, NULL, ?, 1, 1, 15)",
                    (
                        context.user_data["quiz_id"],
                        OWNER_USER_ID,
                        title,
                        context.user_data.get("current_folder", "Default")
                    )
                )
                conn.commit()
        except Exception as e:
            print("⚠️ Quiz creation failed:", e)
            await flash_message(context.bot, chat_id, "❌ Failed to create quiz.")
            return

        # 🔑 Set active quiz
        context.user_data["active_quiz_id"] = context.user_data["quiz_id"]
        context.user_data["state"] = None

        # 🧹 STEP 1 — Delete prompt message
        prompt_id = context.user_data.pop("create_quiz_prompt_id", None)
        if prompt_id:
            try:
                await context.bot.delete_message(chat_id, prompt_id)
            except:
                pass

        # 🧹 STEP 2 — Delete user's typed title
        try:
            await context.bot.delete_message(chat_id, user_msg_id)
        except:
            pass

        # ✅ STEP 3 — Send confirmation
        confirm_msg = await context.bot.send_message(
            chat_id,
            "✅ Quiz created."
        )

        # ⏳ STEP 4 — Wait 2 seconds
        await asyncio.sleep(2)

        # 🧹 STEP 5 — Delete confirmation
        try:
            await confirm_msg.delete()
        except:
            pass

        # 🚀 STEP 6 — Open Quiz Action Menu cleanly
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

        # 🔑 Store user message ID (new title)
        user_msg_id = update.message.message_id

        # Update title
        cur.execute(
            "UPDATE quizzes SET title=? WHERE quiz_id=?",
            (text, quiz_id)
        )
        conn.commit()

        context.user_data["state"] = None

        # 🔔 Temporary confirmation
        confirm_msg = await update.message.reply_text("✅ Title updated.")

        # =========================
        # 🧹 CLEANUP UI MESSAGES
        # =========================
        prompt_id = context.user_data.pop("edit_title_prompt_id", None)

        # Delete "Send new title" message
        if prompt_id:
            try:
                await context.bot.delete_message(update.effective_chat.id, prompt_id)
            except:
                pass

        # Delete user's typed title
        try:
            await context.bot.delete_message(update.effective_chat.id, user_msg_id)
        except:
            pass

        # Delete "Title updated" message
        try:
            await confirm_msg.delete()
        except:
            pass

        # ✅ Show updated quiz overview ONLY
        overview_id = context.user_data.get("quiz_overview_msg_id")
        if overview_id:
            await show_quiz_action_menu_by_id(
                chat_id=update.effective_chat.id,
                message_id=overview_id,
                context=context
            )

        return

    # ================= EDIT DESCRIPTION =================
    if state == "EDIT_DESC":
        quiz_id = context.user_data.get("active_quiz_id")
        if not quiz_id:
            return

        # 🔑 Store user message ID (new description)
        user_msg_id = update.message.message_id

        # Update description
        if text.upper() == "CLEAR":
            cur.execute(
                "UPDATE quizzes SET description=NULL WHERE quiz_id=?",
                (quiz_id,)
            )
        else:
            cur.execute(
                "UPDATE quizzes SET description=? WHERE quiz_id=?",
                (text, quiz_id)
            )
        conn.commit()

        # Exit edit state
        context.user_data["state"] = None

        # 🔔 Temporary confirmation
        confirm_msg = await update.message.reply_text("✅ Description updated.")

        # =========================
        # 🧹 CLEANUP UI MESSAGES
        # =========================
        prompt_id = context.user_data.pop("edit_desc_prompt_id", None)

        # Delete "Send new Quiz description" prompt
        if prompt_id:
            try:
                await context.bot.delete_message(
                    chat_id=update.effective_chat.id,
                    message_id=prompt_id
                )
            except:
                pass

        # Delete user's typed description
        try:
            await context.bot.delete_message(
                chat_id=update.effective_chat.id,
                message_id=user_msg_id
            )
        except:
            pass

        # Delete "Description updated" confirmation
        try:
            await confirm_msg.delete()
        except:
            pass

        # =========================
        # ✅ REFRESH QUIZ OVERVIEW (SAFE & CORRECT)
        # =========================
        overview_id = context.user_data.get("quiz_overview_msg_id")

        if overview_id:
            await show_quiz_action_menu_by_id(
                chat_id=update.effective_chat.id,
                message_id=overview_id,
                context=context
            )

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
    # Pagination state
    page = context.user_data.get("quiz_folder_page", 0)
    PER_PAGE = 5  # folders per page (excluding Default)

    # Load all folders
    cur.execute("""
        SELECT name
        FROM folders
        WHERE owner_id=?
    """, (OWNER_USER_ID,))
    rows = [row[0] for row in cur.fetchall()]

    # 🔑 Separate Default folder
    default_folder = "Default"
    other_folders = sorted([f for f in rows if f != default_folder])

    total = len(other_folders)
    pages = (total - 1) // PER_PAGE + 1 if total else 1
    page = max(0, min(page, pages - 1))

    start = page * PER_PAGE
    end = start + PER_PAGE
    page_items = other_folders[start:end]

    keyboard = []

    # 📁 DEFAULT FOLDER (ALWAYS ON TOP, NOT PAGINATED)
    cur.execute(
        "SELECT COUNT(*) FROM quizzes WHERE owner_id=? AND folder=?",
        (OWNER_USER_ID, default_folder)
    )
    default_count = cur.fetchone()[0]

    keyboard.append([
        InlineKeyboardButton(
            f"📁 Default Folder ({default_count})",
            callback_data=f"OPEN_FOLDER|{default_folder}"
        )
    ])

    # 📁 PAGINATED OTHER FOLDERS
    for folder in page_items:
        cur.execute(
            "SELECT COUNT(*) FROM quizzes WHERE owner_id=? AND folder=?",
            (OWNER_USER_ID, folder)
        )
        count = cur.fetchone()[0]

        keyboard.append([
            InlineKeyboardButton(
                f"📁 {folder} ({count})",
                callback_data=f"OPEN_FOLDER|{folder}"
            )
        ])

    # ◀ ▶ Pagination controls (only if needed)
    if pages > 1:
        nav = []
        if page > 0:
            nav.append(
                InlineKeyboardButton("◀ Prev", callback_data="QUIZ_FOLDER_PREV")
            )
        nav.append(
            InlineKeyboardButton(f"{page + 1}/{pages}", callback_data="QUIZ_FOLDER_NOP")
        )
        if page < pages - 1:
            nav.append(
                InlineKeyboardButton("Next ▶", callback_data="QUIZ_FOLDER_NEXT")
            )
        keyboard.append(nav)

    # Bottom actions
    keyboard.append([
        InlineKeyboardButton("➕ Add Folder", callback_data="ADD_FOLDER"),
        InlineKeyboardButton("🏠 Home", callback_data="GO_HOME")
    ])

    await message.edit_text(
        "📂 Quiz Folder",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def show_database_menu(message, context):
    """
    Database menu.
    UI behaves like Quiz Folder but logic is fully independent.
    """

    # 🔑 Load database folders (EXCLUDING Default)
    cur.execute(
        """
        SELECT id, name
        FROM question_bank_folders
        WHERE owner_id=?
          AND name != 'Default'
        ORDER BY name COLLATE NOCASE
        """,
        (OWNER_USER_ID,)
    )
    folders = cur.fetchall()

    # Pagination
    PER_PAGE = 5
    page = context.user_data.get("db_page", 0)

    total = len(folders)
    pages = (total - 1) // PER_PAGE + 1 if total else 1
    page = max(0, min(page, pages - 1))

    start = page * PER_PAGE
    end = start + PER_PAGE
    page_items = folders[start:end]

    keyboard = []

    # 📁 DEFAULT FOLDER — ALWAYS FIRST
    cur.execute(
        """
        SELECT COUNT(*)
        FROM question_bank qb
        JOIN question_bank_folders f ON f.id = qb.folder_id
        WHERE f.owner_id=? AND f.name='Default'
        """,
        (OWNER_USER_ID,)
    )
    default_count = cur.fetchone()[0]

    keyboard.append([
        InlineKeyboardButton(
            f"📁 Default Folder ({default_count})",
            callback_data="DB_OPEN|Default"
        )
    ])

    # 📁 User-created database folders
    for folder_id, folder_name in page_items:
        cur.execute(
            "SELECT COUNT(*) FROM question_bank WHERE folder_id=?",
            (folder_id,)
        )
        count = cur.fetchone()[0]

        keyboard.append([
            InlineKeyboardButton(
                f"📁 {folder_name} ({count})",
                callback_data=f"DB_OPEN|{folder_name}"
            )
        ])

    # ◀ ▶ Pagination
    if pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("◀ Prev", callback_data="DB_PREV"))
        nav.append(
            InlineKeyboardButton(f"{page + 1}/{pages}", callback_data="DB_NOP")
        )
        if page < pages - 1:
            nav.append(InlineKeyboardButton("Next ▶", callback_data="DB_NEXT"))
        keyboard.append(nav)

    # 🔘 Bottom actions
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
    query = update.callback_query
    await query.answer()

    folder_name = query.data.split("|", 1)[1]

    context.user_data["preview_mode"] = "DATABASE"

    # Get folder_id
    cur.execute(
        """
        SELECT id
        FROM question_bank_folders
        WHERE owner_id=? AND name=?
        """,
        (OWNER_USER_ID, folder_name)
    )
    row = cur.fetchone()

    if not row:
        await flash_message(context.bot, query.message.chat_id, "❌ Folder not found.")
        return

    folder_id = row[0]

    # Load questions in this folder
    cur.execute(
        """
        SELECT id, question
        FROM question_bank
        WHERE folder_id=?
        ORDER BY question COLLATE NOCASE
        """,
        (folder_id,)
    )
    rows = cur.fetchall()

    if not rows:
        await query.message.reply_text(
            f"📁 **{folder_name}**\n\n_No questions in this folder yet._",
            parse_mode="Markdown"
        )
        return

    keyboard = []

    for qid, text in rows:
        keyboard.append([
            InlineKeyboardButton(
                text[:50],
                callback_data=f"Q_{qid}"
            )
        ])

    keyboard.append([
        InlineKeyboardButton("⬅️ Back", callback_data="HOME_DATABASE")
    ])

    await query.message.edit_text(
        f"📁 **{folder_name}**\n\nSelect a question:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

async def qb_pick_folder_menu(message, context):
    """
    Question Bank folder picker (used when adding questions to a quiz)
    """

    # Reset selection state
    context.user_data["qb_selected"] = set()
    context.user_data.setdefault("qb_folder_page", 0)

    page = context.user_data["qb_folder_page"]
    PER_PAGE = 5

    cur.execute(
        """
        SELECT name
        FROM question_bank_folders
        WHERE owner_id=?
        """,
        (OWNER_USER_ID,)
    )
    rows = [row[0] for row in cur.fetchall()]

    # 🔑 Pin Default folder to top
    default_folder = "Default"
    other_folders = sorted(
        [f for f in rows if f != default_folder],
        key=str.lower
    )

    folders = [default_folder] + other_folders if default_folder in rows else other_folders

    total = len(folders)
    pages = (total - 1) // PER_PAGE + 1 if total else 1
    page = max(0, min(page, pages - 1))

    start = page * PER_PAGE
    end = start + PER_PAGE
    page_items = folders[start:end]

    keyboard = []

    # 📁 Folder buttons
    for folder in page_items:
        # 🔑 Get folder_id
        cur.execute(
            """
            SELECT id
            FROM question_bank_folders
            WHERE owner_id=? AND name=?
            """,
            (OWNER_USER_ID, folder)
        )
        row = cur.fetchone()
        if not row:
            continue

        folder_id = row[0]

        # 🔢 Count questions inside this folder
        cur.execute(
            "SELECT COUNT(*) FROM question_bank WHERE folder_id=?",
            (folder_id,)
        )
        count = cur.fetchone()[0]

        keyboard.append([
            InlineKeyboardButton(
                f"📁 {folder} ({count})",
                callback_data=f"QB_OPEN_FOLDER|{folder}"
            )
        ])

    # ◀ ▶ Pagination
    if pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("◀ Prev", callback_data="QB_FOLDER_PREV"))
        nav.append(
            InlineKeyboardButton(f"{page + 1}/{pages}", callback_data="QB_FOLDER_NOP")
        )
        if page < pages - 1:
            nav.append(InlineKeyboardButton("Next ▶", callback_data="QB_FOLDER_NEXT"))
        keyboard.append(nav)

    # ⬅️ Back
    keyboard.append([
        InlineKeyboardButton("⬅️ Back", callback_data="EDIT_QUESTIONS")
    ])

    await message.edit_text(
        "📚 **Question Bank**\n\nSelect a folder:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

# =========================
# MY QUIZZES
# =========================

async def show_quizzes_in_folder(message, context, folder):

    # Store the folder screen message reference
    context.user_data["folder_screen_message_object"] = message
    context.user_data["last_folder_screen_msg_id"] = message.message_id

    cur.execute("""
        SELECT quiz_id, title
        FROM quizzes
        WHERE owner_id=? AND folder=?
        ORDER BY title COLLATE NOCASE
    """, (OWNER_USER_ID, folder))

    rows = cur.fetchall()

    # 🔢 Pagination state
    page_key = f"folder_page_{folder}"
    page = context.user_data.get(page_key, 0)

    PER_PAGE = 5
    total = len(rows)
    pages = (total - 1) // PER_PAGE + 1 if total else 1
    page = max(0, min(page, pages - 1))

    start = page * PER_PAGE
    end = start + PER_PAGE
    page_rows = rows[start:end]

    keyboard = []

    # 📘 Quiz buttons (5 per page)
    for qid, title in page_rows:
        keyboard.append([
            InlineKeyboardButton(f"📘 {title}", callback_data=f"QUIZ_{qid}")
        ])

    # ◀ ▶ Pagination buttons
    if pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("◀ Prev", callback_data=f"FOLDER_PREV|{folder}"))
        nav.append(InlineKeyboardButton(f"{page + 1}/{pages}", callback_data="FOLDER_NOP"))
        if page < pages - 1:
            nav.append(InlineKeyboardButton("Next ▶", callback_data=f"FOLDER_NEXT|{folder}"))
        keyboard.append(nav)

    # 🧰 Folder actions (ONE ROW)
    if folder != "Default":
        keyboard.append([
            InlineKeyboardButton("✏️ Rename", callback_data=f"RENAME_FOLDER|{folder}"),
            InlineKeyboardButton("🗑 Delete", callback_data=f"DELETE_FOLDER|{folder}"),
            InlineKeyboardButton("⬅️ Back", callback_data="BACK_TO_FOLDERS")
        ])
    else:
        keyboard.append([
            InlineKeyboardButton("⬅️ Back", callback_data="BACK_TO_FOLDERS")
        ])

    title = "All Quizzes" if folder == "Default" else folder

    await message.edit_text(
        f"📁 {title}",
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

async def back_to_folders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()  # stops the loading spinner

    await show_quiz_folders(query.message, context)

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
    cur.execute(
        "SELECT folder FROM quizzes WHERE quiz_id=? AND owner_id=?",
        (quiz_id, OWNER_USER_ID)
    )
    row = cur.fetchone()
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

    # 🔑 Load folders (Default first, then alphabetical)
    cur.execute("""
        SELECT name
        FROM folders
        WHERE owner_id=?
        ORDER BY
            CASE WHEN name='Default' THEN 0 ELSE 1 END,
            name COLLATE NOCASE
    """, (OWNER_USER_ID,))

    folders = [row[0] for row in cur.fetchall()]

    total = len(folders)
    pages = (total - 1) // PER_PAGE + 1 if total else 1
    page = max(0, min(page, pages - 1))
    context.user_data["move_quiz_folder_page"] = page

    start = page * PER_PAGE
    end = start + PER_PAGE
    page_items = folders[start:end]

    keyboard = []

    for folder_name in page_items:

        # 🔢 Count quizzes inside this folder
        cur.execute("""
            SELECT COUNT(*)
            FROM quizzes
            WHERE owner_id=? AND folder=?
        """, (OWNER_USER_ID, folder_name))

        count = cur.fetchone()[0]

        keyboard.append([
            InlineKeyboardButton(
                f"📁 {folder_name} ({count})",
                callback_data=f"MOVE_QUIZ_TO|{folder_name}"
            )
        ])

    # 🔄 Pagination
    if pages > 1:
        nav = []

        if page > 0:
            nav.append(
                InlineKeyboardButton("◀ Prev", callback_data="MOVE_FOLDER_PREV")
            )

        nav.append(
            InlineKeyboardButton(f"{page+1}/{pages}", callback_data="MOVE_NOP")
        )

        if page < pages - 1:
            nav.append(
                InlineKeyboardButton("Next ▶", callback_data="MOVE_FOLDER_NEXT")
            )

        keyboard.append(nav)

    # ➕ Create new folder
    keyboard.append([
        InlineKeyboardButton("➕ Create new Folder", callback_data="MOVE_CREATE_FOLDER")
    ])

    # ⬅️ Back button
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

    await query.message.reply_text(
        "➕ Send the new folder name for this quiz:"
    )

async def database_add_folder_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # 🔒 HARD RESET: exit all other text-driven flows
    context.user_data.clear()

    # ✅ Enter Database folder creation mode
    context.user_data["state"] = "DB_ADD_FOLDER"

    await query.message.reply_text(
        "➕ Send the name of the new Database folder:"
    )

async def move_quiz_to_folder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    folder = query.data.split("|", 1)[1]
    quiz_id = context.user_data.get("active_quiz_id")
    if not quiz_id:
        return

    cur.execute(
        "UPDATE quizzes SET folder=? WHERE quiz_id=? AND owner_id=?",
        (folder, quiz_id, OWNER_USER_ID)
    )
    conn.commit()

    confirm_msg = await flash_message(context.bot, query.message.chat_id, 
        f"✅ Quiz moved to 📁 {folder}"
    )

    await asyncio.sleep(2)

    try:
        await confirm_msg.delete()
    except:
        pass

    await show_quiz_action_menu(query.message, context)

async def show_quiz_action_menu(message, context):
    quiz_id = context.user_data.get("active_quiz_id")
    if not quiz_id:
        return
    cur.execute(
        "SELECT title, description, timer, shuffle_q, shuffle_a FROM quizzes WHERE quiz_id=?",
        (quiz_id,)
    )
    title, desc, timer, sq, sa = cur.fetchone()

    cur.execute(
        "SELECT COUNT(*) FROM quiz_question_links WHERE quiz_id=?",
        (quiz_id,)
    )
    total_questions = cur.fetchone()[0]

    # 📘 Title
    text = f"📘 **{title}**"

    # 📝 Description (if any)
    if desc:
        text += f"\n📝 _{desc}_"

    # ⬜ Blank line
    text += "\n\n"

    # 📊 Questions & Timer (same row)
    text += f"📊 Questions: {total_questions}    ⏱ Timer: {timer}s"

    # 🔀 Shuffle settings (last row)
    text += (
        f"\n🔀 Questions: {'ON' if sq else 'OFF'}"
        f"   🔀 Options: {'ON' if sa else 'OFF'}"
    )
    
    keyboard = [
        [
            InlineKeyboardButton("▶️ Start this Quiz", callback_data="START_THIS"),
            InlineKeyboardButton(
                "📤 Post this Quiz",
                callback_data="POST_QUIZ"
            ),
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

    # 🔁 Reset question pagination
    context.user_data["reset_q_page"] = True

    quiz_id = context.user_data.get("active_quiz_id")
    if not quiz_id:
        return
    cur.execute(
        "SELECT title, description, timer, shuffle_q, shuffle_a FROM quizzes WHERE quiz_id=?",
        (quiz_id,)
    )
    title, desc, timer, sq, sa = cur.fetchone()

    cur.execute(
        "SELECT COUNT(*) FROM quiz_question_links WHERE quiz_id=?",
        (quiz_id,)
    )
    total_questions = cur.fetchone()[0]

    text = (
        f"📘 **{title}**"
        + (f"\n📝 _{desc}_" if desc else "")
        + "\n\n"
        + f"📊 Questions: {total_questions}    ⏱ Timer: {timer}s"
        + f"\n🔀 Questions: {'ON' if sq else 'OFF'}"
        + f"   🔀 Options: {'ON' if sa else 'OFF'}"
    )

    keyboard = [
        # Row 1
        [
            InlineKeyboardButton("📝 Edit Title", callback_data="EDIT_TITLE"),
            InlineKeyboardButton("🧾 Edit Description", callback_data="EDIT_DESC"),
        ],
        # Row 2
        [
            InlineKeyboardButton("⏱ Timer Settings", callback_data="EDIT_TIMER"),
            InlineKeyboardButton("🔀 Shuffle Settings", callback_data="EDIT_SHUFFLE"),
        ],
        # Row 3
        [
            InlineKeyboardButton("❓ Show Questions", callback_data="EDIT_QUESTIONS"),
            InlineKeyboardButton("⬅️ Back", callback_data="BACK_TO_ACTION"),
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

    # 🔐 SAFE WRITE SECTION
    try:
        async with DB_LOCK:
            cur.execute(
                "UPDATE quizzes SET timer=? WHERE quiz_id=?",
                (seconds, quiz_id)
            )
            conn.commit()
    except Exception as e:
        print("⚠️ Failed to update timer:", e)
        await query.answer("❌ Failed to update timer.", show_alert=True)
        return

    # 🔔 Temporary confirmation
    confirm_msg = await query.message.reply_text(
        f"✅ Timer set to {seconds}s."
    )

    # =========================
    # 🧹 CLEANUP UI MESSAGES
    # =========================
    prompt_id = context.user_data.pop("edit_timer_prompt_id", None)

    # Delete "Choose timer" prompt
    if prompt_id:
        try:
            await context.bot.delete_message(
                chat_id=query.message.chat_id,
                message_id=prompt_id
            )
        except:
            pass

    # Delete confirmation message
    try:
        await confirm_msg.delete()
    except:
        pass

    # ✅ Refresh quiz overview safely
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
    cur.execute(
        "SELECT shuffle_q, shuffle_a FROM quizzes WHERE quiz_id=?",
        (quiz_id,)
    )
    sq, sa = cur.fetchone()

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

    # 🔐 SAFE WRITE SECTION
    try:
        async with DB_LOCK:
            if query.data == "TOGGLE_Q":
                cur.execute(
                    "UPDATE quizzes SET shuffle_q = 1 - shuffle_q WHERE quiz_id=?",
                    (quiz_id,)
                )
            else:
                cur.execute(
                    "UPDATE quizzes SET shuffle_a = 1 - shuffle_a WHERE quiz_id=?",
                    (quiz_id,)
                )

            conn.commit()
    except Exception as e:
        print("⚠️ Failed to toggle shuffle:", e)
        await query.answer("❌ Failed to update setting.", show_alert=True)
        return

    # 🧹 Remove shuffle menu (fade effect)
    msg_id = context.user_data.pop("shuffle_menu_msg_id", None)
    if msg_id:
        try:
            await context.bot.delete_message(
                chat_id=query.message.chat_id,
                message_id=msg_id
            )
        except:
            pass

    # ✅ Return cleanly to quiz overview
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

    await query.message.edit_text(
        "🏠 Home Menu",
        reply_markup=keyboard
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

    # 🔒 Clear any quiz-specific state
    context.user_data.pop("active_quiz_id", None)

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

    msg = await query.message.reply_text(
        "❓ Create a Question\n\n📝 Send question text:",
        reply_markup=keyboard
    )

    # 🔑 Track the FIRST prompt message (existing system)
    context.user_data["question_flow_msgs"].append(msg.message_id)

    # 🔑 Track specifically for duplicate cancel cleanup
    context.user_data["create_q_prompt_msg_id"] = msg.message_id

async def home_manage_subscribers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    await flash_message(context.bot, query.message.chat_id, 
        "👥 **Manage Subscribers**\n\n🚧 This feature is coming soon.",
        parse_mode="Markdown"
    )

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
    if "q_page" not in context.user_data:
        context.user_data["q_page"] = 0
    page = context.user_data["q_page"]

    # 🔁 Always start from page 1 when entering
    if context.user_data.get("reset_q_page", True):
        context.user_data["q_page"] = 0
        context.user_data["reset_q_page"] = False

    cur.execute(
        """
        SELECT qb.id, qb.question
        FROM quiz_question_links ql
        JOIN question_bank qb ON qb.id = ql.question_id
        WHERE ql.quiz_id=?
        ORDER BY ql.position, qb.question COLLATE NOCASE
        """,
        (quiz_id,)
    )
    rows = cur.fetchall()

    total = len(rows)
    start = page * QUESTIONS_PER_PAGE
    end = start + QUESTIONS_PER_PAGE
    page_rows = rows[start:end]

    keyboard = []

    # ➕ Add new question
    keyboard.append([InlineKeyboardButton("➕ Add from Question Bank", callback_data="QB_PICK_FOLDER")])

    # Question buttons (10 max)
    selected = context.user_data.get("selected_questions", set())

    for i, (qid, q) in enumerate(page_rows, start=start + 1):
        keyboard.append([
            InlineKeyboardButton(
                f"{i}. {q[:40]}",
                callback_data=f"Q_{qid}"
            )
        ])

    # Pagination
    pages = (total - 1) // QUESTIONS_PER_PAGE + 1
    if pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("◀️ Prev", callback_data="QPAGE_PREV"))
        nav.append(InlineKeyboardButton(f"{page + 1}/{pages}", callback_data="QPAGE_NOP"))
        if page < pages - 1:
            nav.append(InlineKeyboardButton("Next ▶️", callback_data="QPAGE_NEXT"))
        keyboard.append(nav)

    # Back button
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

    # 🔑 Load questions linked to this quiz FROM QUESTION BANK
    cur.execute(
        """
        SELECT qb.id, qb.question
        FROM quiz_question_links ql
        JOIN question_bank qb ON qb.id = ql.question_id
        WHERE ql.quiz_id=?
        ORDER BY ql.position, qb.question COLLATE NOCASE
        """,
        (quiz_id,)
    )
    rows = cur.fetchall()

    total = len(rows)
    start = page * QUESTIONS_PER_PAGE
    end = start + QUESTIONS_PER_PAGE
    page_rows = rows[start:end]

    keyboard = []

    # ➕ Add from Question Bank
    keyboard.append([
        InlineKeyboardButton(
            "➕ Add from Question Bank",
            callback_data="QB_PICK_FOLDER"
        )
    ])

    # Question list
    for i, (qid, q) in enumerate(page_rows, start=start + 1):
        keyboard.append([
            InlineKeyboardButton(
                f"{i}. {q[:40]}",
                callback_data=f"Q_{qid}"
            )
        ])

    # Pagination
    pages = (total - 1) // QUESTIONS_PER_PAGE + 1 if total else 1
    if pages > 1:
        nav = []
        if page > 0:
            nav.append(
                InlineKeyboardButton("◀️ Prev", callback_data="QPAGE_PREV")
            )
        nav.append(
            InlineKeyboardButton(f"{page + 1}/{pages}", callback_data="QPAGE_NOP")
        )
        if page < pages - 1:
            nav.append(
                InlineKeyboardButton("Next ▶️", callback_data="QPAGE_NEXT")
            )
        keyboard.append(nav)

    # Back
    keyboard.append([
        InlineKeyboardButton("⬅️ Back", callback_data="EDIT_THIS")
    ])

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

    chat_id = query.message.chat_id

    # 🧹 CLEAN previous creation prompts
    for mid in context.user_data.get("question_flow_msgs", []):
        try:
            await context.bot.delete_message(chat_id, mid)
        except:
            pass

    context.user_data["question_flow_msgs"] = []

    # Proceed to Option 1
    context.user_data["new_question"]["image"] = None
    context.user_data["add_q_state"] = "NEW_Q_OPTION_1"

    msg = await query.message.reply_text("➡️ Send option 1:")

    # Track message for future declutter
    context.user_data.setdefault("question_flow_msgs", []).append(msg.message_id)

async def skip_question_explanation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # Only valid during explanation step
    if context.user_data.get("add_q_state") != "NEW_Q_EXPLANATION":
        return

    context.user_data["new_question"]["explanation"] = None
    await save_new_question(query.message, context)

async def choose_correct_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    message = query.message  # 🔑 ALWAYS use this in callbacks

    # Extract index (0–3)
    correct_index = int(query.data.replace("CORRECT_", ""))

    context.user_data["new_question"]["correct"] = correct_index

    # Move to explanation step
    context.user_data["add_q_state"] = "NEW_Q_EXPLANATION"

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("⏭ Skip explanation", callback_data="SKIP_Q_EXPLANATION")]
    ])

    msg = await message.reply_text(
        "📝 Send explanation:",
        reply_markup=keyboard
    )

    # 🔑 Track explanation prompt
    context.user_data.setdefault("question_flow_msgs", []).append(msg.message_id)

async def save_new_question(message, context):
    q = context.user_data["new_question"]

    options_text = "||".join(q["options"])

    # 🔑 Resolve Question Bank folder (Default for now)
    cur.execute(
        """
        SELECT id
        FROM question_bank_folders
        WHERE owner_id=? AND name='Default'
        """,
        (OWNER_USER_ID,)
    )
    folder_row = cur.fetchone()
    folder_id = folder_row[0]

    # ✅ 1. INSERT INTO QUESTION BANK (SOURCE OF TRUTH)
    cur.execute(
        """
        INSERT INTO question_bank (
            folder_id,
            question,
            image_file_id,
            options,
            correct,
            explanation
        )
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

    question_id = cur.lastrowid

    conn.commit()

    # 🔄 Reset creation state
    context.user_data.pop("add_q_state", None)
    context.user_data.pop("new_question", None)

    # ✅ Send confirmation and track it
    confirm = await message.reply_text("✅ Question saved to Question Bank.")

    context.user_data.setdefault("question_flow_msgs", []).append(confirm.message_id)

    await asyncio.sleep(2)

    chat_id = message.chat_id

    # 🧹 DELETE ALL QUESTION FLOW MESSAGES
    for msg_id in context.user_data.get("question_flow_msgs", []):
        try:
            await context.bot.delete_message(chat_id, msg_id)
        except:
            pass

    # 🧹 Clear tracker
    context.user_data.pop("question_flow_msgs", None)

    # =========================
    # CONTEXT-AWARE RETURN
    # =========================

    # 🔹 CASE 1: Question was added while editing a quiz
    if context.user_data.get("active_quiz_id"):
        context.user_data["reset_q_page"] = True
        await show_questions_from_message(message, context)
        return

    # 🔹 CASE 2: Question created from HOME (Question Bank mode)
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Cancel", callback_data="CANCEL_CREATE_QUESTION")]
    ])

    context.user_data["question_flow_msgs"] = []
    context.user_data["add_q_state"] = "NEW_Q_TEXT"
    context.user_data["new_question"] = {"options": []}

    msg = await message.reply_text(
        "❓ Create a Question\n\n📝 Send question text:",
        reply_markup=keyboard
    )

    context.user_data["question_flow_msgs"].append(msg.message_id)

async def preview_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    chat_id = query.message.chat_id

    # 🔥 HARD CLEAN: delete previous preview if exists
    old_preview_id = context.user_data.get("question_preview_msg_id")
    if old_preview_id:
        try:
            await context.bot.delete_message(chat_id, old_preview_id)
        except:
            pass

    # Remove previous list/menu message
    try:
        await query.message.delete()
    except:
        pass

    qid = int(query.data.replace("Q_", ""))
    context.user_data["active_question_id"] = qid

    cur.execute(
        """
        SELECT question, image_file_id, options, correct, explanation
        FROM question_bank
        WHERE id=?
        """,
        (qid,)
    )
    row = cur.fetchone()
    if not row:
        await context.bot.send_message(chat_id, "❌ Question not found.")
        return

    question, image, options, correct, explanation = row
    options = options.split("||")

    text = f"📝 **{question}**\n\n"

    for i, opt in enumerate(options):
        marker = "✅" if i == correct else "◻️"
        text += f"{marker} {opt}\n"

    if explanation:
        text += f"\n🧾 _{explanation}_"

    preview_mode = context.user_data.get("preview_mode", "QUIZ")

    if preview_mode == "DATABASE":
        delete_callback = "DELETE_Q_FROM_DB"
    else:
        delete_callback = "DELETE_Q_FROM_QUIZ"

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✏️ Edit", callback_data="EDIT_Q"),
            InlineKeyboardButton("⚙️ Manage", callback_data="MANAGE_Q"),
        ],
        [
            InlineKeyboardButton("🗑 Delete", callback_data=delete_callback),
            InlineKeyboardButton("↩️ Return", callback_data="RETURN_TO_QUESTIONS"),
        ]
    ])

    # ✅ TRUE MEDIA LOGIC
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

async def rebuild_question_preview(chat_id, context):
    """
    Completely deletes old preview and rebuilds it
    based on current DB state.
    """

    qid = context.user_data.get("active_question_id")
    if not qid:
        return

    # 🔥 1️⃣ Delete old preview (if exists)
    old_preview_id = context.user_data.pop("question_preview_msg_id", None)
    if old_preview_id:
        try:
            await context.bot.delete_message(chat_id, old_preview_id)
        except:
            pass

    # 🔑 2️⃣ Load latest question data from DB
    cur.execute(
        """
        SELECT question, image_file_id, options, correct, explanation
        FROM question_bank
        WHERE id=?
        """,
        (qid,)
    )
    row = cur.fetchone()
    if not row:
        return

    question, image, options, correct, explanation = row
    options = options.split("||")

    # 📝 3️⃣ Build preview text
    text = f"📝 **{question}**\n\n"

    for i, opt in enumerate(options):
        marker = "✅" if i == correct else "◻️"
        text += f"{marker} {opt}\n"

    if explanation:
        text += f"\n🧾 _{explanation}_"

    # 🎛 4️⃣ Build preview buttons
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

    # 📨 5️⃣ Send correct message type
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

    # 💾 6️⃣ Store new preview message ID
    context.user_data["question_preview_msg_id"] = msg.message_id

async def show_question_preview_by_id(chat_id, context):
    """
    Safely rebuilds the question preview without requiring callback_query.
    Used after editing image.
    """

    qid = context.user_data.get("active_question_id")
    if not qid:
        return

    cur.execute(
        """
        SELECT question, image_file_id, options, correct, explanation
        FROM question_bank
        WHERE id=?
        """,
        (qid,)
    )

    row = cur.fetchone()
    if not row:
        return

    question, image, options, correct, explanation = row
    options = options.split("||")

    text = f"📝 **{question}**\n\n"

    for i, opt in enumerate(options):
        marker = "✅" if i == correct else "◻️"
        text += f"{marker} {opt}\n"

    if explanation:
        text += f"\n🧾 _{explanation}_"

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

    # 🧠 Build EDIT MENU buttons only
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📝 Question Text", callback_data="EDIT_Q_TEXT"),
            InlineKeyboardButton("🖼 Edit Image", callback_data="EDIT_Q_IMAGE"),
        ],
        [
            InlineKeyboardButton("🔁 Edit Choices", callback_data="EDIT_Q_OPTIONS"),
            InlineKeyboardButton("✅ Edit Answer", callback_data="EDIT_Q_CORRECT"),
        ],
        [
            InlineKeyboardButton("🧾 Explanation", callback_data="EDIT_Q_EXPLANATION"),
            InlineKeyboardButton("↩️ Return", callback_data="RETURN_TO_PREVIEW"),
        ],
    ])

    # ✅ Only replace inline buttons (NO deletion, NO resend)
    try:
        await query.message.edit_reply_markup(reply_markup=keyboard)
    except Exception as e:
        print("⚠️ Failed to replace edit menu:", e)

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
            cur.execute(
                "UPDATE question_bank SET image_file_id=NULL WHERE id=?",
                (qid,)
            )
            conn.commit()
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
    cur.execute(
        "SELECT options FROM question_bank WHERE id=?",
        (qid,)
    )
    row = cur.fetchone()
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
        f"1️⃣ {old_options[0]}\n"
        f"2️⃣ {old_options[1]}\n"
        f"3️⃣ {old_options[2]}\n"
        f"4️⃣ {old_options[3]}\n\n"
        "➡️ Send NEW option 1:",
        reply_markup=keyboard
    )

    # 🔑 Store this message for later full declutter
    context.user_data["edit_options_flow_msgs"].append(msg.message_id)

async def edit_question_correct_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # 🔒 Safety: ensure a Question Bank question is active
    qid = context.user_data.get("active_question_id")
    if not qid:
        await flash_message(context.bot, query.message.chat_id, "❌ No question selected.")
        return

    # 🔑 Load options + correct answer FROM QUESTION BANK
    cur.execute(
        "SELECT options, correct FROM question_bank WHERE id=?",
        (qid,)
    )
    row = cur.fetchone()
    if not row:
        await flash_message(context.bot, query.message.chat_id, "❌ Question not found.")
        return

    options_text, current_correct = row
    opts = options_text.split("||")

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"1️⃣ {opts[0]}", callback_data="EDIT_CORRECT_0")],
        [InlineKeyboardButton(f"2️⃣ {opts[1]}", callback_data="EDIT_CORRECT_1")],
        [InlineKeyboardButton(f"3️⃣ {opts[2]}", callback_data="EDIT_CORRECT_2")],
        [InlineKeyboardButton(f"4️⃣ {opts[3]}", callback_data="EDIT_CORRECT_3")],
    ])

    await query.message.reply_text(
        "✅ Choose the NEW correct answer:",
        reply_markup=keyboard
    )

async def edit_question_correct_apply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    qid = context.user_data.get("active_question_id")
    if not qid:
        await flash_message(context.bot, query.message.chat_id, "❌ No question selected.")
        return

    correct_index = int(query.data.replace("EDIT_CORRECT_", ""))

    # 🔐 SAFE WRITE SECTION
    try:
        async with DB_LOCK:
            cur.execute(
                "UPDATE question_bank SET correct=? WHERE id=?",
                (correct_index, qid)
            )
            conn.commit()
    except Exception as e:
        print("⚠️ Failed to update correct answer:", e)
        await query.answer("❌ Failed to update.", show_alert=True)
        return

    chat_id = query.message.chat_id

    # 🧹 Delete the "Choose correct answer" message
    try:
        await query.message.delete()
    except:
        pass

    # ✅ Show temporary confirmation
    confirm_msg = await context.bot.send_message(
        chat_id=chat_id,
        text="✅ Correct answer updated."
    )

    await asyncio.sleep(2)

    # 🧹 Delete confirmation message
    try:
        await confirm_msg.delete()
    except:
        pass

    # 🔄 Rebuild updated preview cleanly
    await rebuild_question_preview(chat_id, context)

async def edit_question_explanation_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    qid = context.user_data.get("active_question_id")
    if not qid:
        await flash_message(context.bot, query.message.chat_id, "❌ No question selected.")
        return

    # Load current explanation
    cur.execute(
        "SELECT explanation FROM question_bank WHERE id=?",
        (qid,)
    )
    row = cur.fetchone()
    current = row[0] if row and row[0] else "— none —"

    context.user_data["edit_q_field"] = "EXPLANATION"

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🗑 Remove Explanation", callback_data="EDIT_Q_EXPL_REMOVE"),
            InlineKeyboardButton("❌ Cancel", callback_data="CANCEL_EDIT_Q_EXPL"),
        ]
    ])

    msg = await query.message.reply_text(
        f"🧾 Current explanation:\n\n{current}\n\n"
        "✏️ Send new explanation text:",
        reply_markup=keyboard
    )

    # Track prompt message for cleanup
    context.user_data["edit_expl_prompt_id"] = msg.message_id

async def edit_question_explanation_remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    qid = context.user_data.get("active_question_id")
    if not qid:
        return

    chat_id = query.message.chat_id

    # Remove explanation from DB
    cur.execute(
        "UPDATE question_bank SET explanation=NULL WHERE id=?",
        (qid,)
    )
    conn.commit()

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

    # 📌 Parse answer
    chosen_index = int(query.data.replace("PLAY_ANSWER_", ""))
    q = play["questions"][play["index"]]
    correct_index = q["correct"]

    # ✅ Score update
    if chosen_index == correct_index:
        play["score"] += 1

    # 🎨 Build feedback buttons (NO explanation inline button)
    labels = ["A", "B", "C", "D"]

    buttons = [[
        InlineKeyboardButton(
            f"{labels[i]}"
            f"{' ✅' if i == correct_index else ' ❌' if i == chosen_index else ' ✖️'}",
            callback_data="LOCKED"
        )
        for i in range(len(q["options"]))
    ]]

    # 🔒 Finalize UI
    try:
        await query.message.edit_reply_markup(
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
            expl_msg = await context.bot.send_message(
                chat_id=query.from_user.id,
                text=f"📖 *Explanation:*\n\n{explanation}",
                parse_mode="Markdown"
            )

            # 🔥 CRITICAL:
            # Add explanation message to bulk cleanup list
            play.setdefault("question_message_ids", []).append(
                expl_msg.message_id
            )

        except:
            pass

    # ⏳ Wait 2 seconds before next question
    await asyncio.sleep(2)

    # 🔴 HARD ASYNC BOUNDARY
    await asyncio.sleep(0)

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

    # Load quiz settings
    cur.execute(
        "SELECT shuffle_q, shuffle_a FROM quizzes WHERE quiz_id=?",
        (quiz_id,)
    )
    row = cur.fetchone()
    shuffle_q, shuffle_a = row if row else (0, 0)

    # Load questions
    cur.execute(
        """
        SELECT
            qb.id,
            qb.question,
            qb.image_file_id,
            qb.options,
            qb.correct,
            qb.explanation
        FROM quiz_question_links ql
        JOIN question_bank qb ON qb.id = ql.question_id
        WHERE ql.quiz_id=?
        ORDER BY ql.position ASC, qb.question COLLATE NOCASE
        """,
        (quiz_id,)
    )
    rows = cur.fetchall()

    if not rows:
        await flash_message(context.bot, query.message.chat_id, "❌ This quiz has no questions.")
        return

    questions = []
    for qid, text, image, options, correct, explanation in rows:
        opts = options.split("||")
        if shuffle_a:
            import random
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
        import random
        random.shuffle(questions)

    # 🔑 CREATE PLAY SESSION
    context.user_data["play"] = {
        "questions": questions,
        "index": 0,
        "score": 0,
        "quiz_id": quiz_id,
        "user_name": format_user_name(query.from_user),

        "locked": False,
        "timer_task": None,
        "timer_message_ids": [],

        # 🧠 NEW: store quiz question message IDs for cleanup
        "question_message_ids": [],

        # 🔐 HARD ASYNC LOCK
        "context_lock": asyncio.Lock(),
    }

    user_id = query.from_user.id
    await send_next_question(user_id, context)

async def send_next_question(user_id, context):
    play = context.user_data.get("play")
    if not play:
        return

    # ⛔ Stop previous timer safely (from outside the task)
    old_task = play.get("timer_task")
    if old_task:
        old_task.cancel()
        play["timer_task"] = None

    quiz_id = play["quiz_id"]

    # ⏱ Load timer value
    cur.execute("SELECT timer FROM quizzes WHERE quiz_id=?", (quiz_id,))
    timer_seconds = cur.fetchone()[0]

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

    # 🔘 Inline buttons
    keyboard = [[
        InlineKeyboardButton(labels[i], callback_data=f"PLAY_ANSWER_{i}")
        for i in range(len(q["options"]))
    ]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    # 📨 Send question (image-safe)
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

    # 🔑 AUTHORITATIVE question message (used by timer + answer)
    play["current_question_message_id"] = msg.message_id

    # 🧹 Keep history for cleanup
    play.setdefault("question_message_ids", [])
    play["question_message_ids"].append(msg.message_id)

    play["locked"] = False

    # 🕒 Send timer message
    timer_msg = await context.bot.send_message(
        chat_id=user_id,
        text=f"⏱ Time left: {timer_seconds}s"
    )
    play.setdefault("timer_message_ids", [])
    play["timer_message_ids"].append(timer_msg.message_id)

    # ⏳ Start timer task
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
                expl_msg = await context.bot.send_message(
                    chat_id=user_id,
                    text=f"📖 *Explanation:*\n\n{explanation}",
                    parse_mode="Markdown"
                )

                # 🔥 Add to bulk cleanup list
                play.setdefault("question_message_ids", []).append(
                    expl_msg.message_id
                )

            except:
                pass

        # ⏳ Wait 2 seconds before advancing
        await asyncio.sleep(2)

        # =========================
        # ▶️ Advance Safely
        # =========================
        if play["index"] >= len(play["questions"]) - 1:
            await finish_quiz(user_id, context)
        else:
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
    cur.execute("""
        SELECT username, score
        FROM leaderboard
        WHERE quiz_id=? AND chat_id=?
        ORDER BY score DESC
        LIMIT 10
    """, (quiz_id, chat_id))

    rows = cur.fetchall()

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

async def send_quiz_to_group(chat_id, quiz_id, context, token):
    # =========================
    # BUILD LEADERBOARD KEY
    # =========================
    leaderboard_key = make_leaderboard_key(quiz_id, token)

    # =========================
    # LOAD QUIZ INFO
    # =========================
    cur.execute("""
        SELECT title, description, timer, shuffle_q, shuffle_a
        FROM quizzes
        WHERE quiz_id=?
    """, (quiz_id,))
    title, desc, timer, sq, sa = cur.fetchone()

    cur.execute(
        "SELECT COUNT(*) FROM quiz_question_links WHERE quiz_id=?",
        (quiz_id,)
    )
    total_questions = cur.fetchone()[0]

    # =========================
    # BUILD MESSAGE TEXT
    # =========================
    text = f"📘 *{title}*\n"

    if desc:
        text += f"📝 _{desc}_\n"

    # Spacer line (visual breathing room)
    text += "\n"

    # Quiz stats
    text += f"🧠 *{total_questions} Questions* • ⏱ *{timer}s*\n"

    # Shuffle settings
    text += (
        f"🔀 Questions: {'ON' if sq else 'OFF'} • "
        f"Answers: {'ON' if sa else 'OFF'}\n\n"
    )

    # Leaderboard header
    text += (
        "🏆 *Leaderboard*\n"
        "— No attempts yet —"
    )

    # =========================
    # START BUTTON (TOKEN-BOUND)
    # =========================
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "▶️ Start Quiz",
                url=f"https://t.me/{BOT_USERNAME}?start=PLAY_{quiz_id}_{token}"
            )
        ]
    ])

    # =========================
    # SEND MESSAGE TO GROUP
    # =========================
    msg = await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        reply_markup=keyboard,
        parse_mode="Markdown"
    )

    # =========================
    # REGISTER LEADERBOARD STATE (SINGLE SOURCE OF TRUTH)
    # =========================
    GROUP_LB_MESSAGES[leaderboard_key] = {
        "quiz_id": quiz_id,
        "token": token,
        "chat_id": chat_id,
        "message_id": msg.message_id,
        "page": 0,
    }

    GROUP_LEADERBOARDS[leaderboard_key] = {}

def build_group_quiz_text(leaderboard_key, page=0):
    # 🔑 Split leaderboard key
    try:
        quiz_id, _ = leaderboard_key.split(":", 1)
    except ValueError:
        return "❌ Invalid leaderboard.", 0
    # Load quiz info
    cur.execute(
        "SELECT title, description, timer, shuffle_q, shuffle_a FROM quizzes WHERE quiz_id=?",
        (quiz_id,)
    )
    title, desc, timer, sq, sa = cur.fetchone()

    cur.execute(
        "SELECT COUNT(*) FROM quiz_question_links WHERE quiz_id=?",
        (quiz_id,)
    )
    total_questions = cur.fetchone()[0]

    # =========================
    # BUILD QUIZ PREVIEW (ADMIN-LIKE FORMAT)
    # =========================
    text = f"📘 *{title}*\n"

    if desc:
        text += f"📝 _{desc}_\n"

    # Spacer
    text += "\n"

    # Quiz stats
    text += f"🧠 *{total_questions} Questions* • ⏱ *{timer}s*\n"

    # Shuffle settings
    text += (
        f"🔀 Questions: {'ON' if sq else 'OFF'} • "
        f"Answers: {'ON' if sa else 'OFF'}\n\n"
    )

    # =========================
    # LEADERBOARD SECTION
    # =========================
    text += "🏆 *Leaderboard*\n"

    # 🔄 HYBRID MODE: Use memory if available, otherwise load from DB
    if leaderboard_key in GROUP_LEADERBOARDS and GROUP_LEADERBOARDS[leaderboard_key]:
        leaderboard = [
            {
                "user_id": uid,
                "name": data["name"],
                "score": data["score"]
            }
            for uid, data in GROUP_LEADERBOARDS[leaderboard_key].items()
        ]
    else:
        # 🔁 Load from DB
        cur.execute("""
            SELECT user_id, name, score
            FROM group_leaderboard
            WHERE leaderboard_key=?
        """, (leaderboard_key,))
        rows = cur.fetchall()

        leaderboard = []

        # 🔄 Rebuild memory from DB
        GROUP_LEADERBOARDS.setdefault(leaderboard_key, {})

        for user_id, name, score in rows:
            GROUP_LEADERBOARDS[leaderboard_key][user_id] = {
                "name": name,
                "score": score
            }
            leaderboard.append({
                "user_id": user_id,
                "name": name,
                "score": score
            })

    if not leaderboard:
        text += "_No attempts yet_\n"
        return text, 0

    # Sort by score (highest first)
    leaderboard.sort(key=lambda x: x["score"], reverse=True)

    per_page = 5
    pages = (len(leaderboard) - 1) // per_page + 1
    page = max(0, min(page, pages - 1))

    start = page * per_page
    end = start + per_page

    medals = {
        1: "🥇",
        2: "🥈",
        3: "🥉"
    }

    for i, user in enumerate(leaderboard[start:end], start=start + 1):
        prefix = medals.get(i, f"{i}.")
        label = f"{prefix} {user['name']} — {user['score']}"

        text += label + "\n"

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

    buttons = []

    # ◀ ▶ Pagination
    if pages > 1:
        nav = []
        if page > 0:
            nav.append(
                InlineKeyboardButton("◀ Prev", callback_data=f"LB_PREV|{leaderboard_key}")
            )
        nav.append(
            InlineKeyboardButton(f"{page+1}/{pages}", callback_data="LB_NOP")
        )
        if page < pages - 1:
            nav.append(
                InlineKeyboardButton("Next ▶", callback_data=f"LB_NEXT|{leaderboard_key}")
            )
        buttons.append(nav)

    # ▶️ Start Quiz button (reconstructed safely)
    buttons.append([
        InlineKeyboardButton(
            "▶️ Start this Quiz",
            url=f"https://t.me/{BOT_USERNAME}?start=PLAY_{quiz_id}_{token}"
        )
    ])

    try:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            reply_markup=InlineKeyboardMarkup(buttons),
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
                    # Delete leaderboard rows
                    cur.execute(
                        "DELETE FROM group_leaderboard WHERE leaderboard_key=?",
                        (leaderboard_key,)
                    )

                    # Delete post token (prevents future PLAY)
                    quiz_id, token = leaderboard_key.split(":", 1)

                    cur.execute(
                        "DELETE FROM quiz_post_tokens WHERE token=? AND quiz_id=?",
                        (token, quiz_id)
                    )

                    conn.commit()

            except Exception as db_error:
                print("⚠️ Failed to clean DB leaderboard:", db_error)

        else:
            # Other harmless errors
            print("⚠️ Failed to edit leaderboard message:", e)

async def post_quiz_to_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    quiz_id = context.user_data.get("active_quiz_id")
    if not quiz_id:
        await flash_message(context.bot, query.message.chat_id, "❌ No quiz selected.")
        return

    # 🔑 Generate a unique token every time
    token = secrets.token_urlsafe(8)
    timestamp = int(time.time())

    # 💾 Save token safely (WRITE LOCK)
    try:
        async with DB_LOCK:
            cur.execute(
                """
                INSERT INTO quiz_post_tokens (token, quiz_id, owner_id, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (token, quiz_id, OWNER_USER_ID, timestamp)
            )
            conn.commit()
    except Exception as e:
        print("⚠️ Failed to save post token:", e)
        await flash_message(context.bot, query.message.chat_id, "❌ Failed to generate post link.")
        return

    # 📤 Send unique post command to admin
    msg = await query.message.reply_text(
        f"/post {quiz_id}_{token}"
    )

    # ⏳ Auto-delete the message after 5 seconds
    async def delete_later():
        await asyncio.sleep(5)
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

    payload = args[0]

    # =========================
    # PARSE: /post <quiz_id>_<token>
    # =========================
    try:
        quiz_id, token = payload.rsplit("_", 1)
    except ValueError:
        await update.message.reply_text("❌ Invalid post command format.")
        return

    # =========================
    # OWNER-ONLY PROTECTION
    # =========================
    if user_id != OWNER_USER_ID:
        warn_msg = await update.message.reply_text(
            "❌ Only the bot owner can post quizzes."
        )

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

    # =========================
    # TOKEN VALIDATION
    # =========================
    cur.execute(
        """
        SELECT token
        FROM quiz_post_tokens
        WHERE token=? AND quiz_id=?
        """,
        (token, quiz_id)
    )
    row = cur.fetchone()

    if not row:
        warn_msg = await update.message.reply_text(
            "❌ This quiz post command is invalid or expired."
        )

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

    # =========================
    # POST QUIZ TO GROUP
    # =========================
    await send_quiz_to_group(chat.id, quiz_id, context, token)

    # =========================
    # MARK TOKEN AS USED FOR POSTING (OPTIONAL)
    # =========================
    # ⚠️ DO NOT DELETE TOKEN — it is still needed for PLAY links
    # If you want to prevent re-posting, add a `used_for_post` column later
    # 🧹 Clean up the /post command message in group
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

    # 🔑 Load all quizzes owned by user
    cur.execute(
        "SELECT quiz_id, title FROM quizzes WHERE owner_id=? ORDER BY title",
        (OWNER_USER_ID,)
    )
    quizzes = cur.fetchall()

    # 🔑 Filter quizzes where question is NOT already linked
    available = []
    for quiz_id, title in quizzes:
        if quiz_id == source_quiz_id:
            continue

        cur.execute(
            """
            SELECT 1
            FROM quiz_question_links
            WHERE quiz_id=? AND question_id=?
            """,
            (quiz_id, qid)
        )
        if cur.fetchone():
            continue

        available.append((quiz_id, title))

    if not available:
        await flash_message(context.bot, query.message.chat_id, "ℹ️ This question is already linked to all quizzes.")
        return

    # Pagination
    PER_PAGE = 5
    pages = (len(available) - 1) // PER_PAGE + 1
    page = max(0, min(page, pages - 1))

    start = page * PER_PAGE
    end = start + PER_PAGE

    keyboard = []

    for quiz_id, title in available[start:end]:
        keyboard.append([
            InlineKeyboardButton(
                f"📘 {title}",
                callback_data=f"COPY_TO|{quiz_id}"
            )
        ])

    if pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("◀ Prev", callback_data="COPY_Q_PREV"))
        nav.append(InlineKeyboardButton(f"{page+1}/{pages}", callback_data="COPY_Q_NOP"))
        if page < pages - 1:
            nav.append(InlineKeyboardButton("Next ▶", callback_data="COPY_Q_NEXT"))
        keyboard.append(nav)

    keyboard.append([
        InlineKeyboardButton("⬅️ Cancel", callback_data="EDIT_QUESTIONS")
    ])

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
    cur.execute(
        """
        SELECT 1
        FROM quiz_question_links
        WHERE quiz_id=? AND question_id=?
        """,
        (target_quiz_id, qid)
    )
    if cur.fetchone():
        await flash_message(context.bot, query.message.chat_id, "ℹ️ Question already exists in this quiz.")
        return

    # 🔑 Insert link ONLY (no duplication)
    cur.execute(
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
    conn.commit()

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
                cur.execute(
                    """
                    DELETE FROM quiz_question_links
                    WHERE quiz_id=? AND question_id=?
                    """,
                    (quiz_id, qid)
                )
                conn.commit()
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
                cur.execute(
                    "DELETE FROM quiz_question_links WHERE quiz_id=?",
                    (quiz_id,)
                )
                cur.execute(
                    "DELETE FROM quizzes WHERE quiz_id=?",
                    (quiz_id,)
                )
                conn.commit()
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

                cur.execute(
                    "SELECT quiz_id FROM quizzes WHERE folder=?",
                    (folder,)
                )
                quiz_ids = [row[0] for row in cur.fetchall()]

                for qid in quiz_ids:
                    cur.execute(
                        "DELETE FROM quiz_question_links WHERE quiz_id=?",
                        (qid,)
                    )

                cur.execute(
                    "DELETE FROM quizzes WHERE folder=?",
                    (folder,)
                )

                cur.execute(
                    "DELETE FROM folders WHERE name=?",
                    (folder,)
                )

                conn.commit()

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

    # 🔒 Ensure finish logic runs ONCE
    if play.get("finish_sent"):
        return
    play["finish_sent"] = True

    quiz_id = play["quiz_id"]
    score = play["score"]
    total = len(play["questions"])

    # ⛔ Stop timers and lock state FIRST
    play["locked"] = True

    task = play.get("timer_task")
    current = asyncio.current_task()
    if task and task is not current:
        task.cancel()
    play["timer_task"] = None

    # =========================
    # 🏆 GROUP LEADERBOARD (HYBRID + SAFE WRITE)
    # =========================
    leaderboard_key = context.user_data.get("leaderboard_key")

    if leaderboard_key:
        lb_info = GROUP_LB_MESSAGES.get(leaderboard_key)

        if lb_info:
            GROUP_LEADERBOARDS.setdefault(leaderboard_key, {})

            # ✅ FIRST ATTEMPT ONLY
            if user_id not in GROUP_LEADERBOARDS[leaderboard_key]:

                # 1️⃣ Save to MEMORY
                GROUP_LEADERBOARDS[leaderboard_key][user_id] = {
                    "name": play["user_name"],
                    "score": score,
                }

                # 2️⃣ Save to DATABASE safely
                try:
                    async with DB_LOCK:
                        cur.execute("""
                            INSERT OR IGNORE INTO group_leaderboard
                            (leaderboard_key, user_id, name, score)
                            VALUES (?, ?, ?, ?)
                        """, (
                            leaderboard_key,
                            user_id,
                            play["user_name"],
                            score
                        ))
                        conn.commit()
                except Exception as e:
                    print("⚠️ Failed to save leaderboard:", e)

                # 3️⃣ Update group message
                try:
                    await update_group_leaderboard(leaderboard_key, context)
                except Exception as e:
                    print("⚠️ Leaderboard update failed:", e)

    # =========================
    # 🧹 INSTANT BULK DELETE
    # =========================

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

    # =========================
    # 📘 LOAD QUIZ META (READ ONLY – NO LOCK NEEDED)
    # =========================
    cur.execute(
        "SELECT title, timer FROM quizzes WHERE quiz_id=?",
        (quiz_id,)
    )
    row = cur.fetchone()
    title, timer = row if row else ("Quiz", 0)

    # =========================
    # 🏁 FINAL SCORE MESSAGE
    # =========================
    buttons = [
        [
            InlineKeyboardButton("🔁 Start Again", callback_data="PLAY_START"),
            InlineKeyboardButton("🗑 Delete", callback_data="DELETE_FINISH_MSG"),
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
        # 🚫 Prevent double-advance
        if play.get("finished"):
            return

        play["index"] += 1

        # 🏁 END OF QUIZ
        if play["index"] >= len(play["questions"]):
            play["finished"] = True
            await finish_quiz(user_id, context)
            return

        # ▶️ NEXT QUESTION
        await send_next_question(user_id, context)

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
    cur.execute(
        """
        INSERT OR IGNORE INTO question_bank_folders (owner_id, name)
        VALUES (?, 'Default')
        """,
        (OWNER_USER_ID,)
    )
    conn.commit()

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

    # Load Question Bank folders
    cur.execute(
        """
        SELECT id, name
        FROM question_bank_folders
        WHERE owner_id=?
        ORDER BY name COLLATE NOCASE
        """,
        (OWNER_USER_ID,)
    )
    folders = cur.fetchall()

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

    # Pagination controls
    if pages > 1:
        nav = []
        if page > 0:
            nav.append(
                InlineKeyboardButton("◀ Prev", callback_data="QB_MOVE_PREV")
            )
        nav.append(
            InlineKeyboardButton(f"{page + 1}/{pages}", callback_data="QB_MOVE_NOP")
        )
        if page < pages - 1:
            nav.append(
                InlineKeyboardButton("Next ▶", callback_data="QB_MOVE_NEXT")
            )
        keyboard.append(nav)

    # Back button
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
            cur.execute(
                "UPDATE question_bank SET folder_id=? WHERE id=?",
                (folder_id, qid)
            )
            conn.commit()
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

    # =========================
    # 🔑 Determine folder name
    # =========================
    if query.data.startswith("QB_OPEN_FOLDER|"):
        folder_name = query.data.split("|", 1)[1]
        context.user_data["qb_folder_name"] = folder_name
        context.user_data["qb_q_page"] = 0  # reset page on new folder
        context.user_data.setdefault("qb_selected", set())
    else:
        folder_name = context.user_data.get("qb_folder_name")

    if not folder_name:
        await flash_message(
            context.bot,
            query.message.chat_id,
            "❌ Folder context lost. Please reopen the folder."
        )
        return

    # =========================
    # 📁 Check if folder exists
    # =========================
    cur.execute(
        """
        SELECT id
        FROM question_bank_folders
        WHERE owner_id=? AND name=?
        """,
        (OWNER_USER_ID, folder_name)
    )
    row = cur.fetchone()

    if not row:
        await flash_message(
            context.bot,
            query.message.chat_id,
            "❌ Folder not found."
        )
        return

    folder_id = row[0]

    # =========================
    # 📚 Check if folder has questions
    # =========================
    cur.execute(
        """
        SELECT COUNT(*)
        FROM question_bank
        WHERE folder_id=?
        """,
        (folder_id,)
    )
    count = cur.fetchone()[0]

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

    # ======================================================
    # 🔥 SINGLE SOURCE OF TRUTH FOR KEYBOARD
    # ======================================================
    reply_markup = build_qb_question_keyboard(context)

    await query.message.edit_text(
        f"📁 **{folder_name}**\n\nSelect questions:",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )

async def qb_select_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # Safety checks
    quiz_id = context.user_data.get("active_quiz_id")
    if not quiz_id:
        await flash_message(context.bot, query.message.chat_id, "❌ No active quiz.")
        return

    qid = int(query.data.split("|", 1)[1])

    # ❌ Prevent duplicate link (read only — no lock needed)
    cur.execute(
        """
        SELECT 1
        FROM quiz_question_links
        WHERE quiz_id=? AND question_id=?
        """,
        (quiz_id, qid)
    )
    if cur.fetchone():
        await flash_message(context.bot, query.message.chat_id, 
            "ℹ️ This question is already in the quiz."
        )
        return

    # 🔐 WRITE SECTION (LOCKED)
    try:
        async with DB_LOCK:

            # 🔢 Determine next position safely
            cur.execute(
                """
                SELECT COALESCE(MAX(position), 0) + 1
                FROM quiz_question_links
                WHERE quiz_id=?
                """,
                (quiz_id,)
            )
            position = cur.fetchone()[0]

            # 🔑 LINK question to quiz
            cur.execute(
                """
                INSERT INTO quiz_question_links (quiz_id, question_id, position)
                VALUES (?, ?, ?)
                """,
                (quiz_id, qid, position)
            )

            conn.commit()

    except Exception as e:
        print("⚠️ Failed to link question:", e)
        await flash_message(context.bot, query.message.chat_id, "❌ Failed to add question.")
        return

    await query.answer("✅ Question added.")

    # 🔁 Reset pagination and return to quiz questions
    context.user_data["reset_q_page"] = True
    await show_questions_from_message(query.message, context)

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

    # Delete the prompt message
    prompt_id = context.user_data.pop("edit_text_prompt_id", None)
    if prompt_id:
        try:
            await context.bot.delete_message(chat_id, prompt_id)
        except:
            pass

    # Exit edit mode safely
    context.user_data.pop("edit_q_field", None)

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
                    cur.execute("""
                        INSERT OR IGNORE INTO group_leaderboard
                        (leaderboard_key, user_id, name, score)
                        VALUES (?, ?, ?, ?)
                    """, (
                        leaderboard_key,
                        user_id,
                        play["user_name"],
                        play["score"]
                    ))
                    conn.commit()
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
    # 🔐 Global Rate Limit
    if is_rate_limited(query.from_user.id):
        raise ApplicationHandlerStop
    if not query:
        return

    data = query.data or ""
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

    msg = await query.message.reply_text(
        "⚠️ A quiz is currently running.\n\n"
        "Please stop or resume the quiz to continue. ⚠️ Stopping the quiz after 3 Questions will update the Score Leaderboard",
        reply_markup=keyboard
    )

    # 🔒 Lock UI with warning message ID
    play["warning_message_id"] = msg.message_id

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
    """
    Builds the Question Bank question list keyboard
    WITHOUT sending a message.
    Used for checkbox toggle UI updates.
    """

    # =========================
    # Context & pagination
    # =========================
    folder_name = context.user_data.get("qb_folder_name")
    page = context.user_data.get("qb_q_page", 0)
    selected = context.user_data.setdefault("qb_selected", set())
    quiz_id = context.user_data.get("active_quiz_id")

    PER_PAGE = 10

    # =========================
    # Resolve folder_id
    # =========================
    cur.execute(
        """
        SELECT id
        FROM question_bank_folders
        WHERE owner_id=? AND name=?
        """,
        (OWNER_USER_ID, folder_name)
    )
    row = cur.fetchone()
    if not row:
        return InlineKeyboardMarkup([])

    folder_id = row[0]

    # =========================
    # Load all questions in folder
    # =========================
    cur.execute(
        """
        SELECT id, question
        FROM question_bank
        WHERE folder_id=?
        ORDER BY question COLLATE NOCASE
        """,
        (folder_id,)
    )
    questions = cur.fetchall()

    # =========================
    # Load already linked questions
    # =========================
    cur.execute(
        "SELECT question_id FROM quiz_question_links WHERE quiz_id=?",
        (quiz_id,)
    )
    linked_questions = {row[0] for row in cur.fetchall()}

    total = len(questions)
    pages = (total - 1) // PER_PAGE + 1 if total else 1
    page = max(0, min(page, pages - 1))
    context.user_data["qb_q_page"] = page

    start = page * PER_PAGE
    end = start + PER_PAGE
    page_items = questions[start:end]

    keyboard = []

    # ======================================================
    # 🎲 ALWAYS SHOW AUTO ADD BUTTONS (TOP SECTION)
    # ======================================================
    keyboard.append([
        InlineKeyboardButton("🎲 Add 10", callback_data="QB_AUTO_ADD|10"),
        InlineKeyboardButton("🎲 Add 50", callback_data="QB_AUTO_ADD|50"),
        InlineKeyboardButton("🎲 Add 100", callback_data="QB_AUTO_ADD|100"),
    ])

    # ======================================================
    # ❓ Question buttons
    # ======================================================
    for qid, text in page_items:

        already_added = qid in linked_questions

        if already_added:
            label = f"✅ {text[:45]}"
            callback = f"QB_REMOVE_Q|{qid}"
        else:
            checked = "☑" if qid in selected else "⬜"
            label = f"{checked} {text[:45]}"
            callback = f"QB_SELECT_Q|{qid}"

        keyboard.append([
            InlineKeyboardButton(label, callback_data=callback)
        ])

    # ======================================================
    # 🧹 Clear Selection + ➕ Add Selected (SAME ROW)
    # ======================================================
    keyboard.append([
        InlineKeyboardButton(
            "🧹 Clear Selection",
            callback_data="QB_CLEAR_SELECTED"
        ),
        InlineKeyboardButton(
            f"➕ Add Selected ({len(selected)})",
            callback_data="QB_ADD_SELECTED"
        )
    ])

    # ======================================================
    # Pagination
    # ======================================================
    if pages > 1:
        nav = []
        if page > 0:
            nav.append(
                InlineKeyboardButton("◀ Prev", callback_data="QB_Q_PREV")
            )
        nav.append(
            InlineKeyboardButton(f"{page + 1}/{pages}", callback_data="QB_Q_NOP")
        )
        if page < pages - 1:
            nav.append(
                InlineKeyboardButton("Next ▶", callback_data="QB_Q_NEXT")
            )
        keyboard.append(nav)

    # ======================================================
    # Back button
    # ======================================================
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

    quiz_id = context.user_data.get("active_quiz_id")
    selected = context.user_data.get("qb_selected", set())

    if not quiz_id or not selected:
        await flash_message(context.bot, query.message.chat_id, "❌ No questions selected.")
        return

    added = 0

    try:
        async with DB_LOCK:

            # 🔢 Get next position in quiz (inside lock)
            cur.execute(
                """
                SELECT COALESCE(MAX(position), 0)
                FROM quiz_question_links
                WHERE quiz_id=?
                """,
                (quiz_id,)
            )
            position = cur.fetchone()[0]

            for qid in selected:

                # ❌ Skip duplicates safely
                cur.execute(
                    """
                    SELECT 1
                    FROM quiz_question_links
                    WHERE quiz_id=? AND question_id=?
                    """,
                    (quiz_id, qid)
                )
                if cur.fetchone():
                    continue

                position += 1

                cur.execute(
                    """
                    INSERT INTO quiz_question_links (quiz_id, question_id, position)
                    VALUES (?, ?, ?)
                    """,
                    (quiz_id, qid, position)
                )

                added += 1

            conn.commit()

    except Exception as e:
        print("⚠️ Failed to add selected questions:", e)
        await flash_message(context.bot, query.message.chat_id, "❌ Failed to add questions.")
        return

    # 🧹 Clear selection state (UI state only)
    context.user_data["qb_selected"] = set()
    context.user_data.pop("qb_q_page", None)

    # ✅ Confirmation
    await flash_message(context.bot, query.message.chat_id, 
        f"✅ {added} question(s) added to the quiz."
    )

    # 🔁 Return to quiz question list
    context.user_data["reset_q_page"] = True
    await show_questions_from_message(query.message, context)

async def qb_remove_question_from_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    qid = int(query.data.split("|")[1])
    quiz_id = context.user_data.get("active_quiz_id")

    if not quiz_id:
        return

    # 🔐 WRITE SECTION (LOCKED)
    try:
        async with DB_LOCK:
            cur.execute(
                """
                DELETE FROM quiz_question_links
                WHERE quiz_id=? AND question_id=?
                """,
                (quiz_id, qid)
            )
            conn.commit()
    except Exception as e:
        print("⚠️ Failed to remove question:", e)
        await query.answer("❌ Failed to remove question.", show_alert=True)
        return

    # Update the SAME message (no re-send)
    reply_markup = build_qb_question_keyboard(context)
    await query.edit_message_reply_markup(reply_markup=reply_markup)

async def qb_auto_add_questions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # How many to select (10 / 50 / 100)
    limit = int(query.data.split("|")[1])

    quiz_id = context.user_data.get("active_quiz_id")
    folder_name = context.user_data.get("qb_folder_name")

    if not quiz_id or not folder_name:
        return

    # =========================
    # Resolve folder_id
    # =========================
    cur.execute(
        """
        SELECT id
        FROM question_bank_folders
        WHERE owner_id=? AND name=?
        """,
        (OWNER_USER_ID, folder_name)
    )
    row = cur.fetchone()
    if not row:
        return

    folder_id = row[0]

    # =========================
    # Load ALL questions in folder
    # =========================
    cur.execute(
        """
        SELECT id
        FROM question_bank
        WHERE folder_id=?
        """,
        (folder_id,)
    )
    all_questions = {row[0] for row in cur.fetchall()}

    # =========================
    # Load already linked questions
    # =========================
    cur.execute(
        """
        SELECT question_id
        FROM quiz_question_links
        WHERE quiz_id=?
        """,
        (quiz_id,)
    )
    already_linked = {row[0] for row in cur.fetchall()}

    # =========================
    # Selection state (SINGLE SOURCE OF TRUTH)
    # =========================
    selected = context.user_data.setdefault("qb_selected", set())

    # Candidates = not linked AND not already selected
    candidates = list(all_questions - already_linked - selected)

    if not candidates:
        return

    import random
    random.shuffle(candidates)

    # 🔥 Add to selection set instead of inserting into DB
    to_select = candidates[:limit]
    selected.update(to_select)

    # 🔁 Rebuild keyboard only (no full reload)
    reply_markup = build_qb_question_keyboard(context)
    await query.edit_message_reply_markup(reply_markup=reply_markup)

async def show_quiz_action_menu_by_id(chat_id, message_id, context):
    quiz_id = context.user_data.get("active_quiz_id")
    if not quiz_id:
        return

    cur.execute("""
        SELECT q.title, q.description, q.timer, q.shuffle_q, q.shuffle_a,
               COUNT(ql.question_id)
        FROM quizzes q
        LEFT JOIN quiz_question_links ql
            ON q.quiz_id = ql.quiz_id
        WHERE q.quiz_id=?
        GROUP BY q.quiz_id
    """, (quiz_id,))

    title, desc, timer, sq, sa, total_questions = cur.fetchone()

    text = f"📘 **{title}**"
    if desc:
        text += f"\n📝 _{desc}_"

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

    # Delete image edit menu message
    menu_id = context.user_data.pop("edit_image_menu_msg_id", None)
    if menu_id:
        try:
            await context.bot.delete_message(chat_id, menu_id)
        except:
            pass

    # Exit image edit mode
    context.user_data.pop("edit_q_field", None)

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
            cur.execute(
                """
                UPDATE question_bank
                SET options=?, correct=?
                WHERE id=?
                """,
                (options_text, correct_index, qid)
            )
            conn.commit()
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
        except:
            pass

    context.user_data.pop("edit_options_flow_msgs", None)
    context.user_data.pop("edit_options", None)
    context.user_data.pop("edit_q_field", None)

async def clear_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    # Track /clear message itself
    context.user_data.setdefault("chat_messages", []).append(update.message.message_id)

    message_ids = context.user_data.get("chat_messages", [])

    # Delete all stored messages
    for msg_id in message_ids:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
        except:
            pass

    # Clear memory safely
    context.user_data.clear()

async def cancel_edit_question_explanation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    chat_id = query.message.chat_id

    # Delete prompt
    prompt_id = context.user_data.pop("edit_expl_prompt_id", None)
    if prompt_id:
        try:
            await context.bot.delete_message(chat_id, prompt_id)
        except:
            pass

    context.user_data.pop("edit_q_field", None)

async def back_to_questions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    chat_id = query.message.chat_id

    # 🔹 Delete the preview message (photo or text)
    try:
        await query.message.delete()
    except:
        pass

    # 🔹 Clean stored preview ID
    context.user_data.pop("question_preview_msg_id", None)
    context.user_data.pop("preview_mode", None)

    # Reset pagination
    context.user_data["reset_q_page"] = True

    # 🔹 Send new placeholder message
    new_msg = await context.bot.send_message(
        chat_id=chat_id,
        text="Loading..."
    )

    # 🔹 Build quiz question list on that message
    await show_questions_from_message(new_msg, context)

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

    # 🔑 Load folders (Default first)
    cur.execute("""
        SELECT name
        FROM folders
        WHERE owner_id=?
        ORDER BY
            CASE WHEN name='Default' THEN 0 ELSE 1 END,
            name COLLATE NOCASE
    """, (OWNER_USER_ID,))

    folders = [row[0] for row in cur.fetchall()]

    total = len(folders)
    pages = (total - 1) // PER_PAGE + 1 if total else 1
    page = max(0, min(page, pages - 1))
    context.user_data["mc_folder_page"] = page

    start = page * PER_PAGE
    end = start + PER_PAGE
    page_items = folders[start:end]

    keyboard = []

    for folder_name in page_items:
        # 🔢 Count quizzes inside folder
        cur.execute("""
            SELECT COUNT(*)
            FROM quizzes
            WHERE owner_id=? AND folder=?
        """, (OWNER_USER_ID, folder_name))

        count = cur.fetchone()[0]

        keyboard.append([
            InlineKeyboardButton(
                f"📁 {folder_name} ({count})",
                callback_data=f"MC_FOLDER|{folder_name}"
            )
        ])

    # 🔄 Pagination
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

            # COPY or MOVE logic
            if mode == "MOVE":
                # Remove from current quiz
                cur.execute(
                    "DELETE FROM quiz_question_links WHERE quiz_id=? AND question_id=?",
                    (current_quiz_id, qid)
                )

            # Check if already exists in target
            cur.execute(
                "SELECT 1 FROM quiz_question_links WHERE quiz_id=? AND question_id=?",
                (target_quiz_id, qid)
            )

            if not cur.fetchone():

                # 🔢 Get next position safely
                cur.execute(
                    """
                    SELECT COALESCE(MAX(position), 0) + 1
                    FROM quiz_question_links
                    WHERE quiz_id=?
                    """,
                    (target_quiz_id,)
                )
                next_position = cur.fetchone()[0]

                # Insert into target quiz
                cur.execute(
                    """
                    INSERT INTO quiz_question_links (quiz_id, question_id, position)
                    VALUES (?, ?, ?)
                    """,
                    (target_quiz_id, qid, next_position)
                )

            conn.commit()

    except Exception as e:
        print("⚠️ Failed to move/copy question:", e)
        await query.answer("❌ Operation failed.", show_alert=True)
        return

    confirm_msg = await flash_message(context.bot, query.message.chat_id, "✅ Operation completed.")

    await asyncio.sleep(2)

    # 🧹 Delete folder/quiz list message
    try:
        await query.message.delete()
    except:
        pass

    # 🧹 Delete confirmation
    try:
        await confirm_msg.delete()
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
    page = context.user_data.get("mc_quiz_page", 0)
    folder = context.user_data.get("mc_folder")

    cur.execute("""
        SELECT quiz_id, title
        FROM quizzes
        WHERE owner_id=? AND folder=?
        ORDER BY title COLLATE NOCASE
    """, (OWNER_USER_ID, folder))

    quizzes = cur.fetchall()

    total = len(quizzes)
    pages = (total - 1) // PER_PAGE + 1 if total else 1
    page = max(0, min(page, pages - 1))
    context.user_data["mc_quiz_page"] = page

    start = page * PER_PAGE
    end = start + PER_PAGE
    page_items = quizzes[start:end]

    keyboard = []

    for quiz_id, title in page_items:
        keyboard.append([
            InlineKeyboardButton(
                f"📘 {title}",
                callback_data=f"MC_APPLY|{quiz_id}"
            )
        ])

    # 🔄 Pagination
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
    import time
    now = time.time()
    last = USER_RATE_LIMIT.get(user_id, 0)

    if now - last < RATE_LIMIT_SECONDS:
        return True

    USER_RATE_LIMIT[user_id] = now
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

    # Rebuild preview safely (handles image/text correctly)
    await rebuild_question_preview(query.message.chat_id, context)

async def manage_question_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # Safety check
    qid = context.user_data.get("active_question_id")
    if not qid:
        await flash_message(context.bot, query.message.chat_id, "❌ No question selected.")
        return

    # Reset pagination
    context.user_data["manage_folder_page"] = 0

    await show_manage_folders(query.message, context)

async def show_manage_folders(message, context):
    PER_PAGE = 5
    page = context.user_data.get("manage_folder_page", 0)

    # Load folders (Default first, alphabetical)
    cur.execute("""
        SELECT name
        FROM folders
        WHERE owner_id=?
        ORDER BY
            CASE WHEN name='Default' THEN 0 ELSE 1 END,
            name COLLATE NOCASE
    """, (OWNER_USER_ID,))

    folders = [row[0] for row in cur.fetchall()]

    total = len(folders)
    pages = (total - 1) // PER_PAGE + 1 if total else 1
    page = max(0, min(page, pages - 1))
    context.user_data["manage_folder_page"] = page

    start = page * PER_PAGE
    end = start + PER_PAGE
    page_items = folders[start:end]

    keyboard = []

    for folder in page_items:

        # Count quizzes inside folder
        cur.execute("""
            SELECT COUNT(*)
            FROM quizzes
            WHERE owner_id=? AND folder=?
        """, (OWNER_USER_ID, folder))

        count = cur.fetchone()[0]

        keyboard.append([
            InlineKeyboardButton(
                f"📁 {folder} ({count})",
                callback_data=f"MANAGE_FOLDER|{folder}"
            )
        ])

    # Pagination
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
    page = context.user_data.get("manage_quiz_page", 0)
    folder = context.user_data.get("manage_folder")
    qid = context.user_data.get("active_question_id")

    # Load quizzes in folder
    cur.execute("""
        SELECT quiz_id, title
        FROM quizzes
        WHERE owner_id=? AND folder=?
        ORDER BY title COLLATE NOCASE
    """, (OWNER_USER_ID, folder))

    quizzes = cur.fetchall()

    # Load quizzes already linked to this question
    cur.execute("""
        SELECT quiz_id
        FROM quiz_question_links
        WHERE question_id=?
    """, (qid,))

    linked = {row[0] for row in cur.fetchall()}

    total = len(quizzes)
    pages = (total - 1) // PER_PAGE + 1 if total else 1
    page = max(0, min(page, pages - 1))
    context.user_data["manage_quiz_page"] = page

    start = page * PER_PAGE
    end = start + PER_PAGE
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

    # Pagination
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
    qid = context.user_data.get("active_question_id")

    # Check if already linked
    cur.execute("""
        SELECT 1 FROM quiz_question_links
        WHERE quiz_id=? AND question_id=?
    """, (quiz_id, qid))

    if cur.fetchone():
        # Remove link
        cur.execute("""
            DELETE FROM quiz_question_links
            WHERE quiz_id=? AND question_id=?
        """, (quiz_id, qid))
    else:
        # Add link
        cur.execute("""
            SELECT COALESCE(MAX(position), 0) + 1
            FROM quiz_question_links
            WHERE quiz_id=?
        """, (quiz_id,))
        position = cur.fetchone()[0]

        cur.execute("""
            INSERT INTO quiz_question_links (quiz_id, question_id, position)
            VALUES (?, ?, ?)
        """, (quiz_id, qid, position))

    conn.commit()

    # Refresh same message
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

    # 1️⃣ Clear manual selection
    context.user_data["qb_selected"] = set()

    # 2️⃣ Unlink all questions from THIS quiz only
    try:
        async with DB_LOCK:
            cur.execute(
                "DELETE FROM quiz_question_links WHERE quiz_id=?",
                (quiz_id,)
            )
            conn.commit()
    except Exception as e:
        print("⚠️ Failed to unlink questions:", e)
        await query.answer("❌ Failed to clear quiz.", show_alert=True)
        return

    # 3️⃣ Rebuild keyboard
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

    try:
        async with DB_LOCK:
            # Remove from all quizzes first
            cur.execute(
                "DELETE FROM quiz_question_links WHERE question_id=?",
                (qid,)
            )

            # Then remove from question bank
            cur.execute(
                "DELETE FROM question_bank WHERE id=?",
                (qid,)
            )

            conn.commit()

    except Exception as e:
        print("⚠️ Failed to permanently delete question:", e)
        await query.answer("❌ Failed to delete.", show_alert=True)
        return

    # Close preview
    try:
        await query.message.delete()
    except:
        pass

    context.user_data.pop("active_question_id", None)

    # Refresh database list
    await show_db_questions(update, context)

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
# HANDLERS
# =========================
# load_owner_from_db()
ensure_default_folder()
ensure_default_qb_folder()
ensure_indexes()

app = ApplicationBuilder().token(BOT_TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("clear", clear_chat))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
app.add_handler(CommandHandler("post", post_quiz_command))
# =========================
# Must Stay on Top of other CallbackQueryHandler
# =========================
app.add_handler(CallbackQueryHandler(global_quiz_guard), group=-1)
# =========================
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
app.add_handler(CallbackQueryHandler(cancel_edit_question_options,pattern="^CANCEL_EDIT_Q_OPTIONS$"))
app.add_handler(CallbackQueryHandler(apply_new_options_correct,pattern="^EDIT_OPT_CORRECT_"))
app.add_handler(CallbackQueryHandler(cancel_edit_question_text, pattern="^CANCEL_EDIT_Q_TEXT$"))
app.add_handler(CallbackQueryHandler(delete_finish_message, pattern="^DELETE_FINISH_MSG$"))
app.add_handler(CallbackQueryHandler(qb_auto_add_questions, pattern=r"^QB_AUTO_ADD\|"))
app.add_handler(CallbackQueryHandler(qb_remove_question_from_quiz,pattern=r"^QB_REMOVE_Q\|"))
app.add_handler(CallbackQueryHandler(qb_add_selected_questions, pattern="^QB_ADD_SELECTED$"))
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
app.add_handler(CallbackQueryHandler(cancel_create_question, pattern="^CANCEL_CREATE_QUESTION$"))
app.add_handler(CallbackQueryHandler(qb_move_question, pattern="^QB_MOVE$"))
app.add_handler(CallbackQueryHandler(qb_move_apply, pattern="^QB_MOVE_TO\\|"))
app.add_handler(CallbackQueryHandler(qb_move_prev, pattern="^QB_MOVE_PREV$"))
app.add_handler(CallbackQueryHandler(qb_move_next, pattern="^QB_MOVE_NEXT$"))
app.add_handler(CallbackQueryHandler(quiz_folder_prev, pattern="^QUIZ_FOLDER_PREV$"))
app.add_handler(CallbackQueryHandler(quiz_folder_next, pattern="^QUIZ_FOLDER_NEXT$"))
app.add_handler(CallbackQueryHandler(qb_open_folder, pattern="^QB_OPEN_FOLDER\\|"))
app.add_handler(CallbackQueryHandler(show_db_questions, pattern="^DB_OPEN\\|"))
app.add_handler(CallbackQueryHandler(qb_pick_folder_start, pattern="^QB_PICK_FOLDER$"))
app.add_handler(CallbackQueryHandler(qb_folder_prev, pattern="^QB_FOLDER_PREV$"))
app.add_handler(CallbackQueryHandler(qb_folder_next, pattern="^QB_FOLDER_NEXT$"))
app.add_handler(CallbackQueryHandler(home_create_question, pattern="^HOME_CREATE_QUESTION$"))
app.add_handler(CallbackQueryHandler(home_manage_subscribers, pattern="^HOME_MANAGE_SUBSCRIBERS$"))
app.add_handler(CallbackQueryHandler(database_add_folder_start, pattern="^DB_ADD$"))
app.add_handler(CallbackQueryHandler(confirm_delete, pattern="^CONFIRM_DELETE$"))
app.add_handler(CallbackQueryHandler(cancel_delete, pattern="^CANCEL_DELETE$"))
app.add_handler(CallbackQueryHandler(copy_question_apply, pattern="^COPY_TO\\|"))
app.add_handler(CallbackQueryHandler(folder_prev, pattern="^FOLDER_PREV\\|"))
app.add_handler(CallbackQueryHandler(folder_next, pattern="^FOLDER_NEXT\\|"))
app.add_handler(CallbackQueryHandler(home_database, pattern="^HOME_DATABASE$"))
app.add_handler(CallbackQueryHandler(database_prev, pattern="^DB_PREV$"))
app.add_handler(CallbackQueryHandler(database_next, pattern="^DB_NEXT$"))
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
app.run_polling()
####################################################################################################################################################################################################################################
# CODE BY PARTS - END OF CODE
####################################################################################################################################################################################################################################
