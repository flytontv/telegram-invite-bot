import os
import time
import asyncio
import sqlite3
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ChatJoinRequestHandler,
    filters,
    ContextTypes,
)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
OWNER_ID = int(os.environ.get("OWNER_ID", "0"))
PORT = int(os.environ.get("PORT", 8080))

# --- DATABASE SETUP ---
DB_FILE = "bot_data.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
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
    conn.commit()
    conn.close()

init_db()

def is_admin(user_id: int) -> bool:
    if user_id == OWNER_ID:
        return True
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT 1 FROM admins WHERE user_id = ?", (user_id,))
    res = c.fetchone()
    conn.close()
    return res is not None

def get_setting(key: str, default: str = "") -> str:
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else default

def set_setting(key: str, value: str):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO settings VALUES (?, ?)", (key, str(value)))
    conn.commit()
    conn.close()

# --- WEB SERVER (Render Keep-Alive) ---
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is Running!")

def run_web_server():
    server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    server.serve_forever()

# --- AUTO DELETE HELPER ---
async def auto_delete(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_ids: list, delay: int = 59):
    await asyncio.sleep(delay)
    for msg_id in message_ids:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
        except Exception:
            pass

# --- USER HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or update.effective_chat.type != "private":
        return
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO users VALUES (?)", (user_id,))
    conn.commit()
    conn.close()

    # Agar deep linking se request aayi hai: /start ch_<id>
    if context.args and context.args[0].startswith("ch_"):
        try:
            target_ch = int(context.args[0].replace("ch_", ""))
            expire_ts = int(time.time()) + 59
            invite = await context.bot.create_chat_invite_link(
                chat_id=target_ch,
                expire_date=expire_ts,
                creates_join_request=True
            )
            keyboard = [[InlineKeyboardButton("• REQUEST TO JOIN •", url=invite.invite_link)]]
            
            msg1 = await update.message.reply_text(
                "<b>HERE IS YOUR LINK! CLICK BELOW TO PROCEED</b>",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="HTML"
            )
            msg2 = await update.message.reply_text(
                "<u><b>Note:</b> If the link is expired, please click the post link again to get a new one.</u>",
                parse_mode="HTML"
            )
            # 59 सेकंड बाद दोनों मैसेज अपने आप डिलीट हो जाएँगे
            asyncio.create_task(auto_delete(context, chat_id, [msg1.message_id, msg2.message_id, update.message.message_id], delay=59))
            return
        except Exception as e:
            err = await update.message.reply_text(f"Error generating link: {e}")
            asyncio.create_task(auto_delete(context, chat_id, [err.message_id], delay=10))
            return

    # Default Start Message (यह भी 20 सेकंड में डिलीट हो जाएगा ताकि चैट साफ़ रहे)
    status_msg = await update.message.reply_text("✅ <b>Bot is active and running!</b>", parse_mode="HTML")
    asyncio.create_task(auto_delete(context, chat_id, [status_msg.message_id, update.message.message_id], delay=20))

# --- ADMIN MANAGEMENT COMMANDS ---
async def addadmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    if not context.args:
        await update.message.reply_text("Usage: /addadmin {user_id}")
        return
    new_admin = int(context.args[0])
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO admins VALUES (?)", (new_admin,))
    conn.commit()
    conn.close()
    await update.message.reply_text(f"Admin `{new_admin}` added successfully.", parse_mode="Markdown")

async def deladmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    if not context.args:
        await update.message.reply_text("Usage: /deladmin {user_id}")
        return
    target = int(context.args[0])
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("DELETE FROM admins WHERE user_id = ?", (target,))
    conn.commit()
    conn.close()
    await update.message.reply_text(f"Admin `{target}` removed.", parse_mode="Markdown")

