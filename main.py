# main.py
import os, re, logging, tempfile, asyncio
from threading import Thread
from pathlib import Path
from urllib.parse import urlparse

from flask import Flask
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# ===== الإعدادات =====
TOKEN = os.getenv("TELEGRAM_TOKEN")  # ضع التوكن في Environment على Render
SNAP_URL = "https://snapchat.com/add/uckr"

ALLOWED_HOSTS = {
    # X (Twitter)
    "twitter.com", "www.twitter.com", "x.com", "www.x.com", "t.co",
    # Snapchat
    "story.snapchat.com", "snapchat.com", "www.snapchat.com",
    # TikTok
    "tiktok.com", "www.tiktok.com", "vm.tiktok.com", "m.tiktok.com", "vt.tiktok.com",
}

URL_RE = re.compile(r"(https?://\S+)", re.IGNORECASE)
SNAP_USERNAME_RE = re.compile(r"^(?:@)?[a-zA-Z0-9._-]{2,32}$")

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

def snap_story_choice_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📹 الفيديو", callback_data="snap_story_v"),
         InlineKeyboardButton("🖼️ الصور", callback_data="snap_story_i")],
        [InlineKeyboardButton("📦 الكل", callback_data="snap_story_all")],
        [InlineKeyboardButton("رجوع ↩️", callback_data="snap_back")]
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
    "أرسل رابط من: **TikTok / X (Twitter) / Snapchat (روابط القصص فقط)**.\n"
    "ملاحظة: روابط حساب سناب العامة لا تكفي للتحميل؛ لازم رابط القصة `story.snapchat.com/...`"
)

# ===== مساعدات =====
def is_allowed_url(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
        return any(host == h or host.endswith("." + h) for h in ALLOWED_HOSTS)
    except Exception:
        return False

def looks_like_snap_profile(text: str) -> bool:
    if text.startswith("https://") and "snapchat.com/add/" in text:
        return True
    if text.startswith("https://www.snapchat.com/add/"):
        return True
    if SNAP_USERNAME_RE.match(text.strip().replace("https://www.snapchat.com/add/", "").replace("https://snapchat.com/add/", "")):
        return True
    return False

def ytdlp_download(url: str) -> tuple[Path, str]:
    """تنزيل بالرابط وإرجاع (مسار الملف, العنوان). قد يرفع استثناء."""
    import yt_dlp

    with tempfile.TemporaryDirectory() as td:
        outtmpl = str(Path(td) / "%(title).100s.%(ext)s")
        ydl_opts = {
            "outtmpl": outtmpl,
            "format": "bv*+ba/b",             # أعلى جودة متاحة بدون إعادة ترميز
            "merge_output_format": "mp4",
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "restrictfilenames": True,
            "retries": 10,
            "fragment_retries": 10,
            "http_headers": {                 # تقليل 403 قدر الإمكان
                "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                               "AppleWebKit/537.36 (KHTML, like Gecko) "
                               "Chrome/127.0.0.0 Safari/537.36"),
                "Referer": url,
            },
            "extractor_args": {
                "twitter": {"legacy_api": ["True"]}  # دعّم تويتر قدر الإمكان
            },
            "concurrent_fragment_downloads": 1,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)

        # التقط الملف الناتج
        dl_dir = Path(td)
        files = [p for p in dl_dir.iterdir() if p.is_file()]
        if not files:
            raise RuntimeError("لم يتم العثور على الملف بعد التحميل.")
        file_path = files[0]
        title = (isinstance(info, dict) and info.get("title")) or file_path.stem
        # انسخ لملف مؤقت دائم حتى بعد خروج الـTemporaryDirectory
        final_path = Path(tempfile.gettempdir()) / file_path.name
        file_path.replace(final_path)
        return final_path, title

# ===== Handlers =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # أول تشغيل: رسالة الترحيب، وبعدها التنبيه
    if not context.user_data.get("welcomed"):
        context.user_data["welcomed"] = True
        await update.message.reply_text(WELCOME_MSG, reply_markup=snap_keyboard(), disable_web_page_preview=False)
    else:
        await update.message.reply_text(NOTICE_MSG, reply_markup=snap_keyboard(), disable_web_page_preview=True)

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(NOTICE_MSG, reply_markup=snap_keyboard(), disable_web_page_preview=True)

async def snap_back_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.message.reply_text(NOTICE_MSG, reply_markup=snap_keyboard(), disable_web_page_preview=True)

