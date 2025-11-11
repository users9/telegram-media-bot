# main.py
import os
import re
import logging
import tempfile
from threading import Thread
from pathlib import Path
from urllib.parse import urlparse

from flask import Flask
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.constants import ChatAction
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# ===== الإعدادات =====
TOKEN = os.getenv("TELEGRAM_TOKEN")  # ضع التوكن في Render كمتغير بيئة
SNAP_URL = "https://snapchat.com/add/uckr"

# المسموح الآن: TikTok / X (Twitter) / Snapchat
ALLOWED_HOSTS = {
    # X (Twitter)
    "twitter.com", "www.twitter.com", "x.com", "www.x.com", "t.co",
    # Snapchat
    "snapchat.com", "www.snapchat.com", "story.snapchat.com",
    # TikTok
    "tiktok.com", "www.tiktok.com", "vm.tiktok.com", "m.tiktok.com", "vt.tiktok.com"
}
URL_RE = re.compile(r"(https?://\S+)", re.IGNORECASE)

# ===== Flask للـ Health Check =====
app = Flask(__name__)

@app.route("/")
def home():
    return "OK"

# ===== أزرار ورسائل =====
def snap_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👻 إضافة السناب", url=SNAP_URL)],
        [InlineKeyboardButton("✅ تم، رجعت", callback_data="snap_back")]
    ])

def snap_profile_choices() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🎥 ستوري الفيديو فقط", callback_data="snap_dl_video"),
            InlineKeyboardButton("🖼️ ستوري الصور فقط", callback_data="snap_dl_image")
        ],
        [InlineKeyboardButton("📦 الكل (صور + فيديو)", callback_data="snap_dl_all")],
        [InlineKeyboardButton("⬅️ رجوع", callback_data="snap_back")]
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
    "أرسل رابط من: TikTok / X (Twitter) / Snapchat."
)

def is_allowed(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
        return any(host == h or host.endswith("." + h) for h in ALLOWED_HOSTS)
    except Exception:
        return False

# ===== util: تنزيل عبر yt-dlp =====
def build_ydl_opts(output_dir: Path) -> dict:
    outtmpl = str(output_dir / "%(title).100s.%(ext)s")
    return {
        "outtmpl": outtmpl,
        "format": "bv*+ba/b",               # أفضل فيديو+صوت ممكن، وإن ما توفر فملف واحد
        "merge_output_format": "mp4",       # دمج (copy) إلى mp4 غالبًا بدون إعادة ترميز
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "restrictfilenames": True,
        "concurrent_fragment_downloads": 1,
        "retries": 5,
        "fragment_retries": 5,
        "nocheckcertificate": True,
        "http_headers": {
            # يساعد ضد 403 لبعض المواقع
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.8,ar;q=0.6",
        },
    }

async def download_and_send(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str):
    chat_id = update.effective_chat.id
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

    host = (urlparse(url).hostname or "").lower()
    send_as_document = ("twitter.com" in host) or ("x.com" in host) or ("t.co" in host)

    try:
        import yt_dlp
    except Exception:
        await context.bot.send_message(chat_id=chat_id, text="❌ مكتبة yt-dlp غير مثبتة.")
        return

    with tempfile.TemporaryDirectory() as td:
        tmpdir = Path(td)
        ydl_opts = build_ydl_opts(tmpdir)

        info = None
        file_path = None
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)

            # التقط اسم الملف الناتج
            if isinstance(info, dict):
                fn = info.get("_filename")
                if fn and Path(fn).exists():
                    file_path = Path(fn)

            # أو أي ملف داخل المجلد
            if not file_path:
                for p in tmpdir.iterdir():
                    if p.is_file():
                        file_path = p
                        break
        except Exception as e:
            log.exception("Download failed", exc_info=e)
            await context.bot.send_message(
                chat_id=chat_id,
                text="❌ حدث خطأ أثناء التحميل (قد تكون المنصة تمنع الوصول أو الفيديو محمي)."
            )
            return

        if not file_path or not file_path.exists():
            await context.bot.send_message(chat_id=chat_id, text="❌ تعذر العثور على الملف بعد التحميل.")
            return

        title = (isinstance(info, dict) and info.get("title")) or "الملف"
        title = (title or "الملف")[:990]
        suffix = file_path.suffix.lower()

        # أرسل “يرفع” حسب النوع
        try:
            if send_as_document:
                await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_DOCUMENT)
                await update.message.reply_document(
                    document=file_path.open("rb"),
                    caption=title,
                    reply_markup=snap_keyboard()
                )
            else:
                if suffix in {".mp4", ".mov", ".mkv", ".webm"}:
                    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_VIDEO)
                    await update.message.reply_video(
                        video=file_path.open("rb"),
                        caption=title,
                        reply_markup=snap_keyboard()
                    )
                elif suffix in {".jpg", ".jpeg", ".png", ".gif"}:
                    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_PHOTO)
                    await update.message.reply_photo(
                        photo=file_path.open("rb"),
                        caption=title,
                        reply_markup=snap_keyboard()
                    )
                else:
                    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_DOCUMENT)
                    await update.message.reply_document(
                        document=file_path.open("rb"),
                        caption=title,
                        reply_markup=snap_keyboard()
                    )
        except Exception as e:
            log.exception("Send failed", exc_info=e)
            await update.message.reply_text(
                "❌ تعذر إرسال الوسائط (قد يكون الحجم كبيرًا لقيود تيليجرام).",
                reply_markup=snap_keyboard()
            )

