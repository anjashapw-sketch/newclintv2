"""
================================================================
  OSINT Bot v8.2 — FAM Gateway Payment Integration
  Config: sab kuch .env se load hota hai
================================================================
"""

import json, os, io, csv, asyncio, time, re, html, threading, traceback
from datetime import datetime, timezone, timedelta
from collections import defaultdict, deque
from typing import Optional, Dict, List, Any

import httpx
from dotenv import load_dotenv
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, KeyboardButton, BotCommand
)
from telegram.ext import (
    Application, CommandHandler, ContextTypes,
    CallbackQueryHandler, MessageHandler, filters
)
from telegram.constants import ParseMode
from telegram.error import BadRequest

# =================================================================
#  LOAD .env
# =================================================================
load_dotenv()

def _env(key: str, default: str = "") -> str:
    """Get env value, strip surrounding quotes & whitespace."""
    v = os.getenv(key, default)
    if v is None:
        return default
    v = str(v).strip()
    if len(v) >= 2 and ((v[0] == '"' and v[-1] == '"') or (v[0] == "'" and v[-1] == "'")):
        v = v[1:-1]
    return v

def _env_int(key: str, default: int = 0) -> int:
    try:
        return int(_env(key, str(default)))
    except Exception:
        return default

def _env_list(key: str, default: str = "") -> List[str]:
    raw = _env(key, default)
    return [x.strip() for x in raw.split(",") if x.strip()]

# =================================================================
#  CONFIG (sab .env se)
# =================================================================
BOT_TOKEN = _env("BOT_TOKEN")

ADMINS = set()
for _x in _env_list("ADMINS"):
    try:
        ADMINS.add(int(_x))
    except Exception:
        pass

USERS_FILE = _env("USERS_FILE", "users.json")
SETTINGS_FILE = _env("SETTINGS_FILE", "settings.json")
HISTORY_FILE = _env("HISTORY_FILE", "search_history.json")
LOGS_FILE = _env("LOGS_FILE", "activity_logs.json")
STATS_FILE = _env("STATS_FILE", "daily_stats.json")
PAYMENTS_FILE = _env("PAYMENTS_FILE", "payments.json")

TZ_OFFSET_MIN = _env_int("TZ_OFFSET_MIN", 330)
DEVELOPER_NAME = _env("DEVELOPER_NAME", "@itzanjasha")

# =================================================================
#  FORCE JOIN (from .env)
# =================================================================
try:
    REQUIRED_CHANNELS = json.loads(_env("REQUIRED_CHANNELS", "[]") or "[]")
except Exception as e:
    print(f"[ENV REQUIRED_CHANNELS] parse error: {e}")
    REQUIRED_CHANNELS = []

REQUIRED_GROUP = _env("REQUIRED_GROUP", "")
GROUP_LINK = _env("GROUP_LINK", "")

# =================================================================
#  SEARCH APIs
# =================================================================
NUM_API_URL = _env("NUM_API_URL")
NUM_API_KEY = _env("NUM_API_KEY")
NUM_PARAM_NAMES = _env_list("NUM_PARAM_NAMES", "number,phone,mobile,q,query")

AADHAAR_API_URL = _env("AADHAAR_API_URL")
AADHAAR_API_KEY = _env("AADHAAR_API_KEY")
AADHAAR_PARAM_NAMES = _env_list("AADHAAR_PARAM_NAMES", "value,aadhaar,number,q,query")

API_TIMEOUT = _env_int("API_TIMEOUT", 30)

# =================================================================
#  FAM GATEWAY
# =================================================================
FAM_CREATE_URL = _env("FAM_CREATE_URL")
FAM_VERIFY_URL = _env("FAM_VERIFY_URL")
FAM_CHECKOUT_STATUS_URL = _env("FAM_CHECKOUT_STATUS_URL")
FAM_API_KEY = _env("FAM_API_KEY")
FAM_REDIRECT_URL = _env("FAM_REDIRECT_URL")

FAM_TIMEOUT = _env_int("FAM_TIMEOUT", 20)
FAM_POLL_INTERVAL = _env_int("FAM_POLL_INTERVAL", 4)
FAM_POLL_MAX_ATTEMPTS = _env_int("FAM_POLL_MAX_ATTEMPTS", 75)

# =================================================================
#  Rate limit / Limits
# =================================================================
RATE_LIMIT_WINDOW = _env_int("RATE_LIMIT_WINDOW", 60)
RATE_LIMIT_MAX = _env_int("RATE_LIMIT_MAX", 10)

MAX_HISTORY_PER_USER = _env_int("MAX_HISTORY_PER_USER", 50)
MAX_MSG_LEN = _env_int("MAX_MSG_LEN", 4000)
TG_MSG_LIMIT = _env_int("TG_MSG_LIMIT", 4096)
STATE_TTL_SECONDS = _env_int("STATE_TTL_SECONDS", 300)

# =================================================================
#  GLOBAL STATE
# =================================================================
BOT_USERNAME_CACHE = "bot"
_pending_saves: Dict[str, Any] = {}
_save_timers: Dict[str, threading.Timer] = {}
_save_lock = threading.Lock()
_active_pollers: Dict[str, threading.Thread] = {}

# =================================================================
#  FANCY TEXT
# =================================================================
_SMALLCAPS = {
    'a':'ᴀ','b':'ʙ','c':'ᴄ','d':'ᴅ','e':'ᴇ','f':'ꜰ','g':'ɢ','h':'ʜ',
    'i':'ɪ','j':'ᴊ','k':'ᴋ','l':'ʟ','m':'ᴍ','n':'ɴ','o':'ᴏ','p':'ᴘ',
    'q':'ǫ','r':'ʀ','s':'ꜱ','t':'ᴛ','u':'ᴜ','v':'ᴠ','w':'ᴡ','x':'x',
    'y':'ʏ','z':'ᴢ','0':'⁰','1':'¹','2':'²','3':'³','4':'⁴','5':'⁵',
    '6':'⁶','7':'⁷','8':'⁸','9':'⁹',
}

def fancy(text) -> str:
    if text is None: return ""
    return "".join(_SMALLCAPS.get(c.lower(), c) for c in str(text))

def esc(text) -> str:
    if text is None: return ""
    return html.escape(str(text), quote=False)

def esc_pre(text) -> str:
    if text is None: return ""
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

def split_message(text: str, limit: int = TG_MSG_LIMIT) -> List[str]:
    if len(text) <= limit: return [text]
    parts = []; rem = text
    while rem:
        if len(rem) <= limit:
            parts.append(rem); break
        chunk = rem[:limit]
        idx = chunk.rfind("\n")
        if idx < limit // 2: idx = limit
        parts.append(rem[:idx]); rem = rem[idx:]
    return parts

# =================================================================
#  TIME
# =================================================================
def now_ist():
    return datetime.now(timezone.utc) + timedelta(minutes=TZ_OFFSET_MIN)
def now_ist_str():
    return now_ist().strftime("%Y-%m-%d %H:%M:%S IST")
def now_ist_pretty():
    return now_ist().strftime("%d-%b-%Y %I:%M %p")
def today_str():
    return now_ist().strftime("%Y-%m-%d")

# =================================================================
#  JSON STORAGE
# =================================================================
def _write_json_now(path: str, data: Any):
    try:
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False, default=str)
        os.replace(tmp, path)
    except Exception as e:
        print(f"[SAVE {path}] {e}")

def _flush_save(path: str):
    with _save_lock:
        data = _pending_saves.pop(path, None)
        _save_timers.pop(path, None)
    if data is not None: _write_json_now(path, data)

def save_json(path: str, data: Any, debounce: float = 0.0):
    if debounce <= 0:
        _write_json_now(path, data); return
    with _save_lock:
        _pending_saves[path] = data
        if path in _save_timers: _save_timers[path].cancel()
        t = threading.Timer(debounce, _flush_save, args=(path,))
        t.daemon = True; t.start(); _save_timers[path] = t

def load_json(path: str, default: Any) -> Any:
    if not os.path.exists(path):
        return default.copy() if isinstance(default, dict) else list(default) if isinstance(default, list) else default
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(default, dict) and not isinstance(data, dict): return default.copy()
        if isinstance(default, list) and not isinstance(data, list): return list(default)
        return data
    except Exception as e:
        print(f"[LOAD {path}] {e}")
        return default.copy() if isinstance(default, dict) else list(default) if isinstance(default, list) else default

def flush_all_pending():
    with _save_lock: paths = list(_pending_saves.keys())
    for p in paths: _flush_save(p)

# =================================================================
#  SETTINGS
# =================================================================
DEFAULT_SETTINGS = {
    "referral_bonus": 10, "welcome_credits": 30,
    "number_cost": 10, "aadhaar_cost": 10,
    "credits_per_rupee": 1,
    "force_join_enabled": True, "maintenance_mode": False,
    "rate_limit_enabled": True, "referral_enabled": True,
    "payment_enabled": True,
}
SETTINGS = load_json(SETTINGS_FILE, DEFAULT_SETTINGS)
for k, v in DEFAULT_SETTINGS.items(): SETTINGS.setdefault(k, v)

def save_settings(): save_json(SETTINGS_FILE, SETTINGS)
def get_setting(key, default=None): return SETTINGS.get(key, default)
def set_setting(key, value): SETTINGS[key] = value; save_settings()

# =================================================================
#  USERS
# =================================================================
USERS = load_json(USERS_FILE, {})
def save_users(): save_json(USERS_FILE, USERS)

def ensure_user(chat_id, referrer=None) -> dict:
    sid = str(chat_id)
    if sid not in USERS:
        USERS[sid] = {
            "user_id": chat_id,
            "credits": int(get_setting("welcome_credits", 30)),
            "joined_at": now_ist_str(), "joined_at_ts": time.time(),
            "last_used_at": None, "last_used_ts": None,
            "searches": 0, "number_searches": 0, "aadhaar_searches": 0,
            "referred_by": referrer, "referrals": 0,
            "total_earned": 0, "banned": False, "ban_reason": "",
        }
        save_users()
        if referrer and str(referrer) in USERS and referrer != chat_id:
            if get_setting("referral_enabled", True):
                rid = str(referrer); bonus = int(get_setting("referral_bonus", 10))
                USERS[rid]["credits"] = int(USERS[rid].get("credits", 0)) + bonus
                USERS[rid]["referrals"] = int(USERS[rid].get("referrals", 0)) + 1
                USERS[rid]["total_earned"] = int(USERS[rid].get("total_earned", 0)) + bonus
                save_users()
    return USERS[sid]

