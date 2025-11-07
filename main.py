import os
from threading import Thread
from flask import Flask
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import yt_dlp
import tempfile
from pathlib import Path
import re

TOKEN = os.getenv("TELEGRAM_TOKEN")
URL_RE = re.compile(r"(https?://\S+)", re.IGNORECASE)

app = Flask(__name__)

@app.route("/")
def home():
    return "Bot is running!"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✅ البوت شغال! أرسل الرابط فقط.")

async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    m = URL_RE.search(text)
    if not m:
        return

    url = m.group(1)
    await update.message.chat.send_action(ChatAction.UPLOAD_DOCUMENT)

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td) / "%(title).80s.%(ext)s"
        ydl_opts = {
            "outtmpl": str(tmp),
            "format": "bv*+ba/best",
            "merge_output_format": "mp4",
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "restrictfilenames": True,
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as y:
                info = y.extract_info(url, download=True)
                fname = Path(info.get("_filename") or next(Path(td).glob("*")))
        except Exception as e:
            await update.message.reply_text(f"❌ خطأ أثناء التحميل: {e}")
            return

        try:
            suffix = fname.suffix.lower()
            title = (info.get("title") or "الملف")[:990]

            if suffix in {".mp4", ".webm", ".mov"}:
                await update.message.reply_video(video=fname.open("rb"), caption=title)
            else:
                await update.message.reply_document(document=fname.open("rb"), caption=title)

        except Exception as e:
            await update.message.reply_text(f"❌ تعذر الإرسال: {e}")

def start_bot():
    app_telegram = Application.builder().token(TOKEN).build()
    app_telegram.add_handler(CommandHandler("start", start))
    app_telegram.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle))
    app_telegram.run_polling()

if __name__ == "__main__":
    Thread(target=start_bot).start()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
