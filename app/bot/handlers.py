import logging
import re
from datetime import UTC, datetime
from typing import cast

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards import back_menu, filter_menu, main_menu, settings_menu, sources_menu
from app.config import Settings
from app.models import AppSettings, ContentItem, Source
from app.schemas import NormalizedItem
from app.services.content import ContentPipeline, format_card, item_keyboard
from app.services.downloader import DownloadError, MediaDownloader
from app.services.youtube import YouTubeService

router = Router()
logger = logging.getLogger(__name__)
SOURCE_MODE_LABELS = {
    "all": "🟢 Все видео",
    "long": "🎬 Только длинные",
    "shorts": "📱 Только Shorts",
    "off": "⏸ Выключен",
}
NEXT_SOURCE_MODE = {"all": "long", "long": "shorts", "shorts": "off", "off": "all"}


class Form(StatesGroup):
    youtube_url = State()
    filter_prompt = State()
    deferred_reminder_time = State()


def allowed(user_id: int | None, settings: Settings) -> bool:
    return user_id == settings.telegram_owner_id


def callback_data(callback: CallbackQuery) -> str:
    if callback.data is None:
        raise ValueError("Callback data is required")
    return callback.data


def callback_message(callback: CallbackQuery) -> Message:
    if not isinstance(callback.message, Message):
        raise ValueError("Accessible callback message is required")
    return callback.message


async def answer_callback_safely(
    callback: CallbackQuery,
    text: str | None = None,
    show_alert: bool = False,
) -> None:
    try:
        await callback.answer(text, show_alert=show_alert)
    except TelegramBadRequest as error:
        if "query is too old" not in str(error).lower():
            raise


async def edit_callback_safely(
    callback: CallbackQuery,
    text: str,
    reply_markup: InlineKeyboardMarkup,
) -> None:
    try:
        await callback_message(callback).edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as error:
        if "message is not modified" not in str(error).lower():
            raise


async def update_original_card(
    callback: CallbackQuery,
    item: ContentItem,
    settings: Settings,
    *,
    sent: bool = False,
    deferred: bool = False,
    dismissed: bool = False,
) -> None:
    message = callback_message(callback)
    if not item.review_message_id or item.review_message_id == message.message_id:
        return
    if message.bot is None:
        return
    try:
        await message.bot.edit_message_text(
            format_card(
                item,
                sent=sent,
                deferred=deferred,
                dismissed=dismissed,
            ),
            chat_id=settings.telegram_owner_id,
            message_id=item.review_message_id,
            reply_markup=item_keyboard(
                item.id,
                item.url,
                item.media_type,
                sent=sent or dismissed,
                deferred=deferred,
            ),
        )
    except TelegramBadRequest as error:
        text = str(error).lower()
        if "message is not modified" not in text and "message to edit not found" not in text:
            raise


def telegram_message_url(message: Message) -> str:
    if message.chat.username:
        return f"https://t.me/{message.chat.username}/{message.message_id}"
    chat_id = str(message.chat.id)
    if message.chat.type == "supergroup" and chat_id.startswith("-100"):
        return f"https://t.me/c/{chat_id[4:]}/{message.message_id}"
    return ""


def is_source_label(value: str) -> bool:
    normalized = re.sub(r"[^\w]+", "", value, flags=re.UNICODE).casefold()
    return normalized == "источник"


def parse_inbox_content(message: Message) -> tuple[str, str, str]:
    content = message.text or message.caption or ""
    lines = content.splitlines()
    author = message.author_signature or message.chat.title or "Telegram"
    if lines:
        name_match = re.fullmatch(r"\s*Имя:\s*(.+?)\s*", lines[0], flags=re.IGNORECASE)
        if name_match:
            author = name_match.group(1)
            lines = lines[1:]

    source_url = ""
    entities = message.entities if message.text is not None else message.caption_entities
    for entity in entities or []:
        label = entity.extract_from(content).strip()
        entity_url = entity.url if entity.type == "text_link" else None
        if entity.type == "url":
            entity_url = label
        if not entity_url:
            continue
        if is_source_label(label):
            source_url = entity_url

    while lines and not lines[-1].strip():
        lines.pop()
    if lines and is_source_label(lines[-1]):
        lines.pop()
    return author, "\n".join(lines).strip(), source_url


@router.message(CommandStart())
async def start(message: Message, settings: Settings) -> None:
    if not allowed(message.from_user.id if message.from_user else None, settings):
        return
    await message.answer(
        "<b>ParserTgYtX</b>\n\nСобираю и фильтрую материалы для вашего криптоканала.",
        reply_markup=main_menu(),
    )


