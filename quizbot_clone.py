# quizbot_clone.py
# FULL STABLE VERSION – TIMER & SHUFFLE FIXED
# All Edit buttons now open real menus

import uuid
import sqlite3
import os
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

# =========================
# CONFIG
# =========================
BOT_TOKEN = os.environ.get("BOT_TOKEN")

if not BOT_TOKEN:
    raise RuntimeError("❌ BOT_TOKEN environment variable is missing")

OWNER_USER_ID = 6254422846
BOT_USERNAME = "EucresiaBot"
DB_FILE = os.path.join(os.getcwd(), "quizbot.db")
QUESTIONS_PER_PAGE = 10

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
CREATE TABLE IF NOT EXISTS questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    quiz_id TEXT,
    question TEXT,
    image_file_id TEXT,
    options TEXT,
    correct INTEGER,
    explanation TEXT
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

# =========================
# UI
# =========================

# =========================
# START
# =========================

# 🟢 GROUP QUIZ POST DETECTION
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_type = update.effective_chat.type

# 🎮 PLAY MODE (deep link)
    if context.args and context.args[0].startswith("PLAY_"):
        quiz_id = context.args[0].replace("PLAY_", "")

        context.user_data.clear()
        context.user_data["play_quiz_id"] = quiz_id
        context.user_data["group_chat_id"] = update.effective_chat.id

        await update.message.reply_text(
            "🎮 Quiz ready!\n\nPress the button below to start.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("▶️ Start Quiz", callback_data="PLAY_START")]
            ])
        )
        return

    # ❌ Block /start inside groups & channels
    if chat_type in ("group", "supergroup", "channel"):
        return

    # ❌ Block /start inside groups
    if chat_type in ("group", "supergroup"):
        return

    # 🔒 Private chat but NOT owner
    if user_id != OWNER_USER_ID:
        await update.message.reply_text(
            "👋 Hi!\n\nPlease open a quiz from a group to start answering.\nYou don’t have access to the admin panel."
        )
        return

    # ✅ OWNER — show admin home
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📂 Quiz Folder", callback_data="HOME_MY_QUIZZES"),
            InlineKeyboardButton("➕ Create a new Quiz", callback_data="HOME_CREATE"),
        ]
    ])

    await update.message.reply_text(
        "🧠 **Welcome to Quiz Bot (Admin Panel)**\n\nChoose an option:",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )

# =========================
# CREATE QUIZ
# =========================
async def create_quiz(update_or_message, context: ContextTypes.DEFAULT_TYPE):
    global OWNER_USER_ID

    # ✅ CORRECT HANDLING FOR BUTTON OR MESSAGE
    if isinstance(update_or_message, Update):
        # Called from a normal message (/start or text)
        user_id = update_or_message.effective_user.id
        message = update_or_message.message
    else:
        # Called from inline button (CallbackQuery)
        query = update_or_message
        user_id = query.from_user.id
        message = query.message

    # 🔑 AUTO-SET OWNER
    #if OWNER_USER_ID is None:
    #   OWNER_USER_ID = None

    # 🔒 OWNER-ONLY CHECK
    if user_id != OWNER_USER_ID:
        await message.reply_text("❌ Only the bot owner can create quizzes.")
        return

    context.user_data.clear()
    context.user_data["quiz_id"] = str(uuid.uuid4())
    context.user_data["state"] = "WAIT_TITLE"

    await message.reply_text("📝 Send quiz title:")


