# main.py
import os
import re
import logging
import tempfile
import asyncio
from pathlib import Path
from urllib.parse import urlparse

from threading import Thread

from flask import Flask
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# ===== إعدادات =====
TOKEN = os.getenv("TELEGRAM_TOKEN")  # ضع التوكن فقط في متغير البيئة
SNAP_URL = "https://snapchat.com/add/uckr"

# نسمح الروابط هذه فقط (حسب طلبك)
ALLOWED_HOSTS = {
    "twitter.com", "www.twitter.com", "x.com", "www.x.com", "t.co",
    "snapchat.com", "www.snapchat.com", "story.snapchat.com",
    "tiktok.com", "www.tiktok.com", "vm.tiktok.com", "m.tiktok.com", "vt.tiktok.com"
}

URL_RE = re.compile(r"(https?://\S+)", re.IGNORECASE)

# ===== Flask (health check) =====
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

def snap_account_keyboard(username: str) -> InlineKeyboardMarkup:
    # يظهر عند إرسال رابط حساب سناب: خيارات تحميل الستوري (فيديو/صور/الكل)
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📽️ الفيديو (الستوري فقط)", callback_data=f"snap_choice:video:{username}")],
        [InlineKeyboardButton("🖼️ الصور (الستوري فقط)", callback_data=f"snap_choice:photo:{username}")],
        [InlineKeyboardButton("🎯 الكل (ستوري فقط)", callback_data=f"snap_choice:all:{username}")],
        [InlineKeyboardButton("❌ إلغاء", callback_data="snap_cancel")]
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

# ===== مساعدة =====
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "أرسل رابط من TikTok / X (Twitter) / Snapchat.\n"
        "للحسابات السناب: أرسل رابط الحساب (https://www.snapchat.com/@username) وستظهر لك أزرار لاختيار ما تريد تحميله."
    )

