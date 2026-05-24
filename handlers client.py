import os
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from datetime import date

import database as db
import keyboards as kb

router = Router()

MANAGER_PHONE = os.getenv("MANAGER_PHONE", "+79780505589")

STATUS_MAP = {
    "new": "🆕 Новый — ожидает подтверждения",
    "in_progress": "🔄 В работе — ваш ковёр чистится",
    "ready": "✅ Готов — ждёт доставки",
    "delivered": "📦 Доставлен — выполнен",
}

FAQ_TEXT = """❓ <b>Часто задаваемые вопросы</b>

<b>Сколько времени занимает стирка?</b>
Обычно 1-3 рабочих дня в зависимости от загрязнения и размера.

<b>Как формируется цена?</b>
Цена = площадь ковра × стоимость услуги за кв.м. Минимальный заказ — 250 руб.

<b>Что входит в доставку?</b>
Забираем ковёр у вас и привозим обратно после чистки — 500 руб. При заказе от 2000 руб. доставка бесплатная!

<b>Какие ковры принимаете?</b>
Любые: шерстяные, синтетические, ковролин, дорожки, паласы.

<b>Как узнать, что ковёр готов?</b>
Бот автоматически пришлёт уведомление, когда статус изменится.

<b>Можно ли сдать ковёр самому?</b>
Да! При оформлении заявки выберите "Привезу сам" и мы сообщим адрес.

<b>Остались вопросы?</b>
Звоните: {phone}"""


class OrderFSM(StatesGroup):
    choosing_service = State()
    entering_width = State()
    entering_length = State()
    choosing_delivery = State()
    choosing_date = State()
    entering_phone = State()
    confirming = State()


class ReviewFSM(StatesGroup):
    choosing_order = State()
    choosing_rating = State()
    entering_text = State()


# ───── START ─────
@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    name = message.from_user.first_name or "Гость"
    await message.answer(
        f"👋 Привет, {name}!\n\n"
        f"Добро пожаловать в <b>Стирка ковров Донецк</b> 🏠\n\n"
        f"Профессиональная чистка ковров с доставкой на дом!\n"
        f"Выберите нужный раздел:",
        reply_markup=kb.main_menu(),
        parse_mode="HTML"
    )


