"""
=============================================================================
Cyvex Store Bot - Enterprise Edition
=============================================================================
A fully automated, AI-driven, highly scalable Telegram Store Bot.
Features:
- PostgreSQL Database Integration
- OpenRouter AI Anti-Fraud Engine ("openrouter/auto")
- Local OCR (Tesseract) Fallback Verification
- Automated Strike & Ban System (With Collage Evidence)
- Background Task Scheduler (Order Cleanup)
- Comprehensive Admin Suite & Settings Dashboard
- State Persistence & Async Threading
- Anti-Spam Rate Limiting
=============================================================================
"""

import logging
import os
import hashlib
import base64
import requests
import random
import string
import asyncio
import time
import json
import psycopg2
import psycopg2.extras
import io
from datetime import datetime, timedelta
from dotenv import load_dotenv
from logging.handlers import RotatingFileHandler
from flask import Flask, send_file
from threading import Thread

# For Local Algorithm Verification (OCR & Image Processing)
import pytesseract
from PIL import Image, ImageEnhance

from telegram import (Update, InlineKeyboardButton, InlineKeyboardMarkup,
                      InputMediaPhoto)
from telegram.ext import (Application, CommandHandler, MessageHandler,
                          CallbackQueryHandler, filters, ContextTypes,
                          ConversationHandler, PicklePersistence)
from telegram.constants import ParseMode

from telegram.error import BadRequest

async def safe_edit(query, text, kb=None):
    try:
        await query.edit_message_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    except BadRequest as e:
        if "Message is not modified" not in str(e): raise e

# ==========================================
# ⚙️ CONFIGURATION & CONSTANTS
# ==========================================
load_dotenv()

BOT_TOKEN = os.getenv("8781246692:AAGZinUj4fOhO3B5B379W653u27zbztTLJU")
ADMIN_ID = int(os.getenv("8464208627", "0"))
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
DATABASE_URL = os.getenv("DATABASE_URL")
QR_IMAGE_PATH = "qr.jpg"

# Setup robust logging
log_formatter = logging.Formatter(
    '%(asctime)s - %(name)s - %(levelname)s - %(message)s')
log_file = 'bot_logs.log'
file_handler = RotatingFileHandler(log_file,
                                   maxBytes=5 * 1024 * 1024,
                                   backupCount=2)
file_handler.setFormatter(log_formatter)
console_handler = logging.StreamHandler()
console_handler.setFormatter(log_formatter)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.addHandler(file_handler)
logger.addHandler(console_handler)

# Conversation States (Expanded for Admin Suite)
(ADD_P_NAME, ADD_P_DESC, ADD_P_OPTS, ADD_P_APK, EDIT_P_NAME, EDIT_P_DESC,
 EDIT_P_OPTS, EDIT_P_APK, B_MSG, U_SEARCH, WAIT_FOR_KEY) = range(11)

# ==========================================
# 🏛️ DATABASE LAYER (POSTGRESQL)
# ==========================================


def get_db_connection():
    """Establishes a robust connection to the PostgreSQL database."""
    try:
        conn = psycopg2.connect(DATABASE_URL,
                                cursor_factory=psycopg2.extras.RealDictCursor)
        return conn
    except Exception as e:
        logger.error(f"Critical Database connection failed: {e}")
        return None


def init_db():
    """Initializes the database schema if it doesn't exist."""
    conn = get_db_connection()
    if not conn:
        logger.error("Skipping DB Init due to connection failure.")
        return
    cur = conn.cursor()

    # Users Table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
            name TEXT,
            username TEXT,
            joined TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            chances INT DEFAULT 3,
            is_banned BOOLEAN DEFAULT FALSE,
            total_spent FLOAT DEFAULT 0.0,
            strike_media JSONB DEFAULT '[]'::jsonb
        )
    """)

    # Products Table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id TEXT PRIMARY KEY,
            name TEXT,
            description TEXT,
            options JSONB,
            apk_link TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Orders Table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id TEXT PRIMARY KEY,
            user_id BIGINT,
            product TEXT,
            validity TEXT,
            amount FLOAT,
            status TEXT DEFAULT 'waiting_payment',
            img_hash TEXT,
            file_id TEXT,
            ai_flag TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Settings Table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value JSONB
        )
    """)

    # Default Configs
    default_settings = {
        'maintenance': '{"enabled": false}',
        'ai_verification': '{"enabled": true}',
        'ocr_verification': '{"enabled": true}'
    }
    for k, v in default_settings.items():
        cur.execute(
            "INSERT INTO settings (key, value) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (k, v))

    # --- AUTO MIGRATION SAFEGUARDS ---
    try:
        cur.execute(
            "ALTER TABLE products ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;"
        )
        cur.execute(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS total_spent FLOAT DEFAULT 0.0;"
        )
        cur.execute(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS strike_media JSONB DEFAULT '[]'::jsonb;"
        )
    except Exception as e:
        logger.warning(f"Auto-Migration warning: {e}")
        conn.rollback()  # Rollback safe-guard

    conn.commit()
    cur.close()
    conn.close()
    logger.info("Database Schema Initialized Successfully.")


init_db()

# ==========================================
# 🎨 UTILS, STYLING & FORMATTING
# ==========================================


def to_stylish(text):
    """Converts standard alphanumeric text to Bold Serif."""
    if not text: return ""
    normal = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    stylish = "𝐀𝐁𝐂𝐃𝐄𝐅𝐆𝐇𝐈𝐉𝐊𝐋𝐌𝐍𝐎𝐏𝐐𝐑𝐒𝐓𝐔𝐕𝐖𝐗𝐘𝐙𝐚𝐛𝐜𝐝𝐞𝐟𝐠𝐡𝐢𝐣𝐤𝐥𝐦𝐧𝐨𝐩𝐪𝐫𝐬𝐭𝐮𝐯𝐰𝐱𝐲𝐳𝟎𝟏𝟐𝟑𝟒𝟓𝟔𝟕𝟖𝟗"
    trans = str.maketrans(normal, stylish)
    return str(text).translate(trans)


def format_validity(val):
    """Formats shorthand validity codes (e.g., 1d -> 1 Day, 2d -> 2 Days)."""
    v = val.lower().strip()
    if v.endswith('d'):
        num_str = v.replace('d', '')
        try:
            num = int(num_str)
            suffix = "Day" if num == 1 else "Days"
            return f"{num} {suffix}"
        except:
            return f"{num_str} Day"
    if v == "fs":
        return to_stylish("Full Season")
    if v == "lt" or v == "ltd":
        return to_stylish("Lifetime")
    return val.title()


def generate_order_id():
    """Generates a random 8-character Alpha-Numeric Order ID."""
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=8))


def get_product_id(name):
    """Generates a short hash-based ID for products to fit callback data limits."""
    return hashlib.md5(name.encode()).hexdigest()[:8]


def get_setting(key, default_val):
    """Retrieves a specific setting from the DB."""
    conn = get_db_connection()
    if not conn: return default_val
    cur = conn.cursor()
    cur.execute("SELECT value FROM settings WHERE key = %s", (key, ))
    row = cur.fetchone()
    cur.close()
    conn.close()
    if row: return row['value']
    return default_val


# ==========================================
# 🔍 STEP 1: LOCAL ALGORITHM (OCR)
# ==========================================


def preprocess_image_for_ocr(img_bytes):
    """Applies grayscaling and thresholding to improve OCR accuracy on receipts."""
    try:
        image = Image.open(io.BytesIO(img_bytes)).convert('L')
        # Enhance contrast
        enhancer = ImageEnhance.Contrast(image)
        image = enhancer.enhance(2.0)
        # Apply strict threshold to make text pop
        image = image.point(lambda p: p > 150 and 255)
        return image
    except Exception as e:
        logger.error(f"Image preprocessing failed: {e}")
        return Image.open(io.BytesIO(img_bytes))


