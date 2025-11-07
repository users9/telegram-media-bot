import os
import re
import tempfile
from pathlib import Path
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# نقرأ التوكن من متغيرات البيئة في Render
TOKEN = os.getenv("TELEGRAM_TOKEN")
URL_RE = re.compile(r"(https?://\S+)", re.IGNORECASE)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("مرحبًا! أرسل رابط فيديو/صورة وسأحاول تحميله لك ✅")

async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    m = URL_RE.search(text)
    if not m:
        return

    url = m.group(1)
    await update.message.chat.send_action(ChatAction.UPLOAD_DOCUMENT)

    # نستخدم yt-dlp للتنزيل
    import yt_dlp

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
            "nocheckcertificate": True,
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as y:
                info = y.extract_info(url, download=True)
                fname = Path(info.get("_filename") or next(Path(td).glob("*")))
        except Exception as e:
            await update.message.reply_text(f"حصل خطأ أثناء التحميل: {e}")
            return

        try:
            # نحدد نوع الملف ونرسله
            suffix = fname.suffix.lower()
            title = (info.get("title") or "الملف")[:990]
            if suffix in {".mp4", ".webm", ".mov", ".mkv"}:
                await update.message.reply_video(video=fname.open("rb"), caption=title)
            elif suffix in {".jpg", ".jpeg", ".png", ".gif"}:
                await update.message.reply_photo(photo=fname.open("rb"), caption=title)
            else:
                await update.message.reply_document(document=fname.open("rb"), caption=title)
        except Exception as e:
            await update.message.reply_text(f"تعذر الإرسال: {e}")

def main():
    if not TOKEN:
        raise RuntimeError("حدد TELEGRAM_TOKEN في بيئة التشغيل (Render).")
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle))
    app.run_polling()

if __name__ == "__main__":
    main()
