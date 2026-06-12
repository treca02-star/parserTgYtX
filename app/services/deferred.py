import logging
from datetime import date, datetime, timedelta, timezone

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.models import AppSettings, ContentItem

logger = logging.getLogger(__name__)
MOSCOW_TZ = timezone(timedelta(hours=3), name="MSK")
DEFAULT_REMINDER_TIME = "18:00"


def reminder_is_due(
    reminder_time: str,
    now: datetime | None = None,
    last_sent_date: date | None = None,
) -> bool:
    current = now.astimezone(MOSCOW_TZ) if now else datetime.now(MOSCOW_TZ)
    try:
        hour, minute = (int(part) for part in reminder_time.split(":", 1))
    except (TypeError, ValueError):
        hour, minute = 18, 0
    return (
        current.hour == hour
        and current.minute == minute
        and last_sent_date != current.date()
    )


async def send_deferred_reminder(
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> int:
    async with session_factory() as session:
        count = int(
            await session.scalar(
                select(func.count()).select_from(ContentItem).where(
                    ContentItem.status == "deferred"
                )
            )
            or 0
        )
    if count:
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=f"🕓 Открыть отложку ({count})",
                        callback_data="menu:deferred",
                    )
                ]
            ]
        )
        await bot.send_message(
            settings.telegram_owner_id,
            f"🕕 <b>В отложке {count} материалов</b>\n\nМожно выбрать, что отправить в обработку.",
            reply_markup=keyboard,
        )
    return count


async def deferred_reminder_loop(
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    import asyncio

    last_sent_date: date | None = None
    while True:
        try:
            async with session_factory() as session:
                app_settings = await session.get(AppSettings, 1)
                reminder_time = (
                    app_settings.deferred_reminder_time
                    if app_settings
                    else DEFAULT_REMINDER_TIME
                )
            now = datetime.now(MOSCOW_TZ)
            if reminder_is_due(reminder_time, now, last_sent_date):
                await send_deferred_reminder(bot, session_factory, settings)
                last_sent_date = now.date()
        except Exception:
            logger.exception("Could not send deferred-items reminder")
        await asyncio.sleep(30)
