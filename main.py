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

# ===== الإعدادات =====
TOKEN = os.getenv("TELEGRAM_TOKEN")  # لا تكتب bot هنا — التوكن فقط
SNAP_URL = "https://snapchat.com/add/uckr"

# المسموح: TikTok / X (Twitter) / Snapchat (+ جميع صيغ تيك توك الجديدة)
ALLOWED_HOSTS = {
    # X (Twitter)
    "twitter.com", "www.twitter.com", "x.com", "www.x.com",
    # TikTok
    "tiktok.com", "www.tiktok.com", "m.tiktok.com", "vm.tiktok.com", "vt.tiktok.com",
    # Snapchat (رابط الستوري/الحساب/الاضافة)
    "snapchat.com", "www.snapchat.com", "story.snapchat.com"
}

URL_RE = re.compile(r"(https?://\S+)", re.IGNORECASE)

# ===== Flask للـ Health Check =====
app = Flask(__name__)

@app.route("/")
def home():
    return "OK"

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

def is_allowed(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
        return any(host == h or host.endswith("." + h) for h in ALLOWED_HOSTS)
    except Exception:
        return False

def is_snap_profile(url: str) -> bool:
    """
    حساب سناب (مو Spotlight):
    أمثلة:
      https://www.snapchat.com/add/username
      https://snapchat.com/add/username
    """
    try:
        u = urlparse(url)
        host = (u.hostname or "").lower()
        if "snapchat.com" not in host:
            return False
        return u.path.strip("/").split("/")[0] in {"add", "profile"}
    except Exception:
        return False

def twitter_like(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host in {"twitter.com", "www.twitter.com", "x.com", "www.x.com"}

# ===== Handlers =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("welcomed"):
        context.user_data["welcomed"] = True
        await update.message.reply_text(WELCOME_MSG, parse_mode="Markdown", reply_markup=snap_keyboard())
    else:
        await update.message.reply_text(NOTICE_MSG, parse_mode="Markdown", reply_markup=snap_keyboard())

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "أرسل رابط فيديو/صورة من: TikTok / X (Twitter) / Snapchat.\n"
        "سيتم إرسال تويتر كـ **Document** للمحافظة على المقاس الأصلي.",
        parse_mode="Markdown",
        reply_markup=snap_keyboard()
    )

async def snap_back_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text(NOTICE_MSG, parse_mode="Markdown", reply_markup=snap_keyboard())

async def snap_profile_options(update: Update, url: str):
    # بدون تسجيل دخول، ما نقدر نسحب الستوري مباشرة. نخلي المستخدم يختار ونوضح.
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🎥 ستوريات الفيديو فقط", callback_data="snap_story_v"),
            InlineKeyboardButton("🖼️ ستوريات الصور فقط", callback_data="snap_story_p"),
        ],
        [InlineKeyboardButton("📦 الكل (صور + فيديو)", callback_data="snap_story_all")],
        [InlineKeyboardButton("👻 زيارة الحساب", url=url)]
    ])
    await update.message.reply_text(
        "حساب سناب مُرسَل.\n"
        "اختر ماذا تريد من الستوري:\n"
        "ملاحظة: **تحميل ستوريات الحساب يتطلب تسجيل دخول سناب** وغير مدعوم حاليًا بدون Cookies.",
        parse_mode="Markdown",
        reply_markup=kb
    )

async def snap_story_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.message.reply_text(
        "تحميل ستوريات الحساب يتطلب تسجيل دخول (Cookies) — غير متاح حاليًا في هذا البوت.\n"
        "أرسل رابط **Spotlight** أو استخدم TikTok/X وسيتم التحميل مباشرة.",
        reply_markup=snap_keyboard()
    )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
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

    # لو رابط حساب سناب — أعرض أزرار خيارات الستوري
    if is_snap_profile(url):
        await snap_profile_options(update, url)
        return

    # تحميل الوسائط
    await update.message.chat.send_action(ChatAction.UPLOAD_DOCUMENT)

    try:
        import yt_dlp
    except Exception:
        await update.message.reply_text("❌ مكتبة yt-dlp غير مثبتة.")
        return

    out_path: Path | None = None
    info = None
    try:
        with tempfile.TemporaryDirectory() as td:
            outtmpl = str(Path(td) / "%(title).80s.%(ext)s")
            ydl_opts = {
                "outtmpl": outtmpl,
                # أعلى جودة ممكنة (بدون تخفيض نهائياً)
                "format": "bv*+ba/best",
                "merge_output_format": "mp4",
                "noplaylist": True,
                "quiet": True,
                "no_warnings": True,
                "restrictfilenames": True,
                "nocheckcertificate": True,
                "concurrent_fragment_downloads": 1,
            }
            # استخراج + تنزيل
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                candidate = Path(info.get("_filename") or "")
                if candidate.exists():
                    out_path = candidate
                else:
                    # التقط أي ملف نزل
                    for p in Path(td).iterdir():
                        if p.is_file():
                            out_path = p
                            break

            if not out_path or not out_path.exists():
                raise RuntimeError("لم يتم العثور على الملف بعد التنزيل")

            title = (info.get("title") if isinstance(info, dict) else "الملف") or "الملف"
            title = title[:990]
            suffix = out_path.suffix.lower()

            # تويتر → Document للحفاظ على المقاس
            force_document = twitter_like(url)

            if suffix in {".jpg", ".jpeg", ".png", ".gif"} and not force_document:
                await update.message.reply_photo(photo=out_path.open("rb"), caption=title, reply_markup=snap_keyboard())
            elif suffix in {".mp4", ".mov", ".mkv", ".webm"} and not force_document:
                await update.message.reply_video(video=out_path.open("rb"), caption=title, reply_markup=snap_keyboard())
            else:
                # أي حالة أخرى (أو تويتر) → document للحفاظ على الملف كما هو
                await update.message.reply_document(document=out_path.open("rb"), caption=title, reply_markup=snap_keyboard())

    except Exception as e:
        log.exception("Download/send failed", exc_info=e)
        await update.message.reply_text(
            "❌ حدث خطأ غير متوقع أثناء التحميل/الإرسال.\n"
            "• جرّب رابطاً آخر من نفس المنصة\n"
            "• أو أرسل TikTok/X/Spotlight",
            reply_markup=snap_keyboard()
        )

# ===== تشغيل البوت =====
async def run_bot():
    if not TOKEN:
        raise RuntimeError("حدد TELEGRAM_TOKEN في Render → Environment.")
    application = Application.builder().token(TOKEN).build()

    # Handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_cmd))
    application.add_handler(CallbackQueryHandler(snap_back_callback, pattern="^snap_back$"))
    application.add_handler(CallbackQueryHandler(snap_story_choice, pattern=r"^snap_story_(v|p|all)$"))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_text))

    # فحص الدخول + إلغاء ويبهوك (نشتغل Polling)
    me = await application.bot.get_me()
    log.info(f"✅ Logged in as @{me.username} (id={me.id})")
    await application.bot.delete_webhook(drop_pending_updates=True)

    # شغّل Flask في ثريد جانبي
    Thread(target=run_flask, daemon=True).start()

    log.info("✅ Telegram polling started")
    # مهم: لا تسجّل سيغنالات في هذا السياق (نحن داخل منصة)
    await application.run_polling(allowed_updates=Update.ALL_TYPES, stop_signals=None)

if __name__ == "__main__":
    import asyncio
    asyncio.run(run_bot())
