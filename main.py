import os, re, tempfile, logging
from pathlib import Path
from urllib.parse import urlparse
from threading import Thread

from flask import Flask
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.constants import ChatAction
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)

logging.basicConfig(level=logging.INFO)
TOKEN = os.getenv("TELEGRAM_TOKEN")
SNAP_URL = "https://snapchat.com/add/uckr"

ALLOWED_HOSTS = {
    "youtube.com","www.youtube.com","youtu.be",
    "twitter.com","www.twitter.com","x.com","www.x.com",
    "snapchat.com","www.snapchat.com","story.snapchat.com",
    "instagram.com","www.instagram.com",
    "tiktok.com","www.tiktok.com","vm.tiktok.com","m.tiktok.com"
}

URL_RE = re.compile(r"(https?://\S+)", re.IGNORECASE)
TARGET_SIZES = [45 * 1024 * 1024, 28 * 1024 * 1024, 18 * 1024 * 1024]

app = Flask(__name__)

@app.route("/")
def home():
    return "Bot is running!"

def start_health_server():
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port, threaded=True)

def snap_keyboard():
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
    "الآن أرسل رابط الميديا من: YouTube / Instagram / X / Snapchat / TikTok."
)

def is_allowed(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
        return host in ALLOWED_HOSTS
    except:
        return False

def pick_format_for(limit_bytes):
    if limit_bytes is None:
        return "bv*+ba/best"
    return (
        f"(bv*+ba/b)[filesize<={limit_bytes}]/"
        f"(bv*+ba/b)[filesize_approx<={limit_bytes}]/"
        "bv*[height<=480]+ba/b[height<=480]/"
        "bv*[height<=360]+ba/b[height<=360]/"
        "b"
    )

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("welcomed"):
        context.user_data["welcomed"] = True
        await update.message.reply_text(WELCOME_MSG, parse_mode="Markdown", reply_markup=snap_keyboard())
    else:
        await update.message.reply_text(NOTICE_MSG, parse_mode="Markdown")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "أرسل رابط فيديو/صورة من: YouTube / Instagram / X / Snapchat / TikTok.",
        reply_markup=snap_keyboard()
    )

async def snap_back_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.message.reply_text(NOTICE_MSG, parse_mode="Markdown")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    m = URL_RE.search(text)
    if not m:
        return

    url = m.group(1)
    if not is_allowed(url):
        await update.message.reply_text("❌ غير مدعوم.", reply_markup=snap_keyboard())
        return

    await update.message.chat.send_action(ChatAction.UPLOAD_DOCUMENT)

    try:
        import yt_dlp
    except:
        await update.message.reply_text("❌ مكتبة yt-dlp غير مثبتة.")
        return

    last_error = None
    sent = False

    for limit in TARGET_SIZES + [None]:
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            outtmpl = str(td / "%(title).80s.%(ext)s")
            opts = {
                "format": pick_format_for(limit),
                "outtmpl": outtmpl,
                "merge_output_format": "mp4",
                "noplaylist": True,
                "quiet": True,
                "no_warnings": True
            }
            info = None
            fpath = None
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                    f = info.get("_filename")
                    fpath = Path(f) if f else None
            except Exception as e:
                last_error = e
                continue

            if not fpath or not fpath.exists():
                continue

            ext = fpath.suffix.lower()
            title = info.get("title")[:990] if info else "الملف"

            try:
                if ext in {".mp4", ".mov", ".mkv", ".webm"}:
                    await update.message.reply_video(fpath.open("rb"), caption=title, reply_markup=snap_keyboard())
                else:
                    await update.message.reply_photo(fpath.open("rb"), caption=title, reply_markup=snap_keyboard())
                sent = True
                break
            except Exception as e:
                last_error = e
                continue

    if not sent:
        await update.message.reply_text("❌ تعذّر إرسال الوسائط.", reply_markup=snap_keyboard())

def build_app():
    if not TOKEN:
        raise RuntimeError("TELEGRAM_TOKEN not set")
    app_tg = Application.builder().token(TOKEN).build()
    app_tg.add_handler(CommandHandler("start", start_cmd))
    app_tg.add_handler(CommandHandler("help", help_cmd))
    app_tg.add_handler(CallbackQueryHandler(snap_back_callback, pattern="^snap_back$"))
    app_tg.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    return app_tg

def main():
    Thread(target=start_health_server, daemon=True).start()

    import asyncio
    app_tg = build_app()

    async def boot():
        me = await app_tg.bot.get_me()
        logging.info(f"✅ Logged in as @{me.username} (id={me.id})")
        try:
            await app_tg.bot.delete_webhook(drop_pending_updates=False)
        except:
            pass
        logging.info("✅ Telegram polling started")
        app_tg.run_polling(allowed_updates=Update.ALL_TYPES)

    asyncio.run(boot())

if __name__ == "__main__":
    main()
