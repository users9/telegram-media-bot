# main.py
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

# ===== إعدادات عامة =====
logging.basicConfig(level=logging.INFO)
TOKEN = os.getenv("TELEGRAM_TOKEN")
SNAP_URL = "https://snapchat.com/add/uckr"

# المنصات المسموح بها
ALLOWED_HOSTS = {
    "youtube.com", "www.youtube.com", "youtu.be",
    "twitter.com", "www.twitter.com", "x.com", "www.x.com",
    "snapchat.com", "www.snapchat.com", "story.snapchat.com",
    "instagram.com", "www.instagram.com",
    "tiktok.com", "www.tiktok.com", "vm.tiktok.com", "m.tiktok.com"
}
URL_RE = re.compile(r"(https?://\S+)", re.IGNORECASE)
TARGET_SIZES = [45 * 1024 * 1024, 28 * 1024 * 1024, 18 * 1024 * 1024]

# ===== Flask Health Check =====
app = Flask(__name__)

@app.route("/")
def home():
    return "Bot is running!"

def start_health_server():
    port = int(os.getenv("PORT", "10000"))
    # نشغل Flask في خيط مستقل
    app.run(host="0.0.0.0", port=port, threaded=True)

# ===== واجهة وأزرار =====
def snap_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👻 إضافة السناب", url=SNAP_URL)],
        [InlineKeyboardButton("✅ تم، رجعت", callback_data="snap_back")]
    ])

WELCOME_MSG = (
    "👋 **مرحبًا!**\n\n"
