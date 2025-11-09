# main.py — PTB v21.6 + Flask + ffmpeg via imageio-ffmpeg
import os, re, tempfile, logging
from pathlib import Path
from urllib.parse import urlparse

from flask import Flask
from threading import Thread

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.constants import ChatAction
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# ====== إعدادات عامة ======
TOKEN = os.getenv("TELEGRAM_TOKEN")
SNAP_URL = "https://snapchat.com/add/uckr"

# منع التخفيض: نحاول دائمًا أعلى جودة
FORCE_BEST_QUALITY = True

# دعم المنصات + نطاقات تيك توك الجديدة
ALLOWED_HOSTS = {
    # YouTube
    "youtube.com","www.youtube.com","youtu.be",
    # X (Twitter)
    "twitter.com","www.twitter.com","x.com","www.x.com",
    # Snapchat
    "snapchat.com","www.snapchat.com","story.snapchat.com",
    # Instagram
    "instagram.com","www.instagram.com",
    # TikTok
    "tiktok.com","www.tiktok.com","m.tiktok.com","vm.tiktok.com","vt.tiktok.com"
}

URL_RE = re.compile(r"(https?://\S+)", re.IGNORECASE)

# ====== Flask Health Check ======
app = Flask(__name__)
@app.route("/")
def home():
    return "Bot is running!"

# ====== UI ======
def snap_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👻 إضافة السناب", url=SNAP_URL)],
        [InlineKeyboardButton("✅ تم، رجعت", callback_data="snap_back")]
    ])

WELCOME_MSG = (
    "👋 **مرحبًا!**\n\n"
    "قبل ما نبدأ… ياليت تضيفني على السناب:\n"
    f"🔗 {SNAP_URL}\n\n"
    "بعد الإضافة، ارجع واضغط **تم، رجعت** أو أرسل **/start** مرة ثانية."
)
NOTICE_MSG = (
    "⚠️ **تنبيه مهم:**\n"
    "لا أُحِل ولا أتحمّل أي مسؤولية عن استخدام البوت في تحميل ما لا يرضي الله.\n"
    "رجاءً استخدمه في الخير فقط.\n\n"
    "أرسل رابط الميديا من: YouTube / Instagram / X / Snapchat / TikTok."
)

# ====== Helpers ======
def is_allowed(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
        # بعض روابط تيك توك المختصرة قد لا تحمل host واضح — نترك yt-dlp يتعامل معها
        return (host in ALLOWED_HOSTS) or ("tiktok.com" in (host or ""))
    except Exception:
        return False

def best_format_string() -> str:
    """
    نحاول أعلى جودة ممكنة:
    1) أفضل فيديو + أفضل صوت (يتطلب ffmpeg للدمج)
    2) إن فشل الدمج، سنحاول أفضل ملف واحد جاهز (b/best)
    """
    return "bv*+ba/best"

# ====== Handlers ======
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("welcomed"):
        context.user_data["welcomed"] = True
        await update.message.reply_text(WELCOME_MSG, parse_mode="Markdown", reply_markup=snap_keyboard())
    else:
        await update.message.reply_text(NOTICE_MSG, parse_mode="Markdown")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "أرسل رابط فيديو/صورة من: YouTube / Instagram / X / Snapchat / TikTok.\n"
        "أحاول دائمًا أعلى جودة ممكنة. لو الملف ضخم جدًا قد يرفضه تيليجرام.",
        reply_markup=snap_keyboard()
    )

