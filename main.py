# main.py — Telegram media bot (TikTok + Twitter/X) + Snap button
import os
import re
import asyncio
import logging
import tempfile
from pathlib import Path

from aiohttp import web

from telegram import (
    Update, InlineKeyboardMarkup, InlineKeyboardButton, InputFile
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes,
    filters
)

import yt_dlp

# ---------- الإعدادات ----------
TOKEN = os.getenv("TELEGRAM_TOKEN")  # لا تضع التوكن صريحاً، خله من env في Render
PORT = int(os.getenv("PORT", "10000"))

SNAP_URL = "https://www.snapchat.com/add/uckr"  # عدّل رابط سنابك هنا

# رسائل + أزرار
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
    "أرسل رابط من: **TikTok / Twitter (X)**."
)

# أنماط الروابط المدعومة
RE_TIKTOK = re.compile(r"(?:https?://)?(?:www\.)?(?:tiktok\.com|vt\.tiktok\.com)/", re.I)
RE_TWITTER = re.compile(r"(?:https?://)?(?:twitter\.com|x\.com)/", re.I)

# ---------- إعداد yt-dlp ----------
# نمنع أي إعادة ترميز ونطلب أفضل mp4/ m4a قدر الإمكان
YDL_OPTS_BASE = {
    "quiet": True,
    "no_warnings": True,
    "noprogress": True,
    "noplaylist": True,
    "merge_output_format": "mp4",   # دمج بدون إعادة ترميز
    "postprocessors": [],           # لا FFmpeg re-encode
    # أعلى جودة ممكنة بدون إجبار ترميز جديد
    "format": (
        "bv*[ext=mp4]+ba[ext=m4a]/"
        "bv*+ba/b[ext=mp4]/b/best"
    ),
    "outtmpl": "%(title).200B.%(ext)s",
    # تعطيل قيود السرعة الافتراضية
    "ratelimit": 0,
}

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("bot")

# ---------- تحميل الوسائط ----------
async def download_media(url: str) -> Path:
    temp_dir = Path(tempfile.mkdtemp(prefix="grab-"))
    out_template = str(temp_dir / "%(title).200B.%(ext)s")
    opts = dict(YDL_OPTS_BASE)
    opts["outtmpl"] = out_template

    # خاص لتويتر: أحياناً أفضل ملف mp4 موجود باسم container=mp4
    if RE_TWITTER.search(url):
        opts["format"] = (
            "((bv*[vcodec~='^((?!av01).)*$'][ext=mp4])"
            "+(ba[acodec~='^((?!opus).)*$'][ext=m4a]))/"
            "best[ext=mp4]/best"
        )

    def _run():
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            path = Path(ydl.prepare_filename(info))
            # في حال الدمج ينتج اسم مُختلف أحياناً
            if not path.exists():
                # ابحث عن ملف mp4 في المجلد
                for p in temp_dir.iterdir():
                    if p.suffix.lower() in (".mp4", ".mov", ".m4v"):
                        return p
            return path

    loop = asyncio.get_running_loop()
    file_path: Path = await loop.run_in_executor(None, _run)
    return file_path

# ---------- Handlers ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_chat.send_message(WELCOME_MSG, reply_markup=snap_keyboard(), parse_mode="Markdown")
    await update.effective_chat.send_message(NOTICE_MSG, parse_mode="Markdown")

async def on_back_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer("يعطيك العافية ✅")
    await update.callback_query.message.reply_text("أرسل الرابط الآن 👇")

def is_supported(url: str) -> bool:
    return bool(RE_TIKTOK.search(url) or RE_TWITTER.search(url))

async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.effective_message.text or "").strip()
    if not is_supported(text):
        await update.effective_message.reply_text(
            "أرسل رابط **TikTok** أو **Twitter (X)** فقط حاليًا.",
            parse_mode="Markdown"
        )
        return

    msg = await update.effective_message.reply_text("⏳ يتم التحميل…")
    try:
        path = await download_media(text)
        caption = Path(path).stem[:1024]

        # نرسل كـ Document حتى ما يعيد تيليجرام ضغط/تغيير الأبعاد
        async with await context.bot.send_document(
            chat_id=update.effective_chat.id,
            document=InputFile(path.open("rb"), filename=path.name),
            caption=caption
        ) as _:
            pass

        await msg.edit_text("✅ تم الإرسال كملف (Document) بدون أي تغيير في المقاس/الجودة.")
    except yt_dlp.utils.DownloadError as e:
        log.exception("yt-dlp error")
        await msg.edit_text(f"❌ تعذر التحميل: {e.exc_info[1] if hasattr(e, 'exc_info') else str(e)}")
    except Exception as e:
        log.exception("unexpected")
        await msg.edit_text(f"❌ حدث خطأ غير متوقع: {e}")

# ---------- AIOHTTP Health (لـ Render) ----------
async def health(_: web.Request):
    return web.Response(text="OK")

async def run_http_server():
    app = web.Application()
    app.add_routes([web.get("/", health)])
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    log.info("HTTP health server on :%s", PORT)

# ---------- الإقلاع ----------
async def run_bot():
    application = Application.builder().token(TOKEN).build()

    # Handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(on_back_cb, pattern="^snap_back$"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))

    # لا نسجّل سيجنالات (رن داخل نفس اللوب)
    await application.bot.delete_webhook(drop_pending_updates=True)
    await application.initialize()
    await application.start()
    log.info("✅ Bot polling started")

    # شغّل سيرفر الصحة و polling معًا
    await run_http_server()
    try:
        await application.updater.start_polling(allowed_updates=Update.ALL_TYPES)
        await application.updater.wait_until_stopped()
    finally:
        await application.stop()
        await application.shutdown()

def main():
    if not TOKEN:
        raise RuntimeError("TELEGRAM_TOKEN مفقود من Environment Variables.")
    asyncio.run(run_bot())

if __name__ == "__main__":
    main()
