"""
Стирка Ковров Донецк — Telegram-бот (финальная версия)
=======================================================
aiogram 3.x · SQLite (aiosqlite) · APScheduler

Функции: расчёт стоимости, заказ вывоза, отслеживание статуса,
отзывы с фото, FAQ, контакты, подписка, рефералы, B2B, админ-панель.
Запуск:  python main.py
"""

import asyncio
import csv
import hashlib
import io
import logging
import os
import re
from datetime import date, timedelta

import aiosqlite
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    BufferedInputFile, CallbackQuery, InlineKeyboardButton,
    InlineKeyboardMarkup, KeyboardButton, Message,
    ReplyKeyboardMarkup, ReplyKeyboardRemove,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(message)s")
logger = logging.getLogger("carpetbot")

BOT_TOKEN        = os.getenv("BOT_TOKEN")
INITIAL_ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x}
DB_PATH          = os.getenv("DB_PATH", "carpet.db")

COMPANY_NAME     = os.getenv("COMPANY_NAME",     "Стирка Ковров Донецк")
COMPANY_PHONE    = os.getenv("COMPANY_PHONE",    "+79780505589")
COMPANY_WHATSAPP = os.getenv("COMPANY_WHATSAPP", "https://wa.me/79780505589")
COMPANY_SITE     = os.getenv("COMPANY_SITE",     "")
COMPANY_ADDRESS  = os.getenv("COMPANY_ADDRESS",  "Донецк")
COMPANY_MAP      = os.getenv("COMPANY_MAP",      "https://yandex.ru/maps/")

REMINDER_HOUR    = int(os.getenv("REMINDER_HOUR",   "10"))
REMINDER_MINUTE  = int(os.getenv("REMINDER_MINUTE", "0"))

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не задан в .env")

# ───────────────────────────── Прайс ─────────────────────────────
# Цены по умолчанию (при первом запуске). Дальше берутся из базы и
# могут меняться администратором — см. db_get_prices / db_set_price.
DEFAULT_PRICES = {
    "standard": 280,
    "longpile": 310,
    "wool":     350,
}
CARPET_TYPES = {
    "standard": {"name": "😊 Обычный ковёр",      "price": 280},
    "longpile": {"name": "🦁 Длинный ворс",        "price": 310},
    "wool":     {"name": "🧵 Шерстяной/дорогой",   "price": 350},
}

EXTRA_SERVICES = {
    "pets":    {"name": "🐱 Удаление пятен от животных", "type": "flat",    "value": 300},
    "smell":   {"name": "🦠 Удаление запаха",             "type": "flat",    "value": 250},
    "express": {"name": "⚡ Экспресс-стирка (6 часов)",   "type": "percent", "value": 30},
}

MIN_ORDER       = 2000   # минимальная сумма заказа
MIN_FREE_PICKUP = 2000   # бесплатная доставка от этой суммы
PAID_PICKUP_FEE = 500    # доставка в обе стороны если меньше минимума

STATUS_FLOW = ["new","confirmed","picked_up","at_cleaning","drying","ready","delivered","cancelled"]
STATUS_LABELS = {
    "new":         "🆕 Ожидает подтверждения",
    "confirmed":   "✅ Подтверждён",
    "picked_up":   "🚚 Ковёр забран",
    "at_cleaning": "✂️ В стирке",
    "drying":      "💨 Сушка",
    "ready":       "📦 Готов к доставке",
    "delivered":   "🎉 Доставлен",
    "cancelled":   "❌ Отменён",
}

PROMO_CODES        = {"CLEAN10": 10, "FRIEND15": 15, "WINTER20": 20}
REFERRAL_DISCOUNT  = 15
SUBSCRIPTION_PRICE = 5999