# =========================
# TEXT HANDLER
# =========================
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    
    # ================= EDIT QUESTION IMAGE =================
    if context.user_data.get("edit_q_field") == "IMAGE":
        photo = update.message.photo[-1]
        file_id = photo.file_id

        qid = context.user_data["active_question_id"]

        cur.execute(
            "UPDATE questions SET image_file_id=? WHERE id=?",
            (file_id, qid)
        )
        conn.commit()

        context.user_data.pop("edit_q_field", None)

        await update.message.reply_text("✅ Image updated.")
        await show_questions_from_message(update.message, context)
        return

    q_state = context.user_data.get("add_q_state")

    # Only accept photos during image step
    if q_state != "NEW_Q_IMAGE":
        return

    photo = update.message.photo[-1]  # highest resolution
    file_id = photo.file_id

    # Save image file_id
    context.user_data["new_question"]["image"] = file_id

    # Move to option 1
    context.user_data["add_q_state"] = "NEW_Q_OPTION_1"

    await update.message.reply_text("➡️ Send option 1:")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = context.user_data.get("state")
    text = update.message.text.strip()

    # ================= ADD QUESTION FLOW =================
    q_state = context.user_data.get("add_q_state")

    # ================= EDIT QUESTION EXPLANATION =================
    if context.user_data.get("edit_q_field") == "EXPLANATION":
        qid = context.user_data["active_question_id"]

        cur.execute(
            "UPDATE questions SET explanation=? WHERE id=?",
            (text, qid)
        )
        conn.commit()

        context.user_data.pop("edit_q_field", None)

        await update.message.reply_text("✅ Explanation updated.")
        await show_questions_from_message(update.message, context)
        return

    # ================= EDIT QUESTION TEXT =================
    edit_field = context.user_data.get("edit_q_field")

    if edit_field == "TEXT":
        qid = context.user_data["active_question_id"]

        cur.execute(
            "UPDATE questions SET question=? WHERE id=?",
            (text, qid)
        )
        conn.commit()

        context.user_data.pop("edit_q_field", None)

        await update.message.reply_text("✅ Question text updated.")
        await show_questions_from_message(update.message, context)
        return

    # 📝 Question text
    if q_state == "NEW_Q_TEXT":
        context.user_data["new_question"]["text"] = text
        context.user_data["add_q_state"] = "NEW_Q_IMAGE"

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("⏭ Skip image", callback_data="SKIP_Q_IMAGE")]
        ])

        await update.message.reply_text(
            "🖼 Send image for this question:",
            reply_markup=keyboard
        )
        return

       # ================= OPTIONS FLOW =================

    # ➡️ Option 1
    if q_state == "NEW_Q_OPTION_1":
        context.user_data["new_question"]["options"].append(text)
        context.user_data["add_q_state"] = "NEW_Q_OPTION_2"
        await update.message.reply_text("➡️ Send option 2:")
        return

    # ➡️ Option 2
    if q_state == "NEW_Q_OPTION_2":
        context.user_data["new_question"]["options"].append(text)
        context.user_data["add_q_state"] = "NEW_Q_OPTION_3"
        await update.message.reply_text("➡️ Send option 3:")
        return

    # ➡️ Option 3
    if q_state == "NEW_Q_OPTION_3":
        context.user_data["new_question"]["options"].append(text)
        context.user_data["add_q_state"] = "NEW_Q_OPTION_4"
        await update.message.reply_text("➡️ Send option 4:")
        return

    # ➡️ Option 4
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

        await update.message.reply_text(
            "✅ Choose the correct answer:",
            reply_markup=keyboard
        )
        return

    # ================= EDIT QUESTION OPTIONS =================
    if context.user_data.get("edit_q_field") == "OPTIONS":
        opts = context.user_data["edit_options"]
        opts.append(text)

        if len(opts) < 4:
            await update.message.reply_text(f"➡️ Send NEW option {len(opts) + 1}:")
            return

        # We now have 4 options
        qid = context.user_data["active_question_id"]
        options_text = "||".join(opts)

        cur.execute(
            "UPDATE questions SET options=? WHERE id=?",
            (options_text, qid)
        )
        conn.commit()

        context.user_data.pop("edit_q_field", None)
        context.user_data.pop("edit_options", None)

        await update.message.reply_text("✅ Options updated.")
        await show_questions_from_message(update.message, context)
        return

    # ================= EXPLANATION =================
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

        quiz_id = context.user_data["active_quiz_id"]
        cur.execute(
            "UPDATE quizzes SET folder=? WHERE quiz_id=? AND owner_id=?",
            (folder, quiz_id, OWNER_USER_ID)
        )
        conn.commit()

        context.user_data["state"] = None
        await update.message.reply_text(f"✅ Folder '{folder}' created and quiz moved.")
        await show_quiz_action_menu(update.message, context)
        return

    # ================= ADD EMPTY FOLDER =================
    if state == "ADD_FOLDER":
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
        conn.commit()

        context.user_data["state"] = None
        await update.message.reply_text(f"✅ Folder '{folder}' created.")
        await my_quizzes(update, context)
        return

    # ================= RENAME FOLDER =================
    if state == "RENAME_FOLDER":
        old = context.user_data["rename_folder"]
        new = text

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

        context.user_data["state"] = None
        context.user_data.pop("rename_folder", None)

        await update.message.reply_text(
            f"✅ Folder renamed to **{new}**.",
            parse_mode="Markdown"
        )

        await show_quiz_folders(update.message, context)
        return

    # ================= CREATE QUIZ =================
    if state == "WAIT_TITLE":
        cur.execute(
            "INSERT INTO quizzes VALUES (?, ?, ?, NULL, ?, 1, 1, 15)",
            (
                context.user_data["quiz_id"],
                OWNER_USER_ID,
                text,
                context.user_data.get("current_folder", "Default")
            )
        )
        conn.commit()
    
        # 🔑 SET ACTIVE QUIZ (IMPORTANT)
        context.user_data["active_quiz_id"] = context.user_data["quiz_id"]
        context.user_data["state"] = None
    
        await update.message.reply_text("✅ Quiz created.")
     
        # 🚀 AUTO-OPEN QUIZ ACTION MENU
        await show_quiz_action_menu(update.message, context)
        return

    # ================= EDIT TITLE =================
    if state == "EDIT_TITLE":
        quiz_id = context.user_data["active_quiz_id"]
        cur.execute("UPDATE quizzes SET title=? WHERE quiz_id=?", (text, quiz_id))
        conn.commit()
        context.user_data["state"] = None
        await update.message.reply_text("✅ Title updated.")
        await show_quiz_action_menu(update.message, context)
        return

    # ================= EDIT DESCRIPTION =================
    if state == "EDIT_DESC":
        quiz_id = context.user_data["active_quiz_id"]
        if text.upper() == "CLEAR":
            cur.execute("UPDATE quizzes SET description=NULL WHERE quiz_id=?", (quiz_id,))
        else:
            cur.execute("UPDATE quizzes SET description=? WHERE quiz_id=?", (text, quiz_id))
        conn.commit()
        context.user_data["state"] = None
        await update.message.reply_text("✅ Description updated.")
        await show_quiz_action_menu(update.message, context)
        return

