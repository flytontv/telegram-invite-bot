import os
import time
import asyncio
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHANNEL_ID = int(os.environ.get("CHANNEL_ID", "-1003980771956"))
PORT = int(os.environ.get("PORT", 8080))

# Render को शांत रखने के लिए छोटा सा वेब सर्वर
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is Running!")

def run_web_server():
    server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    server.serve_forever()

async def auto_delete(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_ids: list, delay: int = 59):
    await asyncio.sleep(delay)
    for msg_id in message_ids:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
        except Exception:
            pass

async def send_invite(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return

    chat_id = update.effective_chat.id
    expire_timestamp = int(time.time()) + 59

    try:
        invite = await context.bot.create_chat_invite_link(
            chat_id=CHANNEL_ID,
            expire_date=expire_timestamp,
            member_limit=1
        )

        keyboard = [
            [InlineKeyboardButton("• JOIN CHANNEL •", url=invite.invite_link)]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        msg1 = await update.message.reply_text(
            "<b>HERE IS YOUR LINK! CLICK BELOW TO PROCEED</b>",
            reply_markup=reply_markup,
            parse_mode="HTML"
        )

        msg2 = await update.message.reply_text(
            "<u><b>Note:</b> If the link is expired, please click the post link again to get a new one.</u>",
            parse_mode="HTML"
        )

        asyncio.create_task(auto_delete(context, chat_id, [msg1.message_id, msg2.message_id], delay=59))

    except Exception:
        await update.message.reply_text("चैनल में बॉट को लिंक बनाने की एडमिन परमिशन नहीं है।")

if __name__ == "__main__":
    # बैकग्राउंड में वेब सर्वर चालू करना
    threading.Thread(target=run_web_server, daemon=True).start()
    
    # टेलीग्राम बॉट चालू करना
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", send_invite))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), send_invite))
    app.run_polling()
