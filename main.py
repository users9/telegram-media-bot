# main.py  — يعمل مع python-telegram-bot==21.6
import os, re, tempfile, logging, asyncio
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

# ===== الإعدادات =====
TOKEN = os.getenv("TELEGRAM_TOKEN")
SNAP_URL = "https://snapchat.com/add/uckr"

# المنصات المسموحة
ALLOWED_HOSTS = {
    "youtube.com", "www.youtube.com", "youtu.be",
    "twitter.com", "www.twitter.com", "x.com", "www.x.com",
    "snapchat.com", "www.snapchat.com", "story.snapchat.com",
    "instagram.com", "www.instagram.com",
    "tiktok.com", "www.tiktok.com", "vm.tiktok.com", "m.tiktok.com"
}

URL_RE = re.compile(r"(https?://\S+)", re.IGNORECASE)
TARGET_SIZES = [45 * 1024 * 1024, 28 * 1024 * 1024, 18 * 1024 * 1024]

# ===== Flask للـ Health Check =====
app = Flask(__name__)

@app.route("/")
def home():
    return "Bot is running!"

def start_flask():
    port = int(os.getenv("PORT", "10000"))
    # تشغيل Flask في خيط ثانوي، والخيط الرئيسي للبوت
    app.run(host="0.0.0.0", port=port)

# ===== الواجهة والأزرار =====
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
    "أرسل رابط من: YouTube / Instagram / X / Snapchat / TikTok."
)

def is_allowed(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
        return host in ALLOWED_HOSTS
    except Exception:
        return False

def pick_format_for(limit_bytes: int | None) -> str:
    if limit_bytes is None:
        return "bv*+ba/best"
    return (
        f"(bv*+ba/b)[filesize<={limit_bytes}]/"
        f"(bv*+ba/b)[filesize_approx<={limit_bytes}]/"
        "bv*[height<=480]+ba/b[height<=480]/"
        "bv*[height<=360]+ba/b[height<=360]/"
        "b"
    )

# ===== Handlers =====
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("welcomed"):
        context.user_data["welcomed"] = True
        await update.message.reply_text(WELCOME_MSG, parse_mode="Markdown", reply_markup=snap_keyboard())
    else:
        await update.message.reply_text(NOTICE_MSG, parse_mode="Markdown")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "أرسل رابط فيديو/صورة من: YouTube / Instagram / X / Snapchat / TikTok.\n"
        "الإرسال سيكون كفيديو/صورة فقط (بدون ملفات).",
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
            "❌ غير مدعوم. المسموح: YouTube / Instagram / X / Snapchat / TikTok.",
            reply_markup=snap_keyboard()
        )
        return

    await update.message.chat.send_action(ChatAction.UPLOAD_DOCUMENT)

    try:
        import yt_dlp
    except Exception:
        await update.message.reply_text("❌ مكتبة yt-dlp غير مثبتة.")
        return

    last_error = None
    sent_ok = False

    for limit in TARGET_SIZES + [None]:
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            outtmpl = str(td_path / "%(title).80s.%(ext)s")
            ydl_opts = {
                "outtmpl": outtmpl,
                "format": pick_format_for(limit),
                "merge_output_format": "mp4",
                "noplaylist": True,
                "quiet": True,
                "no_warnings": True,
                "restrictfilenames": True,
                "nocheckcertificate": True,
                "concurrent_fragment_downloads": 1,
            }
            info = None
            file_path: Path | None = None

            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                    if isinstance(info, dict):
                        file_path = Path(info.get("_filename") or "")
                    if not file_path or not file_path.exists():
                        for p in td_path.iterdir():
                            if p.is_file():
                                file_path = p
                                break
            except Exception as e:
                last_error = e
                continue

            if not file_path or not file_path.exists():
                continue

            title = (info.get("title") if isinstance(info, dict) else "الملف") or "الملف"
            title = title[:990]
            suffix = file_path.suffix.lower()

            try:
                if suffix in {".mp4", ".mov", ".mkv", ".webm"}:
                    await update.message.reply_video(video=file_path.open("rb"), caption=title, reply_markup=snap_keyboard())
                    sent_ok = True
                    break
                elif suffix in {".jpg", ".jpeg", ".png", ".gif"}:
                    await update.message.reply_photo(photo=file_path.open("rb"), caption=title, reply_markup=snap_keyboard())
                    sent_ok = True
                    break
                else:
                    last_error = Exception(f"Unsupported media type: {suffix}")
                    continue
            except Exception as e:
                last_error = e
                continue

    if not sent_ok:
        await update.message.reply_text(
            "❌ تعذّر إرسال الوسائط حتى بعد تخفيض الجودة. جرّب جودة أقل/فيديو أقصر.",
            reply_markup=snap_keyboard()
        )
        if last_error:
            logging.exception("Send failed", exc_info=last_error)

# ===== تشغيل البوت (v21.6) =====
async def build_app() -> Application:
    if not TOKEN:
        raise RuntimeError("حدد TELEGRAM_TOKEN في Render → Environment.")
    app_tg = Application.builder().token(TOKEN).build()

    app_tg.add_handler(CommandHandler("start", start_cmd))
    app_tg.add_handler(CommandHandler("help", help_cmd))
    app_tg.add_handler(CallbackQueryHandler(snap_back_callback, pattern="^snap_back$"))
    app_tg.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_text))

    # فحص سريع + حذف أي Webhook
    async def _post_init(application: Application):
        me = await application.bot.get_me()
        print(f"✅ BOT OK: @{me.username} (id={me.id})")
        try:
            await application.bot.delete_webhook(drop_pending_updates=True)
        except Exception as e:
            print(f"Webhook delete warn: {e}")

    app_tg.post_init = _post_init
    return app_tg

async def run_bot():
    app_tg = await build_app()
    # مهم: بدون إشارات (stop_signals=None) لأننا بالخيط الرئيسي مع Flask في خيط آخر
    await app_tg.run_polling(stop_signals=None, allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    # شغّل Flask في خيط ثانوي (لـ Render health check)
    Thread(target=start_flask, daemon=True).start()
    # شغّل البوت في الخيط الرئيسي (لا تضعه داخل Thread لتفادي مشاكل الإشارات)
    asyncio.run(run_bot())