async def admins_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT user_id FROM admins")
    rows = c.fetchall()
    conn.close()
    text = f"👑 <b>Owner:</b> <code>{OWNER_ID}</code>\n\n🛡 <b>Admins:</b>\n"
    for r in rows:
        text += f"• <code>{r[0]}</code>\n"
    await update.message.reply_text(text, parse_mode="HTML")

# --- CHANNEL MANAGEMENT COMMANDS ---
async def addch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: /addch {channel_id}")
        return
    try:
        ch_id = int(context.args[0])
        chat = await context.bot.get_chat(ch_id)
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("INSERT OR REPLACE INTO channels VALUES (?, ?, 0)", (ch_id, chat.title))
        conn.commit()
        conn.close()
        await update.message.reply_text(f"✅ Channel <b>{chat.title}</b> (<code>{ch_id}</code>) added!", parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"Error: {e}\n(Make sure bot is admin in that channel)")

async def delch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: /delch {channel_id}")
        return
    ch_id = int(context.args[0])
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("DELETE FROM channels WHERE channel_id = ?", (ch_id,))
    conn.commit()
    conn.close()
    await update.message.reply_text(f"Channel `{ch_id}` removed.", parse_mode="Markdown")

async def channels_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT channel_id, title, auto_approve FROM channels")
    rows = c.fetchall()
    conn.close()
    if not rows:
        await update.message.reply_text("No channels connected.")
        return
    msg = "📢 <b>Connected Channels:</b>\n\n"
    for r in rows:
        status = "✅ ON" if r[2] == 1 else "❌ OFF"
        msg += f"• <b>{r[1]}</b> | <code>{r[0]}</code> | Auto-Approve: {status}\n"
    await update.message.reply_text(msg, parse_mode="HTML")

