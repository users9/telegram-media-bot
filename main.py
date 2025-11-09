# main.py — Telegram media bot (TikTok / X / Snapchat)
# PTB v21.6 / Flask keepalive / yt-dlp
import os, re, tempfile, logging, asyncio
from pathlib import Path

from flask import Flask
from threading import Thread

from telegram import (
    Update, InlineKeyboardMarkup, InlineKeyboardButton, InputFile
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)

# ========= إعدادات عامة =========
TOKEN = os.getenv("TELEGRAM_TOKEN")  # لا تحط التوكن هنا داخل الملف؛ خله في ENV على Render
SNAP_URL = "https://www.snapchat.com/add/uckr"  # رابط سنابك لزر الإضافة
MAX_FILE_MB = 190  # حد الحجم المرسل كـ Video؛ إن تجاوزناه نرسل Document
TEMP_DIR = Path(tempfile.gettempdir()) / "tg_media_bot"
TEMP_DIR.mkdir(exist_ok=True)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("bot")

# ========= Flask keepalive =========
app = Flask(__name__)
@app.get("/")
def index():
    return "OK"

def run_http():
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))

# ========= رسائل وأزرار =========
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
    "أرسل رابط من: **X / Snapchat / TikTok**."
)

def snap_profile_menu(username: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🎥 تحميل الستوريات (فيديو)", callback_data=f"snap_vid:{username}"),
            InlineKeyboardButton("🖼️ الستوريات (صور)", callback_data=f"snap_img:{username}"),
        ],
        [InlineKeyboardButton("📦 الكل (صور+فيديو)", callback_data=f"snap_all:{username}")],
        [InlineKeyboardButton("↩️ رجوع", callback_data="snap_back")],
    ])

# ========= تعبيرات الروابط =========
RE_TIKTOK = re.compile(r"(https?://)?(www\.)?(vm|vt|m)?\.?tiktok\.com/[^ \n]+", re.I)
RE_TW     = re.compile(r"(https?://)?(twitter|x)\.com/[^ \n]+", re.I)
RE_SNAP_SPOT = re.compile(r"(https?://)?(www\.)?snapchat\.com/(add|spotlight|discover)/[^ \n]+", re.I)

# نحاول فهم أنه يوزر سناب/رابط حساب (add/username أو @username)
def parse_snap_username(text: str) -> str | None:
    m = re.search(r"snapchat\.com/add/([A-Za-z0-9._-]{2,})", text)
    if m:
        return m.group(1)
    if text.strip().startswith("@") and len(text.strip()) > 1:
        return text.strip()[1:]
    if re.fullmatch(r"[A-Za-z0-9._-]{2,}", text.strip()):
        return text.strip()
    return None

# ========= yt-dlp تنزيل =========
def ytdlp_opts_for_preserving():
    # نحافظ على الأبعاد قدر الإمكان ونمنع إعادة الترميز
    return {
        "noprogress": True,
        "quiet": True,
        "merge_output_format": "mp4",
        "outtmpl": str(TEMP_DIR / "%(title).200B-%(id)s.%(ext)s"),
        "postprocessors": [
            # Remux فقط إن لزم (بدون re-encode)
            {"key": "FFmpegVideoRemuxer", "preferedformat": "mp4"}
        ],
        "http_headers": {"User-Agent": "Mozilla/5.0"},
    }

async def download_with_ytdlp(url: str) -> Path:
    from yt_dlp import YoutubeDL
    opts = ytdlp_opts_for_preserving()

    # لـ X/Twitter خذ أفضل مسار بدون تغيير أبعاد
    if RE_TW.search(url):
        opts["format"] = "bestvideo*+bestaudio/best"

    # TikTok: أفضل جودة مباشرة
    if RE_TIKTOK.search(url):
        opts["format"] = "bv*+ba/best"

    paths: list[Path] = []
    def hook(d):
        if d.get("status") == "finished":
            p = Path(d["filename"])
            paths.append(p)

    opts["progress_hooks"] = [hook]
    with YoutubeDL(opts) as ydl:
        ydl.download([url])

    if not paths:
        raise RuntimeError("لم يتم إنشاء ملف")
    return paths[0]

