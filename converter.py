# converter.py (Enhanced Version with Header/Footer & Text Options)

import os
import time
import requests
from urllib.parse import urlparse
from pymongo import MongoClient
from dotenv import load_dotenv
from datetime import datetime
from threading import Thread
from http.server import HTTPServer, BaseHTTPRequestHandler
from concurrent.futures import ThreadPoolExecutor
import re

load_dotenv()

BOT_TOKEN = os.getenv("CONVERTER_BOT_TOKEN")
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

MONGODB_URI = os.getenv("MONGODB_URI")
DB_NAME = os.getenv("MONGO_DB_NAME", "viralbox_db")
VIRALBOX_DOMAIN = os.getenv("VIRALBOX_DOMAIN", "viralbox.in")
HEALTH_CHECK_PORT = int(os.getenv("PORT", "8000"))
MAX_WORKERS = int(os.getenv("MAX_WORKERS", "10"))

if not BOT_TOKEN or not MONGODB_URI:
    raise RuntimeError("BOT_TOKEN and MONGODB_URI must be in .env")

# ------------------ DB SETUP ------------------ #
client = MongoClient(MONGODB_URI, maxPoolSize=50)
db = client[DB_NAME]

links_col = db["links"]
user_apis_col = db["user_apis"]
user_settings_col = db["user_settings"]  # New collection for user preferences


# ------------------ HEALTH CHECK SERVER ------------------ #
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/health' or self.path == '/':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            response = {
                "status": "healthy",
                "bot": "converter",
                "timestamp": datetime.utcnow().isoformat(),
                "workers": MAX_WORKERS
            }
            self.wfile.write(str(response).encode())
        else:
            self.send_response(404)
            self.end_headers()
    
    def log_message(self, format, *args):
        pass


def start_health_server():
    """Start health check server in background thread"""
    server = HTTPServer(('0.0.0.0', HEALTH_CHECK_PORT), HealthCheckHandler)
    print(f"✅ Health check server running on port {HEALTH_CHECK_PORT}")
    server.serve_forever()


# ------------------ HELPERS ------------------ #

def extract_urls(text):
    """Extract all URLs from text"""
    if not text:
        return []
    urls = re.findall(r"(https?://[^\s]+)", text)
    return urls if urls else []


def replace_urls_in_text(text, url_mapping):
    """Replace old URLs with new shortened URLs in text"""
    if not text:
        return text
    
    result = text
    for old_url, new_url in url_mapping.items():
        result = result.replace(old_url, new_url)
    
    return result


def is_viralbox(url):
    """Check if URL is from viralbox domain"""
    try:
        u = urlparse(url)
        return VIRALBOX_DOMAIN in u.hostname
    except:
        return False


def send_message(chat_id, text):
    """Send text message to chat"""
    try:
        requests.post(
            f"{TELEGRAM_API}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=10
        )
    except Exception as e:
        print(f"Error sending message: {e}")


def send_media(chat_id, mtype, file_id, caption=None):
    """Send media with caption"""
    endpoint = {
        "photo": "sendPhoto",
        "video": "sendVideo",
        "document": "sendDocument",
        "audio": "sendAudio",
        "voice": "sendVoice",
        "animation": "sendAnimation"
    }.get(mtype)

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


# ------------------ DATABASE FUNCTIONS ------------------ #

def save_api_key(user_id, apikey):
    """Save user's API key"""
    try:
        user_apis_col.update_one(
            {"userId": user_id},
            {"$set": {"userId": user_id, "apiKey": apikey}},
            upsert=True
        )
    except Exception as e:
        print(f"DB Error saving API key: {e}")


def get_api_key(user_id):
    """Get user's API key"""
    try:
        doc = user_apis_col.find_one({"userId": user_id})
        return doc["apiKey"] if doc else None
    except Exception as e:
        print(f"DB Error getting API key: {e}")
        return None


def save_user_setting(user_id, key, value):
    """Save user setting (header/footer/keep_text)"""
    try:
        user_settings_col.update_one(
            {"userId": user_id},
            {"$set": {key: value}},
            upsert=True
        )
    except Exception as e:
        print(f"DB Error saving setting: {e}")


def delete_user_setting(user_id, key):
    """Delete user setting"""
    try:
        user_settings_col.update_one(
            {"userId": user_id},
            {"$unset": {key: ""}}
        )
    except Exception as e:
        print(f"DB Error deleting setting: {e}")