async def snap_story_choice_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # توضيح القيود بدون كوكيز
    q = update.callback_query
    await q.answer()
    await q.message.reply_text(
        "ℹ️ لتحميل ستوريات سناب: أرسل **رابط القصة** من داخل التطبيق (يبدأ بـ `https://story.snapchat.com/...`).\n"
        "روابط الحساب العامة أو اسم المستخدم ما تعطينا الوصول للستوري بدون تسجيل دخول.",
        reply_markup=snap_keyboard(),
        disable_web_page_preview=True
    )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()

    # إذا كان شكل نص سناب حساب – اعرض نفس واجهة الاختيار
    if looks_like_snap_profile(text) and "story.snapchat.com" not in text:
        await update.message.reply_text(
            "نبذة عن الحساب 📄\n\n"
            "اختر نوع الوسائط لتحميلها من الستوري (يتطلب رابط القصة الفعلي):",
            reply_markup=snap_story_choice_kb(),
            disable_web_page_preview=True
        )
        return

    # التقط رابط
    m = URL_RE.search(text)
    if not m:
        await update.message.reply_text("أرسل رابط صالح من TikTok/X/Snapchat (القصص).", reply_markup=snap_keyboard())
        return

    url = m.group(1)
    if not is_allowed_url(url):
        await update.message.reply_text("هذا الرابط غير مدعوم حالياً.", reply_markup=snap_keyboard())
        return

    # تويتر → أرسل كـ Document للحفاظ على الأبعاد
    host = (urlparse(url).hostname or "").lower()
    send_as_document = any(h in host for h in ("twitter.com", "x.com", "t.co"))

    # اعمل التحميل في خيط منفصل
    await context.bot.send_chat_action(update.effective_chat.id, "upload_video")
    try:
        file_path, title = await asyncio.to_thread(ytdlp_download, url)
    except Exception as e:
        log.exception("Download failed")
        msg = str(e)
        if "403" in msg:
            msg = "❌ المنصة رفضت التحميل (403). جرّب رابطًا آخر أو بعد قليل."
        await update.message.reply_text(msg, reply_markup=snap_keyboard())
        return

    try:
        suffix = Path(file_path).suffix.lower()
        caption = (title or "الملف")[:990]

        if send_as_document:
            await update.message.reply_document(document=open(file_path, "rb"), caption=caption, reply_markup=snap_keyboard())
        else:
            if suffix in {".mp4", ".mov", ".mkv", ".webm"}:
                await update.message.reply_video(video=open(file_path, "rb"), caption=caption, reply_markup=snap_keyboard())
            elif suffix in {".jpg", ".jpeg", ".png", ".gif"}:
                await update.message.reply_photo(photo=open(file_path, "rb"), caption=caption, reply_markup=snap_keyboard())
            else:
                await update.message.reply_document(document=open(file_path, "rb"), caption=caption, reply_markup=snap_keyboard())
    except Exception:
        log.exception("Send failed")
        await update.message.reply_text("❌ تعذر إرسال الوسائط (قد يكون الحجم تعدّى حد تيليجرام).", reply_markup=snap_keyboard())
    finally:
        try:
            Path(file_path).unlink(missing_ok=True)
        except Exception:
            pass

# ===== تشغيل =====
def run_flask():
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")), debug=False)

def main():
    if not TOKEN:
        raise RuntimeError("حدد TELEGRAM_TOKEN في Render → Environment.")

    app_tg = Application.builder().token(TOKEN).build()

    app_tg.add_handler(CommandHandler("start", start))
    app_tg.add_handler(CommandHandler("help", help_cmd))
    app_tg.add_handler(CallbackQueryHandler(snap_back_callback, pattern="^snap_back$"))
    app_tg.add_handler(CallbackQueryHandler(snap_story_choice_cb, pattern="^snap_story_"))
    app_tg.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # صحّة: Flask في ثريد جانبي
    Thread(target=run_flask, daemon=True).start()

    # نستخدم polling فقط
    try:
        app_tg.bot.delete_webhook(drop_pending_updates=False)
    except Exception:
        pass

    logging.info("✅ Telegram polling started")
    app_tg.run_polling(
        allowed_updates=Update.ALL_TYPES,
        stop_signals=None,   # منع مشاكل set_wakeup_fd على Render
        close_loop=False
    )

if __name__ == "__main__":
    main()
