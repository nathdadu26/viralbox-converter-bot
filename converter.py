# bot.py  (Polling Mode - VPS Ready with Health Check)
# Logic identical to converter.js (no behaviour change)

import os
import time
import requests
from urllib.parse import urlparse
from pymongo import MongoClient
from dotenv import load_dotenv
from datetime import datetime
from threading import Thread
from http.server import HTTPServer, BaseHTTPRequestHandler

load_dotenv()

BOT_TOKEN = os.getenv("CONVERTER_BOT_TOKEN")
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

MONGODB_URI = os.getenv("MONGODB_URI")
DB_NAME = os.getenv("MONGO_DB_NAME", "viralbox_db")
VIRALBOX_DOMAIN = os.getenv("VIRALBOX_DOMAIN", "viralbox.in")
HEALTH_CHECK_PORT = int(os.getenv("PORT", "8000"))  # Koyeb PORT environment variable

if not BOT_TOKEN or not MONGODB_URI:
    raise RuntimeError("BOT_TOKEN and MONGODB_URI must be in .env")

# ------------------ DB SETUP ------------------ #
client = MongoClient(MONGODB_URI)
db = client[DB_NAME]

links_col = db["links"]          # { longURL, shortURL }
user_apis_col = db["user_apis"]  # { userId, apiKey }


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
                "timestamp": datetime.utcnow().isoformat()
            }
            self.wfile.write(str(response).encode())
        else:
            self.send_response(404)
            self.end_headers()
    
    def log_message(self, format, *args):
        # Suppress health check logs
        pass


def start_health_server():
    """Start health check server in background thread"""
    server = HTTPServer(('0.0.0.0', HEALTH_CHECK_PORT), HealthCheckHandler)
    print(f"✅ Health check server running on port {HEALTH_CHECK_PORT}")
    server.serve_forever()


# ------------------ HELPERS ------------------ #

def extract_urls(text):
    if not text:
        return []
    import re
    urls = re.findall(r"(https?://[^\s]+)", text)
    return urls if urls else []


def is_viralbox(url):
    try:
        u = urlparse(url)
        return VIRALBOX_DOMAIN in u.hostname
    except:
        return False


def send_message(chat_id, text):
    requests.post(
        f"{TELEGRAM_API}/sendMessage",
        json={"chat_id": chat_id, "text": text}
    )


def send_media(chat_id, mtype, file_id, caption=None):
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

    payload = {"chat_id": chat_id, "caption": caption}
    payload[mtype] = file_id
    requests.post(f"{TELEGRAM_API}/{endpoint}", json=payload)


# ------------------ DATABASE FUNCTIONS ------------------ #

def save_api_key(user_id, apikey):
    user_apis_col.update_one(
        {"userId": user_id},
        {"$set": {"userId": user_id, "apiKey": apikey}},
        upsert=True
    )


def get_api_key(user_id):
    doc = user_apis_col.find_one({"userId": user_id})
    return doc["apiKey"] if doc else None


def save_converted(longURL, shortURL):
    links_col.insert_one({
        "longURL": longURL,
        "shortURL": shortURL
    })


def find_long_url(shortURL):
    doc = links_col.find_one({"shortURL": shortURL})
    if doc:
        return doc["longURL"]
    return None


# ------------------ SHORTNING ------------------ #

def short_with_user_token(apiKey, longURL):
    try:
        api = f"https://viralbox.in/api?api={apiKey}&url={requests.utils.requote_uri(longURL)}"
        r = requests.get(api, timeout=15)
        j = r.json()

        if j.get("status") == "success":
            return j.get("shortenedUrl") or j.get("short_url") or j.get("short")

        return None

    except Exception:
        return None


# ------------------ PROCESS MESSAGE ------------------ #

def process_message(msg):
    chat_id = msg["chat"]["id"]
    user_id = msg["from"]["id"]
    text = msg.get("text", "")

    # -------- Commands -------- #
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
f"/set_api - Save your API Key\n"
f"/help - Support - @viralbox_support")
        return

    if text.startswith("/help"):
        send_message(chat_id, "Hii For Any Query Contact Support - @viralbox_support")
        return

    if text.startswith("/set_api"):
        parts = text.split()
        if len(parts) < 2:
            send_message(chat_id, "❌ Correct usage: /set_api <API_KEY>")
            return

        apikey = parts[1].strip()
        save_api_key(user_id, apikey)
        send_message(chat_id, "✅ API Key Saved Successfully!")
        return

    # -------- Ensure API Key Exists -------- #
    user_api = get_api_key(user_id)
    if not user_api:
        send_message(chat_id, "❌ Please set your API key first:\n/set_api <API_KEY>")
        return

    # -------- URL Extraction -------- #
    urls = extract_urls(text)
    media_type = None
    file_id = None

    for t in ["photo", "video", "document", "audio", "voice", "animation"]:
        if msg.get(t):
            media_type = t
            if t == "photo":
                file_id = msg[t][-1]["file_id"]
            else:
                file_id = msg[t]["file_id"]

            urls = extract_urls(msg.get("caption", "")) or urls
            break

    if not urls:
        send_message(chat_id, "❌ Please send a valid viralbox.in link.")
        return

    # -------- Process All URLs -------- #
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
        converted_links.append(newShort)

    # -------- Send Converted Links -------- #
    response_text = "\n".join([f"✅Video Link\n{link}" for link in converted_links])

    if not media_type:
        send_message(chat_id, response_text)
    else:
        send_media(chat_id, media_type, file_id, response_text)


# ------------------ BOT POLLING LOOP ------------------ #

def polling_loop():
    print("🤖 Bot Running in Polling Mode…")
    offset = None

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
                    process_message(upd["message"])

        except Exception as e:
            print("Error:", e)
            time.sleep(2)


# ------------------ START BOT ------------------ #

if __name__ == "__main__":
    # Start health check server in background
    health_thread = Thread(target=start_health_server, daemon=True)
    health_thread.start()
    
    # Start bot polling
    polling_loop()
