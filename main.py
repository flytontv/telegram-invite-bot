import os
import time
import asyncio
import sqlite3
import threading
import logging
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import RetryAfter, TelegramError
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ChatJoinRequestHandler,
    ContextTypes,
)

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
OWNER_ID = int(os.environ.get("OWNER_ID", "0"))
PORT = int(os.environ.get("PORT", 8080))

DB_FILE = "bot_data.db"

def get_db():
    conn = sqlite3.connect(DB_FILE, timeout=60.0, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn

def init_db():
    with get_db() as conn:
        c = conn.cursor()
        c.execute("""CREATE TABLE IF NOT EXISTS admins (user_id INTEGER PRIMARY KEY)""")
        c.execute("""CREATE TABLE IF NOT EXISTS channels (
            channel_id INTEGER PRIMARY KEY,
            title TEXT,
            auto_approve INTEGER DEFAULT 0
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY)""")
        c.execute("""CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)""")
        c.execute("INSERT OR IGNORE INTO settings VALUES ('reqtime', '0')")
        c.execute("INSERT OR IGNORE INTO settings VALUES ('reqmode', 'on')")
        c.execute("INSERT OR IGNORE INTO settings VALUES ('custom_image', '')")
        conn.commit()

init_db()

def is_admin(user_id: int) -> bool:
    if user_id == OWNER_ID:
        return True
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT 1 FROM admins WHERE user_id = ?", (user_id,))
        return c.fetchone() is not None

def get_setting(key: str, default: str = "") -> str:
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = c.fetchone()
        return row[0] if row else default

def set_setting(key: str, value: str):
    with get_db() as conn:
        c = conn.cursor()
        c.execute("INSERT OR REPLACE INTO settings VALUES (?, ?)", (key, str(value)))
        conn.commit()

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")
    def log_message(self, format, *args):
        pass

def run_web_server():
    server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    server.serve_forever()

async def delete_job(context: ContextTypes.DEFAULT_TYPE):
    job_data = context.job.data
    chat_id = job_data["chat_id"]
    msg_ids = job_data["msg_ids"]
    for mid in msg_ids:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=mid)
        except Exception:
            pass

async def create_secure_invite(bot, chat_id, is_req, expire_ts, max_retries=3):
    for attempt in range(max_retries):
        try:
            invite = await bot.create_chat_invite_link(
                chat_id=chat_id,
                expire_date=expire_ts,
                creates_join_request=True if is_req else False,
                member_limit=None if is_req else 1
            )
            return invite
        except RetryAfter as e:
            await asyncio.sleep(e.retry_after + 0.5)
        except TelegramError as e:
            logger.error(f"Telegram error on attempt {attempt}: {e}")
            await asyncio.sleep(0.5)
        except Exception:
            break
    return None

