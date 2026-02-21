# converter.py (Webhook Mode - Koyeb Free Tier Optimized)

import os
import requests
from urllib.parse import urlparse
from pymongo import MongoClient
from dotenv import load_dotenv
from datetime import datetime
from flask import Flask, request, jsonify
import re

load_dotenv()

BOT_TOKEN = os.getenv("CONVERTER_BOT_TOKEN")
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

MONGODB_URI = os.getenv("MONGODB_URI")
DB_NAME = os.getenv("MONGO_DB_NAME", "viralbox_db")
VIRALBOX_DOMAIN = os.getenv("VIRALBOX_DOMAIN", "viralbox.in")
PORT = int(os.getenv("PORT", "8000"))
LOG_CHANNEL_ID = os.getenv("LOG_CHANNEL_ID")

if not BOT_TOKEN or not MONGODB_URI:
    raise RuntimeError("BOT_TOKEN and MONGODB_URI must be in .env")

app = Flask(__name__)

# ---------------- LAZY MONGODB ----------------
# Startup pe connect nahi hoga - pehli request pe connect hoga
# Isse cold start fast hoga (2-3 sec)
_client = None
_db = None

def get_db():
    global _client, _db
    if _client is None:
        _client = MongoClient(MONGODB_URI, maxPoolSize=50, serverSelectionTimeoutMS=5000)
        _db = _client[DB_NAME]
        print(f"✅ MongoDB connected: {DB_NAME}")
    return _db

def get_col(name):
    return get_db()[name]


# ------------------ HELPERS ------------------ #

def extract_urls(text):
    if not text:
        return []
    urls = re.findall(r"(https?://[^\s]+)", text)
    return urls if urls else []


def replace_urls_in_text(text, url_mapping, all_urls):
    if not text:
        return text
    result = text
    for old_url, new_url in url_mapping.items():
        result = result.replace(old_url, new_url)
    for url in all_urls:
        if url not in url_mapping:
            result = result.replace(url, "")
    result = re.sub(r'\n\s*\n', '\n\n', result)
    result = result.strip()
    return result


def is_viralbox(url):
    try:
        u = urlparse(url)
        return VIRALBOX_DOMAIN in u.hostname
    except:
        return False


def send_message(chat_id, text):
    try:
        requests.post(f"{TELEGRAM_API}/sendMessage", json={"chat_id": chat_id, "text": text}, timeout=10)
    except Exception as e:
        print(f"Error sending message: {e}")


def send_media(chat_id, mtype, file_id, caption=None):
    endpoint = {"photo": "sendPhoto", "video": "sendVideo", "document": "sendDocument", 
                "audio": "sendAudio", "voice": "sendVoice", "animation": "sendAnimation"}.get(mtype)
    if not endpoint:
        send_message(chat_id, caption or "")
        return
    try:
        payload = {"chat_id": chat_id}
        if caption:
            payload["caption"] = caption
        payload[mtype] = file_id
        requests.post(f"{TELEGRAM_API}/{endpoint}", json=payload, timeout=10)
    except Exception as e:
        print(f"Error sending media: {e}")


def log_to_channel(message):
    if not LOG_CHANNEL_ID:
        return
    try:
        requests.post(f"{TELEGRAM_API}/sendMessage", json={"chat_id": LOG_CHANNEL_ID, "text": message}, timeout=10)
    except Exception as e:
        print(f"Error sending log: {e}")


def format_user_info(user):
    name = user.get("first_name", "Unknown")
    username = user.get("username", "No username")
    user_id = user.get("id", "Unknown")
    return name, username, user_id


# ------------------ DATABASE ------------------ #

def save_api_key(user_id, apikey):
    try:
        get_col("user_apis").update_one({"userId": user_id}, {"$set": {"userId": user_id, "apiKey": apikey}}, upsert=True)
    except Exception as e:
        print(f"DB Error: {e}")


def get_api_key(user_id):
    try:
        doc = get_col("user_apis").find_one({"userId": user_id})
        return doc["apiKey"] if doc else None
    except:
        return None


def save_user_setting(user_id, key, value):
    try:
        get_col("user_settings").update_one({"userId": user_id}, {"$set": {key: value}}, upsert=True)
    except:
        pass


def delete_user_setting(user_id, key):
    try:
        get_col("user_settings").update_one({"userId": user_id}, {"$unset": {key: ""}})
    except:
        pass


def get_user_settings(user_id):
    try:
        doc = get_col("user_settings").find_one({"userId": user_id})
        if not doc:
            return {"header": None, "footer": None, "keep_text": False}
        return {"header": doc.get("header"), "footer": doc.get("footer"), "keep_text": doc.get("keep_text", False)}
    except:
        return {"header": None, "footer": None, "keep_text": False}


def save_converted(longURL, shortURL):
    try:
        get_col("links").insert_one({"longURL": longURL, "shortURL": shortURL, "createdAt": datetime.utcnow()})
    except:
        pass


def find_long_url(shortURL):
    try:
        doc = get_col("links").find_one({"shortURL": shortURL})
        return doc["longURL"] if doc else None
    except:
        return None


def short_with_user_token(apiKey, longURL):
    try:
        api = f"https://viralbox.in/api?api={apiKey}&url={requests.utils.requote_uri(longURL)}"
        r = requests.get(api, timeout=15)
        j = r.json()
        if j.get("status") == "success":
            return j.get("shortenedUrl") or j.get("short_url") or j.get("short")
        return None
    except:
        return None


# ------------------ PROCESS MESSAGE ------------------ #