# ===== Helpers =====
def is_allowed(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
        return any(host == h or host.endswith("." + h) for h in ALLOWED_HOSTS)
    except Exception:
        return False

def detect_snap_username(url: str):
    # يحاول يلتقط username من روابط snapchat مثل:
    # https://www.snapchat.com/@username  أو https://snapchat.com/add/username
    try:
        p = urlparse(url)
        host = (p.hostname or "").lower()
        if "snapchat.com" not in host:
            return None
        path = p.path or ""
        # /@username
        m = re.search(r"/@([^/?#]+)", path)
        if m:
            return m.group(1)
        # /add/username
        m2 = re.search(r"/add/([^/?#]+)", path)
        if m2:
            return m2.group(1)
        return None
    except Exception:
        return None

# ===== Download logic (blocking) =====
def yt_download_blocking(url: str, outdir: str, prefer_document_for_twitter: bool):
    """
    دالة تشغيلية تعمل في thread: تحميل الملف باستخدام yt-dlp.
    ترجع dict: {"file": path, "title": ..., "info": info_dict}
    """
    import yt_dlp  # استيراد داخل الدالة عشان ما يعيق بداية البوت إن المكتبة مفقودة

    outtmpl = str(Path(outdir) / "%(title).100s.%(ext)s")
    ydl_opts = {
        "outtmpl": outtmpl,
        # format: حاول أفضل فيديو+صوت (bv*+ba) وإلا أفضل ملف واحد (b)
        "format": "bv*+ba/b",
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "restrictfilenames": True,
        "nocheckcertificate": True,
        "concurrent_fragment_downloads": 1,
        # لمنع طوابير طويلة في بعض المضيفات
        "socket_timeout": 15,
        "retries": 2,
    }

    # لو المنصة تويتر، ممكن نرسله كـ document لاحقًا — هنا نترك اليو تي ال دي ال يخزن الملف بأفضل صيغة
    result = {"file": None, "title": None, "info": None}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        result["info"] = info
        # يحاول يستخرج اسم الملف الناتج
        if isinstance(info, dict):
            fn = info.get("_filename") or info.get("requested_downloads")  # fallback
            # yt-dlp عادة يضع _filename
            if fn:
                p = Path(fn)
                if p.exists():
                    result["file"] = str(p)
        # لو ما حصل ملف، التقط أول ملف في المجلد
        if not result["file"]:
            for p in Path(outdir).iterdir():
                if p.is_file():
                    result["file"] = str(p)
                    break
        if isinstance(info, dict):
            result["title"] = info.get("title") or Path(result["file"]).name
    return result

# ===== Handlers =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # عرض الترحيب ثم التنبيه بزر السناب
    try:
        await update.message.reply_text(WELCOME_MSG, reply_markup=snap_keyboard())
        await asyncio.sleep(0.2)
        await update.message.reply_text(NOTICE_MSG)
    except Exception as e:
        log.exception("start handler failed", exc_info=e)

async def snap_back_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # عندما يضغط المستخدم "تم، رجعت"
    query = update.callback_query
    await query.answer()
    await query.message.reply_text(NOTICE_MSG)

async def snap_choice_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # نمط callback_data: snap_choice:<type>:<username>
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    parts = data.split(":", 2)
    if len(parts) != 3:
        await query.message.reply_text("خطأ في الطلب.")
        return
    _, choice, username = parts
    await query.message.reply_text(f"🔄 جاري التحميل لستوري {username} (نوع: {choice}) — سأحاول تحميل كل ما أمكن.")
    # نكوّن رابط افتراضي أو نبحث طريقة تحميل ستوري سناب — لاحظ: سناب قد يتطلب مصادقة أو API خاص.
    # هنا نحاول ببساطة تحميل رابط "story.snapchat.com" لو أمكن؛ إذا فشل نعلم المستخدم.
    # **تحذير:** تحميل ستوريات سناب غالباً يحتاج صلاحيات خاصة (cookies / تسجيل دخول).
    await query.message.reply_text("⚠️ ملاحظة: تحميل محتوى Snapchat غالبًا ما يحتاج تسجيل دخول/كوكيز. إذا فشل التحميل فهذا السبب.")
    # placeholder: نعيد رسالة تفيد بعدم دعم التنزيل أو نقوم بمحاولة عامة (يمكن تعديل لاحقًا)
    await query.message.reply_text("❌ للأسف: التحميل التلقائي لستوري Snapchat غير مضمون هنا بدون إعداد خاص. يمكنك تزويدي برابط مباشر للمقطع إن وجد.")

async def snap_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("تم الإلغاء.")
    await query.message.reply_text("تم إلغاء العملية.", reply_markup=snap_keyboard())

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    if not text:
        return

    # بحث عن رابط
    m = URL_RE.search(text)
    if not m:
        await update.message.reply_text("أرسل رابطًا صالحًا من TikTok / X / Snapchat.", reply_markup=snap_keyboard())
        return

    url = m.group(1).rstrip(".,)\"'")  # إزالة علامات ترقيم محتملة في آخر الرابط
    if not is_allowed(url):
        await update.message.reply_text("هذه المنصة غير مدعومة. أرسل رابطًا من TikTok / X / Snapchat.", reply_markup=snap_keyboard())
        return

    # لو رابط حساب سناب (مثال) اعرض أزرار اختيار الستوري
    snap_user = detect_snap_username(url)
    if snap_user:
        await update.message.reply_text(
            f"تم اكتشاف حساب Snapchat: `{snap_user}`\nاختر نوع الوسائط التي تريد تحميلها (ستوري):",
            reply_markup=snap_account_keyboard(snap_user)
        )
        return

    # تأكيد: نعلن للمستخدم أننا بدأنا
    await update.message.reply_text("🔄 جاري محاولة التحميل — انتظر لحظة ...", reply_markup=snap_keyboard())

    # عملية التحميل قد تكون بطيئة — ننفذها في thread حتى لا نعرقل لوب البوت
    async def do_download_and_send():
        try:
            with tempfile.TemporaryDirectory() as td:
                # تنزيل باستخدام yt-dlp في thread
                loop = asyncio.get_running_loop()
                result = await loop.run_in_executor(None, yt_download_blocking, url, td, False)
                file_path = result.get("file")
                title = (result.get("title") or "الملف")[:900]
                info = result.get("info") or {}
                if not file_path or not Path(file_path).exists():
                    await update.message.reply_text("❌ تعذّر تحميل الملف أو العثور عليه بعد التحميل.", reply_markup=snap_keyboard())
                    return

                suffix = Path(file_path).suffix.lower()
                # لو الرابط من X/Twitter نرسل كـ document للحفاظ على الأبعاد
                host = (urlparse(url).hostname or "").lower()
                send_as_document = any(h in host for h in ("twitter.com", "x.com", "t.co"))

                try:
                    if send_as_document:
                        await update.message.reply_document(document=Path(file_path).open("rb"), caption=title, reply_markup=snap_keyboard())
                    else:
                        if suffix in {".mp4", ".mov", ".mkv", ".webm"}:
                            await update.message.reply_video(video=Path(file_path).open("rb"), caption=title, reply_markup=snap_keyboard())
                        elif suffix in {".jpg", ".jpeg", ".png", ".gif"}:
                            await update.message.reply_photo(photo=Path(file_path).open("rb"), caption=title, reply_markup=snap_keyboard())
                        else:
                            await update.message.reply_document(document=Path(file_path).open("rb"), caption=title, reply_markup=snap_keyboard())
                except Exception as send_err:
                    log.exception("Send failed", exc_info=send_err)
                    # لو حجم الملف كبير جداً، ونزعج البوت، نخبر المستخدم
                    await update.message.reply_text("❌ تعذّر إرسال الملف (ربما حجمه كبير جدًا لقيود تيليجرام).", reply_markup=snap_keyboard())
        except Exception as e:
            log.exception("Download failed", exc_info=e)
            # إذا كان خطأ معروف (مثل yt-dlp رفع استثناء به رسالة) نعرض جزءًا منها
            msg = str(e)
            if "HTTP Error 403" in msg or "Requested content is not available" in msg:
                await update.message.reply_text(
                    "❌ فشل التحميل: المحتوى قد يتطلب تسجيل دخول أو محمي (HTTP 403). "
                    "بعض الروابط تتطلب كوكيز/تسجيل دخول — البوت الآن لا يستخدم كوكيز.",
                    reply_markup=snap_keyboard()
                )
            elif "Conflict: terminated by other getUpdates request" in msg:
                await update.message.reply_text(
                    "❌ فشل: يبدو أن هناك نسخة أخرى من البوت تعمل (getUpdates conflict). تأكد من إيقاف أي نسخة أخرى أو Webhook.",
                    reply_markup=snap_keyboard()
                )
            else:
                await update.message.reply_text("❌ حدث خطأ غير متوقع أثناء التحميل.", reply_markup=snap_keyboard())

    # شغّل المهمة لكن لا تنتظرها هنا (ستُرسل الردود عندما تكتمل)
    asyncio.create_task(do_download_and_send())

# ===== CallbackQuery router =====
async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = update.callback_query.data or ""
    if data == "snap_back":
        await snap_back_callback(update, context)
    elif data.startswith("snap_choice:"):
        await snap_choice_callback(update, context)
    elif data == "snap_cancel":
        await snap_cancel_callback(update, context)
    else:
        await update.callback_query.answer()

# ===== Run server & bot =====
def run_flask():
    # PORT من Render أو 10000 محليًا
    port = int(os.getenv("PORT", "10000"))
    log.info("Starting Flask healthcheck on port %s", port)
    app.run(host="0.0.0.0", port=port, debug=False)

def main():
    if not TOKEN:
        raise RuntimeError("حدد TELEGRAM_TOKEN في متغيرات البيئة (قيمة التوكن فقط).")

    application = Application.builder().token(TOKEN).build()

    # Handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_cmd))
    application.add_handler(CallbackQueryHandler(callback_router))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_text))

    # شغّل Flask في ثريد منفصل
    Thread(target=run_flask, daemon=True).start()

    # احذف أي Webhook قديم
    try:
        application.bot.delete_webhook(drop_pending_updates=False)
    except Exception:
        pass

    log.info("✅ Telegram polling starting...")
    # v21: run_polling يملك إدارة الـ event loop داخله؛ لا تستخدم asyncio.run حوله.
    # stop_signals=None و close_loop=False للمشغّلات السحابية مثل Render.
    application.run_polling(allowed_updates=None, stop_signals=None, close_loop=False)

if __name__ == "__main__":
    main()
