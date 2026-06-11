import logging
from datetime import UTC, datetime
from html import escape

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import AppSettings, ContentItem
from app.schemas import NormalizedItem
from app.services.openai_filter import ContentAnalyzer

logger = logging.getLogger(__name__)


def item_keyboard(
    item_id: int,
    url: str,
    media_type: str = "none",
    sent: bool = False,
) -> InlineKeyboardMarkup:
    row = []
    if not sent:
        row.append(
            InlineKeyboardButton(text="✅ Сделать пост", callback_data=f"publish:{item_id}")
        )
    if url:
        row.append(InlineKeyboardButton(text="🔗 Ссылка", url=url))
    if media_type == "video":
        row.append(
            InlineKeyboardButton(text="📥 Скачать", callback_data=f"download:{item_id}")
        )
    return InlineKeyboardMarkup(inline_keyboard=[row])


def format_card(item: ContentItem, sent: bool = False) -> str:
    type_name = "YouTube" if item.kind == "youtube" else "Пост TG"
    state = "\n\n✅ <b>Передано в обработку</b>" if sent else ""
    return (
        f"<b>{escape(item.author)} | {escape(item.title)} | {type_name}</b>\n\n"
        f"{escape(item.summary)}\n\nРелевантность: {item.relevance:.0%}{state}"
    )


class ContentPipeline:
    def __init__(self, bot: Bot, analyzer: ContentAnalyzer, settings: Settings) -> None:
        self.bot = bot
        self.analyzer = analyzer
        self.settings = settings

    async def ingest(self, session: AsyncSession, incoming: NormalizedItem) -> ContentItem | None:
        app_settings = await session.get(AppSettings, 1)
        if app_settings is None:
            app_settings = AppSettings(
                id=1,
                filter_mode=self.settings.default_filter_mode,
                filter_prompt=self.settings.default_filter_prompt,
            )
            session.add(app_settings)
            await session.flush()
        try:
            analysis = await self.analyzer.analyze(
                incoming, app_settings.filter_mode, app_settings.filter_prompt
            )
        except Exception:
            logger.exception("AI analysis failed; delivering item with fallback metadata")
            analysis = self.analyzer_fallback(incoming)
        item = ContentItem(
            kind=incoming.kind,
            external_id=incoming.external_id,
            author=incoming.author,
            title=analysis.title,
            summary=analysis.summary,
            content=incoming.content,
            media_type=incoming.media_type,
            url=incoming.url,
            source_chat_id=incoming.source_chat_id,
            source_message_id=incoming.source_message_id,
            relevance=analysis.score,
            status="new" if analysis.relevant else "filtered",
        )
        session.add(item)
        try:
            await session.flush()
        except IntegrityError:
            await session.rollback()
            return None
        if analysis.relevant:
            message = await self.bot.send_message(
                self.settings.telegram_owner_id,
                format_card(item),
                reply_markup=item_keyboard(item.id, item.url, item.media_type),
            )
            item.review_message_id = message.message_id
        await session.commit()
        return item

    @staticmethod
    def analyzer_fallback(item: NormalizedItem):  # type: ignore[no-untyped-def]
        from app.schemas import AnalysisResult

        return AnalysisResult(
            True,
            0.5,
            (item.title_hint or item.content)[:80],
            "AI временно недоступен",
        )

    async def publish(self, session: AsyncSession, item_id: int) -> tuple[ContentItem, bool]:
        query = select(ContentItem).where(ContentItem.id == item_id).with_for_update()
        item = (await session.execute(query)).scalar_one()
        if item.status == "sent":
            return item, False
        if item.kind == "telegram" and item.source_chat_id and item.source_message_id:
            try:
                await self.bot.copy_message(
                    self.settings.telegram_output_chat_id,
                    item.source_chat_id,
                    item.source_message_id,
                )
            except TelegramBadRequest:
                fallback = "\n\n".join(part for part in (item.content, item.url) if part)
                await self.bot.send_message(
                    self.settings.telegram_output_chat_id,
                    fallback or "Исходное сообщение защищено от копирования.",
                )
        else:
            await self.bot.send_message(self.settings.telegram_output_chat_id, item.url)
        item.status = "sent"
        item.sent_at = datetime.now(UTC)
        await session.commit()
        return item, True
