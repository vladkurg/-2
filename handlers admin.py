import os
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

import database as db
import keyboards as kb

router = Router()

ADMIN_IDS: set = set()

STATUS_LABELS = {
    "new": "🆕 Новый",
    "in_progress": "🔄 В работе",
    "ready": "✅ Готов",
    "delivered": "📦 Доставлен",
}


class AdminFSM(StatesGroup):
    entering_password = State()
    setting_password = State()
    editing_price = State()
    adding_promo_title = State()
    adding_promo_desc = State()
    adding_gallery_before = State()
    adding_gallery_after = State()
    adding_gallery_desc = State()


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


# ───── ADMIN LOGIN ─────
@router.message(Command("admin"))
async def admin_entry(message: Message, state: FSMContext):
    user_id = message.from_user.id

    if is_admin(user_id):
        await message.answer("👑 <b>Добро пожаловать в админ-панель!</b>", reply_markup=kb.admin_main_menu(), parse_mode="HTML")
        return

    saved_password = await db.get_setting("admin_password")

    if not saved_password:
        await message.answer("🔐 Добро пожаловать! Создайте пароль администратора:\n\n(Этот пароль будет сохранён и использован для входа)")
        await state.set_state(AdminFSM.setting_password)
    else:
        await message.answer("🔐 Введите пароль администратора:")
        await state.set_state(AdminFSM.entering_password)


@router.message(AdminFSM.setting_password)
async def set_admin_password(message: Message, state: FSMContext):
    password = message.text.strip()
    if len(password) < 4:
        await message.answer("❌ Пароль должен содержать минимум 4 символа. Попробуйте снова:")
        return
    await db.set_setting("admin_password", password)
    ADMIN_IDS.add(message.from_user.id)
    await state.clear()
    await message.answer(
        f"✅ Пароль установлен!\n\n👑 <b>Вы вошли в админ-панель</b>",
        reply_markup=kb.admin_main_menu(),
        parse_mode="HTML"
    )


@router.message(AdminFSM.entering_password)
async def check_admin_password(message: Message, state: FSMContext):
    saved_password = await db.get_setting("admin_password")
    if message.text.strip() == saved_password:
        ADMIN_IDS.add(message.from_user.id)
        await state.clear()
        await message.answer("✅ <b>Вход выполнен!</b>", reply_markup=kb.admin_main_menu(), parse_mode="HTML")
    else:
        await message.answer("❌ Неверный пароль. Попробуйте снова:")