# =========================
# DELETE QUESTION
# =========================
async def delete_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    qid = context.user_data.get("active_question_id")
    if not qid:
        await query.message.reply_text("❌ No question selected.")
        return

    # Save delete request
    context.user_data["confirm_delete"] = ("QUESTION", qid)

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
        await query.message.reply_text("❌ No quiz selected.")
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
    cur.execute("""
        SELECT name
        FROM folders
        WHERE owner_id=?
    """, (OWNER_USER_ID,))

    rows = [row[0] for row in cur.fetchall()]

    # 🔑 Separate Default folder
    default_folder = "Default"
    other_folders = sorted([f for f in rows if f != default_folder])

    keyboard = []

    # 📁 DEFAULT FOLDER (ALWAYS ON TOP)
    cur.execute(
        "SELECT COUNT(*) FROM quizzes WHERE owner_id=? AND folder=?",
        (OWNER_USER_ID, default_folder)
    )
    count = cur.fetchone()[0]

    keyboard.append([
        InlineKeyboardButton(
            f"📁 Default Folder ({count})",
            callback_data=f"OPEN_FOLDER|{default_folder}"
        )
    ])

    # 📁 OTHER FOLDERS (ALPHABETICAL)
    for folder in other_folders:
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

    keyboard.append([
        InlineKeyboardButton("➕ Add Folder", callback_data="ADD_FOLDER"),
        InlineKeyboardButton("🏠 Home", callback_data="GO_HOME")
    ])

    await message.reply_text(
        "📂 Your quiz folders:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# =========================
# MY QUIZZES
# =========================

async def show_quizzes_in_folder(message, context, folder):
    cur.execute(
        "SELECT quiz_id, title FROM quizzes WHERE owner_id=? AND folder=?",
        (OWNER_USER_ID, folder)
    )
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
            InlineKeyboardButton("✏️ Rename Folder", callback_data=f"RENAME_FOLDER|{folder}"),
            InlineKeyboardButton("🗑 Delete Folder", callback_data=f"DELETE_FOLDER|{folder}"),
            InlineKeyboardButton("⬅️ Back", callback_data="BACK_TO_FOLDERS")
        ])
    else:
        keyboard.append([
            InlineKeyboardButton("⬅️ Back", callback_data="BACK_TO_FOLDERS")
        ])

    title = "All Quizzes" if folder == "Default" else folder

    await message.reply_text(
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

    await query.message.reply_text(
        f"✏️ Send new name for folder:\n\n📁 {folder}"
    )

async def add_folder_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    context.user_data["state"] = "ADD_FOLDER"

    await query.message.reply_text(
        "➕ Send the new folder name:"
    )

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
        await query.message.reply_text("❌ Default folder cannot be deleted.")
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

    quiz_id = context.user_data["active_quiz_id"]

    cur.execute("""
        SELECT name
        FROM folders
        WHERE owner_id=?
        ORDER BY name
    """, (OWNER_USER_ID,))

    folders = [row[0] for row in cur.fetchall()]

    keyboard = []
    for folder in folders:
        keyboard.append([
            InlineKeyboardButton(
                f"📁 {folder}",
                callback_data=f"MOVE_QUIZ_TO|{folder}"
            )
        ])
    keyboard.append([
        InlineKeyboardButton("➕ Create new folder", callback_data="MOVE_CREATE_FOLDER")
    ])

    keyboard.append([
        InlineKeyboardButton("⬅️ Back", callback_data="BACK_TO_ACTION")
    ])

    await query.message.reply_text(
        "📁 Move quiz to folder:",
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

async def move_quiz_to_folder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    folder = query.data.split("|", 1)[1]
    quiz_id = context.user_data["active_quiz_id"]

    cur.execute(
        "UPDATE quizzes SET folder=? WHERE quiz_id=? AND owner_id=?",
        (folder, quiz_id, OWNER_USER_ID)
    )
    conn.commit()

    await query.message.reply_text(
        f"✅ Quiz moved to 📁 {folder}"
    )

    await show_quiz_action_menu(query.message, context)

async def show_quiz_action_menu(message, context):
    quiz_id = context.user_data["active_quiz_id"]
    cur.execute(
        "SELECT title, description, timer, shuffle_q, shuffle_a FROM quizzes WHERE quiz_id=?",
        (quiz_id,)
    )
    title, desc, timer, sq, sa = cur.fetchone()

    cur.execute(
        "SELECT COUNT(*) FROM questions WHERE quiz_id=?",
        (quiz_id,)
    )
    total_questions = cur.fetchone()[0]

    text = f"📘 **{title}**"
    if desc:
        text += f"\n\n_{desc}_"
    text += f"\n\n📊 Questions: {total_questions}"
    text += f"\n⏱ Timer: {timer}s"
    text += f"\n🔀 Shuffle Questions: {'ON' if sq else 'OFF'}"
    text += f"\n🔀 Shuffle Options: {'ON' if sa else 'OFF'}"
    
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

    await message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

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

    quiz_id = context.user_data["active_quiz_id"]
    cur.execute(
        "SELECT title, description, timer, shuffle_q, shuffle_a FROM quizzes WHERE quiz_id=?",
        (quiz_id,)
    )
    title, desc, timer, sq, sa = cur.fetchone()

    text = f"📘 **{title}**"
    if desc:
        text += f"\n\n_{desc}_"
    text += f"\n\n⏱ Timer: {timer}s"
    text += f"\n🔀 Shuffle Questions: {'ON' if sq else 'OFF'}"
    text += f"\n🔀 Shuffle Options: {'ON' if sa else 'OFF'}"

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

    await query.message.reply_text(
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
    await query.message.reply_text(
        "📝 Send new title:",
        reply_markup=InlineKeyboardMarkup([
            cancel_edit_button()
        ])
    )

async def edit_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["state"] = "EDIT_DESC"
    await query.message.reply_text(
        "🧾 Send Quiz description:",
        reply_markup=InlineKeyboardMarkup([
            cancel_edit_button()
        ])
    )

# =========================
# ⏱ TIMER MENU (REAL)
# =========================
async def edit_timer_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    keyboard = [
        [InlineKeyboardButton("15 seconds", callback_data="SET_TIMER_15")],
        [InlineKeyboardButton("30 seconds", callback_data="SET_TIMER_30")],
        [InlineKeyboardButton("45 seconds", callback_data="SET_TIMER_45")],
        [InlineKeyboardButton("1 minute", callback_data="SET_TIMER_60")],
        [InlineKeyboardButton("3 minutes", callback_data="SET_TIMER_180")],
        [InlineKeyboardButton("5 minutes", callback_data="SET_TIMER_300")],
    ]

    keyboard.append([
        InlineKeyboardButton("⬅️ Back", callback_data="BACK_TO_EDIT_MENU")
    ])

    await query.message.reply_text(
        "⏱ Choose timer:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def set_timer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    seconds = int(query.data.replace("SET_TIMER_", ""))
    quiz_id = context.user_data["active_quiz_id"]

    cur.execute("UPDATE quizzes SET timer=? WHERE quiz_id=?", (seconds, quiz_id))
    conn.commit()
    await query.message.reply_text(f"✅ Timer set to {seconds}s.")
    await show_quiz_action_menu(query.message, context)

# =========================
# 🔀 SHUFFLE MENU (REAL)
# =========================
async def edit_shuffle_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    quiz_id = context.user_data["active_quiz_id"]
    cur.execute("SELECT shuffle_q, shuffle_a FROM quizzes WHERE quiz_id=?", (quiz_id,))
    sq, sa = cur.fetchone()

    keyboard = [
        [InlineKeyboardButton(
            f"Shuffle Questions: {'ON' if sq else 'OFF'}",
            callback_data="TOGGLE_Q"
        )],
        [InlineKeyboardButton(
            f"Shuffle Options: {'ON' if sa else 'OFF'}",
            callback_data="TOGGLE_A"
        )],
    ]

    keyboard.append([
        InlineKeyboardButton("⬅️ Back", callback_data="BACK_TO_EDIT_MENU")
    ])

    await query.message.reply_text(
        "🔀 Shuffle settings:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def toggle_shuffle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    quiz_id = context.user_data["active_quiz_id"]
    if query.data == "TOGGLE_Q":
        cur.execute("UPDATE quizzes SET shuffle_q = 1 - shuffle_q WHERE quiz_id=?", (quiz_id,))
    else:
        cur.execute("UPDATE quizzes SET shuffle_a = 1 - shuffle_a WHERE quiz_id=?", (quiz_id,))
    conn.commit()

    await show_quiz_action_menu(query.message, context)

# =========================
# NAVIGATION
# =========================
async def go_home(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📂 Quiz Folder", callback_data="HOME_MY_QUIZZES"),
            InlineKeyboardButton("➕ Create a new Quiz", callback_data="HOME_CREATE"),
        ]
    ])

    await query.message.reply_text(
        "🏠 Home",
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

    # Reuse existing logic
    await create_quiz(query, context)

async def back_to_edit_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    context.user_data["state"] = None
    await edit_menu(update, context)

async def home_my_quizzes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # Reuse existing logic
    await my_quizzes(query.message, context)

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
    await query.message.reply_text("🚧 Coming next.")


# =========================
# SHOW QUESTIONS (STEP 7.1)
# =========================
async def show_questions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    context.user_data.setdefault("selected_questions", set())

    quiz_id = context.user_data["active_quiz_id"]
    if "q_page" not in context.user_data:
        context.user_data["q_page"] = 0
    page = context.user_data["q_page"]

    # 🔁 Always start from page 1 when entering
    if context.user_data.get("reset_q_page", True):
        context.user_data["q_page"] = 0
        context.user_data["reset_q_page"] = False

    cur.execute(
        "SELECT id, question FROM questions WHERE quiz_id=? ORDER BY question COLLATE NOCASE",
        (quiz_id,)
    )

    rows = cur.fetchall()

    total = len(rows)
    start = page * QUESTIONS_PER_PAGE
    end = start + QUESTIONS_PER_PAGE
    page_rows = rows[start:end]

    keyboard = []

    # ➕ Add new question
    keyboard.append([InlineKeyboardButton("➕ Add new question", callback_data="ADD_QUESTION")])

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

    await query.message.reply_text(
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
    quiz_id = context.user_data["active_quiz_id"]
    page = context.user_data.get("q_page", 0)

    cur.execute(
        "SELECT id, question FROM questions WHERE quiz_id=? ORDER BY question COLLATE NOCASE",
        (quiz_id,)
    )

    rows = cur.fetchall()

    total = len(rows)
    start = page * QUESTIONS_PER_PAGE
    end = start + QUESTIONS_PER_PAGE
    page_rows = rows[start:end]

    keyboard = []

    keyboard.append([
        InlineKeyboardButton("➕ Add new question", callback_data="ADD_QUESTION")
    ])

    for i, (qid, q) in enumerate(page_rows, start=start + 1):
        keyboard.append([
            InlineKeyboardButton(f"{i}. {q[:40]}", callback_data=f"Q_{qid}")
        ])

    pages = (total - 1) // QUESTIONS_PER_PAGE + 1
    if pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("◀️ Prev", callback_data="QPAGE_PREV"))
        nav.append(InlineKeyboardButton(f"{page + 1}/{pages}", callback_data="QPAGE_NOP"))
        if page < pages - 1:
            nav.append(InlineKeyboardButton("Next ▶️", callback_data="QPAGE_NEXT"))
        keyboard.append(nav)

    keyboard.append([
        InlineKeyboardButton("⬅️ Back", callback_data="EDIT_THIS")
    ])

    await message.reply_text(
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

    context.user_data["new_question"]["image"] = None
    context.user_data["add_q_state"] = "NEW_Q_OPTION_1"

    await query.message.reply_text("➡️ Send option 1:")

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

    # Extract index (0–3)
    correct_index = int(query.data.replace("CORRECT_", ""))

    context.user_data["new_question"]["correct"] = correct_index

    # Move to explanation step
    context.user_data["add_q_state"] = "NEW_Q_EXPLANATION"

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("⏭ Skip explanation", callback_data="SKIP_Q_EXPLANATION")]
    ])

    await query.message.reply_text(
        "🧾 Send explanation (optional):",
        reply_markup=keyboard
    )

async def save_new_question(message, context):
    quiz_id = context.user_data["active_quiz_id"]
    q = context.user_data["new_question"]

    options_text = "||".join(q["options"])

    cur.execute("""
        INSERT INTO questions (
            quiz_id,
            question,
            image_file_id,
            options,
            correct,
            explanation
        )
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        quiz_id,
        q["text"],
        q.get("image"),
        options_text,
        q["correct"],
        q.get("explanation")
    ))

    conn.commit()

    # Reset question state
    context.user_data.pop("add_q_state", None)
    context.user_data.pop("new_question", None)

    await message.reply_text("✅ Question saved.")
    await show_questions_from_message(message, context)

async def preview_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    qid = int(query.data.replace("Q_", ""))
    context.user_data["active_question_id"] = qid

    cur.execute("""
        SELECT question, image_file_id, options, correct, explanation
        FROM questions
        WHERE id=?
    """, (qid,))

    row = cur.fetchone()
    if not row:
        await query.message.reply_text("❌ Question not found.")
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
            InlineKeyboardButton("✏️ Edit Question", callback_data="EDIT_Q"),
            InlineKeyboardButton("📋 Copy Question", callback_data="COPY_Q"),
        ],
        [
            InlineKeyboardButton("🗑 Delete Question", callback_data="DELETE_QUESTION"),
            InlineKeyboardButton("⬅️ Back", callback_data="EDIT_QUESTIONS"),
        ],
    ])

    if image:
        await query.message.reply_photo(
            photo=image,
            caption=text,
            reply_markup=keyboard,
            parse_mode="Markdown"
        )
    else:
        await query.message.reply_text(
            text,
            reply_markup=keyboard,
            parse_mode="Markdown"
        )

async def edit_question_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    keyboard = [
        [
            InlineKeyboardButton("📝 Edit question text", callback_data="EDIT_Q_TEXT"),
            InlineKeyboardButton("🖼 Change / remove image", callback_data="EDIT_Q_IMAGE"),
        ],
        [
            InlineKeyboardButton("🔁 Edit choices / options", callback_data="EDIT_Q_OPTIONS"),
            InlineKeyboardButton("✅ Change correct answer", callback_data="EDIT_Q_CORRECT"),
        ],
        [
            InlineKeyboardButton("🧾 Edit explanation", callback_data="EDIT_Q_EXPLANATION"),
            InlineKeyboardButton("⬅️ Back to Question Options", callback_data="BACK_TO_Q_OPTIONS"),
        ],
    ]

    await query.message.reply_text(
        "✏️ **Edit Question**",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

async def back_to_question_options(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    await question_action_menu(update, context)

async def edit_question_text_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # Mark that we are editing question text
    context.user_data["edit_q_field"] = "TEXT"

    await query.message.reply_text("📝 Send new question text:")

async def edit_question_image_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # Mark edit mode
    context.user_data["edit_q_field"] = "IMAGE"

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🖼 Send new image", callback_data="EDIT_Q_IMAGE_SEND")],
        [InlineKeyboardButton("🗑 Remove image", callback_data="EDIT_Q_IMAGE_REMOVE")],
        [InlineKeyboardButton("⬅️ Back", callback_data="EDIT_Q_BACK")]
    ])

    await query.message.reply_text(
        "🖼 Change or remove question image:",
        reply_markup=keyboard
    )

async def remove_question_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    qid = context.user_data["active_question_id"]

    cur.execute(
        "UPDATE questions SET image_file_id=NULL WHERE id=?",
        (qid,)
    )
    conn.commit()

    context.user_data.pop("edit_q_field", None)

    await query.message.reply_text("🗑 Image removed.")
    await show_questions_from_message(query.message, context)

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

    await query.message.reply_text(
        "🖼 Please send the new image now."
    )

async def edit_question_options_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    qid = context.user_data["active_question_id"]

    # Load existing options for reference
    cur.execute("SELECT options FROM questions WHERE id=?", (qid,))
    row = cur.fetchone()
    old_options = row[0].split("||")

    context.user_data["edit_q_field"] = "OPTIONS"
    context.user_data["edit_options"] = []

    await query.message.reply_text(
        "✏️ Editing options\n\n"
        f"Current options:\n"
        f"1️⃣ {old_options[0]}\n"
        f"2️⃣ {old_options[1]}\n"
        f"3️⃣ {old_options[2]}\n"
        f"4️⃣ {old_options[3]}\n\n"
        "➡️ Send NEW option 1:"
    )

async def edit_question_correct_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    qid = context.user_data["active_question_id"]

    # Load options
    cur.execute("SELECT options, correct FROM questions WHERE id=?", (qid,))
    options_text, current_correct = cur.fetchone()
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

    correct_index = int(query.data.replace("EDIT_CORRECT_", ""))
    qid = context.user_data["active_question_id"]

    cur.execute(
        "UPDATE questions SET correct=? WHERE id=?",
        (correct_index, qid)
    )
    conn.commit()

    await query.message.reply_text("✅ Correct answer updated.")
    await show_questions_from_message(query.message, context)

async def edit_question_explanation_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    qid = context.user_data["active_question_id"]

    # Load current explanation
    cur.execute("SELECT explanation FROM questions WHERE id=?", (qid,))
    row = cur.fetchone()
    current = row[0] if row and row[0] else "— none —"

    context.user_data["edit_q_field"] = "EXPLANATION"

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("⏭ Remove explanation", callback_data="EDIT_Q_EXPL_REMOVE")]
    ])

    await query.message.reply_text(
        f"🧾 Current explanation:\n\n{current}\n\n"
        "✏️ Send new explanation text:",
        reply_markup=keyboard
    )

async def edit_question_explanation_remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    qid = context.user_data["active_question_id"]

    cur.execute(
        "UPDATE questions SET explanation=NULL WHERE id=?",
        (qid,)
    )
    conn.commit()

    context.user_data.pop("edit_q_field", None)

    await query.message.reply_text("🗑 Explanation removed.")
    await show_questions_from_message(query.message, context)

async def play_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # 🔑 FIX: ADMIN START (from quiz menu)
    if "play_quiz_id" not in context.user_data:
        quiz_id = context.user_data.get("active_quiz_id")
        if not quiz_id:
            await query.message.reply_text("❌ Quiz not found.")
            return
        context.user_data["play_quiz_id"] = quiz_id

    quiz_id = context.user_data.get("play_quiz_id")
    group_chat_id = context.user_data.get("group_chat_id")

    # 🏆 CREATE LEADERBOARD MESSAGE (GROUP ONLY, ONCE)
    if group_chat_id and quiz_id not in GROUP_LB_MESSAGES:
        leaderboard_text = (
            "🏆 *Quiz Leaderboard*\n\n"
            "_Waiting for players to answer..._"
        )

        lb_msg = await context.bot.send_message(
            chat_id=group_chat_id,
            text=leaderboard_text,
            parse_mode="Markdown"
        )

        GROUP_LB_MESSAGES[quiz_id] = {
            "chat_id": group_chat_id,
            "message_id": lb_msg.message_id
        }

        GROUP_LEADERBOARDS.setdefault(quiz_id, {})

    # ▶️ START QUIZ (shared logic)
    await start_play_quiz(update, context)

async def play_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data  # PLAY_ANSWER_{index}
    chosen_index = int(data.replace("PLAY_ANSWER_", ""))

    play = context.user_data["play"]
    question = play["questions"][play["index"]]
    correct_index = question["correct"]

    # 🔒 LOCK ANSWERS AFTER FIRST TAP
    if play.get("locked"):
        return
    play["locked"] = True

    # ✅ INCREASE SCORE WHEN ANSWER IS CORRECT
    if chosen_index == correct_index:
        play["score"] += 1

    # 🎨 BUILD GREEN / RED BUTTONS
    buttons = []
    for i, option in enumerate(question["options"]):
        if i == correct_index:
            label = f"✅ {option}"
        elif i == chosen_index:
            label = f"❌ {option}"
        else:
            label = option

        buttons.append([InlineKeyboardButton(label, callback_data="LOCKED")])

    # 🎨 UPDATE MESSAGE WITH VISUAL FEEDBACK
    await query.message.edit_reply_markup(
        reply_markup=InlineKeyboardMarkup(buttons)
    )

    # ➡️ MOVE TO NEXT QUESTION
    play["index"] += 1
    play["locked"] = False

    # 🏁 QUIZ FINISHED
    if play["index"] >= len(play["questions"]):
        quiz_id = play["quiz_id"]
        user = query.from_user
        score = play["score"]

        GROUP_LEADERBOARDS.setdefault(quiz_id, {})
        entry = GROUP_LEADERBOARDS[quiz_id].get(user.id)

        update_lb = False

        # 🥇 FIRST ATTEMPT → update leaderboard
        if not entry:
            GROUP_LEADERBOARDS[quiz_id][user.id] = {
                "name": user.first_name,
                "score": score,
                "attempts": 1
            }
            update_lb = True

        # 🥈 SECOND ATTEMPT → update leaderboard
        elif entry["attempts"] == 1:
            entry["score"] = score
            entry["attempts"] = 2
            update_lb = True

        # 🚫 THIRD ATTEMPT AND BEYOND → DO NOT update leaderboard
        else:
            entry["attempts"] += 1

        # 🔄 UPDATE GROUP LEADERBOARD (NO AUTO-SCROLL)
        if update_lb:
            await update_group_leaderboard(quiz_id, context)

        # 🧾 PERSONAL RESULT (PRIVATE CHAT)
        await query.message.reply_text(
            f"🏁 Quiz finished!\n\nYour score: {score}"
        )

        return

    # ➡️ NEXT QUESTION
    await send_next_question(query.from_user.id, context)

async def start_quiz_for_user(user_id, context):
    quiz_id = context.user_data.get("play_quiz_id")
    if not quiz_id:
        return

    # Load quiz settings
    cur.execute(
        "SELECT shuffle_q, shuffle_a FROM quizzes WHERE quiz_id=?",
        (quiz_id,)
    )
    shuffle_q, shuffle_a = cur.fetchone() or (0, 0)

    # Load questions
    cur.execute(
        "SELECT question, image_file_id, options, correct, explanation "
        "FROM questions WHERE quiz_id=?",
        (quiz_id,)
    )
    rows = cur.fetchall()

    if not rows:
        await context.bot.send_message(
            chat_id=user_id,
            text="❌ This quiz has no questions."
        )
        return

    questions = []
    for text, image, options, correct, explanation in rows:
        opts = options.split("||")

        if shuffle_a:
            import random
            indexed = list(enumerate(opts))
            random.shuffle(indexed)
            opts = [o for _, o in indexed]
            correct = [i for i, (old_i, _) in enumerate(indexed) if old_i == correct][0]

        questions.append({
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
        "score": 0
    }

    await send_next_question(user_id, context)

async def start_play_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    quiz_id = context.user_data.get("play_quiz_id")
    if not quiz_id:
        await query.message.reply_text("❌ Quiz not found.")
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
        "SELECT id, question, image_file_id, options, correct, explanation "
        "FROM questions WHERE quiz_id=?",
        (quiz_id,)
    )
    rows = cur.fetchall()

    if not rows:
        await query.message.reply_text("❌ This quiz has no questions.")
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

    # 🔑 CREATE PLAY SESSION (THIS FIXES KeyError)
    context.user_data["play"] = {
        "questions": questions,
        "index": 0,
        "score": 0,
        "quiz_id": quiz_id,
    }
    
    user_id = query.from_user.id
    await send_next_question(user_id, context)

async def send_next_question(user_id, context):
    play = context.user_data.get("play")
    if not play:
        await context.bot.send_message(
            chat_id=user_id,
            text="❌ Quiz session expired."
        )
        return

    q = play["questions"][play["index"]]

    text = f"❓ {q['text']}"

    keyboard = []
    for i, option in enumerate(q["options"]):
        keyboard.append([
            InlineKeyboardButton(
                option,
                callback_data=f"PLAY_ANSWER_{i}"
            )
        ])

    reply_markup = InlineKeyboardMarkup(keyboard)

    if q["image"]:
        await context.bot.send_photo(
            chat_id=user_id,
            photo=q["image"],
            caption=text,
            reply_markup=reply_markup
        )
    else:
        await context.bot.send_message(
            chat_id=user_id,
            text=text,
            reply_markup=reply_markup
        )

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

async def send_quiz_to_group(chat_id, quiz_id, context):
    cur.execute("""
        SELECT title, description, timer, shuffle_q, shuffle_a
        FROM quizzes WHERE quiz_id=?
    """, (quiz_id,))
    title, desc, timer, sq, sa = cur.fetchone()

    cur.execute("SELECT COUNT(*) FROM questions WHERE quiz_id=?", (quiz_id,))
    total_questions = cur.fetchone()[0]

    text = f"📘 *{title}*\n"

    if desc:
        text += f"_{desc}_\n"

    text += (
        f"\n📊 {total_questions} questions • ⏱ {timer}s • "
        f"🔀 {'ON' if sq else 'OFF'}/{'ON' if sa else 'OFF'}\n\n"
        "🏆 Leaderboard\n— No attempts yet —"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "▶️ Start Quiz",
                url=f"https://t.me/{BOT_USERNAME}?start=PLAY_{quiz_id}"
            )
        ]
    ])

    msg = await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        reply_markup=keyboard,
        parse_mode="Markdown"
    )

    # 🔑 THIS MESSAGE IS BOTH QUIZ + LEADERBOARD
    GROUP_LB_MESSAGES[quiz_id] = {
        "chat_id": chat_id,
        "message_id": msg.message_id,
        "page": 0
    }

    GROUP_LEADERBOARDS[quiz_id] = {}

    # 🔑 SAVE GROUP QUIZ STATE
    GROUP_QUIZZES[quiz_id] = {
        "chat_id": chat_id,
        "message_id": msg.message_id
    }

    GROUP_LEADERBOARDS[quiz_id] = {}

def build_group_quiz_text(quiz_id, page=0):
    # Load quiz info
    cur.execute(
        "SELECT title, description, timer, shuffle_q, shuffle_a FROM quizzes WHERE quiz_id=?",
        (quiz_id,)
    )
    title, desc, timer, sq, sa = cur.fetchone()

    cur.execute("SELECT COUNT(*) FROM questions WHERE quiz_id=?", (quiz_id,))
    total_questions = cur.fetchone()[0]

    text = f"📘 *{title}*\n"
    if desc:
        text += f"{desc}\n"

    text += "\n"
    text += (
        f"📊 {total_questions} Questions • "
        f"⏱ {timer}s • "
        f"🔀 Q: {'ON' if sq else 'OFF'} / A: {'ON' if sa else 'OFF'}\n\n"
    )

    text += "🏆 *Quiz Leaderboard*\n"

    leaderboard = list(GROUP_LEADERBOARDS.get(quiz_id, {}).values())

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

async def update_group_leaderboard(quiz_id, context):
    info = GROUP_LB_MESSAGES.get(quiz_id)
    if not info:
        return

    chat_id = info["chat_id"]
    message_id = info["message_id"]
    page = info.get("page", 0)

    text, pages = build_group_quiz_text(quiz_id, page)

    buttons = []

    if pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("◀ Prev", callback_data=f"LB_PREV|{quiz_id}"))
        nav.append(InlineKeyboardButton(f"{page+1}/{pages}", callback_data="LB_NOP"))
        if page < pages - 1:
            nav.append(InlineKeyboardButton("Next ▶", callback_data=f"LB_NEXT|{quiz_id}"))
        buttons.append(nav)

    buttons.append([
        InlineKeyboardButton("▶️ Start this Quiz", url=f"https://t.me/{BOT_USERNAME}?start=PLAY_{quiz_id}"
)
    ])

    await context.bot.edit_message_text(
        chat_id=chat_id,
        message_id=message_id,
        text=text,
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="Markdown"
    )

async def post_quiz_to_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    quiz_id = context.user_data.get("active_quiz_id")
    if not quiz_id:
        await query.message.reply_text("❌ No quiz selected.")
        return

    await query.message.reply_text(
        "👥 Add this bot to a group and make it admin.\n"
        "Then type this command in the group:\n\n"
        f"/post_{quiz_id}"
    )

async def post_quiz_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    chat = update.effective_chat
    if chat.type not in ("group", "supergroup"):
        return

    text = update.message.text
    if not text.startswith("/post_"):
        return

    quiz_id = text.replace("/post_", "").strip()

    await send_quiz_to_group(chat.id, quiz_id, context)

async def leaderboard_page_nav(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    action, quiz_id = data.split("|", 1)

    info = GROUP_LB_MESSAGES.get(quiz_id)
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
    GROUP_LB_MESSAGES[quiz_id] = info

    await update_group_leaderboard(quiz_id, context)

async def post_quiz_instructions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    quiz_id = context.user_data.get("active_quiz_id")
    if not quiz_id:
        await query.message.reply_text("❌ No quiz selected.")
        return

    await query.message.reply_text(
        "👥 Add this bot to a group and make it admin.\n\n"
        "Then type this command in the group:\n\n"
        f"/post_{quiz_id}"
    )

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

async def copy_question_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    source_qid = context.user_data.get("active_question_id")
    source_quiz_id = context.user_data.get("active_quiz_id")

    if not source_qid or not source_quiz_id:
        await query.message.reply_text("❌ No question selected.")
        return

    context.user_data["state"] = "COPY_QUESTION"
    page = context.user_data.get("copy_q_page", 0)

    cur.execute(
        "SELECT quiz_id, title FROM quizzes WHERE owner_id=? ORDER BY title",
        (OWNER_USER_ID,)
    )
    quizzes = cur.fetchall()

    source_quiz_id = context.user_data.get("active_quiz_id")

    # ❌ Prevent copying into the same quiz
    quizzes = [q for q in quizzes if q[0] != source_quiz_id]

    per_page = 5
    pages = (len(quizzes) - 1) // per_page + 1
    page = max(0, min(page, pages - 1))

    start = page * per_page
    end = start + per_page

    keyboard = []

    for quiz_id, title in quizzes[start:end]:
        keyboard.append([
            InlineKeyboardButton(
                f"📘 {title}",
                callback_data=f"COPY_TO|{quiz_id}"
            )
        ])

    # Pagination
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

    await query.message.reply_text(
        "📋 *Copy Question*\n\nSelect target quiz:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

async def copy_question_apply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    target_quiz_id = query.data.split("|", 1)[1]
    source_qid = context.user_data.get("active_question_id")

    if not source_qid:
        await query.message.reply_text("❌ Source question not found.")
        return

    # Load source question
    cur.execute("""
        SELECT question, image_file_id, options, correct, explanation
        FROM questions
        WHERE id=?
    """, (source_qid,))
    row = cur.fetchone()

    if not row:
        await query.message.reply_text("❌ Question not found.")
        return

    question, image, options, correct, explanation = row

    # Insert duplicated question
    cur.execute("""
        INSERT INTO questions (
            quiz_id,
            question,
            image_file_id,
            options,
            correct,
            explanation
        )
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        target_quiz_id,
        question,
        image,
        options,
        correct,
        explanation
    ))

    conn.commit()

    context.user_data.pop("state", None)

    await query.message.reply_text(
        "✅ Question copied successfully."
    )

    # Return to questions list
    await show_questions(update, context)

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
        await query.message.reply_text("❌ Nothing to delete.")
        return

    dtype, value = data

    if dtype == "QUESTION":
        cur.execute("DELETE FROM questions WHERE id=?", (value,))
        conn.commit()
        await query.message.reply_text("🗑 Question deleted.")
        await show_questions(update, context)

    elif dtype == "QUIZ":
        cur.execute("DELETE FROM questions WHERE quiz_id=?", (value,))
        cur.execute("DELETE FROM quizzes WHERE quiz_id=?", (value,))
        conn.commit()
        await query.message.reply_text("🗑 Quiz deleted.")
        await my_quizzes(query.message, context)

    elif dtype == "FOLDER":
        cur.execute(
            "UPDATE quizzes SET folder='Default' WHERE folder=?",
            (value,)
        )
        cur.execute(
            "DELETE FROM folders WHERE name=?",
            (value,)
        )
        conn.commit()
        await query.message.reply_text("🗑 Folder deleted.")
        await show_quiz_folders(query.message, context)

async def cancel_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    context.user_data.pop("confirm_delete", None)

    await query.message.reply_text("❌ Deletion cancelled.")

# =========================
# HANDLERS
# =========================
# load_owner_from_db()
ensure_default_folder()

app = ApplicationBuilder().token(BOT_TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
app.add_handler(MessageHandler(filters.Regex(r"^/post_"), post_quiz_command))
app.add_handler(CallbackQueryHandler(confirm_delete, pattern="^CONFIRM_DELETE$"))
app.add_handler(CallbackQueryHandler(cancel_delete, pattern="^CANCEL_DELETE$"))
app.add_handler(CallbackQueryHandler(copy_question_apply, pattern="^COPY_TO\\|"))
app.add_handler(CallbackQueryHandler(copy_question_start, pattern="^COPY_Q$"))
app.add_handler(CallbackQueryHandler(folder_prev, pattern="^FOLDER_PREV\\|"))
app.add_handler(CallbackQueryHandler(folder_next, pattern="^FOLDER_NEXT\\|"))
app.add_handler(CallbackQueryHandler(post_quiz_instructions, pattern="^POST_QUIZ$"))
app.add_handler(CallbackQueryHandler(play_start, pattern="^START_THIS$"))
app.add_handler(CallbackQueryHandler(leaderboard_page_nav, pattern="^LB_PREV\\|"))
app.add_handler(CallbackQueryHandler(leaderboard_page_nav, pattern="^LB_NEXT\\|"))
app.add_handler(CallbackQueryHandler(post_quiz_to_group, pattern="^POST_TO_GROUP$"))
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
app.add_handler(CallbackQueryHandler(preview_question, pattern="^Q_"))
app.add_handler(CallbackQueryHandler(quiz_action_menu, pattern="^QUIZ_"))
app.add_handler(CallbackQueryHandler(edit_menu, pattern="^EDIT_THIS$"))
app.add_handler(CallbackQueryHandler(edit_title, pattern="^EDIT_TITLE$"))
app.add_handler(CallbackQueryHandler(edit_desc, pattern="^EDIT_DESC$"))
app.add_handler(CallbackQueryHandler(edit_timer_menu, pattern="^EDIT_TIMER$"))
app.add_handler(CallbackQueryHandler(set_timer, pattern="^SET_TIMER_"))
app.add_handler(CallbackQueryHandler(edit_shuffle_menu, pattern="^EDIT_SHUFFLE$"))
app.add_handler(CallbackQueryHandler(toggle_shuffle, pattern="^TOGGLE_"))
app.add_handler(CallbackQueryHandler(show_questions, pattern="^EDIT_QUESTIONS$"))
app.add_handler(CallbackQueryHandler(add_new_question, pattern="^ADD_QUESTION$"))
app.add_handler(CallbackQueryHandler(back_to_action, pattern="^BACK_TO_ACTION$"))
app.add_handler(CallbackQueryHandler(edit_correct_answer, pattern="^EDIT_CORRECT$"))
app.add_handler(CallbackQueryHandler(delete_question, pattern="^DELETE_QUESTION$"))

print("✅ QuizBot Clone is running...")
app.run_polling()