@router.callback_query(F.data == "menu:main")
async def menu_home(callback: CallbackQuery, settings: Settings) -> None:
    if not allowed(callback.from_user.id, settings):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback_message(callback).edit_text(
        "<b>Главное меню</b>", reply_markup=main_menu()
    )
    await callback.answer()


@router.callback_query(F.data == "menu:sources")
async def menu_sources(callback: CallbackQuery, settings: Settings) -> None:
    if not allowed(callback.from_user.id, settings):
        return
    await callback_message(callback).edit_text(
        "<b>📺 Источники</b>\n\nУправление YouTube-каналами.", reply_markup=sources_menu()
    )
    await callback.answer()


@router.callback_query(F.data == "source:add")
async def source_add(callback: CallbackQuery, state: FSMContext, settings: Settings) -> None:
    if not allowed(callback.from_user.id, settings):
        return
    await state.set_state(Form.youtube_url)
    await callback_message(callback).edit_text(
        "Отправьте ссылку на YouTube-канал, например:\n<code>https://youtube.com/@handle</code>",
        reply_markup=back_menu(),
    )
    await callback.answer()


@router.message(Form.youtube_url)
async def source_add_value(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    youtube: YouTubeService,
    settings: Settings,
) -> None:
    if not allowed(message.from_user.id if message.from_user else None, settings):
        return
    try:
        channel = await youtube.resolve_channel(message.text or "")
        existing = await session.scalar(
            select(Source).where(Source.kind == "youtube", Source.external_id == channel.channel_id)
        )
        if existing:
            await message.answer("Этот канал уже добавлен.", reply_markup=sources_menu())
        else:
            session.add(
                Source(
                    kind="youtube",
                    external_id=channel.channel_id,
                    title=channel.title,
                    url=channel.url,
                    content_mode="all",
                )
            )
            await session.commit()
            await youtube.subscribe(channel.channel_id)
            await message.answer(
                f"✅ Канал <b>{channel.title}</b> добавлен.", reply_markup=sources_menu()
            )
    except Exception as error:
        await message.answer(f"Не удалось добавить канал: {error}", reply_markup=sources_menu())
    await state.clear()


@router.callback_query(F.data == "source:list")
async def source_list(
    callback: CallbackQuery, session: AsyncSession, settings: Settings
) -> None:
    if not allowed(callback.from_user.id, settings):
        return
    sources = (await session.scalars(select(Source).order_by(Source.title))).all()
    text = (
        "\n".join(
            f"{SOURCE_MODE_LABELS.get(source.content_mode, '🟢 Все видео')} — {source.title}"
            for source in sources
        )
        or "Источники пока не добавлены."
    )
    rows = [
        [
            InlineKeyboardButton(
                text=(
                    f"{SOURCE_MODE_LABELS.get(source.content_mode, '🟢 Все видео')}"
                    f" · {source.title[:24]}"
                ),
                callback_data=f"source:toggle:{source.id}",
            ),
            InlineKeyboardButton(text="🗑", callback_data=f"source:delete:{source.id}"),
        ]
        for source in sources
    ]
    rows.append(
        [InlineKeyboardButton(text="◀️ Назад", callback_data="menu:sources")]
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=rows)
    await callback_message(callback).edit_text(
        f"<b>📋 YouTube-источники</b>\n\n{text}", reply_markup=keyboard
    )
    await callback.answer()


@router.callback_query(F.data.startswith("source:toggle:"))
async def source_toggle(
    callback: CallbackQuery, session: AsyncSession, youtube: YouTubeService, settings: Settings
) -> None:
    if not allowed(callback.from_user.id, settings):
        return
    source = await session.get(Source, int(callback_data(callback).rsplit(":", 1)[1]))
    if source:
        source.content_mode = NEXT_SOURCE_MODE.get(source.content_mode, "all")
        source.enabled = source.content_mode != "off"
        await session.commit()
        mode = "subscribe" if source.content_mode != "off" else "unsubscribe"
        await youtube.subscribe(source.external_id, mode)
    await source_list(callback, session, settings)


@router.callback_query(F.data.startswith("source:delete:"))
async def source_delete(
    callback: CallbackQuery, session: AsyncSession, youtube: YouTubeService, settings: Settings
) -> None:
    if not allowed(callback.from_user.id, settings):
        return
    source = await session.get(Source, int(callback_data(callback).rsplit(":", 1)[1]))
    if source:
        await youtube.subscribe(source.external_id, "unsubscribe")
        await session.delete(source)
        await session.commit()
    await source_list(callback, session, settings)