def get_user(chat_id): return USERS.get(str(chat_id))

def consume_credit(chat_id, cost=1, search_type="number"):
    u = ensure_user(chat_id)
    u["credits"] = max(0, int(u["credits"]) - int(cost))
    u["searches"] = int(u.get("searches", 0)) + 1
    if search_type == "number": u["number_searches"] = int(u.get("number_searches", 0)) + 1
    elif search_type == "aadhaar": u["aadhaar_searches"] = int(u.get("aadhaar_searches", 0)) + 1
    u["last_used_at"] = now_ist_str(); u["last_used_ts"] = time.time()
    save_users()

def add_credits(chat_id, amount) -> int:
    u = ensure_user(chat_id)
    u["credits"] = int(u.get("credits", 0)) + int(amount)
    save_users(); return u["credits"]

def is_banned(chat_id) -> bool:
    u = get_user(chat_id); return bool(u and u.get("banned", False))

def ban_user(chat_id, reason=""):
    u = ensure_user(chat_id); u["banned"] = True; u["ban_reason"] = reason
    u["banned_at"] = now_ist_str(); save_users()

def unban_user(chat_id):
    u = ensure_user(chat_id); u["banned"] = False; u["ban_reason"] = ""; save_users()

_stats_cache = {"data": None, "ts": 0}

def get_user_stats(force=False) -> dict:
    now_ts = time.time()
    if not force and _stats_cache["data"] and (now_ts - _stats_cache["ts"]) < 10:
        return _stats_cache["data"]
    total_users = len(USERS)
    banned = total_credits = total_searches = 0
    number_searches = aadhaar_searches = total_refs = active_24h = 0
    cutoff_24h = now_ts - 86400
    for u in USERS.values():
        if u.get("banned"): banned += 1
        total_credits += int(u.get("credits", 0))
        total_searches += int(u.get("searches", 0))
        number_searches += int(u.get("number_searches", 0))
        aadhaar_searches += int(u.get("aadhaar_searches", 0))
        total_refs += int(u.get("referrals", 0))
        lu = u.get("last_used_ts")
        if lu and lu > cutoff_24h: active_24h += 1
    result = {
        "total_users": total_users, "banned": banned,
        "active": total_users - banned, "active_24h": active_24h,
        "total_credits": total_credits, "total_searches": total_searches,
        "number_searches": number_searches, "aadhaar_searches": aadhaar_searches,
        "total_referrals": total_refs,
    }
    _stats_cache["data"] = result; _stats_cache["ts"] = now_ts
    return result

# =================================================================
#  HISTORY / LOGS / STATS / PAYMENTS
# =================================================================
HISTORY = load_json(HISTORY_FILE, {})
def save_history(): save_json(HISTORY_FILE, HISTORY, debounce=1.0)
def add_to_history(chat_id, query_type, query_value, found, records_count=0):
    sid = str(chat_id); HISTORY.setdefault(sid, [])
    HISTORY[sid].append({"type": query_type, "query": query_value, "found": found,
                         "records": records_count, "at": now_ist_str(), "ts": time.time()})
    if len(HISTORY[sid]) > MAX_HISTORY_PER_USER:
        HISTORY[sid] = HISTORY[sid][-MAX_HISTORY_PER_USER:]
    save_history()
def get_history(chat_id, limit=20) -> list:
    hist = HISTORY.get(str(chat_id), [])
    return list(reversed(hist[-limit:]))

LOGS = load_json(LOGS_FILE, [])
def save_logs(): save_json(LOGS_FILE, LOGS, debounce=2.0)
def log_action(action, user_id=None, details=""):
    LOGS.append({"action": action, "user_id": user_id,
                 "details": str(details)[:500], "at": now_ist_str(), "ts": time.time()})
    if len(LOGS) > 5000: del LOGS[:-5000]
    save_logs()
def get_recent_logs(limit=30): return list(reversed(LOGS[-limit:]))

STATS = load_json(STATS_FILE, {})
def save_stats(): save_json(STATS_FILE, STATS, debounce=3.0)
def bump_daily_stat(key, amount=1):
    today = today_str(); STATS.setdefault(today, {})
    STATS[today][key] = STATS[today].get(key, 0) + amount; save_stats()
def get_daily_stats(days=7) -> dict:
    result = {}
    for i in range(days):
        d = (now_ist() - timedelta(days=i)).strftime("%Y-%m-%d")
        result[d] = STATS.get(d, {})
    return result

PAYMENTS = load_json(PAYMENTS_FILE, [])
def save_payments(): save_json(PAYMENTS_FILE, PAYMENTS, debounce=1.0)
def create_payment(chat_id, amount, credits, order_id=None, payment_link=None, gateway_raw=None):
    pid = f"PAY{int(time.time())}{chat_id % 10000}"
    record = {"id": pid, "user_id": chat_id, "amount": amount, "credits": credits,
              "status": "pending", "order_id": order_id, "payment_link": payment_link,
              "gateway_raw": gateway_raw, "created_at": now_ist_str(), "created_ts": time.time(),
              "processed_at": None, "processed_by": None}
    PAYMENTS.append(record); save_payments(); return pid
def get_payment(pid):
    for p in PAYMENTS:
        if p.get("id") == pid: return p
    return None
def get_payment_by_order(order_id):
    for p in PAYMENTS:
        if p.get("order_id") == order_id: return p
    return None
def update_payment(pid, **kwargs):
    for p in PAYMENTS:
        if p.get("id") == pid:
            p.update(kwargs); save_payments(); return p
    return None
def get_pending_payments():
    return [p for p in PAYMENTS if p.get("status") == "pending"]

# =================================================================
#  RATE LIMIT
# =================================================================
_rate_buckets = defaultdict(deque); _rate_lock = threading.Lock()
def check_rate_limit(chat_id) -> bool:
    if chat_id in ADMINS: return True
    if not get_setting("rate_limit_enabled", True): return True
    now_ts = time.time()
    with _rate_lock:
        bucket = _rate_buckets[chat_id]; cutoff = now_ts - RATE_LIMIT_WINDOW
        while bucket and bucket[0] < cutoff: bucket.popleft()
        if len(bucket) >= RATE_LIMIT_MAX: return False
        bucket.append(now_ts); return True
def rate_limit_reset_seconds(chat_id) -> int:
    with _rate_lock:
        bucket = _rate_buckets.get(chat_id)
        if not bucket: return 0
        now_ts = time.time()
        recent = [t for t in bucket if t > now_ts - RATE_LIMIT_WINDOW]
        if not recent: return 0
        return max(0, int(min(recent) + RATE_LIMIT_WINDOW - now_ts))

# =================================================================
#  USER STATES
# =================================================================
_user_states: Dict[int, Dict[str, Any]] = {}
def set_state(chat_id, state, **kwargs):
    _user_states[chat_id] = {"state": state, "data": kwargs,
                             "expires_at": time.time() + STATE_TTL_SECONDS}
    _cleanup_states()
def get_state(chat_id) -> Optional[str]:
    entry = _user_states.get(chat_id)
    if not entry: return None
    if entry["expires_at"] < time.time():
        _user_states.pop(chat_id, None); return None
    return entry["state"]
def get_state_data(chat_id) -> dict:
    entry = _user_states.get(chat_id)
    return entry.get("data", {}) if entry else {}
def clear_state(chat_id): _user_states.pop(chat_id, None)
def _cleanup_states():
    now_ts = time.time()
    for k in [k for k, v in _user_states.items() if v["expires_at"] < now_ts]:
        _user_states.pop(k, None)

# =================================================================
#  VALIDATION
# =================================================================
def clean_phone(text: str) -> Optional[str]:
    d = re.sub(r"\D", "", str(text or ""))
    if d.startswith("91") and len(d) == 12: d = d[2:]
    if d.startswith("0") and len(d) == 11: d = d[1:]
    return d if re.match(r"^[6-9]\d{9}$", d) else None
def clean_aadhaar(text: str) -> Optional[str]:
    d = re.sub(r"\D", "", str(text or ""))
    return d if re.match(r"^\d{12}$", d) else None

# =================================================================
#  RESPONSE BUILDER
# =================================================================
def record_to_clean_dict(item: dict) -> dict:
    if not isinstance(item, dict): return {"data": str(item)}
    alias_map = {
        "name": "name", "full_name": "name",
        "father_name": "father_name", "father": "father_name", "fname": "father_name",
        "mobile": "mobile", "number": "mobile", "phone": "mobile",
        "alt_mobile": "alt_mobile", "alternate_number": "alt_mobile", "alt": "alt_mobile",
        "aadhaar": "aadhaar", "aadhar": "aadhaar", "uid": "aadhaar",
        "circle": "circle", "operator": "circle",
        "address": "address", "addr": "address",
        "email": "email", "mail": "email",
        "village": "village", "district": "district", "state": "state",
        "pincode": "pincode", "pin": "pincode", "zip": "pincode",
        "dob": "dob", "date_of_birth": "dob", "gender": "gender", "sex": "gender",
    }
    out = {}
    for k, v in item.items():
        if v is None or str(v).strip() == "": continue
        kl = str(k).lower().strip(); key = alias_map.get(kl, kl)
        if key not in out: out[key] = v
    return out

def _clean_address(addr: str) -> str:
    if not addr: return addr
    s = str(addr)
    s = re.sub(r"\s*!\s*", " → ", s)
    s = re.sub(r"!+", " → ", s)
    s = re.sub(r"\s{2,}", " ", s)
    return s.strip(" →").strip()

def build_response(records: list, query_type: str = "number", query: str = "") -> str:
    if not records: return ""
    clean_results = []
    for rec in records:
        clean = record_to_clean_dict(rec)
        if "address" in clean: clean["address"] = _clean_address(clean["address"])
        if clean: clean_results.append(clean)
    payload = {
        "bot": f"@{BOT_USERNAME_CACHE}", "developer": DEVELOPER_NAME,
        "query_type": query_type, "query": query,
        "records_count": len(clean_results), "timestamp": now_ist_str(),
        "results": clean_results,
    }
    json_str = json.dumps(payload, indent=2, ensure_ascii=False, default=str)
    json_escaped = json_str.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return f"<pre>{json_escaped}</pre>"