def local_algorithm_verification(image_bytes):
    """
    Uses OCR (Tesseract) to locally scan the image for payment-related keywords.
    Acts as a strong fallback if the AI model fails or hallucinates.
    """
    settings = get_setting('ocr_verification', {"enabled": True})
    if not settings.get('enabled', True):
        return False, ["OCR Disabled by Admin"]

    try:
        img = preprocess_image_for_ocr(image_bytes)
        text = pytesseract.image_to_string(img).lower()

        # Keywords highly specific to Indian Payment Apps
        keywords = [
            'paid', '₹', 'rs', 'inr', 'transaction', 'utr', 'successful',
            'completed', 'sent', 'payment', 'debited', 'upi', 'rupees',
            'wallet'
        ]

        found_keywords = [k for k in keywords if k in text]

        # Passing Criteria: Find at least 2 distinct payment keywords
        if len(found_keywords) >= 2:
            logger.info(f"Local OCR Passed. Keywords found: {found_keywords}")
            return True, found_keywords

        logger.info(
            f"Local OCR Failed. Insufficient keywords. Found: {found_keywords}"
        )
        return False, found_keywords
    except Exception as e:
        logger.error(f"Local OCR Algorithm crashed: {e}")
        return False, ["OCR Engine Error"]


# ==========================================
# 🧠 STEP 2: AI ANTI-FRAUD ENGINE
# ==========================================


def analyze_payment_screenshot(image_bytes):
    """
    Analyzes an image using OpenRouter AI.
    Implements exponential backoff retries and strict JSON validation.
    """
    settings = get_setting('ai_verification', {"enabled": True})
    if not settings.get('enabled', True):
        return {
            "is_payment": False,
            "confidence": "LOW",
            "amount": 0,
            "suspicious": True,
            "reason": "AI Verification Disabled via Settings",
            "api_failed": True
        }

    if not OPENROUTER_API_KEY:
        return {
            "is_payment": False,
            "confidence": "LOW",
            "amount": 0,
            "suspicious": True,
            "reason": "AI Config Missing",
            "api_failed": True
        }

    base64_image = base64.b64encode(image_bytes).decode('utf-8')
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }

    prompt = """
    CRITICAL SYSTEM INSTRUCTION: Analyze this image for a digital payment receipt from apps like PhonePe, GPay, Paytm, etc.
    Verification Requirements:
    1. Look for text like "Paid Successfully", "Transaction ID", "UTR", "Completed", "Paid to", or "Payment to".
    2. Extract the exact amount paid in INR (look for ₹ or Rs).
    3. Determine if it's a genuine receipt or a fake/meme/unrelated image.

    Response MUST be a clean JSON object ONLY. No markdown, no prefixes.
    {
        "is_payment": boolean,
        "confidence": "high" | "medium" | "low",
        "amount": number,
        "utr": string | null,
        "suspicious": boolean,
        "reason": "short explanation of the findings"
    }
    """

    payload = {
        "model":
        "openrouter/auto",
        "messages": [{
            "role":
            "user",
            "content": [{
                "type": "text",
                "text": prompt
            }, {
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/jpeg;base64,{base64_image}"
                }
            }]
        }]
    }

    max_retries = 4
    for attempt in range(max_retries):
        try:
            logger.info(f"AI Verification Attempt {attempt + 1}/{max_retries}")
            response = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers=headers,
                json=payload,
                timeout=40)
            response.raise_for_status()
            raw_content = response.json()['choices'][0]['message']['content']

            # Sanitize JSON string (remove markdown code blocks if present)
            clean_json = raw_content.replace('```json',
                                             '').replace('```', '').strip()
            result = json.loads(clean_json)
            result["api_failed"] = False
            return result

        except (requests.RequestException, json.JSONDecodeError,
                KeyError) as e:
            logger.warning(f"AI Attempt {attempt+1} failed: {e}")
            if attempt < max_retries - 1:
                time.sleep(2**attempt)  # Exponential backoff: 1s, 2s, 4s...
            else:
                logger.error("AI engine exhausted all retries.")
                return {
                    "is_payment": False,
                    "confidence": "LOW",
                    "amount": 0,
                    "suspicious": True,
                    "reason":
                    f"System Alert: AI Request Failed after {max_retries} attempts.",
                    "api_failed": True
                }


# ==========================================
# 🛠️ GLOBAL ERROR HANDLER & BACKGROUND JOBS
# ==========================================


async def global_error_handler(update: object,
                               context: ContextTypes.DEFAULT_TYPE):
    """Log the error and notify silently."""
    logger.error("Exception while handling an update:", exc_info=context.error)


async def cleanup_stale_orders(context: ContextTypes.DEFAULT_TYPE):
    """
    Background task: Cancels orders that have been waiting for payment for > 24 hours.
    Frees up database resources and maintains clean logs.
    """
    conn = get_db_connection()
    if not conn: return
    cur = conn.cursor()

    threshold_time = datetime.now() - timedelta(hours=24)
    try:
        cur.execute(
            """
            UPDATE orders 
            SET status = 'cancelled_timeout' 
            WHERE status = 'waiting_payment' AND timestamp < %s
            RETURNING id, user_id
        """, (threshold_time, ))
        cancelled = cur.fetchall()
        conn.commit()

        if cancelled:
            logger.info(
                f"Cleanup Job: Cancelled {len(cancelled)} stale orders.")

    except Exception as e:
        logger.error(f"Error in cleanup job: {e}")
    finally:
        cur.close()
        conn.close()


# ==========================================
# 📱 UI COMPONENT BUILDERS
# ==========================================


def get_main_menu_kb():
    return InlineKeyboardMarkup(
        [[
            InlineKeyboardButton(f"🛍️ {to_stylish('View Products')}",
                                 callback_data="view_products")
        ],
         [
             InlineKeyboardButton(f"👤 {to_stylish('My Profile')}",
                                  callback_data="my_profile"),
             InlineKeyboardButton(f"📦 {to_stylish('My Orders')}",
                                  callback_data="my_orders")
         ],
         [
             InlineKeyboardButton(f"💬 {to_stylish('Support & Rules')}",
                                  callback_data="support")
         ]])


def get_admin_menu_kb(maint_status):
    return InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("📦 Manage Products", callback_data="adm_m_p"),
            InlineKeyboardButton("👥 Manage Users", callback_data="adm_m_u")
        ],
         [
             InlineKeyboardButton("⏳ Pending Approvals",
                                  callback_data="adm_pending_orders")
         ],
         [
             InlineKeyboardButton("📢 Broadcast Message",
                                  callback_data="adm_b_start"),
             InlineKeyboardButton("📊 System Stats", callback_data="adm_stats")
         ],
         [
             InlineKeyboardButton("⚙️ System Settings",
                                  callback_data="adm_settings")
         ],
         [
             InlineKeyboardButton(f"🚧 Maintenance: {maint_status}",
                                  callback_data="adm_toggle_maint")
         ],
         [InlineKeyboardButton("❌ Close Console", callback_data="adm_close")]])