async def ensure_app_settings(session: AsyncSession, settings: Settings) -> AppSettings:
    value = await session.get(AppSettings, 1)
    if value is None:
        value = AppSettings(
            id=1,
            filter_mode=settings.default_filter_mode,
            filter_prompt=settings.default_filter_prompt,
        )
        session.add(value)
        await session.commit()
    return value


@router.callback_query(F.data == "menu:filter")
async def menu_filter(
    callback: CallbackQuery, session: AsyncSession, settings: Settings
) -> None:
    if not allowed(callback.from_user.id, settings):
        return
    value = await ensure_app_settings(session, settings)
    await callback_message(callback).edit_text(
        f"<b>🎯 AI-фильтр</b>\n\nРежим: <b>{value.filter_mode}</b>\n"
        f"Промпт:\n<i>{value.filter_prompt or 'не задан'}</i>",
        reply_markup=filter_menu(value.filter_mode),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("filter:set:"))
async def filter_set(
    callback: CallbackQuery, session: AsyncSession, settings: Settings
) -> None:
    if not allowed(callback.from_user.id, settings):
        return
    mode = callback_data(callback).rsplit(":", 1)[1]
    if mode not in {"all", "soft", "medium", "strict"}:
        return
    value = await ensure_app_settings(session, settings)
    value.filter_mode = mode
    await session.commit()
    await menu_filter(callback, session, settings)


@router.callback_query(F.data == "filter:prompt")
async def filter_prompt(callback: CallbackQuery, state: FSMContext, settings: Settings) -> None:
    if not allowed(callback.from_user.id, settings):
        return
    await state.set_state(Form.filter_prompt)
    await callback_message(callback).edit_text(
        "Отправьте новые критерии отбора:", reply_markup=back_menu()
    )
    await callback.answer()


@router.message(Form.filter_prompt)
async def filter_prompt_value(
    message: Message, state: FSMContext, session: AsyncSession, settings: Settings
) -> None:
    if not allowed(message.from_user.id if message.from_user else None, settings):
        return
    value = await ensure_app_settings(session, settings)
    value.filter_prompt = (message.text or "").strip()[:4000]
    await session.commit()
    await state.clear()
    await message.answer("✅ Промпт сохранен.", reply_markup=main_menu())


@router.callback_query(F.data == "menu:stats")
async def menu_stats(
    callback: CallbackQuery, session: AsyncSession, settings: Settings
) -> None:
    if not allowed(callback.from_user.id, settings):
        return
    query = select(ContentItem.status, func.count()).group_by(ContentItem.status)
    rows = (await session.execute(query)).all()
    counts: dict[str, int] = {cast(str, row[0]): cast(int, row[1]) for row in rows}
    await callback_message(callback).edit_text(
        "<b>📊 Статистика</b>\n\n"
        f"Новые: {counts.get('new', 0)}\n"
        f"Отложено: {counts.get('deferred', 0)}\n"
        f"Отфильтровано: {counts.get('filtered', 0)}\n"
        f"Передано в обработку: {counts.get('sent', 0)}",
        reply_markup=back_menu(),
    )
    await callback.answer()


@router.callback_query(F.data == "menu:feed")
async def menu_feed(
    callback: CallbackQuery, session: AsyncSession, settings: Settings
) -> None:
    if not allowed(callback.from_user.id, settings):
        return
    items = (
        await session.scalars(
            select(ContentItem)
            .where(ContentItem.status == "new")
            .order_by(ContentItem.created_at.desc())
            .limit(10)
        )
    ).all()
    await callback_message(callback).edit_text(
        f"<b>📥 Лента</b>\n\nМатериалов в очереди: {len(items)}",
        reply_markup=back_menu(),
    )
    for item in items:
        await callback_message(callback).answer(
            format_card(item),
            reply_markup=item_keyboard(item.id, item.url, item.media_type),
        )
    await callback.answer()


@router.callback_query(F.data == "menu:deferred")
async def menu_deferred(
    callback: CallbackQuery, session: AsyncSession, settings: Settings
) -> None:
    if not allowed(callback.from_user.id, settings):
        await answer_callback_safely(callback, "Нет доступа", show_alert=True)
        return
    count = int(
        await session.scalar(
            select(func.count()).select_from(ContentItem).where(
                ContentItem.status == "deferred"
            )
        )
        or 0
    )
    items = (
        await session.scalars(
            select(ContentItem)
            .where(ContentItem.status == "deferred")
            .order_by(ContentItem.deferred_at.desc(), ContentItem.id.desc())
            .limit(20)
        )
    ).all()
    await callback_message(callback).edit_text(
        f"<b>🕓 Отложка</b>\n\nМатериалов: {count}",
        reply_markup=back_menu(),
    )
    for item in items:
        await callback_message(callback).answer(
            format_card(item, deferred=True),
            reply_markup=item_keyboard(
                item.id,
                item.url,
                item.media_type,
                deferred=True,
            ),
        )
    await answer_callback_safely(callback)


