# main.py
import os, re, tempfile, logging
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

# ===== إعدادات عامة =====
TOKEN = os.getenv("TELEGRAM_TOKEN")  # لا تكتب "bot" هنا، التوكن فقط
SNAP_URL = "https://snapchat.com/add/uckr"

# المسموح: YouTube / Instagram / X / Snapchat / TikTok
ALLOWED_HOSTS = {
    # YouTube
    "youtube.com", "www.youtube.com", "youtu.be",
    # X (Twitter)
    "twitter.com", "www.twitter.com", "x.com", "www.x.com",
    # Snapchat
    "snapchat.com", "www.snapchat.com", "story.snapchat.com",
    # Instagram
    "instagram.com", "www.instagram.com",
    # TikTok (كل الأشكال الجديدة)
    "tiktok.com", "www.tiktok.com", "m.tiktok.com",
    "v.tiktok.com", "vt.tiktok.com", "vm.tiktok.com"
}

# يلقط أول رابط في النص
URL_RE = re.compile(r"(https?://\S+)", re.IGNORECASE)

# ===== Flask للـ Health Check =====
app = Flask(__name__)

@app.route("/")
def home():
    return "OK — bot alive"

def run_flask():
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")), use_reloader=False)

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
    "أرسل رابط من: YouTube / Instagram / X / Snapchat / TikTok."
)

# ===== مساعدات =====
def is_allowed(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
        # أحيانًا تيك توك يحط // بعد الدومين، السطر التالي يتجاهلها
        host = host.strip("/")
        return host in ALLOWED_HOSTS
    except Exception:
        return False

def yt_best_format() -> str:
    # بدون تخفيض جودة: أفضل فيديو + أفضل صوت، ثم أفضل صيغة متوفرة
    return "bv*+ba/b/best"

# ===== Handlers =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("welcomed"):
        context.user_data["welcomed"] = True
        await update.message.reply_text(WELCOME_MSG, parse_mode="Markdown", reply_markup=snap_keyboard())
    else:
        await update.message.reply_text(NOTICE_MSG, parse_mode="Markdown")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "ارسل رابط فيديو/صورة من المنصات المدعومة، وسأرسل لك الوسائط مباشرة كفيديو/صورة.",
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
            "❌ الرابط غير مدعوم. المنصات: YouTube / Instagram / X / Snapchat / TikTok.",
            reply_markup=snap_keyboard()
        )
        return

    await update.message.chat.send_action(ChatAction.UPLOAD_VIDEO)

    try:
        import yt_dlp
    except Exception:
        await update.message.reply_text("❌ مكتبة yt-dlp غير مثبتة على السيرفر.")
        return

    tmp_ok = False
    last_err = None

    # نجرب التحميل بجودة عالية دون تخفيض. إذا كبر جدًا لتيلجرام، نخبر المستخدم.
    with tempfile.TemporaryDirectory() as td:
        outtmpl = str(Path(td) / "%(title).80s.%(ext)s")
        ydl_opts = {
            "outtmpl": outtmpl,
            "format": yt_best_format(),
            "merge_output_format": "mp4",
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "restrictfilenames": True,
            "concurrent_fragment_downloads": 1,
        }

        info = None
        file_path: Path | None = None
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                # حاول إيجاد الملف الناتج
                if isinstance(info, dict) and info.get("_filename"):
                    file_path = Path(info["_filename"])
                if not file_path or not file_path.exists():
                    for p in Path(td).iterdir():
                        if p.is_file():
                            file_path = p
                            break
        except Exception as e:
            last_err = e

        if file_path and file_path.exists():
            title = (isinstance(info, dict) and info.get("title")) or "المقطع"
            title = (title or "المقطع")[:990]
            suffix = file_path.suffix.lower()

            try:
                if suffix in {".mp4", ".mov", ".mkv", ".webm"}:
                    await update.message.reply_video(video=file_path.open("rb"), caption=title, reply_markup=snap_keyboard())
                    tmp_ok = True
                elif suffix in {".jpg", ".jpeg", ".png", ".gif"}:
                    await update.message.reply_photo(photo=file_path.open("rb"), caption=title, reply_markup=snap_keyboard())
                    tmp_ok = True
                else:
                    last_err = Exception(f"نوع وسائط غير مدعوم للإرسال المباشر: {suffix}")
            except Exception as e:
                last_err = e

    if not tmp_ok:
        # حدود تيليجرام للبوتات على رفع الفيديوهات/الصور تسبب فشل المقاطع الكبيرة جداً.
        msg = (
            "❌ تعذّر إرسال الوسائط مباشرة.\n"
            "السبب الشائع: حجم الفيديو أكبر من حد تيليجرام للبوتات.\n"
            "جرّب رابط بجودة أقل/مدة أقصر، أو اعطني رابط آخر."
        )
        # لو فيه خطأ داخلي نطبعه للّوق فقط
        if last_err:
            log.exception("Send failed", exc_info=last_err)
        await update.message.reply_text(msg, reply_markup=snap_keyboard())

# ===== تشغيل البوت (Polling في الخيط الرئيسي) + Flask في خيط جانبي =====
def run_bot_blocking():
    if not TOKEN:
        raise RuntimeError("حدد TELEGRAM_TOKEN في بيئة Render.")
    app_tg = Application.builder().token(TOKEN).build()

    # فحص سريع للتوكن واسم البوت
    async def _probe(_app):
        me = await _app.bot.get_me()
        log.info("✅ Logged in as @%s (id=%s)", me.username, me.id)
        # امسح أي Webhook لأننا نستخدم polling
        await _app.bot.delete_webhook()

    app_tg.post_init = _probe

    # الهاندلرز
    app_tg.add_handler(CommandHandler("start", start))
    app_tg.add_handler(CommandHandler("help", help_cmd))
    app_tg.add_handler(CallbackQueryHandler(snap_back_callback, pattern="^snap_back$"))
    app_tg.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_text))

    # مهم: استدعاء run_polling "بدون asyncio.run" في الخيط الرئيسي
    # حتى ما يصير تضارب لوب/سيغنال.
    log.info("✅ Telegram polling started")
    app_tg.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    # شغّل Flask في خيط جانبي
    Thread(target=run_flask, daemon=True).start()
    # خلي البوت في الخيط الرئيسي
    run_bot_blocking()