@router.callback_query(F.data == "main_menu")
async def back_to_main(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(
        "🏠 <b>Главное меню</b>\n\nВыберите раздел:",
        reply_markup=kb.main_menu(),
        parse_mode="HTML"
    )


# ───── PRICES ─────
@router.callback_query(F.data == "prices")
async def show_prices(callback: CallbackQuery):
    prices = await db.get_prices()
    text = "💰 <b>Прайс-лист</b>\n\n"
    for _, service, price in prices:
        text += f"• {service} — <b>{price:.0f} руб/кв.м</b>\n"
    text += "\n🚚 Доставка: <b>500 руб.</b> (бесплатно при заказе от 2000 руб.)\n"
    text += "📏 Минимальный заказ: <b>250 руб.</b>"
    await callback.message.edit_text(text, reply_markup=kb.back_to_menu(), parse_mode="HTML")


# ───── CALCULATOR ─────
@router.callback_query(F.data == "calculator")
async def start_calculator(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    prices = await db.get_prices()
    await state.update_data(prices=prices)
    await callback.message.edit_text(
        "🧮 <b>Калькулятор стоимости</b>\n\nВыберите услугу:",
        reply_markup=kb.services_keyboard(prices),
        parse_mode="HTML"
    )
    await state.set_state(OrderFSM.choosing_service)


@router.callback_query(OrderFSM.choosing_service, F.data.startswith("service_"))
async def choose_service(callback: CallbackQuery, state: FSMContext):
    service_id = int(callback.data.split("_")[1])
    data = await state.get_data()
    prices = data.get("prices", [])
    service_info = next((p for p in prices if p[0] == service_id), None)
    if not service_info:
        await callback.answer("Ошибка, попробуйте снова")
        return
    await state.update_data(service_id=service_id, service_name=service_info[1], price_per_sqm=service_info[2])
    await callback.message.edit_text(
        f"📐 Услуга: <b>{service_info[1]}</b>\n\nВведите <b>ширину</b> ковра в метрах (например: 2.5):",
        parse_mode="HTML"
    )
    await state.set_state(OrderFSM.entering_width)


@router.message(OrderFSM.entering_width)
async def enter_width(message: Message, state: FSMContext):
    try:
        width = float(message.text.replace(",", "."))
        if width <= 0 or width > 20:
            raise ValueError
    except ValueError:
        await message.answer("❌ Введите корректное число (например: 2.5)")
        return
    await state.update_data(width=width)
    await message.answer(f"✅ Ширина: {width} м\n\nТеперь введите <b>длину</b> ковра в метрах:", parse_mode="HTML")
    await state.set_state(OrderFSM.entering_length)


@router.message(OrderFSM.entering_length)
async def enter_length(message: Message, state: FSMContext):
    try:
        length = float(message.text.replace(",", "."))
        if length <= 0 or length > 20:
            raise ValueError
    except ValueError:
        await message.answer("❌ Введите корректное число (например: 3)")
        return

    data = await state.get_data()
    width = data["width"]
    price_per_sqm = data["price_per_sqm"]
    service_name = data["service_name"]

    area = round(width * length, 2)
    raw_price = area * price_per_sqm
    total_price = max(raw_price, 250)

    await state.update_data(length=length, area=area, base_price=total_price)

    text = (
        f"📊 <b>Расчёт стоимости:</b>\n\n"
        f"🏷 Услуга: {service_name}\n"
        f"📐 Размер: {width} × {length} м = {area} кв.м\n"
        f"💰 Стоимость: <b>{total_price:.0f} руб.</b>\n"
        f"{'(применён минимальный тариф 250 руб.)' if raw_price < 250 else ''}\n\n"
        f"🚚 Нужна ли доставка (забрать и привезти)?"
    )
    await message.answer(text, reply_markup=kb.delivery_keyboard(), parse_mode="HTML")
    await state.set_state(OrderFSM.choosing_delivery)


@router.callback_query(OrderFSM.choosing_delivery, F.data.startswith("delivery_"))
async def choose_delivery(callback: CallbackQuery, state: FSMContext):
    delivery = callback.data == "delivery_yes"
    data = await state.get_data()
    base_price = data["base_price"]

    delivery_cost = 0
    delivery_text = ""
    if delivery:
        if base_price >= 2000:
            delivery_text = "🎉 Доставка <b>бесплатно</b> (заказ от 2000 руб.)"
        else:
            delivery_cost = 500
            delivery_text = f"🚚 Доставка: <b>+{delivery_cost} руб.</b>"

    total = base_price + delivery_cost
    await state.update_data(delivery=delivery, delivery_cost=delivery_cost, total_price=total)

    text = (
        f"✅ Отлично!\n\n"
        f"💰 Стоимость чистки: {base_price:.0f} руб.\n"
    )
    if delivery:
        text += f"{delivery_text}\n"
    text += f"<b>Итого: {total:.0f} руб.</b>\n\n"
    text += "📅 Выберите удобную дату для забора ковра:"

    await callback.message.edit_text(text, reply_markup=kb.calendar_keyboard(), parse_mode="HTML")
    await state.set_state(OrderFSM.choosing_date)


@router.callback_query(F.data == "cal_ignore")
async def cal_ignore(callback: CallbackQuery):
    await callback.answer()


@router.callback_query(F.data.startswith("cal_prev_"))
async def cal_prev(callback: CallbackQuery):
    _, _, year, month = callback.data.split("_")
    year, month = int(year), int(month)
    month -= 1
    if month < 1:
        month = 12
        year -= 1
    await callback.message.edit_reply_markup(reply_markup=kb.calendar_keyboard(year, month))


@router.callback_query(F.data.startswith("cal_next_"))
async def cal_next(callback: CallbackQuery):
    _, _, year, month = callback.data.split("_")
    year, month = int(year), int(month)
    month += 1
    if month > 12:
        month = 1
        year += 1
    await callback.message.edit_reply_markup(reply_markup=kb.calendar_keyboard(year, month))


@router.callback_query(OrderFSM.choosing_date, F.data.startswith("cal_date_"))
async def choose_date(callback: CallbackQuery, state: FSMContext):
    _, _, year, month, day = callback.data.split("_")
    pickup_date = f"{day}.{month}.{year}"
    await state.update_data(pickup_date=pickup_date)
    await callback.message.edit_text(
        f"📅 Дата забора: <b>{pickup_date}</b>\n\n"
        f"📱 Введите ваш <b>номер телефона</b> для связи:",
        parse_mode="HTML"
    )
    await state.set_state(OrderFSM.entering_phone)


@router.message(OrderFSM.entering_phone)
async def enter_phone(message: Message, state: FSMContext):
    phone = message.text.strip()
    if len(phone) < 7:
        await message.answer("❌ Введите корректный номер телефона")
        return
    await state.update_data(phone=phone)
    data = await state.get_data()

    delivery_str = "Да (500 руб.)" if data.get("delivery") else "Нет (привезу сам)"
    if data.get("delivery") and data.get("base_price", 0) >= 2000:
        delivery_str = "Да (бесплатно)"

    text = (
        f"📋 <b>Подтвердите заявку:</b>\n\n"
        f"🏷 Услуга: {data['service_name']}\n"
        f"📐 Размер: {data['width']} × {data['length']} м ({data['area']} кв.м)\n"
        f"🚚 Доставка: {delivery_str}\n"
        f"📅 Дата забора: {data['pickup_date']}\n"
        f"📱 Телефон: {phone}\n"
        f"💰 <b>Итого: {data['total_price']:.0f} руб.</b>"
    )
    await message.answer(text, reply_markup=kb.confirm_order_keyboard(), parse_mode="HTML")
    await state.set_state(OrderFSM.confirming)


@router.callback_query(OrderFSM.confirming, F.data == "confirm_order")
async def confirm_order(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    user = callback.from_user

    order_id = await db.create_order(
        user_id=user.id,
        username=user.username,
        full_name=user.full_name,
        phone=data["phone"],
        service=data["service_name"],
        width=data["width"],
        length=data["length"],
        area=data["area"],
        total_price=data["total_price"],
        delivery=1 if data.get("delivery") else 0,
        pickup_date=data["pickup_date"]
    )

    await state.clear()
    await callback.message.edit_text(
        f"🎉 <b>Заявка №{order_id} принята!</b>\n\n"
        f"Наш менеджер свяжется с вами в ближайшее время.\n"
        f"📱 Телефон менеджера: {MANAGER_PHONE}\n\n"
        f"Отслеживайте статус заказа в разделе <b>📦 Статус заказа</b>",
        reply_markup=kb.back_to_menu(),
        parse_mode="HTML"
    )


# ───── ORDER STATUS ─────
@router.callback_query(F.data == "order_status")
async def show_order_status(callback: CallbackQuery):
    orders = await db.get_user_orders(callback.from_user.id)
    if not orders:
        await callback.message.edit_text(
            "📦 У вас пока нет заказов.\n\nОформите первую заявку! 🧹",
            reply_markup=kb.back_to_menu()
        )
        return
    await callback.message.edit_text(
        "📦 <b>Ваши заказы:</b>\n\nВыберите заказ для просмотра деталей:",
        reply_markup=kb.order_status_keyboard(orders),
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("order_detail_"))
async def show_order_detail(callback: CallbackQuery):
    order_id = int(callback.data.split("_")[2])
    order = await db.get_order(order_id)
    if not order or order[1] != callback.from_user.id:
        await callback.answer("Заказ не найден", show_alert=True)
        return

    status_text = STATUS_MAP.get(order[12], "Неизвестно")
    delivery_text = "Да" if order[10] else "Нет (самовывоз)"

    text = (
        f"📦 <b>Заказ #{order[0]}</b>\n\n"
        f"🏷 Услуга: {order[5]}\n"
        f"📐 Размер: {order[6]} × {order[7]} м ({order[8]} кв.м)\n"
        f"🚚 Доставка: {delivery_text}\n"
        f"📅 Дата забора: {order[11]}\n"
        f"💰 Стоимость: {order[9]:.0f} руб.\n\n"
        f"📊 Статус: {status_text}"
    )
    await callback.message.edit_text(text, reply_markup=kb.back_to_menu(), parse_mode="HTML")


# ───── REVIEWS ─────
@router.callback_query(F.data == "reviews")
async def show_reviews(callback: CallbackQuery):
    reviews = await db.get_reviews(status="approved")
    if not reviews:
        text = "⭐ <b>Отзывы</b>\n\nПока отзывов нет. Будьте первым!"
    else:
        text = "⭐ <b>Отзывы наших клиентов:</b>\n\n"
        for r in reviews[-10:]:
            stars = "⭐" * r[6]
            name = r[3] or "Аноним"
            text += f"{stars} <b>{name}</b>\n{r[7]}\n\n"

    kb_builder = __import__("aiogram.utils.keyboard", fromlist=["InlineKeyboardBuilder"]).InlineKeyboardBuilder()
    kb_builder.button(text="✍️ Оставить отзыв", callback_data="leave_review")
    kb_builder.button(text="🏠 Главное меню", callback_data="main_menu")
    kb_builder.adjust(1)
    await callback.message.edit_text(text, reply_markup=kb_builder.as_markup(), parse_mode="HTML")


@router.callback_query(F.data == "leave_review")
async def start_review(callback: CallbackQuery, state: FSMContext):
    orders = await db.get_user_orders(callback.from_user.id)
    completed = [o for o in orders if o[12] in ("ready", "delivered")]
    if not completed:
        await callback.answer("Оставить отзыв можно только после выполненного заказа", show_alert=True)
        return
    await state.update_data(orders=[(o[0], o[5]) for o in completed])
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    kb2 = InlineKeyboardBuilder()
    for o in completed:
        kb2.button(text=f"Заказ #{o[0]} — {o[5]}", callback_data=f"review_order_{o[0]}")
    kb2.adjust(1)
    await callback.message.edit_text("Выберите заказ, к которому хотите оставить отзыв:", reply_markup=kb2.as_markup())
    await state.set_state(ReviewFSM.choosing_order)


@router.callback_query(ReviewFSM.choosing_order, F.data.startswith("review_order_"))
async def review_choose_order(callback: CallbackQuery, state: FSMContext):
    order_id = int(callback.data.split("_")[2])
    await state.update_data(order_id=order_id)
    await callback.message.edit_text("Оцените нашу работу:", reply_markup=kb.rating_keyboard())
    await state.set_state(ReviewFSM.choosing_rating)


@router.callback_query(ReviewFSM.choosing_rating, F.data.startswith("rating_"))
async def review_choose_rating(callback: CallbackQuery, state: FSMContext):
    rating = int(callback.data.split("_")[1])
    await state.update_data(rating=rating)
    await callback.message.edit_text(f"{'⭐' * rating} Отлично!\n\nНапишите ваш отзыв:")
    await state.set_state(ReviewFSM.entering_text)


@router.message(ReviewFSM.entering_text)
async def review_enter_text(message: Message, state: FSMContext):
    data = await state.get_data()
    user = message.from_user
    review_id = await db.create_review(
        user_id=user.id,
        username=user.username,
        full_name=user.full_name,
        order_id=data.get("order_id"),
        rating=data["rating"],
        text=message.text
    )
    await state.clear()
    await message.answer(
        "✅ <b>Спасибо за отзыв!</b>\n\nОн будет опубликован после проверки модератором.",
        reply_markup=kb.back_to_menu(),
        parse_mode="HTML"
    )


# ───── GALLERY ─────
@router.callback_query(F.data == "gallery")
async def show_gallery(callback: CallbackQuery):
    items = await db.get_gallery()
    if not items:
        await callback.message.edit_text(
            "🖼 Галерея пока пуста. Скоро добавим фото наших работ!",
            reply_markup=kb.back_to_menu()
        )
        return
    await callback.message.edit_text("🖼 <b>Наши работы — фото до/после:</b>", parse_mode="HTML")
    for item in items:
        caption = item[3] or "Результат нашей работы"
        try:
            await callback.message.answer_photo(photo=item[1], caption=f"До: {caption}")
            await callback.message.answer_photo(photo=item[2], caption=f"После: {caption}")
        except Exception:
            pass
    await callback.message.answer("🏠 Вернуться:", reply_markup=kb.back_to_menu())


# ───── PROMOTIONS ─────
@router.callback_query(F.data == "promotions")
async def show_promotions(callback: CallbackQuery):
    promos = await db.get_promotions()
    if not promos:
        await callback.message.edit_text(
            "🎁 Акций пока нет. Следите за обновлениями!",
            reply_markup=kb.back_to_menu()
        )
        return
    text = "🎁 <b>Актуальные акции:</b>\n\n"
    for p in promos:
        text += f"🔥 <b>{p[1]}</b>\n{p[2]}\n\n"
    await callback.message.edit_text(text, reply_markup=kb.back_to_menu(), parse_mode="HTML")


# ───── FAQ ─────
@router.callback_query(F.data == "faq")
async def show_faq(callback: CallbackQuery):
    await callback.message.edit_text(
        FAQ_TEXT.format(phone=MANAGER_PHONE),
        reply_markup=kb.back_to_menu(),
        parse_mode="HTML"
    )


# ───── MANAGER ─────
@router.callback_query(F.data == "manager")
async def show_manager(callback: CallbackQuery):
    await callback.message.edit_text(
        f"📞 <b>Связаться с менеджером</b>\n\n"
        f"Телефон: <b>{MANAGER_PHONE}</b>\n\n"
        f"Мы работаем:\n"
        f"🕗 Пн–Пт: 8:00 – 18:00\n"
        f"🕗 Сб–Вс: 9:00 – 16:00",
        reply_markup=kb.back_to_menu(),
        parse_mode="HTML"
    )