@router.callback_query(F.data == "menu:settings")
async def menu_settings(
    callback: CallbackQuery, session: AsyncSession, settings: Settings
) -> None:
    if not allowed(callback.from_user.id, settings):
        return
    app_settings = await ensure_app_settings(session, settings)
    await callback_message(callback).edit_text(
        "<b>⚙️ Настройки</b>\n\n"
        f"Входящий канал: <code>{settings.telegram_inbox_chat_id}</code>\n"
        f"Группа обработки: <code>{settings.telegram_output_chat_id}</code>\n"
        f"Напоминание об отложке: <b>{app_settings.deferred_reminder_time} МСК</b>",
        reply_markup=settings_menu(),
    )
    await callback.answer()


@router.callback_query(F.data == "settings:deferred-time")
async def deferred_reminder_time_prompt(
    callback: CallbackQuery, state: FSMContext, settings: Settings
) -> None:
    if not allowed(callback.from_user.id, settings):
        await answer_callback_safely(callback, "Нет доступа", show_alert=True)
        return
    await state.set_state(Form.deferred_reminder_time)
    await callback_message(callback).edit_text(
        "Отправьте время ежедневного напоминания по Москве в формате "
        "<code>18:00</code>.",
        reply_markup=back_menu(),
    )
    await answer_callback_safely(callback)


@router.message(Form.deferred_reminder_time)
async def deferred_reminder_time_value(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
) -> None:
    if not allowed(message.from_user.id if message.from_user else None, settings):
        return
    value = (message.text or "").strip()
    match = re.fullmatch(r"([01]\d|2[0-3]):([0-5]\d)", value)
    if not match:
        await message.answer(
            "❌ Неверный формат. Отправьте время, например <code>18:00</code>.",
            reply_markup=back_menu(),
        )
        return
    app_settings = await ensure_app_settings(session, settings)
    app_settings.deferred_reminder_time = value
    await session.commit()
    await state.clear()
    await message.answer(
        f"✅ Напоминание установлено на <b>{value} МСК</b>.",
        reply_markup=settings_menu(),
    )


@router.callback_query(F.data.startswith("defer:add:"))
async def defer_item(
    callback: CallbackQuery, session: AsyncSession, settings: Settings
) -> None:
    if not allowed(callback.from_user.id, settings):
        await answer_callback_safely(callback, "Нет доступа", show_alert=True)
        return
    item = await session.get(ContentItem, int(callback_data(callback).rsplit(":", 1)[1]))
    if item is None:
        await answer_callback_safely(callback, "Материал не найден", show_alert=True)
        return
    if item.status == "sent":
        await answer_callback_safely(callback, "Материал уже передан", show_alert=True)
        return
    item.status = "deferred"
    item.deferred_at = datetime.now(UTC)
    await session.commit()
    await edit_callback_safely(
        callback,
        format_card(item, deferred=True),
        item_keyboard(item.id, item.url, item.media_type, deferred=True),
    )
    await update_original_card(callback, item, settings, deferred=True)
    await answer_callback_safely(callback, "Добавлено в отложку")


@router.callback_query(F.data.startswith("defer:cancel:"))
async def cancel_deferred_item(
    callback: CallbackQuery, session: AsyncSession, settings: Settings
) -> None:
    if not allowed(callback.from_user.id, settings):
        await answer_callback_safely(callback, "Нет доступа", show_alert=True)
        return
    item = await session.get(ContentItem, int(callback_data(callback).rsplit(":", 1)[1]))
    if item is None:
        await answer_callback_safely(callback, "Материал не найден", show_alert=True)
        return
    item.status = "dismissed"
    item.deferred_at = None
    await session.commit()
    await edit_callback_safely(
        callback,
        format_card(item, dismissed=True),
        item_keyboard(item.id, item.url, item.media_type, sent=True),
    )
    await update_original_card(callback, item, settings, dismissed=True)
    await answer_callback_safely(callback, "Убрано из отложки")