def build_tries_footer(chat_id) -> str:
    return "\n".join([
        "━━━━━━━━━━━━━━━━━━━━━",
        f"🔄 {fancy('generated')}: {now_ist_pretty()}",
        f"🚀 {fancy('powered by')} @{BOT_USERNAME_CACHE} 💥TARZAN NETWORK 💥",
        f"🎯 {fancy('tries remaining')}: ᴜɴʟɪᴍɪᴛᴇᴅ ∞",
    ])

def build_json_response(records: list, query_type: str, query: str, chat_id) -> str:
    return f"{build_response(records, query_type=query_type, query=query)}\n{build_tries_footer(chat_id)}"

# =================================================================
#  SEARCH API
# =================================================================
async def api_call_with_fallback(base_url, key, value, param_names) -> tuple:
    last_error = None
    async with httpx.AsyncClient(timeout=API_TIMEOUT) as client:
        for param_name in param_names:
            try:
                params = {param_name: value, "key": key}
                r = await client.get(base_url, params=params,
                                     headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
                if r.status_code == 200:
                    try: return r.json(), None
                    except Exception as e: last_error = f"JSON parse: {e}"; continue
                if r.status_code == 422: last_error = f"422 with '{param_name}'"; continue
                if r.status_code == 404: return None, "Not found (404)"
                last_error = f"HTTP {r.status_code}"
            except httpx.TimeoutException: last_error = "Timeout"
            except Exception as e: last_error = str(e)
    return None, f"Failed. Last: {last_error}"

# =================================================================
#  FAM GATEWAY API
# =================================================================
async def fam_create_order(amount: float, customer_name: str = "User") -> tuple:
    payload_variants = [
        {"amount": float(amount), "api_key": FAM_API_KEY, "customer_name": customer_name},
        {"amount": float(amount), "key": FAM_API_KEY, "customer_name": customer_name},
        {"amount": float(amount), "customer_name": customer_name},
    ]
    headers_variants = [
        {"X-Api-Key": FAM_API_KEY, "Content-Type": "application/json", "Accept": "application/json"},
        {"Authorization": f"Bearer {FAM_API_KEY}", "Content-Type": "application/json", "Accept": "application/json"},
        {"Content-Type": "application/json", "Accept": "application/json"},
    ]
    last_err = "Unknown"
    async with httpx.AsyncClient(timeout=FAM_TIMEOUT) as client:
        for payload in payload_variants:
            for headers in headers_variants:
                try:
                    r = await client.post(FAM_CREATE_URL, json=payload, headers=headers)
                    print(f"[FAM create] status={r.status_code} payload_keys={list(payload.keys())}")
                    if r.status_code in (200, 201):
                        try: raw = r.json()
                        except Exception: last_err = "Bad JSON"; continue
                        if isinstance(raw, dict):
                            if raw.get("status") in ("success", "ok") or raw.get("success") is True:
                                d = raw.get("data") or raw
                                oid = d.get("order_id") or d.get("orderId")
                                if oid:
                                    return True, {
                                        "order_id": str(oid),
                                        "qr_url": d.get("qr_url") or d.get("qr"),
                                        "upi_intent": d.get("upi_intent") or d.get("upi_link"),
                                        "checkout_url": d.get("checkout_url") or d.get("checkout"),
                                        "payable_amount": d.get("payable_amount") or d.get("amount") or amount,
                                        "upi_id": d.get("upi_id"), "raw": raw,
                                    }, None
                            if "order_id" in raw:
                                return True, {
                                    "order_id": str(raw["order_id"]),
                                    "qr_url": raw.get("qr_url"),
                                    "upi_intent": raw.get("upi_intent"),
                                    "checkout_url": raw.get("checkout_url"),
                                    "payable_amount": raw.get("payable_amount", amount),
                                    "upi_id": raw.get("upi_id"), "raw": raw,
                                }, None
                        last_err = f"Unknown response: {str(raw)[:200]}"
                    else:
                        last_err = f"HTTP {r.status_code}: {r.text[:200]}"
                except Exception as e: last_err = str(e)
    return False, None, last_err

async def fam_verify_order(order_id: str) -> tuple:
    urls_to_try = [
        (FAM_CHECKOUT_STATUS_URL, {"order_id": order_id}),
        (FAM_VERIFY_URL, {"order_id": order_id}),
        (FAM_CHECKOUT_STATUS_URL, {"order_id": order_id, "api_key": FAM_API_KEY}),
    ]
    headers_variants = [
        {"X-Api-Key": FAM_API_KEY, "Accept": "application/json"},
        {"Authorization": f"Bearer {FAM_API_KEY}", "Accept": "application/json"},
        {"Accept": "application/json"},
    ]
    last_err = "Unknown"
    async with httpx.AsyncClient(timeout=FAM_TIMEOUT) as client:
        for url, params in urls_to_try:
            for headers in headers_variants:
                try:
                    r = await client.get(url, params=params, headers=headers)
                    if r.status_code == 200:
                        try: raw = r.json()
                        except Exception: last_err = "Bad JSON"; continue
                        if not isinstance(raw, dict):
                            last_err = f"Bad type: {type(raw)}"; continue
                        d = raw.get("data") if isinstance(raw.get("data"), dict) else raw
                        status_raw = str(d.get("status", raw.get("status", ""))).lower()
                        is_paid = bool(d.get("is_paid") or d.get("paid") or status_raw in ("success", "paid", "captured", "completed"))
                        is_expired = bool(d.get("is_expired") or status_raw == "expired")
                        return True, {
                            "status": "success" if is_paid else ("expired" if is_expired else "pending"),
                            "is_paid": is_paid,
                            "utr": d.get("utr") or d.get("rrn") or d.get("transaction_id"),
                            "sender_name": d.get("sender_name") or d.get("payer_name"),
                            "raw": raw,
                        }, None
                    else:
                        last_err = f"HTTP {r.status_code}: {r.text[:200]}"
                except Exception as e: last_err = str(e)
    return False, None, last_err

# =================================================================
#  FORCE JOIN
# =================================================================
def _to_chat_target(value):
    if isinstance(value, int): return value
    s = str(value).strip()
    if s.lstrip("-").isdigit(): return int(s)
    if not s.startswith("@"): s = "@" + s
    return s

def _is_bot_error(err_str: str) -> bool:
    err = err_str.lower()
    for issue in ["chat not found", "chat_admin_required", "not enough rights",
                  "bot is not a member", "bot was blocked", "bot was kicked",
                  "peer_id_invalid", "user not found"]:
        if issue in err: return True
    return False

def _is_user_not_joined(err_str: str) -> bool:
    err = err_str.lower()
    return ("user_not_participant" in err or "user not participant" in err
            or "user is not a participant" in err)

async def check_joined_status(context, user_id) -> bool:
    if not get_setting("force_join_enabled", True): return True
    if user_id in ADMINS: return True
    for ch in REQUIRED_CHANNELS:
        target_raw = ch.get("username") or ch.get("id")
        if not target_raw: continue
        target = _to_chat_target(target_raw)
        try:
            m = await context.bot.get_chat_member(target, user_id)
            if m.status in ("left", "kicked"): return False
        except Exception as e:
            err_str = str(e)
            if _is_user_not_joined(err_str): return False
            if _is_bot_error(err_str):
                print(f"[FJ skip ch {target}] {err_str[:120]}")
                alt_raw = ch.get("id") if ch.get("username") else ch.get("username")
                if alt_raw:
                    alt = _to_chat_target(alt_raw)
                    try:
                        m2 = await context.bot.get_chat_member(alt, user_id)
                        if m2.status in ("left", "kicked"): return False
                        continue
                    except Exception as e2:
                        if _is_user_not_joined(str(e2)): return False
                        continue
                continue
            print(f"[FJ ch err {target}] {err_str[:120]}")
            continue
    if REQUIRED_GROUP:
        target = _to_chat_target(REQUIRED_GROUP)
        try:
            g = await context.bot.get_chat_member(target, user_id)
            if g.status in ("left", "kicked"): return False
        except Exception as e:
            err_str = str(e)
            if _is_user_not_joined(err_str): return False
            if _is_bot_error(err_str):
                print(f"[FJ skip group {target}] {err_str[:120]}")
            else:
                print(f"[FJ group err {target}] {err_str[:120]}")
    return True

async def send_join_prompt(context, chat_id):
    kb = []
    for i, ch in enumerate(REQUIRED_CHANNELS, 1):
        kb.append([InlineKeyboardButton(f"📢 Join {ch.get('name', f'Channel {i}')}", url=ch["link"])])
    if REQUIRED_GROUP and GROUP_LINK:
        kb.append([InlineKeyboardButton("💬 Join Group", url=GROUP_LINK)])
    kb.append([InlineKeyboardButton("✅ Verify", callback_data="check_join")])
    text = ("⚠️ <b>ᴘᴇʜʟᴇ ꜱᴀᴀʀᴇ ᴄʜᴀɴɴᴇʟꜱ ᴊᴏɪɴ ᴋᴀʀᴏ!</b>\n\n"
            "1️⃣ Channels join karo\n2️⃣ Group join karo\n3️⃣ Niche <b>✅ Verify</b> dabao")
    await context.bot.send_message(chat_id=chat_id, text=text,
                                   reply_markup=InlineKeyboardMarkup(kb),
                                   parse_mode=ParseMode.HTML)

# =================================================================
#  KEYBOARDS
# =================================================================
def main_menu_kb(is_admin=False):
    kb = [
        [KeyboardButton("📱 Number Info"), KeyboardButton("🆔 Aadhaar Info")],
        [KeyboardButton("💳 Buy Credits"), KeyboardButton("💰 Refer & Earn")],
        [KeyboardButton("👤 My Account"), KeyboardButton("📜 History")],
        [KeyboardButton("🆔 My ID"), KeyboardButton("ℹ️ About")],
        [KeyboardButton("🆘 Support")],
    ]
    if is_admin: kb.append([KeyboardButton("👑 ADMIN PANEL")])
    return ReplyKeyboardMarkup(kb, resize_keyboard=True, is_persistent=True)

def admin_menu_kb():
    kb = [
        [KeyboardButton("📊 Dashboard"), KeyboardButton("👥 User List")],
        [KeyboardButton("💳 Add Credits"), KeyboardButton("➖ Sub Credits")],
        [KeyboardButton("💰 Set Balance"), KeyboardButton("📋 User Info")],
        [KeyboardButton("📢 Broadcast"), KeyboardButton("🎁 Bonus All")],
        [KeyboardButton("🚫 Ban User"), KeyboardButton("✅ Unban User")],
        [KeyboardButton("💳 Pending Payments"), KeyboardButton("📊 Payment Stats")],
        [KeyboardButton("📤 Export Users"), KeyboardButton("📥 Backup")],
        [KeyboardButton("⚙️ Settings"), KeyboardButton("📝 Logs")],
        [KeyboardButton("🏠 User Menu")],
    ]
    return ReplyKeyboardMarkup(kb, resize_keyboard=True, is_persistent=True)

def back_kb():
    return ReplyKeyboardMarkup([[KeyboardButton("🔙 Cancel")]], resize_keyboard=True)

# =================================================================
#  SAFE EDIT
# =================================================================
async def safe_edit(msg, text: str, **kw):
    parts = split_message(text, MAX_MSG_LEN)
    try: await msg.edit_text(parts[0], **kw)
    except BadRequest as e:
        if "not modified" in str(e).lower(): return
        try:
            plain = re.sub(r"<[^>]+>", "", parts[0])
            await msg.edit_text(plain)
        except Exception:
            try: await msg.edit_text(parts[0][:4000])
            except Exception: pass
    for part in parts[1:]:
        try: await msg.reply_text(part, **kw)
        except Exception: pass

# =================================================================
#  START
# =================================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = update.effective_user
    if is_banned(chat_id):
        await update.message.reply_text("🚫 You are banned."); return
    if chat_id not in ADMINS and not await check_joined_status(context, chat_id):
        await send_join_prompt(context, chat_id); return
    referrer = None
    if context.args and context.args[0].startswith("ref_"):
        try: referrer = int(context.args[0][4:])
        except Exception: pass
    u = ensure_user(chat_id, referrer=referrer)
    is_admin = chat_id in ADMINS
    log_action("start", chat_id); bump_daily_stat("starts")
    if is_admin:
        s = get_user_stats()
        text = (f"👑 <b>{fancy('admin panel')}</b>\n━━━━━━━━━━━━━━━━━━━━━\n"
                f"👥 {fancy('users')}: <b>{s['total_users']}</b>\n"
                f"💰 {fancy('credits')}: <b>{s['total_credits']}</b>\n"
                f"🔄 {fancy('generated')}: {now_ist_pretty()}\n━━━━━━━━━━━━━━━━━━━━━\n"
                f"Niche admin buttons use karo.")
        await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=admin_menu_kb())
    else:
        text = (f"👋 <b>{fancy('welcome')} {esc(user.first_name or 'User')}!</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n🆔 <code>{chat_id}</code>\n"
                f"💰 {fancy('credits')}: <b>{u['credits']}</b>\n"
                f"🎯 {fancy('searches')}: <b>{u['searches']}</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"📱 {fancy('number')}: {get_setting('number_cost', 10)} credit\n"
                f"🆔 {fancy('aadhaar')}: {get_setting('aadhaar_cost', 10)} credit\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n⬇️ Niche buttons use karo.")
        await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=main_menu_kb())

