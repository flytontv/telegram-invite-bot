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

# --- USER START ---
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

    # Link Generation (/start req_xxx ya /start join_xxx)
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
                err = await update.message.reply_text("SERVER BUSY. PLEASE CLICK THE LINK AGAIN.")
                context.job_queue.run_once(delete_job, 10, data={"chat_id": chat_id, "msg_ids": [err.message_id]})
                return

            btn_label = "REQUEST TO JOIN CHANNEL" if is_req else "JOIN CHANNEL NOW"
            keyboard = [[InlineKeyboardButton(f"• {btn_label} •", url=invite.invite_link)]]
            reply_markup = InlineKeyboardMarkup(keyboard)

            caption_text = (
                "YOUR SECURE ACCESS LINK IS READY!\n\n"
                "HOW TO JOIN:\n"
                "1. CLICK THE BUTTON BELOW\n"
                "2. SEND JOIN REQUEST\n"
                "3. GET INSTANT ACCESS TO THE CHANNEL!\n\n"
                "NOTE: THIS LINK EXPIRES AUTOMATICALLY IN 59 SECONDS."
            )

            custom_img = get_setting("custom_image", "")
            sent_ids = [update.message.message_id]

            if custom_img:
                try:
                    m = await update.message.reply_photo(
                        photo=custom_img,
                        caption=caption_text,
                        reply_markup=reply_markup
                    )
                    sent_ids.append(m.message_id)
                except Exception:
                    m = await update.message.reply_text(
                        caption_text,
                        reply_markup=reply_markup
                    )
                    sent_ids.append(m.message_id)
            else:
                m = await update.message.reply_text(
                    caption_text,
                    reply_markup=reply_markup
                )
                sent_ids.append(m.message_id)

            # Auto-delete in 59 seconds
            context.job_queue.run_once(delete_job, 59, data={"chat_id": chat_id, "msg_ids": sent_ids})
            return

    # Default Start Message (Screen-shot Style)
    default_text = (
        "I AM A SECURE LINK CHANGER BOT. YOU CAN USE ME TO GET ACCESS TO CHANNELS SAFELY!\n\n"
        "IT'S EASY TO USE ME:\n"
        "1. CLICK ON ANY POST LINK\n"
        "2. GET YOUR 59-SECOND SECURE LINK\n"
        "3. PROCEED TO JOIN THE CHANNEL EASILY!"
    )
    btn = [[InlineKeyboardButton("FlyTon Anime Channel", url="https://t.me/FlyTonTV")]]
    s_msg = await update.message.reply_text(default_text, reply_markup=InlineKeyboardMarkup(btn))
    context.job_queue.run_once(delete_job, 30, data={"chat_id": chat_id, "msg_ids": [s_msg.message_id, update.message.message_id]})

# --- ADMIN COMMANDS ---
async def addch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("USAGE: /addch -100xxxxxxxxxx")
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
            f"CHANNEL ADDED SUCCESSFULLY!\n\n"
            f"CHANNEL: {chat.title}\n"
            f"CHANNEL ID: {ch_id}\n\n"
            f"INFINITE POST LINKS:\n\n"
            f"1. REQUEST TO JOIN LINK:\n{req_url}\n\n"
            f"2. DIRECT JOIN LINK:\n{join_url}"
        )
        keyboard = [
            [InlineKeyboardButton("TEST REQUEST LINK", url=req_url)],
            [InlineKeyboardButton("TEST JOIN LINK", url=join_url)]
        ]
        await update.message.reply_text(response_text, reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception as e:
        await update.message.reply_text(f"ERROR: {e}\nMAKE SURE BOT IS ADMIN IN THE CHANNEL!")

async def delch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not context.args:
        return
    ch_id = int(context.args[0])
    with get_db() as conn:
        conn.execute("DELETE FROM channels WHERE channel_id = ?", (ch_id,))
        conn.commit()
    await update.message.reply_text(f"CHANNEL {ch_id} REMOVED SUCCESSFULLY!")

async def channels_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT channel_id, title FROM channels")
        rows = c.fetchall()
    if not rows:
        await update.message.reply_text("NO CONNECTED CHANNELS FOUND.")
        return
    bot_user = (await context.bot.get_me()).username
    msg = "ALL CONNECTED CHANNELS:\n\n"
    buttons = []
    for r in rows:
        clean_id = str(r[0]).replace("-100", "")
        link = f"https://t.me/{bot_user}?start=req_{clean_id}"
        msg += f"• {r[1]} ({r[0]})\n{link}\n\n"
        buttons.append([InlineKeyboardButton(f"OPEN {r[1]}", url=link)])
    await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(buttons))

async def setpic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if update.message.reply_to_message and update.message.reply_to_message.photo:
        fid = update.message.reply_to_message.photo[-1].file_id
        set_setting("custom_image", fid)
        await update.message.reply_text("CUSTOM HEADER IMAGE HAS BEEN UPDATED!")
    else:
        await update.message.reply_text("REPLY TO ANY IMAGE WITH /setpic")

async def unsetpic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    set_setting("custom_image", "")
    await update.message.reply_text("CUSTOM IMAGE REMOVED.")

async def approveon(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not context.args:
        return
    ch_id = int(context.args[0])
    with get_db() as conn:
        conn.execute("UPDATE channels SET auto_approve = 1 WHERE channel_id = ?", (ch_id,))
        conn.commit()
    await update.message.reply_text(f"AUTO-APPROVAL ENABLED FOR {ch_id}")

async def approveoff(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not context.args:
        return
    ch_id = int(context.args[0])
    with get_db() as conn:
        conn.execute("UPDATE channels SET auto_approve = 0 WHERE channel_id = ?", (ch_id,))
        conn.commit()
    await update.message.reply_text(f"AUTO-APPROVAL DISABLED FOR {ch_id}")

async def reqmode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    curr = get_setting("reqmode", "on")
    nxt = "off" if curr == "on" else "on"
    set_setting("reqmode", nxt)
    await update.message.reply_text(f"GLOBAL AUTO-APPROVAL IS NOW: {nxt.upper()}")

async def reqtime(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not context.args:
        return
    sec = int(context.args[0])
    set_setting("reqtime", str(sec))
    await update.message.reply_text(f"AUTO-APPROVAL TIMER SET TO: {sec} SECONDS")

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

    img = "SET" if get_setting("custom_image") else "NOT SET"
    text = (
        "SYSTEM STATUS OVERVIEW:\n\n"
        f"CONNECTED CHANNELS: {ch_count}\n"
        f"TOTAL USERS: {u_count}\n"
        f"CUSTOM BRANDING: {img}\n"
        f"AUTO-APPROVAL MODE: {get_setting('reqmode', 'on').upper()}\n"
        f"APPROVAL DELAY: {get_setting('reqtime', '0')} SECONDS"
    )
    await update.message.reply_text(text)

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
    app.add_handler(ChatJoinRequestHandler(handle_join_request))

    app.run_polling(drop_pending_updates=True)
