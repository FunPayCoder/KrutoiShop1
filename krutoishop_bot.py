#!/usr/bin/env python3
# ═══════════════════════════════════════════════════════════════
#  KrutoiShop Bot — автоматизация выдачи Stars и Premium
#  Требования: pip install aiogram aiohttp python-dotenv
# ═══════════════════════════════════════════════════════════════

import asyncio
import logging
import json
import os
import uuid
import aiohttp
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton,
    ReplyKeyboardRemove
)
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

# ═══════════════════════════════════════════════════════════════
#  ⚙️ КОНФИГУРАЦИЯ — заполни эти поля
# ═══════════════════════════════════════════════════════════════
CONFIG = {
    # Токен бота от @BotFather
    "BOT_TOKEN": "8717850614:AAFlom3j1o6m5kplPsehRrxSU8oN6U9E70Y",

    # Ваш Telegram ID (получить у @userinfobot)
    "ADMIN_ID": 6716817812,

    # Второй админ (необязательно, 0 = отключено)
    "ADMIN_ID_2": 0,

    # TON кошелёк для оплаты крипто
    "TON_WALLET": "UQA4_4AfoTbXo1HywT3V158BPwU5BlhfV6UkW8IZKtEOHUYV",

    # Реквизиты СБП (номер телефона или текст)
    "SBP_PHONE": "+7 (994) 017-61-85",
    "SBP_BANK": "Озон Банк",  # или Тинькофф, ВТБ и т.д.
    "SBP_NAME": "Ярослав Л.",   # Имя получателя

    # Цена 1 звезды в рублях
    "STAR_PRICE_RUB": 1.4,

    # Цены Premium (рублей)
    "PREMIUM_PRICES": {3: 1200, 6: 1500, 12: 2600},

    # Курсы конвертации из рублей
    "RATE_KZT": 5.2,
    "RATE_USD": 0.011,

    # Ссылка на канал магазина
    "CHANNEL_URL": "https://t.me/KrutoiShop",

    # Файл базы данных заказов
    "DB_FILE": "orders.json",

    # CryptoBot токен (опционально, для авто-инвойсов крипты)
    # Получить: @CryptoBot → My Apps → Create App
    "CRYPTOBOT_TOKEN": "",  # оставь пустым если не используешь
}

# ═══════════════════════════════════════════════════════════════
#  БАЗА ДАННЫХ (простой JSON-файл)
# ═══════════════════════════════════════════════════════════════
def load_db():
    if os.path.exists(CONFIG["DB_FILE"]):
        with open(CONFIG["DB_FILE"], "r", encoding="utf-8") as f:
            return json.load(f)
    return {"orders": [], "stats": {"total": 0, "revenue": 0}}