# ==========================================
# 🛒 USER INTERFACE ROUTER
# ==========================================


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    settings = get_setting('maintenance', {"enabled": False})
    if settings.get('enabled') and user.id != ADMIN_ID:
        await update.message.reply_text(
            f"🛠️ <b>{to_stylish('System Maintenance')}</b>\n\nOur servers are currently undergoing upgrades to serve you better. Please try again in a few hours.",
            parse_mode=ParseMode.HTML)
        return

    conn = get_db_connection()
    if not conn:
        await update.message.reply_text(
            "Database connection error. Try again later.")
        return
    cur = conn.cursor()

    cur.execute("SELECT is_banned FROM users WHERE user_id = %s", (user.id, ))
    u_row = cur.fetchone()

    if u_row and u_row['is_banned']:
        await update.message.reply_text(
            f"🚫 <b>{to_stylish('Access Denied')}</b>\n\nYou have been permanently banned due to a violation of our Terms of Service (e.g., submitting fraudulent payment proofs).",
            parse_mode=ParseMode.HTML)
        cur.close()
        conn.close()
        return

    if not u_row:
        cur.execute(
            "INSERT INTO users (user_id, name, username) VALUES (%s, %s, %s)",
            (user.id, user.full_name, user.username))
        conn.commit()

    text = (
        f"🌌 <b>{to_stylish('Welcome to Prime Store')}</b>\n\n"
        f"Your premium destination for automated digital deliveries. All payments are verified instantly by our AI systems.\n\n"
        f"Select an option from the menu below to begin:")

    if update.message:
        await update.message.reply_text(text,
                                        reply_markup=get_main_menu_kb(),
                                        parse_mode=ParseMode.HTML)
    else:
        await update.callback_query.edit_message_text(
            text, reply_markup=get_main_menu_kb(), parse_mode=ParseMode.HTML)

    cur.close()
    conn.close()


async def user_button_handler(update: Update,
                              context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = update.effective_user.id

    settings = get_setting('maintenance', {"enabled": False})
    if settings.get('enabled') and user_id != ADMIN_ID:
        await query.answer("Bot is in Maintenance Mode. Please wait.",
                           show_alert=True)
        return

    conn = get_db_connection()
    cur = conn.cursor()

    try:
        # --- NAVIGATION & PROFILES ---
        if data == "main_menu":
            await start(update, context)

        elif data == "my_profile":
            cur.execute("SELECT * FROM users WHERE user_id = %s", (user_id, ))
            u = cur.fetchone()
            if u:
                joined_date = u['joined'].strftime('%B %d, %Y')
                text = (
                    f"👤 <b>{to_stylish('Your Profile')}</b>\n\n"
                    f"<b>Name:</b> {u['name']}\n"
                    f"<b>ID:</b> <code>{u['user_id']}</code>\n"
                    f"<b>Member Since:</b> {joined_date}\n\n"
                    f"📊 <b>Account Stats:</b>\n"
                    f"↳ <b>Total Spent:</b> ₹{u['total_spent']}\n"
                    f"↳ <b>Verification Strikes Left:</b> {u['chances']}/3\n\n"
                    f"<i>Maintain a clean record to avoid permanent bans.</i>")
                await query.edit_message_text(
                    text,
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton(f"🔙 {to_stylish('Back')}",
                                             callback_data="main_menu")
                    ]]),
                    parse_mode=ParseMode.HTML)

        elif data == "support":
            text = (
                f"💬 <b>{to_stylish('Support & Rules')}</b>\n\n"
                f"<b>Rules:</b>\n"
                f"1. Do not use fake or altered payment screenshots.\n"
                f"2. Do not upload the same screenshot twice.\n"
                f"3. Violating these rules costs a 'strike'. 3 strikes = Auto Ban.\n\n"
                f"<b>Need Help?</b>\n"
                f"If you paid but didn't receive your key, or if our AI incorrectly rejected your image, contact our admin team with your Order ID."
            )
            await query.edit_message_text(text,
                                          reply_markup=InlineKeyboardMarkup([[
                                              InlineKeyboardButton(
                                                  f"🔙 {to_stylish('Back')}",
                                                  callback_data="main_menu")
                                          ]]),
                                          parse_mode=ParseMode.HTML)

        # --- PRODUCT BROWSING ---
        elif data == "view_products":
            cur.execute("SELECT * FROM products ORDER BY created_at DESC")
            prods = cur.fetchall()
            if not prods:
                await query.edit_message_text(
                    f"😔 <b>{to_stylish('No Products Available')}</b>\nWe are restocking soon. Please check back later!",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("Back", callback_data="main_menu")
                    ]]),
                    parse_mode=ParseMode.HTML)
            else:
                kb = []
                for p in prods:
                    starting = min(
                        p['options'].values()) if p['options'] else 0
                    kb.append([
                        InlineKeyboardButton(
                            f"🛒 {p['name']} (from ₹{starting})",
                            callback_data=f"prod_{p['id']}")
                    ])
                kb.append([
                    InlineKeyboardButton(f"🔙 {to_stylish('Back to Menu')}",
                                         callback_data="main_menu")
                ])
                await query.edit_message_text(
                    f"🛍️ <b>{to_stylish('Available Products')}</b>\n\nSelect an item below:",
                    reply_markup=InlineKeyboardMarkup(kb),
                    parse_mode=ParseMode.HTML)

        elif data.startswith("prod_"):
            pid = data.split("_")[1]
            cur.execute("SELECT * FROM products WHERE id = %s", (pid, ))
            p = cur.fetchone()
            if p:
                text = f"📦 <b>{to_stylish(p['name'])}</b>\n\n<i>{p['description']}</i>\n\n{to_stylish('Select Your Plan')}:"
                kb = [[
                    InlineKeyboardButton(f"⏳ {format_validity(v)} - ₹{pr}",
                                         callback_data=f"val_{pid}_{v}")
                ] for v, pr in p['options'].items()]
                kb.append([
                    InlineKeyboardButton(f"🔙 {to_stylish('Back to List')}",
                                         callback_data="view_products")
                ])
                await query.edit_message_text(
                    text,
                    reply_markup=InlineKeyboardMarkup(kb),
                    parse_mode=ParseMode.HTML)

        # --- CHECKOUT FLOW & TOS ---
        elif data.startswith("val_"):
            parts = data.split("_")
            pid, val = parts[1], "_".join(parts[2:])
            cur.execute("SELECT * FROM products WHERE id = %s", (pid, ))
            p = cur.fetchone()
            if p and val in p['options']:
                price = p['options'][val]
                text = (
                    f"⚠️ <b>{to_stylish('Order Confirmation & Policy')}</b>\n\n"
                    f"🛒 <b>Product:</b> {p['name']}\n"
                    f"⏳ <b>Plan:</b> {format_validity(val)}\n"
                    f"💰 <b>Total Amount:</b> ₹{price}\n\n"
                    f"<b>Terms of Purchase:</b>\n"
                    f"• All sales are final; no refunds or exchanges.\n"
                    f"• You agree to the product's features and limits.\n"
                    f"• Fraudulent abuse results in an instant ban.\n"
                    f"• Ensure compatibility before proceeding.\n"
                    f"<i>By paying, you accept these terms.</i>")
                kb = [[
                    InlineKeyboardButton(f"✅ {to_stylish('Agree & Pay')}",
                                         callback_data=f"conf_{pid}_{val}")
                ],
                      [
                          InlineKeyboardButton(f"❌ {to_stylish('Cancel')}",
                                               callback_data=f"prod_{pid}")
                      ]]
                await query.edit_message_text(
                    text,
                    reply_markup=InlineKeyboardMarkup(kb),
                    parse_mode=ParseMode.HTML)

        elif data.startswith("conf_"):
            # ANTI-SPAM Rate Limiting Check
            now = time.time()
            last_conf = context.user_data.get('last_conf_time', 0)
            if now - last_conf < 10:  # 10 second cooldown on creating orders
                await query.answer(
                    "Please wait a moment before initiating another order.",
                    show_alert=True)
                return
            context.user_data['last_conf_time'] = now

            parts = data.split("_")
            pid, val = parts[1], "_".join(parts[2:])
            cur.execute("SELECT * FROM products WHERE id = %s", (pid, ))
            p = cur.fetchone()
            if not p: return

            # Cancel any existing pending orders for this user to keep DB clean
            cur.execute(
                "UPDATE orders SET status = 'cancelled_abandoned' WHERE user_id = %s AND status = 'waiting_payment'",
                (user_id, ))

            oid = generate_order_id()
            cur.execute(
                "INSERT INTO orders (id, user_id, product, validity, amount) VALUES (%s, %s, %s, %s, %s)",
                (oid, user_id, p['name'], val, p['options'][val]))
            conn.commit()

            text = (
                f"🧾 <b>{to_stylish('Order Initiated')}</b>\n\n"
                f"🆔 <b>Order ID:</b> <code>{oid}</code>\n"
                f"💰 <b>Amount to Pay:</b> ₹{p['options'][val]}\n\n"
                f"<b>Instructions:</b>\n"
                f"1️⃣ Scan the QR code below or pay via UPI.\n"
                f"2️⃣ Take a screenshot of the <b>Successful Transaction</b> screen.\n"
                f"3️⃣ Send the screenshot directly in this chat.\n\n"
                f"<i>⚠️ Do NOT send fake/edited images. Our system enforces strict verification.</i>"
            )

            if os.path.exists(QR_IMAGE_PATH):
                await context.bot.send_photo(chat_id=user_id,
                                             photo=open(QR_IMAGE_PATH, 'rb'),
                                             caption=text,
                                             parse_mode=ParseMode.HTML)
                await query.message.delete()
            else:
                await query.edit_message_text(
                    f"{text}\n\n<b>[⚠️ QR Image Missing on Server. Please contact Admin.]</b>",
                    parse_mode=ParseMode.HTML)

        # --- ORDER HISTORY ---
        elif data == "my_orders":
            cur.execute(
                "SELECT * FROM orders WHERE user_id = %s ORDER BY timestamp DESC LIMIT 5",
                (user_id, ))
            ords = cur.fetchall()
            if not ords:
                await query.edit_message_text(
                    f"📦 <b>{to_stylish('You have no orders yet.')}</b>",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("🔙 Back",
                                             callback_data="main_menu")
                    ]]),
                    parse_mode=ParseMode.HTML)
            else:
                text = f"📦 <b>{to_stylish('Your Recent Orders')}</b>\n<i>(Showing last 5 records)</i>\n\n"
                for o in ords:
                    st = o['status'].replace('_', ' ').title()
                    emoji = "✅" if o['status'] == 'completed' else "⏳" if o[
                        'status'] in ['pending', 'waiting_payment'] else "❌"
                    text += f"🆔 <code>{o['id']}</code> | <b>{o['product']}</b>\n↳ Status: {emoji} {st}\n↳ Amount: ₹{o['amount']}\n\n"
                await query.edit_message_text(
                    text,
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton(f"🔙 {to_stylish('Back')}",
                                             callback_data="main_menu")
                    ]]),
                    parse_mode=ParseMode.HTML)

    except Exception as e:
        logger.error(f"User Callback Error: {e}")
        await query.answer("An error occurred. Try again.", show_alert=True)
    finally:
        cur.close()
        conn.close()


