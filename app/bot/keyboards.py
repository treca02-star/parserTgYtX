from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📥 Лента", callback_data="menu:feed"),
                InlineKeyboardButton(text="🕓 Отложка", callback_data="menu:deferred"),
            ],
            [
                InlineKeyboardButton(text="📺 Источники", callback_data="menu:sources"),
                InlineKeyboardButton(text="🎯 AI-фильтр", callback_data="menu:filter"),
            ],
            [
                InlineKeyboardButton(text="📊 Статистика", callback_data="menu:stats"),
                InlineKeyboardButton(text="⚙️ Настройки", callback_data="menu:settings"),
            ],
            [
                InlineKeyboardButton(
                    text="🖼 Водяной знак",
                    callback_data="menu:watermark",
                )
            ],
        ]
    )


def back_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="◀️ Главное меню", callback_data="menu:main")]]
    )


def settings_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🕕 Время напоминания",
                    callback_data="settings:deferred-time",
                )
            ],
            [InlineKeyboardButton(text="◀️ Главное меню", callback_data="menu:main")],
        ]
    )


def sources_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➕ Добавить YouTube", callback_data="source:add")],
            [InlineKeyboardButton(text="📋 Список источников", callback_data="source:list")],
            [InlineKeyboardButton(text="◀️ Главное меню", callback_data="menu:main")],
        ]
    )


def filter_menu(current: str) -> InlineKeyboardMarkup:
    labels = [("all", "Все"), ("soft", "Мягкий"), ("medium", "Средний"), ("strict", "Строгий")]
    rows = [
        [
            InlineKeyboardButton(
                text=("✅ " if key == current else "") + label,
                callback_data=f"filter:set:{key}",
            )
            for key, label in labels[:2]
        ],
        [
            InlineKeyboardButton(
                text=("✅ " if key == current else "") + label,
                callback_data=f"filter:set:{key}",
            )
            for key, label in labels[2:]
        ],
        [InlineKeyboardButton(text="✍️ Изменить промпт", callback_data="filter:prompt")],
        [InlineKeyboardButton(text="◀️ Главное меню", callback_data="menu:main")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)