# --- USER GATEWAY: NO BOX, SCREENSHOT EXACT MATCH WITH SMALL CAPS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or update.effective_chat.type != "private":
        return
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    try:
        with get_db() as conn:
            conn.execute("INSERT OR IGNORE INTO users VALUES (?)", (user_id,))
            conn.commit()
    except Exception:
        pass

    # Deep Link Handler (/start req_xxx / /start join_xxx)
    if context.args and len(context.args) > 0:
        arg = context.args[0]
        is_req = arg.startswith("req_")
        is_dir = arg.startswith("join_")

        if is_req or is_dir:
            raw_id = arg.replace("req_", "").replace("join_", "")
            target_ch = int(f"-100{raw_id}" if not raw_id.startswith("-100") else raw_id)
            expire_ts = int(time.time()) + 59

            invite = await create_secure_invite(context.bot, target_ch, is_req, expire_ts)
            if not invite:
                err = await update.message.reply_text("sᴇʀᴠᴇʀ ʙᴜsʏ. ᴘʟᴇᴀsᴇ ᴄʟɪᴄᴋ ᴛʜᴇ ʟɪɴᴋ ᴀɢᴀɪɴ.")
                context.job_queue.run_once(delete_job, 10, data={"chat_id": chat_id, "msg_ids": [err.message_id]})
                return

            btn_label = "• ʀᴇǫᴜᴇsᴛ ᴛᴏ ᴊᴏɪɴ ᴄʜᴀɴɴᴇʟ •" if is_req else "• ᴊᴏɪɴ ᴄʜᴀɴɴᴇʟ •"
            reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton(btn_label, url=invite.invite_link)]])

            sent_ids = [update.message.message_id]
            custom_img = get_setting("custom_image", "")

            # Screenshot line 1
            header_text = "<b>ʜᴇʀᴇ ɪs ʏᴏᴜʀ ʟɪɴᴋ! ᴄʟɪᴄᴋ ʙᴇʟᴏᴡ ᴛᴏ ᴘʀᴏᴄᴇᴇᴅ</b>"
            if custom_img:
                try:
                    m1 = await update.message.reply_photo(
                        photo=custom_img,
                        caption=header_text,
                        reply_markup=reply_markup,
                        parse_mode="HTML"
                    )
                    sent_ids.append(m1.message_id)
                except Exception:
                    m1 = await update.message.reply_text(header_text, reply_markup=reply_markup, parse_mode="HTML")
                    sent_ids.append(m1.message_id)
            else:
                m1 = await update.message.reply_text(header_text, reply_markup=reply_markup, parse_mode="HTML")
                sent_ids.append(m1.message_id)

            # Screenshot line 2 (Underlined Note)
            note_text = "<u><b>ɴᴏᴛᴇ:</b> ɪғ ᴛʜᴇ ʟɪɴᴋ ɪs ᴇxᴘɪʀᴇᴅ, ᴘʟᴇᴀsᴇ ᴄʟɪᴄᴋ ᴛʜᴇ ᴘᴏsᴛ ʟɪɴᴋ ᴀɢᴀɪɴ ᴛᴏ ɢᴇᴛ ᴀ ɴᴇᴡ ᴏɴᴇ.</u>"
            m2 = await update.message.reply_text(note_text, parse_mode="HTML")
            sent_ids.append(m2.message_id)

            # 59s Auto-delete
            context.job_queue.run_once(delete_job, 59, data={"chat_id": chat_id, "msg_ids": sent_ids})
            return

    # Direct /start without payload
    default_text = (
        "<b>ɪ ᴀᴍ ᴀ sᴇᴄᴜʀᴇ ʟɪɴᴋ ᴄʜᴀɴɢᴇʀ ʙᴏᴛ. ʏᴏᴜ ᴄᴀɴ ᴜsᴇ ᴍᴇ ᴛᴏ ɢᴇᴛ ᴀᴄᴄᴇss ᴛᴏ ᴄʜᴀɴɴᴇʟs sᴀғᴇʟʏ!</b>\n\n"
        "<b>ɪᴛ's ᴇᴀsʏ ᴛᴏ ᴜsᴇ ᴍᴇ:</b>\n"
        "1. ᴄʟɪᴄᴋ ᴏɴ ᴀɴʏ ᴘᴏsᴛ ʟɪɴᴋ\n"
        "2. ɢᴇᴛ ʏᴏᴜʀ 59-sᴇᴄᴏɴᴅ sᴇᴄᴜʀᴇ ʟɪɴᴋ\n"
        "3. ᴘʀᴏᴄᴇᴇᴅ ᴛᴏ ᴊᴏɪɴ ᴛʜᴇ ᴄʜᴀɴɴᴇʟ ᴇᴀsɪʟʏ!"
    )
    buttons = [
        [InlineKeyboardButton("• ᴜᴘᴅᴀᴛᴇs ᴄʜᴀɴɴᴇʟ •", url="https://t.me/FlyTonTV")],
        [InlineKeyboardButton("ᴄʟᴏsᴇ", callback_data="close_msg")]
    ]
    s_msg = await update.message.reply_text(default_text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")
    context.job_queue.run_once(delete_job, 30, data={"chat_id": chat_id, "msg_ids": [s_msg.message_id, update.message.message_id]})

# --- BUTTON HANDLER ---
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "close_msg":
        try:
            await query.message.delete()
        except Exception:
            pass
    elif query.data == "refresh_status":
        if not is_admin(query.from_user.id):
            return
        with get_db() as conn:
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM channels")
            ch_count = c.fetchone()[0]
            c.execute("SELECT COUNT(*) FROM users")
            u_count = c.fetchone()[0]

        img = "sᴇᴛ" if get_setting("custom_image") else "ɴᴏᴛ sᴇᴛ"
        text = (
            "╭─ sʏsᴛᴇᴍ sᴛᴀᴛᴜs ᴏᴠᴇʀᴠɪᴇᴡ\n"
            "│\n"
            "├ ⚡ ᴄᴏʀᴇ ᴇɴɢɪɴᴇ: ᴏɴʟɪɴᴇ (ᴄᴏɴᴄᴜʀʀᴇɴᴛ)\n"
            f"├ 📢 ᴄᴏɴɴᴇᴄᴛᴇᴅ ᴄʜᴀɴɴᴇʟs: {ch_count}\n"
            f"├ 👥 ᴛᴏᴛᴀʟ ᴜsᴇʀs: {u_count}\n"
            f"├ 🖼 ᴄᴜsᴛᴏᴍ ʙʀᴀɴᴅɪɴɢ: {img}\n"
            f"├ ⚙️ ᴀᴜᴛᴏ-ᴀᴘᴘʀᴏᴠᴀʟ: {get_setting('reqmode', 'on').upper()}\n"
            f"├ ⏱️ ᴀᴘᴘʀᴏᴠᴀʟ ᴅᴇʟᴀʏ: {get_setting('reqtime', '0')}s\n"
            "│\n"
            "╰─ ᴅᴀᴛᴀʙᴀsᴇ: ᴡᴀʟ-ᴍᴏᴅᴇ ᴀᴄᴛɪᴠᴇ"
        )
        keyboard = [
            [InlineKeyboardButton("ʀᴇғʀᴇsʜ sᴛᴀᴛs", callback_data="refresh_status")],
            [InlineKeyboardButton("ᴄʟᴏsᴇ", callback_data="close_msg")]
        ]
        try:
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception:
            pass

# --- ALL ADMIN COMMANDS: CONSISTENT BOX & SMALL CAPS ---
async def addch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    keyboard = [[InlineKeyboardButton("ᴄʟᴏsᴇ", callback_data="close_msg")]]
    if not context.args:
        text = "╭─ ᴇʀʀᴏʀ\n╰ ᴜsᴀɢᴇ: /addch -100xxxxxxxxxx"
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return
    try:
        ch_id = int(context.args[0])
        chat = await context.bot.get_chat(ch_id)
        with get_db() as conn:
            conn.execute("INSERT OR REPLACE INTO channels VALUES (?, ?, 0)", (ch_id, chat.title))
            conn.commit()

        bot_user = (await context.bot.get_me()).username
        clean_id = str(ch_id).replace("-100", "")

        req_url = f"https://t.me/{bot_user}?start=req_{clean_id}"
        join_url = f"https://t.me/{bot_user}?start=join_{clean_id}"

        response_text = (
            "╭─ ᴄʜᴀɴɴᴇʟ ᴀᴅᴅᴇᴅ sᴜᴄᴄᴇssғᴜʟʟʏ\n"
            "│\n"
            f"├ 📢 ᴄʜᴀɴɴᴇʟ: {chat.title}\n"
            f"├ 🆔 ᴄʜᴀɴɴᴇʟ ɪᴅ: {ch_id}\n"
            "│\n"
            "├ 🔗 ɪɴғɪɴɪᴛᴇ ᴘᴏsᴛ ʟɪɴᴋs:\n"
            "├ 1. ʀᴇǫᴜᴇsᴛ ʟɪɴᴋ:\n"
            f"│  {req_url}\n"
            "│\n"
            "├ 2. ᴅɪʀᴇᴄᴛ ᴊᴏɪɴ ʟɪɴᴋ:\n"
            f"│  {join_url}\n"
            "│\n"
            "╰─ ɴᴏᴛᴇ: ᴘᴇʀᴍᴀɴᴇɴᴛ ʟɪɴᴋ ɪs ғᴜʟʟʏ ʜɪᴅᴅᴇɴ"
        )
        action_keyboard = [
            [InlineKeyboardButton("ᴛᴇsᴛ ʀᴇǫᴜᴇsᴛ ʟɪɴᴋ", url=req_url)],
            [InlineKeyboardButton("ᴛᴇsᴛ ᴊᴏɪɴ ʟɪɴᴋ", url=join_url)],
            [InlineKeyboardButton("ᴄʟᴏsᴇ", callback_data="close_msg")]
        ]
        await update.message.reply_text(response_text, reply_markup=InlineKeyboardMarkup(action_keyboard))
    except Exception as e:
        err_text = f"╭─ ᴇʀʀᴏʀ\n├ {e}\n╰ ᴍᴀᴋᴇ sᴜʀᴇ ʙᴏᴛ ɪs ᴀᴅᴍɪɴ ɪɴ ᴄʜᴀɴɴᴇʟ!"
        await update.message.reply_text(err_text, reply_markup=InlineKeyboardMarkup(keyboard))

async def delch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    keyboard = [[InlineKeyboardButton("ᴄʟᴏsᴇ", callback_data="close_msg")]]
    if not context.args:
        text = "╭─ ᴇʀʀᴏʀ\n╰ ᴜsᴀɢᴇ: /delch -100xxxxxxxxxx"
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return
    ch_id = int(context.args[0])
    with get_db() as conn:
        conn.execute("DELETE FROM channels WHERE channel_id = ?", (ch_id,))
        conn.commit()
    text = (
        "╭─ ᴄʜᴀɴɴᴇʟ ʀᴇᴍᴏᴠᴇᴅ\n"
        f"╰ ᴛᴀʀɢᴇᴛ: {ch_id} ᴜɴʟɪɴᴋᴇᴅ sᴜᴄᴄᴇssғᴜʟʟʏ"
    )
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def channels_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT channel_id, title FROM channels")
        rows = c.fetchall()
    if not rows:
        keyboard = [[InlineKeyboardButton("ᴄʟᴏsᴇ", callback_data="close_msg")]]
        await update.message.reply_text("╭─ ᴀʟʟ ᴄᴏɴɴᴇᴄᴛᴇᴅ ᴄʜᴀɴɴᴇʟs\n╰ ɴᴏ ᴄᴏɴɴᴇᴄᴛᴇᴅ ᴄʜᴀɴɴᴇʟs ғᴏᴜɴᴅ.", reply_markup=InlineKeyboardMarkup(keyboard))
        return
    bot_user = (await context.bot.get_me()).username
    msg = "╭─ ᴀʟʟ ᴄᴏɴɴᴇᴄᴛᴇᴅ ᴄʜᴀɴɴᴇʟs\n│\n"
    buttons = []
    for r in rows:
        clean_id = str(r[0]).replace("-100", "")
        link = f"https://t.me/{bot_user}?start=req_{clean_id}"
        msg += f"├ • {r[1]} ({r[0]})\n│   {link}\n"
        buttons.append([InlineKeyboardButton(f"ᴏᴘᴇɴ {r[1]}", url=link)])
    msg += f"│\n╰─ ᴛᴏᴛᴀʟ ᴀᴄᴛɪᴠᴇ ɴᴏᴅᴇs: {len(rows)}"
    buttons.append([InlineKeyboardButton("ᴄʟᴏsᴇ", callback_data="close_msg")])
    await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(buttons))