def get_user_settings(user_id):
    """Get all user settings"""
    try:
        doc = user_settings_col.find_one({"userId": user_id})
        if not doc:
            return {"header": None, "footer": None, "keep_text": False}
        return {
            "header": doc.get("header"),
            "footer": doc.get("footer"),
            "keep_text": doc.get("keep_text", False)
        }
    except Exception as e:
        print(f"DB Error getting settings: {e}")
        return {"header": None, "footer": None, "keep_text": False}


def save_converted(longURL, shortURL):
    """Save converted link to database"""
    try:
        links_col.insert_one({
            "longURL": longURL,
            "shortURL": shortURL
        })
    except Exception as e:
        print(f"DB Error saving converted link: {e}")


def find_long_url(shortURL):
    """Find original long URL from short URL"""
    try:
        doc = links_col.find_one({"shortURL": shortURL})
        if doc:
            return doc["longURL"]
        return None
    except Exception as e:
        print(f"DB Error finding long URL: {e}")
        return None


# ------------------ SHORTENING ------------------ #

def short_with_user_token(apiKey, longURL):
    """Shorten URL using user's API key"""
    try:
        api = f"https://viralbox.in/api?api={apiKey}&url={requests.utils.requote_uri(longURL)}"
        r = requests.get(api, timeout=15)
        j = r.json()

        if j.get("status") == "success":
            return j.get("shortenedUrl") or j.get("short_url") or j.get("short")

        return None

    except Exception as e:
        print(f"Shortening error: {e}")
        return None


# ------------------ PROCESS MESSAGE ------------------ #

