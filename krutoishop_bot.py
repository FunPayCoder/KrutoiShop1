#!/usr/bin/env python3
# ═══════════════════════════════════════════════════════════════
#  KrutoiShop Bot v4.0
#  + Смена цен из админки
#  + Добавление/удаление админов
#  + Изменение ссылки канала и юза поддержки
#  + Автовыдача звёзд (Fragment API / fragment.com инструкция)
#  + Реферальная программа
#  + Промокоды
#  + Автопроверка TON
#  + Уведомления о новых заказах
#  + История покупок
#  + Отзывы
# ═══════════════════════════════════════════════════════════════

import asyncio, logging, json, os, uuid, hashlib, aiohttp
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

# ═══════════════════════════════════════════════════════════════
#  ⚙️  КОНФИГУРАЦИЯ (хранится в settings.json, редактируется из бота)
# ═══════════════════════════════════════════════════════════════
DEFAULT_SETTINGS = {
    "BOT_TOKEN":        "8717850614:AAFlom3j1o6m5kplPsehRrxSU8oN6U9E70Y",
    "ADMINS":           [6716817812],          # список ID админов
    "SUPPORT_USERNAME": "@KrutoiShopSupport",       # юзернейм поддержки (без @)
    "CHANNEL_URL":      "https://t.me/KrutoiShopp",
    "CHANNEL_NAME":     "@KrutoiShopp",
    "TON_WALLET":       "UQA4_4AfoTbXo1HywT3V158BPwU5BlhfV6UkW8IZKtEOHUYV",
    "SBP_PHONE":        "+7 (994) 017-61-85",
    "SBP_BANK":         "Озон Банк",
    "SBP_NAME":         "Ярослав Л.",
    "STAR_PRICE_RUB":   1.4,
    "PREMIUM_PRICES":   {"3": 1200, "6": 1500, "12": 2600},
    "RATE_KZT":         5.2,
    "RATE_USD":         0.011,
    "TON_PRICE_USD":    7.1,
    "REF_BONUS_PCT":    5,
    "REF_REWARD_PCT":   5,
    "REF_MIN_WITHDRAW": 200,
    "BOT_USERNAME":     "KrutoiShopBot",
    "CRYPTOBOT_TOKEN":  "",
    "TONAPI_KEY":       "",
    # Fragment API (для автовыдачи — читай инструкцию в конце файла)
    "FRAGMENT_API_KEY": "",
    "FRAGMENT_ENABLED": False,
    # Накопительные скидки (сумма потраченных рублей → % скидки)
    "CUMULATIVE_DISCOUNTS": {
        "1000":  3,   # потратил 1000₽  → -3%
        "5000":  5,   # потратил 5000₽  → -5%
        "15000": 8,   # потратил 15000₽ → -8%
        "30000": 12,  # потратил 30000₽ → -12%
    },
    # Ежедневный отчёт — время в МСК (час, минута)
    "DAILY_REPORT_HOUR":   9,
    "DAILY_REPORT_MINUTE": 0,
    # Напоминание о зависших заказах (минут)
    "PENDING_ALERT_MINUTES": 30,
    # Уведомления клиенту каждые N минут пока ожидает
    "ORDER_REMIND_INTERVAL": 5,
    # Максимум напоминаний клиенту
    "ORDER_REMIND_MAX": 4,
}

SETTINGS_FILE = "settings.json"
DB_FILE       = "orders.json"
USERS_FILE    = "users.json"
PROMOS_FILE   = "promos.json"
REVIEWS_FILE  = "reviews.json"

def load_settings() -> dict:
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            s = json.load(f)
        # добавляем новые ключи если их нет
        for k, v in DEFAULT_SETTINGS.items():
            if k not in s:
                s[k] = v
        return s
    save_settings(DEFAULT_SETTINGS.copy())
    return DEFAULT_SETTINGS.copy()

def save_settings(s: dict):
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=2)

# Глобальный объект настроек
S = load_settings()

# ═══════════════════════════════════════════════════════════════
#  БД — заказы, пользователи, промокоды, отзывы
# ═══════════════════════════════════════════════════════════════
def _load(path, default):
    return json.load(open(path, encoding="utf-8")) if os.path.exists(path) else default

