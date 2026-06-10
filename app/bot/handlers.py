from typing import cast

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
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

from app.bot.keyboards import back_menu, filter_menu, main_menu, sources_menu
from app.config import Settings
from app.models import AppSettings, ContentItem, Source
from app.schemas import NormalizedItem
from app.services.content import ContentPipeline, format_card, item_keyboard
from app.services.youtube import YouTubeService

router = Router()


class Form(StatesGroup):
    youtube_url = State()
    filter_prompt = State()


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


def telegram_message_url(message: Message) -> str:
    if message.chat.username:
        return f"https://t.me/{message.chat.username}/{message.message_id}"
    chat_id = str(message.chat.id)
    if message.chat.type == "supergroup" and chat_id.startswith("-100"):
        return f"https://t.me/c/{chat_id[4:]}/{message.message_id}"
    return ""


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
    text = "\n".join(
        f"{'🟢' if source.enabled else '⚪'} {source.title}" for source in sources
    ) or "Источники пока не добавлены."
    rows = [
        [
            InlineKeyboardButton(
                text=f"{'⏸' if source.enabled else '▶️'} {source.title[:30]}",
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
        source.enabled = not source.enabled
        await session.commit()
        mode = "subscribe" if source.enabled else "unsubscribe"
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
        f"Отфильтровано: {counts.get('filtered', 0)}\n"
        f"Передано в Sumify: {counts.get('sent', 0)}",
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
            reply_markup=item_keyboard(item.id, item.url),
        )
    await callback.answer()


@router.callback_query(F.data == "menu:settings")
async def menu_settings(callback: CallbackQuery, settings: Settings) -> None:
    if not allowed(callback.from_user.id, settings):
        return
    await callback_message(callback).edit_text(
        "<b>⚙️ Настройки</b>\n\n"
        f"Входящий канал: <code>{settings.telegram_inbox_chat_id}</code>\n"
        f"Канал Sumify: <code>{settings.telegram_sumify_chat_id}</code>\n\n"
        "Эти значения меняются в <code>.env</code> на сервере.",
        reply_markup=back_menu(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("publish:"))
async def publish_item(
    callback: CallbackQuery, session: AsyncSession, pipeline: ContentPipeline, settings: Settings
) -> None:
    if not allowed(callback.from_user.id, settings):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.answer("Передаю в Sumify…")
    try:
        item, published = await pipeline.publish(
            session, int(callback_data(callback).split(":", 1)[1])
        )
        await callback_message(callback).edit_text(
            format_card(item, sent=True),
            reply_markup=item_keyboard(item.id, item.url, sent=True),
        )
        if not published:
            await callback_message(callback).answer("Этот материал уже был передан.")
    except TelegramAPIError:
        await session.rollback()
        await callback_message(callback).answer(
            "❌ Не удалось передать материал в Sumify. Проверьте права бота и повторите."
        )


async def process_inbox_message(
    message: Message,
    pipeline: ContentPipeline,
    session: AsyncSession,
    settings: Settings,
) -> None:
    if message.chat.id != settings.telegram_inbox_chat_id:
        return
    content = message.text or message.caption or ""
    url = telegram_message_url(message)
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
            author=message.author_signature or message.chat.title or "Telegram",
            content=content,
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