# ==========================================
# 🛡️ 3-STEP ANTI-FRAUD PHOTO VERIFICATION
# ==========================================


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    settings = get_setting('maintenance', {"enabled": False})
    if settings.get('enabled') and user_id != ADMIN_ID:
        await update.message.reply_text(
            f"🛠️ {to_stylish('System is in Maintenance Mode')}.",
            parse_mode=ParseMode.HTML)
        return

    conn = get_db_connection()
    cur = conn.cursor()

    # 0. Basic Checks
    cur.execute(
        "SELECT chances, is_banned, strike_media FROM users WHERE user_id = %s",
        (user_id, ))
    u = cur.fetchone()
    if not u or u['is_banned']:
        cur.close()
        conn.close()
        return

    cur.execute(
        "SELECT * FROM orders WHERE user_id = %s AND status = 'waiting_payment' ORDER BY timestamp DESC LIMIT 1",
        (user_id, ))
    order = cur.fetchone()
    if not order:
        cur.close()
        conn.close()
        return

    # Download Image Data
    photo = update.message.photo[-1]
    file = await photo.get_file()
    img_bytes = bytes(await file.download_as_bytearray())
    img_hash = hashlib.sha256(img_bytes).hexdigest()

    # 1. Local Database Verification (Duplicate Hash Check)
    cur.execute("SELECT id FROM orders WHERE img_hash = %s", (img_hash, ))
    if cur.fetchone():
        strike_media = u.get('strike_media') or []
        strike_media.append(photo.file_id)
        new_c = u['chances'] - 1

        cur.execute(
            "UPDATE users SET chances = %s, strike_media = %s WHERE user_id = %s",
            (new_c, json.dumps(strike_media), user_id))
        conn.commit()
        await update.message.reply_text(
            f"🚨 <b>{to_stylish('Security Alert: Duplicate Screenshot')}</b>\n"
            f"This exact image has been uploaded before.\n\n"
            f"<i>Strike applied. Chances remaining: {new_c}/3</i>",
            parse_mode=ParseMode.HTML)
        if new_c <= 0: await trigger_ban_workflow(context, user_id)
        cur.close()
        conn.close()
        return

    p_msg = await update.message.reply_text(
        "🔄 Verifying your payment screenshot through our system... Please wait.",
        parse_mode=ParseMode.HTML)

    # Run heavy operations in background threads to avoid blocking other users
    algo_passed, keywords_found = await asyncio.to_thread(
        local_algorithm_verification, img_bytes)
    ai = await asyncio.to_thread(analyze_payment_screenshot, img_bytes)

    # 4. Cross-Verification Logic & Scoring
    is_valid_payment = False
    flag = "Pending Review"

    if ai.get('is_payment'):
        # AI confirms it's a payment
        is_valid_payment = True
        flag = "Verified by AI"
    elif algo_passed:
        # AI failed or hallucinated, BUT local OCR found strong payment keywords. Bypass AI.
        is_valid_payment = True
        reason = ai.get('reason', 'AI Rejected')
        flag = f"AI Failed ({reason}), BUT OCR Algorithm Passed {keywords_found}"
    else:
        # Both completely failed. Likely a selfie or meme.
        is_valid_payment = False

    # Immediate rejection if both fail - completely concealing AI rationale from user
    if not is_valid_payment:
        strike_media = u.get('strike_media') or []
        strike_media.append(photo.file_id)
        new_c = u['chances'] - 1

        cur.execute(
            "UPDATE users SET chances = %s, strike_media = %s WHERE user_id = %s",
            (new_c, json.dumps(strike_media), user_id))
        cur.execute(
            "UPDATE orders SET img_hash = %s, ai_flag = 'REJECTED_FAKE', file_id = %s WHERE id = %s",
            (img_hash, photo.file_id, order['id']))
        conn.commit()

        await p_msg.edit_text(
            f"❌ <b>{to_stylish('Verification Failed')}</b>\n\n"
            f"Our system could not identify a valid payment receipt in this image. "
            f"Please send a clear screenshot of your successful transaction.\n\n"
            f"<i>Strike applied. Chances remaining: {new_c}/3</i>",
            parse_mode=ParseMode.HTML)
        if new_c <= 0: await trigger_ban_workflow(context, user_id)
        cur.close()
        conn.close()
        return

    # STEP 5: Successful AI Pass -> Forwarding to Admin Queue

    # Check for amount mismatch if AI managed to extract an amount
    extracted_amount = float(ai.get('amount', 0))
    if extracted_amount > 0 and extracted_amount < float(order['amount']):
        flag = f"Amount Mismatch Warning! Found: ₹{extracted_amount}, Expected: ₹{order['amount']}"

    cur.execute(
        "UPDATE orders SET status = 'pending', img_hash = %s, file_id = %s, ai_flag = %s WHERE id = %s",
        (img_hash, photo.file_id, flag, order['id']))
    conn.commit()

    admin_caption = (
        f"🔔 <b>{to_stylish('Pending Admin Approval')}</b>\n\n"
        f"👤 <b>User:</b> {update.effective_user.full_name} (@{update.effective_user.username})\n"
        f"🆔 <code>{user_id}</code>\n\n"
        f"📦 <b>Item:</b> {order['product']} ({format_validity(order['validity'])})\n"
        f"💰 <b>Expected Amt:</b> ₹{order['amount']}\n"
        f"🧾 <b>Order ID:</b> <code>{order['id']}</code>\n\n"
        f"⚙️ <b>System Verdict:</b>\n"
        f"↳ Confidence: {ai.get('confidence', 'N/A').upper()}\n"
        f"↳ Amount Found: ₹{ai.get('amount', 'N/A')}\n"
        f"↳ Logic Flag: {flag}")

    admin_kb = [[
        InlineKeyboardButton("✅ Approve & Deliver",
                             callback_data=f"adm_app_{order['id']}")
    ],
                [
                    InlineKeyboardButton(
                        "❌ Reject Payment",
                        callback_data=f"adm_rej_{order['id']}")
                ]]

    await context.bot.send_photo(ADMIN_ID,
                                 photo.file_id,
                                 caption=admin_caption,
                                 reply_markup=InlineKeyboardMarkup(admin_kb),
                                 parse_mode=ParseMode.HTML)
    await p_msg.edit_text(
        f"✅ <b>{to_stylish('Screenshot Verified!')}</b>\n\nIt has been forwarded to the admin desk for final confirmation. You will receive your key shortly.",
        parse_mode=ParseMode.HTML)

    cur.close()
    conn.close()