# =================================================================
#  VERIFY
# =================================================================
async def verify_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    chat_id = q.from_user.id
    try:
        joined = await check_joined_status(context, chat_id)
        if joined:
            await q.answer("✅ Verified! Ab /start dabao.", show_alert=True)
            try:
                await q.edit_text("✅ <b>Verification Successful!</b>\n\nAb /start dabao aur bot use karo.",
                                  parse_mode=ParseMode.HTML)
            except Exception: pass
        else:
            await q.answer("❌ Aapne saare channels/group join nahi kiye!\n\nJoin karke dobara Verify dabao.",
                           show_alert=True)
    except Exception as e:
        print(f"[verify_cb] {e}")
        try: await q.answer("⚠️ Error. Try again.", show_alert=True)
        except Exception: pass

# =================================================================
#  NUMBER LOOKUP
# =================================================================
async def do_number_lookup(context, chat_id, raw_input):
    phone = clean_phone(raw_input)
    if not phone:
        await context.bot.send_message(chat_id,
            "❌ <b>Invalid phone number.</b>\n\nExample: <code>9876543210</code>",
            parse_mode=ParseMode.HTML); return
    if not check_rate_limit(chat_id):
        rem = rate_limit_reset_seconds(chat_id)
        await context.bot.send_message(chat_id, f"⚠️ <b>Rate limit.</b> Wait {rem}s.",
                                       parse_mode=ParseMode.HTML); return
    u = ensure_user(chat_id)
    cost = int(get_setting("number_cost", 10))
    if chat_id not in ADMINS and u["credits"] < cost:
        await context.bot.send_message(chat_id,
            f"⚠️ <b>Not enough credits.</b>\n\n💰 Balance: <b>{u['credits']}</b>\n"
            f"💳 Need: <b>{cost}</b>\n\nBuy credits: 💳 Buy Credits",
            parse_mode=ParseMode.HTML); return
    try: await context.bot.send_chat_action(chat_id, "typing")
    except Exception: pass
    msg = await context.bot.send_message(chat_id,
        f"🔍 <b>{fancy('searching')}...</b>\n\n📱 <code>{esc(phone)}</code>",
        parse_mode=ParseMode.HTML)
    data, err = await api_call_with_fallback(NUM_API_URL, NUM_API_KEY, phone, NUM_PARAM_NAMES)
    if err:
        bump_daily_stat("api_errors")
        await safe_edit(msg, f"❌ <b>{fancy('error')}</b>\n\n<code>{esc(err[:200])}</code>",
                        parse_mode=ParseMode.HTML); return
    if not isinstance(data, dict) or not data.get("success"):
        em = data.get("credit", data.get("message", "Unknown")) if isinstance(data, dict) else "Invalid"
        await safe_edit(msg, f"❌ <b>{fancy('error')}</b>\n\n{esc(em)}",
                        parse_mode=ParseMode.HTML); return
    if not data.get("found", False):
        if chat_id not in ADMINS: consume_credit(chat_id, cost, "number")
        await safe_edit(msg, f"❌ <b>{fancy('no records found')}</b>\n\n📱 <code>{esc(phone)}</code>",
                        parse_mode=ParseMode.HTML); return
    records = data.get("result", [])
    if not isinstance(records, list): records = [records] if records else []
    records = [r for r in records if isinstance(r, dict)]
    text = build_json_response(records, "number", phone, chat_id)
    if chat_id not in ADMINS: consume_credit(chat_id, cost, "number")
    add_to_history(chat_id, "number", phone, True, len(records))
    bump_daily_stat("number_found"); log_action("number_search", chat_id, phone)
    if len(text) <= MAX_MSG_LEN:
        await safe_edit(msg, text, parse_mode=ParseMode.HTML)
    else:
        clean_results = []
        for r in records:
            c = record_to_clean_dict(r)
            if "address" in c: c["address"] = _clean_address(c["address"])
            if c: clean_results.append(c)
        payload = {"bot": f"@{BOT_USERNAME_CACHE}", "developer": DEVELOPER_NAME,
                   "query_type": "number", "query": phone,
                   "records_count": len(clean_results), "timestamp": now_ist_str(),
                   "results": clean_results}
        json_str = json.dumps(payload, indent=2, ensure_ascii=False, default=str)
        await safe_edit(msg, f"📋 <b>{fancy('json response')}</b>\n\n"
                             f"📊 Records: <b>{len(records)}</b>\n💳 Used: <b>{cost}</b>\n\n"
                             f"<i>File me JSON bhej raha hoon...</i>",
                        parse_mode=ParseMode.HTML)
        try:
            buf = io.BytesIO(json_str.encode("utf-8"))
            buf.name = f"num_{phone}_{int(time.time())}.json"
            await context.bot.send_document(chat_id=chat_id, document=buf, filename=buf.name,
                                            caption=f"📋 JSON — {len(records)} records")
        except Exception as e: print(f"[JSON file] {e}")
        await context.bot.send_message(chat_id, build_tries_footer(chat_id), parse_mode=ParseMode.HTML)

# =================================================================
#  AADHAAR LOOKUP
# =================================================================
async def do_aadhaar_lookup(context, chat_id, raw_input):
    aadhaar = clean_aadhaar(raw_input)
    if not aadhaar:
        await context.bot.send_message(chat_id,
            "❌ <b>Invalid Aadhaar.</b>\n\nExample: <code>123456789012</code>",
            parse_mode=ParseMode.HTML); return
    if not check_rate_limit(chat_id):
        rem = rate_limit_reset_seconds(chat_id)
        await context.bot.send_message(chat_id, f"⚠️ <b>Rate limit.</b> Wait {rem}s.",
                                       parse_mode=ParseMode.HTML); return
    u = ensure_user(chat_id)
    cost = int(get_setting("aadhaar_cost", 10))
    if chat_id not in ADMINS and u["credits"] < cost:
        await context.bot.send_message(chat_id,
            f"⚠️ <b>Not enough credits.</b>\n\n💰 Balance: <b>{u['credits']}</b>\n"
            f"💳 Need: <b>{cost}</b>", parse_mode=ParseMode.HTML); return
    try: await context.bot.send_chat_action(chat_id, "typing")
    except Exception: pass
    msg = await context.bot.send_message(chat_id,
        f"🔍 <b>{fancy('searching')}...</b>\n\n🆔 <code>{esc(aadhaar)}</code>",
        parse_mode=ParseMode.HTML)
    data, err = await api_call_with_fallback(AADHAAR_API_URL, AADHAAR_API_KEY, aadhaar, AADHAAR_PARAM_NAMES)
    if err:
        bump_daily_stat("api_errors")
        await safe_edit(msg, f"❌ <b>{fancy('error')}</b>\n\n<code>{esc(err[:200])}</code>",
                        parse_mode=ParseMode.HTML); return
    if not isinstance(data, dict) or not data.get("success"):
        em = data.get("credit", data.get("message", "Unknown")) if isinstance(data, dict) else "Invalid"
        await safe_edit(msg, f"❌ <b>{fancy('error')}</b>\n\n{esc(em)}",
                        parse_mode=ParseMode.HTML); return
    if not data.get("found", False):
        if chat_id not in ADMINS: consume_credit(chat_id, cost, "aadhaar")
        await safe_edit(msg, f"❌ <b>{fancy('no records found')}</b>\n\n🆔 <code>{esc(aadhaar)}</code>",
                        parse_mode=ParseMode.HTML); return
    records = data.get("result", [])
    if not isinstance(records, list): records = [records] if records else []
    records = [r for r in records if isinstance(r, dict)]
    text = build_json_response(records, "aadhaar", aadhaar, chat_id)
    if chat_id not in ADMINS: consume_credit(chat_id, cost, "aadhaar")
    add_to_history(chat_id, "aadhaar", aadhaar, True, len(records))
    bump_daily_stat("aadhaar_found"); log_action("aadhaar_search", chat_id, aadhaar[-4:])
    if len(text) <= MAX_MSG_LEN:
        await safe_edit(msg, text, parse_mode=ParseMode.HTML)
    else:
        clean_results = []
        for r in records:
            c = record_to_clean_dict(r)
            if "address" in c: c["address"] = _clean_address(c["address"])
            if c: clean_results.append(c)
        payload = {"bot": f"@{BOT_USERNAME_CACHE}", "developer": DEVELOPER_NAME,
                   "query_type": "aadhaar", "query": aadhaar,
                   "records_count": len(clean_results), "timestamp": now_ist_str(),
                   "results": clean_results}
        json_str = json.dumps(payload, indent=2, ensure_ascii=False, default=str)
        await safe_edit(msg, f"📋 <b>{fancy('json response')}</b>\n\n"
                             f"📊 Records: <b>{len(records)}</b>\n💳 Used: <b>{cost}</b>\n\n"
                             f"<i>File me JSON bhej raha hoon...</i>",
                        parse_mode=ParseMode.HTML)
        try:
            buf = io.BytesIO(json_str.encode("utf-8"))
            buf.name = f"aadhaar_{aadhaar[-4:]}_{int(time.time())}.json"
            await context.bot.send_document(chat_id=chat_id, document=buf, filename=buf.name,
                                            caption=f"📋 JSON — {len(records)} records")
        except Exception as e: print(f"[JSON file] {e}")
        await context.bot.send_message(chat_id, build_tries_footer(chat_id), parse_mode=ParseMode.HTML)