# ═══════════════════════════ База данных ═══════════════════════════
SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    tg_user_id    INTEGER PRIMARY KEY,
    username      TEXT    DEFAULT '',
    full_name     TEXT    DEFAULT '',
    phone         TEXT    DEFAULT '',
    referred_by   INTEGER DEFAULT NULL,
    bonus         INTEGER DEFAULT 0,
    is_subscriber INTEGER DEFAULT 0,
    created_at    TEXT    DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS admins (
    tg_user_id  INTEGER PRIMARY KEY,
    added_at    TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS orders (
    order_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    tg_user_id   INTEGER NOT NULL,
    carpet_type  TEXT    DEFAULT '',
    area         REAL    DEFAULT 0,
    extras       TEXT    DEFAULT '',
    price        INTEGER DEFAULT 0,
    address      TEXT    DEFAULT '',
    phone        TEXT    DEFAULT '',
    pickup_date  TEXT    DEFAULT '',
    comment      TEXT    DEFAULT '',
    status       TEXT    DEFAULT 'new',
    promo        TEXT    DEFAULT '',
    reminder_date TEXT   DEFAULT '',
    created_at   TEXT    DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS status_history (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL,
    status   TEXT    NOT NULL,
    ts       TEXT    DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS reviews (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    tg_user_id INTEGER NOT NULL,
    name       TEXT    DEFAULT '',
    stars      INTEGER DEFAULT 5,
    text       TEXT    DEFAULT '',
    photo_id   TEXT    DEFAULT '',
    approved   INTEGER DEFAULT 0,
    created_at TEXT    DEFAULT (datetime('now'))
);
"""

async def db_init():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(SCHEMA)
        # Переносим начальных админов из .env в таблицу
        for uid in INITIAL_ADMIN_IDS:
            await db.execute("INSERT OR IGNORE INTO admins (tg_user_id) VALUES (?)", (uid,))
        await db.commit()
    logger.info("База данных готова: %s", DB_PATH)

# ── Настройки (пароль) ────────────────────────────────────────────
async def db_get_setting(key: str) -> str:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT value FROM settings WHERE key=?", (key,)) as cur:
            row = await cur.fetchone()
    return row[0] if row else ""

async def db_set_setting(key: str, value: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", (key, value))
        await db.commit()

# ── Цены ──────────────────────────────────────────────────────────
async def db_load_prices():
    """Загружает цены из базы в CARPET_TYPES. При первом запуске
    записывает в базу значения по умолчанию."""
    for key, default in DEFAULT_PRICES.items():
        stored = await db_get_setting(f"price_{key}")
        if stored and stored.isdigit():
            CARPET_TYPES[key]["price"] = int(stored)
        else:
            await db_set_setting(f"price_{key}", str(default))
            CARPET_TYPES[key]["price"] = default

async def db_set_price(key: str, value: int):
    await db_set_setting(f"price_{key}", str(value))
    if key in CARPET_TYPES:
        CARPET_TYPES[key]["price"] = value

# ── Админы ────────────────────────────────────────────────────────
async def db_is_admin(uid: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT 1 FROM admins WHERE tg_user_id=?", (uid,)) as cur:
            return bool(await cur.fetchone())

async def db_add_admin(uid: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO admins (tg_user_id) VALUES (?)", (uid,))
        await db.commit()

async def db_all_admins() -> list[int]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT tg_user_id FROM admins") as cur:
            rows = await cur.fetchall()
    return [r[0] for r in rows]

# ── Пользователи ──────────────────────────────────────────────────
async def db_upsert_user(tg_user_id, username, full_name, referred_by=None):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT tg_user_id FROM users WHERE tg_user_id=?", (tg_user_id,)) as cur:
            exists = await cur.fetchone()
        if exists:
            await db.execute("UPDATE users SET username=?,full_name=? WHERE tg_user_id=?",
                             (username, full_name, tg_user_id))
        else:
            await db.execute("INSERT INTO users (tg_user_id,username,full_name,referred_by) VALUES (?,?,?,?)",
                             (tg_user_id, username, full_name, referred_by))
        await db.commit()

async def db_get_user(tg_user_id) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE tg_user_id=?", (tg_user_id,)) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None

async def db_set_user_field(tg_user_id, field, value):
    allowed = {"phone","bonus","is_subscriber","referred_by"}
    if field not in allowed:
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"UPDATE users SET {field}=? WHERE tg_user_id=?", (value, tg_user_id))
        await db.commit()

async def db_add_bonus(tg_user_id, amount):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET bonus=bonus+? WHERE tg_user_id=?", (amount, tg_user_id))
        await db.commit()

# ── Заказы ────────────────────────────────────────────────────────
async def db_create_order(order: dict) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("""
            INSERT INTO orders (tg_user_id,carpet_type,area,extras,price,address,phone,pickup_date,comment,status,promo)
            VALUES (:tg_user_id,:carpet_type,:area,:extras,:price,:address,:phone,:pickup_date,:comment,'new',:promo)
        """, order)
        order_id = cur.lastrowid
        await db.execute("INSERT INTO status_history (order_id,status) VALUES (?,'new')", (order_id,))
        await db.commit()
    return order_id

async def db_get_order(order_id) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM orders WHERE order_id=?", (order_id,)) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None

async def db_user_orders(tg_user_id, active_only=False) -> list[dict]:
    q = "SELECT * FROM orders WHERE tg_user_id=?"
    if active_only:
        q += " AND status NOT IN ('delivered','cancelled')"
    q += " ORDER BY order_id DESC"
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(q, (tg_user_id,)) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]

async def db_all_orders() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM orders ORDER BY order_id DESC") as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]

async def db_update_status(order_id, status):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE orders SET status=? WHERE order_id=?", (status, order_id))
        await db.execute("INSERT INTO status_history (order_id,status) VALUES (?,?)", (order_id, status))
        await db.commit()

async def db_set_order_field(order_id, field, value):
    allowed = {"reminder_date","phone","address","pickup_date"}
    if field not in allowed:
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"UPDATE orders SET {field}=? WHERE order_id=?", (value, order_id))
        await db.commit()

async def db_order_history(order_id) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM status_history WHERE order_id=? ORDER BY id", (order_id,)) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]

async def db_due_reminders(today_iso) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM orders WHERE reminder_date=?", (today_iso,)) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]

# ── Отзывы ────────────────────────────────────────────────────────
async def db_add_review(review: dict) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO reviews (tg_user_id,name,stars,text,photo_id) VALUES (:tg_user_id,:name,:stars,:text,:photo_id)",
            review)
        await db.commit()
        return cur.lastrowid

async def db_approved_reviews() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM reviews WHERE approved=1 ORDER BY id DESC") as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]

async def db_pending_reviews() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM reviews WHERE approved=0 ORDER BY id") as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]

async def db_approve_review(review_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE reviews SET approved=1 WHERE id=?", (review_id,))
        await db.commit()

async def db_delete_review(review_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM reviews WHERE id=?", (review_id,))
        await db.commit()

# ── Статистика ────────────────────────────────────────────────────
async def db_stats() -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        async def scalar(q, args=()):
            async with db.execute(q, args) as c:
                r = await c.fetchone()
                return r[0] if r else 0
        return {
            "active":        await scalar("SELECT COUNT(*) FROM orders WHERE status NOT IN ('delivered','cancelled')"),
            "today_new":     await scalar("SELECT COUNT(*) FROM orders WHERE date(created_at)=date('now')"),
            "pending":       await scalar("SELECT COUNT(*) FROM orders WHERE status='new'"),
            "revenue_today": await scalar("SELECT COALESCE(SUM(price),0) FROM orders WHERE date(created_at)=date('now') AND status!='cancelled'"),
            "total_users":   await scalar("SELECT COUNT(*) FROM users"),
            "total_orders":  await scalar("SELECT COUNT(*) FROM orders"),
            "revenue_total": await scalar("SELECT COALESCE(SUM(price),0) FROM orders WHERE status='delivered'"),
        }

# ═══════════════════════════ Бот и диспетчер ═══════════════════════════
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp  = Dispatcher(storage=MemoryStorage())

def hash_password(pw: str) -> str:
    return hashlib.sha256(pw.strip().encode()).hexdigest()

# ═══════════════════════════ FSM-состояния ═══════════════════════════
class CalcStates(StatesGroup):
    carpet_type = State()
    area        = State()
    extras      = State()

class PickupStates(StatesGroup):
    address = State()
    phone   = State()
    date    = State()
    comment = State()

class ReviewStates(StatesGroup):
    stars = State()
    name  = State()
    text  = State()
    photo = State()

class AdminAuth(StatesGroup):
    set_password    = State()
    enter_password  = State()
    add_admin_id    = State()

class AdminStates(StatesGroup):
    broadcast      = State()
    set_status_id  = State()
    set_price      = State()

class B2BStates(StatesGroup):
    contact = State()

# ═══════════════════════════ Вспомогательные функции ═══════════════════════════
PHONE_RE = re.compile(r"^\+?\d[\d\s\-()]{8,15}\d$")

def normalise_phone(raw: str) -> str | None:
    raw = (raw or "").strip()
    if not PHONE_RE.match(raw):
        return None
    return re.sub(r"[^\d+]", "", raw)

def parse_area(raw: str) -> float | None:
    raw = (raw or "").strip().lower().replace(",", ".")
    m = re.match(r"^([\d.]+)\s*[x×х*]\s*([\d.]+)$", raw)
    if m:
        try:
            return round(float(m.group(1)) * float(m.group(2)), 2)
        except ValueError:
            return None
    m = re.match(r"^([\d.]+)$", raw)
    if m:
        try:
            v = float(m.group(1))
            return v if 0 < v < 1000 else None
        except ValueError:
            return None
    return None

def calc_price(carpet_type, area, extras) -> dict:
    base_rate    = CARPET_TYPES.get(carpet_type, {}).get("price", 0)
    base         = base_rate * area
    flat_extra   = 0
    percent_extra = 0
    for e in extras:
        svc = EXTRA_SERVICES.get(e)
        if not svc:
            continue
        if svc["type"] == "flat":
            flat_extra += svc["value"]
        else:
            percent_extra += svc["value"]
    subtotal    = base + flat_extra
    percent_sum = subtotal * percent_extra / 100
    total       = round(subtotal + percent_sum)
    return {
        "base": round(base), "flat_extra": flat_extra,
        "percent": percent_extra, "percent_sum": round(percent_sum),
        "total": max(total, 0),
    }

def extras_names(extras) -> str:
    if not extras:
        return "нет"
    return ", ".join(EXTRA_SERVICES[e]["name"] for e in extras if e in EXTRA_SERVICES)

async def safe_delete(message: Message):
    try:
        await message.delete()
    except Exception:
        pass

async def edit_or_answer(message: Message, text: str, reply_markup=None):
    """Редактирует сообщение или отправляет новое."""
    try:
        await message.edit_text(text, reply_markup=reply_markup)
    except Exception:
        await message.answer(text, reply_markup=reply_markup)

# ═══════════════════════════ Клавиатуры — клиент ═══════════════════════════
def main_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🧺 Рассчитать стоимость стирки", callback_data="calc")],
        [InlineKeyboardButton(text="📍 Заказать вывоз ковра",        callback_data="pickup")],
        [InlineKeyboardButton(text="📦 Мои заказы",                  callback_data="my_orders")],
        [InlineKeyboardButton(text="⭐ Отзывы и примеры работ",      callback_data="reviews")],
        [InlineKeyboardButton(text="🎁 Акции и бонусы",              callback_data="promo_menu")],
        [InlineKeyboardButton(text="ℹ️ О нас и контакты",            callback_data="about")],
        [InlineKeyboardButton(text="❓ Частые вопросы",              callback_data="faq")],
    ])

def home_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏠 В главное меню", callback_data="main_menu")]
    ])

def carpet_type_kb() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=v["name"], callback_data=f"ctype:{k}")]
            for k, v in CARPET_TYPES.items()]
    rows.append([InlineKeyboardButton(text="🏠 В главное меню", callback_data="main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def extras_kb(selected: set) -> InlineKeyboardMarkup:
    rows = []
    for k, v in EXTRA_SERVICES.items():
        mark = "✅ " if k in selected else ""
        rows.append([InlineKeyboardButton(text=f"{mark}{v['name']}", callback_data=f"extra:{k}")])
    rows.append([InlineKeyboardButton(text="➡️ Рассчитать",    callback_data="extra_done")])
    rows.append([InlineKeyboardButton(text="🏠 В главное меню", callback_data="main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def after_calc_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📍 Заказать вывоз",      callback_data="pickup")],
        [InlineKeyboardButton(text="🔙 Пересчитать",          callback_data="calc")],
        [InlineKeyboardButton(text="🏠 В главное меню",       callback_data="main_menu")],
    ])

def phone_request_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Отправить мой номер", request_contact=True)]],
        resize_keyboard=True, one_time_keyboard=True,
    )

def skip_kb(callback: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏭ Без комментария", callback_data=callback)],
        [InlineKeyboardButton(text="🏠 В главное меню",  callback_data="main_menu")],
    ])

def contact_kb() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"📞 Позвонить: {COMPANY_PHONE}", url=f"tel:{COMPANY_PHONE}")],
        [InlineKeyboardButton(text="💬 WhatsApp", url=COMPANY_WHATSAPP)],
    ]
    if COMPANY_SITE:
        rows.append([InlineKeyboardButton(text="🌐 Сайт", url=COMPANY_SITE)])
    rows.append([InlineKeyboardButton(text="📍 Открыть карту", url=COMPANY_MAP)])
    rows.append([InlineKeyboardButton(text="🏠 В главное меню", callback_data="main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def stars_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐",     callback_data="stars:1"),
         InlineKeyboardButton(text="⭐⭐",   callback_data="stars:2"),
         InlineKeyboardButton(text="⭐⭐⭐", callback_data="stars:3")],
        [InlineKeyboardButton(text="⭐⭐⭐⭐",   callback_data="stars:4"),
         InlineKeyboardButton(text="⭐⭐⭐⭐⭐", callback_data="stars:5")],
    ])

# ═══════════════════════════ Клавиатуры — админ ═══════════════════════════
def admin_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика",          callback_data="adm_stats")],
        [InlineKeyboardButton(text="📦 Список заказов",      callback_data="adm_orders")],
        [InlineKeyboardButton(text="✏️ Изменить статус",     callback_data="adm_setstatus")],
        [InlineKeyboardButton(text="💵 Изменить цены",       callback_data="adm_prices")],
        [InlineKeyboardButton(text="📤 Выгрузить заказы",    callback_data="adm_export_menu")],
        [InlineKeyboardButton(text="⭐ Модерация отзывов",   callback_data="adm_reviews")],
        [InlineKeyboardButton(text="📢 Рассылка клиентам",   callback_data="adm_broadcast")],
        [InlineKeyboardButton(text="👤 Добавить админа",     callback_data="adm_addadmin")],
        [InlineKeyboardButton(text="👁 Вид клиента",         callback_data="adm_client_view")],
    ])

def admin_back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад в админ-меню", callback_data="adm_back")]
    ])

# ═══════════════════════════ /start ═══════════════════════════
WELCOME = (
    f"🧼 <b>Добро пожаловать в {COMPANY_NAME}!</b>\n\n"
    "✅ Профессиональная стирка ковров\n"
    "✅ Вывоз и доставка\n"
    "✅ Готовность за 24 часа\n\n"
    "Выберите действие 👇"
)

@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, command: CommandObject):
    await state.clear()
    referred_by = None
    if command.args and command.args.startswith("ref_"):
        try:
            ref_id = int(command.args[4:])
            if ref_id != message.from_user.id:
                referred_by = ref_id
        except ValueError:
            pass
    existing = await db_get_user(message.from_user.id)
    await db_upsert_user(message.from_user.id, message.from_user.username or "",
                         message.from_user.full_name or "", referred_by if not existing else None)
    if referred_by and not existing:
        await db_add_bonus(referred_by, 200)
        try:
            await bot.send_message(referred_by,
                "🎉 По вашей ссылке зарегистрировался новый клиент!\nВам начислено 200 бонусных рублей.")
        except Exception:
            pass
    await message.answer(WELCOME, reply_markup=main_menu_kb())

@dp.callback_query(F.data == "main_menu")
async def cb_main_menu(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await edit_or_answer(call.message, WELCOME, reply_markup=main_menu_kb())
    await call.answer()

# ═══════════════════════════ /admin — вход по паролю ═══════════════════════════
@dp.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext):
    await safe_delete(message)
    uid = message.from_user.id
    if await db_is_admin(uid):
        # Уже авторизован
        await _show_admin_panel(message, uid)
        return
    # Проверяем есть ли пароль
    pw_hash = await db_get_setting("admin_password")
    if not pw_hash:
        # Первый вход — устанавливаем пароль
        await state.set_state(AdminAuth.set_password)
        await message.answer(
            "🔐 <b>Первый запуск adminki</b>\n\n"
            "Придумайте пароль для входа в админ-панель.\n"
            "Запомните его — он потребуется при каждом входе."
        )
    else:
        await state.set_state(AdminAuth.enter_password)
        await message.answer("🔐 Введите пароль для входа в админ-панель:")

@dp.message(AdminAuth.set_password)
async def msg_set_password(message: Message, state: FSMContext):
    await safe_delete(message)
    pw = (message.text or "").strip()
    if len(pw) < 4:
        await message.answer("❌ Пароль должен быть не менее 4 символов. Попробуйте снова:")
        return
    await db_set_setting("admin_password", hash_password(pw))
    await db_add_admin(message.from_user.id)
    await state.clear()
    await message.answer("✅ Пароль установлен! Вы вошли в админ-панель.", reply_markup=admin_menu_kb())

@dp.message(AdminAuth.enter_password)
async def msg_enter_password(message: Message, state: FSMContext):
    await safe_delete(message)
    pw = (message.text or "").strip()
    stored = await db_get_setting("admin_password")
    if hash_password(pw) != stored:
        await message.answer("❌ Неверный пароль. Попробуйте снова:")
        return
    await db_add_admin(message.from_user.id)
    await state.clear()
    s = await db_stats()
    await message.answer(
        f"✅ Вход выполнен!\n\n"
        f"📦 Активных заказов: {s['active']}\n"
        f"🆕 Новых сегодня: {s['today_new']}\n"
        f"⏳ Ждут подтверждения: {s['pending']}\n"
        f"💰 Доход сегодня: {s['revenue_today']} ₽",
        reply_markup=admin_menu_kb()
    )

async def _show_admin_panel(message: Message, uid: int):
    s = await db_stats()
    await message.answer(
        f"🛠 <b>Админ-панель</b>\n\n"
        f"📦 Активных: {s['active']} | 🆕 сегодня: {s['today_new']}\n"
        f"⏳ Ждут: {s['pending']} | 💰 сегодня: {s['revenue_today']} ₽",
        reply_markup=admin_menu_kb()
    )

# ═══════════════════════════ Калькулятор ═══════════════════════════
@dp.callback_query(F.data == "calc")
async def cb_calc(call: CallbackQuery, state: FSMContext):
    if await db_is_admin(call.from_user.id):
        await call.answer("Вы в режиме администратора.", show_alert=True)
        return
    await state.clear()
    await state.set_state(CalcStates.carpet_type)
    await edit_or_answer(call.message,
        "🧺 <b>Расчёт стоимости стирки</b>\n\n<b>Шаг 1/3.</b> Выберите тип ковра:",
        reply_markup=carpet_type_kb())
    await call.answer()

@dp.callback_query(CalcStates.carpet_type, F.data.startswith("ctype:"))
async def cb_carpet_type(call: CallbackQuery, state: FSMContext):
    ctype = call.data.split(":")[1]
    await state.update_data(carpet_type=ctype)
    await state.set_state(CalcStates.area)
    await edit_or_answer(call.message,
        f"🧺 Тип: {CARPET_TYPES[ctype]['name']}\n\n"
        "<b>Шаг 2/3.</b> Введите площадь ковра в м².\n"
        "Можно ввести число <code>6</code> или размеры <code>3x2</code>")
    await call.answer()

@dp.message(CalcStates.area)
async def msg_area(message: Message, state: FSMContext):
    await safe_delete(message)
    area = parse_area(message.text or "")
    if not area:
        await message.answer("❌ Не понял площадь. Введите число (например <code>6</code>) или размеры (<code>3x2</code>).")
        return
    await state.update_data(area=area, extras=[])
    await state.set_state(CalcStates.extras)
    await message.answer(
        f"🧺 Площадь: <b>{area} м²</b>\n\n"
        "<b>Шаг 3/3.</b> Выберите дополнительные услуги (если нужны) и нажмите «Рассчитать»:",
        reply_markup=extras_kb(set()))

@dp.callback_query(CalcStates.extras, F.data.startswith("extra:"))
async def cb_extra_toggle(call: CallbackQuery, state: FSMContext):
    key = call.data.split(":")[1]
    data = await state.get_data()
    extras = set(data.get("extras", []))
    if key in extras:
        extras.discard(key)
    else:
        extras.add(key)
    await state.update_data(extras=list(extras))
    await call.message.edit_reply_markup(reply_markup=extras_kb(extras))
    await call.answer()

@dp.callback_query(CalcStates.extras, F.data == "extra_done")
async def cb_extra_done(call: CallbackQuery, state: FSMContext):
    data  = await state.get_data()
    ctype = data["carpet_type"]
    area  = data["area"]
    extras = data.get("extras", [])
    breakdown = calc_price(ctype, area, extras)
    await state.update_data(last_calc=breakdown)

    lines = [
        "💰 <b>Результат расчёта</b>\n",
        f"Тип ковра: {CARPET_TYPES[ctype]['name']}",
        f"Площадь: {area} м²",
        f"Стирка: {breakdown['base']} ₽",
    ]
    if breakdown["flat_extra"]:
        lines.append(f"Доп. услуги: +{breakdown['flat_extra']} ₽")
    if breakdown["percent"]:
        lines.append(f"Экспресс (+{breakdown['percent']}%): +{breakdown['percent_sum']} ₽")
    lines.append(f"\n<b>ИТОГО: {breakdown['total']} ₽</b>")

    if breakdown["total"] < MIN_ORDER:
        lines.append(f"\n⚠️ Минимальная сумма заказа: {MIN_ORDER} ₽")
        lines.append(f"🚚 Доставка в обе стороны: {PAID_PICKUP_FEE} ₽")
    elif breakdown["total"] < MIN_FREE_PICKUP:
        lines.append(f"\n🚚 Доставка в обе стороны: {PAID_PICKUP_FEE} ₽")
        lines.append(f"(бесплатно от {MIN_FREE_PICKUP} ₽)")
    else:
        lines.append("\n✅ Доставка в обе стороны — бесплатно!")
    lines.append("\nХотите оформить вывоз? 👇")

    await edit_or_answer(call.message, "\n".join(lines), reply_markup=after_calc_kb())
    await call.answer()

# ═══════════════════════════ Заказ вывоза ═══════════════════════════
@dp.callback_query(F.data == "pickup")
async def cb_pickup(call: CallbackQuery, state: FSMContext):
    if await db_is_admin(call.from_user.id):
        await call.answer("Вы в режиме администратора.", show_alert=True)
        return
    data = await state.get_data()
    last_calc  = data.get("last_calc")
    calc_ctype = data.get("carpet_type")
    calc_area  = data.get("area")
    calc_extras = data.get("extras", [])
    await state.clear()
    if last_calc:
        await state.update_data(calc_total=last_calc["total"], calc_ctype=calc_ctype,
                                calc_area=calc_area, calc_extras=calc_extras)
    await state.set_state(PickupStates.address)
    await edit_or_answer(call.message,
        "📍 <b>Заказ вывоза ковра</b>\n\n"
        "<b>Шаг 1/4.</b> Укажите адрес забора:\n"
        "<i>Город, улица, дом, квартира</i>")
    await call.answer()

@dp.message(PickupStates.address)
async def msg_pickup_address(message: Message, state: FSMContext):
    await safe_delete(message)
    text = (message.text or "").strip()
    if len(text) < 5:
        await message.answer("❌ Адрес слишком короткий. Укажите город, улицу и дом.")
        return
    await state.update_data(address=text)
    await state.set_state(PickupStates.phone)
    await message.answer(
        "📞 <b>Шаг 2/4.</b> Ваш номер телефона.\n\nВведите вручную или нажмите кнопку:",
        reply_markup=phone_request_kb())

@dp.message(PickupStates.phone, F.contact)
async def msg_pickup_contact(message: Message, state: FSMContext):
    await safe_delete(message)
    phone = message.contact.phone_number
    await state.update_data(phone=phone)
    await db_set_user_field(message.from_user.id, "phone", phone)
    await message.answer("✅ Номер получен.", reply_markup=ReplyKeyboardRemove())
    await _ask_pickup_date(message, state)

@dp.message(PickupStates.phone)
async def msg_pickup_phone(message: Message, state: FSMContext):
    await safe_delete(message)
    phone = normalise_phone(message.text or "")
    if not phone:
        await message.answer("❌ Неверный формат. Пример: <code>+7 978 050 55 89</code>")
        return
    await state.update_data(phone=phone)
    await db_set_user_field(message.from_user.id, "phone", phone)
    await message.answer("✅ Номер принят.", reply_markup=ReplyKeyboardRemove())
    await _ask_pickup_date(message, state)

async def _ask_pickup_date(message: Message, state: FSMContext):
    await state.set_state(PickupStates.date)
    await message.answer(
        "🗓 <b>Шаг 3/4.</b> Укажите удобную дату забора.\n\n"
        "<i>Например: 27 мая или с 1 июня</i>\n\n"
        "После оформления заказа менеджер свяжется с вами для уточнения точного времени.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🏠 Отменить", callback_data="main_menu")]
        ]))

@dp.message(PickupStates.date)
async def msg_pickup_date(message: Message, state: FSMContext):
    await safe_delete(message)
    text = (message.text or "").strip()
    if len(text) < 3:
        await message.answer("❌ Укажите дату, например: <code>27 мая</code>")
        return
    await state.update_data(pickup_date=text)
    await state.set_state(PickupStates.comment)
    await message.answer(
        "📝 <b>Шаг 4/4.</b> Комментарий к заказу (необязательно).\n"
        "<i>Например: домофон не работает, ковёр у двери.</i>",
        reply_markup=skip_kb("pickup_skip_comment"))

@dp.callback_query(PickupStates.comment, F.data == "pickup_skip_comment")
async def cb_skip_comment(call: CallbackQuery, state: FSMContext):
    await _finalize_pickup(call.message, call.from_user, state, comment="")
    await call.answer()

@dp.message(PickupStates.comment)
async def msg_pickup_comment(message: Message, state: FSMContext):
    await safe_delete(message)
    await _finalize_pickup(message, message.from_user, state, comment=(message.text or "").strip())

async def _finalize_pickup(message: Message, user, state: FSMContext, comment: str):
    data = await state.get_data()
    price = data.get("calc_total", 0) or 0
    order = {
        "tg_user_id":  user.id,
        "carpet_type": data.get("calc_ctype", ""),
        "area":        data.get("calc_area", 0) or 0,
        "extras":      ",".join(data.get("calc_extras", [])),
        "price":       price,
        "address":     data.get("address", ""),
        "phone":       data.get("phone", ""),
        "pickup_date": data.get("pickup_date", ""),
        "comment":     comment,
        "promo":       "",
    }
    order_id = await db_create_order(order)
    await state.clear()

    if price >= MIN_FREE_PICKUP:
        delivery_line = "🚚 Доставка в обе стороны: <b>бесплатно</b>"
    else:
        delivery_line = f"🚚 Доставка в обе стороны: <b>{PAID_PICKUP_FEE} ₽</b>"

    price_line = f"💰 Стоимость стирки: <b>{price} ₽</b>\n" if price else ""

    await edit_or_answer(message,
        f"✅ <b>Заказ #{order_id} оформлен!</b>\n\n"
        f"📍 Адрес: {order['address']}\n"
        f"🗓 Желаемая дата: {order['pickup_date']}\n"
        f"📞 Телефон: {order['phone']}\n"
        f"{price_line}"
        f"{delivery_line}\n\n"
        f"📲 <b>Менеджер свяжется с вами для уточнения времени.</b>\n"
        f"📞 Или позвоните сами: {COMPANY_PHONE}",
        reply_markup=home_kb())

    admins = await db_all_admins()
    for admin_id in admins:
        try:
            await bot.send_message(admin_id,
                f"🆕 <b>Новый заказ #{order_id}</b>\n"
                f"От: @{user.username or '—'} (id {user.id})\n"
                f"📍 {order['address']}\n"
                f"🗓 {order['pickup_date']}\n"
                f"📞 {order['phone']}\n"
                f"💰 {price} ₽\n"
                f"📝 {comment or '—'}")
        except Exception as e:
            logger.warning("Не уведомлён админ %s: %s", admin_id, e)

# ═══════════════════════════ Мои заказы ═══════════════════════════
@dp.callback_query(F.data == "my_orders")
async def cb_my_orders(call: CallbackQuery):
    if await db_is_admin(call.from_user.id):
        await call.answer("Вы в режиме администратора.", show_alert=True)
        return
    orders = await db_user_orders(call.from_user.id)
    if not orders:
        await edit_or_answer(call.message,
            "📦 У вас пока нет заказов.\n\nВыберите действие:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🧺 Рассчитать стоимость", callback_data="calc")],
                [InlineKeyboardButton(text="📍 Заказать вывоз",       callback_data="pickup")],
                [InlineKeyboardButton(text="🏠 В главное меню",       callback_data="main_menu")],
            ]))
        await call.answer()
        return
    active  = [o for o in orders if o["status"] not in ("delivered","cancelled")]
    rows    = [[InlineKeyboardButton(
                text=f"#{o['order_id']} — {STATUS_LABELS.get(o['status'], o['status'])}",
                callback_data=f"order:{o['order_id']}")] for o in (active or orders)]
    archive = [o for o in orders if o["status"] in ("delivered","cancelled")]
    if archive:
        rows.append([InlineKeyboardButton(text=f"📦 Архив ({len(archive)})", callback_data="orders_archive")])
    rows.append([InlineKeyboardButton(text="🏠 В главное меню", callback_data="main_menu")])
    header = "📦 <b>Ваши активные заказы:</b>" if active else "📦 <b>Ваши заказы:</b>"
    await edit_or_answer(call.message, header, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await call.answer()

@dp.callback_query(F.data == "orders_archive")
async def cb_orders_archive(call: CallbackQuery):
    orders  = await db_user_orders(call.from_user.id)
    archive = [o for o in orders if o["status"] in ("delivered","cancelled")]
    if not archive:
        await call.answer("Архив пуст.", show_alert=True)
        return
    rows = [[InlineKeyboardButton(
             text=f"#{o['order_id']} — {STATUS_LABELS.get(o['status'], o['status'])}",
             callback_data=f"order:{o['order_id']}")] for o in archive]
    rows.append([InlineKeyboardButton(text="🔙 К активным", callback_data="my_orders")])
    await edit_or_answer(call.message, "📦 <b>Архив заказов:</b>",
                         reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await call.answer()

@dp.callback_query(F.data.startswith("order:"))
async def cb_order_detail(call: CallbackQuery):
    order_id = int(call.data.split(":")[1])
    order    = await db_get_order(order_id)
    if not order or order["tg_user_id"] != call.from_user.id:
        await call.answer("Заказ не найден.", show_alert=True)
        return
    history   = await db_order_history(order_id)
    hist_lines = [f"✓ {h['ts'][5:16].replace('-','.')} — {STATUS_LABELS.get(h['status'],h['status'])}"
                  for h in history]
    extras = order["extras"].split(",") if order["extras"] else []
    text = (
        f"📦 <b>Заказ #{order_id}</b>\n\n"
        f"Статус: {STATUS_LABELS.get(order['status'], order['status'])}\n"
        f"📐 Площадь: {order['area']} м²\n"
        f"🧵 Тип: {CARPET_TYPES.get(order['carpet_type'],{}).get('name','—')}\n"
        f"➕ Доп. услуги: {extras_names(extras)}\n"
        f"💰 Стоимость: {order['price']} ₽\n"
        f"📍 Адрес: {order['address']}\n"
        f"🗓 Дата: {order['pickup_date']}\n\n"
        f"<b>История:</b>\n" + "\n".join(hist_lines)
    )
    await edit_or_answer(call.message, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"📞 Позвонить: {COMPANY_PHONE}", url=f"tel:{COMPANY_PHONE}")],
        [InlineKeyboardButton(text="🔙 К списку",      callback_data="my_orders")],
        [InlineKeyboardButton(text="🏠 В главное меню", callback_data="main_menu")],
    ]))
    await call.answer()

# ═══════════════════════════ Отзывы ═══════════════════════════
@dp.callback_query(F.data == "reviews")
async def cb_reviews(call: CallbackQuery):
    if await db_is_admin(call.from_user.id):
        await call.answer("Вы в режиме администратора.", show_alert=True)
        return
    reviews = await db_approved_reviews()
    if not reviews:
        await edit_or_answer(call.message,
            "⭐ <b>Отзывы клиентов</b>\n\n"
            "Пока нет опубликованных отзывов — станьте первым!\n"
            "За отзыв с фото — 100 бонусных рублей. 🎁",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="➕ Оставить отзыв", callback_data="review_add")],
                [InlineKeyboardButton(text="🔙 Назад",          callback_data="main_menu")],
            ]))
        await call.answer()
        return
    avg = sum(r["stars"] for r in reviews) / len(reviews)
    text = (f"⭐ <b>Отзывы наших клиентов</b>\n\n"
            f"Всего отзывов: {len(reviews)}  |  Средняя оценка: {avg:.1f} ★\n\n")
    for r in reviews[:8]:
        photo_mark = " 📷" if r["photo_id"] else ""
        text += f"{'★'*r['stars']} <b>{r['name'] or 'Аноним'}</b>{photo_mark}\n«{r['text']}»\n\n"
    if len(reviews) > 8:
        text += f"<i>…и ещё {len(reviews)-8} отзывов.</i>"
    rows = [[InlineKeyboardButton(text="➕ Оставить свой отзыв", callback_data="review_add")]]
    if any(r["photo_id"] for r in reviews):
        rows.append([InlineKeyboardButton(text="📷 Смотреть фото работ", callback_data="reviews_photos")])
    rows.append([InlineKeyboardButton(text="🔙 Назад", callback_data="main_menu")])
    await edit_or_answer(call.message, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await call.answer()

@dp.callback_query(F.data == "reviews_photos")
async def cb_reviews_photos(call: CallbackQuery):
    if await db_is_admin(call.from_user.id):
        await call.answer("Вы в режиме администратора.", show_alert=True)
        return
    reviews = [r for r in await db_approved_reviews() if r["photo_id"]]
    if not reviews:
        await call.answer("Пока нет отзывов с фото.", show_alert=True)
        return
    # Отправляем до 5 фото отдельными сообщениями
    for r in reviews[:5]:
        caption = f"{'★'*r['stars']} {r['name'] or 'Аноним'}\n«{r['text']}»"
        try:
            await call.message.answer_photo(r["photo_id"], caption=caption)
        except Exception:
            pass
    await call.message.answer("📷 Примеры работ выше 👆",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 К отзывам",     callback_data="reviews")],
            [InlineKeyboardButton(text="🏠 В главное меню", callback_data="main_menu")],
        ]))
    await call.answer()

@dp.callback_query(F.data == "review_add")
async def cb_review_add(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(ReviewStates.stars)
    await edit_or_answer(call.message,
        "⭐ <b>Оставить отзыв</b>\n\nОцените сервис:", reply_markup=stars_kb())
    await call.answer()

@dp.callback_query(ReviewStates.stars, F.data.startswith("stars:"))
async def cb_review_stars(call: CallbackQuery, state: FSMContext):
    stars = int(call.data.split(":")[1])
    await state.update_data(stars=stars)
    await state.set_state(ReviewStates.name)
    await edit_or_answer(call.message,
        f"Оценка: {'★'*stars}\n\nВаше имя (или «-» чтобы остаться анонимом):")
    await call.answer()

@dp.message(ReviewStates.name)
async def msg_review_name(message: Message, state: FSMContext):
    await safe_delete(message)
    name = (message.text or "").strip()
    if name == "-":
        name = ""
    await state.update_data(name=name)
    await state.set_state(ReviewStates.text)
    await message.answer("Напишите текст отзыва:")

@dp.message(ReviewStates.text)
async def msg_review_text(message: Message, state: FSMContext):
    await safe_delete(message)
    text = (message.text or "").strip()
    if len(text) < 5:
        await message.answer("Отзыв слишком короткий, напишите подробнее.")
        return
    await state.update_data(text=text)
    await state.set_state(ReviewStates.photo)
    await message.answer("📎 Прикрепите фото до/после (необязательно):",
        reply_markup=skip_kb("review_skip_photo"))

@dp.callback_query(ReviewStates.photo, F.data == "review_skip_photo")
async def cb_review_skip_photo(call: CallbackQuery, state: FSMContext):
    await _save_review(call.message, call.from_user, state, photo_id="")
    await call.answer()

@dp.message(ReviewStates.photo, F.photo)
async def msg_review_photo(message: Message, state: FSMContext):
    await safe_delete(message)
    await _save_review(message, message.from_user, state, photo_id=message.photo[-1].file_id)

@dp.message(ReviewStates.photo)
async def msg_review_photo_invalid(message: Message):
    await safe_delete(message)
    await message.answer("Пришлите фото или нажмите «Без комментария».",
        reply_markup=skip_kb("review_skip_photo"))

async def _save_review(message: Message, user, state: FSMContext, photo_id: str):
    data = await state.get_data()
    review = {"tg_user_id": user.id, "name": data.get("name",""),
              "stars": data.get("stars",5), "text": data.get("text",""), "photo_id": photo_id}
    review_id = await db_add_review(review)
    await state.clear()
    await db_add_bonus(user.id, 100)
    await edit_or_answer(message,
        "✅ <b>Спасибо за отзыв!</b>\n\nОн будет опубликован после проверки.\n"
        "Вам начислено 100 бонусных рублей! 🎁", reply_markup=home_kb())
    admins = await db_all_admins()
    for admin_id in admins:
        try:
            await bot.send_message(admin_id,
                f"⭐ Новый отзыв #{review_id} на модерацию ({'★'*review['stars']}).")
        except Exception:
            pass

# ═══════════════════════════ О нас / Контакты ═══════════════════════════
@dp.callback_query(F.data == "about")
async def cb_about(call: CallbackQuery):
    text = (
        f"🧼 <b>{COMPANY_NAME}</b>\n\n"
        f"📞 Телефон: <b>{COMPANY_PHONE}</b>\n\n"
        f"✅ Профессиональная стирка ковров\n"
        f"✅ Доставка в обе стороны от {MIN_FREE_PICKUP} ₽\n"
        f"✅ Минимальный заказ от {MIN_ORDER} ₽\n"
        f"✅ Готовность за 24 часа\n"
        f"✅ Гарантия 100% или вернём 20%\n\n"
        f"📍 Цех: {COMPANY_ADDRESS}\n"
        f"🕒 График: Пн-Вс 8:00–21:00\n\n"
        f"🚚 Зона доставки: Донецк + Макеевка"
    )
    await edit_or_answer(call.message, text, reply_markup=contact_kb())
    await call.answer()

# ═══════════════════════════ FAQ ═══════════════════════════
def build_faq() -> dict:
    """Строит тексты FAQ с актуальными ценами (цены могут меняться админом)."""
    return {
    "price": (
        "❓ <b>Сколько стоит постирать ковёр?</b>\n\n"
        "Стоимость зависит от типа ковра и его площади:\n\n"
        f"• 😊 Обычный ковёр — {CARPET_TYPES['standard']['price']} ₽/м²\n"
        f"• 🦁 Длинный ворс — {CARPET_TYPES['longpile']['price']} ₽/м²\n"
        f"• 🧵 Шерстяной/дорогой — {CARPET_TYPES['wool']['price']} ₽/м²\n\n"
        f"Минимальная сумма заказа — {MIN_ORDER} ₽.\n"
        "Точную цену для вашего ковра рассчитает калькулятор в главном меню — "
        "это займёт меньше минуты."
    ),
    "speed": (
        "❓ <b>Как быстро вы постираете ковёр?</b>\n\n"
        "Стандартный срок — <b>24 часа</b> с момента забора.\n\n"
        "Как проходит работа:\n"
        "✓ Забираем ковёр у вас\n"
        "✓ Стирка профессиональным оборудованием — 2–4 часа\n"
        "✓ Сушка в специальной камере — 4–6 часов\n"
        "✓ Доставляем чистый ковёр обратно\n\n"
        "⚡ Нужно срочно? Закажите экспресс-стирку за 6 часов (+30% к стоимости)."
    ),
    "delivery": (
        "❓ <b>Сколько стоит вывоз и доставка?</b>\n\n"
        f"✅ При заказе от {MIN_FREE_PICKUP} ₽ — доставка в обе стороны "
        "<b>бесплатно</b>.\n"
        f"При заказе меньше {MIN_FREE_PICKUP} ₽ — доставка в обе стороны "
        f"стоит {PAID_PICKUP_FEE} ₽.\n\n"
        "Курьер сам приедет в удобное время, заберёт ковёр и выдаст квитанцию, "
        "а после чистки привезёт его обратно. Вам никуда не нужно ехать.\n\n"
        "🚚 Работаем по Донецку и Макеевке."
    ),
    "safe": (
        "❓ <b>А вдруг ковёр испортят или потеряют?</b>\n\n"
        "Мы несём полную ответственность за ваш ковёр:\n\n"
        "✓ При приёмке выдаём квитанцию с описанием ковра\n"
        "✓ Используем безопасную химию — подходит для шерсти, "
        "шёлка и ковров с рисунком\n"
        "✓ Если результат вас не устроит — перестираем бесплатно "
        "или вернём 20% стоимости\n\n"
        "За всё время работы мы не потеряли ни одного ковра."
    ),
    "order": (
        "❓ <b>Как сделать заказ?</b>\n\n"
        "Всё делается прямо в этом боте за пару минут:\n\n"
        "1️⃣ Нажмите «Рассчитать стоимость» — узнаете цену\n"
        "2️⃣ Нажмите «Заказать вывоз»\n"
        "3️⃣ Укажите адрес, телефон и удобную дату\n"
        "4️⃣ Менеджер свяжется с вами и уточнит точное время\n\n"
        f"Если остались вопросы — звоните: {COMPANY_PHONE}"
    ),
    }

FAQ_TITLES = {
    "price":    "❓ Сколько стоит постирать ковёр?",
    "speed":    "❓ Как быстро вы постираете?",
    "delivery": "❓ Сколько стоит доставка?",
    "safe":     "❓ А вдруг ковёр испортят?",
    "order":    "❓ Как сделать заказ?",
}

@dp.callback_query(F.data == "faq")
async def cb_faq(call: CallbackQuery):
    rows = [[InlineKeyboardButton(text=title, callback_data=f"faq:{key}")]
            for key, title in FAQ_TITLES.items()]
    rows.append([InlineKeyboardButton(text=f"📞 Позвонить: {COMPANY_PHONE}", url=f"tel:{COMPANY_PHONE}")])
    rows.append([InlineKeyboardButton(text="🏠 В главное меню", callback_data="main_menu")])
    await edit_or_answer(call.message, "❓ <b>Частые вопросы</b>\n\nВыберите вопрос 👇",
                         reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await call.answer()

@dp.callback_query(F.data.startswith("faq:"))
async def cb_faq_answer(call: CallbackQuery):
    key  = call.data.split(":")[1]
    text = build_faq().get(key, "Вопрос не найден.")
    await edit_or_answer(call.message, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 К вопросам",    callback_data="faq")],
        [InlineKeyboardButton(text="🏠 В главное меню", callback_data="main_menu")],
    ]))
    await call.answer()

# ═══════════════════════════ Акции / Подписка / Рефералы / B2B ═══════════════════════════
@dp.callback_query(F.data == "promo_menu")
async def cb_promo_menu(call: CallbackQuery):
    if await db_is_admin(call.from_user.id):
        await call.answer("Вы в режиме администратора.", show_alert=True)
        return
    user  = await db_get_user(call.from_user.id)
    bonus = user["bonus"] if user else 0
    sub   = "✅ активна" if (user and user["is_subscriber"]) else "не активна"
    await edit_or_answer(call.message,
        f"🎁 <b>Акции и бонусы</b>\n\n💰 Ваши бонусы: <b>{bonus} ₽</b>\n📦 Подписка: {sub}\n\nВыберите раздел:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📦 Подписка",               callback_data="subscription")],
            [InlineKeyboardButton(text="👥 Привести друга (−15%)",  callback_data="referral")],
            [InlineKeyboardButton(text="🏢 Партнёрство (B2B)",      callback_data="b2b")],
            [InlineKeyboardButton(text="🏠 В главное меню",         callback_data="main_menu")],
        ]))
    await call.answer()

@dp.callback_query(F.data == "subscription")
async def cb_subscription(call: CallbackQuery):
    await edit_or_answer(call.message,
        f"📦 <b>Подписка на стирку ковров</b>\n\n"
        f"<b>{SUBSCRIPTION_PRICE} ₽</b> за 2 стирки в год (экономия 20%)\n\n"
        "✅ Приоритетная очередь\n✅ Бесплатная доставка всегда\n✅ Скидка 10% на доп. услуги\n\n"
        "Нажмите кнопку — с вами свяжется менеджер.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Хочу подписку", callback_data="sub_request")],
            [InlineKeyboardButton(text="🔙 Назад",         callback_data="promo_menu")],
        ]))
    await call.answer()

@dp.callback_query(F.data == "sub_request")
async def cb_sub_request(call: CallbackQuery):
    user   = call.from_user
    admins = await db_all_admins()
    for admin_id in admins:
        try:
            await bot.send_message(admin_id,
                f"📦 Запрос подписки от @{user.username or '—'} (id {user.id})")
        except Exception:
            pass
    await call.answer("Заявка отправлена! Менеджер свяжется с вами.", show_alert=True)

@dp.callback_query(F.data == "referral")
async def cb_referral(call: CallbackQuery):
    me   = await bot.get_me()
    link = f"https://t.me/{me.username}?start=ref_{call.from_user.id}"
    await edit_or_answer(call.message,
        f"👥 <b>Приведи друга!</b>\n\n"
        f"Оба получите скидку {REFERRAL_DISCOUNT}% на следующий заказ.\n"
        "За каждого друга — 200 бонусных рублей.\n\n"
        f"Ваша ссылка:\n<code>{link}</code>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад", callback_data="promo_menu")]
        ]))
    await call.answer()

@dp.callback_query(F.data == "b2b")
async def cb_b2b(call: CallbackQuery, state: FSMContext):
    await state.set_state(B2BStates.contact)
    await edit_or_answer(call.message,
        "🏢 <b>Партнёрская программа</b>\n\n"
        "Для риелторов, управляющих ЖК и клининговых компаний:\n"
        "✅ Скидка 25% для ваших клиентов\n"
        "✅ Вы получаете 10% комиссии\n"
        "✅ Персональный менеджер\n\n"
        "Напишите название компании и контактный телефон:")
    await call.answer()

@dp.message(B2BStates.contact)
async def msg_b2b_contact(message: Message, state: FSMContext):
    await safe_delete(message)
    await state.clear()
    admins = await db_all_admins()
    for admin_id in admins:
        try:
            await bot.send_message(admin_id,
                f"🏢 <b>Заявка B2B</b>\nОт @{message.from_user.username or '—'} (id {message.from_user.id})\n{message.text or ''}")
        except Exception:
            pass
    await message.answer("✅ Заявка отправлена! Менеджер свяжется с вами.", reply_markup=home_kb())

# ═══════════════════════════ АДМИН-ПАНЕЛЬ ═══════════════════════════
async def require_admin(call: CallbackQuery) -> bool:
    if not await db_is_admin(call.from_user.id):
        await call.answer("⛔ Нет доступа. Войдите через /admin", show_alert=True)
        return False
    return True

@dp.callback_query(F.data == "adm_stats")
async def cb_adm_stats(call: CallbackQuery):
    if not await require_admin(call): return
    s = await db_stats()
    await edit_or_answer(call.message,
        "📊 <b>Статистика</b>\n\n"
        f"👥 Пользователей: {s['total_users']}\n"
        f"📦 Всего заказов: {s['total_orders']}\n"
        f"🟢 Активных: {s['active']}\n"
        f"🆕 Новых сегодня: {s['today_new']}\n"
        f"⏳ Ждут подтверждения: {s['pending']}\n\n"
        f"💰 Доход сегодня: {s['revenue_today']} ₽\n"
        f"💵 Всего выручки: {s['revenue_total']} ₽",
        reply_markup=admin_back_kb())
    await call.answer()

@dp.callback_query(F.data == "adm_orders")
async def cb_adm_orders(call: CallbackQuery):
    if not await require_admin(call): return
    orders = await db_all_orders()
    if not orders:
        await edit_or_answer(call.message, "Заказов пока нет.", reply_markup=admin_menu_kb())
        await call.answer()
        return
    # Показываем последние 20 заказов кнопками
    rows = []
    for o in orders[:20]:
        label = f"#{o['order_id']} {STATUS_LABELS.get(o['status'],o['status'])[:10]} | {o['price']}₽"
        rows.append([InlineKeyboardButton(text=label, callback_data=f"adm_order:{o['order_id']}")])
    rows.append([InlineKeyboardButton(text="🔙 В меню", callback_data="adm_back")])
    suffix = f"\n\n...и ещё {len(orders)-20}. Выгрузите CSV для полного списка." if len(orders) > 20 else ""
    await edit_or_answer(call.message,
        f"📦 <b>Заказы ({len(orders)} всего)</b>{suffix}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await call.answer()

@dp.callback_query(F.data.startswith("adm_order:"))
async def cb_adm_order_detail(call: CallbackQuery):
    if not await require_admin(call): return
    order_id = int(call.data.split(":")[1])
    order    = await db_get_order(order_id)
    if not order:
        await call.answer("Заказ не найден.", show_alert=True)
        return
    extras = order["extras"].split(",") if order["extras"] else []
    text = (
        f"📦 <b>Заказ #{order_id}</b>\n\n"
        f"Статус: {STATUS_LABELS.get(order['status'], order['status'])}\n"
        f"👤 ID клиента: {order['tg_user_id']}\n"
        f"📍 Адрес: {order['address']}\n"
        f"📞 Телефон: {order['phone']}\n"
        f"🗓 Дата: {order['pickup_date']}\n"
        f"🧵 Тип: {CARPET_TYPES.get(order['carpet_type'],{}).get('name','—')}\n"
        f"📐 Площадь: {order['area']} м²\n"
        f"➕ Доп: {extras_names(extras)}\n"
        f"💰 Стоимость: {order['price']} ₽\n"
        f"📝 Комментарий: {order['comment'] or '—'}\n"
        f"📅 Создан: {order['created_at'][:16]}"
    )
    rows = []
    for st in STATUS_FLOW:
        rows.append([InlineKeyboardButton(
            text=f"{'✅ ' if st == order['status'] else ''}{STATUS_LABELS[st]}",
            callback_data=f"setst:{order_id}:{st}")])
    rows.append([InlineKeyboardButton(text="🔙 К заказам", callback_data="adm_orders")])
    await edit_or_answer(call.message, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await call.answer()

@dp.callback_query(F.data == "adm_setstatus")
async def cb_adm_setstatus(call: CallbackQuery, state: FSMContext):
    if not await require_admin(call): return
    await state.set_state(AdminStates.set_status_id)
    await edit_or_answer(call.message,
        "✏️ Введите <b>номер заказа</b> для смены статуса:")
    await call.answer()

@dp.message(AdminStates.set_status_id)
async def msg_adm_status_id(message: Message, state: FSMContext):
    if not await db_is_admin(message.from_user.id): return
    await safe_delete(message)
    raw = (message.text or "").strip().lstrip("#")
    if not raw.isdigit():
        await message.answer("Введите число — номер заказа.")
        return
    order = await db_get_order(int(raw))
    if not order:
        await message.answer("Заказ не найден.")
        return
    await state.clear()
    rows = [[InlineKeyboardButton(
             text=f"{'✅ ' if st == order['status'] else ''}{STATUS_LABELS[st]}",
             callback_data=f"setst:{order['order_id']}:{st}")] for st in STATUS_FLOW]
    rows.append([InlineKeyboardButton(text="🔙 В меню", callback_data="adm_back")])
    await message.answer(
        f"Заказ #{order['order_id']}\nТекущий статус: {STATUS_LABELS.get(order['status'],order['status'])}\n\nВыберите новый статус:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))

STATUS_NOTIFY = {
    "confirmed":   "✅ Ваш заказ #{oid} подтверждён! Менеджер свяжется для уточнения времени.",
    "picked_up":   "🚚 Курьер забрал ваш ковёр (заказ #{oid}).",
    "at_cleaning": "✂️ Ваш ковёр (#{oid}) принят в стирку. Готовность — 24 часа.",
    "drying":      "💨 Ковёр #{oid} постиран и сушится.",
    "ready":       "📦 Ваш ковёр #{oid} готов к доставке! Курьер скоро свяжется.",
    "delivered":   "🎉 Заказ #{oid} доставлен! Спасибо. Будем рады отзыву ⭐",
    "cancelled":   "❌ Заказ #{oid} отменён. По вопросам: " + COMPANY_PHONE,
}

@dp.callback_query(F.data.startswith("setst:"))
async def cb_set_status_apply(call: CallbackQuery):
    if not await require_admin(call): return
    _, oid, new_status = call.data.split(":")
    oid   = int(oid)
    order = await db_get_order(oid)
    if not order:
        await call.answer("Заказ не найден.", show_alert=True)
        return
    await db_update_status(oid, new_status)
    await edit_or_answer(call.message,
        f"✅ Заказ #{oid} → {STATUS_LABELS.get(new_status, new_status)}",
        reply_markup=admin_menu_kb())
    await call.answer()
    note = STATUS_NOTIFY.get(new_status)
    if note:
        try:
            await bot.send_message(order["tg_user_id"], note.format(oid=oid))
        except Exception as e:
            logger.warning("Не уведомлён клиент: %s", e)
    if new_status == "delivered":
        reminder = (date.today() + timedelta(days=240)).isoformat()
        await db_set_order_field(oid, "reminder_date", reminder)
        try:
            await bot.send_message(order["tg_user_id"],
                "Оцените наш сервис:",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⭐ Оставить отзыв", callback_data="review_add")]
                ]))
        except Exception:
            pass

# ── Изменение цен ─────────────────────────────────────────────────
@dp.callback_query(F.data == "adm_prices")
async def cb_adm_prices(call: CallbackQuery):
    if not await require_admin(call): return
    rows = []
    for key, v in CARPET_TYPES.items():
        rows.append([InlineKeyboardButton(
            text=f"{v['name']} — {v['price']} ₽/м²",
            callback_data=f"adm_price_set:{key}")])
    rows.append([InlineKeyboardButton(text="🔙 Назад", callback_data="adm_back")])
    await edit_or_answer(call.message,
        "💵 <b>Изменение цен</b>\n\n"
        "Нажмите на тип ковра, чтобы задать новую цену за м²:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await call.answer()

@dp.callback_query(F.data.startswith("adm_price_set:"))
async def cb_adm_price_set(call: CallbackQuery, state: FSMContext):
    if not await require_admin(call): return
    key = call.data.split(":")[1]
    if key not in CARPET_TYPES:
        await call.answer("Неизвестный тип.", show_alert=True)
        return
    await state.set_state(AdminStates.set_price)
    await state.update_data(price_key=key)
    await edit_or_answer(call.message,
        f"💵 <b>{CARPET_TYPES[key]['name']}</b>\n\n"
        f"Текущая цена: {CARPET_TYPES[key]['price']} ₽/м²\n\n"
        "Введите новую цену числом (например <code>300</code>):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад", callback_data="adm_prices")]
        ]))
    await call.answer()

@dp.message(AdminStates.set_price)
async def msg_adm_set_price(message: Message, state: FSMContext):
    if not await db_is_admin(message.from_user.id): return
    await safe_delete(message)
    raw = (message.text or "").strip()
    if not raw.isdigit() or int(raw) <= 0:
        await message.answer("❌ Введите положительное число — цену за м².")
        return
    data = await state.get_data()
    key  = data.get("price_key")
    await state.clear()
    if key not in CARPET_TYPES:
        await message.answer("Ошибка: тип не найден.", reply_markup=admin_menu_kb())
        return
    await db_set_price(key, int(raw))
    rows = []
    for k, v in CARPET_TYPES.items():
        rows.append([InlineKeyboardButton(
            text=f"{v['name']} — {v['price']} ₽/м²",
            callback_data=f"adm_price_set:{k}")])
    rows.append([InlineKeyboardButton(text="🔙 Назад", callback_data="adm_back")])
    await message.answer(
        f"✅ Цена обновлена: {CARPET_TYPES[key]['name']} → {raw} ₽/м²",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))

# ── Выгрузка заказов ──────────────────────────────────────────────
@dp.callback_query(F.data == "adm_export_menu")
async def cb_adm_export_menu(call: CallbackQuery):
    if not await require_admin(call): return
    await edit_or_answer(call.message,
        "📤 <b>Выгрузка заказов</b>\n\nВыберите формат:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📊 CSV (Excel)",       callback_data="adm_export_csv")],
            [InlineKeyboardButton(text="📋 Текст (активные)",  callback_data="adm_export_active")],
            [InlineKeyboardButton(text="📋 Текст (все)",       callback_data="adm_export_all")],
            [InlineKeyboardButton(text="🔙 В меню",            callback_data="adm_back")],
        ]))
    await call.answer()

@dp.callback_query(F.data == "adm_export_csv")
async def cb_adm_export_csv(call: CallbackQuery):
    if not await require_admin(call): return
    orders = await db_all_orders()
    buf  = io.StringIO()
    cols = ["order_id","tg_user_id","carpet_type","area","extras","price",
            "address","phone","pickup_date","comment","status","created_at"]
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    w.writerows(orders)
    data = buf.getvalue().encode("utf-8-sig")
    await call.message.answer_document(
        BufferedInputFile(data, filename=f"orders_{date.today().isoformat()}.csv"),
        caption=f"📊 Все заказы ({len(orders)} шт.)")
    await call.message.answer("Файл выгружен 👆", reply_markup=admin_back_kb())
    await call.answer()

@dp.callback_query(F.data == "adm_export_active")
async def cb_adm_export_active(call: CallbackQuery):
    if not await require_admin(call): return
    orders = [o for o in await db_all_orders() if o["status"] not in ("delivered","cancelled")]
    if not orders:
        await call.answer("Активных заказов нет.", show_alert=True)
        return
    lines = []
    for o in orders:
        lines.append(
            f"#{o['order_id']} | {STATUS_LABELS.get(o['status'],o['status'])}\n"
            f"📍 {o['address']} | 📞 {o['phone']}\n"
            f"🗓 {o['pickup_date']} | 💰 {o['price']} ₽\n"
        )
    text = f"📋 <b>Активные заказы ({len(orders)})</b>\n\n" + "\n".join(lines)
    # Разбиваем если длинный
    if len(text) > 4000:
        text = text[:4000] + "\n...см. CSV для полного списка"
    await call.message.answer(text, reply_markup=admin_back_kb())
    await call.answer()

@dp.callback_query(F.data == "adm_export_all")
async def cb_adm_export_all(call: CallbackQuery):
    if not await require_admin(call): return
    orders = await db_all_orders()
    lines  = []
    for o in orders[:30]:
        lines.append(f"#{o['order_id']} | {STATUS_LABELS.get(o['status'],o['status'])} | {o['phone']} | {o['price']}₽")
    text = f"📋 <b>Все заказы ({len(orders)})</b>\n\n" + "\n".join(lines)
    if len(orders) > 30:
        text += "\n\n...и ещё. Используйте CSV для полного списка."
    await call.message.answer(text, reply_markup=admin_back_kb())
    await call.answer()

# ── Модерация отзывов ──────────────────────────────────────────────
@dp.callback_query(F.data == "adm_reviews")
async def cb_adm_reviews(call: CallbackQuery):
    if not await require_admin(call): return
    pending = await db_pending_reviews()
    if not pending:
        await edit_or_answer(call.message, "✅ Нет отзывов на модерации.", reply_markup=admin_menu_kb())
        await call.answer()
        return
    r    = pending[0]
    text = (f"⭐ <b>Отзыв на модерации</b> ({len(pending)} в очереди)\n\n"
            f"{'★'*r['stars']}\nИмя: {r['name'] or 'Аноним'}\n\n«{r['text']}»")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Опубликовать", callback_data=f"revok:{r['id']}")],
        [InlineKeyboardButton(text="🗑 Удалить отзыв", callback_data=f"revdel:{r['id']}")],
        [InlineKeyboardButton(text="⏭ Пропустить",    callback_data="adm_reviews")],
        [InlineKeyboardButton(text="🔙 Назад",        callback_data="adm_back")],
    ])
    if r["photo_id"]:
        await call.message.answer_photo(r["photo_id"], caption=text, reply_markup=kb)
    else:
        await edit_or_answer(call.message, text, reply_markup=kb)
    await call.answer()

@dp.callback_query(F.data.startswith("revok:"))
async def cb_review_approve(call: CallbackQuery):
    if not await require_admin(call): return
    review_id = int(call.data.split(":")[1])
    await db_approve_review(review_id)
    await call.answer("Отзыв опубликован ✅", show_alert=True)
    await cb_adm_reviews(call)

@dp.callback_query(F.data.startswith("revdel:"))
async def cb_review_delete(call: CallbackQuery):
    if not await require_admin(call): return
    review_id = int(call.data.split(":")[1])
    await db_delete_review(review_id)
    await call.answer("Отзыв удалён 🗑", show_alert=True)
    await cb_adm_reviews(call)

# ── Рассылка ──────────────────────────────────────────────────────
@dp.callback_query(F.data == "adm_broadcast")
async def cb_adm_broadcast(call: CallbackQuery, state: FSMContext):
    if not await require_admin(call): return
    await state.set_state(AdminStates.broadcast)
    await edit_or_answer(call.message, "📢 Отправьте текст рассылки для всех клиентов:")
    await call.answer()

@dp.message(AdminStates.broadcast)
async def msg_broadcast(message: Message, state: FSMContext):
    if not await db_is_admin(message.from_user.id): return
    await safe_delete(message)
    await state.clear()
    text = message.text or ""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT tg_user_id FROM users") as cur:
            ids = [r[0] for r in await cur.fetchall()]
    sent = failed = 0
    for uid in ids:
        try:
            await bot.send_message(uid, f"📢 {text}")
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)
    await message.answer(f"Рассылка завершена.\n✅ Отправлено: {sent}\n❌ Ошибок: {failed}",
                         reply_markup=admin_menu_kb())

# ── Добавить админа ───────────────────────────────────────────────
@dp.callback_query(F.data == "adm_addadmin")
async def cb_adm_addadmin(call: CallbackQuery, state: FSMContext):
    if not await require_admin(call): return
    await state.set_state(AdminAuth.add_admin_id)
    await edit_or_answer(call.message,
        "👤 <b>Добавить администратора</b>\n\n"
        "Введите Telegram ID нового администратора.\n"
        "<i>ID можно узнать через @getmyid_bot</i>")
    await call.answer()

@dp.message(AdminAuth.add_admin_id)
async def msg_add_admin_id(message: Message, state: FSMContext):
    if not await db_is_admin(message.from_user.id): return
    await safe_delete(message)
    raw = (message.text or "").strip()
    if not raw.isdigit():
        await message.answer("Введите числовой Telegram ID.")
        return
    new_uid = int(raw)
    await db_add_admin(new_uid)
    await state.clear()
    await message.answer(f"✅ Пользователь {new_uid} добавлен как администратор.\n\n"
                         "Ему нужно написать /admin и ввести пароль.",
                         reply_markup=admin_menu_kb())

# ── Вид клиента для админа ────────────────────────────────────────
@dp.callback_query(F.data == "adm_client_view")
async def cb_adm_client_view(call: CallbackQuery):
    if not await require_admin(call): return
    await edit_or_answer(call.message,
        f"👁 <b>Вид со стороны клиента</b>\n\n{WELCOME}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🧺 Рассчитать стоимость стирки", callback_data="adm_cv_calc")],
            [InlineKeyboardButton(text="ℹ️ О нас и контакты",            callback_data="adm_cv_about")],
            [InlineKeyboardButton(text="❓ Частые вопросы",              callback_data="adm_cv_faq")],
            [InlineKeyboardButton(text="🔙 Вернуться в админку",         callback_data="adm_back")],
        ]))
    await call.answer()

@dp.callback_query(F.data == "adm_cv_calc")
async def cb_adm_cv_calc(call: CallbackQuery):
    text = "🧺 <b>Расчёт стоимости</b>\n\nТипы ковров и цены:\n"
    for v in CARPET_TYPES.values():
        text += f"• {v['name']}: {v['price']} ₽/м²\n"
    text += f"\nМинимальный заказ: {MIN_ORDER} ₽"
    await edit_or_answer(call.message, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад", callback_data="adm_client_view")]
    ]))
    await call.answer()

@dp.callback_query(F.data == "adm_cv_about")
async def cb_adm_cv_about(call: CallbackQuery):
    text = (
        f"🧼 <b>{COMPANY_NAME}</b>\n\n"
        f"📞 Телефон: <b>{COMPANY_PHONE}</b>\n\n"
        f"✅ Профессиональная стирка ковров\n"
        f"✅ Доставка от {MIN_FREE_PICKUP} ₽ — бесплатно\n"
        f"✅ Минимальный заказ от {MIN_ORDER} ₽\n"
        f"✅ Готовность за 24 часа\n"
        f"📍 {COMPANY_ADDRESS}\n🕒 Пн-Вс 8:00–21:00"
    )
    await edit_or_answer(call.message, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад", callback_data="adm_client_view")]
    ]))
    await call.answer()

@dp.callback_query(F.data == "adm_cv_faq")
async def cb_adm_cv_faq(call: CallbackQuery):
    text = "❓ <b>Частые вопросы (вид клиента)</b>\n\n"
    for key, title in FAQ_TITLES.items():
        text += f"• {title}\n"
    await edit_or_answer(call.message, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад", callback_data="adm_client_view")]
    ]))
    await call.answer()

# ── Общая кнопка "В меню" для админа ────────────────────────────
@dp.callback_query(F.data == "adm_back")
async def cb_adm_back(call: CallbackQuery):
    if not await require_admin(call): return
    s = await db_stats()
    await edit_or_answer(call.message,
        f"🛠 <b>Админ-панель</b>\n\n"
        f"📦 Активных: {s['active']} | 🆕 сегодня: {s['today_new']}\n"
        f"⏳ Ждут: {s['pending']} | 💰 сегодня: {s['revenue_today']} ₽",
        reply_markup=admin_menu_kb())
    await call.answer()

# ═══════════════════════════ Планировщик ═══════════════════════════
async def reminder_job():
    today = date.today().isoformat()
    for o in await db_due_reminders(today):
        try:
            await bot.send_message(o["tg_user_id"],
                "🧼 <b>Пора постирать ковёр!</b>\n\n"
                "Прошло 8 месяцев с последней стирки.\n\n"
                f"Позвоните нам: {COMPANY_PHONE}",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="📍 Заказать вывоз", callback_data="pickup")],
                ]))
            await db_set_order_field(o["order_id"], "reminder_date", "")
        except Exception as e:
            logger.warning("Напоминание не отправлено: %s", e)

def setup_scheduler() -> AsyncIOScheduler:
    sched = AsyncIOScheduler()
    sched.add_job(reminder_job, "cron", hour=REMINDER_HOUR, minute=REMINDER_MINUTE)
    return sched

# ═══════════════════════════ Fallback ═══════════════════════════
@dp.message()
async def fallback(message: Message):
    await safe_delete(message)
    await message.answer("Воспользуйтесь меню 👇", reply_markup=main_menu_kb())

# ═══════════════════════════ Запуск ═══════════════════════════
async def main():
    await db_init()
    await db_load_prices()
    sched = setup_scheduler()
    sched.start()
    logger.info("Бот запущен.")
    await bot.delete_webhook(drop_pending_updates=False)
    try:
        await dp.start_polling(bot)
    finally:
        sched.shutdown(wait=False)
        await bot.session.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен.")
