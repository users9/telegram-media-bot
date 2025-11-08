# main.py
import os, re, tempfile, logging
from threading import Thread
from pathlib import Path
from urllib.parse import urlparse

from flask import Flask
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.constants import ChatAction
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ───────────────────────────────────────────────
# إعداد Flask (لـ Render Health Check)
# ───────────────────────────────────────────────
app = Flask(__name__)

@app.route("/")
def home():
    return "Bot is running ✅"


# ───────────────────────────────────────────────
# دوال البوت الأساسية
# ───────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✅ أهلاً! أرسل رابط أو ملف وسيتم تحميله.")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📌 هذا بوت تحميل الوسائط.\nأرسل أي رابط وسأقوم بتنزيله.")

async def snap_back_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer("✅ تمت المعالجة!")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    # مثال بسيط: لو أرسل رابط
    if text.startswith("http"):
        await update.message.reply_text("🔄 جاري المعالجة…")
        # ممكن نضيف تحميل ملف هنا لاحقاً
        await update.message.reply_text("✅ تم!")

    else:
        await update.message.reply_text("أرسل رابط صحيح.")

# ───────────────────────────────────────────────
# تشغيل البوت مع Flask بدون مشاكل Event Loop
# ───────────────────────────────────────────────

TOKEN = os.getenv("TELEGRAM_TOKEN")

def run_bot_thread():
    import asyncio

    if not TOKEN:
        raise RuntimeError("❌ لم يتم ضبط TELEGRAM_TOKEN في Render")

    # بناء التطبيق (PTB v21+)
    app_tg = Application.builder().token(TOKEN).build()

    # إضافة الهاندلرز
    app_tg.add_handler(CommandHandler("start", start))
    app_tg.add_handler(CommandHandler("help", help_cmd))
    app_tg.add_handler(CallbackQueryHandler(snap_back_callback, pattern="^snap_back$"))
    app_tg.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_text))

    # إنشاء EventLoop مستقل لهذا الخيط
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    print("✅ Telegram polling started")
    loop.run_until_complete(app_tg.run_polling())

# ───────────────────────────────────────────────
# نقطة التشغيل
# ───────────────────────────────────────────────
if __name__ == "__main__":
    # تشغل البوت في الخلفية
    Thread(target=run_bot_thread, daemon=True).start()

    # Flask يشتغل كـ Web Service لـ Render
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