# =================================================================
#  BUY CREDITS (FAM Gateway)
# =================================================================
BUY_PACKAGES = [("₹10", 10), ("₹50", 50), ("₹100", 100), ("₹200", 200), ("₹500", 500)]

def buy_menu_text() -> str:
    rate = get_setting("credits_per_rupee", 1)
    return (f"💳 <b>{fancy('buy credits')}</b>\n━━━━━━━━━━━━━━━━━━━━━\n"
            f"💱 {fancy('rate')}: <b>₹1 = {rate} credit</b>\n"
            f"⚡ {fancy('payment')}: FamGateway (UPI)\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n\n👇 {fancy('select amount')}:")

def buy_menu_kb() -> InlineKeyboardMarkup:
    kb = []; row = []
    for label, amount in BUY_PACKAGES:
        row.append(InlineKeyboardButton(label, callback_data=f"buy_{amount}"))
        if len(row) == 3: kb.append(row); row = []
    if row: kb.append(row)
    kb.append([InlineKeyboardButton("✏️ Custom Amount", callback_data="buy_custom")])
    kb.append([InlineKeyboardButton("❌ Close", callback_data="buy_close")])
    return InlineKeyboardMarkup(kb)

async def show_buy_menu(context, chat_id, edit_msg=None):
    text = buy_menu_text(); kb = buy_menu_kb()
    if edit_msg:
        try:
            await edit_msg.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=kb); return
        except Exception: pass
    await context.bot.send_message(chat_id, text, parse_mode=ParseMode.HTML, reply_markup=kb)

async def show_fam_payment_page(context, chat_id, amount, edit_msg=None):
    if not get_setting("payment_enabled", True):
        return await context.bot.send_message(chat_id, "⚠️ Payments disabled.")
    rate = get_setting("credits_per_rupee", 1)
    credits = int(amount * rate)
    loading_text = f"⚡ <b>{fancy('creating order')}...</b>\n\n💵 ₹{amount}"
    if edit_msg:
        try:
            await edit_msg.edit_text(loading_text, parse_mode=ParseMode.HTML); msg = edit_msg
        except Exception:
            msg = await context.bot.send_message(chat_id, loading_text, parse_mode=ParseMode.HTML)
    else:
        msg = await context.bot.send_message(chat_id, loading_text, parse_mode=ParseMode.HTML)
    ok, data, err = await fam_create_order(amount, f"user_{chat_id}")
    if not ok:
        return await safe_edit(msg,
            f"❌ <b>{fancy('order failed')}</b>\n\n<code>{esc(str(err)[:300])}</code>",
            parse_mode=ParseMode.HTML)
    order_id = data["order_id"]
    qr_url = data.get("qr_url")
    checkout_url = data.get("checkout_url")
    payable = data.get("payable_amount", amount)
    pid = create_payment(chat_id, amount, credits, order_id=order_id,
                         payment_link=checkout_url, gateway_raw=data.get("raw"))
    log_action("fam_order_created", chat_id, f"{pid}|{order_id}|₹{amount}")
    text = (f"💳 <b>{fancy('payment details')}</b>\n━━━━━━━━━━━━━━━━━━━━━\n"
            f"💵 {fancy('amount')}: <b>₹{payable}</b>\n"
            f"💎 {fancy('credits')}: <b>{credits}</b>\n"
            f"🆔 {fancy('order')}: <code>{esc(order_id)}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📌 {fancy('instructions')}:\n"
            f"1️⃣ Niche <b>Pay Now</b> dabao\n2️⃣ UPI app me pay karo\n"
            f"3️⃣ Payment auto-verify hoga\n4️⃣ Credits auto-add ho jayenge\n\n"
            f"⏱ {fancy('order expires in 5 min')}")
    buttons = []
    if checkout_url and checkout_url.startswith("http"):
        buttons.append([InlineKeyboardButton("💳 Pay Now", url=checkout_url)])
    elif data.get("raw", {}).get("payment_link", "").startswith("http"):
        buttons.append([InlineKeyboardButton("💳 Pay Now", url=data["raw"]["payment_link"])])
    buttons.append([InlineKeyboardButton("🔄 Check Status", callback_data=f"famcheck_{order_id}")])
    buttons.append([InlineKeyboardButton("❌ Cancel", callback_data=f"famcancel_{order_id}")])
    kb = InlineKeyboardMarkup(buttons)
    try:
        if qr_url and qr_url.startswith("http"):
            await context.bot.send_photo(chat_id=chat_id, photo=qr_url,
                                         caption=text, parse_mode=ParseMode.HTML, reply_markup=kb)
            try: await msg.delete()
            except Exception: pass
        else:
            await safe_edit(msg, text, parse_mode=ParseMode.HTML, reply_markup=kb)
    except Exception as e:
        print(f"[QR send] {e}")
        await safe_edit(msg, text, parse_mode=ParseMode.HTML, reply_markup=kb)
    start_payment_poller(context.application, chat_id, order_id, pid)

# =================================================================
#  PAYMENT POLLER
# =================================================================
def start_payment_poller(app, chat_id, order_id, pid):
    if order_id in _active_pollers: return
    def _poll():
        try: _poll_loop(app, chat_id, order_id, pid)
        except Exception as e: print(f"[poller {order_id}] {e}")
        finally: _active_pollers.pop(order_id, None)
    t = threading.Thread(target=_poll, daemon=True, name=f"FAMPoll_{order_id}")
    _active_pollers[order_id] = t; t.start()

def _poll_loop(app, chat_id, order_id, pid):
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_poll_async(app, chat_id, order_id, pid))
    finally:
        try: loop.close()
        except Exception: pass

async def _poll_async(app, chat_id, order_id, pid):
    bot = app.bot
    for attempt in range(FAM_POLL_MAX_ATTEMPTS):
        await asyncio.sleep(FAM_POLL_INTERVAL)
        p = get_payment(pid)
        if not p or p.get("status") != "pending": return
        ok, status, err = await fam_verify_order(order_id)
        if not ok:
            print(f"[poll {order_id}] verify failed: {err}"); continue
        if status.get("is_paid"):
            new_bal = add_credits(chat_id, p["credits"])
            update_payment(pid, status="approved", processed_at=now_ist_str(),
                           processed_by="auto_fam", utr=status.get("utr"))
            log_action("payment_auto_approved", chat_id, f"{pid}|{order_id}")
            try:
                await bot.send_message(chat_id,
                    f"✅ <b>{fancy('payment received')}</b>\n\n"
                    f"💵 ₹{p['amount']}\n💎 +{p['credits']} credits\n"
                    f"💰 {fancy('new balance')}: <b>{new_bal}</b>\n"
                    f"🧾 UTR: <code>{esc(status.get('utr') or 'N/A')}</code>\n"
                    f"🆔 Order: <code>{esc(order_id)}</code>",
                    parse_mode=ParseMode.HTML)
            except Exception as e: print(f"[notify user] {e}")
            for aid in ADMINS:
                try:
                    await bot.send_message(aid,
                        f"💰 <b>Auto-paid</b>\n👤 <code>{chat_id}</code>\n"
                        f"₹{p['amount']} → {p['credits']}cr\nUTR: <code>{esc(status.get('utr') or 'N/A')}</code>",
                        parse_mode=ParseMode.HTML)
                except Exception: pass
            return
        if status.get("status") == "expired":
            update_payment(pid, status="expired", processed_at=now_ist_str())
            try:
                await bot.send_message(chat_id,
                    f"⌛ <b>{fancy('order expired')}</b>\n\n🆔 <code>{esc(order_id)}</code>\n\nUse 💳 Buy Credits to retry.",
                    parse_mode=ParseMode.HTML)
            except Exception: pass
            return
    p = get_payment(pid)
    if p and p.get("status") == "pending":
        update_payment(pid, status="timeout", processed_at=now_ist_str())
        try:
            await bot.send_message(chat_id,
                f"⌛ <b>{fancy('payment timeout')}</b>\n\n🆔 <code>{esc(order_id)}</code>",
                parse_mode=ParseMode.HTML)
        except Exception: pass