def create_image_collage(images):
    """Synchronous helper function to combine PIL images side-by-side horizontally."""
    if not images: return None
    min_height = min(img.height for img in images)
    resized = []
    for img in images:
        aspect = img.width / img.height
        new_w = int(min_height * aspect)
        resized.append(img.resize((new_w, min_height)))

    padding = 10 if len(resized) > 1 else 0
    total_width = sum(img.width
                      for img in resized) + padding * (len(resized) - 1)

    collage = Image.new('RGB', (total_width, min_height),
                        color=(255, 255, 255))
    x_offset = 0
    for img in resized:
        collage.paste(img, (x_offset, 0))
        x_offset += img.width + padding

    bio = io.BytesIO()
    collage.save(bio, format='JPEG')
    bio.seek(0)
    return bio


async def trigger_ban_workflow(context, user_id):
    """Gathers evidence, creates a collage, and triggers a ban confirmation for admin."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT strike_media FROM users WHERE user_id = %s",
                (user_id, ))
    u = cur.fetchone()
    cur.close()
    conn.close()

    file_ids = u.get('strike_media') if u and u.get('strike_media') else []

    kb = [[
        InlineKeyboardButton("🚫 Ban Permanently",
                             callback_data=f"adm_ban_u_{user_id}")
    ],
          [
              InlineKeyboardButton("✅ Forgive (Reset to 3)",
                                   callback_data=f"adm_forgive_u_{user_id}")
          ]]
    caption = f"⚠️ <b>{to_stylish('Ban Authorization Required')}</b>\nUser <code>{user_id}</code> has exhausted all 3 verification strikes.\n\nAll flagged screenshots are combined above."

    if not file_ids:
        # Fallback if no images found for some reason
        await context.bot.send_message(ADMIN_ID,
                                       caption,
                                       reply_markup=InlineKeyboardMarkup(kb),
                                       parse_mode=ParseMode.HTML)
        return

    # Download images
    images = []
    for fid in file_ids[-3:]:  # Grab up to 3 most recent strikes
        try:
            f = await context.bot.get_file(fid)
            arr = await f.download_as_bytearray()
            images.append(Image.open(io.BytesIO(arr)))
        except Exception as e:
            logger.error(f"Failed to download image for ban collage: {e}")

    if images:
        collage_io = await asyncio.to_thread(create_image_collage, images)
        if collage_io:
            await context.bot.send_photo(ADMIN_ID,
                                         photo=collage_io,
                                         caption=caption,
                                         reply_markup=InlineKeyboardMarkup(kb),
                                         parse_mode=ParseMode.HTML)
        else:
            await context.bot.send_message(
                ADMIN_ID,
                caption,
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode=ParseMode.HTML)


# ==========================================
# 👑 ADMIN PANEL SUITE
# ==========================================


async def admin_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Entry point for the admin dashboard."""
    if update.effective_user.id != ADMIN_ID: return

    settings = get_setting('maintenance', {"enabled": False})
    m_status = "🔴 ON" if settings.get('enabled') else "🟢 OFF"

    kb = get_admin_menu_kb(m_status)
    text = f"👑 <b>{to_stylish('Admin Command Center')}</b>\n\nAll systems operational. Select a module:"

    if update.message:
        await update.message.reply_text(text,
                                        reply_markup=kb,
                                        parse_mode=ParseMode.HTML)
    else:
        await update.callback_query.edit_message_text(
            text, reply_markup=kb, parse_mode=ParseMode.HTML)


