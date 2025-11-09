import os
import logging
from flask import Flask
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
import yt_dlp
import asyncio

# ========== إعداد اللوق ==========
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# ========== الروابط ==========
SNAP_URL = "https://www.snapchat.com/add/uckr"

# ========== رسائل ==========
def snap_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👻 إضافة السناب", url=SNAP_URL)],
        [InlineKeyboardButton("✅ تم، رجعت", callback_data="snap_back")]
    ])

WELCOME_MSG = (
    "👋 **مرحبًا!**\n\n"
    f"قبل ما نبدأ… ياليت تضيفني على السناب:\n🔗 {SNAP_URL}\n\n"
    "بعد الإضافة ارجع واضغط **تم، رجعت** أو أرسل **/start** مرة ثانية."
)

NOTICE_MSG = (
    "⚠️ **تنبيه مهم:**\n"
    "لا أُحِل ولا أتحمّل أي مسؤولية عن استخدام البوت في تحميل ما لا يرضي الله.\n"
    "رجاءً استخدمه في الخير فقط.\n\n"
    "أرسل رابط من: YouTube / Instagram / X / TikTok / Snapchat."
)

# ========== دوال التحميل ==========
async def download_media(url: str, path="media.mp4"):
    ydl_opts = {
        "outtmpl": path,
        "format": "best/bestvideo+bestaudio/best"
    }
    try:
        loop = asyncio.get_event_loop()
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            await loop.run_in_executor(None, lambda: ydl.download([url]))
        return path
    except Exception as e:
        return None

# ========== أوامر ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        WELCOME_MSG,
        reply_markup=snap_keyboard(),
        parse_mode="Markdown"
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "snap_back":
        await query.edit_message_text(NOTICE_MSG, parse_mode="Markdown")

async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()
    await update.message.reply_text("⏳ يتم التحميل…")

    file_path = await download_media(url)

    if not file_path:
        await update.message.reply_text("❌ فشل التحميل. قد تكون المنصة منعت الوصول أو الرابط غير صالح.")
        return

    await update.message.reply_video(video=open(file_path, "rb"))
    os.remove(file_path)

# ========== تشغيل البوت ==========
async def run_bot():
    token = os.getenv("TELEGRAM_TOKEN")
    application = Application.builder().token(token).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link))

    await application.initialize()
    await application.start()
    log.info("✅ Bot is running (polling started)")
    await application.updater.start_polling()
    await application.updater.wait_closed()

# ========== Flask (لـ Render) ==========
app = Flask(__name__)

@app.route("/")
def home():
    return "Bot is running ✅"

# ========== نقطة التشغيل ==========
if __name__ == "__main__":
    asyncio.run(run_bot())