# =================================================================
#  COMMANDS
# =================================================================
async def cmd_num(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if is_banned(chat_id): return await update.message.reply_text("🚫 Banned.")
    if chat_id not in ADMINS and not await check_joined_status(context, chat_id):
        return await send_join_prompt(context, chat_id)
    if not context.args:
        return await update.message.reply_text("❌ Usage: <code>/num 9876543210</code>", parse_mode=ParseMode.HTML)
    await do_number_lookup(context, chat_id, " ".join(context.args).strip())

async def cmd_aadhar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if is_banned(chat_id): return await update.message.reply_text("🚫 Banned.")
    if chat_id not in ADMINS and not await check_joined_status(context, chat_id):
        return await send_join_prompt(context, chat_id)
    if not context.args:
        return await update.message.reply_text("❌ Usage: <code>/aadhar 123456789012</code>", parse_mode=ParseMode.HTML)
    await do_aadhaar_lookup(context, chat_id, " ".join(context.args).strip())

async def cmd_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    u = ensure_user(chat_id)
    await update.message.reply_text(
        f"💳 <b>{fancy('balance')}</b>\n━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 {fancy('credits')}: <b>{u['credits']}</b>",
        parse_mode=ParseMode.HTML)

async def cmd_myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await update.message.reply_text(f"🆔 <code>{chat_id}</code>", parse_mode=ParseMode.HTML)

async def cmd_approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id not in ADMINS: return
    if not context.args: return await update.message.reply_text("❌ /approve <payment_id>")
    pid = context.args[0]; p = get_payment(pid)
    if not p: return await update.message.reply_text("❌ Not found")
    if p.get("status") != "pending": return await update.message.reply_text("⚠️ Already processed")
    update_payment(pid, status="approved", processed_at=now_ist_str(), processed_by=chat_id)
    new_bal = add_credits(p["user_id"], p["credits"])
    try:
        await context.bot.send_message(p["user_id"],
            f"✅ <b>{fancy('payment approved')}</b>\n\n💵 ₹{p['amount']}\n💎 +{p['credits']}\n"
            f"💰 New balance: <b>{new_bal}</b>", parse_mode=ParseMode.HTML)
    except Exception: pass
    log_action("payment_approved", chat_id, pid)
    await update.message.reply_text(f"✅ Approved: {pid}")

async def cmd_reject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id not in ADMINS: return
    if not context.args: return await update.message.reply_text("❌ /reject <payment_id>")
    pid = context.args[0]; p = get_payment(pid)
    if not p: return await update.message.reply_text("❌ Not found")
    if p.get("status") != "pending": return await update.message.reply_text("⚠️ Already processed")
    update_payment(pid, status="rejected", processed_at=now_ist_str(), processed_by=chat_id)
    try:
        await context.bot.send_message(p["user_id"],
            f"❌ <b>{fancy('payment rejected')}</b>\n\nContact: {DEVELOPER_NAME}",
            parse_mode=ParseMode.HTML)
    except Exception: pass
    log_action("payment_rejected", chat_id, pid)
    await update.message.reply_text(f"❌ Rejected: {pid}")

# =================================================================
#  BUTTON HANDLER
# =================================================================
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = (update.message.text or "").strip()
    user = update.effective_user
    is_admin = chat_id in ADMINS
    if not text: return
    if is_banned(chat_id): return await update.message.reply_text("🚫 Banned.")

    if text == "🔙 Cancel":
        clear_state(chat_id)
        return await update.message.reply_text("🏠 Main Menu",
                                               reply_markup=main_menu_kb(is_admin=is_admin))

    state = get_state(chat_id)
    if state:
        handled = await handle_state_input(update, context, chat_id, text, state, is_admin)
        if handled: return

    if text in {"📱 Number Info", "🆔 Aadhaar Info", "💳 Buy Credits",
                "💰 Refer & Earn", "👤 My Account", "📜 History",
                "🆔 My ID", "ℹ️ About", "🆘 Support"}:
        if chat_id not in ADMINS:
            if not await check_joined_status(context, chat_id):
                return await send_join_prompt(context, chat_id)

    u = ensure_user(chat_id)

    if text == "📱 Number Info":
        set_state(chat_id, "awaiting_number")
        return await update.message.reply_text(
            f"📱 <b>{fancy('send phone number')}</b>\n\nExample: <code>9876543210</code>\n\n"
            f"💳 {fancy('cost')}: {get_setting('number_cost', 10)} credit",
            parse_mode=ParseMode.HTML, reply_markup=back_kb())

    if text == "🆔 Aadhaar Info":
        set_state(chat_id, "awaiting_aadhaar")
        return await update.message.reply_text(
            f"🆔 <b>{fancy('send aadhaar')}</b>\n\nExample: <code>123456789012</code>\n\n"
            f"💳 {fancy('cost')}: {get_setting('aadhaar_cost', 10)} credit",
            parse_mode=ParseMode.HTML, reply_markup=back_kb())

    if text == "💳 Buy Credits":
        if not get_setting("payment_enabled", True):
            return await update.message.reply_text("⚠️ Payments disabled.")
        await show_buy_menu(context, chat_id); return

    if text == "💰 Refer & Earn":
        ref_link = f"https://t.me/{BOT_USERNAME_CACHE}?start=ref_{chat_id}"
        bonus = get_setting("referral_bonus", 10)
        text_out = (f"💰 <b>{fancy('refer & earn')}</b>\n━━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"🔗 {fancy('your link')}:\n<code>{esc(ref_link)}</code>\n\n"
                    f"🎁 {fancy('bonus')}: <b>{bonus} credits</b>\n"
                    f"👥 {fancy('referrals')}: <b>{u.get('referrals', 0)}</b>\n"
                    f"💎 {fancy('earned')}: <b>{u.get('total_earned', 0)}</b>")
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("📋 Share",
            url=f"https://t.me/share/url?url={ref_link}")]])
        return await update.message.reply_text(text_out, parse_mode=ParseMode.HTML, reply_markup=kb)

    if text == "👤 My Account":
        text_out = (f"👤 <b>{fancy('my account')}</b>\n━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🆔 {fancy('id')}: <code>{chat_id}</code>\n"
                    f"👤 {fancy('name')}: {esc(user.first_name or 'N/A')}\n"
                    f"💰 {fancy('credits')}: <b>{u['credits']}</b>\n"
                    f"🔎 {fancy('searches')}: <b>{u['searches']}</b>\n"
                    f"📱 {fancy('number')}: {u.get('number_searches', 0)}\n"
                    f"🆔 {fancy('aadhaar')}: {u.get('aadhaar_searches', 0)}\n"
                    f"👥 {fancy('referrals')}: <b>{u.get('referrals', 0)}</b>\n"
                    f"💎 {fancy('earned')}: <b>{u.get('total_earned', 0)}</b>\n"
                    f"📅 {fancy('joined')}: {esc(u['joined_at'])}")
        return await update.message.reply_text(text_out, parse_mode=ParseMode.HTML)

    if text == "📜 History":
        hist = get_history(chat_id, 10)
        if not hist:
            return await update.message.reply_text(f"📜 <b>{fancy('no history')}</b>", parse_mode=ParseMode.HTML)
        out = f"📜 <b>{fancy('recent')}</b>\n━━━━━━━━━━━━━━━━━━━━━\n\n"
        for h in hist:
            emoji = "✅" if h.get("found") else "❌"
            typ = "📱" if h["type"] == "number" else "🆔"
            out += f"{emoji} {typ} <code>{esc(h['query'])}</code> — {esc(h['at'][11:16])}\n"
        return await update.message.reply_text(out, parse_mode=ParseMode.HTML)

    if text == "🆔 My ID":
        return await update.message.reply_text(f"🆔 <code>{chat_id}</code>", parse_mode=ParseMode.HTML)

    if text == "ℹ️ About":
        return await update.message.reply_text(
            f"ℹ️ <b>{fancy('about')}</b>\n━━━━━━━━━━━━━━━━━━━━━\n"
            f"🤖 OSINT Bot v8.2\n👨‍💻 {esc(DEVELOPER_NAME)}", parse_mode=ParseMode.HTML)

    if text == "🆘 Support":
        return await update.message.reply_text(f"🆘 {fancy('contact')}: {esc(DEVELOPER_NAME)}",
                                               parse_mode=ParseMode.HTML)

    if text == "👑 ADMIN PANEL":
        if not is_admin: return await update.message.reply_text("❌ Admin only.")
        s = get_user_stats(force=True)
        text_out = (f"👑 <b>{fancy('admin panel')}</b>\n━━━━━━━━━━━━━━━━━━━━━\n"
                    f"👥 {fancy('users')}: <b>{s['total_users']}</b>\n"
                    f"💰 {fancy('credits')}: <b>{s['total_credits']}</b>\n"
                    f"💳 {fancy('pending')}: <b>{len(get_pending_payments())}</b>\n"
                    f"🔄 {fancy('generated')}: {now_ist_pretty()}\n━━━━━━━━━━━━━━━━━━━━━\n"
                    f"Niche admin buttons use karo.")
        return await update.message.reply_text(text_out, parse_mode=ParseMode.HTML,
                                               reply_markup=admin_menu_kb())

    if not is_admin: return

    if text == "🏠 User Menu":
        return await update.message.reply_text("🏠 User Menu", reply_markup=main_menu_kb(is_admin=True))

    if text == "📊 Dashboard":
        s = get_user_stats(force=True)
        text_out = (f"📊 <b>{fancy('dashboard')}</b>\n━━━━━━━━━━━━━━━━━━━━━\n"
                    f"👥 Users: <b>{s['total_users']}</b>\n✅ Active: <b>{s['active']}</b>\n"
                    f"🚫 Banned: <b>{s['banned']}</b>\n🔥 24h: <b>{s['active_24h']}</b>\n"
                    f"💰 Credits: <b>{s['total_credits']}</b>\n🔎 Searches: <b>{s['total_searches']}</b>\n"
                    f"📱 Number: <b>{s['number_searches']}</b>\n🆔 Aadhaar: <b>{s['aadhaar_searches']}</b>\n"
                    f"👥 Refs: <b>{s['total_referrals']}</b>\n━━━━━━━━━━━━━━━━━━━━━\n"
                    f"💳 Pending Payments: <b>{len(get_pending_payments())}</b>\n🔄 {now_ist_pretty()}")
        return await update.message.reply_text(text_out, parse_mode=ParseMode.HTML)

    if text == "👥 User List":
        if not USERS: return await update.message.reply_text("No users.")
        top = sorted(USERS.items(), key=lambda x: int(x[1].get("credits", 0)), reverse=True)[:30]
        out = f"👥 <b>{fancy('top users')}</b>\n━━━━━━━━━━━━━━━━━━━━━\n\n"
        for uid, info in top:
            out += f"<code>{esc(uid)}</code> — 💰{info.get('credits', 0)}"
            if info.get("banned"): out += " 🚫"
            out += "\n"
        return await update.message.reply_text(out, parse_mode=ParseMode.HTML)

    if text == "💳 Add Credits":
        set_state(chat_id, "user_addcr")
        return await update.message.reply_text(
            f"💳 <b>{fancy('add credits')}</b>\n\nSend: <code>user_id amount</code>\n"
            f"Example: <code>123456789 100</code>", parse_mode=ParseMode.HTML, reply_markup=back_kb())

    if text == "➖ Sub Credits":
        set_state(chat_id, "user_remcr")
        return await update.message.reply_text(
            f"➖ <b>{fancy('sub credits')}</b>\n\nSend: <code>user_id amount</code>",
            parse_mode=ParseMode.HTML, reply_markup=back_kb())

    if text == "💰 Set Balance":
        set_state(chat_id, "user_setcr")
        return await update.message.reply_text(
            f"💰 <b>{fancy('set balance')}</b>\n\nSend: <code>user_id balance</code>",
            parse_mode=ParseMode.HTML, reply_markup=back_kb())

    if text == "📋 User Info":
        set_state(chat_id, "search_user")
        return await update.message.reply_text(
            f"📋 <b>{fancy('user info')}</b>\n\nSend user_id:",
            parse_mode=ParseMode.HTML, reply_markup=back_kb())

    if text == "📢 Broadcast":
        set_state(chat_id, "broadcast")
        return await update.message.reply_text("📢 Send broadcast message:", reply_markup=back_kb())

    if text == "🎁 Bonus All":
        set_state(chat_id, "bonus_all")
        return await update.message.reply_text("🎁 Send amount to add to ALL users:", reply_markup=back_kb())

    if text == "🚫 Ban User":
        set_state(chat_id, "ban_user")
        return await update.message.reply_text("🚫 Send user_id to ban:", reply_markup=back_kb())

    if text == "✅ Unban User":
        set_state(chat_id, "unban_user")
        return await update.message.reply_text("✅ Send user_id to unban:", reply_markup=back_kb())

    if text == "💳 Pending Payments":
        pending = get_pending_payments()
        if not pending: return await update.message.reply_text("✅ No pending payments.")
        out = f"💳 <b>{fancy('pending payments')}</b>\n━━━━━━━━━━━━━━━━━━━━━\n\n"
        for p in pending[:15]:
            out += (f"🆔 <code>{esc(p['id'])}</code>\n"
                    f"  👤 User: <code>{p['user_id']}</code>\n"
                    f"  💵 ₹{p['amount']} → 💎 {p['credits']}\n"
                    f"  🔗 Order: <code>{esc(p.get('order_id') or 'N/A')}</code>\n"
                    f"  🕒 {esc(p['created_at'])}\n\n")
        out += f"\n📌 Use /approve or /reject <id>"
        return await update.message.reply_text(out, parse_mode=ParseMode.HTML)

    if text == "📊 Payment Stats":
        total = len(PAYMENTS)
        approved = sum(1 for p in PAYMENTS if p.get("status") == "approved")
        rejected = sum(1 for p in PAYMENTS if p.get("status") == "rejected")
        pending = sum(1 for p in PAYMENTS if p.get("status") == "pending")
        revenue = sum(p["amount"] for p in PAYMENTS if p.get("status") == "approved")
        credits_sold = sum(p["credits"] for p in PAYMENTS if p.get("status") == "approved")
        text_out = (f"📊 <b>{fancy('payment stats')}</b>\n━━━━━━━━━━━━━━━━━━━━━\n"
                    f"📋 Total: <b>{total}</b>\n✅ Approved: <b>{approved}</b>\n"
                    f"❌ Rejected: <b>{rejected}</b>\n⏳ Pending: <b>{pending}</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━\n💰 Revenue: <b>₹{revenue}</b>\n"
                    f"💎 Credits sold: <b>{credits_sold}</b>")
        return await update.message.reply_text(text_out, parse_mode=ParseMode.HTML)

    if text == "📤 Export Users":
        buf = io.BytesIO(json.dumps(USERS, indent=2, default=str).encode())
        buf.name = f"users_{int(time.time())}.json"
        return await update.message.reply_document(buf, filename=buf.name)

    if text == "📥 Backup":
        data = {"users": USERS, "settings": SETTINGS, "stats": STATS,
                "payments": PAYMENTS, "exported_at": now_ist_str()}
        buf = io.BytesIO(json.dumps(data, indent=2, default=str).encode())
        buf.name = f"backup_{int(time.time())}.json"
        return await update.message.reply_document(buf, filename=buf.name)

    if text == "⚙️ Settings":
        rate = get_setting("credits_per_rupee", 1)
        text_out = (f"⚙️ <b>{fancy('settings')}</b>\n━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🎁 Referral: <b>{get_setting('referral_bonus')}</b>\n"
                    f"💳 Welcome: <b>{get_setting('welcome_credits')}</b>\n"
                    f"📱 Number cost: <b>{get_setting('number_cost')}</b>\n"
                    f"🆔 Aadhaar cost: <b>{get_setting('aadhaar_cost')}</b>\n"
                    f"💱 Rate: <b>₹1 = {rate} credit</b>\n━━━━━━━━━━━━━━━━━━━━━")
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🎁 Referral Bonus", callback_data="set_referral_bonus")],
            [InlineKeyboardButton("💳 Welcome Credits", callback_data="set_welcome_credits")],
            [InlineKeyboardButton("📱 Number Cost", callback_data="set_number_cost")],
            [InlineKeyboardButton("🆔 Aadhaar Cost", callback_data="set_aadhaar_cost")],
            [InlineKeyboardButton("💱 Credits/Rupee", callback_data="set_credits_per_rupee")],
            [InlineKeyboardButton(f"📢 Force Join: {'ON' if get_setting('force_join_enabled') else 'OFF'}",
                                  callback_data="toggle_fj")],
            [InlineKeyboardButton(f"🔧 Maintenance: {'ON' if get_setting('maintenance_mode') else 'OFF'}",
                                  callback_data="toggle_mm")],
            [InlineKeyboardButton(f"💳 Payments: {'ON' if get_setting('payment_enabled') else 'OFF'}",
                                  callback_data="toggle_pay")],
        ])
        return await update.message.reply_text(text_out, parse_mode=ParseMode.HTML, reply_markup=kb)

    if text == "📝 Logs":
        logs = get_recent_logs(20)
        out = f"📝 <b>{fancy('logs')}</b>\n━━━━━━━━━━━━━━━━━━━━━\n\n"
        for log in logs:
            out += f"<code>{esc(log['at'][11:19])}</code> | {esc(log['action'])}\n"
        return await update.message.reply_text(out[:4000], parse_mode=ParseMode.HTML)