async def admin_router_callback(update: Update,
                                context: ContextTypes.DEFAULT_TYPE):
    """Routes all inline button presses from the Admin Panel."""
    query = update.callback_query
    await query.answer()
    data = query.data

    conn = get_db_connection()
    cur = conn.cursor()

    try:
        # --- CORE NAVIGATION ---
        if data == "adm_home":
            await admin_main(update, context)

        elif data == "adm_close":
            await query.edit_message_text(
                f"👑 <b>{to_stylish('Console Closed.')}</b> Type /admin to reopen.",
                parse_mode=ParseMode.HTML)

        # --- STATISTICS ---
        elif data == "adm_stats":
            cur.execute("SELECT count(*) as uc FROM users")
            uc = cur.fetchone()['uc']
            cur.execute(
                "SELECT count(*) as oc, sum(amount) as rev FROM orders WHERE status = 'completed'"
            )
            row = cur.fetchone()
            cur.execute("SELECT count(*) as pc FROM products")
            pc = cur.fetchone()['pc']
            cur.execute(
                "SELECT count(*) as pending_c FROM orders WHERE status = 'pending'"
            )
            pend = cur.fetchone()['pending_c']

            text = (f"📊 <b>{to_stylish('Live System Statistics')}</b>\n\n"
                    f"👥 <b>Total Users:</b> {uc}\n"
                    f"📦 <b>Active Products:</b> {pc}\n\n"
                    f"✅ <b>Completed Orders:</b> {row['oc']}\n"
                    f"⏳ <b>Orders Pending Review:</b> {pend}\n"
                    f"💰 <b>Total Revenue:</b> ₹{row['rev'] or 0.0}\n\n"
                    f"<i>Stats are updated in real-time.</i>")
            await query.edit_message_text(text,
                                          reply_markup=InlineKeyboardMarkup([[
                                              InlineKeyboardButton(
                                                  "🔙 Back to Dashboard",
                                                  callback_data="adm_home")
                                          ]]),
                                          parse_mode=ParseMode.HTML)

        # --- SETTINGS MANAGEMENT ---
        elif data == "adm_settings":
            maint = get_setting('maintenance', {"enabled": False})['enabled']
            ai_ver = get_setting('ai_verification',
                                 {"enabled": True})['enabled']
            ocr_ver = get_setting('ocr_verification',
                                  {"enabled": True})['enabled']

            kb = [[
                InlineKeyboardButton(
                    f"AI Check: {'🟢 ON' if ai_ver else '🔴 OFF'}",
                    callback_data="adm_set_ai")
            ],
                  [
                      InlineKeyboardButton(
                          f"OCR Check: {'🟢 ON' if ocr_ver else '🔴 OFF'}",
                          callback_data="adm_set_ocr")
                  ],
                  [InlineKeyboardButton("🔙 Back", callback_data="adm_home")]]
            await query.edit_message_text(
                f"⚙️ <b>{to_stylish('Verification Settings')}</b>\nToggle modules below:",
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode=ParseMode.HTML)

        elif data.startswith("adm_set_"):
            setting_map = {"ai": "ai_verification", "ocr": "ocr_verification"}
            target = setting_map.get(data.split("_")[2])
            if target:
                curr = get_setting(target, {"enabled": True})
                curr['enabled'] = not curr['enabled']
                cur.execute("UPDATE settings SET value = %s WHERE key = %s",
                            (json.dumps(curr), target))
                conn.commit()
                # Reroute back to settings view
                query.data = "adm_settings"
                await admin_router_callback(update, context)
                return

        elif data == "adm_toggle_maint":
            val = get_setting('maintenance', {"enabled": False})
            val['enabled'] = not val['enabled']
            cur.execute(
                "UPDATE settings SET value = %s WHERE key = 'maintenance'",
                (json.dumps(val), ))
            conn.commit()
            await admin_main(update, context)
            return

        # --- QUEUE MANAGEMENT (PENDING ORDERS) ---
        elif data == "adm_pending_orders":
            cur.execute(
                "SELECT * FROM orders WHERE status = 'pending' ORDER BY timestamp ASC LIMIT 10"
            )
            pend_ords = cur.fetchall()
            if not pend_ords:
                await query.edit_message_text(
                    "✅ <b>No pending orders in the queue!</b>",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("🔙 Back",
                                             callback_data="adm_home")
                    ]]),
                    parse_mode=ParseMode.HTML)
            else:
                kb = []
                for o in pend_ords:
                    kb.append([
                        InlineKeyboardButton(
                            f"Review {o['id']} (₹{o['amount']})",
                            callback_data=f"adm_view_ord_{o['id']}")
                    ])
                kb.append(
                    [InlineKeyboardButton("🔙 Back", callback_data="adm_home")])
                await query.edit_message_text(
                    f"⏳ <b>{to_stylish('Pending Approvals')}</b>\nShowing oldest first:",
                    reply_markup=InlineKeyboardMarkup(kb),
                    parse_mode=ParseMode.HTML)

        elif data.startswith("adm_view_ord_"):
            oid = data.split("_")[3]
            cur.execute("SELECT * FROM orders WHERE id = %s", (oid, ))
            o = cur.fetchone()
            if o and o['file_id']:
                kb = [[
                    InlineKeyboardButton("✅ Approve",
                                         callback_data=f"adm_app_{oid}"),
                    InlineKeyboardButton("❌ Reject",
                                         callback_data=f"adm_rej_{oid}")
                ],
                      [
                          InlineKeyboardButton(
                              "🔙 Back to Queue",
                              callback_data="adm_pending_orders")
                      ]]
                await context.bot.send_photo(
                    ADMIN_ID,
                    o['file_id'],
                    caption=
                    f"Reviewing: <code>{oid}</code>\nAmount: ₹{o['amount']}",
                    reply_markup=InlineKeyboardMarkup(kb),
                    parse_mode=ParseMode.HTML)
                await query.message.delete()  # Clean up the list message

        # --- PRODUCT MANAGEMENT ---
        elif data == "adm_m_p":
            cur.execute(
                "SELECT id, name FROM products ORDER BY created_at DESC")
            prods = cur.fetchall()
            kb = [[
                InlineKeyboardButton(f"📦 {p['name']}",
                                     callback_data=f"adm_edit_p_{p['id']}")
            ] for p in prods]
            kb.append([
                InlineKeyboardButton("➕ Add New Product",
                                     callback_data="adm_add_p_start")
            ])
            kb.append(
                [InlineKeyboardButton("🔙 Back", callback_data="adm_home")])
            await query.edit_message_text(
                f"🛠️ <b>{to_stylish('Product Database')}</b>",
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode=ParseMode.HTML)

        # --- CONVERSATION STARTERS ---
        elif data == "adm_add_p_start":
            await query.edit_message_text(
                "➕ <b>Add Product Process</b>\nEnter the product name:",
                parse_mode=ParseMode.HTML)
            cur.close()
            conn.close()
            return ADD_P_NAME

        elif data == "adm_b_start":
            await query.edit_message_text(
                "📢 <b>Broadcast Mode</b>\nSend the message (Text, Photo, or Forward) you want to blast to all users. Send /cancel to abort.",
                parse_mode=ParseMode.HTML)
            cur.close()
            conn.close()
            return B_MSG

        elif data == "adm_m_u":
            await query.edit_message_text(
                "👥 <b>User Lookup</b>\nPlease reply with the **Telegram User ID** you want to investigate:",
                parse_mode=ParseMode.HTML)
            cur.close()
            conn.close()
            return U_SEARCH

        # --- EDITING EXISTING PRODUCTS ---
        elif data.startswith("adm_edit_p_"):
            pid = data.split("_")[3]
            cur.execute("SELECT * FROM products WHERE id = %s", (pid, ))
            p = cur.fetchone()
            if p:
                kb = [[
                    InlineKeyboardButton("✏️ Edit Name",
                                         callback_data=f"adm_p_e_name_{pid}"),
                    InlineKeyboardButton("✏️ Edit Desc",
                                         callback_data=f"adm_p_e_desc_{pid}")
                ],
                      [
                          InlineKeyboardButton(
                              "✏️ Edit Plans",
                              callback_data=f"adm_p_e_opts_{pid}"),
                          InlineKeyboardButton(
                              "✏️ Edit APK",
                              callback_data=f"adm_p_e_apk_{pid}")
                      ],
                      [
                          InlineKeyboardButton(
                              "🗑️ Delete Product",
                              callback_data=f"adm_p_del_{pid}")
                      ],
                      [
                          InlineKeyboardButton("🔙 Back to List",
                                               callback_data="adm_m_p")
                      ]]
                await query.edit_message_text(
                    f"Manage: <b>{p['name']}</b>\nOptions: {len(p['options'])}",
                    reply_markup=InlineKeyboardMarkup(kb),
                    parse_mode=ParseMode.HTML)

        elif data.startswith("adm_p_e_name_"):
            context.user_data['edit_pid'] = data.split("_")[4]
            await query.edit_message_text("✏️ Enter the new Name:")
            cur.close()
            conn.close()
            return EDIT_P_NAME
        elif data.startswith("adm_p_e_desc_"):
            context.user_data['edit_pid'] = data.split("_")[4]
            await query.edit_message_text("✏️ Enter the new Description:")
            cur.close()
            conn.close()
            return EDIT_P_DESC
        elif data.startswith("adm_p_e_opts_"):
            context.user_data['edit_pid'] = data.split("_")[4]
            await query.edit_message_text(
                "✏️ Enter new Plans (e.g., 1d 100, 1m 500):")
            cur.close()
            conn.close()
            return EDIT_P_OPTS
        elif data.startswith("adm_p_e_apk_"):
            context.user_data['edit_pid'] = data.split("_")[4]
            await query.edit_message_text("✏️ Enter new APK link (or 'none'):")
            cur.close()
            conn.close()
            return EDIT_P_APK

        elif data.startswith("adm_p_del_"):
            pid = data.split("_")[3]
            kb = [[
                InlineKeyboardButton("🔥 Confirm Delete",
                                     callback_data=f"adm_p_del_yes_{pid}"),
                InlineKeyboardButton("❌ Cancel",
                                     callback_data=f"adm_edit_p_{pid}")
            ]]
            await query.edit_message_text(
                "⚠️ <b>Are you absolutely sure you want to delete this product?</b>",
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode=ParseMode.HTML)

        elif data.startswith("adm_p_del_yes_"):
            pid = data.split("_")[4]
            cur.execute("DELETE FROM products WHERE id = %s", (pid, ))
            conn.commit()
            await query.answer("Product Eradicated", show_alert=True)
            query.data = "adm_m_p"
            await admin_router_callback(update, context)  # Auto return to list
            return

        # --- USER BANNING & FORGIVING ---
        elif data.startswith("adm_ban_u_"):
            uid = int(data.split("_")[3])
            cur.execute("UPDATE users SET is_banned = TRUE WHERE user_id = %s",
                        (uid, ))
            conn.commit()

            # Delete message to clean up the big photo collage from chat history if possible
            try:
                await query.message.delete()
            except:
                pass
            await context.bot.send_message(
                ADMIN_ID,
                f"🚫 <b>User <code>{uid}</code> has been PERMANENTLY BANNED.</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("Back",
                                           callback_data="adm_home")]]))

        elif data.startswith("adm_forgive_u_"):
            uid = int(data.split("_")[3])
            cur.execute(
                "UPDATE users SET chances = 3, is_banned = FALSE, strike_media = '[]'::jsonb WHERE user_id = %s",
                (uid, ))
            conn.commit()

            try:
                await query.message.delete()
            except:
                pass
            await context.bot.send_message(
                ADMIN_ID,
                f"✅ <b>User <code>{uid}</code> forgiven. Verification strikes have been cleared to 3.</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("Back",
                                           callback_data="adm_home")]]))

        # --- ORDER APPROVAL FLOW ---
        elif data.startswith("adm_app_"):
            oid = data.split("_")[2]
            cur.execute("SELECT status FROM orders WHERE id = %s", (oid, ))
            check = cur.fetchone()
            if check and check['status'] == 'completed':
                await query.answer("Order already completed!", show_alert=True)
                return
            context.user_data['approve_oid'] = oid
            context.user_data['approve_msg_id'] = query.message.message_id
            await context.bot.send_message(
                ADMIN_ID,
                f"✅ Approving <code>{oid}</code>.\nPlease reply with the **Product Key**:",
                parse_mode=ParseMode.HTML)
            cur.close()
            conn.close()
            return WAIT_FOR_KEY

        elif data.startswith("adm_rej_"):
            oid = data.split("_")[2]
            cur.execute(
                "UPDATE orders SET status = 'rejected' WHERE id = %s RETURNING user_id",
                (oid, ))
            res = cur.fetchone()
            conn.commit()
            if res:
                try:
                    await context.bot.send_message(
                        res['user_id'],
                        f"❌ <b>{to_stylish('Order Rejected')}</b>\nYour payment for order <code>{oid}</code> was rejected. Contact support.",
                        parse_mode=ParseMode.HTML)
                except:
                    pass
            await query.answer("Rejected.")
            await query.edit_message_caption(
                query.message.caption +
                f"\n\n❌ <b>{to_stylish('REJECTED')}</b>",
                parse_mode=ParseMode.HTML)

    except Exception as e:
        logger.error(f"Admin Router Error: {e}")
        await query.answer("Router Error", show_alert=True)
    finally:
        cur.close()
        conn.close()


