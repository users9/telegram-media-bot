# main.py — Telegram media bot (TikTok + X/Twitter + Snapchat Spotlight)
# PTB v21.x / Python 3.12+ / Render keep-alive via Flask

import os
import re
import asyncio
import logging
import tempfile
from pathlib import Path
from typing import Optional

from flask import Flask
from threading import Thread

from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ===== إعدادات عامة =====
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

TOKEN = os.getenv("TELEGRAM_TOKEN")  # لا تضع التوكن صريحاً؛ خله متغيّر بيئة في Render
PORT = int(os.getenv("PORT", "10000"))

# رابط السناب حقك
SNAP_URL = "https://www.snapchat.com/add/uckr"

# ===== رسائل وأزرار =====
def snap_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("👻 إضافة السناب", url=SNAP_URL)],
            [InlineKeyboardButton("✅ تم، رجعت", callback_data="snap_back")],
        ]
    )

WELCOME_MSG = (
    "👋 **مرحبًا!**\n\n"
    f"قبل ما نبدأ… ياليت تضيفني على السناب:\n🔗 {SNAP_URL}\n\n"
    "بعد الإضافة ارجع واضغط **تم، رجعت** أو أرسل **/start** مرة ثانية."
)

NOTICE_MSG = (
    "⚠️ **تنبيه مهم:**\n"
    "لا أُحِل ولا أتحمّل أي مسؤولية عن استخدام البوت في تحميل ما لا يرضي الله.\n"
    "رجاءً استخدمه في الخير فقط.\n\n"
    "أرسل رابط من: **TikTok / X (Twitter) / Snapchat Spotlight**."
)

HELP_MSG = (
    "أرسل لي رابط فيديو من:\n"
    "• TikTok (بما فيها vt.tiktok.com)\n"
    "• X (twitter.com / x.com)\n"
    "• Snapchat Spotlight فقط\n\n"
    "سيتم الإرسال كـ *ملف (Document)* للمحافظة على المقاس والجودة."
)

UNSUPPORTED_SNAP_MSG = (
    "حالياً أدعم *Snapchat Spotlight* فقط.\n"
    "روابط الحساب/القصص تتطلب تسجيل دخول ولا أدعمها الآن."
)

# ===== كشف المنصّة =====
RE_TIKTOK = re.compile(r"(?:tiktok\.com|vt\.tiktok\.com)", re.I)
RE_TWITTER = re.compile(r"(?:twitter\.com|x\.com)", re.I)
RE_SNAP_SPOT = re.compile(r"(?:snapchat\.com/.*/spotlight|snapchat\.com/spotlight)", re.I)

def detect_platform(url: str) -> Optional[str]:
    if RE_TIKTOK.search(url):
        return "tiktok"
    if RE_TWITTER.search(url):
        return "twitter"
    if RE_SNAP_SPOT.search(url):
        return "snap"
    return None

# ===== تنزيل بدون إعادة ترميز (yt-dlp) وإرسال كـ Document =====
# ملاحظة: نرسل Document لكي لا يغيّر تيليجرام المقاس/الجودة.
async def ytdlp_download(url: str) -> Path:
    """
    ينزّل أفضل فيديو+صوت بدون تحويل، ويُرجع مسار الملف النهائي.
    """
    import yt_dlp  # استيراد داخل الدالة لتسريع بدء البوت

    tmpdir = Path(tempfile.mkdtemp(prefix="dl_"))
    outtmpl = str(tmpdir / "%(title).200B.%(ext)s")

    # صيغة تفضّل mp4/opus إن وُجد وتضمن دمج بدون re-encode
    ydl_opts = {
        "quiet": True,
        "noprogress": True,
        "outtmpl": outtmpl,
        "merge_output_format": "mp4",
        "format": "bv*+ba/b",
        "postprocessors": [],  # لا تحويل
        # تقليل احتمال تشغيل بثوث HLS فقط إن لزم
        "http_headers": {
            "User-Agent": "Mozilla/5.0"
        },
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        file_path = Path(ydl.prepare_filename(info))
        # yt-dlp قد يغير الامتداد بعد الدمج إلى .mp4
        if not file_path.exists():
            # حاول إيجاد أي ملف داخل المجلد
            cand = list(tmpdir.glob("*"))
            if cand:
                file_path = cand[0]
        return file_path

async def send_as_document(update: Update, context: ContextTypes.DEFAULT_TYPE, file_path: Path, caption: str):
    # اسم ملف واضح
    caption = (caption or "")[:1024]
    try:
        await update.effective_chat.send_document(
            document=file_path.open("rb"),
            filename=file_path.name,
            caption=caption,
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception as e:
        log.exception("send_document failed: %s", e)
        await update.effective_chat.send_message("حدث خطأ أثناء الإرسال. جرّب رابطًا آخر.")

# ===== Handlers =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(WELCOME_MSG, reply_markup=snap_keyboard(), parse_mode=ParseMode.MARKDOWN)
    await update.message.reply_text(NOTICE_MSG, parse_mode=ParseMode.MARKDOWN)

async def on_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer("حياك 🌟")
    await update.callback_query.edit_message_text("تم ✅ رجعت. أرسل الرابط الآن.")
    await update.callback_query.message.reply_text(HELP_MSG)

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()

    # مجرد ترحيب/مساعدة
    if text.lower() in {"help", "/help"}:
        await update.message.reply_text(HELP_MSG)
        return

    # لازم URL
    if not re.search(r"https?://", text):
        await update.message.reply_text("أرسل رابط فيديو صالح.")
        return

    platform = detect_platform(text)
    if platform is None:
        # سناب غير Spotlight؟
        if "snapchat.com" in text.lower():
            await update.message.reply_text(UNSUPPORTED_SNAP_MSG, parse_mode=ParseMode.MARKDOWN)
        else:
            await update.message.reply_text("الرابط غير مدعوم. المنصّات المدعومة: TikTok / X / Snapchat Spotlight.")
        return

    # تنزيل ثم إرسال كـ Document للحفاظ على المقاس والجودة
    status = await update.message.reply_text("⏳ جاري التحميل…")
    try:
        file_path = await ytdlp_download(text)
        cap = f"تم التحميل من **{platform.title()}**"
        await send_as_document(update, context, file_path, cap)
        await status.edit_text("✅ تم الإرسال.")
    except Exception as e:
        log.exception("download error: %s", e)
        await status.edit_text("❌ حدث خطأ غير متوقع أثناء التحميل.")
    finally:
        # تنظيف الملفات المؤقتة
        try:
            if 'file_path' in locals() and file_path.exists():
                file_path.unlink(missing_ok=True)
                file_path.parent.rmdir()
        except Exception:
            pass

# ===== Flask keep-alive (Render) =====
app = Flask(__name__)

@app.route("/")
def index():
    return "OK", 200

def run_flask():
    app.run(host="0.0.0.0", port=PORT, debug=False)

# ===== Boot =====
async def run_bot():
    application = Application.builder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(on_back, pattern="^snap_back$"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # polling داخل نفس الحدث (async)
    await application.initialize()
    await application.start()
    # مهم: إزالة أي Webhook
    try:
        await application.bot.delete_webhook()
    except Exception:
        pass
    log.info("✅ Bot is running (polling)")
    await application.run_polling(stop_signals=None, close_loop=False)

def main():
    # شغّل Flask في ثريد جانبي
    Thread(target=run_flask, daemon=True).start()
    asyncio.run(run_bot())

if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("TELEGRAM_TOKEN غير موجود في متغيرات البيئة!")
    main()