# --- LINK GENERATION COMMANDS ---
async def reqlink(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    bot_me = await context.bot.get_me()
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT channel_id, title FROM channels")
    rows = c.fetchall()
    conn.close()
    if not rows:
        await update.message.reply_text("No channels added yet.")
        return
    msg = "🔗 <b>Direct Bot Links (Generates 59s Join Request):</b>\n\n"
    for r in rows:
        link = f"https://t.me/{bot_me.username}?start=ch_{abs(r[0])}"
        msg += f"• <b>{r[1]}</b>:\n<code>{link}</code>\n\n"
    await update.message.reply_text(msg, parse_mode="HTML")

async def links(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT channel_id, title FROM channels")
    rows = c.fetchall()
    conn.close()
    out = ""
    for r in rows:
        try:
            link = await context.bot.create_chat_invite_link(chat_id=r[0], creates_join_request=True)
            out += f"{r[1]}: {link.invite_link}\n"
        except Exception:
            out += f"{r[1]}: [Failed to generate]\n"
    await update.message.reply_text(out if out else "No channels found.")

async def bulklink(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: /bulklink -1001234 -1005678")
        return
    out = "📦 <b>Bulk Request Links:</b>\n\n"
    for cid in context.args:
        try:
            inv = await context.bot.create_chat_invite_link(chat_id=int(cid), creates_join_request=True)
            out += f"• <code>{cid}</code>: {inv.invite_link}\n"
        except Exception as e:
            out += f"• <code>{cid}</code>: Failed ({e})\n"
    await update.message.reply_text(out, parse_mode="HTML")

async def genlink(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: /genlink <url>")
        return
    target_url = context.args[0]
    keyboard = [[InlineKeyboardButton("Open Link", url=target_url)]]
    await update.message.reply_text("🔗 Button preview:", reply_markup=InlineKeyboardMarkup(keyboard))

# --- APPROVAL CONTROLS ---
async def approveon(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: /approveon {channel_id}")
        return
    ch_id = int(context.args[0])
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE channels SET auto_approve = 1 WHERE channel_id = ?", (ch_id,))
    conn.commit()
    conn.close()
    await update.message.reply_text(f"Auto-approval ENABLED for `{ch_id}`", parse_mode="Markdown")

async def approveoff(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: /approveoff {channel_id}")
        return
    ch_id = int(context.args[0])
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE channels SET auto_approve = 0 WHERE channel_id = ?", (ch_id,))
    conn.commit()
    conn.close()
    await update.message.reply_text(f"Auto-approval DISABLED for `{ch_id}`", parse_mode="Markdown")

async def reqmode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    curr = get_setting("reqmode", "on")
    new_mode = "off" if curr == "on" else "on"
    set_setting("reqmode", new_mode)
    await update.message.reply_text(f"Global Auto-Request Mode is now: <b>{new_mode.upper()}</b>", parse_mode="HTML")

async def reqtime(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: /reqtime {seconds} (0 for instant)")
        return
    sec = int(context.args[0])
    set_setting("reqtime", str(sec))
    await update.message.reply_text(f"Auto-approval timer set to <b>{sec} seconds</b>.", parse_mode="HTML")

# --- AUTO JOIN REQUEST HANDLER ---
async def handle_join_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    req = update.chat_join_request
    ch_id = req.chat.id
    user_id = req.from_user.id

    if get_setting("reqmode", "on") != "on":
        return

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT auto_approve FROM channels WHERE channel_id = ?", (ch_id,))
    row = c.fetchone()
    conn.close()

    if row and row[0] == 1:
        delay = int(get_setting("reqtime", "0"))
        if delay > 0:
            await asyncio.sleep(delay)
        try:
            await context.bot.approve_chat_join_request(chat_id=ch_id, user_id=user_id)
        except Exception:
            pass

# --- STATUS & BROADCAST ---
async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM channels")
    ch_count = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM users")
    u_count = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM admins")
    adm_count = c.fetchone()[0]
    conn.close()

    text = (
        "📊 <b>Bot Status:</b>\n\n"
        f"• Connected Channels: <b>{ch_count}</b>\n"
        f"• Total Users Tracked: <b>{u_count}</b>\n"
        f"• Bot Admins: <b>{adm_count + 1}</b>\n"
        f"• Global Approval Mode: <b>{get_setting('reqmode', 'on').upper()}</b>\n"
        f"• Approval Delay: <b>{get_setting('reqtime', '0')}s</b>"
    )
    await update.message.reply_text(text, parse_mode="HTML")

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not update.message.reply_to_message:
        await update.message.reply_text("Reply to a message with /broadcast to send it to all users.")
        return
    target_msg = update.message.reply_to_message
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT user_id FROM users")
    rows = c.fetchall()
    conn.close()

    count = 0
    for r in rows:
        try:
            await context.bot.copy_message(chat_id=r[0], from_chat_id=update.effective_chat.id, message_id=target_msg.message_id)
            count += 1
            await asyncio.sleep(0.05)
        except Exception:
            pass
    await update.message.reply_text(f"✅ Broadcast sent to {count} users.")

# --- MAIN RUNNER ---
if __name__ == "__main__":
    threading.Thread(target=run_web_server, daemon=True).start()
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("addadmin", addadmin))
    app.add_handler(CommandHandler("deladmin", deladmin))
    app.add_handler(CommandHandler("admins", admins_list))
    app.add_handler(CommandHandler("addch", addch))
    app.add_handler(CommandHandler("delch", delch))
    app.add_handler(CommandHandler("channels", channels_list))
    app.add_handler(CommandHandler("reqlink", reqlink))
    app.add_handler(CommandHandler("links", links))
    app.add_handler(CommandHandler("bulklink", bulklink))
    app.add_handler(CommandHandler("genlink", genlink))
    app.add_handler(CommandHandler("approveon", approveon))
    app.add_handler(CommandHandler("approveoff", approveoff))
    app.add_handler(CommandHandler("reqmode", reqmode))
    app.add_handler(CommandHandler("reqtime", reqtime))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("broadcast", broadcast))
    app.add_handler(ChatJoinRequestHandler(handle_join_request))

    app.run_polling()