async def setpic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    keyboard = [[InlineKeyboardButton("ᴄʟᴏsᴇ", callback_data="close_msg")]]
    if update.message.reply_to_message and update.message.reply_to_message.photo:
        fid = update.message.reply_to_message.photo[-1].file_id
        set_setting("custom_image", fid)
        text = "╭─ ʙʀᴀɴᴅɪɴɢ ᴀssᴇᴛ\n╰ ᴄᴜsᴛᴏᴍ ʜᴇᴀᴅᴇʀ ɪᴍᴀɢᴇ sᴀᴠᴇᴅ sᴜᴄᴄᴇssғᴜʟʟʏ!"
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        text = "╭─ ʙʀᴀɴᴅɪɴɢ ᴀssᴇᴛ\n╰ ʀᴇᴘʟʏ ᴛᴏ ᴀɴʏ ɪᴍᴀɢᴇ ᴡɪᴛʜ /setpic"
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def unsetpic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    set_setting("custom_image", "")
    keyboard = [[InlineKeyboardButton("ᴄʟᴏsᴇ", callback_data="close_msg")]]
    text = "╭─ ʙʀᴀɴᴅɪɴɢ ᴀssᴇᴛ\n╰ ᴄᴜsᴛᴏᴍ ɪᴍᴀɢᴇ ʀᴇᴍᴏᴠᴇᴅ sᴜᴄᴄᴇssғᴜʟʟʏ."
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def approveon(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    keyboard = [[InlineKeyboardButton("ᴄʟᴏsᴇ", callback_data="close_msg")]]
    if not context.args:
        text = "╭─ ᴇʀʀᴏʀ\n╰ ᴜsᴀɢᴇ: /approveon -100xxxxxxxxxx"
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return
    ch_id = int(context.args[0])
    with get_db() as conn:
        conn.execute("UPDATE channels SET auto_approve = 1 WHERE channel_id = ?", (ch_id,))
        conn.commit()
    text = (
        "╭─ ᴀᴜᴛᴏ-ᴀᴘᴘʀᴏᴠᴀʟ ᴜᴘᴅᴀᴛᴇ\n"
        "├ sᴛᴀᴛᴜs: ᴇɴᴀʙʟᴇᴅ\n"
        f"╰ ᴛᴀʀɢᴇᴛ: {ch_id}"
    )
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def approveoff(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    keyboard = [[InlineKeyboardButton("ᴄʟᴏsᴇ", callback_data="close_msg")]]
    if not context.args:
        text = "╭─ ᴇʀʀᴏʀ\n╰ ᴜsᴀɢᴇ: /approveoff -100xxxxxxxxxx"
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return
    ch_id = int(context.args[0])
    with get_db() as conn:
        conn.execute("UPDATE channels SET auto_approve = 0 WHERE channel_id = ?", (ch_id,))
        conn.commit()
    text = (
        "╭─ ᴀᴜᴛᴏ-ᴀᴘᴘʀᴏᴠᴀʟ ᴜᴘᴅᴀᴛᴇ\n"
        "├ sᴛᴀᴛᴜs: ᴅɪsᴀʙʟᴇᴅ\n"
        f"╰ ᴛᴀʀɢᴇᴛ: {ch_id}"
    )
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def reqmode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    curr = get_setting("reqmode", "on")
    nxt = "off" if curr == "on" else "on"
    set_setting("reqmode", nxt)
    keyboard = [[InlineKeyboardButton("ᴄʟᴏsᴇ", callback_data="close_msg")]]
    text = (
        "╭─ ɢʟᴏʙᴀʟ ᴘɪᴘᴇʟɪɴᴇ ᴍᴏᴅᴇ\n"
        f"╰ ᴀᴜᴛᴏ-ᴀᴘᴘʀᴏᴠᴀʟ ɪs ɴᴏᴡ: {nxt.upper()}"
    )
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def reqtime(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    keyboard = [[InlineKeyboardButton("ᴄʟᴏsᴇ", callback_data="close_msg")]]
    if not context.args:
        text = "╭─ ᴇʀʀᴏʀ\n╰ ᴜsᴀɢᴇ: /reqtime <seconds>"
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return
    sec = int(context.args[0])
    set_setting("reqtime", str(sec))
    text = (
        "╭─ ᴛɪᴍᴇʀ ᴄᴏɴғɪɢᴜʀᴀᴛɪᴏɴ\n"
        f"╰ ᴀᴘᴘʀᴏᴠᴀʟ ʙᴜғғᴇʀ sᴇᴛ ᴛᴏ: {sec}s"
    )
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def handle_join_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    req = update.chat_join_request
    ch_id = req.chat.id
    user_id = req.from_user.id

    if get_setting("reqmode", "on") != "on":
        return

    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT auto_approve FROM channels WHERE channel_id = ?", (ch_id,))
        row = c.fetchone()

    if row and row[0] == 1:
        delay = int(get_setting("reqtime", "0"))
        if delay > 0:
            await asyncio.sleep(delay)
        try:
            await context.bot.approve_chat_join_request(chat_id=ch_id, user_id=user_id)
        except Exception:
            pass

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM channels")
        ch_count = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM users")
        u_count = c.fetchone()[0]

    img = "sᴇᴛ" if get_setting("custom_image") else "ɴᴏᴛ sᴇᴛ"
    text = (
        "╭─ sʏsᴛᴇᴍ sᴛᴀᴛᴜs ᴏᴠᴇʀᴠɪᴇᴡ\n"
        "│\n"
        "├ ⚡ ᴄᴏʀᴇ ᴇɴɢɪɴᴇ: ᴏɴʟɪɴᴇ (ᴄᴏɴᴄᴜʀʀᴇɴᴛ)\n"
        f"├ 📢 ᴄᴏɴɴᴇᴄᴛᴇᴅ ᴄʜᴀɴɴᴇʟs: {ch_count}\n"
        f"├ 👥 ᴛᴏᴛᴀʟ ᴜsᴇʀs: {u_count}\n"
        f"├ 🖼 ᴄᴜsᴛᴏᴍ ʙʀᴀɴᴅɪɴɢ: {img}\n"
        f"├ ⚙️ ᴀᴜᴛᴏ-ᴀᴘᴘʀᴏᴠᴀʟ: {get_setting('reqmode', 'on').upper()}\n"
        f"├ ⏱️ ᴀᴘᴘʀᴏᴠᴀʟ ᴅᴇʟᴀʏ: {get_setting('reqtime', '0')}s\n"
        "│\n"
        "╰─ ᴅᴀᴛᴀʙᴀsᴇ: ᴡᴀʟ-ᴍᴏᴅᴇ ᴀᴄᴛɪᴠᴇ"
    )
    keyboard = [
        [InlineKeyboardButton("ʀᴇғʀᴇsʜ sᴛᴀᴛs", callback_data="refresh_status")],
        [InlineKeyboardButton("ᴄʟᴏsᴇ", callback_data="close_msg")]
    ]
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

if __name__ == "__main__":
    threading.Thread(target=run_web_server, daemon=True).start()

    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .concurrent_updates(True)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("addch", addch))
    app.add_handler(CommandHandler("delch", delch))
    app.add_handler(CommandHandler("channels", channels_list))
    app.add_handler(CommandHandler("setpic", setpic))
    app.add_handler(CommandHandler("unsetpic", unsetpic))
    app.add_handler(CommandHandler("approveon", approveon))
    app.add_handler(CommandHandler("approveoff", approveoff))
    app.add_handler(CommandHandler("reqmode", reqmode))
    app.add_handler(CommandHandler("reqtime", reqtime))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(ChatJoinRequestHandler(handle_join_request))

    app.run_polling(drop_pending_updates=True)
