# main.py
import os
import re
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

# ===== الإعدادات =====
TOKEN = os.getenv("TELEGRAM_TOKEN")  # لا تكتب bot هنا. القيمة فقط هي التوكن.
SNAP_URL = "https://snapchat.com/add/uckr"

# المسموح الآن: TikTok / X (Twitter) / Snapchat
ALLOWED_HOSTS = {
    # X (Twitter)
    "twitter.com", "www.twitter.com", "x.com", "www.x.com", "t.co",
    # Snapchat
    "snapchat.com", "www.snapchat.com", "story.snapchat.com",
    # TikTok
    "tiktok.com", "www.tiktok.com", "vm.tiktok.com", "m.tiktok.com", "vt.tiktok.com"
}

URL_RE = re.compile(r"(https?://\S+)", re.IGNORECASE)

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

WELCOME_MSG = (
    "👋 **مرحبًا!**\n\n"
    f"قبل ما نبدأ… ياليت تضيفني على السناب:\n🔗 {SNAP_URL}\n\n"
    "بعد الإضافة ارجع واضغط **تم، رجعت** أو أرسل **/start** مرة ثانية."
)

NOTICE_MSG = (
    "⚠️ **تنبيه مهم:**\n"
    "لا أُحِل ولا أتحمّل أي مسؤولية عن استخدام البوت في تحميل ما لا يرضي الله.\n"
    "رجاءً استخدمه في الخير فقط.\n\n"
    "أرسل رابط من: TikTok / X (Twitter) / Snapchat."
)

def is_allowed(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
        return any(host == h or host.endswith("." + h) for h in ALLOWED_HOSTS)
    except Exception:
        return False

# ===== Handlers =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # أول /start: ترحيب + زر السناب؛ ثاني /start أو بعد الرجوع: التنبيه + طلب الرابط
    if not context.user_data.get("welcomed"):
        context.user_data["welcomed"] = True
        await update.message.reply_text(
            WELCOME_MSG, parse_mode="Markdown", reply_markup=snap_keyboard()
        )
    else:
        await update.message.reply_text(NOTICE_MSG, parse_mode="Markdown", reply_markup=snap_keyboard())

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "أرسل رابط من: TikTok / X (Twitter) / Snapchat.\n"
        "ملاحظة: للحفاظ على أبعاد فيديو تويتر بدون تغيير نرسله كـ Document.",
        reply_markup=snap_keyboard()
    )

async def snap_back_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text(NOTICE_MSG, parse_mode="Markdown", reply_markup=snap_keyboard())

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

    await update.message.chat.send_action(ChatAction.UPLOAD_DOCUMENT)

    # نستخدم yt-dlp بدون تخفيض جودة. نختار أفضل صيغة متاحة ونرسل.
    # ملاحظة: تيليجرام يفرض حد حجم للوسائط المرسلة عبر البوت. لو الملف ضخم جدًا قد يفشل الإرسال.
    try:
        import yt_dlp
    except Exception:
        await update.message.reply_text("❌ مكتبة yt-dlp غير مثبتة.")
        return

    host = (urlparse(url).hostname or "").lower()
    send_as_document = ("twitter.com" in host) or ("x.com" in host) or ("t.co" in host)

    with tempfile.TemporaryDirectory() as td:
        outtmpl = str(Path(td) / "%(title).100s.%(ext)s")
        # أفضل صيغة ممكنة بدون إعادة ترميز (عادة دمج copy). إذا ما توفر دمج، ينزل أفضل ملف واحد.
        ydl_opts = {
            "outtmpl": outtmpl,
            "format": "bv*+ba/b",  # حاول أفضل فيديو+صوت، وإذا ما توفر فملف واحد
            "merge_output_format": "mp4",  # دمج إلى MP4 (copy غالباً)
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "restrictfilenames": True,
            "nocheckcertificate": True,
            "concurrent_fragment_downloads": 1,
        }

        info = None
        file_path = None
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                # اسم الملف الناتج
                if isinstance(info, dict):
                    fn = info.get("_filename")
                    if fn:
                        fp = Path(fn)
                        if fp.exists():
                            file_path = fp
                if not file_path:
                    # التقط أي ملف ناتج داخل المجلد
                    for p in Path(td).iterdir():
                        if p.is_file():
                            file_path = p
                            break
        except Exception as e:
            log.exception("Download failed", exc_info=e)
            await update.message.reply_text("❌ حدث خطأ غير متوقع أثناء التحميل.", reply_markup=snap_keyboard())
            return

        if not file_path or not file_path.exists():
            await update.message.reply_text("❌ تعذر العثور على الملف بعد التحميل.", reply_markup=snap_keyboard())
            return

        title = (isinstance(info, dict) and info.get("title")) or "الملف"
        title = (title or "الملف")[:990]
        suffix = file_path.suffix.lower()

        try:
            # تويتر: لإبقاء الأبعاد كما هي → نرسل Document
            if send_as_document:
                await update.message.reply_document(document=file_path.open("rb"), caption=title, reply_markup=snap_keyboard())
            else:
                if suffix in {".mp4", ".mov", ".mkv", ".webm"}:
                    await update.message.reply_video(video=file_path.open("rb"), caption=title, reply_markup=snap_keyboard())
                elif suffix in {".jpg", ".jpeg", ".png", ".gif"}:
                    await update.message.reply_photo(photo=file_path.open("rb"), caption=title, reply_markup=snap_keyboard())
                else:
                    # صيغة غير مدعومة كوسائط — أرسلها Document
                    await update.message.reply_document(document=file_path.open("rb"), caption=title, reply_markup=snap_keyboard())
        except Exception as e:
            log.exception("Send failed", exc_info=e)
            await update.message.reply_text(
                "❌ تعذر إرسال الوسائط (قد يكون الحجم كبيرًا لقيود تيليجرام).",
                reply_markup=snap_keyboard()
            )

def run_flask():
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")), debug=False)

def main():
    if not TOKEN:
        raise RuntimeError("حدد TELEGRAM_TOKEN في Render → Environment (القيمة هي التوكن فقط).")

    application = Application.builder().token(TOKEN).build()

    # Handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_cmd))
    application.add_handler(CallbackQueryHandler(snap_back_callback, pattern="^snap_back$"))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_text))

    # شغّل Flask في ثريد خلفي — وخلي run_polling في الثريد الرئيسي (حتى نتجنب مشاكل event loop والإشارات)
    Thread(target=run_flask, daemon=True).start()

    # تأكد من إلغاء أي Webhook (نستخدم Polling)
    try:
        application.bot.delete_webhook(drop_pending_updates=False)
    except Exception:
        pass

    log.info("✅ Telegram polling started")
    # v21: run_polling دالة متزامنة تدير الحدث بنفسها. لا تضعها داخل asyncio.run ولا داخل ثريد آخر.
    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        stop_signals=None,   # لا تسجل سيجنالات (تفادي set_wakeup_fd في Render)
        close_loop=False     # لا تغلق لوب النظام
    )

if __name__ == "__main__":
    main()