def _save(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_db():    return _load(DB_FILE,      {"orders": [], "stats": {"total": 0, "revenue": 0}})
def save_db(d):   _save(DB_FILE, d)
def load_users(): return _load(USERS_FILE,   {})
def save_users(u):_save(USERS_FILE, u)
def load_promos():return _load(PROMOS_FILE,  {})
def save_promos(p):_save(PROMOS_FILE, p)
def load_reviews():return _load(REVIEWS_FILE, [])
def save_reviews(r):_save(REVIEWS_FILE, r)

def add_order(db, order):
    db["orders"].append(order)
    db["stats"]["total"] += 1
    db["stats"]["revenue"] = db["stats"].get("revenue", 0) + order.get("amount_rub", 0)
    save_db(db)

def get_order(db, oid):
    return next((o for o in db["orders"] if o["id"] == oid), None)

def update_order(db, oid, **kwargs):
    for o in db["orders"]:
        if o["id"] == oid:
            o.update(kwargs)
            o["updated_at"] = datetime.now().isoformat()
            break
    save_db(db)

def ensure_user(uid: int, username: str = "", ref_by: int = None) -> dict:
    users = load_users()
    key = str(uid)
    if key not in users:
        ref_code = hashlib.md5(str(uid).encode()).hexdigest()[:8].upper()
        users[key] = {
            "id": uid, "username": username, "ref_code": ref_code,
            "ref_by": ref_by, "ref_count": 0, "ref_balance": 0.0,
            "total_spent": 0.0, "orders_count": 0,
            "joined_at": datetime.now().isoformat(), "first_purchase": False,
        }
        if ref_by and str(ref_by) in users:
            users[str(ref_by)]["ref_count"] = users[str(ref_by)].get("ref_count", 0) + 1
        save_users(users)
    return users[key]

def get_user(uid: int) -> dict:
    return load_users().get(str(uid), {})

def add_ref_balance(inviter_id: int, amount: float):
    users = load_users()
    key = str(inviter_id)
    if key in users:
        users[key]["ref_balance"] = round(users[key].get("ref_balance", 0) + amount, 2)
        save_users(users)

def get_promo(code: str):
    return load_promos().get(code.upper())

def use_promo(code: str, uid: int) -> bool:
    promos = load_promos()
    p = promos.get(code.upper())
    if not p or p.get("uses_left", 0) <= 0: return False
    if str(uid) in p.get("used_by", []): return False
    if p.get("expires_at") and datetime.fromisoformat(p["expires_at"]) < datetime.now(): return False
    p["uses_left"] -= 1
    p.setdefault("used_by", []).append(str(uid))
    save_promos(promos)
    return True

def create_promo(code: str, discount: int, uses: int, days: int = 30):
    promos = load_promos()
    promos[code.upper()] = {
        "code": code.upper(), "discount_percent": discount,
        "uses_left": uses, "total_uses": uses, "used_by": [],
        "created_at": datetime.now().isoformat(),
        "expires_at": (datetime.now() + timedelta(days=days)).isoformat(),
    }
    save_promos(promos)

# ═══════════════════════════════════════════════════════════════
#  FSM СОСТОЯНИЯ
# ═══════════════════════════════════════════════════════════════
class BuyStars(StatesGroup):
    username = State(); amount = State(); promo = State(); payment = State()

class BuyPremium(StatesGroup):
    username = State(); period = State(); promo = State(); payment = State()

class AdminSt(StatesGroup):
    broadcast      = State()
    promo_code     = State(); promo_disc  = State(); promo_uses = State()
    # Настройки
    set_star_price = State()
    set_prem_price = State(); set_prem_which = State()
    set_sbp_phone  = State(); set_sbp_bank   = State(); set_sbp_name = State()
    set_channel    = State(); set_support    = State()
    add_admin      = State(); del_admin      = State()
    set_ton_wallet = State()
    set_fragment      = State()
    set_cum_discount  = State()
    cabinet_filter    = State()

class ReviewSt(StatesGroup):
    text = State(); rating = State()

# ═══════════════════════════════════════════════════════════════
#  УТИЛИТЫ
# ═══════════════════════════════════════════════════════════════
def is_admin(uid: int) -> bool:
    return uid in S.get("ADMINS", [])

def fmt(n: float) -> str:      return f"{round(n):,} ₽".replace(",", " ")
def fmt_kzt(n: float) -> str:  return f"{round(n * S['RATE_KZT']):,} ₸".replace(",", " ")
def fmt_usd(n: float) -> str:  return f"${n * S['RATE_USD']:.2f}"

def new_oid() -> str: return str(uuid.uuid4())[:8].upper()

def ref_link(code: str) -> str:
    return f"https://t.me/{S['BOT_USERNAME']}?start=ref_{code}"

async def notify_admins(bot: Bot, text: str, markup=None):
    for aid in S.get("ADMINS", []):
        try:
            await bot.send_message(aid, text, reply_markup=markup, parse_mode="HTML")
        except Exception as e:
            logging.error(f"notify admin {aid}: {e}")

# ═══════════════════════════════════════════════════════════════
#  FRAGMENT API — автовыдача звёзд
# ═══════════════════════════════════════════════════════════════
async def fragment_send_stars(recipient_username: str, count: int) -> dict:
    """
    Попытка автовыдачи через Fragment API.
    
    ВАЖНО: У Fragment нет публичного REST API для прямой отправки звёзд.
    Официальный способ — через Telegram Bot API метод sendGift (только реакции)
    или через fragment.com вручную.
    
    Реальные варианты автовыдачи:
    1. Сторонние сервисы-посредники (starsbot.ru, tgstars и т.д.)
    2. Telegram Payments API (официально, требует верификацию)
    3. Ручная выдача через fragment.com (надёжнее всего)
    
    Здесь реализована заглушка — если ключ не задан, возвращает инструкцию.
    """
    if not S.get("FRAGMENT_ENABLED") or not S.get("FRAGMENT_API_KEY"):
        return {"success": False, "manual": True}
    
    # Если у тебя есть доступ к стороннему API (например starsbot.ru)
    # раскомментируй и замени URL:
    # try:
    #     async with aiohttp.ClientSession() as sess:
    #         resp = await sess.post(
    #             "https://api.starsbot.ru/v1/send",
    #             headers={"Authorization": f"Bearer {S['FRAGMENT_API_KEY']}"},
    #             json={"username": recipient_username, "amount": count},
    #             timeout=aiohttp.ClientTimeout(total=30)
    #         )
    #         data = await resp.json()
    #         return {"success": data.get("ok", False), "data": data}
    # except Exception as e:
    #     logging.error(f"Fragment API error: {e}")
    #     return {"success": False, "error": str(e)}
    
    return {"success": False, "manual": True}

# ═══════════════════════════════════════════════════════════════
#  TON БЛОКЧЕЙН — автопроверка
# ═══════════════════════════════════════════════════════════════
async def check_ton_payment(amount_ton: float, order_id: str, timeout_min: int = 20) -> bool:
    wallet = S["TON_WALLET"]
    headers = {"Accept": "application/json"}
    if S.get("TONAPI_KEY"):
        headers["Authorization"] = f"Bearer {S['TONAPI_KEY']}"
    url = f"https://tonapi.io/v2/accounts/{wallet}/transactions?limit=20"
    expected_nano = int(amount_ton * 1_000_000_000 * 0.97)
    deadline = datetime.now() + timedelta(minutes=timeout_min)
    logging.info(f"[TON] Ждём {amount_ton:.4f} TON для #{order_id}")
    while datetime.now() < deadline:
        try:
            async with aiohttp.ClientSession() as sess:
                async with sess.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as r:
                    if r.status != 200:
                        await asyncio.sleep(15); continue
                    data = await r.json()
            cutoff = int((datetime.now() - timedelta(minutes=timeout_min)).timestamp())
            for tx in data.get("transactions", []):
                if tx.get("out_msgs"): continue
                val = int(tx.get("in_msg", {}).get("value", 0))
                if tx.get("utime", 0) < cutoff: continue
                if val >= expected_nano:
                    logging.info(f"[TON] ✅ {val/1e9:.4f} TON найдено для #{order_id}")
                    return True
        except asyncio.CancelledError: return False
        except Exception as e: logging.error(f"[TON] {e}")
        await asyncio.sleep(15)
    return False

# ═══════════════════════════════════════════════════════════════
#  КЛАВИАТУРЫ
# ═══════════════════════════════════════════════════════════════
def kb_main():
    b = ReplyKeyboardBuilder()
    b.button(text="⭐ Купить Stars")
    b.button(text="👑 Купить Premium")
    b.button(text="📂 Личный кабинет")
    b.button(text="👥 Рефералы")
    b.button(text="⭐ Оставить отзыв")
    b.button(text="💬 Поддержка")
    b.adjust(2, 2, 2)
    return b.as_markup(resize_keyboard=True)

def kb_stars():
    b = InlineKeyboardBuilder()
    for n in [50, 100, 250, 500, 1000]:
        p = round(n * S["STAR_PRICE_RUB"])
        b.button(text=f"⭐ {n} — {p} ₽", callback_data=f"stars:{n}")
    b.button(text="✏️ Своё количество (50–1000)", callback_data="stars:custom")
    b.button(text="❌ Отмена", callback_data="cancel")
    b.adjust(1)
    return b.as_markup()

def kb_premium():
    b = InlineKeyboardBuilder()
    for m, p in S["PREMIUM_PRICES"].items():
        label = {"3":"3 месяца","6":"6 месяцев","12":"12 месяцев 🔥"}.get(str(m), f"{m} мес.")
        b.button(text=f"{label} — {p:,} ₽".replace(",", " "), callback_data=f"prem:{m}")
    b.button(text="❌ Отмена", callback_data="cancel")
    b.adjust(1)
    return b.as_markup()

def kb_promo():
    b = InlineKeyboardBuilder()
    b.button(text="🎟 Ввести промокод", callback_data="enter_promo")
    b.button(text="➡️ Пропустить",      callback_data="skip_promo")
    b.button(text="❌ Отмена",          callback_data="cancel")
    b.adjust(2, 1)
    return b.as_markup()

def kb_payment(oid: str):
    b = InlineKeyboardBuilder()
    b.button(text="⚡ СБП (перевод)",      callback_data=f"pay:sbp:{oid}")
    b.button(text="💎 TON (авто-чек)",     callback_data=f"pay:ton:{oid}")
    b.button(text="❌ Отмена",             callback_data="cancel")
    b.adjust(1)
    return b.as_markup()

def kb_confirm_paid(oid: str):
    b = InlineKeyboardBuilder()
    b.button(text="✅ Я оплатил(а)", callback_data=f"paid:{oid}")
    b.button(text="❌ Отмена",       callback_data="cancel")
    b.adjust(1)
    return b.as_markup()

def kb_admin_order(oid: str):
    b = InlineKeyboardBuilder()
    b.button(text="✅ Подтвердить + выдать", callback_data=f"admin:confirm:{oid}")
    b.button(text="❌ Отклонить",            callback_data=f"admin:reject:{oid}")
    b.adjust(2)
    return b.as_markup()

def kb_cancel():
    b = InlineKeyboardBuilder()
    b.button(text="❌ Отмена", callback_data="cancel")
    return b.as_markup()

def kb_admin_main():
    b = InlineKeyboardBuilder()
    b.button(text="📊 Статистика",       callback_data="adm:stats")
    b.button(text="📋 Заказы",           callback_data="adm:orders")
    b.button(text="⚙️ Настройки",        callback_data="adm:settings")
    b.button(text="🎟 Промокоды",        callback_data="adm:promos")
    b.button(text="📢 Рассылка",         callback_data="adm:broadcast")
    b.button(text="👥 Рефералы топ",     callback_data="adm:reftop")
    b.button(text="⭐ Отзывы",          callback_data="adm:reviews")
    b.adjust(2, 2, 2, 1)
    return b.as_markup()

def kb_admin_settings():
    b = InlineKeyboardBuilder()
    b.button(text="💰 Цена Stars",       callback_data="adm:set:star_price")
    b.button(text="👑 Цена Premium",     callback_data="adm:set:prem_price")
    b.button(text="⚡ СБП реквизиты",   callback_data="adm:set:sbp")
    b.button(text="📢 Ссылка канала",    callback_data="adm:set:channel")
    b.button(text="💬 Юз поддержки",    callback_data="adm:set:support")
    b.button(text="👤 Добавить админа",  callback_data="adm:set:add_admin")
    b.button(text="🗑 Удалить админа",   callback_data="adm:set:del_admin")
    b.button(text="💎 TON кошелёк",     callback_data="adm:set:ton")
    b.button(text="◀️ Назад",            callback_data="adm:back")
    b.adjust(2, 2, 2, 2, 1)
    return b.as_markup()

def kb_admin_promos():
    b = InlineKeyboardBuilder()
    b.button(text="➕ Создать промокод", callback_data="adm:promo:create")
    b.button(text="📋 Список",           callback_data="adm:promo:list")
    b.button(text="◀️ Назад",            callback_data="adm:back")
    b.adjust(2, 1)
    return b.as_markup()

# ═══════════════════════════════════════════════════════════════
#  /start
# ═══════════════════════════════════════════════════════════════
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    user = message.from_user
    args = message.text.split(" ", 1)[1] if " " in message.text else ""
    ref_by = None
    if args.startswith("ref_"):
        code = args[4:]
        for uid, ud in load_users().items():
            if ud.get("ref_code") == code and int(uid) != user.id:
                ref_by = int(uid); break
    udata = ensure_user(user.id, user.username or "", ref_by)
    first_ref = ref_by and not udata.get("first_purchase")
    ref_line = f"\n\n🎁 Скидка <b>{S['REF_BONUS_PCT']}%</b> на первую покупку уже активна!" if first_ref else ""
    await message.answer(
        f"👋 Привет, <b>{user.first_name or 'друг'}</b>!{ref_line}\n\n"
        f"🏪 <b>KrutoiShop</b> — магазин Telegram Stars и Premium\n\n"
        f"⭐ Stars — от <b>{fmt(S['STAR_PRICE_RUB'])}/шт</b>\n"
        f"👑 Premium — от <b>{fmt(int(S['PREMIUM_PRICES']['3']))}/мес</b>\n\n"
        f"⚡ Быстро · 🔒 Безопасно · 💬 Поддержка 24/7",
        reply_markup=kb_main(), parse_mode="HTML"
    )

# ═══════════════════════════════════════════════════════════════
#  ПОКУПКА STARS
# ═══════════════════════════════════════════════════════════════
async def buy_stars(message: types.Message, state: FSMContext):
    await state.set_state(BuyStars.username)
    await message.answer(
        "⭐ <b>Покупка Telegram Stars</b>\n\n"
        "Введи <b>@username</b> получателя:",
        reply_markup=kb_cancel(), parse_mode="HTML"
    )

async def stars_username(message: types.Message, state: FSMContext):
    u = message.text.strip().lstrip("@")
    if len(u) < 3 or " " in u:
        await message.answer("❌ Некорректный юзернейм. Попробуй ещё раз:"); return
    await state.update_data(recipient=u)
    await state.set_state(BuyStars.amount)
    await message.answer(
        f"✅ Получатель: <b>@{u}</b>\n\nВыбери количество звёзд:",
        reply_markup=kb_stars(), parse_mode="HTML"
    )

async def stars_amount_cb(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    val = callback.data.split(":")[1]
    if val == "custom":
        await state.update_data(custom=True)
        await callback.message.edit_text("✏️ Введи количество (50–1000):", reply_markup=kb_cancel())
        return
    await state.update_data(count=int(val), custom=False)
    await state.set_state(BuyStars.promo)
    await callback.message.edit_text("🎟 Есть промокод?", reply_markup=kb_promo())

async def stars_custom_amount(message: types.Message, state: FSMContext):
    data = await state.get_data()
    if not data.get("custom"): return
    try:
        n = int(message.text.strip())
        if not 50 <= n <= 1000: raise ValueError
    except ValueError:
        await message.answer("❌ Введи число от 50 до 1000:"); return
    await state.update_data(count=n, custom=False)
    await state.set_state(BuyStars.promo)
    await message.answer("🎟 Есть промокод?", reply_markup=kb_promo())

# ═══════════════════════════════════════════════════════════════
#  ПОКУПКА PREMIUM
# ═══════════════════════════════════════════════════════════════
async def buy_premium(message: types.Message, state: FSMContext):
    await state.set_state(BuyPremium.username)
    await message.answer(
        "👑 <b>Покупка Telegram Premium</b>\n\nВведи <b>@username</b> получателя:",
        reply_markup=kb_cancel(), parse_mode="HTML"
    )

async def prem_username(message: types.Message, state: FSMContext):
    u = message.text.strip().lstrip("@")
    if len(u) < 3 or " " in u:
        await message.answer("❌ Некорректный юзернейм:"); return
    await state.update_data(recipient=u)
    await state.set_state(BuyPremium.period)
    await message.answer(f"✅ Получатель: <b>@{u}</b>\n\nВыбери период:", reply_markup=kb_premium(), parse_mode="HTML")

async def prem_period_cb(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    months = callback.data.split(":")[1]
    await state.update_data(months=months)
    await state.set_state(BuyPremium.promo)
    await callback.message.edit_text("🎟 Есть промокод?", reply_markup=kb_promo())

# ═══════════════════════════════════════════════════════════════
#  ПРОМОКОДЫ — enter / skip / text
# ═══════════════════════════════════════════════════════════════
async def enter_promo_cb(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.edit_text("🎟 Введи промокод:", reply_markup=kb_cancel())

async def skip_promo_cb(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.update_data(promo=None, disc=0)
    await _show_summary(callback.message, state, edit=True)

async def promo_text(message: types.Message, state: FSMContext):
    code = message.text.strip().upper()
    promo = get_promo(code)
    if not promo:
        await message.answer("❌ Промокод не найден:", reply_markup=kb_promo()); return
    if promo.get("uses_left", 0) <= 0:
        await message.answer("❌ Промокод исчерпан.", reply_markup=kb_promo()); return
    if str(message.from_user.id) in promo.get("used_by", []):
        await message.answer("❌ Ты уже использовал этот промокод.", reply_markup=kb_promo()); return
    if promo.get("expires_at") and datetime.fromisoformat(promo["expires_at"]) < datetime.now():
        await message.answer("❌ Срок действия истёк.", reply_markup=kb_promo()); return
    disc = promo["discount_percent"]
    await state.update_data(promo=code, disc=disc)
    await message.answer(f"✅ Промокод <b>{code}</b> применён! Скидка <b>{disc}%</b>", parse_mode="HTML")
    await _show_summary(message, state, edit=False)

# ═══════════════════════════════════════════════════════════════
#  ИТОГОВЫЙ ЗАКАЗ
# ═══════════════════════════════════════════════════════════════
async def _show_summary(msg, state: FSMContext, edit=False):
    data   = await state.get_data()
    uid    = msg.chat.id
    udata  = get_user(uid)
    promo_disc = data.get("disc", 0)
    ref_disc   = S["REF_BONUS_PCT"] if (not udata.get("first_purchase") and udata.get("ref_by")) else 0
    total_disc = min(promo_disc + ref_disc, 50)
    oid = new_oid()

    is_stars = "count" in data
    if is_stars:
        count = data["count"]
        base  = round(count * S["STAR_PRICE_RUB"], 2)
        label = f"⭐ {count} Telegram Stars"
    else:
        months = data["months"]
        base   = int(S["PREMIUM_PRICES"][str(months)])
        label  = f"👑 Premium {months} мес."

    final = round(base * (1 - total_disc / 100), 2)
    await state.update_data(oid=oid, base=base, final=final, disc=total_disc, label=label)

    cur_state = await state.get_state()
    if is_stars: await state.set_state(BuyStars.payment)
    else:        await state.set_state(BuyPremium.payment)

    disc_line = f"\n🎁 Скидка <b>-{total_disc}%</b>\n" if total_disc else "\n"
    text = (
        f"📋 <b>Детали заказа</b>\n\n"
        f"📦 {label}\n"
        f"👤 Получатель: <b>@{data.get('recipient','?')}</b>\n"
        f"💰 Цена: <b>{fmt(base)}</b>"
        f"{disc_line}"
        f"{'💳 К оплате: <b>' + fmt(final) + '</b>' if total_disc else ''}\n"
        f"{fmt_kzt(final)} / {fmt_usd(final)}\n\n"
        f"🆔 Заказ: <code>{oid}</code>\n\n"
        f"Выбери способ оплаты:"
    )
    if edit: await msg.edit_text(text, reply_markup=kb_payment(oid), parse_mode="HTML")
    else:    await msg.answer(text, reply_markup=kb_payment(oid), parse_mode="HTML")

# ═══════════════════════════════════════════════════════════════
#  ОПЛАТА — СБП
# ═══════════════════════════════════════════════════════════════
async def pay_sbp(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    oid  = data.get("oid", "???")
    await callback.message.edit_text(
        f"⚡ <b>Оплата через СБП</b>\n\n"
        f"Переведи <b>{fmt(data.get('final', 0))}</b>:\n\n"
        f"📱 Телефон: <code>{S['SBP_PHONE']}</code>\n"
        f"🏦 Банк: {S['SBP_BANK']}\n"
        f"👤 Получатель: {S['SBP_NAME']}\n\n"
        f"⚠️ В комментарии обязательно:\n<code>Заказ {oid}</code>\n\n"
        f"После перевода нажми 👇",
        reply_markup=kb_confirm_paid(oid), parse_mode="HTML"
    )

# ═══════════════════════════════════════════════════════════════
#  ОПЛАТА — TON авточек
# ═══════════════════════════════════════════════════════════════
async def pay_ton(callback: types.CallbackQuery, state: FSMContext, bot: Bot):
    await callback.answer()
    data = await state.get_data()
    oid  = data.get("oid", "???")
    rub  = data.get("final", 0)
    usd  = rub * S["RATE_USD"]
    ton  = usd / S["TON_PRICE_USD"]
    await state.update_data(ton_amount=ton)
    await callback.message.edit_text(
        f"💎 <b>Оплата TON</b>\n\n"
        f"Отправь ровно <b>{ton:.4f} TON</b> на:\n"
        f"<code>{S['TON_WALLET']}</code>\n\n"
        f"⏱ Бот сам проверит транзакцию (до 20 мин)\n"
        f"💬 Комментарий писать не нужно\n\n"
        f"🔄 Ожидаем поступление...",
        parse_mode="HTML"
    )
    asyncio.create_task(_ton_watcher(bot, callback.from_user, oid, ton, data, state))

async def _ton_watcher(bot: Bot, tg_user, oid: str, ton: float, data: dict, state: FSMContext):
    paid = await check_ton_payment(ton, oid)
    if paid:
        db = load_db()
        is_stars = "count" in data
        order = {
            "id": oid, "user_id": tg_user.id,
            "username": tg_user.username or str(tg_user.id),
            "recipient": data.get("recipient", "?"),
            "type": "stars" if is_stars else "premium",
            "count": data.get("count"), "months": data.get("months"),
            "amount_rub": data.get("final", 0), "base_rub": data.get("base", 0),
            "promo": data.get("promo"), "discount": data.get("disc", 0),
            "payment": "TON", "status": "confirmed", "auto": True,
            "created_at": datetime.now().isoformat(), "updated_at": datetime.now().isoformat(),
        }
        add_order(db, order)
        if data.get("promo"): use_promo(data["promo"], tg_user.id)
        _apply_ref_bonus(tg_user.id, data.get("final", 0))
        await state.clear()

        # Пробуем автовыдачу
        auto_result = {"manual": True}
        if is_stars:
            auto_result = await fragment_send_stars(data.get("recipient", ""), data.get("count", 0))

        label = data.get("label", "Товар")
        if auto_result.get("success"):
            status_line = "✅ Звёзды отправлены автоматически!"
        else:
            status_line = "⏳ Администратор выдаст товар в ближайшее время."

        await bot.send_message(
            tg_user.id,
            f"✅ <b>Оплата TON подтверждена!</b>\n\n"
            f"🎉 Заказ <code>{oid}</code>\n📦 {label}\n"
            f"🎯 @{data.get('recipient','?')}\n\n{status_line}",
            parse_mode="HTML"
        )
        await notify_admins(bot,
            f"💎 <b>TON авто #{oid}</b>\n📦 {label}\n"
            f"🎯 @{data.get('recipient','?')}\n💰 {fmt(data.get('final',0))}\n"
            f"{'✅ Выдано авто' if auto_result.get('success') else '⚠️ Нужна ручная выдача'}",
            None if auto_result.get("success") else kb_admin_order(oid)
        )
    else:
        await state.clear()
        try:
            await bot.send_message(
                tg_user.id,
                f"⏰ <b>Время оплаты истекло</b> (заказ {oid})\n\n"
                f"Транзакция не найдена. Если перевёл — напиши в поддержку @{S['SUPPORT_USERNAME']}",
                parse_mode="HTML"
            )
        except Exception: pass

def _apply_ref_bonus(uid: int, amount: float):
    users = load_users()
    key   = str(uid)
    udata = users.get(key, {})
    if udata.get("ref_by") and not udata.get("first_purchase"):
        reward = round(amount * S["REF_REWARD_PCT"] / 100, 2)
        add_ref_balance(udata["ref_by"], reward)
    if key in users:
        users[key]["first_purchase"]  = True
        users[key]["orders_count"]    = users[key].get("orders_count", 0) + 1
        users[key]["total_spent"]     = round(users[key].get("total_spent", 0) + amount, 2)
        save_users(users)

# ═══════════════════════════════════════════════════════════════
#  "Я оплатил" — СБП
# ═══════════════════════════════════════════════════════════════
async def user_paid(callback: types.CallbackQuery, state: FSMContext, bot: Bot):
    await callback.answer("✅ Принято!")
    oid  = callback.data.split(":")[1]
    data = await state.get_data()
    uid  = callback.from_user.id
    is_stars = "count" in data
    order = {
        "id": oid, "user_id": uid,
        "username": callback.from_user.username or str(uid),
        "recipient": data.get("recipient", "?"),
        "type": "stars" if is_stars else "premium",
        "count": data.get("count"), "months": data.get("months"),
        "amount_rub": data.get("final", 0), "base_rub": data.get("base", 0),
        "promo": data.get("promo"), "discount": data.get("disc", 0),
        "payment": "SBP", "status": "pending", "auto": False,
        "created_at": datetime.now().isoformat(), "updated_at": datetime.now().isoformat(),
    }
    db = load_db(); add_order(db, order)
    await state.clear()
    await callback.message.edit_text(
        f"⏳ <b>Заказ #{oid} ожидает проверки</b>\n\n"
        f"Администратор проверит оплату и выдаст товар.\n"
        f"Обычно до 15 минут.\n\n"
        f"Вопросы: @{S['SUPPORT_USERNAME']}",
        parse_mode="HTML"
    )
    label = data.get("label", "Товар")
    await notify_admins(bot,
        f"🔔 <b>НОВЫЙ ЗАКАЗ #{oid}</b> (СБП)\n\n"
        f"📦 {label}\n🎯 @{data.get('recipient','?')}\n"
        f"👤 @{order['username']} (id:{uid})\n"
        f"💰 {fmt(data.get('final',0))}"
        f"{' · скидка ' + str(data.get('disc',0)) + '%' if data.get('disc') else ''}",
        kb_admin_order(oid)
    )

# ═══════════════════════════════════════════════════════════════
#  МОИ ЗАКАЗЫ
# ═══════════════════════════════════════════════════════════════
async def my_orders(message: types.Message):
    db     = load_db()
    uid    = message.from_user.id
    orders = [o for o in db["orders"] if o.get("user_id") == uid]
    if not orders:
        await message.answer("📭 Заказов пока нет.", reply_markup=kb_main()); return
    se = {"pending":"⏳","confirmed":"✅","rejected":"❌"}
    sl = {"pending":"Ожидает","confirmed":"Выполнен","rejected":"Отклонён"}
    text = "📦 <b>Твои последние заказы:</b>\n\n"
    for o in reversed(orders[-5:]):
        svc = o.get("label") or (f"⭐{o.get('count')} Stars" if o["type"]=="stars" else f"👑Prem{o.get('months')}м")
        st  = o.get("status","pending")
        text += f"{se.get(st,'❓')} <code>{o['id']}</code> · {svc} · {fmt(o['amount_rub'])}\n   {sl.get(st,st)} · {o['created_at'][:10]}\n\n"
    await message.answer(text, parse_mode="HTML")

# ═══════════════════════════════════════════════════════════════
#  РЕФЕРАЛЫ
# ═══════════════════════════════════════════════════════════════
async def ref_menu(message: types.Message):
    uid   = message.from_user.id
    udata = ensure_user(uid, message.from_user.username or "")
    link  = ref_link(udata["ref_code"])
    bal   = udata.get("ref_balance", 0)
    b = InlineKeyboardBuilder()
    b.button(text="💸 Запросить вывод", callback_data="ref:withdraw")
    b.adjust(1)
    await message.answer(
        f"👥 <b>Реферальная программа</b>\n\n"
        f"🔗 Твоя ссылка:\n<code>{link}</code>\n\n"
        f"👤 Приглашено: <b>{udata.get('ref_count',0)}</b>\n"
        f"💰 Баланс бонусов: <b>{fmt(bal)}</b>\n\n"
        f"• Друг по ссылке → скидка <b>{S['REF_BONUS_PCT']}%</b> на первую покупку\n"
        f"• Ты получаешь <b>{S['REF_REWARD_PCT']}%</b> от его заказа\n"
        f"• Минимум вывода: <b>{fmt(S['REF_MIN_WITHDRAW'])}</b>",
        reply_markup=b.as_markup(), parse_mode="HTML"
    )

async def ref_withdraw(callback: types.CallbackQuery, bot: Bot):
    await callback.answer()
    uid   = callback.from_user.id
    udata = get_user(uid)
    bal   = udata.get("ref_balance", 0)
    if bal < S["REF_MIN_WITHDRAW"]:
        await callback.message.answer(
            f"❌ Минимум для вывода: <b>{fmt(S['REF_MIN_WITHDRAW'])}</b>\nТвой баланс: <b>{fmt(bal)}</b>",
            parse_mode="HTML"); return
    b = InlineKeyboardBuilder()
    b.button(text="✅ Выплатить", callback_data=f"admin:payref:{uid}:{round(bal)}")
    b.button(text="❌ Отклонить", callback_data=f"admin:denyref:{uid}")
    b.adjust(2)
    await notify_admins(bot,
        f"💸 <b>ВЫВОД РЕФЕРАЛЬНОГО БОНУСА</b>\n"
        f"👤 @{udata.get('username','?')} (id:{uid})\n💰 {fmt(bal)}",
        b.as_markup()
    )
    await callback.message.answer(f"✅ Запрос на вывод <b>{fmt(bal)}</b> отправлен.", parse_mode="HTML")

# ═══════════════════════════════════════════════════════════════
#  ОТЗЫВЫ
# ═══════════════════════════════════════════════════════════════
async def leave_review(message: types.Message, state: FSMContext):
    # проверяем что есть выполненные заказы
    db  = load_db()
    uid = message.from_user.id
    has = any(o.get("user_id") == uid and o.get("status") == "confirmed" for o in db["orders"])
    if not has:
        await message.answer("❌ Отзыв можно оставить только после выполненного заказа."); return
    await state.set_state(ReviewSt.text)
    await message.answer("✍️ Напиши свой отзыв (1–500 символов):", reply_markup=kb_cancel())

async def review_got_text(message: types.Message, state: FSMContext):
    text = message.text.strip()
    if len(text) < 5 or len(text) > 500:
        await message.answer("❌ Отзыв должен быть от 5 до 500 символов:"); return
    await state.update_data(review_text=text)
    await state.set_state(ReviewSt.rating)
    b = InlineKeyboardBuilder()
    for i in range(1, 6):
        b.button(text="⭐" * i, callback_data=f"review:{i}")
    b.adjust(1)
    await message.answer("Оцени нас:", reply_markup=b.as_markup())

async def review_got_rating(callback: types.CallbackQuery, state: FSMContext, bot: Bot):
    await callback.answer()
    rating = int(callback.data.split(":")[1])
    data   = await state.get_data()
    await state.clear()
    reviews = load_reviews()
    reviews.append({
        "user_id": callback.from_user.id,
        "username": callback.from_user.username or str(callback.from_user.id),
        "text": data.get("review_text", ""),
        "rating": rating,
        "date": datetime.now().isoformat(),
    })
    save_reviews(reviews)
    await callback.message.edit_text(
        f"{'⭐' * rating}\n\n✅ Спасибо за отзыв! Это помогает нам расти 💜"
    )
    await notify_admins(bot,
        f"⭐ <b>Новый отзыв {'⭐' * rating}</b>\n"
        f"👤 @{callback.from_user.username or callback.from_user.id}\n\n"
        f"«{data.get('review_text','')}»",
    )

# ═══════════════════════════════════════════════════════════════
#  ПОДДЕРЖКА
# ═══════════════════════════════════════════════════════════════
async def support_msg(message: types.Message):
    await message.answer(
        f"💬 <b>Поддержка KrutoiShop</b>\n\n"
        f"Пиши напрямую: @{S['SUPPORT_USERNAME']}\n"
        f"Время ответа: до 15 минут.\n\n"
        f"📎 Укажи ID заказа при обращении.",
        parse_mode="HTML"
    )

# ═══════════════════════════════════════════════════════════════
#  ОТМЕНА
# ═══════════════════════════════════════════════════════════════
async def cancel_cb(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer("Отменено")
    await callback.message.edit_text("❌ Отменено.")
    await callback.message.answer("Главное меню:", reply_markup=kb_main())

async def cmd_cancel(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Отменено.", reply_markup=kb_main())

# ═══════════════════════════════════════════════════════════════
#  ADMIN — главная панель
# ═══════════════════════════════════════════════════════════════
async def cmd_admin(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Нет доступа."); return
    db = load_db()
    pending = len([o for o in db["orders"] if o["status"] == "pending"])
    await message.answer(
        f"🛠 <b>Панель администратора</b>\n\n"
        f"📦 Заказов: <b>{db['stats']['total']}</b>\n"
        f"⏳ Ожидают: <b>{pending}</b>\n"
        f"💰 Выручка: <b>{fmt(db['stats'].get('revenue',0))}</b>\n\n"
        f"⭐ Цена Stars: <b>{fmt(S['STAR_PRICE_RUB'])}/шт</b>\n"
        f"💬 Поддержка: @{S['SUPPORT_USERNAME']}",
        reply_markup=kb_admin_main(), parse_mode="HTML"
    )

async def adm_back(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔", show_alert=True); return
    await callback.answer()
    db = load_db()
    pending = len([o for o in db["orders"] if o["status"] == "pending"])
    await callback.message.edit_text(
        f"🛠 <b>Панель администратора</b>\n\n"
        f"📦 Заказов: <b>{db['stats']['total']}</b> · ⏳ Ожидают: <b>{pending}</b>\n"
        f"💰 Выручка: <b>{fmt(db['stats'].get('revenue',0))}</b>",
        reply_markup=kb_admin_main(), parse_mode="HTML"
    )

# ═══════════════════════════════════════════════════════════════
#  ADMIN — статистика
# ═══════════════════════════════════════════════════════════════
async def adm_stats(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔", show_alert=True); return
    await callback.answer()
    db = load_db(); orders = db["orders"]
    confirmed = sum(1 for o in orders if o["status"] == "confirmed")
    pending   = sum(1 for o in orders if o["status"] == "pending")
    rejected  = sum(1 for o in orders if o["status"] == "rejected")
    auto      = sum(1 for o in orders if o.get("auto"))
    users     = load_users()
    reviews   = load_reviews()
    avg_rat   = round(sum(r["rating"] for r in reviews) / len(reviews), 2) if reviews else 0
    await callback.message.edit_text(
        f"📊 <b>Статистика</b>\n\n"
        f"📦 Всего: <b>{db['stats']['total']}</b>\n"
        f"✅ Выполнено: <b>{confirmed}</b>\n"
        f"⏳ Ожидают: <b>{pending}</b>\n"
        f"❌ Отклонено: <b>{rejected}</b>\n"
        f"🤖 Авто-выдача: <b>{auto}</b>\n\n"
        f"⭐ Stars заказов: <b>{sum(1 for o in orders if o['type']=='stars')}</b>\n"
        f"👑 Premium заказов: <b>{sum(1 for o in orders if o['type']=='premium')}</b>\n\n"
        f"💰 Выручка: <b>{fmt(db['stats'].get('revenue',0))}</b>\n"
        f"👥 Пользователей: <b>{len(users)}</b>\n"
        f"⭐ Отзывов: <b>{len(reviews)}</b> · Рейтинг: <b>{avg_rat}</b>",
        reply_markup=kb_admin_main(), parse_mode="HTML"
    )

# ═══════════════════════════════════════════════════════════════
#  ADMIN — список заказов
# ═══════════════════════════════════════════════════════════════
async def adm_orders(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔", show_alert=True); return
    await callback.answer()
    db   = load_db()
    last = list(reversed(db["orders"][-10:]))
    if not last:
        await callback.message.edit_text("📭 Заказов нет.", reply_markup=kb_admin_main()); return
    se   = {"pending":"⏳","confirmed":"✅","rejected":"❌"}
    text = "📋 <b>Последние 10 заказов:</b>\n\n"
    for o in last:
        svc = o.get("label") or (f"Stars{o.get('count','')}" if o["type"]=="stars" else f"Prem{o.get('months','')}м")
        text += f"{se.get(o['status'],'❓')} <code>{o['id']}</code> @{o.get('recipient','?')} {svc} {fmt(o['amount_rub'])}\n"
    await callback.message.edit_text(text, reply_markup=kb_admin_main(), parse_mode="HTML")

# ═══════════════════════════════════════════════════════════════
#  ADMIN — подтверждение и отклонение заказа
# ═══════════════════════════════════════════════════════════════
async def adm_confirm(callback: types.CallbackQuery, bot: Bot):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔", show_alert=True); return
    oid = callback.data.split(":")[2]
    db  = load_db()
    order = get_order(db, oid)
    if not order:
        await callback.answer("❌ Не найден", show_alert=True); return

    update_order(db, oid, status="confirmed")
    if order.get("promo"): use_promo(order["promo"], order["user_id"])
    if not order.get("auto"): _apply_ref_bonus(order["user_id"], order["amount_rub"])

    # Пробуем автовыдачу Stars
    auto_result = {"manual": True}
    if order["type"] == "stars":
        auto_result = await fragment_send_stars(order.get("recipient",""), order.get("count",0))

    await callback.answer("✅ Подтверждено!")
    delivered_line = "✅ Звёзды отправлены автоматически!" if auto_result.get("success") else "📤 Выдай товар вручную через fragment.com"
    await callback.message.edit_text(
        callback.message.text + f"\n\n✅ <b>ВЫДАНО</b> — {callback.from_user.first_name}\n{delivered_line}",
        parse_mode="HTML"
    )
    svc = order.get("label") or (f"⭐ {order.get('count')} Stars" if order["type"]=="stars" else f"👑 Premium {order.get('months')} мес.")
    try:
        await bot.send_message(
            order["user_id"],
            f"🎉 <b>Заказ #{oid} выполнен!</b>\n\n📦 {svc}\n🎯 @{order.get('recipient','?')}\n\n"
            f"{'✅ Звёзды уже на аккаунте!' if auto_result.get('success') else 'Товар отправлен на аккаунт получателя.'}\n\n"
            f"Спасибо за покупку! ⭐\nОставь отзыв в главном меню 👇",
            parse_mode="HTML"
        )
    except Exception as e: logging.error(e)

async def adm_reject(callback: types.CallbackQuery, bot: Bot):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔", show_alert=True); return
    oid   = callback.data.split(":")[2]
    db    = load_db()
    order = get_order(db, oid)
    if not order:
        await callback.answer("❌ Не найден", show_alert=True); return
    update_order(db, oid, status="rejected")
    await callback.answer("❌ Отклонено")
    await callback.message.edit_text(callback.message.text + "\n\n❌ <b>ОТКЛОНЕНО</b>", parse_mode="HTML")
    try:
        await bot.send_message(
            order["user_id"],
            f"❌ <b>Заказ #{oid} отклонён</b>\n\n"
            f"Оплата не подтверждена. По вопросам: @{S['SUPPORT_USERNAME']}",
            parse_mode="HTML"
        )
    except Exception: pass

# ═══════════════════════════════════════════════════════════════
#  ADMIN — НАСТРОЙКИ (главное меню настроек)
# ═══════════════════════════════════════════════════════════════
async def adm_settings(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔", show_alert=True); return
    await callback.answer()
    prem = S["PREMIUM_PRICES"]
    admins_list = ", ".join(str(a) for a in S["ADMINS"])
    await callback.message.edit_text(
        f"⚙️ <b>Настройки магазина</b>\n\n"
        f"⭐ Цена Stars: <b>{fmt(S['STAR_PRICE_RUB'])}/шт</b>\n"
        f"👑 Premium: <b>{prem.get('3',0)}₽ / {prem.get('6',0)}₽ / {prem.get('12',0)}₽</b>\n"
        f"⚡ СБП: <b>{S['SBP_PHONE']}</b> · {S['SBP_BANK']}\n"
        f"💬 Поддержка: <b>@{S['SUPPORT_USERNAME']}</b>\n"
        f"📢 Канал: <b>{S['CHANNEL_NAME']}</b>\n"
        f"💎 TON: <code>{S['TON_WALLET'][:20]}...</code>\n"
        f"👤 Админы: <b>{admins_list}</b>",
        reply_markup=kb_admin_settings(), parse_mode="HTML"
    )

# --- Цена Stars ---
async def adm_set_star_price(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔", show_alert=True); return
    await callback.answer()
    await state.set_state(AdminSt.set_star_price)
    await callback.message.answer(
        f"💰 Текущая цена: <b>{fmt(S['STAR_PRICE_RUB'])}/звезда</b>\n\nВведи новую цену (например: 1.5):\n/cancel — отмена",
        parse_mode="HTML"
    )

async def adm_got_star_price(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    try:
        price = float(message.text.strip().replace(",", "."))
        if not 0.1 <= price <= 100: raise ValueError
    except ValueError:
        await message.answer("❌ Введи корректное число (например: 1.4):"); return
    S["STAR_PRICE_RUB"] = price
    save_settings(S)
    await state.clear()
    await message.answer(f"✅ Цена Stars обновлена: <b>{fmt(price)}/шт</b>", parse_mode="HTML")

# --- Цена Premium ---
async def adm_set_prem_price(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔", show_alert=True); return
    await callback.answer()
    b = InlineKeyboardBuilder()
    for m in ["3", "6", "12"]:
        b.button(text=f"{m} мес. — {S['PREMIUM_PRICES'].get(m,0)} ₽", callback_data=f"set_prem:{m}")
    b.button(text="◀️ Назад", callback_data="adm:settings")
    b.adjust(1)
    await callback.message.edit_text(
        "👑 Выбери период для изменения цены:",
        reply_markup=b.as_markup()
    )

async def adm_set_prem_which(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔", show_alert=True); return
    await callback.answer()
    months = callback.data.split(":")[1]
    await state.update_data(prem_months=months)
    await state.set_state(AdminSt.set_prem_price)
    await callback.message.answer(
        f"👑 Текущая цена на {months} мес.: <b>{S['PREMIUM_PRICES'].get(months,0)} ₽</b>\n\nВведи новую цену:\n/cancel — отмена",
        parse_mode="HTML"
    )

async def adm_got_prem_price(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    try:
        price = int(message.text.strip())
        if price < 1: raise ValueError
    except ValueError:
        await message.answer("❌ Введи целое число:"); return
    data   = await state.get_data()
    months = data.get("prem_months", "3")
    S["PREMIUM_PRICES"][months] = price
    save_settings(S)
    await state.clear()
    await message.answer(f"✅ Цена Premium {months} мес. = <b>{price} ₽</b>", parse_mode="HTML")

# --- СБП ---
async def adm_set_sbp(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔", show_alert=True); return
    await callback.answer()
    await state.set_state(AdminSt.set_sbp_phone)
    await callback.message.answer(
        f"⚡ Текущий телефон СБП: <code>{S['SBP_PHONE']}</code>\n\nВведи новый номер:\n/cancel — отмена",
        parse_mode="HTML"
    )

async def adm_got_sbp_phone(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    S["SBP_PHONE"] = message.text.strip()
    save_settings(S)
    await state.set_state(AdminSt.set_sbp_bank)
    await message.answer(f"✅ Телефон: <code>{S['SBP_PHONE']}</code>\n\nТеперь введи название банка (например: Сбербанк):", parse_mode="HTML")

async def adm_got_sbp_bank(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    S["SBP_BANK"] = message.text.strip()
    save_settings(S)
    await state.set_state(AdminSt.set_sbp_name)
    await message.answer("✅ Банк сохранён.\n\nВведи имя получателя (как в банке, например: Иван И.):")

async def adm_got_sbp_name(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    S["SBP_NAME"] = message.text.strip()
    save_settings(S)
    await state.clear()
    await message.answer(
        f"✅ <b>СБП реквизиты обновлены!</b>\n\n"
        f"📱 {S['SBP_PHONE']}\n🏦 {S['SBP_BANK']}\n👤 {S['SBP_NAME']}",
        parse_mode="HTML"
    )

# --- Ссылка канала ---
async def adm_set_channel(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔", show_alert=True); return
    await callback.answer()
    await state.set_state(AdminSt.set_channel)
    await callback.message.answer(
        f"📢 Текущий канал: <b>{S['CHANNEL_NAME']}</b> — {S['CHANNEL_URL']}\n\n"
        f"Введи новую ссылку (https://t.me/...) и через пробел юзернейм (@...):\n"
        f"Пример: <code>https://t.me/MyShop @MyShop</code>\n/cancel — отмена",
        parse_mode="HTML"
    )

async def adm_got_channel(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    parts = message.text.strip().split()
    if len(parts) < 2:
        await message.answer("❌ Введи ссылку и юзернейм через пробел."); return
    S["CHANNEL_URL"]  = parts[0]
    S["CHANNEL_NAME"] = parts[1] if parts[1].startswith("@") else "@" + parts[1]
    save_settings(S)
    await state.clear()
    await message.answer(f"✅ Канал обновлён: <b>{S['CHANNEL_NAME']}</b>", parse_mode="HTML")

# --- Юзернейм поддержки ---
async def adm_set_support(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔", show_alert=True); return
    await callback.answer()
    await state.set_state(AdminSt.set_support)
    await callback.message.answer(
        f"💬 Текущий юзернейм поддержки: @{S['SUPPORT_USERNAME']}\n\nВведи новый (без @):\n/cancel — отмена",
        parse_mode="HTML"
    )

async def adm_got_support(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    u = message.text.strip().lstrip("@")
    S["SUPPORT_USERNAME"] = u
    save_settings(S)
    await state.clear()
    await message.answer(f"✅ Поддержка: <b>@{u}</b>", parse_mode="HTML")

# --- Добавить админа ---
async def adm_add_admin(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔", show_alert=True); return
    await callback.answer()
    await state.set_state(AdminSt.add_admin)
    admins = ", ".join(str(a) for a in S["ADMINS"])
    await callback.message.answer(
        f"👤 <b>Добавить администратора</b>\n\nТекущие админы: <code>{admins}</code>\n\n"
        f"Введи Telegram ID нового админа (узнать у @userinfobot):\n/cancel — отмена",
        parse_mode="HTML"
    )

async def adm_got_add_admin(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    try:
        new_id = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Введи числовой Telegram ID:"); return
    if new_id in S["ADMINS"]:
        await message.answer("⚠️ Этот пользователь уже админ.")
        await state.clear(); return
    S["ADMINS"].append(new_id)
    save_settings(S)
    await state.clear()
    await message.answer(f"✅ Добавлен новый администратор: <code>{new_id}</code>", parse_mode="HTML")
    try:
        await message.bot.send_message(new_id, "✅ Тебя назначили администратором KrutoiShop! Используй /admin")
    except Exception: pass

# --- Удалить админа ---
async def adm_del_admin(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔", show_alert=True); return
    await callback.answer()
    await state.set_state(AdminSt.del_admin)
    admins = "\n".join(f"• <code>{a}</code>" for a in S["ADMINS"])
    await callback.message.answer(
        f"🗑 <b>Удалить администратора</b>\n\nТекущие админы:\n{admins}\n\n"
        f"Введи ID для удаления:\n/cancel — отмена",
        parse_mode="HTML"
    )

async def adm_got_del_admin(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    try:
        del_id = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Введи числовой ID:"); return
    if del_id == message.from_user.id:
        await message.answer("❌ Нельзя удалить самого себя!"); return
    if del_id not in S["ADMINS"]:
        await message.answer("❌ Такого админа нет."); return
    S["ADMINS"].remove(del_id)
    save_settings(S)
    await state.clear()
    await message.answer(f"✅ Администратор <code>{del_id}</code> удалён.", parse_mode="HTML")

# --- TON кошелёк ---
async def adm_set_ton(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔", show_alert=True); return
    await callback.answer()
    await state.set_state(AdminSt.set_ton_wallet)
    await callback.message.answer(
        f"💎 Текущий TON кошелёк:\n<code>{S['TON_WALLET']}</code>\n\nВведи новый адрес:\n/cancel — отмена",
        parse_mode="HTML"
    )

async def adm_got_ton(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    addr = message.text.strip()
    if len(addr) < 20:
        await message.answer("❌ Неверный адрес TON."); return
    S["TON_WALLET"] = addr
    save_settings(S)
    await state.clear()
    await message.answer(f"✅ TON кошелёк обновлён:\n<code>{addr}</code>", parse_mode="HTML")

# ═══════════════════════════════════════════════════════════════
#  ADMIN — промокоды
# ═══════════════════════════════════════════════════════════════
async def adm_promos(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔", show_alert=True); return
    await callback.answer()
    await callback.message.edit_text("🎟 <b>Управление промокодами</b>", reply_markup=kb_admin_promos(), parse_mode="HTML")

async def adm_promo_create(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔", show_alert=True); return
    await callback.answer()
    await state.set_state(AdminSt.promo_code)
    await callback.message.answer("🎟 Введи код промокода (например: SALE20):\n/cancel — отмена")

async def adm_promo_got_code(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    code = message.text.strip().upper()
    if len(code) < 3 or " " in code:
        await message.answer("❌ Код без пробелов, от 3 символов:"); return
    await state.update_data(new_code=code)
    await state.set_state(AdminSt.promo_disc)
    await message.answer(f"✅ Код: <b>{code}</b>\n\nВведи процент скидки (1–80):", parse_mode="HTML")

async def adm_promo_got_disc(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    try:
        disc = int(message.text.strip())
        if not 1 <= disc <= 80: raise ValueError
    except ValueError:
        await message.answer("❌ Введи число от 1 до 80:"); return
    await state.update_data(new_disc=disc)
    await state.set_state(AdminSt.promo_uses)
    await message.answer(f"✅ Скидка: <b>{disc}%</b>\n\nВведи количество использований:", parse_mode="HTML")

async def adm_promo_got_uses(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    try:
        uses = int(message.text.strip())
        if uses < 1: raise ValueError
    except ValueError:
        await message.answer("❌ Введи положительное число:"); return
    data = await state.get_data()
    create_promo(data["new_code"], data["new_disc"], uses)
    await state.clear()
    await message.answer(
        f"✅ <b>Промокод создан!</b>\n\n"
        f"🎟 Код: <code>{data['new_code']}</code>\n"
        f"💰 Скидка: <b>{data['new_disc']}%</b>\n"
        f"🔢 Использований: <b>{uses}</b>",
        parse_mode="HTML"
    )

async def adm_promo_list(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔", show_alert=True); return
    await callback.answer()
    promos = load_promos()
    if not promos:
        await callback.message.edit_text("📭 Промокодов нет.", reply_markup=kb_admin_promos()); return
    text = "🎟 <b>Все промокоды:</b>\n\n"
    for code, p in promos.items():
        used = p["total_uses"] - p["uses_left"]
        text += f"<code>{code}</code> · -{p['discount_percent']}% · {p['uses_left']}/{p['total_uses']} · использовано: {used}\n"
    await callback.message.edit_text(text, reply_markup=kb_admin_promos(), parse_mode="HTML")

# ═══════════════════════════════════════════════════════════════
#  ADMIN — рассылка
# ═══════════════════════════════════════════════════════════════
async def adm_broadcast(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔", show_alert=True); return
    await callback.answer()
    await state.set_state(AdminSt.broadcast)
    await callback.message.answer("📢 Введи текст рассылки:\n/cancel — отмена")

async def adm_broadcast_send(message: types.Message, state: FSMContext, bot: Bot):
    if not is_admin(message.from_user.id): return
    await state.clear()
    users = load_users()
    ids   = list(set(int(uid) for uid in users.keys()))
    sent  = 0
    for uid in ids:
        try:
            await bot.send_message(uid, f"📢 <b>KrutoiShop:</b>\n\n{message.text}", parse_mode="HTML")
            sent += 1
            await asyncio.sleep(0.05)
        except Exception: pass
    await message.answer(f"✅ Рассылка: {sent}/{len(ids)}")

# ═══════════════════════════════════════════════════════════════
#  ADMIN — топ рефералов и отзывы
# ═══════════════════════════════════════════════════════════════
async def adm_reftop(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔", show_alert=True); return
    await callback.answer()
    users = load_users()
    top   = sorted(users.values(), key=lambda u: u.get("ref_count", 0), reverse=True)[:10]
    text  = "👥 <b>Топ рефераловодов:</b>\n\n"
    for i, u in enumerate(top, 1):
        text += f"{i}. @{u.get('username','?')} — {u.get('ref_count',0)} рефералов · баланс {fmt(u.get('ref_balance',0))}\n"
    await callback.message.edit_text(text, reply_markup=kb_admin_main(), parse_mode="HTML")

async def adm_reviews(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔", show_alert=True); return
    await callback.answer()
    reviews = load_reviews()
    if not reviews:
        await callback.message.edit_text("📭 Отзывов нет.", reply_markup=kb_admin_main()); return
    text = f"⭐ <b>Последние отзывы ({len(reviews)} всего):</b>\n\n"
    for r in reversed(reviews[-5:]):
        text += f"{'⭐'*r['rating']} @{r['username']}: «{r['text'][:80]}»\n\n"
    await callback.message.edit_text(text, reply_markup=kb_admin_main(), parse_mode="HTML")

async def adm_payref(callback: types.CallbackQuery, bot: Bot):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔", show_alert=True); return
    parts  = callback.data.split(":")
    uid    = int(parts[2]); amount = float(parts[3])
    users  = load_users()
    key    = str(uid)
    if key in users:
        users[key]["ref_balance"] = 0; save_users(users)
    await callback.answer("✅ Выплачено")
    await callback.message.edit_text(callback.message.text + f"\n\n✅ ВЫПЛАЧЕНО {fmt(amount)}", parse_mode="HTML")
    try: await bot.send_message(uid, f"✅ Реферальный бонус <b>{fmt(amount)}</b> выплачен!", parse_mode="HTML")
    except Exception: pass

async def adm_denyref(callback: types.CallbackQuery, bot: Bot):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔", show_alert=True); return
    uid = int(callback.data.split(":")[2])
    await callback.answer("❌ Отклонено")
    await callback.message.edit_text(callback.message.text + "\n\n❌ ОТКЛОНЕНО", parse_mode="HTML")
    try: await bot.send_message(uid, f"❌ Запрос на вывод отклонён. @{S['SUPPORT_USERNAME']}", parse_mode="HTML")
    except Exception: pass

# ═══════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════
#  НАКОПИТЕЛЬНЫЕ СКИДКИ
# ═══════════════════════════════════════════════════════════════
def get_cumulative_discount(spent: float) -> int:
    """Возвращает % накопительной скидки по сумме потраченного."""
    levels = S.get("CUMULATIVE_DISCOUNTS", {})
    disc = 0
    for threshold_str, pct in sorted(levels.items(), key=lambda x: int(x[0])):
        if spent >= int(threshold_str):
            disc = pct
    return disc

def next_cumulative_level(spent: float) -> tuple:
    """Возвращает (следующий порог, % скидки) или None если максимум."""
    levels = S.get("CUMULATIVE_DISCOUNTS", {})
    for threshold_str, pct in sorted(levels.items(), key=lambda x: int(x[0])):
        threshold = int(threshold_str)
        if spent < threshold:
            return threshold, pct
    return None, None

# ═══════════════════════════════════════════════════════════════
#  МУЛЬТИВАЛЮТА — выбор при старте
# ═══════════════════════════════════════════════════════════════
def kb_currency():
    b = InlineKeyboardBuilder()
    b.button(text="🇷🇺 Рубли (₽)",  callback_data="currency:RUB")
    b.button(text="🇰🇿 Тенге (₸)",  callback_data="currency:KZT")
    b.button(text="💎 TON",          callback_data="currency:TON")
    b.adjust(1)
    return b.as_markup()

async def choose_currency(message: types.Message):
    """Пользователь может сменить валюту через /currency."""
    await message.answer(
        "🌍 <b>Выбери валюту отображения цен:</b>\n\n"
        "Цены будут показываться в выбранной валюте.",
        reply_markup=kb_currency(), parse_mode="HTML"
    )

async def currency_set(callback: types.CallbackQuery):
    await callback.answer()
    cur = callback.data.split(":")[1]
    users = load_users()
    key   = str(callback.from_user.id)
    if key in users:
        users[key]["currency"] = cur
        save_users(users)
    labels = {"RUB": "🇷🇺 Рубли (₽)", "KZT": "🇰🇿 Тенге (₸)", "TON": "💎 TON"}
    await callback.message.edit_text(
        f"✅ Валюта изменена: <b>{labels.get(cur, cur)}</b>\n\n"
        f"Теперь цены будут в {labels.get(cur, cur)}.",
        parse_mode="HTML"
    )

def get_user_currency(uid: int) -> str:
    return get_user(uid).get("currency", "RUB")

def fmt_by_currency(rub: float, currency: str) -> str:
    if currency == "KZT":
        return fmt_kzt(rub)
    if currency == "TON":
        usd = rub * S["RATE_USD"]
        ton = usd / S["TON_PRICE_USD"]
        return f"{ton:.4f} TON"
    return fmt(rub)

# ═══════════════════════════════════════════════════════════════
#  ЛИЧНЫЙ КАБИНЕТ — полная история с фильтрацией
# ═══════════════════════════════════════════════════════════════
def kb_cabinet(filter_type: str = "all"):
    b = InlineKeyboardBuilder()
    b.button(text=f"{'✅ ' if filter_type=='all' else ''}Все",      callback_data="cab:all")
    b.button(text=f"{'✅ ' if filter_type=='stars' else ''}Stars",   callback_data="cab:stars")
    b.button(text=f"{'✅ ' if filter_type=='premium' else ''}Premium",callback_data="cab:premium")
    b.button(text=f"{'✅ ' if filter_type=='pending' else ''}Ожидают", callback_data="cab:pending")
    b.adjust(2, 2)
    return b.as_markup()

async def cabinet_menu(message: types.Message):
    await _show_cabinet(message, "all", send_new=True)

async def cabinet_filter(callback: types.CallbackQuery):
    await callback.answer()
    ftype = callback.data.split(":")[1]
    await _show_cabinet(callback.message, ftype, send_new=False, edit=True)

async def _show_cabinet(msg, ftype: str, send_new: bool = True, edit: bool = False):
    uid = msg.chat.id if hasattr(msg, "chat") else msg.from_user.id
    db  = load_db()
    udata = get_user(uid)
    cur   = udata.get("currency", "RUB")

    all_orders = [o for o in db["orders"] if o.get("user_id") == uid]
    if ftype == "stars":
        orders = [o for o in all_orders if o["type"] == "stars"]
    elif ftype == "premium":
        orders = [o for o in all_orders if o["type"] == "premium"]
    elif ftype == "pending":
        orders = [o for o in all_orders if o["status"] == "pending"]
    else:
        orders = all_orders

    spent   = udata.get("total_spent", 0)
    cum_disc = get_cumulative_discount(spent)
    next_thr, next_pct = next_cumulative_level(spent)

    se = {"pending": "⏳", "confirmed": "✅", "rejected": "❌"}
    sl = {"pending": "Ожидает", "confirmed": "Выполнен", "rejected": "Отклонён"}

    text = (
        f"👤 <b>Личный кабинет</b>\n\n"
        f"📦 Заказов всего: <b>{len(all_orders)}</b>\n"
        f"💰 Потрачено: <b>{fmt(spent)}</b>\n"
        f"🎁 Накопительная скидка: <b>{cum_disc}%</b>\n"
    )
    if next_thr:
        text += f"📈 До скидки {next_pct}%: ещё <b>{fmt(next_thr - spent)}</b>\n"
    text += f"\n📋 <b>История ({ftype}):</b>\n\n"

    if not orders:
        text += "Заказов в этой категории нет."
    else:
        for o in reversed(orders[-10:]):
            svc = o.get("label") or (f"⭐{o.get('count')} Stars" if o["type"] == "stars" else f"👑Prem{o.get('months')}м")
            st  = o.get("status", "pending")
            amt = fmt_by_currency(o["amount_rub"], cur)
            text += f"{se.get(st,'❓')} <code>{o['id']}</code> · {svc} · {amt}\n   {sl.get(st,st)} · {o['created_at'][:10]}\n\n"

    if send_new:
        await msg.answer(text, reply_markup=kb_cabinet(ftype), parse_mode="HTML")
    elif edit:
        await msg.edit_text(text, reply_markup=kb_cabinet(ftype), parse_mode="HTML")

# ═══════════════════════════════════════════════════════════════
#  ФОНОВЫЕ ЗАДАЧИ — ежедневный отчёт, напоминания, бэкап
# ═══════════════════════════════════════════════════════════════
async def task_daily_report(bot: Bot):
    """Ежедневный отчёт в заданное время."""
    while True:
        now  = datetime.now()
        hour = S.get("DAILY_REPORT_HOUR", 9)
        mins = S.get("DAILY_REPORT_MINUTE", 0)
        # Следующий запуск
        next_run = now.replace(hour=hour, minute=mins, second=0, microsecond=0)
        if next_run <= now:
            next_run += timedelta(days=1)
        await asyncio.sleep((next_run - now).total_seconds())

        try:
            db       = load_db()
            users    = load_users()
            reviews  = load_reviews()
            today    = datetime.now().date().isoformat()
            today_orders = [o for o in db["orders"] if o.get("created_at","")[:10] == today]
            today_rev    = sum(o.get("amount_rub", 0) for o in today_orders if o["status"] == "confirmed")
            pending_cnt  = sum(1 for o in db["orders"] if o["status"] == "pending")
            avg_rat      = round(sum(r["rating"] for r in reviews) / len(reviews), 2) if reviews else 0

            report = (
                f"📈 <b>Ежедневный отчёт KrutoiShop</b>\n"
                f"📅 {today}\n\n"
                f"📦 Заказов за день: <b>{len(today_orders)}</b>\n"
                f"💰 Выручка за день: <b>{fmt(today_rev)}</b>\n"
                f"⏳ Pending заказов: <b>{pending_cnt}</b>\n\n"
                f"📊 Всего заказов: <b>{db['stats']['total']}</b>\n"
                f"💳 Общая выручка: <b>{fmt(db['stats'].get('revenue',0))}</b>\n"
                f"👥 Пользователей: <b>{len(users)}</b>\n"
                f"⭐ Рейтинг: <b>{avg_rat}</b>"
            )
            await notify_admins(bot, report)

            # Бэкап — отправляем файл orders.json
            import io
            db_bytes = json.dumps(db, ensure_ascii=False, indent=2).encode("utf-8")
            for aid in S.get("ADMINS", []):
                try:
                    await bot.send_document(
                        aid,
                        types.BufferedInputFile(db_bytes, filename=f"backup_{today}.json"),
                        caption=f"💾 Резервная копия базы заказов за {today}"
                    )
                except Exception as e:
                    logging.error(f"Backup send error {aid}: {e}")
        except Exception as e:
            logging.error(f"Daily report error: {e}")

async def task_pending_alert(bot: Bot):
    """Уведомление если заказ завис pending > N минут."""
    while True:
        await asyncio.sleep(60)  # проверка каждую минуту
        try:
            db      = load_db()
            limit   = S.get("PENDING_ALERT_MINUTES", 30)
            cutoff  = datetime.now() - timedelta(minutes=limit)
            alerted = set()
            for o in db["orders"]:
                if o["status"] != "pending": continue
                if o.get("alert_sent"): continue
                created = datetime.fromisoformat(o.get("created_at", datetime.now().isoformat()))
                if created < cutoff:
                    oid = o["id"]
                    svc = o.get("label") or (f"⭐{o.get('count')} Stars" if o["type"]=="stars" else f"👑Prem{o.get('months')}м")
                    await notify_admins(
                        bot,
                        f"⚠️ <b>Заказ #{oid} ждёт уже {limit}+ минут!</b>\n\n"
                        f"📦 {svc}\n👤 @{o.get('username','?')}\n"
                        f"🎯 @{o.get('recipient','?')}\n💰 {fmt(o.get('amount_rub',0))}\n\n"
                        f"Не забудь проверить оплату и подтвердить!",
                        kb_admin_order(oid)
                    )
                    # Помечаем чтобы не спамить
                    for order in db["orders"]:
                        if order["id"] == oid:
                            order["alert_sent"] = True
                    save_db(db)
        except Exception as e:
            logging.error(f"Pending alert error: {e}")

async def task_order_remind(bot: Bot):
    """Напоминание клиенту каждые N минут что заказ обрабатывается."""
    # Храним счётчик напоминаний в памяти (oid → count)
    reminded: dict = {}
    interval = S.get("ORDER_REMIND_INTERVAL", 5)
    max_rem  = S.get("ORDER_REMIND_MAX", 4)

    while True:
        await asyncio.sleep(interval * 60)
        try:
            db = load_db()
            for o in db["orders"]:
                if o["status"] != "pending": continue
                oid = o["id"]
                cnt = reminded.get(oid, 0)
                if cnt >= max_rem: continue
                reminded[oid] = cnt + 1
                try:
                    await bot.send_message(
                        o["user_id"],
                        f"⏳ <b>Заказ #{oid} всё ещё обрабатывается</b>\n\n"
                        f"📦 {o.get('label', 'Товар')}\n"
                        f"Администратор скоро проверит оплату.\n\n"
                        f"Вопросы: @{S['SUPPORT_USERNAME']}",
                        parse_mode="HTML"
                    )
                except Exception: pass
        except Exception as e:
            logging.error(f"Order remind error: {e}")

# ═══════════════════════════════════════════════════════════════
#  ПРИМЕНЕНИЕ НАКОПИТЕЛЬНОЙ СКИДКИ в _show_summary
# (патч — переопределяем функцию)
# ═══════════════════════════════════════════════════════════════
_original_show_summary = _show_summary if "_show_summary" in dir() else None

async def _show_summary_v4(msg, state: FSMContext, edit=False):
    """Расширенная версия с накопительной скидкой и мультивалютой."""
    data   = await state.get_data()
    uid    = msg.chat.id if hasattr(msg, "chat") else msg.from_user.id
    udata  = get_user(uid)
    cur    = udata.get("currency", "RUB")

    promo_disc = data.get("disc", 0)
    ref_disc   = S["REF_BONUS_PCT"] if (not udata.get("first_purchase") and udata.get("ref_by")) else 0
    cum_disc   = get_cumulative_discount(udata.get("total_spent", 0))
    total_disc = min(promo_disc + ref_disc + cum_disc, 50)
    oid = new_oid()

    is_stars = "count" in data
    if is_stars:
        count = data["count"]
        base  = round(count * S["STAR_PRICE_RUB"], 2)
        label = f"⭐ {count} Telegram Stars"
    else:
        months = data["months"]
        base   = int(S["PREMIUM_PRICES"][str(months)])
        label  = f"👑 Premium {months} мес."

    final = round(base * (1 - total_disc / 100), 2)
    await state.update_data(oid=oid, base=base, final=final, disc=total_disc, label=label)

    if is_stars: await state.set_state(BuyStars.payment)
    else:        await state.set_state(BuyPremium.payment)

    disc_parts = []
    if promo_disc: disc_parts.append(f"промо -{promo_disc}%")
    if ref_disc:   disc_parts.append(f"реф. -{ref_disc}%")
    if cum_disc:   disc_parts.append(f"накопит. -{cum_disc}%")
    disc_line = f"\n🎁 Скидка <b>-{total_disc}%</b> ({', '.join(disc_parts)})\n" if total_disc else "\n"

    display_price = fmt_by_currency(final, cur)
    text = (
        f"📋 <b>Детали заказа</b>\n\n"
        f"📦 {label}\n"
        f"👤 Получатель: <b>@{data.get('recipient','?')}</b>\n"
        f"💰 Цена: <b>{fmt(base)}</b>"
        f"{disc_line}"
        f"{'💳 К оплате: <b>' + display_price + '</b>' if total_disc else '💳 К оплате: <b>' + display_price + '</b>'}\n"
        f"<i>({fmt(final)} / {fmt_kzt(final)} / {fmt_usd(final)})</i>\n\n"
        f"🆔 Заказ: <code>{oid}</code>\n\n"
        f"Выбери способ оплаты:"
    )
    if edit: await msg.edit_text(text, reply_markup=kb_payment(oid), parse_mode="HTML")
    else:    await msg.answer(text, reply_markup=kb_payment(oid), parse_mode="HTML")

# Подменяем оригинальную функцию
_show_summary = _show_summary_v4


async def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler("bot.log", encoding="utf-8")]
    )
    global S
    S = load_settings()

    bot = Bot(token=S["BOT_TOKEN"])
    dp  = Dispatcher(storage=MemoryStorage())

    # Базовые команды
    dp.message.register(cmd_start,  Command("start"))
    dp.message.register(cmd_cancel, Command("cancel"))
    dp.message.register(cmd_admin,  Command("admin"))
    dp.message.register(ref_menu,   Command("ref"))

    # Главное меню
    dp.message.register(buy_stars,    F.text == "⭐ Купить Stars")
    dp.message.register(buy_premium,  F.text == "👑 Купить Premium")
    dp.message.register(my_orders,    F.text == "📦 Мои заказы")
    dp.message.register(ref_menu,     F.text == "👥 Рефералы")
    dp.message.register(leave_review, F.text == "⭐ Оставить отзыв")
    dp.message.register(support_msg,  F.text == "💬 Поддержка")

    # Stars FSM
    dp.message.register(stars_username,      StateFilter(BuyStars.username))
    dp.callback_query.register(stars_amount_cb, F.data.startswith("stars:"), StateFilter(BuyStars.amount))
    dp.message.register(stars_custom_amount, StateFilter(BuyStars.amount))
    dp.callback_query.register(enter_promo_cb, F.data == "enter_promo", StateFilter(BuyStars.promo))
    dp.callback_query.register(skip_promo_cb,  F.data == "skip_promo",  StateFilter(BuyStars.promo))
    dp.message.register(promo_text, StateFilter(BuyStars.promo))

    # Premium FSM
    dp.message.register(prem_username,  StateFilter(BuyPremium.username))
    dp.callback_query.register(prem_period_cb, F.data.startswith("prem:"), StateFilter(BuyPremium.period))
    dp.callback_query.register(enter_promo_cb, F.data == "enter_promo", StateFilter(BuyPremium.promo))
    dp.callback_query.register(skip_promo_cb,  F.data == "skip_promo",  StateFilter(BuyPremium.promo))
    dp.message.register(promo_text, StateFilter(BuyPremium.promo))

    # Оплата
    dp.callback_query.register(pay_sbp,    F.data.startswith("pay:sbp:"))
    dp.callback_query.register(pay_ton,    F.data.startswith("pay:ton:"))
    dp.callback_query.register(user_paid,  F.data.startswith("paid:"))
    dp.callback_query.register(cancel_cb,  F.data == "cancel")

    # Рефералы
    dp.callback_query.register(ref_withdraw, F.data == "ref:withdraw")

    # Отзывы
    dp.message.register(review_got_text, StateFilter(ReviewSt.text))
    dp.callback_query.register(review_got_rating, F.data.startswith("review:"), StateFilter(ReviewSt.rating))

    # Admin — главная
    dp.callback_query.register(adm_back,     F.data == "adm:back")
    dp.callback_query.register(adm_stats,    F.data == "adm:stats")
    dp.callback_query.register(adm_orders,   F.data == "adm:orders")
    dp.callback_query.register(adm_settings, F.data == "adm:settings")
    dp.callback_query.register(adm_reftop,   F.data == "adm:reftop")
    dp.callback_query.register(adm_reviews,  F.data == "adm:reviews")
    dp.callback_query.register(adm_broadcast,F.data == "adm:broadcast")
    dp.message.register(adm_broadcast_send, StateFilter(AdminSt.broadcast))

    # Admin — заказы
    dp.callback_query.register(adm_confirm, F.data.startswith("admin:confirm:"))
    dp.callback_query.register(adm_reject,  F.data.startswith("admin:reject:"))
    dp.callback_query.register(adm_payref,  F.data.startswith("admin:payref:"))
    dp.callback_query.register(adm_denyref, F.data.startswith("admin:denyref:"))

    # Admin — настройки
    dp.callback_query.register(adm_set_star_price, F.data == "adm:set:star_price")
    dp.message.register(adm_got_star_price, StateFilter(AdminSt.set_star_price))
    dp.callback_query.register(adm_set_prem_price, F.data == "adm:set:prem_price")
    dp.callback_query.register(adm_set_prem_which, F.data.startswith("set_prem:"))
    dp.message.register(adm_got_prem_price, StateFilter(AdminSt.set_prem_price))
    dp.callback_query.register(adm_set_sbp, F.data == "adm:set:sbp")
    dp.message.register(adm_got_sbp_phone, StateFilter(AdminSt.set_sbp_phone))
    dp.message.register(adm_got_sbp_bank,  StateFilter(AdminSt.set_sbp_bank))
    dp.message.register(adm_got_sbp_name,  StateFilter(AdminSt.set_sbp_name))
    dp.callback_query.register(adm_set_channel, F.data == "adm:set:channel")
    dp.message.register(adm_got_channel,   StateFilter(AdminSt.set_channel))
    dp.callback_query.register(adm_set_support, F.data == "adm:set:support")
    dp.message.register(adm_got_support,   StateFilter(AdminSt.set_support))
    dp.callback_query.register(adm_add_admin, F.data == "adm:set:add_admin")
    dp.message.register(adm_got_add_admin, StateFilter(AdminSt.add_admin))
    dp.callback_query.register(adm_del_admin, F.data == "adm:set:del_admin")
    dp.message.register(adm_got_del_admin, StateFilter(AdminSt.del_admin))
    dp.callback_query.register(adm_set_ton, F.data == "adm:set:ton")
    dp.message.register(adm_got_ton, StateFilter(AdminSt.set_ton_wallet))

    # Admin — промокоды
    dp.callback_query.register(adm_promos,       F.data == "adm:promos")
    dp.callback_query.register(adm_promo_create, F.data == "adm:promo:create")
    dp.callback_query.register(adm_promo_list,   F.data == "adm:promo:list")
    dp.message.register(adm_promo_got_code,  StateFilter(AdminSt.promo_code))
    dp.message.register(adm_promo_got_disc,  StateFilter(AdminSt.promo_disc))
    dp.message.register(adm_promo_got_uses,  StateFilter(AdminSt.promo_uses))

    # Личный кабинет
    dp.message.register(cabinet_menu, F.text == "📂 Личный кабинет")
    dp.callback_query.register(cabinet_filter, F.data.startswith("cab:"))

    # Мультивалюта
    dp.message.register(choose_currency, Command("currency"))
    dp.callback_query.register(currency_set, F.data.startswith("currency:"))

    logging.info("🤖 KrutoiShop Bot v4.0 запущен!")
    await bot.delete_webhook(drop_pending_updates=True)

    # Запускаем фоновые задачи
    asyncio.create_task(task_daily_report(bot))
    asyncio.create_task(task_pending_alert(bot))
    asyncio.create_task(task_order_remind(bot))

    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

# ═══════════════════════════════════════════════════════════════
#  📖 ИНСТРУКЦИЯ ПО АВТОВЫДАЧЕ ЗВЁЗД
# ═══════════════════════════════════════════════════════════════
#
#  У Fragment нет публичного API для программной отправки звёзд.
#  Вот реальные варианты:
#
#  ВАРИАНТ 1 — Ручная выдача (сейчас работает так):
#    После нажатия "Подтвердить" в боте — ты заходишь на
#    fragment.com, вводишь @username и количество, нажимаешь Send.
#    Деньги списываются с твоего TON баланса на Fragment.
#
#  ВАРИАНТ 2 — Сторонний API (starsbot.ru и подобные):
#    Некоторые сервисы предоставляют API для автоотправки.
#    1. Зарегистрируйся на starsbot.ru
#    2. Получи API ключ
#    3. Вставь в settings.json: "FRAGMENT_API_KEY": "твой_ключ", "FRAGMENT_ENABLED": true
#    4. В функции fragment_send_stars() раскомментируй код с нужным URL
#
#  ВАРИАНТ 3 — Telegram Payments API (официально):
#    Только для юр.лиц с верификацией. Требует одобрения Telegram.
#
#  ИТОГ: Для старта — ручная выдача через fragment.com вполне
#  рабочий вариант. Занимает 30 секунд на один заказ.
# ═══════════════════════════════════════════════════════════════