def save_db(db):
    with open(CONFIG["DB_FILE"], "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)

def add_order(db, order_data):
    db["orders"].append(order_data)
    db["stats"]["total"] += 1
    db["stats"]["revenue"] += order_data.get("amount_rub", 0)
    save_db(db)

def get_order(db, order_id):
    return next((o for o in db["orders"] if o["id"] == order_id), None)

def update_order_status(db, order_id, status):
    for o in db["orders"]:
        if o["id"] == order_id:
            o["status"] = status
            o["updated_at"] = datetime.now().isoformat()
            break
    save_db(db)

# ═══════════════════════════════════════════════════════════════
#  СОСТОЯНИЯ FSM
# ═══════════════════════════════════════════════════════════════
class BuyStars(StatesGroup):
    waiting_username = State()
    waiting_amount   = State()
    waiting_payment  = State()

class BuyPremium(StatesGroup):
    waiting_username = State()
    waiting_period   = State()
    waiting_payment  = State()

class AdminState(StatesGroup):
    waiting_order_id    = State()
    waiting_broadcast   = State()

# ═══════════════════════════════════════════════════════════════
#  УТИЛИТЫ
# ═══════════════════════════════════════════════════════════════
def is_admin(user_id: int) -> bool:
    return user_id == CONFIG["ADMIN_ID"] or (
        CONFIG["ADMIN_ID_2"] and user_id == CONFIG["ADMIN_ID_2"]
    )

def fmt_rub(n): return f"{round(n):,} ₽".replace(",", " ")
def fmt_kzt(n): return f"{round(n * CONFIG['RATE_KZT']):,} ₸".replace(",", " ")
def fmt_usd(n): return f"${n * CONFIG['RATE_USD']:.2f}"

def calc_stars_price(count: int) -> dict:
    rub = count * CONFIG["STAR_PRICE_RUB"]
    return {"rub": rub, "kzt": rub * CONFIG["RATE_KZT"], "usd": rub * CONFIG["RATE_USD"]}

def new_order_id() -> str:
    return str(uuid.uuid4())[:8].upper()

async def notify_admin(bot: Bot, text: str, markup=None):
    """Отправить уведомление всем админам"""
    for admin_id in [CONFIG["ADMIN_ID"], CONFIG["ADMIN_ID_2"]]:
        if admin_id:
            try:
                await bot.send_message(admin_id, text, reply_markup=markup, parse_mode="HTML")
            except Exception as e:
                logging.error(f"Не удалось уведомить админа {admin_id}: {e}")

# ═══════════════════════════════════════════════════════════════
#  КЛАВИАТУРЫ
# ═══════════════════════════════════════════════════════════════
def kb_main():
    builder = ReplyKeyboardBuilder()
    builder.button(text="⭐ Купить Stars")
    builder.button(text="👑 Купить Premium")
    builder.button(text="📦 Мои заказы")
    builder.button(text="💬 Поддержка")
    builder.button(text="ℹ️ О магазине")
    builder.adjust(2, 2, 1)
    return builder.as_markup(resize_keyboard=True)

def kb_stars_amount():
    builder = InlineKeyboardBuilder()
    for n in [50, 100, 250, 500, 1000]:
        price = round(n * CONFIG["STAR_PRICE_RUB"])
        builder.button(text=f"⭐ {n} — {price} ₽", callback_data=f"stars:{n}")
    builder.button(text="✏️ Ввести своё количество", callback_data="stars:custom")
    builder.button(text="❌ Отмена", callback_data="cancel")
    builder.adjust(1)
    return builder.as_markup()

def kb_premium_period():
    builder = InlineKeyboardBuilder()
    for months, price in CONFIG["PREMIUM_PRICES"].items():
        label = {3: "3 месяца", 6: "6 месяцев", 12: "12 месяцев 🔥"}[months]
        builder.button(text=f"{label} — {price:,} ₽".replace(",", " "), callback_data=f"prem:{months}")
    builder.button(text="❌ Отмена", callback_data="cancel")
    builder.adjust(1)
    return builder.as_markup()

def kb_payment(order_id: str, amount_rub: float):
    builder = InlineKeyboardBuilder()
    builder.button(text="⚡ СБП", callback_data=f"pay:sbp:{order_id}")
    builder.button(text="💎 TON/USDT", callback_data=f"pay:crypto:{order_id}")
    builder.button(text="❌ Отмена", callback_data="cancel")
    builder.adjust(2, 1)
    return builder.as_markup()

def kb_confirm_paid(order_id: str):
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Я оплатил(а)", callback_data=f"paid:{order_id}")
    builder.button(text="❌ Отмена", callback_data="cancel")
    builder.adjust(1)
    return builder.as_markup()

def kb_admin_order(order_id: str):
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Подтвердить выдачу", callback_data=f"admin:confirm:{order_id}")
    builder.button(text="❌ Отклонить", callback_data=f"admin:reject:{order_id}")
    builder.adjust(2)
    return builder.as_markup()

def kb_cancel():
    builder = InlineKeyboardBuilder()
    builder.button(text="❌ Отмена", callback_data="cancel")
    return builder.as_markup()

def kb_admin_panel():
    builder = InlineKeyboardBuilder()
    builder.button(text="📊 Статистика", callback_data="admin:stats")
    builder.button(text="📋 Последние заказы", callback_data="admin:orders")
    builder.button(text="🔍 Найти заказ", callback_data="admin:find")
    builder.button(text="📢 Рассылка", callback_data="admin:broadcast")
    builder.adjust(2, 2)
    return builder.as_markup()

# ═══════════════════════════════════════════════════════════════
#  HANDLERS — /start
# ═══════════════════════════════════════════════════════════════
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    name = message.from_user.first_name or "друг"
    text = (
        f"👋 Привет, <b>{name}</b>!\n\n"
        f"🏪 Добро пожаловать в <b>KrutoiShop</b> — магазин цифровых товаров Telegram.\n\n"
        f"⭐ <b>Telegram Stars</b> — от {fmt_rub(CONFIG['STAR_PRICE_RUB'])}/шт\n"
        f"👑 <b>Telegram Premium</b> — от {fmt_rub(CONFIG['PREMIUM_PRICES'][3])}/мес\n\n"
        f"⚡ Доставка автоматически за секунды\n"
        f"🔒 Безопасная оплата СБП и TON\n\n"
        f"Выбери что хочешь купить 👇"
    )
    await message.answer(text, reply_markup=kb_main(), parse_mode="HTML")

# ═══════════════════════════════════════════════════════════════
#  HANDLERS — Покупка Stars
# ═══════════════════════════════════════════════════════════════
async def buy_stars_start(message: types.Message, state: FSMContext):
    await state.set_state(BuyStars.waiting_username)
    await message.answer(
        "⭐ <b>Покупка Telegram Stars</b>\n\n"
        "Введи <b>@username</b> получателя звёзд\n"
        "<i>Пример: @username или username</i>",
        reply_markup=kb_cancel(), parse_mode="HTML"
    )

async def stars_got_username(message: types.Message, state: FSMContext):
    username = message.text.strip().lstrip("@")
    if len(username) < 3 or " " in username:
        await message.answer("❌ Некорректный юзернейм. Попробуй ещё раз:")
        return
    await state.update_data(username=username)
    await state.set_state(BuyStars.waiting_amount)
    await message.answer(
        f"✅ Получатель: <b>@{username}</b>\n\n"
        f"Выбери количество звёзд:",
        reply_markup=kb_stars_amount(), parse_mode="HTML"
    )

async def stars_got_amount(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    val = callback.data.split(":")[1]
    if val == "custom":
        await state.set_state(BuyStars.waiting_amount)
        await callback.message.edit_text(
            "✏️ Введи количество звёзд (от 50 до 1000):",
            reply_markup=kb_cancel()
        )
        await state.update_data(custom_amount=True)
        return
    count = int(val)
    await _stars_show_price(callback.message, state, count, edit=True)

async def stars_got_custom_amount(message: types.Message, state: FSMContext):
    data = await state.get_data()
    if not data.get("custom_amount"):
        return
    try:
        count = int(message.text.strip())
        if not (50 <= count <= 1000):
            await message.answer("❌ Введи число от 50 до 1000:")
            return
    except ValueError:
        await message.answer("❌ Введи числовое значение:")
        return
    await _stars_show_price(message, state, count, edit=False)

async def _stars_show_price(msg, state, count, edit=False):
    p = calc_stars_price(count)
    data = await state.get_data()
    username = data.get("username", "???")
    order_id = new_order_id()
    await state.update_data(count=count, order_id=order_id, amount_rub=p["rub"])
    await state.set_state(BuyStars.waiting_payment)

    text = (
        f"⭐ <b>Детали заказа</b>\n\n"
        f"👤 Получатель: <b>@{username}</b>\n"
        f"🌟 Количество: <b>{count} Stars</b>\n\n"
        f"💰 <b>Стоимость:</b>\n"
        f"   {fmt_rub(p['rub'])} / {fmt_kzt(p['rub'])} / {fmt_usd(p['rub'])}\n\n"
        f"🆔 ID заказа: <code>{order_id}</code>\n\n"
        f"Выбери способ оплаты:"
    )
    kb = kb_payment(order_id, p["rub"])
    if edit:
        await msg.edit_text(text, reply_markup=kb, parse_mode="HTML")
    else:
        await msg.answer(text, reply_markup=kb, parse_mode="HTML")

# ═══════════════════════════════════════════════════════════════
#  HANDLERS — Покупка Premium
# ═══════════════════════════════════════════════════════════════
async def buy_premium_start(message: types.Message, state: FSMContext):
    await state.set_state(BuyPremium.waiting_username)
    await message.answer(
        "👑 <b>Покупка Telegram Premium</b>\n\n"
        "Введи <b>@username</b> получателя:",
        reply_markup=kb_cancel(), parse_mode="HTML"
    )

async def premium_got_username(message: types.Message, state: FSMContext):
    username = message.text.strip().lstrip("@")
    if len(username) < 3 or " " in username:
        await message.answer("❌ Некорректный юзернейм. Попробуй ещё раз:")
        return
    await state.update_data(username=username)
    await state.set_state(BuyPremium.waiting_period)
    await message.answer(
        f"✅ Получатель: <b>@{username}</b>\n\n"
        f"Выбери период подписки:",
        reply_markup=kb_premium_period(), parse_mode="HTML"
    )

async def premium_got_period(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    months = int(callback.data.split(":")[1])
    price_rub = CONFIG["PREMIUM_PRICES"][months]
    data = await state.get_data()
    username = data.get("username", "???")
    order_id = new_order_id()
    await state.update_data(months=months, order_id=order_id, amount_rub=price_rub)
    await state.set_state(BuyPremium.waiting_payment)

    label = {3: "3 месяца", 6: "6 месяцев", 12: "12 месяцев"}[months]
    text = (
        f"👑 <b>Детали заказа</b>\n\n"
        f"👤 Получатель: <b>@{username}</b>\n"
        f"📅 Период: <b>Telegram Premium {label}</b>\n\n"
        f"💰 <b>Стоимость:</b>\n"
        f"   {fmt_rub(price_rub)} / {fmt_kzt(price_rub)} / {fmt_usd(price_rub)}\n\n"
        f"🆔 ID заказа: <code>{order_id}</code>\n\n"
        f"Выбери способ оплаты:"
    )
    await callback.message.edit_text(text, reply_markup=kb_payment(order_id, price_rub), parse_mode="HTML")

# ═══════════════════════════════════════════════════════════════
#  HANDLERS — Оплата
# ═══════════════════════════════════════════════════════════════
async def payment_sbp(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    order_id = callback.data.split(":")[2]
    data = await state.get_data()
    amount_rub = data.get("amount_rub", 0)

    text = (
        f"⚡ <b>Оплата через СБП</b>\n\n"
        f"💳 Переведи <b>{fmt_rub(amount_rub)}</b> по реквизитам:\n\n"
        f"📱 <b>Телефон:</b> <code>{CONFIG['SBP_PHONE']}</code>\n"
        f"🏦 <b>Банк:</b> {CONFIG['SBP_BANK']}\n"
        f"👤 <b>Получатель:</b> {CONFIG['SBP_NAME']}\n\n"
        f"⚠️ <b>Важно:</b> В комментарии к переводу укажи:\n"
        f"<code>Заказ {order_id}</code>\n\n"
        f"После оплаты нажми кнопку ниже 👇"
    )
    await callback.message.edit_text(text, reply_markup=kb_confirm_paid(order_id), parse_mode="HTML")

async def payment_crypto(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    order_id = callback.data.split(":")[2]
    data = await state.get_data()
    amount_rub = data.get("amount_rub", 0)
    amount_usd = amount_rub * CONFIG["RATE_USD"]
    amount_ton = amount_usd / 7.1  # примерный курс TON

    # Если есть CryptoBot — создаём инвойс
    pay_url = ""
    if CONFIG["CRYPTOBOT_TOKEN"]:
        try:
            async with aiohttp.ClientSession() as session:
                resp = await session.post(
                    "https://pay.crypt.bot/api/createInvoice",
                    headers={"Crypto-Pay-API-Token": CONFIG["CRYPTOBOT_TOKEN"]},
                    json={
                        "asset": "TON",
                        "amount": f"{amount_ton:.4f}",
                        "description": f"KrutoiShop заказ {order_id}",
                        "expires_in": 900,
                    }
                )
                result = await resp.json()
                if result.get("ok"):
                    pay_url = result["result"]["pay_url"]
        except Exception as e:
            logging.error(f"CryptoBot error: {e}")

    builder = InlineKeyboardBuilder()
    if pay_url:
        builder.button(text="💎 Оплатить через CryptoBot", url=pay_url)
    builder.button(text="✅ Я оплатил(а)", callback_data=f"paid:{order_id}")
    builder.button(text="❌ Отмена", callback_data="cancel")
    builder.adjust(1)

    text = (
        f"💎 <b>Оплата криптовалютой</b>\n\n"
        f"Отправь на TON-кошелёк:\n"
        f"<code>{CONFIG['TON_WALLET']}</code>\n\n"
        f"💰 <b>Сумма:</b>\n"
        f"   ~{amount_ton:.2f} TON\n"
        f"   ~{amount_usd:.2f} USDT\n\n"
        f"🆔 Заказ: <code>{order_id}</code>\n\n"
        f"⚠️ Указывай точную сумму. После оплаты нажми ниже 👇"
    )
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")

async def user_paid(callback: types.CallbackQuery, state: FSMContext, bot: Bot):
    await callback.answer("✅ Принято! Ожидай подтверждения от администратора.")
    order_id = callback.data.split(":")[1]
    data = await state.get_data()

    # Сохраняем заказ в БД
    db = load_db()
    service_type = "stars" if "count" in data else "premium"
    order = {
        "id": order_id,
        "user_id": callback.from_user.id,
        "username": callback.from_user.username or str(callback.from_user.id),
        "recipient": data.get("username", "?"),
        "type": service_type,
        "count": data.get("count"),
        "months": data.get("months"),
        "amount_rub": data.get("amount_rub", 0),
        "status": "pending",
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
    }
    add_order(db, order)

    # Сообщение пользователю
    await callback.message.edit_text(
        f"⏳ <b>Заказ #{order_id} ожидает подтверждения</b>\n\n"
        f"Администратор проверит оплату и выдаст товар в течение нескольких минут.\n\n"
        f"Статус можно проверить через «📦 Мои заказы»",
        parse_mode="HTML"
    )

    # Уведомление админу
    service_label = (
        f"⭐ {data.get('count')} Stars" if service_type == "stars"
        else f"👑 Premium {data.get('months')} мес."
    )
    admin_text = (
        f"🔔 <b>НОВЫЙ ЗАКАЗ #{order_id}</b>\n\n"
        f"👤 Покупатель: @{order.get('username')} (ID: {order['user_id']})\n"
        f"📦 Товар: {service_label}\n"
        f"🎯 Получатель: @{order['recipient']}\n"
        f"💰 Сумма: {fmt_rub(order['amount_rub'])}\n"
        f"🕐 Время: {datetime.now().strftime('%d.%m.%Y %H:%M')}\n\n"
        f"⚡ Подтверди выдачу:"
    )
    await notify_admin(bot, admin_text, kb_admin_order(order_id))
    await state.clear()

# ═══════════════════════════════════════════════════════════════
#  HANDLERS — Мои заказы
# ═══════════════════════════════════════════════════════════════
async def my_orders(message: types.Message):
    db = load_db()
    user_id = message.from_user.id
    user_orders = [o for o in db["orders"] if o.get("user_id") == user_id]

    if not user_orders:
        await message.answer("📭 У тебя пока нет заказов.\n\nНачни покупку прямо сейчас! 👇", reply_markup=kb_main())
        return

    text = "📦 <b>Твои последние заказы:</b>\n\n"
    status_emoji = {"pending": "⏳", "confirmed": "✅", "rejected": "❌", "delivered": "🚀"}
    status_label = {"pending": "Ожидает", "confirmed": "Выполнен", "rejected": "Отклонён", "delivered": "Доставлен"}

    for o in reversed(user_orders[-5:]):
        service = f"⭐ {o['count']} Stars" if o["type"] == "stars" else f"👑 Premium {o.get('months')}м."
        status = o.get("status", "pending")
        text += (
            f"🆔 <code>{o['id']}</code> | {service}\n"
            f"   {status_emoji.get(status,'❓')} {status_label.get(status, status)} | {fmt_rub(o['amount_rub'])}\n"
            f"   📅 {o['created_at'][:10]}\n\n"
        )
    await message.answer(text, parse_mode="HTML")

# ═══════════════════════════════════════════════════════════════
#  HANDLERS — Информация и поддержка
# ═══════════════════════════════════════════════════════════════
async def about(message: types.Message):
    await message.answer(
        "ℹ️ <b>О магазине KrutoiShop</b>\n\n"
        f"⭐ Stars — от <b>{fmt_rub(CONFIG['STAR_PRICE_RUB'])}/шт</b> (дешевле официального на 44%)\n"
        f"👑 Premium — от <b>{fmt_rub(CONFIG['PREMIUM_PRICES'][3])}/мес</b>\n\n"
        "✅ Мгновенная доставка\n"
        "🔒 Безопасная оплата\n"
        "💬 Поддержка 24/7\n\n"
        f"📢 Канал: {CONFIG['CHANNEL_URL']}",
        parse_mode="HTML"
    )

async def support(message: types.Message):
    await message.answer(
        "💬 <b>Поддержка</b>\n\n"
        "По любым вопросам пиши администратору.\n"
        "Время ответа: до 15 минут.\n\n"
        "📎 Укажи ID заказа если проблема с конкретным заказом.",
        parse_mode="HTML"
    )

# ═══════════════════════════════════════════════════════════════
#  HANDLERS — Отмена
# ═══════════════════════════════════════════════════════════════
async def cancel_action(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer("Отменено")
    await callback.message.edit_text("❌ Действие отменено.")
    await callback.message.answer("Главное меню:", reply_markup=kb_main())

async def cmd_cancel(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Отменено. Главное меню:", reply_markup=kb_main())

# ═══════════════════════════════════════════════════════════════
#  ADMIN HANDLERS
# ═══════════════════════════════════════════════════════════════
async def cmd_admin(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Нет доступа.")
        return
    db = load_db()
    pending = len([o for o in db["orders"] if o["status"] == "pending"])
    await message.answer(
        f"🛠 <b>Панель администратора</b>\n\n"
        f"📊 Всего заказов: <b>{db['stats']['total']}</b>\n"
        f"⏳ Ожидают выдачи: <b>{pending}</b>\n"
        f"💰 Общая выручка: <b>{fmt_rub(db['stats']['revenue'])}</b>",
        reply_markup=kb_admin_panel(), parse_mode="HTML"
    )

async def admin_confirm_order(callback: types.CallbackQuery, bot: Bot):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    order_id = callback.data.split(":")[2]
    db = load_db()
    order = get_order(db, order_id)
    if not order:
        await callback.answer("❌ Заказ не найден", show_alert=True)
        return

    update_order_status(db, order_id, "confirmed")
    await callback.answer("✅ Выдача подтверждена!")
    await callback.message.edit_text(
        callback.message.text + f"\n\n✅ <b>ВЫДАНО</b> администратором {callback.from_user.first_name}",
        parse_mode="HTML"
    )

    # Уведомление покупателю
    service = (
        f"⭐ {order['count']} Telegram Stars"
        if order["type"] == "stars"
        else f"👑 Telegram Premium на {order.get('months')} мес."
    )
    try:
        await bot.send_message(
            order["user_id"],
            f"🎉 <b>Заказ #{order_id} выполнен!</b>\n\n"
            f"📦 {service}\n"
            f"🎯 Получатель: @{order['recipient']}\n\n"
            f"✅ Товар отправлен на аккаунт получателя.\n"
            f"Спасибо за покупку в KrutoiShop! 🏪",
            parse_mode="HTML"
        )
    except Exception as e:
        logging.error(f"Не удалось уведомить покупателя: {e}")

async def admin_reject_order(callback: types.CallbackQuery, bot: Bot):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    order_id = callback.data.split(":")[2]
    db = load_db()
    order = get_order(db, order_id)
    if not order:
        await callback.answer("❌ Заказ не найден", show_alert=True)
        return

    update_order_status(db, order_id, "rejected")
    await callback.answer("❌ Заказ отклонён")
    await callback.message.edit_text(
        callback.message.text + f"\n\n❌ <b>ОТКЛОНЕНО</b>",
        parse_mode="HTML"
    )
    try:
        await bot.send_message(
            order["user_id"],
            f"❌ <b>Заказ #{order_id} отклонён</b>\n\n"
            f"Оплата не подтверждена или возникла проблема.\n"
            f"Напиши в поддержку для уточнения.",
            parse_mode="HTML"
        )
    except Exception as e:
        logging.error(f"Уведомление покупателя не отправлено: {e}")

async def admin_stats(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    await callback.answer()
    db = load_db()
    orders = db["orders"]
    pending   = len([o for o in orders if o["status"] == "pending"])
    confirmed = len([o for o in orders if o["status"] == "confirmed"])
    rejected  = len([o for o in orders if o["status"] == "rejected"])
    stars_orders   = len([o for o in orders if o["type"] == "stars"])
    premium_orders = len([o for o in orders if o["type"] == "premium"])

    await callback.message.edit_text(
        f"📊 <b>Статистика магазина</b>\n\n"
        f"📦 Всего заказов: <b>{db['stats']['total']}</b>\n"
        f"✅ Выполнено: <b>{confirmed}</b>\n"
        f"⏳ Ожидают: <b>{pending}</b>\n"
        f"❌ Отклонено: <b>{rejected}</b>\n\n"
        f"⭐ Заказов Stars: <b>{stars_orders}</b>\n"
        f"👑 Заказов Premium: <b>{premium_orders}</b>\n\n"
        f"💰 Общая выручка: <b>{fmt_rub(db['stats']['revenue'])}</b>",
        reply_markup=kb_admin_panel(), parse_mode="HTML"
    )

async def admin_orders_list(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    await callback.answer()
    db = load_db()
    last = list(reversed(db["orders"][-8:]))
    if not last:
        await callback.message.edit_text("📭 Заказов пока нет.", reply_markup=kb_admin_panel())
        return

    status_emoji = {"pending":"⏳","confirmed":"✅","rejected":"❌","delivered":"🚀"}
    text = "📋 <b>Последние заказы:</b>\n\n"
    for o in last:
        s = o.get("service_label") or (f"Stars {o.get('count')}" if o["type"]=="stars" else f"Premium {o.get('months')}м.")
        text += f"{status_emoji.get(o['status'],'❓')} <code>{o['id']}</code> | @{o.get('recipient','?')} | {s} | {fmt_rub(o['amount_rub'])}\n"

    await callback.message.edit_text(text, reply_markup=kb_admin_panel(), parse_mode="HTML")

async def admin_broadcast_start(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    await callback.answer()
    await state.set_state(AdminState.waiting_broadcast)
    await callback.message.answer(
        "📢 Введи текст рассылки (отправится всем кто делал заказы):\n\n"
        "/cancel — отмена"
    )

async def admin_broadcast_send(message: types.Message, state: FSMContext, bot: Bot):
    if not is_admin(message.from_user.id):
        return
    await state.clear()
    db = load_db()
    user_ids = list(set(o["user_id"] for o in db["orders"] if o.get("user_id")))
    sent = 0
    for uid in user_ids:
        try:
            await bot.send_message(uid, f"📢 <b>KrutoiShop:</b>\n\n{message.text}", parse_mode="HTML")
            sent += 1
            await asyncio.sleep(0.05)
        except Exception:
            pass
    await message.answer(f"✅ Рассылка отправлена {sent}/{len(user_ids)} пользователям.")

# ═══════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════
async def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler("bot.log", encoding="utf-8"),
        ]
    )

    bot = Bot(token=CONFIG["BOT_TOKEN"])
    dp  = Dispatcher(storage=MemoryStorage())

    # ── Регистрация хэндлеров ──

    # /start и навигация
    dp.message.register(cmd_start,   Command("start"))
    dp.message.register(cmd_cancel,  Command("cancel"))
    dp.message.register(cmd_admin,   Command("admin"))
    dp.message.register(buy_stars_start,   F.text == "⭐ Купить Stars")
    dp.message.register(buy_premium_start, F.text == "👑 Купить Premium")
    dp.message.register(my_orders,         F.text == "📦 Мои заказы")
    dp.message.register(support,           F.text == "💬 Поддержка")
    dp.message.register(about,             F.text == "ℹ️ О магазине")

    # Stars FSM
    dp.message.register(stars_got_username,     StateFilter(BuyStars.waiting_username))
    dp.message.register(stars_got_custom_amount, StateFilter(BuyStars.waiting_amount))
    dp.callback_query.register(stars_got_amount, F.data.startswith("stars:"), StateFilter(BuyStars.waiting_amount))

    # Premium FSM
    dp.message.register(premium_got_username, StateFilter(BuyPremium.waiting_username))
    dp.callback_query.register(premium_got_period, F.data.startswith("prem:"), StateFilter(BuyPremium.waiting_period))

    # Оплата
    dp.callback_query.register(payment_sbp,    F.data.startswith("pay:sbp:"))
    dp.callback_query.register(payment_crypto, F.data.startswith("pay:crypto:"))
    dp.callback_query.register(user_paid,      F.data.startswith("paid:"))
    dp.callback_query.register(cancel_action,  F.data == "cancel")

    # Admin callbacks
    dp.callback_query.register(admin_confirm_order, F.data.startswith("admin:confirm:"))
    dp.callback_query.register(admin_reject_order,  F.data.startswith("admin:reject:"))
    dp.callback_query.register(admin_stats,         F.data == "admin:stats")
    dp.callback_query.register(admin_orders_list,   F.data == "admin:orders")
    dp.callback_query.register(admin_broadcast_start, F.data == "admin:broadcast")
    dp.message.register(admin_broadcast_send, StateFilter(AdminState.waiting_broadcast))

    logging.info("🤖 KrutoiShop Bot запущен!")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
