# main.py — Telegram media downloader (TikTok / X-Twitter / Snapchat)
# PTB v21.6 + Flask healthcheck + background event loop thread

import os
import re
import asyncio
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

# ====== الإعدادات ======
TOKEN = os.getenv("TELEGRAM_TOKEN")  # لا تضع كلمة bot هنا
SNAP_URL = "https://snapchat.com/add/uckr"

# المنصات المدعومة: سناب / تويتر (X) / تيك توك
ALLOWED_HOSTS = {
    # TikTok
    "tiktok.com", "www.tiktok.com", "m.tiktok.com", "vt.tiktok.com", "vm.tiktok.com",
    # X / Twitter
    "twitter.com", "www.twitter.com", "x.com", "www.x.com",
    # Snapchat
    "snapchat.com", "www.snapchat.com", "story.snapchat.com"
}

URL_RE = re.compile(r"(https?://\S+)", re.IGNORECASE)

# ====== Flask (Health Check) ======
app = Flask(__name__)

@app.get("/")
def home():
    return "OK - bot alive"

# ====== أزرار ورسائل ======
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

# ====== أدوات مساعدة ======
def host_of(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""

def is_supported(url: str) -> bool:
    h = host_of(url)
    return any(h == ah or h.endswith("." + ah) for ah in ALLOWED_HOSTS)

def is_twitter(url: str) -> bool:
    h = host_of(url)
    return h in {"twitter.com", "www.twitter.com", "x.com", "www.x.com"}

def is_tiktok(url: str) -> bool:
    h = host_of(url)
    return "tiktok.com" in h

def is_snap(url: str) -> bool:
    h = host_of(url)
    return "snapchat.com" in h

# ====== Handlers ======
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("welcomed"):
        context.user_data["welcomed"] = True
        await update.message.reply_text(
            WELCOME_MSG, parse_mode="Markdown",
            reply_markup=snap_keyboard()
        )
    else:
        await update.message.reply_text(
            NOTICE_MSG, parse_mode="Markdown",
            reply_markup=snap_keyboard()
        )

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "ارسل رابط من: TikTok / X (Twitter) / Snapchat.\n"
        "بالنسبة لتويتر: سيتم الإرسال **كـ Document** للحفاظ على الأبعاد الأصلية.",
        reply_markup=snap_keyboard()
    )

async def cb_snap_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.message.reply_text(NOTICE_MSG, parse_mode="Markdown")