def process_message(msg):
    try:
        # Channel posts / anonymous messages ignore karo
        if not msg.get("from"):
            return

        chat_id = msg["chat"]["id"]
        user_id = msg["from"]["id"]
        text = msg.get("text", "")

        if text.startswith("/start"):
            name = msg["from"].get("first_name", "User")
            username = msg["from"].get("username", "No username")
            log_to_channel(f"🟢 NEW USER\n👤 {name}\n🆔 @{username}\n🔢 {user_id}")
            user_api = get_api_key(user_id)
            if user_api:
                send_message(chat_id, "🔗 Send A Link To Convert !")
            else:
                send_message(chat_id, f"👋 Welcome {name}!\n\n1️⃣ Create account on viralbox.in\n2️⃣ Get API: https://viralbox.in/member/tools/api\n3️⃣ /set_api <KEY>\n4️⃣ Send viralbox.in link\n\n/help for commands")
            return

        if text.startswith("/help"):
            send_message(chat_id, "📚 Commands:\n\n🔑 /set_api <KEY>\n📝 /set_header <text>\n📝 /set_footer <text>\n✅ /keep_text\n❌ /delete_text\n🗑️ /delete_header\n🗑️ /delete_footer")
            return

        if text.startswith("/set_api"):
            parts = text.split(maxsplit=1)
            if len(parts) < 2:
                send_message(chat_id, "❌ Usage: /set_api <API_KEY>")
                return
            save_api_key(user_id, parts[1].strip())
            name, username, uid = format_user_info(msg["from"])
            log_to_channel(f"🔑 API SET\n👤 {name}\n🆔 @{username}\n🔢 {uid}")
            send_message(chat_id, "✅ API Key Saved!")
            return

        if text.startswith("/set_header"):
            parts = text.split(maxsplit=1)
            if len(parts) < 2:
                send_message(chat_id, "❌ Usage: /set_header <text>")
                return
            save_user_setting(user_id, "header", parts[1].strip())
            send_message(chat_id, f"✅ Header Saved!\n\n{parts[1].strip()}")
            return

        if text.startswith("/delete_header"):
            delete_user_setting(user_id, "header")
            send_message(chat_id, "✅ Header Deleted!")
            return

        if text.startswith("/set_footer"):
            parts = text.split(maxsplit=1)
            if len(parts) < 2:
                send_message(chat_id, "❌ Usage: /set_footer <text>")
                return
            save_user_setting(user_id, "footer", parts[1].strip())
            send_message(chat_id, f"✅ Footer Saved!\n\n{parts[1].strip()}")
            return

        if text.startswith("/delete_footer"):
            delete_user_setting(user_id, "footer")
            send_message(chat_id, "✅ Footer Deleted!")
            return

        if text.startswith("/keep_text"):
            save_user_setting(user_id, "keep_text", True)
            send_message(chat_id, "✅ Keep Text Mode Enabled!")
            return

        if text.startswith("/delete_text"):
            save_user_setting(user_id, "keep_text", False)
            send_message(chat_id, "✅ Delete Text Mode Enabled!")
            return

        user_api = get_api_key(user_id)
        if not user_api:
            send_message(chat_id, "❌ Set API key first: /set_api <KEY>")
            return

        settings = get_user_settings(user_id)
        media_type = None
        file_id = None
        original_text = text

        for t in ["photo", "video", "document", "audio", "voice", "animation"]:
            if msg.get(t):
                media_type = t
                file_id = msg[t][-1]["file_id"] if t == "photo" else msg[t]["file_id"]
                original_text = msg.get("caption", "")
                break

        all_urls = extract_urls(original_text)
        viralbox_urls = [url for url in all_urls if is_viralbox(url)]

        if not viralbox_urls:
            send_message(chat_id, "❌ No viralbox.in links found!")
            return

        url_mapping = {}
        converted_links = []
        
        for url in viralbox_urls:
            longURL = find_long_url(url)
            if not longURL:
                send_message(chat_id, f"❌ Link not in database: {url}")
                return
            newShort = short_with_user_token(user_api, longURL)
            if not newShort:
                send_message(chat_id, f"❌ Failed to convert: {url}")
                return
            save_converted(longURL, newShort)
            url_mapping[url] = newShort
            converted_links.append(newShort)

        if settings["keep_text"]:
            response_text = replace_urls_in_text(original_text, url_mapping, all_urls)
        else:
            parts = []
            if settings["header"]:
                parts.append(settings["header"])
            parts.extend(converted_links)
            if settings["footer"]:
                parts.append(settings["footer"])
            response_text = "\n\n".join(parts)

        if not media_type:
            send_message(chat_id, response_text)
        else:
            send_media(chat_id, media_type, file_id, response_text)
    except Exception as e:
        print(f"Error: {e}")


# ------------------ ROUTES ------------------ #

@app.route('/', methods=['GET'])
@app.route('/health', methods=['GET'])
@app.route('/healthz', methods=['GET'])
def health():
    return jsonify({"status": "healthy", "bot": "converter", "timestamp": datetime.utcnow().isoformat()})


@app.route(f'/{BOT_TOKEN}', methods=['POST'])
def webhook():
    try:
        update = request.get_json()
        if update and "message" in update:
            process_message(update["message"])
        return jsonify({"ok": True})
    except Exception as e:
        print(f"Webhook Error: {e}")
        return jsonify({"ok": False}), 500


if __name__ == "__main__":
    print(f"🤖 Converter Bot running on port {PORT}")
    print(f"🌐 Viralbox Domain: {VIRALBOX_DOMAIN}")
    print(f"💾 Database: {DB_NAME}")
    # Gunicorn production me use karo, direct python sirf dev ke liye
    app.run(host='0.0.0.0', port=PORT, debug=False)
