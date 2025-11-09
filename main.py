# main.py — Telegram media downloader (sends as Document)
import os, re, asyncio, logging, tempfile, shutil
from pathlib import Path
from threading import Thread

from flask import Flask
from telegram import Update, InputFile
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ContextTypes, filters
)
import yt_dlp

# ====== إعدادات عامة ======
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
)
log = logging.getLogger("bot")

TOKEN = os.getenv("TELEGRAM_TOKEN")  # ضع التوكن في Render Env Var فقط
if not TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN not set!")

# ====== Flask للـ health check على Render ======
app = Flask(__name__)

@app.get("/")
def root():
    return "OK", 200

def run_flask():
    port = int(os.getenv("PORT", "10000"))
    # تشغيل Flask في ثريد جانبي عشان ما يعطل البوت
    app.run(host="0.0.0.0", port=port, threaded=True)

# ====== أدوات ======
URL_RE = re.compile(r"https?://\S+", re.I)

YDL_OPTS = {
    # أفضل جودة ممكنة بدون إنقاص
    "format": "bv*+ba/best",
    "merge_output_format": "mp4",
    "noplaylist": True,
    "restrictfilenames": True,
    "outtmpl": "%(title).200s.%(ext)s",
    "concurrent_fragment_downloads": 8,
    "quiet": True,
    "no_warnings": True,
    # مفيد لبعض المواقع
    "http_headers": {"User-Agent": "Mozilla/5.0"},
    # لا تستخدم الكوكيز (بعض المواقع قد ترفض بدون تسجيل دخول)
    # لو احتجته لاحقاً نضيفه اختياري.
}

MAX_TG_FILE = 2 * 1024 * 1024 * 1024  # حد تيليجرام 2GB

async def send_as_document(update: Update, context: ContextTypes.DEFAULT_TYPE, file_path: Path, caption: str):
    size = file_path.stat().st_size
    if size >= MAX_TG_FILE:
        await update.effective_chat.send_message(
            f"⚠️ حجم الملف {size/1024/1024:.1f} MB أكبر من حد تيليجرام (2GB). جرّب رابط بجودة أقل."
        )
        return
    with file_path.open("rb") as f:
        await context.bot.send_document(
            chat_id=update.effective_chat.id,
            document=InputFile(f, filename=file_path.name),
            caption=caption[:1024]
        )

def sanitize_title(title: str) -> str:
    return re.sub(r"[\\/:*?\"<>|]+", "_", title).strip() or "video"

# ====== Handlers ======
async def cmd_start(update: Update, _: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "أهلًا! أرسل أي رابط فيديو (تيك توك/يوتيوب/تويتر/إنستقرام…)\n"
        "سأحاول تحميله وإرساله لك **كملف (Document)** بدون تقليل جودة 🎬"
    )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.effective_message.text or ""
    m = URL_RE.search(text)
    if not m:
        return  # تجاهل أي رسالة بدون رابط

    url = m.group(0).strip()

    # دعم روابط تيك توك الحديثة vt.tiktok.com
    if "vt.tiktok.com" in url and not url.endswith("/"):
        url += "/"  # بعض الروابط تحتاج سلاش أخير

    await update.effective_chat.send_message("⏳ جاري التحميل…")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        opts = dict(YDL_OPTS)
        opts["outtmpl"] = str(tmp / "%(title).200s.%(ext)s")

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                # استخرج المسار الفعلي للملف الناتج
                if "_filename" in info:
                    out = Path(info["_filename"])
                else:
                    title = sanitize_title(info.get("title") or "video")
                    ext = info.get("ext") or "mp4"
                    out = tmp / f"{title}.{ext}"

            if not out.exists():
                # أحيانًا يكون المسار النهائي عبر entries
                entries = info.get("entries") or []
                for it in entries:
                    if it.get("_filename"):
                        out = Path(it["_filename"])
                        if out.exists():
                            break

            if not out.exists():
                raise FileNotFoundError("لم أجد الملف الناتج بعد التحميل.")

            await send_as_document(update, context, out, caption=info.get("title") or "")

        except yt_dlp.utils.DownloadError as e:
            msg = str(e)
            # رسائل ودّية لأشهر المشاكل
            if "login required" in msg.lower() or "rate-limit" in msg.lower() or "private" in msg.lower():
                await update.effective_chat.send_message(
                    "❌ المنصّة تطلب تسجيل دخول أو تجاوز حد الاستخدام. بدون كوكيز قد يرفض الموقع.\n"
                    "جرّب رابط آخر أو منصة أخرى."
                )
            else:
                await update.effective_chat.send_message(f"❌ فشل التحميل:\n{msg[:900]}")
            log.exception("Download error")
        except Exception as e:
            await update.effective_chat.send_message(f"❌ صار خطأ غير متوقع.")
            log.exception("Unexpected error: %s", e)

# ====== تشغيل البوت و Flask ======
def build_app() -> Application:
    app_tg = Application.builder().token(TOKEN).build()
    app_tg.add_handler(CommandHandler("start", cmd_start))
    app_tg.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    return app_tg

def main():
    # شغّل Flask أولًا في ثريد جانبي
    Thread(target=run_flask, daemon=True).start()

    # شغّل البوت في الـ Main Thread لتفادي مشاكل event loop
    app_tg = build_app()
    log.info("✅ Logged in, starting polling…")
    # run_polling يدير الـ loop بنفسه. لا نستخدم asyncio.run هنا.
    app_tg.run_polling(
        allowed_updates=Update.ALL_TYPES,
        stop_signals=None,   # لا تسجل إشارات OS (مهم على بعض المنصات)
        close_loop=True
    )

if __name__ == "__main__":
    main()