# ===== Handlers =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # أول /start: ترحيب + زر السناب؛ بعد الرجوع نرسل التنبيه
    welcomed = context.user_data.get("welcomed", False)
    if not welcomed:
        context.user_data["welcomed"] = True
        await update.message.reply_text(WELCOME_MSG, reply_markup=snap_keyboard(), disable_web_page_preview=True)
    else:
        await update.message.reply_text(NOTICE_MSG, reply_markup=snap_keyboard(), disable_web_page_preview=True)

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(NOTICE_MSG, reply_markup=snap_keyboard(), disable_web_page_preview=True)

async def snap_back_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.message.reply_text(NOTICE_MSG, reply_markup=snap_keyboard(), disable_web_page_preview=True)

async def snap_profile_choice_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ردود أزرار سناب لحساب كامل (تنبيه: تنزيل ستوريات حساب يتطلب عادة تسجيل دخول/كوكيز)."""
    q = update.callback_query
    await q.answer()
    choice = q.data  # snap_dl_video / snap_dl_image / snap_dl_all
    await q.message.reply_text(
        "ℹ️ لتنزيل ستوريات حساب سناب بالكامل يلزم رابط ستوري مباشر من `story.snapchat.com` "
        "أو ملفات عامة غير محمية. حاليًا التحميل من رابط حساب يتطلب تسجيل دخول (غير مفعّل هنا).\n\n"
        "أرسل رابط ستوري مباشر وسأنزل لك المحتوى.",
        reply_markup=snap_keyboard(),
        disable_web_page_preview=True
    )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    m = URL_RE.search(text)
    if not m:
        await update.message.reply_text("أرسل رابط مباشر من TikTok / X / Snapchat.", reply_markup=snap_keyboard())
        return

    url = m.group(1)
    if not is_allowed(url):
        await update.message.reply_text("الرابط غير مدعوم. استخدم TikTok / X / Snapchat فقط.", reply_markup=snap_keyboard())
        return

    host = (urlparse(url).hostname or "").lower()

    # إذا أرسل رابط حساب سناب (وليس story)، نعطيه خيارات وهمية (تنبيه: يتطلب تسجيل دخول)
    if "snapchat.com" in host and "story.snapchat.com" not in host:
        await update.message.reply_text(
            "اختر ماذا تريد من ستوريات الحساب (يتطلب عادةً تسجيل دخول — غير مفعّل):",
            reply_markup=snap_profile_choices(),
            disable_web_page_preview=True
        )
        return

    # أي رابط مباشر (تيك توك / تويتر / ستوري سناب) ننزله
    await download_and_send(update, context, url)

# ===== تشغيل Flask + البوت =====
def run_flask():
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")), debug=False)

def main():
    if not TOKEN:
        raise RuntimeError("حدد TELEGRAM_TOKEN في Render → Environment (القيمة هي التوكن فقط).")

    application = Application.builder().token(TOKEN).build()

    # Handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_cmd))
    application.add_handler(CallbackQueryHandler(snap_back_callback, pattern=r"^snap_back$"))
    application.add_handler(CallbackQueryHandler(snap_profile_choice_callback, pattern=r"^snap_dl_(video|image|all)$"))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_text))

    # شغّل Flask في ثريد مستقل
    Thread(target=run_flask, daemon=True).start()

    # Polling بدون إشارات وبلا إغلاق لوب النظام (حل مشاكل Render والـ event loop)
    try:
        application.bot.delete_webhook(drop_pending_updates=False)
    except Exception:
        pass

    log.info("✅ Telegram polling started")
    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        stop_signals=None,   # تفادي set_wakeup_fd بسبب الثريد
        close_loop=False     # لا تغلق لوب النظام
    )

if __name__ == "__main__":
    main()