# ==========================================
# 🔄 STATE HANDLERS (ADMIN SUB-ROUTINES)
# ==========================================


# -- CREATE PRODUCT --
async def admin_add_p_name(u: Update, c: ContextTypes.DEFAULT_TYPE):
    c.user_data['n_name'] = u.message.text
    await u.message.reply_text("📝 Enter Description:")
    return ADD_P_DESC


async def admin_add_p_desc(u: Update, c: ContextTypes.DEFAULT_TYPE):
    c.user_data['n_desc'] = u.message.text
    await u.message.reply_text("💰 Enter Plans (e.g., 1d 100, 1m 500, fs 1000):"
                               )
    return ADD_P_OPTS


async def admin_add_p_opts(u: Update, c: ContextTypes.DEFAULT_TYPE):
    try:
        raw = u.message.text.replace('\n', ',')
        opts = {
            i.split()[:-1][0]: float(i.split()[-1])
            for i in [x.strip() for x in raw.split(",") if x.strip()]
        }
        c.user_data['n_opts'] = opts
        await u.message.reply_text("🔗 Enter APK link (or type 'none'):")
        return ADD_P_APK
    except Exception as e:
        await u.message.reply_text("❌ Format error. Try again (e.g. 1d 100):")
        return ADD_P_OPTS


async def admin_add_p_apk(u: Update, c: ContextTypes.DEFAULT_TYPE):
    apk = None if u.message.text.lower() == 'none' else u.message.text
    conn = get_db_connection()
    cur = conn.cursor()
    name = c.user_data['n_name']
    pid = get_product_id(name)
    cur.execute(
        "INSERT INTO products (id, name, description, options, apk_link) VALUES (%s, %s, %s, %s, %s)",
        (pid, name, c.user_data['n_desc'], json.dumps(
            c.user_data['n_opts']), apk))
    conn.commit()
    cur.close()
    conn.close()
    await u.message.reply_text(f"✅ Product <b>{name}</b> added successfully!",
                               parse_mode=ParseMode.HTML)
    c.user_data.clear()
    return ConversationHandler.END


# -- EDIT PRODUCT --
async def admin_edit_p_name(u: Update, c: ContextTypes.DEFAULT_TYPE):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE products SET name = %s WHERE id = %s",
                (u.message.text, c.user_data['edit_pid']))
    conn.commit()
    cur.close()
    conn.close()
    await u.message.reply_text("✅ Name Updated!")
    c.user_data.clear()
    return ConversationHandler.END


async def admin_edit_p_desc(u: Update, c: ContextTypes.DEFAULT_TYPE):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE products SET description = %s WHERE id = %s",
                (u.message.text, c.user_data['edit_pid']))
    conn.commit()
    cur.close()
    conn.close()
    await u.message.reply_text("✅ Description Updated!")
    c.user_data.clear()
    return ConversationHandler.END


async def admin_edit_p_opts(u: Update, c: ContextTypes.DEFAULT_TYPE):
    try:
        raw = u.message.text.replace('\n', ',')
        opts = {
            i.split()[:-1][0]: float(i.split()[-1])
            for i in [x.strip() for x in raw.split(",") if x.strip()]
        }
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("UPDATE products SET options = %s WHERE id = %s",
                    (json.dumps(opts), c.user_data['edit_pid']))
        conn.commit()
        cur.close()
        conn.close()
        await u.message.reply_text("✅ Plans Updated!")
        c.user_data.clear()
        return ConversationHandler.END
    except:
        await u.message.reply_text("❌ Error parsing. Try again:")
        return EDIT_P_OPTS


async def admin_edit_p_apk(u: Update, c: ContextTypes.DEFAULT_TYPE):
    apk = None if u.message.text.lower() == 'none' else u.message.text
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE products SET apk_link = %s WHERE id = %s",
                (apk, c.user_data['edit_pid']))
    conn.commit()
    cur.close()
    conn.close()
    await u.message.reply_text("✅ APK Updated!")
    c.user_data.clear()
    return ConversationHandler.END


