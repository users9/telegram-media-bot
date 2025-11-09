# main.py
import os, re, tempfile, logging
from threading import Thread
from pathlib import Path
from urllib.parse import urlparse

from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatAction
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# ===== الإعدادات =====
TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TOKEN:
    raise RuntimeError("ضع TELEGRAM_TOKEN في Render → Environment")

SNAP_URL = "https://snapchat.com/add/uckr"

# المسموح: TikTok / X(Twitter) / Snapchat (يشمل نطاقات المشاركة)
ALLOWED_HOSTS = {
    # TikTok
    "tiktok.com", "www.tiktok.com", "m.tiktok.com",
    "vm.tiktok.com", "vt.tiktok.com",
    # X (Twitter)
    "x.com", "www.x.com",
    "twitter.com", "www.twitter.com",
    "vxtwitter.com", "www.vxtwitter.com",  # روابط مشاركات شائعة
    # Snapchat
    "snapchat.com", "www.snapchat.com",
    "story.snapchat.com", "t.snapchat.com", "spotlight.snapchat.com",
}

URL_RE = re.compile(r"(https?://\S+)", re.IGNORECASE)

# ===== Flask للـ Health Check =====
app = Flask(__name__)

@app.get("/")
def home():
    return "OK - bot up"

def run_flask():
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))

# ===== أزرار ورسائل =====
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
    "أرسل رابط من: **TikTok / X (Twitter) / Snapchat**."
)

# ===== أدوات =====
def is_allowed(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
        # قصّ www. لو موجود
        if host.startswith("www."):
            host = host[4:]
        return host in ALLOWED_HOSTS
    except Exception:
        return False

def pick_best_format() -> str:
    # أفضل صيغة بدون خفض جودة؛ نخلي yt-dlp يختار أعلى جودة متاحة
    # للفيديو: أفضل فيديو+صوت، وإلا أفضل ملف واحد
    return "bv*+ba/b"

# ===== Handlers =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # أول مرة: ترحيب + زر السناب؛ بعدها: التنبيه
    if not context.user_data.get("welcomed"):
        context.user_data["welcomed"] = True
        await update.message.reply_text(WELCOME_MSG, parse_mode="Markdown", reply_markup=snap_keyboard())
    else:
        await update.message.reply_text(NOTICE_MSG, parse_mode="Markdown", reply_markup=snap_keyboard())

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "أرسل رابط فيديو/صورة من: TikTok / X (Twitter) / Snapchat.\n"
        "الفيديوهات تُرسل كـ **Document** للمحافظة على الجودة والأبعاد.\n"
        "الصور تُرسل كصورة عادية.",
        parse_mode="Markdown",
        reply_markup=snap_keyboard()
    )

async def snap_back_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text(NOTICE_MSG, parse_mode="Markdown", reply_markup=snap_keyboard())

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    m = URL_RE.search(text)
    if not m:
        return

    url = m.group(1)
    if not is_allowed(url):
        await update.message.reply_text(
            "❌ غير مدعوم. هذا البوت يدعم فقط: TikTok / X (Twitter) / Snapchat.",
            reply_markup=snap_keyboard()
        )
        return

    await update.message.chat.send_action(ChatAction.UPLOAD_DOCUMENT)

    try:
        import yt_dlp
    except Exception:
        await update.message.reply_text("❌ مكتبة yt-dlp غير مثبتة على السيرفر.")
        return

    last_error = None
    with tempfile.TemporaryDirectory() as td:
        outtmpl = str(Path(td) / "%(title).90s.%(ext)s")
        ydl_opts = {
            "outtmpl": outtmpl,
            "format": pick_best_format(),   # لا نخفض الجودة
            "noplaylist": True,
            "merge_output_format": "mp4",
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
                # التقاط اسم الملف
                if isinstance(info, dict):
                    fp = info.get("_filename") or ""
                    if fp:
                        file_path = Path(fp)
                if not file_path or not file_path.exists():
                    # التقط أي ملف تم تنزيله
                    for p in Path(td).iterdir():
                        if p.is_file():
                            file_path = p
                            break
        except Exception as e:
            last_error = e

        if not file_path or not file_path.exists():
            log.exception("Download failed", exc_info=last_error)
            await update.message.reply_text("❌ حدث خطأ غير متوقع أثناء التحميل.", reply_markup=snap_keyboard())
            return

        title = (isinstance(info, dict) and (info.get("title") or "")) or file_path.stem
        title = title[:990]
        suffix = file_path.suffix.lower()

        try:
            # الفيديو كـ Document لتفادي تغيير الأبعاد/الجودة في تيليجرام
            if suffix in {".mp4", ".mov", ".mkv", ".webm"}:
                await update.message.reply_document(
                    document=file_path.open("rb"),
                    caption=title,
                    reply_markup=snap_keyboard()
                )
            elif suffix in {".jpg", ".jpeg", ".png", ".gif"}:
                await update.message.reply_photo(
                    photo=file_path.open("rb"),
                    caption=title,
                    reply_markup=snap_keyboard()
                )
            else:
                # امتداد غير معروف؛ أرسله كوثيقة أيضًا
                await update.message.reply_document(
                    document=file_path.open("rb"),
                    caption=title,
                    reply_markup=snap_keyboard()
                )
        except Exception as e:
            log.exception("Send failed", exc_info=e)
            await update.message.reply_text(
                "❌ تعذّر إرسال الوسائط.\n"
                "قد يكون حجم الملف كبيرًا عن حد تيليجرام للبوتات.",
                reply_markup=snap_keyboard()
            )

def build_application() -> Application:
    app_tg = Application.builder().token(TOKEN).build()
    app_tg.add_handler(CommandHandler("start", start))
    app_tg.add_handler(CommandHandler("help", help_cmd))
    app_tg.add_handler(CallbackQueryHandler(snap_back_callback, pattern="^snap_back$"))
    app_tg.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_text))
    return app_tg

def main():
    # شغّل Flask في ثريد جانبي
    Thread(target=run_flask, daemon=True).start()

    # شغّل بوت تيليجرام في الـ Main Thread (أفضل لـ v21)
    application = build_application()

    async def boot():
        # تأكد من التوكن والعمل
        me = await application.bot.get_me()
        log.info("✅ Logged in as @%s (id=%s)", me.username, me.id)
        # استخدم Polling (وامسح أي Webhook)
        try:
            await application.bot.delete_webhook(drop_pending_updates=True)
        except Exception:
            pass
        log.info("✅ Telegram polling started")
        # ملاحظة: stop_signals=None مفيد لو شغّلت من داخل بيئة تمنع signal handlers
        await application.run_polling(allowed_updates=Update.ALL_TYPES, stop_signals=None)

    import asyncio
    asyncio.run(boot())

if __name__ == "__main__":
    main()
