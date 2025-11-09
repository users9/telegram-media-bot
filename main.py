# main.py
import os, re, logging, tempfile, asyncio, threading
from pathlib import Path

from flask import Flask, jsonify
from telegram import (
    Update, InlineKeyboardMarkup, InlineKeyboardButton, InputFile
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)
import yt_dlp

# ========= إعدادات عامة =========
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("bot")

TOKEN = os.getenv("TELEGRAM_TOKEN")  # لا تكتب التوكن صريح، خله من المتغير
if not TOKEN:
    raise RuntimeError("متغير البيئة TELEGRAM_TOKEN غير موجود")

# سناب شات حقّك
SNAP_URL = "https://www.snapchat.com/add/uckr"

# ========= أزرار ورسائل =========
def snap_keyboard() -> InlineKeyboardMarkup:
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
    "أرسل رابط من: YouTube / Instagram / X / Snapchat / TikTok."
)

# ========= Flask (لـ Render) =========
web = Flask(__name__)

@web.get("/")
def root():
    return jsonify(ok=True, msg="telegram-media-bot is live")

def run_flask():
    port = int(os.getenv("PORT", "10000"))
    web.run(host="0.0.0.0", port=port, debug=False)

# ========= أدوات مساعدة =========
URL_RGX = re.compile(r"https?://\S+", re.I)

def normalize_url(url: str) -> str:
    url = url.strip()

    # دعم vt.tiktok.com المختصر
    if "vt.tiktok.com" in url:
        # yt-dlp يحله مباشرة، بس نتأكد أن البروتوكول مضبوط
        if not url.startswith("http"):
            url = "https://" + url

    # تنظيف روابط تيك توك/انستا المعقّدة جدًا
    if "tiktok.com" in url and "?_" in url:
        url = url.split("?")[0]
    if "instagram.com" in url and "?__" in url:
        url = url.split("?")[0]

    return url

def ytdlp_opts(temp_dir: Path) -> dict:
    out = str(temp_dir / "%(title).200B-%(id)s.%(ext)s")
    return {
        # أعلى جودة متاحة بدون تخفيض
        "format": "bestvideo*+bestaudio/best",
        "merge_output_format": "mp4",
        "outtmpl": out,
        "noplaylist": True,
        "quiet": True,
        "concurrent_fragments": 8,
        "retries": 5,
        "fragment_retries": 5,
        "nocheckcertificate": True,
        "geo_bypass": True,
        # لا نستخدم كوكيز (مثل ما طلبت)
        "cookiesfrombrowser": None,
    }

def download_media(url: str) -> Path:
    url = normalize_url(url)
    with tempfile.TemporaryDirectory() as td:
        temp_dir = Path(td)
        with yt_dlp.YoutubeDL(ytdlp_opts(temp_dir)) as ydl:
            info = ydl.extract_info(url, download=True)
            if "requested_downloads" in info and info["requested_downloads"]:
                filepath = info["requested_downloads"][0]["filepath"]
            else:
                filepath = ydl.prepare_filename(info)
        return Path(filepath).resolve()

# ========= Handlers =========
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.effective_chat.send_message(WELCOME_MSG, reply_markup=snap_keyboard(), parse_mode="Markdown")
    await update.effective_chat.send_message(NOTICE_MSG, parse_mode="Markdown")

async def on_snap_back(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q:
        await q.answer("حيّاك!")
        await q.edit_message_reply_markup(reply_markup=None)
    await update.effective_chat.send_message("أرسل الرابط الآن 👇")

async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    text = update.message.text or ""
    m = URL_RGX.search(text)
    if not m:
        await update.message.reply_text("أرسل رابط الفيديو مباشرة 👇")
        return

    url = m.group(0)
    await update.message.reply_text("⏳ جاري التحميل بأعلى جودة متاحة…")

    try:
        file_path = download_media(url)
        file_name = file_path.name

        # دائمًا نرسل كـ document (مثل طلبك) لتفادي ضغط/تحويل التيليجرام
        async with ctx.bot:
            with file_path.open("rb") as f:
                await update.effective_chat.send_document(
                    document=InputFile(f, filename=file_name),
                    caption=f"تم التحميل ✅\n{url}"
                )

    except yt_dlp.utils.DownloadError as e:
        msg = str(e)
        # انستا غالبًا يحتاج كوكيز/تسجيل دخول – نشرح للمستخدم باختصار
        if "login required" in msg.lower() or "rate-limit" in msg.lower():
            tip = "انستقرام قد يتطلب تسجيل دخول. حاليًا نعمل بدون كوكيز.\nجرب رابطًا آخر أو منصة مختلفة."
        else:
            tip = "تأكد من صحة الرابط أو جرب لاحقًا."
        await update.effective_chat.send_text(f"❌ حدث خطأ أثناء التحميل.\n\nالمنصة قد تمنع التحميل أو تتطلب تسجيل دخول.\n{tip}")
        log.exception("yt-dlp error")

    except Exception as e:
        await update.effective_chat.send_text("❌ حدث خطأ غير متوقع.")
        log.exception("unexpected error")

# ========= تشغيل =========
def build_application() -> Application:
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(on_snap_back, pattern="^snap_back$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    return app

def main():
    # شغّل Flask في خلفية
    threading.Thread(target=run_flask, daemon=True).start()

    # شغّل تيليجرام في الـ main thread (عشان مشاكل الإشارات/اللووب)
    application = build_application()
    # حذف أي webhook سابق والتحويل إلى polling
    async def _prep():
        try:
            me = await application.bot.get_me()
            log.info("✅ Logged in as @%s (id=%s)", me.username, me.id)
            await application.bot.delete_webhook(drop_pending_updates=True)
        except Exception:
            log.exception("webhook cleanup failed")

    asyncio.run(_prep())
    log.info("✅ Telegram polling started")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