async def send_video_or_doc(update: Update, context: ContextTypes.DEFAULT_TYPE, file_path: Path, caption: str = ""):
    size_mb = file_path.stat().st_size / (1024 * 1024)
    # لتويتر نرسل Document للحفاظ على الأبعاد + أي ملف كبير
    send_as_doc = size_mb > MAX_FILE_MB or RE_TW.search(caption or "") is not None
    with file_path.open("rb") as f:
        if send_as_doc:
            await update.effective_message.reply_document(
                document=InputFile(f, filename=file_path.name), caption=caption or ""
            )
        else:
            await update.effective_message.reply_video(
                video=InputFile(f, filename=file_path.name), caption=caption or "", supports_streaming=True
            )

# ========= Handlers =========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(WELCOME_MSG, reply_markup=snap_keyboard(), parse_mode="Markdown")
    await update.message.reply_text(NOTICE_MSG, parse_mode="Markdown")

async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()

    # Snapchat Spotlight/روابط مباشرة: نحاول تنزيلها
    if RE_SNAP_SPOT.search(text):
        await update.message.reply_chat_action("upload_document")
        try:
            p = await download_with_ytdlp(text)
            await send_video_or_doc(update, context, p, caption="Snapchat")
        finally:
            # تنظيف
            pass
        return

    # إذا أرسل يوزر/رابط حساب سناب: نعرض قائمة الستوريات
    snap_user = parse_snap_username(text)
    if snap_user:
        msg = (
            f"📄 **نبذة عن الحساب** `{snap_user}`\n\n"
            "اختر نوع الستوريات المطلوب تنزيلها:\n👇"
        )
        await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=snap_profile_menu(snap_user))
        return

    # TikTok
    if RE_TIKTOK.search(text):
        await update.message.reply_chat_action("upload_document")
        try:
            p = await download_with_ytdlp(text)
            await send_video_or_doc(update, context, p, caption="TikTok")
        finally:
            pass
        return

    # X / Twitter
    if RE_TW.search(text):
        await update.message.reply_chat_action("upload_document")
        try:
            p = await download_with_ytdlp(text)
            # نضع نص يحتوي "twitter" لكي send_video_or_doc يرسله Document
            await send_video_or_doc(update, context, p, caption="twitter")
        finally:
            pass
        return

    await update.message.reply_text(
        "أرسل رابط من: TikTok / X / Snapchat (Spotlight).\n"
        "أو أرسل يوزر سناب/رابط حساب لخيارات الستوريات.",
    )

async def on_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data or ""
    if data == "snap_back":
        await q.edit_message_text("تم ✅", reply_markup=None)
        return

    # أزرار الستوريات لحساب سناب — تنبيه فني
    if data.startswith(("snap_vid:", "snap_img:", "snap_all:")):
        username = data.split(":", 1)[1]
        txt = (
            f"👻 الحساب: `{username}`\n\n"
            "للتحميل التلقائي لكل الستوريات من الحساب يلزم تسجيل دخول (كوكيز) بسبب قيود Snapchat.\n"
            "المدعوم بدون تسجيل دخول: روابط Spotlight/القصص العامة الفردية.\n\n"
            "أرسل أي رابط Spotlight الآن لتنزيله."
        )
        try:
            await q.edit_message_text(txt, parse_mode="Markdown")
        except:
            await q.message.reply_text(txt, parse_mode="Markdown")
        return

# ========= تشغيل =========
async def run_bot():
    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(on_cb))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    # polling بأسلوب v21 (async)
    log.info("✅ Bot is running (polling started)")
    await application.bot.delete_webhook(drop_pending_updates=True)
    await application.run_polling(allowed_updates=Update.ALL_TYPES, close_loop=False)

def main():
    # شغّل Flask في ثريد منفصل
    Thread(target=run_http, daemon=True).start()
    # شغّل البوت
    asyncio.run(run_bot())

if __name__ == "__main__":
    main()