# =================================================================
#  STATE INPUT
# =================================================================
async def handle_state_input(update, context, chat_id, text, state, is_admin):
    if state == "awaiting_number":
        clear_state(chat_id)
        await update.message.reply_text("🏠 Main Menu", reply_markup=main_menu_kb(is_admin=is_admin))
        await do_number_lookup(context, chat_id, text); return True

    if state == "awaiting_aadhaar":
        clear_state(chat_id)
        await update.message.reply_text("🏠 Main Menu", reply_markup=main_menu_kb(is_admin=is_admin))
        await do_aadhaar_lookup(context, chat_id, text); return True

    if state == "buy_custom_amount":
        clear_state(chat_id)
        try:
            amount = int(text.strip())
            if amount < 10 or amount > 10000: raise ValueError
        except Exception:
            await update.message.reply_text("❌ Invalid. Range: ₹10 - ₹10000\nMenu se try karo.",
                                            reply_markup=main_menu_kb(is_admin=is_admin))
            return True
        await show_fam_payment_page(context, chat_id, amount); return True

    if not is_admin: return False

    if state == "user_addcr":
        clear_state(chat_id)
        await update.message.reply_text("🏠 Admin", reply_markup=admin_menu_kb())
        try:
            parts = text.split()
            if len(parts) < 2: raise ValueError
            tid = parts[0]; amt = int(parts[1])
            if amt <= 0: raise ValueError
        except Exception:
            return await update.message.reply_text("❌ Usage: <code>user_id amount</code>", parse_mode=ParseMode.HTML)
        new_bal = add_credits(tid, amt)
        log_action("add_credits", chat_id, f"{tid}:{amt}")
        await update.message.reply_text(f"✅ +{amt} to <code>{esc(tid)}</code>\nNew: <b>{new_bal}</b>",
                                        parse_mode=ParseMode.HTML); return True

    if state == "user_remcr":
        clear_state(chat_id)
        await update.message.reply_text("🏠 Admin", reply_markup=admin_menu_kb())
        try:
            parts = text.split()
            if len(parts) < 2: raise ValueError
            tid = parts[0]; amt = int(parts[1])
            if amt <= 0: raise ValueError
        except Exception:
            return await update.message.reply_text("❌ Usage: <code>user_id amount</code>", parse_mode=ParseMode.HTML)
        if tid not in USERS: return await update.message.reply_text("❌ User not found")
        USERS[tid]["credits"] = max(0, int(USERS[tid].get("credits", 0)) - amt)
        save_users(); log_action("sub_credits", chat_id, f"{tid}:{amt}")
        await update.message.reply_text(f"✅ -{amt} from <code>{esc(tid)}</code>\nNew: <b>{USERS[tid]['credits']}</b>",
                                        parse_mode=ParseMode.HTML); return True

    if state == "user_setcr":
        clear_state(chat_id)
        await update.message.reply_text("🏠 Admin", reply_markup=admin_menu_kb())
        try:
            parts = text.split()
            if len(parts) < 2: raise ValueError
            tid = parts[0]; bal = int(parts[1])
            if bal < 0: raise ValueError
        except Exception:
            return await update.message.reply_text("❌ Usage: <code>user_id balance</code>", parse_mode=ParseMode.HTML)
        ensure_user(tid); USERS[tid]["credits"] = bal; save_users()
        log_action("set_credits", chat_id, f"{tid}:{bal}")
        await update.message.reply_text(f"✅ <code>{esc(tid)}</code> = <b>{bal}</b>",
                                        parse_mode=ParseMode.HTML); return True

    if state == "search_user":
        clear_state(chat_id)
        await update.message.reply_text("🏠 Admin", reply_markup=admin_menu_kb())
        t = text.strip()
        if t not in USERS: return await update.message.reply_text("❌ User not found")
        info = USERS[t]
        out = (f"👤 <b>{fancy('user info')}</b>\n━━━━━━━━━━━━━━━━━━━━━\n"
               f"🆔 <code>{esc(t)}</code>\n💰 Credits: <b>{info.get('credits', 0)}</b>\n"
               f"🔎 Searches: <b>{info.get('searches', 0)}</b>\n"
               f"📱 Number: {info.get('number_searches', 0)}\n"
               f"🆔 Aadhaar: {info.get('aadhaar_searches', 0)}\n"
               f"👥 Refs: {info.get('referrals', 0)}\n"
               f"💎 Earned: {info.get('total_earned', 0)}\n"
               f"🚫 Banned: {'Yes' if info.get('banned') else 'No'}\n"
               f"📅 Joined: {esc(info.get('joined_at', '?'))}")
        return await update.message.reply_text(out, parse_mode=ParseMode.HTML)

    if state == "broadcast":
        clear_state(chat_id)
        await update.message.reply_text("🏠 Admin", reply_markup=admin_menu_kb())
        msg = await update.message.reply_text("📢 Broadcasting...")
        ok, fail = 0, 0
        for uid in list(USERS.keys()):
            try:
                await context.bot.send_message(int(uid),
                    f"📢 <b>{fancy('broadcast')}</b>\n\n{esc(text)}", parse_mode=ParseMode.HTML)
                ok += 1
            except Exception: fail += 1
            await asyncio.sleep(0.05)
        log_action("broadcast", chat_id, f"{ok} ok / {fail} fail")
        return await msg.edit_text(f"✅ <b>{fancy('broadcast complete')}</b>\n\n✅ Sent: {ok}\n❌ Failed: {fail}",
                                   parse_mode=ParseMode.HTML)

    if state == "bonus_all":
        clear_state(chat_id)
        await update.message.reply_text("🏠 Admin", reply_markup=admin_menu_kb())
        try:
            amt = int(text.strip())
            if amt <= 0: raise ValueError
        except Exception:
            return await update.message.reply_text("❌ Invalid amount")
        cnt = 0
        for uid in USERS:
            USERS[uid]["credits"] = int(USERS[uid].get("credits", 0)) + amt
            cnt += 1
        save_users(); log_action("bonus_all", chat_id, f"+{amt} x {cnt}")
        return await update.message.reply_text(f"✅ Added <b>{amt}</b> to <b>{cnt}</b> users",
                                               parse_mode=ParseMode.HTML)

    if state == "ban_user":
        clear_state(chat_id)
        await update.message.reply_text("🏠 Admin", reply_markup=admin_menu_kb())
        try: tid = int(text.strip())
        except Exception: return await update.message.reply_text("❌ Invalid ID")
        if tid in ADMINS: return await update.message.reply_text("❌ Can't ban admin")
        ban_user(tid, "Manual"); log_action("ban", chat_id, str(tid))
        return await update.message.reply_text(f"✅ Banned <code>{tid}</code>", parse_mode=ParseMode.HTML)

    if state == "unban_user":
        clear_state(chat_id)
        await update.message.reply_text("🏠 Admin", reply_markup=admin_menu_kb())
        try: tid = int(text.strip())
        except Exception: return await update.message.reply_text("❌ Invalid ID")
        unban_user(tid)
        return await update.message.reply_text(f"✅ Unbanned <code>{tid}</code>", parse_mode=ParseMode.HTML)

    if state.startswith("set_"):
        key = state[4:]
        clear_state(chat_id)
        await update.message.reply_text("🏠 Admin", reply_markup=admin_menu_kb())
        try:
            val = int(text.strip())
            if val < 0: raise ValueError
        except Exception:
            return await update.message.reply_text("❌ Invalid number")
        set_setting(key, val)
        return await update.message.reply_text(f"✅ <code>{esc(key)}</code> = <b>{val}</b>",
                                               parse_mode=ParseMode.HTML)

    return False