async def snap_back_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.message.reply_text(NOTICE_MSG, parse_mode="Markdown")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    m = URL_RE.search(text)
    if not m:
        return

    url = m.group(1)
    if not is_allowed(url):
        await update.message.reply_text(
            "❌ غير مدعوم. هذا البوت يدعم فقط: YouTube / Instagram / X / Snapchat / TikTok.",
            reply_markup=snap_keyboard()
        )
        return

    await update.message.chat.send_action(ChatAction.UPLOAD_DOCUMENT)

    try:
        import yt_dlp
    except Exception:
        await update.message.reply_text("❌ yt-dlp غير مثبت.")
        return

    # ffmpeg عبر imageio-ffmpeg (تنزيل تلقائي لبيناري ffmpeg واستخدامه)
    ffmpeg_dir = None
    try:
        import imageio_ffmpeg
        ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()
        ffmpeg_dir = os.path.dirname(ffmpeg_path)
    except Exception as e:
        log.warning("ffmpeg unavailable, merge may fail: %s", e)

    # نحاول أعلى جودة مرة واحدة فقط (بدون تخفيض)
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        outtmpl = str(td_path / "%(title).80s.%(ext)s")
        ydl_opts = {
            "outtmpl": outtmpl,
            "format": best_format_string() if FORCE_BEST_QUALITY else "best",
            "merge_output_format": "mp4",
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "restrictfilenames": True,
            "nocheckcertificate": True,
            "concurrent_fragment_downloads": 1,
        }
        if ffmpeg_dir:
            ydl_opts["ffmpeg_location"] = ffmpeg_dir

        info = None
        file_path: Path | None = None

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                # حاول استنتاج اسم الملف الناتج
                candidate = info.get("_filename") if isinstance(info, dict) else None
                if candidate:
                    p = Path(candidate)
                    if p.exists():
                        file_path = p
                if not file_path:
                    for p in td_path.iterdir():
                        if p.is_file():
                            file_path = p
                            break
        except Exception as e:
            log.exception("Download failed", exc_info=e)
            await update.message.reply_text(
                "❌ فشل التحميل بأعلى جودة. قد تكون المنصة تمنع أو يتطلب تسجيل دخول.\n"
                "جرّب رابطًا آخر أو فيديو أقصر.",
                reply_markup=snap_keyboard()
            )
            return

        if not file_path or not file_path.exists():
            await update.message.reply_text(
                "❌ لم أتمكن من إيجاد الملف بعد التحميل.",
                reply_markup=snap_keyboard()
            )
            return

        suffix = file_path.suffix.lower()
        title = (info.get("title") if isinstance(info, dict) else "الملف") or "الملف"
        title = title[:990]

        try:
            if suffix in {".mp4", ".mov", ".mkv", ".webm"}:
                await update.message.reply_video(video=file_path.open("rb"), caption=title, reply_markup=snap_keyboard())
            elif suffix in {".jpg", ".jpeg", ".png", ".gif"}:
                await update.message.reply_photo(photo=file_path.open("rb"), caption=title, reply_markup=snap_keyboard())
            else:
                # نحاول كفيديو على أي حال لو mp4 غير موجود
                await update.message.reply_video(video=file_path.open("rb"), caption=title, reply_markup=snap_keyboard())
        except Exception as e:
            log.exception("Send failed", exc_info=e)
            await update.message.reply_text(
                "❌ فشل إرسال الوسائط. غالبًا حجم الملف كبير ويتجاوز حد تيليجرام للبوت.\n"
                "جرّب فيديو أقصر أو جودة أقل على نفس الرابط.",
                reply_markup=snap_keyboard()
            )

def build_app() -> Application:
    if not TOKEN:
        raise RuntimeError("حدد TELEGRAM_TOKEN في Render → Environment.")
    app_tg = Application.builder().token(TOKEN).build()
    app_tg.add_handler(CommandHandler("start", start))
    app_tg.add_handler(CommandHandler("help", help_cmd))
    app_tg.add_handler(CallbackQueryHandler(snap_back_callback, pattern="^snap_back$"))
    app_tg.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_text))
    return app_tg

def run_flask():
    port = int(os.getenv("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

if __name__ == "__main__":
    # شغّل Flask في ثريد جانبي
    Thread(target=run_flask, daemon=True).start()

    # شغّل تيليجرام في الثريد الرئيسي (بدون إشارات نظام لتجنّب مشاكل السيرفر)
    tg = build_app()
    try:
        me = tg.bot.get_me()
        log.info("✅ Logged in as @%s (id=%s)", me.username, me.id)
    except Exception as e:
        log.exception("Bot login failed", exc_info=e)

    # ملاحظة: stop_signals=None لتفادي مشاكل signal على منصات الاستضافة
    tg.run_polling(allowed_updates=Update.ALL_TYPES, stop_signals=None, close_loop=False)
    log.info("✅ Telegram polling started")