@router.callback_query(F.data.startswith("publish:"))
async def publish_item(
    callback: CallbackQuery, session: AsyncSession, pipeline: ContentPipeline, settings: Settings
) -> None:
    if not allowed(callback.from_user.id, settings):
        await answer_callback_safely(callback, "Нет доступа", show_alert=True)
        return
    await answer_callback_safely(callback, "Передаю в обработку…")
    item_id = int(callback_data(callback).split(":", 1)[1])
    item = await session.get(ContentItem, item_id)
    if item is None:
        await callback_message(callback).answer("Материал не найден.")
        return
    if item.status == "sent":
        await edit_callback_safely(
            callback,
            format_card(item, sent=True),
            item_keyboard(item.id, item.url, item.media_type, sent=True),
        )
        await update_original_card(callback, item, settings, sent=True)
        return
    await edit_callback_safely(
        callback,
        format_card(item, processing=True),
        item_keyboard(
            item.id,
            item.url,
            item.media_type,
            processing=True,
        ),
    )
    was_deferred = item.status == "deferred"
    try:
        item, published = await pipeline.publish(session, item_id)
        await edit_callback_safely(
            callback,
            format_card(item, sent=True),
            item_keyboard(item.id, item.url, item.media_type, sent=True),
        )
        await update_original_card(callback, item, settings, sent=True)
        if not published:
            await callback_message(callback).answer("Этот материал уже был передан.")
    except TelegramAPIError:
        await session.rollback()
        item = await session.get(ContentItem, item_id)
        if item is None:
            return
        await edit_callback_safely(
            callback,
            format_card(item, deferred=was_deferred),
            item_keyboard(
                item.id,
                item.url,
                item.media_type,
                deferred=was_deferred,
            ),
        )
        await callback_message(callback).answer(
            "❌ Не удалось передать материал. Проверьте права бота и повторите."
        )


@router.callback_query(F.data.startswith("download:"))
async def download_item(
    callback: CallbackQuery,
    session: AsyncSession,
    downloader: MediaDownloader,
    settings: Settings,
) -> None:
    if not allowed(callback.from_user.id, settings):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.answer("Готовлю видео…")
    item = await session.get(
        ContentItem, int(callback_data(callback).split(":", 1)[1])
    )
    if item is None or item.media_type != "video":
        await callback_message(callback).answer("❌ Видео для скачивания не найдено.")
        return
    try:
        await downloader.send(item)
    except (DownloadError, TelegramAPIError, TimeoutError) as error:
        await callback_message(callback).answer(f"❌ {error}")
    except Exception:
        logger.exception("Unexpected media download error for item %s", item.id)
        await callback_message(callback).answer(
            "❌ Не удалось скачать видео. Попробуйте еще раз позже."
        )


async def process_inbox_message(
    message: Message,
    pipeline: ContentPipeline,
    session: AsyncSession,
    settings: Settings,
) -> None:
    if message.chat.id != settings.telegram_inbox_chat_id:
        if message.chat.type in {"group", "supergroup"}:
            logger.info(
                "Ignored Telegram chat id=%s title=%r type=%s forum=%s migrated_from=%s",
                message.chat.id,
                message.chat.title,
                message.chat.type,
                getattr(message.chat, "is_forum", None),
                message.migrate_from_chat_id,
            )
        return
    author, content, source_url = parse_inbox_content(message)
    if not content:
        logger.info(
            "Ignored Telegram message without text chat=%s message=%s media_group=%s",
            message.chat.id,
            message.message_id,
            message.media_group_id,
        )
        return
    url = source_url or telegram_message_url(message)
    if not url:
        try:
            if message.bot is None:
                raise RuntimeError("Telegram bot context is unavailable")
            chat = await message.bot.get_chat(message.chat.id)
            url = chat.invite_link or ""
        except Exception:
            url = ""
    await pipeline.ingest(
        session,
        NormalizedItem(
            kind="telegram",
            external_id=f"{message.chat.id}:{message.message_id}",
            author=author,
            content=content,
            media_type=(
                "video"
                if (
                    message.video
                    or message.video_note
                    or (
                        message.document
                        and message.document.mime_type
                        and message.document.mime_type.startswith("video/")
                    )
                )
                else "voice"
                if getattr(message, "voice", None)
                else "audio"
                if (
                    getattr(message, "audio", None)
                    or (
                        message.document
                        and message.document.mime_type
                        and message.document.mime_type.startswith("audio/")
                    )
                )
                else "none"
            ),
            title_hint=content[:80],
            url=url,
            source_chat_id=message.chat.id,
            source_message_id=message.message_id,
        ),
    )


@router.channel_post()
async def inbox_channel_post(
    message: Message,
    pipeline: ContentPipeline,
    session: AsyncSession,
    settings: Settings,
) -> None:
    await process_inbox_message(message, pipeline, session, settings)


@router.message()
async def inbox_group_message(
    message: Message,
    pipeline: ContentPipeline,
    session: AsyncSession,
    settings: Settings,
) -> None:
    await process_inbox_message(message, pipeline, session, settings)