# =================================================================
#  CALLBACK HANDLER
# =================================================================
async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    chat_id = q.from_user.id
    d = q.data
    is_admin = chat_id in ADMINS

    if d == "check_join":
        try: await q.answer()
        except Exception: pass
        return

    try: await q.answer()
    except Exception: pass

    if d == "buy_close":
        try: await q.message.delete()
        except Exception: pass
        return

    if d == "buy_back":
        await show_buy_menu(context, chat_id, edit_msg=q.message); return

    if d == "buy_custom":
        set_state(chat_id, "buy_custom_amount")
        try:
            await q.edit_text(f"✏️ <b>{fancy('custom amount')}</b>\n\nSend amount in ₹ (10 - 10000):",
                              parse_mode=ParseMode.HTML)
        except Exception: pass
        return

    if d.startswith("buy_"):
        try: amount = int(d.split("_", 1)[1])
        except Exception: return
        await show_fam_payment_page(context, chat_id, amount, edit_msg=q.message); return

    if d.startswith("famcheck_"):
        order_id = d.replace("famcheck_", "")
        p = get_payment_by_order(order_id)
        if not p: return await q.answer("❌ Order not found", show_alert=True)
        if p.get("status") == "approved":
            return await q.answer("✅ Already approved!", show_alert=True)
        ok, status, err = await fam_verify_order(order_id)
        if not ok:
            return await q.answer(f"⚠️ Verify error. Try again in 5s.", show_alert=True)
        if status.get("is_paid"):
            new_bal = add_credits(chat_id, p["credits"])
            update_payment(p["id"], status="approved", processed_at=now_ist_str(),
                           processed_by="auto_fam", utr=status.get("utr"))
            log_action("payment_auto_approved", chat_id, f"{p['id']}|{order_id}")
            try:
                await q.edit_message_caption(
                    caption=(q.message.caption or "") + f"\n\n✅ <b>PAID +{p['credits']} cr</b>",
                    parse_mode=ParseMode.HTML, reply_markup=None)
            except Exception:
                try:
                    await q.edit_message_text(f"✅ <b>Paid!</b>\n💎 +{p['credits']} credits\n💰 Balance: <b>{new_bal}</b>",
                                              parse_mode=ParseMode.HTML)
                except Exception: pass
            await q.answer("✅ Payment verified!", show_alert=True)
        elif status.get("status") == "expired":
            update_payment(p["id"], status="expired", processed_at=now_ist_str())
            await q.answer("⌛ Order expired.", show_alert=True)
        else:
            await q.answer("⏳ Payment pending. Complete UPI payment first.", show_alert=True)
        return

    if d.startswith("famcancel_"):
        order_id = d.replace("famcancel_", "")
        p = get_payment_by_order(order_id)
        if p and p.get("status") == "pending":
            update_payment(p["id"], status="cancelled", processed_at=now_ist_str())
        await q.answer("❌ Cancelled")
        try: await q.message.delete()
        except Exception: pass
        return

    if d.startswith("pay_approve_"):
        if not is_admin: return
        pid = d.replace("pay_approve_", "")
        p = get_payment(pid)
        if not p or p.get("status") != "pending": return
        update_payment(pid, status="approved", processed_at=now_ist_str(), processed_by=chat_id)
        new_bal = add_credits(p["user_id"], p["credits"])
        try:
            await context.bot.send_message(p["user_id"],
                f"✅ <b>Payment approved</b>\n💵 ₹{p['amount']}\n💎 +{p['credits']}\n"
                f"💰 Balance: <b>{new_bal}</b>", parse_mode=ParseMode.HTML)
        except Exception: pass
        log_action("payment_approved", chat_id, pid)
        return

    if d.startswith("pay_reject_"):
        if not is_admin: return
        pid = d.replace("pay_reject_", "")
        p = get_payment(pid)
        if not p or p.get("status") != "pending": return
        update_payment(pid, status="rejected", processed_at=now_ist_str(), processed_by=chat_id)
        try:
            await context.bot.send_message(p["user_id"],
                f"❌ <b>Payment rejected</b>\n\nContact: {DEVELOPER_NAME}", parse_mode=ParseMode.HTML)
        except Exception: pass
        log_action("payment_rejected", chat_id, pid)
        return

    if not is_admin: return

    if d == "toggle_fj":
        cur = get_setting("force_join_enabled", True); set_setting("force_join_enabled", not cur); return
    if d == "toggle_mm":
        cur = get_setting("maintenance_mode", False); set_setting("maintenance_mode", not cur); return
    if d == "toggle_pay":
        cur = get_setting("payment_enabled", True); set_setting("payment_enabled", not cur); return

    if d.startswith("set_"):
        key = d[4:]
        set_state(chat_id, f"set_{key}")
        prompts = {
            "referral_bonus": "🎁 Send new referral bonus:",
            "welcome_credits": "💳 Send new welcome credits:",
            "number_cost": "📱 Send new number cost:",
            "aadhaar_cost": "🆔 Send new aadhaar cost:",
            "credits_per_rupee": "💱 Send credits per ₹1:",
        }
        await context.bot.send_message(chat_id, prompts.get(key, f"Send value for {key}:"),
                                       reply_markup=back_kb())
        return

# =================================================================
#  ERROR HANDLER
# =================================================================
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    print(f"[ERROR] {context.error}")
    traceback.print_exception(type(context.error), context.error, context.error.__traceback__)

# =================================================================
#  POST INIT / SHUTDOWN
# =================================================================
async def post_init(app: Application):
    global BOT_USERNAME_CACHE
    try:
        me = await app.bot.get_me()
        BOT_USERNAME_CACHE = me.username or "bot"
        print(f"✅ Bot: @{BOT_USERNAME_CACHE}")
    except Exception as e:
        print(f"[INIT] {e}")
    cmds = [
        BotCommand("start", "🏠 Main Menu"),
        BotCommand("num", "📱 Number Lookup"),
        BotCommand("aadhar", "🆔 Aadhaar Lookup"),
        BotCommand("balance", "💳 Balance"),
        BotCommand("myid", "🆔 My ID"),
    ]
    try: await app.bot.set_my_commands(cmds)
    except Exception: pass

async def post_shutdown(app: Application):
    flush_all_pending()

# =================================================================
#  MAIN
# =================================================================
def main():
    if not BOT_TOKEN:
        raise SystemExit("❌ BOT_TOKEN missing in .env")
    app = (Application.builder()
           .token(BOT_TOKEN)
           .post_init(post_init)
           .post_shutdown(post_shutdown)
           .build())
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("num", cmd_num))
    app.add_handler(CommandHandler("aadhar", cmd_aadhar))
    app.add_handler(CommandHandler("balance", cmd_balance))
    app.add_handler(CommandHandler("myid", cmd_myid))
    app.add_handler(CommandHandler("approve", cmd_approve))
    app.add_handler(CommandHandler("reject", cmd_reject))
    app.add_handler(CallbackQueryHandler(verify_cb, pattern="^check_join$"))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, button_handler))
    app.add_error_handler(error_handler)
    print("🤖 Bot starting...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