# ───── ADMIN MENU ─────
@router.callback_query(F.data == "admin_menu")
async def admin_menu(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    await callback.message.edit_text("👑 <b>Админ-панель</b>", reply_markup=kb.admin_main_menu(), parse_mode="HTML")


# ───── ORDERS ─────
@router.callback_query(F.data == "admin_orders")
async def admin_orders(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    orders = await db.get_orders(limit=30)
    if not orders:
        from aiogram.utils.keyboard import InlineKeyboardBuilder
        kb2 = InlineKeyboardBuilder()
        kb2.button(text="◀️ Назад", callback_data="admin_menu")
        await callback.message.edit_text("📋 Заявок пока нет.", reply_markup=kb2.as_markup())
        return
    await callback.message.edit_text("📋 <b>Все заявки:</b>", reply_markup=kb.admin_orders_keyboard(orders), parse_mode="HTML")


@router.callback_query(F.data.startswith("admin_order_"))
async def admin_order_detail(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    order_id = int(callback.data.split("_")[2])
    order = await db.get_order(order_id)
    if not order:
        await callback.answer("Заказ не найден", show_alert=True)
        return

    status_text = STATUS_LABELS.get(order[12], "Неизвестно")
    delivery_text = "Да (500 руб.)" if order[10] else "Нет (самовывоз)"

    text = (
        f"📋 <b>Заказ #{order[0]}</b>\n\n"
        f"👤 Клиент: {order[3] or 'Без имени'}\n"
        f"📱 Телефон: {order[4]}\n"
        f"🏷 Услуга: {order[5]}\n"
        f"📐 Размер: {order[6]} × {order[7]} м ({order[8]} кв.м)\n"
        f"🚚 Доставка: {delivery_text}\n"
        f"📅 Дата забора: {order[11]}\n"
        f"💰 Стоимость: {order[9]:.0f} руб.\n"
        f"📊 Статус: {status_text}\n"
        f"🕐 Создан: {order[13]}"
    )
    await callback.message.edit_text(text, reply_markup=kb.admin_order_detail_keyboard(order_id), parse_mode="HTML")


@router.callback_query(F.data.startswith("admin_status_"))
async def admin_change_status(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    parts = callback.data.split("_")
    order_id = int(parts[2])
    new_status = parts[3]
    await db.update_order_status(order_id, new_status)

    # Notify client
    order = await db.get_order(order_id)
    if order and order[1]:
        try:
            status_text = STATUS_LABELS.get(new_status, new_status)
            await callback.bot.send_message(
                order[1],
                f"📦 <b>Обновление по заказу #{order_id}</b>\n\nНовый статус: {status_text}",
                parse_mode="HTML"
            )
        except Exception:
            pass

    await callback.answer(f"✅ Статус изменён: {STATUS_LABELS.get(new_status)}")
    await admin_order_detail(callback)


# ───── REVIEWS MODERATION ─────
@router.callback_query(F.data == "admin_reviews")
async def admin_reviews(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    reviews = await db.get_reviews(status="pending")
    if not reviews:
        from aiogram.utils.keyboard import InlineKeyboardBuilder
        kb2 = InlineKeyboardBuilder()
        kb2.button(text="◀️ Назад", callback_data="admin_menu")
        await callback.message.edit_text("⭐ Нет отзывов на модерации.", reply_markup=kb2.as_markup())
        return

    r = reviews[0]
    stars = "⭐" * r[6]
    text = (
        f"⭐ <b>Отзыв на модерации ({len(reviews)} шт.)</b>\n\n"
        f"👤 {r[3] or 'Аноним'}\n"
        f"Оценка: {stars}\n"
        f"📝 {r[7]}\n"
        f"🕐 {r[9]}"
    )
    await callback.message.edit_text(text, reply_markup=kb.admin_review_keyboard(r[0]), parse_mode="HTML")


@router.callback_query(F.data.startswith("admin_review_approve_"))
async def approve_review(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    review_id = int(callback.data.split("_")[3])
    review = await db.get_review(review_id)
    await db.update_review_status(review_id, "approved")

    # Publish to channel
    if review:
        channel_id = os.getenv("CHANNEL_ID")
        if channel_id:
            try:
                stars = "⭐" * review[6]
                name = review[3] or "Аноним"
                text = f"{stars} <b>{name}</b>\n\n{review[7]}"
                await callback.bot.send_message(int(channel_id), text, parse_mode="HTML")
            except Exception as e:
                print(f"Channel publish error: {e}")

    await callback.answer("✅ Отзыв опубликован!")
    await admin_reviews(callback)


@router.callback_query(F.data.startswith("admin_review_reject_"))
async def reject_review(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    review_id = int(callback.data.split("_")[3])
    await db.update_review_status(review_id, "rejected")
    await callback.answer("❌ Отзыв отклонён")
    await admin_reviews(callback)


# ───── PRICES ─────
@router.callback_query(F.data == "admin_prices")
async def admin_prices(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    prices = await db.get_prices()
    await callback.message.edit_text("💰 <b>Управление ценами:</b>", reply_markup=kb.admin_prices_keyboard(prices), parse_mode="HTML")


@router.callback_query(F.data.startswith("admin_edit_price_"))
async def admin_edit_price(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    price_id = int(callback.data.split("_")[3])
    prices = await db.get_prices()
    price_info = next((p for p in prices if p[0] == price_id), None)
    if not price_info:
        await callback.answer("Не найдено")
        return
    await state.update_data(editing_price_id=price_id, editing_price_name=price_info[1])
    await callback.message.edit_text(
        f"✏️ Редактирование: <b>{price_info[1]}</b>\n"
        f"Текущая цена: {price_info[2]:.0f} руб/кв.м\n\n"
        f"Введите новую цену за кв.м:",
        parse_mode="HTML"
    )
    await state.set_state(AdminFSM.editing_price)


@router.message(AdminFSM.editing_price)
async def save_new_price(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    try:
        new_price = float(message.text.replace(",", "."))
        if new_price < 1:
            raise ValueError
    except ValueError:
        await message.answer("❌ Введите корректную цену (число больше 0):")
        return
    data = await state.get_data()
    await db.update_price(data["editing_price_id"], new_price)
    await state.clear()
    prices = await db.get_prices()
    await message.answer(
        f"✅ Цена на <b>{data['editing_price_name']}</b> обновлена: <b>{new_price:.0f} руб/кв.м</b>",
        reply_markup=kb.admin_prices_keyboard(prices),
        parse_mode="HTML"
    )


# ───── PROMOTIONS ─────
@router.callback_query(F.data == "admin_promotions")
async def admin_promotions(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    promos = await db.get_promotions()
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    kb2 = InlineKeyboardBuilder()
    for p in promos:
        kb2.button(text=f"🔥 {p[1]}", callback_data=f"admin_promo_toggle_{p[0]}")
    kb2.button(text="➕ Добавить акцию", callback_data="admin_promo_add")
    kb2.button(text="◀️ Назад", callback_data="admin_menu")
    kb2.adjust(1)
    await callback.message.edit_text("🎁 <b>Акции:</b>", reply_markup=kb2.as_markup(), parse_mode="HTML")


@router.callback_query(F.data == "admin_promo_add")
async def admin_promo_add(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    await callback.message.edit_text("➕ Введите <b>название</b> акции:", parse_mode="HTML")
    await state.set_state(AdminFSM.adding_promo_title)


@router.message(AdminFSM.adding_promo_title)
async def promo_add_title(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.update_data(promo_title=message.text)
    await message.answer("Введите <b>описание</b> акции:", parse_mode="HTML")
    await state.set_state(AdminFSM.adding_promo_desc)


@router.message(AdminFSM.adding_promo_desc)
async def promo_add_desc(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    data = await state.get_data()
    await db.add_promotion(data["promo_title"], message.text)
    await state.clear()
    await message.answer("✅ Акция добавлена!", reply_markup=kb.admin_main_menu())


@router.callback_query(F.data.startswith("admin_promo_toggle_"))
async def admin_promo_toggle(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    promo_id = int(callback.data.split("_")[3])
    await db.toggle_promotion(promo_id, 0)
    await callback.answer("❌ Акция деактивирована")
    await admin_promotions(callback)


# ───── GALLERY ─────
@router.callback_query(F.data == "admin_gallery")
async def admin_gallery(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    await callback.message.edit_text("🖼 Отправьте фото <b>ДО</b> чистки:", parse_mode="HTML")
    await state.set_state(AdminFSM.adding_gallery_before)


@router.message(AdminFSM.adding_gallery_before, F.photo)
async def gallery_before(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    file_id = message.photo[-1].file_id
    await state.update_data(before_file_id=file_id)
    await message.answer("Отправьте фото <b>ПОСЛЕ</b> чистки:", parse_mode="HTML")
    await state.set_state(AdminFSM.adding_gallery_after)


@router.message(AdminFSM.adding_gallery_after, F.photo)
async def gallery_after(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    file_id = message.photo[-1].file_id
    await state.update_data(after_file_id=file_id)
    await message.answer("Введите <b>описание</b> (например: Шерстяной ковёр, удаление пятен):", parse_mode="HTML")
    await state.set_state(AdminFSM.adding_gallery_desc)


@router.message(AdminFSM.adding_gallery_desc)
async def gallery_desc(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    data = await state.get_data()
    await db.add_gallery_item(data["before_file_id"], data["after_file_id"], message.text)
    await state.clear()
    await message.answer("✅ Фото добавлено в галерею!", reply_markup=kb.admin_main_menu())


# ───── STATS ─────
@router.callback_query(F.data == "admin_stats")
async def admin_stats(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    all_orders = await db.get_orders(limit=1000)
    reviews = await db.get_reviews()

    total = len(all_orders)
    new_count = sum(1 for o in all_orders if o[12] == "new")
    in_progress = sum(1 for o in all_orders if o[12] == "in_progress")
    ready = sum(1 for o in all_orders if o[12] == "ready")
    delivered = sum(1 for o in all_orders if o[12] == "delivered")
    total_revenue = sum(o[9] for o in all_orders if o[12] == "delivered")
    pending_reviews = sum(1 for r in reviews if r[8] == "pending")

    text = (
        f"📊 <b>Статистика</b>\n\n"
        f"📋 Всего заявок: <b>{total}</b>\n"
        f"🆕 Новых: <b>{new_count}</b>\n"
        f"🔄 В работе: <b>{in_progress}</b>\n"
        f"✅ Готовых: <b>{ready}</b>\n"
        f"📦 Выполненных: <b>{delivered}</b>\n\n"
        f"💰 Выручка (выполненные): <b>{total_revenue:.0f} руб.</b>\n\n"
        f"⭐ Отзывов на модерации: <b>{pending_reviews}</b>"
    )
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    kb2 = InlineKeyboardBuilder()
    kb2.button(text="◀️ Назад", callback_data="admin_menu")
    await callback.message.edit_text(text, reply_markup=kb2.as_markup(), parse_mode="HTML")