async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    m = URL_RE.search(text)
    if not m:
        return
    url = m.group(1)

    if not is_supported(url):
        await update.message.reply_text(
            "❌ الرابط غير مدعوم. البوت يدعم: TikTok / X (Twitter) / Snapchat.",
            reply_markup=snap_keyboard()
        )
        return

    await update.message.chat.send_action(ChatAction.UPLOAD_DOCUMENT)

    try:
        import yt_dlp  # تثبّت من requirements
    except Exception:
        await update.message.reply_text("❌ مكتبة yt-dlp غير مثبتة.")
        return

    # إعدادات yt-dlp:
    # - لا نعيد ترميز الفيديو (نحافظ على الجودة قدر الإمكان)
    # - في تويتر: لا نفرض merge_output_format حتى لا تتغيّر الأبعاد، ونُرسل كـ Document.
    with tempfile.TemporaryDirectory() as td:
        outtmpl = str(Path(td) / "%(title).100s.%(ext)s")

        ydl_opts_base = {
            "outtmpl": outtmpl,
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "restrictfilenames": True,
            "nocheckcertificate": True,
            "concurrent_fragment_downloads": 1,
        }

        if is_twitter(url):
            # تويتر: لا نفرض mp4 — خليه يحفظ الأصل (webm/mp4...).
            ydl_opts = {
                **ydl_opts_base,
                "format": "bv*+ba/best",   # أفضل المتاح دون تحويل
                # بدون merge_output_format هنا
            }
        else:
            # تيك توك/سناب: نفضّل mp4 عند الدمج فقط (بدون إعادة ترميز)
            ydl_opts = {
                **ydl_opts_base,
                "format": "bv*+ba/best",
                "merge_output_format": "mp4",
            }

        info = None
        file_path = None
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                if isinstance(info, dict):
                    fp = info.get("_filename")
                    if fp:
                        file_path = Path(fp)
                if not file_path or not file_path.exists():
                    # التقط أول ملف داخل المجلد
                    for p in Path(td).iterdir():
                        if p.is_file():
                            file_path = p
                            break
        except Exception as e:
            log.exception("yt-dlp failed", exc_info=e)
            await update.message.reply_text(
                "❌ حدث خطأ أثناء التحميل.\n"
                "إن كان الرابط من إنستغرام/يوتيوب فهذا البوت لا يدعمهم.\n"
                "وإن كان من تويتر/تيك توك/سناب فجرب رابطًا آخر.",
                reply_markup=snap_keyboard()
            )
            return

        if not file_path or not file_path.exists():
            await update.message.reply_text("❌ لم أجد ملفًا بعد التنزيل.", reply_markup=snap_keyboard())
            return

        title = (isinstance(info, dict) and info.get("title")) or "الملف"
        title = title[:990]
        suffix = file_path.suffix.lower()

        try:
            if is_twitter(url):
                # تويتر: أرسل كـ Document للحفاظ على الأبعاد 1:1 كما هي
                await update.message.reply_document(
                    document=file_path.open("rb"),
                    caption=title,
                    reply_markup=snap_keyboard()
                )
            else:
                # تيك توك/سناب: إن كان فيديو أرسله فيديو، وإن كان صورة أرسل صورة
                if suffix in {".mp4", ".mov", ".mkv", ".webm"}:
                    await update.message.reply_video(
                        video=file_path.open("rb"),
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
                    # fallback: أرسل كـ Document
                    await update.message.reply_document(
                        document=file_path.open("rb"),
                        caption=title,
                        reply_markup=snap_keyboard()
                    )
        except Exception as e:
            log.exception("send failed", exc_info=e)
            await update.message.reply_text(
                "❌ تعذّر إرسال الوسائط (ربما الحجم أو صيغة غير مدعومة).\n"
                "جرّب رابطًا آخر أو جودة أقل من نفس المنصة.",
                reply_markup=snap_keyboard()
            )

# ====== تشغيل البوت في ثريد مع event loop خاص ======
def run_bot_loop():
    if not TOKEN:
        raise RuntimeError("متغير TELEGRAM_TOKEN مفقود في Render → Environment.")

    async def boot():
        application = Application.builder().token(TOKEN).build()

        # Handlers
        application.add_handler(CommandHandler("start", cmd_start))
        application.add_handler(CommandHandler("help", cmd_help))
        application.add_handler(CallbackQueryHandler(cb_snap_back, pattern="^snap_back$"))
        application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), on_text))

        # شخّص التوكن + احذف أي Webhook
        me = await application.bot.get_me()
        log.info("✅ Logged in as @%s (id=%s)", me.username, me.id)
        try:
            await application.bot.delete_webhook(drop_pending_updates=False)
        except Exception:
            pass

        log.info("✅ Telegram polling starting…")
        # مهم: داخل الثريد — لا نحاول تسجيل سيجنالز
        await application.run_polling(
            allowed_updates=Update.ALL_TYPES,
            stop_signals=None,     # لا تربط إشارات OS في الثريد
            close_loop=False       # لا تغلق اللووب لأننا نديره بأنفسنا
        )

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(boot())
    finally:
        # لا تغلق loop بالقوة إن كان ما زال يعمل
        try:
            if loop.is_running():
                pass
        finally:
            # في Render يكفي تركه ينتهي مع العملية
            ...

def start_background_bot():
    t = Thread(target=run_bot_loop, name="tg-bot-thread", daemon=True)
    t.start()

# ====== نقطة الدخول ======
if __name__ == "__main__":
    # شغّل البوت بالخلفية
    start_background_bot()
    # شغّل Flask للـ Health Check (Render يطلب بورت)
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")), threaded=True)