# -- BROADCAST ROUTINE --
async def admin_b_msg_receive(u: Update, c: ContextTypes.DEFAULT_TYPE):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users")
    users = cur.fetchall()
    cur.close()
    conn.close()

    m = await u.message.reply_text("🚀 Initializing Broadcast Engine...")
    s, f = 0, 0
    total = len(users)

    for i, usr in enumerate(users):
        try:
            await c.bot.copy_message(usr['user_id'], u.effective_chat.id,
                                     u.message.message_id)
            s += 1
        except:
            f += 1

        # UI Progress Bar Update
        if i % 15 == 0 and total > 0:
            prog = int((i / total) * 15)
            bar = "█" * prog + "░" * (15 - prog)
            try:
                await m.edit_text(
                    f"📢 Broadcasting: [{bar}] {int((i/total)*100)}%\n✅ Sent: {s} | ❌ Failed: {f}"
                )
            except:
                pass
            await asyncio.sleep(0.1)  # Prevent Flood limits

    await m.edit_text(
        f"✅ <b>{to_stylish('Broadcast Terminated')}</b>\n\nTotal Sent: {s}\nTotal Failed/Blocked: {f}",
        parse_mode=ParseMode.HTML)
    return ConversationHandler.END


# -- USER SEARCH --
async def admin_user_search_receive(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message.text.isdigit():
        await u.message.reply_text("❌ Invalid ID format. Must be numeric.")
        return ConversationHandler.END

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE user_id = %s",
                (int(u.message.text), ))
    usr = cur.fetchone()
    cur.close()
    conn.close()

    if not usr:
        await u.message.reply_text("❌ User not found in database.")
        return ConversationHandler.END

    text = (f"👤 <b>Target Profile</b>\n\n"
            f"<b>Name:</b> {usr['name']}\n"
            f"<b>ID:</b> <code>{usr['user_id']}</code>\n"
            f"<b>Join Date:</b> {usr['joined'].strftime('%Y-%m-%d')}\n"
            f"<b>Spent:</b> ₹{usr['total_spent']}\n"
            f"<b>Strikes Remaining:</b> {usr['chances']}/3\n"
            f"<b>Status:</b> {'🚫 BANNED' if usr['is_banned'] else '🟢 ACTIVE'}")
    kb = [[
        InlineKeyboardButton("🚫 Apply Ban",
                             callback_data=f"adm_ban_u_{usr['user_id']}"),
        InlineKeyboardButton("✅ Reset Profile",
                             callback_data=f"adm_forgive_u_{usr['user_id']}")
    ]]
    await u.message.reply_text(text,
                               reply_markup=InlineKeyboardMarkup(kb),
                               parse_mode=ParseMode.HTML)
    return ConversationHandler.END


# -- ORDER APPROVAL & KEY DELIVERY --
async def receive_approval_key(u: Update, c: ContextTypes.DEFAULT_TYPE):
    oid = c.user_data.get('approve_oid')
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("SELECT * FROM orders WHERE id = %s", (oid, ))
    o = cur.fetchone()

    if o:
        cur.execute("SELECT apk_link FROM products WHERE name = %s",
                    (o['product'], ))
        p_data = cur.fetchone()

        # 1. Update order status
        # 2. Reset chances to 3 and clear strike media log
        # 3. Add to user's total spend tracking
        cur.execute("UPDATE orders SET status = 'completed' WHERE id = %s",
                    (oid, ))
        cur.execute(
            "UPDATE users SET chances = 3, total_spent = total_spent + %s, strike_media = '[]'::jsonb WHERE user_id = %s",
            (o['amount'], o['user_id']))
        conn.commit()

        # Deliver to user
        msg = (
            f"🎉 <b>{to_stylish('Payment Approved')}</b>\n\n"
            f"🆔 Order ID: <code>{oid}</code>\n"
            f"📦 Product: {o['product']} ({format_validity(o['validity'])})\n\n"
            f"🔑 <b>YOUR KEY:</b>\n<code>{u.message.text}</code>\n")
        if p_data and p_data['apk_link']:
            msg += f"\n📥 <b>Download Application:</b> {p_data['apk_link']}\n"

        try:
            await c.bot.send_message(o['user_id'],
                                     msg,
                                     parse_mode=ParseMode.HTML)
            await u.message.reply_text(
                "✅ Key successfully transmitted to user. Profile chances reset to 3."
            )

            # Clean up the buttons on the admin's original review message
            if 'approve_msg_id' in c.user_data:
                await c.bot.edit_message_reply_markup(
                    ADMIN_ID, c.user_data['approve_msg_id'], reply_markup=None)
        except Exception as e:
            await u.message.reply_text(
                f"⚠️ User may have blocked the bot. Order is marked as complete, but message delivery failed: {e}"
            )

    cur.close()
    conn.close()
    c.user_data.clear()
    return ConversationHandler.END


async def cancel_flow(u: Update, c: ContextTypes.DEFAULT_TYPE):
    """Universal cancel for all admin conversational states."""
    await u.message.reply_text("❌ Operation Aborted.")
    c.user_data.clear()
    return ConversationHandler.END


# ==========================================
# 🚀 SYSTEM INITIALIZATION & MAIN LOOP
# ==========================================

# ==========================================
# 🌐 WEB SERVER (KEEP-ALIVE)
# ==========================================

web_app = Flask(__name__)


@web_app.route('/')
def home():
    """Serves the status page."""
    try:
        return send_file('index.html')
    except Exception as e:
        return f"Prime Bot is running! (Error loading index.html: {e})"


def run_server():
    """Starts the Flask app on the assigned port."""
    # Port 8080 is standard for web services; 'PORT' is used by most cloud hosts
    port = int(os.environ.get("PORT", 8080))
    # Disable logging for Flask to keep the terminal clean for bot logs
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)
    web_app.run(host='0.0.0.0', port=port)


def keep_alive():
    """Starts the server in a background thread."""
    server_thread = Thread(target=run_server)
    server_thread.daemon = True
    server_thread.start()


def main():
    if not BOT_TOKEN:
        logger.critical("BOT_TOKEN missing. Terminating.")
        return
    if not DATABASE_URL:
        logger.critical("DATABASE_URL missing. Terminating.")
        return

    # Setup Persistence
    persistence = PicklePersistence(filepath="cyvex_bot.pickle")

    # Initialize Application
    app = Application.builder().token(BOT_TOKEN).persistence(persistence).build()

    # Attach Global Error Handler
    app.add_error_handler(global_error_handler)

    # Register Background Job
    job_queue = app.job_queue
    if job_queue:
        job_queue.run_repeating(cleanup_stale_orders, interval=3600, first=10)

    # ---------------------------------------------------------
    # 1. ADMIN CONVERSATION HANDLER (PRIORITY #1)
    # ---------------------------------------------------------
    admin_handler = ConversationHandler(
        entry_points=[
            CommandHandler("admin", admin_main),
            CallbackQueryHandler(admin_router_callback, pattern="^adm_")
        ],
        states={
            ADD_P_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_p_name)],
            ADD_P_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_p_desc)],
            ADD_P_OPTS: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_p_opts)],
            ADD_P_APK:  [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_p_apk)],
            EDIT_P_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_edit_p_name)],
            EDIT_P_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_edit_p_desc)],
            EDIT_P_OPTS: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_edit_p_opts)],
            EDIT_P_APK:  [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_edit_p_apk)],
            B_MSG:      [MessageHandler(filters.ALL & ~filters.COMMAND, admin_b_msg_receive)],
            U_SEARCH:   [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_user_search_receive)],
            WAIT_FOR_KEY: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_approval_key)],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_flow),
            CallbackQueryHandler(admin_router_callback, pattern="^adm_")
        ],
        name="admin_master_conversation",
        persistent=True,
        per_chat=True
    )
    
    # ADD ADMIN HANDLER FIRST so it intercepts /admin command
    app.add_handler(admin_handler)

    # ---------------------------------------------------------
    # 2. STANDARD USER HANDLERS (PRIORITY #2)
    # ---------------------------------------------------------
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(user_button_handler, pattern="^(?!adm_)"))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    # ---------------------------------------------------------
    # 3. START SERVICES
    # ---------------------------------------------------------
    logger.info("Starting Web Keep-Alive...")
    keep_alive()

    logger.info("Bot infrastructure loaded. Commencing polling...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
