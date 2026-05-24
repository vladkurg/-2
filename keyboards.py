from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
import calendar
from datetime import date, timedelta


def main_menu():
    kb = InlineKeyboardBuilder()
    kb.button(text="🧮 Калькулятор и заявка", callback_data="calculator")
    kb.button(text="💰 Прайс-лист", callback_data="prices")
    kb.button(text="📦 Статус заказа", callback_data="order_status")
    kb.button(text="⭐ Отзывы", callback_data="reviews")
    kb.button(text="🖼 Фото до/после", callback_data="gallery")
    kb.button(text="🎁 Акции", callback_data="promotions")
    kb.button(text="❓ FAQ", callback_data="faq")
    kb.button(text="📞 Связаться с менеджером", callback_data="manager")
    kb.adjust(2, 2, 2, 2)
    return kb.as_markup()


def back_to_menu():
    kb = InlineKeyboardBuilder()
    kb.button(text="🏠 Главное меню", callback_data="main_menu")
    return kb.as_markup()


def services_keyboard(prices):
    kb = InlineKeyboardBuilder()
    for pid, service, price in prices:
        kb.button(text=f"{service} — {price:.0f} руб/кв.м", callback_data=f"service_{pid}")
    kb.button(text="🏠 Главное меню", callback_data="main_menu")
    kb.adjust(1)
    return kb.as_markup()


def confirm_order_keyboard():
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Подтвердить заявку", callback_data="confirm_order")
    kb.button(text="🔄 Пересчитать", callback_data="calculator")
    kb.button(text="🏠 Главное меню", callback_data="main_menu")
    kb.adjust(1)
    return kb.as_markup()


def delivery_keyboard():
    kb = InlineKeyboardBuilder()
    kb.button(text="🚚 Да, нужна доставка (+500 руб.)", callback_data="delivery_yes")
    kb.button(text="🚶 Привезу сам", callback_data="delivery_no")
    kb.adjust(1)
    return kb.as_markup()


def calendar_keyboard(year=None, month=None):
    today = date.today()
    if year is None:
        year = today.year
    if month is None:
        month = today.month

    kb = InlineKeyboardBuilder()

    month_names = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
                   "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]

    kb.button(text=f"◀️", callback_data=f"cal_prev_{year}_{month}")
    kb.button(text=f"{month_names[month-1]} {year}", callback_data="cal_ignore")
    kb.button(text=f"▶️", callback_data=f"cal_next_{year}_{month}")

    days = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    for d in days:
        kb.button(text=d, callback_data="cal_ignore")

    cal = calendar.monthcalendar(year, month)
    for week in cal:
        for day in week:
            if day == 0:
                kb.button(text=" ", callback_data="cal_ignore")
            else:
                current = date(year, month, day)
                if current < today + timedelta(days=1):
                    kb.button(text=f"·{day}·", callback_data="cal_ignore")
                else:
                    kb.button(text=str(day), callback_data=f"cal_date_{year}_{month}_{day}")

    kb.button(text="🏠 Главное меню", callback_data="main_menu")
    kb.adjust(3, 7, *[7]*len(cal), 1)
    return kb.as_markup()


def rating_keyboard():
    kb = InlineKeyboardBuilder()
    for i in range(1, 6):
        kb.button(text="⭐" * i, callback_data=f"rating_{i}")
    kb.button(text="🏠 Главное меню", callback_data="main_menu")
    kb.adjust(1)
    return kb.as_markup()


def order_status_keyboard(orders):
    kb = InlineKeyboardBuilder()
    for order in orders:
        oid = order[0]
        status = order[12]
        status_emoji = {"new": "🆕", "in_progress": "🔄", "ready": "✅", "delivered": "📦"}.get(status, "❓")
        kb.button(text=f"{status_emoji} Заказ #{oid}", callback_data=f"order_detail_{oid}")
    kb.button(text="🏠 Главное меню", callback_data="main_menu")
    kb.adjust(1)
    return kb.as_markup()


# ADMIN keyboards
def admin_main_menu():
    kb = InlineKeyboardBuilder()
    kb.button(text="📋 Заявки", callback_data="admin_orders")
    kb.button(text="⭐ Модерация отзывов", callback_data="admin_reviews")
    kb.button(text="💰 Цены", callback_data="admin_prices")
    kb.button(text="🎁 Акции", callback_data="admin_promotions")
    kb.button(text="🖼 Галерея", callback_data="admin_gallery")
    kb.button(text="📊 Статистика", callback_data="admin_stats")
    kb.adjust(2)
    return kb.as_markup()


def admin_orders_keyboard(orders):
    kb = InlineKeyboardBuilder()
    status_map = {"new": "🆕", "in_progress": "🔄", "ready": "✅", "delivered": "📦"}
    for order in orders:
        oid = order[0]
        name = order[3] or "Без имени"
        status = order[12]
        emoji = status_map.get(status, "❓")
        kb.button(text=f"{emoji} #{oid} {name}", callback_data=f"admin_order_{oid}")
    kb.button(text="◀️ Назад", callback_data="admin_menu")
    kb.adjust(1)
    return kb.as_markup()


def admin_order_detail_keyboard(order_id):
    kb = InlineKeyboardBuilder()
    kb.button(text="🔄 В работе", callback_data=f"admin_status_{order_id}_in_progress")
    kb.button(text="✅ Готов", callback_data=f"admin_status_{order_id}_ready")
    kb.button(text="📦 Доставлен", callback_data=f"admin_status_{order_id}_delivered")
    kb.button(text="◀️ Назад", callback_data="admin_orders")
    kb.adjust(3, 1)
    return kb.as_markup()


def admin_review_keyboard(review_id):
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Опубликовать", callback_data=f"admin_review_approve_{review_id}")
    kb.button(text="❌ Отклонить", callback_data=f"admin_review_reject_{review_id}")
    kb.button(text="◀️ К списку", callback_data="admin_reviews")
    kb.adjust(2, 1)
    return kb.as_markup()


def admin_prices_keyboard(prices):
    kb = InlineKeyboardBuilder()
    for pid, service, price in prices:
        kb.button(text=f"✏️ {service}: {price:.0f} руб.", callback_data=f"admin_edit_price_{pid}")
    kb.button(text="◀️ Назад", callback_data="admin_menu")
    kb.adjust(1)
    return kb.as_markup()
