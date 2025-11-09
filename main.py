import os
import re
import asyncio
import logging
import tempfile
import shutil
from pathlib import Path

from flask import Flask
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    ContextTypes,
    filters,
)

import yt_dlp

# -----------------------------
# إعداد اللوق
# -----------------------------
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("BOT")

# -----------------------------
# Flask (لـ Render)
# -----------------------------
app = Flask(__name__)

@app.route("/")
def home():
    return "Bot is running!"

# -----------------------------
# إعدادات تيليجرام
# -----------------------------
TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TOKEN:
    raise RuntimeError("ENV TELEGRAM_TOKEN is missing!")

SEND_AS_DOCUMENT = True       # ← الإرسال دائمًا document
TG_LIMIT = 49 * 1024 * 1024   # حد تيليجرام ~50MB


# -----------------------------
# إعداد yt-dlp
# -----------------------------
def ydl_opts(tmp):
    return {
        "outtmpl": str(tmp / "%(title).200B.%(ext)s"),
        "format": "bv*+ba/bestvideo*+bestaudio/best",
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "prefer_free_formats": False,
        "overwrites": True,
    }


# -----------------------------
# إرسال الملف
# -----------------------------
async def send_document(update: Update, context: ContextTypes.DEFAULT_TYPE, file_path: Path):
    size = file_path.stat().st_size
    chat_id = update.effective_chat.id

    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_DOCUMENT)

    with open(file_path, "rb") as f:
        await context.bot.send_document(
            chat_id=chat_id,
            document=f,
            caption=file_path.name
        )


# -----------------------------
# تحميل + إرسال
# -----------------------------
async def download_and_send(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str):
    chat_id = update.effective_chat.id
    await context.bot.send_message(chat_id, "⏳ جاري التحميل...")

    tmp = Path(tempfile.mkdtemp(prefix="dl_"))

    try:
        with yt_dlp.YoutubeDL(ydl_opts(tmp)) as ydl:
            info = ydl.extract_info(url, download=True)
            filepath = Path(ydl.prepare_filename(info))

        # لو دمج FFmpeg وحول الامتداد
        if not filepath.exists():
            mp4_try = filepath.with_suffix(".mp4")
            if mp4_try.exists():
                filepath = mp4_try

        # الإرسال دائمًا document
        await send_document(update, context, filepath)

    except Exception as e:
        log.exception(e)
        await update.message.reply_text(f"❌ فشل التحميل: {e}")

    finally:
        try:
            shutil.rmtree(tmp, ignore_errors=True)
        except:
            pass


# -----------------------------
# معالج الرسائل
# -----------------------------
async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()

    match = re.search(r"(https?://\S+)", text)
    if not match:
        await update.message.reply_text("أرسل رابط فيديو فقط.")
        return

    url = match.group(1)
    await download_and_send(update, context, url)


# -----------------------------
# تشغيل البوت
# -----------------------------
async def run_bot():
    log.info("✅ Starting bot…")

    bot = ApplicationBuilder().token(TOKEN).build()
    bot.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle))

    # لازم نحذف webhook
    await bot.bot.delete_webhook(drop_pending_updates=True)

    log.info("✅ Telegram polling started")
    await bot.run_polling(stop_signals=None, allowed_updates=Update.ALL_TYPES)


# -----------------------------
# ENTRYPOINT
# -----------------------------
def main():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # تشغيل البوت داخل Thread
    def runner():
        loop.run_until_complete(run_bot())

    import threading
    t = threading.Thread(target=runner, daemon=True)
    t.start()

    # تشغيل Flask
    app.run(host="0.0.0.0", port=10000)


if __name__ == "__main__":
    main()