def process_message(msg):
    """Process a single message (runs in thread pool)"""
    try:
        chat_id = msg["chat"]["id"]
        user_id = msg["from"]["id"]
        text = msg.get("text", "")

        # -------- /start Command -------- #
        if text.startswith("/start"):
            name = msg["from"].get("first_name", "User")
            user_api = get_api_key(user_id)
            
            if user_api:
                send_message(chat_id, "🔗 Send A Link To Convert !")
            else:
                send_message(chat_id,
f"👋 Welcome {name} to viralbox.in Bot!\n\n"
f"I am Link Converter Bot.\n\n"
f"1️⃣ Create an Account on viralbox.in\n"
f"2️⃣ Go To 👉 https://viralbox.in/member/tools/api\n"
f"3️⃣ Copy your API Key\n"
f"4️⃣ Send /set_api <API_KEY>\n"
f"5️⃣ Send me any viralbox.in link\n\n"
f"📋 Available Commands:\n"
f"/set_api - Save your API Key\n"
f"/set_header - Add custom header\n"
f"/set_footer - Add custom footer\n"
f"/keep_text - Keep original text with converted links\n"
f"/delete_text - Remove original text, show only converted links\n"
f"/delete_header - Remove custom header\n"
f"/delete_footer - Remove custom footer\n"
f"/help - Get help and support")
            return

        # -------- /help Command -------- #
        if text.startswith("/help"):
            send_message(chat_id, 
f"📚 Bot Commands Help:\n\n"
f"🔑 /set_api <API_KEY> - Save your viralbox.in API key\n\n"
f"📝 /set_header <text> - Add custom header text above links\n"
f"Example: /set_header 👇 Download Links 👇\n\n"
f"📝 /set_footer <text> - Add custom footer text below links\n"
f"Example: /set_footer Join: @viralbox_support\n\n"
f"✅ /keep_text - Keep your original message text and replace only links\n\n"
f"❌ /delete_text - Show only converted links (with header/footer if set)\n\n"
f"🗑️ /delete_header - Remove custom header\n"
f"🗑️ /delete_footer - Remove custom footer\n\n"
f"💬 For any query contact: @viralbox_support")
            return

        # -------- /set_api Command -------- #
        if text.startswith("/set_api"):
            parts = text.split(maxsplit=1)
            if len(parts) < 2:
                send_message(chat_id, "❌ Correct usage: /set_api <API_KEY>")
                return

            apikey = parts[1].strip()
            save_api_key(user_id, apikey)
            send_message(chat_id, "✅ API Key Saved Successfully!")
            return

        # -------- /set_header Command -------- #
        if text.startswith("/set_header"):
            parts = text.split(maxsplit=1)
            if len(parts) < 2:
                send_message(chat_id, "❌ Usage: /set_header <your header text>\n\nExample: /set_header 👇 Download Links 👇")
                return
            
            header_text = parts[1].strip()
            save_user_setting(user_id, "header", header_text)
            send_message(chat_id, f"✅ Header Saved Successfully!\n\n📝 Your Header:\n{header_text}")
            return

        # -------- /delete_header Command -------- #
        if text.startswith("/delete_header"):
            delete_user_setting(user_id, "header")
            send_message(chat_id, "✅ Header Deleted Successfully!")
            return

        # -------- /set_footer Command -------- #
        if text.startswith("/set_footer"):
            parts = text.split(maxsplit=1)
            if len(parts) < 2:
                send_message(chat_id, "❌ Usage: /set_footer <your footer text>\n\nExample: /set_footer Join: @viralbox_support")
                return
            
            footer_text = parts[1].strip()
            save_user_setting(user_id, "footer", footer_text)
            send_message(chat_id, f"✅ Footer Saved Successfully!\n\n📝 Your Footer:\n{footer_text}")
            return

        # -------- /delete_footer Command -------- #
        if text.startswith("/delete_footer"):
            delete_user_setting(user_id, "footer")
            send_message(chat_id, "✅ Footer Deleted Successfully!")
            return

        # -------- /keep_text Command -------- #
        if text.startswith("/keep_text"):
            save_user_setting(user_id, "keep_text", True)
            send_message(chat_id, "✅ Keep Text Mode Enabled!\n\n📝 Now your original text will be kept and only links will be replaced.")
            return

        # -------- /delete_text Command -------- #
        if text.startswith("/delete_text"):
            save_user_setting(user_id, "keep_text", False)
            send_message(chat_id, "✅ Delete Text Mode Enabled!\n\n📝 Now only converted links (with header/footer) will be shown.")
            return

        # -------- Ensure API Key Exists -------- #
        user_api = get_api_key(user_id)
        if not user_api:
            send_message(chat_id, "❌ Please set your API key first:\n/set_api <API_KEY>")
            return

        # -------- Get User Settings -------- #
        settings = get_user_settings(user_id)

        # -------- URL Extraction -------- #
        media_type = None
        file_id = None
        original_text = text

        # Check for media
        for t in ["photo", "video", "document", "audio", "voice", "animation"]:
            if msg.get(t):
                media_type = t
                if t == "photo":
                    file_id = msg[t][-1]["file_id"]
                else:
                    file_id = msg[t]["file_id"]
                
                original_text = msg.get("caption", "")
                break

        urls = extract_urls(original_text)

        if not urls:
            send_message(chat_id, "❌ Please send a valid viralbox.in link.")
            return

        # -------- Process All URLs -------- #
        url_mapping = {}  # Old URL -> New URL mapping
        converted_links = []
        
        for url in urls:
            if not is_viralbox(url):
                send_message(chat_id, f"❌ Only viralbox.in links are supported! (Invalid: {url})")
                return

            longURL = find_long_url(url)
            if not longURL:
                send_message(chat_id, f"❌ This link does not exist in database. ({url})")
                return

            newShort = short_with_user_token(user_api, longURL)
            if not newShort:
                send_message(chat_id, f"❌ Failed to convert link using your API key. ({url})")
                return

            save_converted(longURL, newShort)
            url_mapping[url] = newShort
            converted_links.append(newShort)

        # -------- Build Response Text -------- #
        if settings["keep_text"]:
            # Keep original text and replace URLs
            response_text = replace_urls_in_text(original_text, url_mapping)
        else:
            # Show only converted links with header/footer
            parts = []
            
            if settings["header"]:
                parts.append(settings["header"])
            
            parts.extend(converted_links)
            
            if settings["footer"]:
                parts.append(settings["footer"])
            
            response_text = "\n\n".join(parts)

        # -------- Send Response -------- #
        if not media_type:
            send_message(chat_id, response_text)
        else:
            send_media(chat_id, media_type, file_id, response_text)
    
    except Exception as e:
        print(f"Error processing message: {e}")


# ------------------ BOT POLLING LOOP (CONCURRENT) ------------------ #

def polling_loop():
    print(f"🤖 Bot Running in Concurrent Mode with {MAX_WORKERS} workers…")
    offset = None
    
    # Create thread pool executor
    executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)

    while True:
        try:
            res = requests.get(
                f"{TELEGRAM_API}/getUpdates",
                params={"timeout": 50, "offset": offset},
                timeout=60
            ).json()

            for upd in res.get("result", []):
                offset = upd["update_id"] + 1

                if "message" in upd:
                    # Submit message to thread pool (non-blocking)
                    executor.submit(process_message, upd["message"])

        except Exception as e:
            print("Polling Error:", e)
            time.sleep(2)


# ------------------ START BOT ------------------ #

if __name__ == "__main__":
    # Start health check server in background
    health_thread = Thread(target=start_health_server, daemon=True)
    health_thread.start()
    
    # Start bot polling
    polling_loop()
