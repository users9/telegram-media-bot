# main.py — Telegram media bot (PTB v21) + Flask healthcheck
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

# ===== الإعدادات =====
TOKEN = os.getenv("TELEGRAM_TOKEN")
SNAP_URL = "https://snapchat.com/add/uckr"

# السماح لمنصات محددة (مضاف TikTok القصير vt.tiktok.com)
ALLOWED_HOSTS = {
    # YouTube
    "youtube.com", "www.youtube.com", "youtu.be", "m.youtube.com",
    # X / Twitter
    "x.com", "www.x.com", "twitter.com", "www.twitter.com", "mobile.twitter.com",
    # Instagram
    "instagram.com", "www.instagram.com",
    # Snapchat
    "snapchat.com", "www.snapchat.com", "story.snapchat.com",
    # TikTok (كل الصيغ الشائعة + القصير)
    "tiktok.com", "www.tiktok.com", "m.tiktok.com", "vm.tiktok.com", "vt.tiktok.com",
    "vxtiktok.com", "www.vxtiktok.com"  # احتياط للروابط المتحولة
}

URL_RE = re.compile(r"(https?://\S+)", re.IGNORECASE)

# أحجام نجربها بالتدريج حتى نضمن الإرسال كـ فيديو/صورة (تيليجرام Bots غالبًا ~50MB)
TARGET_SIZES = [48 * 1024 * 1024, 36 * 1024 * 1024, 24 * 1024 * 1024, 16 * 1024 * 1024]

# ===== Flask للـ Health Check =====
app = Flask(__name__)

@app.route("/")
def home():
    return "OK - bot alive"

# ===== واجهة وأزرار =====
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
    "الآن أرسل رابط الميديا من: **YouTube / Instagram / X / Snapchat / TikTok**."
)

def is_allowed(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
        # بعض روابط TikTok تكون بدون www وعلى vt.tiktok.com — مغطّينها
        return host in ALLOWED_HOSTS
    except Exception:
        return False

def pick_format_for(limit_bytes: int | None) -> str:
    """
    تنسيق انتقائي يفضّل فيديو+صوت ضمن حد الحجم.
    """
    if limit_bytes is None:
        return "bv*+ba/best"
    # نجرب بقيود الحجم، وإذا ما ضبط ننزل الدقة
    return (
        f"(bv*+ba/b)[filesize<={limit_bytes}]/"
        f"(bv*+ba/b)[filesize_approx<={limit_bytes}]/"
        f"b[filesize<={limit_bytes}]/"
        f"b[filesize_approx<={limit_bytes}]/"
        "bv*[height<=480]+ba/b[height<=480]/"
        "bv*[height<=360]+ba/b[height<=360]/"
        "b"
    )

# ===== Handlers =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("welcomed"):
        context.user_data["welcomed"] = True
        await update.message.reply_text(WELCOME_MSG, parse_mode="Markdown", reply_markup=snap_keyboard())
    else:
        await update.message.reply_text(NOTICE_MSG, parse_mode="Markdown")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "أرسل رابط فيديو/صورة من: YouTube / Instagram / X / Snapchat / TikTok.\n"
        "الإرسال يكون كـ **فيديو/صورة فقط** بدون ملفات مرفقة.",
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

    # إظهار “جاري الرفع” (فيديو/صورة)
    await update.message.chat.send_action(ChatAction.UPLOAD_VIDEO)

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
                # TikTok أحيانًا يحتاج UA حديث — yt-dlp غالبًا يضبط لوحده
                # "http_headers": {"User-Agent": "Mozilla/5.0"},
            }

            info = None
            file_path: Path | None = None

            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                    if isinstance(info, dict):
                        fp = info.get("_filename") or ""
                        if fp:
                            file_path = Path(fp)
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

            title = (isinstance(info, dict) and info.get("title")) or "الملف"
            title = (title or "الملف")[:990]
            suffix = file_path.suffix.lower()

            try:
                if suffix in {".mp4", ".mov", ".mkv", ".webm"}:
                    await update.message.reply_video(
                        video=file_path.open("rb"),
                        caption=title,
                        reply_markup=snap_keyboard()
                    )
                    sent_ok = True
                    break
                elif suffix in {".jpg", ".jpeg", ".png", ".gif"}:
                    await update.message.reply_photo(
                        photo=file_path.open("rb"),
                        caption=title,
                        reply_markup=snap_keyboard()
                    )
                    sent_ok = True
                    break
                else:
                    last_error = Exception(f"Unsupported media type: {suffix}")
                    continue
            except Exception as e:
                last_error = e
                continue

    if not sent_ok:
        msg = (
            "❌ تعذّر إرسال الوسائط حتى بعد تخفيض الجودة.\n"
            "• جرّب رابطًا مباشرًا من نفس المنصة.\n"
            "• أو فيديو أقصر/جودة أقل."
        )
        await update.message.reply_text(msg, reply_markup=snap_keyboard())
        if last_error:
            log.exception("Send failed", exc_info=last_error)

# ===== تشغيل =====
def run_flask():
    # نشغّل Flask بخيط جانبي كـ healthcheck
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")), use_reloader=False)

def main():
    if not TOKEN:
        raise RuntimeError("حدد TELEGRAM_TOKEN في Render → Environment.")

    # شغّل Flask في الخلفية
    Thread(target=run_flask, daemon=True).start()

    # ابني تطبيق تيليجرام
    application = Application.builder().token(TOKEN).build()

    # Handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_cmd))
    application.add_handler(CallbackQueryHandler(snap_back_callback, pattern="^snap_back$"))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_text))

    # تأكيد الدخول + مسح أي Webhook قديم
    async def _post_init(app: Application):
        me = await app.bot.get_me()
        log.info("✅ Logged in as @%s (id=%s)", me.username, me.id)
        try:
            await app.bot.delete_webhook(drop_pending_updates=False)
        except Exception:
            pass
        log.info("✅ Telegram polling started")

    application.post_init = _post_init

    # IMPORTANT:
    # نخلي polling في الخيط الرئيسي (main thread) عشان إشارات النظام،
    # ونمنع المشاكل اللي كانت تظهر لما نشغّله داخل ثريد ثاني.
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
